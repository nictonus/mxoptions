import unittest

from tests.support import Clock, make_engine, names
from mxoptions import device, gestures

GESTURE = device.CID_GESTURE


def swipe(engine, moves, cid=GESTURE):
    """Hold the gesture button, feed the raw movement, let go. Returns how the gesture ended."""
    engine._on_buttons({cid})
    for dx, dy in moves:
        engine._on_raw_xy(dx, dy)
    held = list(engine.executor.ran)
    engine._on_buttons(set())
    return engine.listener.gestures[-1]["result"], held


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


if __name__ == "__main__":
    unittest.main()
