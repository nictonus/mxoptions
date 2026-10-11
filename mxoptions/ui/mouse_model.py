"""3D model of the mouse, rendered with QPainter in one of several styles.

The shell is lofted from cross-sections along the length of the mouse; controls are
patches laid on that surface. Axes: X to the right, Y towards the front, Z up (mm)."""

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPen, QPolygonF

from . import theme

LENGTH = 126.0
HEIGHT = 50.0
RAILS = 10          # control points per cross-section: right bottom -> over the top -> thumb rest bottom
CROWN = 4           # rail running along the top ridge
STYLES = ("studio", "solid", "lowpoly", "outline", "wire")
DEFAULT_STYLE = "studio"
CONTROL_KEYS = ("middle", "smartshift", "gesture", "forward", "back", "panel")
PRESS_ONLY = ("left", "right")  # main buttons: only drawn while they are held down or selected

# Camera (yaw, pitch) in degrees that shows a control best.
VIEWS = {
    None: (28.0, 52.0),
    "left": (16.0, 60.0),
    "right": (16.0, 60.0),
    "middle": (16.0, 60.0),
    "smartshift": (16.0, 60.0),
    "gesture": (58.0, 34.0),
    "forward": (58.0, 34.0),
    "back": (58.0, 34.0),
    "panel": (42.0, 46.0),
}
_CENTER = (-6.0, 0.0, 22.0)


# -- shape -------------------------------------------------------------------

def _smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _round_end(t):
    """Quarter-circle profile: 0 at t=0 rising to 1 at t=1 with a vertical start."""
    t = max(0.0, min(1.0, t))
    return math.sqrt(max(0.0, 1.0 - (1.0 - t) ** 2))


def _height(u):
    """Height of the ridge at u (0 = tail, 1 = front edge)."""
    peak = 0.40
    if u < peak:
        rise = 16.0 + (HEIGHT - 16.0) * math.sin(math.pi / 2 * (u / peak)) ** 0.85
        return rise * (0.42 + 0.58 * _round_end(u / 0.12))
    return HEIGHT - (HEIGHT - 17.0) * ((u - peak) / (1.0 - peak)) ** 1.5


def _taper(u):
    """Plan-view narrowing: broad round tail, slightly narrower front."""
    tail = (0.82 + 0.18 * _smooth(u / 0.30)) * (0.34 + 0.66 * _round_end(u / 0.10))
    front = 1.0 - 0.15 * _smooth((u - 0.55) / 0.45)
    nose = 0.80 + 0.20 * _round_end((1.0 - u) / 0.06)
    return tail * front * nose


def _wing(u):
    """How far the thumb rest sticks out at u."""
    return 23.0 * _smooth((u - 0.07) / 0.17) * (1.0 - _smooth((u - 0.47) / 0.17))


def rails(u):
    """Control points of the cross-section at u, as (x, y, z)."""
    y = (u - 0.5) * LENGTH
    h = _height(u)
    taper = _taper(u)
    right, left = 35.0 * taper, 28.0 * taper
    wing = _wing(u)
    rim = 7.5 * min(1.0, h / 20.0)
    points = [
        (right, 0.0),
        (right * 1.03, h * 0.24),
        (right * 0.92, h * 0.60),
        (right * 0.54, h * 0.89),
        (-6.0 * taper, h),
        (-left * 0.64, h * 0.95),
        (-left * 0.99, h * 0.72),                      # the lip the thumb sits under
        (-left * 0.88 - wing * 0.06, h * 0.36 + 1.0),  # scooped-in wall
        (-left - wing, rim),
        (-left - wing, 0.0),
    ]
    # Under the front edge the body curves up to meet the buttons.
    chin = (1.0 - _round_end((1.0 - u) / 0.07)) * 0.55
    # The front edge is raked: the left button reaches further forward than the right one.
    rake = 7.0 * _smooth((u - 0.72) / 0.28)
    return [(x, y - rake * x / 35.0, h * chin + z * (1.0 - chin)) for x, z in points]


def _spline(p0, p1, p2, p3, t):
    """Catmull-Rom point between p1 and p2."""
    t2, t3 = t * t, t * t * t
    return tuple(
        0.5 * (2 * b + (c - a) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (3 * b - a - 3 * c + d) * t3)
        for a, b, c, d in zip(p0, p1, p2, p3)
    )


def surface(u, s, smooth=False):
    """Point on the shell; ``s`` runs 0..RAILS-1 across a section."""
    points = rails(u)
    index = max(0, min(RAILS - 2, int(math.floor(s))))
    t = s - index
    a, b = points[index], points[index + 1]
    if not smooth:
        return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))
    return _spline(points[max(0, index - 1)], a, b, points[min(RAILS - 1, index + 2)], t)


# -- geometry ----------------------------------------------------------------

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _unit(v):
    length = math.sqrt(_dot(v, v))
    return (v[0] / length, v[1] / length, v[2] / length) if length > 1e-9 else (0.0, 0.0, 0.0)


def _normal(points):
    """Newell's method: works for any planar-ish polygon."""
    nx = ny = nz = 0.0
    for i, a in enumerate(points):
        b = points[(i + 1) % len(points)]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    return _unit((nx, ny, nz))


class Mesh:
    """Indexed faces with normals and edge adjacency (for silhouettes)."""

    def __init__(self, sections):
        count = len(sections[0])
        self.vertices = [p for row in sections for p in row]
        self.faces = []
        for i in range(len(sections) - 1):
            for j in range(count - 1):
                a = i * count + j
                self.faces.append((a, a + count, a + count + 1, a + 1))
        last = (len(sections) - 1) * count
        self.faces.append(tuple(range(count)))                          # tail cap
        self.faces.append(tuple(reversed(range(last, last + count))))   # front cap
        self.normals = [_normal([self.vertices[i] for i in face]) for face in self.faces]
        self.neighbours = {}  # edge (low index, high index) -> faces sharing it
        for index, face in enumerate(self.faces):
            for k, a in enumerate(face):
                b = face[(k + 1) % len(face)]
                self.neighbours.setdefault((min(a, b), max(a, b)), []).append(index)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _patch(u0, u1, s0, s1, smooth, lift=0.8, nu=2, ns=2):
    """A part lying on the shell, raised a little. Returns (quads, outline loop)."""
    grid = [[surface(u0 + (u1 - u0) * i / nu, s0 + (s1 - s0) * j / ns, smooth) for j in range(ns + 1)] for i in range(nu + 1)]
    raised = []
    for i in range(nu + 1):
        row = []
        for j in range(ns + 1):
            along = _sub(grid[min(i + 1, nu)][j], grid[max(i - 1, 0)][j])
            across = _sub(grid[i][min(j + 1, ns)], grid[i][max(j - 1, 0)])
            n = _unit(_cross(along, across))
            p = grid[i][j]
            row.append((p[0] + n[0] * lift, p[1] + n[1] * lift, p[2] + n[2] * lift))
        raised.append(row)
    quads = [[raised[i][j], raised[i + 1][j], raised[i + 1][j + 1], raised[i][j + 1]] for i in range(nu) for j in range(ns)]
    loop = ([raised[i][0] for i in range(nu + 1)] + [raised[nu][j] for j in range(1, ns + 1)]
            + [raised[i][ns] for i in range(nu - 1, -1, -1)] + [raised[0][j] for j in range(ns - 1, 0, -1)])
    return quads, loop


def _cylinder(center, axis, radius, length, sides, keep):
    """Outward-facing faces of a cylinder; ``keep(centroid)`` drops those buried in the shell."""
    faces = []
    half = length / 2
    ring = [(math.cos(2 * math.pi * i / sides) * radius, math.sin(2 * math.pi * i / sides) * radius) for i in range(sides)]
    for i in range(sides):
        (a0, b0), (a1, b1) = ring[i], ring[(i + 1) % sides]
        if axis == "x":
            faces.append([(center[0] - half, center[1] + a1, center[2] + b1), (center[0] + half, center[1] + a1, center[2] + b1),
                          (center[0] + half, center[1] + a0, center[2] + b0), (center[0] - half, center[1] + a0, center[2] + b0)])
        else:
            faces.append([(center[0] + a0, center[1] + half, center[2] + b0), (center[0] + a1, center[1] + half, center[2] + b1),
                          (center[0] + a1, center[1] - half, center[2] + b1), (center[0] + a0, center[1] - half, center[2] + b0)])
    for sign in (-1, 1):
        if axis == "x":
            # only the part of the disc above the axle: the rest is inside the shell
            cap = [(center[0] + sign * half, center[1] + a, center[2] + b) for a, b in ring if b >= -0.01]
        else:
            cap = [(center[0] + a, center[1] + sign * half, center[2] + b) for a, b in ring]
        outward = (sign, 0, 0) if axis == "x" else (0, sign, 0)
        faces.append(cap if _dot(_normal(cap), outward) > 0 else cap[::-1])
    return [f for f in faces if keep(tuple(sum(p[i] for p in f) / len(f) for i in range(3)))]


class Geometry:
    def __init__(self, smooth, stations=36, steps=3, fine=3):
        """``stations`` sections along the mouse, ``steps`` samples between rails, ``fine`` subdivides parts."""
        if smooth:
            us = [i / stations for i in range(stations + 1)]
        else:
            us = [0.0, 0.035, 0.085] + [0.16 + (1.0 - 0.16) * i / 9 for i in range(10)]
            steps = fine = 1
        samples = [j / steps for j in range((RAILS - 1) * steps + 1)]
        self.row = len(samples)  # vertices per section
        self.mesh = Mesh([[surface(u, s, smooth) for s in samples] for u in us])
        wheel = surface(0.775, CROWN, smooth)
        thumb = surface(0.52, 6.0, smooth)
        patches = {
            "left": _patch(0.60, 0.985, CROWN + 0.46, 5.85, smooth, lift=0.5, nu=4 * fine, ns=fine),
            "right": _patch(0.60, 0.985, 1.35, CROWN - 0.46, smooth, lift=0.5, nu=4 * fine, ns=2 * fine),
            "smartshift": _patch(0.60, 0.68, CROWN - 0.30, CROWN + 0.30, smooth, lift=1.0, nu=fine, ns=fine),
            "back": _patch(0.335, 0.435, 6.22, 6.92, smooth, lift=1.1, nu=fine, ns=fine),
            "forward": _patch(0.455, 0.555, 6.22, 6.92, smooth, lift=1.1, nu=fine, ns=fine),
            "gesture": _patch(0.575, 0.645, 6.22, 6.92, smooth, lift=1.1, nu=fine, ns=fine),
            "panel": _patch(0.20, 0.50, 7.16, 7.90, smooth, lift=0.7, nu=3 * fine, ns=fine),
        }
        self.parts = {key: quads for key, (quads, _loop) in patches.items()}
        self.outlines = {key: loop for key, (_quads, loop) in patches.items()}  # wheels have none
        self.parts["middle"] = _cylinder((wheel[0], wheel[1], wheel[2] - 3.0), "x", 9.5, 10.0, 20 if smooth else 14,
                                         keep=lambda c: c[2] > wheel[2] - 2.9)
        self.parts["thumbwheel"] = _cylinder((thumb[0] - 1.0, thumb[1], thumb[2] + 3.0), "y", 5.6, 19.0, 16 if smooth else 10,
                                             keep=lambda c: c[0] < thumb[0] + 0.5 or c[2] > thumb[2] + 6.0)
        self.part_normals = {key: [_normal(face) for face in faces] for key, faces in self.parts.items()}
        self.centres = {}
        for key, faces in self.parts.items():
            points = [p for face in faces for p in face]
            self.centres[key] = tuple(sum(p[i] for p in points) / len(points) for i in range(3))
        self.centres["middle"] = (self.centres["middle"][0], self.centres["middle"][1], self.centres["middle"][2] + 3.0)
        # seams: the split between the main buttons and the line where they end
        along = [0.57 + (1.0 - 0.57) * i / 8 for i in range(9)]
        self.seams = [
            [surface(u, CROWN - 0.42, smooth) for u in along],
            [surface(u, CROWN + 0.42, smooth) for u in along],
            [surface(0.57, 1.2 + (CROWN - 0.42 - 1.2) * i / 6, smooth) for i in range(7)],
            [surface(0.57, CROWN + 0.42 + (5.9 - CROWN - 0.42) * i / 4, smooth) for i in range(5)],
        ]


_geometry = {}


def geometry(style):
    smooth = style not in ("lowpoly", "wire")
    if smooth not in _geometry:
        _geometry[smooth] = Geometry(smooth)
    return _geometry[smooth]


# -- rendering ---------------------------------------------------------------

class Camera:
    def __init__(self, yaw, pitch, scale, origin):
        self.scale = scale
        self.origin = origin
        yaw, pitch = math.radians(yaw), math.radians(pitch)
        cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
        # world-space axes of the view: screen right, screen up, towards the viewer
        self.right = (cy, -sy, 0.0)
        self.up = (sy * sp, cy * sp, cp)
        self.toward = (-sy * cp, -cy * cp, sp)
        self.light = _unit(tuple(0.50 * t + 0.80 * u - 0.38 * r for t, u, r in zip(self.toward, self.up, self.right)))
        self.half = _unit(tuple(a + b for a, b in zip(self.light, self.toward)))

    @staticmethod
    def resized(camera, scale, origin):
        """Copy of ``camera`` with another scale and origin (same viewing direction)."""
        other = Camera.__new__(Camera)
        other.__dict__.update(camera.__dict__)
        other.scale, other.origin = scale, origin
        return other

    def transform(self, p):
        """World point -> (screen x, screen y, depth towards the viewer)."""
        d = (p[0] - _CENTER[0], p[1] - _CENTER[1], p[2] - _CENTER[2])
        depth = _dot(d, self.toward)
        k = self.scale * 420.0 / (420.0 - depth)  # mild perspective
        return self.origin.x() + _dot(d, self.right) * k, self.origin.y() - _dot(d, self.up) * k, depth

    def project(self, p):
        x, y, _depth = self.transform(p)
        return QPointF(x, y)


def _lit(base, normal, camera, boost=0.0, glossy=False):
    diffuse = max(0.0, _dot(normal, camera.light))
    level = (0.40 + 0.78 * diffuse if theme.DARK else 0.80 + 0.26 * diffuse) + boost
    shine = 0.0
    if glossy:
        shine = max(0.0, _dot(normal, camera.half)) ** 26 * (60.0 if theme.DARK else 26.0)
    color = QColor(base)
    return QColor(
        min(255, int(color.red() * level + shine)), min(255, int(color.green() * level + shine)),
        min(255, int(color.blue() * level + shine)),
    )


def anchor(camera, key, style=DEFAULT_STYLE):
    """Screen position of a control's centre (for callout lines)."""
    return camera.project(geometry(style).centres[key])


def paint(painter, camera, style=DEFAULT_STYLE, selected=None, pressed=(), visible=CONTROL_KEYS):
    """Draw the model. ``selected`` is the control being configured, ``pressed`` those held down right now."""
    if style == "studio":
        style = "solid"  # stand-in until the studio image is ready
    geo = geometry(style)
    mesh = geo.mesh
    toward = camera.toward
    projected = [camera.transform(v) for v in mesh.vertices]
    points = [QPointF(x, y) for x, y, _depth in projected]
    facing = [_dot(n, toward) > 1e-6 for n in mesh.normals]
    order = sorted(
        (i for i, front in enumerate(facing) if front),
        key=lambda i: sum(projected[v][2] for v in mesh.faces[i]) / len(mesh.faces[i]),
    )

    edge = QColor(theme.MODEL_EDGE)
    soft_edge = QColor(edge)
    soft_edge.setAlpha(150)
    flat = QColor(theme.CARD if style == "outline" else theme.BG)
    line = QPen(QColor(theme.TEXT if style == "outline" else theme.MODEL_EDGE), 1.6 if style == "outline" else 1.0)
    line.setCapStyle(Qt.PenCapStyle.RoundCap)
    for index in order:
        face = mesh.faces[index]
        polygon = QPolygonF([points[v] for v in face])
        if style == "solid":
            color = _lit(theme.MODEL_BASE, mesh.normals[index], camera, glossy=True)
            painter.setPen(QPen(color, 0.7))  # same colour: closes the hairline gaps between faces
            painter.setBrush(color)
        elif style == "lowpoly":
            painter.setPen(QPen(soft_edge, 0.8))
            painter.setBrush(_lit(theme.MODEL_BASE, mesh.normals[index], camera))
        elif style == "wire":
            painter.setPen(QPen(soft_edge, 0.9))
            painter.setBrush(flat)
        else:  # outline
            painter.setPen(QPen(flat, 0.7))
            painter.setBrush(flat)
        painter.drawPolygon(polygon)
        if style == "outline":
            # an edge is part of the outline when the face behind it looks away from the viewer
            painter.setPen(line)
            for k, a in enumerate(face):
                b = face[(k + 1) % len(face)]
                others = mesh.neighbours[(min(a, b), max(a, b))]
                if len(others) < 2 or not all(facing[o] for o in others):
                    painter.drawLine(points[a], points[b])

    if style == "outline":
        seam = QPen(line)
    elif style == "wire":
        seam = QPen(edge, 1.3)
    else:
        seam = QPen(QColor(theme.BG if theme.DARK else theme.MODEL_EDGE), 1.5)
    painter.setPen(seam)
    for seam_points in geo.seams:
        painter.drawPolyline(QPolygonF([camera.project(p) for p in seam_points]))

    # Controls sit on the shell, so they are drawn after it; order them among themselves.
    parts = []
    for key, faces in geo.parts.items():
        if key in PRESS_ONLY and key not in pressed and key != selected:
            continue
        if key in CONTROL_KEYS and key not in visible:
            continue
        parts.append((_dot(_sub(geo.centres[key], _CENTER), toward), key, faces))
    parts.sort(key=lambda item: item[0])
    lines_only = style in ("wire", "outline")
    for _depth, key, faces in parts:
        metal = key in ("middle", "thumbwheel")
        if key in pressed:
            base, outline, boost = theme.ACCENT_HOVER, QColor(theme.TEXT), 0.22
        elif key == selected:
            base, outline, boost = theme.ACCENT, QColor(theme.ACCENT_HOVER), 0.10
        else:
            base, outline, boost = (theme.MODEL_METAL if metal else theme.MODEL_DECAL), edge, 0.0
        highlighted = key in pressed or key == selected
        visible_faces = sorted(
            ((sum(camera.transform(p)[2] for p in face) / len(face), face, normal)
             for face, normal in zip(faces, geo.part_normals[key]) if _dot(normal, toward) > 1e-6),
            key=lambda item: item[0],
        )
        loop = geo.outlines.get(key)
        for _d, face, normal in visible_faces:
            if lines_only and not highlighted:
                color = QColor(flat)
            else:
                color = _lit(base, normal, camera, boost, glossy=metal and style == "solid")
            if key in PRESS_ONLY:
                color.setAlpha(205)
            if loop is None and style != "solid":
                painter.setPen(QPen(outline if highlighted else (line.color() if lines_only else edge), 1.0))  # wheel ribs
            elif style == "lowpoly":
                painter.setPen(QPen(outline, 0.9))
            else:
                painter.setPen(QPen(color, 0.7))
            painter.setBrush(color)
            painter.drawPolygon(QPolygonF([camera.project(p) for p in face]))
        if loop is not None and visible_faces and (lines_only or highlighted):
            painter.setPen(QPen(outline if highlighted else line.color(), 1.6 if highlighted else line.widthF()))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(QPolygonF([camera.project(p) for p in loop]))
