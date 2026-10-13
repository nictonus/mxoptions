"""A plain picture of a mouse seen from above, for the models there is no picture of: it shows
where each button the mouse really has is, and nothing it does not have."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

from . import theme

ASPECT = 0.62  # width / height
# Where each control is, in parts of the picture (x, y, width, height), and how round its corners are.
BODY = (0.14, 0.03, 0.72, 0.94)
SPLIT = 0.43   # how far down the two main buttons reach, as a share of the picture's height
ZONES = {
    "middle": (0.445, 0.13, 0.11, 0.17),
    "smartshift": (0.455, 0.335, 0.09, 0.055),
    "forward": (0.085, 0.40, 0.085, 0.10),
    "back": (0.085, 0.51, 0.085, 0.10),
    "gesture": (0.07, 0.66, 0.10, 0.13),
    "panel": (0.07, 0.66, 0.10, 0.13),
}
ANCHORS = {"left": (0.30, 0.20), "right": (0.70, 0.20)}


def fit(area):
    """The largest rectangle with the picture's proportions centred in ``area``."""
    height = min(area.height(), area.width() / ASPECT)
    width = height * ASPECT
    return QRectF(area.center().x() - width / 2, area.center().y() - height / 2, width, height)


def _box(rect, part):
    x, y, width, height = part
    return QRectF(rect.left() + x * rect.width(), rect.top() + y * rect.height(), width * rect.width(), height * rect.height())


def _body(rect):
    box = _box(rect, BODY)
    path = QPainterPath()
    path.addRoundedRect(box, box.width() * 0.46, box.width() * 0.46)
    return path


def shapes(rect, shown=None):
    """The outline of every control, by key. The main buttons make room for what sits between them
    on this mouse (``shown``: the controls it has; all of them if not given)."""
    body = _body(rect)
    box = _box(rect, BODY)
    seam = rect.top() + SPLIT * rect.height()
    found = {}
    for key, x0, x1 in (("left", box.left(), box.center().x()), ("right", box.center().x(), box.right())):
        half = QPainterPath()
        half.addRect(QRectF(x0, box.top(), x1 - x0, seam - box.top()))
        found[key] = body.intersected(half)
    for key, part in ZONES.items():
        zone = _box(rect, part)
        path = QPainterPath()
        radius = min(zone.width(), zone.height()) * 0.45
        path.addRoundedRect(zone, radius, radius)
        found[key] = path
    for key in ("left", "right"):  # the wheel and the button behind it sit between the two main buttons
        for inner in ("middle", "smartshift"):
            if shown is None or inner in shown:
                found[key] = found[key].subtracted(found[inner])
    return found


def anchor(rect, key):
    """Where the line from a control's callout ends."""
    if key in ANCHORS:
        return QPointF(rect.left() + ANCHORS[key][0] * rect.width(), rect.top() + ANCHORS[key][1] * rect.height())
    return _box(rect, ZONES[key]).center()


def control_at(rect, pos, shown):
    found = shapes(rect, shown)
    for key in ("middle", "smartshift", "forward", "back", "gesture", "panel", "left", "right"):  # small ones first
        if key in shown and found[key].contains(pos):
            return key
    return None


def paint(painter, rect, selected, pressed, shown, hover):
    found = shapes(rect, shown)
    accent = QColor(theme.ACCENT)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(theme.BORDER), 1.6))
    painter.setBrush(QColor(theme.CARD))
    painter.drawPath(_body(rect))
    for key in ("left", "right", "forward", "back", "gesture", "panel", "middle", "smartshift"):
        if key not in shown:
            continue
        held, active = key in pressed, key == selected
        fill = QColor(accent)
        fill.setAlphaF(0.55 if held else 0.34 if active else 0.16 if key == hover else 0.0)
        if key not in ("left", "right") and not (held or active or key == hover):
            fill = QColor(theme.CARD_HOVER)
        line = QColor(accent if (held or active or key == hover) else theme.MUTED)
        if not (held or active or key == hover):
            line.setAlphaF(0.55)
        painter.setPen(QPen(line, 1.8 if (held or active) else 1.2))
        painter.setBrush(fill)
        painter.drawPath(found[key])
    box = _box(rect, BODY)  # the seam under the two main buttons, drawn last so that it is never painted over
    seam = rect.top() + SPLIT * rect.height()
    seam_colour = QColor(theme.MUTED)
    seam_colour.setAlphaF(0.4)
    painter.setPen(QPen(seam_colour, 1.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(QPointF(box.left() + 4, seam), QPointF(box.right() - 4, seam))
