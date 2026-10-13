"""Colours and the application stylesheet.

The colour names below are module globals that custom-painted widgets read at paint
time; :func:`set_theme` swaps them and :func:`apply` restyles the running application."""

from PySide6.QtGui import QColor, QPalette

THEMES = {
    "graphite": dict(dark=True, bg="#14151a", sidebar="#101116", card="#1d1f26", card_hover="#252832",
                     border="#2b2e38", text="#eceef3", muted="#8d93a3"),
    "midnight": dict(dark=True, bg="#0e1320", sidebar="#0a0e18", card="#161c2c", card_hover="#1e2538",
                     border="#293149", text="#e9edf7", muted="#8792ad"),
    "black": dict(dark=True, bg="#000000", sidebar="#050506", card="#0f0f12", card_hover="#18181d",
                  border="#25252c", text="#f2f2f5", muted="#8b8b96"),
    "light": dict(dark=False, bg="#f2f3f7", sidebar="#e8eaf0", card="#ffffff", card_hover="#eef0f5",
                  border="#d5d9e2", text="#1b1d23", muted="#697080"),
}
THEME_LABELS = [("graphite", "Graphite"), ("midnight", "Midnight"), ("black", "Black"), ("light", "Daylight")]
ACCENTS = ["#2fd6a7", "#4c9bff", "#8f7bff", "#ff6fa8", "#ff9d47", "#f2c744", "#f2605d", "#9bd94a"]
DEFAULT_THEME = "graphite"
DEFAULT_ACCENT = ACCENTS[0]

WARN = "#f0b450"
DANGER = "#ef6a6a"

# Filled in by set_theme().
REVISION = 0  # bumped on every change, so cached drawings know they are stale
DARK = True
BG = SIDEBAR = CARD = CARD_HOVER = BORDER = TEXT = MUTED = ""
ACCENT = ACCENT_DIM = ACCENT_HOVER = ACCENT_TEXT = ACCENT_INK = ""
DISABLED = TRACK = ""
MODEL_BASE = MODEL_EDGE = MODEL_DECAL = MODEL_METAL = ""
BUBBLE = BUBBLE_BORDER = ""


def mix(a, b, amount):
    """Blend colour ``a`` towards ``b`` (0 = a, 1 = b); returns "#rrggbb"."""
    a, b = QColor(a), QColor(b)
    return QColor(
        round(a.red() + (b.red() - a.red()) * amount),
        round(a.green() + (b.green() - a.green()) * amount),
        round(a.blue() + (b.blue() - a.blue()) * amount),
    ).name()


def _luminance(color):
    c = QColor(color)
    return (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255.0


def readable_on(color):
    """Text colour that stays legible on top of ``color``."""
    return "#0b1512" if _luminance(color) > 0.55 else "#ffffff"


def _legible(color, dark):
    """``color`` as a text or line colour: moved towards white (dark themes) or black until it stands out."""
    result, amount = (color, 0.0) if dark else (mix(color, "#000000", 0.38), 0.38)
    while amount < 0.9 and (_luminance(result) < 0.50 if dark else _luminance(result) > 0.50):
        amount += 0.06
        result = mix(color, "#ffffff" if dark else "#000000", amount)
    return result


def valid_accent(value):
    return isinstance(value, str) and QColor(value).isValid() and value.startswith("#")


def set_theme(name=DEFAULT_THEME, accent=DEFAULT_ACCENT):
    spec = THEMES.get(name) or THEMES[DEFAULT_THEME]
    accent = accent if valid_accent(accent) else DEFAULT_ACCENT
    dark = spec["dark"]
    values = {
        "REVISION": REVISION + 1,
        "DARK": dark,
        "BG": spec["bg"], "SIDEBAR": spec["sidebar"], "CARD": spec["card"], "CARD_HOVER": spec["card_hover"],
        "BORDER": spec["border"], "TEXT": spec["text"], "MUTED": spec["muted"],
        "ACCENT": accent,
        "ACCENT_DIM": mix(accent, spec["bg"], 0.5),
        "ACCENT_HOVER": mix(accent, "#ffffff", 0.18),
        "ACCENT_TEXT": readable_on(accent),
        # accent for text, outlines and thin lines: adjusted until it reads against the background
        "ACCENT_INK": _legible(accent, dark),
        "DISABLED": mix(spec["muted"], spec["bg"], 0.45),
        "TRACK": mix(spec["border"], spec["text"], 0.12),
        "MODEL_BASE": mix(spec["card_hover"], spec["text"], 0.10 if dark else 0.04),
        "MODEL_EDGE": mix(spec["border"], spec["text"], 0.22),
        "MODEL_DECAL": mix(spec["bg"], spec["text"], 0.06 if dark else 0.16),
        "MODEL_METAL": "#aeb4c2" if dark else "#8f96a6",
        "BUBBLE": "#17181e" if dark else "#ffffff",
        "BUBBLE_BORDER": "#3a3e4b" if dark else "#cfd4de",
    }
    globals().update(values)


def palette():
    result = QPalette()
    roles = {
        QPalette.ColorRole.Window: BG, QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: CARD, QPalette.ColorRole.AlternateBase: CARD_HOVER,
        QPalette.ColorRole.Text: TEXT, QPalette.ColorRole.Button: CARD_HOVER,
        QPalette.ColorRole.ButtonText: TEXT, QPalette.ColorRole.Highlight: ACCENT_DIM,
        QPalette.ColorRole.HighlightedText: TEXT, QPalette.ColorRole.ToolTipBase: CARD,
        QPalette.ColorRole.ToolTipText: TEXT, QPalette.ColorRole.PlaceholderText: MUTED,
        QPalette.ColorRole.Link: ACCENT_INK,
    }
    for role, color in roles.items():
        result.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        result.setColor(QPalette.ColorGroup.Disabled, role, QColor(DISABLED))
    return result


def apply(app):
    """Restyle the running application with the current colours."""
    app.setPalette(palette())
    app.setStyleSheet(stylesheet())
    for widget in app.allWidgets():
        widget.update()


def stylesheet():
    banner_bg, banner_border, banner_text = ("#3a2f17", "#6b5522", "#f3d9a0") if DARK else ("#fff3d2", "#e6cd88", "#6b5010")
    return f"""
* {{ outline: none; }}
QWidget {{ color: {TEXT}; font-size: 10.5pt; }}
QMainWindow, QDialog, #root {{ background: {BG}; }}
QToolTip {{ background: {CARD}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 8px; }}

#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
#deviceName {{ font-size: 13pt; font-weight: 600; }}
#deviceStatus {{ color: {MUTED}; font-size: 9.5pt; }}
#navButton {{
    text-align: left; padding: 10px 14px; border: none; border-radius: 9px;
    color: {MUTED}; font-size: 10.5pt; font-weight: 500; background: transparent;
}}
#navButton:hover {{ background: {CARD}; color: {TEXT}; }}
#navButton:checked {{ background: {CARD_HOVER}; color: {TEXT}; border-left: 3px solid {ACCENT_INK}; padding-left: 11px; }}

#pageTitle {{ font-size: 18pt; font-weight: 600; }}
#pageSubtitle {{ color: {MUTED}; }}
#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 14px; }}
#cardTitle {{ font-size: 11.5pt; font-weight: 600; }}
#rowTitle {{ font-weight: 500; }}
#rowHint, #hint {{ color: {MUTED}; font-size: 9.5pt; }}
#infoBubble {{ background: {CARD_HOVER}; border: 1px solid {mix(BORDER, TEXT, 0.18)}; border-radius: 10px; }}
#infoText {{ color: {TEXT}; font-size: 9.5pt; }}
#valueLabel {{ color: {ACCENT_INK}; font-weight: 600; }}
#pairingCode {{ color: {ACCENT_INK}; font-family: monospace; font-size: 22pt; font-weight: 700; letter-spacing: 3px; }}
#separator {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
#banner {{ background: {banner_bg}; border: 1px solid {banner_border}; border-radius: 10px; color: {banner_text}; padding: 9px 14px; }}

QPushButton {{
    background: {CARD_HOVER}; border: 1px solid {BORDER}; border-radius: 9px; padding: 7px 14px; color: {TEXT};
}}
QPushButton:hover {{ border-color: {ACCENT_DIM}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {DISABLED}; background: {CARD}; }}
QPushButton#primary {{ background: {ACCENT}; color: {ACCENT_TEXT}; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#flat {{ background: transparent; border: none; color: {MUTED}; padding: 4px 8px; }}
QPushButton#flat:hover {{ color: {ACCENT_INK}; }}
QPushButton#danger {{ background: transparent; border: 1px solid {BORDER}; color: {DANGER}; }}
QPushButton#danger:hover {{ border-color: {DANGER}; }}
QPushButton#tool {{ background: transparent; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px 8px; min-width: 18px; }}
QPushButton#tool:hover {{ border-color: {ACCENT_DIM}; background: {CARD_HOVER}; }}
QPushButton#tool:disabled {{ color: {DISABLED}; border-color: {CARD_HOVER}; }}
QPushButton#iconChoice {{ background: transparent; border: 1px solid {BORDER}; border-radius: 10px; padding: 0; }}
QPushButton#iconChoice:hover {{ border-color: {ACCENT_DIM}; background: {CARD_HOVER}; }}
QPushButton#iconChoice:checked {{ border: 2px solid {ACCENT}; background: {CARD_HOVER}; }}

#segment {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 10px; }}
#segmentButton {{ background: transparent; border: none; border-radius: 8px; padding: 6px 14px; color: {MUTED}; }}
#segmentButton:hover {{ color: {TEXT}; }}
#segmentButton:checked {{ background: {ACCENT}; color: {ACCENT_TEXT}; font-weight: 600; }}
#segmentButton:disabled {{ color: {DISABLED}; }}
#segmentButton:checked:disabled {{ background: {BORDER}; color: {MUTED}; }}

#chip {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; text-align: left; padding: 0; }}
#chip:hover {{ background: {CARD_HOVER}; border-color: {ACCENT_DIM}; }}
#chip:checked {{ border: 2px solid {ACCENT_INK}; background: {CARD_HOVER}; }}
#chipTitle {{ color: {MUTED}; font-size: 9pt; background: transparent; }}
#chipValue {{ font-weight: 600; background: transparent; }}

#picker {{ background: {SIDEBAR}; border-left: 1px solid {BORDER}; }}
#pickerTitle {{ font-size: 13pt; font-weight: 600; }}
#fieldLabel {{ color: {MUTED}; font-size: 9pt; font-weight: 600; }}
#categoryLabel {{ color: {MUTED}; font-size: 8.5pt; font-weight: 600; padding: 12px 4px 4px 4px; }}
#actionRow {{ text-align: left; padding: 8px 10px; border: 1px solid transparent; border-radius: 9px; background: transparent; }}
#actionRow:hover {{ background: {CARD}; }}
#actionRow:checked {{ background: {CARD_HOVER}; border: 1px solid {ACCENT_INK}; color: {ACCENT_INK}; font-weight: 600; }}
#recorder {{ background: {BG}; border: 1px dashed {BORDER}; border-radius: 9px; padding: 10px; color: {MUTED}; }}
#recorder:focus {{ border: 1px dashed {ACCENT_INK}; color: {TEXT}; }}
#summary {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 9px; padding: 8px 10px; color: {MUTED}; font-size: 9.5pt; }}
#stepRow {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
#stepKind {{ color: {MUTED}; font-size: 9pt; font-weight: 600; min-width: 64px; }}

QLineEdit, QPlainTextEdit, QSpinBox {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 9px; padding: 7px 10px;
    selection-background-color: {ACCENT_DIM}; selection-color: {TEXT};
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{ border-color: {ACCENT_INK}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
QComboBox {{ background: {CARD_HOVER}; border: 1px solid {BORDER}; border-radius: 9px; padding: 6px 12px; min-width: 150px; }}
QComboBox:hover {{ border-color: {ACCENT_DIM}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {CARD}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_DIM};
    selection-color: {TEXT}; padding: 4px; outline: none;
}}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {TEXT if DARK else "#ffffff"}; border: 1px solid {BORDER if not DARK else TEXT};
    width: 16px; height: 16px; margin: -7px 0; border-radius: 9px;
}}
QSlider::handle:horizontal:hover {{ background: {ACCENT}; border-color: {ACCENT}; }}
QSlider::sub-page:horizontal:disabled {{ background: {TRACK}; }}
QSlider::handle:horizontal:disabled {{ background: {DISABLED}; border-color: {DISABLED}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {TRACK}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; height: 0; }}
QMenu {{ background: {CARD}; border: 1px solid {BORDER}; padding: 6px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {CARD_HOVER}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
#popup {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
#iconCell {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 4px; }}
#iconCell:hover {{ background: {CARD_HOVER}; border-color: {ACCENT_DIM}; }}
"""


set_theme()
