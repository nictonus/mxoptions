"""The gestures page: how a swipe is read, with a live picture of the zones and of the last movement."""

import copy
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import actions, config, gestures
from ..i18n import _
from . import theme
from .pages import BUTTON_TITLES, ScrollPage
from .widgets import Card, PatternRow, Row, Segmented, SliderRow, Switch, hint_label, ui_scale

# Qt angle (degrees, counter-clockwise from 3 o'clock) of each direction, and its arrow
_AXES = {"right": (0.0, "→"), "up": (90.0, "↑"), "left": (180.0, "←"), "down": (270.0, "↓")}
DIRECTION_NAMES = {"up": "Swipe up", "down": "Swipe down", "left": "Swipe left", "right": "Swipe right"}
MIN_SPAN = 24.0  # mm from the centre to the rim, at least
# The moments of a gesture that can vibrate, in the order they happen: (cue, label)
CUE_LABELS = [
    ("armed", "The movement became a swipe"), ("turn", "The direction changed"), ("swipe", "Swipe recognised"),
    ("repeat", "Every repeat"), ("tap", "Tap"), ("reject", "Not accepted"),
]
CUE_PRESET_LABELS = [("quiet", "Quiet"), ("normal", "Normal"), ("rich", "Rich")]


def gesture_button(cfg):
    """Key of the button that does gestures (the thumb buttons first), or None."""
    keys = list(config.GESTURE_BUTTONS) + [key for key in config.BUTTONS if key not in config.GESTURE_BUTTONS]
    return next((key for key in keys if cfg["buttons"][key].get("type") == "gestures"), None)


class GesturePad(QWidget):
    """Compass of the gesture zones, drawn to scale, with the latest movement on top."""

    def __init__(self, parent=None):
        super().__init__(parent)
        side = int(310 * ui_scale())
        self.setMinimumSize(side, side)
        self._settings = copy.deepcopy(config.DEFAULTS["gestures"])
        self._labels = {}
        self._event = None

    def set_settings(self, settings, labels):
        """``labels`` maps "tap" and the four directions to what they run."""
        self._settings, self._labels = dict(settings), dict(labels)
        self.update()

    def show_gesture(self, event):
        self._event = event
        self.update()

    def _half_width(self, direction):
        """Degrees either side of its axis that a direction covers."""
        boundary = gestures.split(self._settings["bias"])
        dead = self._settings["dead"]
        return (boundary - dead) if direction in ("left", "right") else (90.0 - boundary - dead)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        settings = self._settings
        radius = min(self.width(), self.height()) / 2.0 - 6.0
        centre = QPointF(self.width() / 2.0, self.height() / 2.0)
        span = max(MIN_SPAN, settings["far_mm"] * 1.4)
        scale = radius / span  # pixels per millimetre
        tap = max(5.0, settings["min_mm"] * scale)
        event = self._event or {}
        lit = set(event.get("fired") or ())
        result = event.get("result")
        if result in _AXES:
            lit.add(result)

        def circle(r):
            return QRectF(centre.x() - r, centre.y() - r, 2 * r, 2 * r)

        # the rim; what stays visible of it between the zones is where swipes are ignored
        painter.setPen(QPen(QColor(theme.BORDER), 1.0))
        painter.setBrush(QColor(theme.mix(theme.CARD, theme.BG, 0.7)))
        painter.drawEllipse(circle(radius))

        gap = 1.2  # degrees shaved off every zone so neighbours do not touch
        for direction, (axis, arrow) in _AXES.items():
            half = self._half_width(direction) - gap
            if half <= 0.5:
                continue
            wedge = QPainterPath()
            wedge.arcMoveTo(circle(radius - 1.0), axis - half)
            wedge.arcTo(circle(radius - 1.0), axis - half, 2 * half)
            wedge.arcTo(circle(tap + 4.0), axis + half, -2 * half)
            wedge.closeSubpath()
            active = direction in lit
            fill = QColor(theme.ACCENT_INK if active else theme.mix(theme.CARD, theme.TEXT, 0.07 if theme.DARK else 0.05))
            if active:
                fill.setAlpha(95)
            painter.setPen(QPen(QColor(theme.ACCENT_INK if active else theme.BORDER), 1.2 if active else 1.0))
            painter.setBrush(fill)
            painter.drawPath(wedge)

        # how far a swipe has to go to fire at once, and where it repeats
        if settings["trigger"] != "release":
            dashed = QColor(theme.MUTED)
            dashed.setAlpha(150)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(dashed, 1.0, Qt.PenStyle.DashLine))
            far = settings["far_mm"] * scale
            painter.drawEllipse(circle(far))
            if settings["trigger"] == "repeat":
                faint = QColor(theme.MUTED)
                faint.setAlpha(70)
                painter.setPen(QPen(faint, 1.0, Qt.PenStyle.DotLine))
                step = max(3.0, settings["step_mm"] * scale)
                ring = far + step
                while ring < radius - 2.0:
                    painter.drawEllipse(circle(ring))
                    ring += step

        # the press zone in the middle
        tapped = result == "tap"
        fill = QColor(theme.ACCENT_INK if tapped else theme.mix(theme.CARD, theme.TEXT, 0.16))
        painter.setPen(QPen(QColor(theme.ACCENT_INK if tapped else theme.MUTED), 1.2))
        painter.setBrush(fill)
        painter.drawEllipse(circle(tap))

        # what each direction does
        font = painter.font()
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1.5))
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        for direction, (axis, arrow) in _AXES.items():
            if self._half_width(direction) <= 6.0:
                continue
            angle = math.radians(axis)
            horizontal = direction in ("left", "right")
            distance = radius * (0.60 if horizontal else 0.76)
            spot = QPointF(centre.x() + math.cos(angle) * distance, centre.y() - math.sin(angle) * distance)
            width = radius * (0.62 if horizontal else 0.86)
            box = QRectF(spot.x() - width / 2, spot.y() - metrics.height() * 1.6, width, metrics.height() * 3.2)
            active = direction in lit
            painter.setPen(QColor(theme.TEXT if active else theme.MUTED))
            label = self._labels.get(direction, "")
            text = f"{arrow}\n{metrics.elidedText(label, Qt.TextElideMode.ElideRight, width * 1.9)}"
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)

        # the movement itself
        points = event.get("points") or []
        if len(points) > 1:
            painter.save()
            clip = QPainterPath()
            clip.addEllipse(circle(radius))
            painter.setClipPath(clip)
            line = QPolygonF([QPointF(centre.x() + x * scale, centre.y() + y * scale) for x, y in points])
            failed = result in ("cancelled", "diagonal")
            color = QColor(theme.WARN if failed else theme.ACCENT_INK)
            pen = QPen(color, 2.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(line)
            painter.setPen(QPen(QColor(theme.BG), 1.5))
            painter.setBrush(color)
            painter.drawEllipse(line.last(), 5.0, 5.0)
            painter.restore()


class GesturesPage(ScrollPage):
    edit_actions = Signal(str)  # key of the button whose gesture actions should be opened

    def __init__(self, store, parent=None):
        super().__init__(_("Gestures"), _("Hold the gesture button and move the mouse. Here you set how that movement is read."), parent)
        self.store = store

        columns = QHBoxLayout()
        columns.setSpacing(16)

        # live view
        card = Card(_("Try it"))
        self.pad = GesturePad()
        card.add_plain(self.pad)
        self.caption = QLabel()
        self.caption.setObjectName("rowTitle")
        self.caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.caption.setWordWrap(True)
        card.add_plain(self.caption)
        self.practice = Switch()
        self.practice.set_checked_silently(True)
        self.practice.toggled.connect(self._practice_changed)
        card.add(Row(_("Practice mode"), _("While this page is open, gestures are only drawn here and do nothing."), self.practice))
        self.actions_button = QPushButton(_("Change what the gestures do…"))
        self.actions_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.actions_button.clicked.connect(self._edit_actions)
        card.add_plain(self.actions_button)
        left = QVBoxLayout()
        left.addWidget(card)
        left.addStretch(1)
        columns.addLayout(left, 5)

        # recognition
        right = QVBoxLayout()
        right.setSpacing(16)
        card = Card(_("Recognition"))
        self.trigger = Segmented([("release", _("On release")), ("auto", _("At once")), ("repeat", _("Repeating"))])
        self.trigger.selected.connect(lambda value: self._set("trigger", value))
        self.trigger_row = card.add(Row(_("When a swipe fires"), None, self.trigger))
        self.min_mm = card.add(SliderRow(
            _("Shortest swipe"), 1.0, 12.0, 0.5, _("{:.1f} mm"), hint=_("Move less than this and it counts as a press."),
            low_label=_("Sensitive"), high_label=_("Calm"),
        ))
        self.min_mm.changed.connect(lambda value: self._set("min_mm", round(value, 1)))
        self.far_mm = card.add(SliderRow(
            _("Swipe that fires at once"), 6.0, 45.0, 1.0, _("{:.0f} mm"),
            hint=_("A clear swipe this long runs without waiting for you to let go. The dashed circle."),
        ))
        self.far_mm.changed.connect(lambda value: self._set("far_mm", round(value, 1)))
        self.step_mm = card.add(SliderRow(
            _("Repeat every"), 2.0, 30.0, 1.0, _("{:.0f} mm"), hint=_("Keep moving and the action runs again after this much travel."),
        ))
        self.step_mm.changed.connect(lambda value: self._set("step_mm", round(value, 1)))
        self.recognition = card
        self.dead = card.add(SliderRow(
            _("Diagonal dead zone"), 0, 30, 1, "{:.0f}°",
            hint=_("A swipe this close to a diagonal is ignored instead of guessed."),
            low_label=_("Off"), high_label=_("Strict"),
        ))
        self.dead.changed.connect(lambda value: self._set("dead", int(value)))
        self.bias = card.add(SliderRow(
            _("Sideways or up and down"), -25, 25, 1, "{:+.0f}°",
            hint=_("Gives one pair of directions more room, if swipes keep landing in the other."),
            low_label=_("Up / down"), high_label=_("Sideways"),
        ))
        self.bias.changed.connect(lambda value: self._set("bias", int(value)))
        reset = QPushButton(_("Restore defaults"))
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(lambda: self.store.set_section("gestures", config.DEFAULTS["gestures"]))
        card.add(Row(_("Reset the gesture settings"), None, reset))
        right.addWidget(card)

        card = Card(_("Button"))
        self.button = Segmented([(key, _(BUTTON_TITLES[key])) for key in config.GESTURE_BUTTONS])
        self.button.selected.connect(lambda key: self.store.assign_role("gestures", key))
        card.add(Row(_("Gestures are on"), _("The Actions Ring moves to the other thumb button."), self.button))
        right.addWidget(card)
        right.addStretch(1)
        columns.addLayout(right, 6)
        self.body.addLayout(columns)

        # what the hand feels
        card = Card(_("Haptic feedback"))
        self.haptic = Switch()
        self.haptic.toggled.connect(lambda on: self._set("haptic", on))
        card.add(Row(
            _("Vibration during gestures"),
            _("Choose what you feel at each moment of a gesture. ▶ plays the pattern on the mouse."), self.haptic,
        ))
        self.haptics_off = card.add_plain(hint_label(_("Feedback for actions is switched off on the Haptics page, so nothing will be felt.")))
        self.cue_preset = Segmented([(key, _(label)) for key, label in CUE_PRESET_LABELS])
        self.cue_preset.selected.connect(self._cue_preset)
        self.preset_row = card.add(Row(_("Ready-made set"), None, self.cue_preset))
        self.cues = {}
        self.cue_grid = QWidget()
        grid = QGridLayout(self.cue_grid)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(34)
        grid.setVerticalSpacing(10)
        for index, (cue, label) in enumerate(CUE_LABELS):
            grid.addWidget(self._cue_row(cue, _(label)), index // 2, index % 2)
        card.add(self.cue_grid)
        self.per_direction = Switch()
        self.per_direction.toggled.connect(lambda on: self._set("per_direction", on))
        self.per_direction_row = card.add(Row(
            _("A pattern of its own for each direction"), _("Tell the directions apart without looking."), self.per_direction,
        ))
        self.direction_grid = QWidget()
        grid = QGridLayout(self.direction_grid)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(34)
        grid.setVerticalSpacing(10)
        for index, direction in enumerate(("up", "left", "down", "right")):
            grid.addWidget(self._cue_row(direction, f"{_AXES[direction][1]}  {_(DIRECTION_NAMES[direction])}"), index // 2, index % 2)
        card.add_plain(self.direction_grid)
        self.haptics_card = card
        self.body.addWidget(card)
        self.finish()

        store.changed.connect(self._refresh)
        store.state_changed.connect(self._state)
        store.gesture_changed.connect(self._gesture)
        self._refresh()
        self._gesture(None)

    def _set(self, key, value):
        self.store.set("gestures", key, value)

    # -- vibration patterns ---------------------------------------------------

    def _cue_row(self, cue, title):
        row = PatternRow(title)
        row.changed.connect(lambda waveform, name=cue: self._set_cue(name, waveform))
        row.played.connect(self.store.engine.play_haptic)
        self.cues[cue] = row
        return row

    def _set_cue(self, cue, waveform):
        self._set("cues", dict(self.store.cfg["gestures"]["cues"], **{cue: waveform}))
        if waveform is not None:
            self.store.engine.play_haptic(waveform)  # feel what was just picked

    def _cue_preset(self, key):
        self._set("cues", dict(self.store.cfg["gestures"]["cues"], **gestures.CUE_PRESETS[key]))
        self._set("per_direction", False)

    def _state(self, state):
        caps = state.get("caps")
        if caps:
            self.haptics_card.setVisible(bool(caps["waveforms"]))
            for row in self.cues.values():
                row.set_waveforms(caps["waveforms"])
            self._refresh()

    def _edit_actions(self):
        key = gesture_button(self.store.cfg)
        if key:
            self.edit_actions.emit(key)

    # -- practice mode: only while the page is on screen ---------------------------

    def _practice_changed(self, enabled):
        self.store.engine.set_gesture_practice(enabled and self.isVisible())

    def showEvent(self, event):
        super().showEvent(event)
        self.store.engine.set_gesture_practice(self.practice.isChecked())

    def hideEvent(self, event):
        super().hideEvent(event)
        self.store.engine.set_gesture_practice(False)

    # -- display ------------------------------------------------------------

    def _refresh(self):
        cfg = self.store.cfg
        settings = cfg["gestures"]
        self.trigger.set_value(settings["trigger"])
        self.trigger_row.set_hint({
            "release": _("The swipe is judged as a whole when you let go of the button. The most accurate."),
            "auto": _("A long, clear swipe runs straight away; anything shorter or less clear is judged when you let go."),
            "repeat": _("Like \"At once\", and the action runs again as you keep moving: handy for volume or switching desktops."),
        }[settings["trigger"]])
        self.min_mm.set_value(settings["min_mm"])
        self.far_mm.set_value(settings["far_mm"])
        self.far_mm.setEnabled(settings["trigger"] != "release")
        self.step_mm.set_value(settings["step_mm"])
        self.recognition.set_row_visible(self.step_mm, settings["trigger"] == "repeat")
        self.dead.set_value(settings["dead"])
        self.bias.set_value(settings["bias"])
        self.haptic.set_checked_silently(settings["haptic"])
        haptics = cfg["haptics"]
        self.haptics_off.setVisible(not (haptics["enabled"] and haptics["feedback"]))
        cues = settings["cues"]
        self.cue_preset.set_value(next(
            (key for key, preset in gestures.CUE_PRESETS.items()
             if not settings["per_direction"] and all(cues[name] == preset[name] for name in gestures.CUES)), None))
        for name, row in self.cues.items():
            row.set_value(cues[name])
        self.per_direction.set_checked_silently(settings["per_direction"])
        self.direction_grid.setVisible(settings["per_direction"])
        self.cues["swipe"].setEnabled(not settings["per_direction"])
        for widget in (self.preset_row, self.cue_grid, self.per_direction_row, self.direction_grid):
            widget.setEnabled(settings["haptic"])

        key = gesture_button(cfg)
        self.button.set_value(key)
        action = cfg["buttons"][key] if key else {}
        labels = {name: actions.describe(action.get(name)) for name in ("tap",) + gestures.DIRECTIONS} if key else {}
        self.pad.set_settings(settings, labels)
        self.actions_button.setEnabled(key is not None)
        self._tap_label = labels.get("tap", "")
        self._has_button = key is not None
        if self.pad._event is None:
            self._gesture(None)

    def _gesture(self, event):
        self.pad.show_gesture(event)
        if not self._has_button:
            self.caption.setText(_("No button does gestures right now."))
            return
        if event is None:
            self.caption.setText(_("Hold the gesture button and move the mouse."))
            return
        points = event["points"]
        x, y = points[-1]
        length = math.hypot(x, y)
        result = event.get("result")
        fired = event.get("fired") or []
        if result is None:  # still held
            names = " ".join(_AXES[d][1] for d in fired)
            self.caption.setText((names + "  " if names else "") + _("{:.0f} mm").format(length))
        elif result == "tap":
            self.caption.setText(_("Tap") + (f"  ·  {self._tap_label}" if self._tap_label else ""))
        elif result == "cancelled":
            self.caption.setText(_("Cancelled: moved away and came back"))
        elif result == "diagonal":
            self.caption.setText(_("Ignored: too diagonal ({:.0f}° off)").format(self._off_axis(x, y)))
        elif result == "repeat":
            self.caption.setText(" ".join(_AXES[d][1] for d in fired))
        else:
            self.caption.setText("{}  {}  ·  {}  ·  {}".format(
                _AXES[result][1], _(DIRECTION_NAMES[result]), _("{:.0f} mm").format(length),
                _("{:.0f}° off").format(self._off_axis(x, y))))

    @staticmethod
    def _off_axis(x, y):
        """Degrees between the movement and the nearest axis."""
        theta = gestures.angle(x, y)
        return min(theta, 90.0 - theta)
