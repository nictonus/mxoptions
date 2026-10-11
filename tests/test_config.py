import copy
import json
import os
import tempfile
import unittest

import tests.support  # noqa: F401  (puts the project on the path)
from mxoptions import actions, config


class Defaults(unittest.TestCase):
    def test_defaults_are_valid_as_they_stand(self):
        self.assertEqual(config.clean(copy.deepcopy(config.DEFAULTS)), config.DEFAULTS)

    def test_every_ring_preset_is_valid_and_within_limits(self):
        def check(bubbles, depth=1):
            self.assertLessEqual(len(bubbles), config.MAX_BUBBLES)
            self.assertLessEqual(depth, config.MAX_RING_DEPTH)
            for bubble in bubbles:
                if "children" in bubble:
                    check(bubble["children"], depth + 1)
                elif "scroll" in bubble:
                    self.assertIn(bubble["scroll"], actions.SCROLLS)
                else:
                    self.assertIn("type", bubble["action"])

        for name, _label, build in config.RING_PRESETS:
            with self.subTest(preset=name):
                bubbles = build()
                check(bubbles)
                self.assertEqual(config.clean_bubbles(copy.deepcopy(bubbles)), bubbles)


class Cleaning(unittest.TestCase):
    def test_nonsense_falls_back_to_defaults(self):
        cfg = config.clean({
            "pointer": {"dpi": "fast"}, "wheel": {"mode": "sideways", "threshold": 900},
            "gestures": {"trigger": "never", "dead": 99, "cues": {"swipe": 999, "tap": None}},
            "wheel_click": {"mode": "x", "volume": 7, "pitch": 0.1, "free": 12345},
            "app": {"model": "hologram"}, "buttons": {"back": "left"},
        })
        self.assertEqual(cfg["pointer"]["dpi"], config.DEFAULTS["pointer"]["dpi"])
        self.assertEqual((cfg["wheel"]["mode"], cfg["wheel"]["threshold"]), ("ratchet", 50))
        self.assertEqual((cfg["gestures"]["trigger"], cfg["gestures"]["dead"]), ("auto", 30))
        self.assertEqual(cfg["gestures"]["cues"]["swipe"], config.DEFAULTS["gestures"]["cues"]["swipe"])
        self.assertIsNone(cfg["gestures"]["cues"]["tap"])
        click = cfg["wheel_click"]
        self.assertEqual((click["mode"], click["volume"], click["pitch"], click["free"]), ("both", 1.0, 0.6, 0x04))
        self.assertEqual(cfg["app"]["model"], "photo")
        self.assertEqual(cfg["buttons"]["back"], {"type": "default"})

    def test_a_ring_with_junk_in_it(self):
        bubbles = config.clean_bubbles([
            {"label": "ok", "action": {"type": "none"}}, "junk", {"scroll": "no-such-dial"},
            {"children": [{"scroll": "volume"}] * 20},
        ])
        self.assertEqual(len(bubbles), 3)
        self.assertEqual(bubbles[1]["action"], {"type": "none"})
        self.assertEqual(len(bubbles[2]["children"]), config.MAX_BUBBLES)


class Migration(unittest.TestCase):
    def load(self, stored):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "config.json")
            with open(path, "w") as f:
                json.dump(stored, f)
            real = config.CONFIG_PATH, config.CONFIG_DIR
            config.CONFIG_PATH, config.CONFIG_DIR = path, folder
            try:
                loaded = config.load()
                with open(path) as f:
                    return loaded, json.load(f)
            finally:
                config.CONFIG_PATH, config.CONFIG_DIR = real

    def test_version_3_gesture_travel_becomes_millimetres(self):
        loaded, saved = self.load({"version": 3, "app": {"gesture_distance": 2.0, "model": "studio"}})
        self.assertEqual((loaded["gestures"]["min_mm"], loaded["gestures"]["far_mm"]), (5.0, 32.0))
        self.assertEqual(loaded["app"]["model"], "photo")
        self.assertNotIn("gesture_distance", loaded["app"])
        self.assertEqual(saved["version"], config.DEFAULTS["version"])

    def test_version_4_wheel_click_moves_to_the_single_tick(self):
        loaded, _saved = self.load({"version": 4, "wheel_click": {
            "mode": "sound", "free": 0, "ratchet": 1, "sound": "relay", "file": "", "volume": 1.0}})
        click = loaded["wheel_click"]
        self.assertEqual((click["mode"], click["sound"], click["volume"], click["free"]), ("sound", "tick", 0.3, 0x04))

    def test_current_settings_are_left_alone(self):
        stored = copy.deepcopy(config.DEFAULTS)
        stored["wheel_click"]["sound"] = "relay"
        loaded, _saved = self.load(stored)
        self.assertEqual(loaded["wheel_click"]["sound"], "relay")


if __name__ == "__main__":
    unittest.main()
