"""Thumb wheel keystrokes of single apps (mouse.thumbwheel_keys): the settings, the engine sending the pair of the app
in front, the Store, other programs and logs, and the Thumb wheel card with an app chosen. The mouse and the virtual
input are synthetic (tests/support.py); app names are made up."""

import copy
import json
import logging
import unittest

import tests.support as support
import tests.test_holds as holds_tests
import tests.test_mouse_profiles as profiles
import tests.test_thumb_keys as tk
from mxoptions import actions, api, config, device
from mxoptions.ui.store import Store
from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible
from PySide6.QtTest import QTest

KATE, KONSOLE, KWRITE = profiles.KATE, profiles.KONSOLE, profiles.KWRITE
CTRL, SHIFT, Z = tk.CTRL, tk.SHIFT, tk.Z
UNDO, REDO, UP, DOWN, HALF_UP = tk.UNDO, tk.REDO, tk.UP, tk.DOWN, tk.HALF_UP
F13, F14 = ["KEY_F13"], ["KEY_F14"]
NOTHING = "Click to record a shortcut"


def pair(app, up, down):
    return {"app": app, "up": up, "down": down}


def a_store():
    store = Store(copy.deepcopy(config.DEFAULTS), profiles._Engine())
    store.flush = lambda: None
    store._save_timer.timeout.disconnect()
    return store


class PairSettings(unittest.TestCase):
    """mouse.thumbwheel_keys in the settings: kept, emptied or left out, and what counts in each app."""

    def cleaned(self, entries, apps=(KATE, KONSOLE)):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["mouse"]["profiles"] = [profiles.profile(app) for app in apps]
        cfg["mouse"]["thumbwheel_keys"] = copy.deepcopy(entries)
        return config.clean(cfg)["mouse"]["thumbwheel_keys"]

    def test_defaults_hold_no_pair(self):
        cleaned = config.clean(copy.deepcopy(config.DEFAULTS))
        self.assertEqual(cleaned["mouse"], {"profiles": [], "thumbwheel": [], "thumbwheel_keys": [], "gestures": []})
        self.assertEqual(cleaned["version"], 6)

    def test_a_pair_of_an_app_with_a_profile_is_kept_and_the_rest_left_out(self):
        mixed = [{"app": KATE, "up": ["BTN_LEFT"], "extra": 1}, pair(KATE, F13, F14), pair(KWRITE, F13, F14), "x",
                 {"app": 3}, {"app": KONSOLE}]
        self.assertEqual(self.cleaned(mixed), [pair(KATE, [], []), pair(KONSOLE, [], [])])
        for value in (None, "x", {}, 3):
            with self.subTest(value=value):
                self.assertEqual(self.cleaned(value), [])
        sixteen = ["KEY_" + c for c in "ABCDEFGHIJKLMNOP"]
        for value in (sixteen, [SHIFT, CTRL], [CTRL, CTRL, Z]):
            with self.subTest(value=value):
                kept = [pair(KATE, list(value), value[::-1]), pair(KONSOLE, [], list(value))]
                self.assertEqual(self.cleaned(kept), kept)
        bad = (None, "KEY_Z", {"KEY_Z": 1}, 3, [1], ["KEY_NOPE"], ["BTN_LEFT"], [CTRL, "BTN_EXTRA"],
               ["KEY_" + c for c in "ABCDEFGHIJKLMNOPQ"], [["KEY_A"]], [None])
        for value in bad:
            for direction in ("up", "down"):
                with self.subTest(value=value, direction=direction):
                    asked = dict(pair(KATE, UNDO, REDO), **{direction: copy.deepcopy(value)})
                    self.assertEqual(self.cleaned([asked]), [dict(pair(KATE, UNDO, REDO), **{direction: []})])

    def test_what_counts_in_an_app_is_its_own_pair_or_that_of_all_apps(self):
        cfg = copy.deepcopy(config.DEFAULTS)  # as the engine may hold it: not cleaned
        cfg["thumbwheel"].update(up=UNDO, down=REDO)
        cfg["mouse"]["profiles"] = [profiles.profile(KATE)]
        cfg["mouse"]["thumbwheel_keys"] = [pair(KATE, [], F14), pair(KWRITE, F13, F14)]
        self.assertEqual([config.thumbwheel(cfg, app)[d] for app in (KATE, KWRITE, None) for d in ("up", "down")],
                         [[], F14, UNDO, REDO, UNDO, REDO])
        self.assertIsNone(config.app_thumb_keys(cfg, KWRITE))
        self.assertIsNone(config.app_thumb_keys(cfg, None))
        self.assertEqual(config.app_thumb_keys(cfg, KATE), {"up": [], "down": F14})

    def test_what_older_versions_saved_is_loaded(self):
        saved = {"version": 6, "thumbwheel": {"function": "keys", "up": UNDO, "down": REDO},
                 "mouse": {"profiles": [profiles.profile(app) for app in (KATE, KONSOLE, profiles.DOLPHIN)],
                           "thumbwheel": [tk.own(KATE, "keys")],
                           "thumbwheel_keys": [{"app": KATE, "up": ["KEY_RIGHTBRACE"]},
                                               {"app": KONSOLE, "up": ["BTN_LEFT"], "down": None},
                                               {"app": "org.example.gone", "up": ["KEY_A"], "down": ["KEY_B"]},
                                               pair(profiles.DOLPHIN, F13, F14)]}}
        cfg = config.upgrade(json.loads(json.dumps(saved)))
        self.assertEqual(cfg["mouse"]["thumbwheel_keys"], [pair(KATE, ["KEY_RIGHTBRACE"], []), pair(KONSOLE, [], []),
                                                           pair(profiles.DOLPHIN, F13, F14)])
        self.assertEqual((cfg["mouse"]["thumbwheel"], cfg["thumbwheel"]["up"], cfg["version"]),
                         ([tk.own(KATE, "keys")], UNDO, 6))
        del saved["mouse"]["thumbwheel_keys"]  # saved by a version without them
        self.assertEqual(config.upgrade(json.loads(json.dumps(saved)))["mouse"]["thumbwheel_keys"], [])


class PairSending(unittest.TestCase):
    """The engine sending the pair of the app in front, or that of all apps."""

    def setUp(self):
        self.clock = support.Clock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__)

    def made(self, pairs, entries=(), real=False):
        made = tk.keys_engine(self, real=real, apps=(KATE, KONSOLE), entries=list(entries))
        made.cfg["mouse"]["thumbwheel_keys"] = copy.deepcopy(pairs)
        return made

    def test_each_app_sends_its_own_pair_or_those_of_all_apps(self):
        made = self.made([pair(KATE, F13, [])])
        profiles.front(made, KATE)
        tk.report(made, self.clock, UP)
        tk.report(made, self.clock, DOWN)
        profiles.front(made, KONSOLE)
        tk.report(made, self.clock, UP)
        self.assertEqual(tk.ran(made), [F13, UNDO])

    def test_an_empty_direction_of_an_app_sends_nothing_and_leaves_the_gap(self):
        made = self.made([pair(KATE, F13, [])])
        profiles.front(made, KATE)
        tk.report(made, self.clock, DOWN)
        tk.report(made, self.clock, UP, after=0)
        self.assertEqual((tk.ran(made), made.played), ([F13], [device.WAVE_TICK]))
        profiles.front(made, KONSOLE)  # as in all apps
        tk.report(made, self.clock, DOWN)
        self.assertEqual(tk.ran(made), [F13, REDO])

    def test_another_pair_in_front_drops_half_a_step_without_a_write(self):
        made = self.made([pair(KATE, F13, F14)])
        profiles.front(made, KATE)
        before = len(profiles.thumbs(made))
        tk.report(made, self.clock, HALF_UP)
        profiles.front(made, KONSOLE)
        tk.report(made, self.clock, HALF_UP)
        self.assertEqual((tk.ran(made), len(profiles.thumbs(made)) - before), ([], 0))

    def test_the_same_keystrokes_in_front_go_on(self):
        made = self.made([pair(KATE, UNDO, REDO)])
        profiles.front(made, KATE)
        tk.report(made, self.clock, HALF_UP)
        profiles.front(made, KONSOLE)
        tk.report(made, self.clock, HALF_UP)
        self.assertEqual(tk.ran(made), [UNDO])

    def test_changing_an_app_not_in_front_changes_nothing_here(self):
        made = self.made([pair(KATE, F13, F14), pair(KONSOLE, F14, F13)])
        profiles.front(made, KATE)
        before = len(profiles.thumbs(made))
        tk.report(made, self.clock, HALF_UP)
        profiles.edited(made, lambda cfg: cfg["mouse"]["thumbwheel_keys"][1].update(up=["KEY_A"]))
        tk.report(made, self.clock, HALF_UP)
        self.assertEqual((tk.ran(made), len(profiles.thumbs(made)) - before), ([F13], 0))

    def test_changing_the_pair_in_front_drops_half_a_step_online_or_not(self):
        for online, writes in ((True, 1), (False, 0)):
            with self.subTest(online=online):
                made = self.made([pair(KATE, F13, F14)])
                profiles.front(made, KATE)
                made.online = online
                before = len(profiles.thumbs(made))
                tk.report(made, self.clock, HALF_UP)
                profiles.edited(made, lambda cfg: cfg["mouse"]["thumbwheel_keys"][0].update(up=["KEY_A"]))
                tk.report(made, self.clock, HALF_UP)
                self.assertEqual((tk.ran(made), len(profiles.thumbs(made)) - before), ([], writes))

    def test_removing_the_profile_in_front_falls_back_to_all_apps(self):
        made = self.made([pair(KATE, F13, F14)])
        profiles.front(made, KATE)
        tk.report(made, self.clock, HALF_UP)
        profiles.edited(made, lambda cfg: cfg["mouse"].update(profiles=[profiles.profile(KONSOLE)]))
        tk.report(made, self.clock, HALF_UP)
        tk.report(made, self.clock, UP)
        self.assertEqual(tk.ran(made), [UNDO])

    def test_the_ring_turns_its_dials_whatever_the_pair(self):
        made = self.made([pair(KATE, F13, F14)])
        profiles.front(made, KATE)
        made.set_ring_open(True)
        profiles.drain(made)
        tk.report(made, self.clock, UP)
        self.assertEqual((made.listener.turns, tk.ran(made)), ([1.0], []))

    def test_an_app_with_another_function_keeps_its_pair_unused(self):
        made = self.made([pair(KATE, F13, F14)], entries=[tk.own(KATE, "volume")])
        profiles.front(made, KATE)
        tk.report(made, self.clock, UP)
        self.assertEqual(tk.ran(made), [["KEY_VOLUMEUP"]])
        profiles.edited(made, lambda cfg: cfg["mouse"].update(thumbwheel=[]))  # keys, as in all apps
        tk.report(made, self.clock, UP)
        self.assertEqual(tk.ran(made), [["KEY_VOLUMEUP"], F13])

    def test_one_keystroke_per_report_with_a_pair_of_its_own(self):
        made = self.made([pair(KATE, F14, F13)], real=True)
        profiles.front(made, KATE)
        tk.report(made, self.clock, 32767)
        self.assertEqual(made.vinput.sent, [("KEY_F13", True), ("KEY_F13", False)])
        tk.report(made, self.clock, 32767, after=0, times=200)
        self.assertEqual(made.vinput.sent, [("KEY_F13", True), ("KEY_F13", False)])
        self.assertEqual(profiles.held(made), set())
        tk.report(made, self.clock, DOWN)
        self.assertEqual(made.vinput.sent, [("KEY_F13", True), ("KEY_F13", False)] * 2)


class PairStore(unittest.TestCase):
    """The pairs in the Store, for other programs (Set, Get), in settings files and in logs."""

    def setUp(self):
        self.store = a_store()
        self.store.add_mouse_profile(KATE)
        self.pushed = []
        self.store.changed.connect(lambda: self.pushed.append(1))

    def pairs(self):
        return self.store.cfg["mouse"]["thumbwheel_keys"]

    def test_the_store_sets_and_refuses_pairs(self):
        store = self.store
        self.assertFalse(store.set_profile_thumb_keys(KWRITE, {"up": F13, "down": []}))  # no profile
        before = copy.deepcopy(store.cfg)
        for bad in ({"up": F13}, {"up": ["BTN_LEFT"], "down": []}, {"up": F13, "down": None}, [F13, F14], "x",
                    {"up": F13, "down": [], "app": KONSOLE}):
            self.assertFalse(store.set_profile_thumb_keys(KATE, bad), bad)
            self.assertEqual(store.cfg, before)
        self.assertEqual(self.pushed, [])
        given = {"up": F13, "down": []}
        self.assertTrue(store.set_profile_thumb_keys(KATE, given))
        self.assertEqual(self.pairs(), [pair(KATE, F13, [])])
        self.assertIsNot(self.pairs()[0]["up"], given["up"])
        self.assertTrue(store.set_profile_thumb_keys(KATE, {"up": F13, "down": []}))
        self.assertEqual(len(self.pushed), 1)  # the same again: nothing new
        self.assertTrue(store.set_profile_thumb_keys(KATE, {"up": F13, "down": F14}))
        self.assertEqual((self.pairs(), len(self.pushed)), ([pair(KATE, F13, F14)], 2))
        self.assertTrue(store.set_profile_thumb_keys(KATE, None))
        self.assertTrue(store.set_profile_thumb_keys(KATE, None))
        self.assertEqual((self.pairs(), len(self.pushed)), ([], 3))

    def test_removing_a_profile_takes_its_pair_and_adding_it_again_does_not_bring_it_back(self):
        store = self.store
        store.add_mouse_profile(KONSOLE)
        store.set_profile_thumb_keys(KATE, {"up": F13, "down": []})
        store.remove_mouse_profile(KATE)
        store.add_mouse_profile(KATE)
        self.assertEqual(self.pairs(), [])
        self.assertIsNone(config.app_thumb_keys(store.cfg, KATE))
        store.set_profile_thumb_keys(KONSOLE, {"up": F14, "down": F13})
        del self.pushed[:]
        store.remove_mouse_profile(KATE)  # an app without a pair: the others' stay
        self.assertEqual((self.pairs(), len(self.pushed)), ([pair(KONSOLE, F14, F13)], 1))

    def test_restoring_the_defaults_removes_every_pair(self):
        self.store.set_profile_thumb_keys(KATE, {"up": F13, "down": F14})
        self.store.restore_defaults()
        self.assertEqual(self.pairs(), [])

    def test_export_and_import_keep_the_pairs(self):
        store = self.store
        store.add_mouse_profile(KONSOLE)
        store.set_profile_thumb_keys(KATE, {"up": F13, "down": []})
        store.set_profile_thumb_keys(KONSOLE, {"up": [], "down": REDO})
        store.cfg["flow"]["id"] = "this-one"
        current = copy.deepcopy(store.cfg)
        current["flow"]["id"] = "here"
        back = config.import_data(config.export_data(store.cfg), current)
        self.assertEqual(back["mouse"]["thumbwheel_keys"], [pair(KATE, F13, []), pair(KONSOLE, [], REDO)])
        self.assertEqual((back["mouse"]["thumbwheel_keys"], back["flow"]["id"]), (self.pairs(), "here"))

    def test_other_programs_set_pairs_that_may_be_and_are_refused_the_rest(self):
        dotted = "org.kde.dolphin.extra"
        self.store.add_mouse_profile(dotted)
        mouse = api.MouseApi(self.store, None, lambda: None)
        good = [pair(KATE, UNDO, []), pair(dotted, F13, F14)]
        accepted = [("mouse.thumbwheel_keys", good), ("mouse.thumbwheel_keys.0.up", REDO),
                    ("mouse.thumbwheel_keys.0.down", []), ("mouse.thumbwheel_keys.1.up.0", "KEY_F15"),
                    ("mouse.thumbwheel_keys.0", pair(KATE, F13, [])), ("mouse.thumbwheel_keys", [])]
        for path, value in accepted:
            with self.subTest(path=path, value=value):
                self.assertTrue(mouse.Set(path, json.dumps(value)))
                self.assertEqual(json.loads(mouse.Get(path)), value)
        self.assertTrue(mouse.Set("mouse.thumbwheel_keys", json.dumps(good)))
        before = copy.deepcopy(self.store.cfg)
        refused = [("mouse.thumbwheel_keys.0.up", bad) for bad in (
            ["KEY_NOPE"], ["BTN_LEFT"], ["KEY_A", "BTN_EXTRA"], [1], ["KEY_" + c for c in "ABCDEFGHIJKLMNOPQ"],
            [["KEY_A"]], None, {}, "KEY_A")]
        refused += [("mouse.thumbwheel_keys.0.up.0", "KEY_NOPE"), ("mouse.thumbwheel_keys.0.up.0", "BTN_LEFT"),
                    ("mouse.thumbwheel_keys.0.app", KWRITE), ("mouse.thumbwheel_keys.2", pair(KATE, F13, F14)),
                    ("mouse.thumbwheel_keys.0", None),
                    ("mouse.thumbwheel_keys", [pair(KWRITE, F13, F14)]),
                    ("mouse.thumbwheel_keys", [{"app": KATE, "up": F13}]),
                    ("mouse.thumbwheel_keys", [dict(pair(KATE, F13, F14), function="keys")]),
                    ("mouse.thumbwheel_keys", good + [pair(KATE, F14, F13)]),
                    ("mouse.thumbwheel_keys", [pair(KATE, None, F14)]),
                    ("mouse.thumbwheel_keys", {KATE: F13}),
                    ("mouse", dict(before["mouse"], thumbwheel_keys=[pair(KATE, ["BTN_LEFT"], [])])),
                    ("mouse.thumbwheel", [dict(tk.own(KATE, "keys"), up=F13)])]
        for path, value in refused:
            with self.subTest(path=path, value=value):
                self.assertFalse(mouse.Set(path, json.dumps(value)))
                self.assertEqual(self.store.cfg, before)
        self.assertEqual(json.loads(mouse.Get("mouse.thumbwheel_keys")), good)
        self.assertTrue(mouse.Set("mouse.profiles", json.dumps([self.store.mouse_profile(dotted)])))
        self.assertEqual(self.pairs(), [pair(dotted, F13, F14)])  # Kate's pair went with its profile

    def test_pairs_and_apps_are_never_logged(self):
        secret = "org.example.secret"
        with support.Clock() as clock, self.assertLogs("mxoptions", logging.DEBUG) as logs:
            logging.getLogger("mxoptions").debug("start")
            made = tk.keys_engine(self, real=True, apps=(secret,), entries=[tk.own(secret, "keys")])
            made.cfg["mouse"]["thumbwheel_keys"] = [pair(secret, [CTRL, "KEY_F13"], [])]
            profiles.front(made, secret)
            tk.report(made, clock, UP, times=3)
            self.assertEqual(made.vinput.sent.count(("KEY_F13", True)), 3)
            made.vinput._holds._emit = holds_tests.Refusing([("KEY_F13", True)])
            with self.assertRaises(OSError):
                tk.report(made, clock, UP)
            self.store.add_mouse_profile(secret)
            self.assertFalse(self.store.set_profile_thumb_keys(secret, {"up": ["KEY_F14", "BTN_EXTRA"], "down": []}))
            mouse = api.MouseApi(self.store, None, lambda: None)
            self.assertFalse(mouse.Set("mouse.thumbwheel_keys", json.dumps([pair(secret, ["KEY_F15", "KEY_NOPE"], [])])))
        text = "\n".join(logs.output)
        self.assertIn("refused", text)  # the Store's refusal was logged, without names
        for word in ("secret", "F13", "F14", "F15", "LEFTCTRL", "BTN_EXTRA", "NOPE"):
            self.assertNotIn(word, text)


class PairCard(unittest.TestCase):
    """The Thumb wheel card with an app chosen: its own pair, recorded, cleared, or that of all apps."""

    def setUp(self):
        self.store = tk.Card.make_store()
        self.store.add_mouse_profile(KATE)
        self.store.cfg["thumbwheel"].update(function="keys", up=UNDO, down=REDO)

    def page(self, app=None):
        page = tk.Card.page(self)
        if app is not None:
            page.apps.apps.setCurrentIndex(page.apps.apps.findData(app))
            self.assertEqual(self.store.mouse_app, app)
        return page

    def pairs(self):
        return self.store.cfg["mouse"]["thumbwheel_keys"]

    def active(self, page):
        page.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(page, 2000))

    def test_all_apps_has_no_row_for_an_app_and_the_tab_order_stays(self):
        page = self.page()
        self.assertTrue(page.thumb_keys_own_row.isHidden())
        self.assertEqual(tk.Card.visible(page), [True, True])
        order, widget = [], page.thumb_function
        while len(order) < 5:
            widget = widget.nextInFocusChain()
            if widget.isVisible() and widget.isEnabled() and widget.focusPolicy() & Qt.FocusPolicy.TabFocus:
                order.append(widget)
        up, down = page.thumb_keys["up"], page.thumb_keys["down"]
        self.assertEqual(order[:4], [up[1], up[2], down[1], down[2]])

    def test_recording_in_an_app_gives_it_a_pair_of_its_own_and_keeps_all_apps(self):
        page = self.page(KATE)
        up = page.thumb_keys["up"]
        self.assertFalse(page.thumb_keys_own_row.isHidden())
        self.assertFalse(page.thumb_keys_global.isEnabled())
        self.assertIn("As in all apps", page.thumb_keys_own_row.note.text())
        self.assertEqual(up[0].text(), actions.keys_label(UNDO))
        self.active(page)
        QTest.mouseClick(up[1], Qt.MouseButton.LeftButton)
        up[0].recorded.emit(F13)
        self.assertEqual(self.pairs(), [pair(KATE, F13, REDO)])
        self.assertEqual((self.store.cfg["thumbwheel"]["up"], self.store.cfg["thumbwheel"]["down"]), (UNDO, REDO))
        self.assertTrue(up[1].hasFocus())
        self.assertTrue(page.thumb_keys_global.isEnabled())
        self.assertIn("of its own", page.thumb_keys_own_row.note.text())
        self.assertEqual(up[0].text(), actions.keys_label(F13))

    def test_clearing_in_an_app_empties_its_own_direction(self):
        self.store.set_profile_thumb_keys(KATE, {"up": F13, "down": REDO})
        page = self.page(KATE)
        up, down = page.thumb_keys["up"], page.thumb_keys["down"]
        self.assertTrue(down[2].isEnabled())
        QTest.mouseClick(down[2], Qt.MouseButton.LeftButton)
        self.assertEqual(self.pairs(), [pair(KATE, F13, [])])
        self.assertEqual(self.store.cfg["thumbwheel"]["down"], REDO)
        self.assertEqual((down[0].text(), down[2].isEnabled()), (NOTHING, False))
        page.apps.apps.setCurrentIndex(0)
        page.apps.apps.setCurrentIndex(1)  # shown again from the settings
        self.assertEqual((up[0].text(), down[0].text()), (actions.keys_label(F13), NOTHING))

    def test_use_the_keystrokes_for_all_apps_from_the_keyboard(self):
        self.store.set_profile_thumb_keys(KATE, {"up": F13, "down": []})
        page = self.page(KATE)
        up, down = page.thumb_keys["up"], page.thumb_keys["down"]
        self.active(page)
        page.thumb_keys_global.setFocus()
        self.assertTrue(page.thumb_keys_global.hasFocus())
        QTest.keyClick(page.thumb_keys_global, Qt.Key.Key_Space)
        self.assertEqual(self.pairs(), [])
        self.assertTrue(up[1].hasFocus())
        self.assertFalse(page.thumb_keys_global.isEnabled())
        self.assertEqual((up[0].text(), down[0].text()), (actions.keys_label(UNDO), actions.keys_label(REDO)))
        self.assertEqual((self.store.cfg["thumbwheel"]["up"], self.store.cfg["thumbwheel"]["down"]), (UNDO, REDO))

    def test_names_hints_and_rows_in_both_languages(self):
        from mxoptions import i18n
        self.addCleanup(i18n.setup, "en")
        words = {"en": ("Use the keystrokes for all apps", "Keystrokes", f"{KATE} has keystrokes of its own.",
                        f"As in all apps. Recording or clearing one here gives {KATE} keystrokes of its own."),
                 "uk": ("Використовувати комбінації для всіх програм", "Комбінації клавіш",
                        f"{KATE} має власні комбінації клавіш.",
                        f"Як в усіх програмах. Запис чи очищення тут дасть {KATE} власні комбінації клавіш.")}
        for language, (button, title, mine, inherited) in words.items():
            with self.subTest(language=language):
                i18n.setup(language)
                self.setUp()
                page = self.page(KATE)
                named = QAccessible.queryAccessibleInterface(page.thumb_keys_global)
                self.assertEqual((page.thumb_keys_global.text(), named.text(QAccessible.Text.Name)), (button, button))
                self.assertTrue(page.thumb_keys_global.focusPolicy() & Qt.FocusPolicy.TabFocus)
                self.assertEqual((page.thumb_keys_own_row.title.text(), page.thumb_keys_own_row.note.text()),
                                 (title, inherited))
                self.store.set_profile_thumb_keys(KATE, {"up": F13, "down": []})
                self.assertEqual(page.thumb_keys_own_row.note.text(), mine)
                self.store.set_profile_thumb(KATE, "zoom")
                self.assertEqual((page.thumb_keys_own_row.isHidden(), tk.Card.visible(page)), (True, [False, False]))
                self.store.set_profile_thumb(KATE, None)
                self.store.set_profile_thumb_keys(KATE, None)
                self.assertEqual((page.thumb_keys_own_row.isHidden(), tk.Card.visible(page)), (False, [True, True]))
                self.store.set("thumbwheel", "function", "hscroll")
                self.assertEqual((page.thumb_keys_own_row.isHidden(), tk.Card.visible(page)), (True, [False, False]))


if __name__ == "__main__":
    unittest.main()
