"""The mouse (its product picture or a 3D model) with a clickable callout per configurable button."""

import logging
import threading

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QButtonGroup, QPushButton, QVBoxLayout, QWidget

from . import mouse_model, mouse_photo, mouse_render, theme
from .widgets import ElidedLabel, ui_scale

log = logging.getLogger(__name__)

CHIP_W, CHIP_H = 184, 56  # width at the reference font; see chip_width()
CHIP_GAP = 9
MODEL_GAP = 34  # between the callout column and the model
# Callouts top to bottom, roughly in the order the controls appear on the model.
ORDER = ("left", "right", "middle", "smartshift", "gesture", "forward", "back", "panel")
FIXED = mouse_photo.MAIN_BUTTONS  # always shown: they do not depend on what the mouse lets us reassign


def chip_width():
    return int(CHIP_W * ui_scale())


class Chip(QPushButton):
    hovered = Signal(bool)

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("chip")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(chip_width(), CHIP_H)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 7, 14, 7)
        layout.setSpacing(1)
        self.title = ElidedLabel(title)
        self.title.setObjectName("chipTitle")
        self.value = ElidedLabel()
        self.value.setObjectName("chipValue")
        for label in (self.title, self.value):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(label)

    def set_value(self, text):
        self.value.setText(text)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.hovered.emit(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.hovered.emit(False)


class MouseView(QWidget):
    selected = Signal(str)
    _rendered = Signal(object, object, object, object)  # key, image, top-left, shadow (from the render thread)

    def __init__(self, titles, parent=None):
        """``titles`` maps button key -> caption; only those keys get a callout."""
        super().__init__(parent)
        self.setMinimumSize(chip_width() + MODEL_GAP + 300, len(ORDER) * (CHIP_H + CHIP_GAP))
        self.setMouseTracking(True)
        self._selected = None
        self._pressed = frozenset()
        self._hover = None
        self._style = "photo" if mouse_photo.available() else mouse_model.DEFAULT_STYLE
        self._color = mouse_photo.COLORS[0]
        # The view moves from one camera angle to another as the selection changes.
        self._from_view = self._to_view = mouse_model.VIEWS[None]
        self._progress = 1.0
        self._turn = QVariantAnimation(self)
        self._turn.setStartValue(0.0)
        self._turn.setEndValue(1.0)
        self._turn.setDuration(420)
        self._turn.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._turn.valueChanged.connect(self._turned)
        # Studio renders are slow, so they are made once per camera angle on a worker thread.
        self._renders = {}   # key -> (image, top-left, shadow)
        self._wanted = []    # keys still to render, most urgent first
        self._rendering = False
        self._render_broken = False
        self._rendered.connect(self._store_render)
        self._settle = QTimer(self, singleShot=True, interval=160)
        self._settle.timeout.connect(self._prerender)
        self._chips = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for key in ORDER:
            if key not in titles:
                continue
            chip = Chip(titles[key], self)
            chip.clicked.connect(lambda _checked=False, k=key: self.select(k))
            chip.hovered.connect(lambda inside, k=key: self._set_hover(k if inside else None))
            self._group.addButton(chip)
            self._chips[key] = chip

    def keys(self):
        return list(self._chips)

    def _shown(self):
        return [key for key, chip in self._chips.items() if not chip.isHidden()]

    def set_visible_keys(self, keys):
        for key, chip in self._chips.items():
            chip.setVisible(key in keys or key in FIXED)
        self._layout_chips()
        self.update()

    def set_value(self, key, text):
        if key in self._chips:
            self._chips[key].set_value(text)

    def set_style(self, style, color=None):
        """``style`` is "photo" or one of the 3D styles; ``color`` picks the body colour of the photo."""
        if style == "photo" and not mouse_photo.available():
            style = mouse_model.DEFAULT_STYLE
        color = color if color in mouse_photo.COLORS else self._color
        if (style == "photo" or style in mouse_model.STYLES) and (style, color) != (self._style, self._color):
            self._style, self._color = style, color
            self._settle.start()
            self.update()

    def _set_hover(self, key):
        if key != self._hover:
            self._hover = key
            self.update()

    def set_pressed(self, keys):
        """Buttons physically held down right now; they light up on the model."""
        keys = frozenset(keys)
        if keys != self._pressed:
            self._pressed = keys
            self.update()

    def select(self, key):
        if key not in self._chips:
            return
        self._chips[key].setChecked(True)
        if key != self._selected:
            self._selected = key
            target = mouse_model.VIEWS[key]
            if target != self._to_view:
                self._from_view = self._current_view()
                self._to_view = target
                self._turn.stop()
                if self.isVisible():
                    self._progress = 0.0
                    self._turn.start()
                else:
                    self._progress = 1.0
            self.update()
        self.selected.emit(key)

    def _turned(self, value):
        self._progress = float(value)
        self.update()

    def _current_view(self):
        t = self._progress
        return tuple(a + (b - a) * t for a, b in zip(self._from_view, self._to_view))

    # -- geometry -----------------------------------------------------------

    def _layout_chips(self):
        shown = self._shown()
        total = len(shown) * CHIP_H + max(0, len(shown) - 1) * CHIP_GAP
        y = (self.height() - total) / 2
        for key in shown:
            self._chips[key].move(2, int(y))
            y += CHIP_H + CHIP_GAP

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_chips()
        self._settle.start()  # re-render once the size stops changing

    def showEvent(self, event):
        super().showEvent(event)
        self._settle.start()

    def _stage(self):
        """Everything right of the callouts: the model and its glow stay inside."""
        left = chip_width() + 10
        return QRectF(left, 0, self.width() - left, self.height())

    def _photo_rect(self):
        left = chip_width() + MODEL_GAP
        area = QRectF(left, 0, self.width() - left, self.height())
        return mouse_photo.fit(area.adjusted(area.width() * 0.07, area.height() * 0.045, -area.width() * 0.07, -area.height() * 0.045))

    def _control_at(self, pos):
        if self._style != "photo":
            return None
        return mouse_photo.control_at(self._photo_rect(), pos, self._shown())

    def mouseMoveEvent(self, event):
        key = self._control_at(event.position())
        self.setCursor(Qt.CursorShape.PointingHandCursor if key else Qt.CursorShape.ArrowCursor)
        self._set_hover(key)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._set_hover(None)

    def mousePressEvent(self, event):
        key = self._control_at(event.position())
        if key and event.button() == Qt.MouseButton.LeftButton:
            self.select(key)

    def _camera(self, view):
        left = chip_width() + MODEL_GAP
        width = self.width() - left - 8
        # 150 x 150 mm comfortably contains the model from every camera angle used
        scale = max(1.5, min(4.2, width / 150.0, (self.height() - 16) / 150.0))
        origin = QPointF(left + width / 2, self.height() / 2 + 4 * scale)
        return mouse_model.Camera(view[0], view[1], scale, origin)

    # -- studio renders -------------------------------------------------------

    def _studio(self):
        return self._style == "studio" and mouse_render.available() and not self._render_broken

    def _key(self, view):
        return (view, self.width(), self.height(), theme.REVISION)

    def _prerender(self):
        """Queue every camera angle (the current one first) at the present size and theme."""
        if not self._studio() or not self.isVisible():
            return
        current = {self._key(view) for view in mouse_model.VIEWS.values()}
        self._renders = {key: value for key, value in self._renders.items() if key in current}
        views = [self._to_view] + [v for v in dict.fromkeys(mouse_model.VIEWS.values()) if v != self._to_view]
        self._wanted = [self._key(view) for view in views if self._key(view) not in self._renders]
        self._next_render()

    def _next_render(self):
        if self._rendering or not self._wanted:
            return
        key = self._wanted.pop(0)
        camera = self._camera(key[0])
        width, height = self.width(), self.height()
        self._rendering = True

        def job():
            try:
                image, corner = mouse_render.render(camera)
                shadow = mouse_render.shadow(camera, width, height)
            except Exception:  # never leave the queue stuck
                log.exception("studio render failed; using the plain model instead")
                image = corner = shadow = None
            self._rendered.emit(key, image, corner, shadow)

        threading.Thread(target=job, name="mouse-render", daemon=True).start()

    def _store_render(self, key, image, corner, shadow):
        self._rendering = False
        if image is None:
            self._render_broken = True  # do not retry on every repaint
            self._wanted = []
            self.update()
            return
        if key == self._key(key[0]):  # still the right size and theme
            self._renders[key] = (image, corner, shadow)
            self.update()
        self._next_render()

    # -- painting -----------------------------------------------------------

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        shown = self._shown()
        t = self._progress
        photo = self._style == "photo"
        target = self._camera(self._to_view)
        ready = self._studio() and self._key(self._to_view) in self._renders

        if photo:
            rect = self._photo_rect()
            mouse_photo.paint(painter, rect, self._color, self._selected, self._pressed, shown, self._hover, self._stage())
            anchors = {key: mouse_photo.anchor(rect, key) for key in shown}
        elif ready:
            # Dissolve between the finished renders of the two camera angles.
            size = 118.0 * target.scale
            mouse_photo.paint_stage(
                painter, QRectF(target.origin.x() - size / 2, target.origin.y() - size / 2, size, size), self._stage())
            previous = self._renders.get(self._key(self._from_view)) if t < 1.0 else None
            for render, opacity in ((previous, 1.0 - t), (self._renders[self._key(self._to_view)], t if previous else 1.0)):
                if render is None:
                    continue
                image, corner, shadow = render
                painter.setOpacity(opacity)
                painter.drawImage(0, 0, shadow)
                painter.drawImage(corner, image)
            painter.setOpacity(t if previous else 1.0)
            mouse_render.paint_overlays(painter, target, self._selected, self._pressed, shown)
            painter.setOpacity(1.0)
            start_camera = self._camera(self._from_view) if previous else target
            anchors = {}
            for key in shown:
                a, b = mouse_model.anchor(start_camera, key, "studio"), mouse_model.anchor(target, key, "studio")
                anchors[key] = QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t)
        else:
            if self._studio():
                self._settle.start()  # the render for this angle is still to come
            camera = self._camera(self._current_view())
            mouse_model.paint(painter, camera, self._style, self._selected, self._pressed, shown)
            anchors = {key: mouse_model.anchor(camera, key, self._style) for key in shown}

        for key in shown:
            chip = self._chips[key]
            active = key == self._selected
            held = key in self._pressed
            if photo and not (active or held or key == self._hover):
                continue  # the picture stays clean: only the control in question gets a line
            rect = QRectF(chip.geometry())
            start = QPointF(rect.right(), rect.center().y())
            end = anchors[key]
            elbow = QPointF(start.x() + 18, start.y())
            color = QColor(theme.mix(theme.ACCENT_INK, "#ffffff", 0.2) if held else theme.ACCENT_INK if active else theme.MUTED)
            if not (active or held):
                color.setAlpha(120)
            painter.setPen(QPen(color, 2.4 if held else 1.7 if active else 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath(start)
            path.lineTo(elbow)
            path.lineTo(end)
            painter.drawPath(path)
            painter.setPen(QPen(QColor(theme.BG), 1.2))
            painter.setBrush(color if (active or held) else QColor(theme.MUTED))
            radius = 5.2 if held else 4.2 if active else 3.2
            painter.drawEllipse(end, radius, radius)
            if held:  # frame the callout of the button being pressed
                painter.setPen(QPen(color, 2.0))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), 14, 14)
