import copy
import math
import queue
import unittest
from unittest import mock

from tests.support import Clock, make_engine, names
from tests.support import Actions, FakeInput
from mxoptions import device, gestures
from mxoptions import engine as mx_engine, vinput

GESTURE = device.CID_GESTURE


def swipe(engine, moves, cid=GESTURE):
    """Hold the gesture button, feed the raw movement, let go. Returns how the gesture ended."""
    engine._on_buttons({cid})
    for dx, dy in moves:
        engine._on_raw_xy(dx, dy)
    held = list(engine.executor.ran)
    engine._on_buttons(set())
    return engine.listener.gestures[-1]["result"], held


class FailingActions(Actions):
    """``fail``: triggers that raise first, as running an action can."""

    def __init__(self):
        super().__init__()
        self.fail = 0

    def trigger(self, action):
        if self.fail:
            self.fail -= 1
            raise OSError(5, "synthetic")
        super().trigger(action)


def repeating(test, dpi=100, step_mm=2.0, dry=False):
    """Gesture button held in repeat mode, first swipe up done (axis set), Clock standing in for time until test
    ends. Returns (engine, vibrations played, counts of one repeat step)."""
    clock = Clock()
    clock.__enter__()
    test.addCleanup(clock.__exit__)
    engine, played = make_engine(pointer={"dpi": dpi}, gestures={"trigger": "repeat", "step_mm": step_mm})
    engine.executor = FailingActions()
    engine._gesture_dry = dry
    per_mm = max(4.0, dpi / 25.4)
    first = -int(17 * per_mm / 2) - 1
    engine._on_buttons({GESTURE})
    engine._on_raw_xy(0, first)
    engine._on_raw_xy(0, first)
    test.assertEqual(engine._gesture["axis"], 1)
    return engine, played, step_mm * per_mm


def fired(engine):
    return [payload for kind, payload in engine.listener.events if kind == "gesture"]


class Direction(unittest.TestCase):
    def test_axes(self):
        self.assertEqual(gestures.direction(10, 1), "right")
        self.assertEqual(gestures.direction(-10, -1), "left")
        self.assertEqual(gestures.direction(1, -10), "up")
        self.assertEqual(gestures.direction(1, 10), "down")
        self.assertIsNone(gestures.direction(0, 0))

    def test_dead_zone_swallows_diagonals(self):
        self.assertIsNone(gestures.direction(10, 9, dead=5))
        self.assertEqual(gestures.direction(10, 9, dead=0), "right")
        self.assertEqual(gestures.direction(10, 5, dead=5), "right")

    def test_bias_gives_one_pair_more_room(self):
        self.assertEqual(gestures.direction(10, 11), "down")
        self.assertEqual(gestures.direction(10, 11, bias=10), "right")
        self.assertEqual(gestures.direction(11, 10, bias=-10), "down")


class Recognition(unittest.TestCase):
    # At the default 1000 DPI one millimetre is about 39 counts.

    def test_tap(self):
        engine, _ = make_engine()
        self.assertEqual(swipe(engine, [(3, 2)])[0], "tap")
        self.assertEqual(names(engine.executor.ran), ["overview"])

    def test_short_swipe_is_judged_on_release(self):
        engine, _ = make_engine()
        result, while_held = swipe(engine, [(-40, 6)] * 5)
        self.assertEqual((result, while_held), ("left", []))
        self.assertEqual(names(engine.executor.ran), ["desktop_left"])

    def test_upward_drift_does_not_turn_left_into_up(self):
        engine, _ = make_engine()
        self.assertEqual(swipe(engine, [(-40, -23)] * 8)[0], "left")

    def test_long_clear_swipe_fires_while_held(self):
        engine, _ = make_engine()
        result, while_held = swipe(engine, [(90, 5)] * 9)
        self.assertEqual(result, "right")
        self.assertEqual(len(while_held), 1)
        self.assertEqual(len(engine.executor.ran), 1)  # and not again on release

    def test_release_mode_never_fires_early(self):
        engine, _ = make_engine(gestures={"trigger": "release"})
        result, while_held = swipe(engine, [(90, 5)] * 9)
        self.assertEqual((result, while_held), ("right", []))

    def test_one_report_alone_never_fires_while_held(self):
        engine, _ = make_engine()
        result, while_held = swipe(engine, [(846, 138)])
        self.assertEqual((result, while_held), ("right", []))

    def test_diagonal_is_ignored(self):
        engine, _ = make_engine()
        self.assertEqual(swipe(engine, [(-40, -38)] * 8)[0], "diagonal")
        self.assertEqual(engine.executor.ran, [])

    def test_out_and_back_is_cancelled(self):
        engine, _ = make_engine()
        self.assertEqual(swipe(engine, [(40, 0)] * 12 + [(-40, 0)] * 12)[0], "cancelled")
        self.assertEqual(engine.executor.ran, [])

    def test_repeat_mode(self):
        engine, _ = make_engine(gestures={"trigger": "repeat"})
        result, _held = swipe(engine, [(2, -60)] * 30)  # 46 mm up: once at 16 mm, then every 8 mm
        self.assertEqual(result, "repeat")
        self.assertEqual(names(engine.executor.ran), ["all_windows"] * 4)

    def test_practice_mode_recognises_but_does_not_act(self):
        engine, _ = make_engine()
        engine._gesture_dry = True
        self.assertEqual(swipe(engine, [(-40, 6)] * 5)[0], "left")
        self.assertEqual(engine.executor.ran, [])
        self.assertIn(("gesture", "left"), engine.listener.events)

    def test_first_report_after_arming_is_dropped(self):
        engine, _ = make_engine(buttons={"panel": {"type": "gestures", "tap": {"type": "none"}, "up": {"type": "none"},
                                                   "down": {"type": "none"}, "left": {"type": "none"}, "right": {"type": "none"}}})
        engine._apply_buttons()
        panel = device.CID_HAPTIC_PANEL
        self.assertEqual(engine.mouse.reporting[panel], (True, True))
        self.assertEqual(swipe(engine, [(-227, 19)], cid=panel)[0], "tap")   # stale movement, not a swipe
        self.assertEqual(swipe(engine, [(-227, 19)], cid=panel)[0], "left")  # from then on it counts

    def test_one_huge_repeat_report_fires_a_bounded_number_and_owes_nothing(self):
        for dy, name in ((-32768, "up"), (32767, "down")):
            for dry in (False, True):
                with self.subTest(dy=dy, dry=dry):
                    engine, played, step = repeating(self, dry=dry)
                    ran, said, felt = len(engine.executor.ran), len(fired(engine)), len(played)
                    engine._on_raw_xy(0, dy)
                    self.assertEqual(fired(engine)[said:], [name] * mx_engine.REPEAT_MAX)
                    self.assertEqual(len(engine.executor.ran) - ran, 0 if dry else mx_engine.REPEAT_MAX)
                    self.assertEqual(len(played) - felt, mx_engine.REPEAT_MAX)
                    self.assertLess(abs(engine._gesture["y"]), step)
                    engine._on_raw_xy(0, int(math.copysign(step, dy)))  # one step more: one repeat, none owed
                    self.assertEqual(len(fired(engine)) - said, mx_engine.REPEAT_MAX + 1)
                    engine._on_buttons(set())
                    self.assertEqual(engine.listener.gestures[-1]["result"], "repeat")

    def test_ordinary_two_step_repeat_report_fires_twice(self):
        engine, _, step = repeating(self, dpi=1000, step_mm=8.0)
        said = len(fired(engine))
        engine._on_raw_xy(3, -int(2 * step) - 1)
        self.assertEqual(fired(engine)[said:], ["up", "up"])
        engine._on_raw_xy(0, int(2 * step) + 2)  # turned round: down twice
        self.assertEqual(fired(engine)[said:], ["up", "up", "down", "down"])
        engine._on_raw_xy(0, int(step / 2))
        self.assertEqual(len(fired(engine)) - said, 4)

    def test_a_failed_repeat_owes_nothing(self):
        engine, _, step = repeating(self)
        engine.executor.fail = 1
        said = len(fired(engine))
        with self.assertRaises(OSError):
            engine._on_raw_xy(0, -int(3 * step) - 1)
        engine._on_raw_xy(0, -1)
        self.assertEqual(len(fired(engine)) - said, 1)  # the failed one only; nothing fired again


class Vibration(unittest.TestCase):
    RICH = {"cues": dict(gestures.CUE_PRESETS["rich"], up=4, down=4, left=12, right=7)}

    def test_default_is_one_tick_for_a_swipe_and_none_for_a_tap(self):
        engine, played = make_engine()
        swipe(engine, [(-40, 4)] * 6)
        swipe(engine, [(3, 2)])
        self.assertEqual(played, ["Subtle collision"])

    def test_every_moment_can_have_its_own_pattern(self):
        engine, played = make_engine(gestures=self.RICH)
        swipe(engine, [(-40, 4)] * 6)
        self.assertEqual(played, ["Whisper collision", "Sharp collision"])  # became a swipe, recognised
        del played[:]
        swipe(engine, [(-40, -38)] * 8)
        self.assertEqual(played, ["Whisper collision", "Angry alert"])      # became a swipe, not accepted

    def test_direction_change_is_felt(self):
        engine, played = make_engine(gestures=dict(self.RICH, trigger="release"))
        with Clock() as clock:
            engine._on_buttons({GESTURE})
            for dx, dy in [(-40, 2)] * 5 + [(0, -60)] * 9:
                clock.now += 0.02
                engine._on_raw_xy(dx, dy)
            engine._on_buttons(set())
        self.assertEqual(played, ["Whisper collision", "Whisper collision", "Sharp collision"])

    def test_pattern_per_direction(self):
        engine, played = make_engine(gestures=dict(self.RICH, per_direction=True))
        swipe(engine, [(-40, 4)] * 6)
        swipe(engine, [(40, 4)] * 6)
        self.assertEqual(played, ["Whisper collision", "Knock", "Whisper collision", "Completed"])

    def test_switched_off(self):
        engine, played = make_engine(gestures={"haptic": False})
        swipe(engine, [(-40, 4)] * 6)
        engine, played2 = make_engine(haptics={"feedback": False})
        swipe(engine, [(-40, 4)] * 6)
        self.assertEqual(played + played2, [])


CTRL = "KEY_LEFTCTRL"
NOTCH = vinput.NOTCH
F9 = {"type": "keys", "keys": ["KEY_F9"]}


class MotionInput(FakeInput):
    """Virtual input keeping scrolls in same ordered list as keys. ``fail``: scroll raises, as uinput write can."""

    def __init__(self, fail=False):
        super().__init__()
        self.fail = fail

    def scroll(self, delta):
        if self.fail:
            raise OSError(5, "synthetic write error")
        self.sent.append(("scroll", delta))

    def hscroll(self, delta):
        self.sent.append(("hscroll", delta))

    def held(self):
        return self._holds.held()


class Ran(Actions):
    def cancel(self, action):
        pass


class HoldModes(unittest.TestCase):
    """Pan and zoom while gesture button held (action "mode"). Clock stands in for time; sleeps counted, never
    waited: each one moves Clock on by its length."""

    def setUp(self):
        self.clock = Clock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__)
        self.sleeps = []
        patcher = mock.patch.object(mx_engine.time, "sleep", side_effect=self._sleep)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _sleep(self, seconds):
        self.sleeps.append(seconds)
        self.clock.now += seconds

    def make(self, mode="pan", dpi=1000, fail=False, **settings):
        engine, played = make_engine(pointer={"dpi": dpi}, gestures=settings)
        engine.cfg["buttons"]["gesture"]["mode"] = mode
        engine.vinput = MotionInput(fail)
        engine.executor = Ran()
        engine._sync_watcher = lambda: None  # no ordinary reports to follow on synthetic mouse
        return engine, played

    def move(self, engine, moves, step=0.0):
        for dx, dy in moves:
            self.clock.now += step
            engine._on_raw_xy(dx, dy)

    def press(self, engine, moves=(), step=0.0):
        engine._on_buttons({GESTURE})
        self.move(engine, moves, step)

    @staticmethod
    def release(engine):
        engine._on_buttons(set())
        return engine.listener.gestures[-1]["result"]

    @staticmethod
    def drain(engine):
        """Run what waits in engine queue, as engine thread would (bounded)."""
        for _ in range(100):
            try:
                item = engine._queue.get_nowait()
            except queue.Empty:
                return
            engine._handle(item)
        raise AssertionError("engine queue never ran empty")

    def reconfigure(self, engine, change):
        """New settings through set_config, ``change`` applied to a copy of their buttons."""
        cfg = copy.deepcopy(engine.cfg)
        change(cfg["buttons"])
        engine.set_config(cfg)
        self.drain(engine)

    @staticmethod
    def scrolled(engine, kind="scroll", since=0):
        return [value for name, value in engine.vinput.sent[since:] if name == kind]

    @staticmethod
    def units(counts, dpi=1000, speed=1.0):
        """Hi-res wheel units that ``counts`` of raw movement make."""
        return counts * NOTCH / (mx_engine.PAN_MM * max(4.0, dpi / 25.4)) * speed

    @staticmethod
    def gestures_said(engine):
        return [event for event in engine.listener.events if event[0] == "gesture"]

    def test_pan_scrolls_both_ways_as_the_hand_moves_and_runs_nothing(self):  # G01
        engine, _ = self.make()
        self.press(engine, [(100, 100)] + [(40, 40)] * 20)
        self.assertEqual(self.release(engine), "pan")
        down, right = sum(self.scrolled(engine)), sum(self.scrolled(engine, "hscroll"))
        exact = self.units(100 + 40 * 20)
        self.assertGreater(down, 0)  # hand down: wheel up, page comes down with hand
        self.assertLess(right, 0)    # hand right: page goes right with it
        self.assertLessEqual(abs(down - exact), 1)
        self.assertLessEqual(abs(right + exact), 1)
        self.assertEqual(engine.executor.ran, [])
        self.assertEqual(self.gestures_said(engine), [])

    def test_a_press_without_moving_is_still_a_tap(self):  # G02
        for mode in ("pan", "zoom"):
            with self.subTest(mode=mode):
                engine, _ = self.make(mode)
                self.press(engine, [(3, 2), (-30, 20), (40, -10)], step=0.1)
                self.assertEqual(self.release(engine), "tap")
                self.assertEqual(names(engine.executor.ran), ["overview"])
                self.assertEqual(engine.vinput.sent, [])
        with self.subTest("press zone smaller by release: precision speed held meanwhile"):
            engine, _ = self.make()
            engine.cfg["pointer"]["precision_dpi"] = 400
            self.press(engine, [(80, 0)])  # 2 mm: in press zone at 1000 DPI
            engine._dpi_shift = True       # 5 mm at 400 DPI: past it, but never a swipe
            self.assertEqual(self.release(engine), "tap")
            self.assertEqual(names(engine.executor.ran), ["overview"])

    def test_after_panning_coming_back_and_letting_go_runs_no_tap_and_no_swipe(self):  # G03
        for mode in ("pan", "zoom"):
            with self.subTest(mode=mode):
                engine, _ = self.make(mode)
                self.press(engine, [(0, 150)] * 5 + [(0, -150)] * 5, step=0.1)
                self.assertEqual(self.release(engine), mode)
                self.assertTrue(self.scrolled(engine))
                self.assertEqual(engine.executor.ran, [])
                self.assertEqual(self.gestures_said(engine), [])

    def test_slow_movement_adds_up_and_back_and_forth_does_not_drift(self):  # G04
        engine, _ = self.make()
        self.press(engine, [(0, 100)] + [(0, 1)] * 200)
        self.assertLessEqual(abs(sum(self.scrolled(engine)) - self.units(300)), 1)
        engine, _ = self.make()
        self.press(engine, [(0, 100)] + [(0, 3)] * 50 + [(0, -3)] * 50 + [(0, -100)])
        self.assertLessEqual(abs(sum(self.scrolled(engine))), 1)

    def test_speed_scales_and_reverse_flips_both_axes(self):  # G05
        sums = {}
        for name, settings in (("plain", {}), ("fast", {"move_speed": 2.0}), ("reverse", {"move_reverse": True})):
            engine, _ = self.make(**settings)
            self.press(engine, [(150, 150)] * 10)
            sums[name] = (sum(self.scrolled(engine)), sum(self.scrolled(engine, "hscroll")))
        down, right = sums["plain"]
        self.assertTrue(down and right)
        self.assertLessEqual(abs(sums["fast"][0] - 2 * down), 2)
        self.assertLessEqual(abs(sums["fast"][1] - 2 * right), 2)
        self.assertLessEqual(abs(sums["reverse"][0] + down), 1)
        self.assertLessEqual(abs(sums["reverse"][1] + right), 1)
        engine, _ = self.make("zoom", move_reverse=True)
        self.press(engine, [(0, -400)] * 3, step=0.1)
        self.assertLess(sum(self.scrolled(engine)), 0)  # reversed: moving up zooms out

    def test_zoom_is_ctrl_and_whole_wheel_steps_upward_zooms_in(self):  # G06
        whole = int(self.units(1000) / NOTCH) * NOTCH
        engine, _ = self.make("zoom")
        self.press(engine, [(0, -100)] * 10 + [(300, 0)] * 10, step=0.1)  # sideways: nothing
        self.assertEqual(self.release(engine), "zoom")
        sent = engine.vinput.sent
        self.assertTrue(sent)
        self.assertEqual(len(sent) % 3, 0)
        for index in range(0, len(sent), 3):
            down, step, up = sent[index:index + 3]
            self.assertEqual((down, up), ((CTRL, True), (CTRL, False)))
            self.assertEqual(step[0], "scroll")
            self.assertGreater(step[1], 0)
            self.assertEqual(step[1] % NOTCH, 0)
        self.assertEqual(sum(self.scrolled(engine)), whole)
        engine, _ = self.make("zoom")
        self.press(engine, [(0, 100)] * 10, step=0.1)
        self.release(engine)
        self.assertEqual(sum(self.scrolled(engine)), -whole)
        self.assertEqual(engine.vinput.held(), set())

    def test_ctrl_is_never_left_held(self):  # G07
        with self.subTest("held button zooms, scroll fails"):
            engine, _ = self.make("zoom", fail=True)
            engine._on_buttons({GESTURE})
            with self.assertRaises(OSError):
                engine._on_raw_xy(0, -400)  # the engine loop logs it
            self.assertEqual(engine.vinput.sent, [(CTRL, True), (CTRL, False)])
            self.assertEqual(engine.vinput.held(), set())
            sleeps = len(self.sleeps)
            engine._on_raw_xy(0, -400)  # inside ZOOM_GAP after failed step: not tried again at once
            self.assertEqual((len(self.sleeps), len(engine.vinput.sent)), (sleeps, 2))
            engine.vinput.fail = False
            self.assertEqual(self.release(engine), "zoom")
            self.assertEqual(engine.vinput.held(), set())
        with self.subTest("thumb wheel zooms, scroll fails"):
            engine, _ = self.make(fail=True)
            engine.cfg["thumbwheel"]["function"] = "zoom"
            with self.assertRaises(OSError):
                engine._on_thumbwheel(-6)
            self.assertEqual(engine.vinput.sent, [(CTRL, True), (CTRL, False)])
        for owner in ("mouse", "keyboard"):
            with self.subTest(f"Ctrl held by {owner} already"):
                engine, _ = self.make("zoom")
                engine.vinput.press([CTRL], owner)
                self.press(engine, [(0, -400)])
                self.release(engine)
                self.assertTrue(self.scrolled(engine))
                self.assertNotIn((CTRL, False), engine.vinput.sent)
                self.assertEqual(engine.vinput.held(), {CTRL})

    def test_changing_what_the_button_does_while_held_stops_it(self):  # G08
        for name, change in (("swipes", lambda buttons: buttons["gesture"].pop("mode")),
                             ("zoom", lambda buttons: buttons["gesture"].update(mode="zoom")),
                             ("keys", lambda buttons: buttons.update(gesture=F9))):
            with self.subTest(to=name):
                engine, _ = self.make()
                self.press(engine, [(0, 200)])
                sent = len(engine.vinput.sent)
                self.assertTrue(sent)
                self.reconfigure(engine, change)
                self.assertEqual(engine.listener.gestures[-1]["result"], "cancelled")
                self.move(engine, [(0, 200)] * 5, step=0.1)
                engine._on_buttons(set())
                self.assertEqual(len(engine.vinput.sent), sent)
                self.assertEqual(engine.executor.ran, [])

    def test_a_reset_while_panning_stops_it_and_holds_nothing(self):  # G09
        for mode in ("pan", "zoom"):
            with self.subTest(mode=mode):
                engine, _ = self.make(mode)
                self.press(engine, [(0, -400)] * 3, step=0.1)
                sent = list(engine.vinput.sent)
                self.assertTrue(sent)
                engine._reset_input_state()
                self.move(engine, [(0, -400)] * 3, step=0.1)
                engine._on_buttons(set())
                self.assertEqual(engine.vinput.sent, sent)
                self.assertEqual(engine.vinput.held(), set())
                self.assertEqual(engine.executor.ran, [])

    def test_practice_draws_and_feels_but_scrolls_nothing(self):  # G10
        for mode in ("pan", "zoom"):
            with self.subTest(mode=mode):
                engine, played = self.make(mode, cues=Vibration.RICH["cues"])
                engine._gesture_dry = True
                self.press(engine, [(0, -400)] * 5, step=0.1)
                self.assertEqual(self.release(engine), mode)
                self.assertEqual(engine.vinput.sent, [])
                self.assertEqual(played[:1], ["Whisper collision"])  # it left the press zone
                self.assertGreater(len(engine.listener.gestures[-1]["points"]), 2)

    def test_without_virtual_input_nothing_breaks(self):  # G11
        for mode in ("pan", "zoom"):
            with self.subTest(mode=mode):
                engine, _ = self.make(mode)
                engine.vinput = None
                self.press(engine, [(0, -400)] * 5, step=0.1)
                self.assertEqual(self.release(engine), mode)
                self.press(engine, [(3, 2)])
                self.assertEqual(self.release(engine), "tap")
                self.assertEqual(names(engine.executor.ran), ["overview"])

    def test_swipes_shapes_and_repeats_are_not_read_while_panning(self):  # G12
        engine, _ = self.make(trigger="repeat")
        engine.cfg["buttons"]["gesture"]["shapes"] = {"down_right": F9}
        self.press(engine, [(2, -60)] * 30)
        self.assertEqual(self.release(engine), "pan")
        self.press(engine, [(0, 80)] * 6 + [(80, 0)] * 6)
        self.assertEqual(self.release(engine), "pan")
        self.assertEqual(engine.executor.ran, [])
        self.assertEqual(self.gestures_said(engine), [])
        with self.subTest("there and back inside a wide press zone"):
            engine, _ = self.make(min_mm=6.0, shape_mm=3.0)
            engine.cfg["buttons"]["gesture"]["shapes"] = {"left_right": F9}
            self.press(engine, [(-100, 0), (-100, 0), (200, 0), (200, 0)])
            self.assertEqual(self.release(engine), "tap")
            self.assertEqual(names(engine.executor.ran), ["overview"])

    def test_a_mode_it_does_not_know_swipes_as_before(self):  # G13
        for mode in ("rotate", "Pan", 1, True, None, ["pan"], {"pan": 1}):
            with self.subTest(mode=mode):
                engine, _ = self.make(mode)
                self.press(engine, [(-40, 6)] * 5)
                self.assertEqual(self.release(engine), "left")
                self.assertEqual(names(engine.executor.ran), ["desktop_left"])
                self.assertEqual(engine.vinput.sent, [])

    def test_an_app_whose_profile_changes_the_button_stops_the_pan_one_that_does_not_keeps_it(self):  # G14
        engine, _ = self.make()
        engine.cfg["mouse"]["profiles"] = [
            {"app": "other", "name": "Other", "buttons": {"middle": F9}, "ring": None},
            {"app": "takes", "name": "Takes", "buttons": {"gesture": F9}, "ring": None}]
        self.press(engine, [(0, 200)])
        engine._mouse_front("other")
        sent = len(engine.vinput.sent)
        self.move(engine, [(0, 200)])
        self.assertGreater(len(engine.vinput.sent), sent)  # still panning, in the new app
        engine._mouse_front("takes")
        self.assertEqual(engine.listener.gestures[-1]["result"], "cancelled")
        sent = len(engine.vinput.sent)
        self.move(engine, [(0, 200)] * 3)
        engine._on_buttons(set())
        self.assertEqual(len(engine.vinput.sent), sent)
        self.assertEqual(engine.executor.ran, [])

    def test_the_same_hand_movement_pans_the_same_at_any_pointer_speed(self):  # G15
        sums = []
        for dpi in (1000, 2000):
            engine, _ = self.make(dpi=dpi)
            counts = round(3 * dpi / 25.4)  # 3 mm
            self.press(engine, [(counts, counts)] * 10)
            sums.append(sum(self.scrolled(engine)))
        self.assertGreater(sums[0], 0)
        self.assertLessEqual(abs(sums[0] - sums[1]), 1)

    def test_the_thumb_wheel_zooms_as_before(self):  # G16
        for rotation, delta in ((-6, NOTCH), (6, -NOTCH)):  # this mouse reports rolling up as negative
            with self.subTest(rotation=rotation):
                engine, _ = self.make()
                engine.cfg["thumbwheel"]["function"] = "zoom"
                del self.sleeps[:]
                engine._on_thumbwheel(rotation)
                self.assertEqual(engine.vinput.sent, [(CTRL, True), ("scroll", delta), (CTRL, False)])
                self.assertEqual(self.sleeps, [0.008, 0.008])

    def test_one_huge_report_is_one_bounded_zoom_step(self):  # G17
        for dy in (-32768, -32767, 32767):
            for dpi, speed in ((100, 4.0), (32000, 0.25)):
                with self.subTest(dy=dy, dpi=dpi, speed=speed):
                    engine, _ = self.make("zoom", dpi=dpi, move_speed=speed)
                    self.press(engine, [(0, round(2.5 * max(4.0, dpi / 25.4)) + 1)])  # out of press zone
                    self.clock.now += 1.0  # well past ZOOM_GAP
                    sent, sleeps = len(engine.vinput.sent), len(self.sleeps)
                    engine._on_raw_xy(0, dy)
                    steps = self.scrolled(engine, since=sent)
                    self.assertEqual(len(steps), 1 if dpi == 100 else 0)  # 32000 DPI at 0.25: less than a detent
                    self.assertEqual(len(self.sleeps) - sleeps, 2 * len(steps))
                    for delta in steps:
                        self.assertLessEqual(abs(delta), mx_engine.MOVE_MAX * NOTCH)
                        self.assertEqual(delta > 0, dy < 0)  # up zooms in
                    new = engine.vinput.sent[sent:]
                    self.assertEqual(new.count((CTRL, True)), new.count((CTRL, False)))
                    self.assertEqual(engine.vinput.held(), set())

    def test_fast_reports_zoom_at_most_once_per_gap_and_what_waits_behind_them_runs(self):  # G18
        for dy, dpi, speed in ((-9, 100, 4.0), (-32767, 100, 4.0), (-32767, 32000, 0.25), (-90, 1000, 3.0)):
            with self.subTest(dy=dy, dpi=dpi, speed=speed):
                engine, _ = self.make("zoom", dpi=dpi, move_speed=speed)
                del self.sleeps[:]
                engine._on_buttons({GESTURE})
                start = self.clock.now
                self.move(engine, [(0, dy)] * 400, step=0.0075)
                span, during = self.clock.now - start, len(self.sleeps)
                self.assertTrue(self.scrolled(engine))
                self.assertLessEqual(during, 2 * (1 + math.ceil(span / mx_engine.ZOOM_GAP)))
                engine._on_buttons(set())
                self.assertLessEqual(len(self.sleeps) - during, 2)
                self.assertEqual(engine.listener.gestures[-1]["result"], "zoom")
                after = len(self.sleeps)
                engine._handle(("trigger", F9))
                engine._reset_input_state()
                self.assertEqual(len(self.sleeps), after)
                self.assertEqual(engine.executor.ran, [F9])  # what waited ran; no tap
                self.assertEqual(engine.vinput.held(), set())

    def test_one_huge_pan_report_scrolls_once_each_way_within_the_wheel_range(self):  # G19
        for dx, dy in ((32767, 32767), (-32768, -32768), (32767, -32768), (-32768, 32767)):
            with self.subTest(dx=dx, dy=dy):
                engine, _ = self.make(dpi=100, move_speed=4.0)
                self.press(engine, [(0, 11)])  # out of press zone
                sent = len(engine.vinput.sent)
                engine._on_raw_xy(dx, dy)
                new = engine.vinput.sent[sent:]
                self.assertEqual([name for name, _value in new], ["scroll", "hscroll"])
                (_, down), (_, side) = new
                self.assertEqual((down > 0, side > 0), (dy > 0, dx < 0))
                self.assertLessEqual(max(abs(down), abs(side)), mx_engine.MOVE_MAX * NOTCH)
                self.assertEqual(self.sleeps, [])
                sent = len(engine.vinput.sent)
                engine._on_raw_xy(1, 1)  # what did not fit was dropped, not carried over
                self.assertTrue(all(abs(value) < NOTCH for _name, value in engine.vinput.sent[sent:]))

    def test_zoom_held_back_by_the_gap_comes_at_release_and_a_cancel_drops_it(self):  # G20
        moves = [(0, -158)] * 40  # 4 mm up each, all at once: ZOOM_GAP holds them back after first step
        whole = int(self.units(158 * 40) / NOTCH)
        engine, _ = self.make("zoom")
        self.press(engine, moves)
        self.assertEqual(len(self.scrolled(engine)), 1)
        self.assertEqual(self.release(engine), "zoom")
        self.assertEqual(len(self.scrolled(engine)), 2)
        self.assertEqual(sum(self.scrolled(engine)), whole * NOTCH)
        for name, cancel in (("reset", lambda made: made._reset_input_state()),
                             ("mode change", lambda made: self.reconfigure(made, lambda buttons: buttons["gesture"].update(mode="pan")))):
            with self.subTest(cancel=name):
                engine, _ = self.make("zoom")
                self.press(engine, moves)
                cancel(engine)
                engine._on_buttons(set())
                self.assertEqual(len(self.scrolled(engine)), 1)
                self.assertEqual(engine.vinput.held(), set())
                self.assertEqual(engine.executor.ran, [])

    def test_pan_sends_no_empty_scroll(self):  # G21
        for moves, empty in (([(0, 1)] * 300, "hscroll"), ([(1, 0)] * 300, "scroll")):
            with self.subTest(empty=empty):
                engine, _ = self.make()
                self.press(engine, moves)
                self.assertEqual(self.scrolled(engine, empty), [])
                full = self.scrolled(engine, "scroll" if empty == "hscroll" else "hscroll")
                self.assertTrue(full)
                self.assertNotIn(0, full)

    def test_practice_switched_on_while_zoom_waits_sends_none_of_it(self):  # G22
        moves = [(0, -158)] * 40  # one step, then detents wait behind ZOOM_GAP
        with self.subTest("practice on, then release"):
            engine, _ = self.make("zoom")
            self.press(engine, moves)
            sent = list(engine.vinput.sent)
            engine.set_gesture_practice(True)
            self.drain(engine)
            self.assertEqual(self.release(engine), "zoom")
            self.assertEqual(engine.vinput.sent, sent)
        with self.subTest("practice on and off, then release"):
            engine, _ = self.make("zoom")
            self.press(engine, moves)
            sent = list(engine.vinput.sent)
            engine.set_gesture_practice(True)
            engine.set_gesture_practice(False)
            self.drain(engine)
            self.release(engine)
            self.assertEqual(engine.vinput.sent, sent)
            self.clock.now += 1.0
            self.press(engine, [(0, -400)] * 3, step=0.1)  # fresh hold zooms again
            self.release(engine)
            self.assertTrue(self.scrolled(engine, since=len(sent)))
            self.assertEqual(engine.vinput.held(), set())


if __name__ == "__main__":
    unittest.main()
