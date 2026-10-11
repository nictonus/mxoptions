"""Actions Ring: a menu around the cursor. Shared painter, settings preview and the overlay.

A bubble is ``{"label", "icon", "color"}`` plus one of
    "action"    what it runs,
    "children"  a list of bubbles (a folder that opens its own ring),
    "scroll"    a dial id from actions.SCROLLS (turn the wheel while pointing at it).
The menu can be drawn as free bubbles, as a pie or as a grid of tiles."""

import logging
import math
import os

from PySide6.QtCore import QEasingCurve, QObject, QPointF, QRectF, Qt, QTimer, QUrl, QVariantAnimation, Signal, Slot
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
from PySide6.QtQuick import QQuickItem, QQuickPaintedItem
from PySide6.QtWidgets import QWidget

from .. import actions, device
from ..i18n import _
from . import theme

log = logging.getLogger(__name__)

STYLES = ("bubbles", "pie", "grid")
RING_BASE = 380.0       # logical pixels of the whole menu at size 1.0
ORBIT = 0.335           # bubbles style, as fractions of the size: distance of the bubbles from the centre,
BUBBLE = 0.108          # bubble radius,
CENTER = 0.125          # centre bubble radius
FOLDER_DWELL = 330      # ms pointing at a folder (button held) before it opens
IDLE_TIMEOUT = 20000    # an untouched ring closes by itself
WHEEL_STEP = 120        # wheel units per dial step


def is_folder(bubble):
    return "children" in bubble


def is_dial(bubble):
    return bubble.get("scroll") in actions.SCROLLS


def bubble_label(bubble):
    label = bubble.get("label", "").strip()
    if label:
        return _(label)
    if is_dial(bubble):
        return _(actions.SCROLLS[bubble["scroll"]][0])
    return _("Folder") if is_folder(bubble) else actions.describe(bubble.get("action"))


def bubble_icon(bubble):
    """Icon spec: a theme icon name, an image path, or a short text / emoji."""
    custom = bubble.get("icon", "").strip()
    if custom:
        return custom
    if is_dial(bubble):
        return actions.SCROLLS[bubble["scroll"]][1]
    return "folder" if is_folder(bubble) else actions.icon_name(bubble.get("action"))


_icons = {}


def clear_icon_cache():
    _icons.clear()


# Icons that not every theme has, and what to show where they are missing
_STAND_INS = {
    "view-app-grid": "dashboard-show", "focus-windows": "window-duplicate", "Alacritty": "utilities-terminal",
    "youtube-music": "applications-multimedia", "vscodium": "applications-development",
    "telegram": "internet-chat", "discord": "internet-chat", "com.obsproject.Studio": "camera-video",
}


def theme_icon_name(name):
    """``name`` if the icon theme has it, else its stand-in, else None."""
    for candidate in (name, _STAND_INS.get(name)):
        if candidate and QIcon.hasThemeIcon(candidate):
            return candidate
    return None


def themed_icon(spec):
    """QIcon for a spec, or None when it should be drawn as text."""
    if spec not in _icons:
        if spec.startswith("/"):
            icon = QIcon(spec) if os.path.isfile(spec) else None
        else:
            name = theme_icon_name(spec)
            icon = QIcon.fromTheme(name) if name else None
        _icons[spec] = icon if icon is not None and not icon.isNull() else None
    return _icons[spec]


def draw_icon(painter, spec, rect, fallback, color):
    icon = themed_icon(spec)
    if icon is not None:
        icon.paint(painter, rect.toRect())
        return
    # Not an icon: an emoji or a couple of characters typed by the user, else the label's initial.
    typed = spec and not spec.startswith("/") and (len(spec) <= 2 or (len(spec) <= 6 and not spec.isascii()))
    text = spec if typed else fallback[:1].upper()
    font = QFont(QGuiApplication.font())
    font.setPixelSize(max(8, int(rect.height() * (0.78 if len(text) <= 2 or not text.isascii() else 0.5))))
    font.setBold(text.isascii())
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(rect.adjusted(-10, -10, 10, 10), Qt.AlignmentFlag.AlignCenter, text)


# -- layout ------------------------------------------------------------------

class Cell:
    """Where one bubble goes: its outline, the slightly larger area that reacts to the pointer,
    the icon box, an optional label box, and a centre + radius for decorations."""

    __slots__ = ("path", "hit", "icon", "label", "center", "radius")

    def __init__(self, path, hit, icon, label, center, radius):
        self.path, self.hit, self.icon, self.label, self.center, self.radius = path, hit, icon, label, center, radius


def _circle(center, radius):
    path = QPainterPath()
    path.addEllipse(center, radius, radius)
    return path


def _wedge(center, inner, outer, start, sweep):
    """Ring segment; angles in Qt convention (degrees, counter-clockwise from 3 o'clock)."""
    def box(radius):
        return QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
    path = QPainterPath()
    path.arcMoveTo(box(outer), start)
    path.arcTo(box(outer), start, sweep)
    path.arcTo(box(inner), start + sweep, -sweep)
    path.closeSubpath()
    return path


def _grid_shape(count):
    columns = 2 if count <= 4 else 3
    return columns, max(1, math.ceil(count / columns))


def layout(style, rect, count, spread=1.0):
    """Returns (cells, hub path, hub text box). ``spread`` (0..1) lets bubbles fly out from the centre."""
    size = min(rect.width(), rect.height())
    center = rect.center()
    cells = []
    if style == "pie":
        outer, inner = size * 0.47, size * 0.20
        span = 360.0 / max(1, count)
        for index in range(count):
            start = 90.0 - index * span + span / 2  # slot 0 is centred on 12 o'clock, going clockwise
            angle = math.radians(index * span - 90.0)
            middle = (outer + inner) / 2
            point = QPointF(center.x() + math.cos(angle) * middle, center.y() + math.sin(angle) * middle)
            icon = size * 0.088
            gap = 1.5 if count > 1 else 0.0
            cells.append(Cell(
                _wedge(center, inner + 4, outer - 3, start - gap, -(span - 2 * gap)) if count > 1 else _wedge(center, inner + 4, outer - 3, 90, -359.9),
                _wedge(center, inner, outer, start, -span) if count > 1 else _wedge(center, inner, outer, 90, -359.9),
                QRectF(point.x() - icon / 2, point.y() - icon / 2, icon, icon), None, point, icon * 0.75,
            ))
        hub_radius = inner - 4
        hub = _circle(center, hub_radius)
        box = QRectF(center.x() - hub_radius + 7, center.y() - hub_radius + 7, hub_radius * 2 - 14, hub_radius * 2 - 14)
        return cells, hub, box
    if style == "grid":
        columns, rows = _grid_shape(count)
        tile_w, tile_h, gap, bar = size * 0.27, size * 0.235, size * 0.028, size * 0.105
        total_w = 3 * tile_w + 2 * gap
        total_h = bar + gap + rows * tile_h + (rows - 1) * gap
        left, top = center.x() - total_w / 2, center.y() - total_h / 2
        grid_left = center.x() - (columns * tile_w + (columns - 1) * gap) / 2
        for index in range(count):
            row, column = divmod(index, columns)
            tile = QRectF(grid_left + column * (tile_w + gap), top + bar + gap + row * (tile_h + gap), tile_w, tile_h)
            path = QPainterPath()
            path.addRoundedRect(tile, size * 0.045, size * 0.045)
            hit = QPainterPath()
            hit.addRect(tile.adjusted(-gap / 2, -gap / 2, gap / 2, gap / 2))
            icon = size * 0.098
            cells.append(Cell(
                path, hit, QRectF(tile.center().x() - icon / 2, tile.top() + tile_h * 0.16, icon, icon),
                QRectF(tile.left() + 5, tile.top() + tile_h * 0.62, tile_w - 10, tile_h * 0.32), tile.center(), icon * 0.8,
            ))
        hub_rect = QRectF(left, top, total_w, bar)
        hub = QPainterPath()
        hub.addRoundedRect(hub_rect, bar / 2, bar / 2)
        return cells, hub, hub_rect.adjusted(12, 2, -12, -2)
    # bubbles
    radius = size * BUBBLE * (0.55 + 0.45 * min(1.0, spread))
    for index in range(count):
        angle = math.radians(index * 360.0 / max(1, count) - 90.0)
        point = QPointF(center.x() + math.cos(angle) * size * ORBIT * spread, center.y() + math.sin(angle) * size * ORBIT * spread)
        icon = radius * 0.92
        cells.append(Cell(_circle(point, radius), _circle(point, radius * 1.18),
                          QRectF(point.x() - icon / 2, point.y() - icon / 2, icon, icon), None, point, radius))
    hub_radius = size * CENTER
    box = QRectF(center.x() - hub_radius + 7, center.y() - hub_radius + 7, hub_radius * 2 - 14, hub_radius * 2 - 14)
    return cells, _circle(center, hub_radius), box


def cell_at(pos, rect, count, style="bubbles"):
    """Index of the bubble under ``pos``, or -1."""
    cells, _hub, _box = layout(style, rect, count)
    for index, cell in enumerate(cells):
        if cell.hit.contains(pos):
            return index
    return -1


def in_hub(pos, rect, count, style="bubbles"):
    _cells, hub, _box = layout(style, rect, count)
    return hub.contains(pos)


def _back_ease(t):
    """Ease-out with a slight overshoot, for the bubbles flying out."""
    t = max(0.0, min(1.0, t)) - 1.0
    return 1.0 + t * t * (2.2 * t + 1.2)


# -- painting ----------------------------------------------------------------

def paint_ring(painter, rect, bubbles, selected=-1, checked=-1, progress=1.0, center_text="", back=False,
               ghost=False, style="bubbles", spin=None):
    """Draw one level of the menu. ``ghost`` appends a dashed "+" cell (settings preview);
    ``spin`` is (index, degrees) for a dial that is being turned."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    size = min(rect.width(), rect.height())
    count = len(bubbles) + (1 if ghost else 0)
    painter.setOpacity(max(0.0, min(1.0, progress * 1.4)))
    if style == "bubbles":
        cells, hub, hub_box = layout(style, rect, count, _back_ease(progress))
    else:
        cells, hub, hub_box = layout(style, rect, count)
        grow = 0.90 + 0.10 * _back_ease(progress)
        painter.translate(rect.center())
        painter.scale(grow, grow)
        painter.translate(-rect.center())
    app_font = QGuiApplication.font()
    shadow = QColor(0, 0, 0, 70 if theme.DARK else 34)

    if style == "pie":  # one plate under all the segments
        plate = QColor(theme.BUBBLE)
        plate.setAlpha(240)
        painter.setPen(QPen(QColor(theme.BUBBLE_BORDER), 1.2))
        painter.setBrush(plate)
        painter.drawEllipse(rect.center(), size * 0.47, size * 0.47)

    for index, cell in enumerate(cells):
        if index >= len(bubbles):  # the "+" placeholder
            painter.setPen(QPen(QColor(theme.ACCENT if index == selected else theme.MUTED), 1.4, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(cell.path)
            font = QFont(app_font)
            font.setPixelSize(int(cell.radius * 0.9))
            painter.setFont(font)
            painter.setPen(QColor(theme.ACCENT_INK if index == selected else theme.MUTED))
            painter.drawText(cell.path.boundingRect(), Qt.AlignmentFlag.AlignCenter, "+")
            continue

        bubble = bubbles[index]
        tint = bubble.get("color", "")
        tint = tint if tint and QColor(tint).isValid() else ""
        active = index == selected
        path, icon_rect = cell.path, cell.icon
        if active and style == "bubbles":  # the bubble under the pointer swells
            grown = cell.radius * 1.13
            path = _circle(cell.center, grown)
            icon_rect = QRectF(cell.center.x() - grown * 0.46, cell.center.y() - grown * 0.46, grown * 0.92, grown * 0.92)
        if active:
            fill = QColor(tint or theme.ACCENT)
        elif style == "pie":
            fill = QColor(theme.mix(theme.BUBBLE, tint, 0.30) if tint else theme.mix(theme.BUBBLE, theme.TEXT, 0.06))
        else:
            fill = QColor(theme.mix(theme.BUBBLE, tint, 0.24) if tint else theme.BUBBLE)
            fill.setAlpha(246)
        if style != "pie":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shadow)
            painter.drawPath(path.translated(0, cell.radius * 0.09))
        if active:
            painter.setPen(Qt.PenStyle.NoPen)
        elif index == checked:
            painter.setPen(QPen(QColor(theme.ACCENT), 2.4))
        elif style == "pie":
            painter.setPen(Qt.PenStyle.NoPen)
        else:
            painter.setPen(QPen(QColor(tint or theme.BUBBLE_BORDER), 1.3))
        painter.setBrush(fill)
        painter.drawPath(path)

        ink = theme.readable_on(fill.name()) if active else theme.TEXT
        folder, dial = is_folder(bubble), is_dial(bubble)
        if folder and style != "grid":
            icon_rect = icon_rect.adjusted(icon_rect.width() * 0.05, 0, -icon_rect.width() * 0.05, -icon_rect.height() * 0.10)
            icon_rect.translate(0, -cell.radius * 0.06)
        draw_icon(painter, bubble_icon(bubble), icon_rect, bubble_label(bubble), ink)
        mark = QColor(ink)
        mark.setAlpha(190)
        if folder:  # three dots: this one opens more
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(mark)
            base = QPointF(cell.center.x(), cell.center.y() + cell.radius * 0.64) if style != "grid" \
                else QPointF(path.boundingRect().right() - cell.radius * 0.45, path.boundingRect().top() + cell.radius * 0.30)
            for offset in (-1, 0, 1):
                painter.drawEllipse(QPointF(base.x() + offset * cell.radius * 0.20, base.y()), cell.radius * 0.06, cell.radius * 0.06)
        if dial:
            _paint_dial(painter, cell, path, style, mark, active, spin[1] if spin and spin[0] == index else None)
        if cell.label is not None:
            font = QFont(app_font)
            font.setPixelSize(max(8, int(size * 0.029)))
            painter.setFont(font)
            painter.setPen(QColor(ink))
            text = QFontMetricsF(font).elidedText(bubble_label(bubble), Qt.TextElideMode.ElideRight, cell.label.width())
            painter.drawText(cell.label, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), text)

    # hub: name of what is pointed at, or the open folder with a way back
    fill = QColor(theme.BUBBLE)
    fill.setAlpha(246)
    painter.setPen(QPen(QColor(theme.BUBBLE_BORDER), 1.3))
    painter.setBrush(fill)
    painter.drawPath(hub)
    shown = selected if 0 <= selected < len(bubbles) else checked
    named = 0 <= shown < len(bubbles)
    text = bubble_label(bubbles[shown]) if named else center_text
    font = QFont(app_font)
    font.setPixelSize(max(9, int(size * 0.033)))
    font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(font)
    painter.setPen(QColor(theme.TEXT if named else theme.MUTED))
    if style == "grid":
        line = ("‹  " if back else "") + text
        painter.drawText(hub_box, int(Qt.AlignmentFlag.AlignCenter),
                         QFontMetricsF(font).elidedText(line, Qt.TextElideMode.ElideRight, hub_box.width()))
    elif back and not named:
        arrow = QFont(font)
        arrow.setPixelSize(int(size * 0.06))
        painter.setFont(arrow)
        painter.drawText(QRectF(hub_box.left(), hub_box.top(), hub_box.width(), hub_box.height() * 0.5), Qt.AlignmentFlag.AlignCenter, "‹")
        painter.setFont(font)
        painter.drawText(QRectF(hub_box.left(), hub_box.center().y() - 2, hub_box.width(), hub_box.height() * 0.5),
                         int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap), text)
    else:
        painter.drawText(hub_box, int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), text)
    painter.restore()


def _paint_dial(painter, cell, path, style, color, active, angle):
    """Mark a bubble as a dial: a ring of ticks (bubbles) or a small scroll glyph, plus a pointer while turning."""
    if style == "bubbles":
        radius = path.boundingRect().width() / 2
        painter.setPen(QPen(color, 1.3))
        for step in range(24):
            a = math.radians(step * 15.0)
            inner, outer = radius * 0.84, radius * 0.93
            painter.drawLine(QPointF(cell.center.x() + math.cos(a) * inner, cell.center.y() + math.sin(a) * inner),
                             QPointF(cell.center.x() + math.cos(a) * outer, cell.center.y() + math.sin(a) * outer))
        if angle is not None:
            a = math.radians(angle - 90.0)
            pen = QPen(QColor(theme.readable_on(theme.ACCENT) if active else theme.ACCENT), 3.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(cell.center.x() + math.cos(a) * radius * 0.74, cell.center.y() + math.sin(a) * radius * 0.74),
                             QPointF(cell.center.x() + math.cos(a) * radius * 0.95, cell.center.y() + math.sin(a) * radius * 0.95))
        return
    box = path.boundingRect()
    corner = QPointF(box.right() - cell.radius * 0.5, box.top() + cell.radius * 0.45) if style == "grid" \
        else QPointF(cell.center.x(), cell.center.y() + cell.radius * 1.05)
    font = QFont(QGuiApplication.font())
    font.setPixelSize(max(8, int(cell.radius * 0.62)))
    painter.setFont(font)
    painter.setPen(QColor(theme.ACCENT) if angle is not None and not active else color)
    painter.drawText(QRectF(corner.x() - cell.radius, corner.y() - cell.radius * 0.5, cell.radius * 2, cell.radius),
                     Qt.AlignmentFlag.AlignCenter, "⇅")


# -- settings preview --------------------------------------------------------

class RingPreview(QWidget):
    """Clickable menu used on the settings page."""

    bubble_clicked = Signal(int)
    bubble_opened = Signal(int)   # double click on a folder
    add_clicked = Signal()
    center_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 360)
        self.setMouseTracking(True)
        self._bubbles = []
        self._hover = -1
        self._checked = -1
        self._center_text = ""
        self._back = False
        self._ghost = False
        self._style = "bubbles"

    def set_level(self, bubbles, checked, center_text="", back=False, ghost=False, style="bubbles"):
        self._bubbles = list(bubbles)
        self._checked = checked
        self._center_text = center_text
        self._back = back
        self._ghost = ghost
        self._style = style
        self.update()

    def _rect(self):
        size = min(self.width(), self.height(), 470)
        return QRectF((self.width() - size) / 2, (self.height() - size) / 2, size, size)

    def _count(self):
        return len(self._bubbles) + (1 if self._ghost else 0)

    def paintEvent(self, _event):
        painter = QPainter(self)
        paint_ring(painter, self._rect(), self._bubbles, self._hover, self._checked, center_text=self._center_text,
                   back=self._back, ghost=self._ghost, style=self._style)

    def mouseMoveEvent(self, event):
        hover = cell_at(event.position(), self._rect(), self._count(), self._style)
        clickable = hover >= 0 or (self._back and in_hub(event.position(), self._rect(), self._count(), self._style))
        self.setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)
        if hover != self._hover:
            self._hover = hover
            self.update()

    def leaveEvent(self, _event):
        self._hover = -1
        self.update()

    def mousePressEvent(self, event):
        index = cell_at(event.position(), self._rect(), self._count(), self._style)
        if 0 <= index < len(self._bubbles):
            self.bubble_clicked.emit(index)
        elif index >= 0:
            self.add_clicked.emit()
        elif self._back and in_hub(event.position(), self._rect(), self._count(), self._style):
            self.center_clicked.emit()

    def mouseDoubleClickEvent(self, event):
        index = cell_at(event.position(), self._rect(), self._count(), self._style)
        if 0 <= index < len(self._bubbles) and is_folder(self._bubbles[index]):
            self.bubble_opened.emit(index)


# -- overlay -----------------------------------------------------------------

class RingItem(QQuickPaintedItem):
    """The menu as a Qt Quick item, painted with the same code as the preview."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAntialiasing(True)
        self._bubbles = []
        self._selected = -1
        self._progress = 1.0
        self._center_text = ""
        self._back = False
        self._style = "bubbles"
        self._spin = None

    def set_style(self, style):
        self._style = style
        self.update()

    def set_level(self, bubbles, center_text="", back=False):
        self._bubbles = list(bubbles)
        self._center_text = center_text
        self._back = back
        self._selected = -1
        self._spin = None
        self.update()

    def set_selected(self, index):
        if index != self._selected:
            self._selected = index
            self.update()

    def set_progress(self, value):
        self._progress = value
        self.update()

    def set_spin(self, index, angle):
        self._spin = (index, angle)
        self.update()

    def paint(self, painter):
        paint_ring(painter, QRectF(0, 0, self.width(), self.height()), self._bubbles, self._selected,
                   progress=self._progress, center_text=self._center_text, back=self._back, style=self._style,
                   spin=self._spin)


_registered = False


class RingOverlay(QObject):
    """Shows the menu under the cursor; the cursor stays free and picks a bubble by pointing at it.

    Hold the button, point at a bubble, release -> it runs (resting on a folder opens it).
    Tap the button -> the menu stays open; click a bubble, or click elsewhere to dismiss.
    Turning the wheel or the thumb wheel over a dial adjusts it without closing the menu."""

    triggered = Signal(object)  # action to run
    cue = Signal(int)           # haptic waveform to play
    opened = Signal(bool)       # the menu appeared / went away

    def __init__(self, parent=None):
        super().__init__(parent)
        self.available = False
        self._root = []
        self._shown = []        # the bubbles on screen at the top level: the user's ring, or a visitor's
        self._path = []         # folders entered, outermost first
        self._size = RING_BASE
        self._style = "bubbles"
        self._open = False
        self._held = False      # the button that opened the menu is still down
        self._selected = -1
        self._placed = False
        self._armed = False     # the pointer has left the bubbles since the menu (or a folder) opened
        self._swallow_up = False
        self._wheel = 0.0       # wheel travel not yet turned into dial steps
        self._angle = 0.0
        self._window = None
        self._ring = None
        self._place_timer = QTimer(self, singleShot=True, interval=140)
        self._place_timer.timeout.connect(self._place_fallback)
        self._idle_timer = QTimer(self, singleShot=True, interval=IDLE_TIMEOUT)
        self._idle_timer.timeout.connect(self.close)
        self._dwell_timer = QTimer(self, singleShot=True, interval=FOLDER_DWELL)
        self._dwell_timer.timeout.connect(self._dwelled)
        self._grow = QVariantAnimation(self)
        self._grow.setStartValue(0.0)
        self._grow.setEndValue(1.0)
        self._grow.setDuration(190)
        self._grow.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._grow.valueChanged.connect(lambda value: self._ring.set_progress(float(value)))
        self._load()

    def _load(self):
        global _registered
        if not _registered:
            qmlRegisterType(RingItem, "MXOptions", 1, 0, "RingItem")
            _registered = True
        self._engine = QQmlApplicationEngine(self)
        self._engine.warnings.connect(lambda warnings: [log.warning("ring: %s", w.toString()) for w in warnings])
        self._engine.rootContext().setContextProperty("overlay", self)
        self._engine.load(QUrl.fromLocalFile(os.path.join(os.path.dirname(__file__), "ring.qml")))
        roots = self._engine.rootObjects()
        if not roots:
            log.warning("Actions Ring overlay unavailable (layer-shell QML module missing?)")
            return
        self._window = roots[0]
        self._ring = self._window.findChild(QQuickItem, "ring")
        self.available = isinstance(self._ring, RingItem)

    def configure(self, ring):
        """Take the "ring" section of the configuration."""
        self._root = ring["bubbles"]
        self._size = RING_BASE * ring.get("size", 1.0)
        self._style = ring.get("style", "bubbles")
        if self._open:
            self.close()
        if self._ring:
            self._ring.setWidth(self._size)
            self._ring.setHeight(self._size)
            self._ring.set_style(self._style)

    def refresh(self):
        """Repaint after a theme change."""
        if self._ring:
            self._ring.update()

    def _level(self):
        return self._path[-1]["children"] if self._path else self._shown

    def _show_level(self, animate=True):
        self._dwell_timer.stop()
        self._selected = -1
        self._armed = False
        self._wheel = 0.0
        folder = self._path[-1] if self._path else None
        self._ring.set_level(self._level(), bubble_label(folder) if folder else "", back=folder is not None)
        if animate:
            self._grow.stop()
            self._grow.start()

    # -- driven by the mouse button (engine events) -----------------------

    def handle(self, event):
        if not self.available:
            return
        if event == "down":
            if self._open:  # a second press closes a menu left open by a tap
                self._swallow_up = True
                self.close()
            elif self._root:
                self._show(self._root)
        elif event == "up":
            self._held = False
            if self._swallow_up:
                self._swallow_up = False
            elif self._open:
                self._released()
        elif event == "cancel":
            self._swallow_up = False
            self.close()

    def open(self, bubbles=None):
        """Open the menu at the pointer with no button held, as another program asked; it waits for a
        click. ``bubbles`` (already checked) are shown once in place of the user's ring."""
        root = self._root if bubbles is None else bubbles
        if not self.available or self._open or not root:
            return False
        self._show(root)
        self._held = False
        return True

    def _show(self, root):
        self._open = True
        self._held = True
        self._shown = root
        self._path = []
        self._placed = False
        self._ring.set_progress(0.0)
        self._show_level(animate=False)
        self._ring.setProperty("shown", False)
        self._window.setVisible(True)
        self._place_timer.start()
        self._idle_timer.start()
        self.cue.emit(device.WAVE_SUBTLE)
        self.opened.emit(True)

    def close(self):
        for timer in (self._place_timer, self._idle_timer, self._dwell_timer):
            timer.stop()
        self._grow.stop()
        self._held = False
        if self._open:
            self._open = False
            self._ring.setProperty("shown", False)
            self._window.setVisible(False)
            self.opened.emit(False)

    def _released(self):
        """The button came up: run what the pointer is on, otherwise leave the menu open for a click."""
        self._dwell_timer.stop()
        level = self._level()
        if 0 <= self._selected < len(level):
            bubble = level[self._selected]
            if is_folder(bubble):
                self._enter(self._selected)
            elif not is_dial(bubble):  # a dial stays put: it is turned, not released on
                self._choose(self._selected)

    def _select(self, index):
        if index == self._selected:
            return
        self._selected = index
        self._wheel = 0.0
        self._ring.set_selected(index)
        self._dwell_timer.stop()
        level = self._level()
        if 0 <= index < len(level):
            self.cue.emit(device.WAVE_TICK)
            if self._held and is_folder(level[index]):
                self._dwell_timer.start()

    def _dwelled(self):
        level = self._level()
        if self._open and self._held and 0 <= self._selected < len(level) and is_folder(level[self._selected]):
            self._enter(self._selected)

    def _enter(self, index):
        folder = self._level()[index]
        if not folder.get("children"):
            return
        self._path.append(folder)
        self._show_level()
        self.cue.emit(device.WAVE_SUBTLE)

    def _leave(self):
        if self._path:
            self._path.pop()
            self._show_level()

    def _choose(self, index):
        bubble = self._level()[index]
        action = actions.SCROLLS[bubble["scroll"]][4] if is_dial(bubble) else bubble.get("action")
        if not action:
            return  # a dial without a click action
        self.close()
        self.cue.emit(device.WAVE_CONFIRM)
        # Let the overlay unmap first so the action lands in the application below.
        QTimer.singleShot(40, lambda: self.triggered.emit(action))

    # -- driven by the pointer (QML) ---------------------------------------

    def _ring_rect(self):
        return QRectF(self._ring.x(), self._ring.y(), self._ring.width(), self._ring.height())

    def _place(self, x, y):
        self._place_timer.stop()
        width, height = self._window.width(), self._window.height()
        size, margin = self._size, 6
        self._ring.setX(max(margin, min(width - size - margin, x - size / 2)))
        self._ring.setY(max(margin, min(height - size - margin, y - size / 2)))
        self._placed = True
        self._ring.setProperty("shown", True)
        self._grow.stop()
        self._grow.start()

    def _place_fallback(self):
        if self._open and not self._placed:
            self._place(self._window.width() / 2, self._window.height() / 2)

    def _at(self, x, y):
        return cell_at(QPointF(x, y), self._ring_rect(), len(self._level()), self._style)

    @Slot(float, float)
    def pointerAt(self, x, y):
        if not self._open:
            return
        if not self._placed:
            self._place(x, y)
            return
        self._idle_timer.start()
        index = self._at(x, y)
        # Near a screen edge the menu cannot be centred on the cursor, and a freshly opened
        # folder appears under it: ignore the bubble the pointer merely happens to start on.
        if not self._armed:
            if index >= 0:
                return
            self._armed = True
        self._select(index)

    @Slot(float, float)
    def clickAt(self, x, y):
        if not self._open:
            return
        level = self._level()
        index = self._at(x, y)
        if index >= 0:
            if is_folder(level[index]):
                self._enter(index)
            else:
                self._choose(index)
        elif in_hub(QPointF(x, y), self._ring_rect(), len(level), self._style) and self._path:
            self._leave()
        else:
            self.close()

    @Slot(float, float, float)
    def wheelAt(self, x, y, delta):
        """Wheel turned over the menu: adjusts the dial under the pointer."""
        if not self._open or not self._placed:
            return
        level = self._level()
        index = self._at(x, y)
        if not (0 <= index < len(level) and is_dial(level[index])):
            return
        self._armed = True
        if index != self._selected:
            self._select(index)
        self._spin(index, delta)

    def turn(self, clicks):
        """The thumb wheel, when the app handles it itself: turns the dial the pointer rests on."""
        level = self._level()
        if self._open and self._placed and 0 <= self._selected < len(level) and is_dial(level[self._selected]):
            self._spin(self._selected, clicks * WHEEL_STEP)

    def _spin(self, index, delta):
        self._idle_timer.start()
        self._wheel += delta
        _label, _icon, up, down, _click = actions.SCROLLS[self._level()[index]["scroll"]]
        while abs(self._wheel) >= WHEEL_STEP:
            forward = self._wheel > 0
            self._wheel -= WHEEL_STEP if forward else -WHEEL_STEP
            self._angle = (self._angle + (30.0 if forward else -30.0)) % 360.0
            self._ring.set_spin(index, self._angle)
            self.cue.emit(device.WAVE_TICK)
            self.triggered.emit(up if forward else down)
