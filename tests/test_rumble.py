"""Rumble from games: what a game stores and plays on the virtual gamepad, and what the mouse plays for it.
Also what makes other mice than the MX Master 4 work: their battery, and which picture they get."""

import struct
import sys
import unittest

if sys.platform == "win32":
    raise unittest.SkipTest("the virtual gamepad is made with uinput, which is Linux's")

from evdev import ecodes as e  # noqa: E402

from tests.support import make_engine  # noqa: E402
from mxoptions import device, rumble  # noqa: E402


def effect(kind, effect_id=0, length=0, strong=0, weak=0, magnitude=0):
    """A struct ff_effect as a game would upload it."""
    data = bytearray(48)
    struct.pack_into("Hh", data, 0, kind, effect_id)
    struct.pack_into("H", data, 10, length)
    if kind == e.FF_RUMBLE:
        struct.pack_into("HH", data, 16, strong, weak)
    elif kind == e.FF_PERIODIC:
        struct.pack_into("HHh", data, 16, e.FF_SINE, 50, magnitude)
    elif kind == e.FF_CONSTANT:
        struct.pack_into("h", data, 16, magnitude)
    return bytes(data)


class Strength(unittest.TestCase):
    def test_the_large_motor_counts_fully_the_small_one_less(self):
        self.assertAlmostEqual(rumble.strength_of(effect(e.FF_RUMBLE, strong=0xFFFF)), 1.0)
        self.assertAlmostEqual(rumble.strength_of(effect(e.FF_RUMBLE, weak=0xFFFF)), rumble.WEAK_SHARE)
        self.assertAlmostEqual(rumble.strength_of(effect(e.FF_RUMBLE, strong=0x8000, weak=0x2000)), 0x8000 / 65535, places=4)

    def test_other_kinds_of_effect(self):
        self.assertAlmostEqual(rumble.strength_of(effect(e.FF_PERIODIC, magnitude=-16384)), 0.5, places=3)
        self.assertAlmostEqual(rumble.strength_of(effect(e.FF_CONSTANT, magnitude=32767)), 1.0)
        self.assertEqual(rumble.strength_of(effect(e.FF_SPRING)), 0.0)


class Playing(unittest.TestCase):
    def setUp(self):
        self.effects = rumble.Effects()

    def test_an_effect_plays_until_it_is_stopped(self):
        self.effects.store(0, effect(e.FF_RUMBLE, strong=0x8000))
        self.assertEqual(self.effects.level(0.0), 0.0)
        self.effects.play(0, 1, now=1.0)
        self.assertAlmostEqual(self.effects.level(100.0), 0.5, places=3)
        self.assertIsNone(self.effects.next_end())
        self.effects.play(0, 0, now=101.0)
        self.assertEqual(self.effects.level(101.0), 0.0)

    def test_a_timed_effect_ends_by_itself_and_repeats_as_often_as_asked(self):
        self.effects.store(3, effect(e.FF_RUMBLE, effect_id=3, length=400, strong=0xFFFF))
        self.effects.play(3, 2, now=10.0)
        self.assertAlmostEqual(self.effects.next_end(), 10.8)
        self.assertEqual(self.effects.level(10.7), 1.0)
        self.assertEqual(self.effects.level(10.8), 0.0)

    def test_the_strongest_of_several_counts_and_the_gain_scales_it(self):
        self.effects.store(0, effect(e.FF_RUMBLE, strong=0x4000))
        self.effects.store(1, effect(e.FF_RUMBLE, effect_id=1, strong=0xC000))
        self.effects.play(0, 1, 0.0)
        self.effects.play(1, 1, 0.0)
        self.assertAlmostEqual(self.effects.level(0.1), 0xC000 / 65535, places=4)
        self.effects.gain = 0.5
        self.assertAlmostEqual(self.effects.level(0.1), 0xC000 / 65535 / 2, places=4)
        self.effects.erase(1)
        self.assertAlmostEqual(self.effects.level(0.1), 0x4000 / 65535 / 2, places=4)

    def test_playing_what_was_never_stored_is_nothing(self):
        self.effects.play(7, 1, 0.0)
        self.assertEqual(self.effects.level(0.1), 0.0)


class OnTheMouse(unittest.TestCase):
    def setUp(self):
        self.engine, self.played = make_engine(rumble={"enabled": True, "interval": 100})

    def test_weak_medium_and_strong_rumble_get_their_patterns(self):
        cfg = self.engine.cfg["rumble"]
        for level, name in ((0.2, "light"), (0.5, "medium"), (0.9, "strong")):
            self.engine._rumble["next"] = 0.0
            self.engine._rumble_level(level)
            self.assertEqual(self.played[-1], device.WAVEFORMS[cfg[name]], name)

    def test_a_rumble_that_lasts_is_a_row_of_pulses(self):
        self.engine._rumble_level(0.9)
        self.assertEqual(len(self.played), 1)
        self.engine._rumble_level(0.9)                 # the game says the same again at once: no extra pulse
        self.assertEqual(len(self.played), 1)
        self.engine._rumble["next"] -= 0.2             # the interval has passed
        self.engine._tick()
        self.assertEqual(len(self.played), 2)
        self.engine._rumble_level(0.0)
        self.engine._rumble["next"] -= 0.2
        self.engine._tick()
        self.assertEqual(len(self.played), 2)

    def test_sensitivity_moves_the_thresholds(self):
        self.engine.cfg["rumble"]["gain"] = 2.0
        self.engine._rumble_level(0.4)                 # counts as 0.8
        self.assertEqual(self.played[-1], device.WAVEFORMS[self.engine.cfg["rumble"]["strong"]])

    def test_a_strength_with_no_pattern_stays_silent(self):
        self.engine.cfg["rumble"]["light"] = None
        self.engine._rumble_level(0.1)
        self.assertEqual(self.played, [])


class OtherMice(unittest.TestCase):
    def test_which_picture_fits(self):
        self.assertEqual(device.family("MX Master 4"), "mx4")
        self.assertEqual(device.family(""), "mx4")  # nothing connected yet: the usual picture
        for name in ("MX Master 3", "MX Master 3S", "Wireless Mouse MX Master", "MX Master 2S"):
            self.assertEqual(device.family(name), "master", name)
        for name in ("Signature M650 L", "MX Anywhere 3", "MX Vertical"):
            self.assertEqual(device.family(name), "other", name)

    def test_the_battery_of_older_mice(self):
        self.assertEqual(device.battery_status(bytes([80, 50, 0])), (80, device.BATTERY_DISCHARGING))
        self.assertEqual(device.battery_status(bytes([35, 20, 1])), (35, device.BATTERY_CHARGING))
        self.assertEqual(device.battery_status(bytes([100, 90, 3])), (100, device.BATTERY_FULL))
        self.assertEqual(device.battery_status(bytes([10, 5, 6])), (10, device.BATTERY_DISCHARGING))  # a fault is not "charging"


class Returning(unittest.TestCase):
    """A mouse that comes back over Bluetooth is not asked for everything about itself again."""

    class Transport:
        def __init__(self, name=b"MX Master 4", count=3):
            self.name, self.count, self.asked = name, count, 0

        def request(self, devidx, feature, function, params=b"", **_kwargs):
            self.asked += 1
            if feature == 0:                       # root: where is the feature set?
                return bytes([1]) + bytes(15)
            if feature == 1:                       # feature set: how many features?
                return bytes([self.count]) + bytes(15)
            if function == 0:                      # device name: its length
                return bytes([len(self.name)]) + bytes(15)
            return self.name[params[0]:params[0] + 16].ljust(16, b"\0")

    def known(self):
        mouse = device.Mouse(self.Transport(), 0xFF)
        mouse.features = {device.ROOT: 0, device.FEATURE_SET: 1, device.DEVICE_NAME: 2, device.HAPTIC: 3}
        mouse.name, mouse.haptic_waveforms, mouse.host_count = "MX Master 4", [0x1B, 0x04], 3
        return mouse

    def test_the_same_mouse_is_taken_over_with_a_few_questions(self):
        transport = self.Transport()
        mouse = device.Mouse(transport, 0xFF)
        self.assertTrue(mouse.adopt(self.known()))
        self.assertEqual((mouse.name, mouse.host_count, mouse.haptic_waveforms), ("MX Master 4", 3, [0x1B, 0x04]))
        self.assertTrue(mouse.has(device.HAPTIC))
        self.assertLessEqual(transport.asked, 5)

    def test_another_mouse_is_asked_properly(self):
        self.assertFalse(device.Mouse(self.Transport(name=b"MX Anywhere 3"), 0xFF).adopt(self.known()))
        self.assertFalse(device.Mouse(self.Transport(count=7), 0xFF).adopt(self.known()))  # other firmware, other features


class TrayIcon(unittest.TestCase):
    CHARGING = (device.BATTERY_CHARGING, device.BATTERY_ALMOST_FULL, device.BATTERY_SLOW_CHARGE)

    def key(self, style, connected=True, battery=None):
        from mxoptions.ui import tray_icon
        return tray_icon.key_for(style, {"connected": connected, "battery": battery}, self.CHARGING)

    def test_a_plain_style_tells_three_things_apart(self):
        self.assertEqual(self.key("solid", battery=(80, 0)), "solid")
        self.assertEqual(self.key("solid", connected=False, battery=(80, 0)), "solid-off")
        self.assertEqual(self.key("solid", battery=(12, 0)), "solid-low")
        self.assertEqual(self.key("solid", battery=(12, device.BATTERY_CHARGING)), "solid")  # low, but on the charger

    def test_the_battery_style_shows_the_level(self):
        for percent, step in ((100, 5), (81, 5), (80, 4), (41, 3), (20, 1), (16, 1)):
            self.assertEqual(self.key("battery", battery=(percent, 0)), f"battery-{step}", percent)
        self.assertEqual(self.key("battery", battery=(10, 0)), "battery-low")
        self.assertEqual(self.key("battery", connected=False), "battery-off")
        self.assertEqual(self.key("battery", battery=None), "outline")  # a mouse that does not tell its level

    def test_the_app_icon_shows_no_state(self):
        self.assertIsNone(self.key("logo", battery=(5, 0)))

    def test_every_drawing_is_written_once_and_stale_ones_are_removed(self):
        import os
        import tempfile
        import xml.dom.minidom
        from mxoptions.ui import tray_icon
        with tempfile.TemporaryDirectory() as folder:
            open(os.path.join(folder, tray_icon.PREFIX + "from-an-older-version.svg"), "w").close()
            open(os.path.join(folder, "somebody-elses.svg"), "w").close()
            self.assertTrue(tray_icon.install(folder))
            names = sorted(os.listdir(folder))
            self.assertEqual(len(names), len(tray_icon.keys()) + 1)
            self.assertIn("somebody-elses.svg", names)
            for name in names:
                if name.startswith(tray_icon.PREFIX):
                    xml.dom.minidom.parse(os.path.join(folder, name))
            self.assertFalse(tray_icon.install(folder))  # nothing to do the second time


if __name__ == "__main__":
    unittest.main()
