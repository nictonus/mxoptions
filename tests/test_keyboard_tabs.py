"""The keyboard's tabs after Keys, as Options+ has them (Backlighting, Easy-Switch, Settings), in the window and on
their own: where each control is, Flow on the Easy-Switch tab, when the firmware is asked about, and closing the
window while it is."""

import copy
import os
import sys
import threading
import traceback
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402
import shiboken6  # noqa: E402

import tests.support  # noqa: F401,E402
from mxoptions import config, i18n, i18n_uk  # noqa: E402
from mxoptions.ui import keyboard_page, pages, theme  # noqa: E402
from mxoptions.ui.store import Store  # noqa: E402
from tests.test_keyboard_ui import MX_MECHANICAL, keyboard  # noqa: E402

APP = QApplication.instance() or QApplication([])


class Engine:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def make_store():
    store = Store(copy.deepcopy(config.DEFAULTS), Engine())
    store.flush = lambda: None
    store._save_timer.timeout.disconnect()
    return store


def settle():
    for _ in range(8):
        APP.processEvents()


def gone(widget):
    widget.hide()
    widget.deleteLater()
    APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def window(store):
    from mxoptions.ui.main_window import MainWindow
    with mock.patch.object(pages.MouseView, "_prerender"):
        made = MainWindow(store, hide_on_close=False)
    made.show()
    return made


HOSTS = [{"paired": True, "name": "workstation"}, {"paired": True, "name": ""}, {"paired": False, "name": ""}]
BACKLIGHT = {"value": {"on": True, "power_save": True, "mode": "manual", "level": 4, "hands_out": 60, "hands_in": 30,
                       "powered": 300},
             "fields": ["on", "mode", "level"], "can": ["on", "mode", "level"], "levels": 8, "writable": True,
             "busy": False, "outcome": None}


def with_backlight(found, on=True):
    control = copy.deepcopy(BACKLIGHT)
    control["value"]["on"] = on
    return dict(found, controls={"backlight": control})


class Sections(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        patcher = mock.patch.object(keyboard_page.firmware, "available", lambda: False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.page = keyboard_page.KeyboardPage(self.store)
        self.addCleanup(gone, self.page)
        self.page.resize(900, 700)
        self.page.show()

    def show(self, found):
        self.store.update_state(dict(self.store.state, keyboard=found))
        settle()

    def test_three_tabs_each_with_its_cards(self):
        page = self.page
        self.assertEqual(list(page.sections), ["backlight", "easy_switch", "settings"])
        for card, section in ((page.backlight_card, "backlight"), (page.hosts_card, "easy_switch"),
                              (page.flow_card, "easy_switch"), (page.settings_card, "settings"),
                              (page.firmware_row, "settings")):
            self.assertTrue(page.sections[section].isAncestorOf(card), section)
        for title in keyboard_page.SECTION_TITLES.values():
            self.assertIn(title, i18n_uk.STRINGS)
        for text in ("This keyboard has no backlight settings.", "Flow settings",
                     "Press the Easy-Switch keys to switch between the paired computers."):
            self.assertIn(text, i18n_uk.STRINGS)

    def test_backlighting_says_when_there_is_none_and_lights_the_picture(self):
        found = keyboard(MX_MECHANICAL, ["backlight"], HOSTS, "MX Mechanical")
        self.show(with_backlight(found, on=True))
        self.assertTrue(self.page.no_backlight.isHidden())
        self.assertIs(self.page.backlight_picture._lit, True)
        self.show(with_backlight(found, on=False))
        self.assertIs(self.page.backlight_picture._lit, False)
        self.show(keyboard(MX_MECHANICAL, [], HOSTS, "MX Mechanical"))
        self.assertFalse(self.page.no_backlight.isHidden())
        self.assertIsNone(self.page.backlight_picture._lit)

    def test_channels_as_options_plus_lists_them_over_the_easy_switch_keys(self):
        self.show(keyboard(MX_MECHANICAL, [], HOSTS, "MX Mechanical"))
        tiles = [tile for tile in self.page.findChildren(keyboard_page.ChannelTile) if tile.isVisible()]
        self.assertEqual([tile.accessibleName() for tile in tiles],
                         ["Channel 1: workstation · This computer", "Channel 2: Paired", "Channel 3: Empty"])
        numbers = [label.objectName() for tile in tiles for label in tile.findChildren(QLabel)
                   if label.objectName().startswith("channelNumber")]
        self.assertEqual(numbers, ["channelNumberCurrent", "channelNumber", "channelNumber"])
        picture = self.page.easy_picture
        self.assertEqual(set(picture.slots()), {"0x00d1", "0x00d2", "0x00d3"})  # its Easy-Switch keys, outlined
        self.assertEqual(picture._current, "0x00d1")  # this computer's
        self.store.update_state(dict(self.store.state, keyboard=dict(self.store.state["keyboard"], current_host=1)))
        self.assertEqual(picture._current, "0x00d2")

    def test_flow_switches_set_flow_and_follow_it(self):
        page = self.page
        self.show(keyboard(MX_MECHANICAL, [], HOSTS, "MX Mechanical"))
        self.assertEqual(page.mouse_follows.accessibleName(), "Mouse follows the keyboard")
        self.assertEqual(page.keyboard_follows.accessibleName(), "Keyboard follows the mouse")
        page.mouse_follows.click()
        self.assertIs(self.store.cfg["flow"]["mouse_follows_keyboard"], True)
        page.keyboard_follows.click()
        self.assertIs(self.store.cfg["flow"]["keyboard_follows_mouse"], True)
        self.store.set("flow", "mouse_follows_keyboard", False)  # changed elsewhere: followed
        self.assertFalse(page.mouse_follows.isChecked())
        asked = []
        page.open_flow.connect(lambda: asked.append(True))
        next(button for button in page.flow_card.findChildren(keyboard_page.QPushButton)
             if button.text() == "Flow settings").click()
        self.assertEqual(asked, [True])


class InTheWindow(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.asked = []
        patches = [mock.patch.object(keyboard_page.firmware, "available", lambda: True),
                   mock.patch.object(pages.FirmwareQuery, "start",
                                     lambda query, name, check: self.asked.append(name) or setattr(query, "running", True)),
                   mock.patch.object(pages.FirmwareQuery, "running", False, create=True)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.window = window(self.store)
        self.addCleanup(gone, self.window)
        self.window.resize(1180, 760)
        settle()

    def show(self, name="MX Mechanical"):
        self.store.update_state(dict(self.store.state, keyboard=keyboard(MX_MECHANICAL, ["fn"], HOSTS, name)))
        settle()

    def test_the_keyboard_tabs_in_the_sidebar_and_flow_at_its_foot(self):
        self.show()
        win = self.window
        indexes = list(win._keyboard_pages)
        self.assertEqual([win._nav.button(i).text() for i in indexes], ["Keys", "Backlighting", "Easy-Switch", "Settings"])
        expected = [keyboard_page.KeysPage] + [type(section) for section in win.keyboard_page.sections.values()]
        for index, kind in zip(indexes, expected):
            win._nav.button(index).click()
            self.assertIsInstance(win.stack.currentWidget(), kind)
        flow, device = win._nav.button(5), win._nav.button(6)
        self.assertEqual((flow.text(), device.text()), ("Flow", "Device && app"))
        lowest_keyboard = max(win._nav.button(i).mapTo(win, QPoint()).y() for i in indexes)
        self.assertGreater(flow.mapTo(win, QPoint()).y(), lowest_keyboard)
        self.assertGreater(device.mapTo(win, QPoint()).y(), flow.mapTo(win, QPoint()).y())
        chain, widget = [], win._nav.button(0)
        for _ in range(40):  # the Tab order of the sidebar follows it top to bottom
            widget = widget.nextInFocusChain()
            if widget in win._nav.buttons() and widget not in chain:
                chain.append(widget)
        self.assertEqual(chain[-2:], [flow, device])

    def test_flow_settings_opens_the_flow_page(self):
        self.show()
        self.window.keyboard_page.open_flow.emit()
        self.assertIs(self.window.stack.currentIndex(), 5)
        self.assertTrue(self.window._nav.button(5).isChecked())

    def test_the_flow_switches_from_the_keyboard_in_the_window(self):
        self.show()
        win = self.window
        win._nav.button(win._keyboard_pages[2]).click()  # Easy-Switch
        win.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(win))
        switch = win.keyboard_page.mouse_follows
        switch.setFocus()
        self.assertTrue(switch.hasFocus())
        QTest.keyClick(switch, Qt.Key.Key_Space)
        self.assertIs(self.store.cfg["flow"]["mouse_follows_keyboard"], True)
        QTest.keyClick(switch, Qt.Key.Key_Tab)
        self.assertIs(APP.focusWidget(), win.keyboard_page.keyboard_follows)

    def test_the_firmware_is_asked_about_while_settings_is_open(self):
        win = self.window
        settings = win._keyboard_pages[3]
        win._nav.button(settings).click()
        settle()
        self.assertEqual(self.asked, [])  # no keyboard yet
        self.show()
        self.assertEqual(self.asked, ["MX Mechanical"])  # connected while Settings is open
        win.keyboard_page.firmware.running = True  # still asking: another keyboard comes
        self.show("MX Keys S")
        win.keyboard_page.firmware.running = False
        win.keyboard_page._firmware_answered([{"name": "MX Mechanical", "version": "1"}], False)  # about the one before
        self.assertEqual(self.asked, ["MX Mechanical", "MX Keys S"])
        self.assertEqual(win.keyboard_page._firmware_entries, [])
        win._nav.button(0).click()
        win._nav.button(settings).click()  # left and opened again: the same keyboard, not asked again
        settle()
        self.assertEqual(self.asked, ["MX Mechanical", "MX Keys S"])

    def test_the_firmware_is_not_asked_about_while_settings_is_closed(self):
        self.window._nav.button(self.window._keyboard_pages[1]).click()
        self.show()
        self.assertEqual(self.asked, [])


class ClosingWhileAsking(unittest.TestCase):
    def test_a_window_closed_while_fwupd_is_asked_leaves_nothing_behind(self):
        store = make_store()
        release, errors, slot_errors = threading.Event(), [], []
        old_hook = threading.excepthook
        threading.excepthook = lambda args: errors.append(args.exc_value)
        self.addCleanup(setattr, threading, "excepthook", old_hook)
        old_sys_hook = sys.excepthook  # where an exception in a Qt slot goes
        sys.excepthook = lambda kind, value, trace: slot_errors.append((value, traceback.extract_tb(trace)))
        self.addCleanup(setattr, sys, "excepthook", old_sys_hook)
        started = []
        real_thread = threading.Thread

        def thread(*args, **kwargs):
            made = real_thread(*args, **kwargs)
            if kwargs.get("name") == "firmware":
                started.append(made)
            return made

        def installed(name):
            release.wait(5)
            return [{"name": name, "version": "1"}]
        with (mock.patch.object(keyboard_page.firmware, "available", lambda: True),
              mock.patch.object(pages.firmware, "installed", installed),
              mock.patch.object(pages.threading, "Thread", thread)):
            win = window(store)
            win._nav.button(win._keyboard_pages[3]).click()
            store.update_state(dict(store.state, keyboard=keyboard(MX_MECHANICAL, ["fn"], HOSTS, "MX Mechanical")))
            settle()
            other = pages.FirmwareQuery()  # one of its own, as another page has
            other.start("MX Master 4", False)
            self.assertGreaterEqual(len(started), 2)
            controller = win.keyboard_page
            gone(win)
            self.assertFalse(shiboken6.isValid(controller))  # the keyboard's settings went with the window
            other.deleteLater()
            APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            release.set()
            for made in started:
                made.join(5)
                self.assertFalse(made.is_alive())
            settle()
            store.update_state(dict(store.state, keyboard=keyboard(MX_MECHANICAL, ["fn"], HOSTS, "MX Keys S")))
            store.changed.emit()
        self.assertEqual(errors, [])
        # nothing of the keyboard's pages ran after they went (the Flow page's own leftover is another matter)
        ours = [value for value, trace in slot_errors
                if any(frame.filename.endswith(("keyboard_page.py", "keyboard_drawing.py")) for frame in trace)]
        self.assertEqual(ours, [])


class EasySwitchFit(unittest.TestCase):
    """Three channels with the longest names a keyboard keeps, at the smallest window, both languages, letters a
    tenth wider too: the card stays inside its tab and every name is shortened rather than cut."""

    def test_long_names_fit(self):
        longest = [{"paired": True, "name": "W" * 32}, {"paired": True, "name": "ш" * 32}, {"paired": False, "name": ""}]
        old_style, old_sheet, old_palette, old_font = APP.style().objectName(), APP.styleSheet(), APP.palette(), APP.font()
        self.addCleanup(i18n.setup, "en")
        try:
            APP.setStyle("Fusion")
            for spacing, language in ((100, "en"), (100, "uk"), (110, "en"), (110, "uk")):
                font = QFont(old_font)
                font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, spacing)
                APP.setFont(font)
                theme.apply(APP)
                i18n.setup(language)
                store = make_store()
                with mock.patch.object(pages.FirmwareQuery, "start"):
                    win = window(store)
                try:
                    store.update_state(dict(store.state, keyboard=keyboard(MX_MECHANICAL, [], longest, "MX Mechanical")))
                    win._nav.button(win._keyboard_pages[2]).click()
                    for width in (1180, 1200, 1280):
                        with self.subTest(language=language, spacing=spacing, width=width):
                            win.resize(width, 700)
                            settle()
                            self.assertEqual(win.width(), width)
                            section = win.stack.currentWidget()
                            card = win.keyboard_page.hosts_card
                            place = QRect(card.mapTo(section, QPoint()), card.size())
                            self.assertTrue(0 <= place.left() and place.right() <= section.width(), place)
                            self.assertTrue(QRect(QPoint(), win.size()).contains(QRect(card.mapTo(win, QPoint()),
                                                                                       card.size())))
                            for tile in card.findChildren(keyboard_page.ChannelTile):
                                if not tile.isVisible():
                                    continue
                                for label in tile.findChildren(QLabel):
                                    shown = label.text()
                                    self.assertLessEqual(label.fontMetrics().horizontalAdvance(shown), label.width() + 1,
                                                         shown)
                                self.assertIn(tile.name.full_text(), tile.accessibleName())
                finally:
                    store._save_timer.stop()
                    gone(win)
        finally:
            APP.setFont(old_font)
            APP.setStyle(old_style)
            APP.setStyleSheet(old_sheet)
            APP.setPalette(old_palette)


if __name__ == "__main__":
    unittest.main()
