"""Flow: the pairing key, the geometry of crossing from one desktop to another, the link between
two computers (here: two links on this one), and the engine's part in handing the mouse over."""

import asyncio
import copy
import ctypes
import gc
import json
import os
import queue
import shutil
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
import warnings
from unittest import mock

from tests.support import make_engine
from mxoptions import engine as engine_module, flow, flow_files, hidpp
import mxoptions
from mxoptions import environment, x11

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
        self.pairs = queue.Queue()  # which pairing each event was about
        self.link = flow.Link(self.hear, staging=staging)
        self.link.set_status({"name": name, "channel": 0, "present": False})
        self.port = free_port()
        self.link.start()

    def hear(self, kind, payload, pair=""):
        self.heard.put((kind, payload))
        self.pairs.put((kind, pair))

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

    def test_a_stall_is_reported_and_the_link_survives_it(self):
        for name, value in (("PING_INTERVAL", 0.1), ("QUIET_AFTER", 0.3), ("DEAD_AFTER", 30.0)):
            self.addCleanup(setattr, flow, name, getattr(flow, name))
            setattr(flow, name, value)
        self.connect()
        self.laptop.link._call(time.sleep, 1.2)  # the laptop answers nothing for a while, as over a network that stalls
        self.assertIs(self.desk.expect("quiet"), True)
        self.assertIs(self.desk.expect("quiet"), False)
        self.assertTrue(self.desk.quiet("down", 0.3))
        self.desk.link.send({"t": "after"})
        self.assertEqual(self.laptop.expect("message")["t"], "after")

    @unittest.skipIf(sys.platform == "win32", "Windows does not carry a broadcast between two programs on one machine, which is how they find each other here")
    def test_with_no_address_given_they_find_each_other(self):
        self.desk.link.configure(KEY, self.desk.port, "")
        self.laptop.link.configure(KEY, self.laptop.port, "")
        self.assertEqual(self.desk.expect("up")["name"], "laptop")
        self.assertEqual(self.laptop.expect("up")["name"], "desk")

    def test_a_computer_with_another_key_is_not_found(self):
        self.desk.link.configure(KEY, self.desk.port, "")
        self.laptop.link.configure("ZZZZ-ZZZZ-ZZZZ-ZZZZ", self.laptop.port, "")
        self.assertTrue(self.desk.quiet("up", 1.2))


OTHER_KEY = "QRST-UVWX-YZ23-4567"


@unittest.skipUnless(flow.supported(), "this Python has no pre-shared keys for TLS")
class ThreeComputers(TwoComputers):
    """The desk in the middle, linked with the laptop and with a tower, each pairing with a key of its own."""

    def setUp(self):
        super().setUp()
        self.tower = End("tower", os.path.join(self.folder, "at-tower"))
        self.addCleanup(self.tower.link.stop)
        self.with_laptop, self.with_tower = flow.pair_id(KEY), flow.pair_id(OTHER_KEY)
        self.desk.link.configure([{"key": KEY, "address": ""}, {"key": OTHER_KEY, "address": ""}], self.desk.port)
        self.laptop.link.configure(KEY, self.laptop.port, f"127.0.0.1:{self.desk.port}")
        self.tower.link.configure(OTHER_KEY, self.tower.port, f"127.0.0.1:{self.desk.port}")
        self.assertEqual(self.laptop.expect("up")["name"], "desk")
        self.assertEqual(self.tower.expect("up")["name"], "desk")
        time.sleep(0.5)

    def test_the_one_in_the_middle_is_linked_with_both_and_knows_which_is_which(self):
        ups = {}
        while len(ups) < 2:
            kind, pair = self.desk.pairs.get(timeout=5)
            if kind == "up":
                ups[pair] = True
        self.assertEqual(set(ups), {self.with_laptop, self.with_tower})

    def test_a_word_goes_to_the_one_it_is_meant_for(self):
        self.desk.link.send({"t": "enter", "edge": "left", "at": 0.5}, pair=self.with_laptop)
        self.assertEqual(self.laptop.expect("message")["t"], "enter")
        self.assertTrue(self.tower.quiet("message", 0.4))
        self.desk.link.send({"t": "note"})  # to all
        self.assertEqual(self.tower.expect("message")["t"], "note")
        self.assertEqual(self.laptop.expect("message")["t"], "note")

    def test_each_is_told_what_is_meant_for_it(self):
        self.desk.link.set_status({"name": "desk", "channel": 0}, {self.with_laptop: {"edge": "left"}, self.with_tower: {"edge": "right"}})
        def edge_told(computer):  # what it said when the link came up, with no edge in it yet, may still be on its way
            while True:
                status = computer.expect("status")
                if "edge" in status:
                    return status["edge"]

        self.assertEqual(edge_told(self.laptop), "left")
        self.assertEqual(edge_told(self.tower), "right")

    def test_files_go_to_one_of_the_two(self):
        source = os.path.join(self.folder, "note.txt")
        with open(source, "w") as f:
            f.write("for the tower")
        self.tower.link.accept_files(1024 * 1024)
        self.laptop.link.accept_files(1024 * 1024)
        self.desk.link.offer_files(*flow_files.collect([source], 1024 * 1024), pair=self.with_tower)
        arrived = self.tower.expect("files", timeout=15)
        with open(arrived["paths"][0]) as f:
            self.assertEqual(f.read(), "for the tower")
        self.assertTrue(self.laptop.quiet("files", 0.3))

    def test_the_two_at_the_ends_are_not_linked_with_each_other(self):
        self.laptop.link.send({"t": "note"})
        self.assertEqual(self.desk.expect("message")["t"], "note")
        self.assertTrue(self.tower.quiet("message", 0.4))


class NamesFromElsewhere(unittest.TestCase):
    def test_linux_takes_them_as_they_are(self):
        self.assertEqual(flow_files.local_name('draft: "final"?.txt', windows=False), 'draft: "final"?.txt')

    def test_windows_gets_names_it_can_write(self):
        self.assertEqual(flow_files.local_name('draft: "final"?.txt', windows=True), "draft_ _final__.txt")
        self.assertEqual(flow_files.local_name("звіт.txt", windows=True), "звіт.txt")
        self.assertEqual(flow_files.local_name("notes. ", windows=True), "notes")
        self.assertEqual(flow_files.local_name("NUL", windows=True), "_NUL")
        self.assertEqual(flow_files.local_name("com1.txt", windows=True), "_com1.txt")
        self.assertEqual(flow_files.local_name("...", windows=True), "_")


class Beacons(unittest.TestCase):
    def test_a_signed_call_is_read_only_with_the_key_and_in_time(self):
        data = json.dumps(flow.here_beacon(KEY, "abc", 5000, when=1000)).encode()
        self.assertEqual(flow.read_beacon(data, KEY, now=1050),
                         {"t": "here", "pair": flow.pair_id(KEY), "id": "abc", "port": 5000})
        self.assertIsNotNone(flow.read_beacon(data, {flow.pair_id(KEY): KEY, "other": "ZZZZ-ZZZZ-ZZZZ-ZZZZ"}, now=1050))
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

    @unittest.skipIf(sys.platform == "win32", "Windows does not carry a broadcast between two programs on one machine, which is how they find each other here")
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

    def slow(self, end, seconds=0.002):
        """Make ``end`` take its time over every piece that arrives. Between two programs on one machine even a
        large file is across before a test has looked twice, and then there is nothing left to give up."""
        receiver = end.link._receiver
        take = receiver.chunk

        def chunk(*args):
            time.sleep(seconds)
            return take(*args)

        receiver.chunk = chunk

    def test_a_transfer_tells_how_far_it_is_and_can_be_given_up_by_the_sender(self):
        self.connect()
        self.slow(self.laptop)
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
        self.slow(self.laptop)
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
        seen, deadline = {}, time.monotonic() + 30
        while len(seen) < 2 and time.monotonic() < deadline:  # in whichever order the two come
            try:
                kind, payload = self.laptop.heard.get(timeout=0.2)
            except queue.Empty:
                continue
            if kind == "message":
                seen["word"] = time.monotonic() - asked
            elif kind == "files":
                seen["files"] = payload
        self.assertLess(seen["word"], 3.0)  # at once, not after the file
        self.assertEqual(seen["files"]["total"], 300 * 1024 * 1024)
        self.assertEqual(self.desk.expect("files-sent")["count"], 1)

    def test_a_transfer_under_way_outlives_a_link_that_drops(self):
        self.connect()
        self.laptop.link.accept_files(4 * 1024 ** 3)
        self.desk.link.offer_files(*flow_files.collect([self.big(300)], 4 * 1024 ** 3))
        self.desk.expect("files-progress")
        self.desk.link._call(lambda: next(iter(self.desk.link._conns.values())).close())  # the link breaks; the files have their own connection
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

    def test_giving_up_another_computer_s_transfer_leaves_this_one_alone(self):
        self.connect()
        self.slow(self.laptop)
        self.laptop.link.accept_files(4 * 1024 ** 3)
        self.desk.link.offer_files(*flow_files.collect([self.big()], 4 * 1024 ** 3))
        self.desk.expect("files-progress")
        self.desk.link.cancel_files([flow.pair_id(OTHER_KEY)])  # a computer these files are not going to
        self.assertTrue(self.desk.quiet("files-refused", 0.5))
        self.desk.link.cancel_files([flow.pair_id(KEY)])
        self.assertEqual(self.desk.expect("files-refused"), "cancelled")
        self.assertEqual(self.laptop.expect("files-declined"), "cancelled")
        self.assertEqual(self.arrived_folders(), [])

    def test_the_receiver_gives_up_only_that_computer_s_transfer(self):
        self.connect()
        self.slow(self.laptop)
        self.laptop.link.accept_files(4 * 1024 ** 3)
        self.desk.link.offer_files(*flow_files.collect([self.big()], 4 * 1024 ** 3))
        self.laptop.expect("files-progress")
        self.laptop.link.cancel_files([flow.pair_id(OTHER_KEY)])
        self.assertTrue(self.laptop.quiet("files-declined", 0.5))
        self.laptop.link.cancel_files([flow.pair_id(KEY)])
        self.assertEqual(self.laptop.expect("files-declined"), "cancelled")
        self.assertEqual(self.desk.expect("files-refused"), "failed")
        self.assertEqual(self.arrived_folders(), [])

    def one_mebibyte(self):
        """A file too large to go along with the offer: it travels on a connection of its own, in 16 pieces."""
        path = os.path.join(self.folder, "one.bin")
        with open(path, "wb") as f:
            f.write(os.urandom(1024 * 1024))
        return path

    def test_a_transfer_given_up_takes_nothing_more_that_was_on_its_way(self):
        self.connect()
        link = self.laptop.link
        receiver, take = link._receiver, link._receiver.chunk

        def chunk(*args):  # the last piece is written; its end is on its way or here already
            took = take(*args)
            if receiver.done == receiver.total:
                conn = link._arriving
                link._cancel_files()
                conn.reader.feed_data(b'{"t":"files-end"}\n')  # read off the socket before it was closed, not acted on
            return took

        receiver.chunk = chunk
        link.accept_files(4 * 1024 * 1024)
        self.desk.link.offer_files(*flow_files.collect([self.one_mebibyte()], 4 * 1024 * 1024))
        self.assertEqual(self.laptop.expect("files-declined", timeout=15), "cancelled")
        self.assertTrue(self.laptop.quiet("files", 0.5))
        self.assertEqual(self.arrived_folders(), [])

    def test_files_in_place_are_said_before_the_answer_goes(self):
        self.connect()
        link, said, told = self.laptop.link, [], []
        hear, receive = link._on_event, link._receive_files

        def on_event(kind, payload, pair=""):
            said.append(kind)
            hear(kind, payload, pair)

        async def receive_files(conn, hello):
            send = conn.send

            def answer(message, data=None):
                if message.get("t") == "files-got":
                    told.append("files" in said)
                send(message, data)

            conn.send = answer
            await receive(conn, hello)

        link._on_event, link._receive_files = on_event, receive_files
        link.accept_files(4 * 1024 * 1024)
        self.desk.link.offer_files(*flow_files.collect([self.one_mebibyte()], 4 * 1024 * 1024))
        self.laptop.expect("files", timeout=15)
        self.assertEqual(told, [True])


def on_link(link, function):
    """Run ``function`` on the link's own thread, between two of its steps, and return what it gives."""
    done = queue.Queue()
    link._call(lambda: done.put(function()))
    return done.get(timeout=5)


class Held:
    """A call whose reading is held: from now on it is given only the lines put in ``lines`` (an exception there is
    raised instead), and only once let go. Its connection and what the other side sends on it are left alone: lines
    that were on their way and not yet acted on. Used on the link's own thread, through on_link."""

    def __init__(self, conn):
        self.lines = []
        self.reading = threading.Event()  # set once the call waits for its next line here
        self._go = asyncio.Event()
        conn.read = self.read

    def let_go(self, *lines):
        self.lines.extend(lines)
        self._go.set()

    async def read(self):
        self.reading.set()
        while True:
            await self._go.wait()
            if self.lines:
                line = self.lines.pop(0)
                if isinstance(line, BaseException):
                    raise line
                return line
            self._go.clear()


class HeldData:
    """A call whose bytes behind a message are read as they come and then held until let go: the message has been
    read, what rides behind it is in, and the call waits for it. Used on the link's own thread, but ``reached``."""

    def __init__(self, conn):
        self.reached = queue.Queue()  # the message whose bytes are held, as soon as they are
        self._go = asyncio.Event()
        self._read_data = conn.read_data
        conn.read_data = self.read_data

    def let_go(self):
        self._go.set()

    async def read_data(self, message):
        data = await self._read_data(message)
        if data:
            self.reached.put(dict(message))
            await self._go.wait()
        return data


STALE = [{"t": "status", "name": "stale", "channel": 9, "present": True}, {"t": "clipboard", "text": "stale"},
         {"t": "files", "id": "stale1", "items": [{"path": "d", "dir": True}], "inline": True},
         {"t": "files", "id": "stale2", "items": [{"path": "a.txt", "size": 3}]}]


@unittest.skipUnless(flow.supported(), "this Python has no pre-shared keys for TLS")
class TakenOut(TwoComputers):
    """The desk takes the laptop out (pause, forget, Flow off) while a call of the laptop still has words on their
    way: nothing of them is taken, whenever they come, not even once the laptop is back. Only the laptop calls here,
    so that the desk's end of every call is one it was called on, which outlives the desk's own calling."""

    def setUp(self):
        super().setUp()
        for name, value in (("PING_INTERVAL", 60.0), ("broadcast_addresses", lambda: [])):  # no "are you there" in the middle
            self.addCleanup(setattr, flow, name, getattr(flow, name))
            setattr(flow, name, value)
        self.pair = flow.pair_id(KEY)

    def call(self):
        """The call in use at the desk, which the laptop made."""
        conn = on_link(self.desk.link, lambda: self.desk.link._conns[self.pair])
        self.assertFalse(conn.dialed)
        return conn

    def take_out(self, mark):
        """The desk takes the laptop out and has it back at once, as a pause and a resume do."""
        self.desk.link.configure(None)
        self.desk.link.configure(KEY, self.desk.port, "", mark=mark)
        self.assertEqual(self.desk.expect("mark"), mark)
        self.desk.expect("up")  # the laptop has called again: the call in use is a new one

    def keep(self):
        """The desk changes its settings but keeps the laptop, as when the address or another computer changes."""
        self.desk.link.configure(KEY, self.desk.port, "127.0.0.1:1")

    def heard(self, for_=1.0):
        """Everything the desk hears for a while, as (kind, payload)."""
        found, deadline = [], time.monotonic() + for_
        while time.monotonic() < deadline:
            try:
                found.append(self.desk.heard.get(timeout=0.05))
            except queue.Empty:
                pass
        return found

    def held(self, conn):
        """Hold the reading of ``conn``, once the read under way has returned."""
        held = on_link(self.desk.link, lambda: Held(conn))
        self.laptop.link.send({"t": "cycle"})
        self.assertEqual(self.desk.expect("message")["t"], "cycle")
        self.assertTrue(held.reading.wait(5))
        return held

    def test_a_computer_taken_out_says_nothing_more_on_its_old_call(self):
        self.connect()

        def late(settings):
            conn = self.desk.link._conns[self.pair]
            self.desk.link._apply(settings, 7)
            conn.reader.feed_data(b'{"t":"late"}\n')  # read off the socket before it was closed, not yet acted on

        self.call()
        on_link(self.desk.link, lambda: late(None))
        kinds = [kind for kind, _payload in self.heard()]
        self.assertIn("down", kinds)
        self.assertLess(kinds.index("mark"), kinds.index("down"))
        self.assertNotIn("message", kinds)
        self.desk.link.configure(KEY, self.desk.port, "")
        self.desk.expect("up")
        self.call()
        on_link(self.desk.link, lambda: late((self.desk.port, ((self.pair, KEY, "127.0.0.1:1"),))))  # it stays
        self.assertEqual(self.desk.expect("message")["t"], "late")

    def test_a_call_replaced_before_the_pause_says_nothing_after_resume(self):
        for cycles in (1, 2):
            with self.subTest(cycles=cycles):
                if cycles > 1:  # afresh
                    for end in (self.desk, self.laptop):
                        end.link.stop()
                    self.desk = End("desk", os.path.join(self.folder, "at-desk"))
                    self.laptop = End("laptop", os.path.join(self.folder, "at-laptop"))
                    self.addCleanup(self.desk.link.stop)
                    self.addCleanup(self.laptop.link.stop)
                self.connect()
                self.desk.link.accept_files(1 << 20)
                helds = []
                for cycle in range(cycles):
                    conn = self.call()
                    held = self.held(conn)
                    on_link(self.desk.link, lambda: held.let_go({"t": "warm"}))
                    self.assertEqual(self.desk.expect("message")["t"], "warm")  # a held call does deliver
                    on_link(self.desk.link, lambda: held.lines.extend(STALE))
                    on_link(self.laptop.link, lambda: [call.close() for call in self.laptop.link._conns.values()])
                    self.desk.expect("up")  # the laptop called again, and that call took over
                    self.assertIsNot(self.call(), conn)
                    self.take_out(7 + cycle)
                    helds.append(held)
                for held in helds:
                    on_link(self.desk.link, held.let_go)
                for kind, payload in self.heard():
                    self.assertNotIn(kind, ("files", "files-coming", "files-declined"))
                    self.assertNotIn("stale", (payload.get("name"), payload.get("text")) if isinstance(payload, dict) else ())
                self.assertFalse(on_link(self.desk.link, lambda: self.desk.link._receiver.busy))
                self.laptop.link.set_status({"name": "fresh", "channel": 3, "present": False})
                while self.desk.expect("status")["name"] != "fresh":
                    pass
                self.laptop.link.send({"t": "clipboard", "text": "fresh"})
                self.assertEqual(self.desk.expect("message")["text"], "fresh")
                self.laptop.link.offer_files([{"path": "d", "dir": True}], [None])
                self.assertEqual(len(self.desk.expect("files")["paths"]), 1)

    def test_bytes_still_on_their_way_when_its_computer_is_taken_out_are_not_taken(self):
        self.connect()
        self.desk.link.accept_files(1 << 20)
        stale = [({"t": "clip", "mime": "image/png"}, b"stale picture"),
                 ({"t": "files", "id": "stale1", "items": [{"path": "a.txt", "size": 5}], "inline": True}, b"stale")]
        for cycle, (message, data) in enumerate(stale):
            conn = self.call()
            held = on_link(self.desk.link, lambda: HeldData(conn))
            self.laptop.link.send(message, data)
            self.assertEqual(held.reached.get(timeout=5)["t"], message["t"])  # read, its bytes in, and held
            self.take_out(7 + cycle)
            on_link(self.desk.link, held.let_go)
            kinds = [kind for kind, _payload in self.heard()]
            self.assertFalse(set(kinds) & {"message", "files", "files-coming", "files-declined"}, kinds)
            self.assertFalse(on_link(self.desk.link, lambda: self.desk.link._receiver.busy))
        conn = self.call()  # the laptop stays: what it sends is taken, bytes and all, however the settings change
        held = on_link(self.desk.link, lambda: HeldData(conn))
        self.laptop.link.send({"t": "clip", "mime": "image/png"}, b"kept picture")
        held.reached.get(timeout=5)
        self.keep()
        on_link(self.desk.link, held.let_go)
        self.assertEqual(self.desk.expect("message")["data"], b"kept picture")

    def test_a_read_that_runs_out_of_time_after_its_computer_was_taken_out_says_nothing(self):
        self.addCleanup(setattr, flow, "QUIET_AFTER", flow.QUIET_AFTER)
        flow.QUIET_AFTER = 0.0  # any time without a word counts: a read running out of time is a stall at once
        self.connect()
        held = self.held(self.call())
        on_link(self.desk.link, lambda: held.let_go(asyncio.TimeoutError(), {"t": "warm"}))
        self.assertIs(self.desk.expect("quiet"), True)  # the call in use: as always, a stall, and then it is back
        self.assertIs(self.desk.expect("quiet"), False)
        self.assertEqual(self.desk.expect("message")["t"], "warm")
        self.take_out(7)
        on_link(self.desk.link, lambda: held.let_go(asyncio.TimeoutError(), dict(STALE[0])))
        found = [(kind, payload) for kind, payload in self.heard() if kind in ("quiet", "status", "message")]
        self.assertEqual(found, [])
        self.laptop.link.set_status({"name": "fresh", "channel": 3, "present": False})
        while self.desk.expect("status")["name"] != "fresh":
            pass
        held = self.held(self.call())  # the laptop stays: its call runs out of time as always, settings changed or not
        self.keep()
        on_link(self.desk.link, lambda: held.let_go(asyncio.TimeoutError()))
        self.assertIs(self.desk.expect("quiet"), True)


def port_free(port):
    """Nothing here or elsewhere holds ``port``: it can be bound and listened on, as the link does it."""
    for family, address in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
        probe = socket.socket(family)
        try:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            probe.bind((address, port))
            probe.listen()
        except OSError:
            return False
        finally:
            probe.close()
    return True


@unittest.skipUnless(flow.supported(), "this Python has no pre-shared keys for TLS")
class OpeningThePort(TwoComputers):
    """The desk's settings change while it is still opening its port, as a pause and a resume at once do. The
    opening is held at a wait start_server really has: looking up the address (before any socket), or the one
    after the bind (start_server's own when it serves at once, start_serving's otherwise). Only the laptop and the
    tower call, so that only the desk's listening gets them in; nothing here closes a socket of the desk's."""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, flow, "broadcast_addresses", flow.broadcast_addresses)
        flow.broadcast_addresses = lambda: []  # no "here I am": the desk never calls them itself
        self.held, self._go = threading.Event(), asyncio.Event()
        self.addCleanup(self.let_go)

    def hold_after_binding(self):
        """Hold the desk's first wait after binding its port, until let_go()."""
        sleep, thread, armed = asyncio.tasks.sleep, self.desk.link._thread, [True]

        async def held(result):
            self.held.set()
            await self._go.wait()
            return await sleep(0, result)

        def gated(delay, result=None):
            caller = sys._getframe(1).f_code
            if (armed[0] and delay == 0 and threading.current_thread() is thread
                    and caller.co_filename == asyncio.base_events.__file__
                    and caller.co_name in ("create_server", "start_serving")):
                armed[0] = False
                return held(result)
            return sleep(delay, result)
        self.addCleanup(setattr, asyncio.tasks, "sleep", sleep)
        asyncio.tasks.sleep = gated

    def hold_looking_up(self):
        """Hold the desk's first look-up of the address to listen on, until let_go(): no socket is open yet."""
        loop, armed = self.desk.link._loop, [True]
        getaddrinfo = loop.getaddrinfo

        async def gated(host, port, **options):
            if armed[0] and host is None:
                armed[0] = False
                self.held.set()
                await self._go.wait()
            return await getaddrinfo(host, port, **options)
        loop.getaddrinfo = gated

    def let_go(self):
        try:
            self.desk.link._loop.call_soon_threadsafe(self._go.set)
        except RuntimeError:  # stopped: nothing waits any more
            pass

    def peers(self, *keys):
        return [{"key": key, "address": ""} for key in keys]

    def pause_and_resume(self, *kept):
        """Switch the desk on, and while its port opens take the laptop out and have it back, as Pause and Resume do."""
        self.desk.link.configure(self.peers(KEY, *kept), self.desk.port)
        self.assertTrue(self.held.wait(5))
        self.desk.link.configure(self.peers(*kept) or None, self.desk.port)
        self.desk.link.configure(self.peers(KEY, *kept), self.desk.port, mark=1)
        self.assertEqual(self.desk.expect("mark"), 1)

    def problems(self, for_=0.6):
        found, deadline = [], time.monotonic() + for_
        while time.monotonic() < deadline:
            try:
                kind, payload = self.desk.heard.get(timeout=0.05)
            except queue.Empty:
                continue
            if kind == "problem":
                found.append(payload)
        return found

    def listening(self):
        """The desk's own listening, and any task of it still opening a port."""
        def look():
            link = self.desk.link
            opening = [task for task in asyncio.all_tasks(link._loop)
                       if task.get_coro().__qualname__ == "Link._listen" and not task.done()]
            return link._server, opening
        return on_link(self.desk.link, look)

    def assert_listening_for(self, *ends):
        """The desk listens on its port alone, says it can, lets each of ``ends`` in, and frees the port when it stops."""
        self.assertEqual(self.desk.expect("problem"), "")
        self.let_go()
        self.assertEqual(self.problems(), [])  # the older opening says nothing, not even later
        server, opening = self.listening()
        self.assertIsNotNone(server)
        self.assertEqual({sock.getsockname()[1] for sock in server.sockets}, {self.desk.port})
        self.assertEqual(opening, [])
        for end, key in ends:
            end.link.configure(key, end.port, f"127.0.0.1:{self.desk.port}")
            self.assertEqual(end.expect("up")["name"], "desk")
        self.desk.link.configure(self.peers(*(key for _end, key in ends)), self.desk.port)  # the same again
        self.assertTrue(self.desk.quiet("problem", 0.3))
        self.assertIs(self.listening()[0], server)
        self.assert_stops_and_frees_its_port()

    def assert_stops_and_frees_its_port(self):
        """Stopped, the desk has closed every socket it opened itself: none is left for the collector to find."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            self.desk.link.stop()
            gc.collect()
        self.assertFalse(self.desk.link._thread.is_alive())
        self.assertEqual([str(w.message) for w in caught if issubclass(w.category, ResourceWarning)], [])
        self.assertTrue(port_free(self.desk.port))

    def test_resumed_while_its_port_was_bound_and_not_yet_its_own(self):
        self.hold_after_binding()
        self.pause_and_resume()
        self.assert_listening_for((self.laptop, KEY))

    def test_resumed_while_its_port_was_looked_up_and_the_old_opening_stays_silent(self):
        self.hold_looking_up()
        self.pause_and_resume()
        self.assert_listening_for((self.laptop, KEY))

    def test_the_computer_kept_through_the_pause_and_the_one_resumed_both_get_in(self):
        tower = End("tower", os.path.join(self.folder, "at-tower"))
        self.addCleanup(tower.link.stop)
        self.hold_after_binding()
        self.pause_and_resume(OTHER_KEY)
        self.assert_listening_for((self.laptop, KEY), (tower, OTHER_KEY))

    def test_a_port_taken_meanwhile_stays_the_problem(self):
        self.hold_after_binding()
        self.desk.link.configure(KEY, self.desk.port, "")
        self.assertTrue(self.held.wait(5))
        self.desk.link.configure(None)
        on_link(self.desk.link, lambda: None)  # taken out: the desk no longer holds its port
        taken = socket.socket()
        self.addCleanup(taken.close)
        taken.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        taken.bind(("0.0.0.0", self.desk.port))
        taken.listen()
        with self.assertLogs("mxoptions.flow", "WARNING"):
            self.desk.link.configure(KEY, self.desk.port, "")
            self.assertTrue(self.desk.expect("problem"))
        self.let_go()
        self.assertEqual(self.problems(), [])  # the opening overtaken does not say it listens
        self.assertIsNone(self.listening()[0])

    def test_stopped_while_its_port_opens(self):
        for hold in ("hold_after_binding", "hold_looking_up"):
            with self.subTest(hold=hold):
                self.desk = End("desk", os.path.join(self.folder, f"at-desk-{hold}"))
                self.addCleanup(self.desk.link.stop)
                self.held, self._go = threading.Event(), asyncio.Event()
                getattr(self, hold)()
                self.desk.link.configure(KEY, self.desk.port, "")
                self.assertTrue(self.held.wait(5))
                self.assert_stops_and_frees_its_port()


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


class HoldingCtrl(unittest.TestCase):
    """Flow, "Cross only while Ctrl is held": the edge sends the mouse across only while Ctrl is known to be down.
    What the system says of Ctrl is made up here and counted; nothing real is asked."""

    def setUp(self):
        self.engine = engine_with_hosts(hold_ctrl=True)
        self.per_mm = self.engine._counts_per_mm()
        self.held, self.asked = None, 0

        def ctrl_held():
            self.asked += 1
            return self.held

        patch = mock.patch.object(environment, "ctrl_held", ctrl_held)
        patch.start()
        self.addCleanup(patch.stop)

    def sent(self):
        return self.engine.mouse.sent

    def said(self):
        return [payload for kind, payload in self.engine.listener.events if kind == "flow"]

    def push(self, mm, when):
        self.engine._on_push(mm * self.per_mm, 0, when)

    def test_off_the_edge_crosses_without_asking(self):
        self.held = False  # it would refuse, were it asked
        for engine in (engine_with_hosts(), engine_with_hosts(hold_ctrl=False)):
            self.engine = engine
            engine._flow_arm((1, "right", 10.0))
            self.push(11, 0.0)
            engine._flow_arm((1, "right", 0.0))  # at a touch
            engine._watcher = Watcher(time.monotonic(), tracks_motion=False)
            engine._flow_arm((1, "right", 10.0))  # resting at the edge
            engine._push["deadline"] = time.monotonic() - 0.01
            engine._tick()
            self.assertEqual(engine.mouse.sent, [1, 1, 1])
            self.assertEqual(self.said(), [("left", 1)] * 3)
        self.assertEqual(self.asked, 0)

    def test_with_ctrl_held_the_push_crosses(self):
        self.held = True
        self.engine._flow_arm((1, "right", 10.0))
        self.push(6, 0.00)
        self.assertEqual(self.asked, 0)  # asked only once the push is enough
        self.push(5, 0.05)
        self.assertEqual(self.sent(), [1])
        self.assertEqual(self.said(), [("left", 1)])
        self.assertEqual(self.asked, 1)
        self.assertIsNone(self.engine._push)
        self.engine._flow_arm((1, "right", 0.0))  # at a touch, Ctrl held: at once
        self.assertEqual(self.sent(), [1, 1])

    def test_without_ctrl_no_push_crosses_and_nothing_is_said(self):
        self.held = False
        self.engine._flow_arm((1, "right", 10.0))
        for step in range(5):
            self.push(11, 0.05 * step)
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.said(), [])
        self.assertEqual(self.asked, 5)
        self.assertIsNotNone(self.engine._push)  # still armed
        self.assertIsNotNone(self.engine._watcher.on_motion)
        self.assertEqual(self.engine._push["got"], 0.0)

    def test_when_ctrl_cannot_be_told_the_edge_keeps_the_mouse(self):
        self.held = None
        self.engine._flow_arm((1, "right", 10.0))
        self.push(11, 0.00)
        self.engine._flow_arm((1, "right", 0.0))
        self.engine._watcher = Watcher(time.monotonic(), tracks_motion=False)
        self.engine._flow_arm((1, "right", 10.0))
        self.engine._push["deadline"] = time.monotonic() - 0.01
        self.engine._tick()
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.said(), [])
        self.assertEqual(self.asked, 3)

    def test_at_a_touch_ctrl_is_asked_and_a_little_more_push_asks_again(self):
        self.held = False
        self.engine._flow_arm((1, "right", 0.0))
        self.assertEqual((self.sent(), self.asked), ([], 1))
        self.assertAlmostEqual(self.engine._push["need"], engine_module.CTRL_AGAIN_MM * self.per_mm)
        self.assertIsNotNone(self.engine._watcher.on_motion)
        self.push(0.5, 0.00)
        self.assertEqual(self.asked, 1)
        self.held = True
        self.push(0.6, 0.02)
        self.assertEqual(self.sent(), [1])
        self.assertEqual(self.asked, 2)
        self.engine._watcher = Watcher(time.monotonic(), tracks_motion=False)  # and where only resting can do
        self.held = False
        self.engine._flow_arm((1, "right", 0.0))
        self.assertEqual(self.sent(), [1])
        push = self.engine._push
        self.assertAlmostEqual(push["dwell"], engine_module.PUSH_DWELL + engine_module.CTRL_AGAIN_MM * engine_module.PUSH_DWELL_PER_MM)
        self.held = True
        push["deadline"] = time.monotonic() - 0.01
        self.engine._tick()
        self.assertEqual(self.sent(), [1, 1])

    def test_resting_at_the_edge_waits_for_ctrl(self):
        self.engine._watcher = Watcher(time.monotonic(), tracks_motion=False)
        self.held = False
        self.engine._flow_arm((1, "right", 10.0))
        push = self.engine._push
        self.assertEqual(push["dwell"], engine_module.PUSH_DWELL + 10.0 * engine_module.PUSH_DWELL_PER_MM)
        push["deadline"] = time.monotonic() - 0.01
        before = time.monotonic()
        self.engine._tick()
        self.assertEqual((self.sent(), self.said(), self.asked), ([], [], 1))
        self.assertIs(self.engine._push, push)
        self.assertGreaterEqual(push["deadline"], before + push["dwell"])  # asked again only after another dwell
        self.engine._tick()
        self.assertEqual(self.asked, 1)
        self.held = True
        push["deadline"] = time.monotonic() - 0.01
        self.engine._tick()
        self.assertEqual(self.sent(), [1])

    def test_ctrl_let_go_before_the_push_is_enough_keeps_the_mouse(self):
        self.held = True
        self.engine._flow_arm((1, "right", 10.0))
        self.push(6, 0.00)
        self.held = False  # let go while pushing
        self.push(5, 0.05)
        self.assertEqual((self.sent(), self.said(), self.asked), ([], [], 1))
        self.assertIsNotNone(self.engine._push)

    def test_ctrl_pressed_after_a_refusal_needs_a_whole_push(self):
        self.held = False
        self.engine._flow_arm((1, "right", 10.0))
        self.push(11, 0.00)
        self.assertEqual(self.sent(), [])
        self.held = True  # pressed while resting at the edge: no new report from the edge needed
        self.push(5, 0.05)
        self.assertEqual(self.sent(), [])  # what was pushed before the refusal does not count
        self.push(6, 0.10)
        self.assertEqual(self.sent(), [1])

    def test_switching_it_while_armed_counts_at_once(self):
        """Through the engine's own queue, as the settings window and the API change it."""
        engine = self.engine = engine_with_hosts()

        def change(hold_ctrl):
            cfg = copy.deepcopy(engine.cfg)
            cfg["flow"]["hold_ctrl"] = hold_ctrl
            engine.set_config(cfg)
            while not engine._queue.empty():
                engine._handle(engine._queue.get_nowait())

        self.held = False
        engine._flow_arm((1, "right", 10.0))
        self.push(6, 0.00)
        change(True)
        self.assertIsNotNone(engine._push)  # still armed
        self.push(5, 0.05)
        self.assertEqual((self.sent(), self.asked), ([], 1))
        change(False)
        self.push(11, 0.10)
        self.assertEqual(self.sent(), [1])
        self.assertEqual(self.asked, 1)

    def test_nothing_is_logged_about_ctrl(self):
        with self.assertNoLogs("mxoptions", level="DEBUG"):
            for self.held in (False, None):
                self.engine._flow_arm((1, "right", 10.0))
                self.push(11, 0.00)
                self.engine._flow_arm((1, "right", 0.0))
        self.assertEqual(self.said(), [])
        self.assertEqual(set(self.engine._push), {"channel", "axes", "need", "got", "last", "deadline", "dwell"})
        self.assertNotIn("hold_ctrl", self.engine._push)

    def test_disconnect_drops_the_wait(self):
        self.held = False
        self.engine._flow_arm((1, "right", 10.0))
        self.push(11, 0.00)
        self.engine._reset_input_state()  # the mouse went away
        self.assertIsNone(self.engine._push)
        self.held = True
        self.push(11, 0.05)
        self.assertEqual(self.sent(), [])

    def test_the_action_does_not_need_ctrl(self):
        self.held = False
        self.engine._flow_leave(1, False)  # the action, or the mouse following the keyboard
        self.assertEqual(self.sent(), [1])
        self.engine._device_action({"type": "flow"}, "trigger")
        self.assertEqual(self.said(), [("left", 1), ("asked", None)])
        self.assertEqual(self.asked, 0)

    def test_a_held_button_still_keeps_the_mouse(self):
        self.held = True
        self.engine._hid_buttons = 0x01
        self.engine._flow_arm((1, "right", 10.0))
        self.push(11, 0.00)
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.said(), [("refused", "held")])


class CtrlSource(unittest.TestCase):
    """Where "is Ctrl held" comes from on each desktop; the system calls are made up, nothing real is asked."""

    def x11_lib(self, mask, same=1):
        lib = types.SimpleNamespace(XDefaultRootWindow=lambda display: 7)
        asked = []

        def query(*args):
            asked.append(args[:2])
            args[-1]._obj.value = mask
            return same

        lib.XQueryPointer = query
        return lib, asked

    def test_x11_reads_control_mask_only(self):
        shift, lock, mod1, mod4 = 1 << 0, 1 << 1, 1 << 3, 1 << 6
        for mask, same, held in ((1 << 2, 1, True), (shift | 1 << 2 | 1 << 8, 1, True), (0, 1, False),
                                 (shift | lock | mod1 | mod4 | 1 << 8, 1, False), (0xFFFF & ~(1 << 2), 1, False),
                                 (1 << 2, 0, None)):
            lib, asked = self.x11_lib(mask, same)
            with self.subTest(mask=mask, same=same), mock.patch.object(x11, "_open", lambda: 1234), \
                    mock.patch.object(x11, "_lib", lib), mock.patch.object(x11, "_display", 1234):
                self.assertIs(x11.ctrl_held(), held)
                self.assertEqual(asked, [(1234, 7)])  # the default root window of the open display
                self.assertIs(x11.available(), True)
        with mock.patch.object(x11, "_open", lambda: None):
            self.assertIsNone(x11.ctrl_held())
            self.assertIs(x11.available(), False)

    def test_windows_trusts_only_the_high_bit(self):
        asked = []

        class User32:
            def __init__(self, raw):
                self.raw = raw

                def GetAsyncKeyState(vk):  # noqa: N802 as Windows names it
                    asked.append(vk)
                    return GetAsyncKeyState.restype(self.raw).value  # as ctypes hands back what it returned

                self.GetAsyncKeyState = GetAsyncKeyState

        made = {}

        def win_dll(name):
            self.assertEqual(name, "user32")
            return made["dll"]

        with mock.patch.dict(sys.modules, {"winreg": types.ModuleType("winreg")}), \
                mock.patch.object(ctypes, "WinDLL", win_dll, create=True):
            sys.modules.pop("mxoptions.windows", None)
            try:
                import mxoptions.windows as windows
                for raw, held in ((0x8000, True), (0x8001, True), (0xFFFF, True), (0x0001, False), (0x0000, None)):
                    made["dll"] = User32(raw)
                    with self.subTest(raw=hex(raw)):
                        self.assertIs(windows.ctrl_held(), held)
                        self.assertIs(made["dll"].GetAsyncKeyState.restype, ctypes.c_short)
                        self.assertEqual(made["dll"].GetAsyncKeyState.argtypes, [ctypes.c_int])
                self.assertEqual(set(asked), {0x11})  # VK_CONTROL: either Ctrl key
                with mock.patch.object(ctypes, "WinDLL", mock.Mock(side_effect=OSError("synthetic")), create=True):
                    self.assertIsNone(windows.ctrl_held())
            finally:
                sys.modules.pop("mxoptions.windows", None)
                if hasattr(mxoptions, "windows"):
                    del mxoptions.windows

    def test_each_desktop_asks_its_own_system(self):
        fake_windows = types.SimpleNamespace(ctrl_held=lambda: "from windows")
        with mock.patch.object(x11, "ctrl_held", lambda: "from x11"), mock.patch.object(x11, "available", lambda: "x11 there"), \
                mock.patch.object(mxoptions, "windows", fake_windows, create=True), \
                mock.patch.dict(sys.modules, {"mxoptions.windows": fake_windows}):
            for kind, held, readable in (("x11", "from x11", "x11 there"), ("windows", "from windows", True)):
                with self.subTest(kind=kind), mock.patch.object(environment, "session", lambda kind=kind: kind):
                    self.assertEqual(environment.ctrl_held(), held)
                    self.assertEqual(environment.ctrl_readable(), readable)

    def test_wayland_cannot_tell_even_with_an_x_server(self):
        trap = mock.Mock(side_effect=AssertionError("XWayland asked"))
        with mock.patch.object(environment, "session", lambda: "wayland"), mock.patch.dict(os.environ, {"DISPLAY": ":0"}), \
                mock.patch.object(x11, "ctrl_held", trap), mock.patch.object(x11, "available", trap):
            self.assertIsNone(environment.ctrl_held())
            self.assertIs(environment.ctrl_readable(), False)
        trap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
