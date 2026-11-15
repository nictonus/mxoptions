"""Gestures of their own for buttons of single apps ("mouse.gestures"): what the settings keep, the Store, other
programs, suggested settings, the engine following the app in front while such a button is held, and the Buttons
and Gestures pages. The mouse is synthetic (tests/support.py); app names are made up."""

import copy
import json
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import tests.support as support  # noqa: E402
import tests.test_mouse_profiles as mp  # noqa: E402
from tests.test_gestures import MotionInput  # noqa: E402
from mxoptions import APP_ID, actions, api, config, device, engine, vinput  # noqa: E402
from mxoptions.ui import gestures_page, pages  # noqa: E402
from mxoptions.ui.store import Store  # noqa: E402

APP = QApplication.instance() or QApplication([])
KATE, KONSOLE, KWRITE, GONE = "org.kde.kate", "org.kde.konsole", "org.kde.kwrite", "org.example.gone"
MIDDLE, GESTURE = device.CID_MIDDLE, device.CID_GESTURE
NONE = {"type": "none"}
F9 = {"type": "keys", "keys": ["KEY_F9"]}
COPY = actions.keys("LEFTCTRL", "C")
MID = {"type": "button", "button": "BTN_MIDDLE"}
PAN = {"type": "gestures", "tap": MID, "up": NONE, "down": NONE, "left": NONE, "right": NONE, "mode": "pan"}
ZOOM = dict(PAN, mode="zoom")
SWIPES = {"type": "gestures", "tap": F9, "up": F9, "down": NONE, "left": COPY, "right": NONE}
NO_MODE = {key: value for key, value in PAN.items() if key != "mode"}
profile = mp.profile


def two_apps():
    return [profile(KATE), profile(KONSOLE)]


def cfg_with(own, profiles=None):
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["mouse"]["profiles"] = two_apps() if profiles is None else copy.deepcopy(profiles)
    cfg["mouse"]["gestures"] = copy.deepcopy(own)
    return cfg


def cleaned(own, profiles=None):
    return config.clean(cfg_with(own, profiles))["mouse"]["gestures"]


def store_of(cfg):
    store = Store(copy.deepcopy(cfg), mp._Told())
    store.flush = lambda: None  # made-up settings: never written anywhere
    store._save_timer.timeout.disconnect()
    store.front = mp.FakeFront()
    return store


class Settings(unittest.TestCase):
    """What loading the settings keeps of mouse.gestures, and what counts in each app."""

    def test_own_gestures_of_an_app_are_kept_exactly(self):  # AG01
        own = [{"app": KONSOLE, "buttons": {"gesture": dict(SWIPES, shapes={"circle_cw": F9})}},
               {"app": KATE, "buttons": {"middle": PAN, "back": ZOOM}}]
        self.assertEqual(cleaned(own), own)
        self.assertEqual(config.DEFAULTS["mouse"]["gestures"], [])
        self.assertEqual(config.clean(copy.deepcopy(config.DEFAULTS))["mouse"]["gestures"], [])

    def test_what_an_app_button_cannot_have_is_left_out_or_does_nothing(self):  # AG02
        rows = {
            "unprofiled app dropped": ([{"app": GONE, "buttons": {"middle": PAN}}], []),
            "duplicate app: first only": ([{"app": KATE, "buttons": {"middle": PAN}}, {"app": KATE, "buttons": {"back": PAN}}],
                                          [{"app": KATE, "buttons": {"middle": PAN}}]),
            "not a dict, no buttons dict": (["x", None, {"app": KATE}, {"app": KATE, "buttons": []}, {"app": 3, "buttons": {}}], []),
            "unknown button key dropped": ([{"app": KATE, "buttons": {"left": PAN, "middle": PAN}}],
                                           [{"app": KATE, "buttons": {"middle": PAN}}]),
            "non-gestures action dropped, entry without buttons dropped": ([{"app": KATE, "buttons": {"middle": F9}}], []),
            "extra entry key dropped": ([{"app": KATE, "buttons": {"middle": PAN}, "x": 1}],
                                        [{"app": KATE, "buttons": {"middle": PAN}}]),
            "slot ring, default, dpi_shift, spotlight, unknown: none": (
                [{"app": KATE, "buttons": {"middle": dict(SWIPES, up={"type": "ring"}, down={"type": "default"},
                                                          left={"type": "dpi_shift"}, right={"type": "spotlight"},
                                                          tap={"type": "rotate"})}}],
                [{"app": KATE, "buttons": {"middle": dict(SWIPES, up=NONE, down=NONE, left=NONE, right=NONE, tap=NONE)}}]),
            "slot bad keys, bad macro step: none": (
                [{"app": KATE, "buttons": {"middle": dict(SWIPES, up={"type": "keys", "keys": ["KEY_NOPE"]},
                                                          tap={"type": "macro", "steps": [{"do": "nope"}]})}}],
                [{"app": KATE, "buttons": {"middle": dict(SWIPES, up=NONE, tap=NONE)}}]),
            "missing slot none, mouse slots kept": (
                [{"app": KATE, "buttons": {"middle": {"type": "gestures", "up": {"type": "wheel_mode"},
                                                      "down": {"type": "dpi_cycle"}, "left": {"type": "flow"}}}}],
                [{"app": KATE, "buttons": {"middle": {"type": "gestures", "tap": NONE, "up": {"type": "wheel_mode"},
                                                      "down": {"type": "dpi_cycle"}, "left": {"type": "flow"},
                                                      "right": NONE}}}]),
            "mouse slot with more keys: none": (
                [{"app": KATE, "buttons": {"middle": dict(SWIPES, up={"type": "flow", "x": 1})}}],
                [{"app": KATE, "buttons": {"middle": dict(SWIPES, up=NONE)}}]),
            "mode rotate, list, dict dropped": (
                [{"app": KATE, "buttons": {"middle": dict(PAN, mode="rotate"), "back": dict(PAN, mode=["pan"]),
                                           "forward": dict(PAN, mode={"pan": 1})}}],
                [{"app": KATE, "buttons": {"middle": NO_MODE, "back": NO_MODE, "forward": NO_MODE}}]),
            "bad shapes dropped, shape doing nothing dropped": (
                [{"app": KATE, "buttons": {"middle": dict(SWIPES, shapes={"circle_cw": F9, "nope": F9, "up_down": NONE,
                                                                          "zigzag_h": {"type": "ring"}})}}],
                [{"app": KATE, "buttons": {"middle": dict(SWIPES, shapes={"circle_cw": F9})}}]),
            "extra action key dropped": ([{"app": KATE, "buttons": {"middle": dict(PAN, speed=3)}}],
                                         [{"app": KATE, "buttons": {"middle": PAN}}]),
            "not a list": ({"app": KATE}, []),
        }
        for name, (given, expected) in rows.items():
            with self.subTest(name):
                self.assertEqual(cleaned(given), expected)

    def test_a_button_the_profile_lists_keeps_that_action_and_others_resolve_in_order(self):  # AG03
        profiles = [profile(KATE, middle=F9), profile(KONSOLE)]
        cfg = config.clean(cfg_with([{"app": KATE, "buttons": {"middle": PAN, "back": PAN}}], profiles))
        self.assertEqual(cfg["mouse"]["gestures"], [{"app": KATE, "buttons": {"back": PAN}}])  # exactly: middle left out
        kate = config.mouse_buttons(cfg, KATE)
        self.assertEqual((kate["middle"], kate["back"]), (F9, PAN))  # the profile, then its own gestures
        self.assertEqual(kate["forward"], cfg["buttons"]["forward"])  # then the settings for all apps
        self.assertEqual(config.own_buttons(cfg, KATE), {"middle": F9, "back": PAN})
        for app in (KONSOLE, None, GONE):
            with self.subTest(app=app):
                self.assertEqual(config.mouse_buttons(cfg, app), cfg["buttons"])
        self.assertEqual((config.own_buttons(cfg, KONSOLE), config.own_buttons(cfg, GONE)), ({}, {}))

    def test_files_exported_imported_and_saved_by_older_versions(self):  # AG04
        cfg = config.clean(cfg_with([{"app": KATE, "buttons": {"middle": PAN, "gesture": SWIPES}}]))
        current = copy.deepcopy(cfg)
        current["flow"]["id"] = "this-one"
        back = config.import_data(config.export_data(cfg), current)
        self.assertEqual(back["mouse"], cfg["mouse"])
        self.assertEqual(back["flow"]["id"], "this-one")
        self.assertEqual(config.upgrade(json.loads(config.to_bytes(cfg)))["mouse"], cfg["mouse"])  # saved, read again
        old_profile_button = cfg_with([{"app": KATE, "buttons": {"middle": PAN}}], [profile(KATE, middle=F9)])
        self.assertEqual(config.upgrade(json.loads(json.dumps(old_profile_button)))["mouse"]["gestures"], [])
        junk = cfg_with([{"app": KATE, "buttons": {"middle": dict(PAN, mode="rotate")}}, {"junk": True}])
        self.assertEqual(config.upgrade(json.loads(json.dumps(junk)))["mouse"]["gestures"],
                         [{"app": KATE, "buttons": {"middle": NO_MODE}}])
        kept_while_removed = cfg_with([{"app": KATE, "buttons": {"middle": PAN}}], [profile(KONSOLE)])
        self.assertEqual(config.clean(kept_while_removed)["mouse"]["gestures"], [])  # no profile: nothing of it counts
        kept_while_removed["mouse"]["profiles"].append(profile(KATE))  # added again in a version without them
        self.assertEqual(config.clean(kept_while_removed)["mouse"]["gestures"], [{"app": KATE, "buttons": {"middle": PAN}}])


class StoreAndOthers(unittest.TestCase):
    """The Store setter the Buttons page uses, removing, restoring, other programs (Set) and suggested settings."""

    def setUp(self):
        self.store = store_of(config.clean(cfg_with([])))

    def pushes(self):
        return len(self.store.engine.configs)

    def profile_buttons(self, app=KATE):
        return self.store.mouse_profile(app)["buttons"]

    def own(self):
        return self.store.cfg["mouse"]["gestures"]

    def test_the_store_keeps_an_apps_gestures_beside_its_profile(self):  # AG05
        store = self.store
        self.assertTrue(store.set_profile_button(KATE, "back", COPY))
        self.assertTrue(store.set_profile_button(KATE, "back", PAN))
        self.assertEqual((self.own(), self.profile_buttons()), ([{"app": KATE, "buttons": {"back": PAN}}], {}))
        pushes = self.pushes()
        self.assertTrue(store.set_profile_button(KATE, "back", copy.deepcopy(PAN)))  # equal: nothing to tell
        self.assertEqual(self.pushes(), pushes)
        self.assertTrue(store.set_profile_button(KATE, "middle", SWIPES))
        self.assertEqual(self.own(), [{"app": KATE, "buttons": {"back": PAN, "middle": SWIPES}}])
        self.assertTrue(store.set_profile_button(KATE, "back", COPY))
        self.assertEqual((self.own(), self.profile_buttons()), ([{"app": KATE, "buttons": {"middle": SWIPES}}], {"back": COPY}))
        self.assertTrue(store.set_profile_button(KATE, "middle", None))
        self.assertTrue(store.set_profile_button(KATE, "back", None))
        self.assertEqual((self.own(), self.profile_buttons()), ([], {}))
        self.assertTrue(store.set_profile_button(KATE, "gesture", config.default_gestures()))
        self.assertEqual(self.own(), [{"app": KATE, "buttons": {"gesture": config.default_gestures()}}])
        self.assertEqual(config.clean(copy.deepcopy(store.cfg)), store.cfg)  # what the Store keeps loads as it is
        self.assertEqual(store.engine.configs[-1], store.cfg)

    def test_gestures_the_settings_would_not_keep_change_nothing(self):  # AG06
        store = self.store
        self.assertTrue(store.set_profile_button(KATE, "middle", PAN))
        before, pushes = copy.deepcopy(store.cfg), self.pushes()
        for name, app, key, action in (("mode rotate", KATE, "middle", dict(PAN, mode="rotate")),
                                       ("ring slot", KATE, "middle", dict(SWIPES, up={"type": "ring"})),
                                       ("extra key", KATE, "middle", dict(PAN, extra=1)),
                                       ("spotlight", KATE, "middle", {"type": "spotlight"}),
                                       ("not a dict", KATE, "middle", "gestures"),
                                       ("no profile", GONE, "middle", PAN),
                                       ("bad key", KATE, "left", PAN)):
            with self.subTest(name):
                self.assertFalse(store.set_profile_button(app, key, copy.deepcopy(action)))
                self.assertEqual(store.cfg, before)
                self.assertEqual(self.pushes(), pushes)

    def test_removing_and_adding_an_app_again_and_restoring_defaults_drop_them(self):  # AG07
        store = self.store
        self.assertTrue(store.set_profile_button(KATE, "middle", PAN))
        self.assertTrue(store.set_profile_button(KONSOLE, "middle", ZOOM))
        store.remove_mouse_profile(KATE)
        self.assertEqual(self.own(), [{"app": KONSOLE, "buttons": {"middle": ZOOM}}])
        self.assertTrue(store.add_mouse_profile(KATE))
        self.assertEqual(config.mouse_buttons(store.cfg, KATE), store.cfg["buttons"])  # added again: nothing of before
        store.restore_defaults()
        self.assertEqual(self.own(), [])

    def test_set_takes_whole_valid_gestures_only(self):  # AG08
        base = config.clean(cfg_with([{"app": KATE, "buttons": {"middle": PAN}}]))
        rows = (("mouse.gestures", [{"app": KONSOLE, "buttons": {"gesture": SWIPES}}], True),
                ("mouse.gestures.0.buttons.middle.mode", "zoom", True),
                ("mouse.gestures.0.buttons.middle.tap", F9, True),
                ("mouse.gestures.0.buttons.middle", SWIPES, True),
                ("mouse.gestures", [], True),
                ("mouse.profiles.0.buttons", {"back": F9}, True),
                ("mouse.gestures.0.buttons.middle.mode", "rotate", False),
                ("mouse.gestures.0.buttons.middle.tap", {"type": "ring"}, False),
                ("mouse.gestures.0.buttons.middle", F9, False),
                ("mouse.gestures.0.buttons", {}, False),
                ("mouse.gestures.0.app", GONE, False),
                ("mouse.gestures", [{"app": GONE, "buttons": {"middle": PAN}}], False),
                ("mouse.gestures", [{"app": KATE, "buttons": {"middle": PAN}, "x": 1}], False),
                ("mouse.profiles.0.buttons.middle", PAN, False))
        for path, value, taken in rows:
            with self.subTest(path=path, value=json.dumps(value)[:50]):
                store = store_of(base)
                self.assertEqual(api.MouseApi(store, None, lambda: None).Set(path, json.dumps(value)), taken)
                if not taken:
                    self.assertEqual(store.cfg, base)
        store = store_of(base)
        self.assertTrue(api.MouseApi(store, None, lambda: None).Set("mouse.profiles.0.buttons", json.dumps({"middle": F9})))
        self.assertEqual((store.cfg["mouse"]["gestures"], config.mouse_buttons(store.cfg, KATE)["middle"]), ([], F9))

    def test_suggested_settings_never_replace_an_apps_own_gestures(self):  # AG09
        store = self.store
        self.assertTrue(store.set_profile_button(KATE, "middle", PAN))
        setting, function = config.preset_changes(store.cfg, KATE, "browser")
        self.assertEqual((setting, function), ({"back": config.NATIVE, "forward": config.NATIVE}, "tabs"))
        self.assertTrue(store.apply_mouse_preset(KATE, "browser"))
        self.assertEqual(config.mouse_buttons(store.cfg, KATE)["middle"], PAN)
        self.assertEqual(self.own(), [{"app": KATE, "buttons": {"middle": PAN}}])
        self.assertEqual(self.profile_buttons(), {"back": config.NATIVE, "forward": config.NATIVE})
        store = store_of(config.clean(cfg_with([{"app": KATE, "buttons": {"middle": PAN}}])))
        store.set_mouse_app(KATE)
        with qt_isolated():
            page = pages.ButtonsPage(store)
        self.addCleanup(page.deleteLater)
        asked = []

        def question(change):
            def ask(_parent, _title, text):
                asked.append(text)
                change()
                return QMessageBox.StandardButton.Yes
            return ask

        before = copy.deepcopy(store.cfg)
        with mock.patch.object(pages.QMessageBox, "question", question(
                lambda: store.set_profile_button(KATE, "back", ZOOM))), \
                mock.patch.object(pages.QMessageBox, "information") as information:
            page.apps.suggest.menu().actions()[0].trigger()  # own gestures given while it asks
        self.assertIn("Middle button: stays as set for this app (Pan (hold and move))", asked[-1].split("\n"))
        self.assertNotIn("pans while the middle button", asked[-1])  # the browser set has the middle button
        information.assert_called_once_with(page.apps, pages.APP_NAME,
                                            "Settings changed while this dialog was open. Choose Suggested settings again.")
        self.assertEqual(store.cfg["mouse"]["gestures"], [{"app": KATE, "buttons": {"middle": PAN, "back": ZOOM}}])
        self.assertEqual(store.mouse_profile(KATE)["buttons"], before["mouse"]["profiles"][0]["buttons"])  # nothing applied
        store._save_timer.stop()


class qt_isolated:
    """Building pages and the window: no mouse render worker and no firmware query (class-level, before construction)."""

    def __init__(self):
        self.patches = (mock.patch.object(pages.MouseView, "_prerender"), mock.patch.object(pages.FirmwareQuery, "start"))

    def __enter__(self):
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *_exc):
        for patch in self.patches:
            patch.stop()


def engine_with(test, own=(), buttons=None, real=False):
    """An engine (tests.test_mouse_profiles.make) for Kate and Konsole with ``own`` as mouse.gestures, loaded as the
    settings would be; ``real``: a real Executor, else a recording one, with an input that records scrolling."""
    cfg = cfg_with(list(own), [profile(KATE), profile(KONSOLE), profile(KWRITE)])
    cfg["buttons"].update(copy.deepcopy(buttons or {}))
    cfg = config.clean(cfg)
    test.assertEqual(cfg["mouse"].get("gestures", []), list(own))  # what was given counts, unchanged
    made = mp.make(test, cfg=cfg, real=real)
    if not real:
        made.vinput = MotionInput()
    return made


def move(made, clock, moves, step=0.0):
    for dx, dy in moves:
        clock.now += step
        made._on_raw_xy(dx, dy)


def result(made):
    return made.listener.gestures[-1]["result"] if made.listener.gestures else None


def scrolls(made):
    return [value for name, value in made.vinput.sent if name == "scroll"]


class Engine(unittest.TestCase):
    """The engine following the app in front with an app's own gestures. Clock stands in for time; sleeps of zoom
    steps are counted, never waited: each one moves Clock on by its length."""

    def setUp(self):
        self.clock = support.Clock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__)
        patcher = mock.patch.object(engine.time, "sleep", side_effect=lambda seconds: setattr(
            self.clock, "now", self.clock.now + seconds))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_apps_own_pan_pans_there_only_and_a_switch_calls_it_off(self):  # AG10
        made = engine_with(self, [{"app": KATE, "buttons": {"middle": PAN}}, {"app": KWRITE, "buttons": {"middle": PAN}}])
        mp.front(made, KATE)
        self.assertEqual(made.mouse.reporting.get(MIDDLE), (True, True))
        made._on_buttons({MIDDLE})
        move(made, self.clock, [(0, 200)] * 3)
        self.assertGreater(sum(scrolls(made)), 0)
        mp.front(made, KWRITE)  # the same own pan there: it goes on
        sent = len(made.vinput.sent)
        move(made, self.clock, [(0, 200)] * 3)
        self.assertGreater(len(made.vinput.sent), sent)
        mp.front(made, KONSOLE)
        self.assertEqual(result(made), "cancelled")
        self.assertEqual(made.mouse.reporting.get(MIDDLE), (False, False))
        sent = list(made.vinput.sent)
        move(made, self.clock, [(0, 200)] * 3)
        made._on_buttons(set())
        self.assertEqual((made.vinput.sent, made.executor.ran), (sent, []))

    def test_a_tap_swipe_or_macro_held_into_another_app_runs_nothing(self):  # AG11
        for leg, moves in (("tap", []), ("swipe", [(-200, 0)] * 3)):
            with self.subTest(leg):
                made = engine_with(self, [{"app": KATE, "buttons": {"gesture": SWIPES}}])
                mp.front(made, KATE)
                made._on_buttons({GESTURE})
                move(made, self.clock, moves)
                made._on_buttons(set())
                self.assertEqual(made.executor.ran, [F9 if leg == "tap" else COPY])  # in the same app: it runs
                made._on_buttons({GESTURE})
                move(made, self.clock, moves)
                mp.front(made, KONSOLE)
                made._on_buttons(set())
                self.assertEqual(made.executor.ran, [F9 if leg == "tap" else COPY])
        tap = mp.macro("KEY_F5", "KEY_F6", name="own")
        for switch in (False, True):
            with self.subTest("macro", switch=switch):
                made = engine_with(self, [{"app": KATE, "buttons": {"gesture": dict(SWIPES, tap=tap)}}], real=True)
                mp.front(made, KATE)
                made._on_buttons({GESTURE})
                if switch:
                    mp.front(made, KONSOLE)
                with mock.patch.object(made.executor.macros, "run", wraps=made.executor.macros.run) as run:
                    made._on_buttons(set())
                self.assertEqual([call.args[0] for call in run.call_args_list], [] if switch else [tap])  # in its app only

    def test_equal_gestures_in_both_apps_go_on_without_what_was_moved_before(self):  # AG12
        for leg, own, buttons in (("for all apps", [], {"gesture": ZOOM}),
                                  ("own", [{"app": KATE, "buttons": {"gesture": ZOOM}},
                                           {"app": KONSOLE, "buttons": {"gesture": ZOOM}}], None)):
            for fresh in (False, True):
                with self.subTest(leg, fresh=fresh):
                    made = engine_with(self, own, buttons)
                    mp.front(made, KATE)
                    made._on_buttons({GESTURE})
                    move(made, self.clock, [(0, -158)] * 40)  # one zoom step, then whole detents wait for ZOOM_GAP
                    steps = len(scrolls(made))
                    self.assertGreater(steps, 0)
                    mp.front(made, KONSOLE)
                    if fresh:  # what is moved here zooms here
                        self.clock.now += 1.0
                        move(made, self.clock, [(0, -158)] * 6)
                        self.assertGreater(len(scrolls(made)), steps)
                    made._on_buttons(set())
                    self.assertEqual(result(made), "zoom")  # it went on
                    if not fresh:
                        self.assertEqual(len(scrolls(made)), steps)  # nothing moved in the app before zooms here
        made = engine_with(self, [{"app": KATE, "buttons": {"middle": PAN}}, {"app": KONSOLE, "buttons": {"middle": PAN}}])
        mp.front(made, KATE)
        made._on_buttons({MIDDLE})
        move(made, self.clock, [(0, 1)] + [(0, 200)] + [(0, 1)] * 2)  # the first report after arming is skipped
        rate = vinput.NOTCH / (engine.PAN_MM * max(4.0, made.cfg["pointer"]["dpi"] / 25.4))
        left = (200 + 2) * rate - sum(scrolls(made))  # less than a unit waits
        self.assertTrue(0 < left < 1 and left + rate >= 1 > rate, (left, rate))
        mp.front(made, KONSOLE)
        sent = len(made.vinput.sent)
        move(made, self.clock, [(0, 1)])  # with what waited, it would make a whole unit
        self.assertEqual(len(made.vinput.sent), sent)
        made._on_buttons(set())
        self.assertEqual(result(made), "pan")

    def test_edits_for_another_app_and_equal_copies_keep_the_hold(self):  # AG13
        made = engine_with(self, [{"app": KATE, "buttons": {"gesture": SWIPES, "middle": PAN}}])
        mp.front(made, KONSOLE)
        made._on_buttons({GESTURE})
        cfg = copy.deepcopy(made.cfg)
        cfg["mouse"]["gestures"][0]["buttons"]["gesture"] = ZOOM
        mp.set_config(made, cfg)
        made._on_buttons(set())
        self.assertEqual((result(made), made.executor.ran), ("tap", [made.cfg["buttons"]["gesture"]["tap"]]))
        mp.front(made, KATE)
        made._on_buttons({MIDDLE})
        held = made._buttons["middle"]
        for _ in range(2):
            mp.set_config(made, copy.deepcopy(made.cfg))
        self.assertIs(made._buttons["middle"], held)
        move(made, self.clock, [(0, 200)] * 3)
        self.assertGreater(sum(scrolls(made)), 0)
        made._on_buttons(set())
        self.assertEqual(result(made), "pan")

    def test_removing_the_app_in_front_a_reset_or_offline_settings_call_it_off(self):  # AG14
        for leg in ("removed", "reset", "offline"):
            with self.subTest(leg):
                made = engine_with(self, [{"app": KATE, "buttons": {"middle": PAN}}])
                mp.front(made, KATE)
                made._on_buttons({MIDDLE})
                move(made, self.clock, [(0, 200)] * 2)
                writes = len(made.mouse.log)
                if leg == "removed":
                    cfg = copy.deepcopy(made.cfg)
                    cfg["mouse"]["profiles"] = [profile(KONSOLE)]
                    cfg["mouse"]["gestures"] = []
                    mp.set_config(made, config.clean(cfg))
                elif leg == "reset":
                    made._reset_input_state()
                else:
                    made.online = False
                    cfg = copy.deepcopy(made.cfg)
                    cfg["mouse"]["gestures"][0]["buttons"]["middle"] = ZOOM
                    mp.set_config(made, cfg)
                    self.assertEqual(len(made.mouse.log), writes)  # nothing written offline
                self.assertEqual(result(made), "cancelled")
                sent = list(made.vinput.sent)
                move(made, self.clock, [(0, 200)] * 2)
                made._on_buttons(set())
                self.assertEqual((made.vinput.sent, made.executor.ran), (sent, []))

    def test_an_unknown_app_in_front_uses_all_apps_and_this_window_keeps_the_app_before(self):  # AG15
        made = engine_with(self, [{"app": KATE, "buttons": {"middle": PAN}}])
        mp.front(made, KATE)
        writes = len(made.mouse.log)
        mp.front(made, APP_ID)
        self.assertEqual(len(made.mouse.log), writes)
        made._on_buttons({MIDDLE})
        move(made, self.clock, [(0, 200)] * 3)
        made._on_buttons(set())
        self.assertEqual(result(made), "pan")
        mp.front(made, None)
        self.assertEqual(made.mouse.reporting.get(MIDDLE), (False, False))  # the middle button's own function
        made._on_buttons({MIDDLE})
        made._on_buttons(set())
        self.assertEqual((result(made), made.executor.ran), ("pan", []))

    def test_two_buttons_with_equal_own_gestures_one_hold_at_a_time(self):  # AG16
        made = engine_with(self, [{"app": KATE, "buttons": {"middle": PAN, "gesture": PAN}}])
        mp.front(made, KATE)
        made._on_buttons({MIDDLE})
        move(made, self.clock, [(0, 200)] * 2)
        made._on_buttons({MIDDLE, GESTURE})
        self.assertEqual(made._gesture["cid"], GESTURE)  # the second takes over
        made._on_buttons({GESTURE})
        self.assertEqual(made.executor.ran, [])  # the first one's release does nothing
        made._on_buttons(set())
        self.assertEqual((result(made), made.executor.ran), ("tap", [MID]))

    def test_practice_and_a_ctrl_held_by_the_keyboard_with_an_apps_own_zoom(self):  # AG17
        made = engine_with(self, [{"app": KATE, "buttons": {"gesture": ZOOM}}])
        mp.front(made, KATE)
        made._on_buttons({GESTURE})
        move(made, self.clock, [(0, -158)] * 40)
        steps = len(scrolls(made))
        made.set_gesture_practice(True)
        mp.drain(made)
        self.clock.now += 1.0
        move(made, self.clock, [(0, -158)] * 10)
        made._on_buttons(set())
        self.assertEqual((len(scrolls(made)), result(made)), (steps, "zoom"))  # nothing sent once practising
        made = engine_with(self, [{"app": KATE, "buttons": {"gesture": ZOOM}}])
        made.vinput.press(["KEY_LEFTCTRL"], owner="keyboard")
        mp.front(made, KATE)
        made._on_buttons({GESTURE})
        move(made, self.clock, [(0, -158)] * 8)
        made._on_buttons(set())
        self.assertGreater(len(scrolls(made)), 0)
        self.assertEqual(made.vinput.held(), {"KEY_LEFTCTRL"})  # the keyboard's Ctrl stays held


class Window(unittest.TestCase):
    """The settings window: the Buttons page giving an app its own gestures, and the Gestures page for all apps."""

    def setUp(self):
        self.store = store_of(config.clean(cfg_with([])))
        self.store.set_button("middle", dict(config.default_gestures(), up=F9))  # customised for all apps
        self.store.engine.configs.clear()
        isolated = qt_isolated()
        isolated.__enter__()
        self.addCleanup(isolated.__exit__)
        from mxoptions.ui.main_window import MainWindow
        self.window = MainWindow(self.store, hide_on_close=False)
        self.addCleanup(self.close)
        self.window.resize(1200, 780)
        self.window.show()
        self.page = self.window.buttons_page
        self.picker = self.page.picker

    def close(self):
        self.store._save_timer.stop()
        self.window.hide()
        self.window.deleteLater()
        APP.sendPostedEvents(self.window, QEvent.Type.DeferredDelete)

    def own(self):
        return self.store.cfg["mouse"]["gestures"]

    def test_the_buttons_page_gives_an_app_its_own_gestures_and_pan(self):  # AG18
        store, picker = self.store, self.picker
        everywhere = copy.deepcopy(store.cfg["buttons"])
        store.set_mouse_app(KATE)
        self.page.view.select("middle")
        rows = picker.list._rows
        self.assertFalse(rows["gestures"].isHidden())
        self.assertTrue(rows["spotlight"].isHidden())
        self.assertTrue(picker.list.inherit_row.isChecked())
        rows["gestures"].click()
        seeded = config.app_gestures_action(everywhere["middle"])
        self.assertEqual(seeded["up"], F9)
        self.assertEqual(self.own(), [{"app": KATE, "buttons": {"middle": seeded}}])
        mode = picker.mode
        self.assertTrue(mode.isVisible())
        self.window.activateWindow()
        APP.processEvents()
        mode.setFocus()
        mode.setCurrentIndex(mode.findData("pan"))
        self.assertEqual(self.own(), [{"app": KATE, "buttons": {"middle": dict(seeded, mode="pan")}}])
        self.assertIs(picker.mode, mode)  # the store's change rebuilt nothing: focus stays where it was
        self.assertIs(APP.focusWidget(), mode)
        self.assertEqual(self.page.view._chips["middle"].value.text(), "Pan (hold and move)")
        self.assertEqual((picker._gesture_buttons["tap"].isEnabled(), picker._gesture_buttons["up"].isEnabled(),
                          picker.shapes_button.isEnabled()), (True, False, False))
        self.assertEqual(store.cfg["buttons"], everywhere)  # every other app: unchanged
        self.assertEqual(mode.accessibleName(), "Holding it and moving")
        chain, widget = [], picker.about
        for _ in range(40):
            widget = widget.nextInFocusChain()
            chain.append(widget)
        self.assertLess(chain.index(mode), chain.index(picker._gesture_buttons["tap"]))
        self.assertTrue(mode.focusPolicy() & Qt.FocusPolicy.TabFocus)
        picker.list.inherit_row.click()
        self.assertEqual((self.own(), config.mouse_buttons(store.cfg, KATE)["middle"]), ([], everywhere["middle"]))
        store.set_mouse_app(None)
        self.page.view.select("middle")
        mode.setCurrentIndex(mode.findData("zoom"))  # all apps: its mode is set here too
        self.assertEqual(store.cfg["buttons"]["middle"], dict(everywhere["middle"], mode="zoom"))
        self.assertEqual(self.own(), [])

    def test_a_late_pick_for_a_control_no_longer_edited_goes_nowhere(self):  # AG19
        store, picker = self.store, self.picker
        self.assertTrue(store.set_profile_button(KATE, "middle", SWIPES))
        self.assertTrue(store.set_profile_button(KATE, "gesture", PAN))
        store.set_mouse_app(KATE)
        self.page.view.select("middle")
        picker._gesture_buttons["up"].click()
        picker.sub_list.picked.emit(COPY)  # picked before the control changed: it counts
        self.assertEqual(self.own()[0]["buttons"]["middle"]["up"], COPY)
        picker._gesture_buttons["down"].click()
        before = copy.deepcopy(store.cfg)
        self.page.view.select("gesture")  # a dialog of the middle button's still open, say
        told = []
        picker.changed.connect(told.append)
        picker.sub_list.picked.emit(F9)
        self.assertEqual((told, store.cfg, picker._action), ([], before, PAN))

    def test_the_gestures_page_stays_the_page_for_all_apps(self):  # AG20
        store, window = self.store, self.window
        page = window.findChild(gestures_page.GesturesPage)
        self.assertTrue(page.apps_note.isHidden())
        store.set_mouse_app(KATE)
        self.assertFalse(page.apps_note.isHidden())
        self.assertIn(KATE, page.apps_note.text())
        self.assertIn("Buttons page", page.apps_note.text())
        page.actions_button.click()
        key = gestures_page.gesture_button(store.cfg)
        self.assertEqual((store.mouse_app, window.stack.currentWidget()), (None, self.page))
        self.assertEqual(self.picker.title.text(), pages.BUTTON_TITLES[key])
        self.assertTrue(self.picker.list.inherit_row.isHidden())  # the button for all apps
        self.assertTrue(page.apps_note.isHidden())


if __name__ == "__main__":
    unittest.main()
