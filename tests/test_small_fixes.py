"""Small fixes after the keyboard work: a long channel name in full on hover, the Flow page dropping its link to the
store with itself, the ring page coming back as it opens and taking Enter, the chosen option of a row of options
never cut off. Offscreen; a real Store on a stand-in engine."""

import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont, QFontMetrics  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import tests.support  # noqa: F401,E402
from mxoptions.ui import flow_page, keyboard_page, ring_page, theme  # noqa: E402
from mxoptions.ui.widgets import Segmented  # noqa: E402
from tests.test_keyboard_tabs import gone, make_store, settle  # noqa: E402

APP = QApplication.instance() or QApplication([])
LONG = "the very long name of the computer on this channel"


class Fixes(unittest.TestCase):
    def test_a_channel_name_cut_short_says_itself_whole_on_hover(self):
        tile = keyboard_page.ChannelTile(1, {"paired": True, "name": LONG}, False)
        self.addCleanup(gone, tile)
        tile.resize(160, 40)
        tile.show()
        settle()
        self.assertNotEqual(tile.name.text(), LONG)  # shortened
        self.assertEqual(tile.name.toolTip(), LONG)

    def test_the_flow_page_gone_the_store_reaches_it_no_more(self):
        store = make_store()
        page = flow_page.FlowPage(store)
        errors = []
        with mock.patch.object(sys, "excepthook", lambda *args: errors.append(args)):
            gone(page)
            store.update_state(dict(store.state))
            settle()
        self.assertEqual(errors, [])

    def test_the_ring_page_comes_back_on_layout_and_takes_enter(self):
        page = ring_page.RingPage(make_store())
        self.addCleanup(gone, page)
        page.resize(1100, 720)
        page.show()
        page.settings_tab.click()
        page.hide()
        page.show()
        settle()
        self.assertTrue(page.layout_tab.isChecked())
        self.assertEqual(page.sections.currentIndex(), 0)
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            with self.subTest(key=key):
                page.set_customizing(False)
                page.customize.setFocus()
                QTest.keyClick(page.customize, key)
                self.assertTrue(page.customizing())

    def test_the_chosen_option_is_never_cut_off(self):
        old_style, old_sheet, old_palette = APP.style().objectName(), APP.styleSheet(), APP.palette()
        self.addCleanup(lambda: (APP.setStyle(old_style), APP.setStyleSheet(old_sheet), APP.setPalette(old_palette)))
        APP.setStyle("Fusion")
        theme.apply(APP)
        labels = [("a", "Бульбашки"), ("b", "Сенсорна панель"), ("c", "Haptic panel")]
        row = Segmented(labels)
        self.addCleanup(gone, row)
        row.show()
        for value, label in labels:
            with self.subTest(label=label):
                row.set_value(value)
                settle()
                button = row._buttons[value]
                heavy = QFont(button.font())
                heavy.setWeight(QFont.Weight.Bold)
                self.assertGreaterEqual(button.width() - 28, QFontMetrics(heavy).horizontalAdvance(label))  # 14 px a side


if __name__ == "__main__":
    unittest.main()
