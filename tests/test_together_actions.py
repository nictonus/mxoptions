"""The mouse and the keyboard sent to a channel together by one action, and the action that has them follow each
other over Flow or not. The engine runs on the test's thread; nodes and devices are synthetic (tests/test_keyboard.py)."""

import copy
import json
import struct
import sys
import threading
import time
import types
import unittest
from unittest import mock

from tests.support import FakePort
from tests import test_keyboard
from tests.test_keyboard import CTRL, KB_PATH, PATH, PairedKeyboard, Rig, diverts, mouse_device
from tests.test_transport import AGGREGATE, ZERO, KeyNode, bluetooth
from mxoptions import actions, api, app, config, device, flow_service, hidpp

BOTH = {"type": "host", "host": 1, "both": True}  # Easy-Switch 2
TOGETHER = {"type": "together"}
TWO = {"type": "host", "host": 0, "keyboard": 2}  # Easy-Switch 2 devices: the mouse to 1, the keyboard to 3


class Shape(unittest.TestCase):
    def test_the_actions_are_kept_where_a_key_a_button_or_a_gesture_may_have_them(self):
        for action in (TOGETHER, BOTH, TWO, {"type": "host", "host": 1}):
            with self.subTest(action):
                self.assertTrue(config.keyboard_action(action))
                self.assertTrue(config.profile_button_action(action))
                self.assertTrue(config.gesture_slot_action(action))
                self.assertEqual(api.parse_action(json.dumps(action)), action)
        self.assertIn("together", api.MouseApi.ListActions(None))
        self.assertEqual(api.parse_action("together"), TOGETHER)
        self.assertEqual(actions.host_target(TWO), (0, 2))
        self.assertEqual(actions.host_target(BOTH), (1, 1))  # as PR #8 saved them
        self.assertEqual(actions.host_target({"type": "host", "host": 1}), (1, None))

    def test_the_same_number_presets_are_gone_and_their_saved_actions_are_still_named(self):
        for preset in ("both_1", "both_2", "both_3"):
            self.assertNotIn(preset, actions.PRESET_BY_ID)
            self.assertNotIn(preset, api.MouseApi.ListActions(None))
        self.assertEqual(actions.describe(BOTH), "Mouse and keyboard to Easy-Switch 2")
        self.assertEqual(actions.describe(TWO), "Mouse to Easy-Switch 1, keyboard to 3")
        self.assertEqual(actions.icon_name(TWO), "computer")

    def test_anything_else_is_not_and_never_runs(self):
        bad = ({"type": "host", "host": 1, "both": "yes"}, {"type": "host", "host": 1, "both": False},
               {"type": "host", "host": 1, "both": True, "more": 1}, {"type": "host", "host": 16, "both": True},
               {"type": "host", "host": 1, "keyboard": True}, {"type": "host", "host": 1, "keyboard": "2"},
               {"type": "host", "host": 1, "keyboard": 16}, {"type": "host", "host": 1, "keyboard": 2, "both": True},
               {"type": "host", "host": True}, {"type": "together", "on": True})
        for action in bad:
            with self.subTest(action):
                self.assertFalse(config.keyboard_action(action))
                if action["type"] == "host":
                    self.assertIsNone(actions.host_target(action))
                    self.assertIsNone(api.parse_action(json.dumps(action)))

    def test_the_tray_says_which_way_it_went(self):
        self.assertEqual(app.together_message(True, True), "Mouse and keyboard move together")
        self.assertEqual(app.together_message(True, False), "Mouse and keyboard move together once Flow is on")
        self.assertEqual(app.together_message(False, True), "Mouse and keyboard move on their own")


class Toggle(unittest.TestCase):
    def test_both_on_unless_both_are_on(self):
        for before, after in (((False, False), True), ((True, False), True), ((False, True), True), ((True, True), False)):
            with self.subTest(before):
                cfg = {"flow": {"mouse_follows_keyboard": before[0], "keyboard_follows_mouse": before[1]}}
                store = types.SimpleNamespace(cfg=cfg, set=lambda section, key, value: cfg[section].update({key: value}))
                self.assertIs(flow_service.FlowService.toggle_together(types.SimpleNamespace(store=store)), after)
                self.assertEqual(cfg["flow"], {"mouse_follows_keyboard": after, "keyboard_follows_mouse": after})


@unittest.skipIf(sys.platform == "win32", "the keyboard is asked which keys are down on Linux only")
class Together(unittest.TestCase):
    """The keyboard on a Bluetooth node of its own, the mouse on a receiver (or on Bluetooth); Flow's follow switch
    off throughout: the action needs none of it."""

    pump = test_keyboard.FollowsMouse.pump  # not imported by name: its tests would run here a second time
    blocked = test_keyboard.FollowsMouse.blocked

    def setUp(self):
        self.ioctls = bluetooth(self, dict(ZERO))

    def make(self, keys=None, direct=False, kb=None):
        kb = self.kb = kb or PairedKeyboard()
        mouse = self.mouse = mouse_device()
        mouse.features.append(device.CHANGE_HOST)  # three channels, on the first
        rig = Rig(self, {0xFF: mouse} if direct else {2: mouse}, keys=keys or {"0x0f01": CTRL}, receiver=not direct)
        rig.engine.cfg["flow"]["keyboard_follows_mouse"] = False
        rig.start()
        rig.ports[KB_PATH] = KeyNode(FakePort({0xFF: kb}, receiver=False), KB_PATH, AGGREGATE)
        rig.nodes.append((KB_PATH, False))
        rig.kb._next_nodes = 0.0
        rig.engine._tick()
        rig.settle()
        self.assertEqual(rig.kb.transport.path, KB_PATH)
        self.rig = rig
        return rig

    def keys(self, *cids):
        """The keyboard reports which keys are down (Rig.keys_down cannot: a KeyNode has no notify of its own)."""
        self.rig.ports[KB_PATH].fake.notify(bytes([0x11, 0xFF, self.kb.index(device.REPROG_CONTROLS_V4), 0x00])
                                            + struct.pack("!HHHH", *(list(cids) + [0, 0, 0, 0])[:4]).ljust(16, b"\0"))
        self.rig.engine._handle(self.rig.engine._queue.get(timeout=2.0))
        self.rig.settle()

    def config(self, **keys):
        new = copy.deepcopy(self.rig.engine.cfg)
        new["keyboard"]["keys"].update(keys)
        new["flow"]["keyboard_follows_mouse"] = False
        self.rig.engine._apply_config(new)

    # -- from the mouse, the ring, a gesture -------------------------------------------------------------------------

    def test_both_go_with_flow_s_follow_switch_off(self):
        rig = self.make()
        with self.assertLogs("mxoptions.keyboard", "INFO"):
            rig.engine._device_action(BOTH, "press")
            rig.settle()
        self.assertEqual((self.mouse.switched, self.kb.switched), ([1], [1]))
        self.assertTrue(rig.kb._sent_at is not None)  # sent from here: Flow's mouse following it leaves it be
        rig.engine._drop_transport(rig.transport(KB_PATH))
        self.assertTrue(rig.kb.last_drop[1])

    def test_on_release_nothing_and_plain_easy_switch_moves_the_mouse_only(self):
        rig = self.make()
        rig.engine._device_action(BOTH, "release")
        rig.engine._device_action({"type": "host", "host": 1}, "press")
        rig.settle()
        self.assertEqual((self.mouse.switched, self.kb.switched), ([1], []))

    def test_neither_goes_where_the_keyboard_cannot(self):
        rig = self.make()
        cases = {
            "held": lambda: rig.kb._held.update({0x0F01: CTRL}),
            "busy": lambda: setattr(rig.kb, "_leaving", time.monotonic() + 30),
            "pairing": lambda: setattr(rig.kb, "_hosts", (1, rig.kb._hosts[1])),  # it is on channel 2 already
            "unsupported": lambda: setattr(rig.kb.transport, "can_tell_keys", lambda: False),
        }
        hosts = copy.deepcopy(rig.kb._hosts)
        for why, prepare in cases.items():
            with self.subTest(why):
                prepare()
                with self.assertLogs("mxoptions.engine", "INFO") as logs:
                    rig.engine._device_action(BOTH, "press")
                self.assertIn(f"the mouse and keyboard stay ({why})", logs.output[-1])
                rig.settle()
                self.assertEqual((self.mouse.switched, self.kb.switched), ([], []))
                rig.kb._held.clear()
                rig.kb._leaving, rig.kb._hosts = 0.0, copy.deepcopy(hosts)
                vars(rig.kb.transport).pop("can_tell_keys", None)

    def test_a_keyboard_on_the_receiver_stays_and_so_does_the_mouse(self):
        self.kb = kb = PairedKeyboard()
        mouse = self.mouse = mouse_device()
        mouse.features.append(device.CHANGE_HOST)
        rig = Rig(self, {1: kb, 2: mouse}, keys={"0x0f01": CTRL})
        rig.start()
        self.assertTrue(rig.kb.transport.is_receiver)
        with self.assertLogs("mxoptions.engine", "INFO") as logs:
            rig.engine._device_action(BOTH, "press")
        self.assertIn("stay (unsupported)", logs.output[-1])
        rig.settle()
        self.assertEqual((mouse.switched, kb.switched), ([], []))

    def test_the_mouse_failing_to_go_keeps_the_keyboard(self):
        rig = self.make()
        with mock.patch.object(rig.engine.mouse, "change_host", side_effect=hidpp.HidppError("synthetic")):
            rig.engine._device_action(BOTH, "press")
        rig.settle()
        self.assertEqual(self.kb.switched, [])

    def test_without_the_mouse_here_the_keyboard_goes_at_once(self):
        for name in ("gone", "asleep"):
            with self.subTest(name):
                rig = self.make(direct=True)
                if name == "gone":
                    rig.engine._drop_transport(rig.transport(PATH))
                else:
                    rig.engine.online = False  # a mouse on Bluetooth that does not answer: nothing to wait for
                rig.engine._device_action(BOTH, "trigger")
                self.assertIsNone(rig.kb._follow_wait)
                rig.settle()
                self.assertEqual((self.mouse.switched, self.kb.switched), ([], [1]))
                rig.stop()

    def test_the_mouse_on_bluetooth_is_waited_for_and_flow_going_off_does_not_stop_it(self):
        rig = self.make(direct=True)
        rig.engine._device_action(BOTH, "press")
        self.assertEqual(self.mouse.switched, [1])
        self.assertIsNotNone(rig.kb._follow_wait)
        self.config()  # Flow's follow switch off: calls off Flow's follows only
        self.assertIsNotNone(rig.kb._follow_wait)
        rig.engine._drop_transport(rig.transport(PATH))
        rig.engine._tick()
        rig.settle()
        self.assertEqual(self.kb.switched, [1])

    def test_flow_going_off_does_not_stop_one_wished_or_under_way(self):
        for state in ("wished", "reading"):
            with self.subTest(state):
                rig = self.make()
                if state == "wished":
                    self.kb.delays[device.REPROG_CONTROLS_V4] = 0.3
                    rig.kb._reporting.pop(0x0F01)
                    rig.kb.configure()
                    rig.kb.go_along(1, mouse_sent=False)
                    self.assertEqual(rig.kb._wish[0], "follow")
                    rig.kb.config_changed(rig.engine.cfg, rig.engine.cfg)
                    self.assertEqual(rig.kb._wish[0], "follow")
                else:
                    release, reading = self.blocked()
                    rig.kb.go_along(1, mouse_sent=False)
                    self.pump(reading.is_set)
                    rig.kb.config_changed(rig.engine.cfg, rig.engine.cfg)
                    self.assertTrue(rig.kb._following)
                    release.set()
                rig.settle()
                self.ioctls.wait = None
                self.assertEqual(self.kb.switched, [1])
                rig.stop()

    def test_flow_going_off_still_stops_flow_s_own(self):
        rig = self.make(direct=True)
        rig.engine.cfg["flow"]["keyboard_follows_mouse"] = True
        rig.engine.keyboard_follow(1, rig.kb.link, threading.Event())
        rig.settle()
        self.assertIsNotNone(rig.kb._follow_wait)
        with self.assertLogs("mxoptions.keyboard", "INFO") as logs:
            self.config()
        self.assertIn("stays (cancelled)", logs.output[-1])
        self.assertIsNone(rig.kb._follow_wait)

    def test_a_native_key_down_at_the_last_look_keeps_the_keyboard(self):
        rig = self.make()
        self.ioctls.answers = {**ZERO, 1: bytes([1, 0, 0, 0x0D]) + bytes(5)}
        with self.assertLogs("mxoptions.keyboard", "INFO") as logs:
            rig.engine._device_action(BOTH, "press")
            rig.settle()
        self.assertIn("stays (held)", logs.output[-1])
        self.assertEqual((self.mouse.switched, self.kb.switched), ([1], []))
        self.assertTrue(diverts(self.kb)[-1][1])  # its keys taken again

    # -- each to its own channel (Easy-Switch 2 devices) -----------------------------------------------------------

    def test_each_goes_to_its_own_channel(self):
        rig = self.make()
        rig.engine._device_action({"type": "host", "host": 2, "keyboard": 1}, "press")
        rig.settle()
        self.assertEqual((self.mouse.switched, self.kb.switched), ([2], [1]))

    def test_the_keyboard_s_own_channel_decides_whether_both_go(self):
        rig = self.make()  # the keyboard's third channel is not paired
        with self.assertLogs("mxoptions.engine", "INFO") as logs:
            rig.engine._device_action({"type": "host", "host": 1, "keyboard": 2}, "press")
        self.assertIn("the mouse and keyboard stay (pairing)", logs.output[-1])
        rig.settle()
        self.assertEqual((self.mouse.switched, self.kb.switched), ([], []))

    def test_a_mouse_on_its_channel_already_lets_the_keyboard_go_alone(self):
        rig = self.make()
        rig.engine._device_action({"type": "host", "host": 0, "keyboard": 1}, "press")
        self.assertIsNone(rig.kb._follow_wait)  # nothing sent away to wait for
        rig.settle()
        self.assertEqual((self.mouse.switched, self.kb.switched), ([], [1]))

    def test_from_a_key_each_to_its_own_on_release(self):
        rig = self.make(keys={"0x0f01": {"type": "host", "host": 2, "keyboard": 1}})
        self.keys(0x0F01)
        self.assertEqual((self.mouse.switched, self.kb.switched), ([], []))
        self.keys()
        self.assertEqual((self.mouse.switched, self.kb.switched), ([2], [1]))

    def test_one_not_well_formed_moves_nothing_from_anywhere(self):
        rig = self.make()
        for action in ({"type": "host", "host": 1, "keyboard": True}, {"type": "host", "host": 1, "keyboard": "1"},
                       {"type": "host", "host": 1, "keyboard": 16}, {"type": "host", "host": 1, "both": False},
                       {"type": "host", "host": 1, "keyboard": 1, "both": True}, {"type": "host", "host": 1, "x": 0}):
            with self.subTest(action):
                for phase in ("press", "trigger"):
                    rig.engine._device_action(action, phase)
                    rig.kb.device_action(action, phase)
                rig.kb._held[0x0F01] = action  # as if a key had it when it went down
                rig.kb._pressed = rig.kb._pressed | {0x0F01}
                self.keys()
                rig.settle()
                self.assertEqual((self.mouse.switched, self.kb.switched), ([], []))

    # -- from a key of the keyboard ----------------------------------------------------------------------------------

    def test_from_a_key_on_its_release(self):
        rig = self.make(keys={"0x0f01": BOTH})
        self.keys(0x0F01)
        self.assertEqual((self.mouse.switched, self.kb.switched), ([], []))
        self.keys()
        self.assertEqual((self.mouse.switched, self.kb.switched), ([1], [1]))

    def test_with_another_key_let_go_of_in_the_same_report_whichever_comes_first(self):
        for trigger, sibling in ((0x0F01, 0x0F04), (0x0F04, 0x0F01)):
            with self.subTest(trigger=trigger):
                rig = self.make(keys={f"0x{trigger:04x}": BOTH, f"0x{sibling:04x}": CTRL})
                self.keys(trigger, sibling)
                self.keys()
                self.assertEqual((self.mouse.switched, self.kb.switched), ([1], [1]))
                rig.stop()

    def test_another_key_still_down_keeps_both(self):
        rig = self.make(keys={"0x0f01": BOTH, "0x0f04": CTRL})
        self.keys(0x0F01, 0x0F04)
        with self.assertLogs("mxoptions.engine", "INFO") as logs:
            self.keys(0x0F04)
        self.assertIn("the mouse and keyboard stay (held)", "\n".join(logs.output))
        self.keys()
        self.assertEqual((self.mouse.switched, self.kb.switched), ([], []))

    def test_two_such_keys_let_go_of_together_send_once(self):
        rig = self.make(keys={"0x0f01": BOTH, "0x0f04": {"type": "host", "host": 1, "both": True}})
        self.keys(0x0F01, 0x0F04)
        self.keys()
        self.assertEqual((self.mouse.switched, self.kb.switched), ([1], [1]))

    def test_its_action_changed_while_it_is_down_sends_nothing(self):
        rig = self.make(keys={"0x0f01": BOTH})
        self.keys(0x0F01)
        self.config(**{"0x0f01": CTRL})
        rig.settle()
        self.keys()
        self.assertEqual((self.mouse.switched, self.kb.switched), ([], []))

    def test_the_together_action_from_a_key_the_mouse_and_with_no_mouse(self):
        rig = self.make(keys={"0x0f01": TOGETHER})

        def told():
            return [payload for kind, payload in rig.listener.events if kind == "flow" and payload[0] == "together"]
        self.keys(0x0F01)
        self.keys()
        self.assertEqual(told(), [("together", None)])  # once: on the press
        rig.engine._device_action(TOGETHER, "press")
        rig.engine._device_action(TOGETHER, "release")
        rig.engine._drop_transport(rig.transport(PATH))
        rig.engine._device_action(TOGETHER, "trigger")
        self.assertEqual(len(told()), 3)


if __name__ == "__main__":
    unittest.main()
