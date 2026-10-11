import unittest

from tests.support import Clock, make_engine, names
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


class Constants(unittest.TestCase):
    def test_stop_is_noticed_before_a_new_measurement_would_start(self):
        self.assertGreater(engine_module.SPIN_STOP, engine_module.SPIN_GAP)


if __name__ == "__main__":
    unittest.main()
