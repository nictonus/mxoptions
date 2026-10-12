"""Small reusable widgets: switch, cards, setting rows, sliders, segmented control, info notes."""

from PySide6.QtCore import Property, QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QButtonGroup, QComboBox, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from .. import device
from ..i18n import _
from . import theme


class Switch(QAbstractButton):
    """Animated on/off toggle."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._position = 0.0
        self._animation = QPropertyAnimation(self, b"position", self)
        self._animation.setDuration(140)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self):
        return QSize(44, 24)

    def _get_position(self):
        return self._position

    def _set_position(self, value):
        self._position = value
        self.update()

    position = Property(float, _get_position, _set_position)

    def _animate(self, checked):
        self._animation.stop()
        if not self.isVisible():
            self._set_position(1.0 if checked else 0.0)
            return
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def set_checked_silently(self, checked):
        blocked = self.blockSignals(True)
        self.setChecked(checked)
        self.blockSignals(blocked)
        self._animation.stop()
        self._set_position(1.0 if checked else 0.0)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height()).adjusted(1, 1, -1, -1)
        off, on = QColor(theme.TRACK), QColor(theme.ACCENT)
        t = self._position
        track = QColor(
            int(off.red() + (on.red() - off.red()) * t),
            int(off.green() + (on.green() - off.green()) * t),
            int(off.blue() + (on.blue() - off.blue()) * t),
        )
        if not self.isEnabled():
            track = QColor(theme.CARD_HOVER)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        diameter = rect.height() - 6
        x = rect.left() + 3 + (rect.width() - diameter - 6) * t
        painter.setBrush(QColor("#ffffff" if self.isEnabled() else theme.DISABLED))
        painter.drawEllipse(QRectF(x, rect.top() + 3, diameter, diameter))


class InfoBubble(QFrame):
    """The note of an InfoButton: a small card laid over the window, next to the button.

    It is a child of the window rather than a window of its own, so it needs nothing from the
    compositor and can have round corners and a shadow."""

    MARGIN = 12  # kept free between the note and the edge of the window

    def __init__(self, window):
        super().__init__(window)
        self.setObjectName("infoBubble")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 11)
        self.label = QLabel()
        self.label.setObjectName("infoText")
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 120))
        self.setGraphicsEffect(shadow)
        self.owner = None
        self.hide()

    @classmethod
    def of(cls, window):
        """The one note a window has (made when first needed)."""
        bubble = window.findChild(cls, options=Qt.FindChildOption.FindDirectChildrenOnly)
        return bubble or cls(window)

    def show_for(self, button, text):
        window = self.parentWidget()
        self.owner = button
        self.label.setText(text)
        width = min(int(340 * ui_scale()), window.width() - 2 * self.MARGIN)
        self.setFixedWidth(width)
        self.setFixedHeight(self.layout().heightForWidth(width))
        below = button.mapTo(window, QPoint(0, button.height() + 6))
        x = max(self.MARGIN, min(window.width() - width - self.MARGIN, below.x() - 14))
        y = below.y()
        if y + self.height() > window.height() - self.MARGIN:  # no room underneath: go above
            y = button.mapTo(window, QPoint(0, 0)).y() - self.height() - 6
        self.move(x, max(self.MARGIN, y))
        self.raise_()
        self.show()

    def hide_for(self, button):
        if self.owner is button:
            self.owner = None
            self.hide()


class InfoButton(QAbstractButton):
    """A small "i" in a circle. Pointing at it, or clicking it, shows a note about the thing it sits next to.

    Without a note it takes no room at all."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._text = ""
        self._inside = False
        self.clicked.connect(self._toggle)
        self.set_text(text)

    def text(self):
        return self._text

    def set_text(self, text):
        self._text = text or ""
        self.setAccessibleDescription(self._text)
        self.setVisible(bool(self._text))
        if self._inside and self._text:
            InfoBubble.of(self.window()).show_for(self, self._text)

    def _toggle(self):
        bubble = InfoBubble.of(self.window())
        if bubble.owner is self and bubble.isVisible():
            bubble.hide_for(self)
        else:
            bubble.show_for(self, self._text)

    def enterEvent(self, event):
        super().enterEvent(event)
        self._inside = True
        InfoBubble.of(self.window()).show_for(self, self._text)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._inside = False
        InfoBubble.of(self.window()).hide_for(self)
        self.update()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.window() is not self:
            InfoBubble.of(self.window()).hide_for(self)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(theme.ACCENT_INK if self._inside else theme.MUTED)
        if not self.isEnabled():
            color = QColor(theme.DISABLED)
        centre = QPointF(self.width() / 2.0, self.height() / 2.0)
        painter.setPen(QPen(color, 1.3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(centre, 7.0, 7.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(centre.x(), centre.y() - 3.4), 1.0, 1.0)             # the dot
        painter.drawRoundedRect(QRectF(centre.x() - 0.8, centre.y() - 1.4, 1.6, 5.2), 0.8, 0.8)  # the stem


def titled(label, info):
    """A caption with its info button right after it, as one line."""
    line = QHBoxLayout()
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(7)
    line.addWidget(label)
    line.addWidget(info, 0, Qt.AlignmentFlag.AlignVCenter)
    line.addStretch(1)
    return line


class Card(QFrame):
    """Rounded panel with an optional title; rows are separated by hairlines. ``hint`` explains the
    whole card from an info button next to the title."""

    def __init__(self, title=None, hint=None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(20, 16, 20, 16)
        self._layout.setSpacing(12)
        self._rows = []        # in the order they were added
        self._separators = {}  # row -> the hairline above it
        if title:
            label = QLabel(title)
            label.setObjectName("cardTitle")
            self.info = InfoButton(hint)
            self._layout.addLayout(titled(label, self.info))

    def add(self, widget):
        if self._rows:
            line = QFrame()
            line.setObjectName("separator")
            self._layout.addWidget(line)
            self._separators[widget] = line
        self._layout.addWidget(widget)
        self._rows.append(widget)
        return widget

    def set_row_visible(self, widget, visible):
        """Show or hide a row added with add(), together with the hairline above it. The first row
        that is showing never has one."""
        widget.setVisible(visible)
        first = True
        for row in self._rows:
            shown = not row.isHidden()
            if row in self._separators:
                self._separators[row].setVisible(shown and not first)
            first = first and not shown

    def add_plain(self, widget):
        self._layout.addWidget(widget)
        return widget


class Row(QWidget):
    """A title on the left, a control on the right. ``hint`` explains the setting from an info button
    next to the title; ``note`` is a line of its own under the title, for a status that has to be seen."""

    def __init__(self, title, hint=None, control=None, note=None, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("rowTitle")
        self.info = InfoButton(hint)
        text.addLayout(titled(self.title, self.info))
        self.note = None
        if note is not None:
            self.note = QLabel(note)
            self.note.setObjectName("rowHint")
            self.note.setWordWrap(True)
            text.addWidget(self.note)
        layout.addLayout(text, 1)
        self.control = control
        if control is not None:
            layout.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def set_hint(self, text):
        self.info.set_text(text)


class SliderRow(QWidget):
    """Title, live value and a full-width slider. Emits the real value (step applied)."""

    changed = Signal(float)
    released = Signal(float)

    def __init__(self, title, minimum, maximum, step=1, fmt="{:.0f}", hint=None, low_label=None, high_label=None, parent=None):
        super().__init__(parent)
        self._step = step
        self._fmt = fmt
        self._minimum = minimum
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(6)
        self.title = QLabel(title)
        self.title.setObjectName("rowTitle")
        self.info = InfoButton(hint)
        head = titled(self.title, self.info)
        self.value_label = QLabel()
        self.value_label.setObjectName("valueLabel")
        head.addWidget(self.value_label)
        layout.addLayout(head)
        slider_line = QHBoxLayout()
        slider_line.setSpacing(10)
        if low_label:
            low = QLabel(low_label)
            low.setObjectName("rowHint")
            slider_line.addWidget(low)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, max(1, round((maximum - minimum) / step)))
        self.slider.setPageStep(max(1, self.slider.maximum() // 10))
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        slider_line.addWidget(self.slider, 1)
        if high_label:
            high = QLabel(high_label)
            high.setObjectName("rowHint")
            slider_line.addWidget(high)
        layout.addLayout(slider_line)
        self.slider.valueChanged.connect(self._on_changed)
        self.slider.sliderReleased.connect(lambda: self.released.emit(self.value()))
        self._refresh_label()

    def value(self):
        return self._minimum + self.slider.value() * self._step

    def set_value(self, value):
        blocked = self.slider.blockSignals(True)
        self.slider.setValue(round((value - self._minimum) / self._step))
        self.slider.blockSignals(blocked)
        self._refresh_label()

    def set_range(self, minimum, maximum, step):
        value = self.value()
        self._minimum, self._step = minimum, step
        blocked = self.slider.blockSignals(True)
        self.slider.setRange(0, max(1, round((maximum - minimum) / step)))
        self.slider.blockSignals(blocked)
        self.set_value(max(minimum, min(maximum, value)))

    def _on_changed(self, _raw):
        self._refresh_label()
        self.changed.emit(self.value())

    def _refresh_label(self):
        self.value_label.setText(self._fmt.format(self.value()))


class Segmented(QFrame):
    """Exclusive row of options. ``options`` is [(value, label)]."""

    selected = Signal(object)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.setObjectName("segment")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons = {}
        for value, label in options:
            button = QPushButton(label)
            button.setObjectName("segmentButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, v=value: self.selected.emit(v))
            self._group.addButton(button)
            self._buttons[value] = button
            layout.addWidget(button)

    def set_option_enabled(self, value, enabled):
        if value in self._buttons:
            self._buttons[value].setEnabled(enabled)

    def set_value(self, value):
        button = self._buttons.get(value)
        if button:
            button.setChecked(True)
        else:
            self._group.setExclusive(False)
            for other in self._buttons.values():
                other.setChecked(False)
            self._group.setExclusive(True)


def hint_label(text):
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


class ColorSwatch(QAbstractButton):
    """Round colour button. An empty colour means "automatic" and is drawn struck through."""

    def __init__(self, color="", size=26, parent=None):
        super().__init__(parent)
        self.color = color
        self._size = size
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        return QSize(self._size, self._size)

    def set_color(self, color):
        self.color = color
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height()).adjusted(1.5, 1.5, -1.5, -1.5)
        if self.isChecked():
            painter.setPen(QPen(QColor(theme.TEXT), 1.6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect)
        inner = rect.adjusted(3.5, 3.5, -3.5, -3.5)
        if self.color:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self.color))
            painter.drawEllipse(inner)
        else:
            painter.setPen(QPen(QColor(theme.MUTED), 1.3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(inner)
            painter.drawLine(inner.bottomLeft() + QPointF(3, -3), inner.topRight() + QPointF(-3, 3))


class SwatchRow(QWidget):
    """Exclusive row of colour swatches. Emits the chosen colour ("" for automatic)."""

    selected = Signal(str)

    def __init__(self, colors, allow_auto=False, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._swatches = []
        for color in ([""] if allow_auto else []) + list(colors):
            self._add(color)
        self.custom = self._add("")  # shows a colour that is not one of the presets
        self.custom.hide()
        layout.addStretch(1)

    def _add(self, color):
        swatch = ColorSwatch(color)
        swatch.clicked.connect(lambda _checked=False, s=swatch: self.selected.emit(s.color))
        self._group.addButton(swatch)
        self._swatches.append(swatch)
        self.layout().addWidget(swatch)
        return swatch

    def set_value(self, color):
        color = (color or "").lower()
        for swatch in self._swatches:
            if swatch is not self.custom and swatch.color.lower() == color:
                swatch.setChecked(True)
                self.custom.hide()
                return
        self.custom.set_color(color)
        self.custom.show()
        self.custom.setChecked(True)


def ui_scale():
    """How much wider than the reference font the application font is (1.0 .. 1.3).

    Fixed-width panels multiply by this so a wide system font does not clip their text."""
    width = QFontMetricsF(QApplication.font()).averageCharWidth()
    return max(1.0, min(1.3, width / 7.4))


class ElidedLabel(QLabel):
    """Label that shortens its text with an ellipsis instead of clipping it."""

    def __init__(self, text="", mode=Qt.TextElideMode.ElideRight, parent=None):
        super().__init__(parent)
        self._full = text
        self._mode = mode
        self._apply()

    def setText(self, text):
        self._full = text
        self.setToolTip(text)
        self.updateGeometry()
        self._apply()

    def full_text(self):
        return self._full

    def _apply(self):
        super().setText(self.fontMetrics().elidedText(self._full, self._mode, max(12, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 2, super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(24, super().minimumSizeHint().height())


class ElidedButton(QPushButton):
    """Left-aligned button whose caption is shortened to fit; the full text is in the tooltip."""

    def __init__(self, text="", reserved=44, parent=None):
        super().__init__(parent)
        self._full = text
        self._reserved = reserved  # room taken by padding and the icon
        # A push button normally refuses to be narrower than its caption; this one shortens it instead.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._apply()

    def setText(self, text):
        self._full = text
        self._apply()

    def text(self):
        return self._full

    def _apply(self):
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(20, self.width() - self._reserved))
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()

    def minimumSizeHint(self):
        return QSize(60, super().minimumSizeHint().height())


class PatternRow(QWidget):
    """A named moment (of a gesture, of the wheel), the vibration pattern for it and a button to feel that pattern."""

    changed = Signal(object)  # waveform id, or None for no vibration
    played = Signal(int)

    def __init__(self, title, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.title = ElidedLabel(title)
        self.title.setObjectName("rowTitle")
        layout.addWidget(self.title, 1)
        self.combo = QComboBox()
        self.combo.setFixedWidth(int(196 * ui_scale()))
        self.combo.activated.connect(lambda index: self.changed.emit(self.combo.itemData(index)))
        layout.addWidget(self.combo)
        self.play = QPushButton("▶")
        self.play.setObjectName("tool")
        self.play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play.setToolTip(_("Play"))
        self.play.clicked.connect(lambda: self.played.emit(self.combo.currentData()))
        layout.addWidget(self.play)
        self._waveforms = None
        self.set_waveforms(list(device.WAVEFORMS))

    def set_waveforms(self, waveforms):
        """The patterns this mouse can play."""
        if waveforms == self._waveforms:
            return
        current = self.combo.currentData()
        self._waveforms = list(waveforms)
        self.combo.clear()
        self.combo.addItem(_("No vibration"), None)
        for waveform in self._waveforms:
            self.combo.addItem(_(device.WAVEFORMS[waveform]), waveform)
        self.set_value(current)

    def set_value(self, waveform):
        self.combo.setCurrentIndex(max(0, self.combo.findData(waveform)))
        self.play.setEnabled(self.combo.currentData() is not None)
