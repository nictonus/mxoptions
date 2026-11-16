"""The keyboard next to the mouse: found on the receiver or a node of its own, its keys diverted and given
back safely, its actions kept apart from the mouse's. The engine runs on the test's thread with real
transports on FakePorts; the devices are synthetic (tests/support.py)."""

import copy
import errno
import hashlib
import json
import logging
import os
import queue
import shutil
import stat
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import tests.support as tests_support
from tests.support import Actions, Clock, FakeDevice, FakeInput, FakePort, Recorder
from tests.test_transport import AGGREGATE, MODS, ZERO, KeyNode, bluetooth, count, inp, lmax, page, report, size, umax, umin
from mxoptions import actions, config, device, engine, hidpp, keyboard

# Synthetic controls: (cid, tid, getCidInfo flags). 0x30 = reprogrammable and divertable, 0x32 also on the F-row,
# 0x10 reprogrammable only. 0x00D1 and 0x0034 are the Easy-Switch 1 and Fn ids the program keeps away from.
CONTROLS = [(0x00D1, 0x00D1, 0x30), (0x0034, 0x0034, 0x30), (0x0F01, 0x0F01, 0x30), (0x0F02, 0x0F02, 0x32),
            (0x0F03, 0x0F03, 0x10), (0x0F04, 0x0F04, 0x30), (0x0053, 0x0053, 0x30)]
COPY = actions.keys("LEFTCTRL", "C")
CTRL = actions.keys("LEFTCTRL")
UNIT_A, UNIT_B = b"\x5e\x01\x5e\x02", b"\x5e\x03\x5e\x04"
MODEL = b"\x0a\x0b\x0c\x0d\x0e\x0f"
PATH, OTHER_PATH = "/dev/synthetic0", "/dev/synthetic1"


def identity(unit, model=MODEL):
    return hashlib.sha256(b"mxoptions keyboard\0" + model + unit).hexdigest()[:16]


def keyboard_device(unit=UNIT_A, **kwargs):
    return FakeDevice(controls=CONTROLS, unit=unit, model=MODEL, **kwargs)


def mouse_device():
    return FakeDevice("Synthetic mouse", kind=device.KIND_MOUSE, controls=[(0x0053, 0x0053, 0x30)])


class Rig:
    """An engine on the test's thread with one or two synthetic nodes; ``settle()`` runs what the engine's
    thread would until the looking and the keyboard's task are over."""

    def __init__(self, test, slots, keys=None, journal=None, rivals=(), receiver=True, nodes=None):
        self.test = test
        folder = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, folder, True)
        self.journal = os.path.join(folder, "keyboard-diverted.json")
        if journal is not None:
            with open(self.journal, "w") as f:
                f.write(journal if isinstance(journal, str) else json.dumps(journal))
        patches = [mock.patch.object(keyboard, "JOURNAL", self.journal),
                   mock.patch.object(engine.environment, "rivals", lambda: list(self.rivals))]
        self.rivals = list(rivals)
        self.ports = {PATH: FakePort(slots, receiver=receiver)}
        self.nodes = nodes if nodes is not None else [(PATH, receiver)]
        patches += [mock.patch.object(hidpp, "find_hidpp_nodes", lambda: list(self.nodes)),
                    mock.patch.object(hidpp.hid, "Port", lambda path, *_args, **_kwargs: self.ports[path])]
        for patcher in patches:
            patcher.start()
            test.addCleanup(patcher.stop)
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["keyboard"]["keys"] = copy.deepcopy(keys or {})
        self.listener = Recorder()
        self.engine = made = engine.Engine(cfg, self.listener)
        made.vinput = self.input = FakeInput()
        made.executor = self.mouse_actions = Actions()
        made._rivals = list(rivals)
        made._next_rivals = float("inf")
        self.states = []
        made._publish = lambda: self.states.append(made.keyboard.state())
        made.keyboard.start()
        test.addCleanup(self.stop)

    @property
    def port(self):
        return self.ports[PATH]

    @property
    def kb(self):
        return self.engine.keyboard

    def transport(self, path=PATH):
        return self.engine._transports[path]

    def stop(self):
        if self.kb._thread.is_alive():
            self.kb._cancel.set()
            self.kb._called_off.set()
            self.kb._jobs.put(None)
            self.kb._thread.join(2.0)
        for transport in list(self.engine._transports.values()):
            transport.close()

    def settle(self, timeout=8.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                item = self.engine._queue.get(timeout=0.05)
            except queue.Empty:
                if self.kb._task is None and not any(what == "pending" for states in self.kb._slots.values()
                                                     for what in states.values()):
                    return
                continue
            self.engine._handle(item)
        raise AssertionError("the looking or the keyboard's task never ended")

    def start(self):
        """As the engine starts: look for the mouse, and the keyboard with it."""
        self.engine._scan()
        self.settle()
        return self

    def note(self, devidx, dev, feature, data, path=PATH):
        """The device on ``devidx`` reports something (handled at once)."""
        self.ports[path].notify(bytes([0x11, devidx, dev.index(feature), 0x00]) + bytes(data).ljust(16, b"\0"))
        item = self.engine._queue.get(timeout=2.0)
        self.engine._handle(item)

    def keys_down(self, devidx, dev, *cids, path=PATH):
        self.note(devidx, dev, device.REPROG_CONTROLS_V4, struct.pack("!HHHH", *(list(cids) + [0, 0, 0, 0])[:4]), path)

    def link(self, devidx, linked, path=PATH):
        self.ports[path].notify(bytes([0x10, devidx, 0x41, 0x04, 0x00 if linked else 0x40, 0, 0]))
        self.engine._handle(self.engine._queue.get(timeout=2.0))

    def saved(self):
        try:
            with open(self.journal) as f:
                return json.load(f)
        except FileNotFoundError:
            return None

    def state(self):
        return self.kb.state()

    def key(self, cid):
        return next(key for key in self.state()["keys"] if key["cid"] == cid)


def diverts(dev):
    """The keys a device was told to divert (setCidReporting with the divert bit) and to give back."""
    return [(cid, bool(flags & 0x01)) for cid, flags in dev.sets]


class Finding(unittest.TestCase):
    def test_a_keyboard_before_the_mouse_is_taken_from_what_was_loaded(self):
        kb, mouse = keyboard_device(), mouse_device()
        rig = Rig(self, {1: kb, 2: mouse}).start()
        self.assertIs(rig.engine.mouse.devidx, 2)
        self.assertEqual(rig.kb.device.devidx, 1)
        self.assertEqual(len(rig.port.sent(1, kb.index(device.FEATURE_SET), 0)), 1)  # loaded once only

    def test_a_keyboard_after_the_mouse_is_found_without_a_word_from_the_receiver(self):
        rig = Rig(self, {1: mouse_device(), 2: keyboard_device()}).start()
        self.assertEqual((rig.engine.mouse.devidx, rig.kb.device.devidx), (1, 2))
        self.assertTrue(rig.state()["connected"])
        self.assertEqual(rig.state()["battery"], (80, device.BATTERY_DISCHARGING))
        self.assertEqual(len(rig.state()["hosts"]), 3)

    def test_other_devices_are_never_taken_for_a_keyboard(self):
        rig = Rig(self, {1: mouse_device(), 2: FakeDevice("Synthetic numpad", kind=2),
                         3: FakeDevice("Synthetic presenter", kind=6), 4: FakeDevice("Synthetic mouse 2", kind=device.KIND_MOUSE)})
        rig.start()
        self.assertIsNone(rig.kb.device)
        self.assertIsNone(rig.state())

    def test_nothing_is_asked_again_once_every_slot_is_known(self):
        rig = Rig(self, {2: mouse_device()}).start()
        sent = len(rig.port.written)
        with Clock(time.monotonic()) as clock:
            for _ in range(600):
                clock.now += 1.0
                rig.engine._tick()
        self.assertEqual(len(rig.port.written), sent)
        rig.port.devices[3] = keyboard_device()
        rig.link(3, True)  # one slot, and only that one, is asked
        rig.settle()
        self.assertEqual({packet[1] for packet in rig.port.written[sent:]}, {3})
        self.assertEqual(rig.kb.device.devidx, 3)

    def test_a_keyboard_on_a_node_of_its_own_turns_up_while_the_mouse_is_in_use(self):
        rig = Rig(self, {2: mouse_device()}).start()
        with Clock(time.monotonic()) as clock:
            for _ in range(30):
                clock.now += 1.0
                rig.engine._tick()
        self.assertEqual(list(rig.engine._transports), [PATH])  # the same nodes: not a request anywhere
        kb = keyboard_device()
        rig.ports[OTHER_PATH] = FakePort({0xFF: kb}, receiver=False)
        rig.nodes.append((OTHER_PATH, False))
        with Clock(time.monotonic() + 3600) as clock:
            rig.engine._tick()
        rig.settle()
        self.assertEqual(rig.kb.device.devidx, 0xFF)
        self.assertIs(rig.kb.transport, rig.transport(OTHER_PATH))

    def test_a_sleeping_slot_is_asked_again_later(self):
        kb = keyboard_device()
        kb.silent = True
        rig = Rig(self, {1: mouse_device(), 2: kb}).start()
        self.assertEqual(rig.kb._slots[rig.transport()][2], "asleep")
        kb.silent = False
        rig.kb._next_look = 0.0
        rig.engine._tick()
        rig.settle()
        self.assertEqual(rig.kb.device.devidx, 2)

    def test_a_look_that_runs_out_of_time_is_taken_up_again(self):
        kb = keyboard_device()
        kb.delay = 0.03
        with mock.patch.object(keyboard, "PROBE_BUDGET", 0.3):
            rig = Rig(self, {1: mouse_device(), 2: kb}).start()
        self.assertIsNone(rig.kb.device)
        self.assertNotIn(2, rig.kb._slots[rig.transport()])  # not reached: unknown, asked at the next round
        kb.delay = 0.0
        rig.kb._next_look = 0.0
        rig.engine._tick()
        rig.settle()
        self.assertEqual(rig.kb.device.devidx, 2)

    def test_what_a_look_found_counts_for_nothing_once_its_transport_is_gone(self):
        kb = keyboard_device()
        kb.delay = 0.02
        rig = Rig(self, {1: mouse_device(), 2: kb})
        rig.engine._scan()
        transport = rig.transport()
        rig.engine._drop_transport(transport)
        rig.settle()
        self.assertIsNone(rig.kb.device)
        self.assertEqual(rig.kb._slots, {})

    def test_what_a_look_found_counts_for_nothing_once_the_receiver_says_the_slot_changed(self):
        kb = keyboard_device()
        kb.delay = 0.01
        rig = Rig(self, {1: mouse_device(), 2: kb})
        rig.engine._scan()
        generation = rig.kb._generation
        rig.link(2, True)
        self.assertEqual(rig.kb._generation, generation + 1)
        rig.settle()
        self.assertEqual(rig.kb.device.devidx, 2)
        info = kb.index(device.DEVICE_INFO)
        self.assertLessEqual(len(rig.port.sent(2, info, 0)), 2)  # the earlier look may have got that far

    def test_stopping_ends_the_look_at_once(self):
        kb = keyboard_device()
        kb.silent = True
        with mock.patch.object(keyboard, "PROBE_TIMEOUT_SHARED", 0.5):
            rig = Rig(self, {1: mouse_device(), 2: kb})
            rig.engine._scan()
            time.sleep(0.1)
            start = time.monotonic()
            rig.kb.shutdown()
        self.assertFalse(rig.kb._thread.is_alive())
        self.assertLess(time.monotonic() - start, keyboard.PROBE_TIMEOUT_OWN + 0.5)
        sent = len(rig.port.written)
        time.sleep(0.3)
        self.assertEqual(len(rig.port.written), sent)


def flow_link(unit):
    return hashlib.sha256(b"mxoptions keyboard flow\0" + identity(unit).encode("ascii")).hexdigest()[:16]


class FlowRecord(unittest.TestCase):
    """What Flow is told of the keyboard (mouse follows keyboard): its link, and how it went."""

    def setUp(self):
        self.kb = keyboard_device()
        self.rig = Rig(self, {1: self.kb, 2: mouse_device()}, keys={"0x0f04": {"type": "host", "host": 1}}).start()

    def told(self):
        return [payload[1] for kind, payload in self.rig.listener.events if kind == "flow" and payload[0] == "keyboard"]

    def test_the_link_is_made_from_the_identity_and_kept_once_it_is_gone(self):
        rig = self.rig
        self.assertEqual(rig.kb.link, flow_link(UNIT_A))
        self.assertNotEqual(rig.kb.link, identity(UNIT_A))
        self.assertEqual(self.told(), [{"link": flow_link(UNIT_A), "here": True, "drop": None, "channel": None}])
        before = time.monotonic()
        rig.link(1, False)
        self.assertEqual(rig.kb.link, flow_link(UNIT_A))
        when, sent = rig.kb.last_drop
        self.assertTrue(before <= when <= time.monotonic())
        self.assertFalse(sent)
        self.assertEqual(self.told()[-1], {"link": flow_link(UNIT_A), "here": False, "drop": (when, False), "channel": None})
        rig.link(1, True)
        rig.settle()
        self.assertIsNone(rig.kb.last_drop)  # back: its going counts no more
        self.assertEqual(self.told()[-1], {"link": flow_link(UNIT_A), "here": True, "drop": None, "channel": None})

    def test_the_link_is_never_in_the_state(self):
        rig = self.rig
        for _ in range(2):
            shown = json.dumps(rig.state())
            self.assertNotIn(flow_link(UNIT_A), shown)
            self.assertNotIn(identity(UNIT_A), shown)
            rig.link(1, False)

    def test_a_keyboard_sent_away_by_a_key_is_said_to_be(self):
        rig = self.rig
        rig.keys_down(1, self.kb, 0x0f04)
        rig.keys_down(1, self.kb)
        rig.settle()
        self.assertEqual(self.kb.switched, [1])
        rig.link(1, False)
        self.assertTrue(rig.kb.last_drop[1])

    def test_one_that_goes_while_the_switch_is_on_its_way_was_sent_too(self):
        rig, out, answer = self.rig, threading.Event(), threading.Event()
        self.addCleanup(answer.set)
        with mock.patch.object(device.Mouse, "change_host", lambda _dev, _host: (out.set(), answer.wait(2.0))):
            rig.keys_down(1, self.kb, 0x0f04)
            while not out.is_set():  # the keys are given back first; then the request is out, its answer held back
                try:
                    rig.engine._handle(rig.engine._queue.get(timeout=0.05))
                except queue.Empty:
                    pass
            rig.link(1, False)  # and the keyboard goes before it is back
        self.assertTrue(rig.kb.last_drop[1])

    def test_a_delayed_switch_keeps_its_grace_after_the_answer(self):
        rig = self.rig
        with Clock(time.monotonic()) as clock:
            def delayed_reply(_dev, _host):
                clock.now += 4.0
            with mock.patch.object(device.Mouse, "change_host", delayed_reply):
                rig.keys_down(1, self.kb, 0x0f04)
                rig.keys_down(1, self.kb)
                rig.settle()
            clock.now += 2.0
            self.assertGreater(rig.kb._leaving, clock.now)
            rig.link(1, False)
            self.assertTrue(rig.kb.last_drop[1])

    def test_a_pending_switch_stays_owned_past_the_grace_period(self):
        rig, out, answer = self.rig, threading.Event(), threading.Event()
        self.addCleanup(answer.set)
        with Clock(time.monotonic()) as clock:
            with mock.patch.object(device.Mouse, "change_host", lambda _dev, _host: (out.set(), answer.wait(2.0))):
                rig.keys_down(1, self.kb, 0x0f04)
                for _attempt in range(80):
                    if out.is_set():
                        break
                    try:
                        rig.engine._handle(rig.engine._queue.get(timeout=0.05))
                    except queue.Empty:
                        pass
                self.assertTrue(out.is_set())
                clock.now += keyboard.SWITCH_GRACE + 1.0
                rig.link(1, False)
                answer.set()
                self.assertTrue(rig.kb.last_drop[1])

    def test_a_switch_with_no_answer_may_still_have_sent_the_keyboard(self):
        rig = self.rig
        with mock.patch.object(device.Mouse, "change_host", side_effect=hidpp.HidppTimeout("synthetic")):
            rig.keys_down(1, self.kb, 0x0f04)
            rig.keys_down(1, self.kb)
            rig.settle()
        rig.link(1, False)
        self.assertTrue(rig.kb.last_drop[1])

    def test_a_switch_that_failed_or_is_long_past_does_not_count(self):
        rig = self.rig
        with mock.patch.object(device.Mouse, "change_host", side_effect=hidpp.HidppError("synthetic")):
            rig.keys_down(1, self.kb, 0x0f04)
            rig.keys_down(1, self.kb)
            rig.settle()
        rig.link(1, False)
        self.assertFalse(rig.kb.last_drop[1])
        rig.link(1, True)
        rig.settle()
        rig.keys_down(1, self.kb, 0x0f04)
        rig.keys_down(1, self.kb)
        rig.settle()
        rig.kb._sent_at -= keyboard.SWITCH_GRACE + 0.1  # it stayed after all, and goes much later by itself
        rig.link(1, False)
        self.assertFalse(rig.kb.last_drop[1])

    def test_another_unit_takes_the_place_of_the_link(self):
        rig = self.rig
        rig.link(1, False)
        rig.port.devices[1] = keyboard_device(UNIT_B)
        rig.link(1, True)
        rig.settle()
        self.assertEqual((rig.kb.link, rig.kb.last_drop), (flow_link(UNIT_B), None))
        self.assertNotEqual(flow_link(UNIT_A), flow_link(UNIT_B))

    def test_a_keyboard_that_does_not_say_which_one_it_is_has_none(self):
        rig = Rig(self, {1: mouse_device(), 2: keyboard_device(unit=bytes(4))}).start()
        self.assertIsNone(rig.kb.link)
        rig.link(2, False)
        self.assertIsNone(rig.kb.last_drop)
        self.assertEqual([payload[1]["link"] for kind, payload in rig.listener.events if kind == "flow"], [None, None])


class Routing(unittest.TestCase):
    def setUp(self):
        self.kb, self.mouse = keyboard_device(), mouse_device()
        self.rig = Rig(self, {1: self.kb, 2: self.mouse}, keys={"0x0053": COPY, "0x0f01": CTRL, "0x0f04": {"type": "host", "host": 1}})
        self.rig.engine.cfg["buttons"]["back"] = actions.keys("LEFTSHIFT")
        self.rig.start()

    def test_the_same_control_on_both_runs_only_its_own_action(self):
        rig = self.rig
        rig.keys_down(1, self.kb, 0x0053)
        self.assertEqual(rig.input.sent, [("KEY_LEFTCTRL", True), ("KEY_C", True)])
        self.assertEqual(rig.mouse_actions.ran, [])
        told = [kind for kind, payload in rig.listener.events if (kind, payload[0]) != ("flow", "keyboard")]  # found: for Flow
        self.assertEqual(told, [])  # keys never count as mouse buttons
        rig.note(2, self.mouse, device.REPROG_CONTROLS_V4, struct.pack("!HHHH", 0x0053, 0, 0, 0))
        self.assertEqual(rig.mouse_actions.ran, [actions.keys("LEFTSHIFT")])
        self.assertEqual(len(rig.input.sent), 2)

    def test_the_mouse_starting_over_keeps_the_keyboard_s_keys_down(self):
        rig = self.rig
        rig.engine.executor = actions.Executor(rig.input, rig.engine._device_action)
        rig.keys_down(1, self.kb, 0x0f01)
        rig.engine._reset_input_state()
        self.assertEqual(rig.input.sent, [("KEY_LEFTCTRL", True)])
        rig.keys_down(1, self.kb)
        self.assertEqual(rig.input.sent[-1], ("KEY_LEFTCTRL", False))

    def test_the_keyboard_going_away_lets_go_of_its_keys_only(self):
        rig = self.rig
        rig.engine.executor = actions.Executor(rig.input, rig.engine._device_action)
        rig.engine.executor.press(actions.keys("LEFTSHIFT"))
        rig.keys_down(1, self.kb, 0x0f01)
        rig.link(1, False)
        self.assertIsNone(rig.kb.device)
        self.assertEqual(rig.input._holds.held(), {"KEY_LEFTSHIFT"})
        self.assertTrue(rig.engine.online)  # the mouse is not touched
        self.assertFalse(rig.state()["connected"])
        self.assertTrue(rig.state()["known"])

    def test_easy_switch_from_a_key_moves_the_keyboard_not_the_mouse(self):
        rig = self.rig
        start = len(rig.port.written)
        rig.keys_down(1, self.kb, 0x0f04)
        rig.settle()
        self.assertEqual(self.kb.switched, [1])
        packets = rig.port.written[start:]
        give_back = [i for i, p in enumerate(packets) if p[1] == 1 and p[2] == self.kb.index(device.REPROG_CONTROLS_V4) and p[3] >> 4 == 3]
        switch = next(i for i, p in enumerate(packets) if p[1] == 1 and p[2] == self.kb.index(device.CHANGE_HOST) and p[3] >> 4 == 1)
        self.assertTrue(give_back and max(give_back) < switch)  # every key back to normal first
        self.assertEqual(rig.saved()["keyboards"], {})
        self.assertFalse(any(p[1] == 2 for p in packets))
        rig.link(1, False)
        rig.link(1, True)  # back again: diverted again
        rig.settle()
        self.assertEqual(rig.saved()["keyboards"], {identity(UNIT_A): ["0x0053", "0x0f01", "0x0f04"]})

    def test_the_keyboard_stays_while_a_key_cannot_be_given_back(self):
        rig = self.rig
        for failing in (4, None):  # refused; no answer at all
            with self.subTest(failing=failing):
                self.kb.failing[(device.REPROG_CONTROLS_V4, 3)] = failing
                rig.keys_down(1, self.kb, 0x0f04)
                rig.keys_down(1, self.kb)
                rig.settle()
                self.assertEqual(self.kb.switched, [])  # not sent off with keys this program changed
                self.assertEqual(rig.saved()["keyboards"], {identity(UNIT_A): ["0x0053", "0x0f01", "0x0f04"]})
                self.assertEqual(rig.state()["failed"], "switch")
                self.assertEqual([k["id"] for k in rig.state()["keys"] if k["active"]], ["0x0053", "0x0f01", "0x0f04"])
                del self.kb.failing[(device.REPROG_CONTROLS_V4, 3)]  # it stays, its keys as set
        rig.keys_down(1, self.kb, 0x0f04)  # now it goes
        rig.settle()
        self.assertEqual(self.kb.switched, [1])
        self.assertIsNone(rig.state()["failed"])

    def test_a_switch_that_fails_leaves_the_keys_as_they_were(self):
        rig = self.rig
        with mock.patch.object(device.Mouse, "change_host", side_effect=hidpp.HidppError("synthetic")):
            rig.keys_down(1, self.kb, 0x0f04)
            rig.settle()
        self.assertEqual(rig.state()["failed"], "switch")
        self.assertEqual(rig.saved()["keyboards"], {identity(UNIT_A): ["0x0053", "0x0f01", "0x0f04"]})
        self.assertTrue(rig.key(0x0F01)["active"])
        self.assertEqual(diverts(self.kb)[-1], (0x0F04, True))  # given back, then taken again

    def test_a_keyboard_that_does_not_go_has_its_keys_taken_again(self):
        rig = self.rig
        rig.keys_down(1, self.kb, 0x0f04)
        rig.settle()
        self.assertEqual((self.kb.switched, rig.saved()["keyboards"]), ([1], {}))
        self.assertFalse(rig.key(0x0F01)["active"])
        self.assertLessEqual(rig.kb._next_recovery, time.monotonic() + keyboard.SWITCH_GRACE)
        sets = len(self.kb.sets)
        new = copy.deepcopy(rig.engine.cfg)
        new["keyboard"]["keys"]["0x0f02"] = CTRL
        rig.engine._apply_config(new)  # on its way: nothing taken again yet
        rig.settle()
        self.assertEqual(len(self.kb.sets), sets)
        rig.kb.tick(rig.kb._next_recovery)  # still here: no word from the receiver that it went
        rig.settle()
        self.assertTrue(rig.key(0x0F01)["active"])
        self.assertEqual(rig.saved()["keyboards"], {identity(UNIT_A): ["0x0053", "0x0f01", "0x0f02", "0x0f04"]})

    def test_a_key_held_while_its_action_changes_lets_go_at_once(self):
        rig = self.rig
        rig.keys_down(1, self.kb, 0x0f01)
        old = copy.deepcopy(rig.engine.cfg)
        new = copy.deepcopy(old)
        new["keyboard"]["keys"]["0x0f01"] = actions.keys("LEFTALT")
        rig.engine._apply_config(new)
        self.assertEqual(rig.input.sent[-1], ("KEY_LEFTCTRL", False))
        rig.keys_down(1, self.kb)
        self.assertEqual(rig.input.sent[-1], ("KEY_LEFTCTRL", False))
        self.assertNotIn("KEY_LEFTALT", [name for name, _down in rig.input.sent])


NATIVE, NONE = {"type": "default"}, {"type": "none"}
PASTE = actions.keys("LEFTCTRL", "V")


def profile(app, keys):
    return {"app": app, "name": app.title(), "keys": keys}


def front(rig, app):
    """``app`` comes to the front and stays there: the engine takes it up once it has been there long enough."""
    rig.engine.set_front(app)
    rig.engine._handle(rig.engine._queue.get(timeout=1.0))
    rig.engine._take_front(float("inf"))
    rig.settle()


def macro(*names, wait=0, repeat=False):
    steps = [{"do": "keys", "keys": [name]} for name in names]
    if wait:
        steps.insert(1, {"do": "delay", "ms": wait})
    return {"type": "macro", "repeat": repeat, "steps": steps}


class Profiles(unittest.TestCase):
    """App profiles (config "keyboard.profiles"): the keys of the app in front over those for all apps."""

    def rig(self, keys, profiles):
        self.kb = keyboard_device()
        rig = Rig(self, {1: self.kb, 2: mouse_device()}, keys=keys)
        rig.engine.cfg["keyboard"]["profiles"] = copy.deepcopy(profiles)
        return rig.start()

    def taps(self, rig):
        tapped = []
        rig.input.tap = lambda names: tapped.append(names[0])
        return tapped

    def wait_for(self, tapped, name, timeout=3.0):
        end = time.monotonic() + timeout
        while name not in tapped and time.monotonic() < end:
            time.sleep(0.01)
        self.assertIn(name, tapped)

    def test_what_a_key_does_in_an_app(self):
        settings = {"keys": {"0x0f01": COPY, "0x0f02": CTRL, "0x0f03": NONE},
                    "profiles": [profile("editor", {"0x0f01": NATIVE, "0x0f04": PASTE, "0x0f02": NONE}),
                                 profile("editor", {"0x0f05": COPY}), profile("game", {})]}
        self.assertEqual(keyboard.effective_keys(settings, None), settings["keys"])  # no app known: as for all apps
        self.assertEqual(keyboard.effective_keys(settings, "browser"), settings["keys"])  # no profile
        self.assertEqual(keyboard.effective_keys(settings, "game"), settings["keys"])  # a profile that changes nothing
        self.assertEqual(keyboard.effective_keys(settings, "editor"),  # the first profile of the app counts
                         {"0x0f02": NONE, "0x0f03": NONE, "0x0f04": PASTE})
        self.assertEqual(keyboard.effective_keys({"keys": {"0x0f01": COPY}}, "editor"), {"0x0f01": COPY})  # older settings

    def test_default_in_an_app_gives_the_key_back_and_it_is_taken_again_elsewhere(self):
        written, real = [], keyboard.write_journal
        with mock.patch.object(keyboard, "write_journal", lambda entries, path=None: (written.append(
                (copy.deepcopy(entries), list(self.kb.sets) if hasattr(self, "kb") else [])), real(entries, path))):
            rig = self.rig({"0x0f01": COPY, "0x0f02": CTRL}, [profile("editor", {"0x0f01": NATIVE, "0x0f04": PASTE})])
            self.assertEqual(rig.saved()["keyboards"], {identity(UNIT_A): ["0x0f01", "0x0f02"]})
            sets = len(self.kb.sets)
            front(rig, "editor")
            self.assertEqual(sorted(diverts(self.kb)[sets:]), [(0x0F01, False), (0x0F04, True)])
            self.assertEqual(rig.saved()["keyboards"], {identity(UNIT_A): ["0x0f02", "0x0f04"]})
            self.assertFalse(rig.key(0x0F01)["active"])
            rig.keys_down(1, self.kb, 0x0f04)
            rig.keys_down(1, self.kb)
            self.assertEqual(rig.input.sent, [("KEY_LEFTCTRL", True), ("KEY_V", True), ("KEY_V", False), ("KEY_LEFTCTRL", False)])
            sets, mark = len(self.kb.sets), len(written)
            front(rig, "browser")
        self.assertEqual(sorted(diverts(self.kb)[sets:]), [(0x0F01, True), (0x0F04, False)])
        self.assertEqual(rig.saved()["keyboards"], {identity(UNIT_A): ["0x0f01", "0x0f02"]})
        so_far = next(so_far for entries, so_far in written[mark:] if "0x0f01" in entries.get(identity(UNIT_A), []))
        self.assertNotIn((0x0F01, True), [(cid, bool(flags & 1)) for cid, flags in so_far[sets:]])  # written down first
        rig.keys_down(1, self.kb, 0x0f01)
        self.assertEqual(rig.input.sent[-2:], [("KEY_LEFTCTRL", True), ("KEY_C", True)])

    def test_none_in_an_app_keeps_the_key_and_sends_nothing(self):
        rig = self.rig({"0x0f02": CTRL}, [profile("game", {"0x0f02": NONE, "0x0f01": NONE})])
        sets = len(self.kb.sets)
        front(rig, "game")
        self.assertEqual(diverts(self.kb)[sets:], [(0x0F01, True)])  # taken, never given back
        self.assertTrue(rig.key(0x0F02)["active"] and rig.key(0x0F01)["active"])
        rig.keys_down(1, self.kb, 0x0f01, 0x0f02)
        rig.keys_down(1, self.kb)
        self.assertEqual(rig.input.sent, [])

    def test_a_held_key_that_stays_the_same_is_kept_and_one_that_changes_lets_go(self):
        rig = self.rig({"0x0f01": COPY, "0x0f02": CTRL}, [profile("editor", {"0x0f01": PASTE})])
        rig.keys_down(1, self.kb, 0x0f01, 0x0f02)
        front(rig, "editor")
        self.assertEqual(rig.input.sent[-1], ("KEY_C", False))
        self.assertEqual(rig.input._holds.held(), {"KEY_LEFTCTRL"})  # still held by the other key
        rig.keys_down(1, self.kb, 0x0f02)  # the changed key comes up: nothing
        self.assertEqual(rig.input._holds.held(), {"KEY_LEFTCTRL"})
        self.assertNotIn("KEY_V", [name for name, _down in rig.input.sent])
        rig.keys_down(1, self.kb)
        self.assertEqual(rig.input._holds.held(), set())

    def test_the_macros_of_a_changed_key_stop_those_of_the_others_go_on(self):
        rig = self.rig({"0x0f01": macro("KEY_A", "KEY_B", wait=1500), "0x0f02": macro("KEY_M", "KEY_N")},
                       [profile("editor", {"0x0f01": NATIVE})])
        tapped = self.taps(rig)
        rig.keys_down(1, self.kb, 0x0f01)
        rig.keys_down(1, self.kb)          # let go: a macro that does not repeat plays to its end
        self.wait_for(tapped, "KEY_A")
        rig.keys_down(1, self.kb, 0x0f02)  # queued behind it
        rig.keys_down(1, self.kb)
        rig.keys_down(1, self.kb, 0x0f01)  # and the changed key's again
        rig.keys_down(1, self.kb)
        time.sleep(0.1)  # in its wait now
        start = time.monotonic()
        front(rig, "editor")
        self.wait_for(tapped, "KEY_N")
        self.assertLess(time.monotonic() - start, 1.2)  # its wait was cut short
        time.sleep(0.2)
        self.assertEqual(tapped, ["KEY_A", "KEY_M", "KEY_N"])

    def test_the_macro_of_a_key_that_stays_the_same_plays_on(self):
        rig = self.rig({"0x0f01": macro("KEY_A"), "0x0f02": macro("KEY_M", "KEY_N", wait=300)},
                       [profile("editor", {"0x0f01": PASTE})])
        tapped = self.taps(rig)
        rig.keys_down(1, self.kb, 0x0f02)
        rig.keys_down(1, self.kb, 0x0f02, 0x0f01)  # queued behind it
        self.wait_for(tapped, "KEY_M")
        front(rig, "editor")
        self.wait_for(tapped, "KEY_N")
        time.sleep(0.2)
        self.assertEqual(tapped, ["KEY_M", "KEY_N"])

    def test_a_repeating_macro_of_a_changed_key_stops_and_its_command_runs_on(self):
        repeating = {"type": "macro", "repeat": True, "steps": [{"do": "command", "command": "sleep 60"},
                                                                {"do": "keys", "keys": ["KEY_R"]}, {"do": "delay", "ms": 50}]}
        rig = self.rig({"0x0f01": repeating}, [profile("editor", {"0x0f01": NATIVE})])
        tapped = self.taps(rig)
        with mock.patch.object(actions.subprocess, "Popen") as popen:
            rig.keys_down(1, self.kb, 0x0f01)  # held: it repeats
            self.wait_for(tapped, "KEY_R")
            front(rig, "editor")
            time.sleep(0.2)
            count = len(tapped)
            time.sleep(0.3)
        self.assertEqual(len(tapped), count)
        self.assertFalse(popen.return_value.kill.called or popen.return_value.terminate.called)

    def test_only_the_last_app_counts_and_the_same_keys_ask_the_keyboard_nothing(self):
        rig = self.rig({"0x0f01": COPY}, [profile("editor", {"0x0f01": NATIVE}), profile("viewer", {"0x0f02": NONE})])
        sets, written = len(self.kb.sets), len(rig.port.written)
        for app in ("editor", "viewer", "editor", "browser"):
            rig.engine.set_front(app)
            rig.engine._handle(rig.engine._queue.get(timeout=1.0))
            self.assertLessEqual(rig.engine._idle_timeout(), engine.FRONT_DELAY)
            rig.engine._take_front(rig.engine._front[1] - 0.01)  # not yet
            self.assertIsNone(rig.kb._front)
        rig.engine._take_front(float("inf"))
        rig.settle()
        self.assertEqual(rig.kb._front, "browser")
        self.assertEqual(len(self.kb.sets), sets)  # as for all apps: nothing to change
        self.assertEqual([p for p in rig.port.written[written:] if p[1] == 1], [])  # not even a read
        front(rig, None)  # no app known
        self.assertIsNone(rig.kb._front)
        self.assertEqual(len(self.kb.sets), sets)
        for bad in ("Editor", "a\x00b", "x" * 129, "", 3, ["editor"]):  # never one the settings could name
            front(rig, bad)
            self.assertIsNone(rig.kb._front)

    def test_settings_for_another_app_change_nothing_here(self):
        rig = self.rig({"0x0f01": COPY}, [profile("editor", {"0x0f01": NATIVE})])
        rig.keys_down(1, self.kb, 0x0f01)
        sets = len(self.kb.sets)
        new = copy.deepcopy(rig.engine.cfg)
        new["keyboard"]["profiles"][0]["keys"]["0x0f02"] = PASTE
        new["keyboard"]["profiles"].append(profile("viewer", {"0x0f01": PASTE}))
        rig.engine._apply_config(new)
        rig.settle()
        self.assertEqual(len(self.kb.sets), sets)
        self.assertEqual(rig.input._holds.held(), {"KEY_LEFTCTRL", "KEY_C"})  # still held
        new = copy.deepcopy(rig.engine.cfg)
        new["keyboard"]["profiles"][0]["app"] = "browser"  # its own profile now changes it: let go
        new["keyboard"]["profiles"][0]["keys"]["0x0f01"] = PASTE
        rig.engine._apply_config(new)
        front(rig, "browser")
        self.assertEqual(rig.input._holds.held(), set())

    def test_which_app_is_in_front_is_never_told(self):
        rig = self.rig({"0x0f01": COPY}, [profile("secretapp", {"0x0f01": NATIVE})])
        with self.assertLogs("mxoptions", logging.DEBUG) as logs:
            front(rig, "secretapp")
            rig.keys_down(1, self.kb, 0x0f02)
            front(rig, "otherapp")
            logging.getLogger("mxoptions").debug("marker")
        self.assertFalse([line for line in logs.output if "secretapp" in line or "otherapp" in line])
        told = json.dumps([rig.kb.state(), rig.states, [payload for _kind, payload in rig.listener.events]], default=str)
        self.assertNotIn("secretapp", told)
        self.assertNotIn("otherapp", told)


class Diverting(unittest.TestCase):
    def test_nothing_is_diverted_without_settings(self):
        kb = keyboard_device()
        rig = Rig(self, {1: mouse_device(), 2: kb}).start()
        self.assertEqual(kb.sets, [])
        self.assertIsNone(rig.saved())

    def test_only_keys_the_keyboard_lets_go_of_are_diverted(self):
        kb = keyboard_device()
        rig = Rig(self, {1: mouse_device(), 2: kb}, keys={name: COPY for name in ("0x00d1", "0x0034", "0x0f02", "0x0f03", "0x0f99")})
        rig.start()
        self.assertEqual(diverts(kb), [(0x0F02, True)])  # an F-row key may be; Easy-Switch, Fn, the fixed key may not
        self.assertTrue(all(flags & 0x1C == 0 for _cid, flags in kb.sets))  # never persistent, never raw
        self.assertEqual(rig.key(0x00D1)["native_reason"], "protected")
        self.assertTrue(rig.key(0x0F02)["active"])
        self.assertNotIn(0x0F03, [key["cid"] for key in rig.state()["keys"]])
        rig.kb.shutdown()
        self.assertEqual(diverts(kb), [(0x0F02, True), (0x0F02, False)])
        self.assertEqual(rig.saved()["keyboards"], {})

    def test_a_key_given_an_action_that_cannot_run_is_never_diverted(self):
        kb = keyboard_device()
        bad = {"0x0f01": {"type": "keys", "keys": ["KEY_NOT_REAL"]}, "0x0f02": {"type": "macro", "steps": [{"do": "delay", "ms": "x"}]}}
        rig = Rig(self, {1: mouse_device(), 2: kb}, keys=bad).start()  # not through config.clean: as it reaches the engine
        self.assertEqual(kb.sets, [])
        self.assertIsNone(rig.saved())
        rig.keys_down(2, kb, 0x0f01, 0x0f02)
        self.assertEqual(rig.input.sent, [])

    def test_the_journal_comes_first(self):
        kb = keyboard_device()
        written = []
        real = keyboard.write_journal
        with mock.patch.object(keyboard, "write_journal", lambda entries, path=None: (written.append(
                (copy.deepcopy(entries), list(kb.sets))), real(entries, path))):
            Rig(self, {1: mouse_device(), 2: kb}, keys={"0x0f01": COPY}).start()
        self.assertEqual(written[0], ({identity(UNIT_A): ["0x0f01"]}, []))  # written before the keyboard was told
        self.assertEqual(diverts(kb), [(0x0F01, True)])

    def test_resetting_gives_every_key_back(self):
        kb = keyboard_device()
        rig = Rig(self, {1: mouse_device(), 2: kb}, keys={"0x0f01": COPY, "0x0f02": CTRL}).start()
        new = copy.deepcopy(rig.engine.cfg)
        new["keyboard"] = {"keys": {}}
        rig.engine._apply_config(new)
        rig.settle()
        self.assertEqual(sorted(diverts(kb)[2:]), [(0x0F01, False), (0x0F02, False)])
        self.assertEqual(rig.saved()["keyboards"], {})
        self.assertFalse(any(key["active"] for key in rig.state()["keys"]))

    def test_on_exit_the_mouse_is_restored_first_then_the_keyboard_then_the_transports_close(self):
        kb, mouse = keyboard_device(), mouse_device()
        rig = Rig(self, {1: kb, 2: mouse}, keys={"0x0f01": COPY}).start()
        rig.engine.executor = actions.Executor(rig.input, rig.engine._device_action)
        rig.engine.cfg["buttons"]["back"] = COPY
        rig.engine._apply(["buttons"])
        start = len(rig.port.written)
        transport = rig.transport()
        rig.engine._shutdown()
        order = [p[1] for p in rig.port.written[start:] if p[3] >> 4 == 3 and p[2] == 3]
        self.assertEqual(order[0], 2)
        self.assertEqual(order[-1], 1)
        self.assertEqual(diverts(kb)[-1], (0x0F01, False))
        self.assertTrue(transport.closed)
        self.assertEqual(rig.saved()["keyboards"], {})


class Responsiveness(unittest.TestCase):
    """The keyboard slow to answer while the mouse is used: on the mouse's receiver, and on a node of its own."""

    def rig(self, separate):
        kb, mouse = keyboard_device(), mouse_device()
        if separate:
            rig = Rig(self, {2: mouse}, keys={"0x0f01": COPY})
            rig.ports[OTHER_PATH] = FakePort({0xFF: kb}, receiver=False)
            rig.nodes.append((OTHER_PATH, False))
            rig.start()
            rig.kb._next_nodes = 0.0
            rig.engine._tick()
            rig.settle()
            port, devidx = rig.ports[OTHER_PATH], 0xFF
        else:
            rig = Rig(self, {1: kb, 2: mouse}, keys={"0x0f01": COPY}).start()
            port, devidx = rig.port, 1
        rig.engine.cfg["buttons"]["back"] = actions.keys("LEFTSHIFT")
        self.assertTrue(rig.key(0x0F01)["active"])
        return rig, kb, mouse, port, devidx

    def mouse_heard(self, rig, mouse):
        """The mouse's back button, handled as the engine's thread would; how long that took."""
        rig.mouse_actions.ran.clear()
        start = time.monotonic()
        rig.note(2, mouse, device.REPROG_CONTROLS_V4, struct.pack("!HHHH", 0x0053, 0, 0, 0))
        took = time.monotonic() - start
        self.assertEqual(rig.mouse_actions.ran, [actions.keys("LEFTSHIFT")])
        rig.note(2, mouse, device.REPROG_CONTROLS_V4, bytes(8))
        return took

    def test_the_mouse_is_heard_while_the_keyboard_is_slow_to_answer(self):
        for separate in (False, True):
            with self.subTest(separate=separate):
                rig, kb, mouse, _port, devidx = self.rig(separate)
                # each answer later than the mouse waits behind one (PROBE_TIMEOUT_SHARED), yet in time
                kb.delays = dict.fromkeys((device.REPROG_CONTROLS_V4, device.UNIFIED_BATTERY, device.CHANGE_HOST), 0.2)
                steps = {
                    "setter": lambda: rig.engine._apply_config(dict(copy.deepcopy(rig.engine.cfg), keyboard={"keys": {
                        "0x0f01": COPY, "0x0f02": CTRL}})),
                    "getter": lambda: (rig.kb._reporting.pop(0x0F01), rig.kb.tick(rig.kb._next_recovery)),
                    "status": lambda: (rig.link(devidx, False) if not separate else rig.kb._again(True),
                                       rig.link(devidx, True) if not separate else None),
                }
                rig.kb._next_recovery = 0.0
                for name, step in steps.items():
                    start = time.monotonic()
                    step()
                    self.assertLess(time.monotonic() - start, 0.1, name)  # handed to the keyboard's thread
                    time.sleep(0.05)
                    busy = rig.kb._asked is not None or any(
                        what == "pending" for states in rig.kb._slots.values() for what in states.values())
                    self.assertTrue(busy, name)  # the keyboard is still being waited for ...
                    self.assertLess(self.mouse_heard(rig, mouse), 0.1, name)  # ... and the mouse is heard meanwhile
                    rig.settle()
                    rig.kb._next_recovery = 0.0
                self.assertTrue(rig.key(0x0F02)["active"])
                self.assertTrue(rig.key(0x0F01)["active"])
                rig.stop()

    def test_the_engine_s_thread_never_waits_on_the_keyboard(self):
        """Every request to the keyboard goes from the keyboard's thread, but those on exit, which are bounded."""
        for separate in (False, True):
            with self.subTest(separate=separate):
                rig, kb, _mouse, port, devidx = self.rig(separate)
                before = len(port.written)  # the mouse is looked for on the engine's thread, as ever
                new = copy.deepcopy(rig.engine.cfg)
                new["keyboard"]["keys"] = {"0x0f01": COPY, "0x0f02": CTRL, "0x0f04": {"type": "host", "host": 1}}
                rig.engine._apply_config(new)  # diverting
                rig.settle()
                rig.kb._reporting.clear()
                rig.kb.tick(rig.kb._next_recovery if rig.kb._next_recovery < float("inf") else 0.0)  # reading again
                rig.kb.configure()
                rig.settle()
                rig.keys_down(devidx, kb, 0x0f04, path=OTHER_PATH if separate else PATH)  # giving back, Easy-Switch
                rig.settle()
                self.assertEqual(kb.switched, [1])
                sent = [writer for packet, writer in zip(port.written[before:], port.writers[before:]) if packet[1] == devidx]
                self.assertEqual(set(sent), {"keyboard"})
                self.assertGreaterEqual(len(sent), 9)  # read, diverted, given back, switched
                rig.stop()

    def test_on_exit_a_silent_keyboard_is_given_up_on_in_time(self):
        rig, kb, _mouse, _port, _devidx = self.rig(False)
        kb.silent = True
        start = time.monotonic()
        rig.kb.shutdown()
        self.assertLess(time.monotonic() - start, keyboard.SHUTDOWN_BUDGET + keyboard.PROBE_TIMEOUT_OWN + 0.5)
        self.assertEqual(rig.saved()["keyboards"], {identity(UNIT_A): ["0x0f01"]})  # kept for the next start


class CalledOff(unittest.TestCase):
    """A request of a task that was dropped before it went never goes: not to the keyboard, not to another unit
    on its slot. One that went already is not known to be undone, and is read back."""

    def held(self):
        """``keyboard._call`` that holds the first setCidReporting back until ``release`` is set."""
        entered, release, real = threading.Event(), threading.Event(), keyboard._call

        def call(dev, request):
            if request[0] is keyboard._set_reporting and not entered.is_set():
                entered.set()
                self.assertTrue(release.wait(5.0))
            return real(dev, request)
        self.addCleanup(release.set)
        return mock.patch.object(keyboard, "_call", call), entered, release

    def drain(self, rig):
        """Answers the keyboard's thread still has for the engine's (dropped: they no longer count)."""
        time.sleep(0.4)
        while not rig.engine._queue.empty():
            rig.engine._handle(rig.engine._queue.get())

    def divert(self, rig):
        new = copy.deepcopy(rig.engine.cfg)
        new["keyboard"]["keys"] = {"0x0f01": COPY}
        rig.engine._apply_config(new)

    def replaced(self, rig):
        """The keyboard goes, another unit of the same model comes on its slot; it is found and set up as itself."""
        rig.link(1, False)
        self.assertIsNone(rig.kb.device)
        unit_b = rig.port.devices[1] = keyboard_device(UNIT_B)
        return unit_b, len(rig.port.written)

    def found_again(self, rig, unit_b, sent):
        self.drain(rig)
        self.assertEqual(unit_b.sets, [])  # nothing of the old keyboard's task reached it
        self.assertEqual([packet for packet in rig.port.written[sent:] if packet[1] == 1], [])  # not even a read
        self.assertEqual(rig.saved()["keyboards"], {identity(UNIT_A): ["0x0f01"]})  # whether it went: asked when back
        rig.link(1, True)
        rig.settle()
        self.assertEqual(diverts(unit_b), [(0x0F01, True)])  # its own task works
        self.assertTrue(rig.key(0x0F01)["active"])
        self.assertEqual(rig.saved()["keyboards"], {identity(UNIT_A): ["0x0f01"], identity(UNIT_B): ["0x0f01"]})

    def test_a_request_waiting_behind_the_mouse_s_does_not_go_to_the_unit_that_took_its_slot(self):
        old, mouse = keyboard_device(), mouse_device()
        rig = Rig(self, {1: old, 2: mouse}).start()
        mouse.delay = 0.5  # the mouse's own request holds the receiver meanwhile
        foreground = threading.Thread(target=rig.transport().request, args=(2, 0, 1, bytes([0, 0, 1])))
        foreground.start()
        time.sleep(0.05)
        self.divert(rig)
        time.sleep(0.1)
        self.assertIs(rig.kb._asked[0], keyboard._set_reporting)  # waiting for its turn
        unit_b, sent = self.replaced(rig)
        foreground.join()
        self.found_again(rig, unit_b, sent)
        self.assertEqual(old.sets, [])

    def test_a_request_the_keyboard_s_thread_had_not_made_yet_does_not_go_to_the_unit_that_took_its_slot(self):
        old = keyboard_device()
        rig = Rig(self, {1: old, 2: mouse_device()}).start()
        held, entered, release = self.held()
        with held:
            self.divert(rig)
            self.assertTrue(entered.wait(2.0))
            unit_b, sent = self.replaced(rig)
            release.set()
            self.found_again(rig, unit_b, sent)
        self.assertEqual(old.sets, [])

    def test_settings_changed_meanwhile_call_off_what_the_old_ones_wanted(self):
        kb = keyboard_device()
        rig = Rig(self, {1: kb, 2: mouse_device()}).start()
        held, entered, release = self.held()
        with held:
            self.divert(rig)
            self.assertTrue(entered.wait(2.0))
            new = copy.deepcopy(rig.engine.cfg)
            new["keyboard"] = {"keys": {}}  # reset to defaults before the key was diverted
            rig.engine._apply_config(new)
            release.set()
            rig.settle()
        self.assertEqual(kb.sets, [])  # never diverted, so nothing to give back
        self.assertEqual(rig.saved()["keyboards"], {})  # the record made first is struck off: the keyboard says native
        self.assertFalse(rig.key(0x0F01)["active"])
        self.divert(rig)  # and what comes after works
        rig.settle()
        self.assertEqual(diverts(kb), [(0x0F01, True)])

    def test_another_app_in_front_meanwhile_calls_off_what_the_old_keys_wanted(self):
        kb = keyboard_device()
        rig = Rig(self, {1: kb, 2: mouse_device()})
        rig.engine.cfg["keyboard"]["profiles"] = [profile("editor", {"0x0f01": NATIVE})]
        rig.start()
        held, entered, release = self.held()
        with held:
            self.divert(rig)
            self.assertTrue(entered.wait(2.0))
            task = rig.kb._task
            rig.engine.set_front("browser")  # no keys of its own: the same keys, nothing called off
            rig.engine._handle(rig.engine._queue.get(timeout=1.0))
            rig.engine._take_front(float("inf"))
            self.assertIs(rig.kb._task, task)
            rig.engine.set_front("editor")
            rig.engine._handle(rig.engine._queue.get(timeout=1.0))
            rig.engine._take_front(float("inf"))
            self.assertIsNot(rig.kb._task, task)  # called off, started over
            release.set()
            rig.settle()
        self.assertEqual(kb.sets, [])  # the request held back never went
        self.assertEqual(rig.saved()["keyboards"], {})
        front(rig, "browser")
        self.assertEqual(diverts(kb), [(0x0F01, True)])

    def test_on_exit_a_request_that_had_its_turn_but_not_gone_yet_never_goes(self):
        kb = keyboard_device()
        rig = Rig(self, {1: kb, 2: mouse_device()}).start()
        transport, entered, release = rig.transport(), threading.Event(), threading.Event()
        real = transport._take_turn

        def take_turn(background, deadline, cancel):
            real(background, deadline, cancel)
            if background and not entered.is_set():  # its turn: held between that and the write
                entered.set()
                release.wait(5.0)
        self.addCleanup(release.set)
        with mock.patch.object(transport, "_take_turn", take_turn):
            self.divert(rig)
            self.assertTrue(entered.wait(2.0))
            exiting = threading.Thread(target=rig.kb.shutdown)
            exiting.start()
            self.assertTrue(rig.kb._called_off.wait(2.0))
            release.set()
            exiting.join(5.0)
        self.assertNotIn((0x0F01, True), diverts(kb))
        self.assertEqual(rig.saved()["keyboards"], {})  # the keyboard said native on the way out

    def test_a_divert_sent_before_the_keyboard_went_is_kept_on_record_and_read_back(self):
        kb = keyboard_device()
        rig = Rig(self, {1: kb, 2: mouse_device()}).start()
        kb.delays = {device.REPROG_CONTROLS_V4: 0.5}  # sent, the answer still to come
        self.divert(rig)
        end = time.monotonic() + 2.0
        while not kb.sets and time.monotonic() < end:
            time.sleep(0.01)
        rig.link(1, False)
        self.drain(rig)
        self.assertEqual(diverts(kb), [(0x0F01, True)])
        self.assertEqual(rig.saved()["keyboards"], {identity(UNIT_A): ["0x0f01"]})  # its record stays
        kb.delays = {}
        rig.link(1, True)
        rig.settle()
        self.assertEqual(diverts(kb), [(0x0F01, True)])  # read back as diverted: kept, not sent again
        self.assertTrue(rig.key(0x0F01)["active"])
        rig.keys_down(1, kb, 0x0F01)
        self.assertEqual(rig.input.sent[-2:], [("KEY_LEFTCTRL", True), ("KEY_C", True)])


class Recovery(unittest.TestCase):
    """What another run of the program, or another program, left behind."""

    def journal(self, unit=UNIT_A, ids=("0x0f01",)):
        return {"version": 1, "keyboards": {identity(unit): list(ids)}}

    def test_another_unit_of_the_same_model_is_left_alone(self):
        kb = keyboard_device(UNIT_B)
        kb.reporting[0x0F01] = [0x01, 0]  # diverted by someone
        rig = Rig(self, {1: mouse_device(), 2: kb}, keys={"0x0f01": COPY}, journal=self.journal()).start()
        self.assertEqual(kb.sets, [])
        self.assertEqual(rig.key(0x0F01)["native_reason"], "changed")
        self.assertEqual(rig.saved(), self.journal())
        rig.kb.shutdown()
        self.assertEqual(kb.sets, [])

    def test_the_unit_is_known_by_itself_not_by_its_slot(self):
        other, own = keyboard_device(UNIT_B), keyboard_device(UNIT_A)
        own.reporting[0x0F01] = [0x01, 0]
        rig = Rig(self, {1: mouse_device(), 2: other}, journal=self.journal()).start()
        self.assertEqual(other.sets, [])
        rig.link(2, False)
        rig.port.devices = {1: rig.port.devices[1], 3: own}
        rig.link(3, True)
        rig.settle()
        self.assertEqual(diverts(own), [(0x0F01, False)])  # its own leftover, on another slot: given back
        self.assertEqual(rig.saved()["keyboards"], {})

    def test_a_key_recorded_but_never_diverted_is_struck_off(self):
        kb = keyboard_device()
        rig = Rig(self, {1: mouse_device(), 2: kb}, journal=self.journal()).start()
        self.assertEqual(kb.sets, [])
        self.assertEqual(rig.saved()["keyboards"], {})

    def test_a_leftover_is_given_back_or_kept_as_the_settings_say(self):
        for wanted in (False, True):
            with self.subTest(wanted=wanted):
                kb = keyboard_device()
                kb.reporting[0x0F01] = [0x01, 0]
                rig = Rig(self, {1: mouse_device(), 2: kb}, keys={"0x0f01": COPY} if wanted else {}, journal=self.journal())
                rig.start()
                if wanted:
                    self.assertEqual(kb.sets, [])  # taken over as it is
                    self.assertTrue(rig.key(0x0F01)["active"])
                    self.assertEqual(rig.saved(), self.journal())
                else:
                    self.assertEqual(diverts(kb), [(0x0F01, False)])
                    self.assertEqual(rig.saved()["keyboards"], {})
                rig.stop()

    def test_a_journal_that_cannot_be_written_stops_the_diverting(self):
        kb = keyboard_device()
        rig = Rig(self, {1: mouse_device(), 2: kb}, keys={"0x0f01": COPY})
        folder = os.path.dirname(rig.journal)
        os.chmod(folder, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, folder, stat.S_IRWXU)
        rig.start()
        self.assertEqual(kb.sets, [])
        self.assertFalse(rig.key(0x0F01)["active"])
        self.assertTrue(rig.kb._next_recovery < float("inf"))  # tried again later

    def test_a_key_given_back_stays_recorded_until_the_record_is_written(self):
        kb = keyboard_device()
        kb.reporting[0x0F01] = [0x01, 0]
        rig = Rig(self, {1: mouse_device(), 2: kb}, journal=self.journal())
        folder = os.path.dirname(rig.journal)
        os.chmod(folder, stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(os.chmod, folder, stat.S_IRWXU)
        rig.start()
        self.assertEqual(diverts(kb), [(0x0F01, False)])
        self.assertEqual(rig.saved(), self.journal())
        self.assertEqual(rig.kb._journal, self.journal()["keyboards"])
        os.chmod(folder, stat.S_IRWXU)
        rig.kb.tick(rig.kb._next_recovery)
        rig.settle()
        self.assertEqual(rig.saved()["keyboards"], {})

    def test_an_unreadable_journal_touches_nothing(self):
        kb = keyboard_device()
        kb.reporting[0x0F01] = [0x01, 0]
        rig = Rig(self, {1: mouse_device(), 2: kb}, keys={"0x0f04": COPY}, journal=self.journal())
        os.chmod(rig.journal, 0)
        self.addCleanup(os.chmod, rig.journal, stat.S_IRUSR | stat.S_IWUSR)
        rig.start()
        self.assertEqual(kb.sets, [])
        self.assertEqual(rig.state()["reason"], "journal")
        self.assertEqual(rig.key(0x0F01)["native_reason"], "changed")  # not claimed to be back to normal

    def test_a_journal_in_another_form_is_neither_used_nor_replaced(self):
        good = self.journal()
        cases = {
            "not json": "{nope",
            "version 2": json.dumps(dict(good, version=2)),
            "version true": json.dumps(dict(good, version=True)),
            "more fields": json.dumps(dict(good, note="")),
            "bad identity": json.dumps({"version": 1, "keyboards": {"XYZ": ["0x0f01"]}}),
            "bad key": json.dumps({"version": 1, "keyboards": {identity(UNIT_A): ["0x0F01"]}}),
            "unsorted": json.dumps({"version": 1, "keyboards": {identity(UNIT_A): ["0x0f02", "0x0f01"]}}),
            "twice": json.dumps({"version": 1, "keyboards": {identity(UNIT_A): ["0x0f01", "0x0f01"]}}),
            "empty list": json.dumps({"version": 1, "keyboards": {identity(UNIT_A): []}}),
            "too many keyboards": json.dumps({"version": 1, "keyboards": {f"{n:016x}": ["0x0f01"] for n in range(17)}}),
            "too many keys": json.dumps({"version": 1, "keyboards": {identity(UNIT_A): [f"0x{n:04x}" for n in range(129)]}}),
        }
        for name, text in cases.items():
            with self.subTest(name):
                kb = keyboard_device()
                kb.reporting[0x0F01] = [0x01, 0]
                rig = Rig(self, {1: mouse_device(), 2: kb}, keys={"0x0f04": COPY}, journal=text)
                rig.start()
                self.assertEqual(kb.sets, [])
                self.assertEqual(rig.state()["reason"], "journal")
                with open(rig.journal) as f:
                    self.assertEqual(f.read(), text)
                rig.kb.shutdown()
                self.assertEqual(kb.sets, [])
                rig.stop()

    def test_a_keyboard_that_does_not_say_which_one_it_is_is_left_alone(self):
        variants = {"no device information": {"info": False}, "zeros": {"unit": bytes(4)}, "ones": {"unit": b"\xff" * 4}}
        for name, kwargs in [*variants.items(), ("unreadable", {})]:
            with self.subTest(name):
                kb = keyboard_device(**kwargs) if "unit" in kwargs else FakeDevice(controls=CONTROLS, **kwargs)
                if name == "unreadable":
                    kb.failing[(device.DEVICE_INFO, 0)] = 4
                rig = Rig(self, {1: mouse_device(), 2: kb}, keys={"0x0f01": COPY})
                rig.start()
                self.assertEqual(kb.sets, [])
                self.assertEqual(rig.state()["reason"], "identity")
                self.assertTrue(rig.state()["connected"])  # still shown, with its battery
                self.assertIsNone(rig.saved())
                rig.stop()

    def test_with_other_software_running_no_key_is_touched(self):
        kb = keyboard_device()
        kb.reporting[0x0F01] = [0x01, 0]
        rig = Rig(self, {1: mouse_device(), 2: kb}, keys={"0x0f04": COPY}, journal=self.journal(), rivals=["Solaar"])
        rig.start()
        self.assertEqual(kb.sets, [])
        self.assertEqual(rig.state()["reason"], "rivals")
        self.assertEqual(rig.key(0x0F01)["native_reason"], "changed")
        rig.kb.shutdown()
        self.assertEqual(kb.sets, [])
        self.assertEqual(rig.saved(), self.journal())

    def test_a_recorded_key_someone_else_has_changed_since_is_struck_off(self):
        for reporting in ([0x05, 0], [0x00, 0x0F02]):  # diverted persistently; sent as another key
            with self.subTest(reporting=reporting):
                kb = keyboard_device()
                kb.reporting[0x0F01] = list(reporting)
                rig = Rig(self, {1: mouse_device(), 2: kb}, keys={"0x0f01": COPY}, journal=self.journal())
                rig.start()
                self.assertEqual(kb.sets, [])
                self.assertEqual(rig.saved()["keyboards"], {})
                self.assertEqual(rig.key(0x0F01)["native_reason"], "foreign")
                rig.stop()

    def test_what_the_keyboard_says_after_a_doubtful_divert_decides(self):
        kb = keyboard_device()
        kb.echo = False  # it did divert, but answered something else
        rig = Rig(self, {1: mouse_device(), 2: kb}, keys={"0x0f01": COPY}).start()
        self.assertTrue(rig.key(0x0F01)["active"])
        self.assertEqual(rig.saved()["keyboards"], {identity(UNIT_A): ["0x0f01"]})

        kb = keyboard_device()
        kb.failing[(device.REPROG_CONTROLS_V4, 3)] = 4  # refused: still native
        rig = Rig(self, {1: mouse_device(), 2: kb}, keys={"0x0f01": COPY}).start()
        self.assertFalse(rig.key(0x0F01)["active"])
        self.assertEqual(rig.saved()["keyboards"], {})

    def test_a_divert_without_an_answer_is_kept_and_tried_again(self):
        kb = keyboard_device()
        kb.failing[(device.REPROG_CONTROLS_V4, 3)] = None  # no answer at all
        rig = Rig(self, {1: mouse_device(), 2: kb}, keys={"0x0f01": COPY})
        with mock.patch.object(device.Mouse, "call", lambda self, feature, function, *params, no_reply=False, timeout=1.5:
                               self.transport.request(self.devidx, self.features[feature], function, bytes(params),
                                                      timeout=min(timeout, 0.2), no_reply=no_reply)):
            rig.start()
            self.assertEqual(rig.saved()["keyboards"], {identity(UNIT_A): ["0x0f01"]})  # whether it went through is not known
            self.assertEqual(rig.key(0x0F01)["native_reason"], "pending")
            del kb.failing[(device.REPROG_CONTROLS_V4, 3)]
            rig.kb.tick(rig.kb._next_recovery)
            rig.settle()
        self.assertTrue(rig.key(0x0F01)["active"])

    def test_a_key_that_could_not_be_given_back_on_exit_is_given_back_at_the_next_start(self):
        kb = keyboard_device()
        rig = Rig(self, {1: mouse_device(), 2: kb}, keys={"0x0f01": COPY}).start()
        kb.failing[(device.REPROG_CONTROLS_V4, 3)] = 4
        kb.failing[(device.REPROG_CONTROLS_V4, 2)] = 4  # and it cannot say how the key is
        rig.kb.shutdown()
        self.assertEqual(rig.saved()["keyboards"], {identity(UNIT_A): ["0x0f01"]})
        rig.stop()
        kb.failing.clear()
        again = Rig(self, {1: mouse_device(), 2: kb}, journal=rig.saved()).start()
        self.assertEqual(diverts(kb)[-1], (0x0F01, False))
        self.assertEqual(again.saved()["keyboards"], {})

    def test_which_unit_it_is_stays_in_the_journal(self):
        kb = keyboard_device()
        with self.assertLogs("mxoptions", level="DEBUG") as logs:
            logging.getLogger("mxoptions").debug("start")
            rig = Rig(self, {1: mouse_device(), 2: kb}, keys={"0x0f01": COPY}).start()
            rig.keys_down(2, kb, 0x0f01)
            rig.kb.shutdown()
        secret = (identity(UNIT_A), UNIT_A.hex(), UNIT_A.hex(" "), MODEL.hex())
        shown = json.dumps([rig.states, rig.engine.cfg, logs.output], default=str)
        for text in secret:
            self.assertNotIn(text, shown)


class Journal(unittest.TestCase):
    def test_written_for_the_user_alone_and_read_back(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        path = os.path.join(folder, "sub", "keyboard-diverted.json")
        entries = {identity(UNIT_A): ["0x0f02", "0x0f01"], identity(UNIT_B): []}
        keyboard.write_journal(entries, path)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path) as f:
            self.assertEqual(json.load(f), {"version": 1, "keyboards": {identity(UNIT_A): ["0x0f01", "0x0f02"]}})
        self.assertEqual(keyboard.read_journal(path), {identity(UNIT_A): ["0x0f01", "0x0f02"]})
        self.assertEqual(keyboard.read_journal(os.path.join(folder, "none.json")), {})


class Battery(unittest.TestCase):
    def test_low_battery_is_said_once_per_discharge(self):
        armed, said = True, []
        for reading in [(40, 0), (16, 0), (15, 0), (14, 0), (9, 0), (9, 1), (30, 0), (12, 0), None, (12, 0)]:
            armed, say = keyboard.low_battery(armed, reading)
            said.append(say)
        self.assertEqual(said, [False, False, True, False, False, False, False, True, False, False])


class State(unittest.TestCase):
    def test_without_a_keyboard_the_state_is_as_before(self):
        made = engine.Engine(copy.deepcopy(config.DEFAULTS), Recorder())
        seen = []
        made.listener.on_state = seen.append
        made._publish()
        self.assertEqual(set(seen[0]), {"connected", "known", "name", "uinput", "rivals", "battery", "current_host",
                                        "hosts", "receiver", "dpi", "wheel_mode", "caps", "keyboard"})
        self.assertIsNone(seen[0]["keyboard"])

    def test_the_keyboard_and_its_receiver_go_their_own_ways(self):
        kb = keyboard_device()
        rig = Rig(self, {2: mouse_device()})
        rig.ports[OTHER_PATH] = FakePort({0xFF: kb}, receiver=False)
        rig.nodes.append((OTHER_PATH, False))
        rig.start()
        rig.kb._next_nodes = 0.0
        rig.engine._tick()
        rig.settle()
        self.assertIs(rig.kb.transport, rig.transport(OTHER_PATH))
        rig.engine._drop_transport(rig.transport(OTHER_PATH))  # the keyboard's Bluetooth link drops
        self.assertIsNone(rig.kb.device)
        self.assertTrue(rig.engine.online)
        rig.engine._drop_transport(rig.transport())
        self.assertIsNone(rig.engine.mouse)

    def test_the_keyboard_coming_and_going_leaves_the_mouse_be(self):
        kb, mouse = keyboard_device(), mouse_device()
        rig = Rig(self, {1: mouse, 2: kb}).start()
        sent = len(rig.port.sent(1))
        rig.link(2, False)
        rig.link(2, True)
        rig.settle()
        self.assertTrue(rig.engine.online)
        self.assertEqual(len(rig.port.sent(1)), sent)  # the mouse was not set up again
        self.assertEqual(rig.kb.device.devidx, 2)


class MacroSteps(unittest.TestCase):
    """Vibration steps reach the mouse only through the engine's queue, from either executor."""

    def haptic(self, rig):
        """The next vibration asked of the engine; what else was queued is handled as the engine would."""
        end = time.monotonic() + 2.0
        while time.monotonic() < end:
            item = rig.engine._queue.get(timeout=2.0)
            if item[0] == "haptic":
                return item
            rig.engine._handle(item)
        raise AssertionError("no vibration was asked for")

    def test_a_key_s_vibration_step_plays_only_on_a_mouse_that_can(self):
        rig = Rig(self, {1: keyboard_device(), 2: mouse_device()}).start()
        rig.kb.executor.trigger({"type": "macro", "steps": [{"do": "haptic", "waveform": 4}]})
        item = self.haptic(rig)
        self.assertEqual(item, ("haptic", 4, False))
        sent = len(rig.port.written)
        rig.engine._handle(item)  # the synthetic mouse has no haptic motor
        self.assertEqual(len(rig.port.written), sent)
        played = []
        motor = mock.Mock(haptic_waveforms=[4], play_haptic=lambda waveform, wait: played.append((waveform, wait)))
        for mouse, online in ((None, False), (motor, False), (motor, True)):
            rig.engine.mouse, rig.engine.online = mouse, online
            rig.engine._handle(item)
        self.assertEqual(played, [(4, True)])
        self.assertEqual(len(rig.port.written), sent)

    def test_the_mouse_s_macros_vibrate_through_the_same_queue(self):
        made = engine.Engine(copy.deepcopy(config.DEFAULTS), Recorder())
        made._publish = lambda: None
        with mock.patch.object(engine.vinput, "create", FakeInput), mock.patch.object(made.keyboard, "start"), \
                mock.patch.object(made.keyboard, "shutdown"):
            made._running = False
            made._run()
        self.assertEqual(made.executor.macros.haptic, made.play_haptic)
        self.assertEqual(made.executor.macros.trigger, made.executor.trigger)
        made.executor.macros._step({"do": "haptic", "waveform": 7})
        self.assertEqual(made._queue.get_nowait(), ("haptic", 7, False))


RING = {"type": "ring"}


class RingFromAKey(unittest.TestCase):
    """A key given the Actions Ring opens it as the mouse's ring button does. The ring belongs to whichever pressed
    it last, mouse button or key; only that one's release, reset or going away closes it."""

    def setUp(self):
        self.kb, self.mouse = keyboard_device(), mouse_device()

    def rig(self, keys=None, profiles=(), mouse_ring=True, vinput=True):
        rig = Rig(self, {1: self.kb, 2: self.mouse}, keys={"0x0f01": RING, "0x0053": RING, "0x0f02": COPY,
                                                           "0x0f04": {"type": "host", "host": 1}} if keys is None else keys)
        rig.engine.cfg["keyboard"]["profiles"] = copy.deepcopy(list(profiles))
        if mouse_ring:
            rig.engine.cfg["buttons"]["back"] = RING  # the mouse's back button is control 0x0053 too
        if not vinput:
            rig.engine.vinput = None
        self.rings = []
        rig.listener.on_ring = lambda event, *_: self.rings.append(event)
        return rig.start()

    def mouse_buttons(self, rig, *cids):
        rig.note(2, self.mouse, device.REPROG_CONTROLS_V4, struct.pack("!HHHH", *(list(cids) + [0, 0, 0, 0])[:4]))

    def test_a_key_opens_and_lets_go_of_the_ring_and_runs_nothing(self):
        rig = self.rig()
        self.assertTrue(rig.key(0x0F01)["active"])
        with mock.patch.object(rig.kb.executor, "press") as press, mock.patch.object(rig.kb.executor, "release") as release:
            rig.keys_down(1, self.kb, 0x0f01)
            self.assertEqual(self.rings, ["down"])
            rig.keys_down(1, self.kb)
        self.assertEqual(self.rings, ["down", "up"])
        press.assert_not_called()
        release.assert_not_called()
        self.assertEqual(rig.input.sent, [])
        self.assertEqual(rig.mouse_actions.ran, [])
        told = [kind for kind, payload in rig.listener.events if (kind, payload[0]) != ("flow", "keyboard")]
        self.assertEqual(told, [])  # no button event for other programs

    def test_the_same_report_again_changes_nothing(self):
        rig = self.rig()
        rig.keys_down(1, self.kb, 0x0f01)
        rig.keys_down(1, self.kb, 0x0f01)
        rig.keys_down(1, self.kb)
        rig.keys_down(1, self.kb)
        self.assertEqual(self.rings, ["down", "up"])

    def test_a_ring_of_one_app_only_works_there(self):
        rig = self.rig(keys={"0x0f02": RING}, profiles=[profile("kate", {"0x0f01": RING, "0x0f02": NATIVE})])
        for app in (None, "kate", "browser", None, "kate"):
            with self.subTest(app=app):
                front(rig, app)
                in_kate = app == "kate"  # there the ring of every app's key is not this app's
                self.assertEqual((rig.key(0x0F01)["active"], rig.key(0x0F02)["active"]), (in_kate, not in_kate))
                for cid in (0x0F01, 0x0F02):
                    del self.rings[:]
                    rig.keys_down(1, self.kb, cid)
                    rig.keys_down(1, self.kb)
                    self.assertEqual(self.rings, ["down", "up"] if (cid == 0x0F01) == in_kate else [])

    def test_another_app_in_front_closes_a_held_ring_and_runs_nothing(self):
        rig = self.rig(keys={"0x0f01": RING}, profiles=[profile("kate", {"0x0f01": COPY})])
        rig.keys_down(1, self.kb, 0x0f01)
        front(rig, "kate")
        self.assertEqual(self.rings, ["down", "cancel"])
        rig.keys_down(1, self.kb)  # its key-up: nothing
        self.assertEqual(self.rings, ["down", "cancel"])
        self.assertEqual(rig.input.sent, [])

    def test_new_settings_close_a_held_ring_and_its_key_up_does_nothing(self):
        rig = self.rig()
        rig.keys_down(1, self.kb, 0x0f01)
        new = copy.deepcopy(rig.engine.cfg)
        new["keyboard"]["keys"]["0x0f01"] = COPY
        rig.engine._apply_config(new)
        rig.settle()
        self.assertEqual(self.rings, ["down", "cancel"])
        rig.keys_down(1, self.kb)
        self.assertEqual(self.rings, ["down", "cancel"])
        self.assertEqual(rig.input.sent, [])  # neither a press nor a release of the new action

    def test_new_settings_that_keep_the_key_s_ring_keep_it_open(self):
        rig = self.rig()
        rig.keys_down(1, self.kb, 0x0f01)
        new = copy.deepcopy(rig.engine.cfg)
        new["keyboard"]["keys"]["0x0f02"] = CTRL
        rig.engine._apply_config(new)
        rig.settle()
        rig.keys_down(1, self.kb)
        self.assertEqual(self.rings, ["down", "up"])

    def test_the_keyboard_going_away_closes_its_own_ring(self):
        rig = self.rig()
        rig.keys_down(1, self.kb, 0x0f01)
        rig.link(1, False)
        self.assertIsNone(rig.kb.device)
        self.assertEqual(self.rings, ["down", "cancel"])
        self.assertTrue(rig.engine.online)
        self.assertEqual(rig.mouse_actions.ran, [])

    def test_the_keyboard_going_away_leaves_the_mouse_s_ring(self):
        rig = self.rig()
        self.mouse_buttons(rig, 0x0053)
        rig.link(1, False)
        self.assertEqual(self.rings, ["down"])
        self.mouse_buttons(rig)
        self.assertEqual(self.rings, ["down", "up"])

    def test_the_mouse_starting_over_leaves_the_key_s_ring(self):
        rig = self.rig()
        rig.keys_down(1, self.kb, 0x0053)  # the same control id as the mouse's ring button
        rig.engine._reset_input_state()
        self.assertEqual(self.rings, ["down"])
        rig.keys_down(1, self.kb)
        self.assertEqual(self.rings, ["down", "up"])

    def test_the_key_pressed_after_the_mouse_holds_the_ring(self):
        rig = self.rig()
        self.mouse_buttons(rig, 0x0053)
        rig.keys_down(1, self.kb, 0x0053)
        self.mouse_buttons(rig)  # the mouse's release: not its ring any more
        rig.engine._reset_input_state()
        self.assertEqual(self.rings, ["down", "down"])
        rig.keys_down(1, self.kb)
        self.assertEqual(self.rings, ["down", "down", "up"])

    def test_the_mouse_pressed_after_the_key_holds_the_ring(self):
        rig = self.rig()
        rig.keys_down(1, self.kb, 0x0053)
        self.mouse_buttons(rig, 0x0053)
        rig.keys_down(1, self.kb)  # the key's release: not its ring any more
        self.assertEqual(self.rings, ["down", "down"])
        rig.link(1, False)
        self.assertEqual(self.rings, ["down", "down"])
        self.mouse_buttons(rig)
        self.assertEqual(self.rings, ["down", "down", "up"])

    def test_of_two_ring_keys_the_last_pressed_holds_it(self):
        rig = self.rig()
        rig.keys_down(1, self.kb, 0x0f01)
        rig.keys_down(1, self.kb, 0x0f01, 0x0053)
        rig.keys_down(1, self.kb, 0x0053)  # the first one up: not its ring any more
        self.assertEqual(self.rings, ["down", "down"])
        rig.keys_down(1, self.kb)
        self.assertEqual(self.rings, ["down", "down", "up"])

    def test_easy_switch_and_fn_never_open_it(self):
        protected = {config.key_id(cid): RING for cid in device.CID_PROTECTED}
        rig = self.rig(keys=protected)
        self.assertEqual(self.kb.sets, [])
        self.assertIsNone(rig.saved())
        rig.keys_down(1, self.kb, 0x00D1, 0x0034)
        rig.keys_down(1, self.kb)
        self.assertEqual((self.rings, rig.kb._held), ([], {}))
        self.assertIsNone(rig.saved())

    def test_without_input_access_the_ring_key_still_works(self):
        rig = self.rig(keys={"0x0f01": RING, "0x0f02": COPY}, vinput=False)
        self.assertEqual(diverts(self.kb), [(0x0F01, True)])  # a key that types needs input access
        rig.keys_down(1, self.kb, 0x0f01)
        rig.keys_down(1, self.kb)
        self.assertEqual(self.rings, ["down", "up"])

    def test_the_mouse_s_ring_button_alone_is_as_before(self):
        rig = self.rig()
        self.mouse_buttons(rig, 0x0053)
        self.mouse_buttons(rig)
        self.mouse_buttons(rig, 0x0053)
        rig.engine._reset_input_state()
        self.mouse_buttons(rig)
        self.assertEqual(self.rings, ["down", "up", "down", "cancel"])
        rig.engine.cfg["buttons"]["forward"] = RING  # two ring buttons: the last pressed holds it
        del self.rings[:]
        for press, cid in ((True, device.CID_BACK), (True, device.CID_FORWARD), (False, device.CID_BACK),
                           (False, device.CID_FORWARD)):
            (rig.engine._button_down if press else rig.engine._button_up)(cid)
        self.assertEqual(self.rings, ["down", "down", "up"])

    def test_on_exit_the_key_s_ring_closes_and_the_mouse_s_is_left(self):
        for holder in ("key", "mouse"):
            with self.subTest(holder=holder):
                self.kb = keyboard_device()
                rig = self.rig()
                rig.engine.executor = actions.Executor(rig.input, rig.engine._device_action)
                if holder == "key":
                    rig.keys_down(1, self.kb, 0x0053)
                else:
                    self.mouse_buttons(rig, 0x0053)
                rig.engine._shutdown()
                self.assertEqual(self.rings, ["down", "cancel"] if holder == "key" else ["down"])

    def test_easy_switch_from_a_key_closes_the_key_s_ring_only(self):
        for holder in ("key", "mouse"):
            with self.subTest(holder=holder):
                self.kb = keyboard_device()
                rig = self.rig()
                if holder == "key":
                    rig.keys_down(1, self.kb, 0x0f01)
                    rig.keys_down(1, self.kb, 0x0f01, 0x0f04)
                else:
                    self.mouse_buttons(rig, 0x0053)
                    rig.keys_down(1, self.kb, 0x0f04)
                rig.settle()
                self.assertEqual(self.kb.switched, [1])
                self.assertEqual(self.rings, ["down", "cancel"] if holder == "key" else ["down"])
                if holder == "mouse":
                    self.mouse_buttons(rig)
                    self.assertEqual(self.rings, ["down", "up"])

    def test_a_keyboard_that_cannot_give_its_keys_back_keeps_its_ring(self):
        rig = self.rig()
        self.kb.failing[(device.REPROG_CONTROLS_V4, 3)] = 4
        rig.keys_down(1, self.kb, 0x0f01)
        rig.keys_down(1, self.kb, 0x0f01, 0x0f04)
        rig.settle()
        self.assertEqual((self.kb.switched, rig.state()["failed"]), ([], "switch"))
        self.assertEqual(self.rings, ["down"])
        rig.keys_down(1, self.kb)
        self.assertEqual(self.rings, ["down", "up"])


class Picker(unittest.TestCase):
    def test_a_key_is_offered_nothing_it_cannot_do(self):
        from mxoptions.ui import keyboard_page
        for preset in actions.PRESETS:
            kind = preset.action.get("type")
            with self.subTest(preset.id):
                if kind not in config.KEYBOARD_ACTIONS and kind != "default":
                    self.assertIn(preset.id, keyboard_page.KEYBOARD_EXCLUDE)
                else:
                    self.assertNotIn(preset.id, keyboard_page.KEYBOARD_EXCLUDE)
        self.assertEqual(set(keyboard_page.KEYBOARD_EXCLUDE), {"gestures", "wheel_mode", "dpi_cycle", "dpi_shift", "flow", "spotlight"})


# -- the keyboard following the mouse (KeyboardSide.follow) ----------------------------------------------------------

KB_PATH = "/dev/synthetic-kb"


class PairedKeyboard(FakeDevice):
    """A synthetic keyboard that also says which of its channels are paired, and their names (HOSTS_INFO).
    ``fail_at``: the channel whose question it answers with an error; ``capable``: it says it can tell at all."""

    def __init__(self, paired=(True, True, False), names=("desk", "", ""), capable=True, fail_at=None, current=0, **kwargs):
        super().__init__(controls=CONTROLS, unit=UNIT_A, model=MODEL, extra=(device.HOSTS_INFO,),
                         hosts=(len(paired), current), **kwargs)
        self.paired, self.names, self.capable, self.fail_at = list(paired), list(names), capable, fail_at

    def answer(self, feat_idx, function, params):
        if (self.silent or feat_idx >= len(self.features) or self.features[feat_idx] != device.HOSTS_INFO
                or (device.HOSTS_INFO, function) in self.failing):
            return super().answer(feat_idx, function, params)
        if function == 0:
            return bytes([1 if self.capable else 0])
        host = params[0]
        if host == self.fail_at:
            return 2
        name = self.names[host].encode()
        if function == 1:
            return bytes([host, 1 if self.paired[host] else 0, 0, 0, len(name)])
        if function == 3:
            return bytes([host, params[1]]) + name[params[1]:params[1] + 14]
        return 7


@unittest.skipIf(sys.platform == "win32", "the keyboard is asked which keys are down on Linux only")
class FollowsMouse(unittest.TestCase):
    """The keyboard on a Bluetooth node of its own, sent after the mouse once no key is down. The nodes and every
    answer about the keys are synthetic (tests/test_transport.py); the engine runs on the test's thread."""

    def setUp(self):
        self.ioctls = bluetooth(self, dict(ZERO))

    def make(self, kb=None, desc=AGGREGATE, direct=False, flag=True):
        """The mouse on a receiver (or on Bluetooth, ``direct``), the keyboard (a new PairedKeyboard unless given) on
        a node of its own."""
        kb = self.kb = kb or PairedKeyboard()
        mouse = mouse_device()
        rig = Rig(self, {0xFF: mouse} if direct else {2: mouse}, keys={"0x0f01": CTRL, "0x0f02": COPY}, receiver=not direct)
        rig.engine.cfg["flow"]["keyboard_follows_mouse"] = flag
        rig.start()
        rig.ports[KB_PATH] = KeyNode(FakePort({0xFF: kb}, receiver=False), KB_PATH, desc)
        rig.nodes.append((KB_PATH, False))
        rig.kb._next_nodes = 0.0
        rig.engine._tick()
        rig.settle()
        self.assertEqual((rig.kb.transport.path, rig.kb.device.devidx), (KB_PATH, 0xFF))
        self.assertTrue(rig.key(0x0F01)["active"])
        self.rig, self.node = rig, rig.ports[KB_PATH]
        return rig

    def follow(self, host=1, event=None, link=None, settle=True):
        """Flow asks the engine to send the keyboard after the mouse to ``host``."""
        event = event or threading.Event()
        self.rig.engine.keyboard_follow(host, link or self.rig.kb.link, event)
        if settle:
            self.rig.settle()
        return event

    def stays(self, why, **kwargs):
        sets = len(self.kb.sets)
        with self.assertLogs("mxoptions.keyboard", "INFO") as logs:
            self.follow(**kwargs)
        self.assertIn(f"flow: the keyboard stays ({why})", logs.output[-1])
        self.assertEqual(self.kb.switched, [])
        return sets, logs

    def blocked(self):
        """Hold every read of the keys back until the Event returned is set (as a slow Bluetooth answer would)."""
        release, reading = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def wait(_rid):
            reading.set()
            release.wait(5.0)
        self.ioctls.wait = wait
        return release, reading

    def pump(self, until, timeout=4.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end and not until():
            try:
                self.rig.engine._handle(self.rig.engine._queue.get(timeout=0.02))
            except queue.Empty:
                pass
        self.assertTrue(until())

    # -- it goes ---------------------------------------------------------------------------------------------------

    def test_with_no_key_down_it_goes_after_the_keys_are_given_back(self):
        rig = self.make()
        resets = []
        real_reset = rig.kb.executor.reset
        rig.kb.executor.reset = lambda: (resets.append(1), real_reset())
        written = []
        self.ioctls.wait = lambda _rid: written.append(len(self.node.fake.written))
        start = len(self.node.fake.written)
        with self.assertLogs("mxoptions.keyboard", "INFO") as logs:
            self.follow(1)
        self.assertEqual(self.kb.switched, [1])
        self.assertIn("flow: the keyboard follows the mouse to channel 2", logs.output[-1])
        packets = self.node.fake.written[start:]
        give_back = [i for i, p in enumerate(packets) if p[2] == self.kb.index(device.REPROG_CONTROLS_V4) and p[3] >> 4 == 3]
        switch = [i for i, p in enumerate(packets) if p[2] == self.kb.index(device.CHANGE_HOST) and p[3] >> 4 == 1]
        self.assertEqual(len(switch), 1)
        self.assertEqual(len(give_back), 2)  # each of its keys, once
        self.assertEqual(self.ioctls.ids(), [1, 3, 4])  # every key report, once, after the keys were given back
        self.assertTrue(all(max(give_back) < at - start <= switch[0] for at in written))
        self.assertEqual(resets, [1])
        self.assertEqual(rig.saved()["keyboards"], {})
        self.assertIsNotNone(rig.kb._sent_at)
        self.assertGreater(rig.kb._leaving, time.monotonic())
        self.assertIsNone(rig.kb._failed)
        self.assertEqual({call[4] for call in self.ioctls.calls}, {"keyboard"})  # on the keyboard's thread only
        rig.engine._drop_transport(rig.transport(KB_PATH))  # and when it goes, it went because of this
        self.assertTrue(rig.kb.last_drop[1])

    def test_the_mouse_on_bluetooth_is_waited_for_first(self):
        rig = self.make(direct=True)
        mouse_path = PATH
        self.follow(1)
        self.assertIsNotNone(rig.kb._follow_wait)
        self.assertEqual((self.ioctls.calls, self.kb.switched), ([], []))
        self.assertEqual(diverts(self.kb)[-1][1], True)  # nothing given back yet
        self.assertLessEqual(rig.kb.due(time.monotonic()), keyboard.FOLLOW_SETTLE)
        rig.engine._tick()
        self.assertIsNotNone(rig.kb._follow_wait)  # still there
        rig.engine._drop_transport(rig.transport(mouse_path))  # gone to the other computer
        rig.engine._tick()
        rig.settle()
        self.assertEqual(self.kb.switched, [1])
        self.assertIsNone(rig.kb._follow_wait)

    def test_a_mouse_on_bluetooth_that_does_not_go_in_time_keeps_the_keyboard(self):
        for name in ("late", "cancelled", "not-here"):
            with self.subTest(name):
                rig = self.make(direct=True)
                event = self.follow(1)
                with self.assertLogs("mxoptions.keyboard", "INFO") as logs:
                    if name == "late":
                        rig.kb.tick(time.monotonic() + keyboard.FOLLOW_SETTLE + 0.1)
                    elif name == "cancelled":
                        event.set()
                        rig.kb.tick(time.monotonic())
                    else:
                        rig.engine._drop_transport(rig.transport(KB_PATH))
                self.assertIn(f"stays ({name})", logs.output[-1])
                self.assertIsNone(rig.kb._follow_wait)
                rig.engine._drop_transport(rig.transport(PATH))
                rig.engine._tick()
                rig.settle()
                self.assertEqual((self.ioctls.calls, self.kb.switched), ([], []))
                rig.stop()

    # -- it stays, and why -----------------------------------------------------------------------------------------

    def test_before_anything_is_asked_each_reason_to_stay(self):
        rig = self.make()
        hosts = copy.deepcopy(rig.kb._hosts)
        called_off = threading.Event()
        called_off.set()
        cases = [  # (what, why, what makes it so, channel, more for follow())
            ("called off", "cancelled", None, 1, {"event": called_off}),
            ("switched off", "off", lambda: rig.engine.cfg["flow"].update(keyboard_follows_mouse=False), 1, {}),
            ("another keyboard", "not-here", None, 1, {"link": "0" * 16}),
            ("offline", "not-here", lambda: setattr(rig.kb, "online", False), 1, {}),
            ("leaving", "busy", lambda: setattr(rig.kb, "_leaving", time.monotonic() + 30), 1, {}),
            ("a switch wished", "busy", lambda: setattr(rig.kb, "_wish", ("host", 2)), 1, {}),
            ("one waiting", "busy", lambda: setattr(rig.kb, "_follow_wait", (1, rig.kb.link, threading.Event(), 0, 0)), 1, {}),
            ("its own channel", "pairing", None, 0, {}),
            ("a channel not paired", "pairing", None, 2, {}),
            ("past the last channel", "pairing", None, 3, {}),
            ("pairing never read", "pairing", lambda: setattr(rig.kb.device, "pairing_read", False), 1, {}),
            ("fewer channels than it has", "pairing", lambda: setattr(rig.kb, "_hosts", (0, hosts[1][:2])), 1, {}),
            ("current channel out of range", "pairing", lambda: setattr(rig.kb, "_hosts", (3, hosts[1])), 1, {}),
        ]
        for what, why, prepare, host, more in cases:
            with self.subTest(what):
                if prepare:
                    prepare()
                sets, _logs = self.stays(why, host=host, **more)
                self.assertEqual(len(self.kb.sets), sets)  # not a key given back
                self.assertEqual(self.ioctls.calls, [])
                rig.engine.cfg["flow"]["keyboard_follows_mouse"] = True
                rig.kb._leaving, rig.kb._wish, rig.kb._follow_wait, rig.kb.online = 0.0, None, None, True
                rig.kb._hosts, rig.kb.device.pairing_read = copy.deepcopy(hosts), True
        self.follow(1)  # and with all as it should be, it goes
        self.assertEqual(self.kb.switched, [1])

    def test_a_keyboard_that_cannot_be_asked_stays(self):
        buttons = report(1, MODS) + report(2, page(0x09) + umin(1) + umax(8) + lmax(1) + size(1) + count(8) + inp(0x02))
        self.make(desc=buttons)
        self.stays("unsupported")
        self.assertEqual(self.ioctls.calls, [])
        self.kb = PairedKeyboard()
        rig = Rig(self, {1: self.kb, 2: mouse_device()}, keys={"0x0f01": CTRL})  # on the mouse's receiver
        rig.engine.cfg["flow"]["keyboard_follows_mouse"] = True
        rig.start()
        self.rig = rig
        self.assertTrue(rig.kb.transport.is_receiver)
        self.stays("unsupported")
        self.assertEqual(self.ioctls.calls, [])

    def test_a_key_down_keeps_it_here_with_its_keys(self):
        cases = {
            "held": ({1: bytes([1, 0, 0, 0x0D]) + bytes(5)}, None),
            "held (media)": ({3: bytes([3, 0xEA, 0, 0, 0])}, None),
            "unknown": ({3: OSError(errno.EIO, "synthetic")}, None),
            "late": ({}, 0.05),  # the last report read past KEYS_FRESH: all of them were in time to be asked
            "unknown (out of time)": ({}, 0.01),  # past it before the last report: that one is not asked
        }
        for name, (answers, fresh) in cases.items():
            with self.subTest(name):
                rig = self.make()
                self.ioctls.answers = {**ZERO, **answers}
                why = name.split(" ")[0]
                if fresh is not None:
                    self.ioctls.wait = lambda rid_: time.sleep(0.1 if rid_ == 4 else 0.02 if fresh < 0.05 else 0)
                with mock.patch.object(keyboard, "KEYS_FRESH", fresh if fresh is not None else keyboard.KEYS_FRESH):
                    self.stays(why)
                self.assertIsNone(rig.kb._sent_at)
                self.assertTrue(rig.key(0x0F01)["active"])  # taken again
                self.assertEqual(diverts(self.kb)[-1][1], True)
                self.assertEqual(rig.saved()["keyboards"], {identity(UNIT_A): ["0x0f01", "0x0f02"]})
                self.ioctls.wait = None
                rig.stop()

    def test_a_key_it_follows_down_keeps_it_without_asking(self):
        rig = self.make()
        rig.kb._on_keys({0x0F01})  # down, as the keyboard reported
        self.stays("held")
        self.assertEqual(self.ioctls.calls, [])
        self.assertTrue(rig.key(0x0F01)["active"])

    def test_a_ring_key_held_keeps_it_and_its_ring(self):
        rig = self.make()
        rig.engine.cfg["keyboard"]["keys"]["0x0f01"] = {"type": "ring"}
        rings = []
        rig.listener.on_ring = lambda event, *_: rings.append(event)
        rig.kb._on_keys({0x0F01})
        self.stays("held")
        rig.kb._on_keys(set())
        self.assertEqual(rings, ["down", "up"])

    def test_called_off_while_the_keys_are_read(self):
        rig = self.make()
        event = threading.Event()
        self.ioctls.wait = lambda rid_: event.set() if rid_ == 4 else None
        self.stays("cancelled", event=event)
        self.assertTrue(rig.key(0x0F01)["active"])

    def test_a_key_that_cannot_be_given_back_keeps_it(self):
        rig = self.make()
        self.kb.failing[(device.REPROG_CONTROLS_V4, 3)] = 4
        self.stays("give-back")
        self.assertEqual(rig.state()["failed"], "switch")
        self.assertEqual(self.ioctls.calls, [])

    # -- called off while it is under way -----------------------------------------------------------------------------

    def test_whatever_calls_it_off_while_the_keys_are_read_nothing_is_sent(self):
        def drop(rig, event):
            rig.kb.transport_gone(rig.kb.transport)

        def off(rig, event):
            new = copy.deepcopy(rig.engine.cfg)
            new["flow"]["keyboard_follows_mouse"] = False
            rig.engine._apply_config(new)

        def mouse_back(rig, event):
            rig.engine.online = False
            rig.engine._set_online(True)
        stops = {"keyboard gone": drop, "setting off": off, "mouse back here": mouse_back,
                 "Flow calls it off": lambda rig, event: (event.set(), rig.engine.keyboard_follow_cancel(event)),
                 "shutdown": lambda rig, event: rig.kb.shutdown()}
        for name, stop in stops.items():
            with self.subTest(name):
                rig = self.make()
                release, reading = self.blocked()
                event = self.follow(settle=False)
                self.pump(reading.is_set)
                self.assertTrue(rig.kb.reading_keys())
                if name == "shutdown":
                    threading.Timer(0.2, release.set).start()
                stop(rig, event)
                if name == "Flow calls it off":
                    self.pump(lambda: rig.kb._task is None or not rig.kb._following)
                self.assertTrue(event.is_set())
                release.set()
                self.pump(lambda: not rig.kb._reading.is_set())
                time.sleep(0.05)
                while not rig.engine._queue.empty():
                    rig.engine._handle(rig.engine._queue.get())
                self.assertEqual(self.kb.switched, [], name)
                self.ioctls.wait = None
                rig.stop()

    def test_an_answer_for_a_keyboard_gone_and_back_is_dropped(self):
        rig = self.make()
        release, reading = self.blocked()
        self.follow(settle=False)
        self.pump(reading.is_set)
        transport = rig.kb.transport
        rig.kb.transport_gone(transport)
        rig.engine._transports[KB_PATH] = transport
        rig.kb._queue(transport, [0xFF])  # found again: a new start
        self.ioctls.wait = None
        release.set()
        rig.settle()
        self.assertIsNotNone(rig.kb.device)
        self.assertEqual(self.kb.switched, [])
        self.assertTrue(rig.key(0x0F01)["active"])

    def test_one_wished_behind_other_requests_is_called_off_before_it_starts(self):
        rig = self.make()
        self.kb.delays[device.REPROG_CONTROLS_V4] = 0.3
        rig.kb._reporting.pop(0x0F02)
        rig.kb.configure()  # asks the keyboard about a key: under way
        self.assertIsNotNone(rig.kb._task)
        event = self.follow(settle=False)
        rig.engine._handle(rig.engine._queue.get(timeout=1.0))
        self.assertEqual(rig.kb._wish[0], "follow")
        with self.assertLogs("mxoptions.keyboard", "INFO") as logs:
            event.set()
            rig.engine.keyboard_follow_cancel(event)
            rig.settle()
        self.assertIn("stays (cancelled)", "\n".join(logs.output))
        self.assertEqual((self.ioctls.calls, self.kb.switched), ([], []))

    def test_an_easy_switch_from_a_key_goes_before_a_wished_follow(self):
        rig = self.make()
        self.kb.delays[device.REPROG_CONTROLS_V4] = 0.3
        rig.kb._reporting.pop(0x0F02)
        rig.kb.configure()
        self.follow(settle=False)
        rig.engine._handle(rig.engine._queue.get(timeout=1.0))
        with self.assertLogs("mxoptions.keyboard", "INFO") as logs:
            rig.kb.device_action({"type": "host", "host": 2}, "press")
        self.assertIn("stays (busy)", logs.output[-1])
        rig.settle()
        self.assertEqual((self.ioctls.calls, self.kb.switched), ([], [2]))

    def test_one_follow_at_a_time(self):
        rig = self.make()
        release, reading = self.blocked()
        self.follow(settle=False)
        self.pump(reading.is_set)
        with self.assertLogs("mxoptions.keyboard", "INFO") as logs:
            start = time.monotonic()
            self.follow(settle=False)
            rig.engine._handle(rig.engine._queue.get(timeout=1.0))
            self.assertLess(time.monotonic() - start, 0.1)
        self.assertIn("stays (busy)", logs.output[-1])
        release.set()
        rig.settle()
        self.assertEqual(self.kb.switched, [1])

    # -- nothing waits on it ------------------------------------------------------------------------------------------

    def test_the_engine_goes_on_and_opens_no_node_while_the_keys_are_read(self):
        rig = self.make(direct=True)
        rig.engine._drop_transport(rig.transport(PATH))  # the mouse has gone
        scans = []
        rig.engine._scan = lambda: scans.append(1)
        release, reading = self.blocked()
        start = time.monotonic()
        self.follow(settle=False)
        self.assertLess(time.monotonic() - start, 0.05)
        self.pump(reading.is_set)
        for _ in range(3):
            rig.engine._next_scan = 0.0
            start = time.monotonic()
            rig.engine.set_front("synthetic.app")
            rig.engine._handle(rig.engine._queue.get(timeout=1.0))
            rig.engine._tick()
            self.assertLess(time.monotonic() - start, 0.1)
        self.assertEqual(scans, [])  # opening a node now would wait on the read
        release.set()
        rig.settle()
        self.assertFalse(rig.kb.reading_keys())
        rig.engine._next_scan = 0.0
        rig.engine._tick()
        self.assertEqual(scans, [1])
        self.assertEqual(self.kb.switched, [1])

    def test_the_keys_are_only_ever_read_on_the_keyboard_s_thread(self):
        rig = self.make()
        self.ioctls.wait = lambda _rid: self.assertEqual(threading.current_thread().name, "keyboard")
        rig.kb.follow(1, rig.kb.link, threading.Event())
        self.assertEqual(self.ioctls.calls, [])  # handed on, not read here
        rig.settle()
        self.assertEqual(len(self.ioctls.calls), 3)

    # -- what Flow is told ---------------------------------------------------------------------------------------------

    def test_flow_hears_the_channel_only_when_the_keyboard_said_it_is_paired(self):
        def told(rig):
            return [payload[1] for kind, payload in rig.listener.events if kind == "flow" and payload[0] == "keyboard"]
        rig = self.make()
        self.assertEqual(told(rig)[-1]["channel"], 0)
        rig.engine._drop_transport(rig.transport(KB_PATH))
        self.assertIsNone(told(rig)[-1]["channel"])
        for name, kb in (("not paired", PairedKeyboard(paired=(True, True, False), current=2)),
                         ("not said", PairedKeyboard(capable=False)), ("cut short", PairedKeyboard(fail_at=1)),
                         ("no such feature", keyboard_device())):
            with self.subTest(name):
                rig = self.make(kb=kb)
                self.assertIsNone(told(rig)[-1]["channel"])
                self.stays("pairing", host=2 if name == "not paired" else 1)
                rig.stop()

    def test_nothing_of_the_keys_is_logged_kept_or_shown(self):
        rig = self.make()
        self.ioctls.answers = {**ZERO, 1: bytes([1, 0x02, 0, 0x5A]) + bytes(5)}
        with self.assertLogs(level="DEBUG") as logs:
            logging.getLogger("mxoptions").debug("start")
            self.follow()
        text = "\n".join(logs.output)
        self.assertIn("stays (held)", text)
        self.assertNotIn("5a", text.lower())
        self.assertIsNone(rig.kb.transport.on_report)
        for buf in self.ioctls.buffers:
            self.assertEqual(bytes(buf), bytes(len(buf)))
        shown = json.dumps(rig.state())
        self.assertNotIn("held", shown)


@unittest.skipIf(sys.platform == "win32", "the keyboard is asked which keys are down on Linux only")
class SnapSwitch(unittest.TestCase):
    """_snap_switch on its own: every read of the keys and the request that sends the keyboard, all within
    KEYS_FRESH of the start of the first read, counted once for all of them."""

    def setUp(self):
        self.ioctls = bluetooth(self, dict(ZERO))
        self.kb = PairedKeyboard()
        self.node = KeyNode(FakePort({0xFF: self.kb}, receiver=False))
        self.transport = tests_support.open_fake(self.node, receiver=False)
        self.addCleanup(self.transport.close)
        self.cancel = threading.Event()

    def snap(self):
        dev = device.Mouse(None, 0xFF)
        dev.features = {feature: index for index, feature in enumerate(self.kb.features)}
        dev.transport = hidpp.ProbeTransport(self.transport, 1.0, self.cancel, time.monotonic() + keyboard.REQUEST_BUDGET)
        return keyboard._snap_switch(dev, 1, time.monotonic() + keyboard.FOLLOW_BUDGET)

    def test_all_up_sends_it_once(self):
        self.assertEqual(self.snap(), "sent")
        self.assertEqual(self.kb.switched, [1])
        self.assertEqual(self.ioctls.ids(), [1, 3, 4])

    def test_the_time_counts_from_the_first_read_not_from_each(self):
        cases = {0.09: ("sent", [1, 3, 4]), 0.12: ("late", [1, 3, 4]), 0.16: ("unknown", [1, 3])}
        for each, (expected, asked) in cases.items():
            with self.subTest(each=each):
                self.kb.switched.clear()
                self.ioctls.calls.clear()
                with Clock(1000.0) as clock:
                    self.ioctls.wait = lambda _rid, c=clock, step=each: setattr(c, "now", c.now + step)
                    self.assertEqual(self.snap(), expected)  # each read alone is well within KEYS_FRESH
                self.assertEqual(self.ioctls.ids(), asked)
                self.assertEqual(self.kb.switched, [1] if expected == "sent" else [])

    def test_waiting_for_its_turn_counts_too(self):
        """Read in time, but the mouse's own requests keep the request back past KEYS_FRESH: not sent."""
        for hold, expected in ((0.6, "late"), (0.05, "sent")):
            with self.subTest(hold=hold):
                self.kb.switched.clear()
                self.kb.delays[device.ROOT] = hold
                holder = []

                def wait(rid_):
                    time.sleep(0.1)
                    if rid_ == 4:  # the mouse's turn comes while the last report is read
                        thread = threading.Thread(target=lambda: self.transport.ping(0xFF), daemon=True)
                        thread.start()
                        holder.append(thread)
                        time.sleep(0.02)
                self.ioctls.wait = wait
                with mock.patch.object(keyboard, "KEYS_FRESH", 0.6):
                    self.assertEqual(self.snap(), expected)
                holder[0].join(2.0)
                self.assertEqual(self.kb.switched, [1] if expected == "sent" else [])

    def test_called_off_while_reading(self):
        self.ioctls.wait = lambda rid_: self.cancel.set() if rid_ == 4 else None
        self.assertEqual(self.snap(), "cancelled")
        self.assertEqual(self.kb.switched, [])
        self.cancel.clear()
        self.ioctls.calls.clear()
        self.ioctls.wait = lambda rid_: self.cancel.set() if rid_ == 1 else None
        self.assertEqual(self.snap(), "unknown")  # before the next report: not asked
        self.assertEqual((self.ioctls.ids(), self.kb.switched), ([1], []))

    def test_a_keyboard_gone_while_reading_is_said_to_be(self):
        self.ioctls.wait = lambda rid_: self.transport.close() if rid_ == 4 else None
        with self.assertRaises(hidpp.HidppClosed):
            self.snap()
        self.assertEqual(self.kb.switched, [])


class DeviceHosts(unittest.TestCase):
    """device.Mouse.get_hosts: what is shown is what it was, and pairing_read says whether the device told it all."""

    def hosts(self, dev_fake):
        port = FakePort({0xFF: dev_fake}, receiver=False)
        transport = tests_support.open_fake(port, receiver=False)
        self.addCleanup(transport.close)
        dev = device.Mouse(transport, 0xFF)
        dev.features = {feature: index for index, feature in enumerate(dev_fake.features)}
        self.assertIs(dev.pairing_read, False)
        return dev, dev.get_hosts(), port

    def test_what_it_says_and_whether_it_said_it_all(self):
        cases = {
            "all told": (PairedKeyboard(), True, [True, True, False], ["desk", "", ""]),
            "no such feature": (keyboard_device(), False, [True, True, True], ["", "", ""]),
            "says it cannot": (PairedKeyboard(capable=False), False, [True, True, True], ["", "", ""]),
            "stops half way": (PairedKeyboard(fail_at=1), False, [True, True, True], ["desk", "", ""]),
        }
        for name, (fake, read, paired, names) in cases.items():
            with self.subTest(name):
                dev, (current, hosts), _port = self.hosts(fake)
                self.assertIs(dev.pairing_read, read)
                self.assertEqual((current, [h["paired"] for h in hosts], [h["name"] for h in hosts]), (0, paired, names))

    def test_it_asks_exactly_what_it_asked_before(self):
        fake = PairedKeyboard()
        dev, _hosts, port = self.hosts(fake)
        info, change = fake.index(device.HOSTS_INFO), fake.index(device.CHANGE_HOST)
        self.assertEqual([(p[2], p[3] >> 4, p[4:6]) for p in port.written],
                         [(change, 0, b"\0\0"), (info, 0, b"\0\0"), (info, 1, b"\0\0"), (info, 3, b"\0\0"),
                          (info, 1, b"\x01\0"), (info, 1, b"\x02\0")])
        fake.fail_at = 0
        dev.get_hosts()  # read again, and not all of it this time
        self.assertIs(dev.pairing_read, False)


# -- what a key's macros are stopped by (KeyboardSide._remap) --------------------------------------------------------

class Gate:
    """Plays an Executor's macros without typing: each key step is recorded, and the first ``held`` step waits until
    the gate opens. drained() opens it and returns once every macro queued before has played or been dropped."""

    def __init__(self, test, executor, held="KEY_A"):
        self.executor, self.held = executor, held
        self.ran, self.entered, self.open, self.fenced = [], threading.Event(), threading.Event(), threading.Event()
        executor.macros._step = self.step
        test.addCleanup(self.close)

    def step(self, step, wake=None):
        name = step["keys"][0]
        if name == "KEY_F24":
            self.fenced.set()
            return
        self.ran.append(name)
        if name == self.held and not self.open.is_set():
            self.entered.set()
            assert self.open.wait(5), "the gate never opened"

    def playing(self):
        assert self.entered.wait(5), "the first macro never started"

    def drained(self):
        self.open.set()
        jobs = self.executor.macros._queue
        with jobs.not_full:  # the fence fits once the runner took a job
            assert jobs.not_full.wait_for(lambda: jobs._qsize() < 4, 5)
        self.fenced.clear()
        self.executor.trigger(macro("KEY_F24"))
        assert self.fenced.wait(5), "the fence never played"
        return self.ran

    def close(self):
        self.open.set()
        self.executor.close()
        thread = self.executor.macros._thread
        if thread is not None:
            thread.join(5)
            assert not thread.is_alive(), "a macro thread was left running"


class MacroSources(unittest.TestCase):
    """A key whose action changes stops every macro its presses started, playing, queued or turned away by a full
    queue, however often equal settings were saved meanwhile; other keys and the mouse play on."""

    def macro_rig(self, keys, profiles=()):
        self.kb = keyboard_device()
        rig = Rig(self, {1: self.kb, 2: mouse_device()}, keys=keys)
        rig.engine.cfg["keyboard"]["profiles"] = copy.deepcopy(list(profiles))
        rig.start()
        self.gate = Gate(self, rig.kb.executor)
        return rig

    def tap(self, rig, cid=0x0F01):
        rig.keys_down(1, self.kb, cid)
        rig.keys_down(1, self.kb)

    def apply(self, rig, cfg):
        rig.engine.set_config(cfg)
        rig.engine._handle(rig.engine._queue.get(timeout=2.0))
        rig.settle()

    def save(self, rig):
        self.apply(rig, copy.deepcopy(rig.engine.cfg))  # nothing changed: every object new

    def change(self, rig, cid="0x0f01", action=COPY):
        cfg = copy.deepcopy(rig.engine.cfg)
        cfg["keyboard"]["keys"][cid] = action
        self.apply(rig, cfg)

    def node_keys(self, rig, *cids):
        """The keyboard on a node of its own (FollowsMouse.make) reports its keys. Rig.keys_down cannot: a KeyNode
        has no notify of its own."""
        rig.ports[KB_PATH].fake.notify(bytes([0x11, 0xFF, self.kb.index(device.REPROG_CONTROLS_V4), 0x00])
                                       + struct.pack("!HHHH", *(list(cids) + [0, 0, 0, 0])[:4]).ljust(16, b"\0"))
        rig.engine._handle(rig.engine._queue.get(timeout=2.0))

    def test_a_changed_key_stops_its_macro_after_equal_saves_and_a_press_the_full_queue_turned_away(self):
        rig = self.macro_rig({"0x0f01": macro("KEY_A", "KEY_B")})
        self.tap(rig)
        self.gate.playing()
        for _ in range(5):
            self.save(rig)
            self.tap(rig)
        self.assertEqual(rig.kb.executor.macros._queue.qsize(), 4)  # the fifth copy's press was turned away
        self.change(rig)
        self.assertEqual(self.gate.drained(), ["KEY_A"])

    def test_a_key_held_when_it_changes_stops_every_macro_it_started(self):
        rig = self.macro_rig({"0x0f01": macro("KEY_A", "KEY_B", repeat=True)})
        self.tap(rig)
        self.gate.playing()
        for _ in range(4):
            self.save(rig)
            self.tap(rig)
        self.save(rig)
        rig.keys_down(1, self.kb, 0x0F01)  # turned away by the full queue, and held
        self.assertEqual(rig.kb.executor.macros._queue.qsize(), 4)
        self.change(rig)
        rig.keys_down(1, self.kb)
        self.assertEqual(self.gate.drained(), ["KEY_A"])
        self.assertEqual(rig.input._holds.held(), set())

    def test_default_in_an_app_stops_the_macros_started_across_apps_and_saves(self):
        rig = self.macro_rig({"0x0f01": macro("KEY_A", "KEY_B")},
                             [profile("editor", {"0x0f01": macro("KEY_A", "KEY_B")}), profile("game", {"0x0f01": NATIVE})])
        front(rig, "editor")
        self.tap(rig)
        self.gate.playing()
        self.save(rig)
        self.tap(rig)
        front(rig, "browser")
        self.tap(rig)
        self.save(rig)
        self.tap(rig)
        front(rig, "editor")
        self.tap(rig)
        self.save(rig)
        self.tap(rig)  # turned away: the queue is full
        self.assertEqual(rig.kb.executor.macros._queue.qsize(), 4)
        front(rig, "game")
        self.assertEqual(self.gate.drained(), ["KEY_A"])

    def test_every_queued_press_of_a_changed_key_stops(self):
        rig = self.macro_rig({"0x0f01": macro("KEY_A", "KEY_B")})
        for _ in range(3):
            self.tap(rig)
        self.gate.playing()
        self.change(rig)
        self.assertEqual(self.gate.drained(), ["KEY_A"])

    def test_a_key_left_alone_plays_every_press_when_another_key_changes(self):
        rig = self.macro_rig({"0x0f01": macro("KEY_A", "KEY_B"), "0x0f02": CTRL})
        self.tap(rig)
        self.gate.playing()
        for _ in range(5):
            self.save(rig)
            self.tap(rig)
        self.change(rig, "0x0f02", PASTE)
        self.assertEqual(self.gate.drained(), ["KEY_A", "KEY_B"] * 5)

    def test_two_keys_given_one_action_object_stop_apart(self):
        rig = self.macro_rig({"0x0f01": CTRL, "0x0f02": CTRL})
        cfg = copy.deepcopy(rig.engine.cfg)
        cfg["keyboard"]["keys"]["0x0f01"] = cfg["keyboard"]["keys"]["0x0f02"] = macro("KEY_A", "KEY_B")
        self.apply(rig, cfg)  # one object for both, through the settings' deep copy too
        self.tap(rig, 0x0F02)
        self.gate.playing()
        self.change(rig, "0x0f01", COPY)
        self.assertEqual(self.gate.drained(), ["KEY_A", "KEY_B"])

    def test_the_mouse_s_macro_plays_on_when_a_key_changes(self):
        rig = self.macro_rig({"0x0f01": macro("KEY_A", "KEY_B")})
        rig.engine.executor = actions.Executor(rig.input, rig.engine._device_action)
        mouse = Gate(self, rig.engine.executor, held="KEY_M")
        rig.engine.executor.press(macro("KEY_M", "KEY_N"))
        mouse.playing()
        self.tap(rig)
        self.gate.playing()
        self.save(rig)
        self.tap(rig)
        self.change(rig)
        self.assertEqual(self.gate.drained(), ["KEY_A"])
        self.assertEqual(mouse.drained(), ["KEY_M", "KEY_N"])

    def test_equal_saves_keep_no_more_objects_and_a_returning_keyboard_still_stops_its_macro(self):
        import gc
        rig = self.macro_rig({"0x0f01": macro("KEY_F13")})
        self.gate.open.set()

        def count():
            gc.collect()
            return sum(1 for one in gc.get_objects() if type(one) is dict and one.get("type") == "macro"
                       and one.get("steps", [{}])[0].get("keys") == ["KEY_F13"])
        counts = []
        for n in range(31):
            self.change(rig, action=macro("KEY_F13") if n % 2 else macro("KEY_F13", repeat=True))
            self.save(rig)
            self.tap(rig)
            self.gate.drained()
            counts.append(count())
        self.assertEqual(counts[-1], counts[1])
        rig.link(1, False)  # the keyboard is gone: its macros reset, their sources dropped
        self.assertIsNone(rig.kb.device)
        rig.link(1, True)
        rig.settle()
        self.assertIsNotNone(rig.kb.device)
        self.gate.open.clear()
        self.gate.entered.clear()
        self.gate.ran.clear()
        self.change(rig, action=macro("KEY_A", "KEY_B"))
        self.tap(rig)
        self.gate.playing()
        self.save(rig)
        self.tap(rig)
        self.change(rig)
        self.assertEqual(self.gate.drained(), ["KEY_A"])

    def test_shutting_down_stops_the_macros_and_their_thread(self):
        rig = self.macro_rig({"0x0f01": macro("KEY_A", "KEY_B")})
        self.tap(rig)
        self.gate.playing()
        self.save(rig)
        self.tap(rig)
        rig.kb.shutdown()  # reset, then closed: the runner ends and nothing plays after
        self.gate.open.set()
        thread = rig.kb.executor.macros._thread
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.gate.ran, ["KEY_A"])

    @unittest.skipIf(sys.platform == "win32", "the keyboard is asked which keys are down on Linux only")
    def test_following_switched_off_while_keys_are_given_back_stops_only_the_changed_key_s_macro(self):
        self.ioctls = bluetooth(self, dict(ZERO))
        rig = FollowsMouse.make(self)  # the keyboard on a Bluetooth node of its own, following the mouse
        cfg = copy.deepcopy(rig.engine.cfg)
        cfg["keyboard"]["keys"].update({"0x0f01": macro("KEY_A", "KEY_B"), "0x0f02": macro("KEY_C", "KEY_D")})
        self.apply(rig, cfg)
        keys = Gate(self, rig.kb.executor)
        rig.engine.executor = actions.Executor(rig.input, rig.engine._device_action)
        mouse = Gate(self, rig.engine.executor, held="KEY_M")
        rig.engine.executor.press(macro("KEY_M", "KEY_N"))
        mouse.playing()
        self.node_keys(rig, 0x0F01)
        self.node_keys(rig)
        keys.playing()
        self.node_keys(rig, 0x0F02)
        self.node_keys(rig)  # waits behind the first
        event = threading.Event()
        rig.engine.keyboard_follow(1, rig.kb.link, event)
        rig.engine._handle(rig.engine._queue.get(timeout=2.0))  # Flow's request: the keys are given back first
        self.assertTrue(rig.kb._following)
        self.assertEqual(rig.kb._asked, (keyboard._set_reporting, 0x0F01, False))  # its answer not taken yet
        new = copy.deepcopy(rig.engine.cfg)
        new["flow"]["keyboard_follows_mouse"] = False
        new["keyboard"]["keys"]["0x0f01"] = COPY
        rig.engine._apply_config(copy.deepcopy(new))  # the save is taken before that answer
        self.assertTrue(event.is_set())
        self.assertFalse(rig.kb._following)
        rig.settle()
        self.assertEqual(keys.drained(), ["KEY_A", "KEY_C", "KEY_D"])
        self.assertEqual(mouse.drained(), ["KEY_M", "KEY_N"])
        self.assertEqual((self.kb.switched, self.ioctls.calls), ([], []))  # never sent anywhere
        self.assertTrue(rig.key(0x0F01)["active"])


def stoppable(*names, repeat=False):
    return dict(macro(*names, repeat=repeat), stop_on_press=True)


class PressAgain(unittest.TestCase):
    """A macro with "stop_on_press" stops when its key is pressed again while it plays or waits, and nothing new
    starts; the next press starts it again. Without the field a press queues it once more, as before."""

    macro_rig, tap, apply, save, change = (MacroSources.macro_rig, MacroSources.tap, MacroSources.apply,
                                           MacroSources.save, MacroSources.change)

    def test_pressed_again_while_it_plays_it_stops_before_its_next_step(self):
        rig = self.macro_rig({"0x0f01": stoppable("KEY_A", "KEY_B")})
        self.tap(rig)
        self.gate.playing()
        self.tap(rig)
        self.assertEqual(rig.kb.executor.macros._queue.qsize(), 0)
        self.assertEqual(self.gate.drained(), ["KEY_A"])

    def test_pressed_again_while_queued_it_never_plays(self):
        rig = self.macro_rig({"0x0f01": stoppable("KEY_C"), "0x0f02": macro("KEY_A", "KEY_B")})
        self.tap(rig, 0x0F02)
        self.gate.playing()
        self.tap(rig)
        self.tap(rig)
        self.assertEqual(self.gate.drained(), ["KEY_A", "KEY_B"])

    def test_a_third_press_starts_it_anew(self):
        rig = self.macro_rig({"0x0f01": stoppable("KEY_A", "KEY_B")})
        self.tap(rig)
        self.gate.playing()
        self.tap(rig)
        self.tap(rig)
        self.assertEqual(self.gate.drained(), ["KEY_A", "KEY_A", "KEY_B"])

    def test_a_press_after_it_played_to_its_end_starts_it_again(self):
        rig = self.macro_rig({"0x0f01": stoppable("KEY_C")})
        self.tap(rig)
        self.assertEqual(self.gate.drained(), ["KEY_C"])
        self.tap(rig)
        self.assertEqual(self.gate.drained(), ["KEY_C", "KEY_C"])

    def test_without_the_field_or_with_false_presses_queue_as_before(self):
        for value in (None, False):
            with self.subTest(value=value):
                action = macro("KEY_A", "KEY_B")
                if value is not None:
                    action["stop_on_press"] = value
                rig = self.macro_rig({"0x0f01": action})
                self.tap(rig)
                self.gate.playing()
                self.tap(rig)
                self.assertEqual(self.gate.drained(), ["KEY_A", "KEY_B"] * 2)

    def test_another_key_with_an_equal_macro_neither_stops_it_nor_is_stopped(self):
        rig = self.macro_rig({"0x0f01": stoppable("KEY_A", "KEY_B"), "0x0f02": stoppable("KEY_A", "KEY_B")})
        self.tap(rig)
        self.gate.playing()
        self.tap(rig, 0x0F02)  # queued behind it
        self.tap(rig)  # stops the first key's only
        self.assertEqual(self.gate.drained(), ["KEY_A", "KEY_A", "KEY_B"])

    def test_a_press_the_full_queue_turned_away_does_not_count_as_playing(self):
        rig = self.macro_rig({"0x0f01": stoppable("KEY_C"), "0x0f02": macro("KEY_A", "KEY_B")})
        self.tap(rig, 0x0F02)
        self.gate.playing()
        for _ in range(4):
            self.tap(rig, 0x0F02)
        self.assertEqual(rig.kb.executor.macros._queue.qsize(), 4)
        self.tap(rig)  # turned away
        self.assertEqual(self.gate.drained(), ["KEY_A", "KEY_B"] * 5)
        self.tap(rig)
        self.assertEqual(self.gate.drained(), ["KEY_A", "KEY_B"] * 5 + ["KEY_C"])

    def test_equal_saves_between_presses_keep_it_stoppable(self):
        rig = self.macro_rig({"0x0f01": stoppable("KEY_A", "KEY_B")})
        self.tap(rig)
        self.gate.playing()
        self.save(rig)
        self.save(rig)
        self.tap(rig)
        self.assertEqual(self.gate.drained(), ["KEY_A"])

    def test_switching_the_field_off_stops_it_and_later_presses_queue(self):
        rig = self.macro_rig({"0x0f01": stoppable("KEY_A", "KEY_B")})
        self.tap(rig)
        self.gate.playing()
        self.change(rig, action=macro("KEY_A", "KEY_B"))
        self.assertEqual(self.gate.drained(), ["KEY_A"])
        self.gate.open.clear()
        self.gate.entered.clear()
        self.gate.ran.clear()
        self.tap(rig)
        self.gate.playing()
        self.tap(rig)
        self.assertEqual(self.gate.drained(), ["KEY_A", "KEY_B"] * 2)

    def test_an_app_with_an_equal_macro_keeps_it_stoppable(self):
        rig = self.macro_rig({"0x0f01": stoppable("KEY_A", "KEY_B")},
                             [profile("editor", {"0x0f01": stoppable("KEY_A", "KEY_B")})])
        front(rig, "editor")
        self.tap(rig)
        self.gate.playing()
        front(rig, "browser")
        self.tap(rig)
        self.assertEqual(self.gate.drained(), ["KEY_A"])

    def test_after_the_keyboard_went_away_and_back_a_press_starts_it(self):
        rig = self.macro_rig({"0x0f01": stoppable("KEY_A", "KEY_B")})
        self.tap(rig)
        self.gate.playing()
        rig.link(1, False)  # its macros reset, their sources dropped
        self.assertIsNone(rig.kb.device)
        rig.link(1, True)
        rig.settle()
        self.assertIsNotNone(rig.kb.device)
        self.tap(rig)
        self.assertEqual(self.gate.drained(), ["KEY_A", "KEY_A", "KEY_B"])



class KeyPositions(unittest.TestCase):
    """The F-key each control is on, as getCidInfo says it (byte 5): kept from 1 to 16, else 0 (not said)."""

    def test_the_f_key_of_each_key_is_in_the_state(self):
        said = {0x0F01: 0, 0x0F02: 1, 0x0F03: 12, 0x0F04: 16, 0x0F05: 17, 0x0F06: 255}
        kb = FakeDevice(controls=[(cid, cid, 0x30, pos) for cid, pos in said.items()], unit=UNIT_A, model=MODEL)
        rig = Rig(self, {1: kb, 2: mouse_device()}).start()
        self.assertEqual({cid: rig.kb.device.controls[cid].pos for cid in said},
                         {0x0F01: 0, 0x0F02: 1, 0x0F03: 12, 0x0F04: 16, 0x0F05: 0, 0x0F06: 0})
        self.assertEqual({cid: rig.key(cid)["pos"] for cid in said},
                         {0x0F01: 0, 0x0F02: 1, 0x0F03: 12, 0x0F04: 16, 0x0F05: 0, 0x0F06: 0})

    def test_a_control_made_without_one_has_none(self):
        self.assertEqual(device.Control(0x0F01, 0x0F01, 0x30).pos, 0)


if __name__ == "__main__":
    unittest.main()
