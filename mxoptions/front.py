"""Which app is in front, for the app profiles of the keyboard and the mouse ("keyboard.profiles", "mouse.profiles").

Every desktop tells it in its own way, or not at all:

  kwin      KDE Plasma, on Wayland or X11: a few lines of script loaded into KWin call this program over D-Bus
            whenever another window is activated (workspace.windowActivated, Window.resourceClass).
  sway      `swaymsg -t subscribe -m '["window"]'`: the focus events of sway-ipc(7), with the app_id of the
            window, or for an X11 window its class.
  hyprland  the socket Hyprland writes its events to (.socket2.sock): "activewindow>>CLASS,TITLE", of which the
            class is kept, up to the first comma.
  x11       any other X11 desktop: the class in WM_CLASS of the root window's _NET_ACTIVE_WINDOW, twice a second.
  windows   the program of GetForegroundWindow, by the name of its file, twice a second.

GNOME on Wayland tells no program which window is in front, short of an extension of the shell, which this
program does not bring: there the profiles are not offered, and keys and buttons do what they do in every app.

Only the name an app goes by is kept, lowercase (config.app_id); the title of a window never is. Which app is
in front never goes to the log, and is not part of what other programs can ask (api.py). The desktop is only
asked while something wants to know (Front.want)."""

import json
import logging
import ntpath
import os
import shutil
import tempfile

from PySide6.QtCore import ClassInfo, QObject, QProcess, QTimer, Signal, Slot, Qt
from PySide6.QtDBus import QDBusConnection, QDBusServiceWatcher
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QLocalSocket

from . import APP_ID, config, environment, flow_edges

log = logging.getLogger(__name__)

PATH = "/org/mxoptions/Front"
INTERFACE = "org.mxoptions.Front1"
SCRIPT_NAME = "mxoptions-front"
POLL_INTERVAL = 500    # ms between two looks, where the desktop has to be asked
RESTART_AFTER = 5000   # ms before a source that ended (swaymsg, Hyprland's socket) is started again
MAX_PENDING = 1 << 16  # bytes of an event not ended yet that are waited for; beyond that it is dropped
_SCRIPT = """\
// Loaded by MX Options while the keyboard has app profiles: says which app the window in front belongs to.
function tell(window) {
    callDBus(%(service)s, "%(path)s", "%(interface)s", "Front", window ? String(window.resourceClass) : "");
}
workspace.windowActivated.connect(tell);
tell(workspace.activeWindow);
"""


def app_name(text):
    """The name an app goes by (config.app_id) made from what a desktop says, or None if it is not one."""
    name = text.lower() if isinstance(text, str) else None
    return name if config.app_id(name) else None


def sway_app(event):
    """What a sway WINDOW event says of the app in front: None when it says nothing of it (not a focus event),
    "" for a window with no usable name. The window's app_id, or for an X11 one its window_properties.class."""
    if not isinstance(event, dict) or event.get("change") != "focus" or not isinstance(event.get("container"), dict):
        return None
    window = event["container"]
    name = window.get("app_id")
    if not isinstance(name, str) or not name:
        properties = window.get("window_properties")
        name = properties.get("class") if isinstance(properties, dict) else None
    return app_name(name) or ""


def hyprland_app(line):
    """What one line of Hyprland's event socket says of the app in front, as sway_app. Its class goes up to the
    first comma: a class with a comma in it cannot be told from the title, and the rest is the title."""
    event, sep, data = line.partition(">>")
    if event != "activewindow" or not sep:
        return None
    return app_name(data.split(",", 1)[0]) or ""


def windows_app(path):
    """The name of a Windows program: its file's, lowercase ("firefox.exe"), from foreground_program()."""
    return app_name(ntpath.basename(path)) if isinstance(path, str) else None


class Source(QObject):
    """Says which app is in front, once at the start where it can and then whenever that changes."""

    seen = Signal(str)   # its name, "" when it cannot be told
    ended = Signal()     # it stopped by itself: the desktop went away, or the program asked

    def close(self):
        raise NotImplementedError


class Poll(Source):
    def __init__(self, look, parent=None):
        super().__init__(parent)
        self._look, self._last = look, None
        self._timer = QTimer(self, interval=POLL_INTERVAL)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        QTimer.singleShot(0, self._tick)

    def _tick(self):
        if not self._timer.isActive():
            return
        try:
            name = self._look() or ""
        except Exception as error:  # a window gone while it was asked about
            log.debug("front: could not ask: %s", error)
            name = ""
        if name != self._last:
            self._last = name
            self.seen.emit(name)

    def close(self):
        self._timer.stop()


def _x11():
    from . import x11

    return app_name(x11.active_class())


def _windows():
    from . import windows

    return windows_app(windows.foreground_program())


class Sway(Source):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._buffer, self._decoder = "", json.JSONDecoder()
        self._process = QProcess(self)
        self._process.readyReadStandardOutput.connect(self._read)
        self._process.finished.connect(lambda *_args: self.ended.emit())
        self._process.errorOccurred.connect(lambda _error: self.ended.emit())  # no swaymsg, or it could not start
        self._process.setStandardErrorFile(QProcess.nullDevice())
        self._process.start("swaymsg", ["-r", "-t", "subscribe", "-m", '["window"]'])

    def _read(self):
        self._buffer += bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        while True:  # one JSON value after another, whether a line each or spread over lines
            text = self._buffer.lstrip()
            try:
                event, end = self._decoder.raw_decode(text)
            except (ValueError, RecursionError):
                self._buffer = text if len(text) <= MAX_PENDING else ""
                return
            self._buffer = text[end:]
            name = sway_app(event)
            if name is not None:
                self.seen.emit(name)

    def close(self):
        process, self._process = self._process, None
        process.readyReadStandardOutput.disconnect()
        process.finished.disconnect()
        process.errorOccurred.disconnect()
        process.kill()
        process.waitForFinished(500)
        self._buffer = ""


class Hyprland(Source):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self._pending = b""
        self._socket = QLocalSocket(self)
        self._socket.readyRead.connect(self._read)
        self._socket.disconnected.connect(self.ended)
        self._socket.errorOccurred.connect(lambda _error: self.ended.emit())
        self._socket.connectToServer(path)

    def _read(self):
        lines = (self._pending + bytes(self._socket.readAll())).split(b"\n")
        self._pending = lines.pop() if len(lines[-1]) <= MAX_PENDING else b""  # the rest of a line still to come
        for line in lines:
            name = hyprland_app(line.decode("utf-8", "replace"))
            if name is not None:
                self.seen.emit(name)

    def close(self):
        socket, self._socket = self._socket, None
        socket.readyRead.disconnect()
        socket.disconnected.disconnect()
        socket.errorOccurred.disconnect()
        socket.abort()
        self._pending = b""


@ClassInfo({"D-Bus Interface": INTERFACE})
class _FrontSink(QObject):
    """What the KWin script calls."""

    heard = Signal(str)

    @Slot(str)
    def Front(self, name):
        self.heard.emit(app_name(name[:config.MAX_APP_ID + 1]) or "")


class KWin(Source):
    """On the session bus under this program's own connection name only: no name of its own is taken there."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sink = _FrontSink(self)
        self._sink.heard.connect(self.seen)
        bus = QDBusConnection.sessionBus()
        if not bus.registerObject(PATH, self._sink, QDBusConnection.RegisterOption.ExportAllSlots):
            raise RuntimeError(bus.lastError().message() or "the session bus would not take it")
        self._watcher = QDBusServiceWatcher(flow_edges.KWIN, bus, QDBusServiceWatcher.WatchModeFlag.WatchForRegistration, self)
        self._watcher.serviceRegistered.connect(lambda _name: self._load())  # KWin came up (again): its scripts are gone
        self._script = tempfile.TemporaryDirectory(prefix=SCRIPT_NAME + "-")
        self._load()

    def _load(self):
        path = os.path.join(self._script.name, SCRIPT_NAME + ".js")
        try:
            with open(path, "w") as f:
                f.write(_SCRIPT % {"service": json.dumps(QDBusConnection.sessionBus().baseService()), "path": PATH,
                                   "interface": INTERFACE})
        except OSError as e:
            log.warning("front: could not write the script for KWin: %s", e)
            return
        flow_edges._kwin("unloadScript", SCRIPT_NAME)  # one left behind by a copy of the program that did not exit cleanly
        loaded = flow_edges._kwin("loadScript", path, SCRIPT_NAME)
        if loaded is None or loaded < 0:
            log.warning("front: KWin did not take the script")
            return
        flow_edges._kwin("start")

    def close(self):
        self._watcher.serviceRegistered.disconnect()
        self._sink.heard.disconnect()
        flow_edges._kwin("unloadScript", SCRIPT_NAME)
        QDBusConnection.sessionBus().unregisterObject(PATH)
        self._script.cleanup()


def _choice():
    """The source for this desktop, or None where none tells which app is in front."""
    session, desktop = environment.session(), environment.current()
    if session == "windows":
        return "windows"
    if desktop == environment.KDE or flow_edges.kwin_running():
        return "kwin"
    if desktop == environment.SWAY and os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
        return "sway"
    if desktop == environment.HYPRLAND and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and os.environ.get("XDG_RUNTIME_DIR"):
        return "hyprland"
    if session == "x11":
        return "x11"
    return None


def _make(kind, parent):
    if kind == "kwin":
        return KWin(parent)
    if kind == "sway":
        return Sway(parent)
    if kind == "hyprland":
        return Hyprland(os.path.join(os.environ["XDG_RUNTIME_DIR"], "hypr", os.environ["HYPRLAND_INSTANCE_SIGNATURE"],
                                     ".socket2.sock"), parent)
    return Poll(_windows if kind == "windows" else _x11, parent)


class Front(QObject):
    """Tells the engine which app is in front (Engine.set_front) while something wants that: the keyboard or the
    mouse has app profiles, or a page that makes them is open. ``last_other`` is the app last in front that is not
    this program, for that page to offer."""

    changed = Signal()  # last_other changed

    def __init__(self, engine, parent=None, kind=False):
        super().__init__(parent)
        self.engine = engine
        self.kind = _choice() if kind is False else kind
        self.last_other = None
        self._wanted = set()
        self._source = None
        self._restart = QTimer(self, singleShot=True, interval=RESTART_AFTER)
        self._restart.timeout.connect(self._sync)

    @property
    def supported(self):
        return self.kind is not None

    def want(self, who, on):
        """``who`` ("profiles", "page", a page's app chooser) does, or no longer does, need to know."""
        if on:
            self._wanted.add(who)
        else:
            self._wanted.discard(who)
        self._sync()

    def close(self):
        self._wanted.clear()
        self._sync()

    def _sync(self):
        on = bool(self._wanted) and self.supported
        if on and self._source is None and not self._restart.isActive():
            try:
                self._source = _make(self.kind, self)
            except Exception as error:
                log.warning("front: the app in front cannot be followed here (%s): %s", self.kind, error)
                self.kind = None
                return
            self._source.seen.connect(self._seen)
            self._source.ended.connect(self._ended, Qt.ConnectionType.QueuedConnection)
            log.info("front: following the app in front (%s)", self.kind)
        elif not on:
            self._restart.stop()
            if self._source is not None:
                self._stop()
                log.info("front: no longer following the app in front")

    def _stop(self):
        source, self._source = self._source, None
        source.seen.disconnect()
        source.ended.disconnect()
        source.close()
        source.deleteLater()
        self.engine.set_front(None)
        if self.last_other is not None:
            self.last_other = None
            self.changed.emit()

    def _ended(self):
        if self._source is not None and self._source is self.sender():
            log.debug("front: the source ended; started again in a moment")
            self._stop()
            self._restart.start()

    def _seen(self, name):
        self.engine.set_front(name or None)
        mine = name == APP_ID or (isinstance(QGuiApplication.instance(), QGuiApplication)
                                  and QGuiApplication.applicationState() == Qt.ApplicationState.ApplicationActive)
        if name and not mine and name != self.last_other:
            self.last_other = name
            self.changed.emit()
