"""What is asked of the desktop, and what that comes to on each of them."""

import copy
import unittest

import tests.support  # noqa: F401
from mxoptions import actions, config, environment
from mxoptions.environment import GNOME, HYPRLAND, KDE, OTHER, SWAY


class Ways(unittest.TestCase):
    def test_every_catalogue_entry_that_asks_the_desktop_is_known(self):
        asked = [preset.action["do"] for preset in actions.PRESETS if preset.action.get("type") == "desktop"]
        self.assertGreater(len(asked), 20)
        for name in asked:
            self.assertIn(name, environment.WAYS)

    def test_kde_does_all_of_it_with_its_own_shortcuts(self):
        for name in environment.WAYS:
            self.assertEqual(environment.resolve(name, KDE)["type"], "shortcut", name)
        self.assertEqual(environment.resolve("overview", KDE), {"type": "shortcut", "component": "kwin", "name": "Overview"})

    def test_other_desktops_get_keys_or_commands_and_never_a_kde_shortcut(self):
        for desktop in (GNOME, SWAY, HYPRLAND, OTHER):
            for name in environment.WAYS:
                way = environment.resolve(name, desktop, installed=lambda tool: True)
                self.assertIn(way and way["type"], (None, "keys", "command"), (desktop, name))
        self.assertEqual(environment.resolve("win_close", GNOME), {"type": "keys", "keys": ["KEY_LEFTALT", "KEY_F4"]})
        self.assertEqual(environment.resolve("win_close", SWAY), {"type": "command", "command": "swaymsg kill"})
        self.assertIsNone(environment.resolve("desktop_grid", GNOME))
        self.assertIsNone(environment.resolve("no such thing", KDE))

    def test_a_way_that_takes_a_program_is_one_only_where_it_is_installed(self):
        self.assertIsNone(environment.resolve("terminal", SWAY, installed=lambda tool: False))
        self.assertEqual(environment.resolve("terminal", SWAY, installed=lambda tool: tool == "foot"),
                         {"type": "command", "command": "foot"})
        self.assertIsNone(environment.resolve("screenshot_region", SWAY, installed=lambda tool: tool == "grim"))  # and slurp
        way = environment.resolve("screenshot_region", SWAY, installed=lambda tool: tool in ("grim", "slurp"))
        self.assertTrue(way["command"].startswith('grim -g "$(slurp)" '))
        self.assertEqual(environment.resolve("launcher", HYPRLAND, installed=lambda tool: tool in ("rofi", "bemenu-run")),
                         {"type": "command", "command": "rofi -show drun"})

    def test_available(self):
        self.assertTrue(environment.available(environment.wanted("overview"), KDE))
        self.assertFalse(environment.available(environment.wanted("overview"), SWAY))
        self.assertFalse(environment.available({"type": "shortcut", "component": "kwin", "name": "Anything"}, GNOME))
        self.assertTrue(environment.available({"type": "shortcut", "component": "kwin", "name": "Anything"}, KDE))
        self.assertTrue(environment.available({"type": "keys", "keys": ["KEY_A"]}, OTHER))
        self.assertTrue(environment.available(None, OTHER))


class OldSettings(unittest.TestCase):
    OVERVIEW = {"type": "shortcut", "component": "kwin", "name": "Overview"}
    OWN = {"type": "shortcut", "component": "kwin", "name": "KrohnkiteToggleFloat"}

    def test_the_shortcuts_the_catalogue_held_become_what_they_ask_for(self):
        self.assertEqual(environment.modernize(dict(self.OVERVIEW)), {"type": "desktop", "do": "overview"})
        self.assertEqual(environment.modernize({"name": "Window Close", "component": "kwin", "type": "shortcut"}),
                         {"type": "desktop", "do": "win_close"})

    def test_anything_else_is_left_alone(self):
        for action in (self.OWN, {"type": "keys", "keys": ["KEY_A"]}, {"type": "none"}, None, "nonsense",
                       {"type": "shortcut", "component": ["kwin"], "name": {}}):
            self.assertEqual(environment.modernize(copy.deepcopy(action)), action)

    def test_settings_are_converted_wherever_an_action_can_be(self):
        cfg = config.clean({
            "buttons": {"back": dict(self.OVERVIEW),
                        "gesture": {"type": "gestures", "tap": dict(self.OVERVIEW), "up": dict(self.OWN),
                                    "shapes": {"down_right": dict(self.OVERVIEW)}}},
            "ring": {"bubbles": [{"action": dict(self.OVERVIEW)}, {"children": [{"action": dict(self.OVERVIEW)}]},
                                 {"action": dict(self.OWN)}],
                     "saved": [{"name": "mine", "bubbles": [{"action": dict(self.OVERVIEW)}]}]},
        })
        wanted = {"type": "desktop", "do": "overview"}
        self.assertEqual(cfg["buttons"]["back"], wanted)
        self.assertEqual(cfg["buttons"]["gesture"]["tap"], wanted)
        self.assertEqual(cfg["buttons"]["gesture"]["up"], self.OWN)
        self.assertEqual(cfg["buttons"]["gesture"]["shapes"], {"down_right": wanted})
        self.assertEqual(cfg["ring"]["bubbles"][0]["action"], wanted)
        self.assertEqual(cfg["ring"]["bubbles"][1]["children"][0]["action"], wanted)
        self.assertEqual(cfg["ring"]["bubbles"][2]["action"], self.OWN)
        self.assertEqual(cfg["ring"]["saved"][0]["bubbles"][0]["action"], wanted)
        self.assertEqual(config.clean(cfg), cfg)  # and stays as it is from then on

    def test_the_catalogue_still_names_them(self):
        self.assertEqual(actions.find_preset({"type": "desktop", "do": "overview"}).id, "overview")
        self.assertEqual(actions.describe({"type": "desktop", "do": "window_desktop_next"}), "Window desktop next")


class Executing(unittest.TestCase):
    def setUp(self):
        self.keys, self.ran = [], []

        class Keys:
            def tap(_self, keys):
                self.keys.append(("tap", list(keys)))

            def press(_self, keys):
                self.keys.append(("press", list(keys)))

            def release(_self, keys):
                self.keys.append(("release", list(keys)))

            def release_all(_self):
                pass

        self.executor = actions.Executor(Keys(), lambda action, phase: None)
        self.executor._oneshot = self.ran.append
        self._resolve = environment.resolve
        self.addCleanup(lambda: setattr(environment, "resolve", self._resolve))
        self.addCleanup(self.executor.close)

    def on(self, desktop):
        environment.resolve = lambda name, _desktop=None, **more: self._resolve(name, desktop, installed=lambda tool: True)

    def test_a_key_combination_is_tapped_once_however_long_the_button_is_held(self):
        self.on(GNOME)
        action = environment.wanted("overview")
        self.executor.press(action)
        self.executor.release(action)
        self.assertEqual(self.keys, [("tap", ["KEY_LEFTMETA", "KEY_S"])])

    def test_a_command_is_run(self):
        self.on(SWAY)
        self.executor.trigger(environment.wanted("win_close"))
        self.assertEqual(self.ran, [{"type": "command", "command": "swaymsg kill"}])

    def test_kde_gets_its_shortcut(self):
        self.on(KDE)
        self.executor.press(environment.wanted("overview"))
        self.assertEqual(self.ran, [{"type": "shortcut", "component": "kwin", "name": "Overview"}])

    def test_what_a_desktop_cannot_do_does_nothing(self):
        self.on(SWAY)
        self.executor.trigger(environment.wanted("overview"))
        self.assertEqual((self.keys, [action for action in self.ran if action["type"] != "none"]), ([], []))


if __name__ == "__main__":
    unittest.main()
