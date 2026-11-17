"""One request at a time on a transport, and requests in the background that give way: the real
hidpp.Transport on a FakePort, with synthetic devices that answer at once, late or never."""

import ast
import errno
import io
import logging
import os
import sys
import threading
import time
import types
import unittest
from unittest import mock

from tests.support import Clock, FakeDevice, FakePort, open_fake
from mxoptions import device, hidpp
hid_linux = None if sys.platform == "win32" else __import__("mxoptions.hid_linux", fromlist=["hid_linux"])
from mxoptions.hidpp import HidppCancelled, HidppClosed

MARGIN = 0.15


class Admission(unittest.TestCase):
    def setUp(self):
        self.mouse = FakeDevice("Synthetic mouse", kind=device.KIND_MOUSE)
        self.keys = FakeDevice()
        self.port = FakePort({1: self.keys, 2: self.mouse})
        self.transport = open_fake(self.port)
        self.addCleanup(self.transport.close)

    def background(self, devidx=1, timeout=1.0, deadline=None, cancel=None, results=None):
        """Start a background ping on its own thread; its outcome lands in ``results``."""
        results = results if results is not None else []

        def run():
            try:
                results.append(self.transport.request(devidx, 0, 1, b"\0\0\x01", timeout=timeout, background=True,
                                                      deadline=deadline, cancel=cancel))
            except Exception as error:  # noqa: BLE001 - kept for the test to look at
                results.append(error)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, results

    def test_alone_a_request_works_as_before(self):
        self.assertEqual(self.transport.ping(2), (4, 2))
        self.assertIsNone(self.transport.ping(5))  # empty slot: the receiver says so at once
        self.assertEqual(len(self.port.sent(5)), 1)

    def test_a_silent_keyboard_holds_the_mouse_up_by_one_short_answer_at_most(self):
        """The look for a keyboard that never answers goes on and on; the mouse is served in between."""
        self.keys.silent = True
        cancel = threading.Event()
        view = hidpp.ProbeTransport(self.transport, 0.2, cancel, time.monotonic() + 30)

        def keep_asking():
            while not cancel.is_set():
                try:
                    view.ping(1)
                except (HidppCancelled, HidppClosed):
                    return
        thread = threading.Thread(target=keep_asking, daemon=True)
        thread.start()
        try:
            worst = 0.0
            for _ in range(8):
                time.sleep(0.05)
                start = time.monotonic()
                self.assertEqual(self.transport.ping(2), (4, 2))
                worst = max(worst, time.monotonic() - start)
            self.assertLess(worst, 0.2 + 0.1)
        finally:
            cancel.set()
            thread.join(1.0)
        self.assertFalse(thread.is_alive())

    def test_the_foreground_goes_before_a_waiting_background_request(self):
        self.mouse.delay = 0.3
        first = threading.Thread(target=lambda: self.transport.ping(2), daemon=True)
        first.start()
        time.sleep(0.05)
        thread, results = self.background()
        time.sleep(0.05)
        self.transport.ping(2)  # waits for the first, then goes before the background request
        thread.join(2.0)
        order = [packet[1] for packet in self.port.written]
        self.assertEqual(order, [2, 2, 1])
        self.assertEqual(results[0][:2], bytes([4, 2]))

    def test_many_background_requests_never_crowd_the_foreground(self):
        """At most one background request is ever ahead of the mouse, and no two are on the way at once."""
        self.keys.delay = 0.1
        threads = [self.background()[0] for _ in range(3)]
        time.sleep(0.02)
        start = time.monotonic()
        self.transport.ping(2)
        self.assertLess(time.monotonic() - start, 0.1 + MARGIN)
        for thread in threads:
            thread.join(2.0)
        self.assertEqual(self.port.most_in_flight, 1)
        self.assertEqual(len(self.port.sent(1)), 3)

    def test_a_background_request_gives_up_at_its_deadline_unsent(self):
        self.mouse.delay = 0.6
        holder = threading.Thread(target=lambda: self.transport.ping(2), daemon=True)
        holder.start()
        time.sleep(0.05)
        start = time.monotonic()
        thread, results = self.background(deadline=start + 0.2)
        thread.join(2.0)
        self.assertIsInstance(results[0], HidppCancelled)
        self.assertAlmostEqual(time.monotonic() - start, 0.2, delta=MARGIN)
        self.assertEqual(self.port.sent(1), [])
        holder.join(2.0)

    def test_a_background_request_called_off_while_waiting_is_never_sent(self):
        self.mouse.delay = 0.3
        holder = threading.Thread(target=lambda: self.transport.ping(2), daemon=True)
        holder.start()
        time.sleep(0.05)
        cancel = threading.Event()
        thread, results = self.background(cancel=cancel)
        cancel.set()
        holder.join(2.0)
        thread.join(2.0)
        self.assertIsInstance(results[0], HidppCancelled)
        self.assertEqual(self.port.sent(1), [])

    def test_a_failed_write_frees_the_transport(self):
        for background in (False, True):
            with self.subTest(background=background):
                self.port.fail_write = True
                with self.assertRaises(HidppClosed):
                    self.transport.request(2, 0, 1, b"\0\0\x01", background=background)
                self.assertEqual(self.transport.ping(2), (4, 2))
                thread, results = self.background(devidx=2)
                thread.join(2.0)
                self.assertEqual(results[0][:2], bytes([4, 2]))

    def test_closing_wakes_everyone_waiting(self):
        self.mouse.silent = True
        errors = []

        def foreground():
            try:
                self.transport.request(2, 0, 1, b"\0\0\x01", timeout=5.0)
            except HidppClosed as error:
                errors.append(error)
        holder = threading.Thread(target=foreground, daemon=True)
        holder.start()
        time.sleep(0.05)
        waiter = threading.Thread(target=foreground, daemon=True)
        waiter.start()
        thread, results = self.background(deadline=time.monotonic() + 10)
        time.sleep(0.05)
        start = time.monotonic()
        self.transport.close()
        for each in (holder, waiter, thread):
            each.join(2.0)
        self.assertLess(time.monotonic() - start, 1.5)
        self.assertEqual(len(errors), 2)
        self.assertIsInstance(results[0], HidppClosed)

    def test_a_background_answer_is_not_waited_for_past_the_deadline(self):
        self.keys.silent = True
        start = time.monotonic()
        thread, results = self.background(timeout=5.0, deadline=start + 0.2)
        thread.join(2.0)
        self.assertIsInstance(results[0], HidppCancelled)
        self.assertAlmostEqual(time.monotonic() - start, 0.2, delta=MARGIN)
        self.assertEqual(len(self.port.sent(1)), 1)


class Reports(unittest.TestCase):
    def test_what_a_keyboard_types_is_neither_passed_on_nor_logged(self):
        """A Bluetooth keyboard sends its keystrokes on the node its HID++ traffic uses."""
        port = FakePort({0xFF: FakeDevice()}, receiver=False)
        heard = []
        transport = open_fake(port, receiver=False, on_notification=lambda *args: heard.append(args))
        self.addCleanup(transport.close)
        with self.assertNoLogs(level="DEBUG"):
            port.notify(bytes([0x01, 0x00, 0x00, 0x0B, 0x0C, 0x0D, 0x00, 0x00, 0x00]))  # synthetic keystrokes
            self.assertEqual(transport.ping(0xFF), (4, 2))
        self.assertEqual(heard, [])
        self.assertIsNone(transport.on_report)


class ProbeSlot(unittest.TestCase):
    def test_what_each_slot_holds(self):
        """Only a keyboard (kind 0) is taken for one; numpads, presenters and mice are something else."""
        devices = {1: FakeDevice(), 2: FakeDevice("Synthetic mouse", kind=device.KIND_MOUSE),
                   3: FakeDevice("Synthetic numpad", kind=2), 4: FakeDevice("Synthetic presenter", kind=6),
                   5: FakeDevice("Synthetic trackball", kind=device.KIND_TRACKBALL)}
        asleep = devices[4]
        port = FakePort(devices)
        transport = open_fake(port)
        self.addCleanup(transport.close)
        found = {devidx: device.probe_slot(transport, devidx)[0] for devidx in transport.slots()}
        self.assertEqual(found, {1: "keyboard", 2: "mouse", 3: "other", 4: "other", 5: "mouse", 6: "empty"})
        asleep.silent = True
        self.assertEqual(device.probe_slot(transport, 4), ("asleep", None))
        old = FakeDevice()
        old.answer = lambda feat_idx, function, params: bytes([1, 0, 0])  # a HID++ 1.0 era protocol version
        port.devices[3] = old
        self.assertEqual(device.probe_slot(transport, 3), ("old", None))

    def test_the_mouse_is_looked_for_exactly_as_before(self):
        """The new arguments of find_mouse change nothing in what is sent; keyboards come out of ``others``."""
        sequences = []
        for kwargs in ({}, {"others": [], "skip": ()}):
            port = FakePort({1: FakeDevice(), 2: FakeDevice("Synthetic mouse", kind=device.KIND_MOUSE)})
            transport = open_fake(port)
            try:
                found = device.find_mouse(transport, **kwargs)
            finally:
                transport.close()
            self.assertEqual((found.devidx, found.name), (2, "Synthetic mouse"))
            # the ping byte is the time of day: everything else is compared
            sequences.append([p[:6] + p[7:] if p[2] == 0 and p[3] >> 4 == 1 else p for p in port.written])
            if kwargs:
                self.assertEqual([(dev.devidx, dev.kind) for dev in kwargs["others"]], [(1, device.KIND_KEYBOARD)])
        self.assertEqual(sequences[0], sequences[1])

    def test_skipped_slots_are_not_asked(self):
        port = FakePort({1: FakeDevice()})
        transport = open_fake(port)
        self.addCleanup(transport.close)
        self.assertIsNone(device.find_mouse(transport, skip=(1,)))
        self.assertEqual(port.sent(1), [])


# -- which keys of a Bluetooth keyboard are down (hid_linux.key_reports, Port.keys_held) ----------------------------
# Descriptors made up item by item for the tests: they follow the HID 1.11 item format, and say nothing of any real
# keyboard. Every report a node answers comes from the stand-in for the ioctl below; no node is opened.

def _short(prefix, value, length=None):
    """One short item: ``prefix`` is its tag and type (the size bits 0), ``value`` its data."""
    if length is None:
        length = 0 if value is None else 1 if -128 <= value <= 255 else 2 if -32768 <= value <= 65535 else 4
    code = {0: 0, 1: 1, 2: 2, 4: 3}[length]
    data = b"" if length == 0 else (value % (1 << (8 * length))).to_bytes(length, "little")
    return bytes([prefix | code]) + data


def page(p):
    return _short(0x04, p, 2 if p > 0xFF else 1)


def usage(u):
    return _short(0x08, u, 2 if u > 0xFF else 1)


def umin(u):
    return _short(0x18, u, 2 if u > 0xFF else 1)


def umax(u):
    return _short(0x28, u, 2 if u > 0xFF else 1)


def lmin(v):
    return _short(0x14, v, 1 if -128 <= v <= 127 else 2)


def lmax(v):
    return _short(0x24, v, 1 if 0 <= v <= 127 else 2)


def size(n):
    return _short(0x74, n)


def count(n):
    return _short(0x94, n)


def rid(n):
    return _short(0x84, n)


def inp(flags):
    return _short(0x80, flags)


def outp(flags):
    return _short(0x90, flags)


def feat(flags):
    return _short(0xB0, flags)


def coll(kind=1):
    return _short(0xA0, kind)


END = b"\xc0"
DATA_ARRAY, DATA_VAR, CONST = 0x00, 0x02, 0x01
MODS = page(0x07) + umin(0xE0) + umax(0xE7) + lmin(0) + lmax(1) + size(1) + count(8) + inp(DATA_VAR)
PAD = size(8) + count(1) + inp(CONST)
KEYS = page(0x07) + umin(0) + umax(0x65) + lmin(0) + lmax(0x65) + size(8) + count(6) + inp(DATA_ARRAY)
LEDS = page(0x08) + umin(1) + umax(5) + lmax(1) + size(1) + count(5) + outp(DATA_VAR) + size(3) + count(1) + outp(CONST)
CONSUMER = page(0x0C) + umin(1) + umax(0x29C) + lmin(1) + lmax(0x29C) + size(16) + count(2) + inp(DATA_ARRAY)
SYSTEM = page(0x01) + umin(0x81) + umax(0x83) + lmin(1) + lmax(3) + size(8) + count(1) + inp(DATA_ARRAY)


def report(number, *items, application=(0x01, 0x06)):
    """An application collection (Generic Desktop keyboard unless said) holding one numbered report."""
    return page(application[0]) + usage(application[1]) + coll(1) + (rid(number) if number else b"") + b"".join(items) + END


def hidpp_report(number, length, vendor=0xFF43):
    """A HID++ report as Logitech nodes describe one: ``length`` bytes with the id, in and out."""
    data = size(8) + count(length - 1) + usage(1)
    return page(vendor) + usage(0x0202) + coll(1) + rid(number) + data + inp(DATA_ARRAY) + usage(1) + data + outp(DATA_ARRAY) + END


BOOT = page(0x01) + usage(0x06) + coll(1) + MODS + PAD + KEYS + LEDS + END
KB_MASK = 0xFF | ((1 << 48) - 1) << 16  # modifiers, the padding byte left out, six keys
# A Bluetooth keyboard as these tests have it: keyboard 1, HID++ 0x11, consumer keys 3, system keys 4.
AGGREGATE = (report(1, MODS, PAD, KEYS, LEDS) + hidpp_report(0x11, 20) + report(3, CONSUMER, application=(0x0C, 0x01))
             + report(4, SYSTEM, application=(0x01, 0x80)))
AGGREGATE_LAYOUT = ((1, 9, KB_MASK), (3, 5, 0xFFFFFFFF), (4, 2, 0xFF))
FAKE_FD = 9999  # never a real descriptor: the ioctl stand-in checks it is the one asked


class Ioctls:
    """Stands in for fcntl.ioctl as hid_linux uses it, for HIDIOCGINPUT: ``answers`` maps a report id to the bytes the
    keyboard answers with (its id first when numbered), an OSError to raise, or a function of the buffer that returns
    either. As the kernel does, the answer is cut to the buffer. Each call is kept, and the buffers, to be looked at."""

    def __init__(self, answers, wait=None):
        self.answers = answers
        self.wait = wait            # a function called before each answer (time passing, a test holding it up)
        self.calls = []             # (fd, request, buffer length, byte 0, thread name)
        self.buffers = []

    def ioctl(self, fd, request, buf, mutate=False):
        assert fd == FAKE_FD and mutate is True and isinstance(buf, bytearray)
        self.calls.append((fd, request, len(buf), buf[0], threading.current_thread().name))
        self.buffers.append(buf)
        if self.wait:
            self.wait(buf[0])
        answer = self.answers[buf[0]]
        if callable(answer):
            answer = answer(buf)
        if isinstance(answer, BaseException):
            raise answer
        got = min(len(buf), len(answer))
        buf[:got] = answer[:got]
        return got

    def ids(self):
        return [call[3] for call in self.calls]


NODES = {}  # path -> the KeyNode made last for it


class KeyNode(hid_linux.Port if hid_linux else object):
    """hid_linux.Port as it is, on no device: its HID++ traffic goes to ``fake`` (a FakePort) and its descriptor is
    ``desc``; the ioctls go to whatever Ioctls the test put in hid_linux's place of fcntl (see bluetooth)."""

    def __init__(self, fake=None, path=None, desc=AGGREGATE):
        path = path or f"/dev/synthetic-kb{len(NODES)}"
        with mock.patch.object(hid_linux.os, "open", lambda *_args: FAKE_FD), \
                mock.patch.object(hid_linux.os, "pipe", lambda: (-1, -1)):
            super().__init__(path)
        self.fake = fake or FakePort(receiver=False)
        self.desc = desc
        NODES[path] = self

    def read(self):
        return self.fake.read()

    def write(self, packet):
        self.fake.write(packet)

    def wake(self):
        self._closed = True
        self.fake.wake()

    def close(self):
        self._closed = True


def bluetooth(test, answers, wait=None):
    """Have hid_linux ask ``answers`` (see Ioctls), and take every node for a Bluetooth one that BlueZ runs, its
    descriptor that of the KeyNode asked. Returns the Ioctls."""
    ioctls = Ioctls(answers, wait)
    for patcher in (mock.patch.object(hid_linux, "fcntl", types.SimpleNamespace(ioctl=ioctls.ioctl)),
                    mock.patch.object(hid_linux, "_keys_layout", lambda path: hid_linux.key_reports(NODES[path].desc))):
        patcher.start()
        test.addCleanup(patcher.stop)
    return ioctls


ZERO = {1: bytes([1]) + bytes(8), 3: bytes([3]) + bytes(4), 4: bytes([4, 0])}


def never():
    return False


@unittest.skipIf(sys.platform == "win32", "hidraw is Linux's")
class KeyDescriptors(unittest.TestCase):
    """key_reports: which input reports of a descriptor carry keys, and every descriptor it refuses."""

    def assertNone(self, desc):
        self.assertIsNone(hid_linux.key_reports(desc))

    def test_layouts_it_takes(self):
        consumer_in_keyboard = report(1, MODS, PAD, KEYS, page(0x0C) + umin(0) + umax(0x29C) + lmin(0) + lmax(0x29C)
                                      + size(16) + count(1) + inp(DATA_ARRAY))
        nkro = report(1, MODS, page(0x07) + umin(0) + umax(119) + lmin(0) + lmax(1) + size(1) + count(120) + inp(DATA_VAR))
        keys_from_four = report(1, MODS, PAD, page(0x07) + umin(4) + umax(0x65) + lmin(1) + lmax(0x62) + size(8)
                                + count(6) + inp(DATA_ARRAY))
        call_mute = page(0x01) + usage(0xE1) + lmin(0) + lmax(1) + size(1) + count(1) + inp(0x06) + size(15) + inp(CONST)
        consumer_from_zero = page(0x0C) + umin(0) + umax(0x29C) + lmin(0) + lmax(0x29C) + size(16) + count(1) + inp(DATA_ARRAY)
        cases = {
            "boot keyboard, no report ids": (BOOT, ((0, 8, KB_MASK),)),
            "keyboard, HID++ and consumer": (report(1, MODS, PAD, KEYS) + hidpp_report(0x11, 20) + report(3, CONSUMER),
                                             ((1, 9, KB_MASK), (3, 5, 0xFFFFFFFF))),
            "modifiers and keys apart": (report(1, MODS) + report(2, KEYS), ((1, 2, 0xFF), (2, 7, (1 << 48) - 1))),
            "a bit for every key": (nkro, ((1, 17, (1 << 128) - 1),)),
            "list from 1: 0 is none": (keys_from_four, ((1, 9, KB_MASK),)),
            "consumer key in the keyboard report": (consumer_in_keyboard, ((1, 11, KB_MASK | 0xFFFF << 64),)),
            "modifiers and keys, two items": (report(1, MODS + KEYS), ((1, 8, (1 << 56) - 1),)),
            "consumer report, 0 is none": (report(1, MODS) + report(3, consumer_from_zero), ((1, 2, 0xFF), (3, 3, 0xFFFF))),
            "system keys": (report(1, MODS) + report(4, SYSTEM), ((1, 2, 0xFF), (4, 2, 0xFF))),
            "call mute key, as on the MX Mechanical": (report(1, MODS) + report(0x0B, call_mute, application=(0x01, 0x13)),
                                                       ((1, 2, 0xFF), (0x0B, 3, 0x0001))),
            "every kind, HID++ left out": (AGGREGATE, AGGREGATE_LAYOUT),
            "both HID++ reports left out": (report(1, MODS) + hidpp_report(0x10, 7, 0xFF00) + hidpp_report(0x11, 20, 0xFF00), ((1, 2, 0xFF),)),
            "a report of padding only": (report(1, MODS) + report(5, PAD), ((1, 2, 0xFF),)),
            "output and feature of any page": (report(1, MODS, page(0x09) + usage(1) + size(8) + count(2) + outp(DATA_VAR)
                                                         + page(0xFF01) + usage(1) + feat(DATA_VAR)), ((1, 2, 0xFF),)),
        }
        for name, (desc, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(hid_linux.key_reports(desc), expected)

    def test_values_where_0_could_be_a_key_down_are_refused(self):
        for name, field in {
                "keyboard list from 0, usages from 4": page(0x07) + umin(4) + umax(0x65) + lmin(0) + lmax(0x61) + size(8) + count(6) + inp(0),
                "keyboard key of 8 bits": page(0x07) + umin(0xE0) + umax(0xE7) + lmin(0) + lmax(1) + size(8) + count(1) + inp(DATA_VAR),
                "consumer list from 0, usages from 1": page(0x0C) + umin(1) + umax(0x29C) + lmin(0) + lmax(0x29B) + size(16) + count(1) + inp(0),
                "consumer list below 0": page(0x0C) + umin(1) + umax(0x29C) + lmin(-1) + lmax(0x29C) + size(16) + count(1) + inp(0),
                "consumer level of 2 bits": page(0x0C) + usage(0xE0) + lmin(0) + lmax(3) + size(2) + count(1) + inp(DATA_VAR) + size(6) + inp(CONST),
                "keyboard variable 0 to 2": page(0x07) + umin(0xE0) + umax(0xE7) + lmin(0) + lmax(2) + size(1) + count(8) + inp(DATA_VAR),
                "system list from 0": page(0x01) + umin(0x81) + umax(0x83) + lmin(0) + lmax(2) + size(8) + count(1) + inp(0),
                "list of 17 bits": page(0x07) + umin(1) + umax(0x65) + lmin(1) + lmax(0x65) + size(17) + count(1) + inp(0) + size(7) + inp(CONST),
                "list from 0 with a usage before the range": page(0x07) + usage(4) + umin(0) + umax(0x65) + lmin(0) + lmax(0x65) + size(8) + count(1) + inp(0)}.items():
            with self.subTest(name):
                self.assertNone(report(1, MODS) + report(2, field))

    def test_items_it_does_not_follow_are_refused(self):
        for name, desc in {
                "push": report(1, b"\xa4", MODS), "pop": report(1, MODS, b"\xb4"),
                "long item": report(1, MODS) + b"\xfe\x01\x00\x00",
                "cut short": report(1, MODS) + b"\x26\x01",
                "end without collection": report(1, MODS) + END,
                "collection left open": report(1, MODS)[:-1],
                "17 collections deep": page(1) + usage(6) + coll(1) * 17 + rid(1) + MODS + END * 17,
                "input before the first report id": page(1) + usage(6) + coll(1) + MODS + rid(1) + KEYS + END,
                "report id 0": report(0, MODS)[:4] + rid(0) + MODS + END,
                "usage with its page in it": report(1, page(0x07) + _short(0x08, 0x000700E0, 4) + lmin(0) + lmax(1) + size(1) + count(8) + inp(DATA_VAR)),
                "delimiter": report(1, b"\xa9\x01" + MODS),
                "reserved item type": report(1, MODS) + b"\x0d\x00",
                "unknown main item": report(1, MODS) + b"\xd1\x00",
                "unknown local item": report(1, b"\x68\x00" + MODS),
                "usage maximum alone": report(1, page(0x07) + umax(0xE7) + lmin(0) + lmax(1) + size(1) + count(8) + inp(DATA_VAR)),
                "usage minimum alone": report(1, page(0x07) + umin(0xE0) + lmin(0) + lmax(1) + size(1) + count(8) + inp(DATA_VAR)),
                "report size 0": report(1, page(0x07) + umin(0xE0) + umax(0xE7) + lmin(0) + lmax(1) + size(0) + count(8) + inp(DATA_VAR)),
                "report size 33": report(1, MODS, page(0x07) + lmin(1) + lmax(5) + umin(1) + umax(5) + size(33) + count(1) + inp(0) + size(7) + inp(CONST)),
                "report count 0": report(1, page(0x07) + umin(0xE0) + umax(0xE7) + lmin(0) + lmax(1) + size(1) + count(0) + inp(DATA_VAR)),
                "bits not whole bytes": report(1, page(0x07) + umin(0xE0) + umax(0xE6) + lmin(0) + lmax(1) + size(1) + count(7) + inp(DATA_VAR)),
                "more than 64 bytes": page(1) + usage(6) + coll(1) + MODS + page(0x07) + umin(0) + umax(0x65) + lmin(0) + lmax(0x65) + size(8) + count(70) + inp(0) + END,
                "empty": b""}.items():
            with self.subTest(name):
                self.assertNone(desc)

    def test_a_keyboard_with_input_of_any_other_kind_is_not_asked(self):
        pointer = page(0x01) + usage(0x30) + usage(0x31) + lmin(-127) + lmax(127) + size(8) + count(2) + inp(0x06)
        for name, desc in {
                "pointer axes": report(1, MODS) + report(2, pointer, application=(0x01, 0x02)),
                "buttons": report(1, MODS) + report(2, page(0x09) + umin(1) + umax(8) + lmin(0) + lmax(1) + size(1) + count(8) + inp(DATA_VAR)),
                "system key without a usage": report(1, MODS) + report(4, page(0x01) + lmin(0) + lmax(1) + size(1) + count(8) + inp(DATA_VAR)),
                "Generic Desktop beside call mute": report(1, MODS) + report(4, page(0x01) + usage(0xE0) + lmin(0) + lmax(1) + size(1) + count(8) + inp(DATA_VAR)),
                "Generic Desktop past the system keys": report(1, MODS) + report(4, page(0x01) + umin(0xB0) + umax(0xB8) + lmin(1) + lmax(9) + size(8) + count(1) + inp(0)),
                "HID++ id 0x12": report(1, MODS) + hidpp_report(0x12, 20),
                "HID++ long report of 19 bytes": report(1, MODS) + hidpp_report(0x11, 19),
                "HID++ long report of 21 bytes": report(1, MODS) + hidpp_report(0x11, 21),
                "HID++ short report of 20 bytes": report(1, MODS) + hidpp_report(0x10, 20, 0xFF00),
                "keys and HID++ in one report": report(1, MODS, page(0xFF43) + usage(1) + size(8) + count(19) + inp(0)),
                "keys in a report of HID++'s id and length": report(1, MODS) + report(0x11, MODS, page(0xFF43) + usage(1)
                                                                                    + size(8) + count(18) + inp(0)),
                "another vendor's report": report(1, MODS) + report(5, page(0xFF01) + usage(1) + size(8) + count(4) + inp(0)),
                "a battery": report(1, MODS) + report(6, page(0x06) + usage(0x20) + lmin(0) + lmax(100) + size(8) + count(1) + inp(DATA_VAR)),
                "five key reports": report(1, MODS) + b"".join(report(n, CONSUMER) for n in (2, 3, 5, 6)),
                "no keyboard page at all": report(3, CONSUMER) + report(4, SYSTEM),
                "a mouse": page(0x01) + usage(0x02) + coll(1) + rid(2) + page(0x09) + umin(1) + umax(16) + lmin(0) + lmax(1) + size(1) + count(16) + inp(DATA_VAR) + pointer + END,
                "a receiver's HID++ node": hidpp_report(0x10, 7, 0xFF00) + hidpp_report(0x11, 20, 0xFF00),
                "unnumbered HID++": page(0xFF00) + usage(1) + coll(1) + size(8) + count(19) + inp(0) + END}.items():
            with self.subTest(name):
                self.assertNone(desc)

    def test_four_key_reports_are_the_most(self):
        four = report(1, MODS) + b"".join(report(n, CONSUMER) for n in (2, 3, 5))
        self.assertEqual(len(hid_linux.key_reports(four)), hid_linux.KEY_REPORTS_MAX)

    def test_the_request_is_the_one_linux_defines(self):
        self.assertEqual(hid_linux.HIDIOCGINPUT(64), 0xC040480A)  # _IOC(_IOC_WRITE|_IOC_READ, 'H', 0x0A, 64)
        self.assertEqual(hid_linux.HIDIOCGINPUT(10), 0xC00A480A)


@unittest.skipIf(sys.platform == "win32", "hidraw is Linux's")
class KeysHeld(unittest.TestCase):
    """Port.keys_held on a node that is never opened: each answer of the keyboard as the kernel would hand it on."""

    def node(self, desc=AGGREGATE, answers=None, wait=None):
        self.ioctls = bluetooth(self, dict(ZERO) if answers is None else answers, wait)
        return KeyNode(desc=desc)

    def single(self, answer, numbered=True):
        """keys_held of a keyboard with one report (id 1, or none): ``answer`` as the keyboard gives it."""
        desc = report(1, MODS, PAD, KEYS) if numbered else BOOT
        return self.node(desc, {1 if numbered else 0: answer}).keys_held(never)

    def test_one_report_each_answer(self):
        key = bytes([1, 0, 0, 0x0D, 0, 0, 0, 0, 0])  # "j" down
        cases = {
            "all up": (bytes([1]) + bytes(8), True, False),
            "a key down": (key, True, True),
            "another report's id": (bytes([2]) + bytes(8), True, None),
            "short: the zeros of its start only": (bytes([1, 0, 0]), True, None),
            "one byte too many": (bytes([1]) + bytes(9), True, None),
            "padding set, no key": (bytes([1, 0, 0xFF, 0, 0, 0, 0, 0, 0]), True, False),
            "ErrorRollOver": (bytes([1, 0, 0, 1, 1, 1, 1, 1, 1]), True, True),
            "no ids, all up": (bytes(8), False, False),
            "no ids, Left Shift": (bytes([0x02]) + bytes(7), False, True),
            "no ids, one byte short": (bytes(7), False, None),
            "no ids, one byte too many": (bytes(9), False, None),
        }
        for name, (answer, numbered, expected) in cases.items():
            with self.subTest(name):
                self.assertIs(self.single(answer, numbered), expected)
        for error in (errno.EIO, errno.ENODEV, errno.ENOTTY, errno.EINVAL, errno.EINTR, 512):  # 512: ERESTARTSYS
            with self.subTest(error=error):
                self.assertIsNone(self.single(OSError(error, "synthetic")))

    def test_the_request_asks_one_byte_more_than_the_report(self):
        port = self.node()
        port.keys_held(never)
        self.assertEqual([call[1:4] for call in self.ioctls.calls],
                         [(hid_linux.HIDIOCGINPUT(10), 10, 1), (hid_linux.HIDIOCGINPUT(6), 6, 3), (hid_linux.HIDIOCGINPUT(3), 3, 4)])
        self.single(bytes(8), numbered=False)
        self.assertEqual(self.ioctls.calls[-1][1:4], (hid_linux.HIDIOCGINPUT(9), 9, 0))

    def test_every_report_of_the_keyboard(self):
        volume_down = bytes([3, 0xEA, 0x00, 0, 0])
        sleep = bytes([4, 2])
        cases = {
            "all up": ({}, False, [1, 3, 4]),
            "volume down held": ({3: volume_down}, True, [1, 3]),
            "sleep held": ({4: sleep}, True, [1, 3, 4]),
            "shift held: no more is asked": ({1: bytes([1, 0x02]) + bytes(7)}, True, [1]),
            "consumer report short": ({3: bytes([3, 0, 0, 0])}, None, [1, 3]),
            "system report with the consumer id": ({4: bytes([3, 0])}, None, [1, 3, 4]),
            "consumer report fails": ({3: OSError(errno.EIO, "synthetic")}, None, [1, 3]),
            "consumer report too long": ({3: bytes([3]) + bytes(5)}, None, [1, 3]),
        }
        for name, (changes, expected, asked) in cases.items():
            with self.subTest(name):
                port = self.node(answers={**ZERO, **changes})
                self.assertIs(port.keys_held(never), expected)
                self.assertEqual(self.ioctls.ids(), asked)
        self.assertNotIn(0x11, self.ioctls.ids())  # the HID++ report is never asked

    def test_a_consumer_list_where_0_is_none(self):
        port = self.node(answers=dict(ZERO))
        self.assertIs(port.keys_held(never), False)
        port = self.node(answers={**ZERO, 3: bytes([3, 0xE9, 0x00, 0, 0])})  # volume up
        self.assertIs(port.keys_held(never), True)

    def test_stopped_before_a_report_is_none_and_that_report_is_not_asked(self):
        for name, after in (("before the second", 1), ("before the third", 2)):
            with self.subTest(name):
                port, asked = self.node(), []
                self.assertIsNone(port.keys_held(lambda: len(asked.append(0) or asked) > after))
                self.assertEqual(self.ioctls.ids(), [1, 3][:after])

    def test_no_answer_left_out_is_ever_taken_for_all_up(self):
        """Whatever report is not read in full, or not read at all, the answer is never False."""
        bad = {"short": lambda rid_, n: bytes([rid_]) + bytes(n - 2), "long": lambda rid_, n: bytes([rid_]) + bytes(n),
               "other id": lambda rid_, n: bytes([(rid_ % 4) + 1]) + bytes(n - 1), "error": lambda rid_, n: OSError(errno.EIO, "x")}
        for number, total, _mask in AGGREGATE_LAYOUT:
            for kind, make in bad.items():
                with self.subTest(report=number, kind=kind):
                    port = self.node(answers={**ZERO, number: make(number, total)})
                    self.assertIsNone(port.keys_held(never))
            for stop_at in range(len(AGGREGATE_LAYOUT)):
                with self.subTest(report=number, stopped_before=stop_at):
                    port, asked = self.node(), []
                    self.assertIsNone(port.keys_held(lambda: len(asked.append(0) or asked) > stop_at))

    def test_ways_a_key_could_be_missed_find_it(self):
        """Keys held where reading less than every key report, or trusting a short answer, would say none is."""
        split = report(1, MODS) + report(2, KEYS)
        from_four = report(1, MODS, PAD, page(0x07) + umin(4) + umax(0x65) + lmin(0) + lmax(0x61) + size(8) + count(6) + inp(0))
        nkro_far = report(1, MODS, page(0x07) + umin(0) + umax(0x1F7) + lmin(0) + lmax(1) + size(1) + count(504) + inp(DATA_VAR))
        cases = {
            "key in the second report": (split, {1: bytes([1, 0]), 2: bytes([2, 0x0D, 0, 0, 0, 0, 0])}, True),
            "zero start only": (report(1, MODS, PAD, KEYS), {1: bytes([1, 0, 0])}, None),
            "a bit past 63 bytes": (nkro_far, {}, None),
            "the id of another report": (report(1, MODS, PAD, KEYS), {1: bytes([2, 0x02]) + bytes(7)}, None),
            "no ids, Left Shift in byte 0": (BOOT, {0: bytes([0x02]) + bytes(7)}, True),
            "a list from 0 whose usages start at 4": (from_four, {1: bytes(9)}, None),
            "a key past a short answer": (report(1, MODS, PAD, KEYS), {1: bytes([1, 0, 0, 0, 0, 0, 0, 0])}, None),
            "keyboard clear, volume down held": (AGGREGATE, {**ZERO, 3: bytes([3, 0xEA, 0, 0, 0])}, True),
            "keyboard clear, sleep held": (AGGREGATE, {**ZERO, 4: bytes([4, 2])}, True),
        }
        for name, (desc, answers, expected) in cases.items():
            with self.subTest(name):
                port = self.node(desc, answers)
                result = port.keys_held(never)
                self.assertIsNot(result, False)
                self.assertIs(result, expected)

    def test_no_more_than_32_asks_on_one_open_node(self):
        port = self.node()
        for _ in range(10):
            self.assertIs(port.keys_held(never), False)
        with self.assertLogs("mxoptions.hid_linux", "INFO") as logs:
            self.assertIsNone(port.keys_held(never))  # three more would be 33: none is asked
            self.assertIsNone(port.keys_held(never))
        self.assertEqual(len(self.ioctls.calls), 30)
        self.assertEqual(len(logs.output), 1)
        single = self.node(report(1, MODS, PAD, KEYS), {1: OSError(errno.EIO, "synthetic")})
        for _ in range(hid_linux.IOCTLS_PER_PORT):
            self.assertIsNone(single.keys_held(never))  # failed: counted all the same
        self.assertIsNone(single.keys_held(never))
        self.assertEqual(len(self.ioctls.calls), hid_linux.IOCTLS_PER_PORT)
        again = KeyNode(desc=report(1, MODS, PAD, KEYS))  # opened anew: asked again
        self.ioctls.answers[1] = bytes([1]) + bytes(8)
        self.assertIs(again.keys_held(never), False)

    def test_a_closed_node_is_not_asked(self):
        port = self.node()
        port.wake()
        self.assertIsNone(port.keys_held(never))
        self.assertEqual(self.ioctls.calls, [])

    def test_what_is_read_is_wiped_and_never_logged(self):
        marked = {1: bytes([1, 0x5A, 0, 0x5B, 0x5C, 0, 0, 0, 0]), 3: bytes([3, 0x5D, 0x5E, 0, 0]), 4: bytes([4, 0x5F])}
        for answers in (marked, dict(ZERO), {**ZERO, 4: bytes([4, 0x5F, 0x60])}):
            port = self.node(answers=answers)
            with self.assertLogs(level="DEBUG") as logs:
                logging.getLogger("mxoptions").debug("start")
                port.keys_held(never)
            for buf in self.ioctls.buffers:
                self.assertEqual(bytes(buf), bytes(len(buf)))
            text = "\n".join(logs.output)
            for byte in (0x5A, 0x5B, 0x5D, 0x5F):
                self.assertNotIn(f"{byte:02x}", text.lower())

    def test_no_node_is_opened_and_nothing_else_is_read(self):
        port = self.node()
        with mock.patch.object(hid_linux.os, "open", side_effect=AssertionError("opened")), \
                mock.patch.object(hid_linux.os, "read", side_effect=AssertionError("read")), \
                mock.patch.object(hid_linux.select, "select", side_effect=AssertionError("waited")):
            self.assertIs(port.keys_held(never), False)


@unittest.skipIf(sys.platform == "win32", "hidraw is Linux's")
class KeysWhere(unittest.TestCase):
    """Which nodes are asked at all: a keyboard on Bluetooth that BlueZ runs, and nothing else."""

    def sysfs(self, uevent, real, desc=AGGREGATE):
        files = {"/sys/class/hidraw/hidraw7/device/uevent": uevent,
                 "/sys/class/hidraw/hidraw7/device/report_descriptor": desc}

        def fake_open(path, mode="r", *_args, **_kwargs):
            if path not in files:
                raise FileNotFoundError(path)
            data = files[path]
            return io.BytesIO(data) if "b" in mode else io.StringIO(data)
        patches = (mock.patch.object(hid_linux, "open", fake_open, create=True),
                   mock.patch.object(hid_linux.os.path, "realpath", lambda path: real))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return hid_linux._keys_layout("/dev/hidraw7")

    def test_only_a_bluetooth_node_that_bluez_runs(self):
        uhid = "/sys/devices/virtual/misc/uhid/0005:046D:B35F.0007"
        self.assertEqual(self.sysfs("HID_ID=0005:0000046D:0000B35F\n", uhid), AGGREGATE_LAYOUT)
        self.assertIsNone(self.sysfs("HID_ID=0003:0000046D:0000C548\n", uhid))  # USB
        self.assertIsNone(self.sysfs("HID_ID=0005:0000046D:0000B35F\n",
                                     "/sys/devices/pci0000:00/0000:00:14.0/usb1/1-6/1-6:1.0/bluetooth/hci0/hci0:3/0005:046D:B35F.0007"))
        self.assertIsNone(self.sysfs("HID_NAME=no id\n", uhid))
        self.assertIsNone(self.sysfs("HID_ID=0005:0000046D:0000B35F\n", uhid, report(1, MODS) + report(2, page(0x09) + umin(1)
                                     + umax(8) + lmax(1) + size(1) + count(8) + inp(DATA_VAR))))

    def test_a_receiver_and_a_closed_transport_are_never_asked(self):
        ioctls = bluetooth(self, dict(ZERO))
        for receiver in (True, False):
            with self.subTest(receiver=receiver):
                node = KeyNode(FakePort({0xFF: FakeDevice()}, receiver=receiver))
                transport = open_fake(node, receiver=receiver)
                self.assertIs(transport.can_tell_keys(), not receiver)
                self.assertIs(transport.keys_held(never), None if receiver else False)
                transport.close()
                self.assertFalse(transport.can_tell_keys())
                self.assertIsNone(transport.keys_held(never))
        self.assertEqual(ioctls.ids(), [1, 3, 4])

    def test_a_probe_view_stops_when_called_off_or_out_of_time(self):
        ioctls = bluetooth(self, dict(ZERO))
        transport = open_fake(KeyNode(), receiver=False)
        self.addCleanup(transport.close)
        cancel = threading.Event()
        view = hidpp.ProbeTransport(transport, 1.0, cancel, time.monotonic() + 10)
        self.assertTrue(view.can_tell_keys())
        self.assertIs(view.keys_held(time.monotonic() + 10), False)
        self.assertIsNone(view.keys_held(time.monotonic() - 0.01))  # its time is up already
        self.assertIsNone(hidpp.ProbeTransport(transport, 1.0, cancel, time.monotonic() - 0.01).keys_held(time.monotonic() + 10))
        cancel.set()
        self.assertIsNone(view.keys_held(time.monotonic() + 10))
        self.assertEqual(len(ioctls.calls), 3)

    def test_the_view_s_time_counts_before_every_report(self):
        ioctls = bluetooth(self, dict(ZERO))
        transport = open_fake(KeyNode(), receiver=False)
        self.addCleanup(transport.close)
        with Clock(100.0) as fake:
            ioctls.wait = lambda _rid: setattr(fake, "now", fake.now + 0.16)
            view = hidpp.ProbeTransport(transport, 1.0, threading.Event(), fake.now + 10)
            self.assertIsNone(view.keys_held(fake.now + 0.3))  # 0.32 s gone after two: the third is not asked
        self.assertEqual(ioctls.ids(), [1, 3])

    def test_windows_never_asks(self):
        """The Windows node says no layout, and binds nothing of Windows to find out which keys are down."""
        with open(os.path.join(os.path.dirname(hidpp.__file__), "hid_windows.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "keys_layout")
        self.assertEqual([type(statement) for statement in method.body], [ast.Expr, ast.Return])
        self.assertIsNone(method.body[1].value.value)
        used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        used |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        used |= {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
                 and len(node.value) < 40}  # names given as text; docstrings are longer
        for name in ("GetAsyncKeyState", "GetKeyState", "GetKeyboardState", "OpenInputDesktop", "user32", "advapi32",
                     "SetWindowsHookExW", "RegisterRawInputDevices"):
            self.assertNotIn(name, used)


if __name__ == "__main__":
    unittest.main()
