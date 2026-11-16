"""The Actions Ring page as Options+ has it: Layout and Settings tabs, the ring blurred under "Customize ring" until it is
being changed, then sharp with its editor. Offscreen; a real Store on a stand-in engine."""

import copy
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QMessageBox, QPushButton, QWidget  # noqa: E402

import tests.support  # noqa: F401,E402
from mxoptions import config, i18n  # noqa: E402
from mxoptions.ui import ring, ring_page, theme  # noqa: E402
from tests.test_keyboard_tabs import gone, make_store, settle, window  # noqa: E402

APP = QApplication.instance() or QApplication([])
KATE = "org.kde.kate"


def bubble(label):
    return {"label": label, "icon": "", "color": "", "action": {"type": "keys", "keys": ["KEY_A"]}}


def scrolled(widget):
    """Whether ``widget`` is content of a scroll area: it may run past the page, the area shows a part."""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea) and parent.viewport().isAncestorOf(widget):
            return True
        parent = parent.parentWidget()
    return False


def point_of(preview, index):
    """A point of the preview inside cell ``index`` (the ghost after the bubbles)."""
    rect = preview._rect()
    for y in range(0, preview.height(), 3):
        for x in range(0, preview.width(), 3):
            if ring.cell_at(QPointF(x, y), rect, preview._count(), preview._style) == index:
                return QPoint(x, y)
    raise AssertionError(f"no point of cell {index}")


class Page(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.store.set("ring", "bubbles", [bubble("a"), bubble("b"),
                                           {"label": "f", "icon": "", "color": "", "children": [bubble("c")]}])
        self.page = ring_page.RingPage(self.store)
        self.addCleanup(gone, self.page)
        self.page.resize(1100, 720)
        self.page.show()
        settle()

    def overview(self):
        page = self.page
        self.assertFalse(page.customizing())
        self.assertTrue(page.editor.isHidden() and page.done.isHidden())
        self.assertFalse(page.customize.isHidden())
        self.assertTrue(page._blur.isEnabled())

    def editing(self):
        page = self.page
        self.assertTrue(page.customizing())
        self.assertFalse(page.editor.isHidden() or page.done.isHidden())
        self.assertTrue(page.customize.isHidden())
        self.assertFalse(page._blur.isEnabled())

    def test_it_opens_on_layout_with_the_ring_blurred_under_customize(self):
        self.assertTrue(self.page.layout_tab.isChecked())
        self.assertEqual(self.page.sections.currentIndex(), 0)
        self.overview()

    def test_customize_then_done_or_escape_and_the_focus_with_it(self):
        page = self.page
        page.customize.click()
        settle()
        self.editing()
        self.assertIs(APP.focusWidget(), page.editor.name)
        page.done.click()
        settle()
        self.overview()
        self.assertIs(APP.focusWidget(), page.customize)
        for inside in (page.editor.name, page.editor.icon, page.editor.actions.search):  # Esc from what is in the editor
            with self.subTest(inside=inside.objectName() or type(inside).__name__):
                page.set_customizing(True)
                inside.setFocus()
                QTest.keyClick(inside, Qt.Key.Key_Escape)
                self.overview()
                self.assertIs(APP.focusWidget(), page.customize)

    def test_escape_closes_a_popup_or_menu_first(self):
        page = self.page
        page.set_customizing(True)
        page.editor.icon.click()
        picker = page.editor._picker if hasattr(page.editor, "_picker") else next(
            w for w in APP.topLevelWidgets() if isinstance(w, ring_page.IconPicker) and w.isVisible())
        self.assertTrue(picker.isVisible())
        QTest.keyClick(picker, Qt.Key.Key_Escape)
        self.assertFalse(picker.isVisible())
        self.editing()
        page.presets_menu.popup(page.presets.mapToGlobal(QPoint()))
        settle()
        QTest.keyClick(page.presets_menu, Qt.Key.Key_Escape)
        self.assertFalse(page.presets_menu.isVisible())
        self.editing()

    def test_a_touch_of_the_blurred_ring_only_starts_editing(self):
        page, preview = self.page, self.page.preview
        before = copy.deepcopy(self.store.cfg)
        QTest.mouseClick(preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point_of(preview, 1))
        self.editing()
        self.assertEqual(page._index, 1)
        self.assertEqual(page.editor.name.text(), "b")
        page.set_customizing(False)
        QTest.mouseClick(preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point_of(preview, 3))  # ghost
        self.editing()
        self.assertEqual(len(self.store.cfg["ring"]["bubbles"]), 3)  # nothing added
        page.set_customizing(False)
        QTest.mouseDClick(preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point_of(preview, 2))
        self.editing()
        self.assertEqual((page._path, page._index), ([], 2))  # the folder chosen, not opened
        self.assertEqual(self.store.cfg, before)
        # A real double click on the blurred ring: Qt sends press (it starts editing), release, then the double click
        # with no press of its own: the folder is only chosen
        page.set_customizing(False)
        preview.bubble_clicked.emit(2)
        preview.bubble_opened.emit(2)
        self.assertEqual((page._path, page._index), ([], 2))
        preview.bubble_clicked.emit(2)  # a double click while editing: opens it
        preview.bubble_opened.emit(2)
        self.assertEqual(page._path, [2])
        page._go_up()
        page.set_customizing(False)
        preview.bubble_clicked.emit(2)  # the press that started editing, then a later double click: opens it
        preview.bubble_clicked.emit(2)
        preview.bubble_opened.emit(2)
        self.assertEqual(page._path, [2])
        page._go_up()
        for end in ("done", "escape", "settings", "hide"):  # the entry press forgotten once editing ends
            with self.subTest(end=end):
                page.set_customizing(False)
                preview.bubble_clicked.emit(2)
                if end == "done":
                    page.done.click()
                elif end == "escape":
                    QTest.keyClick(page.editor.name, Qt.Key.Key_Escape)
                elif end == "settings":
                    page.settings_tab.click()
                else:
                    page.hide()
                    page.show()
                page.customize.click()
                preview.bubble_opened.emit(2)
                self.assertEqual(page._path, [2])
                page._go_up()
        page._go_up()
        QTest.mouseClick(preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point_of(preview, 3))
        self.assertEqual(len(self.store.cfg["ring"]["bubbles"]), 4)  # while editing: as always

    def test_another_bubble_clicked_shows_its_name_though_the_name_field_has_the_focus(self):
        page, preview = self.page, self.page.preview
        page.customize.click()
        self.assertIs(APP.focusWidget(), page.editor.name)
        self.assertEqual(page.editor.name.text(), "a")
        QTest.mouseClick(preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point_of(preview, 1))
        self.assertEqual(page.editor.name.text(), "b")
        QTest.keyClicks(page.editor.name, "X")
        self.assertEqual([b["label"] for b in self.store.cfg["ring"]["bubbles"]][:2], ["a", "bX"])
        self.assertEqual(page.editor.name.text(), "bX")  # what is typed stays, while the same bubble is shown

    def test_settings_tab_ends_editing_and_holds_the_ring_s_settings(self):
        page = self.page
        page.set_customizing(True)
        page.settings_tab.setFocus()
        page.settings_tab.click()
        self.overview()
        self.assertIs(APP.focusWidget(), page.settings_tab)
        looks = page.sections.widget(1)
        for widget in (page.style, page.size, page.opens_with):
            self.assertTrue(looks.isAncestorOf(widget))
            self.assertTrue(widget.isVisible())
        page.style.selected.emit("pie")
        page.size.changed.emit(1.2)
        page.button.selected.emit("gesture")
        self.assertEqual((self.store.cfg["ring"]["style"], self.store.cfg["ring"]["size"]), ("pie", 1.2))
        self.assertEqual(self.store.cfg["buttons"]["gesture"], {"type": "ring"})
        page.customize.click()  # back on Layout, editing
        self.assertTrue(page.layout_tab.isChecked())
        self.editing()

    def test_folders_kept_through_done_and_settings_reset_when_the_page_goes(self):
        page = self.page
        page.set_customizing(True)
        page.preview.bubble_opened.emit(2)
        self.assertEqual(page._path, [2])
        page.done.click()
        page.settings_tab.click()
        page.layout_tab.click()
        self.assertEqual(page._path, [2])
        page.set_customizing(True)
        page.hide()
        page.show()
        settle()
        self.overview()
        self.assertEqual(page._path, [])

    def test_an_app_chosen_ends_editing_even_while_hidden(self):
        page = self.page
        self.assertTrue(self.store.add_mouse_profile(KATE))
        page.set_customizing(True)
        self.store.set_mouse_app(KATE)
        self.overview()
        page.set_customizing(True)
        page.hide()
        self.store.set_mouse_app(None)
        page.show()
        settle()
        self.overview()

    def test_presets_open_the_new_ring_for_editing_and_nothing_else_does(self):
        page = self.page
        for answer, stale, edits in ((QMessageBox.StandardButton.No, False, False),
                                     (QMessageBox.StandardButton.Yes, True, False),
                                     (QMessageBox.StandardButton.Yes, False, True)):
            with self.subTest(answer=answer, stale=stale):
                page.set_customizing(False, focus=False)

                def asked(*_args, a=answer, s=stale):
                    if s:
                        self.store.set("ring", "bubbles", [bubble("meanwhile")])
                    return a
                with mock.patch.object(ring_page.QMessageBox, "question", asked), \
                        mock.patch.object(ring_page.QMessageBox, "information"):
                    page._apply_preset("x", lambda: [bubble("preset")])
                self.assertEqual(page.customizing(), edits)
        self.assertEqual(self.store.cfg["ring"]["bubbles"], [bubble("preset")])

    def test_tab_order_and_names(self):
        page = self.page
        self.assertIs(page.layout_tab.nextInFocusChain(), page.settings_tab)
        self.assertEqual((page.layout_tab.accessibleName(), page.settings_tab.accessibleName()), ("Layout", "Settings"))
        self.assertEqual(page.customize.accessibleName(), "Customize ring")
        page.customize.setFocus()
        QTest.keyClick(page.customize, Qt.Key.Key_Space)
        self.editing()

    def test_new_controls_show_focus(self):
        sheet = theme.stylesheet()
        for rule in ("QPushButton#ringTab:focus", "QPushButton#pillButton:focus"):
            self.assertIn(rule, sheet)


class Fit(unittest.TestCase):
    def test_every_tab_and_mode_fits_the_smallest_window(self):
        old_style, old_sheet, old_font = APP.style().objectName(), APP.styleSheet(), APP.font()
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
                for app, own in ((KATE, True), ("org.kde.konsole", False)):
                    self.assertTrue(store.add_mouse_profile(app))
                    if own:
                        self.assertTrue(store.set_profile_ring(app, copy.deepcopy(store.cfg["ring"]["bubbles"])))
                win = window(store)
                try:
                    win.resize(1180, 700)
                    win._open(4)  # Actions Ring
                    page = win.stack.currentWidget()
                    self.assertIsInstance(page, ring_page.RingPage)
                    settle()
                    width = win.width()
                    for app in (None, KATE, "org.kde.konsole"):
                        for mode in ("overview", "customize", "settings"):
                            with self.subTest(spacing=spacing, language=language, app=app, mode=mode):
                                store.set_mouse_app(app)
                                if mode == "customize":
                                    page.customize.click()
                                elif mode == "settings":
                                    page.settings_tab.click()
                                else:
                                    page.layout_tab.click()
                                    page.set_customizing(False)
                                settle()
                                self.assertEqual(win.width(), width)
                                area = page.rect()
                                for widget in page.findChildren(QWidget):
                                    if widget.isVisible() and not widget.isWindow() and not scrolled(widget):
                                        rect = QRect(widget.mapTo(page, QPoint()), widget.size())
                                        self.assertTrue(area.contains(rect), (widget.objectName(), type(widget).__name__, rect))
                                chooser = page.apps
                                controls = [c for c in (chooser.apps, chooser.add_app, chooser.remove_app, page.presets,
                                                        page.own, page.layout_tab, page.settings_tab, page.customize,
                                                        page.done) if c.isVisible()]
                                for control in controls:
                                    if isinstance(control, QPushButton):
                                        self.assertGreaterEqual(control.width(), control.sizeHint().width(), control.text())
                                for index, control in enumerate(controls):
                                    for other in controls[index + 1:]:
                                        a = QRect(control.mapTo(page, QPoint()), control.size())
                                        b = QRect(other.mapTo(page, QPoint()), other.size())
                                        self.assertFalse(a.intersects(b), (control.text() if hasattr(control, "text") else "",
                                                                           other.text() if hasattr(other, "text") else ""))
                finally:
                    gone(win)
        finally:
            APP.setFont(old_font)
            APP.setStyle(old_style)
            APP.setStyleSheet(old_sheet)


if __name__ == "__main__":
    unittest.main()
