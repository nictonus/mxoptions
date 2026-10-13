"""What depends on the desktop besides the actions: the way to the clipboard, the watcher of the screen
edges, the kind of window the ring is drawn in. The desktops themselves are stand-ins here."""

import os
import stat
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402

import tests.support  # noqa: E402,F401
from mxoptions import clipboard, environment, flow, flow_edges  # noqa: E402
from mxoptions.ui import ring  # noqa: E402

APP = QGuiApplication.instance() or QGuiApplication([])
ONE = (0, 0, 1920, 1080)
TWO = [(0, 0, 1920, 1080), (1920, 200, 1280, 720)]  # a smaller screen to the right, lower down


class Desktop(unittest.TestCase):
    """Pretends to be on some desktop for the length of a test."""

    def pretend(self, desktop, session, offers=()):
        for module, name, value in ((environment, "current", lambda: desktop), (environment, "session", lambda: session),
                                    (environment, "wayland_globals", lambda timeout=1.0: frozenset(offers))):
            self.addCleanup(setattr, module, name, getattr(module, name))
            setattr(module, name, value)

    def without(self, *names):
        for name in names:
            if name in os.environ:
                self.addCleanup(os.environ.__setitem__, name, os.environ[name])
                del os.environ[name]


class WayToTheClipboard(Desktop):
    CONTROL = ("ext_data_control_manager_v1",)

    def setUp(self):
        self.without("MXOPTIONS_CLIPBOARD")
        self.addCleanup(setattr, clipboard, "KSystemClipboard", clipboard.KSystemClipboard)
        clipboard.KSystemClipboard = object()  # as if the bindings were installed

    def test_x11_is_qt_itself(self):
        self.pretend(environment.OTHER, "x11")
        self.assertEqual(clipboard._order()[0], "qt")

    def test_data_control_is_the_kde_service_where_its_bindings_are(self):
        self.pretend(environment.SWAY, "wayland", self.CONTROL)
        self.assertEqual(clipboard._order(), ["ksystem", "wl-clipboard", "klipper"])
        clipboard.KSystemClipboard = None
        self.assertEqual(clipboard._order(), ["wl-clipboard", "klipper"])

    def test_without_data_control_the_kde_service_would_see_nothing(self):
        self.pretend(environment.GNOME, "wayland")
        self.assertEqual(clipboard._order(), ["wl-clipboard", "klipper"])

    def test_one_can_be_named(self):
        self.pretend(environment.KDE, "wayland", self.CONTROL)
        os.environ["MXOPTIONS_CLIPBOARD"] = "klipper"
        self.addCleanup(os.environ.pop, "MXOPTIONS_CLIPBOARD", None)
        self.assertEqual(clipboard._order(), ["klipper"])
        os.environ["MXOPTIONS_CLIPBOARD"] = "none"
        self.assertEqual(clipboard._order(), [])

    @unittest.skipIf(sys.platform == "win32", "the lists of wl-clipboard hold Unix paths")
    def test_files_of_a_list(self):
        with tempfile.TemporaryDirectory() as folder:
            here = os.path.join(folder, "звіт 1.txt")
            open(here, "w").close()
            listed = f"# copied\r\nfile://{here.replace(' ', '%20')}\r\nfile:///no/such/file\r\nhttps://example.org/x\r\nfile://elsewhere/etc/hosts\r\n"
            self.assertEqual(clipboard._local_files(listed), [here])


WL_COPY = """#!/bin/sh
# stands in for wl-copy: keeps what it is given, and of which kind
kind=text/plain
while [ $# -gt 0 ]; do case "$1" in --type) kind="$2"; shift;; esac; shift; done
printf '%s' "$kind" > "$STORE/kind"
cat > "$STORE/data"
"""
WL_PASTE = """#!/bin/sh
# stands in for wl-paste
[ -f "$STORE/kind" ] || { echo "Nothing is copied" >&2; exit 1; }
kind=
while [ $# -gt 0 ]; do case "$1" in --list-types) echo "$(cat "$STORE/kind")"; [ "$(cat "$STORE/kind")" = text/uri-list ] && echo text/plain; exit 0;;
  --type) kind="$2"; shift;; esac; shift; done
[ "$kind" = "$(cat "$STORE/kind")" ] || [ "$kind" = text/plain ] || exit 1
cat "$STORE/data"
"""


@unittest.skipIf(sys.platform == "win32", "wl-clipboard is a Wayland thing")
class ThroughWlClipboard(unittest.TestCase):
    """The wl-clipboard way, with stand-ins for its two programs that keep to how those are called."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="mxoptions-test-")
        self.addCleanup(__import__("shutil").rmtree, self.folder, True)
        for name, text in (("wl-copy", WL_COPY), ("wl-paste", WL_PASTE)):
            path = os.path.join(self.folder, name)
            with open(path, "w") as f:
                f.write(text)
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        for name, value in (("PATH", self.folder + os.pathsep + os.environ["PATH"]), ("STORE", self.folder),
                            ("MXOPTIONS_CLIPBOARD", "wl-clipboard")):
            self.addCleanup(os.environ.__setitem__, name, os.environ[name]) if name in os.environ else self.addCleanup(os.environ.pop, name, None)
            os.environ[name] = value
        clipboard.reset()
        self.addCleanup(clipboard.reset)

    def test_nothing_copied(self):
        self.assertTrue(clipboard.available() and clipboard.rich())
        self.assertEqual(clipboard.read(), {"files": [], "image": None, "text": ""})

    def test_text(self):
        self.assertTrue(clipboard.write(text="привіт, wl-copy"))
        self.assertEqual(clipboard.read()["text"], "привіт, wl-copy")

    def test_a_picture(self):
        from PySide6.QtGui import QImage

        picture = QImage(3, 2, QImage.Format.Format_RGB32)
        picture.fill(0x336699)
        png = clipboard._png(picture)
        self.assertTrue(clipboard.write(image=png))
        self.assertEqual(clipboard.read()["image"], png)
        self.assertIsNone(clipboard.read(image_limit=10)["image"])  # larger than wanted: left out

    def test_files(self):
        paths = [os.path.join(self.folder, "a file.txt"), os.path.join(self.folder, "тека")]
        open(paths[0], "w").close()
        os.mkdir(paths[1])
        self.assertTrue(clipboard.write(files=paths))
        with open(os.path.join(self.folder, "data"), "rb") as f:
            self.assertIn(b"a%20file.txt\r\n", f.read())  # as a list of addresses, which is what file managers take
        self.assertEqual(clipboard.read()["files"], paths)

    def test_no_way_at_all(self):
        os.environ["MXOPTIONS_CLIPBOARD"] = "none"
        clipboard.reset()
        self.assertFalse(clipboard.available() or clipboard.rich())
        self.assertFalse(clipboard.write(text="x"))
        self.assertEqual(clipboard.read()["text"], "")


class EdgeGeometry(unittest.TestCase):
    def test_at_an_outer_edge(self):
        self.assertEqual(flow.at_edge("right", (1919, 500), [ONE]), (ONE, 0))
        self.assertEqual(flow.at_edge("right", (1915, 500), [ONE]), (ONE, 4))
        self.assertEqual(flow.at_edge("top", (5, 3), [ONE]), (ONE, 3))
        self.assertIsNone(flow.at_edge("right", (5000, 5), [ONE]))  # on no screen at all

    def test_an_edge_another_screen_continues_behind_is_none(self):
        self.assertIsNone(flow.at_edge("right", (1919, 500), TWO))
        self.assertEqual(flow.at_edge("right", (1919, 100), TWO), (TWO[0], 0))  # above where the second screen begins
        self.assertEqual(flow.at_edge("right", (3199, 500), TWO), (TWO[1], 0))

    def test_the_free_stretch_of_a_side(self):
        self.assertEqual(flow.outer_span("right", ONE, [ONE]), (0, 1080))
        self.assertEqual(flow.outer_span("right", TWO[0], TWO), (0, 200))   # the longer of the two free ends
        self.assertIsNone(flow.outer_span("left", TWO[1], TWO))
        self.assertEqual(flow.outer_span("top", TWO[1], TWO), (0, 1280))

    def test_where_the_lines_go(self):
        self.assertEqual(flow_edges.strip_places({"right"}, [ONE], 0.1), [("right", ONE, 108, 864)])
        self.assertEqual(flow_edges.strip_places({"top", "right"}, TWO, 0.0),
                         [("right", TWO[0], 0, 200), ("right", TWO[1], 0, 720), ("top", TWO[0], 0, 1920), ("top", TWO[1], 0, 1280)])
        self.assertEqual(flow_edges.strip_places({"right"}, TWO, 0.25), [("right", TWO[1], 180, 360)])  # the corners take the rest


class AskingWhereThePointerIs(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, flow_edges, "_fullscreen_in_front", flow_edges._fullscreen_in_front)
        flow_edges._fullscreen_in_front = lambda: False
        self.watcher = flow_edges.Poll()
        self.heard = []
        self.watcher.reported.connect(self.heard.append)
        self.watcher.watch(frozenset({"right"}))
        self.addCleanup(self.watcher.close)

    def look(self, *points):
        del self.heard[:]
        for point in points:
            self.watcher.look(point, [ONE])
        return [(report["at"], report.get("y")) for report in self.heard]

    def test_reaching_takes_touching_and_leaving_takes_a_few_pixels(self):
        self.assertTrue(self.watcher.watching)
        self.assertEqual(self.look((1915, 500)), [])               # near is not there
        self.assertEqual(self.look((1919, 500)), [(True, 500)])
        self.assertEqual(self.look((1916, 500), (1919, 503)), [])  # pushed back a little, moved a little: nothing new
        self.assertEqual(self.look((1919, 520)), [(True, 520)])    # slid along it
        self.assertEqual(self.look((1900, 520)), [(False, None)])
        self.assertEqual(self.look((1900, 600)), [])

    def test_a_full_screen_window_is_told(self):
        flow_edges._fullscreen_in_front = lambda: True
        self.watcher.look((1919, 500), [ONE])
        self.assertTrue(self.heard[0]["full"])
        self.assertEqual(self.heard[0]["screen"], list(ONE))

    def test_nothing_is_watched_once_stopped(self):
        self.watcher.watch(frozenset())
        self.assertFalse(self.watcher.watching)
        self.assertEqual(self.look((1919, 500)), [])


class WhichWatcherAndWhichWindow(Desktop):
    def setUp(self):
        self.without("MXOPTIONS_FLOW_EDGES", "MXOPTIONS_RING")
        self.addCleanup(os.environ.__setitem__, "DISPLAY", os.environ["DISPLAY"]) if "DISPLAY" in os.environ else \
            self.addCleanup(os.environ.pop, "DISPLAY", None)
        self.addCleanup(setattr, flow_edges, "kwin_running", flow_edges.kwin_running)
        flow_edges.kwin_running = lambda: False

    def test_kde(self):
        self.pretend(environment.KDE, "wayland", (flow_edges.LAYER_SHELL,))
        self.assertEqual((flow_edges._choice(), ring.overlay_kind()), ("kwin", ring.LAYER))
        self.pretend(environment.KDE, "x11")
        self.assertEqual((flow_edges._choice(), ring.overlay_kind()), ("kwin", ring.X11))

    def test_x11(self):
        self.pretend(environment.OTHER, "x11")
        self.assertEqual((flow_edges._choice(), ring.overlay_kind()), ("poll", ring.X11))

    def test_wayland_with_layers(self):
        self.pretend(environment.SWAY, "wayland", (flow_edges.LAYER_SHELL,))
        self.assertEqual((flow_edges._choice(), ring.overlay_kind()), ("strips", ring.LAYER))

    def test_wayland_without(self):
        self.pretend(environment.GNOME, "wayland", ("xdg_wm_base",))
        os.environ["DISPLAY"] = ":7"  # such a session keeps an X server for X11 programs: the ring goes there
        self.assertEqual((flow_edges._choice(), ring.overlay_kind()), (None, ring.XWAYLAND))
        self.assertIsNone(flow_edges.create())
        del os.environ["DISPLAY"]     # ... unless it was told not to keep one
        self.assertEqual(ring.overlay_kind(), ring.WINDOW)

    def test_each_can_be_named(self):
        self.pretend(environment.GNOME, "wayland")
        os.environ.update(MXOPTIONS_FLOW_EDGES="poll", MXOPTIONS_RING="x11")
        self.addCleanup(os.environ.pop, "MXOPTIONS_FLOW_EDGES", None)
        self.addCleanup(os.environ.pop, "MXOPTIONS_RING", None)
        self.assertEqual((flow_edges._choice(), ring.overlay_kind()), ("poll", ring.X11))
        os.environ["MXOPTIONS_FLOW_EDGES"] = "none"
        self.assertIsNone(flow_edges._choice())


@unittest.skipIf(sys.platform == "win32", "only GNOME needs the ring in a program of its own")
class RingInAProgramOfItsOwn(unittest.TestCase):
    """The overlay's own program and what stands for it here, talking as they do on GNOME; only the X server
    is left out (the other program draws to nowhere)."""

    def setUp(self):
        self.addCleanup(setattr, ring, "HELPER_PLATFORM", ring.HELPER_PLATFORM)
        ring.HELPER_PLATFORM = "offscreen"
        self.proxy = ring.RingProxy()
        self.addCleanup(self.proxy.shutdown)
        self.heard = []
        self.proxy.opened.connect(lambda on: self.heard.append(("opened", on)))
        self.proxy.cue.connect(lambda wave: self.heard.append(("cue", wave)))

    def wait(self, condition, seconds=15.0):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(0.01)
        return condition()

    def test_it_opens_and_closes_when_told_and_says_so(self):
        self.assertFalse(self.proxy.open())  # not there yet, and nothing to show
        self.proxy.set_look("graphite", "#2fd6a7", "en")
        self.proxy.configure({"size": 1.0, "style": "bubbles", "bubbles": [
            {"label": "", "icon": "", "color": "", "action": {"type": "keys", "keys": ["KEY_A"]}}]})
        self.assertTrue(self.wait(lambda: self.proxy.available), "the other program never said it was ready")
        self.assertTrue(self.proxy.open())
        self.assertTrue(self.wait(lambda: ("opened", True) in self.heard))
        self.assertFalse(self.proxy.open())  # open already
        self.proxy.handle("down")            # the button that opens the ring, pressed again, closes it
        self.assertTrue(self.wait(lambda: ("opened", False) in self.heard))
        self.assertTrue(any(kind == "cue" for kind, _value in self.heard))

    def test_a_ring_of_the_callers_own(self):
        self.assertTrue(self.wait(lambda: self.proxy.available))
        self.assertFalse(self.proxy.open())  # no ring of the user's has been told
        self.assertTrue(self.proxy.open([{"label": "x", "icon": "", "color": "", "action": {"type": "hook", "name": "x"}}]))
        self.assertTrue(self.wait(lambda: ("opened", True) in self.heard))

    def test_it_is_started_again_when_it_dies(self):
        self.assertTrue(self.wait(lambda: self.proxy.available))
        self.proxy.configure({"size": 1.0, "style": "pie", "bubbles": [
            {"label": "", "icon": "", "color": "", "action": {"type": "keys", "keys": ["KEY_A"]}}]})
        self.proxy._process.kill()
        self.assertTrue(self.wait(lambda: not self.proxy.available))
        self.assertTrue(self.wait(lambda: self.proxy.available), "it did not come back")
        self.assertTrue(self.proxy.open())   # and it was told the ring again
        self.assertTrue(self.wait(lambda: ("opened", True) in self.heard))


@unittest.skipIf(sys.platform == "win32", "there is one desktop on Windows")
class FindingTheCompositor(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="mxoptions-test-")
        self.addCleanup(__import__("shutil").rmtree, self.folder, True)
        kept = {name: os.environ.get(name) for name in ("XDG_RUNTIME_DIR", "XDG_CURRENT_DESKTOP", "MXOPTIONS_DESKTOP", "SWAYSOCK",
                                                        "HYPRLAND_INSTANCE_SIGNATURE")}
        self.addCleanup(lambda: [os.environ.pop(name, None) if value is None else os.environ.__setitem__(name, value)
                                 for name, value in kept.items()])
        self.addCleanup(setattr, environment, "_current", environment._current)
        for name in kept:
            os.environ.pop(name, None)
        os.environ["XDG_RUNTIME_DIR"] = self.folder
        environment._current = None

    def test_by_what_the_session_says(self):
        for said, desktop in (("KDE", environment.KDE), ("ubuntu:GNOME", environment.GNOME), ("sway", environment.SWAY),
                              ("Hyprland", environment.HYPRLAND), ("XFCE", environment.OTHER), ("", environment.OTHER)):
            os.environ["XDG_CURRENT_DESKTOP"] = said
            environment._current = None
            self.assertEqual(environment.current(), desktop, said)

    def test_by_the_socket_where_a_service_was_told_nothing(self):
        socket = os.path.join(self.folder, f"sway-ipc.{os.getuid()}.4242.sock")
        open(socket, "w").close()
        self.assertEqual(environment.current(), environment.SWAY)
        self.assertEqual(os.environ["SWAYSOCK"], socket)  # for the swaymsg run from here

    def test_hyprland_by_its_socket(self):
        os.makedirs(os.path.join(self.folder, "hypr", "abc_1700000000"))
        open(os.path.join(self.folder, "hypr", "abc_1700000000", ".socket.sock"), "w").close()
        self.assertEqual(environment.current(), environment.HYPRLAND)
        self.assertEqual(os.environ["HYPRLAND_INSTANCE_SIGNATURE"], "abc_1700000000")

    def test_it_can_be_named(self):
        os.environ.update(MXOPTIONS_DESKTOP="gnome", XDG_CURRENT_DESKTOP="KDE")
        self.assertEqual(environment.current(), environment.GNOME)

    def test_no_compositor_to_ask(self):
        self.addCleanup(setattr, environment, "_globals", environment._globals)
        self.addCleanup(os.environ.__setitem__, "WAYLAND_DISPLAY", os.environ["WAYLAND_DISPLAY"]) if "WAYLAND_DISPLAY" in os.environ else None
        os.environ["WAYLAND_DISPLAY"] = "wayland-none"
        environment._globals = None
        self.assertEqual(environment.wayland_globals(), frozenset())


if __name__ == "__main__":
    unittest.main()


class RawInputOfWindows(unittest.TestCase):
    """The records Windows hands out about a mouse, read as bytes (rawmouse_windows.py)."""

    def setUp(self):
        import struct

        from mxoptions import rawmouse_windows
        self.raw = rawmouse_windows
        self.record = lambda device=7, flags=0, buttons=0, data=0, dx=0, dy=0, kind=0: (
            struct.pack("<IIQQ", kind, 48, device, 0) + struct.pack("<HxxHhIiiI", flags, buttons, data, 0, dx, dy, 0))

    def test_a_movement(self):
        self.assertEqual(self.raw.parse(self.record(dx=5, dy=-3)), (7, 5, -3, 0, 0))

    def test_a_position_is_no_movement(self):
        self.assertEqual(self.raw.parse(self.record(flags=1, dx=30000, dy=20000))[1:3], (0, 0))

    def test_the_wheel_and_a_button(self):
        self.assertEqual(self.raw.parse(self.record(buttons=0x0400, data=-120))[3:], (0x0400, -120))
        self.assertEqual(self.raw.parse(self.record(buttons=0x0001))[3], 0x0001)

    def test_what_is_no_mouse_or_too_short_is_nothing(self):
        self.assertIsNone(self.raw.parse(self.record(kind=1)))
        self.assertIsNone(self.raw.parse(b"\0" * 20))

    def test_the_collections_of_one_mouse_go_by_one_name(self):
        hidpp_side = r"\\?\hid#{00001812-0000-1000-8000-00805f9b34fb}_dev_vid&02046d_pid&b042_rev&0017_0123456789ab&col02#a&1b2c3d4&0&0001#{4d1e55b2}"
        mouse_side = r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}_Dev_VID&02046d_PID&b042_REV&0017_0123456789ab&Col01#a&1b2c3d4&0&0000#{378de44c}"
        other = r"\\?\HID#VID_06CB&PID_CE7E&Col01#5&2a3b&0&0000#{378de44c}"
        self.assertEqual(self.raw.device_key(hidpp_side), self.raw.device_key(mouse_side))
        self.assertNotEqual(self.raw.device_key(hidpp_side), self.raw.device_key(other))
        self.assertEqual(self.raw.device_key(r"\\?\hid#vid_046d&pid_c548&mi_02&col02#8&1&0&1#{4d1e}"),
                         self.raw.device_key(r"\\?\HID#VID_046D&PID_C548&MI_00#8&2&0&0#{378d}"))

    def test_what_a_hand_does_is_told_and_what_a_program_sends_is_not(self):
        told = []
        mouse = self.raw.RawMouse.__new__(self.raw.RawMouse)  # the part that makes sense of records, without the window
        mouse._key, mouse._any, mouse._buttons, mouse._wheel, mouse._devices = "k", False, 0, 0, {7: True, 9: False}
        mouse._on_change = lambda _m, bits: told.append(("buttons", bits))
        mouse._on_wheel = lambda _m, notches, _when: told.append(("wheel", notches))
        mouse.on_motion, mouse.last_input = (lambda _m, dx, dy, _when: told.append(("moved", dx, dy))), None
        mouse._record(self.record(dx=4, dy=1))
        mouse._record(self.record(buttons=0x0001))          # left down
        mouse._record(self.record(buttons=0x0002 | 0x0100)) # left up, forward down
        mouse._record(self.record(buttons=0x0400, data=-60))  # half a notch
        mouse._record(self.record(buttons=0x0400, data=-60))  # the other half
        mouse._record(self.record(device=0, dx=50))         # sent by a program
        mouse._record(self.record(device=9, dx=50))         # another mouse
        self.assertEqual(told, [("moved", 4, 1), ("buttons", 1), ("buttons", 16), ("wheel", -1)])


class RumbleOnWindows(unittest.TestCase):
    def test_what_two_motors_come_to(self):
        from mxoptions import rumble_windows

        self.assertEqual(rumble_windows.strength_of(0, 0), 0.0)
        self.assertEqual(rumble_windows.strength_of(255, 0), 1.0)
        self.assertAlmostEqual(rumble_windows.strength_of(0, 255), 0.6)   # the small motor counts for less
        self.assertAlmostEqual(rumble_windows.strength_of(128, 255), 0.6)
        self.assertIsNone(rumble_windows.create(lambda level: None) if sys.platform != "win32" else None)
