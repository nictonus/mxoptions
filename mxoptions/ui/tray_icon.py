"""The icon in the system tray, in a choice of styles.

All but one are plain drawings in a single colour. They are written into the user's icon theme
under "-symbolic" names, and the desktop that draws the tray recolours them for its panel, light
or dark. Where that is not possible the same drawing is rendered here, in the text colour of the
application. The styles tell three things apart: a mouse is there, none is, its battery is low;
the "battery" style shows the level itself."""

import math
import os

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtDBus import QDBusConnection, QDBusMessage
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .. import desktop

STYLES = ("outline", "solid", "master", "battery", "logo")  # "logo": the application's own colourful icon
DEFAULT_STYLE = "outline"
LOW_BATTERY = 15   # percent
BATTERY_STEPS = 5  # how many levels the "battery" style tells apart
PREFIX = "mxoptions-tray-"
ICON_DIR = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                        "icons", "hicolor", "scalable", "status")
_INK, _RED = "#232629", "#da4453"  # what the drawings carry until the desktop puts its own colours in
_HEAD = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 22 22">\n'
    '  <!-- MX Options in the system tray. The desktop recolours what carries the ColorScheme classes. -->\n'
    '  <style id="current-color-scheme" type="text/css">\n'
    f'    .ColorScheme-Text {{ color: {_INK}; }}\n    .ColorScheme-NegativeText {{ color: {_RED}; }}\n  </style>\n')
_LINE = ('  <g class="ColorScheme-Text" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" '
         'stroke-linejoin="round"%s>%s</g>\n')
_FILL = '  <g class="ColorScheme-Text" fill="currentColor" fill-rule="evenodd"%s>%s</g>\n'
_DOT = '  <circle class="ColorScheme-NegativeText" fill="currentColor" cx="17.2" cy="17.6" r="3.1"/>\n'
_BODY = '<path d="M11 2.2a5 5 0 0 0-5 5v7.6a5 5 0 0 0 10 0V7.2a5 5 0 0 0-5-5z"/>'
_SHAPES = {  # style -> (how it is drawn, its paths): any mouse from above, the same filled in, an MX Master
    "outline": (_LINE, _BODY + '<path d="M6.2 10.2h9.6"/><path d="M11 5.3v2.4" stroke-width="1.8"/>'),
    "solid": (_FILL, '<path d="M11 1.6a5.6 5.6 0 0 0-5.6 5.6v2.3h11.2V7.2A5.6 5.6 0 0 0 11 1.6zM10.2 4.4h1.6v3.3h-1.6z"/>'
                     '<path d="M5.4 10.9v3.9a5.6 5.6 0 0 0 11.2 0v-3.9z"/>'),
    "master": (_LINE, '<path d="M12.4 2.2c-2.7 0-4.9 2.2-4.9 4.9v3.4L4.9 13c-.9.9-.9 2.3 0 3.2l1.9 1.9c1.1 1.1 2.6 1.7 4.2 1.7h.7'
                      'c3.2 0 5.7-2.6 5.7-5.8V7.1c0-2.7-2.2-4.9-5-4.9z"/><path d="M7.7 10.5h9.5"/>'
                      '<path d="M12.4 5.2v2.4" stroke-width="1.8"/>'),
}
_FAINT = ' opacity="0.4"'


def _level(share):
    """The inside of the plain mouse, filled from the bottom up to ``share`` of its height."""
    centre, top, bottom, radius = 11.0, 4.0, 18.0, 3.2
    cut = bottom - share * (bottom - top)
    if cut >= bottom - radius:  # still within the round end
        half = math.sqrt(max(0.0, radius * radius - (cut - (bottom - radius)) ** 2))
        return f'<path d="M{centre - half:.2f} {cut:.2f}H{centre + half:.2f}A{radius} {radius} 0 0 1 {centre - half:.2f} {cut:.2f}z"/>'
    if cut > top + radius:      # in the straight part
        return f'<path d="M{centre - radius} {cut:.2f}H{centre + radius}V{bottom - radius}a{radius} {radius} 0 0 1 {-2 * radius} 0z"/>'
    half = math.sqrt(max(0.0, radius * radius - ((top + radius) - cut) ** 2))
    return (f'<path d="M{centre - half:.2f} {cut:.2f}A{radius} {radius} 0 0 0 {centre - radius} {top + radius}V{bottom - radius}'
            f'a{radius} {radius} 0 0 0 {2 * radius} 0V{top + radius}A{radius} {radius} 0 0 0 {centre + half:.2f} {cut:.2f}z"/>')


def keys():
    """Every drawing there is: "outline", "outline-off", "outline-low", ..., "battery-1" .. "battery-5"."""
    found = [f"{style}{state}" for style in _SHAPES for state in ("", "-off", "-low")]
    return found + ["battery-off", "battery-low"] + [f"battery-{step}" for step in range(1, BATTERY_STEPS + 1)]


def svg(key):
    """The drawing for ``key`` as SVG text."""
    style, _dash, state = key.partition("-")
    if style == "battery":
        if state == "off":
            return _HEAD + _LINE % (_FAINT, _BODY) + "</svg>\n"
        if state == "low":
            return (_HEAD + _LINE % ("", _BODY)
                    + f'  <g class="ColorScheme-NegativeText" fill="currentColor">{_level(0.14)}</g>\n</svg>\n')
        return _HEAD + _LINE % ("", _BODY) + _FILL % ("", _level(int(state) / BATTERY_STEPS)) + "</svg>\n"
    drawn, paths = _SHAPES[style]
    return _HEAD + drawn % (_FAINT if state == "off" else "", paths) + (_DOT if state == "low" else "") + "</svg>\n"


def key_for(style, state, charging):
    """Which drawing shows what the engine reports, in ``style`` (``charging``: the battery statuses that count as such)."""
    if style not in _SHAPES and style != "battery":
        return None  # the application's own icon says nothing about the mouse
    battery = state.get("battery") if state["connected"] else None
    if not state["connected"]:
        return f"{style}-off"
    if battery and battery[0] <= LOW_BATTERY and battery[1] not in charging:
        return f"{style}-low"
    if style == "battery":
        if not battery:
            return "outline"  # this mouse does not tell its level
        return f"battery-{max(1, min(BATTERY_STEPS, math.ceil(battery[0] / (100 / BATTERY_STEPS))))}"
    return style


def install(folder=ICON_DIR):
    """Write the drawings into the icon theme (only what is missing or has changed) and remove what
    earlier versions left there. Returns whether anything changed; False as well if it cannot be written."""
    wanted = {f"{PREFIX}{key}-symbolic.svg": svg(key) for key in keys()}
    changed = False
    try:
        os.makedirs(folder, exist_ok=True)
        for name in os.listdir(folder):
            if name.startswith(PREFIX) and name not in wanted:
                os.remove(os.path.join(folder, name))
                changed = True
        for name, text in wanted.items():
            path = os.path.join(folder, name)
            try:
                with open(path) as f:
                    if f.read() == text:
                        continue
            except OSError:
                pass
            with open(path, "w") as f:
                f.write(text)
            changed = True
    except OSError:
        return False
    return changed


def announce():
    """Tell KDE programs (the panel among them) that icons have changed, so that they look again."""
    message = QDBusMessage.createSignal("/KIconLoader", "org.kde.KIconLoader", "iconChanged")
    message.setArguments([0])
    QDBusConnection.sessionBus().send(message)


def _drawn(key, ink=None, size=None):
    """The drawing rendered here: as an icon in the application's text colour, or one pixmap of ``size``."""
    ink = ink or QGuiApplication.palette().color(QPalette.ColorRole.WindowText).name()
    renderer = QSvgRenderer(QByteArray(svg(key).replace(_INK, ink).encode()))
    icon = QIcon()
    for edge in ((size,) if size else (16, 22, 24, 32, 44, 48, 64)):
        pixmap = QPixmap(edge, edge)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, edge, edge))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def icon(style, state, charging):
    """The tray icon for what the engine reports."""
    key = key_for(style, state, charging)
    if key is None:
        return QIcon(desktop.ICON_PATH)
    name = f"{PREFIX}{key}-symbolic"
    return QIcon.fromTheme(name) if QIcon.hasThemeIcon(name) else _drawn(key)


def preview(style, ink, size=26):
    """What a style looks like, for the settings page."""
    if style == "logo":
        return QIcon(desktop.ICON_PATH)
    return _drawn("battery-3" if style == "battery" else style, ink, size)
