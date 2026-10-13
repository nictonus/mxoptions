"""Shapes drawn with a gesture button held: reading the strokes of a movement, and what the engine does with them."""

import copy
import math
import random
import unittest

from tests.support import make_engine
from mxoptions import config, device, gestures

CLOSE = {"type": "keys", "keys": ["KEY_LEFTALT", "KEY_F4"]}
SHOT = {"type": "keys", "keys": ["KEY_SYSRQ"]}


def path(*legs, noise=0.5, step=0.8, seed=3):
    """A movement through ``legs`` (dx, dy in mm each), sampled as a hand would make it: in small steps, not quite straight."""
    rng = random.Random(seed)
    x = y = 0.0
    points = [(0.0, 0.0)]
    for dx, dy in legs:
        count = max(2, int(math.hypot(dx, dy) / step))
        for _ in range(count):
            x += dx / count
            y += dy / count
            points.append((x + rng.uniform(-noise, noise), y + rng.uniform(-noise, noise)))
    return points


def circle(clockwise=True, radius=15.0, turns=1.0):
    points = [(radius * math.sin(a), radius - radius * math.cos(a)) for a in (2 * math.pi * turns * i / 40 for i in range(41))]
    return points if clockwise else [(-x, y) for x, y in points]


class Reading(unittest.TestCase):
    def read(self, points):
        return gestures.shape(gestures.strokes(points))

    def test_corners(self):
        self.assertEqual(self.read(path((0, 25), (25, 0))), "down_right")
        self.assertEqual(self.read(path((-30, 0), (0, -20))), "left_up")
        self.assertEqual(self.read(path((0, 20), (4, 4), (20, 0))), "down_right")  # with the corner cut round

    def test_there_and_back(self):
        self.assertEqual(self.read(path((0, -30), (2, 28))), "up_down")
        self.assertEqual(self.read(path((30, 0), (-28, 1))), "right_left")

    def test_circles_wherever_they_start_and_however_far_they_go(self):
        self.assertEqual(self.read(circle()), "circle_cw")
        self.assertEqual(self.read(circle(clockwise=False)), "circle_ccw")
        self.assertEqual(self.read(circle(turns=1.6)), "circle_cw")
        self.assertEqual(self.read(circle(turns=0.8)), "circle_cw")

    def test_shakes(self):
        self.assertEqual(self.read(path((25, 0), (-25, 1), (25, 0))), "zigzag_h")
        self.assertEqual(self.read(path((0, -20), (0, 20), (1, -20), (0, 20))), "zigzag_v")

    def test_a_plain_swipe_and_a_wobble_are_no_shapes(self):
        self.assertEqual(gestures.strokes(path((40, 3))), "R")
        self.assertEqual(gestures.strokes(path((3, 0), (0, 3))), "")
        self.assertEqual(gestures.strokes(path((30, 0), (0, 3), (20, 0))), "R")  # a bump on the way does not split it
        self.assertEqual(gestures.strokes([]), "")

    def test_something_else_is_nothing(self):
        self.assertIsNone(gestures.shape("RDR"))   # a step, not in the list
        self.assertIsNone(gestures.shape("RUDL"))

    def test_every_shape_has_a_picture_inside_the_square(self):
        for name in gestures.SHAPES:
            points = gestures.outline(name)
            self.assertGreaterEqual(len(points), 3, name)
            for x, y in points:
                self.assertTrue(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0, name)


class Drawing(unittest.TestCase):
    """The engine: the button goes down, the mouse moves, the button comes up."""

    def engine(self, shapes=None, **settings):
        made, _played = make_engine(gestures=dict({"trigger": "auto"}, **settings))
        action = config.default_gestures()
        if shapes:
            action["shapes"] = shapes
        made.cfg["buttons"]["gesture"] = action
        return made

    def draw(self, engine, points):
        per_mm = engine._counts_per_mm()
        engine._button_down(device.CID_GESTURE)
        last = (0.0, 0.0)
        for point in points[1:]:
            engine._on_raw_xy(round((point[0] - last[0]) * per_mm), round((point[1] - last[1]) * per_mm))
            last = point
        engine._button_up(device.CID_GESTURE)
        return engine.executor.ran

    def test_a_shape_runs_its_action(self):
        self.assertEqual(self.draw(self.engine({"down_right": CLOSE}), path((0, 25), (25, 0))), [CLOSE])
        self.assertEqual(self.draw(self.engine({"circle_cw": SHOT, "down_right": CLOSE}), circle()), [SHOT])

    def test_plain_swipes_still_work_on_a_button_with_shapes(self):
        engine = self.engine({"down_right": CLOSE})
        ran = self.draw(engine, path((40, 2)))
        self.assertEqual(ran, [engine.cfg["buttons"]["gesture"]["right"]])

    def test_a_long_first_stroke_does_not_fire_as_a_swipe_half_way(self):
        engine = self.engine({"down_right": CLOSE})
        ran = self.draw(engine, path((0, 40), (30, 0)))  # far beyond the distance at which "at once" would fire
        self.assertEqual(ran, [CLOSE])

    def test_a_shape_with_no_action_does_nothing(self):
        engine = self.engine({"down_right": CLOSE})
        self.assertEqual(self.draw(engine, path((0, -25), (25, 0))), [])

    def test_without_shapes_everything_is_as_before(self):
        engine = self.engine()
        ran = self.draw(engine, path((0, 40), (30, 0)))  # fires "down" at once, half-way through
        self.assertEqual(ran, [engine.cfg["buttons"]["gesture"]["down"]])

    def test_other_programs_hear_the_shape(self):
        engine = self.engine({"up_down": CLOSE})
        self.draw(engine, path((0, -30), (1, 29)))
        self.assertIn(("gesture", "up_down"), engine.listener.events)


class Settings(unittest.TestCase):
    def test_shapes_survive_cleaning_and_nonsense_does_not(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["buttons"]["gesture"]["shapes"] = {"down_right": CLOSE, "pentagram": CLOSE, "up_left": {"type": "none"}, "left_up": "x"}
        self.assertEqual(config.clean(cfg)["buttons"]["gesture"]["shapes"], {"down_right": CLOSE})
        cfg["buttons"]["gesture"]["shapes"] = "x"
        self.assertNotIn("shapes", config.clean(cfg)["buttons"]["gesture"])


if __name__ == "__main__":
    unittest.main()
