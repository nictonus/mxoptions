"""Side panel for choosing what a button, gesture or ring slot does."""

import copy

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import actions, config
from ..i18n import _
from . import ring
from .editors import MacroDialog, ScriptDialog
from .recorder import ShortcutRecorder
from .widgets import ElidedButton, ElidedLabel, ui_scale

GESTURE_LABELS = {
    "tap": "Tap",
    "up": "Swipe up",
    "down": "Swipe down",
    "left": "Swipe left",
    "right": "Swipe right",
}
GESTURE_ARROWS = {"tap": "●", "up": "↑", "down": "↓", "left": "←", "right": "→"}


class ActionList(QWidget):
    """Searchable catalogue plus the two custom actions."""

    picked = Signal(object)

    def __init__(self, exclude=frozenset(), parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Search actions"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        search_line = QHBoxLayout()
        search_line.setContentsMargins(0, 0, 16, 0)  # line up with the rows, which leave room for the scroll bar
        search_line.addWidget(self.search)
        layout.addLayout(search_line)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(0, 0, 6, 0)
        self._body.setSpacing(2)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._sections = []  # (category label, [(row, extra widget shown under it or None)])
        self._rows = {}  # preset id -> row
        self._current = None
        have_kde = _kde_shortcuts_available()
        for category, title in actions.CATEGORIES:
            presets = [
                p for p in actions.PRESETS
                if p.category == category and p.id not in exclude and (have_kde or p.action.get("type") != "shortcut")
            ]
            if not presets:
                continue
            rows = []
            label = self._category(_(title))
            for preset in presets:
                row = self._row(_(preset.label), preset.icon)
                row.clicked.connect(lambda _checked=False, p=preset: self.picked.emit(copy.deepcopy(p.action)))
                self._rows[preset.id] = row
                rows.append((row, None))
            self._sections.append((label, rows))

        label = self._category(_("Custom"))
        self.keys_row = self._row(_("Keyboard shortcut"), "input-keyboard")
        self.recorder = ShortcutRecorder()
        self._body.addWidget(self.recorder)
        self.command_row = self._row(_("Run command"), "system-run")
        self.command = QLineEdit()
        self.command.setPlaceholderText(_("e.g. konsole or a script path"))
        self._body.addWidget(self.command)
        self.script_row = self._row(_("Script"), "text-x-script")
        self.script_summary = self._summary()
        self.macro_row = self._row(_("Macro"), "media-record")
        self.macro_summary = self._summary()
        self.hook_row = self._row(_("Signal to another program"), "network-connect")
        self.hook = QLineEdit()
        self.hook.setPlaceholderText(_("a name that program listens for"))
        self._body.addWidget(self.hook)
        self._sections.append((label, [
            (self.keys_row, self.recorder), (self.command_row, self.command),
            (self.script_row, self.script_summary), (self.macro_row, self.macro_summary),
            (self.hook_row, self.hook),
        ]))
        self._body.addStretch(1)

        self.keys_row.clicked.connect(self._keys_clicked)
        self.recorder.recorded.connect(lambda key_names: self.picked.emit({"type": "keys", "keys": key_names}))
        self.command_row.clicked.connect(self._command_clicked)
        self.command.editingFinished.connect(self._command_edited)
        self.script_row.clicked.connect(lambda: self._edit_with(ScriptDialog, "script"))
        self.macro_row.clicked.connect(lambda: self._edit_with(MacroDialog, "macro"))
        self.hook_row.clicked.connect(self._hook_clicked)
        self.hook.editingFinished.connect(self._hook_edited)
        self._sync_custom()

    def _summary(self):
        label = QLabel()
        label.setObjectName("summary")
        label.setWordWrap(True)
        self._body.addWidget(label)
        return label

    def _edit_with(self, dialog_class, kind):
        """Open the script/macro editor; clicking the row of the current action edits it."""
        current = self._current if (self._current or {}).get("type") == kind else None
        dialog = dialog_class(current, self.window())
        if dialog.exec():
            self.picked.emit(dialog.action())
        else:
            self.set_current(self._current)

    def _category(self, text):
        label = QLabel(text.upper())
        label.setObjectName("categoryLabel")
        self._body.addWidget(label)
        return label

    def _row(self, text, icon):
        row = ElidedButton(text)
        row.setObjectName("actionRow")
        row.setCheckable(True)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setIcon(QIcon.fromTheme(ring.theme_icon_name(icon) or icon))
        row.setIconSize(QSize(18, 18))
        self._group.addButton(row)
        self._body.addWidget(row)
        return row

    def _keys_clicked(self):
        self._sync_custom()
        self.recorder.setFocus()

    def _command_clicked(self):
        self._sync_custom()
        self.command.setFocus()
        if self.command.text().strip():
            self._command_edited()

    def _command_edited(self):
        text = self.command.text().strip()
        if text and self.command_row.isChecked():
            self.picked.emit({"type": "command", "command": text})

    def _hook_clicked(self):
        self._sync_custom()
        self.hook.setFocus()
        if self.hook.text().strip():
            self._hook_edited()

    def _hook_edited(self):
        text = self.hook.text().strip()
        if text and self.hook_row.isChecked():
            self.picked.emit({"type": "hook", "name": text})

    def _sync_custom(self):
        for row, extra in ((self.keys_row, self.recorder), (self.command_row, self.command),
                           (self.script_row, self.script_summary), (self.macro_row, self.macro_summary),
                           (self.hook_row, self.hook)):
            extra.setVisible(row.isChecked() and not row.isHidden())

    def set_current(self, action):
        self._current = action
        preset = actions.find_preset(action)
        kind = (action or {}).get("type")
        self._group.setExclusive(False)
        for button in self._group.buttons():
            button.setChecked(False)
        self._group.setExclusive(True)
        if preset and preset.id in self._rows:
            self._rows[preset.id].setChecked(True)
        elif kind == "keys":
            self.keys_row.setChecked(True)
            self.recorder.set_keys(action.get("keys", []))
        elif kind == "command":
            self.command_row.setChecked(True)
            if self.command.text().strip() != action.get("command", ""):
                self.command.setText(action.get("command", ""))
        elif kind == "script":
            self.script_row.setChecked(True)
            lines = [line for line in action.get("script", "").splitlines() if line.strip()]
            self.script_summary.setText(_("{name} · {count} lines. Click \"Script\" to edit.").format(
                name=actions.describe(action), count=len(lines)))
        elif kind == "hook":
            self.hook_row.setChecked(True)
            if self.hook.text().strip() != action.get("name", ""):
                self.hook.setText(action.get("name", ""))
        elif kind == "macro":
            self.macro_row.setChecked(True)
            steps = action.get("steps", [])
            preview = "  →  ".join(filter(None, (actions.describe_step(step) for step in steps[:4])))
            if len(steps) > 4:
                preview += "  → …"
            self.macro_summary.setText(preview or _("Empty macro. Click \"Macro\" to edit."))
        self._sync_custom()

    def _filter(self, text):
        needle = text.strip().lower()
        for label, rows in self._sections:
            any_visible = False
            for row, extra in rows:
                visible = not needle or needle in row.text().lower()
                row.setVisible(visible)
                any_visible = any_visible or visible
            label.setVisible(any_visible)
        self._sync_custom()


_kde_available = None


def _kde_shortcuts_available():
    global _kde_available
    if _kde_available is None:
        try:
            from PySide6.QtDBus import QDBusConnection

            _kde_available = bool(QDBusConnection.sessionBus().interface().isServiceRegistered("org.kde.kglobalaccel").value())
        except Exception:
            _kde_available = False
    return _kde_available


class ActionPicker(QFrame):
    """``edit(title, action)`` shows the chooser; ``changed`` carries the new action."""

    changed = Signal(object)

    def __init__(self, allow_button_actions=True, parent=None):
        super().__init__(parent)
        self.setObjectName("picker")
        self.setFixedWidth(int(320 * ui_scale()))
        self._action = None
        self._direction = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 20, 12, 14)
        outer.setSpacing(10)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)

        # page 0: the action for the control itself
        main = QWidget()
        layout = QVBoxLayout(main)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.title = QLabel()
        self.title.setObjectName("pickerTitle")
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("hint")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)

        self.gesture_block = QWidget()
        block = QVBoxLayout(self.gesture_block)
        block.setContentsMargins(0, 0, 16, 0)
        block.setSpacing(4)
        self._gesture_rows = {}
        for direction in actions.DIRECTIONS:
            row = QPushButton()
            row.setObjectName("chip")
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            inner = QHBoxLayout(row)
            inner.setContentsMargins(12, 8, 12, 8)
            name = QLabel(f"{GESTURE_ARROWS[direction]}   {_(GESTURE_LABELS[direction])}")
            name.setObjectName("chipTitle")
            value = ElidedLabel()
            value.setObjectName("chipValue")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            for label in (name, value):
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            inner.addWidget(name)
            inner.addWidget(value, 1)
            row.setMinimumHeight(38)
            row.clicked.connect(lambda _checked=False, d=direction: self._edit_direction(d))
            block.addWidget(row)
            self._gesture_rows[direction] = value
        layout.addWidget(self.gesture_block)

        self.list = ActionList(exclude=frozenset() if allow_button_actions else actions.BUTTON_ONLY)
        self.list.picked.connect(self._picked)
        layout.addWidget(self.list, 1)
        self.stack.addWidget(main)

        # page 1: the action for one gesture direction
        sub = QWidget()
        layout = QVBoxLayout(sub)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        back = QPushButton("‹  " + _("Back"))
        back.setObjectName("flat")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        layout.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        self.sub_title = QLabel()
        self.sub_title.setObjectName("pickerTitle")
        layout.addWidget(self.sub_title)
        self.sub_list = ActionList(exclude=actions.BUTTON_ONLY)
        self.sub_list.picked.connect(self._sub_picked)
        layout.addWidget(self.sub_list, 1)
        self.stack.addWidget(sub)

        # page 2: a control that cannot be reassigned
        fixed = QWidget()
        layout = QVBoxLayout(fixed)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(10)
        self.fixed_title = QLabel()
        self.fixed_title.setObjectName("pickerTitle")
        self.fixed_title.setWordWrap(True)
        layout.addWidget(self.fixed_title)
        self.fixed_value = QLabel()
        self.fixed_value.setObjectName("rowTitle")
        self.fixed_value.setWordWrap(True)
        layout.addWidget(self.fixed_value)
        self.fixed_text = QLabel()
        self.fixed_text.setObjectName("hint")
        self.fixed_text.setWordWrap(True)
        layout.addWidget(self.fixed_text)
        layout.addStretch(1)
        self.stack.addWidget(fixed)

    def show_fixed(self, title, value, text):
        """Describe a control whose function is built into the mouse."""
        self._action = None
        self.fixed_title.setText(title)
        self.fixed_value.setText(value)
        self.fixed_text.setText(text)
        self.stack.setCurrentIndex(2)

    def edit(self, title, action, subtitle=""):
        self._action = copy.deepcopy(action)
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.subtitle.setVisible(bool(subtitle))
        self.stack.setCurrentIndex(0)
        self._refresh()

    def _refresh(self):
        action = self._action or {}
        self.list.set_current(action)
        is_gestures = action.get("type") == "gestures"
        self.gesture_block.setVisible(is_gestures)
        if is_gestures:
            for direction, label in self._gesture_rows.items():
                label.setText(actions.describe(action.get(direction)))

    def _picked(self, action):
        if action.get("type") == "gestures":
            if (self._action or {}).get("type") == "gestures":
                return
            action = config.default_gestures()
        self._action = action
        self._refresh()
        self.changed.emit(copy.deepcopy(action))

    def _edit_direction(self, direction):
        self._direction = direction
        self.sub_title.setText(_(GESTURE_LABELS[direction]))
        self.sub_list.search.clear()
        self.sub_list.set_current(self._action.get(direction))
        self.stack.setCurrentIndex(1)

    def _sub_picked(self, action):
        self._action[self._direction] = action
        self.sub_list.set_current(action)
        self._refresh()
        self.changed.emit(copy.deepcopy(self._action))
