"""Gestures page: what gesture button does while held (swipes, pan, zoom), with speed and direction of pan and zoom."""

import copy
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import tests.support  # noqa: F401,E402  (puts project on path)
from mxoptions import actions, config, gestures  # noqa: E402
from mxoptions.ui import gestures_page  # noqa: E402
from mxoptions.ui.store import Store  # noqa: E402

APP = QApplication.instance() or QApplication([])


class Engine:
    def __getattr__(self, name):
        return lambda *args: None


class HoldMode(unittest.TestCase):
    def setUp(self):
        self.store = Store(copy.deepcopy(config.DEFAULTS), Engine())
        self.store.flush = lambda: None  # made-up settings: never written anywhere
        self.store._save_timer.timeout.disconnect()
        self.page = gestures_page.GesturesPage(self.store)
        self.addCleanup(self.page.deleteLater)

    def choose(self, mode):
        self.page.mode._buttons[mode].click()

    def gesture(self):
        return self.store.cfg["buttons"]["gesture"]

    def test_choosing_pan_changes_only_that_button_and_swipes_puts_it_back(self):  # U01
        before = copy.deepcopy(self.store.cfg)
        for mode in ("pan", "zoom"):
            with self.subTest(mode=mode):
                self.choose(mode)
                self.assertEqual(self.gesture(), dict(before["buttons"]["gesture"], mode=mode))  # directions kept
                self.assertEqual(dict(self.store.cfg, buttons=before["buttons"]), before)
                self.assertTrue(self.page.mode._buttons[mode].isChecked())
        self.choose("swipes")
        self.assertEqual(self.store.cfg, before)  # no "mode" left behind

    def test_speed_and_reverse_show_only_for_pan_and_zoom_and_swipe_rows_wait(self):  # U02
        page = self.page
        swipe_rows = (page.trigger_row, page.far_mm, page.step_mm, page.dead, page.bias)
        self.assertTrue(page.move_speed.isHidden() and page.reverse_row.isHidden())
        self.assertTrue(all(row.isEnabled() for row in (page.trigger_row, page.far_mm, page.dead, page.bias)))
        for mode in ("pan", "zoom"):
            with self.subTest(mode=mode):
                self.choose(mode)
                self.assertFalse(page.move_speed.isHidden() or page.reverse_row.isHidden())
                self.assertFalse(any(row.isEnabled() for row in swipe_rows))
                self.assertTrue(page.min_mm.isEnabled())  # press zone still counts: a tap
        page.move_speed.slider.setValue(35)  # 0.25 + 35 steps of 0.05
        page.move_reverse.click()
        self.assertEqual((self.store.cfg["gestures"]["move_speed"], self.store.cfg["gestures"]["move_reverse"]), (2.0, True))
        self.choose("swipes")
        self.assertTrue(page.move_speed.isHidden() and page.reverse_row.isHidden())
        self.assertTrue(all(row.isEnabled() for row in (page.trigger_row, page.far_mm, page.dead, page.bias)))

    def test_the_compass_and_caption_say_pan_and_zoom(self):  # U03
        page, tap = self.page, actions.describe(self.gesture()["tap"])
        event = {"points": [(0.0, 0.0), (0.0, 30.0)], "fired": [], "held": False}
        self.choose("pan")
        self.assertEqual(page.pad._labels, dict.fromkeys(gestures.DIRECTIONS, "Pan") | {"tap": tap})
        self.store.gesture_changed.emit(dict(event, result="pan"))
        self.assertEqual(page.caption.text(), "Pan  ·  30 mm")
        self.choose("zoom")
        self.assertEqual(page.pad._labels, {"tap": tap, "up": "Zoom in", "down": "Zoom out", "left": "", "right": ""})
        self.store.gesture_changed.emit(dict(event, result="zoom"))
        self.assertEqual(page.caption.text(), "Zoom  ·  30 mm")

    def test_the_new_controls_have_names_for_screen_readers(self):  # U04
        self.assertEqual(self.page.mode.accessibleName(), "Holding it and moving")
        self.assertEqual(self.page.mode_row.title.text(), "Holding it and moving")
        self.assertEqual(self.page.move_reverse.accessibleName(), "Reverse direction")
        self.assertEqual(self.page.move_speed.title.text(), "Speed")

    def test_with_no_gesture_button_the_choice_is_off(self):  # U05
        buttons = {key: {"type": "none"} if action.get("type") == "gestures" else action
                   for key, action in self.store.cfg["buttons"].items()}
        self.store.set_section("buttons", buttons)
        self.assertFalse(self.page.mode.isEnabled())
        before = copy.deepcopy(self.store.cfg)
        self.choose("pan")
        self.page._set_mode("pan")
        self.assertEqual(self.store.cfg, before)

    def test_the_buttons_page_says_pan_for_a_panning_button(self):  # U06
        gesture = copy.deepcopy(self.gesture())
        self.assertEqual(actions.describe(dict(gesture, mode="pan")), "Pan (hold and move)")
        self.assertEqual(actions.describe(dict(gesture, mode="zoom")), "Zoom (hold and move)")
        plain = actions.describe(gesture)
        for mode in (["pan"], {"pan": 1}, True, "rotate"):
            with self.subTest(mode=mode):
                self.assertEqual(actions.describe(dict(gesture, mode=mode)), plain)
                self.store.cfg["buttons"]["gesture"] = dict(gesture, mode=mode)  # straight from a settings file
                self.page._refresh()
                self.assertTrue(self.page.mode._buttons["swipes"].isChecked())


if __name__ == "__main__":
    unittest.main()
