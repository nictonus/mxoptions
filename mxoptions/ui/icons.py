"""Icons by name.

The program asks for icons by the names Linux desktops share (the freedesktop ones, and a few of
KDE's), and on Linux the desktop's own icon theme has them. Windows has an icon for about half of
those names; for the rest there is a table here of the glyphs of its own icon font that stand for
them, so that all of them look of a piece."""

import sys

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QIconEngine, QPainter, QPixmap

from . import theme

# icon name -> its glyph in "Segoe Fluent Icons" (Windows 11) and "Segoe MDL2 Assets" (Windows 10), which
# share these places
GLYPHS = {
    "accessories-text-editor": 0xE70F, "applications-all": 0xE71D, "applications-graphics": 0xE790,
    "applications-office": 0xE8A5, "audio-headphones": 0xE7F6, "bookmarks": 0xE734, "brightness-high": 0xE706,
    "brightness-low": 0xEC8A, "clock": 0xE917, "color-picker": 0xEF3C, "configure": 0xE713, "crosshairs": 0xE81D,
    "dialog-cancel": 0xE733, "dialog-close": 0xE711, "dialog-messages": 0xE8BD, "dialog-ok": 0xE73E, "draw-brush": 0xE771,
    "face-smile": 0xE76E, "focus-windows": 0xE7C4, "folder-favorites": 0xE728, "games-highscores": 0xE7FC,
    "go-first": 0xE892, "go-last": 0xE893, "internet-web-browser": 0xE774, "keyboard-enter": 0xE751, "klipper": 0xF0E3,
    "love": 0xEB51, "mail-message": 0xE715, "microphone-sensitivity-high": 0xE720, "microphone-sensitivity-muted": 0xF781,
    "network-connect": 0xE703, "notifications-disabled": 0xE7ED, "preferences-other": 0xE9E9, "preferences-system": 0xE713,
    "preferences-system-bluetooth": 0xE702, "preferences-system-windows": 0xE7C4, "preferences-system-windows-move": 0xE7C2,
    "redshift-status-on": 0xF08C, "select-rectangular": 0xF406, "speedometer": 0xEC4A, "starred": 0xE735, "system-run": 0xE756,
    "tab-close": 0xE8BB, "tab-new": 0xE710, "text-x-script": 0xE943, "transform-move": 0xE7C2, "transform-scale": 0xE740,
    "user-desktop": 0xE7F4, "user-identity": 0xE77B, "video-display": 0xE7F4, "view-app-grid": 0xF0E2, "view-calendar": 0xE787,
    "view-grid": 0xE80A, "view-list-icons": 0xE8FD, "view-split-left-right": 0xE90C, "window": 0xE923,
    "window-keep-above": 0xE718, "window-maximize": 0xE922, "window-minimize": 0xE921,
}
_family = False  # the icon font, once looked for (None: there is none)


def _font():
    global _family
    if _family is False:
        _family = None
        if sys.platform == "win32":
            there = QFontDatabase.families()
            _family = next((name for name in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if name in there), None)
    return _family


class _Glyph(QIconEngine):
    """Draws one glyph of the icon font, in the colour of the text around it."""

    def __init__(self, code):
        super().__init__()
        self._code = code

    def paint(self, painter, rect, mode, state):
        font = QFont(_font())
        font.setPixelSize(max(6, int(min(rect.width(), rect.height()) * 0.9)))
        color = QColor(theme.TEXT or "#000000")
        if mode == QIcon.Mode.Disabled:
            color.setAlphaF(0.4)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, chr(self._code))
        painter.restore()

    def pixmap(self, size, mode, state):
        made = QPixmap(size)
        made.fill(Qt.GlobalColor.transparent)
        painter = QPainter(made)
        self.paint(painter, QRect(QPoint(0, 0), size), mode, state)
        painter.end()
        return made

    def clone(self):
        return _Glyph(self._code)


def has(name):
    """Whether there is an icon called ``name`` here."""
    return bool(name) and (QIcon.hasThemeIcon(name) or (name in GLYPHS and _font() is not None))


def icon(name):
    """The icon called ``name`` (one that draws nothing if there is none: see has())."""
    if name and QIcon.hasThemeIcon(name):
        return QIcon.fromTheme(name)
    if name in GLYPHS and _font() is not None:
        return QIcon(_Glyph(GLYPHS[name]))
    return QIcon()
