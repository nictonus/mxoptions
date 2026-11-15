"""Custom keystrokes on the thumb wheel: the settings, the engine sending them (at most one per report, a gap
between them, the rest dropped), what other programs may set, and the Thumb wheel card. The mouse and the
virtual input are synthetic (tests/support.py); app names are made up."""

import copy
import json
import logging
import unittest
from unittest import mock

import tests.support as support
import tests.test_holds as holds_tests
import tests.test_mouse_profiles as profiles
from mxoptions import actions, api, config, device, engine, vinput
from mxoptions.ui import pages
from mxoptions.ui.store import Store
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

KATE, KONSOLE = profiles.KATE, profiles.KONSOLE
CTRL, SHIFT, Z = "KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_Z"
UNDO, REDO = [CTRL, Z], [CTRL, SHIFT, Z]
UP, DOWN, HALF_UP = profiles.STEP_UP, -profiles.STEP_UP, profiles.STEP_UP // 2  # one click; half a click
AFTER = engine.THUMB_KEY_GAP + 0.0125  # s: past the gap
SENT_UNDO = [(CTRL, True), (Z, True), (Z, False), (CTRL, False)]


def own(app, function):
    return {"app": app, "function": function}


def keys_engine(test, real=False, apps=(), entries=(), **thumb):
    """An engine whose thumb wheel sends Ctrl+Z turning up and Ctrl+Shift+Z turning down unless ``thumb`` says
    otherwise; ``real``: a real Executor on the virtual input instead of one that writes down what it ran."""
    made = profiles.thumb_engine(test, [profiles.profile(app) for app in apps], entries,
                                 thumbwheel=dict({"function": "keys", "up": UNDO, "down": REDO}, **thumb))
    if real:
        made.executor = actions.Executor(made.vinput, made._device_action)
        test.addCleanup(profiles.stop, test, made.executor)
    return made


def report(made, clock, rotation, after=AFTER, times=1):
    for _ in range(times):
        clock.now += after
        made._on_thumbwheel(rotation)


def ran(made):
    return [action["keys"] for action in made.executor.ran]


class Settings(unittest.TestCase):
    """thumbwheel.up and thumbwheel.down in the settings: keyboard keys only, kept as they are or emptied."""

    def clean(self, **thumb):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["thumbwheel"].update(thumb)
        return config.clean(cfg)

    def test_the_defaults(self):
        cleaned = config.clean(copy.deepcopy(config.DEFAULTS))
        self.assertEqual((cleaned["thumbwheel"]["up"], cleaned["thumbwheel"]["down"]), ([], []))
        self.assertEqual((cleaned["thumbwheel"]["function"], cleaned["version"]), ("hscroll", 6))
        self.assertIn("keys", config.THUMB_FUNCTIONS)

    def test_a_combination_of_keyboard_keys_is_kept_in_its_order(self):
        sixteen = ["KEY_" + c for c in "ABCDEFGHIJKLMNOP"]
        for value in ([Z], sixteen, [SHIFT, CTRL], [CTRL, CTRL, Z]):
            with self.subTest(value=value):
                thumb = self.clean(function="keys", up=list(value), down=list(reversed(value)))["thumbwheel"]
                self.assertEqual((thumb["up"], thumb["down"], thumb["function"]), (value, value[::-1], "keys"))

    def test_what_is_not_one_is_emptied_and_the_rest_kept(self):
        bad = (None, "KEY_Z", {"KEY_Z": 1}, 3, [1], ["KEY_NOPE"], ["BTN_LEFT"], [CTRL, "BTN_EXTRA"],
               ["KEY_" + c for c in "ABCDEFGHIJKLMNOPQ"], [["KEY_A"]], [{}], [None])
        for value in bad:
            for direction in ("up", "down"):
                with self.subTest(value=value, direction=direction):
                    asked = {"function": "keys", "invert": True, "speed": 2.0, "ticks": False, "up": UNDO, "down": REDO}
                    asked[direction] = copy.deepcopy(value)
                    for load in (lambda: config.upgrade(json.loads(json.dumps({"version": 6, "thumbwheel": asked}))),
                                 lambda: self.clean(**copy.deepcopy(asked))):
                        thumb = load()["thumbwheel"]
                        self.assertEqual(thumb[direction], [])
                        self.assertEqual(dict(thumb, **{direction: asked[direction]}), asked)

    def test_keys_and_buttons_may_still_hold_mouse_buttons(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        mixed = {"type": "keys", "keys": [CTRL, "BTN_EXTRA"]}
        cfg["keyboard"]["keys"] = {"0x00c7": copy.deepcopy(mixed)}
        cfg["buttons"]["back"] = copy.deepcopy(mixed)
        cleaned = config.clean(cfg)
        self.assertEqual((cleaned["keyboard"]["keys"]["0x00c7"], cleaned["buttons"]["back"]), (mixed, mixed))

    def test_keys_of_one_app_use_the_combinations_of_all_apps(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["thumbwheel"].update(up=UNDO, down=REDO)
        cfg["mouse"]["profiles"] = [profiles.profile(KATE)]
        cfg["mouse"]["thumbwheel"] = [own(KATE, "keys")]
        cfg = config.clean(cfg)
        self.assertEqual(cfg["mouse"]["thumbwheel"], [own(KATE, "keys")])
        thumb = config.thumbwheel(cfg, KATE)
        self.assertEqual((thumb["function"], thumb["up"], thumb["down"]), ("keys", UNDO, REDO))
        self.assertEqual(config.thumbwheel(cfg, KONSOLE)["function"], "hscroll")


class Sending(unittest.TestCase):
    """The engine sending the keystrokes: the direction turned, one per report at most, a gap between them."""

    def setUp(self):
        self.clock = support.Clock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__)

    def test_turning_up_and_down(self):
        made = keys_engine(self)
        report(made, self.clock, UP)
        report(made, self.clock, DOWN)
        self.assertEqual(ran(made), [UNDO, REDO])

    def test_reverse_direction_swaps_them(self):
        made = keys_engine(self, invert=True)
        report(made, self.clock, UP)
        self.assertEqual(ran(made), [REDO])

    def test_one_huge_report_sends_one_and_owes_nothing(self):
        for rotation, speed in ((32766, 1.0), (-32766, 4.0), (32767, 4.0), (-32768, 4.0), (32767, 0.25)):
            with self.subTest(rotation=rotation, speed=speed):
                made = keys_engine(self, speed=speed)
                report(made, self.clock, rotation)
                self.assertEqual(len(made.executor.ran), 1)
                self.assertLess(abs(made._thumb["acc"]), 1)
                report(made, self.clock, rotation, times=3)
                self.assertEqual(len(made.executor.ran), 4)
        made = keys_engine(self)
        report(made, self.clock, -32766)
        report(made, self.clock, HALF_UP)  # half a click more: nothing of the big turn was kept
        self.assertEqual(ran(made), [UNDO])

    def test_many_reports_at_once_send_one(self):
        made = keys_engine(self)
        report(made, self.clock, UP)
        report(made, self.clock, UP, after=0, times=199)
        self.assertEqual(ran(made), [UNDO])
        report(made, self.clock, UP)
        self.assertEqual(ran(made), [UNDO, UNDO])

    def test_a_report_inside_the_gap_is_dropped(self):
        made = keys_engine(self)
        for after in (AFTER, 0.01, 0.01, AFTER, 0.03, AFTER):
            report(made, self.clock, UP, after=after)
        self.assertEqual(ran(made), [UNDO] * 3)

    def test_reports_apart_enough_send_one_each(self):
        made = keys_engine(self)
        report(made, self.clock, UP, times=10)
        self.assertEqual(ran(made), [UNDO] * 10)

    def test_a_direction_without_keys_sends_nothing_and_leaves_the_gap(self):
        made = keys_engine(self, down=[])
        report(made, self.clock, DOWN)
        report(made, self.clock, UP, after=0)
        self.assertEqual((ran(made), made.played), ([UNDO], [device.WAVE_TICK]))

    def test_a_tick_for_each_one_sent_if_wanted(self):
        for ticks, expected in ((True, [device.WAVE_TICK] * 2), (False, [])):
            with self.subTest(ticks=ticks):
                made = keys_engine(self, ticks=ticks)
                report(made, self.clock, UP)
                report(made, self.clock, UP, after=0)
                report(made, self.clock, UP)
                self.assertEqual((len(made.executor.ran), made.played), (2, expected))

    def test_while_the_ring_is_open_it_turns_the_dials(self):
        made = keys_engine(self)
        report(made, self.clock, HALF_UP)
        made.set_ring_open(True)
        profiles.drain(made)
        report(made, self.clock, UP)
        self.assertEqual((made.listener.turns, ran(made)), ([1.0], []))
        made.set_ring_open(False)
        profiles.drain(made)
        report(made, self.clock, HALF_UP)  # the half before the ring opened is gone
        self.assertEqual(ran(made), [])

    def test_new_settings_drop_half_a_step(self):
        for change in (lambda cfg: cfg["thumbwheel"].update(up=[Z]), lambda cfg: cfg["thumbwheel"].update(function="volume")):
            with self.subTest():
                made = keys_engine(self)
                report(made, self.clock, HALF_UP)
                profiles.edited(made, change)
                report(made, self.clock, HALF_UP)
                self.assertEqual(made.executor.ran, [])

    def test_half_a_step_does_not_follow_into_another_app(self):
        made = keys_engine(self, apps=(KATE, KONSOLE), entries=[own(KATE, "keys"), own(KONSOLE, "hscroll")],
                           function="hscroll")
        profiles.front(made, KATE)
        report(made, self.clock, HALF_UP)
        profiles.front(made, KONSOLE)
        profiles.front(made, KATE)
        report(made, self.clock, HALF_UP)
        self.assertEqual(made.executor.ran, [])
        made = keys_engine(self, apps=(KATE, KONSOLE), entries=[own(KATE, "keys")])
        profiles.front(made, KATE)
        report(made, self.clock, HALF_UP)
        profiles.front(made, KONSOLE)  # keys there too, those of all apps: the step goes on
        report(made, self.clock, HALF_UP)
        self.assertEqual(ran(made), [UNDO])

    def test_keys_the_keyboard_holds_stay_held(self):
        made = keys_engine(self, real=True)
        made.vinput.press([CTRL], "keyboard")
        report(made, self.clock, UP)
        self.assertEqual(made.vinput.sent, [(CTRL, True), (Z, True), (Z, False)])
        self.assertEqual(profiles.held(made), {CTRL})
        made.vinput.release([CTRL], "keyboard")
        self.assertEqual(made.vinput.sent[-1], (CTRL, False))

    def test_every_key_sent_goes_up_and_none_is_held(self):
        made = keys_engine(self, real=True)
        report(made, self.clock, UP, times=5)
        self.assertEqual(made.vinput.sent, SENT_UNDO * 5)
        self.assertEqual(profiles.held(made), set())

    def test_where_the_thumb_wheel_goes(self):
        made = keys_engine(self, apps=(KATE, KONSOLE), entries=[own(KONSOLE, "hscroll")], function="hscroll")
        profiles.edited(made, lambda cfg: cfg["thumbwheel"].update(function="keys"))
        self.assertEqual(profiles.thumbs(made), [("thumb", True, False)])
        made.vinput = None
        made._apply_thumbwheel()
        self.assertEqual(profiles.thumbs(made)[-1], ("thumb", False, False))
        made = keys_engine(self, apps=(KATE, KONSOLE), entries=[own(KATE, "keys"), own(KONSOLE, "hscroll")],
                           function="hscroll")
        profiles.front(made, KATE)
        profiles.front(made, KONSOLE)
        self.assertEqual(profiles.thumbs(made), [("thumb", True, False), ("thumb", False, False)])

    def test_reconnecting_drops_half_a_step(self):
        made = keys_engine(self)
        report(made, self.clock, HALF_UP)
        for name in ("_apply_pointer", "_apply_wheel", "_apply_haptics"):
            setattr(made, name, lambda: None)
        made._read_status = lambda **_kwargs: None
        made._configure()
        report(made, self.clock, HALF_UP)
        self.assertEqual(made.executor.ran, [])

    def test_one_wait_of_the_virtual_input_per_keystroke(self):
        class Waiting(support.FakeInput):
            def tap(self, names):
                self._holds.tap(list(names))  # with the wait of the real backends
        made = keys_engine(self)
        made.vinput = Waiting()
        made.executor = actions.Executor(made.vinput, made._device_action)
        self.addCleanup(profiles.stop, self, made.executor)
        slept = []
        with mock.patch.object(vinput.time, "sleep", slept.append):
            report(made, self.clock, UP)
            report(made, self.clock, UP, after=0, times=199)
            self.assertEqual((slept, len(made.vinput.sent)), ([0.012], 4))
            report(made, self.clock, UP)
        self.assertEqual((slept, len(made.vinput.sent)), ([0.012] * 2, 8))


class Failing(unittest.TestCase):
    """A keystroke the virtual input fails to send: its keys go up, nothing is owed, the gap still holds."""

    def setUp(self):
        self.clock = support.Clock()
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__)

    def failing(self, *trips, **thumb):
        made = keys_engine(self, real=True, **thumb)
        emit = made.vinput._holds._emit = holds_tests.Refusing(trips)
        return made, emit

    def test_a_failed_down_sends_the_ups_and_leaves_no_debt(self):
        made, emit = self.failing((Z, True))
        with self.assertRaises(OSError) as caught:
            report(made, self.clock, UP)
        self.assertIs(caught.exception, emit.raised[0])
        self.assertEqual((emit.calls, profiles.held(made), made.played), (SENT_UNDO, set(), []))
        report(made, self.clock, HALF_UP)  # nothing of the failed one is owed: half a step more sends nothing
        self.assertEqual(emit.calls, SENT_UNDO)
        emit.trips[(Z, True)] = OSError
        with self.assertRaises(OSError):
            report(made, self.clock, UP, after=1.0)  # a new turn
        report(made, self.clock, UP, after=0.01)  # inside the gap: the gap counts from the failed one
        self.assertEqual(emit.calls, SENT_UNDO * 2)
        report(made, self.clock, UP)
        self.assertEqual(emit.calls, SENT_UNDO * 3)
        self.assertEqual(emit.active, set())

    def test_a_failed_up_leaves_what_others_hold(self):
        made, emit = self.failing(up=REDO)
        made.vinput.press([CTRL], "keyboard")
        made.executor.press(actions.keys("LEFTSHIFT"))
        emit.trips[(Z, False)] = OSError
        with self.assertRaises(OSError):
            report(made, self.clock, UP)
        self.assertEqual(emit.calls[2:], [(Z, True), (Z, False)])
        self.assertEqual(profiles.held(made), {CTRL, SHIFT})
        made.executor.reset()
        self.assertEqual(emit.calls[-1], (SHIFT, False))
        made.vinput.release([CTRL], "keyboard")
        self.assertEqual(emit.calls[-1], (CTRL, False))
        self.assertEqual(emit.active, set())

    def test_the_error_of_an_up_comes_out_after_every_up(self):
        made, emit = self.failing((Z, False))
        with self.assertRaises(OSError) as caught:
            report(made, self.clock, UP)
        self.assertIs(caught.exception, emit.raised[0])
        self.assertEqual(emit.calls, SENT_UNDO)
        self.assertEqual(emit.active, set())


class Others(unittest.TestCase):
    """The Store, other programs (Set), the settings file and logs."""

    def setUp(self):
        self.store = Store(copy.deepcopy(config.DEFAULTS), profiles._Engine())
        self.store.flush = lambda: None
        self.store._save_timer.timeout.disconnect()
        self.api = api.MouseApi(self.store, None, lambda: None)
        self.pushed = []
        self.store.changed.connect(lambda: self.pushed.append(copy.deepcopy(self.store.cfg["thumbwheel"])))

    def test_the_store_and_restoring_the_defaults(self):
        self.store.set("thumbwheel", "up", UNDO)
        self.store.set("thumbwheel", "down", REDO)
        self.assertEqual([(thumb["up"], thumb["down"]) for thumb in self.pushed], [(UNDO, []), (UNDO, REDO)])
        self.store.restore_defaults()
        self.assertEqual((self.store.cfg["thumbwheel"]["up"], self.store.cfg["thumbwheel"]["down"]), ([], []))

    def test_other_programs_set_what_may_be_and_are_refused_the_rest(self):
        self.store.add_mouse_profile(KATE)
        accepted = (("thumbwheel.function", "keys"), ("thumbwheel.up", UNDO), ("thumbwheel.down", []),
                    ("thumbwheel.down", REDO), ("thumbwheel.up.0", "KEY_Y"), ("thumbwheel", {"function": "keys", "up": UNDO}),
                    ("mouse.thumbwheel", [own(KATE, "keys")]))
        for path, value in accepted:
            with self.subTest(path=path, value=value):
                self.assertTrue(self.api.Set(path, json.dumps(value)))
        self.assertEqual(self.store.cfg["thumbwheel"]["up"], UNDO)
        self.assertEqual(self.store.cfg["mouse"]["thumbwheel"], [own(KATE, "keys")])
        before = copy.deepcopy(self.store.cfg)
        refused = [("thumbwheel.up", bad) for bad in (["KEY_NOPE"], ["BTN_LEFT"], [CTRL, "BTN_EXTRA"], [1],
                                                      ["KEY_" + c for c in "ABCDEFGHIJKLMNOPQ"], [["KEY_A"]], None, {})]
        refused += [("thumbwheel.down", None), ("thumbwheel.up.0", "KEY_NOPE"), ("thumbwheel.up.0", "BTN_LEFT"),
                    ("thumbwheel", {"function": "keys", "up": ["KEY_NOPE"]}), ("thumbwheel", {"up": ["KEY_A", "BTN_LEFT"]}),
                    ("thumbwheel", {"up": None}), ("thumbwheel", {"down": None}), ("thumbwheel", "x"),
                    ("mouse.thumbwheel", [dict(own(KATE, "keys"), up=["KEY_A"])])]
        for path, bad in refused:
            with self.subTest(path=path, value=bad):
                self.assertFalse(self.api.Set(path, json.dumps(bad)))
                self.assertEqual(self.store.cfg, before)
        self.assertTrue(self.api.Set("thumbwheel.speed", json.dumps(10)))
        self.assertEqual(self.store.cfg["thumbwheel"]["speed"], 4.0)

    def test_export_and_import_keep_them(self):
        self.store.cfg["thumbwheel"].update(function="keys", up=UNDO, down=REDO)
        self.store.cfg["flow"]["id"] = "this-one"
        current = copy.deepcopy(self.store.cfg)
        current["flow"]["id"] = "here"
        back = config.import_data(config.export_data(self.store.cfg), current)
        self.assertEqual((back["thumbwheel"], back["flow"]["id"]), (self.store.cfg["thumbwheel"], "here"))

    def test_keys_and_apps_are_never_logged(self):
        secret = "org.example.secret"
        with support.Clock() as clock, self.assertLogs("mxoptions", logging.DEBUG) as logs:
            logging.getLogger("mxoptions").debug("start")
            made = keys_engine(self, real=True, apps=(secret,), entries=[own(secret, "keys")], up=[CTRL, "KEY_F13"])
            profiles.front(made, secret)
            report(made, clock, UP, times=3)
            made.vinput._holds._emit = holds_tests.Refusing([("KEY_F13", True)])
            with self.assertRaises(OSError):
                report(made, clock, UP)
            self.store.add_mouse_profile(secret)
            self.assertFalse(self.api.Set("thumbwheel.up", json.dumps(["KEY_F14", "BTN_EXTRA"])))
            self.assertFalse(self.api.Set("thumbwheel", json.dumps({"up": ["KEY_F15", "KEY_NOPE"]})))
        text = "\n".join(logs.output)
        for word in ("secret", "F13", "F14", "F15", "LEFTCTRL", "BTN_EXTRA", "NOPE"):
            self.assertNotIn(word, text)


NAMES = {"en": {"up": ("Record the keystroke for turning up", "Clear the keystroke for turning up"),
                "down": ("Record the keystroke for turning down", "Clear the keystroke for turning down")},
         "uk": {"up": ("Записати комбінацію для обертання вгору", "Очистити комбінацію для обертання вгору"),
                "down": ("Записати комбінацію для обертання вниз", "Очистити комбінацію для обертання вниз")}}


class Card(unittest.TestCase):
    """The Turning up and Turning down rows of the Thumb wheel card."""

    def setUp(self):
        self.store = self.make_store()

    @staticmethod
    def make_store():
        store = Store(copy.deepcopy(config.DEFAULTS), profiles._Engine())
        store.flush = lambda: None
        store._save_timer.timeout.disconnect()
        store.front = profiles.FakeFront()
        return store

    def page(self):
        page = pages.PointScrollPage(self.store)
        self.addCleanup(page.deleteLater)
        page.resize(1100, 700)
        page.show()
        return page

    @staticmethod
    def pick(page, function):
        page.thumb_function.activated.emit(page.thumb_function.findData(function))  # as the user picks it

    @staticmethod
    def visible(page):
        return [page.thumb_keys_rows[d].isVisible() for d in ("up", "down")]

    def test_picking_keys_shows_the_rows(self):
        page = self.page()
        last = page.thumb_function.count() - 1
        self.assertEqual((page.thumb_function.itemData(last), page.thumb_function.itemText(last)), ("keys", "Custom keystrokes"))
        self.assertEqual(self.visible(page), [False, False])
        self.pick(page, "keys")
        self.assertEqual((self.store.cfg["thumbwheel"]["function"], self.visible(page)), ("keys", [True, True]))
        for recorder, _record, clear in page.thumb_keys.values():
            self.assertEqual(recorder.text(), "Click to record a shortcut")
            self.assertFalse(clear.isEnabled())
        self.assertIn("Fast turns may skip steps", page.thumb_keys_rows["up"].info.text())
        self.assertEqual(page.thumb_keys_rows["down"].info.text(), "")
        self.assertTrue(page.thumb_ticks_row.isEnabled())
        self.pick(page, "zoom")
        self.assertEqual(self.visible(page), [False, False])

    def test_recording_and_clearing(self):
        page = self.page()
        self.pick(page, "keys")
        recorder, _record, clear = page.thumb_keys["up"]
        recorder.recorded.emit(UNDO)
        self.assertEqual((self.store.cfg["thumbwheel"]["up"], self.store.cfg["thumbwheel"]["down"]), (UNDO, []))
        self.assertEqual(recorder.text(), actions.keys_label(UNDO))
        self.assertTrue(clear.isEnabled())
        QTest.mouseClick(clear, Qt.MouseButton.LeftButton)
        self.assertEqual(self.store.cfg["thumbwheel"]["up"], [])
        self.assertEqual(recorder.text(), "Click to record a shortcut")
        self.assertFalse(clear.isEnabled())

    def test_in_an_app_the_rows_follow_what_it_does_and_write_for_it(self):
        self.store.add_mouse_profile(KATE)
        page = self.page()
        page.apps.apps.setCurrentIndex(1)
        self.assertIn("keystrokes", page.apps.hint.text())
        self.pick(page, "keys")
        self.assertEqual((self.store.cfg["mouse"]["thumbwheel"], self.visible(page)), ([own(KATE, "keys")], [True, True]))
        page.thumb_keys["down"][0].recorded.emit(REDO)
        self.assertEqual(self.store.cfg["mouse"]["thumbwheel_keys"], [{"app": KATE, "up": [], "down": REDO}])
        self.assertEqual(self.store.cfg["thumbwheel"]["down"], [])  # all apps: unchanged
        self.assertEqual((self.store.cfg["mouse"]["thumbwheel"], self.store.cfg["thumbwheel"]["function"]),
                         ([own(KATE, "keys")], "hscroll"))
        self.pick(page, "zoom")
        self.store.set("thumbwheel", "function", "keys")
        self.assertEqual(self.visible(page), [False, False])  # its own function, not the one of all apps
        page.thumb_function.activated.emit(0)  # as in all apps: keys
        self.assertEqual((self.store.cfg["mouse"]["thumbwheel"], self.visible(page)), ([], [True, True]))
        self.assertEqual(page.thumb_keys["down"][0].text(), actions.keys_label(REDO))  # its own

    def test_names_for_assistive_technology_and_the_keyboard(self):
        from mxoptions import i18n
        self.addCleanup(i18n.setup, "en")
        for language in ("en", "uk"):
            with self.subTest(language=language):
                i18n.setup(language)
                self.store = self.make_store()
                page = self.page()
                self.pick(page, "keys")
                for direction in ("up", "down"):
                    recorder, record, clear = page.thumb_keys[direction]
                    self.assertEqual((record.accessibleName(), clear.accessibleName()), NAMES[language][direction])
                    self.assertTrue(record.focusPolicy() & Qt.FocusPolicy.TabFocus)
                    self.assertTrue(clear.focusPolicy() & Qt.FocusPolicy.TabFocus)
                    page.activateWindow()
                    self.assertTrue(QTest.qWaitForWindowActive(page, 2000))
                    QTest.mouseClick(record, Qt.MouseButton.LeftButton)
                    self.assertTrue(recorder.hasFocus())
                    recorder.recorded.emit(UNDO)
                    self.assertTrue(record.hasFocus())
                    self.assertEqual(self.store.cfg["thumbwheel"][direction], UNDO)
                order, widget = [], page.thumb_function
                while len(order) < 4:
                    widget = widget.nextInFocusChain()
                    if widget.isVisible() and widget.isEnabled() and widget.focusPolicy() & Qt.FocusPolicy.TabFocus:
                        order.append(widget)
                up, down = page.thumb_keys["up"], page.thumb_keys["down"]
                self.assertEqual(order, [up[1], up[2], down[1], down[2]])


if __name__ == "__main__":
    unittest.main()
