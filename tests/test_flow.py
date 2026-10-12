"""Flow: the pairing key, the geometry of crossing from one desktop to another, the link between
two computers (here: two links on this one), and the engine's part in handing the mouse over."""

import json
import os
import queue
import shutil
import socket
import tempfile
import time
import unittest

from tests.support import make_engine
from mxoptions import engine as engine_module, flow, flow_files, hidpp

KEY = "ABCD-EFGH-IJKL-MNOP"


class Key(unittest.TestCase):
    def test_a_new_key_is_usable_and_different_every_time(self):
        first, second = flow.new_key(), flow.new_key()
        self.assertTrue(flow.key_usable(first))
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^([A-Z2-7]{4}-){5}[A-Z2-7]{4}$")

    def test_case_spaces_and_separators_do_not_count(self):
        self.assertEqual(flow.normalize_key(" abcd efgh-ijkl "), "ABCDEFGHIJKL")
        self.assertEqual(flow._secret("abcd efgh ijkl mnop"), flow._secret(KEY))

    def test_a_short_key_is_not_usable(self):
        self.assertFalse(flow.key_usable("ABCD-EFGH"))
        self.assertFalse(flow.key_usable(""))


class Address(unittest.TestCase):
    def test_what_the_user_may_type(self):
        self.assertEqual(flow.split_address(" 192.168.1.20 "), ("192.168.1.20", flow.PORT))
        self.assertEqual(flow.split_address("laptop.local:5000"), ("laptop.local", 5000))
        self.assertEqual(flow.split_address("[fe80::1]"), ("fe80::1", flow.PORT))
        self.assertEqual(flow.split_address("[fe80::1]:5000"), ("fe80::1", 5000))
        self.assertEqual(flow.split_address("fe80::1"), ("fe80::1", flow.PORT))
        self.assertEqual(flow.split_address("laptop:what", 7000), ("laptop", 7000))


class Geometry(unittest.TestCase):
    ONE = [(0, 0, 1920, 1080)]

    def test_where_the_pointer_left(self):
        screen = (0, 0, 1920, 1080)
        self.assertEqual(flow.leave_fraction("right", (1919, 0), screen), 0.0)
        self.assertEqual(flow.leave_fraction("right", (1919, 1079), screen), 1.0)
        self.assertAlmostEqual(flow.leave_fraction("bottom", (960, 1079), screen), 0.5, places=3)
        self.assertEqual(flow.leave_fraction("left", (0, 5000), screen), 1.0)

    def test_it_comes_in_a_little_inside_the_facing_edge(self):
        self.assertEqual(flow.entry_point("left", 0.5, self.ONE), (24, 540))
        self.assertEqual(flow.entry_point("right", 0.0, self.ONE), (1895, 0))
        self.assertEqual(flow.entry_point("top", 1.0, self.ONE), (1919, 24))
        self.assertEqual(flow.entry_point("bottom", 0.25, self.ONE), (480, 1055))

    def test_a_smaller_screen_keeps_the_proportion(self):
        self.assertEqual(flow.entry_point("left", 0.5, [(0, 0, 1280, 800)]), (24, 400))

    def test_with_two_screens_it_enters_the_one_that_forms_the_edge(self):
        screens = [(0, 0, 1920, 1080), (1920, 0, 2560, 1440)]
        self.assertEqual(flow.bounds(screens), (0, 0, 4480, 1440))
        self.assertEqual(flow.entry_point("left", 1.0, screens), (24, 1079))
        self.assertEqual(flow.entry_point("right", 1.0, screens), (4480 - 1 - 24, 1439))
        # the top edge is formed by both: half-way along is on the second screen
        self.assertEqual(flow.entry_point("top", 0.5, screens), (2240, 24))

    def test_nonsense_from_the_other_side_is_harmless(self):
        self.assertEqual(flow.entry_point("left", float("nan"), self.ONE)[0], 24)
        self.assertEqual(flow.entry_point("left", 99, self.ONE), (24, 1079))


class Sides(unittest.TestCase):
    def test_facing_edges_need_no_change(self):
        self.assertIsNone(flow.edge_to_adopt(("left", 0, "a"), ("right", 50, "b")))
        self.assertIsNone(flow.edge_to_adopt(("top", 9, "b"), ("bottom", 1, "a")))

    def test_the_later_choice_wins(self):
        self.assertEqual(flow.edge_to_adopt(("right", 10, "a"), ("right", 20, "b")), "left")
        self.assertIsNone(flow.edge_to_adopt(("right", 20, "b"), ("right", 10, "a")))
        self.assertEqual(flow.edge_to_adopt(("left", 10, "z"), ("top", 20, "a")), "bottom")

    def test_exactly_one_gives_way_when_neither_was_ever_chosen(self):
        desk, laptop = ("right", 0.0, "aaaa"), ("right", 0.0, "bbbb")
        self.assertEqual([flow.edge_to_adopt(desk, laptop), flow.edge_to_adopt(laptop, desk)], ["left", None])

    def test_an_edge_that_is_none_is_ignored(self):
        self.assertIsNone(flow.edge_to_adopt(("right", 0, "a"), ("sideways", 99, "b")))
        self.assertIsNone(flow.edge_to_adopt(("right", 0, "a"), (None, 99, "b")))


def free_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class End:
    """One computer's end of the link, with what it heard."""

    def __init__(self, name, staging=None):
        self.heard = queue.Queue()
        self.link = flow.Link(lambda kind, payload: self.heard.put((kind, payload)), staging=staging)
        self.link.set_status({"name": name, "channel": 0, "present": False})
        self.port = free_port()
        self.link.start()

    def expect(self, kind, timeout=6.0):
        """The next event of this kind (others before it are dropped)."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                found, payload = self.heard.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                raise AssertionError(f"no {kind!r} event") from None
            if found == kind:
                return payload

    def quiet(self, kind, for_=0.6):
        """True if no event of this kind arrives for a while."""
        deadline = time.monotonic() + for_
        while time.monotonic() < deadline:
            try:
                found, _payload = self.heard.get(timeout=0.05)
            except queue.Empty:
                continue
            if found == kind:
                return False
        return True


class TwoComputers(unittest.TestCase):
    """Two ends on this machine. Nothing leaves it: they call out on the loopback network only, on
    ports of their own."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="mxoptions-test-")
        self.addCleanup(shutil.rmtree, self.folder, True)
        changed = {"RETRY": 0.2, "HERE_INTERVAL": 0.2, "OFFER_INTERVAL": 0.1, "DISCOVERY_PORT": free_port(),
                   "PAIR_PORT": free_port(), "broadcast_addresses": lambda: ["127.255.255.255"]}
        for name, value in changed.items():
            self.addCleanup(setattr, flow, name, getattr(flow, name))
            setattr(flow, name, value)
        self.desk = End("desk", os.path.join(self.folder, "at-desk"))
        self.laptop = End("laptop", os.path.join(self.folder, "at-laptop"))
        self.addCleanup(self.desk.link.stop)
        self.addCleanup(self.laptop.link.stop)

    def connect(self, both_call=False):
        self.desk.link.configure(KEY, self.desk.port, f"127.0.0.1:{self.laptop.port}" if both_call else "")
        self.laptop.link.configure(KEY, self.laptop.port, f"127.0.0.1:{self.desk.port}")
        first = self.desk.expect("up"), self.laptop.expect("up")
        time.sleep(0.4)  # in case each called the other: let the two calls become one
        return first


@unittest.skipUnless(flow.supported(), "this Python has no pre-shared keys for TLS")
class Linked(TwoComputers):
    def test_each_side_learns_about_the_other(self):
        at_desk, at_laptop = self.connect()
        self.assertEqual((at_desk["name"], at_laptop["name"]), ("laptop", "desk"))
        self.assertEqual(at_desk["channel"], 0)

    def test_status_and_messages_travel_both_ways(self):
        self.connect()
        self.laptop.link.set_status({"name": "laptop", "channel": 1, "present": True})
        status = self.desk.expect("status")
        if status["channel"] != 1:  # said again as it was, after two calls that crossed became one
            status = self.desk.expect("status")
        self.assertEqual(status["channel"], 1)
        self.desk.link.send({"t": "enter", "edge": "right", "at": 0.25})
        self.assertEqual(self.laptop.expect("message"), {"t": "enter", "edge": "right", "at": 0.25})
        text = "скопійоване\n" * 20000  # a few hundred kilobytes, not ASCII
        self.laptop.link.send({"t": "clipboard", "text": text})
        self.assertEqual(self.desk.expect("message")["text"], text)

    def test_bytes_ride_behind_a_message(self):
        self.connect()
        picture = os.urandom(3 * 1024 * 1024)
        self.desk.link.send({"t": "clip", "mime": "image/png"}, picture)
        self.desk.link.send({"t": "after"})
        arrived = self.laptop.expect("message")
        self.assertEqual((arrived["mime"], arrived["data"]), ("image/png", picture))
        self.assertEqual(self.laptop.expect("message")["t"], "after")

    def test_when_both_call_each_other_one_link_remains(self):
        self.connect(both_call=True)
        time.sleep(0.5)  # let the calls that crossed sort themselves out
        self.desk.link.send({"t": "note", "number": 1})
        self.laptop.link.send({"t": "note", "number": 2})
        self.assertEqual(self.laptop.expect("message")["number"], 1)
        self.assertEqual(self.desk.expect("message")["number"], 2)
        self.assertTrue(self.desk.quiet("down") and self.laptop.quiet("down"))

    def test_a_different_key_gets_nowhere(self):
        self.desk.link.configure(KEY, self.desk.port, "")
        self.laptop.link.configure("ZZZZ-ZZZZ-ZZZZ-ZZZZ", self.laptop.port, f"127.0.0.1:{self.desk.port}")
        self.assertTrue(self.desk.quiet("up", 1.5))
        self.assertTrue(self.laptop.quiet("up", 0.1))

    def test_the_other_side_going_away_is_noticed_and_it_is_found_again(self):
        self.connect()
        self.desk.link.configure(None)
        self.laptop.expect("down")
        self.desk.link.configure(KEY, self.desk.port, "")
        self.assertEqual(self.laptop.expect("up")["name"], "desk")

    def test_a_port_in_use_is_reported(self):
        taken = socket.socket()
        taken.bind(("0.0.0.0", 0))
        taken.listen()
        self.addCleanup(taken.close)
        with self.assertLogs("mxoptions.flow", "WARNING"):
            self.desk.link.configure(KEY, taken.getsockname()[1], "")
            self.assertTrue(self.desk.expect("problem"))

    def test_with_no_address_given_they_find_each_other(self):
        self.desk.link.configure(KEY, self.desk.port, "")
        self.laptop.link.configure(KEY, self.laptop.port, "")
        self.assertEqual(self.desk.expect("up")["name"], "laptop")
        self.assertEqual(self.laptop.expect("up")["name"], "desk")

    def test_a_computer_with_another_key_is_not_found(self):
        self.desk.link.configure(KEY, self.desk.port, "")
        self.laptop.link.configure("ZZZZ-ZZZZ-ZZZZ-ZZZZ", self.laptop.port, "")
        self.assertTrue(self.desk.quiet("up", 1.2))


class Beacons(unittest.TestCase):
    def test_a_signed_call_is_read_only_with_the_key_and_in_time(self):
        data = json.dumps(flow.here_beacon(KEY, "abc", 5000, when=1000)).encode()
        self.assertEqual(flow.read_beacon(data, KEY, now=1050), {"t": "here", "id": "abc", "port": 5000})
        self.assertIsNone(flow.read_beacon(data, "ZZZZ-ZZZZ-ZZZZ-ZZZZ", now=1050))
        self.assertIsNone(flow.read_beacon(data, None, now=1050))
        self.assertIsNone(flow.read_beacon(data, KEY, now=1000 + flow.BEACON_AGE + 1))
        moved = json.dumps(dict(flow.here_beacon(KEY, "abc", 5000, when=1000), port=6000)).encode()
        self.assertIsNone(flow.read_beacon(moved, KEY, now=1050))

    def test_a_pairing_offer_needs_no_key(self):
        data = json.dumps(flow.offer_beacon("abc", "desk", 47847)).encode()
        self.assertEqual(flow.read_beacon(data), {"t": "pair", "id": "abc", "name": "desk", "port": 47847})

    def test_other_datagrams_are_ignored(self):
        for data in (b"", b"hello", b"[1]", b'{"app":"other"}', b'{"app":"mxoptions-flow","t":"pair","id":"x","port":"80"}',
                     b'{"app":"mxoptions-flow","t":"pair","id":5,"port":80}'):
            self.assertIsNone(flow.read_beacon(data, KEY))


@unittest.skipUnless(flow.supported(), "this Python has no pre-shared keys for TLS")
class Pairing(TwoComputers):
    INFO = {"name": "laptop", "port": 5001}

    def test_the_code_pairs_them_and_both_get_the_same_key(self):
        self.laptop.link.watch_offers(True)
        self.desk.link.open_pairing("482913", {"name": "desk", "port": 5000})
        offer = self.laptop.expect("offer")
        self.assertEqual((offer["name"], offer["port"]), ("desk", flow.PAIR_PORT))
        self.laptop.link.pair_with("127.0.0.1", offer["port"], "482913", self.INFO)
        at_laptop, at_desk = self.laptop.expect("paired"), self.desk.expect("paired")
        self.assertEqual(at_laptop["key"], at_desk["key"])
        self.assertTrue(flow.key_usable(at_desk["key"]))
        self.assertEqual((at_desk["name"], at_desk["port"], at_desk["host"]), ("laptop", 5001, True))
        self.assertEqual((at_laptop["name"], at_laptop["port"], at_laptop["host"]), ("desk", 5000, False))
        # the code was for one computer only
        self.laptop.link.pair_with("127.0.0.1", offer["port"], "482913", self.INFO)
        self.assertEqual(self.laptop.expect("pair-failed"), "unreachable")

    def test_wrong_codes_are_counted_and_then_the_code_is_withdrawn(self):
        self.desk.link.open_pairing("482913", {"name": "desk", "port": 5000})
        time.sleep(0.3)
        for left in (2, 1, 0):
            self.laptop.link.pair_with("127.0.0.1", flow.PAIR_PORT, "000000", self.INFO)
            self.assertEqual(self.laptop.expect("pair-failed"), "code")
            self.assertEqual(self.desk.expect("pairing"), left)
        self.laptop.link.pair_with("127.0.0.1", flow.PAIR_PORT, "482913", self.INFO)  # too late even with the right one
        self.assertIn(self.laptop.expect("pair-failed"), ("unreachable", "closed"))
        self.assertTrue(self.desk.quiet("paired", 0.3))

    def test_the_paired_key_links_them(self):
        self.desk.link.open_pairing("482913", {"name": "desk", "port": self.desk.port})
        time.sleep(0.3)
        self.laptop.link.pair_with("127.0.0.1", flow.PAIR_PORT, "482913", {"name": "laptop", "port": self.laptop.port})
        key = self.desk.expect("paired")["key"]
        self.assertEqual(self.laptop.expect("paired")["key"], key)
        self.desk.link.configure(key, self.desk.port, "")
        self.laptop.link.configure(key, self.laptop.port, f"127.0.0.1:{self.desk.port}")
        self.assertEqual(self.desk.expect("up")["name"], "laptop")


@unittest.skipUnless(flow.supported(), "this Python has no pre-shared keys for TLS")
class Files(TwoComputers):
    def make(self):
        """Something to copy: a file, and a folder with files, a folder and a link in it."""
        source = os.path.join(self.folder, "source")
        os.makedirs(os.path.join(source, "album", "more"))
        contents = {"notes.txt": "нотатки\n".encode(), "album/a.bin": os.urandom(700 * 1024), "album/empty": b"",
                    "album/more/run.sh": b"#!/bin/sh\n"}
        for name, data in contents.items():
            with open(os.path.join(source, name), "wb") as f:
                f.write(data)
        os.chmod(os.path.join(source, "album/more/run.sh"), 0o755)
        os.symlink("/etc/passwd", os.path.join(source, "album", "link"))
        return source, contents

    def test_copied_files_and_folders_arrive_as_they_were(self):
        source, contents = self.make()
        self.connect()
        self.laptop.link.accept_files(50 * 1024 * 1024)
        items, sources = flow_files.collect([os.path.join(source, "notes.txt"), os.path.join(source, "album")], 50 * 1024 * 1024)
        self.desk.link.offer_files(items, sources)
        arrived = self.laptop.expect("files", timeout=15)
        self.assertEqual(self.desk.expect("files-sent")["count"], 4)
        self.assertEqual([os.path.basename(path) for path in arrived["paths"]], ["notes.txt", "album"])
        self.assertEqual(arrived["count"], 4)
        base = os.path.dirname(arrived["paths"][0])
        self.assertTrue(base.startswith(os.path.join(self.folder, "at-laptop")))
        for name, data in contents.items():
            with open(os.path.join(base, name), "rb") as f:
                self.assertEqual(f.read(), data, name)
        self.assertTrue(os.access(os.path.join(base, "album/more/run.sh"), os.X_OK))
        self.assertFalse(os.path.lexists(os.path.join(base, "album/link")))  # links are not followed out of what was copied
        self.desk.link.send({"t": "after"})  # the link is as good as before
        self.assertEqual(self.laptop.expect("message")["t"], "after")

    def big(self, megabytes=600):
        """A large file that costs nothing to make (it is all holes)."""
        path = os.path.join(self.folder, "big.bin")
        with open(path, "wb") as f:
            f.truncate(megabytes * 1024 * 1024)
        return path

    def arrived_folders(self):
        base = os.path.join(self.folder, "at-laptop")
        return os.listdir(base) if os.path.isdir(base) else []

    def test_a_transfer_tells_how_far_it_is_and_can_be_given_up_by_the_sender(self):
        self.connect()
        self.laptop.link.accept_files(4 * 1024 ** 3)
        self.desk.link.offer_files(*flow_files.collect([self.big()], 4 * 1024 ** 3))
        self.assertEqual(self.laptop.expect("files-coming")["count"], 1)
        progress = self.desk.expect("files-progress")
        self.assertTrue(progress["sending"] and 0 < progress["done"] < progress["total"])
        self.assertFalse(self.laptop.expect("files-progress")["sending"])
        self.desk.link.cancel_files()
        self.assertEqual(self.desk.expect("files-refused"), "cancelled")
        self.assertEqual(self.laptop.expect("files-declined"), "cancelled")
        self.assertEqual(self.arrived_folders(), [])  # nothing half-written is left behind
        self.desk.link.send({"t": "after"})           # and the link itself was never in the way
        self.assertEqual(self.laptop.expect("message")["t"], "after")

    def test_the_receiver_can_give_it_up_too(self):
        self.connect()
        self.laptop.link.accept_files(4 * 1024 ** 3)
        self.desk.link.offer_files(*flow_files.collect([self.big()], 4 * 1024 ** 3))
        self.laptop.expect("files-progress")
        self.laptop.link.cancel_files()
        self.assertEqual(self.laptop.expect("files-declined"), "cancelled")
        self.assertEqual(self.desk.expect("files-refused"), "failed")
        self.assertEqual(self.arrived_folders(), [])

    def test_words_get_through_while_a_large_file_is_on_its_way(self):
        self.connect()
        self.laptop.link.accept_files(4 * 1024 ** 3)
        self.desk.link.offer_files(*flow_files.collect([self.big(300)], 4 * 1024 ** 3))
        self.desk.expect("files-progress")
        asked = time.monotonic()
        self.desk.link.send({"t": "enter", "edge": "right", "at": 0.5})
        self.assertEqual(self.laptop.expect("message")["t"], "enter")
        self.assertLess(time.monotonic() - asked, 1.0)
        self.assertEqual(self.laptop.expect("files", timeout=30)["total"], 300 * 1024 * 1024)
        self.assertEqual(self.desk.expect("files-sent")["count"], 1)

    def test_a_transfer_under_way_outlives_a_link_that_drops(self):
        self.connect()
        self.laptop.link.accept_files(4 * 1024 ** 3)
        self.desk.link.offer_files(*flow_files.collect([self.big(300)], 4 * 1024 ** 3))
        self.desk.expect("files-progress")
        self.desk.link._call(lambda: self.desk.link._conn.close())  # the link breaks; the files have their own connection
        self.assertEqual(self.laptop.expect("files", timeout=30)["total"], 300 * 1024 * 1024)

    def test_more_than_the_other_side_accepts_is_refused_before_anything_is_sent(self):
        source, _contents = self.make()
        self.connect()
        self.laptop.link.accept_files(1024)
        self.desk.link.offer_files(*flow_files.collect([os.path.join(source, "album")], 50 * 1024 * 1024))
        self.assertEqual(self.desk.expect("files-refused"), "too big")
        self.assertTrue(self.laptop.quiet("files", 0.3))

    def test_files_are_refused_where_they_are_switched_off(self):
        source, _contents = self.make()
        self.connect()
        self.desk.link.offer_files(*flow_files.collect([os.path.join(source, "notes.txt")], 1024 * 1024))
        self.assertEqual(self.desk.expect("files-refused"), "off")


class Collecting(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="mxoptions-test-")
        self.addCleanup(shutil.rmtree, self.folder, True)

    def write(self, name, size=10):
        path = os.path.join(self.folder, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"x" * size)
        return path

    def test_two_things_of_one_name_get_two_names(self):
        items, _sources = flow_files.collect([self.write("a/report.txt"), self.write("b/report.txt")], 1000)
        self.assertEqual([item["path"] for item in items], ["report.txt", "report (2).txt"])

    def test_too_much_is_refused(self):
        with self.assertRaises(flow_files.Refused):
            flow_files.collect([self.write("big.bin", 2000)], 1000)

    def test_names_that_would_leave_the_folder_are_refused(self):
        for bad in ("../x", "/etc/passwd", "a/../../x", "a//b", "", "a/./b", "a\0b", "x" * 300):
            self.assertIsNone(flow_files.safe_parts(bad), bad)
        self.assertEqual(flow_files.safe_parts("album/more/run.sh"), ["album", "more", "run.sh"])
        receiver = flow_files.Receiver(os.path.join(self.folder, "in"))
        for items in ([{"path": "../evil", "size": 1}], [{"path": "ok", "size": -1}], [{"path": "ok", "size": "1"}], "x", []):
            with self.assertRaises(flow_files.Refused):
                receiver.begin("t1", items, 1000)
        self.assertFalse(receiver.busy)

    def test_more_bytes_than_announced_are_refused(self):
        receiver = flow_files.Receiver(os.path.join(self.folder, "in"))
        receiver.begin("t1", [{"path": "small", "size": 4}], 1000)
        receiver.chunk("t1", 0, b"1234")
        with self.assertRaises(flow_files.Refused):
            receiver.chunk("t1", 0, b"5")
        receiver.abort()
        self.assertEqual(os.listdir(os.path.join(self.folder, "in")), [])

    def test_fewer_bytes_than_announced_are_refused(self):
        receiver = flow_files.Receiver(os.path.join(self.folder, "in"))
        receiver.begin("t1", [{"path": "small", "size": 4}], 1000)
        receiver.chunk("t1", 0, b"12")
        with self.assertRaises(flow_files.Refused):
            receiver.end("t1")

    def test_what_nobody_pasted_is_removed_after_a_while(self):
        base = os.path.join(self.folder, "in")
        old = os.path.join(base, "old")
        os.makedirs(old)
        os.utime(old, (time.time() - 2 * flow_files.KEEP_FOR,) * 2)
        os.makedirs(os.path.join(base, "fresh"))
        flow_files.sweep(base)
        self.assertEqual(os.listdir(base), ["fresh"])


class Watcher:
    """Stands in for what follows the mouse's ordinary reports."""

    def __init__(self, last_input=None, tracks_motion=True):
        self.last_input = last_input
        self.tracks_motion = tracks_motion
        self.on_motion = None


def engine_with_hosts(**flow_settings):
    engine, _played = make_engine(flow=dict({"enabled": True}, **flow_settings))
    engine.mouse.host_count = 3
    engine.mouse.sent = []
    engine.mouse.change_host = engine.mouse.sent.append
    engine._hosts = (0, [])
    engine._watcher = Watcher(time.monotonic())  # the mouse is being moved
    return engine


class Reports(unittest.TestCase):
    """The mouse's ordinary reports, as they come over Bluetooth: buttons, 12-bit X and Y, wheel, pan."""

    def setUp(self):
        self.buttons, self.wheel = [], []
        self.reports = hidpp.MouseReports("/nowhere", lambda _w, bits: self.buttons.append(bits),
                                          lambda _w, notches, _when: self.wheel.append(notches))
        self.reports._wheel = 6  # where the wheel is in that layout (no descriptor to read it from here)

    def test_buttons_are_reported_when_they_change(self):
        for report in ("02 00 00 00 10 00 00 00", "02 01 00 00 10 00 00 00", "02 01 00 05 00 00 00 00",
                       "02 00 00 00 00 00 00 00"):
            self.reports.feed(bytes.fromhex(report))
        self.assertEqual(self.buttons, [0x01, 0x00])
        self.assertIsNotNone(self.reports.last_input)

    def test_the_wheel_is_signed(self):
        self.reports.feed(bytes.fromhex("02 00 00 00 00 00 01 00"))
        self.reports.feed(bytes.fromhex("02 00 00 00 00 00 fe 00"))
        self.assertEqual(self.wheel, [1, -2])

    def test_other_reports_are_not_the_mouse_moving(self):
        self.reports.feed(bytes.fromhex("11 ff 0b 00" + "00" * 16))
        self.assertIsNone(self.reports.last_input)

    def test_a_directly_connected_mouse_is_followed_through_its_transport(self):
        class Transport:
            path, on_report = "/nowhere", None

        transport = Transport()
        watcher = hidpp.DirectWatcher(transport, lambda _w, bits: self.buttons.append(bits))
        transport.on_report(bytes.fromhex("02 02 00 00 00 00 00 00"))
        self.assertEqual(self.buttons, [0x02])
        watcher.close()
        self.assertIsNone(transport.on_report)


BLUETOOTH = bytes.fromhex(  # the report descriptor of an MX Master 4 over Bluetooth
    "05010902a10185020901a10095077501150025010509190129078102950981030501a49502750c1600f826ff073600f846ff07550d6513"
    "093009318106b4950175081580257f09388106050c0a38028106c0c00643ff0a0202a101851195137508150026ff000902810009029100c0")


class Layout(unittest.TestCase):
    def test_where_things_are_over_bluetooth(self):
        self.assertEqual(hidpp.mouse_layout(BLUETOOTH), {"wheel": 6, "x": (16, 12), "y": (28, 12)})

    def test_twelve_bit_movement_is_signed(self):
        report = bytes.fromhex("02 00 00 05 f0 ff 00 00")  # x = 5, y = -1
        self.assertEqual((hidpp._signed(report, (16, 12)), hidpp._signed(report, (28, 12))), (5, -1))

    def test_movement_is_passed_on_only_while_somebody_asks(self):
        moves = []
        reports = hidpp.MouseReports("/nowhere", lambda _w, _bits: None)
        reports._xy = ((16, 12), (28, 12))
        reports.feed(bytes.fromhex("02 00 00 05 f0 ff 00 00"))
        reports.on_motion = lambda _w, dx, dy, _when: moves.append((dx, dy))
        reports.feed(bytes.fromhex("02 00 00 05 f0 ff 00 00"))
        reports.feed(bytes.fromhex("02 00 00 00 00 00 01 00"))  # only the wheel turned
        self.assertEqual(moves, [(5, -1)])

    def test_nothing_usable_in_an_empty_descriptor(self):
        self.assertEqual(hidpp.mouse_layout(b""), {"wheel": None, "x": None, "y": None})


class Pushing(unittest.TestCase):
    """The pointer rests against the edge: the mouse has to be pushed on before it is sent across."""

    def setUp(self):
        self.engine = engine_with_hosts()
        self.per_mm = self.engine._counts_per_mm()

    def sent(self):
        return self.engine.mouse.sent

    def push(self, mm, when, dx=1, dy=0):
        self.engine._on_push(dx * mm * self.per_mm, dy * mm * self.per_mm, when)

    def test_a_push_of_the_asked_length_sends_the_mouse(self):
        self.engine._flow_arm((1, "right", 10.0))
        self.assertIsNotNone(self.engine._watcher.on_motion)
        self.push(4, 0.00)
        self.push(4, 0.05)
        self.assertEqual(self.sent(), [])
        self.push(3, 0.10)
        self.assertEqual(self.sent(), [1])
        self.assertIsNone(self.engine._watcher.on_motion)  # nobody listens to the movement any more

    def test_only_movement_towards_the_edge_counts(self):
        self.engine._flow_arm((1, "right", 10.0))
        self.push(30, 0.00, dx=0, dy=1)   # along the edge
        self.push(30, 0.02, dx=-1)        # away from it
        self.push(6, 0.04)
        self.assertEqual(self.sent(), [])
        self.engine._flow_arm((1, "top", 10.0))
        self.push(11, 0.10, dx=0, dy=-1)  # up is negative for the mouse
        self.assertEqual(self.sent(), [1])

    def test_a_pause_starts_the_push_over(self):
        self.engine._flow_arm((1, "right", 10.0))
        self.push(8, 0.00)
        self.push(8, 0.00 + 2 * engine_module.PUSH_PAUSE)
        self.assertEqual(self.sent(), [])
        self.push(3, 0.05 + 2 * engine_module.PUSH_PAUSE)
        self.assertEqual(self.sent(), [1])

    def test_pulling_back_takes_away_from_the_push(self):
        self.engine._flow_arm((1, "right", 10.0))
        self.push(8, 0.00)
        self.push(6, 0.02, dx=-1)
        self.push(6, 0.04)
        self.assertEqual(self.sent(), [])

    def test_leaving_the_edge_calls_it_off(self):
        self.engine._flow_arm((1, "right", 10.0))
        self.push(8, 0.00)
        self.engine._flow_arm(None)
        self.push(8, 0.02)
        self.assertEqual(self.sent(), [])

    def test_with_no_resistance_touching_the_edge_is_enough(self):
        self.engine._flow_arm((1, "right", 0.0))
        self.assertEqual(self.sent(), [1])

    def test_a_held_button_keeps_the_mouse_however_far_it_is_pushed(self):
        self.engine._hid_buttons = 0x01
        self.engine._flow_arm((1, "right", 10.0))
        self.push(50, 0.00)
        self.assertEqual(self.sent(), [])

    def test_where_movement_cannot_be_followed_resting_at_the_edge_does_it(self):
        self.engine._watcher = Watcher(time.monotonic(), tracks_motion=False)
        self.engine._flow_arm((1, "right", 10.0))
        self.assertEqual(self.sent(), [])
        self.engine._push["deadline"] = time.monotonic() - 0.01
        self.engine._tick()
        self.assertEqual(self.sent(), [1])


class Leaving(unittest.TestCase):
    def outcome(self, engine):
        return [payload for kind, payload in engine.listener.events if kind == "flow"]

    def test_the_mouse_is_sent_to_the_other_channel(self):
        engine = engine_with_hosts()
        engine._flow_leave(1, True)
        self.assertEqual(engine.mouse.sent, [1])
        self.assertEqual(self.outcome(engine), [("left", 1)])

    def test_not_while_a_button_is_held(self):
        engine = engine_with_hosts()
        engine._hid_buttons = 0x01  # the left button: a drag in progress
        engine._flow_leave(1, True)
        self.assertEqual(engine.mouse.sent, [])
        self.assertEqual(self.outcome(engine), [("refused", "held")])

    def test_not_when_something_else_pushed_the_pointer_to_the_edge(self):
        engine = engine_with_hosts()
        engine._watcher.last_input = time.monotonic() - 5.0  # the mouse has been lying still
        engine._flow_leave(1, True)
        self.assertEqual(self.outcome(engine), [("refused", "idle")])
        engine._flow_leave(1, False)  # asked for with a button: no pointer movement is needed
        self.assertEqual(engine.mouse.sent, [1])

    def test_not_to_its_own_or_a_missing_channel(self):
        engine = engine_with_hosts()
        for channel in (0, 3, -1):
            engine._flow_leave(channel, False)
        self.assertEqual(engine.mouse.sent, [])
        self.assertEqual(self.outcome(engine), [("refused", "channel")] * 3)

    def test_where_the_mouse_cannot_be_followed_the_edge_is_taken_on_trust(self):
        engine = engine_with_hosts()
        engine._watcher = None
        engine._flow_leave(1, True)
        self.assertEqual(engine.mouse.sent, [1])

    def test_not_when_the_mouse_is_away(self):
        engine = engine_with_hosts()
        engine.online = False
        engine._flow_leave(1, False)
        self.assertEqual(self.outcome(engine), [("refused", "away")])

    def test_an_action_asks_the_flow_service(self):
        engine = engine_with_hosts()
        engine._device_action({"type": "flow"}, "trigger")
        self.assertEqual(self.outcome(engine), [("asked", None)])


if __name__ == "__main__":
    unittest.main()
