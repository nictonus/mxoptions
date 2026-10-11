"""The mouse as Logitech's own product picture, with the controls marked on it.

The pictures are Logitech's official MX Master 4 renders (the angle Options+ uses, one per body
colour). They are Logitech's property and are not shipped with this program: it fetches them from
logitech.com when the user asks for them and keeps them in the user's data directory. The outlines
below were traced on them by hand."""

import logging
import os
import threading
import urllib.request

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainterPath, QPainterPathStroker, QPen, QRadialGradient

from . import theme

try:
    import numpy as np
except ImportError:  # only the soft shadow needs it
    np = None

log = logging.getLogger(__name__)

PHOTO_DIR = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "mxoptions", "mouse")
COLORS = ("graphite", "pale-grey", "black")
# Where a picture comes from, and the part of it that holds the mouse (fractions of the image:
# left, top, width, height). The outlines below belong to exactly this crop.
SOURCE = ("https://resource.logitech.com/w_2400,c_limit,q_auto:best,f_png/d_transparent.gif/content/dam/logitech/en/"
          "products/mice/mx-master-4/gallery/mx-master-4-{color}-software-angle-gallery-7.png")
CROP = (637 / 2400, 192 / 2074, 1073 / 2400, 1669 / 2074)
GRID = (797.0, 1240.0)  # pixel grid the outlines were traced on (the size the pictures are stored at)
ASPECT = GRID[0] / GRID[1]
CONTROL_KEYS = ("middle", "smartshift", "gesture", "forward", "back", "panel")
MAIN_BUTTONS = ("left", "right")  # shown and explained, but the mouse does not let them be reassigned

# Pill-shaped side buttons: (one end, other end, radius)
_CAPSULES = {
    "gesture": ((315, 446), (333, 527), 16),
    "forward": ((345, 578), (361, 652), 15),
    "back": ((370, 698), (392, 779), 15),
}
# Straight-sided controls: (corners, corner radius)
_QUADS = {
    "smartshift": ([(616, 492), (647, 484), (667, 565), (642, 575)], 7),
    "thumbwheel": ([(375, 555), (462, 528), (510, 727), (420, 748)], 6),
}
# Free-form outlines, smoothed through these points
_CURVES = {
    "panel": [
        (283, 497), (250, 517), (210, 540), (165, 553), (135, 568), (120, 591), (100, 655), (75, 730), (57, 795),
        (55, 822), (63, 857), (80, 895), (125, 945), (175, 995), (215, 1035), (240, 1057), (265, 1067), (289, 1062),
        (305, 1044), (340, 975), (370, 915), (384, 880), (385, 858), (375, 820), (350, 730), (325, 640), (305, 560),
        (297, 514),
    ],
    "middle": [(513, 211), (545, 205), (572, 214), (604, 250), (618, 300), (621, 372), (592, 380), (566, 372),
               (548, 320), (530, 262)],
    "left": [(318, 22), (400, 20), (472, 56), (532, 188), (512, 210), (530, 262), (548, 320), (566, 372), (600, 384),
             (622, 458), (470, 494), (405, 400), (350, 230), (310, 100), (300, 50)],
    "right": [(478, 58), (560, 95), (640, 150), (700, 230), (745, 350), (753, 432), (628, 458), (604, 386),
              (622, 372), (618, 300), (604, 250), (572, 212), (538, 190)],
}
# Where a callout line ends
ANCHORS = {
    "middle": (552, 296), "smartshift": (630, 532), "gesture": (322, 487), "forward": (352, 616),
    "back": (380, 738), "panel": (176, 700), "left": (430, 240), "right": (680, 280), "thumbwheel": (440, 640),
}

_images = {}   # colour -> QImage
_scaled = {}   # (colour, width, height) in device pixels -> QImage
_shadows = {}  # (colour, dark) -> QImage


def photo_path(color):
    return os.path.join(PHOTO_DIR, color + ".png")


def available():
    """Whether the pictures have been fetched."""
    return os.path.exists(photo_path(COLORS[0]))


def download():
    """Fetch the pictures from logitech.com, cut the mouse out of them and store them. Raises OSError
    (which covers network errors) or ValueError if what comes back is not the picture expected."""
    os.makedirs(PHOTO_DIR, exist_ok=True)
    for color in COLORS:
        request = urllib.request.Request(SOURCE.format(color=color), headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
        with urllib.request.urlopen(request, timeout=40) as reply:
            data = reply.read()
        image = QImage.fromData(data)
        if image.isNull() or image.width() < 800 or not image.hasAlphaChannel():
            raise ValueError(f"unexpected picture for {color}")
        left, top, width, height = CROP
        crop = QRect(round(left * image.width()), round(top * image.height()), round(width * image.width()),
                     round(height * image.height()))
        picture = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied).copy(crop).scaled(
            int(GRID[0]), int(GRID[1]), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        if not picture.save(photo_path(color) + ".tmp", "PNG"):
            raise OSError(f"could not write {photo_path(color)}")
        os.replace(photo_path(color) + ".tmp", photo_path(color))
    _images.clear()
    _scaled.clear()
    _shadows.clear()


class Downloader(QObject):
    """Runs download() off the GUI thread; ``finished`` carries an error message, empty on success."""

    finished = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = False

    def start(self):
        if self.running:
            return
        self.running = True

        def job():
            try:
                download()
                error = ""
            except (OSError, ValueError) as e:
                log.warning("could not fetch the mouse pictures: %s", e)
                error = str(e) or type(e).__name__
            self.running = False
            self.finished.emit(error)

        threading.Thread(target=job, name="mouse-pictures", daemon=True).start()


def _image(color):
    if color not in _images:
        path = photo_path(color if color in COLORS and os.path.exists(photo_path(color)) else COLORS[0])
        _images[color] = QImage(path).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    return _images[color]


def fit(area):
    """The largest rectangle with the picture's proportions centred in ``area``."""
    height = min(area.height(), area.width() / ASPECT)
    width = height * ASPECT
    return QRectF(area.center().x() - width / 2, area.center().y() - height / 2, width, height)


def _point(rect, p):
    return QPointF(rect.left() + p[0] * rect.width() / GRID[0], rect.top() + p[1] * rect.height() / GRID[1])


def anchor(rect, key):
    return _point(rect, ANCHORS[key])


def _closed_spline(points):
    """Catmull-Rom curve through ``points``, closed."""
    path = QPainterPath(points[0])
    count = len(points)
    for i in range(count):
        p0, p1, p2, p3 = points[i - 1], points[i], points[(i + 1) % count], points[(i + 2) % count]
        path.cubicTo(p1 + (p2 - p0) / 6.0, p2 - (p3 - p1) / 6.0, p2)
    path.closeSubpath()
    return path


def _rounded_polygon(points, radius):
    path = QPainterPath()
    count = len(points)
    for i in range(count):
        corner, before, after = points[i], points[i - 1], points[(i + 1) % count]
        to_before, to_after = before - corner, after - corner
        length_before = max(1e-6, (to_before.x() ** 2 + to_before.y() ** 2) ** 0.5)
        length_after = max(1e-6, (to_after.x() ** 2 + to_after.y() ** 2) ** 0.5)
        start = corner + to_before * (min(radius, length_before / 2) / length_before)
        end = corner + to_after * (min(radius, length_after / 2) / length_after)
        if i == 0:
            path.moveTo(start)
        else:
            path.lineTo(start)
        path.quadTo(corner, end)
    path.closeSubpath()
    return path


def shape(rect, key):
    """Outline of a control as a path in the coordinates of ``rect`` (where the picture is drawn)."""
    scale = rect.width() / GRID[0]
    if key in _CAPSULES:
        a, b, radius = _CAPSULES[key]
        pen = QPen(Qt.GlobalColor.black, 2 * radius * scale)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        line = QPainterPath(_point(rect, a))
        line.lineTo(_point(rect, b))
        return QPainterPathStroker(pen).createStroke(line).simplified()
    if key in _QUADS:
        corners, radius = _QUADS[key]
        return _rounded_polygon([_point(rect, p) for p in corners], radius * scale)
    return _closed_spline([_point(rect, p) for p in _CURVES[key]])


def control_at(rect, pos, visible=CONTROL_KEYS + MAIN_BUTTONS):
    """The control under ``pos``, or None. The wheel and the small buttons win over the main buttons around them."""
    for key in CONTROL_KEYS + MAIN_BUTTONS:
        if key in visible and shape(rect, key).contains(pos):
            return key
    return None


def _picture(color, size, ratio):
    key = (color, int(round(size.width() * ratio)), int(round(size.height() * ratio)))
    if key not in _scaled:
        if len(_scaled) > 6:
            _scaled.clear()
        _scaled[key] = _image(color).scaled(QSize(max(1, key[1]), max(1, key[2])), Qt.AspectRatioMode.IgnoreAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation)
    return _scaled[key]


def _box(a, radius):
    k = 2 * radius + 1
    c = np.cumsum(np.pad(a, ((radius + 1, radius), (0, 0)), mode="constant"), axis=0, dtype=np.float32)
    a = (c[k:] - c[:-k]) / k
    c = np.cumsum(np.pad(a, ((0, 0), (radius + 1, radius)), mode="constant"), axis=1, dtype=np.float32)
    return (c[:, k:] - c[:, :-k]) / k


SHADOW_PAD = 0.22  # the shadow image is this much larger than the picture on every side (fraction of its width)


def _shadow(color, size):
    """Blurred silhouette of the mouse, drawn small (it is blurry anyway)."""
    if np is None:
        return None
    width = 96
    height = int(round(width / ASPECT))
    key = (color, theme.DARK)
    if key not in _shadows:
        pad = int(round(width * SHADOW_PAD))
        small = _image(color).scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_RGBA8888)
        alpha = np.frombuffer(small.constBits(), dtype=np.uint8).reshape(height, small.bytesPerLine() // 4, 4)[:, :width, 3]
        canvas = np.zeros((height + 2 * pad, width + 2 * pad), dtype=np.float32)
        canvas[pad:pad + height, pad:pad + width] = alpha / 255.0
        for _ in range(3):
            canvas = _box(canvas, 5)
        out = np.zeros(canvas.shape + (4,), dtype=np.uint8)
        out[..., 3] = (np.clip(canvas, 0.0, 1.0) * (200 if theme.DARK else 60)).astype(np.uint8)
        _shadows[key] = QImage(out.data, out.shape[1], out.shape[0], out.shape[1] * 4, QImage.Format.Format_RGBA8888).copy()
    return _shadows[key]


def _accent():
    """The accent, light enough to show on the picture (the mouse is dark or mid grey in every colour)."""
    return theme.ACCENT_INK if theme.DARK else theme.ACCENT


def paint_stage(painter, rect, bounds):
    """Soft pool of light behind the picture. It fades out completely inside ``bounds``, whatever their shape."""
    centre = rect.center()
    radius_x = min(rect.width() * 0.92, centre.x() - bounds.left(), bounds.right() - centre.x())
    radius_y = min(rect.height() * 0.66, centre.y() - bounds.top(), bounds.bottom() - centre.y())
    if radius_x <= 4 or radius_y <= 4:
        return
    gradient = QRadialGradient(QPointF(0.0, 0.0), 1.0)
    glow = QColor(theme.TEXT if theme.DARK else "#ffffff")
    for stop, alpha in ((0.0, 30), (0.45, 17), (0.8, 4), (1.0, 0)) if theme.DARK else ((0.0, 190), (0.5, 110), (1.0, 0)):
        glow.setAlpha(alpha)
        gradient.setColorAt(stop, glow)
    painter.save()
    painter.translate(centre)
    painter.scale(radius_x, radius_y)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawEllipse(QPointF(0.0, 0.0), 1.0, 1.0)
    painter.restore()


def paint(painter, rect, color, selected=None, pressed=(), visible=CONTROL_KEYS + MAIN_BUTTONS, hover=None, bounds=None):
    """Draw the picture in ``rect`` with the selected, hovered and held-down controls lit up."""
    paint_stage(painter, rect, bounds or rect)
    shadow = _shadow(color, rect.size())
    if shadow is not None:
        pad = rect.width() * SHADOW_PAD
        target = rect.adjusted(-pad, -pad, pad, pad).translated(rect.width() * 0.035, rect.height() * 0.035)
        painter.drawImage(target, shadow)
    ratio = painter.device().devicePixelRatioF()
    painter.drawImage(rect, _picture(color, rect.size(), ratio))

    def lit(key, fill_alpha, line, width):
        path = shape(rect, key)
        fill = QColor(line)
        fill.setAlpha(fill_alpha)
        halo = QColor(line)
        halo.setAlpha(60)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(halo, width + 4.0))
        painter.drawPath(path)
        painter.setPen(QPen(QColor(line), width))
        painter.setBrush(fill)
        painter.drawPath(path)

    shown = [key for key in CONTROL_KEYS + MAIN_BUTTONS if key in visible]
    large = MAIN_BUTTONS  # big surfaces: a lighter tint is enough
    if hover in shown and hover != selected and hover not in pressed:
        lit(hover, 22 if hover in large else 34, theme.mix(_accent(), "#ffffff", 0.35), 1.2)
    if selected in shown and selected not in pressed:
        lit(selected, 70 if selected in large else 105, _accent(), 1.6)
    for key in pressed:
        if key in MAIN_BUTTONS or key in shown:
            lit(key, 105 if key in large else 150, theme.mix(_accent(), "#ffffff", 0.22), 2.0)

    # a quiet dot on every other control, so it is clear what can be clicked
    for key in shown:
        if key == selected or key in pressed:
            continue
        centre = anchor(rect, key)
        painter.setPen(QPen(QColor(0, 0, 0, 150), 1.0))
        painter.setBrush(QColor(255, 255, 255, 215 if key == hover else 150))
        painter.drawEllipse(centre, 3.4, 3.4)
