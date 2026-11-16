"""The home page, as Options+ starts: greeting, both devices with their battery and how they are connected, each
opening its own pages. Offscreen; a real Store on a stand-in engine, the window as MainWindow builds it."""

import glob
import json
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import tests.support  # noqa: F401,E402
from mxoptions import device, engine  # noqa: E402
from mxoptions.ui import home_page, icons, mouse_model, mouse_photo, mouse_render  # noqa: E402
from mxoptions.ui.home_page import HomePage, badge_text, greeting  # noqa: E402
from mxoptions.ui.mouse_view import MODEL_GAP, MouseView, chip_width  # noqa: E402
from tests.test_keyboard_tabs import gone, make_store, settle, window  # noqa: E402
from tests.test_keyboard_ui import keyboard  # noqa: E402

APP = QApplication.instance() or QApplication([])
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ops", "ship"))
import hwgate  # noqa: E402

DISCHARGING, CHARGING, FULL = device.BATTERY_DISCHARGING, device.BATTERY_CHARGING, device.BATTERY_FULL


def mouse(battery=(85, DISCHARGING), receiver=False, connected=True, kb=None):
    return {"connected": connected, "known": True, "name": "MX Master 4", "uinput": True, "rivals": [],
            "battery": battery, "current_host": 0, "hosts": [], "receiver": receiver if connected else None,
            "dpi": 1000, "wheel_mode": "", "caps": None, "keyboard": kb}


def kb(battery=(60, CHARGING), receiver=False, connected=True):
    found = keyboard([], name="MX Mechanical")
    found.update(battery=battery if connected else None, connected=connected, receiver=receiver if connected else None)
    return found


class Words(unittest.TestCase):
    def test_the_greeting_follows_the_hour(self):
        for hour, text in ((4, "Good evening"), (5, "Good morning"), (11, "Good morning"), (12, "Good afternoon"),
                           (17, "Good afternoon"), (18, "Good evening"), (23, "Good evening")):
            self.assertEqual(greeting(hour), text, hour)

    def test_the_badge_never_takes_a_missing_level_for_a_device_gone(self):
        self.assertEqual(badge_text(mouse()), "85%")
        self.assertEqual(badge_text(mouse(battery=None)), "Connected")
        self.assertEqual(badge_text(mouse(battery=(0, DISCHARGING))), "Connected")  # 0: no level
        self.assertEqual(badge_text(mouse(connected=False)), "Not connected")
        self.assertEqual(badge_text(None), "Not connected")
        self.assertEqual(badge_text(kb(battery=None)), "Connected")


class Page(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.page = HomePage(self.store)
        self.addCleanup(gone, self.page)

    def show(self, state):
        self.store.update_state(state)
        settle()

    def test_each_device_its_battery_charging_and_link(self):
        self.show(mouse(kb=kb()))
        card, other = self.page.mouse, self.page.keyboard
        self.assertEqual((card.percent.text(), card.percent.property("level")), ("85%", "ok"))
        self.assertTrue(card.bolt.isHidden())
        self.assertEqual(card.accessibleName(), "MX Master 4: 85%, Bluetooth")
        self.assertEqual((other.percent.text(), other.percent.property("level")), ("60%", "charging"))
        self.assertFalse(other.bolt.isHidden())
        self.assertEqual(other.accessibleName(), "MX Mechanical: Charging · 60%, Bluetooth")

    def test_charging_comes_and_goes(self):
        card = self.page.mouse
        for battery, level, bolt, spoken in (((60, DISCHARGING), "ok", False, "60%"),
                                             ((60, CHARGING), "charging", True, "Charging · 60%"),
                                             ((100, FULL), "charging", True, "Fully charged"),
                                             ((12, DISCHARGING), "low", False, "12%"),
                                             ((12, CHARGING), "charging", True, "Charging · 12%")):
            with self.subTest(battery=battery):
                self.show(mouse(battery=battery))
                self.assertEqual(card.percent.property("level"), level)
                self.assertEqual(card.bolt.isHidden(), not bolt)
                self.assertEqual(card.accessibleName(), f"MX Master 4: {spoken}, Bluetooth")

    def test_the_link_is_an_icon_or_said_in_words(self):
        card = self.page.mouse
        for has, receiver, word in ((True, False, "Bluetooth"), (True, True, "Receiver"),
                                    (False, False, "Bluetooth"), (False, True, "Receiver")):
            with self.subTest(icon=has, receiver=receiver), mock.patch.object(icons, "has", lambda _name, h=has: h):
                self.show(mouse(receiver=receiver))
                self.assertEqual(card.link_icon.isHidden(), not has)
                self.assertEqual(card.link_text.isHidden(), has)
                self.assertEqual(card.link_text.text(), word)
                self.assertTrue(card.accessibleName().endswith(", " + word))
        self.show(mouse(receiver=None))  # not known: neither
        self.assertTrue(card.link_icon.isHidden() and card.link_text.isHidden())

    def test_a_device_away_is_dimmed_and_says_so(self):
        self.show(mouse(connected=False, kb=kb(connected=False)))
        for card in (self.page.mouse, self.page.keyboard):
            self.assertEqual(card.percent.text(), "Not connected")
            self.assertEqual(card.percent.property("level"), "off")
            self.assertAlmostEqual(card.picture.graphicsEffect().opacity(), home_page.DIM)
            self.assertTrue(card.battery.isHidden())
        self.assertEqual(self.page.mouse.accessibleName(), "MX Master 4: Not connected")
        self.show(mouse(battery=None))
        self.assertEqual(self.page.mouse.accessibleName(), "MX Master 4: Connected, Bluetooth")
        self.assertAlmostEqual(self.page.mouse.picture.graphicsEffect().opacity(), 1.0)

    def test_no_keyboard_no_card(self):
        self.show(mouse())
        self.assertTrue(self.page.keyboard.isHidden())
        self.store.set_key("0x0f01", {"type": "keys", "keys": ["KEY_A"]})  # its keys have settings: shown, away
        self.assertFalse(self.page.keyboard.isHidden())
        self.assertEqual(self.page.keyboard.accessibleName(), "Keyboard: Not connected")

    def test_click_space_and_enter_open_its_pages(self):
        self.show(mouse(kb=kb()))
        opened = []
        self.page.open_mouse.connect(lambda: opened.append("mouse"))
        self.page.open_keyboard.connect(lambda: opened.append("keyboard"))
        self.page.mouse.click()
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            QTest.keyClick(self.page.keyboard, key)
        QTest.keyClick(self.page.keyboard, Qt.Key.Key_A)
        self.assertEqual(opened, ["mouse", "keyboard", "keyboard", "keyboard"])


class Window(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.store.update_state(mouse(kb=kb()))
        self.win = window(self.store)
        self.addCleanup(gone, self.win)
        settle()

    def test_it_starts_on_home_and_home_comes_first(self):
        win = self.win
        self.assertIs(win.stack.currentWidget(), win.home_page)
        home = win._nav.button(win._home)
        self.assertTrue(home.isChecked())
        self.assertEqual(home.text(), "Devices")
        self.assertEqual([win._nav.button(i).text() for i in range(7)],
                         ["Buttons", "Gestures", "Point && scroll", "Haptics", "Actions Ring", "Flow", "Device && app"])
        self.assertEqual(list(win._keyboard_pages), [7, 8, 9, 10])
        self.assertEqual(win._home, 11)
        widget, chain = win, []
        for _ in range(5000):  # once round the whole focus chain of the window
            widget = widget.nextInFocusChain()
            if widget is win:
                break
            if widget in win._nav.buttons():
                chain.append(widget)
        self.assertEqual(chain[:2], [home, win._nav.button(0)])  # Home before every other
        home.setFocus()
        for _ in range(40):  # Tab from Home reaches both cards of its page
            win.focusNextChild()
            if APP.focusWidget() is win.home_page.keyboard:
                break
        self.assertIs(APP.focusWidget(), win.home_page.keyboard)

    def test_a_device_opens_its_pages_and_back_home(self):
        win = self.win
        QTest.keyClick(win.home_page.mouse, Qt.Key.Key_Return)
        self.assertIs(win.stack.currentWidget(), win.buttons_page)
        self.assertTrue(win._nav.button(0).isChecked())
        win._nav.button(win._home).click()
        self.assertIs(win.stack.currentWidget(), win.home_page)
        win.home_page.keyboard.click()
        self.assertEqual(win.stack.currentIndex(), win._keyboard_pages[0])
        self.assertTrue(win._nav.button(win._keyboard_pages[0]).isChecked())
        self.store.update_state(mouse())  # the keyboard forgotten while its page shows: back home
        self.assertIs(win.stack.currentWidget(), win.home_page)
        self.assertTrue(win._nav.button(win._home).isChecked())

    def test_fits_the_smallest_window_in_both_languages(self):
        from mxoptions import i18n
        self.addCleanup(i18n.setup, "en")
        for language in ("en", "uk"):
            with self.subTest(language=language):
                i18n.setup(language)
                store = make_store()
                store.update_state(mouse(kb=kb()))
                win = window(store)
                self.addCleanup(gone, win)
                win.resize(1180, 700)
                settle()
                page = win.home_page
                for card in (page.mouse, page.keyboard):
                    rect = card.geometry()
                    self.assertTrue(page.rect().contains(rect), (language, rect, page.rect()))
                    label = card.percent
                    self.assertLessEqual(label.fontMetrics().horizontalAdvance(label.text()), label.width())


class Picture(unittest.TestCase):
    def test_with_callouts_where_it_always_was_and_alone_from_the_edge(self):
        titled = MouseView({"middle": "Middle"})
        alone = MouseView({})
        self.addCleanup(gone, titled)
        self.addCleanup(gone, alone)
        for width, height in ((700, 560), (900, 640)):
            for view in (titled, alone):
                view.resize(width, height)
            self.assertEqual((titled.width(), titled.height()), (width, height))
            left = chip_width() + MODEL_GAP
            area = QRectF(left, 0, width - left, height)
            self.assertEqual(titled._stage(), QRectF(chip_width() + 10, 0, width - chip_width() - 10, height))
            self.assertEqual(titled._photo_rect(), mouse_photo.fit(area.adjusted(
                area.width() * 0.07, area.height() * 0.045, -area.width() * 0.07, -area.height() * 0.045)))
            camera = titled._camera(mouse_model.VIEWS[None])
            self.assertEqual(camera.origin, QPointF(left + (width - left - 8) / 2, camera.origin.y()))
            self.assertEqual(alone._stage().left(), 0)
            self.assertEqual(alone._left(), 0)
        self.assertEqual((alone.minimumWidth(), alone.minimumHeight()), (200, 200))

    @unittest.skipUnless(mouse_render.available(), "the studio model's bounds need its renderer")
    def test_the_whole_mouse_is_in_view_on_the_home_page(self):
        for size in ((1180, 700), (1280, 780), (1600, 1000)):
            with self.subTest(size=size):
                store = make_store()
                store.update_state(mouse(kb=kb()))
                win = window(store)
                self.addCleanup(gone, win)
                win.resize(*size)
                settle()
                view = win.home_page.mouse_view
                left, top, right, bottom = mouse_render._bounds(
                    view._camera(mouse_model.VIEWS[None]), mouse_render._fine_geometry(), 0)
                self.assertTrue(0 <= left and right <= view.width() and 0 <= top and bottom <= view.height(),
                                (size, (left, top, right, bottom), view.size()))


class State(unittest.TestCase):
    def test_how_each_device_is_connected_is_published(self):
        from tests.test_together_actions import Together
        case = Together("test_each_goes_to_its_own_channel")
        case.setUp()
        self.addCleanup(case.doCleanups)
        rig = case.make()  # the mouse on a receiver, the keyboard on Bluetooth
        seen = []
        rig.engine.listener.on_state = seen.append
        engine.Engine._publish(rig.engine)
        self.assertIs(seen[-1]["receiver"], True)
        self.assertIs(seen[-1]["keyboard"]["receiver"], False)
        rig.engine._drop_transport(rig.transport(kb_path()))
        self.assertIsNone(rig.kb.state()["receiver"])


def kb_path():
    from tests.test_keyboard import KB_PATH
    return KB_PATH


class Gate(unittest.TestCase):
    def test_every_ui_file_is_classified_for_the_hardware_gate(self):
        with open(os.path.join(ROOT, "ops", "ship", "hardware-gates.json")) as f:
            manifest = json.load(f)
        for path in sorted(glob.glob(os.path.join(ROOT, "mxoptions", "ui", "*.py"))):
            name = os.path.relpath(path, ROOT)
            self.assertTrue(hwgate.matches(name, manifest["paths"]) or hwgate.matches(name, manifest["ungated"]), name)


if __name__ == "__main__":
    unittest.main()
