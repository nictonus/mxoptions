"""Side panel for choosing what a button, gesture or ring slot does."""

import copy

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import actions, config, gestures
from ..i18n import _
from . import ring, theme
from .editors import MacroDialog, ScriptDialog
from .recorder import ShortcutRecorder
from .widgets import ElidedButton, ElidedLabel, InfoButton, titled, ui_scale

GESTURE_LABELS = {
    "tap": "Tap",
    "up": "Swipe up",
    "down": "Swipe down",
    "left": "Swipe left",
    "right": "Swipe right",
}
GESTURE_ARROWS = {"tap": "●", "up": "↑", "down": "↓", "left": "←", "right": "→"}
SHAPE_LABELS = {
    "down_right": "Down, then right", "down_left": "Down, then left", "up_right": "Up, then right", "up_left": "Up, then left",
    "right_down": "Right, then down", "right_up": "Right, then up", "left_down": "Left, then down", "left_up": "Left, then up",
    "up_down": "Up and back", "down_up": "Down and back", "left_right": "Left and back", "right_left": "Right and back",
    "circle_cw": "Circle, clockwise", "circle_ccw": "Circle, anticlockwise",
    "zigzag_h": "Shake sideways", "zigzag_v": "Shake up and down",
}


def shape_icon(name, size=26):
    """A little picture of a shape: its line, a dot where it starts and an arrowhead where it ends."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    points = [QPointF(x * size, y * size) for x, y in gestures.outline(name)]
    ink = QColor(theme.ACCENT_INK)
    painter.setPen(QPen(ink, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.drawPolyline(QPolygonF(points))
    painter.setBrush(ink)
    painter.drawEllipse(points[0], 1.6, 1.6)
    tip, before = points[-1], points[-2]
    along = QPointF(tip.x() - before.x(), tip.y() - before.y())
    length = max(1e-6, (along.x() ** 2 + along.y() ** 2) ** 0.5)
    ux, uy = along.x() / length, along.y() / length
    wing = size * 0.17
    painter.drawPolygon(QPolygonF([
        tip, QPointF(tip.x() - ux * wing - uy * wing * 0.6, tip.y() - uy * wing + ux * wing * 0.6),
        QPointF(tip.x() - ux * wing + uy * wing * 0.6, tip.y() - uy * wing - ux * wing * 0.6)]))
    painter.end()
    return QIcon(pixmap)


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
        self._slot = None  # what of a gesture button is being edited: "tap", a direction or a shape
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
        self.about = InfoButton()
        heading = titled(self.title, self.about)
        heading.setContentsMargins(0, 0, 16, 0)
        layout.addLayout(heading)

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
            row.clicked.connect(lambda _checked=False, d=direction: self._edit_slot(d))
            block.addWidget(row)
            self._gesture_rows[direction] = value
        shapes = QPushButton()  # one more row: the shapes that can be drawn with the button held
        shapes.setObjectName("chip")
        shapes.setCursor(Qt.CursorShape.PointingHandCursor)
        inner = QHBoxLayout(shapes)
        inner.setContentsMargins(12, 8, 12, 8)
        name = QLabel("✎   " + _("Shapes"))
        name.setObjectName("chipTitle")
        self.shapes_value = ElidedLabel()
        self.shapes_value.setObjectName("chipValue")
        self.shapes_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for label in (name, self.shapes_value):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        inner.addWidget(name)
        inner.addWidget(self.shapes_value, 1)
        shapes.setMinimumHeight(38)
        shapes.clicked.connect(lambda: self.stack.setCurrentIndex(3))
        block.addWidget(shapes)
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
        back.clicked.connect(lambda: self.stack.setCurrentIndex(3 if self._slot in gestures.SHAPES else 0))
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

        # page 3: the shapes of a gesture button, each with its action
        drawn = QWidget()
        layout = QVBoxLayout(drawn)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        back = QPushButton("‹  " + _("Back"))
        back.setObjectName("flat")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        layout.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        heading = QLabel(_("Shapes"))
        heading.setObjectName("pickerTitle")
        about = InfoButton(_(
            "Hold the button and draw: a corner, there and back, a circle, a shake. A shape that has an action here "
            "runs it when the button comes up. While any shape has one, plain swipes of this button are also judged "
            "when it comes up, not half-way; with repeating swipes shapes are not read at all."))
        line = titled(heading, about)
        line.setContentsMargins(0, 0, 16, 0)
        layout.addLayout(line)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        rows = QVBoxLayout(holder)
        rows.setContentsMargins(0, 0, 16, 0)
        rows.setSpacing(4)
        self._shape_rows = {}
        self._shape_icons = None  # the theme they were drawn for
        for shape in gestures.SHAPES:
            row = QPushButton()
            row.setObjectName("chip")
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.setIconSize(QSize(26, 26))
            inner = QHBoxLayout(row)
            inner.setContentsMargins(46, 6, 12, 6)  # room for the picture, which the button draws itself
            texts = QVBoxLayout()
            texts.setSpacing(0)
            name = QLabel(_(SHAPE_LABELS[shape]))
            name.setObjectName("chipTitle")
            value = ElidedLabel()
            value.setObjectName("chipValue")
            for label in (name, value):
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                texts.addWidget(label)
            inner.addLayout(texts, 1)
            row.setMinimumHeight(48)
            row.clicked.connect(lambda _checked=False, k=shape: self._edit_slot(k))
            rows.addWidget(row)
            self._shape_rows[shape] = (row, value)
        rows.addStretch(1)
        area.setWidget(holder)
        layout.addWidget(area, 1)
        self.stack.addWidget(drawn)

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
        self.about.set_text(subtitle)
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
            drawn = action.get("shapes") or {}
            self.shapes_value.setText(_("{} with an action").format(len(drawn)) if drawn else _("None yet"))
            if self._shape_icons != theme.REVISION:
                self._shape_icons = theme.REVISION
                for shape, (row, _value) in self._shape_rows.items():
                    row.setIcon(shape_icon(shape))
            for shape, (_row, value) in self._shape_rows.items():
                value.setText(actions.describe(drawn[shape]) if shape in drawn else _("No action"))

    def _picked(self, action):
        if action.get("type") == "gestures":
            if (self._action or {}).get("type") == "gestures":
                return
            action = config.default_gestures()
        self._action = action
        self._refresh()
        self.changed.emit(copy.deepcopy(action))

    def _edit_slot(self, slot):
        """Choose the action for one direction, the tap, or one shape of a gesture button."""
        self._slot = slot
        if slot in gestures.SHAPES:
            self.sub_title.setText(_(SHAPE_LABELS[slot]))
            current = (self._action.get("shapes") or {}).get(slot) or {"type": "none"}
        else:
            self.sub_title.setText(_(GESTURE_LABELS[slot]))
            current = self._action.get(slot)
        self.sub_list.search.clear()
        self.sub_list.set_current(current)
        self.stack.setCurrentIndex(1)

    def _sub_picked(self, action):
        if self._slot in gestures.SHAPES:
            drawn = self._action.setdefault("shapes", {})
            if action.get("type") == "none":
                drawn.pop(self._slot, None)
            else:
                drawn[self._slot] = action
            if not drawn:
                self._action.pop("shapes", None)
        else:
            self._action[self._slot] = action
        self.sub_list.set_current(action)
        self._refresh()
        self.changed.emit(copy.deepcopy(self._action))
