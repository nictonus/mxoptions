"""The mouse and the keyboard holding keys through the one virtual keyboard (vinput.Holds), on its own
and inside both backends with what they send to the system recorded instead of sent."""

import copy
import ctypes
import importlib
import struct
import sys
import threading
import time
import types
import unittest
from unittest import mock

from tests.support import FakeInput
from mxoptions import actions, vinput
from mxoptions import clipboard
from mxoptions import config, device, engine, hidpp
from tests import support, test_kbsettings, test_keyboard, test_mouse_profiles as profiles

CTRL, C = "KEY_LEFTCTRL", "KEY_C"


class Holds(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.holds = vinput.Holds(lambda name, down: self.sent.append((name, down)))

    def test_a_key_held_by_two_goes_up_with_the_last(self):
        self.holds.press([CTRL], "mouse")
        self.holds.press([CTRL], "keyboard")
        self.holds.release([CTRL], "mouse")
        self.assertEqual(self.sent, [(CTRL, True)])
        self.holds.release([CTRL], "keyboard")
        self.assertEqual(self.sent, [(CTRL, True), (CTRL, False)])

    def test_letting_go_of_what_one_never_held_changes_nothing(self):
        self.holds.press([CTRL], "keyboard")
        for _ in range(3):
            self.holds.release([CTRL], "mouse")
        self.assertEqual(self.sent, [(CTRL, True)])
        self.assertEqual(self.holds.held(), {CTRL})

    def test_one_owner_holding_twice_lets_go_twice(self):
        self.holds.press([CTRL], "mouse")
        self.holds.press([CTRL], "mouse")
        self.holds.release([CTRL], "mouse")
        self.assertEqual(self.sent, [(CTRL, True)])
        self.holds.release([CTRL], "mouse")
        self.assertEqual(self.sent[-1], (CTRL, False))

    def test_a_tap_leaves_held_keys_alone(self):
        self.holds.press([CTRL], "keyboard")
        self.holds.tap([CTRL, C], wait=0)
        self.assertEqual(self.sent, [(CTRL, True), (C, True), (C, False)])

    def test_resetting_one_owner_keeps_the_other_s_keys(self):
        self.holds.press([CTRL], "keyboard")
        self.holds.press([CTRL, "KEY_LEFTSHIFT"], "mouse")
        self.holds.release_all("mouse")
        self.assertEqual(self.sent, [(CTRL, True), ("KEY_LEFTSHIFT", True), ("KEY_LEFTSHIFT", False)])
        self.holds.release_all()
        self.assertEqual(self.sent[-1], (CTRL, False))
        self.assertEqual(self.holds.held(), set())


class Refusing:
    """An emitter that records every event and what the system would have down, and raises once at each event of
    ``trips`` ((name, down) pairs, or a dict of them to the exception class). ``applied``: whether a refused
    event took effect all the same (a down that got through before the error)."""

    def __init__(self, trips=(), kind=OSError, applied=True):
        self.trips = dict(trips) if isinstance(trips, dict) else {trip: kind for trip in trips}
        self.applied = applied
        self.calls, self.active, self.raised = [], set(), []

    def __call__(self, name, down):
        self.calls.append((name, down))
        kind = self.trips.pop((name, down), None)
        if kind is None or self.applied:
            (self.active.add if down else self.active.discard)(name)
        if kind is not None:
            self.raised.append(kind(f"synthetic {name} {'down' if down else 'up'}"))
            raise self.raised[-1]


SHIFT, Z = "KEY_LEFTSHIFT", "KEY_Z"


class TapFailures(unittest.TestCase):
    """A tap that fails halfway sends every key it may have put down up again, and says what went wrong."""

    def tap(self, emit, names, error=OSError, wait=0, holds=None):
        holds = holds or vinput.Holds(emit)
        try:
            holds.tap(names, wait=wait)
        except BaseException as caught:  # an interrupt of the wrong kind fails this test, not the whole run
            self.assertIsInstance(caught, error)
            return holds, caught
        self.fail(f"no {error.__name__}")

    def assert_next_tap_works(self, holds, emit):
        before = len(emit.calls)
        holds.tap([CTRL, Z], wait=0)
        self.assertEqual(emit.calls[before:], [(CTRL, True), (Z, True), (Z, False), (CTRL, False)])
        self.assertEqual(emit.active, set())

    def test_the_first_down_failing_still_sends_its_up(self):
        emit = Refusing([(CTRL, True)])
        holds, error = self.tap(emit, [CTRL, Z])
        self.assertIs(error, emit.raised[0])
        self.assertEqual(emit.calls, [(CTRL, True), (CTRL, False)])
        self.assertEqual((emit.active, holds.held()), (set(), set()))
        self.assert_next_tap_works(holds, emit)

    def test_a_later_down_failing_sends_every_up(self):
        emit = Refusing([(Z, True)])
        holds, error = self.tap(emit, [CTRL, Z])
        self.assertIs(error, emit.raised[0])
        self.assertEqual(emit.calls, [(CTRL, True), (Z, True), (Z, False), (CTRL, False)])
        self.assertEqual(emit.active, set())
        self.assert_next_tap_works(holds, emit)

    def test_the_wait_failing_sends_every_up(self):
        for kind in (OSError, KeyboardInterrupt):
            with self.subTest(kind=kind.__name__):
                emit, stopped = Refusing(), kind("synthetic wait")
                with mock.patch.object(vinput.time, "sleep", side_effect=stopped):
                    holds, error = self.tap(emit, [CTRL, Z], kind, wait=0.012)
                self.assertIs(error, stopped)
                self.assertEqual(emit.calls, [(CTRL, True), (Z, True), (Z, False), (CTRL, False)])
                self.assertEqual(emit.active, set())
                self.assert_next_tap_works(holds, emit)

    def test_an_up_failing_still_sends_the_others_and_is_raised(self):
        emit = Refusing([(Z, False)])
        holds, error = self.tap(emit, [CTRL, Z])
        self.assertIs(error, emit.raised[0])
        self.assertEqual(emit.calls, [(CTRL, True), (Z, True), (Z, False), (CTRL, False)])
        self.assertEqual(emit.active, set())
        self.assert_next_tap_works(holds, emit)

    def test_any_kind_of_up_error_lets_the_others_go_up_in_reverse(self):
        emit = Refusing([(SHIFT, False)], ValueError)
        holds, error = self.tap(emit, [CTRL, SHIFT, Z], ValueError)
        self.assertIs(error, emit.raised[0])
        self.assertEqual(emit.calls[3:], [(Z, False), (SHIFT, False), (CTRL, False)])
        self.assertEqual(emit.active, set())

    def test_the_error_of_a_down_wins_over_one_of_an_up(self):
        emit = Refusing({(Z, True): OSError, (CTRL, False): ValueError})
        holds, error = self.tap(emit, [CTRL, Z])
        self.assertIs(error, emit.raised[0])
        self.assertEqual(emit.calls, [(CTRL, True), (Z, True), (Z, False), (CTRL, False)])
        self.assertEqual(len(emit.raised), 2)

    def test_an_interrupt_during_an_up_still_lets_the_others_go_up(self):
        legs = {"alone": ({(Z, False): KeyboardInterrupt}, KeyboardInterrupt),
                "after a down error": ({(Z, True): OSError, (Z, False): KeyboardInterrupt}, OSError),
                "before another up error": ({(Z, False): KeyboardInterrupt, (CTRL, False): OSError}, KeyboardInterrupt),
                "an ordinary error as control": ({(Z, False): OSError}, OSError)}
        for leg, (trips, kind) in legs.items():
            with self.subTest(leg=leg):
                emit = Refusing(trips)
                holds, error = self.tap(emit, [CTRL, Z], kind)
                self.assertIs(error, emit.raised[0])  # the original or the first one, never a later one
                self.assertEqual(emit.calls, [(CTRL, True), (Z, True), (Z, False), (CTRL, False)])
                self.assertEqual(emit.active, set())
                self.assert_next_tap_works(holds, emit)

    def test_a_backend_refusing_every_up_is_asked_once_per_key(self):
        """It is not retried: keys the system never let go of stay down there, and nobody tracks them."""
        emit = Refusing([(Z, False), (CTRL, False)], applied=False)
        holds, error = self.tap(emit, [CTRL, Z])
        self.assertIs(error, emit.raised[0])
        self.assertEqual(emit.calls, [(CTRL, True), (Z, True), (Z, False), (CTRL, False)])
        self.assertEqual(emit.active, {CTRL, Z})
        self.assertEqual(holds.held(), set())
        self.assert_next_tap_works(holds, emit)

    def test_keys_held_by_the_mouse_or_the_keyboard_stay_held(self):
        emit = Refusing()
        holds = vinput.Holds(emit)
        holds.press([CTRL], "keyboard")
        holds.press([SHIFT], "mouse")
        emit.trips[(Z, True)] = OSError
        self.tap(emit, [CTRL, SHIFT, Z], holds=holds)
        self.assertEqual(emit.calls[2:], [(Z, True), (Z, False)])
        self.assertEqual(holds.held(), {CTRL, SHIFT})
        holds.release_all("mouse")
        self.assertEqual(emit.calls[-1], (SHIFT, False))
        holds.release([CTRL], "keyboard")
        self.assertEqual(emit.calls[-1], (CTRL, False))
        self.assertEqual(emit.active, set())

    def test_a_key_named_twice_goes_up_twice_as_on_success(self):
        emit = Refusing([(Z, True)])
        self.tap(emit, [CTRL, CTRL, Z])
        self.assertEqual(emit.calls, [(CTRL, True), (CTRL, True), (Z, True), (Z, False), (CTRL, False), (CTRL, False)])

    def test_a_tap_that_works_sends_and_waits_as_before(self):
        emit, slept = Refusing(), []
        holds = vinput.Holds(emit)
        holds.press([CTRL], "keyboard")
        with mock.patch.object(vinput.time, "sleep", slept.append):
            holds.tap([CTRL, C])
            self.assertEqual(slept, [0.012])
            for names in ([], [CTRL]):
                holds.tap(names)
            holds.tap([Z], wait=0)
        self.assertEqual(slept, [0.012])
        self.assertEqual(emit.calls, [(CTRL, True), (C, True), (C, False), (Z, True), (Z, False)])
        self.assertEqual(holds.held(), {CTRL})

    def test_a_trigger_failing_leaves_what_the_mouse_holds(self):
        made = FakeInput()
        emit = made._holds._emit = Refusing()
        executor = actions.Executor(made, lambda action, phase: None)
        self.addCleanup(executor.close)
        executor.press(actions.keys("LEFTSHIFT"))
        emit.trips[(Z, True)] = OSError
        with self.assertRaises(OSError) as caught:
            executor.trigger(actions.keys("LEFTCTRL", "LEFTSHIFT", "Z"))
        self.assertIs(caught.exception, emit.raised[0])
        self.assertEqual(emit.calls, [(SHIFT, True), (CTRL, True), (Z, True), (Z, False), (CTRL, False)])
        self.assertEqual(made._holds.held(), {SHIFT})
        executor.reset()
        self.assertEqual(emit.calls[-1], (SHIFT, False))
        self.assertEqual(emit.active, set())


class Executors(unittest.TestCase):
    def setUp(self):
        self.input = FakeInput()
        self.mouse = actions.Executor(self.input, lambda action, phase: None)
        self.keyboard = actions.Executor(self.input, lambda action, phase: None, owner="keyboard")
        self.addCleanup(self.mouse.close)
        self.addCleanup(self.keyboard.close)

    def test_the_mouse_starting_over_keeps_the_keyboard_s_ctrl_down(self):
        self.keyboard.press(actions.keys("LEFTCTRL"))
        self.mouse.press(actions.keys("LEFTCTRL", "C"))
        self.mouse.reset()
        self.assertEqual(self.input.sent, [(CTRL, True), (C, True), (C, False)])
        self.keyboard.release(actions.keys("LEFTCTRL"))
        self.assertEqual(self.input.sent[-1], (CTRL, False))

    def test_a_reset_stops_a_repeating_macro(self):
        macro = {"type": "macro", "name": "synthetic", "repeat": True, "steps": [{"do": "keys", "keys": ["KEY_A"]}]}
        started = threading.Event()
        real_tap = self.input.tap
        self.input.tap = lambda names: (started.set(), real_tap(names))
        self.keyboard.press(macro)
        self.assertTrue(started.wait(2.0))
        self.keyboard.reset()
        self.mouse.press(actions.keys("LEFTSHIFT"))
        before = len(self.input.sent)
        threading.Event().wait(0.2)
        taps = [entry for entry in self.input.sent[before:] if entry[0] == "KEY_A"]
        self.assertLessEqual(len(taps), 2)  # the round under way may finish; no more come
        threading.Event().wait(0.2)
        self.assertEqual([entry for entry in self.input.sent[before + len(taps):] if entry[0] == "KEY_A"], [])
        self.assertIn("KEY_LEFTSHIFT", self.input._holds.held())


    def test_a_reset_ends_the_macro_under_way_and_those_queued(self):
        tapped = []
        self.input.tap = lambda names: tapped.append(tuple(names))
        one_shot = {"type": "macro", "steps": [{"do": "delay", "ms": 300}, {"do": "keys", "keys": ["KEY_A"]}]}
        repeating = {"type": "macro", "repeat": True, "steps": [{"do": "keys", "keys": ["KEY_B"]}, {"do": "delay", "ms": 300},
                                                               {"do": "keys", "keys": ["KEY_C"]}]}
        for first in (one_shot, repeating):
            with self.subTest(repeat=first is repeating):
                tapped.clear()
                self.mouse.press({"type": "macro", "repeat": True, "steps": [{"do": "keys", "keys": ["KEY_M"]}]})
                self.keyboard.press(first)
                self.keyboard.trigger({"type": "macro", "steps": [{"do": "keys", "keys": ["KEY_Q"]}]})  # queued behind it
                threading.Event().wait(0.1)
                start = time.monotonic()
                self.keyboard.reset()
                threading.Event().wait(0.5)
                mine = [names for names in tapped if names != ("KEY_M",)]
                self.assertEqual(mine, [("KEY_B",)] if first is repeating else [])  # nothing after the reset
                self.assertIn(("KEY_M",), tapped[-3:])  # the mouse's macro goes on
                self.mouse.reset()
                self.assertLess(time.monotonic() - start, 0.6)
                done = threading.Event()
                self.input.tap = lambda names: (tapped.append(tuple(names)), done.set())
                self.keyboard.trigger({"type": "macro", "steps": [{"do": "keys", "keys": ["KEY_Z"]}]})  # and later ones run
                self.assertTrue(done.wait(1.0))
                self.assertEqual(tapped[-1], ("KEY_Z",))
                self.input.tap = lambda names: tapped.append(tuple(names))


class Steps(unittest.TestCase):
    """The action and vibration steps of a macro, played by each executor on its macro thread."""

    def setUp(self):
        self.input = FakeInput()
        self.devices, self.hooks, self.played = [], [], []
        made = lambda owner: actions.Executor(  # noqa: E731
            self.input, lambda action, phase: self.devices.append(action), lambda name, phase: self.hooks.append((name, phase)),
            owner=owner, haptic=self.played.append)
        self.mouse, self.keyboard = made("mouse"), made("keyboard")
        self.addCleanup(self.mouse.close)
        self.addCleanup(self.keyboard.close)

    def play(self, executor, *steps):
        """Run a macro of ``steps`` and wait for it to end (its last step, a text, says so)."""
        done = threading.Event()
        with mock.patch.object(actions, "insert_text", lambda _vinput, _text: done.set()):
            executor.trigger({"type": "macro", "steps": [*steps, {"do": "text", "text": "end"}]})
            self.assertTrue(done.wait(2.0))

    def test_a_mouse_macro_runs_only_steps_that_would_be_kept(self):
        on_thread = []
        real_tap = self.input.tap
        self.input.tap = lambda names: (on_thread.append(threading.current_thread().name), real_tap(names))
        self.play(self.mouse,
                  {"do": "action", "action": {"type": "host", "host": 1}}, {"do": "action", "action": {"type": "flow"}},
                  {"do": "action", "action": {"type": "wheel_mode"}}, {"do": "action", "action": {"type": "dpi_cycle"}},
                  {"do": "action", "action": {"type": "macro", "steps": [{"do": "keys", "keys": ["KEY_Z"]}]}},
                  {"do": "action", "action": "keys"}, {"do": "action"}, {"do": "haptic", "waveform": True},
                  {"do": "haptic", "waveform": 0x10}, {"do": "haptic"}, {"do": "warp"},
                  {"do": "action", "action": {"type": "keys", "keys": ["KEY_A"]}},
                  {"do": "action", "action": {"type": "hook", "name": "paint.brush"}}, {"do": "haptic", "waveform": 4})
        self.assertEqual(self.input.sent, [("KEY_A", True), ("KEY_A", False)])
        self.assertEqual(on_thread, ["macro"])
        self.assertEqual(self.devices, [])  # nothing that talks to a device
        self.assertEqual(self.hooks, [("paint.brush", "trigger")])
        self.assertEqual(self.played, [4])

    def test_nested_keys_leave_the_keyboard_s_ctrl_down(self):
        self.keyboard.press(actions.keys("LEFTCTRL"))
        self.play(self.keyboard, {"do": "action", "action": actions.keys("LEFTCTRL", "C")})
        self.play(self.mouse, {"do": "action", "action": actions.keys("LEFTCTRL", "V")})
        self.assertEqual(self.input.sent, [(CTRL, True), (C, True), (C, False), ("KEY_V", True), ("KEY_V", False)])
        self.assertEqual(self.input._holds.held(), {CTRL})
        self.keyboard.release(actions.keys("LEFTCTRL"))
        self.assertEqual(self.input.sent[-1], (CTRL, False))

    def test_a_reset_stops_repeating_and_queued_steps_of_its_own_executor_only(self):
        tapped = []
        self.input.tap = lambda names: tapped.append(tuple(names))
        key = lambda name: {"do": "action", "action": {"type": "keys", "keys": [name]}}  # noqa: E731
        self.mouse.press({"type": "macro", "repeat": True, "steps": [key("KEY_M")]})
        self.keyboard.press({"type": "macro", "repeat": True, "steps": [key("KEY_B"), {"do": "delay", "ms": 300},
                                                                        {"do": "haptic", "waveform": 4}]})
        self.keyboard.trigger({"type": "macro", "steps": [key("KEY_Q"), {"do": "haptic", "waveform": 7}]})  # queued behind
        threading.Event().wait(0.1)
        self.keyboard.reset()
        threading.Event().wait(0.5)
        mine = [names for names in tapped if names != ("KEY_M",)]
        self.assertEqual(mine, [("KEY_B",)])
        self.assertEqual(self.played, [])  # the vibration after the wait, and the queued one, never came
        self.assertIn(("KEY_M",), tapped[-3:])  # the mouse's macro goes on
        self.mouse.reset()

    def test_a_command_started_by_a_step_is_not_waited_for(self):
        with mock.patch.object(actions.subprocess, "Popen") as popen:
            self.play(self.keyboard, {"do": "action", "action": {"type": "command", "command": "sleep 60"}},
                      {"do": "action", "action": {"type": "script", "name": "s", "script": "true"}},
                      {"do": "action", "action": actions.keys("A")})
        self.assertEqual([call.args[0] for call in popen.call_args_list], ["sleep 60", ["bash", "-c", "true"]])
        self.assertEqual(self.input.sent[-2:], [("KEY_A", True), ("KEY_A", False)])


class SourceCancel(unittest.TestCase):
    """Executor.cancel(action): only the macros started from that one action object stop (app profiles)."""

    def setUp(self):
        self.input = FakeInput()
        self.tapped = []
        self.input.tap = lambda names: self.tapped.append(names[0])
        self.keyboard = actions.Executor(self.input, lambda action, phase: None, owner="keyboard")
        self.addCleanup(self.keyboard.close)

    def wait_for(self, name, timeout=2.0):
        end = time.monotonic() + timeout
        while name not in self.tapped and time.monotonic() < end:
            time.sleep(0.01)
        self.assertIn(name, self.tapped)

    @staticmethod
    def macro(*names, wait=0, repeat=False):
        steps = [{"do": "keys", "keys": [name]} for name in names]
        if wait:
            steps.insert(1, {"do": "delay", "ms": wait})
        return {"type": "macro", "repeat": repeat, "steps": steps}

    def test_the_one_playing_stops_before_its_next_step_its_wait_cut_short(self):
        changed, other = self.macro("KEY_A", "KEY_B", wait=3000), self.macro("KEY_M", "KEY_N")
        self.keyboard.press(changed)
        self.keyboard.press(changed)  # the same key again: queued behind
        self.keyboard.press(other)    # another key: queued too
        self.wait_for("KEY_A")
        time.sleep(0.1)  # in its wait now
        start = time.monotonic()
        self.keyboard.cancel(changed)
        self.wait_for("KEY_N")
        self.assertLess(time.monotonic() - start, 1.0)  # the wait of 3 s was cut short
        time.sleep(0.2)
        self.assertEqual(self.tapped, ["KEY_A", "KEY_M", "KEY_N"])  # no B, no second A: the other key's runs

    def test_an_unchanged_one_plays_on_and_a_queued_changed_one_is_dropped(self):
        unchanged, changed = self.macro("KEY_M", "KEY_N", wait=300), self.macro("KEY_A")
        self.keyboard.press(unchanged)
        self.keyboard.trigger(changed)
        self.wait_for("KEY_M")
        self.keyboard.cancel(changed)
        self.wait_for("KEY_N")
        time.sleep(0.2)
        self.assertEqual(self.tapped, ["KEY_M", "KEY_N"])

    def test_a_repeating_one_stops_and_a_command_it_started_runs_on(self):
        repeating = {"type": "macro", "repeat": True, "steps": [{"do": "command", "command": "sleep 60"},
                                                                {"do": "keys", "keys": ["KEY_R"]}, {"do": "delay", "ms": 50}]}
        with mock.patch.object(actions.subprocess, "Popen") as popen:
            self.keyboard.press(repeating)  # held down: it repeats
            self.wait_for("KEY_R")
            self.keyboard.cancel(repeating)
            time.sleep(0.3)
            count = len(self.tapped)
            time.sleep(0.3)
            self.assertEqual(len(self.tapped), count)  # stopped
            self.assertTrue(popen.called)
            started = popen.return_value
            self.assertFalse(started.kill.called or started.terminate.called)  # the command keeps running

    def test_an_equal_action_that_is_another_object_is_not_stopped(self):
        playing = self.macro("KEY_A", "KEY_B", wait=300)
        self.keyboard.press(playing)
        self.wait_for("KEY_A")
        self.keyboard.cancel(copy.deepcopy(playing))
        self.wait_for("KEY_B")

    def test_nothing_is_kept_once_the_macros_are_over(self):
        for _round in range(20):
            action = self.macro("KEY_A")
            self.keyboard.trigger(action)
            self.keyboard.press(action)
            self.keyboard.release(action)
        end = time.monotonic() + 2.0
        while (len(self.tapped) < 40 or len(self.keyboard._sources)) and time.monotonic() < end:
            time.sleep(0.01)
        self.assertEqual(len(self.keyboard._sources), 0)
        self.assertEqual(self.keyboard._macro_holds, {})

    def test_a_new_macro_after_a_cancel_or_a_reset_plays(self):
        action = self.macro("KEY_A", "KEY_B", wait=2000)
        for stop in (lambda: self.keyboard.cancel(action), self.keyboard.reset):
            self.tapped.clear()
            self.keyboard.trigger(action)
            self.wait_for("KEY_A")
            stop()
            self.tapped.clear()
            self.keyboard.trigger(self.macro("KEY_Z"))
            self.keyboard.trigger(action)  # the very same object again
            self.wait_for("KEY_Z")
            self.wait_for("KEY_A")
            self.keyboard.cancel(action)  # not left in its wait for the next round


class Backends(unittest.TestCase):
    """Both backends with the system left out: what they would send is recorded."""

    def check(self, made, sent):
        made.press([CTRL], "keyboard")
        made.press([CTRL, C], "mouse")
        made.release_all("mouse")
        made.tap([CTRL, "KEY_V"])
        self.assertEqual(sent, [(CTRL, True), (C, True), (C, False), ("KEY_V", True), ("KEY_V", False)])
        made.release([CTRL], "keyboard")
        self.assertEqual(sent[-1], (CTRL, False))

    def test_linux(self):
        from mxoptions import vinput_uinput
        made = vinput_uinput.VirtualInput.__new__(vinput_uinput.VirtualInput)
        sent = []
        made._lock = threading.Lock()
        made._holds = vinput.Holds(lambda name, down: sent.append((name, down)))
        self.check(made, sent)

    def test_windows(self):
        """Only its logic: SendInput itself is not reached from here."""
        fake_windows = types.ModuleType("mxoptions.windows")
        fake_windows.BRIGHTNESS_STEP = 10
        fake_windows.brightness = mock.Mock()
        with mock.patch.object(ctypes, "WinDLL", mock.MagicMock(), create=True), \
                mock.patch.dict(sys.modules, {"mxoptions.windows": fake_windows}):
            sys.modules.pop("mxoptions.vinput_windows", None)
            backend = importlib.import_module("mxoptions.vinput_windows")
            try:
                sent = []
                backend._send = lambda *inputs: sent.extend(
                    ((made.ki.wVk or made.ki.wScan), not made.ki.dwFlags & backend.KEY_UP) for made in (i.u for i in inputs))
                made = backend.VirtualInput()
                sent.clear()
                names = []
                made._holds = vinput.Holds(lambda name, down: names.append((name, down)) or backend.VirtualInput._key(name, down))
                self.check(made, names)
                self.assertEqual(len(sent), len(names))  # each of them did go to SendInput
                made.tap(["KEY_BRIGHTNESSUP"])  # no key for it: the screens are told directly, once
                fake_windows.brightness.change.assert_called_once_with(10)
            finally:
                sys.modules.pop("mxoptions.vinput_windows", None)

    def test_linux_tap_failing_sends_its_ups_and_lets_go_of_the_lock(self):
        from mxoptions import vinput_uinput
        made = vinput_uinput.VirtualInput.__new__(vinput_uinput.VirtualInput)
        emit = Refusing([(Z, True)])
        made._lock = threading.Lock()
        made._holds = vinput.Holds(emit)
        with mock.patch.object(vinput.time, "sleep"):
            with self.assertRaises(OSError) as caught:
                made.tap([CTRL, Z])
            self.assertIs(caught.exception, emit.raised[0])
            self.assertFalse(made._lock.locked())
            made.tap([CTRL, Z])
        self.assertEqual(emit.calls, [(CTRL, True), (Z, True), (Z, False), (CTRL, False)] * 2)
        self.assertEqual(emit.active, set())

    def test_windows_tap_failing_sends_its_ups_and_lets_go_of_the_lock(self):
        """Only its logic: SendInput itself is not reached from here."""
        fake_windows = types.ModuleType("mxoptions.windows")
        fake_windows.BRIGHTNESS_STEP = 10
        fake_windows.brightness = mock.Mock()
        with mock.patch.object(ctypes, "WinDLL", mock.MagicMock(), create=True), \
                mock.patch.dict(sys.modules, {"mxoptions.windows": fake_windows}), mock.patch.object(vinput.time, "sleep"):
            sys.modules.pop("mxoptions.vinput_windows", None)
            backend = importlib.import_module("mxoptions.vinput_windows")
            try:
                code = lambda made: ((made.ki.wVk or made.ki.wScan), not made.ki.dwFlags & backend.KEY_UP)  # noqa: E731
                sent, refused = [], []
                refuse = {code(backend._key(Z, True).u)}

                def send(*inputs):
                    sent.extend(code(i.u) for i in inputs)
                    if sent[-1] in refuse:
                        refuse.clear()
                        refused.append(OSError("synthetic: Windows did not take the input"))
                        raise refused[-1]
                backend._send = send
                made = backend.VirtualInput()
                sent.clear()
                with self.assertRaises(OSError) as caught:
                    made.tap([CTRL, Z])
                self.assertIs(caught.exception, refused[0])
                self.assertFalse(made._lock.locked())
                expected = [code(backend._key(name, down).u) for name, down in ((CTRL, True), (Z, True), (Z, False), (CTRL, False))]
                self.assertEqual(sent, expected)
                made.tap([CTRL, Z])
                self.assertEqual(sent, expected * 2)
            finally:
                sys.modules.pop("mxoptions.vinput_windows", None)


V, X = "KEY_V", "KEY_X"


class Failing(unittest.TestCase):
    def fails(self, fn, *args, error=OSError):
        try:
            fn(*args)
        except BaseException as caught:  # an interrupt of the wrong kind fails this test, not the whole run
            self.assertIsInstance(caught, error)
            return caught
        self.fail(f"no {error.__name__}")

    def assert_healthy(self, holds, emit, owner="mouse"):
        before = len(emit.calls)
        holds.press([CTRL, Z], owner)
        holds.tap([C], wait=0)
        holds.release([CTRL, Z], owner)
        self.assertEqual(emit.calls[before:], [(CTRL, True), (Z, True), (C, True), (C, False), (Z, False), (CTRL, False)])
        self.assertEqual((emit.active, holds.held()), (set(), set()))


class PressFailures(Failing):
    """A press that fails holds nothing: what it may have put down goes up again, and its error is raised."""

    def test_the_first_down_failing_sends_its_up_and_holds_nothing(self):
        emit = Refusing([(CTRL, True)])
        holds = vinput.Holds(emit)
        self.assertIs(self.fails(holds.press, [CTRL, Z], "mouse"), emit.raised[0])
        self.assertEqual(emit.calls, [(CTRL, True), (CTRL, False)])
        self.assertEqual((emit.active, holds.held()), (set(), set()))
        self.assert_healthy(holds, emit)

    def test_a_later_down_failing_lets_every_key_go_up_in_reverse(self):
        emit = Refusing([(Z, True)])
        holds = vinput.Holds(emit)
        self.assertIs(self.fails(holds.press, [CTRL, Z, X], "mouse"), emit.raised[0])
        self.assertEqual(emit.calls, [(CTRL, True), (Z, True), (Z, False), (CTRL, False)])  # X never went down
        self.assertEqual((emit.active, holds.held()), (set(), set()))
        self.assert_healthy(holds, emit)

    def test_what_another_owner_holds_stays_held(self):
        emit = Refusing()
        holds = vinput.Holds(emit)
        holds.press([CTRL], "keyboard")
        emit.trips[(Z, True)] = OSError
        self.fails(holds.press, [CTRL, Z], "mouse")
        self.assertEqual(emit.calls, [(CTRL, True), (Z, True), (Z, False)])
        holds.release_all("mouse")
        self.assertEqual((emit.active, holds.held()), ({CTRL}, {CTRL}))
        holds.release([CTRL], "keyboard")
        self.assertEqual(emit.calls[-1], (CTRL, False))

    def test_what_the_same_owner_holds_already_keeps_its_count(self):
        emit = Refusing()
        holds = vinput.Holds(emit)
        holds.press([CTRL, X], "mouse")  # another button of the same owner
        emit.trips[(Z, True)] = OSError
        self.fails(holds.press, [CTRL, Z, X], "mouse")
        self.assertEqual(emit.calls[2:], [(Z, True), (Z, False)])
        self.assertEqual(emit.active, {CTRL, X})
        holds.release([CTRL, X], "mouse")
        self.assertEqual(emit.calls[-2:], [(X, False), (CTRL, False)])
        self.assertEqual((emit.active, holds.held()), (set(), set()))

    def test_a_key_named_twice_goes_up_once(self):
        emit = Refusing([(Z, True)])
        holds = vinput.Holds(emit)
        self.fails(holds.press, [CTRL, CTRL, Z], "mouse")
        self.assertEqual(emit.calls, [(CTRL, True), (Z, True), (Z, False), (CTRL, False)])
        self.assertEqual(holds.held(), set())

    def test_an_interrupt_lets_the_keys_go_up_and_the_press_s_error_wins(self):
        for trips, error in (({(Z, True): KeyboardInterrupt}, KeyboardInterrupt),
                             ({(Z, True): OSError, (Z, False): KeyboardInterrupt}, OSError),
                             ({(Z, True): OSError, (CTRL, False): ValueError}, OSError)):
            with self.subTest(trips=trips):
                emit = Refusing(trips)
                holds = vinput.Holds(emit)
                self.assertIs(self.fails(holds.press, [CTRL, Z], "mouse", error=error), emit.raised[0])
                self.assertEqual(emit.calls, [(CTRL, True), (Z, True), (Z, False), (CTRL, False)])
                self.assertEqual(holds.held(), set())
                self.assert_healthy(holds, emit)

    def test_a_backend_refusing_everything_is_asked_once_and_nothing_is_tracked(self):
        """Not retried: a key the system never let go of stays down there; nobody tracks it."""
        emit = Refusing([(CTRL, True), (CTRL, False)], applied=False)
        holds = vinput.Holds(emit)
        self.fails(holds.press, [CTRL, Z], "mouse")
        self.assertEqual(emit.calls, [(CTRL, True), (CTRL, False)])
        self.assertEqual(holds.held(), set())

    def test_presses_that_work_send_as_before(self):
        emit = Refusing()
        holds = vinput.Holds(emit)
        holds.press([CTRL, C], "mouse")
        holds.press([CTRL], "keyboard")
        holds.press([CTRL, C], "mouse")
        holds.release([CTRL, C], "mouse")
        holds.release([CTRL, C], "mouse")
        self.assertEqual(emit.calls, [(CTRL, True), (C, True), (C, False)])
        self.assertEqual(holds.held(), {CTRL})


class ReleaseFailures(Failing):
    """Every key a release lets go of is sent up once, the others even when one fails; the first error is raised."""

    def test_an_up_failing_still_sends_the_others_and_holds_nothing(self):
        for kind in (OSError, ValueError, KeyboardInterrupt):
            with self.subTest(kind=kind.__name__):
                emit = Refusing()
                holds = vinput.Holds(emit)
                holds.press([CTRL, SHIFT, Z], "mouse")
                emit.trips.update({(SHIFT, False): kind, (CTRL, False): OSError})
                self.assertIs(self.fails(holds.release, [CTRL, SHIFT, Z], "mouse", error=kind), emit.raised[0])
                self.assertEqual(emit.calls[3:], [(Z, False), (SHIFT, False), (CTRL, False)])
                self.assertEqual(holds.held(), set())
                self.assert_healthy(holds, emit)

    def test_a_backend_refusing_every_up_is_asked_once_per_key(self):
        emit = Refusing()
        holds = vinput.Holds(emit)
        holds.press([CTRL, Z], "mouse")
        emit.applied = False
        emit.trips.update({(Z, False): OSError, (CTRL, False): OSError})
        self.fails(holds.release, [CTRL, Z], "mouse")
        holds.release([CTRL, Z], "mouse")
        holds.release_all()
        self.assertEqual(emit.calls[2:], [(Z, False), (CTRL, False)])
        self.assertEqual((emit.active, holds.held()), ({CTRL, Z}, set()))

    def test_letting_go_of_everything_tries_every_key(self):
        emit = Refusing()
        holds = vinput.Holds(emit)
        holds.press([CTRL], "keyboard")
        holds.press([SHIFT, Z], "mouse")
        emit.trips.update({(Z, False): ValueError, (SHIFT, False): OSError})
        self.assertIs(self.fails(holds.release_all, error=ValueError), emit.raised[0])
        self.assertEqual(emit.calls[3:], [(Z, False), (SHIFT, False), (CTRL, False)])
        self.assertEqual(holds.held(), set())

    def test_letting_go_of_one_owner_keeps_the_other_s_keys(self):
        emit = Refusing()
        holds = vinput.Holds(emit)
        holds.press([CTRL], "keyboard")
        holds.press([SHIFT, Z], "mouse")
        emit.trips[(Z, False)] = OSError  # refused while closing down: let be, as before
        holds.release_all("mouse")
        self.assertEqual(emit.calls[3:], [(Z, False), (SHIFT, False)])
        self.assertEqual((emit.active, holds.held()), ({CTRL}, {CTRL}))


BACK, FORWARD = config.BUTTONS["back"], config.BUTTONS["forward"]


class MouseButtonFailures(Failing):
    """A mouse button whose keys could not be pressed leaves nothing down, nor anything another button holds.
    Reports go the whole way: synthetic port, transport, engine, real Executor, FakeInput."""

    def rig(self, device_action=None, hook=None, **buttons):
        mouse = support.FakeDevice("Synthetic mouse", kind=device.KIND_MOUSE,
                                   controls=[(BACK, BACK, 0x30), (FORWARD, FORWARD, 0x30)])
        rig = test_keyboard.Rig(self, {2: mouse})
        rig.engine.cfg["buttons"].update(buttons)
        rig.start()
        rig.engine.executor = actions.Executor(rig.input, device_action or rig.engine._device_action, hook)
        self.addCleanup(rig.engine.executor.close)
        emit = rig.input._holds._emit = Refusing()
        report = lambda *cids: rig.note(2, mouse, device.REPROG_CONTROLS_V4,  # noqa: E731
                                        struct.pack("!HHHH", *(list(cids) + [0] * 4)[:4]))
        return report, rig, emit

    def test_a_press_failing_leaves_nothing_down_once_the_button_is_up(self):
        buttons, rig, emit = self.rig(back=actions.keys("LEFTCTRL", "Z"))
        emit.trips[(Z, True)] = OSError
        self.assertIs(self.fails(buttons, BACK), emit.raised[0])
        buttons()
        self.assertEqual(emit.calls, [(CTRL, True), (Z, True), (Z, False), (CTRL, False)])
        buttons(BACK)
        buttons()
        self.assertEqual(emit.calls[4:], [(CTRL, True), (Z, True), (Z, False), (CTRL, False)])
        self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))

    def test_one_button_failing_in_a_report_lets_the_other_go_up_with_its_button(self):
        worked = False
        for failing in (BACK, FORWARD):
            with self.subTest(failing=config.BUTTON_BY_CID[failing]):
                buttons, rig, emit = self.rig(back=actions.keys("LEFTSHIFT", "C"), forward=actions.keys("LEFTCTRL", "Z"))
                emit.trips[(C if failing == BACK else Z, True)] = OSError
                self.fails(buttons, BACK, FORWARD)
                worked = worked or emit.calls[0] == ((CTRL, True) if failing == BACK else (SHIFT, True))
                buttons()
                self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))
        self.assertTrue(worked)  # the other button's press did go first at least once

    def test_another_button_of_the_mouse_keeps_its_keys(self):
        buttons, rig, emit = self.rig(back=actions.keys("LEFTCTRL", "C"), forward=actions.keys("LEFTCTRL", "V"))
        buttons(BACK)
        emit.trips[(V, True)] = OSError
        self.fails(buttons, BACK, FORWARD)
        buttons(BACK)
        self.assertEqual(emit.active, {CTRL, C})
        buttons()
        self.assertEqual(emit.calls, [(CTRL, True), (C, True), (V, True), (V, False), (C, False), (CTRL, False)])
        self.assertEqual(rig.input._holds.held(), set())

    def test_a_failed_button_is_not_pressed_again_by_a_later_report(self):
        buttons, rig, emit = self.rig(back=actions.keys("LEFTSHIFT"), forward=actions.keys("LEFTCTRL", "Z"))
        buttons(BACK)
        emit.trips[(Z, True)] = OSError
        self.fails(buttons, BACK, FORWARD)
        buttons(FORWARD)
        buttons()
        self.assertEqual(emit.calls, [(SHIFT, True), (CTRL, True), (Z, True), (Z, False), (CTRL, False), (SHIFT, False)])

    def test_after_its_up_failed_the_button_works_again(self):
        buttons, rig, emit = self.rig(back=actions.keys("LEFTCTRL", "Z"))
        buttons(BACK)
        emit.trips[(Z, False)] = OSError
        self.assertIs(self.fails(buttons), emit.raised[0])
        buttons(BACK)
        buttons()
        self.assertEqual(emit.calls, [(CTRL, True), (Z, True), (Z, False), (CTRL, False)] * 2)
        self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))


class TemporaryZoom(Failing):
    """The legacy thumb wheel zoom: Ctrl held for one wheel step, whatever fails, never longer."""

    def zoom(self, fail):
        made = profiles.thumb_engine(self, thumbwheel={"function": "zoom"})
        emit = made.vinput._holds._emit = Refusing()
        fail(made, emit)
        with support.Clock() as clock, mock.patch.object(engine.time, "sleep") as slept:
            self.fails(profiles.turn, made, clock, profiles.STEP_UP, 4)
            failed = list(emit.calls), slept.call_count
            made.vinput.scroll = made.vinput.scrolled.append
            profiles.turn(made, clock, profiles.STEP_UP)
        return made, emit, failed, slept.call_count

    def test_ctrl_going_down_failing_goes_up_again(self):
        made, emit, (calls, sleeps), _total = self.zoom(lambda made, emit: emit.trips.__setitem__((CTRL, True), OSError))
        self.assertEqual((calls, sleeps), ([(CTRL, True), (CTRL, False)], 0))
        self.assertEqual(emit.calls[2:], [(CTRL, True), (CTRL, False)])  # the next step works
        self.assertEqual((emit.active, made.vinput._holds.held()), (set(), set()))

    def test_the_scroll_failing_lets_ctrl_go_up(self):
        made, emit, (calls, sleeps), _total = self.zoom(
            lambda made, emit: setattr(made.vinput, "scroll", mock.Mock(side_effect=OSError("synthetic scroll"))))
        self.assertEqual((calls, sleeps), ([(CTRL, True), (CTRL, False)], 1))
        self.assertEqual((emit.active, made.vinput._holds.held()), (set(), set()))

    def test_ctrl_held_by_the_keyboard_or_a_button_stays_held(self):
        for owner in ("keyboard", "mouse"):
            with self.subTest(owner=owner):
                def fail(made, emit, owner=owner):
                    made.vinput.press([CTRL], owner)
                    made.vinput.scroll = mock.Mock(side_effect=OSError("synthetic scroll"))
                made, emit, (calls, _sleeps), _total = self.zoom(fail)
                self.assertEqual(calls, [(CTRL, True)])
                self.assertEqual((emit.active, made.vinput._holds.held()), ({CTRL}, {CTRL}))
                made.vinput.release([CTRL], owner)
                self.assertEqual(emit.calls[-1], (CTRL, False))


class BackendPresses(Failing):
    """Both backends with the system left out: a press failing there lets its keys go up and the lock go."""

    def test_linux_press_failing_lets_its_keys_go_up_and_the_lock_go(self):
        from mxoptions import vinput_uinput
        made = vinput_uinput.VirtualInput.__new__(vinput_uinput.VirtualInput)
        emit = Refusing([(Z, True)])
        made._lock = threading.Lock()
        made._holds = vinput.Holds(emit)
        self.assertIs(self.fails(made.press, [CTRL, Z]), emit.raised[0])
        self.assertFalse(made._lock.locked())
        made.press([CTRL, Z])
        made.release([CTRL, Z])
        self.assertEqual(emit.calls, [(CTRL, True), (Z, True), (Z, False), (CTRL, False)] * 2)
        self.assertEqual((emit.active, made._holds.held()), (set(), set()))

    def test_windows_press_failing_lets_its_keys_go_up_and_the_lock_go(self):
        """Only its logic: SendInput itself is not reached from here."""
        fake_windows = types.ModuleType("mxoptions.windows")
        fake_windows.BRIGHTNESS_STEP = 10
        fake_windows.brightness = mock.Mock()
        with mock.patch.object(ctypes, "WinDLL", mock.MagicMock(), create=True), \
                mock.patch.dict(sys.modules, {"mxoptions.windows": fake_windows}):
            sys.modules.pop("mxoptions.vinput_windows", None)
            backend = importlib.import_module("mxoptions.vinput_windows")
            try:
                code = lambda made: ((made.ki.wVk or made.ki.wScan), not made.ki.dwFlags & backend.KEY_UP)  # noqa: E731
                sent, refused = [], []
                refuse = {code(backend._key(Z, True).u)}

                def send(*inputs):
                    sent.extend(code(i.u) for i in inputs)
                    if sent[-1] in refuse:
                        refuse.clear()
                        refused.append(OSError("synthetic: Windows did not take the input"))
                        raise refused[-1]
                backend._send = send
                made = backend.VirtualInput()
                sent.clear()
                self.assertIs(self.fails(made.press, [CTRL, Z]), refused[0])
                self.assertFalse(made._lock.locked())
                expected = [code(backend._key(name, down).u) for name, down in ((CTRL, True), (Z, True), (Z, False), (CTRL, False))]
                self.assertEqual(sent, expected)
                made.press([CTRL, Z])
                made.release([CTRL, Z])
                self.assertEqual(sent, expected * 2)
            finally:
                sys.modules.pop("mxoptions.vinput_windows", None)


ONE, TWO = 0x0053, 0x0f01


class KeyboardKeyFailures(Failing):
    """A key of the keyboard whose keys could not be pressed leaves nothing down, nor what another key holds."""

    def rig(self, keys, hook=None):
        kb, mouse = test_keyboard.keyboard_device(), test_keyboard.mouse_device()
        rig = test_keyboard.Rig(self, {1: kb, 2: mouse}, keys=keys)
        rig.start()
        if hook is not None:
            rig.kb.executor = actions.Executor(rig.input, rig.kb.device_action, hook, owner="keyboard")
            self.addCleanup(rig.kb.executor.close)
        rig.engine.executor = actions.Executor(rig.input, rig.engine._device_action)
        self.addCleanup(rig.engine.executor.close)
        emit = rig.input._holds._emit = Refusing()
        return rig, kb, emit

    def test_another_key_keeps_its_ctrl(self):
        rig, kb, emit = self.rig({"0x0053": actions.keys("LEFTCTRL"), "0x0f01": actions.keys("LEFTCTRL", "Z")})
        rig.keys_down(1, kb, ONE)
        emit.trips[(Z, True)] = OSError
        self.fails(rig.keys_down, 1, kb, ONE, TWO)
        rig.keys_down(1, kb, ONE)
        self.assertEqual(emit.active, {CTRL})
        rig.keys_down(1, kb)
        self.assertEqual(emit.calls, [(CTRL, True), (Z, True), (Z, False), (CTRL, False)])
        self.assertEqual(rig.input._holds.held(), set())

    def test_after_its_up_failed_the_key_works_again(self):
        rig, kb, emit = self.rig({"0x0053": actions.keys("LEFTCTRL", "Z")})
        rig.keys_down(1, kb, ONE)
        emit.trips[(Z, False)] = OSError
        self.assertIs(self.fails(rig.keys_down, 1, kb), emit.raised[0])
        rig.keys_down(1, kb, ONE)
        rig.keys_down(1, kb)
        self.assertEqual(emit.calls, [(CTRL, True), (Z, True), (Z, False), (CTRL, False)] * 2)
        self.assertEqual(rig.input._holds.held(), set())

    def test_one_key_failing_in_a_report_lets_the_other_go_up_with_its_key(self):
        rig, kb, emit = self.rig({"0x0053": actions.keys("LEFTSHIFT"), "0x0f01": actions.keys("LEFTCTRL", "Z")})
        emit.trips[(Z, True)] = OSError
        self.fails(rig.keys_down, 1, kb, ONE, TWO)  # keys in their order: ONE first
        rig.keys_down(1, kb)
        self.assertEqual(emit.calls, [(SHIFT, True), (CTRL, True), (Z, True), (Z, False), (CTRL, False), (SHIFT, False)])
        self.assertEqual(rig.input._holds.held(), set())



class Calls:
    """A hook or device action that records each (kind, phase) and raises once at each of ``trips``."""

    def __init__(self, *trips, kind=RuntimeError):
        self.trips, self.kind, self.seen = set(trips), kind, []

    def __call__(self, action_or_name, phase):
        what = action_or_name.get("type") if isinstance(action_or_name, dict) else action_or_name
        self.seen.append((what, phase))
        if (what, phase) in self.trips:
            self.trips.discard((what, phase))
            raise self.kind(f"synthetic {what} {phase}")


class MouseReports(Failing):
    """Every button of one report is handled even when one of them fails: what was let go of goes up before the
    report's first error is raised, with no report, tick or reset after it. An interrupt or the mouse going away
    ends the report at once."""

    rig = MouseButtonFailures.rig

    def test_every_button_up_at_once_sends_every_up_even_when_some_fail(self):
        first_failed = False
        for failing in ((CTRL,), (SHIFT,), (CTRL, SHIFT)):
            with self.subTest(failing=failing):
                buttons, rig, emit = self.rig(back=actions.keys("LEFTCTRL"), forward=actions.keys("LEFTSHIFT"))
                buttons(BACK, FORWARD)
                emit.trips.update({(name, False): OSError for name in failing})
                self.assertIs(self.fails(buttons), emit.raised[0])
                self.assertEqual(len(emit.raised), len(failing))
                self.assertEqual(sorted(emit.calls[2:]), [(CTRL, False), (SHIFT, False)])
                first_failed = first_failed or (len(failing) == 1 and emit.calls[2][0] == failing[0])
                self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))
                buttons(BACK)
                buttons()
                self.assertEqual(emit.calls[4:], [(CTRL, True), (CTRL, False)])
        self.assertTrue(first_failed)  # at least once the up that failed came first, and the other still went

    def test_a_press_failing_still_lets_another_button_go_up_in_the_same_report(self):
        buttons, rig, emit = self.rig(back=actions.keys("LEFTSHIFT"), forward=actions.keys("LEFTCTRL", "Z"))
        buttons(BACK)
        emit.trips[(Z, True)] = OSError
        self.assertIs(self.fails(buttons, FORWARD), emit.raised[0])  # back up, forward down
        self.assertEqual(emit.calls[1:], [(CTRL, True), (Z, True), (Z, False), (CTRL, False), (SHIFT, False)])
        self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))
        buttons()
        buttons(FORWARD)
        buttons()
        self.assertEqual(emit.calls[6:], [(CTRL, True), (Z, True), (Z, False), (CTRL, False)])

    def test_several_failures_raise_the_first_and_leave_what_the_keyboard_holds(self):
        buttons, rig, emit = self.rig(back=actions.keys("LEFTSHIFT"), forward=actions.keys("LEFTCTRL", "Z"))
        rig.input.press([CTRL], "keyboard")
        buttons(BACK)
        emit.trips.update({(Z, True): OSError, (SHIFT, False): ValueError})
        self.assertIs(self.fails(buttons, FORWARD), emit.raised[0])  # the press's: presses go first
        self.assertEqual(len(emit.raised), 2)
        self.assertEqual(emit.calls[2:], [(Z, True), (Z, False), (SHIFT, False)])  # the keyboard's Ctrl: no event
        self.assertEqual((emit.active, rig.input._holds.held()), ({CTRL}, {CTRL}))
        buttons()
        buttons(BACK)
        buttons()
        self.assertEqual(emit.calls[5:], [(SHIFT, True), (SHIFT, False)])
        rig.input.release([CTRL], "keyboard")
        self.assertEqual((emit.calls[-1], emit.active, rig.input._holds.held()), ((CTRL, False), set(), set()))

    def test_an_interrupt_ends_the_report_and_the_next_report_takes_up_the_rest(self):
        buttons, rig, emit = self.rig(back=actions.keys("LEFTSHIFT"), forward=actions.keys("LEFTCTRL"))
        emit.trips.update({(SHIFT, True): KeyboardInterrupt, (CTRL, True): KeyboardInterrupt})
        self.fails(buttons, BACK, FORWARD, error=KeyboardInterrupt)
        self.assertEqual(len(emit.raised), 1)  # no other button began after it
        emit.trips.clear()  # the other button's trip was never reached
        failed = emit.calls[0][0]
        other = CTRL if failed == SHIFT else SHIFT
        self.assertEqual(emit.calls, [(failed, True), (failed, False)])
        buttons(BACK, FORWARD)  # the one not reached is pressed now; the one that failed is not again
        self.assertEqual(emit.calls[2:], [(other, True)])
        buttons()
        self.assertEqual(emit.calls[3:], [(other, False)])
        self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))

    def test_after_an_interrupted_up_the_button_works_again(self):
        buttons, rig, emit = self.rig(back=actions.keys("LEFTCTRL"))
        buttons(BACK)
        emit.trips[(CTRL, False)] = KeyboardInterrupt
        self.fails(buttons, error=KeyboardInterrupt)
        buttons(BACK)
        buttons()
        self.assertEqual(emit.calls, [(CTRL, True), (CTRL, False)] * 2)
        self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))

    def test_the_mouse_going_away_ends_the_report_and_its_going_lets_go(self):
        def gone(action, phase):
            rig.port.wake()  # the node reads nothing more: the mouse went away
            raise hidpp.HidppClosed("synthetic: the mouse went away")
        buttons, rig, emit = self.rig(device_action=gone, back=actions.keys("LEFTSHIFT"), forward={"type": "dpi_cycle"})
        buttons(BACK)
        self.fails(buttons, FORWARD, error=hidpp.HidppClosed)
        self.assertEqual(emit.calls, [(SHIFT, True)])  # nothing more of that report is done
        rig.settle()  # the engine's thread handles the going
        self.assertEqual(emit.calls, [(SHIFT, True), (SHIFT, False)])
        self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))


class OtherPressFailures(Failing):
    """A failed press of something other than keys or a mouse button (hook, device action, macro) is not known to
    hold nothing: its button's or key's up still lets go of it, as before."""

    def test_a_mouse_button_s_up_still_lets_go_of_a_failed_hook_or_device_action(self):
        for action in ({"type": "hook", "name": "synthetic"}, {"type": "dpi_shift"}):
            with self.subTest(kind=action["type"]):
                calls = Calls(("synthetic", "press"), ("dpi_shift", "press"))
                buttons, rig, emit = self.rig(device_action=calls, hook=calls, back=action)
                self.fails(buttons, BACK, error=RuntimeError)
                buttons()
                self.assertEqual(calls.seen[-2:], [(action.get("name", action["type"]), "press"),
                                                   (action.get("name", action["type"]), "release")])

    rig = MouseButtonFailures.rig

    def test_a_mouse_button_s_up_still_ends_a_macro_whose_start_failed(self):
        macro = {"type": "macro", "steps": [{"do": "keys", "keys": ["KEY_A"]}], "repeat": True}
        buttons, rig, emit = self.rig(back=macro)
        with mock.patch.object(rig.engine.executor.macros, "run", side_effect=RuntimeError("synthetic: no thread")) as run:
            self.fails(buttons, BACK, error=RuntimeError)
        held = run.call_args.args[1]
        self.assertTrue(held.is_set())
        buttons()
        self.assertFalse(held.is_set())  # a repeating macro started all the same would stop with the button

    def test_a_key_s_up_still_lets_go_of_a_failed_hook(self):
        calls = Calls(("synthetic", "press"))
        rig, kb, emit = KeyboardKeyFailures.rig(self, {"0x0053": {"type": "hook", "name": "synthetic"}}, hook=calls)
        self.fails(rig.keys_down, 1, kb, ONE, error=RuntimeError)
        rig.keys_down(1, kb)
        self.assertEqual(calls.seen, [("synthetic", "press"), ("synthetic", "release")])


class KeyboardReports(Failing):
    """Every key of one report is handled even when one of them fails, as with the mouse; an interrupt ends the
    report at once. The mouse going away (from the ring or a hook) is not the keyboard's: its keys carry on."""

    rig = KeyboardKeyFailures.rig

    def test_every_key_up_at_once_sends_every_up_even_when_some_fail(self):
        for failing in ((CTRL,), (SHIFT,), (CTRL, SHIFT)):
            with self.subTest(failing=failing):
                rig, kb, emit = self.rig({"0x0053": actions.keys("LEFTCTRL"), "0x0f01": actions.keys("LEFTSHIFT")})
                rig.keys_down(1, kb, ONE, TWO)
                emit.trips.update({(name, False): OSError for name in failing})
                self.assertIs(self.fails(rig.keys_down, 1, kb), emit.raised[0])
                self.assertEqual(len(emit.raised), len(failing))
                self.assertEqual(emit.calls[2:], [(CTRL, False), (SHIFT, False)])  # keys in their order: ONE first
                self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))
                rig.keys_down(1, kb, ONE)
                rig.keys_down(1, kb)
                self.assertEqual(emit.calls[4:], [(CTRL, True), (CTRL, False)])

    def test_a_press_failing_still_lets_another_key_go_up_in_the_same_report(self):
        rig, kb, emit = self.rig({"0x0053": actions.keys("LEFTSHIFT"), "0x0f01": actions.keys("LEFTCTRL", "Z")})
        rig.keys_down(1, kb, ONE)
        emit.trips[(Z, True)] = OSError
        self.assertIs(self.fails(rig.keys_down, 1, kb, TWO), emit.raised[0])
        self.assertEqual(emit.calls[1:], [(CTRL, True), (Z, True), (Z, False), (CTRL, False), (SHIFT, False)])
        self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))
        rig.keys_down(1, kb)
        rig.keys_down(1, kb, TWO)
        rig.keys_down(1, kb)
        self.assertEqual(emit.calls[6:], [(CTRL, True), (Z, True), (Z, False), (CTRL, False)])

    def test_several_failures_raise_the_first_and_leave_what_the_mouse_holds(self):
        rig, kb, emit = self.rig({"0x0053": actions.keys("LEFTSHIFT"), "0x0f01": actions.keys("LEFTCTRL", "Z")})
        rig.input.press([CTRL], "mouse")
        rig.keys_down(1, kb, ONE)
        emit.trips.update({(Z, True): OSError, (SHIFT, False): ValueError})
        self.assertIs(self.fails(rig.keys_down, 1, kb, TWO), emit.raised[0])
        self.assertEqual(len(emit.raised), 2)
        self.assertEqual(emit.calls[2:], [(Z, True), (Z, False), (SHIFT, False)])
        self.assertEqual((emit.active, rig.input._holds.held()), ({CTRL}, {CTRL}))
        rig.keys_down(1, kb)
        rig.keys_down(1, kb, ONE)
        rig.keys_down(1, kb)
        self.assertEqual(emit.calls[5:], [(SHIFT, True), (SHIFT, False)])
        rig.input.release([CTRL], "mouse")
        self.assertEqual((emit.calls[-1], emit.active, rig.input._holds.held()), ((CTRL, False), set(), set()))

    def test_an_interrupt_ends_the_report_and_the_next_report_takes_up_the_rest(self):
        rig, kb, emit = self.rig({"0x0053": actions.keys("LEFTSHIFT"), "0x0f01": actions.keys("LEFTCTRL")})
        emit.trips[(SHIFT, True)] = KeyboardInterrupt
        self.fails(rig.keys_down, 1, kb, ONE, TWO, error=KeyboardInterrupt)
        self.assertEqual(emit.calls, [(SHIFT, True), (SHIFT, False)])  # TWO never began
        rig.keys_down(1, kb, ONE, TWO)
        self.assertEqual(emit.calls[2:], [(CTRL, True)])  # ONE that failed is not pressed again
        rig.keys_down(1, kb)
        self.assertEqual(emit.calls[3:], [(CTRL, False)])
        self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))

    def test_after_an_interrupted_up_the_key_works_again(self):
        rig, kb, emit = self.rig({"0x0053": actions.keys("LEFTCTRL")})
        rig.keys_down(1, kb, ONE)
        emit.trips[(CTRL, False)] = KeyboardInterrupt
        self.fails(rig.keys_down, 1, kb, error=KeyboardInterrupt)
        rig.keys_down(1, kb, ONE)
        rig.keys_down(1, kb)
        self.assertEqual(emit.calls, [(CTRL, True), (CTRL, False)] * 2)
        self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))

    def test_the_mouse_going_away_in_a_hook_does_not_stop_the_keyboard_s_report(self):
        calls = Calls(("synthetic", "press"), kind=hidpp.HidppClosed)
        rig, kb, emit = self.rig({"0x0053": {"type": "hook", "name": "synthetic"}, "0x0f01": actions.keys("LEFTSHIFT")},
                                 hook=calls)
        rig.keys_down(1, kb, TWO)
        self.fails(rig.keys_down, 1, kb, ONE, error=hidpp.HidppClosed)
        self.assertEqual(emit.calls, [(SHIFT, True), (SHIFT, False)])
        self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))



class Told:
    """A listener method that records each call and raises once, at the call ``at`` picks."""

    def __init__(self, at, kind=OSError):
        self.at, self.kind, self.seen, self.raised = at, kind, [], []

    def __call__(self, *args):
        self.seen.append(args)
        if not self.raised and self.at(*args):
            self.raised.append(self.kind(f"synthetic listener {args}"))
            raise self.raised[-1]


class ReportListeners(Failing):
    """What is told about a report failing (the program's listener) neither stops the report nor hides its error:
    every control of it is still handled at once, and the first error is raised."""

    rig = MouseButtonFailures.rig

    def test_the_listener_failing_on_a_button_up_still_lets_every_other_button_go_up(self):
        not_last = False
        for failing in ("back", "forward"):
            with self.subTest(failing=failing):
                buttons, rig, emit = self.rig(back=actions.keys("LEFTCTRL"), forward=actions.keys("LEFTSHIFT"))
                buttons(BACK, FORWARD)
                told = Told(lambda kind, payload, failing=failing: (kind, payload) == ("button", (failing, False)))
                with mock.patch.object(rig.listener, "on_event", side_effect=told):
                    self.assertIs(self.fails(buttons), told.raised[0])
                self.assertEqual(sorted(told.seen), [("button", ("back", False)), ("button", ("forward", False))])
                not_last = not_last or told.seen[0] == ("button", (failing, False))
                self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))
                buttons(BACK)
                buttons()
                self.assertEqual(emit.calls[4:], [(CTRL, True), (CTRL, False)])
        self.assertTrue(not_last)  # at least once the listener failed before the other button was handled

    def test_the_listener_failing_on_a_button_down_still_lets_another_go_up_in_the_same_report(self):
        buttons, rig, emit = self.rig(back=actions.keys("LEFTSHIFT"), forward=actions.keys("LEFTCTRL"))
        buttons(BACK)
        told = Told(lambda kind, payload: (kind, payload) == ("button", ("forward", True)))
        with mock.patch.object(rig.listener, "on_event", side_effect=told):
            self.assertIs(self.fails(buttons, FORWARD), told.raised[0])  # back up, forward down
        self.assertEqual(told.seen, [("button", ("forward", True)), ("button", ("back", False))])
        self.assertEqual(emit.calls, [(SHIFT, True), (CTRL, True), (SHIFT, False)])
        buttons()
        self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))

    def test_the_listener_failing_on_a_key_s_hook_still_lets_the_other_key_go_up(self):
        rig, kb, emit = KeyboardKeyFailures.rig(self, {"0x0053": {"type": "hook", "name": "synthetic"},
                                                       "0x0f01": actions.keys("LEFTSHIFT")})
        rig.keys_down(1, kb, ONE, TWO)
        told = Told(lambda kind, payload: (kind, payload) == ("hook", ("synthetic", "release")))
        with mock.patch.object(rig.listener, "on_event", side_effect=told):
            self.assertIs(self.fails(rig.keys_down, 1, kb), told.raised[0])  # keys in their order: ONE first
        self.assertEqual(emit.calls, [(SHIFT, True), (SHIFT, False)])
        self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))

    def test_the_buttons_held_are_told_and_a_failure_telling_them_hides_no_earlier_error(self):
        buttons, rig, emit = self.rig(back=actions.keys("LEFTCTRL"))
        buttons(BACK)
        emit.trips[(CTRL, False)] = OSError
        told = Told(lambda keys: True, kind=RuntimeError)
        with mock.patch.object(rig.listener, "on_pressed", side_effect=told):
            self.assertIs(self.fails(buttons), emit.raised[0])
        self.assertEqual(told.seen, [(frozenset(),)])  # told all the same, once the report was handled
        self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))

    def test_a_failure_telling_the_buttons_held_is_raised_when_nothing_else_failed(self):
        buttons, rig, emit = self.rig(back=actions.keys("LEFTCTRL"))
        told = Told(lambda keys: True, kind=RuntimeError)
        with mock.patch.object(rig.listener, "on_pressed", side_effect=told):
            self.assertIs(self.fails(buttons, BACK, error=RuntimeError), told.raised[0])
        self.assertEqual(told.seen, [(frozenset({"back"}),)])
        buttons()
        self.assertEqual(emit.calls, [(CTRL, True), (CTRL, False)])

    def test_each_button_down_told_has_its_up_even_when_its_action_failed(self):
        for trips in ({(Z, True): OSError}, {(Z, False): OSError}):
            with self.subTest(trips=trips):
                buttons, rig, emit = self.rig(back=actions.keys("LEFTCTRL", "Z"), forward=actions.keys("LEFTSHIFT"))
                emit.trips.update(trips)
                if (Z, True) in trips:
                    self.fails(buttons, BACK, FORWARD)
                    buttons()
                else:
                    buttons(BACK, FORWARD)
                    self.fails(buttons)
                told = [payload for kind, payload in rig.listener.events if kind == "button"]
                self.assertEqual(sorted(told), [("back", False), ("back", True), ("forward", False), ("forward", True)])
                for name in ("back", "forward"):
                    self.assertLess(told.index((name, True)), told.index((name, False)))
                self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))


class WaitingSetting(Failing):
    """A change of a keyboard setting waits while a key is down; the key's up starts it at once, though the up
    failed, before that error is raised."""

    def test_a_change_waiting_for_a_key_goes_once_the_key_is_up_though_its_up_failed(self):
        dev = test_kbsettings.fn_keyboard()
        rig = test_keyboard.Rig(self, {1: dev, 2: test_keyboard.mouse_device()}, keys={"0x0f01": test_keyboard.COPY}).start()
        emit = rig.input._holds._emit = Refusing()
        rig.keys_down(1, dev, 0x0F01)
        rig.kb.set_setting("fn", True)
        rig.settle()
        self.assertEqual(dev.setting_writes, [])  # waits for the key
        self.assertTrue(test_kbsettings.fn(rig)["busy"])
        emit.trips[(C, False)] = OSError
        self.assertIs(self.fails(rig.keys_down, 1, dev), emit.raised[0])
        rig.settle()  # runs only a task already started: none is started by it
        self.assertEqual(dev.setting_writes, [(test_kbsettings.HOSTS, b"\x00\x00")])
        self.assertFalse(test_kbsettings.fn(rig)["busy"])
        self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))

    def test_a_waiting_setting_queue_failure_preserves_the_first_error(self):
        for input_failed in (False, True):
            with self.subTest(input_failed=input_failed):
                dev = test_kbsettings.fn_keyboard()
                rig = test_keyboard.Rig(self, {1: dev, 2: test_keyboard.mouse_device()},
                                        keys={"0x0f01": test_keyboard.COPY}).start()
                emit = rig.input._holds._emit = Refusing()
                rig.keys_down(1, dev, 0x0F01)
                rig.kb.set_setting("fn", True)
                rig.settle()
                self.assertEqual(dev.setting_writes, [])
                self.assertTrue(test_kbsettings.fn(rig)["busy"])
                if input_failed:
                    emit.trips[(C, False)] = OSError
                fault = MemoryError("synthetic queue allocation failure")
                with mock.patch.object(rig.kb._jobs, "put", side_effect=fault) as put:
                    error = self.fails(rig.keys_down, 1, dev, error=Exception)
                    self.assertEqual(put.call_count, 1)
                self.assertIs(error, emit.raised[0] if input_failed else fault)
                self.assertEqual((emit.active, rig.input._holds.held()), (set(), set()))


V = "KEY_V"
PRIOR, MACRO, NEW = "prior-SECRET", "macro-text", "user-new-copy"  # synthetic: none may reach a log


class Falsy(OSError):
    """An error that is false: only ``is None`` tells it from no error at all."""

    def __bool__(self):
        return False


class FalsyStop(RuntimeError):
    def __bool__(self):
        return False


class Pasting(unittest.TestCase):
    """A text step pastes through the clipboard, then puts back what was there unless something else was copied
    meanwhile; a paste that fails does the same and raises its own error. Fake clipboard, clock and input only:
    each real way to the clipboard fails the test when reached."""

    def setUp(self):
        self.reached = []

        def tripwire(name):
            def reached(*_args, **_kwargs):
                self.reached.append(name)
                raise AssertionError(f"real clipboard reached: {name}")
            return reached
        for name in ("_tool", "klipper", "_text_tools", "backend", "read", "write"):
            patcher = mock.patch.object(clipboard, name, tripwire(name))
            patcher.start()
            self.addCleanup(patcher.stop)

    def fakes(self, prior, trips=(), sleeps=None, reads=(), write=True, restore=True):
        """A fake board holding ``prior`` and a fake clock, as patches, and an input whose tap goes through
        self.holds over self.emit (a Refusing with ``trips``). ``sleeps``: seconds -> error to raise or hook(board).
        ``reads``: what the reads after the first return or raise, in turn; then the board. ``write``, ``restore``:
        result or error of the first set_text and of those after it. Returns (board, events, input, patches)."""
        board, events, reads, sleeps, count = [prior], [], list(reads), sleeps or {}, {"get": 0, "set": 0}

        def get_text():
            events.append(("get",))
            count["get"] += 1
            if count["get"] > 1 and reads:
                found = reads.pop(0)
                if isinstance(found, BaseException):
                    raise found
                return found
            return board[0]

        def set_text(value):
            events.append(("set", value))
            count["set"] += 1
            outcome = write if count["set"] == 1 else restore
            if isinstance(outcome, BaseException):
                raise outcome
            if outcome:
                board[0] = value
            return outcome

        def sleep(seconds):
            events.append(("sleep", seconds))
            fault = sleeps.get(seconds)
            if isinstance(fault, BaseException):
                raise fault
            if fault is not None:
                fault(board)

        def tap(names):
            events.append(("tap",))
            self.tapped_on.append(threading.current_thread().name)
            self.holds.tap(names, wait=0)
        self.emit, self.tapped_on = Refusing(trips), []
        self.holds = vinput.Holds(self.emit)
        fake = types.SimpleNamespace(tap=tap, type_ascii=lambda typed: events.append(("typed", typed)))
        patches = (mock.patch.multiple(clipboard, get_text=get_text, set_text=set_text),
                   mock.patch.object(actions, "time", types.SimpleNamespace(sleep=sleep)))
        return board, events, fake, patches

    def paste(self, prior=PRIOR, text=MACRO, has_input=True, **faults):
        """insert_text of ``text`` over fakes(prior, **faults). Returns (board, events, raised): what it raised is
        caught, so a failure here fails one test, not the run."""
        board, events, fake, patches = self.fakes(prior, **faults)
        raised = None
        with patches[0], patches[1]:
            try:
                actions.insert_text(fake if has_input else None, text)
            except BaseException as error:  # an interrupt too
                raised = error
        self.assertEqual(self.reached, [])
        return board[0], events, raised

    def no_keys(self):
        self.assertEqual((self.emit.active, self.holds.held()), (set(), set()))

    def no_content(self, logs):
        self.assertEqual([found for found in (PRIOR, MACRO, NEW) if found in "\n".join(logs.output)], [])

    def test_a_paste_puts_back_the_text_that_was_there(self):
        for kind, prior in (("text", PRIOR), ("empty", ""), ("the same", MACRO)):
            with self.subTest(prior=kind):
                board, events, raised = self.paste(prior)
                self.assertIsNone(raised)
                self.assertEqual(events, [("get",), ("set", MACRO), ("sleep", 0.07), ("tap",), ("sleep", 0.18),
                                          ("get",), ("set", prior)])
                self.assertEqual(self.emit.calls, [(CTRL, True), (V, True), (V, False), (CTRL, False)])
                self.assertEqual(board, prior)
                self.no_keys()

    def test_a_failing_paste_puts_the_text_back_and_raises_its_own_error(self):
        tapped = [("get",), ("set", MACRO), ("sleep", 0.07), ("tap",), ("sleep", 0.18), ("get",), ("set", PRIOR)]
        for kind, trips in (("ctrl down", {(CTRL, True): OSError}), ("v down", {(V, True): OSError}),
                            ("v up", {(V, False): OSError}), ("v down interrupted", {(V, True): KeyboardInterrupt})):
            with self.subTest(kind):
                board, events, raised = self.paste(trips=trips)
                self.assertIs(raised, self.emit.raised[0])
                self.assertEqual(events, tapped)  # the wait after the paste comes before what is put back
                self.assertEqual(board, PRIOR)
                self.no_keys()
        with self.subTest("first wait"):
            stopped = RuntimeError("synthetic wait")
            board, events, raised = self.paste(sleeps={0.07: stopped})
            self.assertIs(raised, stopped)
            self.assertEqual(events, [e for e in tapped if e != ("tap",)])
            self.assertEqual(board, PRIOR)
            self.no_keys()

    def test_what_was_copied_meanwhile_is_left_as_it_is(self):
        def copy_new(board):
            board[0] = NEW
        for tap_fails in (False, True):
            trips = {(V, True): OSError} if tap_fails else {}
            for kind, faults, left in (("new text", dict(sleeps={0.18: copy_new}), NEW),
                                       ("empty", dict(reads=[""]), MACRO), ("unreadable", dict(reads=[None]), MACRO)):
                with self.subTest(kind, tap_fails=tap_fails):
                    with self.assertLogs("mxoptions.actions", "INFO") as logs:
                        board, events, raised = self.paste(trips=trips, **faults)
                    self.assertIs(raised, self.emit.raised[0] if tap_fails else None)
                    self.assertEqual(events[-2:], [("sleep", 0.18), ("get",)])  # read back, nothing written
                    self.assertEqual(board, left)
                    self.assertEqual([r.levelname for r in logs.records], ["INFO"])
                    self.no_content(logs)
                    self.no_keys()
        with self.subTest("the same text copied meanwhile is overwritten (known limit)"):
            board, events, raised = self.paste(sleeps={0.18: lambda board: board.__setitem__(0, MACRO)})
            self.assertEqual((board, raised, events[-1]), (PRIOR, None, ("set", PRIOR)))
        with self.subTest("a copy between reading back and writing is overwritten (known limit)"):
            board, events, raised = self.paste(sleeps={0.18: copy_new}, reads=[MACRO])  # it lands after the read
            self.assertEqual((board, raised, events[-1]), (PRIOR, None, ("set", PRIOR)))

    def test_putting_back_failing_is_logged_without_what_was_copied(self):
        for tap_fails in (False, True):
            trips = {(V, True): OSError} if tap_fails else {}
            for kind, faults, told in (
                    ("refused", dict(restore=False), None),
                    ("write raises", dict(restore=UnicodeEncodeError("utf-8", PRIOR, 0, 1, "synthetic")),
                     "UnicodeEncodeError"),
                    ("read raises", dict(reads=[OSError(PRIOR)]), "OSError")):
                with self.subTest(kind, tap_fails=tap_fails):
                    with self.assertLogs("mxoptions.actions", "INFO") as logs:
                        board, events, raised = self.paste(trips=trips, **faults)
                    self.assertIs(raised, self.emit.raised[0] if tap_fails else None)
                    self.assertEqual(board, MACRO)
                    self.assertEqual([r.levelname for r in logs.records], ["WARNING"])
                    if told:
                        self.assertTrue(logs.output[0].endswith(": " + told))
                    self.no_content(logs)
                    self.no_keys()
            for kind in ("read", "write"):
                with self.subTest(kind + " interrupted", tap_fails=tap_fails):
                    interrupt = KeyboardInterrupt("synthetic " + kind)
                    faults = dict(reads=[interrupt]) if kind == "read" else dict(restore=interrupt)
                    board, events, raised = self.paste(trips=trips, **faults)
                    self.assertIs(raised, self.emit.raised[0] if tap_fails else interrupt)
                    self.assertEqual(board, MACRO)
                    self.no_keys()

    def test_without_a_way_to_the_clipboard_the_text_is_typed_as_before(self):
        with self.subTest("no snapshot"):
            board, events, raised = self.paste(None)
            self.assertEqual((board, events, raised), (None, [("get",), ("typed", MACRO)], None))
        with self.subTest("write refused"):
            board, events, raised = self.paste(write=False)
            self.assertEqual((board, events, raised), (PRIOR, [("get",), ("set", MACRO), ("typed", MACRO)], None))
        with self.subTest("write raises"):
            error = UnicodeEncodeError("utf-8", MACRO, 0, 1, "synthetic")
            board, events, raised = self.paste(write=error)
            self.assertIs(raised, error)
            self.assertEqual((board, events), (PRIOR, [("get",), ("set", MACRO)]))
        for kind, call in (("no text", dict(text="")), ("no input", dict(has_input=False))):
            with self.subTest(kind):
                self.assertEqual(self.paste(**call), (PRIOR, [], None))
        self.assertEqual(self.emit.calls, [])

    def macro(self, *macros, settle=None, **faults):
        """Play ``macros`` (lists of steps) on a real MacroRunner over fakes(PRIOR, **faults) until a command step
        "next" runs; ``settle(runner)`` is called in the wait after the paste. Its thread is stopped and joined
        while the fakes are still in place. Returns (board, commands run, log records)."""
        ran, done = [], threading.Event()

        def run_command(command):
            ran.append(command)
            if command == "next":
                done.set()
        if settle:
            faults["sleeps"] = {0.18: lambda _board: settle(runner)}
        board, _events, fake, patches = self.fakes(PRIOR, **faults)
        runner = actions.MacroRunner(fake)
        with patches[0], patches[1], mock.patch.object(actions, "run_command", run_command), \
                self.assertLogs("mxoptions.actions", "DEBUG") as logs:
            try:
                for steps in macros:
                    runner.run({"type": "macro", "steps": steps})
                self.assertTrue(done.wait(2.0))
            finally:
                runner.stop()
                runner._thread.join(2.0)
            self.assertFalse(runner._thread.is_alive())
            actions.log.debug("synthetic end")
        self.assertEqual(self.reached, [])
        return board[0], ran, logs.records[:-1]

    def test_a_paste_failing_in_a_macro_ends_it_with_the_text_put_back(self):
        board, ran, records = self.macro([{"do": "text", "text": MACRO}, {"do": "command", "command": "later"}],
                                         [{"do": "command", "command": "next"}], trips={(V, True): OSError})
        self.assertEqual(self.tapped_on, ["macro"])
        self.assertEqual(board, PRIOR)
        self.assertEqual(ran, ["next"])  # the step after the paste never ran, the next macro did
        failed = [r for r in records if r.getMessage() == "macro failed"]
        self.assertEqual(len(failed), 1)
        self.assertIs(failed[0].exc_info[1], self.emit.raised[0])
        self.no_keys()

    def test_a_cancel_during_a_paste_lets_it_put_the_text_back(self):
        def cancel(runner):
            runner.cancel()
            runner.run({"type": "macro", "steps": [{"do": "command", "command": "next"}]})
        board, ran, records = self.macro([{"do": "text", "text": MACRO}, {"do": "command", "command": "later"}],
                                         settle=cancel)
        self.assertEqual(self.tapped_on, ["macro"])
        self.assertEqual(board, PRIOR)
        self.assertEqual(ran, ["next"])
        self.assertEqual(records, [])
        self.no_keys()

    def test_a_settle_failure_still_attempts_restoration_and_preserves_the_first_error(self):
        written = [("get",), ("set", MACRO), ("get",), ("set", PRIOR)]
        read = written[:3]

        def check(raised, error, board, events, left=PRIOR, tried=written):
            self.assertIs(raised, error)
            self.assertEqual(board, left)
            self.assertEqual([e for e in events if e[0] in ("get", "set")], tried)
            reads = [i for i, e in enumerate(events) if e == ("get",)]
            self.assertIn(("sleep", 0.18), events[:reads[-1]])  # the settle wait before the read back
            self.no_keys()

        for settle in (RuntimeError("synthetic settle"), KeyboardInterrupt("synthetic settle")):
            with self.subTest("settle alone", kind=type(settle).__name__):
                board, events, raised = self.paste(sleeps={0.18: settle})
                check(raised, settle, board, events)
        with self.subTest("first wait and settle"):
            first, settle = RuntimeError("synthetic wait"), RuntimeError("synthetic settle")
            board, events, raised = self.paste(sleeps={0.07: first, 0.18: settle})
            check(raised, first, board, events)
        with self.subTest("tap and settle"):
            board, events, raised = self.paste(trips={(V, True): OSError}, sleeps={0.18: RuntimeError("synthetic")})
            check(raised, self.emit.raised[0], board, events)
        with self.subTest("false tap error alone"):
            board, events, raised = self.paste(trips={(V, True): Falsy})
            self.assertFalse(self.emit.raised[0])
            check(raised, self.emit.raised[0], board, events)
        with self.subTest("false tap error and settle"):
            board, events, raised = self.paste(trips={(V, True): Falsy}, sleeps={0.18: RuntimeError("synthetic")})
            check(raised, self.emit.raised[0], board, events)
        with self.subTest("false tap error and interrupted read"):
            board, events, raised = self.paste(trips={(V, True): Falsy}, reads=[KeyboardInterrupt("synthetic")])
            check(raised, self.emit.raised[0], board, events, left=MACRO, tried=read)
        with self.subTest("false settle error and interrupted read"):
            settle = FalsyStop("synthetic settle")
            self.assertFalse(settle)
            board, events, raised = self.paste(sleeps={0.18: settle}, reads=[KeyboardInterrupt("synthetic")])
            check(raised, settle, board, events, left=MACRO, tried=read)
        for first in ("tap", "settle"):
            for kind, faults, tried in (("read raises", dict(reads=[OSError(PRIOR)]), read),
                                        ("write raises", dict(restore=ValueError(PRIOR)), written)):
                with self.subTest(first + " error, then " + kind):
                    settle = RuntimeError("synthetic settle")
                    if first == "tap":
                        faults["trips"] = {(V, True): OSError}
                    else:
                        faults["sleeps"] = {0.18: settle}
                    with self.assertLogs("mxoptions.actions", "WARNING") as logs:
                        board, events, raised = self.paste(**faults)
                    check(raised, self.emit.raised[0] if first == "tap" else settle, board, events, left=MACRO,
                          tried=tried)
                    self.no_content(logs)
        with self.subTest("a copy during a failing settle"):
            settle = RuntimeError("synthetic settle")

            def copy_then_fail(board):
                board[0] = NEW
                raise settle
            board, events, raised = self.paste(sleeps={0.18: copy_then_fail})
            check(raised, settle, board, events, left=NEW, tried=read)


if __name__ == "__main__":
    unittest.main()
