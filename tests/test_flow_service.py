"""Flow as the program runs it: two services on this machine pair by code, agree on their sides,
hand the mouse over at the edge and carry what was copied. The mouse, the desktop and the
clipboard are stand-ins; the network between the two is real, on the loopback interface."""

import copy
import json
import os
import shutil
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402

import tests.support  # noqa: E402,F401
from tests.test_flow import free_port  # noqa: E402
from mxoptions import clipboard, config, flow, flow_files, flow_service, vinput  # noqa: E402
from mxoptions.ui.store import Store  # noqa: E402

APP = QGuiApplication.instance() or QGuiApplication([])
HOSTS = [{"paired": True, "name": "desk"}, {"paired": True, "name": "laptop"}, {"paired": False, "name": ""}]


class Engine:
    """Stands in for the engine: remembers what Flow asked of it."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args))

    def asked(self, name):
        return [call[1:] for call in self.calls if call[0] == name]


class Clipboard:
    """One computer's clipboard."""

    PNG = clipboard.PNG
    signature = staticmethod(clipboard.signature)

    def __init__(self):
        self.content = {"files": [], "image": None, "text": ""}
        self.written = []

    def rich(self):
        return True

    def read(self, image_limit=None):
        return dict(self.content)

    def write(self, text=None, image=None, files=None):
        self.written.append({"text": text, "image": image, "files": files})
        self.content = {"files": list(files or []), "image": image, "text": text or ""}
        return True


class Pointer:
    def __init__(self):
        self.moves = []

    def move(self, point, desktop):
        self.moves.append((point, desktop))

    def close(self):
        pass


class Computer:
    def __init__(self, port):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["flow"]["port"] = port
        self.engine = Engine()
        self.store = Store(cfg, self.engine)
        self.store.flush = lambda: None  # these settings are made up: never write them anywhere
        self.store._save_timer.timeout.disconnect()
        self.flow = flow_service.FlowService(self.store)
        self.flow.clipboard = self.clipboard = Clipboard()
        self.notes = []
        self.flow.notify = lambda kind, details: self.notes.append((kind, details))

    @property
    def cfg(self):
        return self.store.cfg["flow"]

    def mouse(self, connected, channel=0):
        self.store.update_state(dict(self.store.state, connected=connected, current_host=channel, hosts=HOSTS if connected else []))

    def at_edge(self, edge, along, full=False):
        """What the edge watcher says when the pointer comes to rest ``along`` (0..1) the edge of a 1000x1000 screen."""
        x, y = {"right": (999, along * 999), "left": (0, along * 999), "top": (along * 999, 0), "bottom": (along * 999, 999)}[edge]
        self.flow._edge_report(json.dumps({"at": True, "edge": edge, "x": x, "y": y, "screen": [0, 0, 1000, 1000], "full": full}))


def wait(condition, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


@unittest.skipUnless(flow.supported(), "this Python has no pre-shared keys for TLS")
class TwoServices(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="mxoptions-test-")
        self.addCleanup(shutil.rmtree, self.folder, True)
        changes = [(flow, "RETRY", 0.2), (flow, "HERE_INTERVAL", 0.2), (flow, "OFFER_INTERVAL", 0.1),
                   (flow, "DISCOVERY_PORT", free_port()), (flow, "PAIR_PORT", free_port()),
                   (flow, "broadcast_addresses", lambda: ["127.255.255.255"]),
                   (flow_files, "STAGING", os.path.join(self.folder, "arrived")),
                   (flow_service, "_kwin", lambda *args: None), (flow_service, "kwin_running", lambda: False),
                   (flow_service.FlowService, "_export", lambda self, wanted: False),  # stay off the session bus
                   (vinput, "create_pointer", Pointer)]
        for module, name, value in changes:
            self.addCleanup(setattr, module, name, getattr(module, name))
            setattr(module, name, value)
        self.desk, self.laptop = Computer(free_port()), Computer(free_port())
        self.addCleanup(self.desk.flow.close)
        self.addCleanup(self.laptop.flow.close)

    def pair(self):
        """Pair by code, as the two settings pages would, and wait for the link."""
        self.laptop.flow.watch_offers(True)
        self.desk.flow.start_pairing()
        self.assertTrue(wait(lambda: self.laptop.flow.status()["offers"]), "the laptop never saw the desk's offer")
        offer = self.laptop.flow.status()["offers"][0]
        self.laptop.flow.pair(offer["address"], offer["port"], self.desk.flow.status()["pairing"]["code"])
        self.assertTrue(wait(lambda: self.desk.flow.status()["peer"] and self.laptop.flow.status()["peer"]), "no link after pairing")
        self.laptop.flow.watch_offers(False)

    def meet(self):
        """Pair, and let each computer see the mouse once: the desk is channel 1, the laptop channel 2, the mouse at the desk."""
        self.pair()
        self.laptop.mouse(True, 1)
        self.laptop.mouse(False)
        self.desk.mouse(True, 0)
        self.assertTrue(wait(lambda: (self.desk.flow.status()["peer"] or {}).get("channel") == 1))
        self.assertTrue(wait(lambda: (self.laptop.flow.status()["peer"] or {}).get("present") is True))
        time.sleep(0.4)  # in case each called the other: let the two calls become one
        APP.processEvents()

    def cross(self, along=0.5):
        """The pointer reaches the desk's edge and the engine reports that the mouse has gone."""
        self.desk.at_edge("right", along)
        self.desk.flow.on_engine(("left", 1))

    def test_pairing_by_code_sets_everything_up(self):
        self.pair()
        self.assertEqual(self.desk.cfg["key"], self.laptop.cfg["key"])
        self.assertTrue(flow.key_usable(self.desk.cfg["key"]))
        self.assertTrue(self.desk.cfg["enabled"] and self.laptop.cfg["enabled"])
        self.assertEqual(self.laptop.cfg["peer"], f"127.0.0.1:{self.desk.cfg['port']}")
        self.assertIsNone(self.desk.flow.status()["pairing"])
        # the computer that showed the code keeps its side, the other one takes the facing one
        self.assertTrue(wait(lambda: self.laptop.cfg["edge"] == "left"))
        self.assertEqual(self.desk.cfg["edge"], "right")

    def test_a_wrong_code_is_said_and_counted(self):
        self.laptop.flow.watch_offers(True)
        self.desk.flow.start_pairing()
        self.assertTrue(wait(lambda: self.laptop.flow.status()["offers"]))
        offer = self.laptop.flow.status()["offers"][0]
        wrong = "000000" if self.desk.flow.status()["pairing"]["code"] != "000000" else "111111"
        self.laptop.flow.pair(offer["address"], offer["port"], wrong)
        self.assertTrue(wait(lambda: self.laptop.flow.status()["pair_error"] == "code"))
        self.assertTrue(wait(lambda: self.desk.flow.status()["pairing"]["left"] == 2))
        self.assertFalse(self.laptop.cfg["key"])

    def test_moving_the_other_computer_on_one_moves_it_on_both(self):
        self.pair()
        self.assertTrue(wait(lambda: self.laptop.cfg["edge"] == "left"))
        self.laptop.store.set("flow", "edge", "top")
        self.assertTrue(wait(lambda: self.desk.cfg["edge"] == "bottom"))

    def test_the_edge_arms_the_push_and_the_pointer_arrives_on_the_other_side(self):
        self.meet()
        self.desk.at_edge("right", 0.25)
        self.assertEqual(self.desk.engine.asked("flow_arm"), [(1, "right", 10.0)])
        self.desk.flow.on_engine(("left", 1))
        self.assertTrue(wait(lambda: self.laptop.engine.asked("expect_mouse")))
        (point, _desktop), = self.laptop.flow._pointer.moves
        screen = QGuiApplication.primaryScreen().geometry()
        self.assertEqual(point[0], screen.x() + flow.ENTRY_INSET)  # through the laptop's left edge
        self.assertAlmostEqual(point[1], screen.y() + 0.25 * (screen.height() - 1), delta=1.5)

    def test_a_mouse_that_has_only_just_arrived_is_not_pushed_straight_back(self):
        self.meet()
        self.addCleanup(setattr, flow_service, "ARRIVAL_QUIET", flow_service.ARRIVAL_QUIET)
        flow_service.ARRIVAL_QUIET = 0.3
        self.cross()
        self.assertTrue(wait(lambda: self.laptop.engine.asked("expect_mouse")))
        self.desk.mouse(False)
        self.laptop.at_edge("left", 0.5)   # the pointer was put near the edge; the mouse is still on its way
        self.laptop.mouse(True, 1)
        self.assertTrue(wait(lambda: (self.laptop.flow.status()["peer"] or {}).get("present") is False))
        self.laptop.at_edge("left", 0.6)   # it is here, and the hand that kept moving reaches the edge at once
        self.assertEqual(self.laptop.engine.asked("flow_arm"), [])
        time.sleep(0.35)
        self.laptop.at_edge("left", 0.7)   # a moment later the edge leads back as usual
        self.assertEqual(self.laptop.engine.asked("flow_arm"), [(0, "left", 10.0)])

    def test_corners_a_full_screen_window_and_leaving_the_edge_do_not_arm(self):
        self.meet()
        self.desk.at_edge("right", 0.03)             # inside the corner kept free (10 % by default)
        self.desk.at_edge("right", 0.5, full=True)   # a game in front
        self.assertEqual(self.desk.engine.asked("flow_arm"), [])
        self.desk.at_edge("right", 0.5)
        self.desk.at_edge("right", 0.03)             # slid into the corner: the push is called off
        self.assertEqual(len(self.desk.engine.asked("flow_arm")), 1)
        self.assertEqual(len(self.desk.engine.asked("flow_disarm")), 1)
        self.desk.store.set("flow", "fullscreen", True)
        self.desk.at_edge("right", 0.5, full=True)
        self.assertEqual(len(self.desk.engine.asked("flow_arm")), 2)

    def test_nothing_is_armed_while_the_other_computer_does_not_know_its_channel(self):
        self.pair()
        self.desk.mouse(True, 0)
        self.desk.at_edge("right", 0.5)
        self.assertEqual(self.desk.engine.asked("flow_arm"), [])

    def test_copied_text_crosses_once(self):
        self.meet()
        self.desk.clipboard.content["text"] = "скопійовано на столі"
        self.cross()
        self.assertTrue(wait(lambda: self.laptop.clipboard.written))
        self.assertEqual(self.laptop.clipboard.written[0]["text"], "скопійовано на столі")
        self.cross()  # the same text again: the laptop has it already
        self.assertTrue(wait(lambda: len(self.laptop.engine.asked("expect_mouse")) == 2))
        self.assertEqual(len(self.laptop.clipboard.written), 1)

    def test_a_copied_picture_crosses(self):
        self.meet()
        picture = b"\x89PNG\r\n\x1a\n" + os.urandom(200_000)
        self.desk.clipboard.content["image"] = picture
        self.cross()
        self.assertTrue(wait(lambda: self.laptop.clipboard.written))
        self.assertEqual(self.laptop.clipboard.written[0]["image"], picture)

    def test_copied_files_cross_and_wait_on_the_clipboard(self):
        self.meet()
        source = os.path.join(self.folder, "report.txt")
        with open(source, "w") as f:
            f.write("звіт")
        self.desk.clipboard.content["files"] = [source]
        self.cross()
        self.assertTrue(wait(lambda: any(entry["files"] for entry in self.laptop.clipboard.written), timeout=12))
        # while they were on their way a note stood in for what had been on the laptop's clipboard before
        self.assertIn("1 file", self.laptop.clipboard.written[0]["text"])
        arrived, = self.laptop.clipboard.written[-1]["files"]
        self.assertTrue(arrived.startswith(flow_files.STAGING) and arrived.endswith("report.txt"))
        with open(arrived) as f:
            self.assertEqual(f.read(), "звіт")
        self.assertEqual(self.laptop.notes[0][0], "files")
        # coming back with the same files on the clipboard does not send them home again
        self.laptop.mouse(True, 1)
        self.desk.mouse(False)
        self.assertTrue(wait(lambda: (self.laptop.flow.status()["peer"] or {}).get("present") is False))
        self.laptop.at_edge("left", 0.5)
        self.laptop.flow.on_engine(("left", 0))
        self.assertTrue(wait(lambda: self.desk.engine.asked("expect_mouse")))
        time.sleep(0.3)
        APP.processEvents()
        self.assertEqual(self.desk.clipboard.written, [])
        self.assertIsNone(self.laptop.flow.status()["transfer"])

    def test_files_the_other_side_would_not_take_are_offered_again_next_time(self):
        self.meet()
        source = os.path.join(self.folder, "big.bin")
        with open(source, "wb") as f:
            f.write(b"x" * (2 * 1024 * 1024))
        self.laptop.store.set("flow", "files_max_mb", 1)   # the laptop accepts less than the desk would send
        self.desk.clipboard.content["files"] = [source]
        self.cross()
        self.assertTrue(wait(lambda: len(self.laptop.notes) == 1))
        self.assertEqual(self.laptop.notes[0][1]["why"], "too big")
        self.laptop.store.set("flow", "files_max_mb", 100)
        self.cross()                                       # the same files are still on the desk's clipboard
        self.assertTrue(wait(lambda: any(entry["files"] for entry in self.laptop.clipboard.written), timeout=12))

    def test_files_over_the_limit_stay_and_the_user_is_told_where_they_are_now(self):
        self.meet()
        source = os.path.join(self.folder, "big.bin")
        with open(source, "wb") as f:
            f.write(b"x" * (2 * 1024 * 1024))
        self.desk.store.set("flow", "files_max_mb", 1)
        self.desk.clipboard.content["files"] = [source]
        self.cross()
        self.assertTrue(wait(lambda: self.laptop.notes))
        self.assertEqual(self.laptop.notes[0][0], "skipped")
        self.assertEqual(self.laptop.notes[0][1]["why"], "too big")
        self.assertEqual(self.laptop.clipboard.written, [])

    def test_forgetting_the_other_computer_switches_flow_off(self):
        self.pair()
        self.desk.flow.unpair()
        self.assertFalse(self.desk.cfg["key"] or self.desk.cfg["enabled"])
        self.assertTrue(wait(lambda: self.laptop.flow.status()["peer"] is None))


if __name__ == "__main__":
    unittest.main()
