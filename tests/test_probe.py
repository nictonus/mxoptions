"""tools/hidpp_probe.py against made-up devices: it only ever reads, stops in time and shows nothing private."""

import contextlib
import copy
import io
import json
import os
import struct
import sys
import time
import unittest
from unittest.mock import patch

import tests.support  # noqa: F401
from mxoptions import hidpp

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import hidpp_probe  # noqa: E402

# Functions that only read: (feature ID, function), as Logitech's HID++ 2.0 documents number them, and for
# the keyboard settings as Solaar and OpenLogi read them. Kept apart from the tool's own list so that a change to
# that list has to be made here as well.
DOCUMENTED_READS = {(0x0000, 0), (0x0000, 1), (0x0001, 0), (0x0001, 1), (0x0005, 0), (0x0005, 1), (0x0005, 2),
                    (0x1B04, 0), (0x1B04, 1), (0x1B04, 2), (0x0003, 1), (0x1815, 0), (0x40A0, 0), (0x40A2, 0),
                    (0x40A3, 0), (0x1982, 0), (0x1982, 2), (0x1983, 1), (0x4521, 0), (0x4521, 1), (0x4530, 0),
                    (0x4531, 0), (0x4531, 1), (0x4220, 0), (0x4540, 0)}


class Device:
    """A made-up HID++ 2.0 device: its feature table, name, kind and controls."""

    def __init__(self, name="Synthetic Keyboard K1", kind=0, features=(0x0001, 0x0003, 0x0005, 0x1814, 0x1B04),
                 controls=((0x00C7, 0x00C7, 0x30, 0x01, 0), (0x00D1, 0x00D1, 0x04, 0x00, 0), (0x0034, 0x0034, 0x02, 0x00, 0))):
        self.name = name.encode()
        self.kind = kind
        self.table = [0x0000, *features]   # index -> feature ID
        self.controls = list(controls)     # (cid, tid, capability flags, reporting flags, remap)
        self.feature_count = None          # what getCount says, if not the truth
        self.control_count = None
        self.name_chunk = 16
        self.answers = {}                  # (feature ID, function) -> params -> answer, or raises
        self.versions = {}                 # feature ID -> the version Root getFeature tells


class Port:
    """Stands in for a transport: answers as its devices would and keeps every request it was sent."""

    def __init__(self, devices, is_receiver=True):
        self.devices = devices          # devidx -> Device, or "silent"
        self.is_receiver = is_receiver
        self.sent = []                  # (devidx, feature ID, function, params)
        self.timeouts = []
        self.closed = False
        self.trap = None                # an exception to raise on the next request

    def slots(self):
        return hidpp.RECEIVER_SLOTS if self.is_receiver else (hidpp.DIRECT_INDEX,)

    def close(self):
        self.closed = True

    def request(self, devidx, feat_idx, function, params=b"", timeout=1.5, no_reply=False):
        if self.trap:
            raise self.trap
        self.timeouts.append(timeout)
        dev = self.devices.get(devidx)
        if dev is None:
            raise hidpp.HidppOffline(f"slot {devidx}")
        if dev == "silent":
            raise hidpp.HidppTimeout("no reply")
        feature = dev.table[feat_idx] if feat_idx < len(dev.table) else None
        self.sent.append((devidx, feature, function, bytes(params)))
        answer = dev_answer(dev, feature, function, bytes(params))
        return answer.ljust(16, b"\0") if answer else answer  # a long report, or a report with nothing in it


def dev_answer(dev, feature, function, params):
    if (feature, function) in dev.answers:
        return dev.answers[feature, function](params)
    if (feature, function) == (0x0000, 0):
        wanted = struct.unpack("!H", params[:2])[0]
        return bytes([dev.table.index(wanted) if wanted in dev.table else 0, 0, dev.versions.get(wanted, 0)])
    if (feature, function) == (0x0000, 1):
        return bytes([4, 5, params[2]])
    if (feature, function) == (0x0001, 0):
        return bytes([len(dev.table) - 1 if dev.feature_count is None else dev.feature_count])
    if (feature, function) == (0x0001, 1):
        index = params[0]
        return struct.pack("!H", dev.table[index] if index < len(dev.table) else 0x7000 + index)
    if (feature, function) == (0x0005, 0):
        return bytes([len(dev.name)])
    if (feature, function) == (0x0005, 1):
        return dev.name[params[0]:params[0] + dev.name_chunk]
    if (feature, function) == (0x0005, 2):
        return bytes([dev.kind])
    if (feature, function) == (0x1B04, 0):
        return bytes([len(dev.controls) if dev.control_count is None else dev.control_count])
    if (feature, function) == (0x1B04, 1):
        cid, tid, flags, _, _ = dev.controls[params[0] % len(dev.controls)]
        return struct.pack("!HHBBBBB", cid, tid, flags, 0, 0, 0, 0)
    if (feature, function) == (0x1B04, 2):
        cid = struct.unpack("!H", params[:2])[0]
        _, _, _, reporting, remap = next(c for c in dev.controls if c[0] == cid)
        return struct.pack("!HBH", cid, reporting, remap)
    raise hidpp.HidppFeatureError(7)


def receiver():
    """Keyboard in slot 1, a silent (sleeping) device in slot 3, a mouse in slot 4, the rest empty."""
    return Port({1: Device(), 3: "silent", 4: Device(name="Synthetic Mouse", kind=3, features=(0x0001, 0x0005, 0x1B04),
                                                     controls=((0x0050, 0x0038, 0x01, 0, 0),))})


def probe(port):
    return hidpp_probe.probe(hidpp_probe.ReadOnly(port, timeout=0.2))


class OnlyReads(unittest.TestCase):
    def test_the_tool_list_is_the_documented_reads(self):
        self.assertEqual(set(hidpp_probe.RULES), DOCUMENTED_READS)

    def test_every_request_sent_is_a_documented_read(self):
        port = receiver()
        probe(port)
        self.assertTrue(port.sent)
        self.assertEqual({(f, fn) for _, f, fn, _ in port.sent} - DOCUMENTED_READS, set())
        self.assertNotIn((0x0003, 0), {(f, fn) for _, f, fn, _ in port.sent})   # getDeviceInfo: the unit ID
        self.assertNotIn(0x1814, {f for _, f, _, _ in port.sent})   # change host

    def test_what_is_not_a_read_is_never_sent(self):
        port = receiver()
        guard = hidpp_probe.ReadOnly(port)
        with self.assertRaises(hidpp_probe.Refused):
            guard.request(1, 5, 0)  # index 5 has not been named by the device yet
        reprog = guard.request(1, 0, 0, struct.pack("!H", 0x1B04))[0]
        with self.assertRaises(hidpp_probe.Refused):
            guard.request(1, reprog, 3, struct.pack("!HBH", 0x00C7, 0x03, 0))  # setCidReporting
        change_host = guard.request(1, 0, 0, struct.pack("!H", 0x1814))[0]
        with self.assertRaises(hidpp_probe.Refused):
            guard.request(1, change_host, 1, bytes([0]))
        with self.assertRaises(hidpp_probe.Refused):
            guard.request(1, change_host, 0)  # a read, but not of anything this tool shows
        with self.assertRaises(hidpp_probe.Refused):
            guard.request(9, 0, 1, bytes([0, 0, 1]))  # not a slot of this node
        self.assertEqual([fn for _, _, fn, _ in port.sent], [0, 0])

    def test_a_device_that_contradicts_itself_is_left(self):
        port = receiver()
        guard = hidpp_probe.ReadOnly(port)
        guard.request(1, 0, 0, struct.pack("!H", 0x1B04))
        port.devices[1].table[5], port.devices[1].table[4] = port.devices[1].table[4], port.devices[1].table[5]
        with self.assertRaises(hidpp.HidppError):
            guard.request(1, 0, 0, struct.pack("!H", 0x1814))  # now at the index 0x1B04 was at

    def test_no_request_waits_longer_than_asked(self):
        port = receiver()
        probe(port)
        self.assertLessEqual(max(port.timeouts), 0.2)


class Findings(unittest.TestCase):
    def test_what_the_devices_say(self):
        found = probe(receiver())
        self.assertEqual([d["slot"] for d in found], [1, 4])
        keyboard = found[0]
        self.assertEqual((keyboard["name"], keyboard["kind"], keyboard["protocol"]), ("Synthetic Keyboard K1", 0, "4.5"))
        self.assertEqual([f["id"] for f in keyboard["features"]], ["0x0001", "0x0003", "0x0005", "0x1814", "0x1b04"])
        self.assertEqual(keyboard["controls"][0],
                         {"cid": "0x00c7", "tid": "0x00c7", "flags": "0x0030", "reporting": "0x01", "remap": "0x0000"})
        self.assertIsNone(keyboard["error"])
        text = "\n".join(hidpp_probe.describe([{"node": 1, "receiver": True, "devices": found, "error": None}]))
        self.assertIn("slot 1: Synthetic Keyboard K1 (keyboard), HID++ 4.5", text)
        self.assertIn("control 0x00c7 task 0x00c7: reprog, divert; now diverted", text)
        self.assertIn("slot 4: Synthetic Mouse (mouse)", text)

    def test_nothing_private_in_the_output(self):
        port = receiver()
        results = hidpp_probe.run([("/dev/hidraw7", True)], open_transport=lambda path, is_receiver: port)
        self.assertNotIn("hidraw", json.dumps(results))
        allowed = {"slot", "protocol", "name", "kind", "features", "controls", "firmware", "settings", "truncated",
                   "error"}
        for dev in results[0]["devices"]:
            self.assertLessEqual(set(dev), allowed)

    def test_a_name_that_stops_coming(self):
        port = receiver()
        port.devices[1].name_chunk = 0
        found = probe(port)
        self.assertEqual(found[0]["error"], "the name stopped coming")
        self.assertEqual(found[1]["name"], "Synthetic Mouse")  # the next device is still asked

    def test_bad_ping_leaves_other_slots_available(self):
        for error in (hidpp.HidppClosed("synthetic"), IndexError("short"), struct.error("short")):
            port = receiver()
            original = port.request

            def request(devidx, feat_idx, function, params=b"", **kwargs):
                if devidx == 1:
                    raise error
                return original(devidx, feat_idx, function, params, **kwargs)

            port.request = request
            results = hidpp_probe.run([("synthetic", True)], open_transport=lambda *args: port)
            devices = results[0]["devices"]
            self.assertIsNotNone(devices[0]["error"])
            self.assertEqual(devices[-1]["name"], "Synthetic Mouse")
            self.assertIn("stopped:", "\n".join(hidpp_probe.describe(results)))
            self.assertTrue(port.closed)

    def test_counts_are_capped(self):
        port = receiver()
        port.devices[1].feature_count = 255
        port.devices[1].control_count = 255
        port.devices[1].name = b"N" * 200
        found = probe(port)[0]
        self.assertEqual(found["truncated"], ["features", "name", "controls"])
        self.assertEqual(len(found["features"]), hidpp_probe.MAX_FEATURES)
        self.assertEqual(len(found["controls"]), hidpp_probe.MAX_CONTROLS)
        self.assertEqual(len(found["name"]), hidpp_probe.MAX_NAME)

    def test_a_device_that_stops_answering(self):
        port = receiver()
        port.devices[1].controls = [(0x00C7, 0x00C7, 0x30, 0x01, 0)]
        real = port.request

        def request(devidx, feat_idx, function, params=b"", timeout=1.5):
            if devidx == 1 and function == 2 and feat_idx == 5:
                raise hidpp.HidppTimeout("no reply from device 1")
            return real(devidx, feat_idx, function, params, timeout)

        port.request = request
        found = probe(port)
        self.assertEqual(found[0]["error"], "no reply from device 1")
        self.assertEqual(found[1]["slot"], 4)


# A made-up keyboard with every setting feature the tool reads, those it leaves and those it must never touch.
UNIT_ID = bytes.fromhex("c0ffee42")
HOST_NAME = b"SYNTHETIC-HOST"
TRAIL = b"\xee" * 6  # bytes after what each answer means: never shown
FEATURES = (0x0001, 0x0003, 0x0005, 0x1814, 0x1815, 0x40A0, 0x40A2, 0x40A3, 0x1981, 0x1982, 0x1983, 0x1990,
            0x4220, 0x4521, 0x4522, 0x4530, 0x4531, 0x4540, 0x1B04)
BACKLIGHT = bytes(range(0x10, 0x1E))  # fourteen bytes, shown as they are
BACKLIGHT_INFO = bytes([0x08, 0x02, 0x05, 0x00]) + bytes(range(0x30, 0x38))  # twelve bytes, shown as they are
FIRMWARE = (bytes([0, *b"ABC", 0x12, 0x03, 0x00, 0x21, 0x01, 0xB3, 0x69]) + TRAIL,   # main application, running
            bytes([1, *b"BOT", 0x01, 0x02, 0x00, 0x05, 0x00, 0xB3, 0x69]) + TRAIL,   # bootloader
            bytes([2, 0x00, 0x00, 0x00, 0x1A, 0x00, 0x00, 0x00, 0x00]) + TRAIL)       # neither ASCII nor BCD
FIRMWARE_SHOWN = [
    {"index": 0, "type": 0, "prefix": "ABC", "version": "12.3.21", "active": True},
    {"index": 1, "type": 1, "prefix": "BOT", "version": "1.2.5", "active": False},
    {"index": 2, "type": 2, "prefix": None, "version": None, "active": False},
]
SETTINGS_SHOWN = {
    "0x40a0": {"0": {"bytes": "01"}},
    "0x40a2": {"0": {"bytes": "0001"}},
    "0x40a3": {"0": {"bytes": "01010001"}},
    "0x1982": {"0": {"bytes": BACKLIGHT.hex()}, "2": {"bytes": BACKLIGHT_INFO.hex()}, "version": {"bytes": "03"}},
    "0x1983": {"1": {"bytes": "01f4"}},
    "0x4521": {"0": {"bytes": "1f"}, "1": {"bytes": "04"}},
    "0x4530": {"0": {"bytes": "01"}},
    "0x4531": {"0": {"bytes": "02000502030101"}, "1:0": {"bytes": "000001000a000f00"},
               "1:1": {"bytes": "010001000a000f00"}},
    "0x4220": {"0": {"bytes": "02"}},
    "0x4540": {"0": {"bytes": "0d"}},
}


def refuse(code=2):
    def answer(params):
        raise hidpp.HidppFeatureError(code)
    return answer


def silent(params):
    raise hidpp.HidppTimeout("no reply")


def keyboard(host=1):
    dev = Device(features=FEATURES)
    dev.versions[0x1982] = 3

    def firmware(params):
        if params[0] >= len(FIRMWARE):
            raise hidpp.HidppFeatureError(2)
        return FIRMWARE[params[0]]

    def fn_hosts(params):
        if params != bytes([host]):
            raise hidpp.HidppFeatureError(2)
        return bytes([host, 0x01, 0x00, 0x01]) + TRAIL

    def platform(params):
        return bytes([params[0], 0x00, 0x01, 0x00, 0x0A, 0x00, 0x0F, 0x00]) + TRAIL

    dev.answers.update({
        (0x0003, 0): lambda params: bytes([3]) + UNIT_ID + bytes(2) + b"MODEL1",
        (0x0003, 1): firmware,
        (0x1815, 0): lambda params: bytes([0x01, 0x00, 3, host]) + TRAIL,
        (0x1815, 1): lambda params: HOST_NAME,
        (0x40A0, 0): lambda params: b"\x01" + TRAIL,
        (0x40A2, 0): lambda params: b"\x00\x01" + TRAIL,
        (0x40A3, 0): fn_hosts,
        (0x1982, 0): lambda params: BACKLIGHT + TRAIL[:2],
        (0x1982, 2): lambda params: BACKLIGHT_INFO + TRAIL,
        (0x1983, 1): lambda params: b"\x01\xf4" + TRAIL,
        (0x4521, 0): lambda params: b"\x1f" + TRAIL,
        (0x4521, 1): lambda params: b"\x04" + TRAIL,
        (0x4530, 0): lambda params: b"\x01" + TRAIL,
        # five platforms, two descriptors (byte 3: the count, not byte 2), three hosts, on host 1, platform 1
        (0x4531, 0): lambda params: bytes([0x02, 0x00, 5, 2, 3, 1, 0x01]) + TRAIL,
        (0x4531, 1): platform,
        (0x4220, 0): lambda params: b"\x02" + TRAIL,
        (0x4540, 0): lambda params: b"\x0d" + TRAIL,
    })
    return dev


def index_of(dev, feature):
    return dev.table.index(feature)


def learned(port, devidx=1):
    """A guard that has learned every feature index of the keyboard at ``devidx`` from Root getFeature."""
    guard = hidpp_probe.ReadOnly(port, timeout=0.2)
    for feature in port.devices[devidx].table[1:]:
        guard.request(devidx, 0, 0, struct.pack("!H", feature))
    return guard


# Each read with parameters it takes, and parameters it must refuse (once the guard knows the current host
# 1 and two platform descriptors).
VALID = {
    (0x0000, 0): struct.pack("!H", 0x1815), (0x0000, 1): bytes([0, 0, 7]),
    (0x0001, 0): b"", (0x0001, 1): bytes([1]),
    (0x0005, 0): b"", (0x0005, 1): bytes([0]), (0x0005, 2): b"",
    (0x1B04, 0): b"", (0x1B04, 1): bytes([0]), (0x1B04, 2): struct.pack("!H", 0x00C7),
    (0x0003, 1): bytes([0]), (0x1815, 0): b"",
    (0x40A0, 0): b"", (0x40A2, 0): b"", (0x40A3, 0): bytes([1]),
    (0x1982, 0): b"", (0x1982, 2): b"", (0x1983, 1): b"",
    (0x4521, 0): b"", (0x4521, 1): b"", (0x4530, 0): b"",
    (0x4531, 0): b"", (0x4531, 1): bytes([1]), (0x4220, 0): b"", (0x4540, 0): b"",
}
INVALID = {
    (0x0000, 0): [b"", b"\x18", b"\x18\x15\x00"], (0x0000, 1): [b"\x01\x00\x07", b"\x00\x00", b"\x00\x00\x07\x00"],
    (0x0001, 0): [b"\x00"], (0x0001, 1): [b"", bytes([65]), b"\x01\x00"],
    (0x0005, 0): [b"\x00"], (0x0005, 1): [bytes([64]), b"", b"\x00\x00"], (0x0005, 2): [b"\x00"],
    (0x1B04, 0): [b"\x00"], (0x1B04, 1): [bytes([128]), b""], (0x1B04, 2): [b"\x00", b"\x00\xc7\x01"],
    (0x0003, 1): [bytes([8]), b"", b"\x00\x00"], (0x1815, 0): [b"\x00"],
    (0x40A0, 0): [b"\x00"], (0x40A2, 0): [b"\x00"], (0x40A3, 0): [b"", bytes([2]), bytes([0xFF]), b"\x01\x00"],
    (0x1982, 0): [b"\x00"], (0x1982, 2): [b"\x00"], (0x1983, 1): [b"\x09"],
    (0x4521, 0): [b"\x00"], (0x4521, 1): [b"\x00"], (0x4530, 0): [b"\x00"],
    (0x4531, 0): [b"\x00"], (0x4531, 1): [bytes([2]), bytes([4]), b"", b"\x00\x00"],  # 4: under byte 2, not byte 3
    (0x4220, 0): [b"\x00"], (0x4540, 0): [b"\x00"],
}
# Writes, and reads of what is private or of features the tool leaves alone; none may ever be sent.
NEVER = [
    (0x0003, 0, b""),                                         # getDeviceInfo: the unit ID
    (0x1815, 1, b"\x00"), (0x1815, 2, b"\x00"), (0x1815, 3, b"\x00\x00"),  # host names
    (0x1814, 0, b""), (0x1814, 1, b"\x00"),                   # change host
    (0x40A0, 1, b"\x01"), (0x40A2, 1, b"\x01"), (0x40A3, 1, b"\x01\x01"),
    (0x1982, 1, bytes(12)), (0x1982, 3, bytes(3)), (0x1983, 2, b"\x00\x00\x09"), (0x4521, 2, b"\x00"),
    (0x4530, 1, b"\x00"), (0x4530, 2, b"\x00"), (0x4531, 3, b"\xff\x00"), (0x4531, 3, b"\x01\x00"),
    (0x4522, 1, b"\x00\x39"), (0x4522, 2, b"\x00\x39"), (0x4522, 3, b""),
    (0x1B04, 3, struct.pack("!HBH", 0x00C7, 0x03, 0)),
    (0x1981, 0, b""), (0x1990, 0, b""), (0x4522, 0, b""),
]


class KeyboardGuard(unittest.TestCase):
    def test_each_read_goes_out_with_its_parameters_only(self):
        self.assertEqual(set(VALID), DOCUMENTED_READS)
        self.assertEqual(set(INVALID), DOCUMENTED_READS)
        port = Port({1: keyboard()})
        guard = learned(port)
        guard.request(1, index_of(port.devices[1], 0x1815), 0)
        guard.request(1, index_of(port.devices[1], 0x4531), 0)
        for (feature, function), params in VALID.items():
            with self.subTest(feature=hex(feature), function=function):
                guard.request(1, index_of(port.devices[1], feature), function, params)
                self.assertEqual(port.sent[-1], (1, feature, function, params))
        sent = list(port.sent)
        for (feature, function), wrong in INVALID.items():
            for params in wrong:
                with self.subTest(feature=hex(feature), function=function, params=params.hex()):
                    with self.assertRaises(hidpp_probe.Refused):
                        guard.request(1, index_of(port.devices[1], feature), function, params)
        self.assertEqual(port.sent, sent)

    def test_reads_that_depend_on_an_earlier_answer_wait_for_it(self):
        port = Port({1: keyboard()})
        guard = learned(port)
        sent = list(port.sent)
        for feature, function, params in ((0x40A3, 0, b"\x01"), (0x4531, 1, b"\x00")):
            with self.subTest(feature=hex(feature)), self.assertRaises(hidpp_probe.Refused):
                guard.request(1, index_of(port.devices[1], feature), function, params)
        self.assertEqual(port.sent, sent)

    def test_the_current_host_is_that_of_the_same_device(self):
        port = Port({1: keyboard(host=1), 2: keyboard(host=2), 3: keyboard(host=1)})
        guard = learned(port, 1)
        for devidx in (2, 3):
            for feature in port.devices[devidx].table[1:]:
                guard.request(devidx, 0, 0, struct.pack("!H", feature))
        hosts, fn_hosts = index_of(port.devices[1], 0x1815), index_of(port.devices[1], 0x40A3)
        guard.request(2, hosts, 0)
        guard.request(1, hosts, 0)
        for devidx, host in ((1, 2), (2, 1), (3, 1)):  # another device's host, or none read from this one
            with self.subTest(devidx=devidx), self.assertRaises(hidpp_probe.Refused):
                guard.request(devidx, fn_hosts, 0, bytes([host]))
        guard.request(1, fn_hosts, 0, b"\x01")
        guard.request(2, fn_hosts, 0, b"\x02")
        self.assertEqual([(d, p) for d, f, _, p in port.sent if f == 0x40A3], [(1, b"\x01"), (2, b"\x02")])

    def test_writes_and_private_reads_are_never_sent(self):
        port = Port({1: keyboard()})
        guard = learned(port)
        guard.request(1, index_of(port.devices[1], 0x1815), 0)
        sent, waited = list(port.sent), list(port.timeouts)
        for feature, function, params in NEVER:
            with self.subTest(feature=hex(feature), function=function), self.assertRaises(hidpp_probe.Refused):
                guard.request(1, index_of(port.devices[1], feature), function, params)
        for feature in FEATURES:  # and every other function, of every feature, with or without parameters
            for function in range(16):
                if (feature, function) in DOCUMENTED_READS:
                    continue
                for params in (b"", b"\x00", b"\x01", b"\xff\x00"):
                    with self.assertRaises(hidpp_probe.Refused):
                        guard.request(1, index_of(port.devices[1], feature), function, params)
        self.assertEqual((port.sent, port.timeouts), (sent, waited))

    def test_an_index_named_twice_is_not_used_for_the_second_feature(self):
        port = Port({1: keyboard()})
        guard = hidpp_probe.ReadOnly(port, timeout=0.2)
        fn_index = index_of(port.devices[1], 0x40A0)
        guard.request(1, 0, 0, struct.pack("!H", 0x0001))
        guard.request(1, 1, 1, bytes([fn_index]))  # FeatureSet: 0x40A0 at this index
        port.devices[1].answers[0x0000, 0] = lambda params: bytes([fn_index, 0, 3])  # now every feature is there
        with self.assertRaises(hidpp.HidppError):
            guard.request(1, 0, 0, struct.pack("!H", 0x40A3))
        sent = list(port.sent)
        with self.assertRaises(hidpp_probe.Refused):
            guard.request(1, fn_index, 0, b"\x01")  # a 0x40A3 read sent to 0x40A0
        self.assertEqual(port.sent, sent)

    def test_a_keyboard_that_contradicts_itself_is_left(self):
        port = Port({1: keyboard(), 4: Device(name="Synthetic Mouse", kind=3, features=(0x0001, 0x0005))})
        dev = port.devices[1]

        def get_feature(params):  # the backlight feature is said to be where Fn inversion is
            wanted = struct.unpack("!H", params)[0]
            index = index_of(dev, 0x40A0) if wanted == 0x1982 else dev.table.index(wanted) if wanted in dev.table else 0
            return bytes([index, 0, 3])

        dev.answers[0x0000, 0] = get_feature
        found = probe(port)
        self.assertIn("two features", found[0]["error"])
        self.assertNotIn((1, 0x1982, 2, b""), port.sent)
        self.assertEqual(found[1]["name"], "Synthetic Mouse")


class KeyboardReads(unittest.TestCase):
    def test_what_a_keyboard_says(self):
        port = Port({1: keyboard()})
        found = probe(port)[0]
        self.assertIsNone(found["error"])
        self.assertEqual(found["firmware"], FIRMWARE_SHOWN)
        self.assertEqual(found["settings"], SETTINGS_SHOWN)
        self.assertEqual({(f, fn) for _, f, fn, _ in port.sent} - DOCUMENTED_READS, set())
        text = "\n".join(hidpp_probe.describe([{"node": 1, "receiver": True, "devices": [found], "error": None}]))
        self.assertIn("firmware 0: application ABC 12.3.21, running", text)
        self.assertIn("firmware 2: hardware ? ?", text)
        self.assertIn("setting 0x40a3: function 0 01010001", text)
        self.assertIn("setting 0x4531: function 0 02000502030101, function 1:0 000001000a000f00", text)
        self.assertIn("setting 0x1982: function 0 " + BACKLIGHT.hex() + ", function 2 " + BACKLIGHT_INFO.hex()
                      + ", function version 03", text)

    def test_firmware_is_listed_without_getdeviceinfo(self):
        for case in ("error", "no answer", "short", "all eight"):
            with self.subTest(case=case):
                port = Port({1: keyboard()})
                dev = port.devices[1]

                def firmware(params, case=case):
                    if case == "all eight":
                        return FIRMWARE[0]
                    if params[0] == 1:
                        if case == "no answer":
                            silent(params)
                        if case == "short":
                            return b""
                        refuse(2)(params)
                    return FIRMWARE[params[0]]

                dev.answers[0x0003, 1] = firmware
                found = probe(port)[0]
                asked = [p[0] for _, f, fn, p in port.sent if (f, fn) == (0x0003, 1)]
                self.assertEqual(asked, list(range(8)) if case == "all eight" else [0, 1])
                self.assertEqual(len(found["firmware"]), 8 if case == "all eight" else 1)
                self.assertEqual(found["settings"], SETTINGS_SHOWN)  # the rest is still read
                self.assertNotIn((0x0003, 0), {(f, fn) for _, f, fn, _ in port.sent})

    def test_current_host_comes_from_the_same_device(self):
        port = Port({1: keyboard(host=1), 2: keyboard(host=2)})
        probe(port)
        fn_hosts = [(devidx, p) for devidx, f, fn, p in port.sent if f == 0x40A3]
        self.assertEqual(fn_hosts, [(1, b"\x01"), (2, b"\x02")])

    def test_without_the_current_host_fn_inversion_per_host_is_not_read(self):
        for case in ("no hosts info", "error", "no answer", "short"):
            with self.subTest(case=case):
                dev = keyboard()
                if case == "no hosts info":
                    dev.table[index_of(dev, 0x1815)] = 0x7777
                elif case == "error":
                    dev.answers[0x1815, 0] = refuse(9)
                elif case == "short":
                    dev.answers[0x1815, 0] = lambda params: b""
                else:
                    dev.answers[0x1815, 0] = silent
                port = Port({1: dev})
                found = probe(port)[0]
                self.assertNotIn(0x40A3, {f for _, f, _, _ in port.sent})
                self.assertEqual(found["settings"], {**SETTINGS_SHOWN, "0x40a3": {"0": {"error": "not read"}}})

    def test_backlight_information_is_read_on_every_version_and_the_version_shown(self):
        for version in (2, 3):
            with self.subTest(version=version):
                port = Port({1: keyboard()})
                port.devices[1].versions[0x1982] = version
                found = probe(port)[0]
                self.assertIn((0x1982, 2), {(f, fn) for _, f, fn, _ in port.sent})
                self.assertEqual(found["settings"]["0x1982"], {"0": {"bytes": BACKLIGHT.hex()},
                                                               "2": {"bytes": BACKLIGHT_INFO.hex()},
                                                               "version": {"bytes": f"{version:02x}"}})
        port = Port({1: keyboard()})
        dev = port.devices[1]

        def get_feature(params):  # no version told for the backlight feature: an empty answer
            wanted = struct.unpack("!H", params)[0]
            return b"" if wanted == 0x1982 else bytes([dev.table.index(wanted) if wanted in dev.table else 0, 0, 0])
        dev.answers[0x0000, 0] = get_feature
        found = probe(port)[0]
        self.assertEqual(found["settings"]["0x1982"]["version"], {"error": "not read"})
        self.assertEqual(found["settings"]["0x1982"]["2"], {"bytes": BACKLIGHT_INFO.hex()})

    def test_platform_descriptors_are_counted_by_byte_3_alone(self):
        for byte2, byte3, asked in ((5, 2, 2), (0, 3, 3), (9, 0, 0), (0, 40, hidpp_probe.MAX_PLATFORMS)):
            with self.subTest(byte2=byte2, byte3=byte3):
                port = Port({1: keyboard()})
                port.devices[1].answers[0x4531, 0] = lambda params, b2=byte2, b3=byte3: bytes([0x02, 0, b2, b3, 3, 1, 1])
                found = probe(port)[0]
                self.assertEqual([p for _, f, fn, p in port.sent if (f, fn) == (0x4531, 1)],
                                 [bytes([i]) for i in range(asked)])
                self.assertEqual(len(found["settings"]["0x4531"]), 1 + asked)

    def test_a_read_that_fails_does_not_hide_the_others(self):
        # (feature, function, platform index or None) -> keys of that feature asked after it
        reads = [(0x40A0, 0, None), (0x40A2, 0, None), (0x40A3, 0, None), (0x1982, 0, None), (0x1982, 2, None),
                 (0x1983, 1, None), (0x4521, 0, None), (0x4521, 1, None), (0x4530, 0, None), (0x4531, 0, None),
                 (0x4531, 1, 0), (0x4220, 0, None), (0x4540, 0, None)]
        for (feature, function, which), failure in [(r, f) for r in reads for f in ("error", "no answer", "empty")]:
            with self.subTest(feature=hex(feature), function=function, failure=failure):
                dev = keyboard()
                good = dev.answers[feature, function]

                def answer(params, good=good, failure=failure, which=which):
                    if which is not None and params != bytes([which]):
                        return good(params)
                    if failure == "error":
                        raise hidpp.HidppFeatureError(9)
                    if failure == "no answer":
                        silent(params)
                    return b""

                dev.answers[feature, function] = answer
                found = probe(Port({1: dev}))[0]
                self.assertIsNone(found["error"])
                name, key = f"0x{feature:04x}", str(function) if which is None else f"1:{which}"
                expected = copy.deepcopy(SETTINGS_SHOWN)
                if feature == 0x1982 and function == 0 and failure == "no answer":
                    del expected[name]["2"], expected[name]["version"]  # the rest of the feature is left
                if feature == 0x1982 and function == 2 and failure == "no answer":
                    del expected[name]["version"]
                if feature == 0x4521 and function == 0 and failure == "no answer":
                    del expected[name]["1"]
                if feature == 0x4531 and function == 0:
                    del expected[name]["1:0"], expected[name]["1:1"]  # no count, no descriptors
                if feature == 0x4531 and function == 1 and failure == "no answer":
                    del expected[name]["1:1"]
                expected[name][key] = {"error": {"error": "HID++ error 9 (unsupported)", "no answer": "no answer",
                                                 "empty": "invalid reply"}[failure]}
                self.assertEqual(found["settings"], expected)
                self.assertEqual(found["firmware"], FIRMWARE_SHOWN)

    def test_only_the_named_bytes_are_shown(self):
        port = Port({1: keyboard()})
        results = hidpp_probe.run([("/dev/hidraw7", True)], open_transport=lambda path, is_receiver: port)
        text = json.dumps(results) + "\n".join(hidpp_probe.describe(results))
        for private in (UNIT_ID.hex(), HOST_NAME.decode(), HOST_NAME.hex(), "MODEL1", "b369", "eeee", "hidraw"):
            self.assertNotIn(private, text)
        found = results[0]["devices"][0]
        for entity in found["firmware"]:
            self.assertEqual(set(entity), {"index", "type", "prefix", "version", "active"})
        for answers in found["settings"].values():
            for answer in answers.values():
                self.assertEqual(len(answer), 1)
        self.assertEqual(found["settings"]["0x1982"]["0"], {"bytes": BACKLIGHT.hex()})  # not taken apart
        self.assertEqual({(f, fn) for _, f, fn, _ in port.sent} & {(0x0003, 0), (0x1815, 1), (0x1815, 2), (0x1815, 3)},
                         set())

    def test_the_reads_are_bounded(self):
        port = Port({1: keyboard()})
        dev = port.devices[1]
        dev.answers[0x0003, 1] = lambda params: FIRMWARE[0]
        dev.answers[0x4531, 0] = lambda params: bytes([0x02, 0x00, 0, 255, 0, 0, 0x01])
        found = probe(port)[0]
        self.assertEqual(len(found["settings"]["0x4531"]), 1 + hidpp_probe.MAX_PLATFORMS)
        keyboard_reads = [s for s in port.sent if s[1] in (0x0003, 0x1815, 0x40A0, 0x40A2, 0x40A3, 0x1982, 0x1983,
                                                            0x4521, 0x4530, 0x4531, 0x4220, 0x4540)]
        root_lookups = [s for s in port.sent if s[1:3] == (0x0000, 0) and s[3] == struct.pack("!H", 0x1982)]
        self.assertEqual(len(keyboard_reads) + len(root_lookups), 38)
        self.assertLessEqual(max(port.timeouts), 0.2)


class Bounds(unittest.TestCase):
    def test_time_runs_out(self):
        ports = [receiver(), receiver()]
        opened = iter(ports)
        hidpp_probe.BUDGET, budget = -1.0, hidpp_probe.BUDGET
        try:
            results = hidpp_probe.run([("a", True), ("b", True)], open_transport=lambda path, is_receiver: next(opened))
        finally:
            hidpp_probe.BUDGET = budget
        self.assertEqual([r["error"] for r in results], ["time ran out"])
        self.assertEqual(ports[0].sent, [])
        self.assertTrue(ports[0].closed)

    def test_a_budget_bounds_the_whole_run(self):
        port = Port({slot: "silent" for slot in hidpp.RECEIVER_SLOTS})
        guard = hidpp_probe.ReadOnly(port, timeout=0.2, deadline=time.monotonic() + 0.05)
        start = time.monotonic()
        with self.assertRaises(hidpp_probe.OutOfTime):
            while True:
                time.sleep(0.01)
                hidpp_probe.probe(guard)
        self.assertLess(time.monotonic() - start, 1.0)

    def test_closed_on_interrupt_and_on_a_refusal(self):
        for trap in (KeyboardInterrupt(), hidpp_probe.Refused("no")):
            port = receiver()
            port.trap = trap
            with self.assertRaises(type(trap)):
                hidpp_probe.run([("a", True)], open_transport=lambda path, is_receiver: port)
            self.assertTrue(port.closed)

    def test_a_node_that_cannot_be_opened(self):
        def refuse(path, is_receiver):
            raise PermissionError(13, "Permission denied", path)

        results = hidpp_probe.run([("/dev/hidraw7", False)], open_transport=refuse)
        self.assertEqual(results[0]["error"], "cannot be opened: Permission denied")
        self.assertIn("Node 1 (direct): cannot be opened", hidpp_probe.describe(results)[0])

    def test_running_manager_prevents_hardware_access(self):
        for app_running, rivals in ((True, []), (False, ["Solaar"]), (True, ["Solaar"])):
            with patch.object(hidpp_probe, "_app_running", return_value=app_running), \
                    patch.object(hidpp_probe.environment, "rivals", return_value=rivals), \
                    patch.object(hidpp_probe, "run") as run, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(hidpp_probe.main([]), 2)
                run.assert_not_called()

    def test_help(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as stop:
            hidpp_probe.main(["--help"])
        self.assertEqual(stop.exception.code, 0)
        self.assertIn("--json", out.getvalue())
        self.assertNotIn("--request", out.getvalue())


if __name__ == "__main__":
    unittest.main()
