"""The tray as app.main wires it: icon and notices follow engine state and settings. main() runs for real in a
Python of its own (it makes the QApplication, takes signals, runs the event loop); engine, overlay, window,
sounds, D-Bus interface, Flow, app-in-front watcher and tray are stand-ins. No device, bus or panel is touched."""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 120  # s for the whole run of main()
MOUSE_LOW = "The battery of Synthetic mouse is low ({}%). Charge it soon."
KEYBOARD_LOW = "The battery of Synthetic keyboard is low ({}%). Charge it soon."


def _child(result_path):
    """Run app.main() with stand-ins and write what the tray got to ``result_path`` (JSON)."""
    import threading

    from PySide6.QtCore import QObject, QTimer, Signal
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from mxoptions import api, app, device, flow_service, front
    from mxoptions.ui import main_window, ring, sounds, tray_icon

    seen = {"errors": [], "icons": [], "messages": [], "tooltip": None, "finished": False, "status": None}
    step = {"name": "start"}
    drawn = {}  # the last icon tray_icon.icon made, and what it shows

    def hook(kind, value, _tb):
        seen["errors"].append(f"{step['name']}: {kind.__name__}: {value}")

    sys.excepthook = hook
    threading.excepthook = lambda args: hook(args.exc_type, args.exc_value, args.exc_traceback)

    real_icon = tray_icon.icon

    def icon(style, state, charging):
        made = real_icon(style, state, charging)
        drawn.update(icon=made, key=[style, tray_icon.key_for(style, state, charging)])
        return made

    class Tray(QSystemTrayIcon):
        """A tray with a panel to show in, which says what it is given."""

        visible = False

        @staticmethod
        def isSystemTrayAvailable():
            return True

        def show(self):
            self.visible = True

        def isVisible(self):
            return self.visible

        def setIcon(self, made):
            seen["icons"].append([step["name"], *(drawn["key"] if made is drawn.get("icon") else ["?", "?"])])

        def setToolTip(self, text):
            seen["tooltip"] = text

        def showMessage(self, _title, text, kind):
            kind = {QSystemTrayIcon.MessageIcon.Warning: "warning",
                    QSystemTrayIcon.MessageIcon.Information: "info"}.get(kind, str(kind))
            seen["messages"].append([step["name"], text, kind])

    made = {}

    class Engine:
        def __init__(self, _cfg, listener, seed=False):
            made["listener"] = listener

        def start(self):
            QTimer.singleShot(0, scenario)
            guard = made["guard"] = QTimer(QApplication.instance(), singleShot=True)  # never left running
            guard.timeout.connect(QApplication.instance().quit)
            guard.start(TIMEOUT * 500)

        def stop(self):
            pass

        def set_config(self, _cfg):
            pass

        def trigger_action(self, *_args):
            pass

        set_ring_open = ring_action = feedback = trigger_action

    class Overlay(QObject):
        opened = Signal(bool)
        triggered = Signal(object, object)
        cue = Signal(int)

        def _nothing(self, *_args):
            pass

        set_look = configure = handle = turn = effect = refresh = close = _nothing

    class Sounds(QObject):
        def configure(self, _settings):
            pass

        def play(self, _kind):
            pass

    class Window:
        def __init__(self, store, hide_on_close=False):
            pass

        def show_and_raise(self):
            pass

        show = hide = show_and_raise

        def isVisible(self):
            return False

        isActiveWindow = isVisible

    class Service:
        def __init__(self, store, _overlay, _show, _parent=None):
            made["store"] = store
            self.api = self

        def set_enabled(self, _on):
            pass

        def on_event(self, _kind, _payload):
            pass

    class Side:
        def __init__(self, *_args):
            pass

        def want(self, _who, _on):
            pass

        def close(self):
            pass

        def on_engine(self, _payload):
            pass

    def no_announce():
        raise AssertionError("the tray icons were announced on a bus")

    def keyboard(percent, notice=None):
        return {"connected": True, "name": "Synthetic keyboard", "battery": [percent, device.BATTERY_DISCHARGING],
                "notice": notice}

    def mouse(percent, kb):
        return {"connected": True, "known": True, "name": "Synthetic mouse", "uinput": True, "rivals": [],
                "battery": [percent, device.BATTERY_DISCHARGING], "current_host": 0, "hosts": [], "dpi": 1000,
                "wheel_mode": "ratchet", "caps": None, "keyboard": kb}

    caps = {"seq": 1, "kind": "locks", "keys": {"caps_lock": True}}
    fn = {"seq": 2, "kind": "fn", "value": True}
    steps = [  # (name, state the engine reports, or a tray style chosen in the settings)
        ("mouse", mouse(80, keyboard(80))),
        ("mouse low", mouse(10, keyboard(80))),
        ("mouse low again", mouse(10, keyboard(80))),
        ("keyboard notice and low", mouse(10, keyboard(10, caps))),
        ("keyboard again", mouse(10, keyboard(10, caps))),
        ("battery style", "battery"),
        ("charged, fn notice", mouse(60, keyboard(60, fn))),
        ("both low", mouse(12, keyboard(9, fn))),
    ]

    def scenario():
        try:
            for name, what in steps:
                step["name"] = name
                if isinstance(what, str):
                    made["store"].set("app", "tray_icon", what)
                else:
                    made["listener"].on_state(what)
            seen["finished"] = True
        except BaseException as e:  # noqa: BLE001 (said in the result, as the hook says the slots')
            hook(type(e), e, None)
        finally:
            step["name"] = "quit"
            made["guard"].stop()
            QApplication.instance().quit()

    tray_icon.icon, tray_icon.install, tray_icon.announce = icon, lambda *_args: False, no_announce
    app.QSystemTrayIcon, app.Engine = Tray, Engine
    app._socket_name = lambda: f"mxoptions-test-{os.getpid()}"
    ring.create_overlay = lambda _parent=None: Overlay()
    sounds.WheelSounds, main_window.MainWindow, api.Service = Sounds, Window, Service
    flow_service.FlowService = front.Front = Side
    try:
        seen["status"] = app.main(["--background"])
    except BaseException as e:  # noqa: BLE001
        hook(type(e), e, None)
    finally:
        sys.excepthook, threading.excepthook = sys.__excepthook__, threading.__excepthook__
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, signal.SIG_DFL)
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(seen, f)


class Tray(unittest.TestCase):
    """The tray of the program as main() wires it, fed by the engine's state and the settings."""

    @classmethod
    def setUpClass(cls):
        home = tempfile.mkdtemp(prefix="mxapp.")
        cls.addClassCleanup(shutil.rmtree, home, True)
        env = {key: value for key, value in os.environ.items() if not key.startswith(("XDG_", "LC_", "MXOPTIONS_"))
               and key not in ("DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "WAYLAND_DISPLAY", "LANG", "LANGUAGE")}
        for name in ("config", "data", "cache", "state", "runtime", "tmp"):
            os.mkdir(os.path.join(home, name), 0o700)
        env.update(HOME=home, XDG_CONFIG_HOME=os.path.join(home, "config"), XDG_DATA_HOME=os.path.join(home, "data"),
                   XDG_CACHE_HOME=os.path.join(home, "cache"), XDG_STATE_HOME=os.path.join(home, "state"),
                   XDG_RUNTIME_DIR=os.path.join(home, "runtime"), TMPDIR=os.path.join(home, "tmp"),
                   QT_QPA_PLATFORM="offscreen", LANG="C.UTF-8")
        result = os.path.join(home, "result.json")
        code = f"import sys; sys.path.insert(0, {ROOT!r}); from tests.test_app import _child; _child(sys.argv[1])"
        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", code, result], cwd=home, env=env,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        try:
            out, _err = child.communicate(timeout=TIMEOUT)
            cls.timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            out, _err = child.communicate()
            cls.timed_out = True
        try:  # anything of its group still alive
            os.killpg(child.pid, signal.SIGKILL)
            cls.left = True
        except ProcessLookupError:
            cls.left = False
        cls.returncode, cls.output = child.returncode, out.decode("utf-8", "replace")
        try:
            with open(result, encoding="utf-8") as f:
                cls.seen = json.load(f)
        except (OSError, ValueError):
            cls.seen = None

    def result(self):
        self.assertIsNotNone(self.seen, self.output)
        return self.seen

    def test_main_runs_to_the_end_with_no_error_in_any_slot(self):
        seen = self.result()
        self.assertEqual((self.timed_out, self.left, self.returncode), (False, False, 0), self.output)
        self.assertEqual(seen["errors"], [], self.output)
        self.assertNotIn("Traceback", self.output)
        self.assertEqual((seen["finished"], seen["status"]), (True, 0), self.output)

    def test_the_icon_follows_the_mouse_and_the_style_and_is_drawn_only_when_it_changes(self):
        self.assertEqual(self.result()["icons"], [
            ["start", "outline", "outline-off"],
            ["mouse", "outline", "outline"],
            ["mouse low", "outline", "outline-low"],
            ["battery style", "battery", "battery-low"],
            ["charged, fn notice", "battery", "battery-3"],
            ["both low", "battery", "battery-low"],
        ], self.output)

    def test_each_notice_and_each_low_battery_is_said_once(self):
        self.assertEqual(self.result()["messages"], [
            ["mouse low", MOUSE_LOW.format(10), "warning"],
            ["keyboard notice and low", "Caps Lock on", "info"],
            ["keyboard notice and low", KEYBOARD_LOW.format(10), "warning"],
            ["charged, fn notice", "Fn lock on: the top row gives F1–F12", "info"],
            ["both low", KEYBOARD_LOW.format(9) + "\n" + MOUSE_LOW.format(12), "warning"],
        ], self.output)

    def test_the_tooltip_tells_both_devices(self):
        self.assertEqual(self.result()["tooltip"], "Synthetic mouse — 12%\nSynthetic keyboard — 9%", self.output)


if __name__ == "__main__":
    unittest.main()
