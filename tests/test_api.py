import copy
import io
import json
import os
import unittest
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import tests.support  # noqa: F401,E402
from mxoptions import actions, api, config, device, environment  # noqa: E402
from mxoptions.ui import keyboard_page, sounds  # noqa: E402
from mxoptions.ui.store import Store  # noqa: E402

APP = QApplication.instance() or QApplication([])  # widgets too: the keyboard's page is tested here


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



class Settings(unittest.TestCase):
    """Set() and the settings it changes, as other programs see them."""

    COPY = {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_C"]}

    def setUp(self):
        class Engine:
            def __getattr__(self, name):
                return lambda *args: None
        self.store = Store(copy.deepcopy(config.DEFAULTS), Engine())
        self.store.flush = lambda: None  # made-up settings: never written anywhere
        self.store._save_timer.timeout.disconnect()
        self.api = api.MouseApi(self.store, None, lambda: None)

    def keys(self):
        return self.store.cfg["keyboard"]["keys"]

    def test_keys_are_added_and_taken_out_as_a_whole(self):
        self.assertTrue(self.api.Set("keyboard.keys", json.dumps({"0x00c7": self.COPY, "0x00c8": {"type": "host", "host": 1}})))
        self.assertEqual(json.loads(self.api.Get("keyboard.keys.0x00c7")), self.COPY)
        self.assertTrue(self.api.Set("keyboard.keys.0x00c7", json.dumps({"type": "keys", "keys": ["KEY_F5"]})))  # one that is there
        self.assertEqual(self.keys()["0x00c7"], {"type": "keys", "keys": ["KEY_F5"]})
        self.assertFalse(self.api.Set("keyboard.keys.0x00c9", json.dumps(self.COPY)))  # one that is not: no such path
        self.assertFalse(self.api.Set("keyboard.keys.0x00c7", json.dumps({"type": "default"})))  # taken out as a whole only
        self.assertTrue(self.api.Set("keyboard.keys", json.dumps({"0x00c8": {"type": "host", "host": 1}})))
        self.assertEqual(list(self.keys()), ["0x00c8"])
        self.assertIn('"keyboard"', self.api.GetConfig())

    def test_what_a_key_cannot_have_is_refused_not_dropped(self):
        self.api.Set("keyboard.keys", json.dumps({"0x00c7": self.COPY}))
        before = copy.deepcopy(self.store.cfg)
        for value in ({"0x00c7": {"type": "gestures"}}, {"0x00c7": {"type": "flow"}}, {"0x00C7": self.COPY},
                      {"0x00c7": {"type": "keys"}}, {"0x00c7": self.COPY, "0x00c8": {"type": "default"}}):
            with self.subTest(value=value):
                self.assertFalse(self.api.Set("keyboard.keys", json.dumps(value)))
                self.assertEqual(self.store.cfg, before)
        self.assertFalse(self.api.Set("keyboard.keys.0x00c7", json.dumps({"type": "ring", "x": 1})))
        self.assertFalse(self.api.Set("keyboard", json.dumps({"keys": {"0x00c7": {"type": "dpi_shift"}}})))
        self.assertEqual(self.store.cfg, before)

    def test_a_key_may_open_the_actions_ring_and_trigger_still_may_not(self):
        listed = self.api.ListActions()
        self.api.Set("keyboard.keys", json.dumps({"0x00c7": self.COPY}))
        self.assertTrue(self.api.Set("keyboard.keys.0x00c7", json.dumps({"type": "ring"})))
        self.assertEqual(self.keys(), {"0x00c7": {"type": "ring"}})
        self.assertTrue(self.api.Set("keyboard.keys", json.dumps({"0x00c8": {"type": "ring"}})))
        self.assertFalse(self.api.Set("keyboard.keys", json.dumps({"0x00c8": {"type": "ring", "bubbles": []}})))
        self.assertEqual(self.keys(), {"0x00c8": {"type": "ring"}})
        self.assertFalse(self.api.Trigger(json.dumps({"type": "ring"})))
        self.assertFalse(self.api.Trigger("ring"))
        self.assertNotIn("ring", listed)
        self.assertEqual(self.api.ListActions(), listed)

    def test_a_key_with_an_action_that_cannot_run_is_refused_everywhere(self):
        self.api.Set("keyboard.keys", json.dumps({"0x00c7": self.COPY}))
        before = copy.deepcopy(self.store.cfg)
        for action in ({"type": "desktop", "do": []}, {"type": "desktop", "do": {}},
                       {"type": "keys", "keys": ["KEY_NOT_REAL"]}, {"type": "macro", "steps": [{"do": "delay", "ms": "invalid"}]},
                       {"type": "macro", "steps": [{"do": "keys"}]}, {"type": "macro", "steps": [{"do": "click", "button": 1}]}):
            with self.subTest(action=action):
                self.assertFalse(self.api.Set("keyboard.keys", json.dumps({"0x00c7": action})))
                self.assertFalse(self.api.Set("keyboard.keys.0x00c7", json.dumps(action)))
                self.assertFalse(self.store.set_key("0x00c7", action))  # what the pages set goes through the same check
                self.assertFalse(self.store.set_key("0x00c8", action))
                self.assertEqual(self.store.cfg, before)
        self.assertFalse(self.store.set_key("0x00C7", self.COPY))
        self.assertTrue(self.store.set_key("0x00c8", self.COPY))

    def test_the_mouse_settings_work_as_before(self):
        self.assertTrue(self.api.Set("pointer.dpi", "1600"))
        self.assertEqual(self.store.cfg["pointer"]["dpi"], 1600)
        self.assertTrue(self.api.Set("wheel.threshold", "900"))  # clamped, as ever
        self.assertEqual(self.store.cfg["wheel"]["threshold"], 50)
        self.assertFalse(self.api.Set("wheel.mode", "sideways"))  # text it does not know: refused
        self.assertFalse(self.api.Set("pointer.dpi", '"fast"'))
        self.assertFalse(self.api.Set("version", "7"))
        self.assertFalse(self.api.Set("no.such.thing", "1"))
        self.assertTrue(self.api.Set("buttons.back", json.dumps({"type": "gestures"})))  # a mouse button still may
        self.assertEqual(self.store.cfg["buttons"]["back"]["type"], "gestures")

    def test_store_keys_and_restore_defaults(self):
        self.store.set_key("0x00c7", self.COPY)
        self.store.set_key("0x00c8", {"type": "none"})
        self.store.set_key("0x00c8", {"type": "default"})
        self.assertEqual(self.keys(), {"0x00c7": self.COPY})
        self.store.restore_defaults()
        self.assertEqual(self.keys(), {})
        self.assertIsNone(self.store.state["keyboard"])

    def test_a_key_macro_with_a_stop_value_not_true_or_false_is_refused(self):
        stoppable = {"type": "macro", "name": "m", "repeat": False, "steps": [{"do": "keys", "keys": ["KEY_A"]}],
                     "stop_on_press": True}
        self.api.Set("keyboard.keys", json.dumps({"0x00c7": self.COPY}))
        before = copy.deepcopy(self.store.cfg)
        for value in ("yes", 1, None):
            with self.subTest(value=value):
                wrong = dict(stoppable, stop_on_press=value)
                self.assertFalse(self.api.Set("keyboard.keys", json.dumps({"0x00c7": wrong})))
                self.assertFalse(self.api.Set("keyboard.keys.0x00c7", json.dumps(wrong)))
                self.assertFalse(self.store.set_key("0x00c7", wrong))
                self.assertEqual(self.store.cfg, before)
        self.assertTrue(self.api.Set("keyboard.keys.0x00c7", json.dumps(stoppable)))
        self.assertEqual(json.loads(self.api.Get("keyboard.keys.0x00c7")), stoppable)

    def test_a_gesture_button_can_be_set_to_pan_and_its_speed_changed(self):  # A01
        plain = copy.deepcopy(self.store.cfg["buttons"]["gesture"])
        pan = dict(plain, mode="pan")
        self.assertTrue(self.api.Set("buttons.gesture", json.dumps(pan)))
        self.assertEqual(self.store.cfg["buttons"]["gesture"], pan)
        self.assertTrue(self.api.Set("buttons.gesture.mode", json.dumps("zoom")))  # there now: one field will do
        self.assertTrue(self.api.Set("gestures.move_speed", json.dumps(2.0)))
        self.assertTrue(self.api.Set("gestures.move_reverse", json.dumps(True)))
        self.assertEqual((self.store.cfg["buttons"]["gesture"]["mode"], self.store.cfg["gestures"]["move_speed"],
                          self.store.cfg["gestures"]["move_reverse"]), ("zoom", 2.0, True))
        before = copy.deepcopy(self.store.cfg)
        self.assertFalse(self.api.Set("gestures.move_reverse", json.dumps("yes")))
        for value in ("rotate", ["pan"], {"pan": 1}, True):
            with self.subTest(leaf=value):
                self.assertFalse(self.api.Set("buttons.gesture.mode", json.dumps(value)))
                self.assertEqual(self.store.cfg, before)
        for value in (["pan"], {"pan": 1}, True):
            with self.subTest(whole=value):  # as before this mode: the action is taken, what it cannot have left out
                self.assertTrue(self.api.Set("buttons.gesture", json.dumps(dict(plain, mode=value))))
                self.assertEqual(self.store.cfg["buttons"]["gesture"], plain)


class KeyChooser(unittest.TestCase):
    """The keys on the Keys page, picked and given an action with the keyboard alone."""

    def test_a_second_key_is_picked_and_set_without_a_pointer(self):
        class Engine:
            def __getattr__(self, name):
                return lambda *args: None
        store = Store(copy.deepcopy(config.DEFAULTS), Engine())
        store.flush = lambda: None
        store._save_timer.timeout.disconnect()
        keys = [{"id": config.key_id(cid), "cid": cid, "tid": cid, "protected": cid == 0x00D1, "active": False,
                 "native_reason": "protected" if cid == 0x00D1 else None} for cid in (0x00D1, 0x0F01, 0x0F02)]
        page = keyboard_page.KeysPage(store)
        self.addCleanup(page.deleteLater)
        page.resize(1100, 600)
        page.show()
        page.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(page))
        store.update_state(dict(store.state, keyboard={"connected": True, "known": True, "name": "Synthetic keyboard",
                                                       "battery": None, "current_host": 0, "hosts": [], "keys": keys,
                                                       "reason": None, "failed": None}))
        caps = page.strip.caps
        self.assertEqual([cap.accessibleName() for cap in caps.values()], ["Easy-Switch 1", "Key 0x0f01", "Key 0x0f02"])
        self.assertEqual(caps["0x00d1"].accessibleDescription(), "Kept by the keyboard")
        self.assertTrue(all(cap.focusPolicy() & Qt.FocusPolicy.TabFocus for cap in caps.values()))
        self.assertTrue(caps["0x0f01"].isChecked())  # the first key that can be changed, to begin with
        caps["0x0f01"].setFocus()
        QTest.keyClick(caps["0x0f01"], Qt.Key.Key_Right)
        self.assertTrue(caps["0x0f02"].hasFocus())
        self.assertTrue(caps["0x0f02"].isChecked())
        self.assertEqual(page._key["id"], "0x0f02")
        page.picker.changed.emit(actions.keys("LEFTCTRL", "V"))
        self.assertEqual(store.cfg["keyboard"]["keys"], {"0x0f02": actions.keys("LEFTCTRL", "V")})
        self.assertEqual(caps["0x0f02"].accessibleDescription(), actions.describe(actions.keys("LEFTCTRL", "V")))
        QTest.keyClick(caps["0x0f02"], Qt.Key.Key_Left)
        QTest.keyClick(caps["0x0f01"], Qt.Key.Key_Left)
        self.assertEqual(page._key["id"], "0x00d1")  # a key the keyboard keeps is reached too, and says so
        QTest.keyClick(caps["0x00d1"], Qt.Key.Key_Right)
        QTest.keyClick(caps["0x0f01"], Qt.Key.Key_Space)
        self.assertEqual(page._key["id"], "0x0f01")


class BatterySaving(unittest.TestCase):
    """haptics.battery_saving as other programs and the Haptics page see it: from the settings and the state the
    engine last sent; the engine itself decides again before it plays (tests.test_rumble.BatterySaving)."""

    HINT = ("At 10% battery or less the mouse does not vibrate, not even for patterns you try or for games; above 10% "
            "it does again. What the mouse does by itself, as when it is switched on, stays.")

    def setUp(self):
        played = self.played = []

        class Engine:
            def play_haptic(self, waveform):
                played.append(waveform)

            def __getattr__(self, name):
                return lambda *args: None
        self.store = Store(copy.deepcopy(config.DEFAULTS), Engine())
        self.store.flush = lambda: None  # made-up settings: never written anywhere
        self.store._save_timer.timeout.disconnect()
        self.api = api.MouseApi(self.store, None, lambda: None)

    def mouse(self, battery=(82, device.BATTERY_DISCHARGING), connected=True, waveforms=(0x0C, 0x1B)):
        self.store.update_state(dict(self.store.state, connected=connected, known=True, name="Synthetic mouse",
                                     battery=battery, caps={"waveforms": list(waveforms), "force": None}))

    def page(self):
        from mxoptions.ui.pages import HapticsPage
        page = HapticsPage(self.store)
        self.addCleanup(page.deleteLater)
        page.show()
        return page

    def test_another_program_is_told_no_while_it_keeps_the_mouse_still(self):
        self.mouse()
        listed = self.api.ListHaptics()
        self.assertEqual(listed, ["knock", "whisper-collision"])
        for battery, saving, connected, told in (((10, 0), True, True, False), ((1, 0), True, True, False),
                                                 ((10, device.BATTERY_CHARGING), True, True, False),
                                                 ((11, 0), True, True, True), ((10, 0), False, True, True),
                                                 ((0, 0), True, True, True), (None, True, True, True),
                                                 ((50, 0), True, False, False), ((10, 0), False, False, False)):
            with self.subTest(battery=battery, saving=saving, connected=connected):
                self.played.clear()
                self.store.set("haptics", "battery_saving", saving)
                self.mouse(battery, connected)
                self.assertIs(self.api.PlayHaptic("knock"), told)
                self.assertEqual(self.played, [0x0C] if told else [])
                self.assertEqual(self.api.ListHaptics(), listed)  # what it can play is the same

    def test_other_programs_switch_it_with_a_true_or_false_only(self):
        self.assertEqual(json.loads(self.api.Get("haptics.battery_saving")), True)
        for value in ("1", "0", '"false"', "[]"):
            with self.subTest(value=value):
                self.assertFalse(self.api.Set("haptics.battery_saving", value))
                self.assertIs(self.store.cfg["haptics"]["battery_saving"], True)
        self.assertTrue(self.api.Set("haptics.battery_saving", "false"))
        self.assertIs(self.store.cfg["haptics"]["battery_saving"], False)
        self.assertIs(self.store.cfg["haptics"]["enabled"], True)

    def test_defaults_put_it_back_on(self):
        self.store.set("haptics", "battery_saving", False)
        self.store.restore_defaults()
        self.assertIs(self.store.cfg["haptics"]["battery_saving"], True)

    def test_the_switch_on_the_haptics_page(self):
        self.mouse()
        page = self.page()
        rows = page.feedback_card._rows
        self.assertEqual(rows[0].title.text(), "Haptic feedback")
        self.assertIs(rows[1], page.saving_row)  # right under it
        self.assertIs(page.saving_row.control, page.saving)
        self.assertEqual((page.saving_row.title.text(), page.saving.accessibleName()), ("Battery saving", "Battery saving"))
        self.assertEqual(page.saving_row.info.text(), self.HINT)
        self.assertEqual(page.saving_row.note.text(), "Vibration is off now to save the battery.")
        self.assertTrue(page.saving.isChecked())
        page.saving.click()
        self.assertIs(self.store.cfg["haptics"]["battery_saving"], False)
        self.store.set("haptics", "battery_saving", True)
        self.assertTrue(page.saving.isChecked())
        self.store.set("haptics", "enabled", False)
        self.assertFalse(page.saving_row.isEnabled())
        self.store.set("haptics", "enabled", True)
        self.assertTrue(page.saving_row.isEnabled())
        self.mouse(waveforms=())
        self.assertTrue(page.feedback_card.isHidden())  # a mouse without a motor: no word of it

    def test_the_page_says_when_it_keeps_the_mouse_still_now(self):
        page = self.page()
        for battery, connected, saving, enabled, said in (((82, 0), True, True, True, False),
                                                          ((10, 0), True, True, True, True),
                                                          ((10, device.BATTERY_CHARGING), True, True, True, True),
                                                          ((11, 0), True, True, True, False),
                                                          ((0, 0), True, True, True, False),
                                                          ((10, 0), False, True, True, False),
                                                          ((10, 0), True, False, True, False),
                                                          ((10, 0), True, True, False, False),
                                                          ((5, 0), True, True, True, True)):
            with self.subTest(battery=battery, connected=connected, saving=saving, enabled=enabled):
                self.store.set("haptics", "battery_saving", saving)
                self.store.set("haptics", "enabled", enabled)
                self.mouse(battery, connected)
                self.assertEqual(not page.saving_row.note.isHidden(), said)

    def test_in_ukrainian(self):
        from mxoptions import i18n
        self.addCleanup(i18n.setup, "en")
        i18n.setup("uk")
        self.mouse((9, 0))
        page = self.page()
        self.assertEqual((page.saving_row.title.text(), page.saving.accessibleName()), ("Економія заряду", "Економія заряду"))
        self.assertEqual(page.saving_row.info.text(), (
            "При заряді 10% або менше миша не вібрує, навіть для візерунків, які ви пробуєте, чи для ігор; понад 10% "
            "вона знову вібрує. Те, що миша робить сама, як-от під час увімкнення, не змінюється."))
        self.assertEqual(page.saving_row.note.text(), "Зараз вібрацію вимкнено, щоб зберегти заряд.")
        self.assertFalse(page.saving_row.note.isHidden())

    def test_other_pages_that_vibrate_say_nothing_of_it(self):
        from PySide6.QtWidgets import QAbstractButton, QLabel
        from mxoptions.ui import flow_page, gestures_page
        self.mouse((5, 0))
        for made in (gestures_page.GesturesPage, flow_page.FlowPage):
            with self.subTest(page=made.__name__):
                page = made(self.store)
                self.addCleanup(page.deleteLater)
                texts = [widget.text() for widget in page.findChildren(QLabel)]
                names = [widget.accessibleName() for widget in page.findChildren(QAbstractButton)]
                self.assertFalse([text for text in texts + names if "Battery saving" in text or "save the battery" in text])


class SwitchingScreens(unittest.TestCase):
    """haptics.screens through Set() and on the Haptics page: last row of Feedback, honest where it cannot be."""

    HINT = ("A short tick when the pointer moves onto another screen of this computer. Moving to another computer with "
            "Flow does not count.")
    NOTE = "This desktop does not tell programs which screen the pointer is on."

    setUp, mouse, page = BatterySaving.setUp, BatterySaving.mouse, BatterySaving.page

    def test_other_programs_switch_it_with_a_true_or_false_only(self):
        self.assertEqual(json.loads(self.api.Get("haptics.screens")), False)
        for value in ("1", "0", '"true"', "[]"):
            with self.subTest(value=value):
                self.assertFalse(self.api.Set("haptics.screens", value))
                self.assertIs(self.store.cfg["haptics"]["screens"], False)
        self.assertTrue(self.api.Set("haptics.screens", "true"))
        self.assertIs(self.store.cfg["haptics"]["screens"], True)
        self.store.restore_defaults()
        self.assertIs(self.store.cfg["haptics"]["screens"], False)

    def test_the_switch_on_the_haptics_page(self):
        self.mouse()
        page = self.page()
        rows = page.feedback_card._rows
        self.assertIs(rows[-1], page.screens_row)
        self.assertIs(rows[-2], page.feedback_row)
        self.assertIs(page.screens_row.control, page.screens)
        self.assertEqual((page.screens_row.title.text(), page.screens.accessibleName()), ("Switching screens",) * 2)
        self.assertEqual(page.screens_row.info.text(), self.HINT)
        self.assertEqual(page.screens_row.note.text(), self.NOTE)
        self.assertTrue(page.screens_row.note.isHidden())  # the page on its own: as where it can be
        self.assertTrue(page.screens.isEnabled())
        self.assertFalse(page.screens.isChecked())
        page.screens.click()
        self.assertIs(self.store.cfg["haptics"]["screens"], True)
        self.store.set("haptics", "screens", False)
        self.assertFalse(page.screens.isChecked())
        self.store.set("haptics", "enabled", False)
        self.assertFalse(page.screens_row.isEnabled())
        self.store.set("haptics", "enabled", True)
        self.assertTrue(page.screens_row.isEnabled())
        self.mouse(waveforms=())
        self.assertTrue(page.feedback_card.isHidden())  # no motor: no word of it

    def test_where_the_desktop_cannot_tell(self):
        class Flow:
            followable = False

            def screens_followable(self):
                return self.followable
        self.store.flow = service = Flow()
        self.mouse()
        page = self.page()
        self.assertFalse(page.screens.isEnabled())
        self.assertFalse(page.screens_row.note.isHidden())
        self.assertEqual(page.screens.accessibleDescription(), self.NOTE)
        service.followable = True
        self.store.set("haptics", "level", 40)  # any change draws the page again
        self.assertTrue(page.screens.isEnabled())
        self.assertTrue(page.screens_row.note.isHidden())
        self.assertEqual(page.screens.accessibleDescription(), "")

    def test_in_ukrainian(self):
        from mxoptions import i18n
        self.addCleanup(i18n.setup, "en")
        i18n.setup("uk")
        self.mouse()
        page = self.page()
        self.assertEqual((page.screens_row.title.text(), page.screens.accessibleName()), ("Перемикання екранів",) * 2)
        self.assertEqual(page.screens_row.note.text(), "Цей робочий стіл не повідомляє програмам, на якому екрані вказівник.")


if __name__ == "__main__":
    unittest.main()
