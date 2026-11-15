"""What depends on the desktop besides the actions: the way to the clipboard, the watcher of the screen
edges, the kind of window the ring is drawn in. The desktops themselves are stand-ins here."""

import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

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


A, B = (0, 0, 1920, 1080), (1920, 0, 1280, 1024)  # two screens side by side
SIDE = (A, B)


class ScreenCrossing(unittest.TestCase):
    """Which screen the pointer is on, a watcher's word of it, and when going onto another one is a tick."""

    def test_the_screen_a_point_is_on(self):
        self.assertEqual(flow.screen_at((1919, 500), SIDE), A)
        self.assertEqual(flow.screen_at((1920, 500), SIDE), B)
        self.assertIsNone(flow.screen_at((2000, 1050), SIDE))  # below the smaller screen: on none
        self.assertEqual(flow.screen_at((5, 5), [list(A), list(A)]), A)  # mirrored: the first

    def test_a_report_is_taken_only_as_a_watcher_makes_it(self):
        good = {"on": list(B), "screens": [list(A), list(B)]}
        self.assertEqual(flow.screen_report(good), (B, SIDE))
        self.assertEqual(flow.screen_report(dict(good, on=None)), (None, SIDE))
        for bad in (None, [], "x", {}, {"screens": []}, {"screens": [list(A)] * 17}, {"screens": list(A)},
                    {"on": list(A), "screens": [[0, 0, 1920]]}, {"on": None, "screens": [[0, 0, True, 1080]]},
                    {"on": None, "screens": [[0, 0, "1920", 1080]]}, {"on": None, "screens": [[0, 0, float("nan"), 1080]]},
                    {"on": None, "screens": [[0, float("inf"), 1920, 1080]]}, {"on": [1, 2, 3, 4], "screens": [list(A)]},
                    {"on": list(A), "screens": [list(B)]}):
            with self.subTest(bad=bad):
                self.assertIsNone(flow.screen_report(bad))

    def test_numbers_no_screen_can_have_are_refused_without_error(self):
        huge = 10 ** 400  # JSON lets an integer have any number of digits; no float holds it
        for rect in ([huge, 0, 1920, 1080], [-huge, 0, 1920, 1080], [0, 0, huge, 1080], [0, 0, 1920, -huge],
                     [False, 0, 1920, 1080], [0, 0, 1920, "1080"], [0, 0, float("nan"), 1080], [0, 0, 1920, float("-inf")],
                     [0, 0, 0, 1080], [0, 0, 1920, 0], [0, 0, -1920, 1080], [0, 0, 1920, -1.5]):
            with self.subTest(rect=str(rect)[:40]):
                self.assertIsNone(flow._rect(rect))
                self.assertIsNone(flow.screen_report({"on": None, "screens": [list(A), rect]}))
        for text in ('{"on": null, "screens": [[%d, 0, 1920, 1080]]}' % huge, '{"on": null, "screens": [[0, 0, NaN, 1080]]}',
                     '{"on": null, "screens": [[0, 0, 1920, Infinity]]}', '{"on": null, "screens": [[0, 0, 1920, -%d]]}' % huge):
            with self.subTest(text=text[:50]):
                self.assertIsNone(flow.screen_report(json.loads(text)))
        left = (-1280, -200, 1280, 1024)  # left of and above the main screen: a real place for one
        self.assertEqual(flow._rect(list(left)), left)
        self.assertEqual(flow._rect([0.0, 0.0, 1920.5, 1080.0]), (0.0, 0.0, 1920.5, 1080.0))
        self.assertEqual(flow.screen_report(json.loads('{"on": [-1280, -200, 1280, 1024], "screens": [[0, 0, 1920, 1080], '
                                                       '[-1280, -200, 1280, 1024]]}')), (left, (A, left)))

    def steps(self, tracker, *words):
        return [tracker.step(screen, screens, now) for screen, screens, now in words]

    def test_the_first_word_says_where_it_is_and_no_more(self):
        tracker = flow.ScreenCrossings()
        self.assertEqual(self.steps(tracker, (A, SIDE, 0.0), (A, SIDE, 1.0), (A, SIDE, 2.0)), [False, False, False])
        tracker.reset()
        self.assertEqual(self.steps(tracker, (B, SIDE, 3.0), (A, SIDE, 4.0)), [False, True])

    def test_onto_another_screen_ticks_once(self):
        tracker = flow.ScreenCrossings()
        self.assertEqual(self.steps(tracker, (A, SIDE, 0.0), (B, SIDE, 1.0), (B, SIDE, 1.1), (B, SIDE, 2.0)),
                         [False, True, False, False])

    def test_between_screens_counts_as_still_on_the_one_before(self):
        tracker = flow.ScreenCrossings()
        self.assertEqual(self.steps(tracker, (A, SIDE, 0.0), (None, SIDE, 1.0), (B, SIDE, 2.0)), [False, False, True])
        self.assertEqual(self.steps(tracker, (None, SIDE, 3.0), (B, SIDE, 4.0)), [False, False])  # back where it was

    def test_new_screens_say_where_it_is_and_no_more(self):
        added = SIDE + ((3200, 0, 800, 600),)
        moved = (A, (1920, 100, 1280, 1024))
        resized = (A, (1920, 0, 1920, 1080))
        for layout in (added, moved, resized):
            with self.subTest(layout=layout):
                tracker = flow.ScreenCrossings()
                self.assertEqual(self.steps(tracker, (A, SIDE, 0.0), (layout[1], layout, 1.0), (A, layout, 2.0),
                                            (A, SIDE, 3.0), (B, SIDE, 4.0)), [False, False, True, False, True])

    def test_back_and_forth_on_the_seam_ticks_at_most_every_gap(self):
        tracker, gap = flow.ScreenCrossings(), flow.SCREEN_CUE_GAP
        self.assertEqual(gap, 0.3)
        self.assertEqual(self.steps(tracker, (A, SIDE, 10.0), (B, SIDE, 11.0), (A, SIDE, 11.1), (B, SIDE, 11.0 + gap),
                                    (A, SIDE, 11.35), (A, SIDE, 12.0), (B, SIDE, 12.0)), [False, True, False, True, False, False, True])

    def test_mirrored_screens_never_tick(self):
        tracker, mirrored = flow.ScreenCrossings(), (A, A)
        words = [(flow.screen_at(point, mirrored), mirrored, when) for when, point in enumerate(((5, 5), (1900, 5), (5, 1000)))]
        self.assertEqual(self.steps(tracker, *words), [False, False, False])


class PollFollowsScreens(unittest.TestCase):
    """Poll asked to follow: its one timer runs for the edges or the screens, and it tells a screen once looks agree."""

    def setUp(self):
        self.addCleanup(setattr, flow_edges, "_fullscreen_in_front", flow_edges._fullscreen_in_front)
        flow_edges._fullscreen_in_front = lambda: False
        self.watcher = flow_edges.Poll()
        self.watcher._timer.timeout.disconnect()  # the timer runs, but the system is never asked here
        self.addCleanup(self.watcher.close)
        self.seen, self.heard = [], []
        self.watcher.screen_seen.connect(lambda screen, screens: self.seen.append((screen, screens)))
        self.watcher.reported.connect(self.heard.append)

    def looks(self, *points, screens=SIDE):
        del self.seen[:]
        for point in points:
            self.watcher.look(point, list(screens))
        return list(self.seen)

    def test_one_timer_for_edges_and_screens(self):
        self.assertTrue(self.watcher.follows_screens)
        self.assertFalse(self.watcher._timer.isActive())
        self.watcher.follow(True)
        self.assertTrue(self.watcher._timer.isActive())
        self.assertFalse(self.watcher.watching)  # watching is for the edges alone
        self.watcher.watch(frozenset({"right"}))
        self.watcher.follow(False)
        self.assertTrue(self.watcher._timer.isActive())  # the edges still want it
        self.assertTrue(self.watcher.watching)
        self.watcher.watch(frozenset())
        self.assertFalse(self.watcher._timer.isActive())
        self.watcher.follow(True)
        self.assertTrue(self.watcher._timer.isActive())
        self.watcher.watch(frozenset({"left"}))
        self.watcher.watch(frozenset())
        self.assertFalse(self.watcher.watching)  # following is no watching of edges
        self.assertTrue(self.watcher._timer.isActive())
        self.watcher.close()
        self.assertFalse(self.watcher._timer.isActive())

    def test_a_screen_is_told_once_three_looks_agree(self):
        self.watcher.follow(True)
        self.assertEqual(self.looks((100, 100), (110, 100)), [])
        self.assertEqual(self.looks((120, 100)), [(A, SIDE)])
        self.assertIs(type(self.seen[0][1][0]), tuple)
        self.assertEqual(self.looks((130, 100), (140, 100), (150, 100)), [])  # the same again: nothing
        self.assertEqual(self.looks((2000, 100), (2001, 100), (2002, 100)), [(B, SIDE)])
        self.assertEqual(self.looks((2000, 1050), (2000, 1050), (2000, 1050)), [(None, SIDE)])  # below B: on none
        bigger = (A, (1920, 0, 1920, 1080))
        # Screens change while the pointer is being told: what was pending goes, the new screens start afresh.
        self.assertEqual(self.looks((100, 100), (100, 100)), [])
        self.assertEqual(self.looks((2000, 1050), screens=bigger), [])
        self.assertEqual(self.looks((2000, 1050), (2000, 1050), screens=bigger), [(bigger[1], bigger)])
        self.watcher.follow(False)
        self.assertEqual(self.looks((100, 100), (100, 100), (100, 100)), [])
        self.watcher.follow(True)
        self.assertEqual(self.looks((2000, 1050), (2000, 1050), (2000, 1050), screens=bigger), [(bigger[1], bigger)])  # told anew

    def test_edges_are_told_as_without_it(self):
        self.watcher.follow(True)
        self.watcher.watch(frozenset({"right"}))
        for point in ((1915, 500), (1919, 500), (1916, 500), (1919, 503), (1919, 520), (1900, 520), (1900, 600)):
            self.watcher.look(point, [ONE])
        self.assertEqual([(report["at"], report.get("y")) for report in self.heard], [(True, 500), (True, 520), (False, None)])


class KWinFollowsScreens(unittest.TestCase):
    """The KWin watcher: one script for the edges and the screens, in a folder of its own; nothing of a session here."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="mxoptions-test-")
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.target = os.path.join(self.folder, "existing-file")
        with open(self.target, "w") as f:
            f.write("keep")
        self.old_path = os.path.join(self.folder, flow_edges.SCRIPT_NAME + ".js")  # where older versions wrote it
        os.symlink(self.target, self.old_path)
        self.bus = mock.Mock()
        self.bus.registerObject.return_value = self.bus.registerService.return_value = True
        self.calls, self.scripts, self.answer, self.running = [], [], 1, True

        def kwin(method, *args):
            self.calls.append(method)
            if method == "loadScript":
                with open(args[0]) as f:
                    self.scripts.append((args[0], f.read()))
                return self.answer
            return True
        for patch in (mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}),
                      mock.patch.object(flow_edges.tempfile, "gettempdir", return_value=self.folder),
                      mock.patch.object(flow_edges.QDBusConnection, "sessionBus", return_value=self.bus),
                      mock.patch.object(flow_edges, "QDBusServiceWatcher"),
                      mock.patch.object(flow_edges, "_kwin", kwin),
                      mock.patch.object(flow_edges, "kwin_running", lambda: self.running)):
            patch.start()
            self.addCleanup(patch.stop)
        self.watcher = flow_edges.KWin()
        self.addCleanup(self.watcher.close)
        self.seen, self.heard, self.changes = [], [], []
        self.watcher.screen_seen.connect(lambda screen, screens: self.seen.append((screen, screens)))
        self.watcher.reported.connect(self.heard.append)
        self.watcher.changed.connect(lambda: self.changes.append(True))

    def loaded(self):
        """(edges, screens) of each script KWin was given since asked last."""
        found = [re.search(r"var EDGES = (\[[^]]*\]), SCREENS = (true|false),", text).groups() for _path, text in self.scripts]
        del self.scripts[:]
        return [(json.loads(edges), screens == "true") for edges, screens in found]

    def exported(self):
        return self.watcher._exported

    def test_one_script_for_the_edges_and_the_screens(self):
        self.assertTrue(self.watcher.follows_screens)
        self.watcher.follow(True)
        self.assertEqual(self.loaded(), [([], True)])
        self.assertFalse(self.watcher.watching)
        self.watcher.watch(frozenset({"right"}))
        self.assertEqual(self.loaded(), [(["right"], True)])
        self.assertTrue(self.watcher.watching)
        self.watcher.watch(frozenset({"right"}))
        self.watcher.follow(True)
        self.assertEqual(self.loaded(), [])  # the same again: nothing new
        self.watcher.follow(False)
        self.assertEqual(self.loaded(), [(["right"], False)])  # the edges stay
        self.assertTrue(self.watcher.watching and self.exported())
        self.watcher.follow(True)
        self.assertEqual(self.loaded(), [(["right"], True)])
        del self.calls[:]
        self.watcher.watch(frozenset())
        self.assertEqual((self.loaded(), self.calls), ([([], True)], ["unloadScript", "unloadScript", "loadScript", "start"]))
        self.assertFalse(self.watcher.watching)
        self.watcher.follow(False)
        self.assertEqual(self.loaded(), [])
        self.assertFalse(self.exported())
        self.watcher.follow(True)
        self.loaded()
        self.watcher._started()  # KWin came up again
        self.assertEqual((self.loaded(), self.changes), ([([], True)], [True]))

    def test_what_kwin_says_goes_to_its_own_signal(self):
        good = json.dumps({"on": list(B), "screens": [list(A), list(B)]})
        self.watcher._heard(good)
        self.assertEqual(self.seen, [])  # not following: an old word, dropped
        self.watcher.follow(True)
        self.watcher._heard(good)
        self.assertEqual((self.seen, self.heard), ([(B, SIDE)], []))
        for bad in (json.dumps({"on": list(B), "screens": [list(A)]}), json.dumps({"screens": "all"}), "not json", "[1, 2]",
                    '{"on": null, "screens": [[%d, 0, 1920, 1080]]}' % 10 ** 400, '{"on": null, "screens": [[0, 0, NaN, 1]]}',
                    '{"on": null, "screens": [[0, 0, 0, 1080]]}'):
            with self.subTest(bad=bad[:40]):
                self.watcher._heard(bad)  # nothing raised
        self.assertEqual((len(self.seen), self.heard), (1, []))
        self.watcher._heard(good)  # a good word after bad ones is taken
        self.assertEqual(len(self.seen), 2)
        edge = {"at": True, "edge": "right", "x": 1919, "y": 5, "screen": list(A), "full": False}
        self.watcher._heard(json.dumps(edge))
        self.assertEqual((self.heard, len(self.seen)), ([edge], 2))

    def test_the_script_lives_in_a_folder_of_its_own(self):
        self.watcher.follow(True)
        (path, text), = self.scripts
        folder = os.path.dirname(path)
        self.assertNotEqual(path, self.old_path)
        self.assertEqual(os.path.dirname(folder), self.folder)
        self.assertTrue(os.path.basename(folder).startswith(flow_edges.SCRIPT_NAME + "-"))
        self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)
        self.assertIn("function follow(pos)", text)
        self.watcher.watch(frozenset({"left"}))
        self.watcher._started()
        self.assertEqual({os.path.dirname(found) for found, _text in self.scripts}, {folder})  # used again, every time
        with open(self.target) as f:
            self.assertEqual(f.read(), "keep")
        held = self.watcher._folder  # still referred to: only closing itself removes the folder, not the collector
        self.watcher.close()
        self.assertFalse(os.path.exists(folder))
        del held
        self.assertFalse(self.exported())
        del self.calls[:]
        self.watcher.close()  # again: nothing more to do
        self.assertEqual(self.calls, [])
        self.assertTrue(os.path.islink(self.old_path))
        with open(self.target) as f:
            self.assertEqual(f.read(), "keep")

    def test_closing_stops_the_edges_and_the_screens_at_once(self):
        self.watcher.watch(frozenset({"right"}))
        self.watcher.follow(True)
        self.loaded()
        del self.calls[:]
        self.watcher.close()
        self.assertEqual((self.loaded(), self.calls), ([], ["unloadScript"]))  # no script for the edges alone on the way
        self.assertFalse(self.watcher.watching or self.exported())
        self.watcher._started()  # KWin coming up after that loads nothing
        self.assertEqual(self.loaded(), [])

    def test_a_start_that_failed_is_undone_when_switched_off(self):
        def no_kwin():
            self.running = False
            return mock.patch.dict(os.environ)

        def refused():
            self.answer = -1
            return mock.patch.dict(os.environ)

        def unwritable():
            return mock.patch.object(flow_edges, "open", side_effect=OSError("synthetic"), create=True)
        for name, fail in (("KWin not there", no_kwin), ("script not written", unwritable), ("KWin refused it", refused)):
            with self.subTest(name):
                self.running, self.answer = True, 1
                watcher = flow_edges.KWin()
                watcher.screen_seen.connect(lambda screen, screens: self.seen.append((screen, screens)))
                with fail():
                    watcher.follow(True)
                    self.assertTrue(watcher._exported)  # on the bus, and nothing runs
                    self.assertEqual((watcher.watching, watcher._loaded), (False, (frozenset(), False)))
                    del self.calls[:]
                    watcher.follow(False)
                    self.assertEqual(self.calls, ["unloadScript"])  # whatever KWin may have kept of it
                    self.assertFalse(watcher._exported)
                    self.assertFalse(watcher.watching)
                    self.bus.unregisterService.assert_called_with(flow_edges.SERVICE)
                    watcher.close()
                    del self.calls[:]
                    watcher._started()  # KWin coming up later loads nothing
                    self.assertNotIn("loadScript", self.calls)
                    self.assertFalse(watcher._exported)
                    watcher._heard(json.dumps({"on": list(A), "screens": [list(A)]}))
                self.assertEqual(self.seen, [])
                self.assertEqual(sorted(os.listdir(self.folder)), ["existing-file", flow_edges.SCRIPT_NAME + ".js"])  # no folder left

    def test_a_failed_start_comes_good_when_asked_again(self):
        self.running = False
        self.watcher.follow(True)
        self.assertEqual(self.loaded(), [])
        self.running = True
        self.watcher.follow(True)  # asked again, as the settings do when anything changes
        self.assertEqual(self.loaded(), [([], True)])
        self.watcher.follow(False)
        self.assertFalse(self.exported())


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

    def test_press_release_and_cancel_as_a_mouse_button_or_a_key_sends_them(self):
        self.proxy.configure({"size": 1.0, "style": "bubbles", "bubbles": [
            {"label": "", "icon": "", "color": "", "action": {"type": "keys", "keys": ["KEY_A"]}}]})
        self.assertTrue(self.wait(lambda: self.proxy.available))
        opened = lambda: [value for kind, value in self.heard if kind == "opened"]  # noqa: E731
        self.proxy.handle("down")  # held open
        self.assertTrue(self.wait(lambda: opened() == [True]))
        self.proxy.handle("up")    # let go over no bubble: left open for a click
        self.proxy.handle("down")  # pressed again: closed, and its release does nothing
        self.assertTrue(self.wait(lambda: opened() == [True, False]))
        self.proxy.handle("up")
        self.proxy.handle("down")
        self.assertTrue(self.wait(lambda: opened() == [True, False, True]))
        self.proxy.handle("cancel")
        self.assertTrue(self.wait(lambda: opened() == [True, False, True, False]))
        self.assertEqual(opened(), [True, False, True, False])

    def test_a_ring_of_the_callers_own(self):
        self.assertTrue(self.wait(lambda: self.proxy.available))
        self.assertFalse(self.proxy.open())  # no ring of the user's has been told
        self.assertTrue(self.proxy.open([{"label": "x", "icon": "", "color": "", "action": {"type": "hook", "name": "x"}}]))
        self.assertTrue(self.wait(lambda: ("opened", True) in self.heard))

    def test_a_ring_s_number_goes_there_and_a_bad_one_is_dropped(self):
        self.proxy.configure({"size": 1.0, "style": "bubbles", "bubbles": [
            {"label": "", "icon": "", "color": "", "action": {"type": "keys", "keys": ["KEY_A"]}}]})
        self.assertTrue(self.wait(lambda: self.proxy.available))
        opened = lambda: [value for kind, value in self.heard if kind == "opened"]  # noqa: E731
        bubbles = [{"label": "x", "icon": "", "color": "", "action": {"type": "hook", "name": "x"}}]
        for bad in ("1", 1.5, True, [1]):
            self.proxy._send("handle", event="down", session=bad, bubbles=bubbles)
        self.proxy._send("handle", event="down", session=5, bubbles=["nothing usable"])
        self.proxy.handle("down", 6, bubbles)
        self.assertTrue(self.wait(lambda: opened() == [True]))
        self.proxy.handle("cancel", 5)  # an earlier ring's: this one stays
        self.proxy.handle("cancel", 6)
        self.assertTrue(self.wait(lambda: opened() == [True, False]))
        self.assertTrue(self.proxy.open(bubbles))  # another program's ring
        self.assertTrue(self.wait(lambda: opened() == [True, False, True]))
        self.proxy.handle("cancel", 99)
        self.proxy.handle("cancel")
        self.assertTrue(self.wait(lambda: opened() == [True, False, True, False]))

    def test_a_pick_from_there_counts_only_with_a_ring_number_it_may_have(self):
        picks = []
        self.proxy.triggered.connect(lambda action, session: picks.append((action, session)))
        action = {"type": "hook", "name": "x"}
        lines = [{"event": "triggered", "action": action, "session": 3}, {"event": "triggered", "action": action, "session": None},
                 {"event": "triggered", "action": action}, {"event": "triggered", "action": action, "session": "3"},
                 {"event": "triggered", "action": action, "session": 1.5}, {"event": "triggered", "action": action, "session": True},
                 {"event": "triggered", "action": "x", "session": 3}]
        data = b"".join(json.dumps(line).encode("utf-8") + b"\n" for line in lines)
        with mock.patch.object(self.proxy._process, "readAllStandardOutput", lambda: data):
            self.proxy._read()
        self.assertEqual(picks, [(action, 3), (action, None)])

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
