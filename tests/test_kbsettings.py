"""Settings the keyboard keeps itself (mxoptions/kbsettings.py): Fn lock read, changed, read back and, where the
keyboard takes something else, put back; never on what is not known for certain, and never in the way of the keys or
the mouse. The engine runs on the test's thread with real transports on FakePorts; devices, answers and ids are
synthetic (tests/support.py)."""

import copy
import itertools
import json
import logging
import os
import queue
import struct
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.support import Actions, FakeDevice, FakePort  # noqa: E402
from tests.test_keyboard import COPY, UNIT_A, UNIT_B, Rig, identity, keyboard_device, mouse_device  # noqa: E402
from mxoptions import actions, config, device, engine, hidpp, kbsettings, keyboard  # noqa: E402

FnCodec = kbsettings.FnCodec
HOSTS = device.FN_INVERSION_HOSTS


def fn_keyboard(unit=UNIT_A, states=None, current=0, **kwargs):
    """A synthetic keyboard with Fn inversion per channel, on channel ``current`` of three. State 1: the bare top-row
    key gives its other function (value False); default state 1 too."""
    states = states if states is not None else {0: [1, 1], 1: [0, 1], 2: [1, 1]}
    return keyboard_device(unit, hosts=(3, current), settings={device.HOSTS_INFO: current, HOSTS: states}, **kwargs)


def fn(rig):
    return rig.state()["controls"].get("fn")


def packets(port, dev, feature, function):
    index = dev.index(feature)
    return [p for p in port.written if p[1] == 1 and p[2] == index and p[3] >> 4 == function]


class Stub:
    """A device for the codec alone: the features it lists and what each (feature, function) answers."""

    def __init__(self, features, answers=(), host_count=3):
        self.features, self.answers, self.host_count, self.calls = set(features), dict(answers), host_count, []

    def has(self, feature):
        return feature in self.features

    def call(self, feature, function, *params):
        self.calls.append((feature, function, bytes(params)))
        answer = self.answers.get((feature, function), b"")
        if isinstance(answer, Exception):
            raise answer
        return answer


class Decode(unittest.TestCase):
    def test_per_channel_state_default_and_what_does_not_fit(self):
        dev = Stub({HOSTS, device.HOSTS_INFO})
        for reply, value, default in ((b"\x01\x00\x00", True, True), (b"\x01\x01\x01", False, False),
                                      (b"\x01\x00\x01\xee\xee", True, False), (b"\x01\x01\x02", False, None),
                                      (b"\x01\x00\xff", True, None)):
            with self.subTest(reply=reply.hex()):
                dev.answers[HOSTS, 0] = reply
                self.assertEqual(FnCodec.read(dev, 1), {"value": value, "default": default})
                self.assertEqual(dev.calls[-1], (HOSTS, 0, b"\x01"))
        for reply in (b"\x02\x00\x00", b"\x01\x02\x00", b"\x01\xff\x00", b"\x01\x00", b""):
            with self.subTest(reply=reply.hex()), self.assertRaises(kbsettings.BadReply):
                dev.answers[HOSTS, 0] = reply
                FnCodec.read(dev, 1)

    def test_the_features_without_channels(self):
        dev = Stub({device.FN_INVERSION_DEFAULT})
        for reply, expected in ((b"\x00\x01", {"value": True, "default": False}),
                                (b"\x01\x00", {"value": False, "default": True}),
                                (b"\x01\x07", {"value": False, "default": None}),
                                (b"\x00", {"value": True, "default": None})):
            dev.answers[device.FN_INVERSION_DEFAULT, 0] = reply
            self.assertEqual(FnCodec.read(dev, None), expected)
        self.assertEqual(dev.calls[-1], (device.FN_INVERSION_DEFAULT, 0, b""))
        dev = Stub({device.FN_INVERSION})
        for reply, value in ((b"\x00\x05", True), (b"\x01", False), (b"\x01\x00", False), (b"\x00\x01", True)):
            dev.answers[device.FN_INVERSION, 0] = reply
            self.assertEqual(FnCodec.read(dev, None), {"value": value, "default": None})
        for reply in (b"\x11", b"\x10", b"\x02", b""):
            with self.subTest(reply=reply.hex()), self.assertRaises(kbsettings.BadReply):
                dev.answers[device.FN_INVERSION, 0] = reply
                FnCodec.read(dev, None)

    def test_setters_and_the_channel_they_name(self):
        for features, host, value, sent in (({HOSTS, device.HOSTS_INFO}, 2, True, (HOSTS, 1, b"\x02\x00")),
                                            ({HOSTS, device.HOSTS_INFO}, 0, False, (HOSTS, 1, b"\x00\x01")),
                                            ({device.FN_INVERSION_DEFAULT}, None, True, (device.FN_INVERSION_DEFAULT, 1, b"\x00")),
                                            ({device.FN_INVERSION}, None, False, (device.FN_INVERSION, 1, b"\x01"))):
            dev = Stub(features | {device.FN_INVERSION})  # the first feature listed in FnCodec.features is used
            FnCodec.write(dev, host, value)
            self.assertEqual(dev.calls, [sent])
        dev = Stub({device.HOSTS_INFO}, {(device.HOSTS_INFO, 0): b"\x01\x00\x03\x02\xee"})
        self.assertEqual(kbsettings.current_host(dev, 2), 2)
        self.assertEqual(dev.calls, [(device.HOSTS_INFO, 0, b"")])

    def test_a_channel_that_is_not_certain_is_unsupported(self):
        for features, reply, current in (({HOSTS}, b"\x00\x00\x03\x01", 1),                      # no host information
                                         ({device.HOSTS_INFO}, b"\x00\x00\x03\x03", 3),          # past the channels
                                         ({device.HOSTS_INFO}, b"\x00\x00\x03\x01", 0),          # not CHANGE_HOST's
                                         ({device.HOSTS_INFO}, b"\x00\x00\x03", 0)):             # too short
            with self.subTest(reply=reply.hex(), current=current), self.assertRaises(kbsettings.Unsupported):
                kbsettings.current_host(Stub(features, {(device.HOSTS_INFO, 0): reply}), current)
        self.assertFalse(FnCodec.supported(Stub({HOSTS})))
        self.assertTrue(FnCodec.supported(Stub({HOSTS, device.HOSTS_INFO})))
        self.assertTrue(FnCodec.supported(Stub({device.FN_INVERSION})))
        self.assertFalse(FnCodec.supported(Stub(set())))

    def test_only_a_bool_is_a_value(self):
        self.assertTrue(FnCodec.valid(True) and FnCodec.valid(False))
        for value in (1, 0, "true", None, [], 1.0):
            self.assertFalse(FnCodec.valid(value), value)


class Levels:
    """A made-up codec with four values on a made-up feature, for what a two-valued setting cannot show: a keyboard
    that takes a third value. Not a real feature or layout."""

    name = "level"
    features = (0x7F01,)

    @staticmethod
    def supported(dev):
        return dev.has(0x7F01)

    @staticmethod
    def needs_host(dev):
        return False

    @staticmethod
    def valid(value):
        return type(value) is int and 0 <= value <= 3

    @staticmethod
    def read(dev, host):
        state = dev.call(0x7F01, 0)[0]
        if state > 3:
            raise kbsettings.BadReply("out of range")
        return {"value": state, "default": None}

    @staticmethod
    def write(dev, host, value):
        dev.call(0x7F01, 1, value)


class Base(unittest.TestCase):
    """A rig with an Fn keyboard on slot 1 and a mouse on slot 2, found and read."""

    def make(self, kb=None, keys=None, **rig):
        self.dev = kb or fn_keyboard()
        self.rig = Rig(self, {1: self.dev, 2: mouse_device()}, keys=keys, **rig).start()
        return self.rig

    def hold(self, when):
        """``keyboard._call`` that holds the first request ``when`` picks back until the Event returned is set."""
        entered, release, real = threading.Event(), threading.Event(), keyboard._call

        def call(dev, request):
            if when(request) and not entered.is_set():
                entered.set()
                self.assertTrue(release.wait(5.0))
            return real(dev, request)
        self.addCleanup(release.set)
        patcher = mock.patch.object(keyboard, "_call", call)
        patcher.start()
        self.addCleanup(patcher.stop)
        return entered, release

    def reached(self, entered, timeout=3.0):
        """Run what the engine's thread would until the held request is reached."""
        end = time.monotonic() + timeout
        while not entered.is_set() and time.monotonic() < end:
            try:
                self.rig.engine._handle(self.rig.engine._queue.get(timeout=0.02))
            except queue.Empty:
                pass
        return entered.is_set()

    def drain(self):
        time.sleep(0.4)
        while not self.rig.engine._queue.empty():
            self.rig.engine._handle(self.rig.engine._queue.get())

    def set(self, value, settle=True):
        self.rig.kb.set_setting("fn", value)
        if settle:
            self.rig.settle()


class Transaction(Base):
    def test_read_once_found_then_changed_and_read_back(self):
        rig = self.make()
        self.assertEqual(fn(rig), {"value": False, "default": False, "writable": True, "busy": False, "outcome": None})
        port, dev = rig.port, self.dev
        reads = len(packets(port, dev, HOSTS, 0))
        self.assertEqual(reads, 1)
        self.set(True)
        self.assertEqual(dev.setting_writes, [(HOSTS, b"\x00\x00")])
        self.assertEqual(dev.settings[HOSTS][0], [0, 1])
        self.assertEqual(fn(rig), {"value": True, "default": False, "writable": True, "busy": False, "outcome": None})
        self.assertEqual(len(packets(port, dev, HOSTS, 0)), reads + 2)  # fresh read before, read back after
        setter = packets(port, dev, HOSTS, 1)
        self.assertEqual(len(setter), 1)
        self.assertEqual(setter[0][4:6], b"\x00\x00")       # channel 0 (HostsInfo byte 3), state 0
        self.assertEqual(set(setter[0][6:]), {0})          # nothing else in it
        self.assertEqual(dev.settings[HOSTS][1], [0, 1])   # the other channels untouched
        self.assertEqual(dev.settings[HOSTS][2], [1, 1])

    def test_the_channel_is_the_one_the_keyboard_is_on(self):
        rig = self.make(fn_keyboard(current=2))
        self.set(True)
        self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x02\x00")])
        self.assertEqual(packets(rig.port, self.dev, HOSTS, 0)[-1][4], 2)

    def test_the_features_without_channels_are_changed_too(self):
        for feature, state in ((device.FN_INVERSION_DEFAULT, [1, 1]), (device.FN_INVERSION, [1])):
            with self.subTest(feature=hex(feature)):
                rig = self.make(keyboard_device(settings={feature: state}))
                self.assertEqual(fn(rig)["value"], False)
                self.set(True)
                self.assertEqual(self.dev.setting_writes, [(feature, b"\x00")])
                self.assertEqual(fn(rig)["value"], True)
                self.assertEqual(fn(rig)["outcome"], None)
                rig.stop()

    def test_a_channel_that_is_not_certain_is_never_written(self):
        cases = {
            "no host information": keyboard_device(settings={HOSTS: {0: [1, 1]}}),
            "past the channels": keyboard_device(hosts=(3, 0), settings={device.HOSTS_INFO: 3, HOSTS: {3: [1, 1]}}),
            "another channel": keyboard_device(hosts=(3, 0), settings={device.HOSTS_INFO: 1, HOSTS: {0: [1, 1], 1: [1, 1]}}),
        }
        for name, kb in cases.items():
            with self.subTest(name):
                rig = self.make(kb)
                control = fn(rig)
                self.assertTrue(control is None or (control["writable"] is False and control["value"] is None))
                sent = len(rig.port.written)
                for value in (True, False):
                    self.set(value)
                self.assertEqual(rig.port.written[sent:], [])  # not even asked
                self.assertEqual(kb.setting_writes, [])
                self.assertEqual(packets(rig.port, kb, HOSTS, 1), [])
                self.assertEqual([p[4] for p in packets(rig.port, kb, HOSTS, 0)], [])  # not even read
                rig.stop()

    def test_a_channel_no_longer_certain_at_the_change_sends_nothing(self):
        rig = self.make()
        self.dev.settings[device.HOSTS_INFO] = 1  # says another channel than CHANGE_HOST did
        self.set(True)
        self.assertEqual(packets(rig.port, self.dev, HOSTS, 1), [])
        self.assertEqual(fn(rig), {"value": None, "default": None, "writable": False, "busy": False,
                                   "outcome": "unsupported"})
        self.assertEqual(rig.kb._pending, {})

    def test_already_so_nothing_is_sent(self):
        rig = self.make()
        self.set(False)
        self.assertEqual(self.dev.setting_writes, [])
        self.assertIsNone(fn(rig)["outcome"])

    def test_a_keyboard_that_ignores_the_change(self):
        rig = self.make()
        self.dev.ignore_writes = True
        self.set(True)
        self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])  # once, nothing sent back
        self.assertEqual(fn(rig)["outcome"], "not-applied")
        self.assertEqual(fn(rig)["value"], False)

    def test_a_setter_refused_is_read_back(self):
        rig = self.make()
        self.dev.failing[(HOSTS, 1)] = 5
        self.set(True)
        self.assertEqual(len(packets(rig.port, self.dev, HOSTS, 1)), 1)
        self.assertEqual(fn(rig)["outcome"], "not-applied")
        self.assertEqual(fn(rig)["value"], False)

    def test_a_setter_without_an_answer(self):
        class Mute(FakeDevice):
            """Takes the change but does not answer it; then, if ``then_silent``, answers nothing at all."""
            then_silent = False

            def answer(self, feat_idx, function, params):
                reply = super().answer(feat_idx, function, params)
                if feat_idx < len(self.features) and self.features[feat_idx] == HOSTS and function == 1:
                    self.silent = self.then_silent
                    return None
                return reply

        for then_silent in (False, True):
            with self.subTest(then_silent=then_silent):
                kb = Mute(controls=(), hosts=(3, 0), settings={device.HOSTS_INFO: 0, HOSTS: {0: [1, 1]}})
                kb.then_silent = then_silent
                rig = self.make(kb)
                start = time.monotonic()
                self.set(True)
                self.assertEqual(len(packets(rig.port, kb, HOSTS, 1)), 1)  # never a second, blind one
                if not then_silent:
                    self.assertEqual(fn(rig), dict(fn(rig), value=True, outcome=None))
                    rig.stop()
                    continue
                self.assertEqual(fn(rig)["outcome"], "unconfirmed")
                self.assertIsNone(fn(rig)["value"])
                self.assertFalse(fn(rig)["writable"])
                at = rig.kb._settings_at
                self.assertGreaterEqual(at, start + keyboard.RECOVERY_RETRY)
                self.assertLessEqual(at, time.monotonic() + keyboard.RECOVERY_RETRY)
                kb.silent = False
                self.assertGreaterEqual(rig.kb.due(time.monotonic()), keyboard.RECOVERY_RETRY - 5.0)  # not before
                rig.kb._settings_at = time.monotonic()  # that time come
                rig.kb.tick(time.monotonic())
                rig.settle()
                self.assertEqual(fn(rig)["value"], True)
                self.assertTrue(fn(rig)["writable"])
                self.assertEqual(len(kb.setting_writes), 1)
                rig.stop()

    def test_a_keyboard_that_takes_another_value_gets_the_earlier_one_back(self):
        for taken, outcome, last in (([3], "restored", 1), ([3, 3], "unconfirmed", 3)):
            with self.subTest(outcome=outcome), mock.patch.dict(kbsettings.CODECS, level=Levels):
                kb = keyboard_device(settings={0x7F01: [1]})
                kb.taken[0x7F01] = list(taken)
                rig = self.make(kb)
                self.assertEqual(rig.state()["controls"]["level"]["value"], 1)
                rig.kb.set_setting("level", 2)
                rig.settle()
                self.assertEqual(kb.setting_writes, [(0x7F01, b"\x02"), (0x7F01, b"\x01")])  # the earlier value back
                self.assertEqual(rig.state()["controls"]["level"]["outcome"], outcome)
                self.assertEqual(rig.state()["controls"]["level"]["value"], last)
                rig.stop()

    def test_a_read_only_field_that_changes_does_not_count(self):
        rig = self.make()
        real = self.dev._setting

        def setting(feature, function, params):  # the setter also changes the default the keyboard tells
            reply = real(feature, function, params)
            if feature == HOSTS and function == 1:
                self.dev.settings[HOSTS][0][1] = 0
            return reply
        self.dev._setting = setting
        self.set(True)
        self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])  # no restore
        self.assertEqual(fn(rig), dict(fn(rig), value=True, default=True, outcome=None))

    def test_an_answer_that_does_not_fit_is_unknown_and_never_written(self):
        for reply in (b"\x01\x00\x00", b"\x00\x02\x00", b"\x00\xff\x01"):  # another channel, no state
            with self.subTest(reply=reply.hex()):
                kb = fn_keyboard()
                kb.replies[(HOSTS, 0)] = reply
                rig = self.make(kb)
                self.assertEqual(fn(rig), {"value": None, "default": None, "writable": False, "busy": False,
                                           "outcome": None})
                sent = len(rig.port.written)
                self.set(True)
                self.assertEqual(kb.setting_writes, [])
                self.assertEqual(rig.port.written[sent:], [])  # not even asked
                rig.stop()

    def test_an_answer_gone_bad_before_the_change_sends_nothing(self):
        rig = self.make()
        self.dev.replies[(HOSTS, 0)] = b"\x00\x05\x00"
        self.set(True)
        self.assertEqual(self.dev.setting_writes, [])
        self.assertEqual(fn(rig)["outcome"], "unknown")
        self.assertIsNone(fn(rig)["value"])
        self.assertFalse(fn(rig)["busy"])

    def test_a_keyboard_without_the_feature_has_no_control(self):
        rig = self.make(keyboard_device())
        self.assertEqual(rig.state()["controls"], {})
        self.set(True)
        rig.kb.set_setting("fn", True)
        self.assertEqual(self.dev.setting_writes, [])
        self.assertNotIn("fn", rig.state()["settings"])

    def test_what_is_not_a_change_of_a_setting_it_has_is_dropped(self):
        rig = self.make()
        sent = len(rig.port.written)
        for name, value in (("fn", 1), ("fn", 0), ("fn", "true"), ("fn", None), ("fn", [True]), ("other", True),
                            (["fn"], True), (None, True), (7, True), ({"fn": 1}, True), (b"fn", True)):
            with self.subTest(name=name, value=value):
                rig.engine.set_keyboard_setting(name, value)
                rig.engine._handle(rig.engine._queue.get(timeout=1.0))
                self.assertIsNone(rig.kb._task)
                self.assertEqual(rig.kb._pending, {})
        self.assertEqual(rig.port.written[sent:], [])
        rig.link(1, False)
        rig.engine.set_keyboard_setting("fn", True)
        rig.engine._handle(rig.engine._queue.get(timeout=1.0))
        self.assertEqual(rig.kb._pending, {})
        self.assertEqual(self.dev.setting_writes, [])
        rig.link(1, True)  # and the engine's way in works once it is back
        rig.settle()
        rig.engine.set_keyboard_setting("fn", True)
        rig.engine._handle(rig.engine._queue.get(timeout=1.0))
        rig.settle()
        self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])


class Order(Base):
    def test_keys_called_for_before_the_setter_goes_first_and_the_change_follows(self):
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is kbsettings.current_host)
        self.set(True, settle=False)
        self.assertTrue(self.reached(entered))
        self.assertTrue(fn(rig)["busy"])
        new = copy.deepcopy(rig.engine.cfg)
        new["keyboard"]["keys"] = {"0x0f01": COPY}
        rig.engine._apply_config(new)
        self.assertEqual(rig.kb._task.__name__, "_configure")  # the write was called off
        self.assertEqual(rig.kb._pending, {"fn": True})  # and its value kept
        release.set()
        rig.settle()
        self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])
        self.assertTrue(rig.key(0x0F01)["active"])
        def at(feature, function):
            index = self.dev.index(feature)
            return next(i for i, p in enumerate(rig.port.written) if p[1] == 1 and p[2] == index and p[3] >> 4 == function)
        divert, setter = at(device.REPROG_CONTROLS_V4, 3), at(HOSTS, 1)
        self.assertLess(divert, setter)

    def test_once_the_setter_is_on_its_way_easy_switch_waits_for_the_read_back(self):
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is FnCodec.write)
        self.set(True, settle=False)
        self.assertTrue(self.reached(entered))
        task = rig.kb._task
        rig.kb.device_action({"type": "host", "host": 1}, "press")
        self.assertIs(rig.kb._task, task)  # not called off
        self.assertEqual(rig.kb._wish, ("host", 1))
        release.set()
        rig.settle()
        self.assertEqual(self.dev.switched, [1])
        self.assertEqual(fn(rig)["value"], True)
        self.assertIsNone(fn(rig)["outcome"])
        def at(feature, function, after=-1):  # where in the order of everything sent, after ``after``
            index = self.dev.index(feature)
            return next(i for i, p in enumerate(rig.port.written)
                        if i > after and p[1] == 1 and p[2] == index and p[3] >> 4 == function)
        setter = at(HOSTS, 1)
        readback = at(HOSTS, 0, setter)
        switch = at(device.CHANGE_HOST, 1)
        self.assertLess(setter, readback)
        self.assertLess(readback, switch)

    def test_a_read_is_called_off_by_the_keys_and_done_again_after(self):
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is FnCodec.read)
        rig.kb._settings_at = 0.0
        rig.kb.tick(time.monotonic())
        self.assertTrue(self.reached(entered))
        rig.kb.device_action({"type": "host", "host": 1}, "press")
        self.assertEqual(rig.kb._task.__name__, "_switch")
        self.assertEqual(rig.kb._settings_at, 0.0)  # read again after
        release.set()
        rig.settle()
        self.assertEqual(self.dev.switched, [1])

    def test_a_change_waits_while_a_key_it_follows_is_down(self):
        rig = self.make(keys={"0x0f01": COPY})
        rig.keys_down(1, self.dev, 0x0F01)
        self.set(True)
        self.assertEqual(self.dev.setting_writes, [])
        self.assertTrue(fn(rig)["busy"])
        self.assertEqual(rig.kb.due(time.monotonic()), float("inf"))  # nothing to wake up for: the key-up starts it
        rig.keys_down(1, self.dev)
        rig.settle()
        self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])
        self.assertFalse(fn(rig)["busy"])

    def test_a_read_due_while_a_change_waits_for_a_key_is_made_and_nothing_sent(self):
        rig = self.make(keys={"0x0f01": COPY})
        rig.keys_down(1, self.dev, 0x0F01)
        self.set(True)
        reads = len(packets(rig.port, self.dev, HOSTS, 0))
        self.dev.settings[HOSTS][0][0] = 0
        rig.note(1, self.dev, HOSTS, b"")
        rig.settle()
        self.assertEqual(len(packets(rig.port, self.dev, HOSTS, 0)), reads + 1)
        self.assertEqual(packets(rig.port, self.dev, HOSTS, 1), [])
        self.assertEqual(fn(rig)["value"], True)
        self.assertEqual(rig.kb._pending, {"fn": True})
        rig.keys_down(1, self.dev)
        rig.settle()
        self.assertEqual(packets(rig.port, self.dev, HOSTS, 1), [])  # already so: nothing to send
        self.assertEqual(rig.kb._pending, {})

    def test_a_key_down_after_the_read_holds_the_setter_back(self):
        rig = self.make(keys={"0x0f01": COPY})
        entered, release = self.hold(lambda request: request[0] is FnCodec.read)
        self.set(True, settle=False)
        self.assertTrue(self.reached(entered))
        rig.keys_down(1, self.dev, 0x0F01)
        release.set()
        rig.settle()
        self.assertEqual(self.dev.setting_writes, [])
        self.assertEqual(rig.kb._pending, {"fn": True})
        rig.keys_down(1, self.dev)
        rig.settle()
        self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])


class Going(Base):
    def test_gone_before_the_setter_it_never_goes(self):
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is FnCodec.read)
        self.set(True, settle=False)
        self.assertTrue(self.reached(entered))
        rig.link(1, False)
        release.set()
        self.drain()
        self.assertEqual(self.dev.setting_writes, [])
        self.assertEqual(rig.state()["controls"], {})
        rig.link(1, True)
        rig.settle()
        self.assertEqual(self.dev.setting_writes, [])  # not asked for again
        self.assertEqual(fn(rig)["value"], False)

    def test_gone_after_the_setter_nothing_is_sent_back(self):
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is FnCodec.read and self.dev.setting_writes)
        self.set(True, settle=False)
        self.assertTrue(self.reached(entered))
        rig.link(1, False)
        release.set()
        self.drain()
        self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])
        self.assertEqual(rig.state()["controls"], {})
        self.assertFalse(rig.state()["connected"])
        rig.link(1, True)
        rig.settle()
        self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])
        self.assertEqual(fn(rig), dict(fn(rig), value=True, outcome=None))  # read afresh: what the keyboard has

    def test_another_unit_never_gets_what_was_asked_of_the_first(self):
        rig = self.make(keys={"0x0f01": COPY})
        rig.keys_down(1, self.dev, 0x0F01)
        self.set(True)
        self.assertEqual(rig.kb._pending, {"fn": True})
        rig.link(1, False)
        unit_b = rig.port.devices[1] = fn_keyboard(UNIT_B)
        rig.link(1, True)
        rig.settle()
        rig.keys_down(1, unit_b)
        rig.settle()
        self.assertEqual(unit_b.setting_writes, [])
        self.assertEqual(self.dev.setting_writes, [])
        self.assertEqual(fn(rig)["value"], False)

    def test_on_exit_a_change_asked_for_never_goes(self):
        rig = self.make(keys={"0x0f01": COPY})
        rig.keys_down(1, self.dev, 0x0F01)
        self.set(True)
        start = time.monotonic()
        rig.kb.shutdown()
        self.assertLess(time.monotonic() - start, keyboard.SHUTDOWN_BUDGET)
        self.assertEqual(self.dev.setting_writes, [])

    def test_on_exit_during_the_read_the_setter_never_goes(self):
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is FnCodec.read)
        self.set(True, settle=False)
        self.assertTrue(self.reached(entered))
        exiting = threading.Thread(target=rig.kb.shutdown)
        exiting.start()
        self.assertTrue(rig.kb._called_off.wait(2.0))
        release.set()
        exiting.join(5.0)
        time.sleep(0.2)
        self.assertEqual(self.dev.setting_writes, [])
        self.assertEqual(packets(rig.port, self.dev, HOSTS, 1), [])


class Notified(Base):
    def test_a_report_of_a_change_on_the_keyboard_is_read_not_believed(self):
        rig = self.make()
        reads = len(packets(rig.port, self.dev, HOSTS, 0))
        self.dev.settings[HOSTS][0][0] = 0  # its own Fn lock shortcut
        rig.note(1, self.dev, HOSTS, b"\x00\x01")  # says state 1: not taken from the report
        rig.settle()
        self.assertEqual(len(packets(rig.port, self.dev, HOSTS, 0)), reads + 1)
        self.assertEqual(fn(rig)["value"], True)
        self.assertEqual(self.dev.setting_writes, [])

    def test_a_burst_of_reports_is_one_read_more(self):
        rig = self.make()
        reads = len(packets(rig.port, self.dev, HOSTS, 0))
        entered, release = self.hold(lambda request: request[0] is FnCodec.read)
        for _ in range(5):
            rig.port.notify(bytes([0x11, 1, self.dev.index(HOSTS), 0x00]) + bytes(16))
        handled, end = 0, time.monotonic() + 3.0
        while (handled < 5 or not entered.is_set()) and time.monotonic() < end:
            try:
                item = rig.engine._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            handled += item[0] == "note"
            rig.engine._handle(item)
        self.assertTrue(entered.is_set())
        self.assertEqual(handled, 5)
        release.set()
        rig.settle()
        self.assertEqual(len(packets(rig.port, self.dev, HOSTS, 0)), reads + 2)


class LastWins(Base):
    def last_wins(self, last):
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is FnCodec.read)
        self.set(True, settle=False)
        self.assertTrue(self.reached(entered))
        for value in (False, True, False, last):
            rig.kb.set_setting("fn", value)
        self.assertEqual(rig.kb._pending, {"fn": last})
        release.set()
        rig.settle()
        self.assertEqual(fn(rig)["value"], last)
        return self.dev.setting_writes

    def test_before_the_setter_only_the_last_value_goes(self):
        self.assertEqual(self.last_wins(True), [(HOSTS, b"\x00\x00")])

    def test_before_the_setter_the_last_value_back_to_what_it_was_sends_nothing(self):
        self.assertEqual(self.last_wins(False), [])

    def test_after_the_setter_the_next_value_is_a_change_of_its_own(self):
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is FnCodec.write)
        self.set(True, settle=False)
        self.assertTrue(self.reached(entered))
        rig.kb.set_setting("fn", False)  # too late to call the first back
        rig.kb.set_setting("fn", True)
        rig.kb.set_setting("fn", False)
        self.assertEqual(rig.kb._pending, {"fn": False})
        release.set()
        rig.settle()
        self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00"), (HOSTS, b"\x00\x01")])
        self.assertEqual(fn(rig)["value"], False)
        self.assertIsNone(fn(rig)["outcome"])


class Guarded(Base):
    """No setting is changed where no key may be (keyboard.KeyboardSide._blocked): other software for the device
    running, a keyboard that does not say which one it is, a journal that cannot be read. It is still read."""

    def rivals(self, *names):
        """Other software is seen to start or stop, as the engine looks for it."""
        self.rig.rivals = list(names)
        self.rig.engine._look_for_rivals(time.monotonic())

    def refused(self, rig, reason):
        self.assertEqual(rig.state()["reason"], reason)
        self.assertEqual(fn(rig), dict(fn(rig), value=False, writable=False, busy=False))  # read all the same
        sent = len(rig.port.written)
        for value in (True, False, True):
            rig.engine.set_keyboard_setting("fn", value)
            rig.engine._handle(rig.engine._queue.get(timeout=1.0))
        rig.settle()
        self.assertEqual(rig.kb._pending, {})
        self.assertEqual(rig.port.written[sent:], [])  # not even asked
        self.assertEqual(self.dev.setting_writes, [])

    def test_never_on_a_keyboard_that_does_not_say_which_one_it_is(self):
        rig = self.make(fn_keyboard(info=False))
        self.assertEqual(len(packets(rig.port, self.dev, HOSTS, 0)), 1)
        self.refused(rig, "identity")

    def test_never_while_other_software_runs_and_again_once_it_has_stopped(self):
        rig = self.make(rivals=["Solaar"])
        self.refused(rig, "rivals")
        self.rivals()
        self.assertIsNone(rig.state()["reason"])
        self.assertTrue(fn(rig)["writable"])  # without the keyboard having to be found again
        rig.settle()
        self.assertEqual(self.dev.setting_writes, [])  # nothing asked for while it ran is sent now
        self.set(True)
        self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])
        self.assertEqual(fn(rig), dict(fn(rig), value=True, outcome=None))

    def test_never_while_the_journal_cannot_be_read_and_again_once_it_can(self):
        rig = self.make(journal="not a journal")
        self.refused(rig, "journal")
        os.remove(rig.journal)
        rig.kb._next_recovery = 0.0  # its time come: the keys are tried again
        rig.kb.tick(time.monotonic())
        rig.settle()
        self.assertIsNone(rig.state()["reason"])
        self.assertTrue(fn(rig)["writable"])
        self.set(True)
        self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])

    def test_other_software_seen_during_the_read_before_the_change_sends_nothing(self):
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is FnCodec.read)
        self.set(True, settle=False)
        self.assertTrue(self.reached(entered))
        self.rivals("Solaar")
        self.assertEqual(rig.kb._pending, {})
        release.set()
        rig.settle()
        self.assertEqual(self.dev.setting_writes, [])
        self.assertEqual(packets(rig.port, self.dev, HOSTS, 1), [])
        self.assertEqual(fn(rig), dict(fn(rig), value=False, writable=False, busy=False, outcome=None))
        self.rivals()
        rig.settle()
        self.assertEqual(self.dev.setting_writes, [])  # not sent once it has stopped either

    def test_a_journal_gone_bad_during_the_read_before_the_change_sends_nothing(self):
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is FnCodec.read)
        self.set(True, settle=False)
        self.assertTrue(self.reached(entered))
        with open(rig.journal, "w") as f:
            f.write("not a journal")
        release.set()
        rig.settle()
        self.assertEqual(self.dev.setting_writes, [])
        self.assertEqual(rig.state()["reason"], "journal")
        self.assertEqual(fn(rig), dict(fn(rig), value=False, writable=False, busy=False, outcome=None))
        self.assertEqual(rig.kb._pending, {})

    def test_a_setter_waiting_behind_the_mouse_is_called_off_once_other_software_is_seen(self):
        rig = self.make()
        mouse = rig.port.devices[2]
        entered, release = self.hold(lambda request: request[0] is FnCodec.write)
        self.set(True, settle=False)
        self.assertTrue(self.reached(entered))  # on the keyboard's thread, not asked of the receiver yet
        mouse.delay = 0.5  # the mouse's own request holds the receiver meanwhile
        foreground = threading.Thread(target=rig.transport().request, args=(2, 0, 1, bytes([0, 0, 1])))
        foreground.start()
        time.sleep(0.05)
        release.set()
        time.sleep(0.1)
        self.assertTrue(rig.transport()._busy)
        self.assertIs(rig.kb._asked[0], FnCodec.write)  # waiting for its turn
        rig.kb.device_action({"type": "host", "host": 1}, "press")  # waits for the change to be over
        self.rivals("Solaar")
        foreground.join()
        rig.settle()
        self.assertEqual(self.dev.setting_writes, [])
        self.assertEqual(packets(rig.port, self.dev, HOSTS, 1), [])
        self.assertEqual(self.dev.switched, [1])  # what was wished for after it still goes
        self.assertEqual(fn(rig), dict(fn(rig), writable=False, busy=False, outcome="unconfirmed"))
        self.assertEqual(rig.kb._settings_at, 0.0)  # read again: whether it went is for the keyboard to say

    def taken(self, gone_bad):
        """The made-up four-value codec: the keyboard takes 3 for 2; ``gone_bad`` makes changes not allowed while
        the read back is on its way. Returns the rig, after."""
        kb = keyboard_device(settings={0x7F01: [1]})
        kb.taken[0x7F01] = [3]
        rig = self.make(kb)
        entered, release = self.hold(lambda request: request[0] is Levels.read and kb.setting_writes)
        rig.kb.set_setting("level", 2)
        self.assertTrue(self.reached(entered))
        gone_bad(rig)
        release.set()
        rig.settle()
        self.assertEqual(kb.setting_writes, [(0x7F01, b"\x02")])  # never the earlier value sent back
        return rig

    def test_other_software_seen_after_the_setter_nothing_is_sent_back(self):
        with mock.patch.dict(kbsettings.CODECS, level=Levels):
            rig = self.taken(lambda rig: self.rivals("Solaar"))
            self.assertEqual(rig.state()["controls"]["level"], dict(rig.state()["controls"]["level"], value=3,
                                                                    writable=False, busy=False, outcome="unconfirmed"))

    def test_a_journal_gone_bad_after_the_setter_nothing_is_sent_back(self):
        def gone_bad(rig):
            with open(rig.journal, "w") as f:
                f.write("not a journal")
        with mock.patch.dict(kbsettings.CODECS, level=Levels):
            rig = self.taken(gone_bad)
            self.assertEqual(rig.state()["controls"]["level"], dict(rig.state()["controls"]["level"], value=3,
                                                                    writable=False, busy=False, outcome="unconfirmed"))

    def test_a_change_waiting_for_a_key_is_dropped_once_other_software_is_seen(self):
        rig = self.make(keys={"0x0f01": COPY})
        rig.keys_down(1, self.dev, 0x0F01)
        self.set(True)
        self.assertEqual(rig.kb._pending, {"fn": True})
        self.rivals("Solaar")
        self.rivals()
        rig.keys_down(1, self.dev)
        rig.settle()
        self.assertEqual(self.dev.setting_writes, [])  # asked for before: never sent after
        self.assertTrue(fn(rig)["writable"])
        self.set(True)  # asked for afresh: sent
        self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])

    def test_other_software_seen_with_no_keyboard_or_no_change_under_way(self):
        rig = self.make()
        self.rivals("Solaar")
        self.assertIsNone(rig.kb._task)
        self.assertEqual(fn(rig), dict(fn(rig), value=False, writable=False))
        self.rivals()
        rig.link(1, False)
        self.rivals("Solaar")
        self.rivals()
        rig.link(1, True)
        rig.settle()
        self.assertTrue(fn(rig)["writable"])


class Mouse(Base):
    def test_the_mouse_is_heard_while_a_change_is_slow(self):
        rig = self.make()
        mouse = rig.port.devices[2]
        rig.engine.cfg["buttons"]["back"] = actions.keys("LEFTSHIFT")
        self.dev.delays = dict.fromkeys((HOSTS, device.HOSTS_INFO), 0.2)
        start = time.monotonic()
        self.set(True, settle=False)
        self.assertLess(time.monotonic() - start, 0.1)  # handed to the keyboard's thread
        for _ in range(3):
            time.sleep(0.05)
            self.assertIsNotNone(rig.kb._asked)  # the keyboard is still being waited for ...
            rig.mouse_actions.ran.clear()
            start = time.monotonic()
            rig.port.notify(bytes([0x11, 2, mouse.index(device.REPROG_CONTROLS_V4), 0]) + struct.pack("!HHHH", 0x0053, 0, 0, 0).ljust(16, b"\0"))
            while not rig.mouse_actions.ran:  # ... and the mouse is heard meanwhile
                rig.engine._handle(rig.engine._queue.get(timeout=1.0))
            self.assertLess(time.monotonic() - start, 0.1)
            self.assertEqual(rig.mouse_actions.ran, [actions.keys("LEFTSHIFT")])
            rig.port.notify(bytes([0x11, 2, mouse.index(device.REPROG_CONTROLS_V4), 0]) + bytes(16))
        rig.settle()
        self.assertEqual(fn(rig)["value"], True)
        writers = {w for p, w in zip(rig.port.written, rig.port.writers) if p in packets(rig.port, self.dev, HOSTS, 1)
                   or p in packets(rig.port, self.dev, HOSTS, 0)}
        self.assertEqual(writers, {"keyboard"})


class Kept(Base):
    def test_nothing_of_it_is_in_the_settings(self):
        with mock.patch.object(config, "save", side_effect=AssertionError("saved")), \
                mock.patch.object(config, "write_durable", side_effect=AssertionError("written")):
            rig = self.make()
            before, exported = copy.deepcopy(rig.engine.cfg), config.export_data(rig.engine.cfg)
            self.set(True)
            self.dev.ignore_writes = True
            self.set(False)
            rig.note(1, self.dev, HOSTS, b"")
            rig.settle()
            self.assertEqual(rig.engine.cfg, before)
            self.assertEqual(config.export_data(rig.engine.cfg), exported)

    def test_logs_and_state_name_the_setting_and_the_outcome_only(self):
        with self.assertLogs("mxoptions", logging.DEBUG) as logs:
            rig = self.make(fn_keyboard(current=2, states={2: [1, 1]}))
            self.set(True)
            self.dev.ignore_writes = True
            self.set(False)
            self.dev.replies[(HOSTS, 0)] = b"\x02\x07\x09"
            self.set(False)
            rig.kb.set_setting(["no"], True)
        text = "\n".join(logs.output)
        state = json.dumps(rig.state())
        for private in (identity(UNIT_A), UNIT_A.hex(), "5e015e02", "020709", "\\x07", "flow_link", rig.kb.link):
            self.assertNotIn(private, text)
            self.assertNotIn(private, state)
        for control in rig.state()["controls"].values():
            self.assertEqual(set(control), {"value", "default", "writable", "busy", "outcome"})
        self.assertEqual(rig.state()["controls"]["fn"]["outcome"], "unknown")
        self.assertIn("keyboard setting fn: the keyboard did not take the change", text)


class Page(unittest.TestCase):
    """The Fn lock row of the Keyboard page (Qt offscreen)."""

    def setUp(self):
        from PySide6.QtWidgets import QApplication
        from mxoptions.ui import keyboard_page
        from mxoptions.ui.store import Store
        self.app = QApplication.instance() or QApplication([])
        self.pages = keyboard_page
        calls = self.calls = []

        class Engine:
            def set_keyboard_setting(self, name, value):
                calls.append((name, value))

            def __getattr__(self, name):
                return lambda *args: None
        patcher = mock.patch.object(keyboard_page.firmware, "available", lambda: False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = Store(copy.deepcopy(config.DEFAULTS), Engine())
        self.store.flush = lambda: None
        self.store._save_timer.timeout.disconnect()
        self.page = keyboard_page.KeyboardPage(self.store)
        self.addCleanup(self.page.deleteLater)
        self.page.resize(900, 700)
        self.page.show()
        self.switch, self.row = self.page.fn_switch, self.page.settings_rows["fn"]

    def show(self, control=None, connected=True, **extra):
        found = {"connected": connected, "known": True, "name": "Synthetic keyboard", "battery": None, "current_host": 0,
                 "hosts": [], "keys": [], "reason": None, "failed": None, "firmware": None, "settings": ["fn"],
                 "controls": {"fn": dict({"value": True, "default": None, "writable": True, "busy": False,
                                          "outcome": None}, **(control or {}))} if control is not None else {}}
        found.update(extra)
        self.store.update_state(dict(self.store.state, keyboard=found))

    def test_a_switch_only_while_the_keyboard_lets_it_be_changed(self):
        self.show()
        self.assertTrue(self.switch.isHidden())
        self.assertIn("MX Options does not", self.row.note.text())
        self.assertEqual(self.row.info.text(), "")
        self.show({})
        self.assertFalse(self.switch.isHidden())
        self.assertTrue(self.switch.isChecked() and self.switch.isEnabled())
        self.assertEqual(self.switch.accessibleName(), "Use F1–F12 without Fn")
        self.assertEqual(self.row.info.text(), self.pages.FN_HINT)
        self.assertTrue(self.row.note.isHidden())
        self.show({}, connected=False)
        self.assertTrue(self.switch.isHidden())
        self.assertEqual(self.calls, [])

    def test_disabled_while_unknown_not_writable_or_busy(self):
        for control, note in (({"value": None, "writable": False}, "The keyboard did not say how this is set."),
                              ({"writable": False}, ""), ({"busy": True}, "")):
            with self.subTest(control=control):
                self.show(control)
                self.assertFalse(self.switch.isEnabled())
                self.assertEqual(self.row.note.text(), note)
        self.assertEqual(self.calls, [])

    def test_a_toggle_asks_the_engine_once_and_the_switch_shows_the_keyboard(self):
        self.show({"value": False})
        self.assertFalse(self.switch.isChecked())
        self.switch.click()
        self.assertEqual(self.calls, [("fn", True)])
        self.show({"value": False, "busy": True})
        self.assertTrue(self.switch.isChecked())  # what was asked for, until the keyboard answers
        self.show({"value": False, "outcome": "not-applied"})
        self.assertFalse(self.switch.isChecked())
        self.assertEqual(self.row.note.text(), "The keyboard did not take the change.")
        self.show({"value": True})
        self.assertTrue(self.switch.isChecked())
        self.assertEqual(self.calls, [("fn", True)])  # following the keyboard asks nothing

    def test_every_outcome_has_its_note(self):
        for outcome, text in self.pages.OUTCOMES.items():
            with self.subTest(outcome):
                self.show({"outcome": outcome})
                self.assertEqual(self.row.note.text(), text)
                self.assertFalse(self.row.note.isHidden())

    def test_the_new_words_are_translated(self):
        from mxoptions import i18n_uk
        for text in (*self.pages.OUTCOMES.values(), self.pages.FN_HINT, "Use F1–F12 without Fn",
                     "Settings the keyboard controls itself. Changes are made on the keyboard. Its own shortcuts may "
                     "change them too."):
            self.assertIn(text, i18n_uk.STRINGS)



# -- keys turned off (0x4521) and the operating system layout (0x4531, 0x4530 shown) --------------------------------

DisableCodec, PlatformCodec = kbsettings.DisableCodec, kbsettings.PlatformCodec
DK, MP, DP = device.DISABLE_KEYS, device.MULTI_PLATFORM, device.DUAL_PLATFORM
ALL_KEYS = ["caps_lock", "num_lock", "scroll_lock", "insert", "super"]


def info(flags=0x02, platforms=3, count=4, hosts=3, slot=0, current=1):
    """MULTI_PLATFORM function 0's answer."""
    return bytes([flags, 0, platforms, count, hosts, slot, current])


def descriptor(platform, index, mask):
    """MULTI_PLATFORM function 1's answer: platform, its own index, operating systems (big-endian), versions."""
    return bytes([platform, index]) + mask.to_bytes(2, "big") + b"\x01\x02\x03\x04"


# platform 0: Windows (0x0100) + Android (0x1000) in two descriptors; 1: macOS + iOS; 2: Linux
DESCRIPTORS = [descriptor(0, 0, 0x0100), descriptor(0, 1, 0x1000), descriptor(1, 2, 0x6000), descriptor(2, 3, 0x0400)]
CHOICES = [{"index": 0, "os": ["windows", "android"]}, {"index": 1, "os": ["macos", "ios"]}, {"index": 2, "os": ["linux"]}]


def settings_keyboard(disable=(0x1F, 0x00), platform=None, dual=None, hosts_info=True, current=0, unit=UNIT_A, **kwargs):
    """A synthetic keyboard with keys it can turn off ((caps, current) bits) and, given, a multi-platform layout
    ({"info", "descriptors"}) or a dual one ([platform]), on channel ``current`` of three."""
    settings = {}
    if hosts_info:
        settings[device.HOSTS_INFO] = current
    if disable is not None:
        settings[DK] = {"caps": disable[0], "current": disable[1]}
    if platform is not None:
        settings[MP] = copy.deepcopy(platform)
    if dual is not None:
        settings[DP] = list(dual)
    return keyboard_device(unit, hosts=(3, current), settings=settings, **kwargs)


def multi(**kwargs):
    return {"info": info(**kwargs), "descriptors": list(DESCRIPTORS)}


def control(rig, name):
    return rig.state()["controls"].get(name)


class MuteAfter(FakeDevice):
    """Takes a setter of ``mute`` (feature, function) but answers nothing from then on."""
    mute = None

    def answer(self, feat_idx, function, params):
        reply = super().answer(feat_idx, function, params)
        if feat_idx < len(self.features) and (self.features[feat_idx], function) == self.mute:
            self.silent = True
            return None
        return reply


class DisableDecode(unittest.TestCase):
    def test_bits_read_and_shown_by_name(self):  # DK01
        dev = Stub({DK}, {(DK, 0): b"\x1f", (DK, 1): b"\x05\xee"})
        read = DisableCodec.read(dev, None)
        self.assertEqual(read, {"value": 0x05, "caps": 0x1F})
        self.assertEqual(dev.calls, [(DK, 0, b""), (DK, 1, b"")])
        self.assertEqual(DisableCodec.public(read), {"value": ["caps_lock", "scroll_lock"], "keys": ALL_KEYS})
        dev = Stub({DK}, {(DK, 0): b"\x1d", (DK, 1): b"\x00"})  # no Num Lock
        self.assertEqual(DisableCodec.public(DisableCodec.read(dev, None)),
                         {"value": [], "keys": ["caps_lock", "scroll_lock", "insert", "super"]})
        DisableCodec.write(dev, None, 0x83)
        self.assertEqual(dev.calls[-1], (DK, 2, b"\x83"))
        self.assertFalse(DisableCodec.needs_host(dev))
        self.assertTrue(DisableCodec.supported(dev))
        self.assertFalse(DisableCodec.supported(Stub({device.DISABLE_KEYS_BY_USAGE})))

    def test_bits_nobody_asked_about_go_back_as_read(self):  # DK02
        before = {"value": 0x81, "caps": 0x9F}
        self.assertEqual(DisableCodec.target(before, {"num_lock": True}), 0x83)
        self.assertEqual(DisableCodec.target(before, {"caps_lock": False}), 0x80)
        self.assertEqual(DisableCodec.target(before, {"caps_lock": False, "super": True, "insert": False}), 0x90)
        self.assertEqual(DisableCodec.public(before), {"value": ["caps_lock"], "keys": ALL_KEYS})  # 0x80 not named

    def test_what_does_not_fit_is_not_believed(self):  # DK03, DK13
        for answers in ({(DK, 0): b"\x0f", (DK, 1): b"\x10"},          # turned off but cannot be
                        {(DK, 0): b"", (DK, 1): b"\x00"},              # short
                        {(DK, 0): b"\x1f", (DK, 1): b""}):
            with self.subTest(answers=answers), self.assertRaises(kbsettings.BadReply):
                DisableCodec.read(Stub({DK}, answers), None)
        dev = Stub({DK}, {(DK, 0): b""})
        with self.assertRaises(kbsettings.BadReply):
            DisableCodec.read(dev, None)
        self.assertEqual(dev.calls, [(DK, 0, b"")])  # nothing more asked
        error = hidpp.HidppError("synthetic")
        with self.assertRaises(hidpp.HidppError):
            DisableCodec.read(Stub({DK}, {(DK, 0): b"\x1f", (DK, 1): error}), None)

    def test_a_key_it_cannot_turn_off_is_unsupported(self):  # DK04
        for before in ({"value": 0, "caps": 0x1D}, {"value": 0, "caps": 0}):
            with self.assertRaises(kbsettings.Unsupported):
                DisableCodec.target(before, {"num_lock": True})
        with self.assertRaises(kbsettings.Unsupported):
            DisableCodec.target({"value": 0, "caps": 0x1D}, {"caps_lock": True, "num_lock": False})

    def test_only_keys_by_name_and_on_or_off_are_a_value(self):  # DK10
        self.assertTrue(DisableCodec.valid({"caps_lock": True}))
        self.assertTrue(DisableCodec.valid(dict.fromkeys(ALL_KEYS, False)))
        for value in (0x01, 1, True, None, [], ["caps_lock"], frozenset({"caps_lock"}), {}, {"caps_lock": 1},
                      {"caps_lock": None}, {1: True}, {"Caps_Lock": True}, {"unknown": True}, {b"caps_lock": True},
                      dict(dict.fromkeys(ALL_KEYS, True), other=True), "caps_lock"):
            self.assertFalse(DisableCodec.valid(value), value)
        self.assertEqual(DisableCodec.merge(None, {"caps_lock": True}), {"caps_lock": True})
        self.assertEqual(DisableCodec.merge({"caps_lock": True, "insert": True}, {"caps_lock": False}),
                         {"caps_lock": False, "insert": True})


class PlatformDecode(unittest.TestCase):
    def stub(self, first=None, rows=DESCRIPTORS, features=(MP, device.HOSTS_INFO)):
        answers = {(MP, 0): first if first is not None else info()}
        dev = Stub(features, answers)
        real = dev.call

        def call(feature, function, *params):
            if feature == MP and function == 1:
                dev.calls.append((feature, function, bytes(params)))
                row = rows[params[0]] if params[0] < len(rows) else b""
                if isinstance(row, Exception):
                    raise row
                return row
            return real(feature, function, *params)
        dev.call = call
        return dev

    def test_layouts_by_platform_each_with_every_system_it_covers(self):  # OS01, OS02
        dev = self.stub(info(platforms=5, count=4))  # five platforms said, four descriptors: byte 3 counts
        read = PlatformCodec.read(dev, 0)
        self.assertEqual(read, {"value": 1, "choices": CHOICES, "writable": True})
        self.assertEqual(dev.calls, [(MP, 0, b"")] + [(MP, 1, bytes([i])) for i in range(4)])
        dev = self.stub(info(count=2, current=0), [descriptor(0, 0, 0x2000), descriptor(1, 1, 0x0020)])
        self.assertEqual(PlatformCodec.read(dev, 0)["choices"], [{"index": 0, "os": ["macos"]}, {"index": 1, "os": []}])
        every = sum(bit for _name, bit in kbsettings.OS_BITS)
        dev = self.stub(info(count=1, current=0), [descriptor(0, 0, every)])
        self.assertEqual(PlatformCodec.read(dev, 0)["choices"][0]["os"], [name for name, _bit in kbsettings.OS_BITS])
        self.assertEqual(PlatformCodec.public(read), {"value": 1, "choices": CHOICES})

    def test_read_only_where_the_keyboard_says_so_or_the_channel_is_not_known(self):  # OS06, OS04
        self.assertFalse(PlatformCodec.read(self.stub(info(flags=0x01)), 0)["writable"])
        read = PlatformCodec.read(self.stub(info(slot=2)), None)  # no channel asked: byte 5 not compared
        self.assertEqual(read, {"value": 1, "choices": CHOICES, "writable": False})
        self.assertFalse(PlatformCodec.needs_host(Stub({MP})))
        self.assertTrue(PlatformCodec.needs_host(Stub({MP, device.HOSTS_INFO})))
        self.assertFalse(PlatformCodec.needs_host(Stub({DP, device.HOSTS_INFO})))

    def test_what_does_not_fit_is_not_believed(self):  # OS03, OS04, OS10
        bad = {
            "short": (info()[:6], DESCRIPTORS),
            "another channel": (info(slot=1), DESCRIPTORS),
            "channel past the count": (info(slot=3, hosts=3), DESCRIPTORS),
            "current platform undefined": (info(current=0xFF), DESCRIPTORS),
            "current platform without a descriptor": (info(current=2, count=3), DESCRIPTORS),
            "platform past the count": (info(platforms=2), DESCRIPTORS),
            "descriptor index not echoed": (info(), [descriptor(0, 1, 0x0100)] + DESCRIPTORS[1:]),
            "descriptor for no system": (info(), [descriptor(0, 0, 0)] + DESCRIPTORS[1:]),
            "descriptor short": (info(), [DESCRIPTORS[0][:7]] + DESCRIPTORS[1:]),
            "descriptor missing": (info(count=5), DESCRIPTORS),
        }
        for name, (first, rows) in bad.items():
            with self.subTest(name), self.assertRaises(kbsettings.BadReply):
                PlatformCodec.read(self.stub(first, rows), 0)
        dev = self.stub(info(), DESCRIPTORS[:2] + [hidpp.HidppError("synthetic")] + DESCRIPTORS[3:])
        with self.assertRaises(hidpp.HidppError):  # mid-list: no partial list
            PlatformCodec.read(dev, 0)
        for count in (0, 17, 0xFF):
            with self.subTest(count=count):
                dev = self.stub(info(count=count))
                with self.assertRaises(kbsettings.Unsupported):
                    PlatformCodec.read(dev, 0)
                self.assertEqual(dev.calls, [(MP, 0, b"")])  # no descriptor asked for
        rows = [descriptor(i % 3, i, 0x0100) for i in range(16)]
        self.assertEqual(len(PlatformCodec.read(self.stub(info(count=16), rows), 0)["choices"]), 3)

    def test_the_setter_names_the_channel_and_only_a_platform_it_has(self):  # OS07, OS08
        dev = Stub({MP, device.HOSTS_INFO})
        PlatformCodec.write(dev, 2, 1)
        self.assertEqual(dev.calls, [(MP, 3, b"\x02\x01")])
        with self.assertRaises(kbsettings.Unsupported):
            PlatformCodec.write(dev, None, 1)
        with self.assertRaises(kbsettings.Unsupported):
            PlatformCodec.write(Stub({DP}), 0, 1)
        self.assertEqual(dev.calls, [(MP, 3, b"\x02\x01")])
        before = {"value": 1, "choices": CHOICES, "writable": True}
        self.assertEqual(PlatformCodec.target(before, 2), 2)
        for request, writable in ((3, True), (7, True), (2, False)):
            with self.assertRaises(kbsettings.Unsupported):
                PlatformCodec.target(dict(before, writable=writable), request)

    def test_dual_platform_is_shown_never_set(self):  # DP01, DP02
        for byte, value in ((0, 0), (1, 1)):
            dev = Stub({DP}, {(DP, 0): bytes([byte, 0xEE])})
            self.assertEqual(PlatformCodec.read(dev, None), {"value": value, "writable": False,
                                                             "choices": [dict(c, os=list(c["os"])) for c in kbsettings.DUAL_CHOICES]})
        for reply in (b"\x02", b"\xff", b""):
            with self.subTest(reply=reply.hex()), self.assertRaises(kbsettings.BadReply):
                PlatformCodec.read(Stub({DP}, {(DP, 0): reply}), None)
        both = self.stub(features=(MP, DP, device.HOSTS_INFO))
        PlatformCodec.read(both, 0)
        self.assertNotIn(DP, {call[0] for call in both.calls})  # 0x4530 never asked when 0x4531 is listed
        read = PlatformCodec.read(Stub({DP}, {(DP, 0): b"\x00"}), None)
        read["choices"][0]["os"].append("mutated")
        self.assertEqual(kbsettings.DUAL_CHOICES[0]["os"], ["ios", "macos"])

    def test_only_a_platform_index_is_a_value(self):  # OS13
        self.assertTrue(PlatformCodec.valid(0) and PlatformCodec.valid(0xFE))
        for value in (True, False, -1, 0xFF, 256, "1", 1.0, None, [1], {"index": 1}):
            self.assertFalse(PlatformCodec.valid(value), value)


class SettingsBase(Base):
    def make(self, kb=None, keys=None, **rig):
        return super().make(kb or settings_keyboard(platform=multi()), keys=keys, **rig)

    def ask(self, name, value, settle=True):
        self.rig.kb.set_setting(name, value)
        if settle:
            self.rig.settle()

    def setters(self, feature):
        return [params for f, params in self.dev.setting_writes if f == feature]


class DisableChange(SettingsBase):
    def test_read_once_then_changed_and_read_back(self):  # DK05
        rig = self.make()
        self.assertEqual(control(rig, "disable"), {"value": [], "keys": ALL_KEYS, "writable": True, "busy": False,
                                                   "outcome": None})
        reads = len(packets(rig.port, self.dev, DK, 1))
        self.ask("disable", {"super": True})
        self.assertEqual(self.setters(DK), [b"\x10"])
        self.assertEqual(len(packets(rig.port, self.dev, DK, 1)), reads + 2)  # fresh read before, read back after
        setter = packets(rig.port, self.dev, DK, 2)
        self.assertEqual(len(setter), 1)
        self.assertEqual(set(setter[0][5:]), {0})  # nothing but the bits
        self.assertEqual(control(rig, "disable"), dict(control(rig, "disable"), value=["super"], outcome=None))
        self.ask("disable", {"super": False})
        self.assertEqual(self.setters(DK), [b"\x10", b"\x00"])

    def test_bits_nobody_asked_about_are_kept(self):  # DK02 on the wire
        rig = self.make(settings_keyboard(disable=(0x9F, 0x81)))
        self.assertEqual(control(rig, "disable")["value"], ["caps_lock"])
        self.assertEqual(control(rig, "disable")["keys"], ALL_KEYS)
        self.ask("disable", {"num_lock": True})
        self.assertEqual(self.setters(DK), [b"\x83"])
        self.assertIsNone(control(rig, "disable")["outcome"])

    def test_already_so_nothing_is_sent(self):  # DK06
        rig = self.make(settings_keyboard(disable=(0x1F, 0x01)))
        self.ask("disable", {"caps_lock": True, "num_lock": False})
        self.assertEqual(self.setters(DK), [])
        self.assertEqual(packets(rig.port, self.dev, DK, 2), [])

    def test_a_key_it_cannot_turn_off_sends_nothing(self):  # DK04
        rig = self.make(settings_keyboard(disable=(0x1D, 0x00)))
        self.assertEqual(control(rig, "disable")["keys"], ["caps_lock", "scroll_lock", "insert", "super"])
        self.ask("disable", {"caps_lock": True, "num_lock": True})
        self.assertEqual(packets(rig.port, self.dev, DK, 2), [])
        self.assertEqual(control(rig, "disable"), dict(control(rig, "disable"), value=[], outcome="unsupported", busy=False))
        self.assertEqual(rig.kb._pending, {})

    def test_what_does_not_fit_is_unknown_and_never_written(self):  # DK03, DK13
        for name, change in (("outside the capabilities", lambda kb: kb.settings[DK].update(current=0x20)),
                             ("refused read", lambda kb: kb.failing.update({(DK, 1): 5})),
                             ("refused capabilities", lambda kb: kb.failing.update({(DK, 0): 5}))):
            with self.subTest(name):
                kb = settings_keyboard()
                change(kb)
                rig = self.make(kb)
                self.assertEqual(control(rig, "disable"), {"value": None, "writable": False, "busy": False, "outcome": None})
                sent = len(rig.port.written)
                self.ask("disable", {"caps_lock": True})
                self.assertEqual(rig.port.written[sent:], [])  # not even asked
                rig.stop()
        rig = self.make()
        self.dev.settings[DK]["current"] = 0x40  # gone bad before the change
        self.ask("disable", {"caps_lock": True})
        self.assertEqual(packets(rig.port, self.dev, DK, 2), [])
        self.assertEqual(control(rig, "disable"), {"value": None, "writable": False, "busy": False, "outcome": "unknown"})

    def test_a_keyboard_that_ignores_or_refuses_the_change(self):  # DK12
        rig = self.make()
        self.dev.ignore_writes = True
        self.ask("disable", {"insert": True})
        self.assertEqual(self.setters(DK), [b"\x08"])  # once, nothing sent back
        self.assertEqual(control(rig, "disable"), dict(control(rig, "disable"), value=[], outcome="not-applied"))
        self.dev.ignore_writes = False
        self.dev.failing[(DK, 2)] = 5
        self.ask("disable", {"insert": True})
        self.assertEqual(len(packets(rig.port, self.dev, DK, 2)), 2)
        self.assertEqual(control(rig, "disable")["outcome"], "not-applied")

    def test_another_set_taken_gets_the_whole_earlier_one_back(self):  # DK11
        for taken, outcome, last in (([0xA3], "restored", ["caps_lock"]), ([0xA3, 0xA3], "unconfirmed", ["caps_lock", "num_lock"])):
            with self.subTest(outcome=outcome):
                kb = settings_keyboard(disable=(0xBF, 0x81))
                kb.taken[DK] = list(taken)
                rig = self.make(kb)
                self.ask("disable", {"num_lock": True})
                self.assertEqual(self.setters(DK), [b"\x83", b"\x81"])  # the earlier set, unnamed bit too
                self.assertEqual(control(rig, "disable"), dict(control(rig, "disable"), value=last, outcome=outcome))
                rig.stop()

    def test_no_read_back_is_unconfirmed_and_read_again_later(self):  # DK12 (timeout)
        kb = MuteAfter(controls=(), hosts=(3, 0), settings={DK: {"caps": 0x1F, "current": 0}})
        kb.mute = (DK, 2)
        rig = self.make(kb)
        start = time.monotonic()
        self.ask("disable", {"caps_lock": True})
        self.assertEqual(len(packets(rig.port, kb, DK, 2)), 1)  # never a second, blind one
        self.assertEqual(control(rig, "disable"), {"value": None, "writable": False, "busy": False, "outcome": "unconfirmed"})
        self.assertGreaterEqual(rig.kb._settings_at, start + keyboard.RECOVERY_RETRY)


class DisableToggles(SettingsBase):
    """Keys switched one after another: each change names its key alone, and goes over what a fresh read says."""

    def held(self):
        return self.hold(lambda request: request[0] is DisableCodec.read)

    def test_two_keys_before_the_setter_go_in_one(self):  # DK07
        rig = self.make(settings_keyboard(disable=(0x9F, 0x80)))
        entered, release = self.held()
        self.ask("disable", {"caps_lock": True}, settle=False)
        self.assertTrue(self.reached(entered))
        self.ask("disable", {"num_lock": True}, settle=False)
        self.assertEqual(rig.kb._pending, {"disable": {"caps_lock": True, "num_lock": True}})
        self.assertTrue(control(rig, "disable")["busy"])
        release.set()
        rig.settle()
        self.assertEqual(self.setters(DK), [b"\x83"])
        self.assertEqual(control(rig, "disable")["value"], ["caps_lock", "num_lock"])

    def test_on_and_off_again_before_the_setter_sends_nothing(self):  # DK08
        rig = self.make()
        entered, release = self.held()
        self.ask("disable", {"caps_lock": True}, settle=False)
        self.assertTrue(self.reached(entered))
        self.ask("disable", {"caps_lock": False}, settle=False)
        release.set()
        rig.settle()
        self.assertEqual(self.setters(DK), [])

    def test_a_key_after_the_setter_is_a_change_of_its_own_over_the_first(self):  # DK09
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is DisableCodec.write)
        self.ask("disable", {"caps_lock": True}, settle=False)
        self.assertTrue(self.reached(entered))
        self.ask("disable", {"num_lock": True}, settle=False)
        self.assertEqual(rig.kb._pending, {"disable": {"num_lock": True}})
        release.set()
        rig.settle()
        self.assertEqual(self.setters(DK), [b"\x01", b"\x03"])  # never 0x02 alone
        self.assertEqual(control(rig, "disable")["value"], ["caps_lock", "num_lock"])

    def test_bits_changed_on_the_keyboard_meanwhile_are_kept(self):  # DK10 (case 5)
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is DisableCodec.read)
        self.ask("disable", {"caps_lock": True}, settle=False)
        self.assertTrue(self.reached(entered))
        self.dev.settings[DK]["current"] = 0x14  # someone else turned Scroll Lock and Super off
        release.set()
        rig.settle()
        self.assertEqual(self.setters(DK), [b"\x15"])

    def test_a_report_is_read_not_believed_and_a_burst_is_one_read(self):  # DK14
        rig = self.make()
        reads = len(packets(rig.port, self.dev, DK, 1))
        self.dev.settings[DK]["current"] = 0x08
        rig.note(1, self.dev, DK, b"\x1f")
        rig.settle()
        self.assertEqual(len(packets(rig.port, self.dev, DK, 1)), reads + 1)
        self.assertEqual(control(rig, "disable")["value"], ["insert"])
        entered, release = self.held()
        for _ in range(5):
            rig.port.notify(bytes([0x11, 1, self.dev.index(DK), 0x00]) + bytes(16))
        self.assertTrue(self.reached(entered))
        self.drain()
        release.set()
        rig.settle()
        self.assertLessEqual(len(packets(rig.port, self.dev, DK, 1)), reads + 3)  # the one held, and at most one more
        self.assertEqual(self.dev.setting_writes, [])

    def test_what_is_not_a_change_of_keys_it_has_is_dropped(self):  # DK15, GX09
        rig = self.make()
        sent = len(rig.port.written)
        for name, value in (("disable", 0x01), ("disable", ["caps_lock"]), ("disable", {"caps_lock": 1}),
                            ("disable", {}), ("disable", {1: True}), ("disable", None), ("disable", frozenset({"insert"})),
                            (["disable"], {"caps_lock": True}), (None, {"caps_lock": True}), (5, {"caps_lock": True}),
                            ("platform", "1"), ("platform", True), ("platform", -1), ("platform", 0xFF), ("platform", None)):
            with self.subTest(name=name, value=value):
                rig.engine.set_keyboard_setting(name, value)
                rig.engine._handle(rig.engine._queue.get(timeout=1.0))
                self.assertIsNone(rig.kb._task)
                self.assertEqual(rig.kb._pending, {})
        self.assertEqual(rig.port.written[sent:], [])
        rig.link(1, False)
        for name, value in (("disable", {"caps_lock": True}), ("platform", 2)):
            rig.engine.set_keyboard_setting(name, value)
            rig.engine._handle(rig.engine._queue.get(timeout=1.0))
        self.assertEqual(rig.kb._pending, {})
        rig.link(1, True)
        rig.settle()
        self.assertEqual(self.dev.setting_writes, [])


class PlatformChange(SettingsBase):
    def test_read_then_set_on_this_channel_and_read_back(self):  # OS07
        for current in (0, 2):
            with self.subTest(current=current):
                rig = self.make(settings_keyboard(platform=multi(slot=current), current=current))
                self.assertEqual(control(rig, "platform"), {"value": 1, "choices": CHOICES, "writable": True,
                                                            "busy": False, "outcome": None})
                self.ask("platform", 2)
                self.assertEqual(self.setters(MP), [bytes([current, 2])])
                setter = packets(rig.port, self.dev, MP, 3)
                self.assertEqual(len(setter), 1)
                self.assertEqual(setter[0][4:6], bytes([current, 2]))
                self.assertNotEqual(setter[0][4], 0xFF)
                self.assertEqual(set(setter[0][6:]), {0})
                self.assertEqual(control(rig, "platform"), dict(control(rig, "platform"), value=2, outcome=None))
                rig.stop()

    def test_already_so_or_not_a_platform_it_has_sends_nothing(self):  # OS08, OS09
        rig = self.make()
        self.ask("platform", 1)
        self.assertEqual(self.setters(MP), [])
        self.assertIsNone(control(rig, "platform")["outcome"])
        self.ask("platform", 7)
        self.assertEqual(packets(rig.port, self.dev, MP, 3), [])
        self.assertEqual(control(rig, "platform"), dict(control(rig, "platform"), value=1, outcome="unsupported"))

    def test_read_only_where_the_keyboard_says_so(self):  # OS06 and G5 through the transaction
        rig = self.make(settings_keyboard(platform=multi(flags=0x01)))
        self.assertEqual(control(rig, "platform"), dict(control(rig, "platform"), value=1, writable=False))
        self.assertFalse(rig.kb._controls["platform"]["writable"])
        self.ask("platform", 2)  # as from a page still showing it writable
        self.assertEqual(rig.kb._pending, {})
        self.assertEqual(packets(rig.port, self.dev, MP, 3), [])
        rig.stop()
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is PlatformCodec.read)
        self.ask("platform", 2, settle=False)
        self.assertTrue(self.reached(entered))
        self.dev.settings[MP]["info"] = info(flags=0x00)  # no longer lets software set it
        release.set()
        rig.settle()
        self.assertEqual(packets(rig.port, self.dev, MP, 3), [])
        self.assertEqual(control(rig, "platform"), dict(control(rig, "platform"), value=1, writable=False,
                                                        outcome="unsupported"))

    def test_a_channel_that_is_not_certain_is_never_written(self):  # OS04
        rig = self.make(settings_keyboard(platform=multi(), hosts_info=False))  # without HOSTS_INFO: shown only
        self.assertEqual(control(rig, "platform"), {"value": 1, "choices": CHOICES, "writable": False, "busy": False,
                                                    "outcome": None})
        sent = len(rig.port.written)
        self.ask("platform", 2)
        self.assertEqual(rig.port.written[sent:], [])
        rig.stop()
        cases = {"HOSTS_INFO not CHANGE_HOST's": dict(platform=multi(slot=1)),
                 "byte 5 another channel": dict(platform=multi(slot=1), current=0),
                 "byte 5 past the count": dict(platform=multi(slot=0, hosts=0))}
        for name, kwargs in cases.items():
            with self.subTest(name):
                kb = settings_keyboard(**kwargs)
                if name.startswith("HOSTS_INFO"):
                    kb.settings[device.HOSTS_INFO] = 1  # says channel 1, CHANGE_HOST said 0
                rig = self.make(kb)
                self.assertEqual(control(rig, "platform"), {"value": None, "writable": False, "busy": False, "outcome": None})
                sent = len(rig.port.written)
                self.ask("platform", 2)
                self.assertEqual(rig.port.written[sent:], [])
                self.assertEqual(kb.setting_writes, [])
                rig.stop()

    def test_what_does_not_fit_is_unknown_and_never_written(self):  # OS03, OS05, OS10
        cases = {"undefined platform": multi(current=0xFF), "too many descriptors": multi(count=17),
                 "no descriptors": multi(count=0), "bad echo": {"info": info(), "descriptors": [descriptor(0, 3, 0x100)] * 4},
                 "refused descriptor": multi()}
        for name, platform in cases.items():
            with self.subTest(name):
                kb = settings_keyboard(platform=platform)
                if name == "refused descriptor":
                    kb.failing[(MP, 1)] = 5
                rig = self.make(kb)
                self.assertEqual(control(rig, "platform"), {"value": None, "writable": False, "busy": False, "outcome": None})
                if name == "too many descriptors":
                    self.assertEqual(packets(rig.port, kb, MP, 1), [])
                sent = len(rig.port.written)
                self.ask("platform", 0)
                self.assertEqual(rig.port.written[sent:], [])
                rig.stop()
        rig = self.make()
        self.dev.settings[MP]["descriptors"][2] = descriptor(1, 2, 0)  # gone bad before the change
        self.ask("platform", 2)
        self.assertEqual(packets(rig.port, self.dev, MP, 3), [])
        self.assertEqual(control(rig, "platform"), {"value": None, "writable": False, "busy": False, "outcome": "unknown"})

    def test_ignored_restored_and_unconfirmed(self):  # OS09
        rig = self.make()
        self.dev.ignore_writes = True
        self.ask("platform", 2)
        self.assertEqual(self.setters(MP), [b"\x00\x02"])
        self.assertEqual(control(rig, "platform")["outcome"], "not-applied")
        rig.stop()
        for taken, outcome, last in (([0], "restored", 1), ([0, 0], "unconfirmed", 0)):
            with self.subTest(outcome=outcome):
                kb = settings_keyboard(platform=multi())
                kb.taken[MP] = list(taken)
                rig = self.make(kb)
                self.ask("platform", 2)
                self.assertEqual(self.setters(MP), [b"\x00\x02", b"\x00\x01"])
                self.assertEqual(control(rig, "platform"), dict(control(rig, "platform"), value=last, outcome=outcome))
                rig.stop()
        kb = MuteAfter(controls=(), hosts=(3, 0), settings={device.HOSTS_INFO: 0, MP: multi()})
        kb.mute = (MP, 3)
        rig = self.make(kb)
        self.ask("platform", 2)
        self.assertEqual(len(packets(rig.port, kb, MP, 3)), 1)
        self.assertEqual(control(rig, "platform"), {"value": None, "writable": False, "busy": False, "outcome": "unconfirmed"})

    def test_a_report_is_read_not_believed(self):  # OS11
        rig = self.make()
        reads = len(packets(rig.port, self.dev, MP, 0))
        self.dev.settings[MP]["info"] = info(current=0)  # the keyboard's own shortcut
        rig.note(1, self.dev, MP, b"\x00\x02")
        rig.settle()
        self.assertEqual(len(packets(rig.port, self.dev, MP, 0)), reads + 1)
        self.assertEqual(control(rig, "platform")["value"], 0)
        self.assertEqual(self.dev.setting_writes, [])

    def test_dual_platform_is_shown_and_never_written(self):  # DP01, DP02
        rig = self.make(settings_keyboard(dual=[1]))
        self.assertEqual(control(rig, "platform"), {"value": 1, "choices": [dict(c, os=list(c["os"])) for c in kbsettings.DUAL_CHOICES],
                                                    "writable": False, "busy": False, "outcome": None})
        self.ask("platform", 0)
        self.assertEqual(packets(rig.port, self.dev, DP, 1) + packets(rig.port, self.dev, DP, 2), [])
        reads = len(packets(rig.port, self.dev, DP, 0))
        rig.note(1, self.dev, DP, b"\x00")
        rig.settle()
        self.assertEqual(len(packets(rig.port, self.dev, DP, 0)), reads + 1)
        self.assertEqual(self.dev.setting_writes, [])
        rig.stop()
        rig = self.make(settings_keyboard(platform=multi(), dual=[0]))
        self.assertEqual(packets(rig.port, self.dev, DP, 0), [])  # never asked when 0x4531 is listed
        self.ask("platform", 2)
        self.assertEqual(self.setters(MP), [b"\x00\x02"])
        self.assertEqual(packets(rig.port, self.dev, DP, 0) + packets(rig.port, self.dev, DP, 1), [])

    def test_the_mouse_is_heard_while_many_descriptors_are_slow(self):  # OS12
        rows = [descriptor(i % 3, i, 0x0100 << (i % 3)) for i in range(16)]
        rig = self.make(settings_keyboard(platform={"info": info(count=16), "descriptors": rows}))
        mouse = rig.port.devices[2]
        rig.engine.cfg["buttons"]["back"] = actions.keys("LEFTSHIFT")
        self.dev.delays = {MP: 0.03, device.HOSTS_INFO: 0.03}
        self.ask("platform", 2, settle=False)
        for _ in range(3):
            time.sleep(0.05)
            self.assertIsNotNone(rig.kb._asked)
            rig.mouse_actions.ran.clear()
            start = time.monotonic()
            rig.port.notify(bytes([0x11, 2, mouse.index(device.REPROG_CONTROLS_V4), 0]) + struct.pack("!HHHH", 0x0053, 0, 0, 0).ljust(16, b"\0"))
            while not rig.mouse_actions.ran:
                rig.engine._handle(rig.engine._queue.get(timeout=1.0))
            self.assertLess(time.monotonic() - start, 0.1)
            rig.port.notify(bytes([0x11, 2, mouse.index(device.REPROG_CONTROLS_V4), 0]) + bytes(16))
        rig.settle()
        self.assertEqual(self.setters(MP), [b"\x00\x02"])


class SettingsCommon(SettingsBase):
    """What holds for every setting holds for these two: the guard, the wishes, the keys, going and staying."""

    CHANGES = (("disable", {"caps_lock": True}, DisableCodec, DK), ("platform", 2, PlatformCodec, MP))

    def rivals(self, *names):
        self.rig.rivals = list(names)
        self.rig.engine._look_for_rivals(time.monotonic())

    def test_never_where_no_key_may_be_changed(self):  # GX04 (F10 guard)
        for kwargs, reason in ((dict(rivals=["Solaar"]), "rivals"), (dict(journal="not a journal"), "journal")):
            with self.subTest(reason):
                rig = self.make(**kwargs)
                self.assertEqual(rig.state()["reason"], reason)
                for name, value, _codec, _feature in self.CHANGES:
                    self.assertFalse(control(rig, name)["writable"])
                    self.assertIsNotNone(control(rig, name)["value"])  # read all the same
                    rig.engine.set_keyboard_setting(name, value)
                    rig.engine._handle(rig.engine._queue.get(timeout=1.0))
                rig.settle()
                self.assertEqual(rig.kb._pending, {})
                self.assertEqual(self.dev.setting_writes, [])
                rig.stop()
        rig = self.make(settings_keyboard(platform=multi(), info=False))
        self.assertEqual(rig.state()["reason"], "identity")
        self.ask("disable", {"caps_lock": True})
        self.ask("platform", 2)
        self.assertEqual(self.dev.setting_writes, [])

    def test_other_software_seen_before_the_setter_sends_nothing(self):  # GX04
        for name, value, codec, feature in self.CHANGES:
            with self.subTest(name):
                rig = self.make()
                entered, release = self.hold(lambda request, codec=codec: request[0] is codec.read)
                self.ask(name, value, settle=False)
                self.assertTrue(self.reached(entered))
                self.rivals("Solaar")
                self.assertEqual(rig.kb._pending, {})
                release.set()
                rig.settle()
                self.rivals()
                rig.settle()
                self.assertEqual(self.dev.setting_writes, [])
                self.assertEqual(packets(rig.port, self.dev, feature, 3 if feature == MP else 2), [])
                rig.stop()

    def test_a_setter_waiting_is_called_off_once_other_software_is_seen(self):  # GX04 (admission)
        for name, value, codec, feature in self.CHANGES:
            with self.subTest(name):
                rig = self.make()
                mouse = rig.port.devices[2]
                entered, release = self.hold(lambda request, codec=codec: request[0] is codec.write)
                self.ask(name, value, settle=False)
                self.assertTrue(self.reached(entered))
                mouse.delay = 0.5
                foreground = threading.Thread(target=rig.transport().request, args=(2, 0, 1, bytes([0, 0, 1])))
                foreground.start()
                time.sleep(0.05)
                release.set()
                time.sleep(0.1)
                self.assertIs(rig.kb._asked[0], codec.write)
                self.rivals("Solaar")
                foreground.join()
                rig.settle()
                self.assertEqual(self.dev.setting_writes, [])
                self.assertEqual(control(rig, name), dict(control(rig, name), writable=False, outcome="unconfirmed"))
                rig.stop()

    def test_other_software_after_the_setter_nothing_is_sent_back(self):  # GX04 (restore guard)
        kb = settings_keyboard(disable=(0x1F, 0x00), platform=multi())
        kb.taken[DK] = [0x03]
        rig = self.make(kb)
        entered, release = self.hold(lambda request: request[0] is DisableCodec.read and kb.setting_writes)
        self.ask("disable", {"caps_lock": True}, settle=False)
        self.assertTrue(self.reached(entered))
        self.rivals("Solaar")
        release.set()
        rig.settle()
        self.assertEqual(self.setters(DK), [b"\x01"])  # never the earlier set sent back
        self.assertEqual(control(rig, "disable"), dict(control(rig, "disable"), value=["caps_lock", "num_lock"],
                                                       writable=False, outcome="unconfirmed"))

    def test_the_earlier_value_goes_back_only_where_the_read_back_lets_it(self):  # restore admission
        def unsettable(kb):
            kb.settings[MP]["info"] = bytes([0]) + kb.settings[MP]["info"][1:]  # no longer settable by software

        def layout_gone(kb):
            kb.settings[MP]["descriptors"][2] = descriptor(0, 2, 0x6000)

        def layout_two_gone(kb):
            kb.settings[MP]["descriptors"][3] = descriptor(0, 3, 0x0400)

        def super_gone(kb):
            kb.settings[DK]["caps"] = 0x0F

        def bit_0x20_gone(kb):
            kb.settings[DK]["caps"] = 0x9F

        cases = (  # (disable bits, taken, change after each setter, name, asked, setters sent, value then, outcome)
            ((0x1F, 0x00), [0], unsettable, "platform", 2, [b"\x00\x02"], 0, "unconfirmed"),
            ((0x1F, 0x00), [0], layout_gone, "platform", 2, [b"\x00\x02"], 0, "unconfirmed"),
            ((0x1F, 0x10), [0x01], super_gone, "disable", {"caps_lock": True}, [b"\x11"], ["caps_lock"], "unconfirmed"),
            ((0x1F, 0x00), [0], layout_two_gone, "platform", 2, [b"\x00\x02", b"\x00\x01"], 1, "restored"),
            ((0xBF, 0x81), [0x03], bit_0x20_gone, "disable", {"num_lock": True}, [b"\x83", b"\x81"], ["caps_lock"],
             "restored"),  # the unnamed bit 0x80 goes back with it, as the keyboard still has it
        )
        for disable, taken, change, name, asked, setters, value, outcome in cases:
            with self.subTest(change.__name__):
                kb = settings_keyboard(disable=disable, platform=multi())
                feature, setter = (MP, 3) if name == "platform" else (DK, 2)
                kb.taken[feature] = list(taken)

                def changing(feat_idx, function, params, kb=kb, answer=kb.answer, sets=(feature, setter), change=change):
                    reply = answer(feat_idx, function, params)
                    if feat_idx < len(kb.features) and (kb.features[feat_idx], function) == sets:
                        change(kb)
                    return reply

                kb.answer = changing
                rig = self.make(kb)
                self.ask(name, asked)
                self.assertEqual(self.setters(feature), setters)
                self.assertEqual(control(rig, name), dict(control(rig, name), value=value, outcome=outcome))
                rig.stop()

    def test_a_wish_calls_the_change_off_and_it_follows_merged(self):  # GX08
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is DisableCodec.read)
        self.ask("disable", {"caps_lock": True}, settle=False)
        self.assertTrue(self.reached(entered))
        self.ask("disable", {"num_lock": True}, settle=False)
        new = copy.deepcopy(rig.engine.cfg)
        new["keyboard"]["keys"] = {"0x0f01": COPY}
        rig.engine._apply_config(new)
        self.assertEqual(rig.kb._task.__name__, "_configure")
        self.assertEqual(rig.kb._pending, {"disable": {"caps_lock": True, "num_lock": True}})
        release.set()
        rig.settle()
        self.assertEqual(self.setters(DK), [b"\x03"])
        self.assertTrue(rig.key(0x0F01)["active"])
        rig.stop()
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is PlatformCodec.read)
        self.ask("platform", 2, settle=False)
        self.assertTrue(self.reached(entered))
        rig.kb.device_action({"type": "host", "host": 1}, "press")  # Easy-Switch goes first
        self.assertEqual(rig.kb._pending, {"platform": 2})
        release.set()
        rig.settle()
        self.assertEqual(self.dev.switched, [1])
        self.assertEqual(self.setters(MP), [])  # not while the keyboard may be on its way

    def test_a_change_waits_while_a_key_is_down(self):  # GX05
        rig = self.make(keys={"0x0f01": COPY})
        rig.keys_down(1, self.dev, 0x0F01)
        self.ask("disable", {"super": True})
        self.ask("platform", 0)
        self.assertEqual(self.dev.setting_writes, [])
        self.assertEqual(rig.kb._pending, {"disable": {"super": True}, "platform": 0})
        rig.keys_down(1, self.dev)
        rig.settle()
        self.assertEqual(self.dev.setting_writes, [(DK, b"\x10"), (MP, b"\x00\x00")])

    def test_gone_replaced_or_exiting_never_sends_or_sends_back(self):  # GX03
        for name, value, codec, feature in self.CHANGES:
            with self.subTest(name, when="before the setter"):
                rig = self.make()
                entered, release = self.hold(lambda request, codec=codec: request[0] is codec.read)
                self.ask(name, value, settle=False)
                self.assertTrue(self.reached(entered))
                rig.link(1, False)
                release.set()
                self.drain()
                rig.link(1, True)
                rig.settle()
                self.assertEqual(self.dev.setting_writes, [])
                rig.stop()
            with self.subTest(name, when="after the setter"):
                kb = settings_keyboard(platform=multi())
                kb.taken.update({DK: [0x03], MP: [0]})
                rig = self.make(kb)
                entered, release = self.hold(lambda request, codec=codec: request[0] is codec.read and kb.setting_writes)
                self.ask(name, value, settle=False)
                self.assertTrue(self.reached(entered))
                rig.link(1, False)
                release.set()
                self.drain()
                self.assertEqual(len(kb.setting_writes), 1)  # nothing sent back
                rig.stop()
            with self.subTest(name, when="another unit"):
                rig = self.make(keys={"0x0f01": COPY})
                rig.keys_down(1, self.dev, 0x0F01)
                self.ask(name, value)
                rig.link(1, False)
                other = rig.port.devices[1] = settings_keyboard(platform=multi(), unit=UNIT_B)
                rig.link(1, True)
                rig.settle()
                rig.keys_down(1, other)
                rig.settle()
                self.assertEqual(other.setting_writes, [])
                rig.stop()
            with self.subTest(name, when="exit"):
                rig = self.make(keys={"0x0f01": COPY})
                rig.keys_down(1, self.dev, 0x0F01)
                self.ask(name, value)
                start = time.monotonic()
                rig.kb.shutdown()
                self.assertLess(time.monotonic() - start, keyboard.SHUTDOWN_BUDGET)
                self.assertEqual(self.dev.setting_writes, [])
                rig.stop()

    def test_nothing_is_put_back_on_finding_reconnecting_or_new_settings(self):  # GX02
        with mock.patch.object(config, "save", side_effect=AssertionError("saved")), \
                mock.patch.object(config, "write_durable", side_effect=AssertionError("written")):
            rig = self.make()
            before, exported = copy.deepcopy(rig.engine.cfg), config.export_data(rig.engine.cfg)
            self.ask("disable", {"insert": True})
            self.ask("platform", 2)
            writes = list(self.dev.setting_writes)
            self.dev.settings[DK]["current"] = 0
            self.dev.settings[MP]["info"] = info(current=1)
            rig.link(1, False)
            rig.link(1, True)
            rig.settle()
            new = copy.deepcopy(rig.engine.cfg)
            new["keyboard"]["keys"] = {"0x0f01": COPY}
            rig.engine._apply_config(new)
            rig.settle()
            self.assertEqual(self.dev.setting_writes, writes)
            self.assertEqual(control(rig, "disable")["value"], [])  # what the keyboard says, not what was asked
            self.assertEqual(control(rig, "platform")["value"], 1)
            self.assertEqual(config.export_data(before), exported)
            self.assertNotIn("disable", json.dumps(rig.engine.cfg))
            self.assertNotIn("platform", json.dumps(rig.engine.cfg))

    def test_state_is_plain_json_and_tells_no_bits_slots_or_unit(self):  # GX06, GX07
        def plain(value):
            if isinstance(value, dict):
                return all(type(k) is str and plain(v) for k, v in value.items())
            if isinstance(value, list):
                return all(plain(v) for v in value)
            return value is None or type(value) in (str, int, bool)
        with self.assertLogs("mxoptions", logging.DEBUG) as logs:
            kb = settings_keyboard(disable=(0x9F, 0x81), platform=multi(slot=2), current=2)
            rig = self.make(kb)
            self.ask("disable", {"num_lock": True})
            kb.ignore_writes = True
            self.ask("platform", 2)
            kb.settings[DK]["current"] = 0x40
            self.ask("disable", {"insert": True})
            rig.kb.set_setting("platform", "x")
        controls = rig.state()["controls"]
        self.assertTrue(plain(controls))
        self.assertEqual(json.loads(json.dumps(controls)), controls)
        self.assertEqual(set(controls["disable"]), {"value", "writable", "busy", "outcome"})
        self.assertEqual(set(controls["platform"]), {"value", "choices", "writable", "busy", "outcome"})
        state, shown = json.dumps(rig.state()), json.dumps(controls)
        for private in (identity(UNIT_A), UNIT_A.hex(), "5e015e02", rig.kb.link):
            self.assertNotIn(private, state)
        for private in ("caps", "slot", "host", "\\x", "mask", "129", "131", "159"):  # 0x81, 0x83, 0x9f
            self.assertNotIn(private, shown)
        lines = [line for line in logs.output if "setting" in line]
        self.assertTrue(lines)
        for line in lines:
            self.assertNotRegex(line, r"\d|\\x|identity|mask")
        self.assertIn("keyboard setting platform: the keyboard did not take the change", "\n".join(lines))


class SettingsPage(unittest.TestCase):
    """The keys to turn off and the layout on the Keyboard page (Qt offscreen)."""

    def setUp(self):
        Page.setUp(self)
        self.disable_row, self.platform_row = self.page.settings_rows["disable"], self.page.settings_rows["platform"]
        self.box = self.page.platform_box

    def show(self, disable=None, platform=None, reason=None, connected=True):
        generic = {"writable": True, "busy": False, "outcome": None}
        controls = {}
        if disable is not None:
            controls["disable"] = dict(generic, **dict({"value": ["insert"], "keys": ALL_KEYS}, **disable))
        if platform is not None:
            controls["platform"] = dict(generic, **dict({"value": 1, "choices": CHOICES}, **platform))
        found = {"connected": connected, "known": True, "name": "Synthetic keyboard", "battery": None, "current_host": 0,
                 "hosts": [], "keys": [], "reason": reason, "failed": None, "firmware": None,
                 "settings": ["platform", "disable"], "controls": controls}
        self.store.update_state(dict(self.store.state, keyboard=found))

    def shown_keys(self):
        return [key for key, row in self.page.disable_rows.items() if not row.isHidden()]

    def test_a_row_for_each_key_it_can_turn_off(self):  # UI01
        self.show()
        self.assertEqual(self.shown_keys(), [])
        self.assertIn("MX Options does not", self.disable_row.note.text())
        self.assertTrue(self.box.isHidden())
        self.show({"keys": ["caps_lock", "scroll_lock", "super"], "value": ["super"]}, {})
        self.assertEqual(self.shown_keys(), ["caps_lock", "scroll_lock", "super"])
        self.assertEqual([key for key, switch in self.page.disable_switches.items() if switch.isChecked()], ["super"])
        self.assertEqual(self.disable_row.note.text(), self.pages.DISABLE_NOTE)
        self.assertEqual(self.disable_row.info.text(), self.pages.DISABLE_HINT)
        self.show({"value": None, "writable": False})
        self.store.state["keyboard"]["controls"]["disable"].pop("keys")  # as a failed read is published
        self.store.update_state(copy.deepcopy(self.store.state))
        self.assertEqual(self.shown_keys(), [])
        self.assertEqual(self.disable_row.note.text(), "The keyboard did not say how this is set.")
        self.show({}, {}, connected=False)
        self.assertEqual(self.shown_keys(), [])
        self.assertTrue(self.box.isHidden())
        self.assertEqual(self.calls, [])

    def test_layouts_by_their_systems_numbered_where_alike(self):  # UI02
        self.show(None, {})
        self.assertFalse(self.box.isHidden())
        self.assertEqual([self.box.itemText(i) for i in range(self.box.count())], ["Windows, Android", "macOS, iOS", "Linux"])
        self.assertEqual(self.box.currentIndex(), 1)
        self.assertIs(self.platform_row.title.buddy(), self.box)
        self.assertEqual(self.platform_row.info.text(), self.pages.PLATFORM_HINT)
        self.assertEqual(self.pages.layout_labels([{"index": 0, "os": []}, {"index": 4, "os": ["linux"]},
                                                   {"index": 6, "os": ["linux"]}, {"index": 7, "os": ["webos", "tizen"]}]),
                         ["Layout 1", "Linux · Layout 5", "Linux · Layout 7", "webOS, Tizen"])
        self.show(None, {"value": None, "writable": False, "choices": []})
        self.assertEqual(self.box.count(), 0)
        self.assertFalse(self.box.isEnabled())

    def test_disabled_while_unknown_not_writable_busy_or_held(self):  # UI03
        for change in ({"writable": False}, {"busy": True}, {"value": None, "writable": False}):
            with self.subTest(change=change):
                self.show(change, change)
                self.assertFalse(any(switch.isEnabled() for switch in self.page.disable_switches.values()))
                self.assertFalse(self.box.isEnabled())
        self.show({"writable": False}, {"writable": False})
        self.assertEqual(self.platform_row.note.text(), "The keyboard does not let MX Options change this.")
        self.show({"writable": False}, {"writable": False}, reason="rivals")
        self.assertEqual(self.platform_row.note.text(), "")  # the banner says why
        self.show({}, {})
        self.assertTrue(all(switch.isEnabled() for switch in self.page.disable_switches.values()))
        self.assertTrue(self.box.isEnabled())
        self.assertEqual(self.calls, [])

    def test_one_engine_call_per_toggle_or_pick_none_on_refresh(self):  # UI04
        self.show({}, {})
        self.page.disable_switches["caps_lock"].click()
        self.assertEqual(self.calls, [("disable", {"caps_lock": True})])
        self.show({"busy": True}, {})
        self.assertTrue(self.page.disable_switches["caps_lock"].isChecked())  # as asked, until the keyboard answers
        self.show({"value": ["insert", "caps_lock"]}, {})
        self.page.disable_switches["insert"].click()
        self.assertEqual(self.calls[-1], ("disable", {"insert": False}))
        self.show({"value": ["caps_lock"]}, {"value": 2})
        self.assertEqual(self.box.currentIndex(), 2)
        self.box.activated.emit(0)
        self.assertEqual(self.calls[-1], ("platform", 0))
        self.box.activated.emit(2)  # the one it has: nothing to ask
        self.show({"value": []}, {"value": 0})
        self.assertEqual(self.calls, [("disable", {"caps_lock": True}), ("disable", {"insert": False}), ("platform", 0)])

    def test_every_outcome_has_its_note(self):  # UI05
        for outcome, text in self.pages.OUTCOMES.items():
            with self.subTest(outcome):
                self.show({"outcome": outcome}, {"outcome": outcome})
                self.assertEqual(self.disable_row.note.text(), text)
                self.assertEqual(self.platform_row.note.text(), text)

    def test_names_for_assistive_technology_and_words_translated(self):  # UI06
        self.show({}, {})
        self.assertEqual([switch.accessibleName() for switch in self.page.disable_switches.values()],
                         ["Turn off the Caps Lock key", "Turn off the Num Lock key", "Turn off the Scroll Lock key",
                          "Turn off the Insert key", "Turn off the Windows/Super key"])
        self.assertEqual(self.box.accessibleName(), "Operating system layout")
        from mxoptions import i18n_uk
        for text in (self.pages.DISABLE_NOTE, self.pages.DISABLE_HINT, self.pages.PLATFORM_HINT, "Turn off the {} key",
                     "Layout {}", "Windows/Super key"):
            self.assertIn(text, i18n_uk.STRINGS)


# -- the backlight (0x1982), and the notice of a brightness the keyboard reports ------------------------------------

BL, Backlight = device.BACKLIGHT_2, kbsettings.BacklightCodec
ROOT = device.ROOT


def bl_config(on=1, options=0x19, caps=0x3D, level=4, timers=(12, 6, 60), fourth=0x0405):
    """BACKLIGHT_2 function 0's answer: on, options (0x19: wow and a fixed level), what it has (0x3D: wow, off at low
    battery, automatic, set with its keys, fixed), effects 0x007F, the level, three timers in steps of 5 s and a
    fourth one."""
    return bytes([on, options, caps, 0x7F, 0x00, level]) + struct.pack("<4H", *timers, fourth)


def bl_info(levels=8, shown=4):
    """BACKLIGHT_2 function 2's answer: the number of levels, the level shown now, a status and an effect."""
    return bytes([levels, shown, 2, 0]) + bytes(8)


V2_CONFIG = bytes([0x01, 0x05, 0x05, 0x7F, 0x00, 0x00]) + bytes(8)  # on, wow and low battery, no modes, all timers 0
V2_INFO = bytes([8, 0, 4, 0]) + bytes(8)
ALL_FIELDS = ["on", "power_save", "mode", "level", "hands_out", "hands_in", "powered"]
ASKABLE = ["on", "mode", "level", "hands_out", "hands_in", "powered"]


def backlight_keyboard(config=None, levels=None, version=3, settings=None, **kwargs):
    """A synthetic keyboard with BACKLIGHT_2: function 0 answers ``config``, function 2 ``levels``."""
    settings = dict(settings or {})
    settings[BL] = {"config": bytearray(config or bl_config()), "info": bytearray(levels or bl_info())}
    kb = keyboard_device(settings=settings, **kwargs)
    kb.versions[BL] = version
    return kb


class BlStub(Stub):
    """A device with BACKLIGHT_2 at index 11 for the codec alone."""

    def __init__(self, config=None, info=None, version=3, root=None):
        super().__init__({BL}, {(ROOT, 0): root if root is not None else bytes([11, 0, version]),
                                (BL, 0): config or bl_config(), (BL, 2): info if info is not None else bl_info()})
        self.features = {ROOT: 0, BL: 11}


def read(config=None, info=None, version=3, **kwargs):
    return Backlight.read(BlStub(config, info, version, **kwargs), None)


class BacklightDecode(unittest.TestCase):
    def test_an_older_keyboard_shows_on_and_low_battery_only(self):
        dev = BlStub(V2_CONFIG, V2_INFO, version=2)
        result = Backlight.read(dev, None)
        self.assertEqual(dev.calls, [(ROOT, 0, b"\x19\x82"), (BL, 0, b""), (BL, 2, b"")])
        self.assertEqual(result["value"], {"on": True, "power_save": True, "mode": "none", "level": None,
                                           "hands_out": None, "hands_in": None, "powered": None})
        self.assertEqual((result["fields"], result["can"], result["levels"], result["shown_level"], result["writable"]),
                         (["on", "power_save"], ["on"], 8, 0, True))
        self.assertEqual((result["raw"], result["version"]), (V2_CONFIG, 2))

    def test_every_field_of_a_newer_keyboard(self):
        result = read(bl_config(options=0x09), bl_info(shown=3))
        self.assertEqual(result["value"], {"on": True, "power_save": False, "mode": "auto", "level": 4, "hands_out": 60,
                                           "hands_in": 30, "powered": 300})
        self.assertEqual((result["fields"], result["can"], result["levels"], result["shown_level"]),
                         (ALL_FIELDS, ASKABLE, 8, 3))
        self.assertEqual(read(bl_config(on=0, options=0x0D))["value"]["on"], False)
        self.assertEqual(read(bl_config(options=0x0D))["value"]["power_save"], True)
        self.assertEqual(read(bl_config(timers=(1, 1440, 7)))["value"], dict(read()["value"], hands_out=5, hands_in=7200,
                                                                             powered=35))

    def test_level_and_timers_only_from_version_3_and_changes_only_on_2_and_3(self):
        result = read(version=2)
        self.assertEqual((result["fields"], result["can"]), (["on", "power_save", "mode"], ["on", "mode"]))
        self.assertIsNone(result["value"]["level"])
        for version, fields in ((4, ALL_FIELDS), (1, ["on", "power_save", "mode"]), (0, ["on", "power_save", "mode"])):
            with self.subTest(version=version):
                result = read(version=version)
                self.assertEqual((result["fields"], result["can"], result["writable"]), (fields, [], False))
                self.assertEqual(result["value"]["on"], True)
                with self.assertRaises(kbsettings.Unsupported):
                    Backlight.target(result, {"on": False})
        for root in (bytes([12, 0, 3]), bytes([11, 0])):  # not the feature asked about, too short
            with self.subTest(root=root.hex()), self.assertRaises(kbsettings.BadReply):
                read(root=root)

    def test_what_the_keyboard_has_decides_what_is_shown(self):
        for caps, fields in ((0x3D, ALL_FIELDS), (0x39, ["on", "mode", "level", "hands_out", "hands_in", "powered"]),
                             (0x09, ["on", "hands_out", "hands_in", "powered"]),
                             (0x21, ["on", "level", "hands_out", "hands_in", "powered"]),
                             (0x28, ["on", "mode", "level", "hands_out", "hands_in", "powered"])):
            with self.subTest(caps=hex(caps)):
                self.assertEqual(read(bl_config(caps=caps))["fields"], fields)
        for levels, shown in ((0, False), (1, False), (2, True), (16, True), (17, False), (255, False)):
            with self.subTest(levels=levels):
                result = read(bl_config(level=1), bl_info(levels=levels, shown=1))
                self.assertEqual("level" in result["fields"], shown)
                self.assertEqual("level" in result["can"], shown)
                self.assertEqual(result["levels"], levels if shown else None)
                self.assertEqual(result["shown_level"], 1 if shown else None)

    def test_an_answer_that_does_not_fit_is_not_believed(self):
        for config in (bl_config()[:13], bytes([2]) + bl_config()[1:], bytes([0xFF]) + bl_config()[1:]):
            with self.subTest(config=config.hex()), self.assertRaises(kbsettings.BadReply):
                read(config)
        with self.assertRaises(hidpp.HidppFeatureError):
            self._fails(BL, 0)
        with self.assertRaises(hidpp.HidppTimeout):
            self._fails(BL, 2, hidpp.HidppTimeout("no answer"))
        for info in (hidpp.HidppFeatureError(5), b"\x08"):  # levels not said: no level, the rest as ever
            with self.subTest(info=info):
                dev = BlStub()
                dev.answers[BL, 2] = info
                result = Backlight.read(dev, None)
                self.assertEqual((result["levels"], result["shown_level"]), (None, None))
                self.assertNotIn("level", result["fields"])
                self.assertEqual(result["can"], ["on", "mode", "hands_out", "hands_in", "powered"])
        dev = BlStub()
        dev.answers[BL, 2] = bytes([8, 8])  # shows a level past the 0-7 it has
        result = Backlight.read(dev, None)
        self.assertEqual((result["levels"], result["shown_level"]), (8, None))

    def _fails(self, feature, function, error=None):
        dev = BlStub()
        dev.answers[feature, function] = error or hidpp.HidppFeatureError(5)
        return Backlight.read(dev, None)

    def test_a_value_it_cannot_say_is_unknown_and_the_rest_still_set(self):
        for units in (0, 1441, 0xFFFF):
            with self.subTest(units=units):
                result = read(bl_config(timers=(units, 6, 60)))
                self.assertIsNone(result["value"]["hands_out"])
                self.assertIn("hands_out", result["fields"])
                self.assertEqual(result["can"], ["on", "mode", "level", "hands_in", "powered"])
        result = read(bl_config(level=8, timers=(0, 6, 60)), bl_info(levels=8))
        self.assertIsNone(result["value"]["level"])
        self.assertEqual(result["can"], ["on", "mode", "hands_in", "powered"])
        self.assertEqual(Backlight.target(result, {"on": False}), bytes.fromhex("0019ff08000006003c000504"))

    def test_each_change_sends_its_own_bytes_and_every_other_as_read(self):
        before = read(bl_config(options=0xDF, caps=0x3D))  # every option bit but bit 5 set: a fixed level
        self.assertEqual(before["value"]["mode"], "manual")
        for request, params in (({"on": False}, "00dfff040c0006003c000504"),
                                ({"mode": "auto"}, "01cfff040c0006003c000504"),
                                ({"level": 6}, "01dfff060c0006003c000504"),
                                ({"level": 0}, "01dfff000c0006003c000504"),
                                ({"hands_out": 30}, "01dfff04060006003c000504"),
                                ({"hands_in": 7200}, "01dfff040c00a0053c000504"),
                                ({"powered": 5}, "01dfff040c00060001000504"),
                                ({"on": True, "hands_in": 35, "powered": 600}, "01dfff040c0007007800" "0504")):
            with self.subTest(request=request):
                self.assertEqual(Backlight.target(before, request), bytes.fromhex(params))
        automatic = read(bl_config(options=0xCF, level=5))
        self.assertEqual(Backlight.target(automatic, {"on": False}), bytes.fromhex("00cfff050c0006003c000504"))
        self.assertEqual(Backlight.target(automatic, {"mode": "manual"}), bytes.fromhex("01dfff050c0006003c000504"))
        dev = Stub(set())
        Backlight.write(dev, None, bytes.fromhex("01dfff050c0006003c000504"))
        self.assertEqual(dev.calls, [(BL, 1, bytes.fromhex("01dfff050c0006003c000504"))])

    def test_while_set_with_its_keys_only_automatic_or_fixed_is_sent(self):
        keys = read(bl_config(options=0x11))  # wow, set with its keys
        self.assertEqual((keys["value"]["mode"], keys["can"]), ("keys", ["mode"]))
        for request in ({"on": False}, {"hands_out": 30}, {"level": 3}, {"mode": "manual", "level": 3}):
            with self.subTest(request=request), self.assertRaises(kbsettings.Unsupported):
                Backlight.target(keys, request)
        self.assertEqual(Backlight.target(keys, {"mode": "auto"}), bytes.fromhex("0109ff040c0006003c000504"))
        self.assertEqual(Backlight.target(keys, {"mode": "manual"}), bytes.fromhex("0119ff040c0006003c000504"))
        self.assertEqual(read(bl_config(options=0x11, caps=0x15))["can"], [])  # no Automatic or Fixed to pick
        unknown = read(bl_config(options=0x29))  # bit 5 set: a mode it does not know
        self.assertIsNone(unknown["value"]["mode"])
        self.assertEqual(unknown["can"], ["on", "hands_out", "hands_in", "powered"])
        for request in ({"mode": "auto"}, {"level": 2}):
            with self.subTest(request=request), self.assertRaises(kbsettings.Unsupported):
                Backlight.target(unknown, request)
        self.assertEqual(Backlight.target(unknown, {"on": False}), bytes.fromhex("0029ff040c0006003c000504"))
        for mode in ("keys", "none", None):  # what a keyboard had, sent back: never a mode but these two
            with self.subTest(mode=mode), self.assertRaises(kbsettings.Unsupported):
                Backlight.target(read(), {"mode": mode})

    def test_a_level_only_while_fixed_and_one_it_has(self):
        automatic = read(bl_config(options=0x09))
        with self.assertRaises(kbsettings.Unsupported):
            Backlight.target(automatic, {"level": 5})
        self.assertEqual(Backlight.target(automatic, {"mode": "manual", "level": 5}),
                         bytes.fromhex("0119ff050c0006003c000504"))
        for request in ({"level": 8}, {"level": 15}, {"mode": "auto", "level": 2}):
            with self.subTest(request=request), self.assertRaises(kbsettings.Unsupported):
                Backlight.target(read(), request)
        with self.assertRaises(kbsettings.Unsupported):
            Backlight.target(read(bl_config(options=0x01)), {"level": 2})  # mode 0: no fixed level
        with self.assertRaises(kbsettings.Unsupported):
            Backlight.target(dict(read(), writable=False), {"on": False})  # read-only: nothing, whatever it lets

    def test_what_is_a_change(self):
        for value in ({"on": True}, {"mode": "auto"}, {"mode": "manual"}, {"level": 0}, {"level": 15},
                      {"hands_out": 5}, {"hands_in": 3600}, {"powered": 7200}, dict.fromkeys(["on"], False),
                      {"on": False, "mode": "manual", "level": 3, "hands_out": 10, "hands_in": 15, "powered": 20}):
            self.assertTrue(Backlight.valid(value), value)
        for value in ({"level": True}, {"level": 2.0}, {"level": 16}, {"level": -1}, {}, {"power_save": True},
                      {"on": 1}, {"on": "true"}, {"mode": "keys"}, {"mode": "none"}, {"mode": 1}, {"hands_in": 4},
                      {"hands_in": 7201}, {"hands_in": 7}, {"hands_out": 60.0}, {"powered": 0}, {1: True}, [], None,
                      True, {"on": True, "mode": "auto", "level": 1, "hands_out": 5, "hands_in": 5, "powered": 5,
                             "power_save": True}):
            self.assertFalse(Backlight.valid(value), value)
        self.assertEqual(Backlight.merge({"mode": "manual"}, {"level": 5}), {"mode": "manual", "level": 5})
        self.assertEqual(Backlight.merge(None, {"level": 5}), {"level": 5})
        self.assertEqual(Backlight.merge({"level": 3}, {"level": 4}), {"level": 4})

    def test_shown_without_bytes_or_version(self):
        result = read(bl_config(options=0x09))
        shown = Backlight.public(result)
        self.assertEqual(set(shown), {"value", "fields", "can", "levels", "shown_level"})
        self.assertEqual(json.loads(json.dumps(shown)), shown)
        self.assertEqual(Backlight.fields(result, {"on": False, "hands_in": 5}), {"on": True, "hands_in": 30})


def on_setter(kb, change, times=1):
    """Have ``change(kb)`` run as the keyboard takes each of its next ``times`` backlight setters."""
    answer, left = kb.answer, [times]

    def answering(feat_idx, function, params):
        reply = answer(feat_idx, function, params)
        if feat_idx < len(kb.features) and kb.features[feat_idx] == BL and function == 1 and left[0]:
            left[0] -= 1
            change(kb)
        return reply
    kb.answer = answering


def muted_once(kb):
    """The backlight read after the next backlight setter gets no answer at all."""
    answer, seen = kb.answer, {"setter": False, "muted": False}

    def answering(feat_idx, function, params):
        feature = kb.features[feat_idx] if feat_idx < len(kb.features) else None
        if feature == ROOT and function == 0 and seen["setter"] and not seen["muted"]:
            seen["muted"] = True
            return None
        reply = answer(feat_idx, function, params)
        seen["setter"] = seen["setter"] or feature == BL and function == 1
        return reply
    kb.answer = answering


class Later:
    """time.monotonic as it runs, moved on at will: a deadline far off comes without being waited for."""

    def __init__(self, test):
        real, self.offset = time.monotonic, 0.0
        patcher = mock.patch.object(time, "monotonic", lambda: real() + self.offset)
        patcher.start()
        test.addCleanup(patcher.stop)

    def skip(self, seconds):
        self.offset += seconds


class BacklightBase(SettingsBase):
    """A rig with a backlit keyboard (version 3, fixed level 4 of 0-7, timers 60 s, 30 s, 300 s) found and read."""

    def make(self, kb=None, notices=True, **rig):
        made = super().make(kb or backlight_keyboard(), **rig)
        made.engine.cfg["keyboard"]["notices"] = notices
        return made

    def report(self, event=0):
        """The keyboard reports a change of its backlight (event 0: of its brightness), handled at once. The level it
        names is never believed."""
        self.rig.port.notify(bytes([0x11, 1, self.dev.index(BL), event << 4]) + bytes([0, 7]).ljust(16, b"\0"))
        self.rig.engine._handle(self.rig.engine._queue.get(timeout=2.0))

    def shows(self, level):
        """Its brightness changes on the keyboard (its keys, the light around it): function 2 says ``level`` now."""
        self.dev.settings[BL]["info"][1] = level

    def reads(self):
        return len(packets(self.rig.port, self.dev, BL, 0))

    def hold_nth(self, nth, when=lambda request: request[0] is Backlight.read):
        """Hold back the ``nth`` request ``when`` picks from now on (see hold)."""
        count = [0]

        def picked(request):
            if when(request):
                count[0] += 1
                return count[0] == nth
            return False
        return self.hold(picked)

    def due_read(self):
        """A read of the settings comes due, as after a report or RECOVERY_RETRY, and is started."""
        self.rig.kb._settings_at = time.monotonic()
        self.rig.kb.tick(time.monotonic())

    def retried(self, start):
        """A read comes due once RECOVERY_RETRY is over: not at once, and never not at all."""
        at = self.rig.kb._settings_at
        self.assertTrue(start + keyboard.RECOVERY_RETRY <= at <= time.monotonic() + keyboard.RECOVERY_RETRY, at)

    def owned(self):
        """(level settled, reports, of them settled, a change of this program's unsettled)"""
        kb = self.rig.kb
        return kb._level_seen, kb._level_event, kb._level_handled, kb._level_own

    def notice(self):
        return self.rig.state()["notice"]

    def told(self, level, seq=1):
        self.assertEqual(self.notice(), {"seq": seq, "kind": "backlight", "level": level, "top": 7})
        self.assertEqual(self.rig.kb._notice_seq, seq)


class BacklightChange(BacklightBase):
    def test_read_when_found_then_changed_and_read_back(self):
        rig = self.make()
        self.assertEqual(control(rig, "backlight"), {
            "value": {"on": True, "power_save": False, "mode": "manual", "level": 4, "hands_out": 60, "hands_in": 30,
                      "powered": 300},
            "fields": ALL_FIELDS, "can": ASKABLE, "levels": 8, "shown_level": 4, "writable": True, "busy": False,
            "outcome": None})
        reads = self.reads()
        self.ask("backlight", {"level": 6})
        self.assertEqual(self.setters(BL), [bytes.fromhex("0119ff060c0006003c000504")])
        self.assertEqual(self.reads(), reads + 2)  # fresh before, read back after
        self.assertEqual(control(rig, "backlight")["value"]["level"], 6)
        self.assertIsNone(control(rig, "backlight")["outcome"])
        self.ask("backlight", {"on": False, "powered": 600})
        self.assertEqual(self.setters(BL)[-1], bytes.fromhex("0019ff060c00060078000504"))
        self.assertEqual(control(rig, "backlight")["value"], dict(control(rig, "backlight")["value"], on=False, powered=600))
        self.assertEqual(self.dev.settings[BL]["config"][2:5], bytearray([0x3D, 0x7F, 0x00]))  # never sent

    def test_only_the_fields_asked_for_decide_how_it_went(self):
        def elsewhere(kb):  # meanwhile: another brightness shown, other effects, another timer it was not asked about
            kb.settings[BL]["info"][1] = 2
            kb.settings[BL]["config"][3] = 0x01
            kb.settings[BL]["config"][8:10] = struct.pack("<H", 9)
        kb = backlight_keyboard()
        on_setter(kb, elsewhere)
        rig = self.make(kb)
        self.ask("backlight", {"hands_out": 30})
        self.assertEqual(self.setters(BL), [bytes.fromhex("0119ff04060006003c000504")])  # nothing sent back
        shown = control(rig, "backlight")
        self.assertEqual((shown["outcome"], shown["value"]["hands_out"], shown["value"]["hands_in"], shown["shown_level"]),
                         (None, 30, 45, 2))

    def test_what_it_had_goes_back_laid_over_what_it_has_now(self):
        took = bytes.fromhex("0119ff06070006003c000504")  # 35 s, not 30, and its keys moved the level to 6 meanwhile
        for taken, outcome, last in (([took], "restored", 60), ([took, took], "unconfirmed", 35)):
            with self.subTest(outcome):
                kb = backlight_keyboard()
                kb.taken[BL] = list(taken)
                rig = self.make(kb)
                self.ask("backlight", {"hands_out": 30})
                self.assertEqual(self.setters(BL), [bytes.fromhex("0119ff04060006003c000504"),
                                                    bytes.fromhex("0119ff060c0006003c000504")])  # the level as it is now
                self.assertEqual(control(rig, "backlight")["outcome"], outcome)
                self.assertEqual(control(rig, "backlight")["value"]["hands_out"], last)
                self.assertEqual(control(rig, "backlight")["value"]["level"], 6)
                rig.stop()

    def test_nothing_goes_back_where_the_read_back_does_not_let_it(self):
        def keys(kb):  # took 35 s, not 30, and is set with its keys now: only Automatic or Fixed may be sent
            kb.settings[BL]["config"][1], kb.settings[BL]["config"][6:8] = 0x11, b"\x07\x00"

        def newer(kb):  # took 35 s, and says a version changes are not sent to
            kb.versions[BL], kb.settings[BL]["config"][6:8] = 4, b"\x07\x00"

        def no_timer(kb):  # that timer no longer known
            kb.settings[BL]["config"][6:8] = b"\x00\x00"

        for change in (keys, newer, no_timer):
            with self.subTest(change.__name__):
                kb = backlight_keyboard()
                on_setter(kb, change)
                rig = self.make(kb)
                self.ask("backlight", {"hands_out": 30})
                self.assertEqual(len(self.setters(BL)), 1)
                self.assertEqual(control(rig, "backlight")["outcome"], "unconfirmed")
                rig.stop()

    def test_so_already_ignored_refused_or_unanswered(self):
        rig = self.make()
        self.ask("backlight", {"level": 4, "on": True})
        self.assertEqual(self.setters(BL), [])
        self.dev.ignore_writes = True
        self.ask("backlight", {"level": 2})
        self.assertEqual(control(rig, "backlight")["outcome"], "not-applied")
        self.dev.ignore_writes = False
        self.dev.failing[(BL, 1)] = 5  # refused: the read back says what it has
        self.ask("backlight", {"level": 2})
        self.assertEqual(control(rig, "backlight")["outcome"], "not-applied")
        del self.dev.failing[(BL, 1)]
        self.assertEqual(len(self.setters(BL)), 1)  # the refused one never reached the fake's list
        rig.stop()
        kb = backlight_keyboard()
        on_setter(kb, lambda kb: setattr(kb, "silent", True))
        rig = self.make(kb)
        start = time.monotonic()
        self.ask("backlight", {"level": 2})
        self.assertEqual((control(rig, "backlight")["outcome"], control(rig, "backlight")["value"]), ("unconfirmed", None))
        self.assertEqual(len(self.setters(BL)), 1)  # never a second, blind one
        self.retried(start)
        kb.silent = False
        self.due_read()
        rig.settle()
        self.assertEqual(control(rig, "backlight")["value"]["level"], 2)
        self.assertEqual(len(self.setters(BL)), 1)

    def test_changes_asked_before_the_setter_go_as_one_and_after_it_on_a_fresh_read(self):
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is Backlight.read)
        self.ask("backlight", {"level": 3}, settle=False)
        self.assertTrue(self.reached(entered))
        self.ask("backlight", {"level": 5}, settle=False)
        self.ask("backlight", {"level": 4, "hands_in": 10}, settle=False)
        self.ask("backlight", {"level": 6}, settle=False)
        self.assertEqual(rig.kb._pending, {"backlight": {"level": 6, "hands_in": 10}})
        release.set()
        rig.settle()
        self.assertEqual(self.setters(BL), [bytes.fromhex("0119ff060c0002003c000504")])
        rig.stop()
        rig = self.make(backlight_keyboard(bl_config(options=0x09)))
        entered, release = self.hold(lambda request: request[0] is Backlight.read)
        self.ask("backlight", {"mode": "manual"}, settle=False)
        self.assertTrue(self.reached(entered))
        self.ask("backlight", {"level": 5}, settle=False)
        release.set()
        rig.settle()
        self.assertEqual(self.setters(BL), [bytes.fromhex("0119ff050c0006003c000504")])
        entered, release = self.hold(lambda request: request[0] is Backlight.write)
        on_setter(self.dev, lambda kb: kb.settings[BL]["config"].__setitem__(slice(12, 14), b"\x09\x09"))
        self.ask("backlight", {"level": 2}, settle=False)
        self.assertTrue(self.reached(entered))
        self.ask("backlight", {"hands_in": 15}, settle=False)  # too late for the first: a change of its own
        reads = self.reads()
        release.set()
        rig.settle()
        self.assertEqual(self.setters(BL)[1:], [bytes.fromhex("0119ff020c0006003c000504"),
                                                bytes.fromhex("0119ff020c0003003c000909")])
        self.assertEqual(self.reads(), reads + 3)  # read back, then afresh before the second

    def test_what_is_not_a_change_of_its_backlight_is_dropped(self):
        rig = self.make(backlight_keyboard(bl_config(options=0x11)))  # set with its keys
        for value in ({"level": 2.0}, {"power_save": False}, {"mode": "keys"}, {}, [1], None):
            rig.kb.set_setting("backlight", value)
        self.assertEqual(rig.kb._pending, {})
        self.ask("backlight", {"on": False})
        self.ask("backlight", {"hands_out": 30})
        self.ask("backlight", {"level": 2})
        self.assertEqual(control(rig, "backlight")["outcome"], "unsupported")
        self.assertEqual(self.setters(BL), [])
        self.ask("backlight", {"mode": "auto"})
        self.assertEqual(self.setters(BL), [bytes.fromhex("0109ff040c0006003c000504")])
        rig.stop()
        for kb in (backlight_keyboard(version=1), backlight_keyboard(bytes([2]) + bl_config()[1:])):
            rig = self.make(kb)
            self.assertFalse(control(rig, "backlight")["writable"])
            self.ask("backlight", {"on": False})
            self.assertEqual(kb.setting_writes, [])
            rig.stop()
        rig = self.make(keyboard_device())  # no backlight
        self.ask("backlight", {"on": False})
        self.assertNotIn("backlight", rig.state()["controls"])


class BacklightGuarded(BacklightBase):
    def rivals(self, *names):
        self.rig.rivals = list(names)
        self.rig.engine._look_for_rivals(time.monotonic())

    def test_never_where_no_key_may_be_changed_and_not_later_either(self):
        for kwargs, reason in ((dict(rivals=["Solaar"]), "rivals"), (dict(journal="not a journal"), "journal")):
            with self.subTest(reason):
                rig = self.make(**kwargs)
                self.assertEqual(rig.state()["reason"], reason)
                self.assertFalse(control(rig, "backlight")["writable"])
                self.assertEqual(control(rig, "backlight")["value"]["level"], 4)  # read all the same
                self.ask("backlight", {"level": 2})
                self.assertEqual((rig.kb._pending, self.setters(BL)), ({}, []))
                rig.stop()
        rig = self.make(backlight_keyboard(info=False))
        self.assertEqual(rig.state()["reason"], "identity")
        self.ask("backlight", {"level": 2})
        self.assertEqual(self.setters(BL), [])
        rig.stop()
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is Backlight.read)
        self.ask("backlight", {"level": 2}, settle=False)
        self.assertTrue(self.reached(entered))
        self.rivals("Solaar")  # seen during the read before the change
        self.assertEqual(rig.kb._pending, {})
        release.set()
        rig.settle()
        self.rivals()  # gone again: what was asked is not brought back
        rig.settle()
        self.assertEqual(self.setters(BL), [])
        self.assertTrue(control(rig, "backlight")["writable"])

    def test_a_setter_waiting_behind_the_mouse_is_called_off_once_other_software_is_seen(self):
        rig = self.make()
        mouse = rig.port.devices[2]
        entered, release = self.hold(lambda request: request[0] is Backlight.write)
        self.ask("backlight", {"level": 2}, settle=False)
        self.assertTrue(self.reached(entered))
        self.assertTrue(rig.kb._level_own)
        mouse.delay = 0.5
        foreground = threading.Thread(target=rig.transport().request, args=(2, 0, 1, bytes([0, 0, 1])))
        foreground.start()
        time.sleep(0.05)
        release.set()
        time.sleep(0.1)
        self.assertIs(rig.kb._asked[0], Backlight.write)
        self.rivals("Solaar")
        foreground.join()
        rig.settle()
        self.assertEqual(self.setters(BL), [])
        self.assertEqual(control(rig, "backlight"), dict(control(rig, "backlight"), writable=False, outcome="unconfirmed"))

    def test_other_software_after_the_setter_nothing_is_sent_back(self):
        kb = backlight_keyboard()
        kb.taken[BL] = [bytes.fromhex("0119ff030c0006003c000504")]  # took level 3, not 2
        rig = self.make(kb)
        entered, release = self.hold(lambda request: request[0] is Backlight.read and kb.setting_writes)
        self.ask("backlight", {"level": 2}, settle=False)
        self.assertTrue(self.reached(entered))
        self.rivals("Solaar")
        release.set()
        rig.settle()
        self.assertEqual(len(self.setters(BL)), 1)  # never the earlier level sent back
        self.assertEqual(control(rig, "backlight"), dict(control(rig, "backlight"), writable=False, outcome="unconfirmed"))
        self.assertEqual(control(rig, "backlight")["value"]["level"], 3)
        kb2 = backlight_keyboard()
        kb2.taken[BL] = [bytes.fromhex("0119ff030c0006003c000504")]
        rig.stop()
        rig = self.make(kb2)
        entered, release = self.hold(lambda request: request[0] is Backlight.write and kb2.setting_writes)
        self.ask("backlight", {"level": 2}, settle=False)
        self.assertTrue(self.reached(entered))  # the earlier level, waiting to go back ...
        self.rivals("Solaar")  # ... is never sent once other software is seen
        release.set()
        rig.settle()
        self.assertEqual(len(kb2.setting_writes), 1)

    def test_gone_replaced_or_exiting_never_sends_or_sends_back(self):
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is Backlight.read)
        self.ask("backlight", {"level": 2}, settle=False)
        self.assertTrue(self.reached(entered))
        rig.link(1, False)
        release.set()
        self.drain()
        rig.link(1, True)
        rig.settle()
        self.assertEqual(self.dev.setting_writes, [])
        rig.stop()
        kb = backlight_keyboard()
        kb.taken[BL] = [bytes.fromhex("0119ff030c0006003c000504")]
        rig = self.make(kb)
        entered, release = self.hold(lambda request: request[0] is Backlight.read and kb.setting_writes)
        self.ask("backlight", {"level": 2}, settle=False)
        self.assertTrue(self.reached(entered))
        rig.link(1, False)
        release.set()
        self.drain()
        self.assertEqual(len(kb.setting_writes), 1)  # nothing sent back
        rig.stop()
        rig = self.make(backlight_keyboard(), keys={"0x0f01": COPY})
        rig.keys_down(1, self.dev, 0x0F01)
        self.ask("backlight", {"level": 2})
        rig.link(1, False)
        other = rig.port.devices[1] = backlight_keyboard(unit=UNIT_B)
        rig.link(1, True)
        rig.settle()
        rig.keys_down(1, other)
        rig.settle()
        self.assertEqual(other.setting_writes, [])
        rig.stop()
        rig = self.make(backlight_keyboard(), keys={"0x0f01": COPY})
        rig.keys_down(1, self.dev, 0x0F01)
        self.ask("backlight", {"level": 2})
        start = time.monotonic()
        rig.kb.shutdown()
        self.assertLess(time.monotonic() - start, keyboard.SHUTDOWN_BUDGET)
        self.assertEqual(self.dev.setting_writes, [])

    def test_easy_switch_goes_first_and_a_key_down_holds_the_change(self):
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is Backlight.read)
        self.ask("backlight", {"level": 2}, settle=False)
        self.assertTrue(self.reached(entered))
        rig.kb.device_action({"type": "host", "host": 1}, "press")
        self.assertEqual(rig.kb._pending, {"backlight": {"level": 2}})
        release.set()
        rig.settle()
        self.assertEqual(self.dev.switched, [1])
        self.assertEqual(self.setters(BL), [])  # not while the keyboard may be on its way
        rig.stop()
        rig = self.make(keys={"0x0f01": COPY})
        rig.keys_down(1, self.dev, 0x0F01)
        self.ask("backlight", {"level": 2})
        self.assertEqual(self.setters(BL), [])
        rig.keys_down(1, self.dev)
        rig.settle()
        self.assertEqual(self.setters(BL), [bytes.fromhex("0119ff020c0006003c000504")])

    def test_the_mouse_is_heard_while_the_backlight_is_slow(self):
        rig = self.make()
        mouse = rig.port.devices[2]
        rig.engine.cfg["buttons"]["back"] = actions.keys("LEFTSHIFT")
        self.dev.delays = dict.fromkeys((ROOT, BL), 0.2)
        for start_it in (self.due_read, lambda: self.ask("backlight", {"level": 2}, settle=False)):
            start_it()
            for _ in range(3):
                time.sleep(0.05)
                self.assertIsNotNone(rig.kb._asked)  # the keyboard is still being waited for ...
                rig.mouse_actions.ran.clear()
                start = time.monotonic()
                rig.port.notify(bytes([0x11, 2, mouse.index(device.REPROG_CONTROLS_V4), 0])
                                + struct.pack("!HHHH", 0x0053, 0, 0, 0).ljust(16, b"\0"))
                while not rig.mouse_actions.ran:  # ... and the mouse is heard meanwhile
                    rig.engine._handle(rig.engine._queue.get(timeout=1.0))
                self.assertLess(time.monotonic() - start, 0.1)
                rig.port.notify(bytes([0x11, 2, mouse.index(device.REPROG_CONTROLS_V4), 0]) + bytes(16))
            rig.settle()
        self.assertEqual(control(rig, "backlight")["value"]["level"], 2)
        index = self.dev.index(BL)
        writers = {w for p, w in zip(rig.port.written, rig.port.writers)
                   if p[1] == 1 and (p[2] == index or p[2] == 0 and p[4:6] == b"\x19\x82")}
        self.assertEqual(writers, {"keyboard"})


class BacklightKept(BacklightBase):
    def test_reported_changes_are_read_not_believed_and_a_burst_is_one_read(self):
        rig = self.make()
        reads = self.reads()
        self.shows(2)
        self.dev.settings[BL]["config"][5] = 2
        self.report()
        rig.settle()
        self.assertEqual(self.reads(), reads + 1)
        self.assertEqual((control(rig, "backlight")["value"]["level"], control(rig, "backlight")["shown_level"]), (2, 2))
        self.report(event=1)  # another of its reports: read again, no change of brightness said
        self.assertEqual(rig.kb._level_event, 1)
        rig.settle()
        self.assertEqual(self.reads(), reads + 2)
        entered, release = self.hold(lambda request: request[0] is Backlight.read)
        for _ in range(5):
            self.report()
            if not entered.is_set():
                self.assertTrue(self.reached(entered))
        release.set()
        rig.settle()
        self.assertEqual(self.reads(), reads + 4)  # the one under way, and one more after the burst
        self.assertEqual(self.setters(BL), [])

    def test_nothing_is_put_back_or_kept_on_this_computer(self):
        with mock.patch.object(config, "save", side_effect=AssertionError("saved")), \
                mock.patch.object(config, "write_durable", side_effect=AssertionError("written")):
            rig = self.make()
            before, exported = copy.deepcopy(rig.engine.cfg), config.export_data(rig.engine.cfg)
            self.ask("backlight", {"level": 6, "hands_in": 3600})
            writes = list(self.dev.setting_writes)
            self.dev.settings[BL]["config"][:] = bl_config(level=1)
            rig.link(1, False)
            rig.link(1, True)
            rig.settle()
            new = copy.deepcopy(rig.engine.cfg)
            new["keyboard"]["keys"] = {"0x0f01": COPY}
            rig.engine._apply_config(new)
            rig.kb.front_changed("kate")
            rig.settle()
            self.assertEqual(self.dev.setting_writes, writes)
            self.assertEqual(control(rig, "backlight")["value"]["level"], 1)  # what the keyboard says
            self.assertEqual(config.export_data(before), exported)
            for text in ("backlight", "hands_in", "3600"):
                self.assertNotIn(text, json.dumps(rig.engine.cfg))

    def test_state_is_plain_json_and_logs_tell_no_bytes_or_levels(self):
        def plain(value):
            if isinstance(value, dict):
                return all(type(k) is str and plain(v) for k, v in value.items())
            if isinstance(value, list):
                return all(plain(v) for v in value)
            return value is None or type(value) in (str, int, bool)
        with self.assertLogs("mxoptions", logging.DEBUG) as logs:
            kb = backlight_keyboard()
            kb.taken[BL] = [bytes.fromhex("0119ff030c0006003c000504")] * 2
            rig = self.make(kb)
            rig.engine.cfg["keyboard"]["notices"] = True
            self.ask("backlight", {"level": 6})
            kb.ignore_writes = True
            self.ask("backlight", {"hands_out": 5})
            self.shows(5)
            self.report()
            rig.settle()
            rig.kb.set_setting("backlight", {"level": "x"})
        state = rig.state()
        self.assertTrue(plain(state["controls"]) and plain(state["notice"]))
        self.assertEqual(json.loads(json.dumps(state["controls"])), state["controls"])
        self.assertEqual(set(state["controls"]["backlight"]), {"value", "fields", "can", "levels", "shown_level",
                                                               "writable", "busy", "outcome"})
        text, shown = "\n".join(logs.output), json.dumps(state)
        for private in (identity(UNIT_A), UNIT_A.hex(), "5e015e02", rig.kb.link, "raw", "version", "\\x", "0119ff"):
            self.assertNotIn(private, shown)
        lines = [line for line in logs.output if "setting" in line]
        self.assertTrue(lines)
        for line in lines:
            self.assertNotRegex(line, r"\d|\\x|level")
        self.assertIn("keyboard setting backlight: the keyboard did not take the change", text)
        self.assertNotIn("0119ff", text)


class BrightnessNotice(BacklightBase):
    """A brightness the keyboard reports changed is told once, with its level, where the settings ask for it."""

    def test_none_for_what_is_found_and_one_for_a_reported_change_in_each_mode_but_automatic(self):
        for options, told in ((0x19, True), (0x01, True), (0x11, True), (0x09, False), (0x29, False)):
            with self.subTest(options=hex(options)):
                rig = self.make(backlight_keyboard(bl_config(options=options)))
                self.assertEqual(self.owned(), (4 if options != 0x29 else None, 0, 0, False))  # found: no notice
                self.assertIsNone(self.notice())
                self.shows(6)
                self.report()
                rig.settle()
                if told:
                    self.told(6)
                    self.assertEqual(self.owned(), (6, 1, 1, False))
                else:  # Automatic: the light around it; a mode it does not know: compared once it is known
                    self.assertIsNone(self.notice())
                self.assertEqual(self.setters(BL), [])
                rig.stop()

    def test_none_for_the_same_level_off_or_with_notices_off_and_none_later_for_those(self):
        rig = self.make()
        self.report()
        rig.settle()
        self.assertIsNone(self.notice())
        self.assertEqual(self.owned(), (4, 1, 1, False))
        self.dev.settings[BL]["config"][0] = 0  # turned off on the keyboard
        self.shows(0)
        self.report()
        rig.settle()
        self.assertIsNone(self.notice())
        self.assertEqual(self.owned(), (0, 2, 2, False))  # settled without a word
        self.dev.settings[BL]["config"][0] = 1
        rig.engine.cfg["keyboard"]["notices"] = False
        self.shows(3)
        self.report()
        rig.settle()
        self.assertIsNone(self.notice())
        self.assertEqual(self.owned(), (3, 3, 3, False))
        new = copy.deepcopy(rig.engine.cfg)
        new["keyboard"]["notices"] = True
        reads = self.reads()
        rig.engine._apply_config(new)  # turned on: nothing said of what was settled before, nothing read or sent
        rig.settle()
        self.assertEqual((self.notice(), self.reads(), self.setters(BL)), (None, reads, []))
        self.shows(5)
        self.report()
        rig.settle()
        self.told(5)

    def test_a_burst_is_told_once_with_the_level_it_ends_at(self):
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is Backlight.read)
        for level in (5, 6, 7, 6, 2):
            self.shows(level)
            self.report()
            if not entered.is_set():
                self.assertTrue(self.reached(entered))
        release.set()
        rig.settle()
        self.told(2)
        for _ in range(3):  # the same level reported again and again
            self.report()
            rig.settle()
        self.told(2)

    def test_a_new_connection_starts_afresh_and_the_number_goes_on(self):
        rig = self.make()
        self.shows(6)
        self.report()
        rig.settle()
        self.told(6)
        entered, release = self.hold(lambda request: request[0] is Backlight.read)
        self.shows(1)
        self.report()
        self.assertTrue(self.reached(entered))
        rig.link(1, False)  # gone while that is read: its answer is not taken
        self.assertIsNone(self.notice())
        self.assertEqual(self.owned(), (None, 0, 0, False))
        release.set()
        self.drain()
        self.assertEqual(rig.kb._notice_seq, 1)
        for kb in (self.dev, backlight_keyboard(levels=bl_info(shown=1), unit=UNIT_B)):
            rig.port.devices[1] = kb
            rig.link(1, True)
            rig.settle()
            self.assertIsNone(self.notice())  # found: no notice, whatever it shows
            self.assertEqual(rig.kb._level_seen, 1)
            kb.settings[BL]["info"][1] = 3
            rig.note(1, kb, BL, b"\x00\x07")
            rig.settle()
            self.assertEqual(self.notice()["seq"], rig.kb._notice_seq)
            self.assertEqual(self.notice()["level"], 3)
            rig.link(1, False)
            self.assertIsNone(self.notice())
        self.assertEqual(rig.kb._notice_seq, 3)

    def test_none_for_a_change_it_did_not_report(self):
        rig = self.make()
        self.shows(6)
        self.due_read()  # read for another reason: what it says now is settled, never told
        rig.settle()
        self.assertIsNone(self.notice())
        self.assertEqual(self.owned(), (6, 0, 0, False))

    def test_each_notice_is_said_once_and_only_while_connected(self):
        found = {"connected": True, "notice": {"seq": 2, "kind": "backlight", "level": 3, "top": 7}}
        self.assertEqual(keyboard.new_notice(0, None), (0, None))
        self.assertEqual(keyboard.new_notice(0, {"connected": True, "notice": None}), (0, None))
        self.assertEqual(keyboard.new_notice(0, found), (2, found["notice"]))
        self.assertIsNot(keyboard.new_notice(0, found)[1], found["notice"])  # a copy
        self.assertEqual(keyboard.new_notice(2, found), (2, None))
        self.assertEqual(keyboard.new_notice(1, dict(found, connected=False)), (1, None))
        self.assertEqual(keyboard.new_notice(2, dict(found, notice=dict(found["notice"], seq=3, level=0))),
                         (3, dict(found["notice"], seq=3, level=0)))


class BrightnessReports(BacklightBase):
    """A report of a change of brightness is settled only by a read that says the level for certain, made wholly after
    it: never by one under way when it came, one that failed or was called off, or another setting's change."""

    def test_a_report_during_a_read_is_settled_by_the_read_after_it(self):
        rig = self.make()
        first, release_first = self.hold_nth(1)
        second, release_second = self.hold_nth(2)
        self.due_read()  # a read nothing was reported for
        self.assertTrue(self.reached(first))
        self.shows(6)
        self.report()  # while it is read: the answer says 6 already
        release_first.set()
        self.assertTrue(self.reached(second))  # one read more, due
        self.assertEqual(control(rig, "backlight")["shown_level"], 6)
        self.assertEqual(self.owned(), (4, 1, 0, False))
        self.assertIsNone(self.notice())
        release_second.set()
        rig.settle()
        self.told(6)
        self.assertEqual(self.owned(), (6, 1, 1, False))
        self.due_read()
        rig.settle()
        self.told(6)

    def test_a_read_that_fails_settles_nothing_and_is_not_tried_at_once(self):
        rig = self.make()
        self.shows(6)
        self.dev.silent = True
        start = time.monotonic()
        self.report()
        rig.settle()
        self.assertEqual(self.owned(), (4, 1, 0, False))
        self.retried(start)
        self.assertIsNone(rig.kb._task)
        self.assertGreater(rig.kb.due(time.monotonic()), keyboard.RECOVERY_RETRY - 5.0)
        self.dev.silent = False
        self.due_read()
        rig.settle()
        self.told(6)
        self.assertEqual(self.owned(), (6, 1, 1, False))
        self.due_read()
        rig.settle()
        self.told(6)

    def test_a_read_called_off_by_the_keys_or_easy_switch_settles_nothing(self):
        for wish in ("configure", "host"):
            with self.subTest(wish):
                rig = self.make()
                entered, release = self.hold(lambda request: request[0] is Backlight.read)
                self.shows(6)
                self.report()
                self.assertTrue(self.reached(entered))
                if wish == "configure":
                    rig.kb.configure()
                else:
                    rig.kb.device_action({"type": "host", "host": 1}, "press")
                    self.assertEqual(rig.kb._task.__name__, "_switch")
                release.set()
                rig.settle()
                if wish == "host":  # it stayed: once the switch's grace is over, it is read again
                    self.assertEqual(self.dev.switched, [1])
                    self.assertEqual(self.owned(), (4, 1, 0, False))
                    rig.kb._leaving = 0.0
                    rig.kb._next_recovery = time.monotonic()
                    rig.kb.tick(time.monotonic())
                    rig.settle()
                self.told(6)
                self.assertEqual(self.owned(), (6, 1, 1, False))
                rig.stop()
        rig = self.make()
        self.shows(6)
        self.dev.silent = True
        self.report()
        rig.settle()
        rig.link(1, False)  # gone for real: nothing of it is kept
        self.dev.silent = False
        rig.link(1, True)
        rig.settle()
        self.assertEqual(self.owned(), (6, 0, 0, False))
        self.assertIsNone(self.notice())

    def test_a_report_during_another_setting_s_change_is_told_after_it(self):
        for name, value, codec in (("fn", True, FnCodec), ("disable", {"caps_lock": True}, DisableCodec),
                                   ("platform", 2, PlatformCodec)):
            with self.subTest(name):
                kb = backlight_keyboard(hosts=(3, 0), settings={
                    device.HOSTS_INFO: 0, HOSTS: {0: [1, 1], 1: [0, 1], 2: [1, 1]},
                    DK: {"caps": 0x1F, "current": 0}, MP: multi()})
                rig = self.make(kb)
                entered, release = self.hold(lambda request, codec=codec: request[0] is codec.write)
                self.ask(name, value, settle=False)
                self.assertTrue(self.reached(entered))
                self.shows(6)
                self.report()
                release.set()
                rig.settle()
                self.assertEqual(len(kb.setting_writes), 1)
                self.told(6)
                self.assertEqual(self.owned(), (6, 1, 1, False))
                rig.stop()

    def test_what_is_not_said_for_certain_keeps_the_report_until_a_read_that_is(self):
        def unknown_mode(kb):
            kb.settings[BL]["config"][1] = 0x39

        def known_mode(kb):
            kb.settings[BL]["config"][1] = 0x19

        def no_levels(kb):
            kb.failing[(BL, 2)] = 5

        def levels(kb):
            kb.failing.pop((BL, 2), None)

        def too_many(kb):
            kb.settings[BL]["info"][0] = 17

        def eight(kb):
            kb.settings[BL]["info"][0] = 8

        for spoil, mend in ((unknown_mode, known_mode), (no_levels, levels), (too_many, eight)):
            with self.subTest(spoil.__name__):
                rig = self.make()
                spoil(self.dev)
                self.shows(6)
                start = time.monotonic()
                self.report()
                rig.settle()
                self.assertEqual(self.owned(), (4, 1, 0, False))
                self.assertIsNone(self.notice())
                self.retried(start)  # not at once
                self.due_read()
                rig.settle()
                self.assertEqual(self.owned(), (4, 1, 0, False))
                mend(self.dev)
                self.due_read()
                rig.settle()
                self.told(6)  # compared with what was settled before
                self.assertEqual(self.setters(BL), [])
                rig.stop()


class BrightnessReadFails(BacklightBase):
    """A read that fails (an answer that does not fit, an error of the feature) leaves a report, or a change of this
    program's, to be settled by a read RECOVERY_RETRY later: as time goes on, never stranded, never a setter again."""

    def malformed(self):
        config, was = self.dev.settings[BL]["config"], self.dev.settings[BL]["config"][0]
        config[0] = 2  # neither on nor off

        def mend():
            config[0] = was
        return mend

    def refused(self):
        self.dev.failing[(BL, 0)] = 5

        def mend():
            del self.dev.failing[(BL, 0)]
        return mend

    def later(self, clock):
        """RECOVERY_RETRY goes by, and the keyboard's clock ticks."""
        clock.skip(keyboard.RECOVERY_RETRY + 1)
        self.rig.kb.tick(time.monotonic())
        self.rig.settle()

    def test_a_report_read_wrongly_is_settled_later(self):
        for fail in (self.malformed, self.refused):
            with self.subTest(fail.__name__):
                rig = self.make()
                clock = Later(self)
                self.shows(6)
                mend = fail()
                start = time.monotonic()
                self.report()
                rig.settle()
                self.assertEqual(self.owned(), (4, 1, 0, False))
                self.assertIsNone(self.notice())
                self.retried(start)
                self.later(clock)  # still failing: tried again later still
                self.assertEqual(self.owned(), (4, 1, 0, False))
                self.retried(start + keyboard.RECOVERY_RETRY + 1)
                mend()
                self.later(clock)
                self.told(6)
                self.assertEqual(self.owned(), (6, 1, 1, False))
                self.assertEqual(rig.kb._settings_at, float("inf"))  # settled: nothing more to read
                self.assertEqual(self.setters(BL), [])
                rig.stop()

    def test_its_own_change_read_wrongly_is_settled_later(self):
        for fail in (self.malformed, self.refused):
            with self.subTest(fail.__name__):
                rig = self.make()
                clock = Later(self)
                self.dev.settings[BL]["info"][0] = 17  # the level not said: the read back settles nothing
                start = time.monotonic()
                self.ask("backlight", {"on": False})
                self.assertEqual(self.owned(), (4, 0, 0, True))
                self.retried(start)
                self.dev.settings[BL]["info"][0] = 8
                mend = fail()
                self.later(clock)
                self.assertEqual(self.owned(), (4, 0, 0, True))
                self.retried(start + keyboard.RECOVERY_RETRY + 1)
                mend()
                self.later(clock)
                self.assertEqual(self.owned(), (4, 0, 0, False))
                self.assertIsNone(self.notice())
                self.assertEqual(rig.kb._settings_at, float("inf"))
                self.assertEqual(len(self.setters(BL)), 1)  # never sent again
                self.dev.settings[BL]["config"][0] = 1  # on again, brighter, on the keyboard after it: told
                self.shows(6)
                self.report()
                rig.settle()
                self.told(6)
                rig.stop()


class BrightnessOwnChange(BacklightBase):
    """What the keyboard reports while this program changes its backlight is settled without a word, by the first
    read after the change that says the level for certain; it never holds the change up, nor sends it again."""

    def test_a_report_during_the_setter_is_settled_by_the_read_back(self):
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is Backlight.write)
        self.ask("backlight", {"level": 6}, settle=False)
        self.assertTrue(self.reached(entered))
        self.assertTrue(rig.kb._level_own)
        self.report()
        release.set()
        rig.settle()
        self.assertIsNone(self.notice())
        self.assertEqual(self.owned(), (6, 1, 1, False))
        self.assertEqual(len(self.setters(BL)), 1)

    def test_a_report_during_the_read_back_waits_for_one_read_more(self):
        rig = self.make()
        after = lambda request: request[0] is Backlight.read and self.dev.setting_writes  # noqa: E731
        first, release_first = self.hold_nth(1, after)
        second, release_second = self.hold_nth(2, after)
        self.ask("backlight", {"level": 6}, settle=False)
        self.assertTrue(self.reached(first))
        self.report()  # the answer says 6 already
        release_first.set()
        self.assertTrue(self.reached(second))
        self.assertEqual(self.owned(), (4, 1, 0, True))
        self.assertIsNone(control(rig, "backlight")["outcome"])  # the change itself is confirmed
        release_second.set()
        rig.settle()
        self.assertIsNone(self.notice())
        self.assertEqual(self.owned(), (6, 1, 1, False))
        self.report()  # the same level later: nothing
        rig.settle()
        self.assertIsNone(self.notice())
        self.shows(5)  # another one later: told
        self.report()
        rig.settle()
        self.told(5)

    def test_no_read_back_keeps_it_unsettled_until_a_read_later_and_nothing_is_sent_again(self):
        kb = backlight_keyboard()
        muted_once(kb)
        rig = self.make(kb)
        start = time.monotonic()
        self.ask("backlight", {"level": 6})
        self.assertEqual(control(rig, "backlight")["outcome"], "unconfirmed")
        self.assertEqual(self.owned(), (4, 0, 0, True))
        self.retried(start)
        self.report()  # reported meanwhile: still this change, as far as can be told
        rig.settle()
        self.assertEqual(self.owned(), (6, 1, 1, False))
        self.assertIsNone(self.notice())
        self.assertEqual(len(self.setters(BL)), 1)

    def test_the_read_before_sending_back_settles_nothing(self):
        kb = backlight_keyboard()
        kb.taken[BL] = [bytes.fromhex("0119ff020c0006003c000504")]  # took level 2, not 6
        rig = self.make(kb)
        entered, release = self.hold(lambda request: request[0] is Backlight.write and kb.setting_writes)
        self.ask("backlight", {"level": 6}, settle=False)
        self.assertTrue(self.reached(entered))  # level 4 about to go back
        self.assertEqual(self.owned(), (4, 0, 0, True))
        self.assertEqual(control(rig, "backlight")["shown_level"], 2)
        release.set()
        rig.settle()
        self.assertEqual(control(rig, "backlight")["outcome"], "restored")
        self.assertEqual(self.owned(), (4, 0, 0, False))
        self.assertIsNone(self.notice())
        self.assertEqual(self.setters(BL), [bytes.fromhex("0119ff060c0006003c000504"),
                                            bytes.fromhex("0119ff040c0006003c000504")])

    def test_called_off_after_the_setter_it_is_settled_by_the_read_that_follows(self):
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is Backlight.read and self.dev.setting_writes)
        self.ask("backlight", {"level": 6}, settle=False)
        self.assertTrue(self.reached(entered))
        self.rig.rivals = ["Solaar"]
        rig.engine._look_for_rivals(time.monotonic())
        self.assertTrue(rig.kb._level_own)
        release.set()
        rig.settle()
        self.assertEqual(control(rig, "backlight")["outcome"], "unconfirmed")
        self.assertEqual(self.owned(), (6, 0, 0, False))
        self.assertIsNone(self.notice())
        self.assertEqual(len(self.setters(BL)), 1)

    def test_a_change_on_the_keyboard_while_this_one_is_unsettled_goes_unsaid(self):
        rig = self.make()
        entered, release = self.hold(lambda request: request[0] is Backlight.read and self.dev.setting_writes)
        self.ask("backlight", {"hands_out": 30}, settle=False)
        self.assertTrue(self.reached(entered))
        self.shows(2)  # its keys, meanwhile: cannot be told apart from this program's change
        self.report()
        release.set()
        rig.settle()
        self.assertIsNone(control(rig, "backlight")["outcome"])
        self.assertIsNone(self.notice())
        self.assertEqual(self.owned(), (2, 1, 1, False))
        self.report()  # once settled, the same level says nothing, and another one is told
        rig.settle()
        self.assertIsNone(self.notice())
        self.shows(3)
        self.report()
        rig.settle()
        self.told(3)


class BrightnessBeforeTheSetter(BacklightBase):
    """A report while the backlight is read before its setter gets one read more, so that it can still be told."""

    def test_reported_during_the_read_before_it_is_told_before_the_setter_goes(self):
        rig = self.make()
        first, release_first = self.hold_nth(1)
        second, release_second = self.hold_nth(2)
        sending, release_sending = self.hold(lambda request: request[0] is Backlight.write)
        reads = self.reads()
        self.ask("backlight", {"hands_out": 30}, settle=False)
        self.assertTrue(self.reached(first))
        self.shows(6)
        self.report()
        release_first.set()
        self.assertTrue(self.reached(second))  # one read more
        self.assertIsNone(self.notice())
        self.dev.settings[BL]["config"][5] = 6  # what only that one sees
        release_second.set()
        self.assertTrue(self.reached(sending))
        self.told(6)  # before the setter goes
        self.assertEqual(self.reads(), reads + 2)
        self.assertEqual(self.dev.setting_writes, [])
        release_sending.set()
        rig.settle()
        self.assertEqual(self.setters(BL), [bytes.fromhex("0119ff06060006003c000504")])  # laid over the last read
        self.told(6)
        self.assertEqual(self.owned(), (6, 1, 1, False))

    def test_still_not_certain_then_the_change_goes_and_its_read_back_settles_both(self):
        def no_levels(kb):
            kb.failing[(BL, 2)] = 5

        def unknown_mode(kb):
            kb.settings[BL]["config"][1] = 0x39

        for spoil in (no_levels, unknown_mode, None):
            with self.subTest(spoil.__name__ if spoil else "reported again"):
                rig = self.make()
                first, release_first = self.hold_nth(1)
                second, release_second = self.hold_nth(2)
                self.ask("backlight", {"on": False} if spoil is unknown_mode else {"hands_out": 30}, settle=False)
                self.assertTrue(self.reached(first))
                self.shows(6)
                self.report()
                release_first.set()
                self.assertTrue(self.reached(second))  # the one read more
                if spoil:
                    spoil(self.dev)
                else:
                    self.report()  # reported again while that is read
                release_second.set()
                for _ in range(20):
                    if self.dev.setting_writes:
                        break
                    self.drain()
                self.assertEqual(len(self.setters(BL)), 1)  # sent all the same, after two reads, never three
                if spoil is no_levels:
                    self.dev.failing.pop((BL, 2))
                rig.settle()
                if rig.kb._level_own:  # not settled by its read back: by the read after RECOVERY_RETRY
                    self.due_read()
                    rig.settle()
                self.assertIsNone(self.notice())  # 6 went unsaid: taken as part of this program's change
                self.assertEqual(self.owned()[1:], (rig.kb._level_event, rig.kb._level_event, False))
                self.assertEqual(len(self.setters(BL)), 1)
                rig.stop()

    def test_the_read_more_that_fails_sends_nothing(self):
        rig = self.make()
        first, release_first = self.hold_nth(1)
        second, release_second = self.hold_nth(2)
        start = time.monotonic()
        self.ask("backlight", {"hands_out": 30}, settle=False)
        self.assertTrue(self.reached(first))
        self.shows(6)
        self.report()
        release_first.set()
        self.assertTrue(self.reached(second))
        self.dev.silent = True
        release_second.set()
        rig.settle()
        self.assertEqual(self.setters(BL), [])
        self.assertEqual(control(rig, "backlight")["outcome"], "unknown")
        self.assertEqual((rig.kb._pending, self.owned()), ({}, (4, 1, 0, False)))
        self.retried(start)

    def test_without_notices_or_while_its_own_change_is_unsettled_no_read_more(self):
        for notices in (False, True):
            with self.subTest(notices=notices):
                kb = backlight_keyboard()
                if notices:  # its first change stays unsettled: no levels said as it is read back
                    on_setter(kb, lambda kb: kb.failing.__setitem__((BL, 2), 5))
                rig = self.make(kb, notices=notices)
                if notices:
                    self.ask("backlight", {"hands_in": 10})
                    self.assertTrue(rig.kb._level_own)
                    kb.failing.pop((BL, 2))
                entered, release = self.hold(lambda request: request[0] is Backlight.read)
                sending, release_sending = self.hold(lambda request: request[0] is Backlight.write)
                reads = self.reads()
                self.ask("backlight", {"hands_out": 30}, settle=False)
                self.assertTrue(self.reached(entered))
                self.shows(6)
                self.report()
                release.set()
                self.assertTrue(self.reached(sending))
                self.assertEqual(self.reads(), reads + 1)  # straight on to the setter
                release_sending.set()
                rig.settle()
                self.assertIsNone(self.notice())
                self.assertFalse(rig.kb._level_own)
                rig.stop()


class BrightnessNeverHoldsUpAChange(BacklightBase):
    """Whatever the brightness reads say, or do not, every change the keyboard lets be made goes, once."""

    def test_changes_go_while_the_levels_are_never_said(self):
        for spoil in ("error", "too many", "too few", "unknown mode"):
            for notices in (True, False):
                with self.subTest(spoil, notices=notices):
                    kb = backlight_keyboard()
                    rig = self.make(kb, notices=notices)
                    if spoil == "error":
                        kb.failing[(BL, 2)] = 5
                    elif spoil == "unknown mode":
                        kb.settings[BL]["config"][1] = 0x39
                    else:
                        kb.settings[BL]["info"][0] = 17 if spoil == "too many" else 1
                    self.shows(6)
                    self.report()  # waiting to be settled, and never is while this lasts
                    rig.settle()
                    self.assertEqual(self.owned()[:3], (4, 1, 0))
                    options = "39" if spoil == "unknown mode" else "19"
                    for request, params in (({"on": False}, "00" + options + "ff040c0006003c000504"),
                                            ({"on": True}, "01" + options + "ff040c0006003c000504"),
                                            ({"hands_out": 5}, "01" + options + "ff04010006003c000504"),
                                            ({"hands_in": 7200}, "01" + options + "ff040100a0053c000504"),
                                            ({"powered": 900}, "01" + options + "ff040100a005b4000504")):
                        reads, sent = self.reads(), len(self.setters(BL))
                        self.ask("backlight", request)
                        self.assertEqual(self.setters(BL)[sent:], [bytes.fromhex(params)])
                        self.assertIsNone(control(rig, "backlight")["outcome"])
                        self.assertEqual(self.reads(), reads + 2)  # no read for the notice's sake
                        self.assertEqual(rig.kb._pending, {})
                    self.ask("backlight", {"level": 2})  # where no level is known, or the mode is not: refused
                    self.assertEqual(control(rig, "backlight")["outcome"], "unsupported")
                    if spoil != "unknown mode":
                        self.ask("backlight", {"mode": "auto"})
                        self.assertIsNone(control(rig, "backlight")["outcome"])
                    else:
                        self.ask("backlight", {"mode": "auto"})
                        self.assertEqual(control(rig, "backlight")["outcome"], "unsupported")
                    sent = len(self.setters(BL))
                    rig.settle()
                    self.assertEqual(rig.kb._level_own, spoil != "unknown mode")  # with levels said, it settles
                    self.assertIsNone(self.notice())
                    self.assertEqual(len(self.setters(BL)), sent)  # nothing more of its own accord
                    rig.stop()

    def test_a_whole_read_that_fails_still_sends_nothing(self):
        kb = backlight_keyboard()
        rig = self.make(kb)
        kb.failing[(BL, 2)] = 5
        self.shows(6)
        self.report()
        rig.settle()
        kb.settings[BL]["config"][0] = 2  # not a reading that fits
        self.ask("backlight", {"on": False})
        self.assertEqual((control(rig, "backlight")["outcome"], self.setters(BL)), ("unknown", []))
        kb.settings[BL]["config"][0] = 1
        kb.silent = True
        self.ask("backlight", {"on": False})
        self.assertEqual((control(rig, "backlight")["outcome"], self.setters(BL)), ("unknown", []))
        self.assertEqual(self.owned(), (4, 1, 0, False))

    def test_unsettled_for_a_long_time_it_is_read_once_each_retry_and_never_sent_again(self):
        later = Later(self)
        kb = backlight_keyboard()
        rig = self.make(kb)
        kb.failing[(BL, 2)] = 5
        self.shows(6)
        self.report()
        rig.settle()
        self.ask("backlight", {"hands_out": 30})
        self.assertEqual(self.owned(), (4, 1, 0, True))
        for _ in range(4):
            reads = self.reads()
            later.skip(keyboard.RECOVERY_RETRY - 1)
            for _ in range(5):  # before the retry: nothing
                rig.kb.tick(time.monotonic())
                rig.settle()
            self.assertEqual(self.reads(), reads)
            later.skip(2)
            for _ in range(5):
                rig.kb.tick(time.monotonic())
                rig.settle()
            self.assertEqual(self.reads(), reads + 1)  # once each retry
        kb.failing.pop((BL, 2))
        later.skip(keyboard.RECOVERY_RETRY + 1)
        rig.kb.tick(time.monotonic())
        rig.settle()
        self.assertEqual(self.owned(), (4, 1, 1, False))  # its fixed level 4, as sent again with the timer
        self.assertIsNone(self.notice())  # 6 went unsaid with this program's change
        self.assertEqual(len(self.setters(BL)), 1)
        reads = self.reads()
        later.skip(keyboard.RECOVERY_RETRY * 3)
        rig.kb.tick(time.monotonic())
        rig.settle()
        self.assertEqual(self.reads(), reads)  # settled: no more reads
        self.shows(3)
        self.report()
        rig.settle()
        self.told(3)

    def test_a_second_change_goes_while_the_first_is_unsettled_and_neither_is_sent_again(self):
        for first_back in ("no levels", "no answer"):
            with self.subTest(first_back):
                kb = backlight_keyboard()
                if first_back == "no levels":
                    on_setter(kb, lambda kb: kb.failing.__setitem__((BL, 2), 5))
                else:
                    muted_once(kb)
                rig = self.make(kb)
                entered, release = self.hold(lambda request: request[0] is Backlight.write)
                self.ask("backlight", {"level": 6}, settle=False)
                self.assertTrue(self.reached(entered))
                self.ask("backlight", {"hands_in": 10}, settle=False)  # asked after the first setter
                release.set()
                rig.settle()
                self.assertEqual(self.setters(BL), [bytes.fromhex("0119ff060c0006003c000504"),
                                                    bytes.fromhex("0119ff060c0002003c000504")])
                self.assertIsNone(control(rig, "backlight")["outcome"])
                if first_back == "no levels":
                    self.assertTrue(rig.kb._level_own)
                    kb.failing.pop((BL, 2))
                    self.due_read()
                    rig.settle()
                self.assertEqual(self.owned(), (6, 0, 0, False))
                self.assertIsNone(self.notice())
                self.assertEqual(len(self.setters(BL)), 2)
                self.shows(1)
                self.report()
                rig.settle()
                self.told(1)
                rig.stop()

    def test_a_change_that_is_not_sent_leaves_the_report_as_it_was(self):
        def equal(rig):
            self.ask("backlight", {"hands_out": 60})

        def refused(rig):
            self.ask("backlight", {"level": 9})

        def blocked(rig):
            entered, release = self.hold(lambda request: request[0] is Backlight.read)
            self.ask("backlight", {"hands_out": 30}, settle=False)
            self.assertTrue(self.reached(entered))
            self.rig.rivals = ["Solaar"]
            rig.engine._look_for_rivals(time.monotonic())
            release.set()
            rig.settle()
            self.rig.rivals = []
            rig.engine._look_for_rivals(time.monotonic())

        def called_off(rig):
            entered, release = self.hold(lambda request: request[0] is Backlight.read)
            self.ask("backlight", {"hands_out": 30}, settle=False)
            self.assertTrue(self.reached(entered))
            rig.link(1, False)
            release.set()
            self.drain()
            rig.link(1, True)
            rig.settle()

        for attempt in (equal, refused, blocked, called_off):
            with self.subTest(attempt.__name__):
                kb = backlight_keyboard()
                rig = self.make(kb)
                kb.failing[(BL, 2)] = 5
                self.shows(6)
                self.report()
                rig.settle()
                attempt(rig)
                self.assertEqual(self.setters(BL), [])
                self.assertFalse(rig.kb._level_own)
                kb.failing.pop((BL, 2))
                self.due_read()
                rig.settle()
                if attempt is called_off:  # a new connection: nothing kept, nothing said
                    self.assertEqual((self.owned(), self.notice()), ((6, 0, 0, False), None))
                else:
                    self.told(6)  # the report is still told, compared with what was settled before
                self.assertEqual(self.setters(BL), [])
                rig.stop()
        kb = backlight_keyboard()
        muted_once(kb)
        rig = self.make(kb)
        self.ask("backlight", {"level": 6})
        self.assertTrue(rig.kb._level_own)
        kb.failing[(BL, 2)] = 5
        self.ask("backlight", {"level": 6})  # so already: its own change stays unsettled, nothing sent
        self.assertTrue(rig.kb._level_own)
        self.assertEqual(len(self.setters(BL)), 1)


V2_SHOWN = {"value": {"on": True, "power_save": True, "mode": "none", "level": None, "hands_out": None, "hands_in": None,
                      "powered": None},
            "fields": ["on", "power_save"], "can": ["on"], "levels": 8, "shown_level": 0}
V3_SHOWN = {"value": {"on": True, "power_save": False, "mode": "manual", "level": 4, "hands_out": 60, "hands_in": 30,
                      "powered": 300},
            "fields": ALL_FIELDS, "can": ASKABLE, "levels": 8, "shown_level": 4}


class BacklightPage(unittest.TestCase):
    """The backlight rows of the Keyboard page (Qt offscreen)."""

    def setUp(self):
        Page.setUp(self)
        self.row, self.switch = self.page.settings_rows["backlight"], self.page.backlight_switch
        self.rows = self.page.backlight_rows

    def buttons(self):
        from PySide6.QtWidgets import QPushButton
        return self.page.brightness.findChildren(QPushButton)

    def show(self, control=V3_SHOWN, connected=True, reason=None, **changes):
        controls = {}
        if control is not None:
            control = copy.deepcopy(control)
            control["value"] = dict(control["value"], **changes.pop("value", {})) if control.get("value") else None
            controls["backlight"] = dict({"writable": True, "busy": False, "outcome": None}, **control)
            controls["backlight"].update(changes)
        found = {"connected": connected, "known": True, "name": "Synthetic keyboard", "battery": None, "current_host": 0,
                 "hosts": [], "keys": [], "reason": reason, "failed": None, "firmware": None, "settings": ["backlight"],
                 "controls": controls, "notice": None}
        self.store.update_state(dict(self.store.state, keyboard=found))

    def shown_rows(self):
        return [key for key, row in self.rows.items() if not row.isHidden()]

    def test_rows_for_what_the_keyboard_has(self):
        self.show(None)
        self.assertTrue(self.switch.isHidden())
        self.assertEqual(self.shown_rows(), [])
        self.assertIn("MX Options does not change them yet", self.row.note.text())
        self.show(V2_SHOWN)
        self.assertFalse(self.switch.isHidden())
        self.assertEqual(self.shown_rows(), ["power_save", "notices"])
        self.show()
        self.assertEqual(self.shown_rows(), ["mode", "level", "hands_out", "hands_in", "powered", "power_save", "notices"])
        self.show(dict(V3_SHOWN, fields=["on", "hands_out"], levels=None))
        self.assertEqual(self.shown_rows(), ["hands_out"])
        self.show({"value": None}, writable=False, outcome="unknown")
        self.assertEqual(self.shown_rows(), [])
        self.assertEqual(self.row.note.text(), "The keyboard did not say how this is set.")
        self.show(connected=False)
        self.assertEqual(self.shown_rows(), [])
        self.assertTrue(self.switch.isHidden())
        self.assertEqual(self.calls, [])

    def test_changeable_as_the_keyboard_lets_it_be(self):
        page = self.page
        boxes = list(page.timer_boxes.values())
        self.show()
        self.assertTrue(all(widget.isEnabled() for widget in [self.switch, page.brightness, page.level] + boxes))
        self.assertFalse(page.power_save.isEnabled())  # shown only
        self.show(value={"mode": "auto"})
        self.assertFalse(page.level.isEnabled())  # set by the light around it
        self.assertTrue(page.brightness.isEnabled())
        self.show(busy=True)
        self.assertFalse(any(widget.isEnabled() for widget in [self.switch, page.brightness] + boxes))
        self.assertTrue(page.level.isEnabled())  # keeps the keyboard's focus; a move is laid over the change asked
        for changes in ({"writable": False}, {"writable": False, "reason": "rivals"}):
            with self.subTest(changes):
                self.show(**changes)
                self.assertFalse(any(widget.isEnabled() for widget in [self.switch, page.brightness, page.level] + boxes))
        self.show(can=["on", "hands_in"])
        self.assertEqual([widget.isEnabled() for widget in [self.switch, page.brightness, page.level] + boxes],
                         [True, False, False, False, True, False])
        self.show(value={"mode": "keys"}, can=["mode"])
        self.assertEqual([widget.isEnabled() for widget in [self.switch, page.brightness, page.level] + boxes],
                         [False, True, False, False, False, False])
        self.assertFalse(any(button.isChecked() for button in self.buttons()))
        self.assertEqual(self.calls, [])

    def test_one_request_for_each_choice_and_none_on_refresh(self):
        page = self.page
        self.show()
        self.switch.click()
        self.buttons()[0].click()
        box = page.timer_boxes["hands_out"]
        self.assertEqual(box.itemData(box.currentIndex()), 60)
        box.activated.emit(box.findData(30))
        box.activated.emit(box.findData(60))  # the one it has: nothing to ask
        self.assertEqual(self.calls, [("backlight", {"on": False}), ("backlight", {"mode": "auto"}),
                                      ("backlight", {"hands_out": 30})])
        self.show(busy=True)
        self.assertFalse(self.switch.isChecked())  # what was asked for, until the keyboard answers
        self.show(value={"on": False, "mode": "auto", "hands_out": 30, "level": 2})
        self.show(value={"level": 6, "hands_in": 3600})
        self.assertEqual((page.level.value(), page.timer_boxes["hands_in"].currentData()), (6, 3600))
        self.assertTrue(self.switch.isChecked())
        self.assertEqual(len(self.calls), 3)  # following the keyboard asks nothing

    def test_the_slider_asks_for_whole_levels_from_its_keys_at_once_and_from_a_drag_once_let_go(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QStyle, QStyleOptionSlider
        page, slider = self.page, self.page.level.slider
        emitted = []
        page.level.changed.connect(lambda value: emitted.append(("changed", value)))
        page.level.released.connect(lambda value: emitted.append(("released", value)))
        self.show()
        self.assertEqual((slider.minimum(), slider.maximum(), page.level.value()), (0, 7, 4))
        slider.setFocus()
        QTest.keyClick(slider, Qt.Key.Key_Right)
        QTest.keyClick(slider, Qt.Key.Key_PageDown)
        self.app.processEvents()
        self.assertEqual(emitted, [("changed", 5.0), ("changed", 4.0)])
        self.assertTrue(all(type(value) is float for _kind, value in emitted))  # what the widget says ...
        self.assertEqual(self.calls, [("backlight", {"level": 5})])  # ... and only a whole level goes; 4 it has
        option = QStyleOptionSlider()
        slider.initStyleOption(option)
        handle = slider.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                               QStyle.SubControl.SC_SliderHandle, slider).center()
        emitted.clear()
        QTest.mousePress(slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, handle)
        QTest.mouseMove(slider, QPoint(slider.width() - 2, handle.y()))
        self.app.processEvents()
        self.assertTrue(slider.isSliderDown())
        self.assertEqual(len(self.calls), 1)  # nothing while dragged ...
        self.show(value={"level": 2})  # ... nor moved back by what the keyboard says meanwhile
        self.assertEqual(page.level.value(), 7)
        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                           QPoint(slider.width() - 2, handle.y()))
        self.app.processEvents()
        self.assertEqual(emitted[-1], ("released", 7.0))
        self.assertEqual(self.calls[1:], [("backlight", {"level": 7})])
        for _name, payload in self.calls:
            self.assertIs(type(payload["level"]), int)
            self.assertTrue(Backlight.valid(payload))
        self.assertFalse(Backlight.valid({"level": 5.0}))
        self.show(value={"level": 7})
        QTest.keyClick(slider, Qt.Key.Key_Left)
        QTest.keyClick(slider, Qt.Key.Key_Right)  # back to the level the keyboard has: nothing to ask
        self.show(value={"level": 6}, busy=True)  # while a change is under way, the last move counts
        self.assertEqual(page.level.value(), 7)
        QTest.keyClick(slider, Qt.Key.Key_Left)
        self.assertEqual(self.calls[2:], [("backlight", {"level": 6}), ("backlight", {"level": 6})])
        kb = backlight_keyboard()  # what the page asks, the engine takes and sends
        rig = Rig(self, {1: kb, 2: mouse_device()}).start()
        for _name, payload in self.calls:
            rig.kb.set_setting("backlight", payload)
            rig.settle()
        self.assertEqual([params[3] for _feature, params in kb.setting_writes], [5, 7, 6])

    def test_timer_choices_and_values_it_has_of_its_own(self):
        page = self.page
        self.show(value={"hands_out": 35, "hands_in": 3665})
        box = page.timer_boxes["hands_out"]
        labels = [box.itemText(i) for i in range(box.count())]
        self.assertEqual(labels, ["5 s", "10 s", "15 s", "30 s", "35 s", "1 min", "2 min", "5 min", "10 min", "15 min",
                                  "30 min", "1 h", "2 h"])
        self.assertEqual(box.currentText(), "35 s")
        self.assertEqual(page.timer_boxes["hands_in"].currentText(), "1 h 1 min 5 s")
        self.assertEqual(page.timer_boxes["powered"].currentText(), "5 min")
        self.show(value={"powered": None}, can=["on", "mode", "level", "hands_out", "hands_in"])
        powered = page.timer_boxes["powered"]
        self.assertEqual(([powered.itemText(i) for i in range(powered.count())], powered.isEnabled()), (["Not known"], False))
        powered.activated.emit(0)
        self.assertEqual(self.calls, [])

    def test_a_note_for_each_state(self):
        notes = self.pages.BACKLIGHT_NOTES
        for changes, note in (({}, ""), ({"writable": False}, notes["read-only"]),
                              ({"writable": False, "reason": "rivals"}, ""),
                              ({"value": {"mode": "keys"}, "can": ["mode"]}, notes["keys"]),
                              ({"value": {"mode": "keys"}, "can": [], "fields": ["on", "power_save"]}, notes["keys-only"]),
                              ({"value": {"mode": None}, "can": ["on", "hands_out", "hands_in", "powered"]},
                               notes["mode-unknown"]),
                              ({"fields": ["on", "power_save", "mode"], "can": ["on", "mode"]}, notes["untimed"]),
                              ({"outcome": "restored"}, self.pages.OUTCOMES["restored"]),
                              ({"value": {"mode": "keys"}, "outcome": "unsupported"}, self.pages.OUTCOMES["unsupported"])):
            with self.subTest(changes):
                self.show(**copy.deepcopy(changes))
                self.assertEqual(self.row.note.text(), note)
                self.assertEqual(self.row.note.isHidden(), not note)
        self.show()
        self.assertEqual(self.row.info.text(), self.pages.BACKLIGHT_HINT)
        self.assertEqual(self.page.level.info.text(), self.pages.LEVEL_HINT.format(top=7))
        self.show(levels=16)
        self.assertEqual(self.page.level.info.text(), self.pages.LEVEL_HINT.format(top=15))
        self.assertEqual(self.page.level.slider.maximum(), 15)

    def test_names_for_assistive_technology_and_every_word_translated(self):
        page = self.page
        self.show()
        self.assertEqual(self.switch.accessibleName(), "Backlight on")
        self.assertEqual(page.brightness.accessibleName(), "Brightness")
        self.assertEqual([button.accessibleName() for button in self.buttons()],
                         ["Automatic brightness", "Fixed brightness"])
        self.assertEqual(page.level.slider.accessibleName(), "Backlight brightness level")
        self.assertEqual([box.accessibleName() for box in page.timer_boxes.values()],
                         ["Backlight off after, hands away", "Backlight off after, hands near",
                          "Backlight off after, plugged in"])
        for key, box in page.timer_boxes.items():
            self.assertIs(self.rows[key].title.buddy(), box)
        self.assertEqual(page.power_save.accessibleName(), "Off at low battery")
        self.assertEqual(page.notices.accessibleName(), "Show a notice when the brightness changes on the keyboard")
        from mxoptions import i18n_uk
        words = [*self.pages.BACKLIGHT_NOTES.values(), self.pages.BACKLIGHT_HINT, self.pages.LEVEL_HINT,
                 self.pages.POWER_SAVE_HINT, *(text for row in self.pages.TIMER_ROWS for text in row[1:]), "Backlight on",
                 "Automatic", "Fixed", "Brightness", "Automatic brightness", "Fixed brightness", "Brightness level",
                 "Backlight brightness level", "Dimmer", "Brighter", "Not known", "Off at low battery",
                 "Show a notice when the brightness changes on the keyboard", "{} h", "{} min", "{} s",
                 "Keyboard backlight: level {level} of {top}"]
        for text in words:
            self.assertIn(text, i18n_uk.STRINGS)
        for text in words + ["Backlight", "Typing"]:  # no protocol words in what is shown
            self.assertNotRegex(text + i18n_uk.STRINGS[text], r"0x|1982|\bV\d|\bbit|\bunits?\b|\bbyte")

    def test_the_notice_switch_is_a_setting_of_this_program_only(self):
        self.show()
        self.assertFalse(self.page.notices.isChecked())
        self.page.notices.click()
        self.assertIs(self.store.cfg["keyboard"]["notices"], True)
        self.show()
        self.assertTrue(self.page.notices.isChecked())
        self.store.set("keyboard", "notices", False)  # changed elsewhere (a settings file): followed
        self.assertFalse(self.page.notices.isChecked())
        self.assertEqual(self.calls, [])  # the keyboard is never asked

    def test_durations_in_words_keep_every_second(self):
        from mxoptions import i18n
        cases = ((5, "5 s", "5 с"), (60, "1 min", "1 хв"), (90, "1 min 30 s", "1 хв 30 с"), (3600, "1 h", "1 год"),
                 (3605, "1 h 5 s", "1 год 5 с"), (3660, "1 h 1 min", "1 год 1 хв"),
                 (3665, "1 h 1 min 5 s", "1 год 1 хв 5 с"), (5400, "1 h 30 min", "1 год 30 хв"), (7200, "2 h", "2 год"))
        self.assertEqual([self.pages.duration_text(seconds) for seconds, _en, _uk in cases], [en for _s, en, _uk in cases])
        self.addCleanup(i18n.setup, "en")
        i18n.setup("uk")
        self.assertEqual([self.pages.duration_text(seconds) for seconds, _en, _uk in cases], [uk for _s, _en, uk in cases])
        self.assertEqual(len({self.pages.duration_text(seconds) for seconds, _en, _uk in cases}), len(cases))

    def test_tab_reaches_every_control(self):
        from PySide6.QtCore import Qt
        page = self.page
        self.show()
        wanted = [self.switch, *self.buttons(), page.level.slider,
                  *page.timer_boxes.values(), page.notices]
        reached, widget = [], self.switch
        for _ in range(500):
            if widget.isVisibleTo(page) and widget.isEnabled() and widget.focusPolicy() in (
                    Qt.FocusPolicy.TabFocus, Qt.FocusPolicy.StrongFocus, Qt.FocusPolicy.WheelFocus):
                reached.append(widget)
            widget = widget.nextInFocusChain()
            if widget is self.switch:
                break
        for control in wanted:
            self.assertIn(control, reached)
        self.assertEqual(sorted(reached.index(control) for control in wanted), [reached.index(c) for c in wanted])



# -- the lock keys (0x4220) and the Fn lock, told as notices -----------------------------------------------------------

LKS = device.LOCK_KEY_STATE
CAPS, NUM, SCROLL = 0x02, 0x01, 0x04
FN_CHANGE = [(device.HOSTS_INFO, 0), (HOSTS, 0), (HOSTS, 1), (HOSTS, 0)]  # a change of Fn lock: read, set, read back
FN_READ = [(device.HOSTS_INFO, 0), (HOSTS, 0)]


def lock_keyboard(locks=0, disable=False, unit=UNIT_A, **kwargs):
    """A synthetic keyboard with Fn inversion per channel (channel 0 of three, False there) and the lock key state,
    answering ``locks``; with ``disable``, keys it can turn off too."""
    settings = {device.HOSTS_INFO: 0, HOSTS: {0: [1, 1], 1: [0, 1], 2: [1, 1]}}
    if disable:
        settings[DK] = {"caps": 0x1F, "current": 0}
    kb = keyboard_device(unit, hosts=(3, 0), settings=settings, extra=[LKS], **kwargs)
    kb.replies[(LKS, 0)] = bytes([locks])
    return kb


def fn_read(request):
    return request[0] is FnCodec.read


def lock_read(request):
    return request[0] is kbsettings.lock_state


class NoticeBase(Base):
    """A rig with a keyboard found and read, its notices as asked before it is found. The settings change afterwards
    only as the engine takes new ones; time goes on only with Later and the keyboard's tick."""

    hold_nth = BacklightBase.hold_nth

    def make(self, kb=None, locks=False, fns=False, notices=False, **rig):
        self.dev = kb or lock_keyboard()
        self.rig = Rig(self, {1: self.dev, 2: mouse_device()}, **rig)
        self.rig.engine.cfg["keyboard"].update(lock_notices=locks, fn_notices=fns, notices=notices)
        return self.rig.start()

    def prefs(self, **changes):
        """The settings change, as from the window or a settings file: the engine takes them."""
        new = copy.deepcopy(self.rig.engine.cfg)
        new["keyboard"].update(changes)
        self.rig.engine._apply_config(new)

    def locks(self, bits):
        self.dev.replies[(LKS, 0)] = bytes([bits])

    def fn_is(self, value):
        self.dev.settings[HOSTS][0][0] = 0 if value else 1

    def lock_report(self):
        self.rig.note(1, self.dev, LKS, b"\x02")  # what it says is never read

    def fn_report(self):
        self.rig.note(1, self.dev, HOSTS, b"")

    def lock_reads(self):
        return len(packets(self.rig.port, self.dev, LKS, 0))

    def fn_reads(self):
        return len(packets(self.rig.port, self.dev, HOSTS, 0))

    def fn_tries(self):
        """Reads of Fn lock begun: each asks for the channel first."""
        return len(packets(self.rig.port, self.dev, device.HOSTS_INFO, 0))

    def wire(self, start):
        """What was asked of the keyboard since packet ``start``, as (feature, function)."""
        return [(self.dev.features[p[2]], p[3] >> 4) for p in self.rig.port.written[start:] if p[1] == 1]

    def notice(self):
        return self.rig.state()["notice"]

    def notices(self):
        """Every notice the window was given so far, once each, in order."""
        seen = {}
        for state in self.rig.states:
            if state and state.get("notice"):
                seen.setdefault(state["notice"]["seq"], state["notice"])
        return list(seen.values())

    @staticmethod
    def record(rec):
        return rec.seen, rec.event, rec.handled, rec.own, rec.retried

    def later(self, clock, times=1):
        """RECOVERY_RETRY goes by ``times`` times, the keyboard's clock ticking each time."""
        for _ in range(times):
            clock.skip(keyboard.RECOVERY_RETRY + 1)
            self.rig.kb.tick(time.monotonic())
            self.rig.settle()

    def idle(self):
        """Nothing is due before RECOVERY_RETRY is nearly over, but something is then."""
        due = self.rig.kb.due(time.monotonic())
        self.assertTrue(keyboard.RECOVERY_RETRY - 5.0 < due <= keyboard.RECOVERY_RETRY, due)

    def nothing_due(self):
        self.assertEqual(self.rig.kb.due(time.monotonic()), float("inf"))


def told(seq, **notice):
    return dict(notice, seq=seq)


def locks_told(seq, **keys):
    return {"seq": seq, "kind": "locks", "keys": keys}


def fn_told(seq, value):
    return {"seq": seq, "kind": "fn", "value": value}


class LockDecode(unittest.TestCase):
    def test_the_three_named_bits_of_function_0_and_nothing_else(self):
        self.assertEqual((dict(kbsettings.LOCK_KEYS), kbsettings.LOCK_MASK),
                         ({"caps_lock": CAPS, "num_lock": NUM, "scroll_lock": SCROLL}, 0x07))
        for byte, bits in ((0x00, 0), (CAPS, CAPS), (0x07, 0x07), (0xFF, 0x07), (0xF8, 0), (0xFA, CAPS)):
            with self.subTest(byte=hex(byte)):
                stub = Stub([LKS], {(LKS, 0): bytes([byte, 0xFF])})
                self.assertEqual(kbsettings.lock_state(stub), bits)
                self.assertEqual(stub.calls, [(LKS, 0, b"")])  # function 0 with nothing, and nothing more
        with self.assertRaises(kbsettings.BadReply):
            kbsettings.lock_state(Stub([LKS], {(LKS, 0): b""}))


class ReportedRecord(unittest.TestCase):
    """keyboard.Reported, the record a reported state is settled in, alone."""

    def rec(self, seen=None, event=0, handled=0, own=False, retried=None):
        made = keyboard.Reported()
        made.seen, made.event, made.handled, made.own, made.retried = seen, event, handled, own, retried
        return made

    def state(self, rec):
        return rec.seen, rec.event, rec.handled, rec.own, rec.retried

    def test_a_read_settles_it_only_where_no_report_came_meanwhile(self):
        self.assertEqual(self.state(keyboard.Reported()), (None, 0, 0, False, None))
        for before, start, now, said, after in (
                ((None, 0, 0, False, None), 0, 2, False, (2, 0, 0, False, None)),  # the first read: no word
                ((None, 1, 0, False, None), 1, 2, False, (2, 1, 1, False, None)),  # first, even with a report
                ((0, 1, 0, False, None), 0, 2, False, (0, 1, 0, False, None)),     # a report while read: nothing
                ((0, 1, 0, False, None), 1, 2, True, (2, 1, 1, False, None)),      # reported, and changed: told
                ((0, 1, 1, False, None), 1, 2, False, (2, 1, 1, False, None)),     # nothing reported: no word
                ((2, 1, 0, False, None), 1, 2, False, (2, 1, 1, False, None)),     # the same again: no word
                ((False, 2, 0, True, None), 1, True, False, (False, 2, 0, True, None)),  # its own, overtaken: nothing
                ((False, 2, 0, True, None), 2, True, False, (True, 2, 2, False, None)),  # its own: settled, no word
                ((False, 2, 0, True, 1), 2, False, False, (False, 2, 2, False, 1))):
            with self.subTest(before=before, start=start, now=now):
                rec = self.rec(*before)
                self.assertIs(rec.read(start, now), said)
                self.assertEqual(self.state(rec), after)

    def test_a_failed_read_is_read_once_more_for_each_report_or_change_sent(self):
        rec = self.rec(0, 1, 0)
        self.assertEqual([rec.failed(), rec.failed()], [True, False])
        self.assertEqual(self.state(rec), (0, 1, 0, False, 1))  # only how far it was tried again changes
        rec.event += 1
        self.assertEqual([rec.failed(), rec.failed()], [True, False])
        self.assertEqual(self.state(rec), (0, 2, 0, False, 2))
        rec = self.rec(0, 1, 1)
        self.assertIs(rec.failed(), False)  # nothing to settle
        rec = self.rec(0)
        rec.sent()
        self.assertEqual(self.state(rec), (0, 0, 0, True, None))
        self.assertEqual([rec.failed(), rec.failed()], [True, False])  # its own change, with no report at all
        rec.sent()
        self.assertEqual([rec.failed(), rec.failed()], [True, False])  # each change sent: once more
        self.assertEqual(self.state(rec), (0, 0, 0, True, 0))


class LockNotices(NoticeBase):
    """A lock key the keyboard reports turned on or off is told once, as a fresh read says it is, where the settings
    ask for it: never what made it change, and nothing of it kept but for that."""

    def test_off_at_first_nothing_is_asked_or_kept(self):
        rig = self.make(lock_keyboard(CAPS))
        self.assertIs(rig.engine.cfg["keyboard"]["lock_notices"], False)
        self.assertEqual(self.lock_reads(), 0)
        self.assertEqual(rig.state()["settings"], ["fn", "locks"])
        sent = len(rig.port.written)
        self.dev.failing[(LKS, 0)] = 5
        for bits in (0, CAPS, NUM):
            self.locks(bits)
            self.lock_report()
            rig.settle()
        self.assertEqual(rig.port.written[sent:], [])  # not even read again
        self.nothing_due()
        self.assertEqual((self.notices(), rig.kb._locks.seen, self.record(rig.kb._locks)),
                         ([], None, (None, 0, 0, False, None)))
        self.assertNotIn("locks", rig.state()["controls"])

    def test_a_keyboard_without_them_is_never_asked_or_read_more_for_them(self):
        with self.assertNoLogs("mxoptions.keyboard", logging.WARNING):
            rig = self.make(fn_keyboard(), locks=True)
            start = len(rig.port.written)
            self.prefs(lock_notices=False)
            self.prefs(lock_notices=True)
            rig.settle()
            self.assertEqual(rig.port.written[start:], [])
            self.fn_is(True)
            self.fn_report()
            rig.settle()
            self.assertEqual(self.wire(start), FN_READ)
        self.assertEqual(rig.state()["settings"], ["fn"])

    def test_a_settings_read_cut_short_by_no_answer_leaves_them_to_the_read_after(self):
        rig = self.make(locks=True)
        clock = Later(self)
        self.dev.failing[(HOSTS, 0)] = None  # Fn lock, read first, unanswered: the reading ends there
        self.locks(CAPS)
        reads = self.lock_reads()
        self.lock_report()
        rig.settle()
        self.assertEqual((self.lock_reads(), self.record(rig.kb._locks)), (reads, (0, 1, 0, False, None)))
        self.idle()
        del self.dev.failing[(HOSTS, 0)]
        self.later(clock)
        self.assertEqual((self.lock_reads(), self.notices()), (reads + 1, [locks_told(1, caps_lock=True)]))

    def test_found_found_again_or_replaced_it_is_read_without_a_word(self):
        rig = self.make(lock_keyboard(CAPS), locks=True)
        self.assertEqual(self.lock_reads(), 1)
        self.assertEqual(self.record(rig.kb._locks), (CAPS, 0, 0, False, None))
        self.assertIsNone(self.notice())
        self.locks(CAPS | NUM)
        self.lock_report()
        rig.settle()
        self.assertEqual(self.notices(), [locks_told(1, num_lock=True)])
        for kb in (self.dev, lock_keyboard(SCROLL, unit=UNIT_B)):
            rig.link(1, False)
            self.assertIsNone(self.notice())
            self.assertIsNone(rig.kb._locks.seen)  # forgotten
            rig.port.devices[1] = kb
            rig.link(1, True)
            rig.settle()
            self.assertIsNone(self.notice())  # whatever it says now: no word
        self.dev = kb
        self.assertEqual(self.record(rig.kb._locks), (SCROLL, 0, 0, False, None))
        self.locks(0)
        self.lock_report()
        rig.settle()
        self.assertEqual(self.notices(), [locks_told(1, num_lock=True), locks_told(2, scroll_lock=False)])
        self.assertEqual(rig.kb._notice_seq, 2)

    def test_a_reported_change_is_told_once_as_it_is_now(self):
        rig = self.make(locks=True)
        self.locks(CAPS)
        self.lock_report()
        rig.settle()
        self.assertEqual(self.notice(), locks_told(1, caps_lock=True))
        self.locks(0)
        self.lock_report()
        rig.settle()
        self.assertEqual(self.notice(), locks_told(2, caps_lock=False))
        self.locks(NUM | CAPS)
        self.lock_report()
        rig.settle()
        self.assertEqual(self.notice(), locks_told(3, caps_lock=True, num_lock=True))
        self.assertEqual(list(self.notice()["keys"]), ["caps_lock", "num_lock"])  # in the order they are shown
        self.assertEqual([notice["seq"] for notice in self.notices()], [1, 2, 3])
        self.assertEqual(self.dev.setting_writes, [])

    def test_the_same_again_is_not_told_and_a_burst_is_one_read_more(self):
        rig = self.make(locks=True)
        for _ in range(5):
            self.lock_report()
            rig.settle()
        self.assertEqual((self.notices(), self.record(rig.kb._locks)), ([], (0, 5, 5, False, None)))
        reads, fns = self.lock_reads(), self.fn_reads()
        entered, release = self.hold(lock_read)
        for bits in (CAPS, CAPS | NUM, NUM, 0, SCROLL):
            self.locks(bits)
            self.lock_report()
            if not entered.is_set():
                self.assertTrue(self.reached(entered))
        release.set()
        rig.settle()
        self.assertEqual((self.lock_reads(), self.fn_reads()), (reads + 2, fns + 2))  # the one under way, one more
        self.assertEqual(self.notices(), [locks_told(1, scroll_lock=True)])

    def test_keys_not_named_are_never_compared_kept_or_shown(self):
        rig = self.make(locks=True)
        self.locks(0xF8)
        self.lock_report()
        rig.settle()
        self.assertEqual((self.notices(), rig.kb._locks.seen), ([], 0))
        self.locks(0xF8 | CAPS)
        self.lock_report()
        rig.settle()
        self.assertEqual((self.notices(), rig.kb._locks.seen), ([locks_told(1, caps_lock=True)], CAPS))
        self.assertNotIn("locks", rig.state()["controls"])

    def test_a_report_while_it_is_read_is_settled_by_the_read_after_it(self):
        rig = self.make(locks=True)
        first, release_first = self.hold_nth(1, lock_read)
        second, release_second = self.hold_nth(2, lock_read)
        self.locks(CAPS)
        self.lock_report()
        self.assertTrue(self.reached(first))
        self.lock_report()  # while it is read; its answer says Caps Lock on already
        release_first.set()
        self.assertTrue(self.reached(second))
        self.assertEqual((self.notices(), self.record(rig.kb._locks)), ([], (0, 2, 0, False, None)))
        release_second.set()
        rig.settle()
        self.assertEqual((self.notices(), self.record(rig.kb._locks)),
                         ([locks_told(1, caps_lock=True)], (CAPS, 2, 2, False, None)))

    def test_a_read_that_fails_is_read_once_more_then_waits_for_a_report(self):
        rig = self.make(locks=True)
        clock = Later(self)
        self.dev.failing[(LKS, 0)] = 5
        self.locks(CAPS)
        reads = self.lock_reads()
        self.lock_report()
        rig.settle()
        self.assertEqual((self.lock_reads(), self.notices()), (reads + 1, []))
        self.assertEqual(self.record(rig.kb._locks), (0, 1, 0, False, 1))
        self.idle()  # not at once
        self.later(clock)
        self.assertEqual(self.lock_reads(), reads + 2)  # once more, failing still
        self.nothing_due()
        self.later(clock, 3)
        self.assertEqual(self.lock_reads(), reads + 2)  # and no more
        del self.dev.failing[(LKS, 0)]
        self.lock_report()
        rig.settle()
        self.assertEqual(self.notices(), [locks_told(1, caps_lock=True)])  # against what was settled before
        self.assertEqual(self.dev.setting_writes, [])

    def test_a_read_unanswered_is_read_again_after_recovery_retry(self):
        rig = self.make(locks=True)
        clock = Later(self)
        self.dev.failing[(LKS, 0)] = None
        self.locks(CAPS)
        self.lock_report()
        rig.settle()
        self.assertEqual((self.notices(), self.record(rig.kb._locks)), ([], (0, 1, 0, False, None)))
        self.idle()
        self.later(clock)  # unanswered still: later still
        self.idle()
        del self.dev.failing[(LKS, 0)]
        self.later(clock)
        self.assertEqual(self.notices(), [locks_told(1, caps_lock=True)])
        self.nothing_due()

    def test_a_read_called_off_or_cut_short_settles_nothing(self):
        for wish in ("configure", "host"):
            with self.subTest(wish):
                rig = self.make(locks=True)
                clock = Later(self)
                entered, release = self.hold(lock_read)
                self.locks(CAPS)
                self.lock_report()
                self.assertTrue(self.reached(entered))
                if wish == "configure":
                    rig.kb.configure()
                else:
                    rig.kb.device_action({"type": "host", "host": 1}, "press")
                release.set()
                rig.settle()
                if wish == "host":  # it stayed: once the switch's grace is over, it is read again
                    self.assertEqual(self.dev.switched, [1])
                    self.assertEqual(self.record(rig.kb._locks), (0, 1, 0, False, None))
                    clock.skip(keyboard.SWITCH_GRACE + 1)
                    rig.kb.tick(time.monotonic())
                    rig.settle()
                self.assertEqual(self.notices(), [locks_told(1, caps_lock=True)])
                self.assertEqual(self.record(rig.kb._locks), (CAPS, 1, 1, False, None))
                rig.stop()
        rig = self.make(locks=True)
        entered, release = self.hold(lock_read)
        self.locks(CAPS)
        self.lock_report()
        self.assertTrue(self.reached(entered))
        rig.link(1, False)  # gone while it is read: its answer is not taken
        release.set()
        self.drain()
        self.assertIsNone(rig.kb._locks.seen)
        rig.link(1, True)
        rig.settle()
        self.assertEqual((self.notices(), self.record(rig.kb._locks)), ([], (CAPS, 0, 0, False, None)))

    def test_turned_off_a_read_under_way_tells_and_keeps_nothing_turned_on_it_is_read_once(self):
        rig = self.make(locks=True)
        entered, release = self.hold(lock_read)
        self.locks(CAPS)
        self.lock_report()
        self.assertTrue(self.reached(entered))
        self.prefs(lock_notices=False)
        release.set()
        rig.settle()
        self.assertEqual((self.notices(), rig.kb._locks.seen), ([], None))
        sent = len(rig.port.written)
        self.prefs(lock_notices=False)  # the same: nothing
        self.prefs(notices=True)  # the brightness one: nothing either
        self.lock_report()  # off: not even counted
        rig.settle()
        self.assertEqual(rig.port.written[sent:], [])
        self.prefs(lock_notices=True)  # on while connected: one read, without a word
        rig.settle()
        self.assertEqual(self.wire(sent), FN_READ + [(LKS, 0)])
        self.assertEqual((self.notices(), self.record(rig.kb._locks), self.dev.setting_writes),
                         ([], (CAPS, 0, 0, False, None), []))
        self.locks(0)
        self.lock_report()
        rig.settle()
        self.assertEqual(self.notices(), [locks_told(1, caps_lock=False)])

    def test_on_off_and_on_while_its_first_read_is_held_the_old_answer_settles_nothing(self):
        rig = self.make()
        first, release_first = self.hold_nth(1, lock_read)
        second, release_second = self.hold_nth(2, lock_read)
        self.prefs(lock_notices=True)
        self.assertTrue(self.reached(first))
        self.prefs(lock_notices=False)
        self.prefs(lock_notices=True)
        self.locks(CAPS)
        release_first.set()  # what the first record asked: Caps Lock on
        self.assertTrue(self.reached(second))  # the read the new record asked for, at once after
        self.assertEqual(self.record(rig.kb._locks), (None, 0, 0, False, None))
        self.locks(0)
        self.lock_report()  # while that is held
        release_second.set()
        rig.settle()
        self.assertEqual((self.notices(), self.record(rig.kb._locks)), ([], (0, 1, 1, False, None)))
        self.locks(CAPS)
        self.lock_report()
        rig.settle()
        self.assertEqual(self.notices(), [locks_told(1, caps_lock=True)])

    def test_turned_off_a_read_under_way_that_fails_asks_nothing_more(self):
        rig = self.make(locks=True)
        clock = Later(self)
        entered, release = self.hold(lock_read)
        self.locks(CAPS)
        self.lock_report()
        self.assertTrue(self.reached(entered))
        old = rig.kb._locks
        self.prefs(lock_notices=False)
        self.dev.failing[(LKS, 0)] = 5
        release.set()
        rig.settle()
        self.assertEqual(self.record(old), (0, 1, 0, False, None))  # the record it was made for: not even tried again
        sent = len(rig.port.written)
        self.nothing_due()
        self.later(clock, 3)
        self.assertEqual(rig.port.written[sent:], [])

    def test_a_report_before_it_is_turned_off_and_on_is_settled_afresh(self):
        rig = self.make(lock_keyboard(disable=True), locks=True)
        entered, release = self.hold(lambda request: request[0] is DisableCodec.write)
        baseline, release_baseline = self.hold_nth(1, lock_read)
        rig.kb.set_setting("disable", {"caps_lock": True})
        self.assertTrue(self.reached(entered))  # another setting's change under way: the read due waits for it
        self.locks(CAPS)
        self.lock_report()
        self.prefs(lock_notices=False)
        self.prefs(lock_notices=True)
        release.set()
        self.assertTrue(self.reached(baseline))  # the read due: the first of the new record
        self.locks(0)
        self.lock_report()  # while it is held: the read it makes due settles it, without a word
        release_baseline.set()
        rig.settle()
        self.assertEqual((self.notices(), self.record(rig.kb._locks)), ([], (0, 1, 1, False, None)))
        self.locks(NUM)
        self.lock_report()
        rig.settle()
        self.assertEqual(self.notices(), [locks_told(1, num_lock=True)])
        self.assertEqual(len(self.dev.setting_writes), 1)

    def test_only_ever_read(self):
        rig = self.make(locks=True)
        for bits in (CAPS, 0, NUM | SCROLL):
            self.locks(bits)
            self.lock_report()
            rig.settle()
        asked = rig.port.sent(1, self.dev.index(LKS))
        self.assertEqual(len(asked), 4)
        self.assertTrue(all(p[3] >> 4 == 0 and not any(p[4:]) for p in asked))  # function 0, with nothing
        sent = len(rig.port.written)
        rig.kb.set_setting("locks", True)
        rig.kb.set_setting(kbsettings.LOCKS, {"caps_lock": True})
        rig.settle()
        self.assertEqual((rig.port.written[sent:], self.dev.setting_writes), ([], []))

    def test_a_lock_read_that_fails_never_changes_how_a_change_goes(self):
        for failing in (5, None):
            with self.subTest(failing=failing):
                rig = self.make(locks=True)
                self.dev.failing[(LKS, 0)] = failing
                self.locks(CAPS)
                self.lock_report()
                rig.settle()
                start = len(rig.port.written)
                rig.kb.set_setting("fn", True)
                rig.settle()
                self.assertEqual(self.wire(start), FN_CHANGE)  # first, and only that
                self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])
                self.assertEqual(fn(rig), dict(fn(rig), value=True, outcome=None))
                rig.stop()
        kb = backlight_keyboard(extra=[LKS])
        kb.failing[(LKS, 0)] = 5
        rig = self.make(kb, locks=True, notices=True)
        self.lock_report()
        rig.settle()
        rig.kb.set_setting("backlight", {"level": 6})
        rig.settle()
        self.assertEqual([params for feature, params in kb.setting_writes if feature == BL],
                         [bytes.fromhex("0119ff060c0006003c000504")])
        self.assertEqual(control(rig, "backlight")["value"]["level"], 6)
        self.assertIsNone(control(rig, "backlight")["outcome"])

    def test_nothing_of_it_is_logged_saved_or_shown_but_the_notice(self):
        with self.assertLogs("mxoptions.keyboard", logging.DEBUG) as logs, \
                mock.patch.object(config, "save", side_effect=AssertionError("saved")), \
                mock.patch.object(config, "write_durable", side_effect=AssertionError("written")):
            rig = self.make(lock_keyboard(CAPS), locks=True)
            before, exported, journal = copy.deepcopy(rig.engine.cfg), config.export_data(rig.engine.cfg), rig.saved()
            for bits in (CAPS | NUM, SCROLL):
                self.locks(bits)
                self.lock_report()
                rig.settle()
            self.dev.failing[(LKS, 0)] = 5
            self.lock_report()
            rig.settle()
            self.assertEqual(rig.engine.cfg, before)
            self.assertEqual(config.export_data(rig.engine.cfg), exported)
            self.assertEqual(rig.saved(), journal)
        text = "\n".join(logs.output).lower()
        self.assertIn("keyboard lock state not read: hidppfeatureerror", text)
        for private in ("caps", "num_lock", "scroll", "true", "false", "\\x0"):
            self.assertNotIn(private, text)
        state = rig.state()
        self.assertEqual(set(state["controls"]), {"fn"})
        self.assertEqual(state["notice"], locks_told(2, caps_lock=False, num_lock=False, scroll_lock=True))
        json.dumps(state)
        self.prefs(lock_notices=False)
        self.assertIsNone(rig.kb._locks.seen)  # forgotten once off
        del self.dev.failing[(LKS, 0)]
        self.prefs(lock_notices=True)
        rig.settle()
        self.assertEqual(rig.kb._locks.seen, SCROLL)
        rig.link(1, False)
        self.assertIsNone(rig.kb._locks.seen)  # and once gone

    def test_the_mouse_is_heard_while_the_lock_keys_are_slow(self):
        rig = self.make(locks=True)
        mouse = rig.port.devices[2]
        rig.engine.cfg["buttons"]["back"] = actions.keys("LEFTSHIFT")
        self.dev.delays = {LKS: 0.2}
        self.locks(CAPS)
        self.lock_report()
        end = time.monotonic() + 3.0
        while not (rig.kb._asked and rig.kb._asked[0] is kbsettings.lock_state) and time.monotonic() < end:
            try:
                rig.engine._handle(rig.engine._queue.get(timeout=0.02))
            except queue.Empty:
                pass
        self.assertIs(rig.kb._asked[0], kbsettings.lock_state)
        for _ in range(3):
            time.sleep(0.02)
            self.assertIsNotNone(rig.kb._asked)  # the keyboard is still being waited for ...
            rig.mouse_actions.ran.clear()
            start = time.monotonic()
            rig.port.notify(bytes([0x11, 2, mouse.index(device.REPROG_CONTROLS_V4), 0]) + struct.pack("!HHHH", 0x0053, 0, 0, 0).ljust(16, b"\0"))
            while not rig.mouse_actions.ran:  # ... and the mouse is heard meanwhile
                rig.engine._handle(rig.engine._queue.get(timeout=1.0))
            self.assertLess(time.monotonic() - start, 0.1)
            rig.port.notify(bytes([0x11, 2, mouse.index(device.REPROG_CONTROLS_V4), 0]) + bytes(16))
        rig.settle()
        self.assertEqual(self.notices(), [locks_told(1, caps_lock=True)])


class FnNotices(NoticeBase):
    """Fn lock the keyboard reports changed is told once, as a fresh read says it is, where the settings ask for it;
    a change of this program's never, and no change of it waits for, or asks more of the keyboard for, a notice."""

    def spoil(self, how):
        """The next Fn reads fail: refused, an answer that does not fit, or the channel not certain. Returns the mend."""
        dev = self.dev
        where = {"refused": (dev.failing, (HOSTS, 0), 5), "malformed": (dev.replies, (HOSTS, 0), b"\x00\x02\x01"),
                 "channel": (dev.replies, (device.HOSTS_INFO, 0), b"\x00\x00\x00\x01"),
                 "unanswered": (dev.failing, (HOSTS, 0), None)}[how]
        where[0][where[1]] = where[2]
        return lambda: where[0].pop(where[1])

    def test_off_its_reports_are_read_and_settled_without_a_word(self):
        rig = self.make()
        self.fn_is(True)
        self.fn_report()
        rig.settle()
        self.assertEqual((self.notices(), self.record(rig.kb._fn)), ([], (True, 1, 1, False, None)))
        start = len(rig.port.written)
        self.prefs(fn_notices=True)  # on: one read, without a word; nothing told of what came before
        rig.settle()
        self.assertEqual(self.wire(start), FN_READ)
        self.assertEqual((self.notices(), self.record(rig.kb._fn)), ([], (True, 0, 0, False, None)))
        self.fn_is(False)
        self.fn_report()
        rig.settle()
        self.assertEqual(self.notices(), [fn_told(1, False)])

    def test_a_read_that_fails_is_read_once_more_only_where_it_is_told(self):  # off: nothing asked of its own
        for how in ("refused", "malformed", "channel"):
            for on in (False, True):
                with self.subTest(how, fn_notices=on):
                    rig = self.make(fns=on)
                    clock = Later(self)
                    self.spoil(how)
                    self.fn_is(True)
                    start = len(rig.port.written)
                    self.fn_report()
                    rig.settle()
                    one = FN_READ[:1] if how == "channel" else FN_READ
                    self.assertEqual(self.wire(start), one)
                    if on:
                        self.idle()
                    else:
                        self.nothing_due()  # as before notices of it were known: no read of its own
                    self.later(clock, 3)
                    self.assertEqual(self.wire(start), one * (2 if on else 1))
                    self.assertEqual((self.notices(), self.record(rig.kb._fn)),
                                     ([], (False, 1, 0, False, 1 if on else None)))
                    rig.stop()

    def test_a_reported_change_is_told_once(self):
        rig = self.make(fns=True)
        self.assertEqual((self.notices(), self.record(rig.kb._fn)), ([], (False, 0, 0, False, None)))
        self.fn_is(True)
        self.fn_report()
        rig.settle()
        self.assertEqual(self.notice(), fn_told(1, True))
        for _ in range(3):
            self.fn_report()
            rig.settle()
        self.fn_is(False)
        self.fn_report()
        rig.settle()
        self.assertEqual(self.notices(), [fn_told(1, True), fn_told(2, False)])
        self.assertEqual(self.dev.setting_writes, [])

    def test_its_own_change_is_never_told(self):
        for when in ("setter", "read back", "after"):
            with self.subTest(when):
                rig = self.make(fns=True)
                if when == "setter":
                    entered, release = self.hold(lambda request: request[0] is FnCodec.write)
                elif when == "read back":
                    entered, release = self.hold_nth(2, fn_read)
                rig.kb.set_setting("fn", True)
                if when != "after":
                    self.assertTrue(self.reached(entered))
                    self.assertTrue(rig.kb._fn.own)
                    self.fn_report()  # reported while it is sent, or read back
                    release.set()
                rig.settle()
                if when == "after":
                    self.fn_report()
                    rig.settle()
                self.assertEqual((self.notices(), self.record(rig.kb._fn)), ([], (True, 1, 1, False, None)))
                self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])
                rig.stop()

    def test_its_own_change_unconfirmed_is_settled_by_a_read_later(self):
        kb = MuteAfter(controls=(), hosts=(3, 0), settings={device.HOSTS_INFO: 0, HOSTS: {0: [1, 1]}}, extra=[LKS])
        kb.replies[(LKS, 0)] = b"\x00"
        kb.mute = (HOSTS, 1)
        rig = self.make(kb, fns=True)
        clock = Later(self)
        rig.kb.set_setting("fn", True)
        rig.settle()
        self.assertEqual(fn(rig)["outcome"], "unconfirmed")
        self.assertEqual(self.record(rig.kb._fn), (False, 0, 0, True, None))
        self.idle()
        kb.silent = False
        self.later(clock)
        self.assertEqual(self.record(rig.kb._fn), (True, 0, 0, False, None))
        self.assertEqual((self.notices(), len(kb.setting_writes)), ([], 1))
        self.later(clock, 2)
        self.assertEqual(len(packets(rig.port, kb, HOSTS, 0)), 4)  # found, before, read back, later: no more
        self.fn_is(False)
        self.fn_report()
        rig.settle()
        self.assertEqual(self.notices(), [fn_told(1, False)])

    def test_not_taken_or_read_back_wrongly_its_change_is_never_told(self):
        rig = self.make(fns=True)
        clock = Later(self)
        self.dev.ignore_writes = True
        rig.kb.set_setting("fn", True)
        rig.settle()
        self.assertEqual(fn(rig)["outcome"], "not-applied")
        self.assertEqual(self.record(rig.kb._fn), (False, 0, 0, False, None))  # the read back settled it
        self.dev.ignore_writes = False
        self.dev.taken[HOSTS] = [2]  # takes a state that does not fit: read back wrongly
        rig.kb.set_setting("fn", True)
        rig.settle()
        self.assertEqual(fn(rig)["outcome"], "unconfirmed")
        self.assertEqual(self.record(rig.kb._fn), (False, 0, 0, True, 0))
        self.idle()
        self.fn_is(True)
        self.later(clock)
        self.assertEqual(self.record(rig.kb._fn), (True, 0, 0, False, 0))
        self.assertEqual((self.notices(), len(self.dev.setting_writes)), ([], 2))  # each sent once, never again
        self.later(clock, 2)
        self.nothing_due()

    def test_a_report_while_it_is_read_is_settled_by_the_read_after_it(self):
        rig = self.make(fns=True)
        first, release_first = self.hold_nth(1, fn_read)
        second, release_second = self.hold_nth(2, fn_read)
        self.fn_is(True)
        self.fn_report()
        self.assertTrue(self.reached(first))
        self.fn_report()
        release_first.set()
        self.assertTrue(self.reached(second))
        self.assertEqual((self.notices(), self.record(rig.kb._fn)), ([], (False, 2, 0, False, None)))
        release_second.set()
        rig.settle()
        self.assertEqual((self.notices(), self.record(rig.kb._fn)), ([fn_told(1, True)], (True, 2, 2, False, None)))

    def test_a_read_that_does_not_fit_or_fails_is_read_once_more(self):
        for how in ("refused", "malformed", "channel"):
            with self.subTest(how):
                rig = self.make(fns=True)
                clock = Later(self)
                self.fn_is(True)
                mend = self.spoil(how)
                self.fn_report()
                rig.settle()
                self.assertEqual((self.notices(), self.record(rig.kb._fn)), ([], (False, 1, 0, False, 1)))
                self.idle()
                tries = self.fn_tries()
                mend()
                self.later(clock)
                self.assertEqual(self.notices(), [fn_told(1, True)])  # against what was settled before
                self.later(clock, 2)
                self.assertEqual(self.fn_tries(), tries + 1)
                rig.stop()

    def test_other_software_seen_after_its_change_a_read_settles_it_without_a_word(self):
        rig = self.make(fns=True)
        entered, release = self.hold_nth(2, fn_read)
        rig.kb.set_setting("fn", True)
        self.assertTrue(self.reached(entered))
        Guarded.rivals(self, "Solaar")
        self.assertEqual(fn(rig)["outcome"], "unconfirmed")
        self.assertTrue(rig.kb._fn.own)
        release.set()
        rig.settle()
        self.assertEqual((self.notices(), self.record(rig.kb._fn)), ([], (True, 0, 0, False, None)))
        self.fn_is(False)
        self.fn_report()
        rig.settle()
        self.assertEqual(self.notices(), [fn_told(1, False)])
        self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])  # never sent again or back

    def test_gone_during_its_change_and_found_again_it_says_nothing(self):
        rig = self.make(fns=True)
        entered, release = self.hold_nth(2, fn_read)
        rig.kb.set_setting("fn", True)
        self.assertTrue(self.reached(entered))
        rig.link(1, False)
        release.set()
        self.drain()
        self.assertEqual(self.record(rig.kb._fn), (None, 0, 0, False, None))
        rig.link(1, True)
        rig.settle()
        self.assertEqual((self.notices(), self.record(rig.kb._fn)), ([], (True, 0, 0, False, None)))
        self.fn_is(False)
        self.fn_report()
        rig.settle()
        self.assertEqual(self.notices(), [fn_told(1, False)])
        self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])

    def test_a_change_the_keyboard_makes_later_is_told_as_it_is_never_by_whom(self):
        rig = self.make(fns=True)
        self.dev.ignore_writes = True
        rig.kb.set_setting("fn", True)
        rig.settle()
        self.assertEqual(fn(rig)["outcome"], "not-applied")
        self.fn_is(True)  # it takes it after all, and reports it
        self.fn_report()
        rig.settle()
        self.assertEqual(self.notices(), [fn_told(1, True)])
        self.assertEqual(set(self.notice()), {"seq", "kind", "value"})

    def test_its_change_asks_what_it_did_before_whatever_is_told(self):
        for locks, fns in itertools.product((False, True), repeat=2):
            with self.subTest(locks=locks, fns=fns):
                rig = self.make(locks=locks, fns=fns)
                start = len(rig.port.written)
                rig.kb.set_setting("fn", True)
                rig.settle()
                self.assertEqual(self.wire(start), FN_CHANGE)
                rig.stop()

    def test_a_report_while_its_change_is_read_back_waits_for_a_read_that_counts(self):
        rig = self.make(fns=True)
        back, release_back = self.hold_nth(2, fn_read)
        due, release_due = self.hold_nth(3, fn_read)
        rig.kb.set_setting("fn", True)
        self.assertTrue(self.reached(back))  # its read back, the setter gone
        self.assertEqual(self.record(rig.kb._fn), (False, 0, 0, True, None))
        self.fn_report()
        release_back.set()  # True, overtaken by the report
        self.assertTrue(self.reached(due))  # the read the report made due, once the change is over
        self.assertEqual(self.record(rig.kb._fn), (False, 1, 0, True, None))  # nothing settled
        self.fn_is(False)  # changed on the keyboard again meanwhile
        release_due.set()
        rig.settle()
        self.assertEqual((self.notices(), self.record(rig.kb._fn)), ([], (False, 1, 1, False, None)))
        self.fn_is(True)
        self.fn_report()
        rig.settle()
        self.assertEqual(self.notices(), [fn_told(1, True)])
        self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])

    def test_a_read_after_its_change_that_fails_is_read_once_more(self):
        for how in ("refused", "malformed", "channel", "unanswered"):
            for twice in (False, True):
                with self.subTest(how, twice=twice):
                    rig = self.make(fns=True)
                    clock = Later(self)
                    back, release_back = self.hold_nth(2, fn_read)
                    due, release_due = self.hold_nth(3, fn_read)
                    rig.kb.set_setting("fn", True)
                    self.assertTrue(self.reached(back))
                    self.fn_report()
                    if how == "channel":  # asked before the read the report makes due
                        mend = self.spoil(how)
                        release_due.set()
                    release_back.set()
                    if how != "channel":
                        self.assertTrue(self.reached(due))
                        mend = self.spoil(how)
                        release_due.set()
                    rig.settle()
                    timeout = how == "unanswered"
                    self.assertEqual((self.notices(), self.record(rig.kb._fn)),
                                     ([], (False, 1, 0, True, None if timeout else 1)))
                    self.idle()
                    tries = self.fn_tries()
                    if twice:
                        self.later(clock)  # fails again
                        self.assertEqual(self.fn_tries(), tries + 1)
                        self.assertEqual(self.record(rig.kb._fn), (False, 1, 0, True, None if timeout else 1))
                        if timeout:
                            self.idle()  # unanswered: once each RECOVERY_RETRY, as for every setting
                            self.later(clock)
                            self.assertEqual(self.fn_tries(), tries + 2)
                            tries += 1
                        else:
                            self.nothing_due()
                            self.later(clock, 3)
                            self.assertEqual(self.fn_tries(), tries + 1)  # no more
                        mend()
                        if timeout:
                            self.later(clock)
                        else:
                            self.fn_is(False)
                            self.fn_report()  # the next report: settled as its own, without a word
                            rig.settle()
                        self.assertEqual(self.notices(), [])
                        self.assertFalse(rig.kb._fn.own)
                    else:
                        mend()
                        self.later(clock)
                        self.assertEqual(self.fn_tries(), tries + 1)
                        self.assertEqual(self.record(rig.kb._fn), (True, 1, 1, False, None if timeout else 1))
                        self.assertEqual(self.notices(), [])
                    value = not rig.kb._fn.seen
                    self.fn_is(value)
                    self.fn_report()
                    rig.settle()
                    self.assertEqual(self.notices(), [fn_told(1, value)])
                    self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])
                    rig.stop()

    def test_turned_on_while_a_read_from_before_is_held_it_tells_nothing_of_it(self):
        rig = self.make()
        entered, release = self.hold(fn_read)
        self.fn_is(True)
        self.fn_report()
        self.assertTrue(self.reached(entered))
        old = rig.kb._fn
        self.prefs(fn_notices=True)
        self.assertEqual(self.record(rig.kb._fn), (None, 0, 0, False, None))
        reads = self.fn_reads()
        release.set()
        rig.settle()
        self.assertEqual(self.fn_reads(), reads + 2)  # the one held, then the new record's first, without a word
        self.assertEqual((self.notices(), self.record(rig.kb._fn)), ([], (True, 0, 0, False, None)))
        self.assertEqual(self.record(old), (False, 1, 0, False, None))  # nothing settled on the old one either
        self.fn_is(False)
        self.fn_report()
        rig.settle()
        self.assertEqual(self.notices(), [fn_told(1, False)])

    def test_turned_off_and_on_while_a_read_is_held_it_settles_nothing(self):
        rig = self.make(fns=True)
        entered, release = self.hold(fn_read)
        self.fn_is(True)
        self.fn_report()
        self.assertTrue(self.reached(entered))
        self.prefs(fn_notices=False)
        self.prefs(fn_notices=True)
        reads = self.fn_reads()
        release.set()
        rig.settle()
        self.assertEqual(self.fn_reads(), reads + 2)
        self.assertEqual((self.notices(), self.record(rig.kb._fn)), ([], (True, 0, 0, False, None)))
        self.fn_is(False)
        self.fn_report()
        rig.settle()
        self.assertEqual(self.notices(), [fn_told(1, False)])

    def test_turned_off_while_a_read_is_held_its_failure_asks_nothing_more(self):
        for back_on in (False, True):
            with self.subTest(back_on=back_on):
                rig = self.make(fns=True)
                clock = Later(self)
                entered, release = self.hold(fn_read)
                self.fn_is(True)
                self.fn_report()
                self.assertTrue(self.reached(entered))
                old = rig.kb._fn
                self.prefs(fn_notices=False)
                if back_on:
                    self.prefs(fn_notices=True)
                self.dev.failing[(HOSTS, 0)] = 5
                release.set()
                rig.settle()
                self.assertEqual(self.record(old), (False, 1, 0, False, None))  # not even tried again for it
                self.nothing_due()
                tries = self.fn_tries()
                self.later(clock, 3)
                self.assertEqual(self.fn_tries(), tries)
                self.assertEqual(self.notices(), [])
                rig.stop()

    def test_a_report_before_it_is_turned_on_is_settled_afresh(self):
        rig = self.make(lock_keyboard(disable=True))
        entered, release = self.hold(lambda request: request[0] is DisableCodec.write)
        baseline, release_baseline = self.hold_nth(1, fn_read)
        rig.kb.set_setting("disable", {"caps_lock": True})
        self.assertTrue(self.reached(entered))
        self.fn_is(True)
        self.fn_report()  # its read waits for the change under way
        self.prefs(fn_notices=True)
        release.set()
        self.assertTrue(self.reached(baseline))
        self.fn_is(False)
        self.fn_report()  # while the first read of the new record is held
        release_baseline.set()
        rig.settle()
        self.assertEqual((self.notices(), self.record(rig.kb._fn)), ([], (False, 1, 1, False, None)))
        self.fn_is(True)
        self.fn_report()
        rig.settle()
        self.assertEqual(self.notices(), [fn_told(1, True)])

    def test_its_own_change_stays_its_own_across_a_turn_on_or_off(self):
        for start_on, turns in ((True, (False, True)), (False, (True,))):
            for fails in (False, True):
                with self.subTest(start_on=start_on, fails=fails):
                    rig = self.make(fns=start_on)
                    clock = Later(self)
                    back, release_back = self.hold_nth(2, fn_read)
                    first, release_first = self.hold_nth(3, fn_read)
                    rig.kb.set_setting("fn", True)
                    self.assertTrue(self.reached(back))
                    for on in turns:
                        self.prefs(fn_notices=on)
                    self.assertEqual(self.record(rig.kb._fn), (None, 0, 0, True, None))  # still its own
                    release_back.set()
                    self.assertTrue(self.reached(first))
                    self.assertEqual(self.record(rig.kb._fn), (None, 0, 0, True, None))  # the read back: nothing
                    if fails:
                        self.dev.failing[(HOSTS, 0)] = 5
                    release_first.set()
                    rig.settle()
                    if fails:
                        self.assertEqual(self.record(rig.kb._fn), (None, 0, 0, True, 0))
                        self.idle()  # read once more for it
                        del self.dev.failing[(HOSTS, 0)]
                        self.later(clock)
                    self.assertEqual((self.notices(), self.record(rig.kb._fn)),
                                     ([], (True, 0, 0, False, 0 if fails else None)))
                    self.nothing_due()
                    self.fn_is(False)
                    self.fn_report()
                    rig.settle()
                    self.assertEqual(self.notices(), [fn_told(1, False)])
                    self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])
                    rig.stop()

    def test_a_reset_keeps_its_own_change_its_own_and_asks_nothing(self):
        rig = self.make(locks=True, fns=True)
        back, release_back = self.hold_nth(2, fn_read)
        rig.kb.set_setting("fn", True)
        self.assertTrue(self.reached(back))
        start, record = len(rig.port.written), rig.kb._fn
        new = copy.deepcopy(rig.engine.cfg)
        new["keyboard"] = dict(new["keyboard"], keys={}, profiles=[])  # the keys reset, the notices as they were
        rig.engine._apply_config(new)
        self.assertIs(rig.kb._fn, record)
        new = copy.deepcopy(rig.engine.cfg)
        new["keyboard"] = copy.deepcopy(config.DEFAULTS["keyboard"])  # all as at first: the notices off too
        rig.engine._apply_config(new)
        self.assertEqual((self.record(rig.kb._fn), rig.kb._locks.seen), ((None, 0, 0, True, None), None))
        self.assertEqual(rig.port.written[start:], [])  # neither asks the keyboard anything
        release_back.set()
        rig.settle()
        self.assertEqual(self.record(rig.kb._fn), (None, 0, 0, True, None))  # still its own
        self.fn_report()
        rig.settle()
        self.assertEqual((self.notices(), self.record(rig.kb._fn)), ([], (True, 1, 1, False, None)))
        self.assertEqual(self.dev.setting_writes, [(HOSTS, b"\x00\x00")])

    def test_turned_on_while_connected_it_is_read_once_and_otherwise_never(self):
        rig = self.make()
        start = len(rig.port.written)
        self.prefs(fn_notices=True)
        rig.settle()
        self.assertEqual(self.wire(start), FN_READ)
        for change in ({"fn_notices": True}, {"notices": True}, {"fn_notices": False}, {"notices": False}):
            start = len(rig.port.written)
            self.prefs(**change)
            rig.settle()
            self.assertEqual(self.wire(start), [], change)
        start = len(rig.port.written)
        self.prefs(fn_notices=True, lock_notices=True)  # both at once: one read
        rig.settle()
        self.assertEqual(self.wire(start), FN_READ + [(LKS, 0)])
        start = len(rig.port.written)
        rig.kb.set_setting("fn", True)
        rig.settle()
        self.assertEqual(self.wire(start), FN_CHANGE)
        self.assertEqual(self.notices(), [])
        rig.link(1, False)
        start = len(rig.port.written)
        self.prefs(fn_notices=False)
        self.prefs(fn_notices=True)
        self.assertEqual(rig.port.written[start:], [])  # gone: it is read once it is found again anyway
        rig.stop()
        kb = keyboard_device(extra=[LKS])  # the lock keys, and no Fn lock
        kb.replies[(LKS, 0)] = b"\x00"
        rig = self.make(kb)
        start = len(rig.port.written)
        self.prefs(fn_notices=True)
        rig.settle()
        self.assertEqual(rig.port.written[start:], [])
        self.prefs(lock_notices=True)
        rig.settle()
        self.assertEqual(self.wire(start), [(LKS, 0)])


class NoticesTogether(NoticeBase):
    def test_two_changes_in_one_read_are_two_notices_one_after_the_other(self):
        rig = self.make(locks=True, fns=True)
        entered, release = self.hold(fn_read)
        self.fn_is(True)
        self.locks(CAPS)
        self.fn_report()
        self.assertTrue(self.reached(entered))
        self.lock_report()
        release.set()
        rig.settle()
        self.assertEqual(self.notices(), [fn_told(1, True), locks_told(2, caps_lock=True)])  # the window saw both
        rig.stop()
        kb = backlight_keyboard(extra=[LKS])
        kb.replies[(LKS, 0)] = b"\x00"
        rig = self.make(kb, locks=True, notices=True)
        entered, release = self.hold(lambda request: request[0] is Backlight.read)
        self.dev.settings[BL]["info"][1] = 6
        rig.note(1, self.dev, BL, b"\x00\x07")
        self.assertTrue(self.reached(entered))
        self.locks(NUM)
        self.lock_report()
        release.set()
        rig.settle()
        self.assertEqual(self.notices(), [told(1, kind="backlight", level=6, top=7), locks_told(2, num_lock=True)])


class TrayMessages(unittest.TestCase):
    """What the tray says of the keyboard's notices and its battery (app.tray_messages)."""

    def setUp(self):
        from mxoptions import app, i18n
        self.app, self.i18n = app, i18n

    def found(self, notice=None, battery=(80, device.BATTERY_DISCHARGING), connected=True):
        return {"connected": connected, "name": "Synthetic keyboard", "battery": battery, "notice": notice}

    def test_the_newest_notice_first_and_a_low_battery_last_each_once(self):
        low, notice = (10, device.BATTERY_DISCHARGING), locks_told(1, caps_lock=True)
        armed, seen, messages = self.app.tray_messages(True, 0, self.found(notice, low))
        self.assertEqual((armed, seen, messages), (False, 1, [
            ("Caps Lock on", "info"), ("The battery of Synthetic keyboard is low (10%). Charge it soon.", "warning")]))
        self.assertEqual(self.app.tray_messages(armed, seen, self.found(notice, low)), (False, 1, []))  # once
        self.assertEqual(self.app.tray_messages(True, 0, self.found(notice, connected=False)), (True, 0, []))
        self.assertEqual(self.app.tray_messages(True, 0, None), (True, 0, []))
        for odd in ({"kind": "other"}, {"kind": "locks", "keys": {"insert": True}}, {"kind": "locks", "keys": {}},
                    {"kind": "locks", "keys": ["caps_lock"]}, {"kind": "fn", "value": 1}, {"kind": "fn"}):
            with self.subTest(odd=odd):  # not known here: taken, and nothing said
                self.assertEqual(self.app.tray_messages(True, 4, self.found(told(5, **odd))), (True, 5, []))

    def test_each_kind_in_words_in_english_and_ukrainian(self):
        cases = ((told(1, kind="backlight", level=3, top=7), "Keyboard backlight: level 3 of 7",
                  "Підсвітка клавіатури: рівень 3 з 7"),
                 (fn_told(1, True), "Fn lock on: the top row gives F1–F12", "Фіксацію Fn увімкнено: верхній ряд дає F1–F12"),
                 (fn_told(1, False), "Fn lock off: the top row gives its other functions",
                  "Фіксацію Fn вимкнено: верхній ряд дає інші функції"),
                 (locks_told(1, caps_lock=True, num_lock=False), "Caps Lock on, Num Lock off",
                  "Caps Lock увімкнено, Num Lock вимкнено"),
                 (locks_told(1, scroll_lock=True), "Scroll Lock on", "Scroll Lock увімкнено"))
        self.assertEqual([self.app.notice_message(notice) for notice, _en, _uk in cases], [en for _n, en, _uk in cases])
        self.addCleanup(self.i18n.setup, "en")
        self.i18n.setup("uk")
        self.assertEqual([self.app.notice_message(notice) for notice, _en, _uk in cases], [uk for _n, _en, uk in cases])

    MOUSE_LOW = "The battery of Synthetic mouse is low ({}%). Charge it soon."

    def mouse(self, battery=(80, device.BATTERY_DISCHARGING), connected=True):
        return {"connected": connected, "name": "Synthetic mouse", "battery": battery}

    def test_a_low_mouse_is_said_once_as_the_keyboard_is(self):
        low = (15, device.BATTERY_DISCHARGING)
        self.assertEqual(self.app.mouse_messages(True, self.mouse(low), []), (False, [(self.MOUSE_LOW.format(15), "warning")]))
        self.assertEqual(self.app.mouse_messages(False, self.mouse((9, device.BATTERY_DISCHARGING)), []), (False, []))  # once
        self.assertEqual(self.app.mouse_messages(True, self.mouse((16, device.BATTERY_DISCHARGING)), []), (True, []))
        self.assertEqual(self.app.mouse_messages(False, self.mouse((10, device.BATTERY_CHARGING)), []), (True, []))  # re-armed
        self.assertEqual(self.app.mouse_messages(False, self.mouse((16, device.BATTERY_DISCHARGING)), []), (True, []))

    def test_a_mouse_that_is_away_or_tells_no_level_is_not_said_and_keeps_its_latch(self):
        for armed in (True, False):
            for mouse in (self.mouse((10, 0), connected=False), self.mouse((0, 0)), self.mouse(None), {}):
                with self.subTest(armed=armed, mouse=mouse):
                    self.assertEqual(self.app.mouse_messages(armed, mouse, []), (armed, []))
        armed, _said = self.app.mouse_messages(True, self.mouse((12, 0)), [])
        armed, _gone = self.app.mouse_messages(armed, self.mouse((12, 0), connected=False), [])
        self.assertEqual(self.app.mouse_messages(armed, self.mouse((12, 0)), []), (False, []))  # back, still low: not again

    def test_both_low_at_once_is_one_warning_after_the_notice(self):
        notice, low = locks_told(1, caps_lock=True), (10, device.BATTERY_DISCHARGING)
        armed, seen, messages = self.app.tray_messages(True, 0, self.found(notice, low))
        mouse_armed, messages = self.app.mouse_messages(True, self.mouse((12, 0)), messages)
        self.assertEqual((armed, seen, mouse_armed), (False, 1, False))
        self.assertEqual(messages, [("Caps Lock on", "info"), (
            "The battery of Synthetic keyboard is low (10%). Charge it soon.\n" + self.MOUSE_LOW.format(12), "warning")])
        _armed, _seen, messages = self.app.tray_messages(True, 1, self.found(notice))
        self.assertEqual(self.app.mouse_messages(True, self.mouse((12, 0)), messages),
                         (False, [(self.MOUSE_LOW.format(12), "warning")]))  # after a notice alone: its own warning

    def test_each_one_low_later_than_the_other_is_said_alone(self):
        low = (10, device.BATTERY_DISCHARGING)
        kb_armed, _seen, messages = self.app.tray_messages(True, 0, self.found(None, low))
        mouse_armed, messages = self.app.mouse_messages(True, self.mouse(), messages)
        self.assertEqual((kb_armed, mouse_armed, [kind for _text, kind in messages]), (False, True, ["warning"]))
        kb_armed, _seen, messages = self.app.tray_messages(kb_armed, 0, self.found(None, low))
        mouse_armed, messages = self.app.mouse_messages(mouse_armed, self.mouse(low), messages)
        self.assertEqual((kb_armed, mouse_armed, messages), (False, False, [(self.MOUSE_LOW.format(10), "warning")]))
        mouse_armed, _messages = self.app.mouse_messages(True, self.mouse(low), [])
        kb_armed, _seen, messages = self.app.tray_messages(True, 0, self.found(None, low))
        self.assertEqual(self.app.mouse_messages(mouse_armed, self.mouse(low), messages)[1],
                         [("The battery of Synthetic keyboard is low (10%). Charge it soon.", "warning")])

    def test_the_latches_are_each_their_own(self):
        low, charging = (10, device.BATTERY_DISCHARGING), (10, device.BATTERY_CHARGING)
        kb_armed, _seen, _messages = self.app.tray_messages(True, 0, self.found(None, low))
        mouse_armed, _messages = self.app.mouse_messages(True, self.mouse(low), [])
        mouse_armed, _messages = self.app.mouse_messages(mouse_armed, self.mouse(charging), [])  # the mouse charges
        self.assertEqual((kb_armed, mouse_armed), (False, True))
        self.assertEqual(self.app.tray_messages(kb_armed, 0, self.found(None, low))[2], [])  # the keyboard: not again
        kb_armed, _seen, _messages = self.app.tray_messages(kb_armed, 0, self.found(None, charging))
        self.assertEqual(self.app.mouse_messages(False, self.mouse(low), []), (False, []))  # the mouse: not again
        self.assertTrue(kb_armed)

    def test_the_mouse_in_ukrainian(self):
        self.addCleanup(self.i18n.setup, "en")
        self.i18n.setup("uk")
        low = (10, device.BATTERY_DISCHARGING)
        _armed, _seen, messages = self.app.tray_messages(True, 0, self.found(None, low))
        self.assertEqual(self.app.mouse_messages(True, self.mouse(low), messages)[1], [(
            "Акумулятор Synthetic keyboard майже розряджений (10%). Зарядіть його найближчим часом.\n"
            "Акумулятор Synthetic mouse майже розряджений (10%). Зарядіть його найближчим часом.", "warning")])


class NoticePage(unittest.TestCase):
    """The notice switches for Fn lock and the lock keys on the Keyboard page (Qt offscreen)."""

    def setUp(self):
        Page.setUp(self)

    def show(self, settings=("fn", "locks"), fn_control=True, connected=True):
        controls = {"fn": {"value": True, "default": None, "writable": True, "busy": False, "outcome": None}}
        found = {"connected": connected, "known": True, "name": "Synthetic keyboard", "battery": None, "current_host": 0,
                 "hosts": [], "keys": [], "reason": None, "failed": None, "firmware": None, "settings": list(settings),
                 "controls": controls if fn_control else {}, "notice": None}
        self.store.update_state(dict(self.store.state, keyboard=found))

    def test_each_switch_where_the_keyboard_has_what_it_is_for(self):
        page = self.page
        rows = page.settings_card._rows
        self.assertEqual(rows.index(page.fn_notices_row), rows.index(page.settings_rows["fn"]) + 1)  # under Fn lock
        self.assertIs(page.settings_rows["locks"].control, page.lock_notices)
        for shown, fn_row, locks_row in ((dict(), True, True), (dict(fn_control=False), False, True),
                                         (dict(connected=False), False, True), (dict(settings=("fn",)), True, False),
                                         (dict(settings=("locks",), fn_control=False), False, True)):
            with self.subTest(shown=shown):
                self.show(**shown)
                self.assertEqual((not page.fn_notices_row.isHidden(), not page.settings_rows["locks"].isHidden()),
                                 (fn_row, locks_row))

    def test_the_switches_are_settings_of_this_program_only(self):
        page = self.page
        self.show()
        for switch, name in ((page.fn_notices, "fn_notices"), (page.lock_notices, "lock_notices")):
            with self.subTest(name):
                self.assertFalse(switch.isChecked())
                switch.click()
                self.assertIs(self.store.cfg["keyboard"][name], True)
                before = copy.deepcopy(self.store.cfg)
                for _ in range(3):
                    self.show()
                self.assertEqual(self.store.cfg, before)  # shown again: nothing changed by it
                self.assertTrue(switch.isChecked())
                self.store.set("keyboard", name, False)  # changed elsewhere (a settings file): followed
                self.assertFalse(switch.isChecked())
        self.assertIs(self.store.cfg["keyboard"]["notices"], False)  # the brightness one stays as it was
        self.assertEqual(self.calls, [])  # the keyboard is never asked

    def test_names_for_assistive_technology_tab_and_words_translated(self):
        from PySide6.QtCore import Qt
        from mxoptions import i18n_uk
        page = self.page
        self.show()
        self.assertEqual(page.fn_notices.accessibleName(), "Show a notice when Fn lock changes on the keyboard")
        self.assertEqual(page.fn_notices_row.title.text(), "Show a notice when Fn lock changes on the keyboard")
        self.assertEqual(page.lock_notices.accessibleName(), "Show a notice when a lock key turns on or off")
        note = page.settings_rows["locks"].note.text()
        self.assertEqual(note, "The keyboard reports when Caps Lock, Num Lock or Scroll Lock turns on or off. MX Options "
                               "can show a notice then; it never saves or logs their state.")
        wanted, reached, widget = [page.fn_switch, page.fn_notices, page.lock_notices], [], page.fn_switch
        for _ in range(500):
            if widget.isVisibleTo(page) and widget.isEnabled() and widget.focusPolicy() in (
                    Qt.FocusPolicy.TabFocus, Qt.FocusPolicy.StrongFocus, Qt.FocusPolicy.WheelFocus):
                reached.append(widget)
            widget = widget.nextInFocusChain()
            if widget is page.fn_switch:
                break
        self.assertEqual([control for control in reached if control in wanted], wanted)
        words = [note, "Show a notice when Fn lock changes on the keyboard", "Show a notice when a lock key turns on or off",
                 "Fn lock on: the top row gives F1–F12", "Fn lock off: the top row gives its other functions",
                 "{key} on", "{key} off"]
        for text in words:
            self.assertIn(text, i18n_uk.STRINGS)
            self.assertNotRegex(text + i18n_uk.STRINGS[text], r"0x|4220|40[aA]\d|\bbit|\bbyte")


if __name__ == "__main__":
    unittest.main()
