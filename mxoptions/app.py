"""Application entry: wires the engine, the settings window, the tray icon and the ring overlay."""

import argparse
import copy
import json
import logging
import os
import signal
import sys
import time

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import APP_ID, APP_NAME, __version__, config, desktop, device, i18n, paths
from .engine import Engine, Listener
from .i18n import _

log = logging.getLogger(__name__)


class Bridge(QObject, Listener):
    """Carries engine callbacks from the worker thread onto the GUI thread."""

    state = Signal(object)
    seeded = Signal(object)
    ring = Signal(str)
    pressed = Signal(object)
    ring_turn = Signal(float)
    gesture = Signal(object)
    event = Signal(str, object)

    def on_state(self, state):
        self.state.emit(state)

    def on_config(self, cfg):
        self.seeded.emit(cfg)

    def on_ring(self, event):
        self.ring.emit(event)

    def on_pressed(self, keys):
        self.pressed.emit(keys)

    def on_ring_turn(self, clicks):
        self.ring_turn.emit(clicks)

    def on_gesture(self, event):
        self.gesture.emit(event)

    def on_event(self, kind, payload):
        self.event.emit(kind, payload)


def _socket_name():
    """What the one running copy is reached by: one per user."""
    user = os.getuid() if hasattr(os, "getuid") else "".join(ch for ch in os.environ.get("USERNAME", "user") if ch.isalnum())
    return f"{APP_ID}-{user}"


def _log_to():
    """Where the log goes besides the standard error stream: on Windows a program without a console has none,
    so there it is a file (the last half megabyte, and the one before)."""
    if sys.platform != "win32":
        return []
    from logging.handlers import RotatingFileHandler

    os.makedirs(paths.DATA, exist_ok=True)
    return [RotatingFileHandler(os.path.join(paths.DATA, f"{APP_ID}.log"), maxBytes=512 * 1024, backupCount=1, encoding="utf-8")]


def _notify_running_instance(show):
    """True if the program is already running; ``show`` asks that copy to bring up its window."""
    socket = QLocalSocket()
    socket.connectToServer(_socket_name())
    if not socket.waitForConnected(300):
        return False
    socket.write(b"show" if show else b"ping")
    socket.waitForBytesWritten(300)
    socket.disconnectFromServer()
    return True


def _ask_running_instance(name, arguments):
    """`mxoptions --call`: have the running copy do something its interface for other programs offers, and print
    what it answers. The exit status is 0 if it was done, 2 if it could not be, 3 if the program is not running."""
    socket = QLocalSocket()
    socket.connectToServer(_socket_name())
    if not socket.waitForConnected(500):
        print(f"{APP_NAME} is not running", file=sys.stderr)
        return 3
    socket.write(b"call " + json.dumps([name, *arguments]).encode("utf-8") + b"\n")
    answer = b""
    while not answer.endswith(b"\n") and socket.waitForReadyRead(5000):
        answer += bytes(socket.readAll())
    socket.disconnectFromServer()
    try:
        done, value = json.loads(answer)
    except ValueError:
        print("no answer", file=sys.stderr)
        return 2
    if isinstance(value, list):
        print("\n".join(str(item) for item in value))
    elif value is not None:
        print(str(value).lower() if isinstance(value, bool) else value, file=sys.stdout if done else sys.stderr)
    return 0 if done else 2


def _tray_tooltip(state):
    if not state["connected"]:
        return f"{APP_NAME} — {_('Not connected')}"
    text = state["name"]
    if state.get("battery"):
        text += f" — {state['battery'][0]}%"
        if state["battery"][1] in (device.BATTERY_CHARGING, device.BATTERY_ALMOST_FULL, device.BATTERY_SLOW_CHARGE):
            text += f" ({_('charging')})"
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(prog=APP_ID, description=f"{APP_NAME} {__version__}")
    parser.add_argument("--background", action="store_true",
                        help="start without opening the window (as the service does); start it again to open the window")
    parser.add_argument("--install", action="store_true", help="add MX Options to the application menu and exit")
    parser.add_argument("--uninstall", action="store_true", help="remove the menu and autostart entries and exit")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    parser.add_argument("--call", nargs="+", metavar=("NAME", "ARGUMENT"),
                        help="ask the running program for something its interface for other programs offers "
                             "(see docs/API.md), e.g. --call Trigger play_pause, --call Battery")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args = parser.parse_args(argv)

    streams = [logging.StreamHandler()] if sys.stderr is not None else []
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(name)s: %(message)s",
                        handlers=streams + _log_to())
    if args.install or args.uninstall:
        desktop.set_launcher(args.install)
        if args.uninstall:
            desktop.set_autostart(False)
        print(f"{'installed' if args.install else 'removed'}: {desktop.LAUNCHER_PATH}")
        return 0

    if args.call:
        # Asking the running program needs no window and no desktop: it has to work from a script, a timer or a
        # remote shell, where a program with windows could not even start.
        _plain = QCoreApplication(sys.argv[:1])  # noqa: F841 (Qt wants one to exist while sockets are used)
        return _ask_running_instance(args.call[0], args.call[1:])

    first_run = not config.exists()
    cfg = config.load()
    i18n.setup(cfg["app"]["language"])

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName(APP_ID)
    app.setQuitOnLastWindowClosed(False)
    if _notify_running_instance(show=not args.background):
        return 0
    if not args.background and os.environ.get("MXOPTIONS_LAUNCHER") and desktop.start_service():
        # An installed copy opened from the menu while its service was stopped: let the service be the
        # one copy that runs (so it is looked after like any service) and ask it for the window.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if _notify_running_instance(show=True):
                return 0
            time.sleep(0.2)
        log.warning("the service did not come up; running without it")

    from . import api, flow_service
    from .ui import editors, ring, theme, tray_icon
    from .ui.main_window import MainWindow
    from .ui.sounds import WheelSounds
    from .ui.store import Store

    app.setStyle("Fusion")
    theme.ensure_icon_theme()
    theme.set_theme(cfg["app"]["theme"], cfg["app"]["accent"])
    theme.apply(app)
    icon = QIcon(desktop.ICON_PATH)
    app.setWindowIcon(icon)

    QLocalServer.removeServer(_socket_name())
    server = QLocalServer(app)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)  # this user's programs only
    server.listen(_socket_name())

    bridge = Bridge()
    engine = Engine(cfg, bridge, seed=first_run)
    sounds = WheelSounds(app)
    sounds.configure(cfg["wheel_click"])
    store = Store(cfg, engine, sounds)
    store.flow = flow_service.FlowService(store, app)
    bridge.state.connect(store.update_state)
    bridge.seeded.connect(lambda seeded: store.replace(seeded, push=False))
    bridge.pressed.connect(store.pressed_changed)
    bridge.gesture.connect(store.gesture_changed)

    overlay = ring.create_overlay(app)
    overlay.set_look(cfg["app"]["theme"], cfg["app"]["accent"], cfg["app"]["language"])
    overlay.configure(cfg["ring"])
    editors.test_runner = engine.trigger_action
    applied = {"look": (cfg["app"]["theme"], cfg["app"]["accent"]), "ring": None}

    def config_changed():
        look = (store.cfg["app"]["theme"], store.cfg["app"]["accent"])
        if look != applied["look"]:
            applied["look"] = look
            theme.set_theme(*look)
            ring.clear_icon_cache()
            theme.apply(app)
            overlay.set_look(*look, store.cfg["app"]["language"])
            overlay.refresh()
        if store.cfg["ring"] != applied["ring"]:
            applied["ring"] = copy.deepcopy(store.cfg["ring"])
            overlay.configure(applied["ring"])
        sounds.configure(store.cfg["wheel_click"])
        service.set_enabled(store.cfg["app"]["api"])

    def engine_event(kind, payload):
        if kind == "wheel_mode":
            mode, quiet = payload
            if not quiet and store.cfg["wheel_click"]["mode"] in ("sound", "both"):
                sounds.play(mode)
        elif kind == "flow":
            store.flow.on_engine(payload)
        service.api.on_event(kind, payload)

    store.changed.connect(config_changed)
    bridge.ring.connect(overlay.handle)
    bridge.ring_turn.connect(overlay.turn)
    overlay.opened.connect(engine.set_ring_open)
    overlay.triggered.connect(engine.trigger_action)
    overlay.cue.connect(engine.feedback)

    # Started in the background (the service does that) the program keeps running without a window:
    # starting it again opens the window. Otherwise it needs a tray icon to live on behind.
    has_tray = QSystemTrayIcon.isSystemTrayAvailable()
    stays = args.background or has_tray
    window = MainWindow(store, hide_on_close=stays)
    service = api.Service(store, overlay, window.show_and_raise, app)
    service.set_enabled(cfg["app"]["api"])
    bridge.event.connect(engine_event)
    app.setQuitOnLastWindowClosed(not stays)

    def instance_message(connection):
        """Another copy of the program was started: it wants the window shown, or passes on an `--call`."""
        message = bytes(connection.readAll())
        if message.startswith(b"show"):
            window.show_and_raise()
        elif message.startswith(b"call "):
            try:
                name, *arguments = json.loads(message[5:])
                if not store.cfg["app"]["api"]:  # the same switch as for the interface on the session bus
                    answer = (False, "other programs are not let in (see the settings: Device & app)")
                else:
                    answer = api.call(service.api, str(name), [str(value) for value in arguments])
            except (ValueError, TypeError):
                answer = (False, "not understood")
            connection.write(json.dumps(answer, default=str).encode("utf-8") + b"\n")
            connection.flush()

    def read_instance_message():
        connection = server.nextPendingConnection()
        if connection:
            connection.readyRead.connect(lambda: instance_message(connection))
            connection.disconnected.connect(connection.deleteLater)

    server.newConnection.connect(read_instance_message)

    charging = (device.BATTERY_CHARGING, device.BATTERY_ALMOST_FULL, device.BATTERY_SLOW_CHARGE)
    if sys.platform != "win32" and tray_icon.install():  # its drawings go into the icon theme, where the panel finds them
        tray_icon.announce()
    shown = {"tray": None}

    def tray_look():
        """The tray icon in the chosen style, showing whether a mouse is there and how its battery is."""
        style = store.cfg["app"]["tray_icon"]
        key = (style, tray_icon.key_for(style, store.state, charging))
        if key != shown["tray"]:
            shown["tray"] = key
            tray.setIcon(tray_icon.icon(style, store.state, charging))

    tray = QSystemTrayIcon(app)
    tray_look()
    store.changed.connect(tray_look)
    menu = QMenu()
    menu.addAction(_("Open {}").format(APP_NAME), window.show_and_raise)
    menu.addSeparator()
    menu.addAction(_("Quit"), app.quit)
    tray.setContextMenu(menu)
    tray.setToolTip(APP_NAME)
    tray.activated.connect(
        lambda reason: (window.hide() if window.isVisible() and window.isActiveWindow() else window.show_and_raise())
        if reason == QSystemTrayIcon.ActivationReason.Trigger else None
    )

    def tray_state(state):
        tray.setToolTip(_tray_tooltip(state))
        tray_look()

    store.state_changed.connect(tray_state)

    tray_wait = QTimer(app)  # at login the service may be up before the panel that hosts the tray
    tray_wait.setInterval(2000)

    def show_tray():
        if QSystemTrayIcon.isSystemTrayAvailable():
            tray_wait.stop()
            tray.show()

    tray_wait.timeout.connect(show_tray)
    show_tray()
    if not tray.isVisible():
        tray_wait.start()

    def shutdown():
        overlay.close()
        if hasattr(overlay, "shutdown"):  # it lives in a program of its own: that one ends with this one
            overlay.shutdown()
        store.flow.close()
        store.flush()
        engine.stop()

    app.aboutToQuit.connect(shutdown)
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_args: app.quit())
    wakeup = QTimer()  # gives the interpreter a chance to run the signal handlers
    wakeup.start(250)
    wakeup.timeout.connect(lambda: None)

    engine.start()
    if not args.background:
        window.show()
    status = app.exec()
    for _moment in range(10):  # the clipboard service may still be reading: it must finish before Qt is taken down
        app.processEvents()
        time.sleep(0.02)
    return status
