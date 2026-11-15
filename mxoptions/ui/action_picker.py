"""Side panel for choosing what a button, gesture or ring slot does."""

import copy

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractButton, QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .. import actions, config, gestures
from ..i18n import _
from . import icons, ring, theme
from .editors import MacroDialog, ScriptDialog
from .recorder import ShortcutRecorder
from .widgets import ElidedButton, ElidedLabel, InfoButton, Row, SliderRow, SwatchRow, Switch, hint_label, titled, ui_scale

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
# "Still Magnify" is this program's own: a still picture of the screen, not a live magnifier
EFFECT_LABELS = {"highlight": "Highlight", "magnify": "Still Magnify", "laser": "Digital Laser"}
EFFECT_COLORS = ["#000000", "#ffffff", "#f2605d", "#2fd6a7", "#4c9bff", "#f2c744", "#ff6fa8", "#8f7bff"]


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
        self._hidden = frozenset()  # rows of the presets not offered for the control being edited (see set_hidden)
        self._current = None
        # Offered for a key of an app profile only: what the key does in every app (picked as None)
        self.inherit_row = self._row(_("As in all apps"), "edit-undo")
        self.inherit_row.clicked.connect(lambda: self.picked.emit(None))
        self.inherit_row.hide()
        for category, title in actions.CATEGORIES:
            presets = [p for p in actions.PRESETS if p.category == category and p.id not in exclude and p.available]
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
        self.command.setPlaceholderText(_("e.g. firefox or a script path"))
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
        row.setIcon(icons.icon(ring.theme_icon_name(icon) or icon))
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
        if action is None and not self.inherit_row.isHidden():
            self.inherit_row.setChecked(True)
        elif preset and preset.id in self._rows:
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

    def set_hidden(self, ids):
        """Offer none of the presets ``ids``, a search finding them neither."""
        hidden = frozenset(self._rows[preset] for preset in ids if preset in self._rows)
        if hidden != self._hidden:  # unchanged: what the list shows stays as its owner left it
            self._hidden = hidden
            self._filter(self.search.text())

    def _filter(self, text):
        needle = text.strip().lower()
        for label, rows in self._sections:
            any_visible = False
            for row, extra in rows:
                visible = row not in self._hidden and (not needle or needle in row.text().lower())
                row.setVisible(visible)
                any_visible = any_visible or visible
            label.setVisible(any_visible)
        self._sync_custom()


class ActionPicker(QFrame):
    """``edit(title, action)`` shows the chooser; ``changed`` carries the new action."""

    changed = Signal(object)

    def __init__(self, allow_button_actions=True, exclude=None, parent=None):
        """``exclude``: catalogue ids not to offer (what a keyboard key cannot do)."""
        super().__init__(parent)
        self.setObjectName("picker")
        self.setFixedWidth(int(320 * ui_scale()))
        self._action = None
        self._inherited = None  # what "As in all apps" does for the control being edited, when it is offered
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
        moving = QLabel(_("Holding it and moving"))
        moving.setObjectName("hint")
        block.addWidget(moving)
        self.mode = QComboBox()  # a row of three does not fit this panel in every language
        for value, label in (("swipes", "Swipes"), ("pan", "Pan"), ("zoom", "Zoom")):
            self.mode.addItem(_(label), value)
        self.mode.setAccessibleName(_("Holding it and moving"))
        moving.setBuddy(self.mode)
        self.mode.currentIndexChanged.connect(lambda index: self._mode_picked(self.mode.itemData(index)))
        block.addWidget(self.mode)
        self._gesture_rows = {}
        self._gesture_buttons = {}
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
            self._gesture_buttons[direction] = row
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
        self.shapes_button = shapes
        layout.addWidget(self.gesture_block)

        self.spotlight_block = QWidget()  # the three Spotlight effects of the button, each to open and set up
        block = QVBoxLayout(self.spotlight_block)
        block.setContentsMargins(0, 0, 16, 0)
        block.setSpacing(4)
        self._effect_rows = {}
        for effect in config.EFFECTS:
            row = QPushButton()
            row.setObjectName("chip")
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            inner = QHBoxLayout(row)
            inner.setContentsMargins(12, 8, 12, 8)
            name = QLabel(_(EFFECT_LABELS[effect]))
            name.setObjectName("chipTitle")
            value = ElidedLabel()
            value.setObjectName("chipValue")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            for label in (name, value):
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            inner.addWidget(name)
            inner.addWidget(value, 1)
            row.setMinimumHeight(38)
            row.clicked.connect(lambda _checked=False, e=effect: self._edit_effect(e))
            block.addWidget(row)
            self._effect_rows[effect] = (row, value)
        block.addWidget(hint_label(_(
            "Hold the button: the effect follows the pointer until you let go. With more than one effect on, "
            "double-click and hold for the next one. Clicks and scrolling do nothing while it shows. Still Magnify "
            "shows the screen as it was when you pressed, and not on Wayland desktops.")))
        layout.addWidget(self.spotlight_block)

        self.list = ActionList(exclude=(frozenset() if allow_button_actions else actions.BUTTON_ONLY) | frozenset(exclude or ()))
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

        # page 4: one Spotlight effect of the button
        self._effect = None
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 16, 0)
        layout.setSpacing(10)
        back = QPushButton("‹  " + _("Back"))
        back.setObjectName("flat")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        layout.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        self.effect_title = QLabel()
        self.effect_title.setObjectName("pickerTitle")
        layout.addWidget(self.effect_title)
        self.effect_switch = Switch()
        self.effect_switch.setAccessibleName(_("Show this effect"))
        self.effect_switch.toggled.connect(lambda on: self._effect_changed(self._effect, "on", on))
        layout.addWidget(Row(_("Show this effect"), control=self.effect_switch))
        self.effect_size = SliderRow(_("Size"), *config.EFFECT_SIZE)
        self.effect_size.slider.setAccessibleName(_("Size"))
        self.effect_size.changed.connect(lambda value: self._effect_changed(self._effect, "size", int(value)))
        layout.addWidget(self.effect_size)
        colour = QLabel(_("Colour"))
        colour.setObjectName("rowTitle")
        layout.addWidget(colour)
        self.effect_color = SwatchRow(EFFECT_COLORS)
        for swatch in self.effect_color.findChildren(QAbstractButton):
            swatch.setAccessibleName(swatch.color)
        self.effect_color.selected.connect(lambda color: self._effect_changed(self._effect, "color", color))
        layout.addWidget(self.effect_color)
        self.effect_note = hint_label("")
        layout.addWidget(self.effect_note)
        layout.addStretch(1)
        self.stack.addWidget(page)

    def show_fixed(self, title, value, text):
        """Describe a control whose function is built into the mouse."""
        self._action = None
        self.fixed_title.setText(title)
        self.fixed_value.setText(value)
        self.fixed_text.setText(text)
        self.stack.setCurrentIndex(2)

    def edit(self, title, action, subtitle="", inherit=False, hide=(), inherited=None):
        """``inherit``: offer "As in all apps" too, picked as None (``action`` None: it is the one chosen);
        ``inherited``: what that does, so that Gestures picked then start from those of all apps. ``hide``: preset
        ids not offered this time."""
        self._inherited = copy.deepcopy(inherited) if inherit else None
        self.list.inherit_row.setVisible(inherit)
        self.list.set_hidden(hide)
        self._action = copy.deepcopy(action)
        self._slot = None  # a direction or shape being picked for the control before is not one of this one
        self.title.setText(title)
        self.about.set_text(subtitle)
        self.stack.setCurrentIndex(0)
        self._refresh()

    def _refresh(self):
        action = self._action or {}
        self.list.set_current(self._action)
        is_gestures = action.get("type") == "gestures"
        self.gesture_block.setVisible(is_gestures)
        is_spotlight = action.get("type") == "spotlight"
        self.spotlight_block.setVisible(is_spotlight)
        if is_spotlight:
            look = config.spotlight(action)
            for effect, (row, value) in self._effect_rows.items():
                state = _("On") if look[effect]["on"] else _("Off")
                value.setText(state)
                row.setAccessibleName(f"{_(EFFECT_LABELS[effect])}: {state}")
        if is_gestures:
            mode = gestures.hold_mode(action)
            self.mode.blockSignals(True)
            self.mode.setCurrentIndex(self.mode.findData(mode or "swipes"))
            self.mode.blockSignals(False)
            for direction, label in self._gesture_rows.items():
                label.setText(actions.describe(action.get(direction)))
                self._gesture_buttons[direction].setEnabled(direction == "tap" or not mode)  # pan, zoom: only the tap
            self.shapes_button.setEnabled(not mode)
            drawn = action.get("shapes") or {}
            self.shapes_value.setText(_("{} with an action").format(len(drawn)) if drawn else _("None yet"))
            if self._shape_icons != theme.REVISION:
                self._shape_icons = theme.REVISION
                for shape, (row, _value) in self._shape_rows.items():
                    row.setIcon(shape_icon(shape))
            for shape, (_row, value) in self._shape_rows.items():
                value.setText(actions.describe(drawn[shape]) if shape in drawn else _("No action"))

    def _picked(self, action):
        if action is None:  # "As in all apps"
            self._action = None
            self._refresh()
            self.changed.emit(None)
            return
        if action.get("type") in ("gestures", "spotlight"):
            if (self._action or {}).get("type") == action["type"]:
                return
            if action["type"] == "spotlight":
                action = config.default_spotlight()
            elif (self._inherited or {}).get("type") == "gestures":  # an app's own, from those of all apps
                action = config.app_gestures_action(self._inherited)
            else:
                action = config.default_gestures()
        self._action = action
        self._refresh()
        self.changed.emit(copy.deepcopy(action))

    def _mode_picked(self, mode):
        """Swipes, or pan or zoom while the button is held (gestures.HOLD_MODES): the directions stay saved."""
        if mode in gestures.HOLD_MODES:
            self._action["mode"] = mode
        else:
            self._action.pop("mode", None)
        self._refresh()
        self.changed.emit(copy.deepcopy(self._action))

    def _edit_effect(self, name):
        """Set up one Spotlight effect of the button."""
        self._effect = name
        self._fill_effect()
        self.stack.setCurrentIndex(4)

    def _fill_effect(self):
        look = config.spotlight(self._action)
        effect = look[self._effect]
        self.effect_title.setText(_(EFFECT_LABELS[self._effect]))
        self.effect_switch.set_checked_silently(effect["on"])
        # the one effect left on stays on: a press shows something
        self.effect_switch.setEnabled(not effect["on"] or sum(look[name]["on"] for name in config.EFFECTS) > 1)
        self.effect_size.set_value(effect["size"])
        self.effect_color.set_value(effect["color"])
        kind = ring.overlay_kind()
        if self._effect in ring.effects_here(kind):
            note = _("Shows the screen as it was when you pressed: a still picture, kept in memory until you let go. "
                     "Not a live magnifier.") if self._effect == "magnify" else ""
        elif self._effect == "magnify" and kind != ring.X11:
            note = _("Not available on Wayland desktops.")
        else:
            note = _("Not available on this desktop.")
        self.effect_note.setText(note)
        self.effect_note.setVisible(bool(note))

    def _effect_changed(self, name, key, value):
        updated = config.spotlight(self._action)
        updated[name][key] = value
        self._action = config.spotlight(updated)
        self._refresh()
        self._fill_effect()
        self.changed.emit(copy.deepcopy(self._action))

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
        if self._slot is None:  # picked late (a dialog's), after another control came to be edited: not for it
            return
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
