"""Which app is in front (front.py), and the app profiles of the keyboard as other programs and the Keys page
change them. The desktops are stood in for: what each says is made up here, as its documentation describes it."""

import copy
import itertools
import json
import os
import stat
import tempfile
from pathlib import Path
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import tests.support  # noqa: F401,E402
from tests.test_config import BAD_PROFILES, deep  # noqa: E402
from mxoptions import actions, api, config, environment, flow_edges, front, x11  # noqa: E402
from mxoptions.ui import keyboard_page  # noqa: E402
from mxoptions.ui.store import Store  # noqa: E402

APP = QApplication.instance() or QApplication([])
NATIVE = {"type": "default"}
COPY = actions.keys("LEFTCTRL", "C")


def focus(**container):
    return {"change": "focus", "container": container}


class Engine:
    """Stands in for the engine: what it was told of the app in front."""

    def __init__(self):
        self.fronts = []

    def set_front(self, app):
        self.fronts.append(app)

    def __getattr__(self, name):
        return lambda *args: None


class Names(unittest.TestCase):
    def test_what_an_app_may_go_by(self):
        self.assertEqual(front.app_name("Firefox"), "firefox")
        self.assertEqual(front.app_name("org.kde.Kate"), "org.kde.kate")
        for bad in ("", "a\nb", "a\u200bb", "x" * 129, None, 3, b"firefox", ["firefox"]):
            with self.subTest(bad=bad):
                self.assertIsNone(front.app_name(bad))

    def test_sway(self):
        self.assertEqual(front.sway_app(focus(app_id="org.gnome.Nautilus", window_properties={"class": "x"})), "org.gnome.nautilus")
        self.assertEqual(front.sway_app(focus(app_id=None, window_properties={"class": "URxvt"})), "urxvt")  # Xwayland
        self.assertEqual(front.sway_app(focus(app_id=None)), "")  # focused, but nothing to name it by
        self.assertEqual(front.sway_app(focus(app_id="bad\x01id")), "")
        for other in ({"change": "title", "container": {"app_id": "kate"}}, {"success": True}, {"change": "focus"},
                      {"change": "focus", "container": "kate"}, [], "focus", None, 3, deep(50)):
            with self.subTest(other=other):
                self.assertIsNone(front.sway_app(other))
        self.assertEqual(front.sway_app(focus(app_id=["kate"], window_properties=[])), "")
        self.assertEqual(front.sway_app(focus(app_id=True, window_properties={"class": {"x": 1}})), "")

    def test_hyprland_keeps_the_class_and_never_the_title(self):
        self.assertEqual(front.hyprland_app("activewindow>>kitty,~/secret, folder>>x"), "kitty")
        self.assertEqual(front.hyprland_app("activewindow>>firefox,"), "firefox")
        self.assertEqual(front.hyprland_app("activewindow>>Firefox"), "firefox")
        self.assertEqual(front.hyprland_app("activewindow>>,"), "")  # no window
        self.assertEqual(front.hyprland_app("activewindow>>"), "")
        for other in ("activewindowv2>>5d1e0c80", "workspace>>2", "activewindow", "", "openwindow>>a,b,kitty,title"):
            with self.subTest(other=other):
                self.assertIsNone(front.hyprland_app(other))

    def test_kwin_says_it_over_the_bus(self):
        sink, heard = front._FrontSink(), []
        sink.heard.connect(heard.append)
        for said in ("org.kde.dolphin", "Firefox", "", "a\x00b", "x" * 128, "x" * 129, "x" * 5000):
            sink.Front(said)
        self.assertEqual(heard, ["org.kde.dolphin", "firefox", "", "", "x" * 128, "", ""])
        self.assertIn("workspace.windowActivated.connect", front._SCRIPT)
        self.assertIn("resourceClass", front._SCRIPT)
        self.assertNotIn("caption", front._SCRIPT)  # the title is never sent

    def test_x11_takes_the_class_of_wm_class(self):
        lib = mock.Mock()
        with mock.patch.object(x11, "_open", lambda: object()), mock.patch.object(x11, "_lib", lib, create=True), \
                mock.patch.object(x11, "_property", lambda window, name: [42]):
            for raw, class_ in ((b"navigator\x00Firefox\x00", "Firefox"), (b"xterm\x00", None), (b"", None),
                                (b"a\x00\x00", None)):
                with self.subTest(raw=raw), mock.patch.object(x11, "_string", lambda window, name, raw=raw: raw):
                    self.assertEqual(x11.active_class(), class_)
            with mock.patch.object(x11, "_string", lambda window, name: b"navigator\x00Firefox\x00"):
                self.assertEqual(front._x11(), "firefox")
        with mock.patch.object(x11, "_open", lambda: object()), mock.patch.object(x11, "_lib", lib, create=True), \
                mock.patch.object(x11, "_property", lambda window, name: [0]):
            self.assertIsNone(x11.active_class())  # no window has the focus

    def test_windows_goes_by_the_program_s_file(self):
        self.assertEqual(front.windows_app("C:\\Program Files\\Mozilla Firefox\\firefox.exe"), "firefox.exe")
        self.assertEqual(front.windows_app("C:\\Windows\\System32\\NOTEPAD.EXE"), "notepad.exe")
        self.assertIsNone(front.windows_app(None))
        self.assertIsNone(front.windows_app(""))


class Choice(unittest.TestCase):
    def choose(self, session, desktop, env=None, kwin=False, which="/usr/bin/swaymsg"):
        with mock.patch.object(environment, "session", lambda: session), \
                mock.patch.object(environment, "current", lambda: desktop), \
                mock.patch.object(flow_edges, "kwin_running", lambda: kwin), \
                mock.patch.object(front.shutil, "which", lambda name: which), \
                mock.patch.dict(os.environ, env or {}, clear=True):
            return front._choice()

    def test_each_desktop(self):
        self.assertEqual(self.choose("windows", environment.WINDOWS), "windows")
        self.assertEqual(self.choose("wayland", environment.KDE), "kwin")
        self.assertEqual(self.choose("x11", environment.OTHER, kwin=True), "kwin")
        self.assertEqual(self.choose("wayland", environment.SWAY, {"SWAYSOCK": "/run/sway.sock"}), "sway")
        self.assertIsNone(self.choose("wayland", environment.SWAY, {"SWAYSOCK": "/run/sway.sock"}, which=None))
        self.assertEqual(self.choose("wayland", environment.HYPRLAND, {"HYPRLAND_INSTANCE_SIGNATURE": "s",
                                                                        "XDG_RUNTIME_DIR": "/run/user/1"}), "hyprland")
        self.assertEqual(self.choose("x11", environment.OTHER), "x11")
        self.assertEqual(self.choose("x11", environment.GNOME), "x11")

    def test_gnome_on_wayland_has_none_and_nothing_is_asked(self):
        self.assertIsNone(self.choose("wayland", environment.GNOME))
        self.assertIsNone(self.choose("wayland", environment.OTHER))
        engine = Engine()
        made = front.Front(engine, kind=None)
        with mock.patch.object(front, "_make", side_effect=AssertionError("asked")):
            made.want("profiles", True)
            made.want("page", True)
        self.assertFalse(made.supported)
        self.assertEqual(engine.fronts, [])


class FakeProcess(QObject):
    readyReadStandardOutput = Signal()
    finished = Signal(int)
    errorOccurred = Signal(object)
    made = []

    def __init__(self, parent=None):
        super().__init__(parent)
        self.chunks, self.started, self.killed = [], None, False
        FakeProcess.made.append(self)

    @staticmethod
    def nullDevice():
        return os.devnull

    def setStandardErrorFile(self, path):
        self.stderr = path

    def start(self, program, arguments):
        self.started = (program, arguments)

    def readAllStandardOutput(self):
        out = b"".join(self.chunks)
        self.chunks.clear()
        return out

    def feed(self, data):
        self.chunks.append(data)
        self.readyReadStandardOutput.emit()

    def kill(self):
        self.killed = True

    def waitForFinished(self, _ms):
        return True


class FakeSocket(QObject):
    readyRead = Signal()
    disconnected = Signal()
    errorOccurred = Signal(object)
    made = []

    def __init__(self, parent=None):
        super().__init__(parent)
        self.chunks, self.path, self.aborted = [], None, False
        FakeSocket.made.append(self)

    def connectToServer(self, path):
        self.path = path

    def readAll(self):
        out = b"".join(self.chunks)
        self.chunks.clear()
        return out

    def feed(self, data):
        self.chunks.append(data)
        self.readyRead.emit()

    def abort(self):
        self.aborted = True


class Sources(unittest.TestCase):
    def setUp(self):
        FakeProcess.made.clear()
        FakeSocket.made.clear()
        for patcher in (mock.patch.object(front, "QProcess", FakeProcess), mock.patch.object(front, "QLocalSocket", FakeSocket),
                        mock.patch.object(front.QGuiApplication, "applicationState",
                                          lambda: Qt.ApplicationState.ApplicationInactive)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.engine = Engine()

    def test_sway_events_one_after_another_however_they_come(self):
        made = front.Front(self.engine, kind="sway")
        made.want("profiles", True)
        process = FakeProcess.made[-1]
        self.assertEqual(process.started, ("swaymsg", ["-r", "-t", "subscribe", "-m", '["window"]']))
        process.feed(b'{"success": true}\n')
        event = json.dumps(focus(app_id="org.kde.kate", name="a title")).encode()
        process.feed(event[:20])  # half an event: waited for
        self.assertEqual(self.engine.fronts, [])
        process.feed(event[20:] + b"\n" + json.dumps(focus(app_id=None, window_properties={"class": "Gimp"}), indent=2).encode())
        process.feed(b"\n" + json.dumps({"change": "title", "container": {"app_id": "x"}}).encode())
        process.feed(b"\n" + json.dumps(focus(app_id=None)).encode())
        self.assertEqual(self.engine.fronts, ["org.kde.kate", "gimp", None])
        self.assertEqual(made.last_other, "gimp")
        process.feed(b"{" * 10 + b"x" * (front.MAX_PENDING + 1))  # never ends: dropped, not kept growing
        self.assertEqual(made._source._buffer, "")
        process.feed(b"\n" + event)
        self.assertEqual(self.engine.fronts[-1], "org.kde.kate")

    def test_hyprland_lines(self):
        made = front.Front(self.engine, kind="hyprland")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1", "HYPRLAND_INSTANCE_SIGNATURE": "sig"}):
            made.want("profiles", True)
        socket = FakeSocket.made[-1]
        self.assertEqual(socket.path, "/run/user/1/hypr/sig/.socket2.sock")
        socket.feed(b"workspace>>2\nactivewindow>>kitty,my secret title\nactivewindowv2>>5d1e")
        self.assertEqual(self.engine.fronts, ["kitty"])
        self.assertNotIn(b"secret", made._source._pending)  # only the start of a line still to come is waited for
        socket.feed(b"0c80\nactivewindow>>Firefox,a, b, c\nactivewindow>>,\n")
        self.assertEqual(self.engine.fronts, ["kitty", "firefox", None])
        self.assertEqual(made._source._pending, b"")

    def test_while_nothing_needs_it_nothing_is_asked_and_nothing_heard_after(self):
        made = front.Front(self.engine, kind="sway")
        self.assertEqual(FakeProcess.made, [])
        made.want("page", True)
        made.want("profiles", True)
        made.want("page", False)
        self.assertEqual(len(FakeProcess.made), 1)  # still wanted: the same one
        process = FakeProcess.made[0]
        process.feed(json.dumps(focus(app_id="kate")).encode())
        made.want("profiles", False)
        self.assertTrue(process.killed)
        self.assertEqual(self.engine.fronts, ["kate", None])  # no app known any more
        self.assertIsNone(made.last_other)
        process.feed(json.dumps(focus(app_id="gimp")).encode())  # what it says after: not heard
        process.finished.emit(0)
        QCoreApplication.processEvents()
        self.assertEqual(self.engine.fronts, ["kate", None])
        made.want("profiles", True)
        hypr = front.Front(Engine(), kind="hyprland")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run", "HYPRLAND_INSTANCE_SIGNATURE": "s"}):
            hypr.want("profiles", True)
        hypr.close()
        socket = FakeSocket.made[-1]
        self.assertTrue(socket.aborted)
        socket.feed(b"activewindow>>kitty,t\n")
        self.assertEqual(hypr.engine.fronts, [None])
        made.close()

    def test_a_source_that_ends_is_started_again(self):
        made = front.Front(self.engine, kind="sway")
        made._restart.setInterval(10)
        made.want("profiles", True)
        first = FakeProcess.made[-1]
        first.feed(json.dumps(focus(app_id="kate")).encode())
        first.errorOccurred.emit(None)
        first.finished.emit(1)  # both: told once
        QCoreApplication.processEvents()
        self.assertEqual(self.engine.fronts, ["kate", None])
        self.assertIsNone(made._source)
        QTest.qWait(60)
        self.assertEqual(len(FakeProcess.made), 2)
        made.close()
        QTest.qWait(30)
        self.assertEqual(len(FakeProcess.made), 2)  # closed: not again

    def test_polling_and_this_program_itself(self):
        looks = iter(["firefox", "firefox", "mxoptions", ""])
        heard = []
        poll = front.Poll(lambda: next(looks, ""))
        poll.seen.connect(heard.append)
        for _tick in range(4):
            poll._tick()
        self.assertEqual(heard, ["firefox", "mxoptions", ""])
        poll.close()
        poll._tick()
        self.assertEqual(heard, ["firefox", "mxoptions", ""])
        failing = front.Poll(mock.Mock(side_effect=OSError("gone")))
        failing.seen.connect(heard.append)
        failing._tick()
        self.assertEqual(heard[-1], "")
        failing.close()
        made = front.Front(self.engine, kind="sway")
        made.want("page", True)
        process = FakeProcess.made[-1]
        process.feed(json.dumps(focus(app_id="mxoptions")).encode())
        self.assertIsNone(made.last_other)  # not offered for a profile of its own
        with mock.patch.object(front.QGuiApplication, "applicationState", lambda: Qt.ApplicationState.ApplicationActive):
            process.feed(json.dumps(focus(app_id="python3")).encode())
        self.assertIsNone(made.last_other)  # this program's window, under whatever name
        self.assertEqual(self.engine.fronts, ["mxoptions", "python3"])  # its keys count all the same
        made.close()

    def test_the_app_never_goes_to_the_log(self):
        made = front.Front(self.engine, kind="sway")
        with self.assertLogs("mxoptions", "DEBUG") as logs:
            made.want("profiles", True)
            FakeProcess.made[-1].feed(json.dumps(focus(app_id="secretapp")).encode())
            made.close()
        self.assertFalse([line for line in logs.output if "secretapp" in line])


class ScriptPath(unittest.TestCase):
    def test_kwin_uses_a_private_script_and_never_follows_the_old_path(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "existing-file"
            target.write_text("keep")
            old_path = Path(folder) / (front.SCRIPT_NAME + ".js")
            old_path.symlink_to(target)
            bus = mock.Mock()
            bus.baseService.return_value = ":1.synthetic"
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}), \
                    mock.patch.object(front.tempfile, "gettempdir", return_value=folder), \
                    mock.patch.object(front.QDBusConnection, "sessionBus", return_value=bus), \
                    mock.patch.object(front, "QDBusServiceWatcher"), \
                    mock.patch.object(front.flow_edges, "_kwin", return_value=1) as loader:
                source = front.KWin()
                try:
                    self.assertEqual(target.read_text(), "keep")
                    script = Path(next(call.args[1] for call in loader.call_args_list if call.args[0] == "loadScript"))
                    self.assertNotEqual(script, old_path)
                    self.assertEqual(stat.S_IMODE(script.parent.stat().st_mode), 0o700)
                    self.assertIn("windowActivated", script.read_text())
                    source._load()
                    self.assertEqual(target.read_text(), "keep")
                finally:
                    source.close()
                self.assertFalse(script.exists())
                self.assertTrue(old_path.is_symlink())


class Settings(unittest.TestCase):
    """keyboard.profiles through Set(), and the Store as the Keys page changes it."""

    def setUp(self):
        self.store = Store(copy.deepcopy(config.DEFAULTS), Engine())
        self.store.flush = lambda: None
        self.store._save_timer.timeout.disconnect()
        self.api = api.MouseApi(self.store, None, lambda: None)

    def profiles(self):
        return self.store.cfg["keyboard"]["profiles"]

    def test_set_takes_profiles_as_a_whole(self):
        value = [{"app": "kate", "name": "Kate", "keys": {"0x0f01": NATIVE, "0x0f02": COPY, "0x0f03": {"type": "none"}}}]
        self.assertTrue(self.api.Set("keyboard.profiles", json.dumps(value)))
        self.assertEqual(self.profiles(), value)
        self.assertEqual(json.loads(self.api.Get("keyboard.profiles.0.keys.0x0f01")), NATIVE)
        self.assertTrue(self.api.Set("keyboard.profiles.0.keys.0x0f02", json.dumps(NATIVE)))
        self.assertTrue(self.api.Set("keyboard.profiles", "[]"))
        self.assertEqual(self.profiles(), [])

    def test_anything_malformed_is_refused_whole(self):
        good = {"app": "kate", "name": "Kate", "keys": {}}
        self.api.Set("keyboard.profiles", json.dumps([good]))
        before = copy.deepcopy(self.store.cfg)
        bad_keys = [{"0x0F01": COPY}, {"0x0f01": {"type": "default", "x": 1}}, {"0x0f01": {"type": "gestures"}},
                    {"0x0f01": {"type": "host", "host": True}}, {"0x0f01": []}, {"0x0f01": True}, {"0x0f01": None},
                    {config.key_id(cid): COPY for cid in range(1, 130)}]
        values = [[good, bad] for bad in BAD_PROFILES] + [[good, good], [good] * 33] + [[dict(good, keys=k)] for k in bad_keys]
        values += [[dict(good, app=f"a{n}") for n in range(33)], {"app": "kate"}, "kate", True, 1, None, deep(40)]
        for value in values:
            with self.subTest(value=json.dumps(value)[:80]):
                self.assertFalse(self.api.Set("keyboard.profiles", json.dumps(value)))
                self.assertEqual(self.store.cfg, before)
        for text in ("[" * 100000 + "]" * 100000, "[" * 3000 + "]" * 3000, "{broken", '[{"app": "kate"'):
            with self.subTest(text=text[:20]):
                self.assertFalse(self.api.Set("keyboard.profiles", text))
        self.assertFalse(self.api.Set("keyboard.keys", json.dumps({"0x0f01": NATIVE})))  # for profiles only
        self.assertFalse(self.api.Set("keyboard.profiles.0.app", '"Kate"'))
        self.assertFalse(self.api.Set("keyboard.profiles.0.keys", json.dumps({"0x0f01": {"type": "flow"}})))
        self.assertEqual(self.store.cfg, before)

    def test_the_store_as_the_page_uses_it(self):
        store = self.store
        for bad in ("Kate", "", "x" * 129, "a\tb", None, 3):
            self.assertFalse(store.add_profile(bad))
        self.assertTrue(store.add_profile("kate"))
        self.assertFalse(store.add_profile("kate"))  # one each
        self.assertEqual(self.profiles(), [{"app": "kate", "name": "kate", "keys": {}}])
        self.assertTrue(store.set_profile_key("kate", "0x0f01", NATIVE))
        self.assertTrue(store.set_profile_key("kate", "0x0f02", COPY))
        self.assertTrue(store.set_profile_key("kate", "0x0f02", None))  # as in all apps
        self.assertTrue(store.set_profile_key("kate", "0x0f03", {"type": "ring"}))
        self.assertTrue(store.set_profile_key("kate", "0x0f03", None))
        for key, action in (("0x0F01", COPY), ("0x0f01", {"type": "default", "x": 1}), ("0x0f01", {"type": "ring", "x": 1}),
                            ("0x0f01", {"type": "gestures"}), ("0x0f01", [])):
            self.assertFalse(store.set_profile_key("kate", key, action))
        self.assertTrue(store.set_key("0x0f04", {"type": "ring"}))
        self.assertFalse(store.set_key("0x0f04", {"type": "ring", "x": 1}))
        self.assertEqual(store.cfg["keyboard"]["keys"], {"0x0f04": {"type": "ring"}})
        self.assertTrue(store.set_key("0x0f04", {"type": "default"}))
        self.assertFalse(store.set_profile_key("gimp", "0x0f01", COPY))  # no profile
        self.assertEqual(self.profiles()[0]["keys"], {"0x0f01": NATIVE})
        for n in range(config.MAX_PROFILES - 1):
            self.assertTrue(store.add_profile(f"app{n}"))
        self.assertFalse(store.add_profile("one-too-many"))
        store.remove_profile("app0")
        self.assertIsNone(store.profile("app0"))
        store.set_key("0x0f05", COPY)
        for notices, locks, fn in itertools.product((False, True), repeat=3):  # the keys go back, never which notices
            store.set("keyboard", "notices", notices)                          # are shown
            store.set("keyboard", "lock_notices", locks)
            store.set("keyboard", "fn_notices", fn)
            store.set_key("0x0f05", COPY)
            store.restore_keyboard()
            self.assertEqual(store.cfg["keyboard"], {"keys": {}, "profiles": [], "notices": notices,
                                                     "lock_notices": locks, "fn_notices": fn})
            self.assertEqual(config.clean(copy.deepcopy(store.cfg)), store.cfg)
        store.restore_defaults()  # all the settings back as they were at first: every notice off, on purpose
        self.assertEqual(store.cfg["keyboard"], {"keys": {}, "profiles": [], "notices": False, "lock_notices": False,
                                                 "fn_notices": False})


class FakeFront(QObject):
    changed = Signal()

    def __init__(self, supported=True):
        super().__init__()
        self.supported, self.last_other, self.wanted = supported, None, []

    def want(self, who, on):
        self.wanted.append((who, on))


class Page(unittest.TestCase):
    """The Keys page: whose keys are shown, and a key of one app set to its own function or to every app's."""

    def keys_page(self, supported=True):
        self.store = Store(copy.deepcopy(config.DEFAULTS), Engine())
        self.store.flush = lambda: None
        self.store._save_timer.timeout.disconnect()
        self.store.front = FakeFront(supported)
        self.store.set_key("0x0f01", COPY)
        page = keyboard_page.KeysPage(self.store)
        self.addCleanup(page.deleteLater)
        page.resize(1100, 600)
        page.show()
        keys = [{"id": config.key_id(cid), "cid": cid, "tid": cid, "protected": False, "active": True, "native_reason": None}
                for cid in (0x0F01, 0x0F02)]
        self.store.update_state(dict(self.store.state, keyboard={"connected": True, "known": True, "name": "K", "battery": None,
                                                                 "current_host": 0, "hosts": [], "keys": keys,
                                                                 "reason": None, "failed": None}))
        return page

    def apps(self, page):
        return [page.apps.itemText(i) for i in range(page.apps.count())]

    def test_an_app_is_added_and_its_keys_set(self):
        page = self.keys_page()
        self.assertIn(("page", True), self.store.front.wanted)
        self.assertEqual(self.apps(page), ["All apps"])
        self.assertFalse(page.add_app.isEnabled())  # no other app seen in front yet
        self.assertFalse(page.remove_app.isEnabled())
        self.store.front.last_other = "kate"
        self.store.front.changed.emit()
        self.assertTrue(page.add_app.isEnabled())
        self.assertIn("kate", page.apps_hint.text())
        QTest.mouseClick(page.add_app, Qt.MouseButton.LeftButton)
        self.assertEqual(self.store.profile("kate"), {"app": "kate", "name": "kate", "keys": {}})
        self.assertEqual((self.apps(page), page.apps.currentIndex()), (["All apps", "kate"], 1))
        self.assertFalse(page.add_app.isEnabled())  # it has its keys already
        self.assertEqual(page._key["id"], "0x0f01")
        self.assertFalse(page.picker.list.inherit_row.isHidden())
        self.assertTrue(page.picker.list.inherit_row.isChecked())  # nothing of its own yet
        self.assertEqual(page.strip.caps["0x0f01"].accessibleDescription(), actions.describe(COPY))  # as in all apps
        page.picker.changed.emit(copy.deepcopy(NATIVE))  # "Default"
        self.assertEqual(self.store.profile("kate")["keys"], {"0x0f01": NATIVE})
        self.assertEqual(self.store.cfg["keyboard"]["keys"], {"0x0f01": COPY})  # every other app: unchanged
        self.assertEqual(page.strip.caps["0x0f01"].accessibleDescription(), actions.describe(NATIVE))
        page.picker.list.inherit_row.click()  # "As in all apps"
        self.assertEqual(self.store.profile("kate")["keys"], {})
        page.apps.setCurrentIndex(0)
        self.assertTrue(page.picker.list.inherit_row.isHidden())  # all apps: there is nothing to take it from
        page.picker.changed.emit(actions.keys("LEFTCTRL", "V"))
        self.assertEqual(self.store.cfg["keyboard"]["keys"]["0x0f01"], actions.keys("LEFTCTRL", "V"))
        page.apps.setCurrentIndex(1)
        with mock.patch.object(keyboard_page.QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes):
            QTest.mouseClick(page.remove_app, Qt.MouseButton.LeftButton)
        self.assertEqual(self.store.cfg["keyboard"]["profiles"], [])
        self.assertEqual((self.apps(page), page.apps.currentIndex()), (["All apps"], 0))
        page.hide()
        self.assertEqual(self.store.front.wanted[-1], ("page", False))

    def test_a_key_is_given_the_actions_ring_by_its_name_in_each_language(self):
        from mxoptions import i18n
        self.addCleanup(i18n.setup, "en")
        for language, label in (("en", "Actions Ring"), ("uk", "Кільце дій")):
            with self.subTest(language=language):
                i18n.setup(language)
                page = self.keys_page()
                row = page.picker.list._rows["ring"]
                self.assertEqual(row.text(), label)
                row.click()
                self.assertEqual(self.store.cfg["keyboard"]["keys"]["0x0f01"], {"type": "ring"})
                self.assertEqual(page.strip.caps["0x0f01"].accessibleDescription(), label)
                for hidden in ("gestures", "wheel_mode", "dpi_cycle", "dpi_shift", "flow"):
                    self.assertNotIn(hidden, page.picker.list._rows)

    def test_where_the_desktop_does_not_say_the_page_says_why(self):
        page = self.keys_page(supported=False)
        self.store.front.last_other = "kate"
        self.store.cfg["keyboard"]["profiles"] = [{"app": "kate", "name": "Kate", "keys": {"0x0f01": NATIVE}}]
        self.store.changed.emit()
        self.assertFalse(page.apps.isEnabled())
        self.assertFalse(page.add_app.isEnabled())
        self.assertEqual(self.apps(page), ["All apps"])
        self.assertIn("does not tell", page.apps_hint.text())
        self.assertTrue(page.picker.list.inherit_row.isHidden())

    def test_restoring_the_keyboard_s_defaults_clears_every_app_s_keys(self):
        store = Store(copy.deepcopy(config.DEFAULTS), Engine())
        store.flush = lambda: None
        store._save_timer.timeout.disconnect()
        store.set_key("0x0f01", COPY)
        store.add_profile("kate")
        store.set("keyboard", "notices", True)
        store.set("keyboard", "lock_notices", True)
        page = keyboard_page.KeyboardPage(store)
        self.addCleanup(page.deleteLater)
        asked = []
        with mock.patch.object(keyboard_page.QMessageBox, "question",
                               lambda _parent, _title, text: (asked.append(text), QMessageBox.StandardButton.No)[1]):
            page._reset()
        self.assertEqual(store.profile("kate")["app"], "kate")  # not without a yes
        self.assertIn("single apps", asked[0])
        with mock.patch.object(keyboard_page.QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes):
            page._reset()
        self.assertEqual(store.cfg["keyboard"], {"keys": {}, "profiles": [], "notices": True, "lock_notices": True,
                                                 "fn_notices": False})  # notices stay
        self.assertEqual(config.clean(copy.deepcopy(store.cfg)), store.cfg)
        for notices, locks, fn in itertools.product((False, True), repeat=3):
            choice = {"notices": notices, "lock_notices": locks, "fn_notices": fn}
            for name, on in choice.items():
                store.set("keyboard", name, on)
            store.set_key("0x0f01", COPY)
            with mock.patch.object(keyboard_page.QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes):
                page._reset()
            self.assertEqual(store.cfg["keyboard"], dict(choice, keys={}, profiles=[]))
            self.assertEqual((page.notices.isChecked(), page.lock_notices.isChecked(), page.fn_notices.isChecked()),
                             (notices, locks, fn))  # the switches follow, and changed nothing themselves


if __name__ == "__main__":
    unittest.main()
