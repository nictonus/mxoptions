"""Rumble from games: what a game stores and plays on the virtual gamepad, and what the mouse plays for it.
Also what makes other mice than the MX Master 4 work: their battery, and which picture they get."""

import copy
import struct
import sys
import types
import unittest
from unittest import mock

if sys.platform == "win32":
    raise unittest.SkipTest("the virtual gamepad is made with uinput, which is Linux's")

from evdev import ecodes as e  # noqa: E402

from tests.support import Actions, FakeInput, Mouse, Recorder, make_engine  # noqa: E402
from mxoptions import config, device, engine, flow_service, hidpp, keyboard, rumble  # noqa: E402


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


class Wire:
    """Where a synthetic device.Mouse's requests go: each kept as (feature id, function) and answered with zeros;
    the features in ``refuse`` answer with an error."""

    is_receiver = True

    def __init__(self, refuse=()):
        self.sent, self.refuse, self.features = [], set(refuse), {}

    def request(self, devidx, index, function, params=b"", **_kwargs):
        feature = self.features[index]
        self.sent.append((feature, function))
        if feature in self.refuse:
            raise hidpp.HidppError("refused")
        return bytes(16)

    def played(self):
        return [sent for sent in self.sent if sent == (device.HAPTIC, 4)]


def motor(refuse=()):
    """A made-up mouse with a haptic motor, as far as the engine talks to it, over a Wire."""
    wire = Wire(refuse)
    made = device.Mouse(wire, 1)
    made.features = {feature: index for index, feature in
                     enumerate((device.ROOT, device.HAPTIC, device.UNIFIED_BATTERY, device.ADJUSTABLE_DPI,
                                device.THUMB_WHEEL))}
    made.index_to_feature = wire.features = {index: feature for feature, index in made.features.items()}
    made.name, made.haptic_waveforms, made.dpi_range = "Synthetic mouse", list(device.WAVEFORMS), Mouse.dpi_range
    made.thumbwheel = dict(Mouse.thumbwheel)
    return made


def saver(battery, saving=True):
    """An engine with the engine's own vibration path to a motor() whose last reading is ``battery``."""
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["haptics"]["battery_saving"] = saving
    cfg["thumbwheel"].update(function="tabs", ticks=True)
    made = engine.Engine(cfg, Recorder())
    made.executor = Actions()
    made.mouse, made.online, made._battery = motor(), True, battery
    return made


def drain(made):
    while not made._queue.empty():
        made._handle(made._queue.get_nowait())


def mouse_macro(made):
    with mock.patch.object(engine.vinput, "create", FakeInput), mock.patch.object(made.keyboard, "start"), \
            mock.patch.object(made.keyboard, "shutdown"):
        mouse, made.mouse, made._running = made.mouse, None, False
        made._run()  # how the engine makes the executor of the mouse's buttons
        made.mouse = mouse
    made.executor.macros._step({"do": "haptic", "waveform": 7})
    drain(made)


def key_macro(made):
    with mock.patch.object(keyboard.threading, "Thread"):
        made.keyboard.start()  # how the engine makes the executor of the keyboard's keys
    made.keyboard.executor.macros._step({"do": "haptic", "waveform": 7})
    drain(made)


def flow_cue(made):
    store = types.SimpleNamespace(cfg=made.cfg, state={"connected": True}, engine=made)
    flow_service.FlowService._cue(types.SimpleNamespace(store=store), "arrived", None)
    drain(made)


def screen_cue(made):
    made.cfg["haptics"]["screens"] = True
    made.screen_cue()
    drain(made)


def gesture_cue(made):
    made._gesture_cue(next(name for name, waveform in made.cfg["gestures"]["cues"].items() if waveform is not None))


# Every way something asks the mouse to vibrate, through its real way in
PRODUCERS = {
    "a pattern tried on a page or asked for by another program": lambda made: (made.play_haptic(0x0C), drain(made)),
    "a cue of the Actions Ring": lambda made: (made.feedback(0x0C), drain(made)),
    "a vibration step of a mouse macro": mouse_macro,
    "a vibration step of a key macro": key_macro,
    "a cue of Flow": flow_cue,
    "a cue of a gesture": gesture_cue,
    "the pointer going onto another screen": screen_cue,
    "the pointer speed switched by a button": lambda made: made._device_action({"type": "dpi_cycle"}, "press"),
    "ticks of the thumb wheel": lambda made: made._on_thumbwheel(-600),
    "the wheel letting go": lambda made: made._wheel_changed("freespin", "spun"),
    "a pulse of game rumble": lambda made: made._rumble_pulse(0.0),
    "the rumble test": lambda made: made._handle(("rumble", "test", 1.0)),
}


class BatterySaving(unittest.TestCase):
    """With battery saving on, a mouse at 10 % or less is sent no vibration from anywhere; above that, or with it off,
    everything vibrates as before. Nothing is sent to the mouse for it."""

    def played(self, battery, saving=True, producers=PRODUCERS):
        counts = {}
        for name, produce in producers.items():
            made = saver(battery, saving)
            produce(made)
            counts[name] = len(made.mouse.transport.played())
        return counts

    def test_from_anywhere_nothing_vibrates_at_10_percent_and_all_as_before_above(self):
        before = self.played((10, device.BATTERY_DISCHARGING), saving=False)
        self.assertTrue(all(before.values()), before)  # each of them vibrates the mouse
        self.assertEqual(self.played((10, device.BATTERY_DISCHARGING)), dict.fromkeys(PRODUCERS, 0))
        self.assertEqual(self.played((1, device.BATTERY_DISCHARGING)), dict.fromkeys(PRODUCERS, 0))
        self.assertEqual(self.played((11, device.BATTERY_DISCHARGING)), before)
        self.assertEqual(self.played(None), before)

    def test_which_readings_keep_it_still(self):
        for battery in ((1, 0), (10, 0), (10, 1), (10, 3), [10, 0], (5, device.BATTERY_SLOW_CHARGE)):
            self.assertTrue(device.saving_battery(True, battery), battery)
        for battery in ((11, 0), (0, 0), (100, 0), None, (10,), (10, 0, 0), ("10", 0), (10.0, 0), (True, 0), True, 10):
            self.assertFalse(device.saving_battery(True, battery), battery)
        for enabled in (False, 1, None, "true"):
            self.assertFalse(device.saving_battery(enabled, (5, 0)), enabled)
        self.assertEqual(device.HAPTIC_SAVING, 10)

    def test_charging_alone_does_not_end_it_a_level_above_10_percent_does(self):
        self.assertEqual(self.played((8, device.BATTERY_CHARGING)), dict.fromkeys(PRODUCERS, 0))
        self.assertEqual(self.played((11, device.BATTERY_CHARGING)), self.played((11, device.BATTERY_CHARGING), False))

    def test_it_ends_and_comes_back_with_what_the_mouse_reports(self):
        made = saver((10, device.BATTERY_DISCHARGING))
        wire, index = made.mouse.transport, made.mouse.features[device.UNIFIED_BATTERY]
        made.play_haptic(0x0C)
        drain(made)
        self.assertEqual(wire.played(), [])
        made._on_notification(wire, 1, index, 0x00, bytes([11, 2, device.BATTERY_CHARGING, 0]))
        made.play_haptic(0x0C)
        drain(made)
        self.assertEqual(len(wire.played()), 1)
        made._on_notification(wire, 1, index, 0x00, bytes([9, 2, device.BATTERY_DISCHARGING, 0]))
        made.play_haptic(0x0C)
        drain(made)
        self.assertEqual(len(wire.played()), 1)

    def test_switched_off_and_on_again_at_once(self):
        made = saver((5, device.BATTERY_DISCHARGING), saving=False)
        wire = made.mouse.transport
        for saving, played in ((False, 1), (True, 1), (False, 2)):
            cfg = copy.deepcopy(made.cfg)
            cfg["haptics"]["battery_saving"] = saving
            made._apply_config(cfg)
            made.play_haptic(0x0C)
            drain(made)
            self.assertEqual(len(wire.played()), played, saving)

    def test_without_a_mouse_that_can_vibrate_nothing_changes(self):
        for saving in (True, False):
            for mouse, online, waveforms in ((None, False, None), (motor(), False, None), (motor(), True, [])):
                made = saver((5, 0), saving)
                made.mouse, made.online = mouse, online
                if waveforms is not None:
                    mouse.haptic_waveforms = waveforms
                made.play_haptic(0x0C)
                drain(made)  # nothing sent, nothing raised
                self.assertEqual(mouse.transport.played() if mouse else [], [])

    def test_a_new_mouse_is_not_kept_still_by_the_battery_of_the_one_before(self):
        made = saver((5, device.BATTERY_DISCHARGING))
        made.mouse, made.online = None, False
        found = motor(refuse={device.UNIFIED_BATTERY})  # it does not say how full it is
        with mock.patch.object(hidpp, "find_hidpp_nodes", lambda: [("/dev/synthetic0", True)]), \
                mock.patch.object(made, "_transport", lambda path, is_receiver: found.transport), \
                mock.patch.object(device, "find_mouse", lambda *args, **kwargs: found), \
                mock.patch.object(made.keyboard, "skip"), mock.patch.object(made.keyboard, "after_scan"):
            made._scan()
        self.assertIs(made.mouse, found)
        self.assertIn((device.UNIFIED_BATTERY, 1), found.transport.sent)  # asked, and it failed
        self.assertIsNone(made._battery)
        made.play_haptic(0x0C)
        drain(made)
        self.assertEqual(len(found.transport.played()), 1)

    def test_switching_it_sends_the_mouse_nothing_other_settings_as_before(self):
        made = saver((50, device.BATTERY_DISCHARGING))
        wire = made.mouse.transport
        for saving in (False, True, False):
            cfg = copy.deepcopy(made.cfg)
            cfg["haptics"]["battery_saving"] = saving
            made._apply_config(cfg)
            self.assertEqual(made.cfg["haptics"]["battery_saving"], saving)
        self.assertEqual(wire.sent, [])
        self.assertIs(made.cfg["haptics"]["enabled"], True)
        cfg = copy.deepcopy(made.cfg)
        cfg["haptics"]["level"] = 30
        made._apply_config(cfg)
        self.assertEqual(wire.sent, [(device.HAPTIC, 2)])

    def test_kept_still_means_nothing_at_all_goes_to_the_mouse(self):
        both = {name: PRODUCERS[name] for name in ("a pattern tried on a page or asked for by another program", "the rumble test")}
        for saving, sent in ((True, False), (False, True)):
            for name, produce in both.items():
                made = saver((5, device.BATTERY_DISCHARGING), saving)
                produce(made)
                self.assertEqual(bool(made.mouse.transport.sent), sent, (name, saving))


class SwitchingScreens(unittest.TestCase):
    """haptics.screens: a subtle tick through the engine's one way to the motor, decided when it is played."""

    def made(self, screens=True, enabled=True):
        made = saver((50, device.BATTERY_DISCHARGING))
        made.cfg["haptics"].update(screens=screens, enabled=enabled)
        return made

    def test_a_tick_while_it_is_on(self):
        for screens, enabled, ticks in ((True, True, 1), (False, True, 0), (True, False, 0), (False, False, 0)):
            with self.subTest(screens=screens, enabled=enabled):
                made = self.made(screens, enabled)
                with mock.patch.object(made.mouse, "play_haptic", wraps=made.mouse.play_haptic) as played:
                    made.screen_cue()
                    drain(made)
                self.assertEqual(played.call_args_list, [mock.call(device.WAVE_SUBTLE, True)] * ticks)
                self.assertEqual(len(made.mouse.transport.played()), ticks)

    def test_switched_off_before_it_is_played_nothing_plays(self):
        for key in ("screens", "enabled"):
            with self.subTest(key=key):
                made = self.made()
                made.screen_cue()
                cfg = copy.deepcopy(made.cfg)
                cfg["haptics"][key] = False
                made._apply_config(cfg)
                drain(made)
                self.assertEqual(made.mouse.transport.played(), [])

    def test_switching_it_sends_the_mouse_nothing(self):
        made = self.made(screens=False)
        wire = made.mouse.transport
        for screens in (True, False, True):
            cfg = copy.deepcopy(made.cfg)
            cfg["haptics"]["screens"] = screens
            made._apply_config(cfg)
            self.assertIs(made.cfg["haptics"]["screens"], screens)
        self.assertEqual(wire.sent, [])
        cfg = copy.deepcopy(made.cfg)
        cfg["haptics"]["level"] = 30
        made._apply_config(cfg)
        self.assertEqual(wire.sent, [(device.HAPTIC, 2)])

    def test_without_a_mouse_that_can_vibrate_nothing_is_sent(self):
        for mouse, online, waveforms in ((None, False, None), (motor(), False, None), (motor(), True, [])):
            made = self.made()
            made.mouse, made.online = mouse, online
            if waveforms is not None:
                mouse.haptic_waveforms = waveforms
            made.screen_cue()
            drain(made)  # nothing sent, nothing raised
            self.assertEqual(mouse.transport.played() if mouse else [], [])


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
