"""A keyboard drawn from above, as the Keys page shows it, the way Logi Options+ shows one: every key with its
legend, the keys whose action can be set here outlined where they sit.

Two generic layouts, built from rectangles in key units: "full" (navigation keys and a number pad, the F-row of the
MX Mechanical) and "compact" (a Mini: no number pad, navigation keys down the right side). A key is drawn where it
is only when that is known: from the F-key the keyboard says it is on (getCidInfo), from a control id that names a
key every layout has in the same place (CID_SLOTS), or, for a model whose picture in Options+ shows it, from that
(F_ROWS, MODEL_SLOTS). Any other key has no place on the picture."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen

from . import icons, theme

LAYOUTS = {"full": 22.5, "compact": 16.5}  # layout -> width of the board, in key units
ROWS = 6.5  # height of the board in key units: the F-row, a gap, five rows
MAIN_TOP = 1.5  # key units: where the rows under the F-row begin
MAX_UNIT = 44.0  # px: the largest a key unit is drawn
# Control ids of keys every layout has in the same place (Solaar's names for them) -> slot
CID_SLOTS = {
    0x010B: "grave", 0x010C: "tab", 0x010D: "caps", 0x010E: "lshift", 0x010F: "lctrl", 0x0110: "lwin",
    0x0111: "lalt", 0x0112: "ralt", 0x0113: "rwin", 0x0114: "rctrl", 0x0115: "rshift", 0x0116: "insert",
    0x0117: "delete", 0x0118: "home", 0x0119: "end", 0x011A: "pgup", 0x011B: "pgdn", 0x011E: "backslash",
}
# The keys of the F-row, F1 to F12, of a model whose picture in Logi Options+ shows them: used only while no key of
# the keyboard says which F-key it is on
F_ROWS = {"MX Mechanical": (0x00C7, 0x00C8, 0x00E2, 0x00E3, 0x0103, 0x0108, 0x010A, 0x011C, 0x00E4, 0x00E5, 0x00E6,
                            0x00E7)}
# Further keys of a model, where its picture in Logi Options+ has them ("full" layout only): after F12, over the
# navigation keys, over the number pad
MODEL_SLOTS = {"MX Mechanical": {0x00E8: "vol_down", 0x00E9: "vol_up", 0x00D1: "es1", 0x00D2: "es2", 0x00D3: "es3",
                                 0x000A: "np1", 0x006E: "np2", 0x00D4: "np3", 0x006F: "np4", 0x0034: "fn"}}
# What the keys say on them: keycap legends, the same in every language
EXTRA_LEGENDS = {"vol_down": "−", "vol_up": "+", "es1": "1", "es2": "2", "es3": "3"}


def _row(y, start, keys):
    """Keys side by side from ``start``: each (slot, width in units, legend)."""
    result, x = [], start
    for slot, width, legend in keys:
        result.append((slot, QRectF(x, y, width, 1), legend))
        x += width
    return result


def _plain(text):
    """Keys of one unit with no slot, one for each character of ``text``."""
    return [("", 1, char) for char in text]


def _main(right_shift, bottom):
    """The main block, 15 units wide: five rows under the F-row; ``bottom`` the keys of the bottom row."""
    y = [MAIN_TOP + i for i in range(5)]
    return (_row(y[0], 0, [("grave", 1, "`")] + _plain("1234567890-=") + [("backspace", 2, "⌫")])
            + _row(y[1], 0, [("tab", 1.5, "Tab")] + _plain("QWERTYUIOP[]") + [("backslash", 1.5, "\\")])
            + _row(y[2], 0, [("caps", 1.75, "Caps")] + _plain("ASDFGHJKL;'") + [("enter", 2.25, "Enter")])
            + _row(y[3], 0, [("lshift", 2.25, "Shift")] + _plain("ZXCVBNM,./") + [("rshift", right_shift, "Shift")])
            + _row(y[4], 0, bottom))


def board(layout):
    """Every key of ``layout`` as (slot or "", rect in key units, legend)."""
    if layout == "compact":
        keys = [("esc", QRectF(0, 0, 1, 1), "Esc")] + [
            (f"f{n}", QRectF(1.25 + (n - 1) + 0.25 * ((n - 1) // 4), 0, 1, 1), f"F{n}") for n in range(1, 13)]
        keys.append(("delete", QRectF(15.5, 0, 1, 1), "Del"))
        keys += _main(1.75, [("lctrl", 1.25, "Ctrl"), ("lwin", 1.25, "Start"), ("lalt", 1.25, "Alt"), ("", 6.25, ""),
                             ("ralt", 1, "Alt"), ("rctrl", 1, "Ctrl")])
        keys += [(slot, QRectF(15.5, MAIN_TOP + row, 1, 1), legend)
                 for row, (slot, legend) in enumerate((("home", "Home"), ("pgup", "PgUp"), ("pgdn", "PgDn"), ("end", "End")))]
        keys += [("", QRectF(14.5, MAIN_TOP + 3, 1, 1), "↑")]
        keys += [("", QRectF(13.5 + i, MAIN_TOP + 4, 1, 1), arrow) for i, arrow in enumerate("←↓→")]
        return keys
    # the F-row as on the MX Mechanical: Esc, F1 to F12 and two keys more in one run, as wide as the main block
    keys = [("esc", QRectF(0, 0, 1, 1), "Esc")] + [(f"f{n}", QRectF(n, 0, 1, 1), f"F{n}") for n in range(1, 13)]
    keys += [("vol_down", QRectF(13, 0, 1, 1), ""), ("vol_up", QRectF(14, 0, 1, 1), "")]
    keys += [(f"es{i + 1}", QRectF(15.25 + i, 0, 1, 1), "") for i in range(3)]  # over the navigation keys
    keys += [(f"np{i + 1}", QRectF(18.5 + i, 0, 1, 1), "") for i in range(4)]  # over the number pad
    keys += _main(2.75, [("lctrl", 1.25, "Ctrl"), ("lwin", 1.25, "Start"), ("lalt", 1.25, "Alt"), ("", 6.25, ""),
                         ("ralt", 1.25, "Alt"), ("fn", 1.25, "Fn"), ("rwin", 1.25, "Start"), ("rctrl", 1.25, "Ctrl")])
    for row, slots in enumerate(((("insert", "Ins"), ("home", "Home"), ("pgup", "PgUp")),
                                 (("delete", "Del"), ("end", "End"), ("pgdn", "PgDn")))):
        keys += [(slot, QRectF(15.25 + i, MAIN_TOP + row, 1, 1), legend) for i, (slot, legend) in enumerate(slots)]
    keys += [("", QRectF(16.25, MAIN_TOP + 3, 1, 1), "↑")]
    keys += [("", QRectF(15.25 + i, MAIN_TOP + 4, 1, 1), arrow) for i, arrow in enumerate("←↓→")]
    pad = 18.5  # the number pad
    keys += [("", QRectF(pad + i, MAIN_TOP, 1, 1), legend) for i, legend in enumerate(("Num", "/", "*", "−"))]
    keys += [("", QRectF(pad + i, MAIN_TOP + row, 1, 1), legend)
             for row, legends in ((1, "789"), (2, "456"), (3, "123")) for i, legend in enumerate(legends)]
    keys += [("", QRectF(pad + 3, MAIN_TOP + 1, 1, 2), "+"), ("", QRectF(pad + 3, MAIN_TOP + 3, 1, 2), "Enter"),
             ("", QRectF(pad, MAIN_TOP + 4, 2, 1), "0"), ("", QRectF(pad + 2, MAIN_TOP + 4, 1, 1), ".")]
    return keys


def layout_for(name):
    """The layout drawn for a keyboard called ``name``: compact for a Mini, else full."""
    return "compact" if name and "Mini" in name else "full"


def slots(keys, layout, name=None):
    """Key id -> the slot it is drawn in, for the keys of ``keys`` whose place is known. Keys are taken in the
    order of their control ids, so neither the order of ``keys`` nor anything else about them changes it: a slot two
    keys claim stays with the lower id. Keys without a known place are left out.

    A key's place, the first that applies: the F-key it says it is on (F1 to F12); while no key says one (1 to 16),
    its F-key in F_ROWS for this model; CID_SLOTS; on the full layout, MODEL_SLOTS for this model."""
    there = {slot for slot, _rect, _legend in board(layout) if slot}
    said = any(1 <= (key.get("pos") or 0) <= 16 for key in keys)
    f_row = {} if said else {cid: f"f{n}" for n, cid in enumerate(F_ROWS.get(name, ()), 1)}
    model = MODEL_SLOTS.get(name, {}) if layout == "full" else {}
    taken, result = set(), {}
    for key in sorted(keys, key=lambda key: key["cid"]):
        pos, cid = key.get("pos") or 0, key["cid"]
        slot = f"f{pos}" if 1 <= pos <= 12 else f_row.get(cid) or CID_SLOTS.get(cid) or model.get(cid)
        if slot in there and slot not in taken:
            taken.add(slot)
            result[key["id"]] = slot
    return result


def fit(area, layout):
    """The board's rect inside ``area``, centred, as large as fits (at most MAX_UNIT px a key), and the size of one
    key unit in px."""
    width = LAYOUTS[layout] + 0.8  # with the case's rim on both sides
    unit = min(area.width() / width, area.height() / (ROWS + 0.8), MAX_UNIT)
    rect = QRectF(0, 0, LAYOUTS[layout] * unit, ROWS * unit)
    rect.moveCenter(area.center())
    return rect, unit


def _cap(rect, unit, place):
    """A key's cap in px: ``place`` in key units, inside board ``rect``, with a gap all round."""
    gap = unit * 0.07
    return QRectF(rect.left() + place.left() * unit + gap, rect.top() + place.top() * unit + gap,
                  place.width() * unit - 2 * gap, place.height() * unit - 2 * gap)


def key_rects(area, layout, slot_of):
    """Key id -> the rect of its cap in px, for the keys of ``slot_of`` (as slots() gives it)."""
    rect, unit = fit(area, layout)
    of_slot = {slot: key_id for key_id, slot in slot_of.items()}
    return {of_slot[slot]: _cap(rect, unit, place) for slot, place, _legend in board(layout) if slot in of_slot}


def key_at(area, layout, slot_of, pos):
    """The key id drawn at ``pos``, or None."""
    return next((key_id for key_id, cap in key_rects(area, layout, slot_of).items() if cap.contains(pos)), None)


def paint(painter, area, layout, slot_of, selected=None, hover=None, changed=frozenset(), kept=frozenset(),
          icon_names=None, lit=None):
    """Draw the keyboard inside ``area``; ``slot_of`` as slots() gives it; ``changed`` the ids of keys set to
    something of their own, ``kept`` of those left as they are; ``icon_names`` key id -> the icon on its cap; ``lit``
    True or False: its backlight on (legends glow) or off (legends dim), None: not said. Returns key id -> the rect of
    its cap."""
    rect, unit = fit(area, layout)
    of_slot = {slot: key_id for key_id, slot in slot_of.items()}
    icon_names = icon_names or {}
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rim = unit * 0.4
    painter.setPen(QPen(QColor(theme.MODEL_EDGE), 1.2))
    painter.setBrush(QColor(theme.MODEL_BASE))
    painter.drawRoundedRect(rect.adjusted(-rim, -rim, rim, rim), unit * 0.45, unit * 0.45)
    font = QFont(painter.font())
    font.setPixelSize(max(7, int(unit * 0.24)))
    font.setStyleStrategy(QFont.StyleStrategy.NoSubpixelAntialias)  # small legends: no coloured fringes
    painter.setFont(font)
    rects = {}
    for slot, place, legend in board(layout):
        cap = _cap(rect, unit, place)
        key_id = of_slot.get(slot) if slot else None
        listed, locked = key_id is not None, key_id in kept
        radius = unit * 0.12
        painter.setPen(Qt.PenStyle.NoPen)  # the cap
        painter.setBrush(QColor(theme.mix(theme.MODEL_DECAL, theme.TEXT, 0.08) if lit else theme.MODEL_DECAL))
        painter.drawRoundedRect(cap, radius, radius)
        if listed:
            rects[key_id] = cap
            if key_id == selected:
                painter.setBrush(QColor(theme.mix(theme.ACCENT, theme.MODEL_DECAL, 0.72)))
                painter.setPen(QPen(QColor(theme.ACCENT_INK), 2.0))
            elif locked:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(theme.MUTED), 1.0, Qt.PenStyle.DashLine))
            else:  # a key whose action can be set: outlined, as in Options+
                color = QColor(theme.TEXT)
                color.setAlpha(255 if key_id == hover else 200)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(color, 2.0 if key_id == hover else 1.4))
            painter.drawRoundedRect(cap.adjusted(-1, -1, 1, 1), radius + 1, radius + 1)
        icon = icon_names.get(key_id, "") if listed else ""
        legend = legend or EXTRA_LEGENDS.get(slot, "")
        text_color = QColor(theme.TEXT if listed and not locked or lit else theme.MUTED)
        if not listed and not lit:
            text_color.setAlpha(110 if lit is False else 170)
        inner = cap.adjusted(unit * 0.08, unit * 0.06, -unit * 0.08, -unit * 0.06)
        if icons.has(icon) and unit >= 18:
            size = int(unit * 0.34)
            icons.icon(icon).paint(painter, int(inner.center().x() - size / 2), int(inner.top()), size, size)
            if legend and unit >= 20:
                painter.setPen(text_color)
                painter.drawText(inner, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, legend)
        elif legend and unit >= 20:
            painter.setPen(text_color)
            align = Qt.AlignmentFlag.AlignCenter if len(legend) == 1 else \
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            painter.drawText(inner, align, legend)
        if listed and locked:  # a padlock: the keyboard keeps this key
            size = unit * 0.18
            body = QRectF(cap.right() - size - unit * 0.1, cap.bottom() - size * 0.8 - unit * 0.1, size, size * 0.7)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.MUTED))
            painter.drawRoundedRect(body, 1, 1)
            painter.setPen(QPen(QColor(theme.MUTED), max(1.0, unit * 0.035)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(QRectF(body.left() + size * 0.15, body.top() - size * 0.45, size * 0.7, size * 0.9), 0, 180 * 16)
        elif listed and key_id in changed:  # set to something of its own: a dot of the accent
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.ACCENT))
            dot = max(2.0, unit * 0.07)
            painter.drawEllipse(QRectF(cap.right() - unit * 0.12 - 2 * dot, cap.top() + unit * 0.1, 2 * dot, 2 * dot))
    painter.restore()
    return rects
