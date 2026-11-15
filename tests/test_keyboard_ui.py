"""The keyboard's pages as they are laid out: the names and order of the keys, the key picked first, which card holds
which setting, the headings of a key's actions, and the window with the keyboard's pages at its smallest size."""

import copy
import os
import threading
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QRect  # noqa: E402
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QComboBox, QLabel, QSlider, QWidget  # noqa: E402

import tests.support  # noqa: F401,E402
from mxoptions import config, i18n, i18n_uk  # noqa: E402
from mxoptions.ui import action_picker, icons, keyboard_page, pages, theme  # noqa: E402
from mxoptions.ui.store import Store  # noqa: E402

APP = QApplication.instance() or QApplication([])


class Engine:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def make_store():
    store = Store(copy.deepcopy(config.DEFAULTS), Engine())
    store.flush = lambda: None
    store._save_timer.timeout.disconnect()
    return store


def key(cid, protected=False, reason=None):
    return {"id": config.key_id(cid), "cid": cid, "tid": cid, "protected": protected, "active": False,
            "native_reason": "protected" if protected else reason}


def keyboard(keys, settings=(), hosts=()):
    return {"connected": True, "known": True, "name": "Synthetic keyboard", "battery": (64, 0), "current_host": 0,
            "hosts": list(hosts), "keys": keys, "reason": None, "failed": None, "firmware": None,
            "settings": list(settings), "controls": {}}


def settle():
    for _ in range(8):
        APP.processEvents()


class KeyNamesAndOrder(unittest.TestCase):
    def test_known_ids_have_names_others_their_id(self):
        self.assertEqual(keyboard_page.key_title(key(0x00C7)), "Screen brightness down")
        self.assertEqual(keyboard_page.key_title(key(0x0141)), "Play/Pause")  # MX Mechanical Mini's own
        self.assertEqual(keyboard_page.key_title(key(0x0F01)), "Key 0x0f01")

    def test_changeable_keys_first_in_top_row_order_then_kept_ones(self):
        keys = [key(0x0F02), key(0x00D1, protected=True), key(0x00E5), key(0x0F01), key(0x00C7),
                key(0x0108, reason="foreign"), key(0x0034, protected=True)]
        self.assertEqual([k["cid"] for k in keyboard_page.key_sort(keys)],
                         [0x00C7, 0x00E5, 0x0F01, 0x0F02, 0x00D1, 0x0108, 0x0034])
        self.assertEqual([keyboard_page.key_kept(k) for k in keyboard_page.key_sort(keys)], [False] * 4 + [True] * 3)
        self.assertTrue(keyboard_page.key_kept(key(0x00E0, reason="changed")))
        self.assertFalse(keyboard_page.key_kept(key(0x00E0, reason="pending")))

    def test_first_key_is_one_that_can_be_changed(self):
        keys = [key(0x00E0, reason="foreign"), key(0x0F01)]  # a key another program changed, listed first
        self.assertEqual(keyboard_page.first_key(keys), "0x0f01")
        self.assertEqual(keyboard_page.first_key(keys, "0x00e0"), "0x00e0")  # one picked stays picked
        self.assertEqual(keyboard_page.first_key([key(0x00D1, protected=True)]), "0x00d1")
        self.assertIsNone(keyboard_page.first_key([]))

    def test_every_key_name_and_heading_is_translated(self):
        texts = set(keyboard_page.KEY_LABELS.values()) | set(keyboard_page.KEYBOARD_TITLES.values()) | {
            "Typing", "Backlight", "Backlight on", "Firmware & reset", "This computer", "Channel {}: {}",
            "Left as they are", "Mouse", "Keyboard"}
        self.assertEqual(sorted(text for text in texts if text not in i18n_uk.STRINGS), [])


class KeysPageLayout(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.page = keyboard_page.KeysPage(self.store)
        self.addCleanup(self.page.deleteLater)
        self.page.resize(1100, 700)
        self.page.show()

    def show(self, keys):
        self.store.update_state(dict(self.store.state, keyboard=keyboard(keys)))
        settle()

    def test_a_key_left_as_it_is_is_not_picked_first(self):
        self.show([key(0x00E0, reason="foreign"), key(0x0F01), key(0x0F02)])
        self.assertEqual(self.page._key["id"], "0x0f01")
        self.assertTrue(self.page.strip.caps["0x0f01"].isChecked())

    def test_when_the_picked_key_goes_the_first_changeable_one_is_picked(self):
        self.show([key(0x0F01), key(0x0F02), key(0x00C8)])
        self.page.strip.select("0x0f02")
        self.show([key(0x00D1, protected=True), key(0x0F01), key(0x00C8)])
        self.assertEqual(self.page._key["id"], "0x00c8")  # in the order shown: Screen brightness up before 0x0f01

    def test_caps_fill_the_width_and_stay_inside(self):
        self.show([key(cid) for cid in (0x00C7, 0x00C8, 0x00E0, 0x0103)] + [key(0x00D1, protected=True)])
        strip = self.page.strip
        rect = strip.rect()
        for cap in strip.caps.values():
            self.assertTrue(rect.contains(cap.geometry()), cap.accessibleName())
        changeable = [strip.caps[i] for i in ("0x00c7", "0x00c8", "0x00e0", "0x0103")]
        self.assertGreaterEqual(changeable[0].width(), keyboard_page.CAP_MIN_W)
        right = max(cap.geometry().right() for cap in changeable)
        self.assertGreater(right, rect.width() - keyboard_page.PAD - 3)  # no wide empty margin on the right
        self.assertTrue(strip.kept_label.isVisible())
        self.assertGreater(strip.caps["0x00d1"].y(), max(cap.geometry().bottom() for cap in changeable))

    def test_a_changed_key_is_marked(self):
        self.store.set_key("0x0f01", {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_C"]})
        self.show([key(0x0F01), key(0x0F02)])
        self.assertTrue(self.page.strip.caps["0x0f01"].changed)
        self.assertFalse(self.page.strip.caps["0x0f02"].changed)

    def test_the_actions_of_a_key_are_headed_general_not_mouse(self):
        headings = [label.text() for label in self.page.picker.list.findChildren(QLabel)
                    if label.objectName() == "categoryLabel"]
        self.assertIn("GENERAL", headings)
        self.assertNotIn("MOUSE", headings)
        mouse = action_picker.ActionPicker()
        self.addCleanup(mouse.deleteLater)
        self.assertIn("MOUSE", [label.text() for label in mouse.list.findChildren(QLabel)
                                if label.objectName() == "categoryLabel"])

    def test_the_heading_is_translated(self):
        self.addCleanup(i18n.setup, "en")
        i18n.setup("uk")
        picker = action_picker.ActionPicker(exclude=keyboard_page.KEYBOARD_EXCLUDE, titles=keyboard_page.KEYBOARD_TITLES)
        self.addCleanup(picker.deleteLater)
        self.assertIn(i18n_uk.STRINGS["General"].upper(),
                      [label.text() for label in picker.list.findChildren(QLabel) if label.objectName() == "categoryLabel"])


class KeyboardPageCards(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        patcher = mock.patch.object(keyboard_page.firmware, "available", lambda: False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.page = keyboard_page.KeyboardPage(self.store)
        self.addCleanup(self.page.deleteLater)
        self.page.resize(900, 700)
        self.page.show()

    def show(self, settings, hosts=()):
        self.store.update_state(dict(self.store.state, keyboard=keyboard([], settings, hosts)))

    def test_backlight_has_a_card_of_its_own(self):
        page = self.page
        for row in [page.settings_rows["backlight"], *page.backlight_rows.values()]:
            self.assertIn(row, page.backlight_card._rows)
            self.assertNotIn(row, page.settings_card._rows)
        self.show(["backlight"])
        self.assertFalse(page.backlight_card.isHidden())
        self.assertTrue(page.settings_card.isHidden())  # nothing to type with: no Typing card
        self.show(["fn"])
        self.assertTrue(page.backlight_card.isHidden())
        self.assertFalse(page.settings_card.isHidden())
        self.show([])
        self.assertTrue(page.backlight_card.isHidden() and page.settings_card.isHidden())

    def test_channels_say_which_is_this_computer(self):
        self.show([], hosts=[{"paired": True, "name": "workstation"}, {"paired": True, "name": ""},
                             {"paired": False, "name": ""}])
        settle()
        tiles = [tile for tile in self.page.findChildren(keyboard_page.ChannelTile) if tile.isVisible()]
        self.assertEqual([tile.accessibleName() for tile in tiles],
                         ["Channel 1: workstation · This computer", "Channel 2: Paired", "Channel 3: Empty"])
        self.assertEqual(self.page.name.text(), "Synthetic keyboard")
        self.assertEqual(self.page.battery.text(), "64%")


class WindowFit(unittest.TestCase):
    """The window with a keyboard, at its smallest sizes, in both languages, with letters a tenth wider too, with and
    without icons in the sidebar: it keeps the size asked for, and the keyboard's pages fit inside it."""

    def test_keyboard_pages_fit_inside_the_window(self):
        from mxoptions.ui.main_window import MainWindow

        pixmap = QPixmap(4, 4)
        pixmap.fill(QColor("#888888"))
        old_style, old_sheet, old_palette, old_font = APP.style().objectName(), APP.styleSheet(), APP.palette(), APP.font()
        self.addCleanup(i18n.setup, "en")
        keys = [key(cid) for cid in (0x00C7, 0x00C8, 0x00E0, 0x0103, 0x0108, 0x010A, 0x011C, 0x00E5)] + [
            key(cid, protected=True) for cid in (0x00D1, 0x00D2, 0x00D3, 0x0034)]
        found = keyboard(keys, ["fn", "backlight", "platform", "disable", "locks"],
                         [{"paired": True, "name": "workstation"}, {"paired": True, "name": "laptop"}])
        try:
            APP.setStyle("Fusion")
            for with_icons in (False, True):
                for spacing, language in ((100, "en"), (100, "uk"), (110, "en"), (110, "uk")):
                    font = QFont(old_font)
                    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, spacing)
                    APP.setFont(font)
                    theme.apply(APP)
                    i18n.setup(language)
                    store = make_store()
                    with (mock.patch.object(pages.FirmwareQuery, "start"),
                          mock.patch.object(pages.MouseView, "_prerender"),
                          mock.patch.object(icons, "has", lambda name: with_icons and bool(name)),
                          mock.patch.object(icons, "icon", lambda name: QIcon(pixmap)),
                          mock.patch.object(threading.Thread, "start")):
                        window = MainWindow(store, hide_on_close=False)
                        try:
                            self.assertEqual(all(not button.icon().isNull() for button in window._nav.buttons()),
                                             with_icons)
                            window.show()
                            store.update_state(dict(store.state, keyboard=found))
                            for index in window._keyboard_pages:
                                for width in (1180, 1200, 1280):
                                    with self.subTest(icons=with_icons, language=language, spacing=spacing,
                                                      page=index, width=width):
                                        window._nav.button(index).click()
                                        window.resize(width, 780)
                                        settle()
                                        self.assertEqual(window.width(), width)
                                        page = window.stack.currentWidget()
                                        frame = QRect(QPoint(), window.size())
                                        # every control across the page: inside the window from side to side (the
                                        # Keyboard page scrolls, so its lower cards may be below the window's edge)
                                        for widget in page.findChildren(QWidget):
                                            if not isinstance(widget, (QAbstractButton, QComboBox, QSlider, QLabel)) \
                                                    or not widget.isVisible():
                                                continue
                                            place = QRect(widget.mapTo(window, QPoint()), widget.size())
                                            self.assertTrue(0 <= place.left() and place.right() <= frame.right(),
                                                            widget.accessibleName() or widget.objectName())
                                            if isinstance(widget, keyboard_page.KeyCap):
                                                self.assertTrue(frame.contains(place), widget.accessibleName())
                                        for button in window._nav.buttons():
                                            place = QRect(button.mapTo(window, QPoint()), button.size())
                                            self.assertTrue(frame.contains(place), button.text())
                                            self.assertGreaterEqual(button.width(), button.sizeHint().width(),
                                                                    button.text())
                        finally:
                            store._save_timer.stop()
                            window.hide()
                            window.deleteLater()
                            APP.sendPostedEvents(window, QEvent.Type.DeferredDelete)
        finally:
            APP.setFont(old_font)
            APP.setStyle(old_style)
            APP.setStyleSheet(old_sheet)
            APP.setPalette(old_palette)


if __name__ == "__main__":
    unittest.main()
