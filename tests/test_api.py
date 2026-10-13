import io
import unittest
import wave

import tests.support  # noqa: F401
from mxoptions import actions, api, device, environment
from mxoptions.ui import sounds


class Patterns(unittest.TestCase):
    def test_names_and_numbers(self):
        self.assertEqual(api.find_pattern("knock"), 0x0C)
        self.assertEqual(api.find_pattern("Sharp Collision"), 0x02)
        self.assertEqual(api.find_pattern("whisper_collision"), 0x1B)
        self.assertEqual(api.find_pattern("0x1b"), 0x1B)
        self.assertEqual(api.find_pattern("27"), 0x1B)
        self.assertIsNone(api.find_pattern("bogus"))
        self.assertIsNone(api.find_pattern("99"))

    def test_every_pattern_has_a_distinct_bus_name(self):
        names = [api.pattern_name(waveform) for waveform in device.WAVEFORMS]
        self.assertEqual(len(set(names)), len(names))
        for name in names:
            self.assertEqual(name, name.lower())
            self.assertNotIn(" ", name)


class Actions(unittest.TestCase):
    def test_catalogue_id_or_json(self):
        self.assertEqual(api.parse_action("copy"), actions.PRESET_BY_ID["copy"].action)
        self.assertEqual(api.parse_action('{"type": "keys", "keys": ["KEY_F5"]}'), {"type": "keys", "keys": ["KEY_F5"]})
        self.assertEqual(api.parse_action(' {"type": "hook", "name": "x"} ')["name"], "x")

    def test_what_cannot_be_triggered(self):
        for text in ("gestures", "ring", "default", "dpi_shift", "no-such-action", "{broken", '["list"]', '{"type": "warp"}'):
            with self.subTest(text=text):
                self.assertIsNone(api.parse_action(text))

    def test_what_the_desktop_does_in_its_own_way_goes_by_name(self):
        self.assertEqual(api.parse_action("overview"), {"type": "desktop", "do": "overview"})
        self.assertEqual(api.parse_action('{"type": "desktop", "do": "win_close"}'), {"type": "desktop", "do": "win_close"})
        self.assertTrue(environment.available(api.parse_action("overview"), environment.KDE))
        self.assertFalse(environment.available(api.parse_action("overview"), environment.SWAY))
        self.assertFalse(environment.available(api.parse_action('{"type": "desktop", "do": "no_such_thing"}'), environment.KDE))

    def test_catalogue_ids_are_unique(self):
        ids = [preset.id for preset in actions.PRESETS]
        self.assertEqual(len(set(ids)), len(ids))


class Clicks(unittest.TestCase):
    def read(self, data):
        with wave.open(io.BytesIO(data)) as f:
            return f.getframerate(), f.getnchannels(), f.getsampwidth(), f.getnframes()

    def test_every_style_is_a_short_mono_wav(self):
        for style in sounds.STYLES:
            for kind in sounds.KINDS:
                with self.subTest(style=style, kind=kind):
                    rate, channels, width, frames = self.read(sounds.synthesize(style, kind))
                    self.assertEqual((rate, channels, width), (sounds.RATE, 1, 2))
                    self.assertLess(frames / rate, 0.3)

    def test_the_tick_is_the_shortest_and_length_stretches_it(self):
        def length(style, stretch=1.0):
            return self.read(sounds.synthesize(style, "freespin", 1.0, stretch))[3]

        self.assertLess(length("tick"), length("clack"))
        self.assertLess(length("clack"), length("relay"))
        self.assertLess(length("tick", 0.5), length("tick"))
        self.assertLess(length("tick"), length("tick", 2.5))

    def test_volume_slider_is_gentle_at_the_quiet_end(self):
        self.assertEqual(sounds.loudness(0.0), 0.0)
        self.assertEqual(sounds.loudness(1.0), 1.0)
        self.assertAlmostEqual(sounds.loudness(0.3), 0.09)
        self.assertEqual(sounds.loudness(5), 1.0)


if __name__ == "__main__":
    unittest.main()
