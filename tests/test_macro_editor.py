"""The macro editor's action and vibration steps (Qt offscreen)."""

import copy
import os
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests.support  # noqa: F401,E402  (puts the project on the path)
from PySide6.QtWidgets import QApplication, QDialog, QWidget  # noqa: E402

from mxoptions import actions, config, device  # noqa: E402
from mxoptions.ui import editors  # noqa: E402

APP = QApplication.instance() or QApplication([])

STEPS = [
    {"do": "keys", "keys": ["KEY_LEFTCTRL", "KEY_C"]}, {"do": "text", "text": "hi"}, {"do": "delay", "ms": 250},
    {"do": "click", "button": "BTN_RIGHT"}, {"do": "command", "command": "true"},
    {"do": "action", "action": {"type": "desktop", "do": "overview"}},
    {"do": "action", "action": {"type": "script", "name": "s", "script": "true"}},
    {"do": "haptic", "waveform": 0x07},
]


def window(waveforms):
    """A window with the store the editor looks at: the mouse can play ``waveforms`` (None: no mouse known)."""
    made = QWidget()
    made.store = types.SimpleNamespace(state={"caps": None if waveforms is None else {"waveforms": waveforms}})
    return made


class Editor(unittest.TestCase):
    def dialog(self, action, waveforms=None):
        parent = window(waveforms)
        self.addCleanup(parent.deleteLater)
        made = editors.MacroDialog(copy.deepcopy(action), parent)
        self.addCleanup(made.deleteLater)
        return made

    def test_every_step_comes_back_as_it_went_in(self):
        action = {"type": "macro", "name": "m", "repeat": False, "steps": STEPS}
        for waveforms in (None, [], [0x07], [0x00, 0x04]):
            with self.subTest(waveforms=waveforms):
                self.assertEqual(self.dialog(action, waveforms).action(), action)
        self.assertTrue(config.keyboard_action(action))

    def test_an_action_step_offers_only_what_it_may_do(self):
        made = self.dialog({"type": "macro", "steps": [{"do": "action"}]})
        row = made._rows[0]
        self.assertEqual(made.action()["steps"], [])  # no action chosen yet: left out
        chooser = row._chooser()
        self.addCleanup(chooser.deleteLater)
        listed = chooser.picker.list
        offered = {preset_id for preset_id in listed._rows}
        self.assertTrue(offered)
        for preset_id in offered:
            self.assertIn(actions.PRESET_BY_ID[preset_id].action["type"], actions.STEP_ACTIONS)
        for preset_id in ("host_1", "host_2", "host_3", "flow", "ring", "gestures", "wheel_mode", "dpi_cycle", "dpi_shift",
                          "default", "none"):
            self.assertNotIn(preset_id, offered)
        for search in ("", "mac", "Macro", ""):
            listed.search.setText(search)
            self.assertTrue(listed.macro_row.isHidden())
        for row_widget in (listed.keys_row, listed.command_row, listed.script_row, listed.hook_row):
            self.assertFalse(row_widget.isHidden())

        chooser.picker.changed.emit({"type": "host", "host": 1})  # never kept, should one come
        self.assertEqual(made.action()["steps"], [])
        chooser.picker.changed.emit({"type": "keys", "keys": ["KEY_F5"]})
        self.assertEqual(chooser.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(made.action()["steps"], [{"do": "action", "action": {"type": "keys", "keys": ["KEY_F5"]}}])
        self.assertEqual(row.editor.text(), actions.describe({"type": "keys", "keys": ["KEY_F5"]}))

    def test_incomplete_action_steps_can_be_previewed_and_repaired(self):
        from mxoptions.ui.action_picker import ActionPicker

        invalid = [
            {"type": "command", "command": []}, {"type": "script", "name": {}, "script": "true"},
            {"type": "keys", "keys": [9]}, {"type": "macro", "steps": []},
        ]
        for nested in invalid:
            with self.subTest(nested=nested):
                step = {"do": "action", "action": nested}
                original = {"type": "macro", "steps": [step]}
                kept = config.clean({"buttons": {"back": copy.deepcopy(original)}})["buttons"]["back"]
                self.assertEqual(kept, original)
                self.assertEqual(actions.describe_step(kept["steps"][0]), "")
                picker = ActionPicker()
                self.addCleanup(picker.deleteLater)
                picker.edit("Back", kept)
                made = self.dialog(kept)
                self.assertEqual(made.action()["steps"], [])
                made._rows[0]._set_action({"type": "keys", "keys": ["KEY_A"]})
                self.assertTrue(config.keyboard_action(made.action()))

    def test_a_vibration_step_shows_what_the_mouse_can_play(self):
        cases = [(None, list(device.WAVEFORMS), True), ([], list(device.WAVEFORMS), False), ([0x00, 0x04], [0x00, 0x04, 0x07], True)]
        for waveforms, listed, enabled in cases:
            with self.subTest(waveforms=waveforms):
                made = self.dialog({"type": "macro", "steps": [{"do": "haptic", "waveform": 0x07}]}, waveforms)
                combo = made._rows[0].editor
                self.assertEqual([combo.itemData(i) for i in range(combo.count())], listed)
                self.assertEqual(combo.isEnabled(), enabled)
                hints = [label.text() for label in made._rows[0].findChildren(editors.QLabel) if label.objectName() == "hint"]
                self.assertEqual(hints, [] if enabled else [editors._("This mouse has no haptic motor.")])
                self.assertEqual(made.action()["steps"], [{"do": "haptic", "waveform": 0x07}])

    def test_new_steps_start_complete_or_are_left_out(self):
        made = self.dialog({"type": "macro", "steps": []}, [0x00, 0x04])
        made.add_step(copy.deepcopy(editors.STEP_DEFAULTS["haptic"]))
        made.add_step(copy.deepcopy(editors.STEP_DEFAULTS["action"]))
        self.assertEqual(made.action()["steps"], [{"do": "haptic", "waveform": device.WAVE_SUBTLE}])
        for step in made.action()["steps"]:
            self.assertTrue(config.macro_step(step))
        self.assertEqual(set(editors.STEP_LABELS), set(actions.MACRO_STEPS))

    def test_press_again_comes_back_as_it_went_in_and_is_left_out_when_off(self):
        old = {"type": "macro", "name": "m", "repeat": True, "steps": STEPS}
        stoppable = dict(old, stop_on_press=True)
        made = self.dialog(stoppable)
        self.assertTrue(made.again.isChecked())
        self.assertEqual(made.action(), stoppable)
        made.again.setChecked(False)
        self.assertEqual(made.action(), old)
        for action in (old, dict(old, stop_on_press=False), dict(old, stop_on_press="yes")):
            with self.subTest(action=action):
                made = self.dialog(action)
                self.assertFalse(made.again.isChecked())
                self.assertEqual(made.action(), old)
                made.again.setChecked(True)
                self.assertEqual(made.action(), stoppable)

    def test_the_press_again_switch_has_a_name_for_screen_readers(self):
        self.assertEqual(self.dialog({"type": "macro", "steps": []}).again.accessibleName(), editors._("Press again to stop"))


if __name__ == "__main__":
    unittest.main()
