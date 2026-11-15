import copy
import json
import os
import tempfile
import unittest

import tests.support  # noqa: F401  (puts the project on the path)
from mxoptions import actions, config
from mxoptions import gestures, i18n_uk


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



class KeyboardFollowsMouse(unittest.TestCase):
    """flow.keyboard_follows_mouse: off at first, only ever true or false, in the same settings version."""

    load = Migration.load

    def test_off_at_first_and_only_true_or_false(self):
        self.assertIs(config.DEFAULTS["flow"]["keyboard_follows_mouse"], False)
        self.assertEqual(config.DEFAULTS["version"], 6)
        for value, kept in ((True, True), (1, True), ("yes", True), (None, False), (0, False), ([], False)):
            with self.subTest(value=value):
                self.assertIs(config.clean({"flow": {"keyboard_follows_mouse": value}})["flow"]["keyboard_follows_mouse"], kept)

    def test_settings_from_before_it_load_with_it_off_and_are_not_written_back(self):
        stored = copy.deepcopy(config.DEFAULTS)
        del stored["flow"]["keyboard_follows_mouse"]
        stored["flow"]["mouse_follows_keyboard"] = True
        loaded, saved = self.load(stored)
        self.assertIs(loaded["flow"]["keyboard_follows_mouse"], False)
        self.assertIs(loaded["flow"]["mouse_follows_keyboard"], True)
        self.assertEqual(loaded["version"], 6)
        self.assertNotIn("keyboard_follows_mouse", saved["flow"])  # the same version: nothing was written back

    def test_it_goes_into_a_settings_file_and_back(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["flow"]["keyboard_follows_mouse"] = True
        raw = config.export_data(cfg)
        self.assertIs(json.loads(raw)["config"]["flow"]["keyboard_follows_mouse"], True)
        self.assertIs(config.import_data(raw, config.DEFAULTS)["flow"]["keyboard_follows_mouse"], True)


class KeyboardNotices(unittest.TestCase):
    """keyboard.notices: off at first, on only for a JSON true, in the same settings version."""

    load = Migration.load

    def test_off_at_first_and_on_only_for_true(self):
        self.assertIs(config.DEFAULTS["keyboard"]["notices"], False)
        self.assertEqual(config.DEFAULTS["version"], 6)
        for value, kept in ((True, True), (False, False), (1, False), ("true", False), (None, False), ([], False),
                            ({}, False), (1.0, False)):
            with self.subTest(value=value):
                self.assertIs(config.clean({"keyboard": {"notices": value}})["keyboard"]["notices"], kept)

    def test_settings_from_before_it_load_with_it_off_and_are_not_written_back(self):
        stored = copy.deepcopy(config.DEFAULTS)
        del stored["keyboard"]["notices"]
        stored["keyboard"]["keys"] = {"0x0f01": Keyboard.COPY}
        stored["pointer"]["dpi"] = 1800
        loaded, saved = self.load(stored)
        self.assertIs(loaded["keyboard"]["notices"], False)
        self.assertEqual(loaded["keyboard"]["keys"], {"0x0f01": Keyboard.COPY})
        self.assertEqual(loaded["pointer"]["dpi"], 1800)
        self.assertEqual(loaded["version"], 6)
        self.assertEqual(saved, stored)  # the same version: nothing was written back

    def test_it_goes_into_a_settings_file_and_back(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["keyboard"]["notices"] = True
        raw = config.export_data(cfg)
        self.assertIs(json.loads(raw)["config"]["keyboard"]["notices"], True)
        self.assertIs(config.import_data(raw, config.DEFAULTS)["keyboard"]["notices"], True)
        data = json.loads(raw)
        data["config"]["keyboard"]["notices"] = "yes"
        self.assertIs(config.import_data(json.dumps(data).encode(), config.DEFAULTS)["keyboard"]["notices"], False)

    def test_it_survives_the_build_before(self):
        stored = copy.deepcopy(config.DEFAULTS)
        stored["keyboard"]["notices"] = True
        before = copy.deepcopy(config.DEFAULTS)
        del before["keyboard"]["notices"]  # its defaults: the merge it does keeps what it does not know
        self.assertIs(config._merge(before, stored)["keyboard"]["notices"], True)


class BatterySaving(unittest.TestCase):
    """haptics.battery_saving: on at first, off only for a JSON false, in the same settings version."""

    load = Migration.load

    def test_on_at_first_and_off_only_for_false(self):
        self.assertIs(config.DEFAULTS["haptics"]["battery_saving"], True)
        self.assertEqual(config.DEFAULTS["version"], 6)
        for value, kept in ((True, True), (False, False), (0, True), (1, True), ("false", True), (None, True),
                            ([], True), ({}, True), (0.0, True)):
            with self.subTest(value=value):
                self.assertIs(config.clean({"haptics": {"battery_saving": value}})["haptics"]["battery_saving"], kept)

    def test_settings_from_before_it_load_with_it_on_and_are_not_written_back(self):
        stored = copy.deepcopy(config.DEFAULTS)
        del stored["haptics"]["battery_saving"]
        stored["haptics"]["level"] = 30
        stored["keyboard"]["notices"] = True
        loaded, saved = self.load(stored)
        self.assertIs(loaded["haptics"]["battery_saving"], True)
        self.assertEqual((loaded["haptics"]["level"], loaded["keyboard"]["notices"], loaded["version"]), (30, True, 6))
        self.assertEqual(saved, stored)  # the same version: nothing was written back

    def test_it_goes_into_a_settings_file_and_back_with_everything_else(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["haptics"]["battery_saving"] = False
        cfg["keyboard"].update(notices=True, keys={"0x0f01": Keyboard.COPY},
                               profiles=[{"app": "kate", "name": "Kate", "keys": {"0x0f02": Keyboard.COPY}}])
        cfg["flow"].update(id="here", channel=2)
        current = copy.deepcopy(config.DEFAULTS)
        current["flow"].update(id="in use", channel=1)
        raw = config.export_data(cfg)
        self.assertIs(json.loads(raw)["config"]["haptics"]["battery_saving"], False)
        imported = config.import_data(raw, current)
        self.assertIs(imported["haptics"]["battery_saving"], False)
        self.assertEqual(imported["keyboard"], cfg["keyboard"])
        self.assertEqual({key: imported["flow"][key] for key in config.LOCAL_FLOW},
                         {key: current["flow"][key] for key in config.LOCAL_FLOW})  # Flow's own fields stay as in use
        data = json.loads(raw)
        del data["config"]["haptics"]["battery_saving"]  # a file from before it
        self.assertIs(config.import_data(json.dumps(data).encode(), current)["haptics"]["battery_saving"], True)
        data["config"]["haptics"]["battery_saving"] = "no"
        self.assertIs(config.import_data(json.dumps(data).encode(), current)["haptics"]["battery_saving"], True)

    def test_it_survives_the_build_before(self):
        stored = copy.deepcopy(config.DEFAULTS)
        stored["haptics"]["battery_saving"] = False
        before = copy.deepcopy(config.DEFAULTS)
        del before["haptics"]["battery_saving"]  # its defaults: the merge it does keeps what it does not know
        self.assertIs(config._merge(before, stored)["haptics"]["battery_saving"], False)


class SwitchingScreens(unittest.TestCase):
    """haptics.screens: off at first, on only for a JSON true, global, in the same settings version."""

    load = Migration.load

    def test_off_at_first_and_on_only_for_true(self):
        self.assertIs(config.DEFAULTS["haptics"]["screens"], False)
        self.assertEqual(config.DEFAULTS["version"], 6)
        for value, kept in ((True, True), (False, False), (1, False), (1.0, False), ("true", False), (None, False),
                            ([], False), ({}, False), ([True], False)):
            with self.subTest(value=value):
                self.assertIs(config.clean({"haptics": {"screens": value}})["haptics"]["screens"], kept)

    def test_settings_from_before_it_load_with_it_off_and_are_not_written_back(self):
        stored = copy.deepcopy(config.DEFAULTS)
        del stored["haptics"]["screens"]
        stored["haptics"]["battery_saving"] = False
        loaded, saved = self.load(stored)
        self.assertIs(loaded["haptics"]["screens"], False)
        self.assertEqual((loaded["haptics"]["battery_saving"], loaded["version"]), (False, 6))
        self.assertEqual(saved, stored)  # the same version: nothing was written back

    def test_it_goes_into_a_settings_file_and_back_and_flow_s_own_fields_stay(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["haptics"]["screens"] = True
        cfg["flow"].update(id="here", channel=2)
        current = copy.deepcopy(config.DEFAULTS)
        current["flow"].update(id="in use", channel=1)
        raw = config.export_data(cfg)
        self.assertIs(json.loads(raw)["config"]["haptics"]["screens"], True)
        imported = config.import_data(raw, current)
        self.assertIs(imported["haptics"]["screens"], True)
        self.assertEqual({key: imported["flow"][key] for key in config.LOCAL_FLOW},
                         {key: current["flow"][key] for key in config.LOCAL_FLOW})
        self.assertNotIn("screens", config.LOCAL_FLOW)
        data = json.loads(raw)
        for value in ("yes", 1, None):
            data["config"]["haptics"]["screens"] = value
            with self.subTest(value=value):
                self.assertIs(config.import_data(json.dumps(data).encode(), current)["haptics"]["screens"], False)
        del data["config"]["haptics"]["screens"]  # a file from before it
        self.assertIs(config.import_data(json.dumps(data).encode(), current)["haptics"]["screens"], False)

    def test_it_survives_the_build_before(self):
        stored = copy.deepcopy(config.DEFAULTS)
        stored["haptics"]["screens"] = True
        before = copy.deepcopy(config.DEFAULTS)
        del before["haptics"]["screens"]  # its defaults: the merge it does keeps what it does not know
        self.assertIs(config._merge(before, stored)["haptics"]["screens"], True)


class LockAndFnNotices(unittest.TestCase):
    """keyboard.lock_notices and keyboard.fn_notices: each as keyboard.notices, on its own, in the same version."""

    load = Migration.load
    NAMES = ("lock_notices", "fn_notices")

    def test_off_at_first_and_on_only_for_true_each_on_its_own(self):
        self.assertEqual(config.DEFAULTS["version"], 6)
        for name in self.NAMES:
            self.assertIs(config.DEFAULTS["keyboard"][name], False)
            for value, kept in ((True, True), (False, False), (1, False), ("true", False), (None, False), ([], False),
                                ({}, False), (1.0, False)):
                with self.subTest(name=name, value=value):
                    cleaned = config.clean({"keyboard": {name: value, "notices": True}})["keyboard"]
                    self.assertIs(cleaned[name], kept)
                    self.assertIs(cleaned["notices"], True)  # the brightness one stays as it is
                    other = next(n for n in self.NAMES if n != name)
                    self.assertIs(cleaned[other], False)

    def test_settings_from_before_them_load_with_them_off_and_are_not_written_back(self):
        stored = copy.deepcopy(config.DEFAULTS)
        for name in self.NAMES:
            del stored["keyboard"][name]
        stored["keyboard"]["notices"] = True
        loaded, saved = self.load(stored)
        self.assertEqual({name: loaded["keyboard"][name] for name in ("notices", *self.NAMES)},
                         {"notices": True, "lock_notices": False, "fn_notices": False})
        self.assertEqual(loaded["version"], 6)
        self.assertEqual(saved, stored)

    def test_they_go_into_a_settings_file_and_back_and_survive_the_build_before(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["keyboard"].update(lock_notices=True, fn_notices=True)
        raw = config.export_data(cfg)
        self.assertEqual({name: json.loads(raw)["config"]["keyboard"][name] for name in self.NAMES},
                         dict.fromkeys(self.NAMES, True))
        imported = config.import_data(raw, config.DEFAULTS)["keyboard"]
        self.assertEqual({name: imported[name] for name in self.NAMES}, dict.fromkeys(self.NAMES, True))
        data = json.loads(raw)
        data["config"]["keyboard"].update(lock_notices="yes", fn_notices=1)
        imported = config.import_data(json.dumps(data).encode(), config.DEFAULTS)["keyboard"]
        self.assertEqual({name: imported[name] for name in self.NAMES}, dict.fromkeys(self.NAMES, False))
        before = copy.deepcopy(config.DEFAULTS)
        for name in self.NAMES:  # the build before: it knows neither, and its merge keeps what it does not know
            del before["keyboard"][name]
        merged = config._merge(before, cfg)["keyboard"]
        self.assertEqual({name: merged[name] for name in self.NAMES}, dict.fromkeys(self.NAMES, True))


class Keyboard(unittest.TestCase):
    COPY = {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_C"]}
    load = Migration.load

    def test_what_keys_may_do(self):
        good = {
            "0x00c7": self.COPY, "0x00c8": {"type": "none"}, "0x00c9": {"type": "button", "button": "BTN_LEFT"},
            "0x00ca": {"type": "shortcut", "component": "org_kde_spectacle_desktop", "name": "RecordRegion"},
            "0x00cb": {"type": "desktop", "do": "overview"}, "0x00cc": {"type": "command", "command": "true"},
            "0x00cd": {"type": "script", "name": "s", "script": "echo"},
            "0x00ce": {"type": "macro", "name": "m", "repeat": True, "steps": [{"do": "keys", "keys": ["KEY_A"]}]},
            "0x00cf": {"type": "host", "host": 1}, "0x00d0": {"type": "hook", "name": "h"}, "0x00d1": {"type": "ring"},
        }
        self.assertEqual(config.clean({"keyboard": {"keys": copy.deepcopy(good)}})["keyboard"]["keys"], good)
        profiles = [{"app": "kate", "name": "Kate", "keys": {"0x00c7": {"type": "ring"}}}]
        self.assertEqual(config.clean({"keyboard": {"profiles": copy.deepcopy(profiles)}})["keyboard"]["profiles"], profiles)

    def test_what_keys_may_not_do_or_are_not_is_dropped(self):
        bad = {
            "0x00C7": self.COPY, "199": self.COPY, "0x0c7": self.COPY, "0x000c7": self.COPY,  # not a key id
            "0x0001": {"type": "default"}, "0x0002": {"type": "gestures"}, "0x0003": {"type": "ring", "x": 1},
            "0x0004": {"type": "wheel_mode"}, "0x0005": {"type": "dpi_cycle"}, "0x0006": {"type": "dpi_shift"},
            "0x0007": {"type": "flow"}, "0x0008": "copy", "0x0009": {"type": "keys", "keys": []},
            "0x000a": {"type": "keys", "keys": [1]}, "0x000b": {"type": "button", "button": "KEY_A"},
            "0x000c": {"type": "host", "host": "1"}, "0x000d": {"type": "host", "host": True},
            "0x000e": {"type": "host", "host": 99}, "0x000f": {"type": "command"}, "0x0010": {"type": "hook", "name": 3},
            "0x0011": {"type": "macro", "steps": [{"do": "warp"}]}, "0x0012": {"type": "macro", "steps": "x"},
            "0x0013": {"type": "shortcut", "name": "n"}, "0x0014": {"type": "Ring"}, "0x0015": {"type": ["ring"]},
            "0x0016": {"type": "ring", "bubbles": []},
        }
        self.assertEqual(config.clean({"keyboard": {"keys": bad}})["keyboard"]["keys"], {})
        for section in ("nonsense", ["list"], {"keys": "nonsense"}, {"keys": ["0x00c7"]}):
            with self.subTest(section=section):
                self.assertEqual(config.clean({"keyboard": section})["keyboard"],
                                 {"keys": {}, "profiles": [], "notices": False, "lock_notices": False, "fn_notices": False})

    def test_malformed_desktop_fields_are_dropped_without_raising(self):
        for value in ([], {}, None, True, 4):
            with self.subTest(value=value):
                cleaned = config.clean({"keyboard": {"keys": {"0x00c7": {"type": "desktop", "do": value}}}})
                self.assertEqual(cleaned["keyboard"]["keys"], {})

    def test_what_a_key_runs_is_checked_to_the_last_step(self):
        step = lambda **fields: {"type": "macro", "name": "m", "steps": [fields]}  # noqa: E731
        good = {
            "0x0001": step(do="keys", keys=["KEY_LEFTCTRL", "KEY_C"]), "0x0002": step(do="keys", keys=[]),
            "0x0003": step(do="text", text="hello"), "0x0004": step(do="delay", ms=0), "0x0005": step(do="delay", ms=10000),
            "0x0006": step(do="click", button="BTN_RIGHT"), "0x0007": step(do="command", command="true"),
            "0x0008": {"type": "keys", "keys": ["KEY_F5"]}, "0x0009": {"type": "button", "button": "BTN_SIDE"},
            "0x000a": {"type": "macro", "steps": [{"do": "delay", "ms": 5}] * 64},
        }
        self.assertEqual(config.clean({"keyboard": {"keys": copy.deepcopy(good)}})["keyboard"]["keys"], good)
        bad = {
            "0x0001": {"type": "keys", "keys": ["KEY_NOT_REAL"]}, "0x0002": {"type": "keys", "keys": ["KEY_A"] * 17},
            "0x0003": step(do="keys", keys=["KEY_NOT_REAL"]), "0x0004": step(do="keys"), "0x0005": step(do="keys", keys="KEY_A"),
            "0x0006": step(do="delay", ms="invalid"), "0x0007": step(do="delay", ms=-1), "0x0008": step(do="delay", ms=10001),
            "0x0009": step(do="delay", ms=1.5), "0x000a": step(do="delay", ms=True), "0x000b": step(do="delay"),
            "0x000c": step(do="click", button="KEY_A"), "0x000d": step(do="click"), "0x000e": step(do="text"),
            "0x000f": step(do="text", text=3), "0x0010": step(do="command"), "0x0011": step(do="command", command=["ls"]),
            "0x0012": {"type": "macro", "steps": []}, "0x0013": {"type": "macro", "steps": [{"do": "delay", "ms": 5}] * 65},
            "0x0014": {"type": "macro", "repeat": "yes", "steps": [{"do": "delay", "ms": 5}]},
            "0x0015": {"type": "macro", "count": 3, "repeat": 1, "steps": [{"do": "delay", "ms": 5}]},
            "0x0016": {"type": "macro", "steps": ["delay"]}, "0x0017": {"type": "desktop", "do": "no_such_thing"},
            "0x0018": {"type": "command", "command": ""}, "0x0019": {"type": "script", "script": ""},
            "0x001a": {"type": "shortcut", "component": "", "name": "n"}, "0x001b": {"type": "hook", "name": ""},
            "0x001c": {"type": "keys", "keys": ["KEY_A", None]}, "0x001d": {"type": "command", "command": "x" * 70000},
        }
        self.assertEqual(config.clean({"keyboard": {"keys": bad}})["keyboard"]["keys"], {})
        for action in bad.values():
            with self.subTest(action=action):
                self.assertFalse(config.keyboard_action(action))

    def test_at_most_so_many_keys_the_lowest_first(self):
        many = {config.key_id(cid): self.COPY for cid in range(300, 0, -1)}
        kept = config.clean({"keyboard": {"keys": many}})["keyboard"]["keys"]
        self.assertEqual(len(kept), config.MAX_KEYBOARD_KEYS)
        self.assertEqual(list(kept), [config.key_id(cid) for cid in range(1, config.MAX_KEYBOARD_KEYS + 1)])

    def test_settings_without_a_keyboard_load_as_before(self):
        stored = copy.deepcopy(config.DEFAULTS)
        del stored["keyboard"]
        stored["pointer"]["dpi"] = 1800
        stored["wheel"]["threshold"] = 30
        loaded, saved = self.load(stored)
        self.assertEqual(loaded["keyboard"], {"keys": {}, "profiles": [], "notices": False, "lock_notices": False, "fn_notices": False})
        for section in config.DEFAULTS:
            if section != "keyboard":
                self.assertEqual(loaded[section], config.clean(stored)[section])
        self.assertEqual(loaded["version"], 6)
        self.assertNotIn("keyboard", saved)  # the same version: nothing was written back

    def test_keys_of_a_keyboard_that_is_not_there_are_kept(self):
        stored = copy.deepcopy(config.DEFAULTS)
        stored["keyboard"]["keys"] = {"0x00c7": self.COPY}
        loaded, _saved = self.load(stored)
        self.assertEqual(loaded["keyboard"]["keys"], {"0x00c7": self.COPY})


def deep(depth):
    value = []
    for _level in range(depth):
        value = [value]
    return value


# Not a profile, whatever else is in the file: each one dropped on load, and refused by Set (test_front.py)
BAD_PROFILES = [
    "editor", 3, True, None, [], ["editor"], {},
    {"app": "editor", "name": "E"}, {"app": "editor", "keys": {}}, {"name": "E", "keys": {}},
    {"app": "editor", "name": "E", "keys": {}, "extra": 1},
    {"app": "Editor", "name": "E", "keys": {}}, {"app": "", "name": "E", "keys": {}},
    {"app": "x" * 129, "name": "E", "keys": {}}, {"app": "ed\nitor", "name": "E", "keys": {}},
    {"app": "ed\u200bitor", "name": "E", "keys": {}}, {"app": "edi\x7ftor", "name": "E", "keys": {}},
    {"app": 1, "name": "E", "keys": {}}, {"app": True, "name": "E", "keys": {}}, {"app": ["editor"], "name": "E", "keys": {}},
    {"app": {"a": 1}, "name": "E", "keys": {}}, {"app": "editor", "name": "x" * 65, "keys": {}},
    {"app": "editor", "name": None, "keys": {}}, {"app": "editor", "name": 5, "keys": {}},
    {"app": "editor", "name": "E", "keys": []}, {"app": "editor", "name": "E", "keys": "0x0f01"},
    {"app": "editor", "name": "E", "keys": None}, {"app": "editor", "name": "E", "keys": deep(60)},
]


class Profiles(unittest.TestCase):
    """keyboard.profiles: at most 32 apps, each with keys of its own; NATIVE for what the key always does there."""

    COPY = {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_C"]}
    load = Migration.load

    def keep(self, profiles):
        return config.clean({"keyboard": {"profiles": profiles}})["keyboard"]["profiles"]

    def test_what_a_profile_may_be(self):
        good = [{"app": "org.kde.kate", "name": "Kate", "keys": {"0x0f01": self.COPY, "0x0f02": {"type": "default"},
                                                                 "0x0f03": {"type": "none"}}},
                {"app": "firefox.exe", "name": "", "keys": {}}, {"app": "x" * 128, "name": "n" * 64, "keys": {}},
                {"app": "кейт", "name": "Кейт", "keys": {}}, {"app": "a b,c", "name": "w", "keys": {}}]
        self.assertEqual(self.keep(copy.deepcopy(good)), good)
        self.assertEqual(config.clean(copy.deepcopy(config.DEFAULTS))["keyboard"]["profiles"], [])

    def test_what_is_not_one_is_dropped_and_the_rest_kept_in_order(self):
        kept = {"app": "kept", "name": "K", "keys": {}}
        for bad in BAD_PROFILES:
            with self.subTest(bad=bad):
                self.assertEqual(self.keep([bad, kept]), [kept])
        for value in ("nonsense", 3, True, None, {"app": "kept"}, deep(60)):
            with self.subTest(value=value):
                self.assertEqual(self.keep(value), [])

    def test_keys_a_profile_may_not_have_are_dropped_from_it(self):
        bad = {"0x0F01": self.COPY, "0x0f02": {"type": "default", "x": 1}, "0x0f03": {"type": "gestures"},
               "0x0f04": {"type": "keys"}, "0x0f05": {"type": "host", "host": True}, "0x0f06": [], "0x0f07": True,
               "0x0f08": {"type": "macro", "steps": [{"do": "action", "action": {"type": "macro", "steps": deep(40)}}]},
               "0x0f09": None, "0x0f0a": "default", "0x0f0b": {"type": "DEFAULT"}, 7: self.COPY}
        kept = self.keep([{"app": "a", "name": "A", "keys": dict(bad, **{"0x0f10": self.COPY})}])
        self.assertEqual(kept, [{"app": "a", "name": "A", "keys": {"0x0f10": self.COPY}}])

    def test_at_most_so_many_one_for_each_app(self):
        many = [{"app": f"app{n}", "name": "", "keys": {}} for n in range(40)]
        self.assertEqual([p["app"] for p in self.keep(many)], [f"app{n}" for n in range(config.MAX_PROFILES)])
        twice = [{"app": "a", "name": "first", "keys": {}}, {"app": "a", "name": "second", "keys": {}}]
        self.assertEqual(self.keep(twice), twice[:1])
        keys = {config.key_id(cid): self.COPY for cid in range(200, 0, -1)}
        kept = self.keep([{"app": "a", "name": "", "keys": keys}])[0]["keys"]
        self.assertEqual(list(kept), [config.key_id(cid) for cid in range(1, config.MAX_KEYBOARD_KEYS + 1)])

    def test_the_mark_for_a_key_s_own_function_is_for_profiles_only(self):
        self.assertEqual(config.clean({"keyboard": {"keys": {"0x0f01": {"type": "default"}}}})["keyboard"]["keys"], {})

    def test_settings_of_the_build_before_profiles_load_unchanged(self):
        stored = copy.deepcopy(config.DEFAULTS)
        stored["keyboard"] = {"keys": {"0x0f01": self.COPY}}  # what the build before wrote: version 6, no profiles
        loaded, saved = self.load(stored)
        self.assertEqual(loaded["keyboard"], {"keys": {"0x0f01": self.COPY}, "profiles": [], "notices": False, "lock_notices": False, "fn_notices": False})
        self.assertEqual(saved, stored)  # the same version: not written back

    def test_profiles_survive_the_build_before(self):
        stored = copy.deepcopy(config.DEFAULTS)
        stored["keyboard"]["profiles"] = [{"app": "kate", "name": "Kate", "keys": {"0x0f01": {"type": "default"}}}]
        before = copy.deepcopy(config.DEFAULTS)
        del before["keyboard"]["profiles"]  # its defaults: the merge it does keeps what it does not know
        merged = config._merge(before, stored)
        self.assertEqual(merged["keyboard"]["profiles"], stored["keyboard"]["profiles"])
        self.assertEqual(config._keyboard_keys(merged["keyboard"]["keys"]), {})  # its own part, cleaned as it did
        self.assertEqual(stored["version"], 6)


class MacroSteps(unittest.TestCase):
    """The action and vibration steps of a macro: what a key keeps, what any macro may run."""

    @staticmethod
    def macro(*steps):
        return {"type": "macro", "name": "m", "steps": list(steps)}

    GOOD = [
        {"do": "action", "action": {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_C"]}},
        {"do": "action", "action": {"type": "button", "button": "BTN_SIDE"}},
        {"do": "action", "action": {"type": "shortcut", "component": "kwin", "name": "Overview"}},
        {"do": "action", "action": {"type": "desktop", "do": "overview"}},
        {"do": "action", "action": {"type": "command", "command": "true"}},
        {"do": "action", "action": {"type": "script", "name": "s", "script": "true"}},
        {"do": "action", "action": {"type": "hook", "name": "paint.brush"}},
        {"do": "haptic", "waveform": 0x00}, {"do": "haptic", "waveform": 0x04}, {"do": "haptic", "waveform": 0x1B},
    ]

    def test_every_allowed_action_and_known_waveform_is_kept(self):
        self.assertEqual({step["action"]["type"] for step in self.GOOD if step["do"] == "action"}, set(actions.STEP_ACTIONS))
        keys = {config.key_id(n + 1): self.macro(step) for n, step in enumerate(self.GOOD)}
        keys["0x0100"] = self.macro(*self.GOOD)
        self.assertEqual(config.clean({"keyboard": {"keys": copy.deepcopy(keys)}})["keyboard"]["keys"], keys)

    def test_nested_macros_device_actions_and_malformed_steps_are_refused(self):
        bad = [
            {"type": "macro", "steps": [{"do": "delay", "ms": 5}]}, {"type": "host", "host": 1}, {"type": "flow"},
            {"type": "wheel_mode"}, {"type": "dpi_cycle"}, {"type": "dpi_shift"}, {"type": "ring"}, {"type": "gestures"},
            {"type": "default"}, {"type": "none"}, {"type": "keys", "keys": ["KEY_NOT_REAL"]}, {"type": "command", "command": ""},
            {"type": ["keys"], "keys": ["KEY_A"]}, {"type": {"keys": 1}}, {"keys": ["KEY_A"]}, {}, [], "keys", None, 3,
        ]
        waveforms = [True, False, -1, 0x10, 0x0F, 256, 4.0, "4", None, [4], {"w": 4}]
        steps = [{"do": "action", "action": action} for action in bad] + [{"do": "action"}]
        steps += [{"do": "haptic", "waveform": waveform} for waveform in waveforms] + [{"do": "haptic"}]
        for step in steps:
            with self.subTest(step=step):
                self.assertFalse(config.macro_step(step))
                self.assertFalse(config.keyboard_action(self.macro({"do": "delay", "ms": 5}, step)))
        keys = {config.key_id(n + 1): self.macro(step) for n, step in enumerate(steps)}
        self.assertEqual(config.clean({"keyboard": {"keys": keys}})["keyboard"]["keys"], {})

    def test_deep_nesting_is_refused_without_looking_inside(self):
        deep = {"do": "delay", "ms": 5}
        for _level in range(5000):  # far past the recursion limit, were each level looked into
            deep = {"do": "action", "action": self.macro(deep)}
        self.assertFalse(config.macro_step(deep))
        self.assertFalse(config.keyboard_action(self.macro(deep)))
        shallow = {"do": "delay", "ms": 5}
        for _level in range(50):
            shallow = {"do": "action", "action": self.macro(shallow)}
        self.assertEqual(config.clean({"keyboard": {"keys": {"0x0001": self.macro(shallow)}}})["keyboard"]["keys"], {})

    def test_a_mouse_macro_is_kept_as_it_was(self):
        """Buttons are not checked step by step: what a macro holds is left to the runner."""
        mouse = self.macro(*self.GOOD, {"do": "action", "action": {"type": "host", "host": 1}}, {"do": "haptic", "waveform": True},
                           {"do": "warp"})
        cleaned = config.clean({"buttons": {"back": copy.deepcopy(mouse)}})
        self.assertEqual(cleaned["buttons"]["back"], mouse)

    def test_macros_without_the_new_steps_stay_as_they_were(self):
        old = self.macro({"do": "keys", "keys": ["KEY_A"]}, {"do": "text", "text": "hi"}, {"do": "delay", "ms": 5},
                         {"do": "click", "button": "BTN_LEFT"}, {"do": "command", "command": "true"})
        cfg = {"buttons": {"back": copy.deepcopy(old)}, "keyboard": {"keys": {"0x00c7": copy.deepcopy(old)}}}
        cleaned = config.clean(cfg)
        self.assertEqual((cleaned["buttons"]["back"], cleaned["keyboard"]["keys"]), (old, {"0x00c7": old}))


class StopOnPress(unittest.TestCase):
    """A macro's "stop_on_press": true or false where keys and profiles are checked, kept everywhere a macro is."""

    MACRO = {"type": "macro", "name": "m", "repeat": False, "steps": [{"do": "keys", "keys": ["KEY_A"]}]}
    load = Migration.load

    def stoppable(self, value=True):
        return dict(copy.deepcopy(self.MACRO), stop_on_press=value)

    def test_a_key_takes_true_or_false_only(self):
        self.assertTrue(config.keyboard_action(copy.deepcopy(self.MACRO)))
        for value in (True, False):
            with self.subTest(value=value):
                self.assertTrue(config.keyboard_action(self.stoppable(value)))
        for value in ("yes", 1, 0, None, [True]):
            with self.subTest(value=value):
                self.assertFalse(config.keyboard_action(self.stoppable(value)))
                self.assertEqual(config.clean({"keyboard": {"keys": {"0x0f01": self.stoppable(value)}}})["keyboard"]["keys"], {})
                profile = {"app": "org.kde.kate", "name": "Kate", "buttons": {"back": self.stoppable(value)}, "ring": None}
                self.assertEqual(config.mouse_profile(profile)["buttons"], {})

    def test_the_field_survives_cleaning_export_and_import(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["buttons"]["back"] = self.stoppable()
        cfg["buttons"]["gesture"]["tap"] = self.stoppable()
        cfg["keyboard"]["keys"] = {"0x0f01": self.stoppable()}
        cfg["keyboard"]["profiles"] = [{"app": "kate", "name": "Kate", "keys": {"0x0f01": self.stoppable()}}]
        cfg["mouse"]["profiles"] = [{"app": "org.kde.kate", "name": "Kate", "buttons": {"back": self.stoppable()}, "ring": None}]
        cfg["ring"]["bubbles"][0]["action"] = self.stoppable()
        cleaned = config.clean(copy.deepcopy(cfg))
        self.assertEqual(cleaned, cfg)
        self.assertEqual(config.import_data(config.export_data(cleaned), cleaned), cleaned)

    def test_macros_saved_before_come_back_without_it(self):
        stored = copy.deepcopy(config.DEFAULTS)
        stored["buttons"]["back"] = copy.deepcopy(self.MACRO)
        stored["keyboard"]["keys"] = {"0x0f01": copy.deepcopy(self.MACRO)}
        loaded, saved = self.load(stored)
        self.assertEqual((loaded["buttons"]["back"], loaded["keyboard"]["keys"]), (self.MACRO, {"0x0f01": self.MACRO}))
        self.assertEqual(saved, stored)


class HoldModeSettings(unittest.TestCase):
    """buttons.<key>.mode ("pan", "zoom": what a gestures button does while held) and gestures.move_speed,
    gestures.move_reverse."""

    SHAPES = {"down_right": {"type": "keys", "keys": ["KEY_F9"]}}
    load = Migration.load

    @staticmethod
    def gesture(**fields):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["buttons"]["gesture"].update(fields)
        return config.clean(cfg)["buttons"]["gesture"]

    def test_pan_and_zoom_are_kept_anything_else_is_left_out(self):  # C01
        plain = self.gesture(shapes=self.SHAPES)
        for mode in ("pan", "zoom"):
            with self.subTest(mode=mode):
                self.assertEqual(self.gesture(mode=mode, shapes=self.SHAPES), dict(plain, mode=mode))
                self.assertEqual(gestures.hold_mode({"type": "gestures", "mode": mode}), mode)
        for mode in ("swipes", "rotate", None, 1, "PAN", True, ["pan"], {"pan": 1}):
            with self.subTest(mode=mode):
                self.assertEqual(self.gesture(mode=mode, shapes=self.SHAPES), plain)  # directions, shapes kept
                self.assertIsNone(gestures.hold_mode({"type": "gestures", "mode": mode}))
        self.assertIsNone(gestures.hold_mode({"type": "gestures"}))

    def test_speed_is_clamped_and_reverse_is_true_or_false(self):  # C02
        for value, kept in ((0, 0.25), (9, 4.0), ("x", 1.0), (True, 1.0), (None, 1.0), (2.5, 2.5)):
            with self.subTest(speed=value):
                self.assertEqual(config.clean({"gestures": {"move_speed": value}})["gestures"]["move_speed"], kept)
        for value, kept in ((1, True), ("yes", True), (0, False), (None, False), (False, False)):
            with self.subTest(reverse=value):
                self.assertIs(config.clean({"gestures": {"move_reverse": value}})["gestures"]["move_reverse"], kept)

    def test_pan_and_its_settings_survive_export_and_import_local_flow_stays_local(self):  # C03
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["buttons"]["gesture"]["mode"] = "zoom"
        cfg["buttons"]["middle"] = dict(config.default_gestures(), mode="pan")
        cfg["gestures"].update(move_speed=2.5, move_reverse=True)
        cfg["flow"].update(id="here", channel=2)
        cfg = config.clean(cfg)
        current = copy.deepcopy(cfg)
        current["flow"].update(id="mine", channel=1)
        imported = config.import_data(config.export_data(cfg), current)
        self.assertEqual((imported["buttons"]["gesture"]["mode"], imported["buttons"]["middle"]["mode"]), ("zoom", "pan"))
        self.assertEqual((imported["gestures"]["move_speed"], imported["gestures"]["move_reverse"]), (2.5, True))
        self.assertEqual((imported["flow"]["id"], imported["flow"]["channel"]), ("mine", 1))

    def test_an_app_profile_cannot_pan_or_carry_the_pan_settings(self):  # C04
        pan = dict(config.default_gestures(), mode="pan")
        self.assertFalse(config.profile_button_action(pan))
        profile = {"app": "kate", "name": "Kate", "buttons": {"gesture": pan, "middle": self.SHAPES["down_right"]},
                   "ring": None}
        self.assertEqual(list(config.mouse_profile(copy.deepcopy(profile))["buttons"]), ["middle"])
        cfg = config.clean({"mouse": {"profiles": [profile]}})
        self.assertEqual(config.mouse_buttons(cfg, "kate")["gesture"], config.DEFAULTS["buttons"]["gesture"])
        self.assertEqual(set(cfg["mouse"]["profiles"][0]), {"app", "name", "buttons", "ring"})

    def test_settings_saved_before_come_back_unchanged(self):  # C05
        self.assertNotIn("mode", config.DEFAULTS["buttons"]["gesture"])
        stored = copy.deepcopy(config.DEFAULTS)
        for key in ("move_speed", "move_reverse"):
            stored["gestures"].pop(key, None)  # as saved before these settings were there
        loaded, saved = self.load(stored)
        self.assertEqual(loaded, config.DEFAULTS)
        self.assertEqual(saved, stored)  # loading alone writes nothing

    def test_every_hold_mode_label_has_a_translation(self):  # C06
        for label in gestures.HOLD_MODES.values():
            self.assertIn(label, i18n_uk.STRINGS)


if __name__ == "__main__":
    unittest.main()
