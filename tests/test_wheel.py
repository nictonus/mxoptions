import unittest
from unittest import mock

from tests.support import Actions, Clock, FakeInput, make_engine, names
from mxoptions import engine as engine_module


def spin(engine, clock, count, rate, notches=1):
    """Turn the wheel ``count`` reports at ``rate`` reports a second."""
    for _ in range(count):
        clock.now += 1.0 / rate
        engine._on_spin(notches, clock.now)


def stop(engine, clock, pause=0.2):
    clock.now += pause
    engine._tick()


def modes(engine):
    """(mode, silent) for every change the engine announced."""
    return [payload for kind, payload in engine.listener.events if kind == "wheel_mode"]


CTRL = "KEY_LEFTCTRL"


class ScrollInput(FakeInput):
    """Virtual input keeping scrolls in same ordered list as keys. ``fail``: scrolls that raise first, as uinput
    write can."""

    def __init__(self):
        super().__init__()
        self.fail = 0

    def scroll(self, delta):
        if self.fail:
            self.fail -= 1
            raise OSError(5, "synthetic write error")
        self.sent.append(("scroll", delta))


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


def thumb_engine(test, **thumbwheel):
    """Engine with ScrollInput and FailingActions. Clock stands in for time until test ends; sleeps counted, never
    waited: each moves clock on by its length. Returns (engine, clock, sleeps)."""
    clock = Clock()
    clock.__enter__()
    test.addCleanup(clock.__exit__)
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.now += seconds

    patcher = mock.patch.object(engine_module.time, "sleep", side_effect=sleep)
    patcher.start()
    test.addCleanup(patcher.stop)
    engine, _ = make_engine(thumbwheel=thumbwheel)
    engine.vinput, engine.executor = ScrollInput(), FailingActions()
    engine._sync_watcher = lambda: None
    return engine, clock, sleeps


def dispatched(engine, function):
    """Steps sent: Ctrl presses for zoom, actions run for the others."""
    return engine.vinput.sent.count((CTRL, True)) if function == "zoom" else len(engine.executor.ran)


class SmartShift(unittest.TestCase):
    """The mouse does not say when SmartShift lets the wheel go, so the engine works it out from the
    wheel's speed. The threshold is in quarter turns a second; 6 means 36 notches a second."""

    def setUp(self):
        self.clock = Clock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__)

    def test_ordinary_scrolling_changes_nothing(self):
        engine, played = make_engine(wheel={"threshold": 6})
        spin(engine, self.clock, 20, 10)
        spin(engine, self.clock, 30, 30)
        stop(engine, self.clock)
        self.assertEqual((modes(engine), played), ([], []))

    def test_long_spin_clicks_when_let_go_and_when_caught(self):
        engine, played = make_engine(wheel={"threshold": 6})
        spin(engine, self.clock, 60, 150)
        self.assertEqual(modes(engine), [("freespin", False)])
        stop(engine, self.clock)
        self.assertEqual(modes(engine), [("freespin", False), ("ratchet", False)])
        self.assertEqual(played, ["Subtle collision", "Whisper collision"])

    def test_quick_stroke_is_a_single_click(self):
        engine, played = make_engine(wheel={"threshold": 6})
        spin(engine, self.clock, 6, 60)
        stop(engine, self.clock)
        self.assertEqual(modes(engine), [("freespin", False), ("ratchet", True)])
        self.assertEqual(played, ["Subtle collision"])

    def test_higher_threshold_needs_a_faster_spin(self):
        engine, _ = make_engine(wheel={"threshold": 20})  # 120 notches a second
        spin(engine, self.clock, 30, 100)
        self.assertEqual(modes(engine), [])
        spin(engine, self.clock, 30, 125, notches=2)
        self.assertEqual(modes(engine), [("freespin", False)])

    def test_chosen_moments_only(self):
        engine, played = make_engine(wheel={"threshold": 6}, wheel_click={"moments": "ratchet"})
        spin(engine, self.clock, 6, 60)
        stop(engine, self.clock)
        self.assertEqual(played, ["Whisper collision"])
        engine, played = make_engine(wheel={"threshold": 6}, wheel_click={"moments": "free"})
        spin(engine, self.clock, 60, 150)
        stop(engine, self.clock)
        self.assertEqual(played, ["Subtle collision"])

    def test_sound_only_leaves_the_motor_alone_but_still_announces(self):
        engine, played = make_engine(wheel={"threshold": 6}, wheel_click={"mode": "sound"})
        spin(engine, self.clock, 60, 150)
        self.assertEqual((played, modes(engine)), ([], [("freespin", False)]))

    def test_reports_read_in_one_gulp_do_not_fake_speed(self):
        engine, _ = make_engine(wheel={"threshold": 6})
        for when in (0.0, 0.0001, 0.2, 0.2001):
            engine._on_spin(1, self.clock.now + when)
        self.assertEqual(modes(engine), [])

    def test_reversing_starts_the_measurement_again(self):
        engine, _ = make_engine(wheel={"threshold": 6})
        for when, notches in ((0.00, 1), (0.02, 1), (0.04, -1), (0.06, -1)):
            engine._on_spin(notches, self.clock.now + when)
        self.assertEqual(modes(engine), [])

    def test_nothing_without_smartshift(self):
        engine, _ = make_engine(wheel={"threshold": 6, "smartshift": False})
        spin(engine, self.clock, 60, 150)
        self.assertEqual(modes(engine), [])

    def test_mode_button(self):
        engine, played = make_engine()
        engine._on_ratchet(False)
        engine._on_ratchet(False)  # the mouse repeats itself: no second click
        engine._on_ratchet(True)
        self.assertEqual(modes(engine), [("freespin", False), ("ratchet", False)])
        self.assertEqual(played, ["Subtle collision", "Whisper collision"])

    def test_settings_being_applied_do_not_click(self):
        engine, played = make_engine()
        engine._wheel_quiet = self.clock.now + 1.0
        engine._on_ratchet(False)
        self.assertEqual((played, modes(engine)), ([], [("freespin", True)]))


class ThumbWheel(unittest.TestCase):
    def test_rolled_up_is_more_and_every_step_ticks(self):
        engine, played = make_engine(thumbwheel={"function": "volume"})
        with Clock() as clock:
            for _ in range(3):
                clock.now += 0.05
                engine._on_thumbwheel(-6)  # this mouse reports rolling up as negative
        self.assertEqual(names(engine.executor.ran), [["KEY_VOLUMEUP"]] * 3)
        self.assertEqual(played, ["Whisper collision"] * 3)

    def test_ticks_can_be_switched_off(self):
        engine, played = make_engine(thumbwheel={"function": "volume", "ticks": False})
        with Clock():
            engine._on_thumbwheel(6)
        self.assertEqual(names(engine.executor.ran), [["KEY_VOLUMEDOWN"]])
        self.assertEqual(played, [])

    def test_turns_a_dial_of_the_open_ring_instead(self):
        engine, _ = make_engine(thumbwheel={"function": "volume"})
        turned = []
        engine.listener.on_ring_turn = turned.append
        engine._ring_open = True
        with Clock():
            engine._on_thumbwheel(-6)
        self.assertEqual((turned, engine.executor.ran), ([1.0], []))

    def test_one_huge_thumb_report_steps_a_bounded_number_and_owes_nothing(self):
        for function in ("zoom", "volume", "brightness", "tabs", "desktops"):
            for rotation in (-32768, 32767):
                with self.subTest(function=function, rotation=rotation):
                    engine, _, sleeps = thumb_engine(self, function=function, speed=4.0)
                    engine._on_thumbwheel(rotation)
                    self.assertEqual(dispatched(engine, function), engine_module.THUMB_STEPS_MAX)
                    self.assertEqual(len(sleeps), 2 * engine_module.THUMB_STEPS_MAX if function == "zoom" else 0)
                    self.assertLess(abs(engine._thumb["acc"]), engine_module.THUMB_STEPS[function][0])
                    self.assertEqual(engine.vinput._holds.held(), set())
                    before = len(sleeps)
                    engine._handle(("trigger", {"type": "keys", "keys": ["KEY_F9"]}))
                    self.assertEqual(len(sleeps), before)  # what waited behind it runs next, no step of report left

    def test_ordinary_thumb_reports_step_as_before(self):
        cases = [("zoom", 1.0, [-6], 1, 120), ("zoom", 4.0, [-6], 4, 120), ("zoom", 4.0, [12], 8, -120),
                 ("volume", 4.0, [-12], 8, None), ("brightness", 1.0, [6], 1, None), ("tabs", 1.0, [-6, -6, -6], 1, None),
                 ("tabs", 4.0, [-12], 2, None), ("desktops", 4.0, [-12], 1, None)]
        for function, speed, reports, expect, delta in cases:
            with self.subTest(function=function, speed=speed, reports=reports):
                engine, clock, sleeps = thumb_engine(self, function=function, speed=speed)
                for rotation in reports:
                    clock.now += 0.05
                    engine._on_thumbwheel(rotation)
                if function == "zoom":
                    self.assertEqual(engine.vinput.sent, [(CTRL, True), ("scroll", delta), (CTRL, False)] * expect)
                    self.assertEqual(sleeps, [0.008] * (2 * expect))
                else:
                    forward = engine_module.THUMB_STEPS[function][1 if reports[0] < 0 else 2]
                    self.assertEqual(engine.executor.ran, [forward] * expect)

    def test_a_failed_thumb_step_owes_nothing(self):
        engine, clock, _ = thumb_engine(self, function="zoom", speed=1.0)
        engine.vinput.fail = 1
        with self.assertRaises(OSError):
            engine._on_thumbwheel(-60)  # 10 steps; the first scroll fails
        self.assertEqual(engine.vinput._holds.held(), set())
        clock.now += 0.05
        engine._on_thumbwheel(-6)
        self.assertEqual(engine.vinput.sent.count(("scroll", 120)), 1)
        engine, clock, _ = thumb_engine(self, function="volume", speed=1.0)
        engine.executor.fail = 1
        with self.assertRaises(OSError):
            engine._on_thumbwheel(-60)
        clock.now += 0.05
        engine._on_thumbwheel(-6)
        self.assertEqual(len(engine.executor.ran), 1)


class Constants(unittest.TestCase):
    def test_stop_is_noticed_before_a_new_measurement_would_start(self):
        self.assertGreater(engine_module.SPIN_STOP, engine_module.SPIN_GAP)


if __name__ == "__main__":
    unittest.main()
