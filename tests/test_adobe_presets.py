"""Own Photoshop and Premiere suggested settings (config.APP_PRESETS, APP_PRESET_KEYS): offered on Windows only
(config.PRESET_SYSTEMS), given only where an app does as in all apps, thumb wheel keystrokes included, in one push,
previewed exactly, refused when what the preview named changed meanwhile, and what the buttons and the thumb wheel then
send through a real Executor on a FakeInput. App names are made up; nothing native."""

import copy
import unittest
from unittest import mock

import tests.support as support
import tests.test_mouse_profiles as profiles
import tests.test_thumb_keys as tk
from tests.test_holds import Refusing
from mxoptions import actions, config
from mxoptions.ui import pages
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QMessageBox

KATE, KONSOLE, KWRITE = profiles.KATE, profiles.KONSOLE, profiles.KWRITE
CTRL, SHIFT, Z = "KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_Z"
UNDO, REDO = actions.keys("LEFTCTRL", "Z"), actions.keys("LEFTCTRL", "LEFTSHIFT", "Z")
BRUSH = {"up": ["KEY_RIGHTBRACE"], "down": ["KEY_LEFTBRACE"]}
PAGES = {"up": ["KEY_PAGEDOWN"], "down": ["KEY_PAGEUP"]}
F13, F14, F15 = ["KEY_F13"], ["KEY_F14"], ["KEY_F15"]
YES = QMessageBox.StandardButton.Yes
OFFERED = config.offered_presets  # the real one, before any test replaces it
STALE = "Settings changed while this dialog was open. Choose Suggested settings again."
NOTE_PS = pages.PRESET_NOTES["photoshop"]
LAYOUT = ("These are key positions of a US keyboard: with another layout, or shortcuts changed in the app, they may "
          "do something else.")
PAIR_UNDO = ('To undo the keystrokes of the thumb wheel, choose "Use the keystrokes for all apps" on the Point & '
             'scroll page, before its function.')
EVERYTHING = 'Everything else stays as it is. To undo one of these, choose "As in all apps" for it.'


def pair(app, keys):
    return {"app": app, "up": list(keys["up"]), "down": list(keys["down"])}


def a_store():
    store = profiles.Store(copy.deepcopy(config.DEFAULTS), profiles._Told())
    store.flush = lambda: None
    store._save_timer.timeout.disconnect()
    store.front = profiles.FakeFront()
    return store


class Sets(unittest.TestCase):
    def test_what_the_two_sets_hold(self):  # AD01
        from mxoptions import i18n_uk
        self.assertEqual(config.APP_PRESET_KEYS, {"photoshop": BRUSH, "premiere": PAGES})
        self.assertEqual({preset: config.PRESET_SYSTEMS.get(preset) for preset in ("photoshop", "premiere")},
                         {"photoshop": "win32", "premiere": "win32"})
        for preset in ("photoshop", "premiere"):
            with self.subTest(preset=preset):
                _label, buttons, function = config.APP_PRESETS[preset]
                self.assertEqual((buttons, function), ({"back": UNDO, "forward": REDO}, "keys"))
                self.assertEqual([actions.describe(buttons[key]) for key in ("back", "forward")], ["Undo", "Redo"])
                self.assertTrue(all(map(config.thumb_combo, config.APP_PRESET_KEYS[preset].values())))
                note = pages.PRESET_NOTES[preset]
                self.assertIn(note, i18n_uk.STRINGS)
                self.assertTrue(note.startswith("Own choice") and note.endswith(LAYOUT), note)
                self.assertNotIn("Logitech", note)
        self.assertIn(PAIR_UNDO, i18n_uk.STRINGS)


class Giving(unittest.TestCase):
    """What applying a set writes: per field, only where the app does as in all apps, in one push, as copies."""

    def store(self):
        store = a_store()
        store.cfg["thumbwheel"].update(function="volume", up=F13, down=F14)  # all apps: kept as they are
        store.cfg["mouse"]["profiles"] = [profiles.profile(KATE, profiles.RING_A), profiles.profile(KONSOLE)]
        store.cfg["mouse"]["gestures"] = [{"app": KATE, "buttons": {"gesture": config.default_gestures()}}]
        store.cfg["mouse"]["thumbwheel_keys"] = [pair(KONSOLE, {"up": ["KEY_A"], "down": []})]
        return store

    def test_each_mix_of_own_function_and_own_keystrokes(self):  # AD02
        mine, everywhere = {"up": F14, "down": []}, {"up": F13, "down": F14}
        cases = [  # own function, own keystrokes -> function written, keystrokes written, then in that app
            (None, None, "keys", BRUSH, ("keys", BRUSH)),
            (None, mine, "keys", None, ("keys", mine)),  # its keystrokes stay; never dropped for the set's
            ("keys", None, None, BRUSH, ("keys", BRUSH)),
            ("keys", mine, None, None, ("keys", mine)),
            ("zoom", None, None, None, ("zoom", everywhere)),  # no keystrokes it would not send
            ("zoom", mine, None, None, ("zoom", mine)),
        ]
        for function, keys, wrote_function, wrote_keys, there in cases:
            with self.subTest(function=function, keys=keys):
                store = self.store()
                if function is not None:
                    store.cfg["mouse"]["thumbwheel"].append({"app": KATE, "function": function})
                if keys is not None:
                    store.cfg["mouse"]["thumbwheel_keys"].append(pair(KATE, keys))
                before = copy.deepcopy(store.cfg)
                self.assertEqual(config.preset_changes(store.cfg, KATE, "photoshop"),
                                 ({"back": UNDO, "forward": REDO}, wrote_function))
                self.assertEqual(config.preset_keys(store.cfg, KATE, "photoshop"), wrote_keys)
                self.assertTrue(store.apply_mouse_preset(KATE, "photoshop"))
                expected = copy.deepcopy(before)
                expected["mouse"]["profiles"][0]["buttons"].update(back=UNDO, forward=REDO)
                if wrote_function:
                    expected["mouse"]["thumbwheel"].append({"app": KATE, "function": wrote_function})
                if wrote_keys:
                    expected["mouse"]["thumbwheel_keys"].append(pair(KATE, wrote_keys))
                self.assertEqual(store.cfg, expected)  # ring, gestures, the other app, all apps: as they were
                self.assertEqual(store.engine.configs, [expected])  # in one go
                sent = config.thumbwheel(store.cfg, KATE)
                self.assertEqual((sent["function"], {"up": sent["up"], "down": sent["down"]}), there)
                self.assertEqual(config.clean(copy.deepcopy(store.cfg)), store.cfg)  # kept as they are when loaded

    def test_copies_of_their_own_for_each_app(self):  # AD03
        store = self.store()
        store.cfg["mouse"]["thumbwheel_keys"] = []
        for app in (KATE, KONSOLE):
            store.apply_mouse_preset(app, "premiere")
        entries = store.cfg["mouse"]["thumbwheel_keys"]
        self.assertEqual([entry["app"] for entry in entries], [KATE, KONSOLE])
        entries[0]["up"].append("KEY_F1")
        entries[0]["down"].append("KEY_F2")
        self.assertEqual(entries[1], pair(KONSOLE, PAGES))
        self.assertEqual(config.APP_PRESET_KEYS["premiere"], PAGES)

    def test_only_what_is_left_is_given_and_nothing_is_not_pushed(self):  # AD04
        for own_keys in (None, {"up": F13, "down": F14}):
            with self.subTest(own_keys=own_keys):
                store = self.store()
                store.cfg["mouse"]["profiles"][0]["buttons"].update(back=profiles.COPY, forward=profiles.PASTE)
                store.cfg["mouse"]["thumbwheel"].append({"app": KATE, "function": "keys"})
                if own_keys:
                    store.cfg["mouse"]["thumbwheel_keys"].append(pair(KATE, own_keys))
                expected = copy.deepcopy(store.cfg)
                if not own_keys:
                    expected["mouse"]["thumbwheel_keys"].append(pair(KATE, BRUSH))  # the keystrokes alone
                self.assertTrue(store.apply_mouse_preset(KATE, "photoshop"))
                self.assertFalse(store.apply_mouse_preset(KWRITE, "photoshop"))  # no profile
                self.assertEqual((store.cfg, store.engine.configs), (expected, [] if own_keys else [expected]))
        self.assertIsNone(config.preset_keys(store.cfg, KWRITE, "photoshop"))
        self.assertIsNone(config.preset_keys(store.cfg, KONSOLE, "spreadsheet"))  # a set without keystrokes

    def test_removed_and_added_again(self):  # AD05
        store = self.store()
        store.apply_mouse_preset(KATE, "photoshop")
        store.remove_mouse_profile(KATE)
        self.assertNotIn(KATE, [entry["app"] for name in ("thumbwheel", "thumbwheel_keys", "gestures")
                                for entry in store.cfg["mouse"][name]])
        store.add_mouse_profile(KATE)
        self.assertEqual(config.preset_keys(store.cfg, KATE, "photoshop"), BRUSH)


class Offering(unittest.TestCase):
    """The Buttons page: the two sets on Windows only, the preview naming every write, stale answers refused."""

    def page(self, store, windows):
        prerender = mock.patch.object(pages.MouseView, "_prerender")  # no render workers: they would outlive the test
        prerender.start()
        self.addCleanup(prerender.stop)
        system = "win32" if windows else "linux"
        with mock.patch.object(config, "offered_presets", lambda platform=None: OFFERED(platform or system)):
            page = pages.ButtonsPage(store)
        self.addCleanup(self.gone, page)
        page.resize(1100, 700)
        page.show()
        return page

    @staticmethod
    def gone(page):
        """Hidden and deleted before the next test: nothing of it is left to answer timers or the Store."""
        page.hide()
        page.deleteLater()
        profiles.APP.sendPostedEvents(page, QEvent.Type.DeferredDelete)

    def chooser(self, store, windows=True):
        store.set_mouse_app(KATE)
        return self.page(store, windows).apps

    def entry(self, chooser, name):
        offered = {action.text(): action for action in chooser.suggest.menu().actions()}
        self.assertIn(name, offered)
        return offered[name]

    def test_offered_on_windows_only(self):  # AD06
        self.assertEqual(OFFERED("win32")[-2:], ["photoshop", "premiere"])
        self.assertEqual([preset for preset in OFFERED("linux") if preset in ("photoshop", "premiere")], [])
        for windows, system in ((False, "linux"), (True, "win32")):
            with self.subTest(windows=windows):
                store = a_store()
                store.cfg["mouse"]["profiles"] = [profiles.profile(KATE)]
                shown = [action.text() for action in self.chooser(store, windows).suggest.menu().actions()]
                self.assertEqual(shown, [config.APP_PRESETS[preset][0] for preset in OFFERED(system)])
                self.assertEqual(("Photoshop" in shown, "Premiere" in shown), (windows, windows))

    def ask(self, store, chooser, name, reply=QMessageBox.StandardButton.No, change=lambda: None):
        asked = []

        def question(_parent, _title, text):
            asked.append(text)
            change()
            return reply

        with mock.patch.object(pages.QMessageBox, "question", question), \
                mock.patch.object(pages.QMessageBox, "information") as information:
            self.entry(chooser, name).trigger()
        return asked[-1].split("\n"), information

    def test_the_preview_names_what_changes_and_what_stays(self):  # AD07
        head = ['Apply the suggested settings "Photoshop" to kate?', ""]
        tail = ["", NOTE_PS, "", EVERYTHING]
        cases = [
            (None, None, ["Back button: Undo", "Forward button: Redo", "Thumb wheel: Custom keystrokes",
                          "Turning up: ]", "Turning down: ["], [PAIR_UNDO]),
            (None, {"up": F14, "down": []},
             ["Back button: Undo", "Forward button: Redo", "Thumb wheel: Custom keystrokes",
              "Turning up: stays as set for this app (F14)", "Turning down: stays as set for this app (Do nothing)"], []),
            ("zoom", None, ["Back button: Undo", "Forward button: Redo",
                            "Thumb wheel: stays as set for this app (Zoom)"], []),
        ]
        for function, keys, body, after in cases:
            with self.subTest(function=function, keys=keys):
                store = a_store()
                store.cfg["mouse"]["profiles"] = [profiles.profile(KATE)]
                if function:
                    store.cfg["mouse"]["thumbwheel"] = [{"app": KATE, "function": function}]
                if keys:
                    store.cfg["mouse"]["thumbwheel_keys"] = [pair(KATE, keys)]
                before = copy.deepcopy(store.cfg)
                lines, _information = self.ask(store, self.chooser(store), "Photoshop")
                self.assertEqual(lines, head + body + tail + after)
                self.assertEqual(store.cfg, before)  # No: nothing written
                self.assertNotIn("pans", "\n".join(lines))

    def test_a_changed_preview_writes_nothing(self):  # AD08
        cases = {
            "keys_recorded": lambda store: store.set_profile_thumb_keys(KATE, {"up": F13, "down": []}),
            "keys_dropped": lambda store: store.set_profile_thumb_keys(KATE, None),
            "function_other": lambda store: store.set_profile_thumb(KATE, "zoom"),
            "profile_recreated": lambda store: (store.remove_mouse_profile(KATE), store.add_mouse_profile(KATE),
                                                store.set_profile_thumb(KATE, "keys")),
            "settings_replaced": lambda store: store.replace(copy.deepcopy(store.cfg)),
        }
        for case, change in cases.items():
            with self.subTest(case=case):
                store = a_store()
                store.cfg["mouse"]["profiles"] = [profiles.profile(KATE)]
                store.cfg["mouse"]["thumbwheel"] = [{"app": KATE, "function": "keys"}]  # function: the set's already
                if case == "keys_dropped":
                    store.cfg["mouse"]["thumbwheel_keys"] = [pair(KATE, {"up": F14, "down": F13})]
                chooser = self.chooser(store)
                during = {}

                def changed():
                    change(store)
                    during["settings"] = copy.deepcopy(store.cfg)
                    during["pushes"] = len(store.engine.configs)
                    during["selection"] = store.mouse_app

                _lines, information = self.ask(store, chooser, "Photoshop", QMessageBox.StandardButton.Yes, changed)
                self.assertEqual((store.cfg, len(store.engine.configs)), (during["settings"], during["pushes"]))
                information.assert_called_once_with(chooser, pages.APP_NAME, STALE)
                self.assertEqual(store.mouse_app, during["selection"])  # never chosen anew here
                store._save_timer.stop()

    def test_yes_gives_exactly_the_preview(self):  # AD09
        store = a_store()
        store.cfg["mouse"]["profiles"] = [profiles.profile(KATE, forward=profiles.PASTE)]
        chooser = self.chooser(store)
        lines, information = self.ask(store, chooser, "Premiere", QMessageBox.StandardButton.Yes,
                                      lambda: chooser.apps.setCurrentIndex(0))  # all apps chosen meanwhile
        self.assertEqual(lines[2:7], ["Back button: Undo", "Forward button: stays as set for this app (Paste)",
                                      "Thumb wheel: Custom keystrokes", "Turning up: PgDn", "Turning down: PgUp"])
        information.assert_not_called()
        self.assertEqual(store.mouse_profile(KATE)["buttons"], {"back": UNDO, "forward": profiles.PASTE})
        self.assertEqual(config.thumbwheel(store.cfg, KATE)["function"], "keys")
        self.assertEqual(store.cfg["mouse"]["thumbwheel_keys"], [pair(KATE, PAGES)])
        self.assertEqual(len(store.engine.configs), 1)
        store._save_timer.stop()

    # The pair a written "keys" wakes (P1): the app's own pair, unused before, is part of what Yes gives.
    ASLEEP = {"up": F13, "down": F15}
    SETS = (("photoshop", "Photoshop"), ("premiere", "Premiere"))

    def asleep(self, function=None):
        """kate with a pair of its own that its thumb wheel does not send now (all apps: hscroll), or, with
        ``function``, a thumb wheel function of its own as well."""
        store = a_store()
        store.cfg["mouse"]["profiles"] = [profiles.profile(KATE)]
        store.cfg["mouse"]["thumbwheel_keys"] = [pair(KATE, self.ASLEEP)]
        if function is not None:
            store.cfg["mouse"]["thumbwheel"] = [{"app": KATE, "function": function}]
        self.assertEqual(config.thumbwheel(store.cfg, KATE)["function"], function or "hscroll")
        self.addCleanup(store._save_timer.stop)
        return store

    def meanwhile(self, store, change):
        """``change`` while the dialog is open; what the settings and the pushes were right after it."""
        during = {}

        def changed():
            change()
            during.update(settings=copy.deepcopy(store.cfg), pushes=len(store.engine.configs))
        return changed, during

    def given(self, settings, preset, function):
        """``settings`` with ``preset`` applied to kate as Yes should: its buttons, and its function if written."""
        expected = copy.deepcopy(settings)
        expected["mouse"]["profiles"][0]["buttons"].update(copy.deepcopy(config.APP_PRESETS[preset][1]))
        if function is not None:
            expected["mouse"]["thumbwheel"].append({"app": KATE, "function": function})
        return expected

    def test_a_pair_the_function_would_wake_changed_meanwhile_writes_nothing(self):  # AD14
        for preset, label in self.SETS:
            for case, changed in (("other_keys", {"up": F14, "down": F15}), ("emptied", {"up": F13, "down": []})):
                with self.subTest(preset=preset, case=case):
                    store = self.asleep()
                    chooser = self.chooser(store)
                    change, during = self.meanwhile(
                        store, lambda: self.assertTrue(store.set_profile_thumb_keys(KATE, changed)))
                    lines, information = self.ask(store, chooser, label, YES, change)
                    self.assertEqual(lines[4:7], ["Thumb wheel: Custom keystrokes",
                                                  "Turning up: stays as set for this app (F13)",
                                                  "Turning down: stays as set for this app (F15)"])
                    information.assert_called_once_with(chooser, pages.APP_NAME, STALE)
                    self.assertEqual(store.cfg, during["settings"])  # exactly as changed meanwhile
                    self.assertEqual(len(store.engine.configs), during["pushes"])  # nothing pushed for the set
                    self.assertEqual(config.app_thumb_keys(store.cfg, KATE), changed)
                    self.assertIsNone(config.app_thumb_function(store.cfg, KATE))  # still asleep

    def test_an_unchanged_or_fresh_preview_wakes_the_pair_it_named(self):  # AD15
        for preset, label in self.SETS:
            with self.subTest(preset=preset, case="unchanged"):
                store = self.asleep()
                chooser = self.chooser(store)
                before = copy.deepcopy(store.cfg)
                lines, information = self.ask(store, chooser, label, YES)
                self.assertEqual(lines[5:7], ["Turning up: stays as set for this app (F13)",
                                              "Turning down: stays as set for this app (F15)"])
                information.assert_not_called()
                self.assertEqual(store.cfg, self.given(before, preset, "keys"))
                self.assertEqual(len(store.engine.configs), 1)
                sent = config.thumbwheel(store.cfg, KATE)
                self.assertEqual((sent["function"], sent["up"], sent["down"]), ("keys", F13, F15))
            with self.subTest(preset=preset, case="fresh"):
                store = self.asleep()
                chooser = self.chooser(store)
                other = {"up": F14, "down": []}
                _lines, stale = self.ask(store, chooser, label, YES,
                                         lambda: self.assertTrue(store.set_profile_thumb_keys(KATE, other)))
                stale.assert_called_once_with(chooser, pages.APP_NAME, STALE)
                before, pushes = copy.deepcopy(store.cfg), len(store.engine.configs)
                lines, information = self.ask(store, chooser, label, YES)  # chosen again: the new pair named
                self.assertEqual(lines[5:7], ["Turning up: stays as set for this app (F14)",
                                              "Turning down: stays as set for this app (Do nothing)"])
                information.assert_not_called()
                self.assertEqual(store.cfg, self.given(before, preset, "keys"))
                self.assertEqual(len(store.engine.configs), pushes + 1)
                sent = config.thumbwheel(store.cfg, KATE)
                self.assertEqual((sent["function"], sent["up"], sent["down"]), ("keys", F14, []))

    def test_a_pair_already_awake_or_left_asleep_may_change_meanwhile(self):  # AD16
        other = {"up": F14, "down": []}
        cases = [(preset, label, "keys", None) for preset, label in self.SETS]  # awake: only buttons written
        cases.append(("documents", "Documents and slides", None, "zoom"))  # asleep, and Zoom keeps it asleep
        for preset, label, function, written in cases:
            with self.subTest(preset=preset):
                store = self.asleep(function)
                chooser = self.chooser(store)
                change, during = self.meanwhile(
                    store, lambda: self.assertTrue(store.set_profile_thumb_keys(KATE, other)))
                _lines, information = self.ask(store, chooser, label, YES, change)
                information.assert_not_called()
                expected = self.given(during["settings"], preset, written)
                self.assertEqual(store.cfg, expected)  # the pair as changed meanwhile, and the set
                self.assertEqual(store.engine.configs[during["pushes"]:], [expected])  # in one go
                self.assertEqual(config.app_thumb_keys(store.cfg, KATE), other)

    def test_an_unrelated_setting_may_change_meanwhile(self):  # AD17
        for preset, label in self.SETS:
            with self.subTest(preset=preset):
                store = self.asleep()
                self.assertIs(store.cfg["haptics"]["battery_saving"], True)
                pushes = len(store.engine.configs)

                def saving():
                    store.set("haptics", "battery_saving", False)
                    self.assertEqual(len(store.engine.configs), pushes + 1)  # a real change, pushed
                    self.assertIs(store.engine.configs[-1]["haptics"]["battery_saving"], False)
                change, during = self.meanwhile(store, saving)
                _lines, information = self.ask(store, self.chooser(store), label, YES, change)
                information.assert_not_called()
                expected = self.given(during["settings"], preset, "keys")
                self.assertEqual(store.cfg, expected)
                self.assertEqual(store.engine.configs[during["pushes"]:], [expected])
                self.assertIs(store.cfg["haptics"]["battery_saving"], False)
                self.assertEqual(config.app_thumb_keys(store.cfg, KATE), self.ASLEEP)


class Sending(unittest.TestCase):
    """What the set makes the mouse send in that app, through a real Executor and vinput.Holds on a FakeInput."""

    def setUp(self):
        self.clock = support.Clock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__)

    def given(self, preset, apps=(KATE,)):
        store = a_store()
        store.cfg["mouse"]["profiles"] = [profiles.profile(app) for app in apps] + [profiles.profile(KONSOLE)]
        for app in apps:
            store.apply_mouse_preset(app, preset)
        return store.cfg

    def test_the_thumb_wheel_sends_the_pair_in_that_app_only(self):  # AD10
        for preset, keys in (("photoshop", BRUSH), ("premiere", PAGES)):
            with self.subTest(preset=preset):
                made = tk.keys_engine(self, real=True)
                profiles.set_config(made, self.given(preset))
                profiles.front(made, KATE)
                tk.report(made, self.clock, tk.UP)
                tk.report(made, self.clock, tk.DOWN)
                up, down = keys["up"][0], keys["down"][0]
                self.assertEqual(made.vinput.sent, [(up, True), (up, False), (down, True), (down, False)])
                profiles.front(made, KONSOLE)  # all apps: horizontal scroll, nothing typed
                tk.report(made, self.clock, tk.UP)
                self.assertEqual(len(made.vinput.sent), 4)
                self.assertEqual(profiles.held(made), set())

    def rig(self, apps=(KATE,)):
        made = profiles.make(self, cfg=self.given("photoshop", apps))
        made.mouse = profiles.ThumbMouse()  # its thumb wheel's reporting is written too, on the way between apps
        profiles.front(made, KATE)
        return made

    def test_back_holds_undo_until_let_go(self):  # AD11
        made = self.rig()
        made._button_down(profiles.BACK)
        self.assertEqual(made.vinput.sent, [(CTRL, True), (Z, True)])
        self.assertEqual(profiles.held(made), {CTRL, Z})  # held, not repeated: nothing more until let go
        made._button_up(profiles.BACK)
        self.assertEqual(made.vinput.sent[2:], [(Z, False), (CTRL, False)])
        made._button_down(profiles.FORWARD)
        made._button_up(profiles.FORWARD)
        self.assertEqual(made.vinput.sent[4:], [(CTRL, True), (SHIFT, True), (Z, True), (Z, False), (SHIFT, False),
                                                (CTRL, False)])

    def test_another_app_lets_go_and_an_equal_copy_holds_on(self):  # AD12
        made = self.rig(apps=(KATE, KWRITE))
        made._button_down(profiles.BACK)
        profiles.front(made, KWRITE)  # the same set: the same keys held on
        self.assertEqual(made.vinput.sent, [(CTRL, True), (Z, True)])
        profiles.front(made, KONSOLE)  # the mouse's own back button there: let go of once
        self.assertEqual(made.vinput.sent[2:], [(Z, False), (CTRL, False)])
        made._button_up(profiles.BACK)
        self.assertEqual((len(made.vinput.sent), profiles.held(made)), (4, set()))

    def test_the_keyboard_s_ctrl_reset_and_a_refused_key(self):  # AD13
        made = self.rig()
        keyboard = actions.Executor(made.vinput, lambda action, phase: None, owner="keyboard")
        self.addCleanup(profiles.stop, self, keyboard)
        keyboard.press(actions.keys("LEFTCTRL"))
        made._button_down(profiles.BACK)
        made._button_up(profiles.BACK)
        self.assertEqual(made.vinput.sent, [(CTRL, True), (Z, True), (Z, False)])  # Ctrl stays the keyboard's
        made._button_down(profiles.BACK)
        made.executor.reset()
        self.assertEqual(made.vinput.sent[3:], [(Z, True), (Z, False)])
        self.assertEqual(profiles.held(made), {CTRL})
        keyboard.release(actions.keys("LEFTCTRL"))
        made._button_up(profiles.BACK)
        emit = made.vinput._holds._emit = Refusing(trips=[(Z, True)])
        with self.assertRaises(OSError):
            made._button_down(profiles.BACK)
        made._button_up(profiles.BACK)
        self.assertEqual(emit.calls, [(CTRL, True), (Z, True), (Z, False), (CTRL, False)])
        self.assertEqual((emit.active, profiles.held(made)), (set(), set()))


if __name__ == "__main__":
    unittest.main()
