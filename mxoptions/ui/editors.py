"""Dialogs for the two free-form actions: a script and a macro."""

import copy
import os

from PySide6.QtCore import QStandardPaths, Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from .. import APP_NAME, actions, config, device
from ..i18n import _
from .recorder import ShortcutRecorder
from .store import _reason
from .widgets import Segmented, Switch, hint_label, ui_scale

# Set by the application: callable(action) that runs an action right now.
test_runner = None

STEP_LABELS = {
    "keys": "Keys",
    "text": "Text",
    "delay": "Wait",
    "click": "Click",
    "command": "Command",
    "action": "Action",
    "haptic": "Vibration",
}
# Catalogue entries an action step cannot do (see actions.STEP_ACTIONS); the custom macro is hidden apart
STEP_EXCLUDE = frozenset(p.id for p in actions.PRESETS if p.action.get("type") not in actions.STEP_ACTIONS)
STEP_DEFAULTS = {
    "keys": {"do": "keys", "keys": []},
    "text": {"do": "text", "text": ""},
    "delay": {"do": "delay", "ms": 250},
    "click": {"do": "click", "button": "BTN_LEFT"},
    "command": {"do": "command", "command": ""},
    "action": {"do": "action"},  # no action until one is chosen: left out when saved
    "haptic": {"do": "haptic", "waveform": device.WAVE_SUBTLE},
}


def _mouse_waveforms(window):
    """The patterns the mouse can play: a list (empty: no haptic motor), or None while no mouse is known."""
    store = getattr(window, "store", None)
    caps = store.state.get("caps") if store is not None else None
    return list(caps["waveforms"]) if caps else None


def _field_label(text):
    label = QLabel(text)
    label.setObjectName("fieldLabel")
    return label


class _Dialog(QDialog):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(22, 20, 22, 18)
        self.body.setSpacing(10)
        heading = QLabel(title)
        heading.setObjectName("pickerTitle")
        self.body.addWidget(heading)
        self.body.addWidget(_field_label(_("Name")))
        self.name = QLineEdit()
        self.body.addWidget(self.name)

    def add_buttons(self, test_label, extra=()):
        """The bottom row: the test button, then ``extra`` (buttons), then Cancel and Save."""
        row = QHBoxLayout()
        row.setSpacing(8)
        self.test = QPushButton(test_label)
        self.test.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test.setVisible(test_runner is not None)
        row.addWidget(self.test)
        for button in extra:
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(button)
        row.addStretch(1)
        cancel = QPushButton(_("Cancel"))
        cancel.clicked.connect(self.reject)
        save = QPushButton(_("Save"))
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        for button in (cancel, save):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(button)
        self.body.addLayout(row)


class ScriptDialog(_Dialog):
    def __init__(self, action=None, parent=None):
        super().__init__(_("Script"), parent)
        action = action or {}
        self.name.setText(action.get("name", ""))
        self.name.setPlaceholderText(_("e.g. Toggle VPN"))
        self.body.addWidget(_field_label(_("Script")))
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.editor.setPlaceholderText('notify-send "MX Options" "Hello"\n# ...')
        self.editor.setPlainText(action.get("script", ""))
        self.editor.setTabChangesFocus(False)
        self.editor.setMinimumSize(560, 300)
        self.body.addWidget(self.editor, 1)
        hint = hint_label(_(
            "Runs with bash. Start with a #! line (for example #!/usr/bin/env python3) to use another interpreter."
        ))
        hint.setMinimumHeight(hint.fontMetrics().lineSpacing() * 2 + 6)  # wraps to two lines at this width
        self.body.addWidget(hint)
        self.add_buttons(_("Run now"))
        self.test.clicked.connect(lambda: test_runner(self.action()))
        self.editor.setFocus()

    def action(self):
        return {"type": "script", "name": self.name.text().strip(), "script": self.editor.toPlainText()}


class StepRow(QFrame):
    def __init__(self, step, dialog):
        super().__init__()
        self.setObjectName("stepRow")
        self.kind = step["do"]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(8)
        kind_label = QLabel(_(STEP_LABELS[self.kind]).upper())
        kind_label.setObjectName("stepKind")
        layout.addWidget(kind_label)

        if self.kind == "keys":
            self.editor = ShortcutRecorder()
            self.editor.set_keys(step.get("keys", []))
            self._keys = list(step.get("keys", []))
            self.editor.recorded.connect(self._set_keys)
        elif self.kind == "text":
            self.editor = QLineEdit(step.get("text", ""))
            self.editor.setPlaceholderText(_("Text to insert"))
        elif self.kind == "delay":
            self.editor = QSpinBox()
            self.editor.setRange(0, actions.MAX_DELAY_MS)
            self.editor.setSingleStep(50)
            self.editor.setSuffix(" ms")
            self.editor.setValue(int(step.get("ms", 250)))
        elif self.kind == "click":
            self.editor = Segmented([("BTN_LEFT", _("Left")), ("BTN_RIGHT", _("Right")), ("BTN_MIDDLE", _("Middle"))])
            self._button = step.get("button", "BTN_LEFT")
            self.editor.set_value(self._button)
            self.editor.selected.connect(self._set_button)
        elif self.kind == "action":
            self.editor = QPushButton()
            self.editor.setCursor(Qt.CursorShape.PointingHandCursor)
            self.editor.clicked.connect(self._choose)
            self._set_action(step["action"] if config.macro_step(step) else None)
        elif self.kind == "haptic":
            self.editor = QComboBox()
            waveforms = dialog.waveforms
            offered = list(waveforms or device.WAVEFORMS)
            value = step.get("waveform") if config.macro_step(step) else None
            if value is not None and value not in offered:
                offered.append(value)  # what the macro has stays, if another mouse can play it
            for waveform in offered:
                self.editor.addItem(_(device.WAVEFORMS[waveform]), waveform)
            self.editor.setCurrentIndex(max(0, self.editor.findData(value)))
            self.editor.setEnabled(waveforms != [])
        else:
            self.editor = QLineEdit(step.get("command", ""))
            self.editor.setPlaceholderText(_("e.g. firefox or a script path"))
        layout.addWidget(self.editor, 1)
        if self.kind == "haptic" and dialog.waveforms == []:
            layout.addWidget(hint_label(_("This mouse has no haptic motor.")), 1)
        if self.kind in ("delay", "click"):
            layout.addStretch(1)

        self.up = self._tool("↑", lambda: dialog.move_step(self, -1))
        self.down = self._tool("↓", lambda: dialog.move_step(self, 1))
        remove = self._tool("✕", lambda: dialog.remove_step(self))
        for button in (self.up, self.down, remove):
            layout.addWidget(button)

    def _tool(self, text, callback):
        button = QPushButton(text)
        button.setObjectName("tool")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(callback)
        return button

    def _set_keys(self, key_names):
        self._keys = list(key_names)

    def _set_button(self, value):
        self._button = value

    def _set_action(self, action):
        self._action = action
        self.editor.setText(actions.describe(action) if action else _("Choose an action"))

    def _choose(self):
        self._chooser().exec()

    def _chooser(self):
        """The action picker, with only what an action step can do."""
        from .action_picker import ActionPicker  # it imports this module

        chooser = QDialog(self.window())
        chooser.setWindowTitle(_("Action"))
        layout = QVBoxLayout(chooser)
        picker = ActionPicker(allow_button_actions=False, exclude=STEP_EXCLUDE)
        macro = picker.list.macro_row
        macro.hide()
        picker.list.search.textChanged.connect(lambda _text: macro.hide())  # after the search showed what it matches
        picker.edit(_("Action"), self._action or {})

        def picked(action):
            if config.macro_step({"do": "action", "action": action}):
                self._set_action(action)
                chooser.accept()

        picker.changed.connect(picked)
        layout.addWidget(picker, 1)
        cancel = QPushButton(_("Cancel"))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(chooser.reject)
        layout.addWidget(cancel, 0, Qt.AlignmentFlag.AlignRight)
        chooser.resize(chooser.sizeHint().width(), int(560 * ui_scale()))
        chooser.picker = picker
        return chooser

    def step(self):
        if self.kind == "keys":
            return {"do": "keys", "keys": list(self._keys)}
        if self.kind == "text":
            return {"do": "text", "text": self.editor.text()}
        if self.kind == "delay":
            return {"do": "delay", "ms": self.editor.value()}
        if self.kind == "click":
            return {"do": "click", "button": self._button}
        if self.kind == "action":
            return {"do": "action", "action": copy.deepcopy(self._action)} if self._action else None
        if self.kind == "haptic":
            return {"do": "haptic", "waveform": self.editor.currentData()}
        return {"do": "command", "command": self.editor.text().strip()}


class MacroDialog(_Dialog):
    def __init__(self, action=None, parent=None):
        super().__init__(_("Macro"), parent)
        action = action or {}
        self.waveforms = _mouse_waveforms(parent)
        self.name.setPlaceholderText(_("e.g. Sign and send"))
        self.body.addWidget(_field_label(_("Steps")))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumSize(600, 280)
        holder = QWidget()
        self.steps = QVBoxLayout(holder)
        self.steps.setContentsMargins(0, 0, 6, 0)
        self.steps.setSpacing(6)
        self.empty = hint_label(_("No steps yet. Add the first one below."))
        self.steps.addWidget(self.empty)
        self.steps.addStretch(1)
        scroll.setWidget(holder)
        self.body.addWidget(scroll, 1)

        add = QHBoxLayout()
        add.setSpacing(6)
        add.addWidget(_field_label(_("Add")))
        for kind in actions.MACRO_STEPS:
            button = QPushButton("+ " + _(STEP_LABELS[kind]))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, k=kind: self.add_step(copy.deepcopy(STEP_DEFAULTS[k]), focus=True))
            add.addWidget(button)
        add.addStretch(1)
        self.body.addLayout(add)

        repeat = QHBoxLayout()
        repeat_text = QVBoxLayout()
        repeat_text.setSpacing(1)
        title = QLabel(_("Repeat while the button is held"))
        title.setObjectName("rowTitle")
        repeat_text.addWidget(title)
        repeat_text.addWidget(hint_label(_("Only applies when the macro is assigned to a mouse button.")))
        repeat.addLayout(repeat_text, 1)
        self.repeat = Switch()
        repeat.addWidget(self.repeat)
        self.body.addLayout(repeat)

        again = QHBoxLayout()
        again_text = QVBoxLayout()
        again_text.setSpacing(1)
        title = QLabel(_("Press again to stop"))
        title.setObjectName("rowTitle")
        again_text.addWidget(title)
        again_text.addWidget(hint_label(_("Pressing its mouse button or key again while it plays stops it, instead of "
                                          "playing it once more. Not for gestures, Actions Ring bubbles or other programs.")))
        again.addLayout(again_text, 1)
        self.again = Switch()
        self.again.setAccessibleName(_("Press again to stop"))
        again.addWidget(self.again)
        self.body.addLayout(again)

        self.imported = hint_label("")  # after an import: where from, and to look before saving
        self.imported.hide()
        self.body.addWidget(self.imported)
        self.export_button = QPushButton(_("Export…"))
        self.export_button.setAccessibleName(_("Export macro"))
        self.export_button.clicked.connect(self._export)
        self.import_button = QPushButton(_("Import…"))
        self.import_button.setAccessibleName(_("Import macro"))
        self.import_button.clicked.connect(self._import)
        self.add_buttons(_("Test in 3 s"), (self.export_button, self.import_button))
        self.test.clicked.connect(self._test)
        self._rows = []
        self._load(action)

    def _load(self, action):
        """Show ``action``: its name, switches and steps in place of what the editor had."""
        self.name.setText(action.get("name", ""))
        self.repeat.set_checked_silently(bool(action.get("repeat")))
        self.again.set_checked_silently(action.get("stop_on_press") is True)
        for row in list(self._rows):
            self.remove_step(row)
        for step in action.get("steps", []):
            if isinstance(step, dict) and step.get("do") in actions.MACRO_STEPS:
                self.add_step(step)
        self._sync()

    def _export(self):
        """Write the macro as it is in the editor, changes not saved yet too, to a file the user picks."""
        try:
            raw = config.export_macro(self.action())
        except ValueError as e:
            QMessageBox.warning(self, APP_NAME, _("Nothing was exported. {}").format(e))
            return
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        path, _filter = QFileDialog.getSaveFileName(  # the dialog asks before it replaces a file
            self, _("Export macro"), os.path.join(folder, "mxoptions-macro.json"), _("Macro (*.json)"))
        if not path:
            return
        try:
            config.write_durable(raw, path)
        except config.NotDurable as e:
            QMessageBox.warning(self, APP_NAME, _("The macro was written, but the system could not confirm that it is "
                                                  "safely on the disk ({}).").format(_reason(e)))
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, _("The macro could not be written ({}).").format(_reason(e)))
        else:
            QMessageBox.information(self, APP_NAME, _("The macro is in {}. Everything it types or runs is in the file "
                                                      "as written.").format(os.path.basename(path)))

    def _import(self):
        """Fill the editor from a macro file, checked whole first. Nothing runs and nothing is kept until Save."""
        path, _filter = QFileDialog.getOpenFileName(self, _("Import macro"), "", _("Macro (*.json)"))
        if not path:
            return
        name = os.path.basename(path)
        try:
            macro = config.import_macro(config.read_settings(path, macro=True))
        except ValueError as e:
            QMessageBox.warning(self, APP_NAME, _("{name} cannot be imported. {reason}").format(name=name, reason=e))
            return
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, _("{name} could not be read ({reason}).").format(
                name=name, reason=_reason(e)))
            return
        self._load(macro)
        self.imported.setText(_("Imported from {}. A macro file can run commands: check each step before you "
                                "save.").format(name))
        self.imported.show()

    def add_step(self, step, focus=False):
        if len(self._rows) >= actions.MAX_MACRO_STEPS:
            return
        row = StepRow(step, self)
        self._rows.append(row)
        self.steps.insertWidget(self.steps.count() - 1, row)
        self._sync()
        if focus:
            row.editor.setFocus()

    def remove_step(self, row):
        self._rows.remove(row)
        self.steps.removeWidget(row)
        row.deleteLater()
        self._sync()

    def move_step(self, row, offset):
        index = self._rows.index(row)
        target = index + offset
        if not 0 <= target < len(self._rows):
            return
        self._rows.insert(target, self._rows.pop(index))
        self.steps.removeWidget(row)
        self.steps.insertWidget(target + 1, row)  # +1: the "empty" hint sits at index 0
        self._sync()

    def _sync(self):
        self.empty.setVisible(not self._rows)
        for index, row in enumerate(self._rows):
            row.up.setEnabled(index > 0)
            row.down.setEnabled(index < len(self._rows) - 1)

    def _test(self):
        action = self.action()
        action["repeat"] = False
        self.test.setEnabled(False)
        QTimer.singleShot(3000, lambda: test_runner(action))
        QTimer.singleShot(3200, self, lambda: self.test.setEnabled(True))  # dropped if the dialog closes

    def action(self):
        made = {
            "type": "macro",
            "name": self.name.text().strip(),
            "repeat": self.repeat.isChecked(),
            "steps": [step for step in (row.step() for row in self._rows) if step is not None],
        }
        if self.again.isChecked():
            made["stop_on_press"] = True  # left out when off: macros saved before stay as they were
        return made
