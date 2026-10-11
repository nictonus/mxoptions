"""Studio render of the mouse model: glossy per-pixel shading.

QPainter rasterises the mesh twice, once with surface normals encoded as colours and once
with material ids. numpy then smooths the normals and lights every pixel (soft key light,
studio reflections, rim light), which gives a smooth product-shot look instead of flat faces."""

import math
import threading

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF

from . import mouse_model, theme

try:
    import numpy as np
except ImportError:  # the app then falls back to the polygon styles
    np = None

BODY, METAL, INSERT, PAD = 1, 2, 3, 4
_MATERIAL_OF = {"middle": METAL, "thumbwheel": METAL, "smartshift": INSERT, "back": INSERT, "forward": INSERT,
                "gesture": INSERT, "panel": PAD}


def available():
    return np is not None


def _unit(v):
    length = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / length for c in v)


def _encode(normal, camera):
    """View-space normal as an RGB colour."""
    x = sum(a * b for a, b in zip(normal, camera.right))
    y = sum(a * b for a, b in zip(normal, camera.up))
    z = sum(a * b for a, b in zip(normal, camera.toward))
    return QColor(int((x * 0.5 + 0.5) * 255), int((y * 0.5 + 0.5) * 255), int((max(-1.0, z) * 0.5 + 0.5) * 255))


_lock = threading.Lock()
_cache = {}


def _fine_geometry():
    """Dense mesh for the render (built once; renders may run on a worker thread)."""
    with _lock:
        if "geo" not in _cache:
            _cache["geo"] = mouse_model.Geometry(True, stations=64, steps=7, fine=4)
        return _cache["geo"]


def _facing(normal, toward):
    return normal[0] * toward[0] + normal[1] * toward[1] + normal[2] * toward[2] > 1e-6


def _bounds(camera, geo, margin):
    points = list(geo.mesh.vertices)
    for key in ("middle", "thumbwheel"):
        points.extend(p for face in geo.parts[key] for p in face)
    xs, ys = zip(*((x, y) for x, y, _depth in map(camera.transform, points)))
    return (int(math.floor(min(xs))) - margin, int(math.floor(min(ys))) - margin,
            int(math.ceil(max(xs))) + margin, int(math.ceil(max(ys))) + margin)


def _rasterise(camera, width, height):
    """Draw the model into a normal buffer and a material buffer."""
    geo = _fine_geometry()
    mesh = geo.mesh
    normals = QImage(width, height, QImage.Format.Format_RGB32)
    materials = QImage(width, height, QImage.Format.Format_RGB32)
    normals.fill(QColor(128, 128, 255))
    materials.fill(QColor(0, 0, 0))
    normal_painter, material_painter = QPainter(normals), QPainter(materials)

    toward = camera.toward
    projected = [camera.transform(v) for v in mesh.vertices]
    points = [QPointF(x, y) for x, y, _d in projected]
    front = [i for i, n in enumerate(mesh.normals) if _facing(n, toward)]
    front.sort(key=lambda i: sum(projected[v][2] for v in mesh.faces[i]) / len(mesh.faces[i]))
    body = QColor(0, 0, BODY)
    material_painter.setPen(QPen(body, 1.0))
    material_painter.setBrush(body)
    for index in front:
        polygon = QPolygonF([points[v] for v in mesh.faces[index]])
        color = _encode(mesh.normals[index], camera)
        normal_painter.setPen(QPen(color, 1.0))  # same colour: closes the hairline gaps between faces
        normal_painter.setBrush(color)
        normal_painter.drawPolygon(polygon)
        material_painter.drawPolygon(polygon)

    parts = []
    for key, faces in geo.parts.items():
        if key not in mouse_model.PRESS_ONLY:
            centre = geo.centres[key]
            parts.append((sum((centre[i] - mouse_model._CENTER[i]) * toward[i] for i in range(3)), key, faces))
    parts.sort(key=lambda item: item[0])
    for _depth, key, faces in parts:
        material = QColor(0, 0, _MATERIAL_OF[key])
        material_painter.setPen(QPen(material, 1.0))
        material_painter.setBrush(material)
        visible = [(sum(camera.transform(p)[2] for p in face) / len(face), face, normal)
                   for face, normal in zip(faces, geo.part_normals[key]) if _facing(normal, toward)]
        visible.sort(key=lambda item: item[0])
        for _d, face, normal in visible:
            color = _encode(normal, camera)
            polygon = QPolygonF([camera.project(p) for p in face])
            normal_painter.setPen(QPen(color, 1.0))
            normal_painter.setBrush(color)
            normal_painter.drawPolygon(polygon)
            material_painter.drawPolygon(polygon)
    normal_painter.end()
    material_painter.end()
    return normals, materials


def _array(image):
    """BGRA bytes of a 32-bit QImage as an (H, W, 4) array (a view: keep the image alive)."""
    return np.frombuffer(image.constBits(), dtype=np.uint8).reshape(image.height(), image.bytesPerLine() // 4, 4)[:, :image.width()]


def _box(a, radius):
    """Box blur of an (H, W, C) array along both axes."""
    if radius < 1:
        return a
    k = 2 * radius + 1
    c = np.cumsum(np.pad(a, ((radius + 1, radius), (0, 0), (0, 0)), mode="edge"), axis=0, dtype=np.float32)
    a = (c[k:] - c[:-k]) / k
    c = np.cumsum(np.pad(a, ((0, 0), (radius + 1, radius), (0, 0)), mode="edge"), axis=1, dtype=np.float32)
    return (c[:, k:] - c[:, :-k]) / k


def _smoothstep(low, high, x):
    t = np.clip((x - low) / (high - low), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _rgb(color):
    c = QColor(color)
    return np.array([c.redF(), c.greenF(), c.blueF()], dtype=np.float32)


def _materials():
    """Per material id: base colour, diffuse strength, reflection strength."""
    dark = theme.DARK
    body = _rgb(theme.mix(theme.MODEL_BASE, "#000000", 0.46) if dark else theme.mix(theme.MODEL_BASE, "#ffffff", 0.25))
    insert = _rgb(theme.mix(theme.MODEL_BASE, "#000000", 0.72) if dark else theme.mix(theme.MODEL_BASE, "#000000", 0.30))
    pad = _rgb(theme.mix(theme.MODEL_BASE, "#000000", 0.58) if dark else theme.mix(theme.MODEL_BASE, "#000000", 0.16))
    metal = _rgb("#9aa0ad" if dark else "#b9bec9")
    base = np.zeros((5, 3), dtype=np.float32)
    base[BODY], base[METAL], base[INSERT], base[PAD] = body, metal, insert, pad
    diffuse = np.array([0.0, 0.95, 0.55, 0.80, 0.90], dtype=np.float32)
    gloss = np.array([0.0, 0.62, 1.35, 0.95, 0.16], dtype=np.float32) * (1.0 if dark else 0.55)
    return base, diffuse, gloss


def _shade(normals, materials, blur):
    n = _array(normals)[..., 2::-1].astype(np.float32) / 127.5 - 1.0
    ids = _array(materials)[..., 0]
    mask = (ids > 0).astype(np.float32)[..., None]
    n = _box(n * mask, blur) / np.maximum(_box(mask, blur), 1e-3)
    n /= np.maximum(np.sqrt((n * n).sum(axis=2, keepdims=True)), 1e-4)
    nx, ny, nz = n[..., 0], n[..., 1], np.clip(n[..., 2], 0.0, 1.0)

    base, diffuse_k, gloss_k = _materials()
    base, diffuse_k, gloss_k = base[ids], diffuse_k[ids], gloss_k[ids]

    key = _unit((-0.50, 0.66, 0.56))
    fill = _unit((0.74, 0.12, 0.66))
    diffuse = np.clip(nx * key[0] + ny * key[1] + nz * key[2], 0.0, 1.0) \
        + 0.22 * np.clip(nx * fill[0] + ny * fill[1] + nz * fill[2], 0.0, 1.0)

    # mirror direction of the view ray, and what the "studio" shows there
    rx, ry, rz = 2.0 * nz * nx, 2.0 * nz * ny, 2.0 * nz * nz - 1.0
    softbox = _smoothstep(0.15, 0.95, ry) * (0.55 + 0.45 * _smoothstep(-0.9, 0.2, -rx))
    strip = np.exp(-((ry - 0.62) / 0.075) ** 2) * _smoothstep(-0.95, -0.15, rx) * _smoothstep(0.75, 0.15, rx)
    low_strip = np.exp(-((ry - 0.24) / 0.05) ** 2) * _smoothstep(-0.9, -0.3, rx) * _smoothstep(0.5, 0.0, rx) * 0.35
    side = np.exp(-((rx - 0.80) / 0.10) ** 2) * _smoothstep(-0.2, 0.5, ry) * 0.55
    fresnel = 0.10 + 0.90 * (1.0 - nz) ** 4
    reflection = gloss_k * (0.30 + 0.70 * fresnel) * (0.48 * softbox + 1.20 * strip + low_strip + side)
    # light from behind, so the far edge stands out against the background
    rim = (1.0 - nz) ** 2.6 * np.clip(0.30 + 0.55 * ny + 0.45 * nx, 0.0, 1.0) * (0.85 if theme.DARK else 0.25)

    ambient = 0.20 if theme.DARK else 0.55
    light = (ambient + diffuse_k * diffuse * (0.95 if theme.DARK else 0.55))[..., None]
    tint = np.array([0.92, 0.96, 1.0], dtype=np.float32)  # slightly cool studio light
    color = base * light + (reflection + rim)[..., None] * tint
    out = np.empty(color.shape[:2] + (4,), dtype=np.uint8)
    out[..., :3] = (np.clip(color, 0.0, 1.0) * 255.0).astype(np.uint8)
    out[..., 3] = (ids > 0) * 255
    return out


def render(camera, supersample=2):
    """Shaded model for ``camera``: returns (QImage with alpha, its top-left corner in view coordinates)."""
    ss = supersample
    geo = _fine_geometry()
    scale = camera.scale * ss
    left, top, right, bottom = _bounds(
        mouse_model.Camera.resized(camera, scale, QPointF(camera.origin.x() * ss, camera.origin.y() * ss)), geo, 4 * ss)
    left, top = left - left % ss, top - top % ss  # keep the crop on whole output pixels
    width = (right - left + ss - 1) // ss * ss
    height = (bottom - top + ss - 1) // ss * ss
    cropped = mouse_model.Camera.resized(camera, scale, QPointF(camera.origin.x() * ss - left, camera.origin.y() * ss - top))
    normals, materials = _rasterise(cropped, width, height)
    pixels = _shade(normals, materials, blur=max(2, int(round(1.25 * scale))))
    image = QImage(pixels.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
    if ss != 1:
        image = image.scaled(width // ss, height // ss, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return image, QPointF(left / ss, top / ss)


def shadow(camera, width, height):
    """Soft shadow the mouse casts on the desk, as a QImage with alpha covering the whole view."""
    geo = _fine_geometry()
    scale = 0.5  # the shadow is blurry anyway: draw it small
    w, h = max(8, int(width * scale)), max(8, int(height * scale))
    rows = len(geo.mesh.vertices) // geo.row
    right = [geo.mesh.vertices[i * geo.row] for i in range(rows)]
    left = [geo.mesh.vertices[i * geo.row + geo.row - 1] for i in range(rows)]
    outline = [(p[0], p[1], 0.0) for p in right + left[::-1]]
    image = QImage(w, h, QImage.Format.Format_RGB32)
    image.fill(QColor(0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255))
    painter.scale(scale, scale)
    painter.drawPolygon(QPolygonF([camera.project(p) for p in outline]))
    painter.end()
    alpha = _array(image)[..., :1].astype(np.float32) / 255.0
    radius = max(2, int(camera.scale * scale * 3.4))
    alpha = _box(_box(alpha, radius), radius)[..., 0]
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[..., 3] = (np.clip(alpha, 0.0, 1.0) * (165 if theme.DARK else 70)).astype(np.uint8)
    image = QImage(out.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
    return image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def paint_overlays(painter, camera, selected=None, pressed=(), visible=mouse_model.CONTROL_KEYS):
    """Seams and the highlighted controls, drawn over the shaded image."""
    geo = _fine_geometry()
    toward = camera.toward
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for seam in geo.seams:
        line = QPolygonF([camera.project(p) for p in seam])
        painter.setPen(QPen(QColor(0, 0, 0, 150 if theme.DARK else 70), 1.3))
        painter.drawPolyline(line)
        painter.setPen(QPen(QColor(255, 255, 255, 22 if theme.DARK else 90), 0.8))
        painter.drawPolyline(line.translated(0.0, 1.2))

    for key in list(pressed) + ([selected] if selected else []):
        if key not in geo.parts or (key in mouse_model.CONTROL_KEYS and key not in visible):
            continue
        held = key in pressed
        fill = QColor(theme.ACCENT_HOVER if held else theme.ACCENT)
        fill.setAlpha(215 if held else 170)
        loop = geo.outlines.get(key)
        if loop is not None:
            normal = geo.part_normals[key][len(geo.part_normals[key]) // 2]
            if not _facing(normal, toward):
                continue
            painter.setPen(QPen(QColor(theme.ACCENT_HOVER), 1.4))
            painter.setBrush(fill)
            painter.drawPolygon(QPolygonF([camera.project(p) for p in loop]))
        else:  # a wheel: tint its visible faces, keeping the shading underneath
            painter.setPen(QPen(fill, 0.6))
            painter.setBrush(fill)
            for face, normal in zip(geo.parts[key], geo.part_normals[key]):
                if _facing(normal, toward):
                    painter.drawPolygon(QPolygonF([camera.project(p) for p in face]))
