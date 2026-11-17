"""Application entry: wires the engine, the settings window, the tray icon and the ring overlay."""

import argparse
import copy
import gc
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

from . import APP_ID, APP_NAME, __version__, config, desktop, device, i18n, keyboard, paths
from .engine import Engine, Listener
from .i18n import _

log = logging.getLogger(__name__)


class Bridge(QObject, Listener):
    """Carries engine callbacks from the worker thread onto the GUI thread."""

    state = Signal(object)
    seeded = Signal(object)
    ring = Signal(str, object, object)
    pressed = Signal(object)
    ring_turn = Signal(float)
    effect = Signal(str, object)
    gesture = Signal(object)
    event = Signal(str, object)

    def on_state(self, state):
        self.state.emit(state)

    def on_config(self, cfg):
        self.seeded.emit(cfg)

    def on_ring(self, event, session=None, bubbles=None):
        self.ring.emit(event, session, bubbles)

    def on_pressed(self, keys):
        self.pressed.emit(keys)

    def on_ring_turn(self, clicks):
        self.ring_turn.emit(clicks)

    def on_effect(self, event, look=None):
        self.effect.emit(event, look)

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


def _device_line(found):
    text = found["name"]
    if found.get("battery"):
        text += f" — {found['battery'][0]}%"
        if found["battery"][1] in (device.BATTERY_CHARGING, device.BATTERY_ALMOST_FULL, device.BATTERY_SLOW_CHARGE):
            text += f" ({_('charging')})"
    return text


def _tray_tooltip(state):
    found = state.get("keyboard")
    second = "\n" + _device_line(found) if found and found["connected"] else ""
    if not state["connected"]:
        return f"{APP_NAME} — {_('Not connected')}" + second
    return _device_line(state) + second


LOCK_TITLES = {"caps_lock": "Caps Lock", "num_lock": "Num Lock", "scroll_lock": "Scroll Lock"}  # keycap names: as they are
GC_PERIOD = 30.0  # s between two collections of reference cycles, on the GUI thread (see main)


def notice_message(notice):
    """What the tray says of one notice of the keyboard's (keyboard.new_notice), or None for one not known here."""
    kind = notice.get("kind")
    if kind == "backlight":
        return _("Keyboard backlight: level {level} of {top}").format(level=notice["level"], top=notice["top"])
    if kind == "fn" and type(notice.get("value")) is bool:
        return _("Fn lock on: the top row gives F1–F12") if notice["value"] else _(
            "Fn lock off: the top row gives its other functions")
    keys = notice.get("keys") if kind == "locks" else None
    if not isinstance(keys, dict) or not keys or not all(name in LOCK_TITLES for name in keys):
        return None
    return ", ".join((_("{key} on") if on else _("{key} off")).format(key=LOCK_TITLES[name]) for name, on in keys.items())


def together_message(on, flow_on):
    """What the tray says once the action "together" switched the two follow settings ``on`` or off."""
    if not on:
        return _("Mouse and keyboard move on their own")
    return _("Mouse and keyboard move together") if flow_on else _("Mouse and keyboard move together once Flow is on")


STAYS_REASONS = {
    "held": "a key is held down",
    "busy": "the keyboard is busy, try again in a moment",
    "pairing": "the keyboard has no computer paired on that channel",
    "unsupported": "MX Options cannot tell whether a key of this keyboard is held down",
}


def stays_message(why):
    """What the tray says once an Easy-Switch of mouse and keyboard left both here (engine._switch_host)."""
    reason = STAYS_REASONS.get(why)
    return _("Mouse and keyboard stay here: {reason}").format(reason=_(reason)) if reason else _("Mouse and keyboard stay here")


def tray_messages(armed, seen, found):
    """What the tray says of one state of the keyboard (``found``, or None): (armed, seen, [(text, "info" or
    "warning")]), the newest notice first and a low battery last, so that a notice never takes the warning's place.
    ``armed``: see keyboard.low_battery; ``seen``: see keyboard.new_notice."""
    armed, low = keyboard.low_battery(armed, found.get("battery") if found and found["connected"] else None)
    seen, notice = keyboard.new_notice(seen, found)
    text = notice_message(notice) if notice else None
    messages = [(text, "info")] if text else []
    if low:
        messages.append((_("The battery of {name} is low ({percent}%). Charge it soon.").format(
            name=found["name"], percent=found["battery"][0]), "warning"))
    return armed, seen, messages


def mouse_messages(armed, state, messages):
    """``messages`` (tray_messages) with a low battery of the mouse (``state``, the engine's) added, said as
    keyboard.low_battery says it of the keyboard, with an ``armed`` of its own; a percent of 0 tells no level.
    Both low at once: one warning of both, so that neither takes the other's place. Returns (armed, messages)."""
    battery = state.get("battery") if state.get("connected") else None
    armed, low = keyboard.low_battery(armed, battery if battery and battery[0] else None)
    if not low:
        return armed, messages
    line = _("The battery of {name} is low ({percent}%). Charge it soon.").format(name=state["name"], percent=battery[0])
    if messages and messages[-1][1] == "warning":
        return armed, messages[:-1] + [(messages[-1][0] + "\n" + line, "warning")]
    return armed, messages + [(line, "warning")]


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

    from . import api, flow_service, front
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

    # Python's cyclic collector runs on whichever thread happens to allocate (the engine's, the keyboard's, Flow's,
    # a worker's); a Qt object it frees there is torn down off its own thread, which Qt does not allow. So cycles are
    # collected here only, on the GUI thread, now and then: from before the first of those threads starts.
    gc.disable()
    collector = QTimer(app)
    collector.timeout.connect(gc.collect)
    collector.start(int(GC_PERIOD * 1000))

    bridge = Bridge()
    engine = Engine(cfg, bridge, seed=first_run)
    sounds = WheelSounds(app)
    sounds.configure(cfg["wheel_click"])
    store = Store(cfg, engine, sounds)
    store.flow = flow_service.FlowService(store, app)
    from .ui import updates as update_check
    update_check.checker_for(store).start()  # the daily check: only the program itself runs it
    store.front = front.Front(engine, app)
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
        store.front.want("profiles", bool(store.cfg["keyboard"]["profiles"] or store.cfg["mouse"]["profiles"]))

    def engine_event(kind, payload):
        if kind == "wheel_mode":
            mode, quiet = payload
            if not quiet and store.cfg["wheel_click"]["mode"] in ("sound", "both"):
                sounds.play(mode)
        elif kind == "flow" and payload[0] == "together":
            on = store.flow.toggle_together()
            if tray.isVisible():
                shown["update"] = False  # this message's click opens no page
                tray.showMessage(APP_NAME, together_message(on, store.cfg["flow"]["enabled"]),
                                 QSystemTrayIcon.MessageIcon.Information)
        elif kind == "host" and payload[0] == "stays":
            if tray.isVisible():
                shown["update"] = False  # this message's click opens no page
                tray.showMessage(APP_NAME, stays_message(payload[1]), QSystemTrayIcon.MessageIcon.Warning)
        elif kind == "flow":
            store.flow.on_engine(payload)
        service.api.on_event(kind, payload)

    store.changed.connect(config_changed)
    bridge.ring.connect(overlay.handle)
    bridge.ring_turn.connect(overlay.turn)
    bridge.effect.connect(overlay.effect)
    overlay.opened.connect(engine.set_ring_open)
    overlay.triggered.connect(engine.ring_action)
    overlay.cue.connect(engine.feedback)

    # Started in the background (the service does that) the program keeps running without a window:
    # starting it again opens the window. Otherwise it needs a tray icon to live on behind.
    has_tray = QSystemTrayIcon.isSystemTrayAvailable()
    stays = args.background or has_tray
    window = MainWindow(store, hide_on_close=stays)
    service = api.Service(store, overlay, window.show_and_raise, app)
    service.set_enabled(cfg["app"]["api"])
    store.front.want("profiles", bool(cfg["keyboard"]["profiles"] or cfg["mouse"]["profiles"]))
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

    low = {"armed": True, "mouse": True}
    shown["notice"] = 0  # the number of the last notice of a change the keyboard reported that was said

    def tray_state(state):
        tray.setToolTip(_tray_tooltip(state))
        tray_look()
        low["armed"], shown["notice"], messages = tray_messages(low["armed"], shown["notice"], state.get("keyboard"))
        low["mouse"], messages = mouse_messages(low["mouse"], state, messages)
        for text, kind in messages if tray.isVisible() else ():  # hidden: said to no one, and not said later either
            shown["update"] = False
            tray.showMessage(APP_NAME, text, QSystemTrayIcon.MessageIcon.Warning if kind == "warning"
                             else QSystemTrayIcon.MessageIcon.Information)

    store.state_changed.connect(tray_state)

    shown["update"] = False  # the last message the tray showed is the one of a new release

    def update_found(result):
        checker = store.updates
        if result["state"] != "available" or result["version"] == checker.state["notified"] or not tray.isVisible():
            return
        tray.showMessage(APP_NAME, _("MX Options {version} is available.").format(version=result["version"]),
                         QSystemTrayIcon.MessageIcon.Information)
        shown["update"] = True
        checker.mark_notified(result["version"])

    def message_clicked():
        window.show_and_raise()
        if shown["update"]:
            window.open_page(window.device_page)

    store.updates.result.connect(update_found)
    tray.messageClicked.connect(message_clicked)
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
        store.front.close()
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
