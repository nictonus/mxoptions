"""Dialogs for the two free-form actions: a script and a macro."""

import copy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from .. import actions
from ..i18n import _
from .recorder import ShortcutRecorder
from .widgets import Segmented, Switch, hint_label

# Set by the application: callable(action) that runs an action right now.
test_runner = None

STEP_LABELS = {
    "keys": "Keys",
    "text": "Text",
    "delay": "Wait",
    "click": "Click",
    "command": "Command",
}
STEP_DEFAULTS = {
    "keys": {"do": "keys", "keys": []},
    "text": {"do": "text", "text": ""},
    "delay": {"do": "delay", "ms": 250},
    "click": {"do": "click", "button": "BTN_LEFT"},
    "command": {"do": "command", "command": ""},
}


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

    def add_buttons(self, test_label):
        row = QHBoxLayout()
        row.setSpacing(8)
        self.test = QPushButton(test_label)
        self.test.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test.setVisible(test_runner is not None)
        row.addWidget(self.test)
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
        else:
            self.editor = QLineEdit(step.get("command", ""))
            self.editor.setPlaceholderText(_("e.g. konsole or a script path"))
        layout.addWidget(self.editor, 1)
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

    def step(self):
        if self.kind == "keys":
            return {"do": "keys", "keys": list(self._keys)}
        if self.kind == "text":
            return {"do": "text", "text": self.editor.text()}
        if self.kind == "delay":
            return {"do": "delay", "ms": self.editor.value()}
        if self.kind == "click":
            return {"do": "click", "button": self._button}
        return {"do": "command", "command": self.editor.text().strip()}


class MacroDialog(_Dialog):
    def __init__(self, action=None, parent=None):
        super().__init__(_("Macro"), parent)
        action = action or {}
        self.name.setText(action.get("name", ""))
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
        self.repeat.set_checked_silently(bool(action.get("repeat")))
        repeat.addWidget(self.repeat)
        self.body.addLayout(repeat)

        self.add_buttons(_("Test in 3 s"))
        self.test.clicked.connect(self._test)
        self._rows = []
        for step in action.get("steps", []):
            if isinstance(step, dict) and step.get("do") in actions.MACRO_STEPS:
                self.add_step(step)
        self._sync()

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
        return {
            "type": "macro",
            "name": self.name.text().strip(),
            "repeat": self.repeat.isChecked(),
            "steps": [row.step() for row in self._rows],
        }
