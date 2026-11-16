"""The keyboard's pages as they are laid out: the names and order of the keys, the key picked first, which card holds
which setting, the headings of a key's actions, and the window with the keyboard's pages at its smallest size."""

import copy
import itertools
import os
import threading
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QComboBox, QLabel, QSlider, QWidget  # noqa: E402

import tests.support  # noqa: F401,E402
from mxoptions import config, i18n, i18n_uk  # noqa: E402
from mxoptions.ui import action_picker, icons, keyboard_drawing, keyboard_page, pages, theme  # noqa: E402
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


def key(cid, protected=False, reason=None, pos=0):
    return {"id": config.key_id(cid), "cid": cid, "tid": cid, "protected": protected, "active": False,
            "native_reason": "protected" if protected else reason, "pos": pos}


# What a real MX Mechanical may list: the keys Logi Options+ lets be programmed on it (its device list), twelve on the
# F-row, and the Easy-Switch keys and Fn, which the keyboard keeps
MX_MECHANICAL = (
    [key(cid, pos=n) for n, cid in enumerate((0x00C7, 0x00C8, 0x00E2, 0x00E3, 0x0103, 0x0108, 0x010A, 0x011C, 0x00E4,
                                              0x00E5, 0x00E6, 0x00E7), 1)]
    + [key(cid) for cid in (0x00E8, 0x00E9, *range(0x010B, 0x0116), 0x011E, *range(0x0116, 0x011C), 0x000A, 0x006E,
                            0x00D4, 0x006F)]
    + [key(cid, protected=True) for cid in (0x00D1, 0x00D2, 0x00D3, 0x0034)])


def keyboard(keys, settings=(), hosts=(), name="Synthetic keyboard"):
    return {"connected": True, "known": True, "name": name, "battery": (64, 0), "current_host": 0,
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


class Drawing(unittest.TestCase):
    """Where keys go on the picture: only where that is known, the same whatever else changes."""

    def test_the_f_key_said_and_known_ids_give_places_nothing_else(self):
        keys = [key(0x00C7, pos=1), key(0x00E9, pos=12), key(0x00E4, pos=13), key(0x00E5, pos=16),
                key(0x0F01), key(0x010C), key(0x0116), key(0x0118), key(0x00E8)]
        full = keyboard_drawing.slots(keys, "full", "Synthetic keyboard")
        self.assertEqual(full, {"0x00c7": "f1", "0x00e9": "f12", "0x010c": "tab", "0x0116": "insert", "0x0118": "home"})
        compact = keyboard_drawing.slots(keys, "compact")  # a Mini: Home, End and the pages down the side, no Insert
        self.assertEqual(compact, {"0x00c7": "f1", "0x00e9": "f12", "0x010c": "tab", "0x0118": "home"})
        on_mini = {slot for slot, _rect, _legend in keyboard_drawing.board("compact")}
        self.assertTrue({"home", "end", "pgup", "pgdn", "delete"} <= on_mini)
        self.assertNotIn("insert", on_mini)

    def test_a_place_two_keys_claim_stays_with_the_lower_id_whatever_the_order_or_protection(self):
        keys = [key(0x0F02, pos=3), key(0x0F01, pos=3), key(0x010C), key(0x00E0, pos=4)]
        expected = {"0x0f01": "f3", "0x010c": "tab", "0x00e0": "f4"}
        self.assertEqual(keyboard_drawing.slots(keys, "full"), expected)
        self.assertEqual(keyboard_drawing.slots(list(reversed(keys)), "full"), expected)
        keys[1] = key(0x0F01, protected=True, pos=3)
        self.assertEqual(keyboard_drawing.slots(keys, "full"), expected)

    def test_an_mx_mechanical_is_drawn_as_options_plus_draws_it(self):
        unsaid = [dict(k, pos=0) for k in MX_MECHANICAL]  # no key says its F-key: the F-row Options+ shows
        slot_of = keyboard_drawing.slots(unsaid, "full", "MX Mechanical")
        self.assertEqual([slot_of[config.key_id(cid)] for cid in keyboard_drawing.F_ROWS["MX Mechanical"]],
                         [f"f{n}" for n in range(1, 13)])
        self.assertEqual({slot_of[config.key_id(cid)] for cid in (0x00E8, 0x00E9, 0x00D1, 0x00D2, 0x00D3, 0x000A,
                                                                    0x006E, 0x00D4, 0x006F, 0x0034)},
                         {"vol_down", "vol_up", "es1", "es2", "es3", "np1", "np2", "np3", "np4", "fn"})
        self.assertEqual(slot_of, keyboard_drawing.slots(list(reversed(unsaid)), "full", "MX Mechanical"))
        other = keyboard_drawing.slots(unsaid, "full", "Some full-size keyboard")  # no picture of it: no such places
        self.assertFalse({"f1", "vol_down", "es1", "np1", "fn"} & set(other.values()))
        mini = keyboard_drawing.slots(unsaid, "compact", "MX Mechanical")
        self.assertFalse({"vol_down", "es1", "np1"} & set(mini.values()))

    def test_a_key_saying_its_f_key_turns_the_model_s_f_row_off(self):
        keys = [key(0x00C7), key(0x0200, pos=1)]
        for order in (keys, list(reversed(keys)), [key(0x00C7, protected=True), key(0x0200, pos=1)]):
            self.assertEqual(keyboard_drawing.slots(order, "full", "MX Mechanical"), {"0x0200": "f1"})

    def test_every_placed_key_is_found_where_it_is_drawn(self):
        image = QImage(900, 300, QImage.Format.Format_ARGB32)
        for layout in keyboard_drawing.LAYOUTS:
            with self.subTest(layout=layout):
                slot_of = keyboard_drawing.slots(MX_MECHANICAL, layout, "MX Mechanical")
                self.assertGreaterEqual(len(slot_of), 20)
                area = QRectF(0, 0, 900, 300)
                painter = QPainter(image)
                rects = keyboard_drawing.paint(painter, area, layout, slot_of, kept={"0x00d1"})
                painter.end()
                board, _unit = keyboard_drawing.fit(area, layout)
                self.assertEqual(rects, keyboard_drawing.key_rects(area, layout, slot_of))
                self.assertEqual(set(rects), set(slot_of))
                for key_id, rect in rects.items():
                    self.assertTrue(board.contains(rect), key_id)
                    self.assertEqual(keyboard_drawing.key_at(area, layout, slot_of, rect.center()), key_id)
                for one, other in itertools.combinations(rects.values(), 2):
                    self.assertFalse(one.intersects(other))


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

    def test_keys_on_the_picture_are_buttons_over_their_keys_the_rest_under_it(self):
        self.show([key(0x00E0, pos=3), key(0x00C7, pos=1), key(0x0F01), key(0x010C), key(0x00D1, protected=True)])
        strip = self.page.strip
        board = strip._board()
        rects = keyboard_drawing.key_rects(board, strip._layout_name, strip._slot_of)
        for key_id in ("0x00c7", "0x00e0", "0x010c"):  # laid over their keys, drawing only the focus
            cap = strip.caps[key_id]
            self.assertTrue(cap.overlay, key_id)
            self.assertEqual(cap.geometry(), rects[key_id].toRect())
        for key_id in ("0x0f01", "0x00d1"):  # no place known: under the picture
            self.assertFalse(strip.caps[key_id].overlay)
            self.assertGreater(strip.caps[key_id].y(), board.bottom(), key_id)
        self.assertGreater(strip.caps["0x00d1"].y(), strip.caps["0x0f01"].geometry().bottom())
        self.assertTrue(strip.other_label.isVisible() and strip.kept_label.isVisible())
        rect = strip.rect()
        for cap in strip.caps.values():
            self.assertTrue(rect.contains(cap.geometry()), cap.accessibleName())
        self.assertEqual(list(strip.caps), ["0x00c7", "0x00e0", "0x010c", "0x0f01", "0x00d1"])  # the Tab order

    def test_the_key_in_question_has_a_tag_over_it(self):
        self.store.set_key("0x00e0", {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_C"]})
        self.show([key(0x00C7, pos=1), key(0x00E0, pos=3)])
        strip = self.page.strip
        tags = []
        original = keyboard_page.KeyStrip._tag
        with mock.patch.object(keyboard_page.KeyStrip, "_tag", lambda self, painter, key_id, text, strong:
                               tags.append((key_id, text, strong)) or original(self, painter, key_id, text, strong)):
            strip.select("0x00e0")
            strip._set_hover("0x00c7")
            strip.grab()
        self.assertIn(("0x00e0", strip.caps["0x00e0"].value, True), tags)  # what it does, once changed
        self.assertIn(("0x00c7", "Screen brightness down", False), tags)  # the name of the key the pointer is on

    def test_a_click_on_a_key_of_the_picture_picks_it_and_the_keyboard_goes_on_from_there(self):
        self.show([key(0x00C7, pos=1), key(0x00C8, pos=2), key(0x010C)])
        strip = self.page.strip
        self.page.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(self.page))
        cap = strip.caps["0x00c8"]
        QTest.mouseClick(cap, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, cap.rect().center())
        self.assertEqual(self.page._key["id"], "0x00c8")
        cap.setFocus()
        QTest.keyClick(cap, Qt.Key.Key_Left)
        self.assertEqual(self.page._key["id"], "0x00c7")
        QTest.mouseClick(strip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         QPoint(2, int(strip._board().bottom()) - 2))  # the case, no key: nothing changes
        self.assertEqual(self.page._key["id"], "0x00c7")

    def test_tab_comes_to_the_picked_key_and_goes_on_past_the_keys(self):
        """The keys are one group, as radio buttons are: Tab comes to the key picked and leaves the group for the
        next control, Shift+Tab comes back to it; the arrows go from key to key (next test)."""
        self.show([key(0x00C7, pos=1), key(0x00C8, pos=2), key(0x010C), key(0x00D1, protected=True)])
        strip = self.page.strip
        self.page.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(self.page))
        strip.select("0x010c")
        self.page.remove_app.setEnabled(True)
        self.page.remove_app.setFocus()
        QTest.keyClick(self.page.remove_app, Qt.Key.Key_Tab)
        self.assertIs(APP.focusWidget(), strip.caps["0x010c"])  # the key picked, in the section under the picture
        QTest.keyClick(APP.focusWidget(), Qt.Key.Key_Tab)
        self.assertIs(APP.focusWidget(), self.page.restore)  # past the keys: Restore keyboard defaults
        QTest.keyClick(APP.focusWidget(), Qt.Key.Key_Backtab)
        self.assertIs(APP.focusWidget(), strip.caps["0x010c"])

    def test_the_arrows_go_from_key_to_key_on_the_picture_and_under_it(self):
        self.show([key(0x00C7, pos=1), key(0x00C8, pos=2), key(0x010C), key(0x0F01)])
        strip = self.page.strip
        first = strip.caps["0x00c7"]
        first.setFocus()
        QTest.keyClick(first, Qt.Key.Key_Right)
        self.assertEqual(self.page._key["id"], "0x00c8")  # along the F-row
        seen = ["0x00c8"]
        for _ in range(3):  # down the picture, and on to the keys under it
            QTest.keyClick(APP.focusWidget(), Qt.Key.Key_Down)
            now = self.page._key["id"]
            self.assertGreater(strip.caps[now].y(), strip.caps[seen[-1]].y() - 1, now)
            seen.append(now)
        self.assertEqual(seen[-1], "0x0f01")
        QTest.keyClick(APP.focusWidget(), Qt.Key.Key_Up)
        self.assertTrue(strip.caps[self.page._key["id"]].overlay)  # back up onto the picture

    def test_a_mini_gets_the_compact_picture_with_the_same_keys(self):
        keys = [key(0x00C7, pos=1), key(0x0116), key(0x0118)]
        self.show(keys)
        self.assertEqual(self.page.strip._layout_name, "full")
        self.assertEqual(set(self.page.strip._slot_of), {"0x00c7", "0x0116", "0x0118"})
        self.store.update_state(dict(self.store.state, keyboard=keyboard(keys, name="MX Mechanical Mini")))
        settle()
        self.assertEqual(self.page.strip._layout_name, "compact")
        self.assertEqual(set(self.page.strip._slot_of), {"0x00c7", "0x0118"})  # no Insert on a Mini

    def test_a_key_given_the_focus_is_scrolled_into_view(self):
        self.page.resize(1100, 420)
        self.show(MX_MECHANICAL)
        last = list(self.page.strip.caps.values())[-1]
        bar = self.page.scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)  # more than fits: the page scrolls
        bar.setValue(0)
        last.setFocus()
        settle()
        viewport = self.page.scroll.viewport()
        place = QRect(last.mapTo(viewport, QPoint()), last.size())
        self.assertTrue(viewport.rect().contains(place))

    def test_a_focused_key_stays_in_view_when_the_keys_are_laid_out_again(self):
        """The keys laid out again (here: another program takes one): the key that had the focus keeps it, given back
        only once it is where it now goes, so that the page scrolls to it there (on a desktop the focus moves at once;
        here it is checked where the focus is given)."""
        self.page.resize(1100, 700)
        keys = [key(0x00C7, pos=1)] + [key(0x0F01 + i) for i in range(20)]
        self.show(keys)
        self.page.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(self.page))
        self.page.strip.caps["0x0f14"].setFocus()
        settle()
        given = []
        original = keyboard_page.KeyCap.setFocus

        def set_focus(cap, *args):
            given.append((cap.accessibleName(), cap.geometry()))
            return original(cap, *args)
        keys = [key(0x0F01, reason="foreign") if k["cid"] == 0x0F01 else k for k in keys]
        with mock.patch.object(keyboard_page.KeyCap, "setFocus", set_focus):
            self.show(keys)
        last = self.page.strip.caps["0x0f14"]
        self.assertEqual(given, [("Key 0x0f14", last.geometry())])  # where it ended up, not where a new button starts
        self.assertTrue(last.hasFocus())
        viewport = self.page.scroll.viewport()
        self.assertTrue(viewport.rect().contains(QRect(last.mapTo(viewport, QPoint()), last.size())))

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
        hosts = [{"paired": True, "name": "workstation"}, {"paired": True, "name": "laptop"}]
        settings = ["fn", "backlight", "platform", "disable", "locks"]
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
                            for index, name, width in ((index, name, width) for index in window._keyboard_pages
                                                       for name in ("MX Mechanical", "MX Mechanical Mini")
                                                       for width in (1180, 1200, 1280)):
                                    store.update_state(dict(store.state, keyboard=keyboard(MX_MECHANICAL, settings,
                                                                                           hosts, name)))
                                    with self.subTest(icons=with_icons, language=language, spacing=spacing,
                                                      page=index, name=name, width=width):
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
                                            if isinstance(widget, keyboard_page.KeyCap):  # the page scrolls down
                                                strip = widget.parentWidget()
                                                self.assertTrue(strip.rect().contains(widget.geometry()),
                                                                widget.accessibleName())
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
