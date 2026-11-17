"""Options+'s "Easy-Switch 2 devices": the dialog naming each channel by the computer the device says is paired there,
kept up to date while open, and the row of every action list that opens it. Offscreen; the store is a real Store on a
stand-in engine."""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

import tests.support  # noqa: F401,E402
from mxoptions import actions  # noqa: E402
from mxoptions.ui import action_picker, editors  # noqa: E402
from mxoptions.ui.editors import EasySwitchDialog  # noqa: E402
from tests.test_keyboard_tabs import gone, make_store, settle, window  # noqa: E402
from tests.test_keyboard_ui import keyboard  # noqa: E402

APP = QApplication.instance() or QApplication([])
NAMED = [{"paired": True, "name": "desk"}, {"paired": True, "name": "laptop"}, {"paired": False, "name": ""}]


def state(mouse_hosts=NAMED, kb_hosts=NAMED, kb_connected=True, mouse_current=0, kb_current=0):
    kb = keyboard([], hosts=kb_hosts, name="MX Mechanical")
    kb.update(connected=kb_connected, current_host=kb_current)
    return {"connected": True, "known": True, "name": "MX Master 4", "battery": None, "current_host": mouse_current,
            "hosts": list(mouse_hosts), "keyboard": kb}


def texts(combo):
    return [combo.itemText(i) for i in range(combo.count())]


class Dialog(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.store.update_state(state())

    def dialog(self, action=None, store=True):
        made = EasySwitchDialog(action, store=self.store if store else None)
        self.addCleanup(gone, made)
        return made

    def test_each_channel_is_named_by_its_computer_and_this_one_is_marked(self):
        made = self.dialog()
        for which in ("mouse", "keyboard"):
            self.assertEqual(texts(made.combos[which]), ["1 · desk (this computer)", "2 · laptop", "3 · Empty"])
        self.assertEqual(made.names["mouse"].text(), "Mouse · MX Master 4")
        self.assertEqual(made.names["keyboard"].text(), "Keyboard · MX Mechanical")
        self.assertEqual(made.action(), {"type": "host", "host": 1, "keyboard": 1})  # the first that is not this one

    def test_it_opens_on_the_action_given_and_saves_what_is_picked(self):
        made = self.dialog({"type": "host", "host": 2, "keyboard": 1})
        self.assertEqual((made.combos["mouse"].currentIndex(), made.combos["keyboard"].currentIndex()), (2, 1))
        made.combos["keyboard"].setCurrentIndex(0)
        self.assertEqual(made.action(), {"type": "host", "host": 2, "keyboard": 0})
        self.assertEqual(self.dialog({"type": "host", "host": 1, "both": True}).action(),
                         {"type": "host", "host": 1, "keyboard": 1})  # as PR #8 saved it

    def test_without_a_store_plain_numbers(self):
        made = self.dialog(store=False)
        self.assertEqual(texts(made.combos["mouse"]), ["1", "2", "3"])
        self.assertEqual(made.names["mouse"].text(), "Mouse")
        self.assertEqual(made.action(), {"type": "host", "host": 0, "keyboard": 0})

    def test_names_follow_the_devices_while_open_and_the_choice_stays(self):
        made = self.dialog({"type": "host", "host": 1, "keyboard": 2})
        renamed = [{"paired": True, "name": "desk"}, {"paired": True, "name": "studio"}, {"paired": True, "name": "nas"}]
        self.store.update_state(state(mouse_hosts=renamed, kb_hosts=renamed, mouse_current=1))
        self.assertEqual(texts(made.combos["mouse"]), ["1 · desk", "2 · studio (this computer)", "3 · nas"])
        self.store.update_state(state(kb_connected=False))  # the keyboard away: numbers only, as many as it had
        self.assertEqual(texts(made.combos["keyboard"]), ["1", "2", "3"])
        self.store.update_state(state(kb_hosts=NAMED[:2]))  # back with two channels: the one chosen is kept
        self.assertEqual(texts(made.combos["keyboard"]), ["1 · desk (this computer)", "2 · laptop", "3"])
        self.assertEqual(made.action(), {"type": "host", "host": 1, "keyboard": 2})

    def test_this_computer_only_where_the_channel_is_known(self):
        made = self.dialog()
        self.store.update_state(state(kb_current=None, mouse_current=7))
        self.assertNotIn("this computer", " ".join(texts(made.combos["mouse"]) + texts(made.combos["keyboard"])))

    def test_closed_it_no_longer_listens(self):
        made = self.dialog()
        made.reject()
        before = texts(made.combos["mouse"])
        self.store.update_state(state(mouse_hosts=[{"paired": True, "name": "elsewhere"}] * 3))
        self.assertEqual(texts(made.combos["mouse"]), before)

    def test_each_list_is_named_and_reached_by_tab(self):
        made = self.dialog()
        made.show()
        self.assertEqual(made.combos["mouse"].accessibleName(), "Mouse channel")
        self.assertEqual(made.combos["keyboard"].accessibleName(), "Keyboard channel")
        made.combos["mouse"].setFocus()
        settle()
        made.focusNextChild()
        self.assertIs(APP.focusWidget(), made.combos["keyboard"])
        self.assertTrue(made.save.isDefault())


class Picker(unittest.TestCase):
    def test_the_row_opens_the_dialog_and_shows_the_action(self):
        listing = action_picker.ActionList()
        self.addCleanup(gone, listing)
        picked = []
        listing.picked.connect(picked.append)
        with mock.patch.object(EasySwitchDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            listing.two_row.click()
        self.assertEqual(picked, [{"type": "host", "host": 0, "keyboard": 0}])
        listing.set_current({"type": "host", "host": 0, "keyboard": 2})
        self.assertTrue(listing.two_row.isChecked())
        self.assertEqual(listing.two_summary.text(), "Mouse to Easy-Switch 1, keyboard to 3")
        listing.set_current(actions.PRESET_BY_ID["host_2"].action)  # a plain Easy-Switch: its own row
        self.assertFalse(listing.two_row.isChecked())

    def test_the_row_comes_right_after_easy_switch_3_and_the_names_fit(self):
        from mxoptions.ui import theme
        old_style, old_sheet, old_palette = APP.style().objectName(), APP.styleSheet(), APP.palette()
        self.addCleanup(lambda: (APP.setStyle(old_style), APP.setStyleSheet(old_sheet), APP.setPalette(old_palette)))
        APP.setStyle("Fusion")
        theme.apply(APP)  # the app's own fonts and padding, as the window has them
        store = make_store()
        store.update_state(state())
        made = window(store)
        self.addCleanup(gone, made)
        made.resize(1180, 700)
        made._open(0)  # Buttons
        made.buttons_page.view.select("middle")
        settle()
        listing = made.buttons_page.picker.list
        body = listing._body
        order = [body.itemAt(i).widget() for i in range(body.count()) if body.itemAt(i).widget() is not None]
        at = order.index(listing._rows["host_3"])
        self.assertEqual(order[at + 1:at + 3], [listing.two_row, listing.two_summary])
        self.assertLess(order.index(listing.two_row), order.index(listing._rows["together"]))
        for row in (listing.two_row, listing._rows["together"]):  # whole: an elided row says itself in its tooltip
            self.assertEqual(row.toolTip(), "", row.text())

    def test_no_row_among_a_macro_s_steps(self):
        listing = action_picker.ActionList(exclude=editors.STEP_EXCLUDE)
        self.addCleanup(gone, listing)
        self.assertIsNone(listing.two_row)
        listing.set_current({"type": "host", "host": 0, "keyboard": 2})  # nothing to show it on, nothing breaks

    def test_every_action_list_of_the_window_names_the_computers(self):
        store = make_store()
        store.update_state(state())
        made = window(store)
        self.addCleanup(gone, made)
        seen = []

        def opened(dialog):
            seen.append(texts(dialog.combos["keyboard"]))
            return QDialog.DialogCode.Rejected
        lists = [listing for listing in made.findChildren(action_picker.ActionList) if listing.two_row is not None]
        self.assertGreaterEqual(len(lists), 4)  # buttons, their gestures, the keys, the ring's bubbles
        with mock.patch.object(EasySwitchDialog, "exec", opened):
            for listing in lists:
                listing.two_row.click()
        self.assertEqual(seen, [["1 · desk (this computer)", "2 · laptop", "3 · Empty"]] * len(lists))


if __name__ == "__main__":
    unittest.main()
