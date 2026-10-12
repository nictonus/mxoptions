"""The Flow page: where the other computer stands, pairing with it, and how crossing behaves."""

import socket

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget

from .. import APP_NAME, flow, flow_files, flow_pairing
from ..i18n import _
from . import theme
from .pages import ScrollPage
from .widgets import Card, Row, Segmented, SliderRow, Switch

FILE_LIMITS = [(100, "100 MB"), (1024, "1 GB"), (2048, "2 GB"), (10240, "10 GB"), (65536, "No limit")]


def _time_left(seconds):
    if seconds < 60:
        return _("less than a minute left")
    minutes = round(seconds / 60)
    if minutes < 60:
        return _("about {} min left").format(minutes)
    return _("about {hours} h {minutes} min left").format(hours=minutes // 60, minutes=minutes % 60)


class FlowMap(QWidget):
    """The two computers as they stand: this one in the middle, the other on the side its edge leads
    to. A click on another side puts it there. The stretch of the edge that leads across is lit;
    its dark ends are the corners kept free."""

    moved = Signal(str)

    SCREEN = (124.0, 80.0)
    GAP = 16.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(int(3 * self.SCREEN[1] + 2 * self.GAP + 24))
        self.setMouseTracking(True)
        self._edge = "right"
        self._corner = 0.1
        self._names = ("", "")
        self._linked = False
        self._mouse = None   # "here", "there" or None: where the mouse is
        self._hover = None
        self._enabled = True

    def set_state(self, edge, corner, here, there, linked, mouse, enabled):
        state = (edge, corner, (here, there), linked, mouse, enabled)
        if state != (self._edge, self._corner, self._names, self._linked, self._mouse, self._enabled):
            self._edge, self._corner, self._names, self._linked, self._mouse, self._enabled = state
            self.update()

    def _rects(self):
        width, height = self.SCREEN
        centre = QPointF(self.width() / 2.0, self.height() / 2.0)
        here = QRectF(centre.x() - width / 2, centre.y() - height / 2, width, height)
        step_x, step_y = width + self.GAP, height + self.GAP
        slots = {"left": here.translated(-step_x, 0), "right": here.translated(step_x, 0),
                 "top": here.translated(0, -step_y), "bottom": here.translated(0, step_y)}
        return here, slots

    def _slot_at(self, pos):
        _here, slots = self._rects()
        return next((edge for edge, rect in slots.items() if rect.contains(pos)), None)

    def mouseMoveEvent(self, event):
        hover = self._slot_at(event.position()) if self._enabled else None
        if hover != self._hover:
            self._hover = hover
            self.setCursor(Qt.CursorShape.PointingHandCursor if hover and hover != self._edge else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, _event):
        if self._hover is not None:
            self._hover = None
            self.update()

    def mousePressEvent(self, event):
        edge = self._slot_at(event.position()) if self._enabled else None
        if edge and edge != self._edge:
            self.moved.emit(edge)

    def _computer(self, painter, rect, name, caption, lit, has_mouse):
        accent, text, muted = QColor(theme.ACCENT), QColor(theme.TEXT), QColor(theme.MUTED)
        painter.setPen(QPen(accent if has_mouse else QColor(theme.BORDER), 1.6 if has_mouse else 1.2))
        painter.setBrush(QColor(theme.CARD_HOVER if lit else theme.CARD))
        painter.drawRoundedRect(rect, 9, 9)
        stand = QRectF(rect.center().x() - 16, rect.bottom() + 3, 32, 3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.BORDER))
        painter.drawRoundedRect(stand, 1.5, 1.5)
        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(text if lit else muted)
        inside = rect.adjusted(8, 8, -8, -8)
        label = QFontMetricsF(font).elidedText(name, Qt.TextElideMode.ElideRight, inside.width())
        painter.drawText(QRectF(inside.left(), inside.top(), inside.width(), 20), Qt.AlignmentFlag.AlignCenter, label)
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, small.pointSizeF() * 0.82))
        painter.setFont(small)
        painter.setPen(muted)
        painter.drawText(QRectF(inside.left(), inside.top() + 20, inside.width(), 16), Qt.AlignmentFlag.AlignCenter,
                         QFontMetricsF(small).elidedText(caption, Qt.TextElideMode.ElideRight, inside.width()))
        if has_mouse:  # a little mouse on the computer it is on
            body = QRectF(rect.center().x() - 5.5, rect.bottom() - 25, 11, 17)
            painter.setPen(QPen(accent, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(body, 5.5, 5.5)
            painter.drawLine(QPointF(body.center().x(), body.top() + 3.5), QPointF(body.center().x(), body.top() + 7))

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        here, slots = self._rects()
        for edge, rect in slots.items():  # the places the other computer could stand in
            if edge == self._edge:
                continue
            hovered = edge == self._hover
            colour = QColor(theme.ACCENT if hovered else theme.MUTED)
            colour.setAlphaF(0.95 if hovered else 0.42)
            painter.setPen(QPen(colour, 1.3, Qt.PenStyle.DashLine))
            fill = QColor(theme.ACCENT)
            fill.setAlphaF(0.10 if hovered else 0.0)
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, 9, 9)
        # the edge that leads across, between the two
        upright = self._edge in ("left", "right")
        if upright:
            x = here.right() + self.GAP / 2 if self._edge == "right" else here.left() - self.GAP / 2
            start, end = QPointF(x, here.top() + 4), QPointF(x, here.bottom() - 4)
        else:
            y = here.bottom() + self.GAP / 2 if self._edge == "bottom" else here.top() - self.GAP / 2
            start, end = QPointF(here.left() + 4, y), QPointF(here.right() - 4, y)
        painter.setPen(QPen(QColor(theme.BORDER), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(start, end)
        if self._enabled:
            lit = QColor(theme.ACCENT if self._linked else theme.MUTED)
            painter.setPen(QPen(lit, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(start + (end - start) * self._corner, start + (end - start) * (1.0 - self._corner))
        self._computer(painter, here, self._names[0], _("This computer"), True, self._mouse == "here")
        caption = _("Connected") if self._linked else _("Not connected")
        self._computer(painter, slots[self._edge], self._names[1], caption, self._linked, self._mouse == "there")


class FlowPage(ScrollPage):
    def __init__(self, store, parent=None):
        super().__init__(_("Flow"), _(
            "One mouse on two computers. Push the pointer past the edge of the screen and the mouse moves on to the "
            "other computer, together with what you copied: text, pictures and files. Both computers run MX Options, "
            "are on the same network and are paired with the mouse on different Easy-Switch channels."
        ), parent)
        self.store = store
        self._offers_shown = None
        self._codes = {}  # offer id -> the line edit its code is typed into

        # where the two stand, and whether Flow is on
        card = Card()
        self.enabled = Switch()
        self.enabled.toggled.connect(lambda on: self.store.set("flow", "enabled", on))
        self.head = card.add(Row(_("Flow"), None, self.enabled, note=" "))
        stop = QPushButton(_("Cancel"))
        stop.setCursor(Qt.CursorShape.PointingHandCursor)
        stop.clicked.connect(lambda: self.store.flow and self.store.flow.cancel_transfer())
        self.transfer_row = card.add(Row(" ", None, stop, note=" "))  # shown while copied files are on their way
        self.head_card = card
        self.map = FlowMap()
        self.map.moved.connect(lambda edge: self.store.set("flow", "edge", edge))
        card.add_plain(self.map)
        self.map_hint = QLabel(_("Click a place next to this computer to put the other one there. It follows by itself."))
        self.map_hint.setObjectName("rowHint")
        self.map_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.add_plain(self.map_hint)
        self.body.addWidget(card)

        # pairing
        card = Card(_("Pairing"), hint=_(
            "One computer shows a six-digit code, you type it on the other, and the two agree on a key that everything "
            "between them is encrypted with. The code itself is never sent, and three wrong tries withdraw it."
        ))
        self.unpair = QPushButton(_("Forget"))
        self.unpair.setCursor(Qt.CursorShape.PointingHandCursor)
        self.unpair.clicked.connect(self._forget)
        self.paired_row = card.add(Row(_("Paired"), None, self.unpair, note=" "))
        self.show_code = QPushButton(_("Show a code"))
        self.show_code.setObjectName("primary")
        self.show_code.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_code.clicked.connect(lambda: self.store.flow and self.store.flow.start_pairing())
        self.start_row = card.add(Row(
            _("Pair with another computer"),
            _("Press this on one of the two computers. On the other, open this page: it finds this computer by itself "
              "and asks for the code."),
            self.show_code, note=" "))
        self.cancel_code = QPushButton(_("Cancel"))
        self.cancel_code.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_code.clicked.connect(lambda: self.store.flow and self.store.flow.cancel_pairing())
        self.code = QLabel()
        self.code.setObjectName("pairingCode")
        self.code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        code_box = QWidget()
        code_layout = QHBoxLayout(code_box)
        code_layout.setContentsMargins(0, 0, 0, 0)
        code_layout.setSpacing(14)
        code_layout.addWidget(self.code)
        code_layout.addWidget(self.cancel_code)
        self.code_row = card.add(Row(_("Type this code on the other computer"), None, code_box, note=" "))
        self.offers = QWidget()
        self.offers_layout = QVBoxLayout(self.offers)
        self.offers_layout.setContentsMargins(0, 0, 0, 0)
        self.offers_layout.setSpacing(12)
        card.add_plain(self.offers)
        self.pair_card = card
        self.body.addWidget(card)

        # the edge
        card = Card(_("Crossing"))
        self.push = SliderRow(
            _("Push past the edge"), 0, 40, 1, _("{:.0f} mm"),
            hint=_("How far the mouse has to keep moving once the pointer is at the edge. A longer push keeps the "
                   "pointer on this computer when you only aim at something near the edge."),
            low_label=_("At a touch"), high_label=_("Firmly"))
        self.push.changed.connect(lambda value: self.store.set("flow", "push_mm", float(value)))
        card.add(self.push)
        self.corner = SliderRow(
            _("Keep the corners free"), 0, 40, 1, "{:.0f}%",
            hint=_("This much of the edge, at each of its ends, does not lead across, so that the corners of the "
                   "screen go on doing what they do. On the picture above these are the dark ends of the lit edge."))
        self.corner.changed.connect(lambda value: self.store.set("flow", "corner", int(value)))
        card.add(self.corner)
        self.fullscreen = Switch()
        self.fullscreen.toggled.connect(lambda on: self.store.set("flow", "fullscreen", on))
        card.add(Row(
            _("Cross while a full-screen window is in front"),
            _("Off, a game or a film in full screen keeps the mouse however hard you push."),
            self.fullscreen))
        self.body.addWidget(card)

        # what goes along
        card = Card(_("What goes along"))
        self.clipboard = Switch()
        self.clipboard.toggled.connect(lambda on: self.store.set("flow", "clipboard", on))
        self.clipboard_row = card.add(Row(
            _("Copied text and pictures"),
            _("What you copied on one computer can be pasted on the other after crossing."),
            self.clipboard))
        self.files = Switch()
        self.files.toggled.connect(lambda on: self.store.set("flow", "files", on))
        self.files_row = card.add(Row(
            _("Copied files and folders"),
            _("Copy them in the file manager, cross, and paste. They are sent over when you cross and wait on the "
              "other computer for a day."),
            self.files, note=" "))
        self.limit = Segmented([(value, _(label) if label == "No limit" else label) for value, label in FILE_LIMITS])
        self.limit.selected.connect(lambda value: self.store.set("flow", "files_max_mb", value))
        self.limit_row = card.add(Row(
            _("Largest amount in one go"),
            _("More than this stays behind, so that a huge folder left on the clipboard is not sent by accident."),
            self.limit))
        self.body.addWidget(card)

        # by hand
        card = Card(_("Setting it up by hand"), hint=_(
            "For networks where the computers cannot find each other: give one of them the other's address, and both "
            "the same key. Each listens on TCP port {port}; finding each other and pairing by code also use UDP port "
            "{port} and TCP port {pair}."
        ).format(port=flow.PORT, pair=flow.PAIR_PORT))
        self.peer = QLineEdit()
        self.peer.setPlaceholderText("192.168.1.20")
        self.peer.setMinimumWidth(250)
        self.peer.editingFinished.connect(lambda: self.store.set("flow", "peer", self.peer.text().strip()))
        self.peer_row = card.add(Row(_("Address of the other computer"), None, self.peer, note=" "))
        key = QWidget()
        key_layout = QHBoxLayout(key)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(8)
        self.key = QLineEdit()
        self.key.setPlaceholderText("XXXX-XXXX-XXXX-XXXX-XXXX-XXXX")
        self.key.setMinimumWidth(310)
        self.key.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self.key.editingFinished.connect(lambda: self.store.set("flow", "key", self.key.text().strip()))
        key_layout.addWidget(self.key)
        for label, slot in ((_("Generate"), self._new_key), (_("Copy"), lambda: QApplication.clipboard().setText(self.store.cfg["flow"]["key"]))):
            button = QPushButton(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(slot)
            key_layout.addWidget(button)
        card.add(Row(_("Key"), _("The same on both computers. Pairing by code sets it for you."), key))
        self.body.addWidget(card)
        self.finish()

        store.changed.connect(self._refresh)
        store.state_changed.connect(lambda _state: self._refresh())
        if store.flow is not None:
            store.flow.changed.connect(self._refresh)
        self._refresh()

    # -- actions -----------------------------------------------------------------

    def _forget(self):
        answer = QMessageBox.question(self, APP_NAME, _("Forget the other computer? To use Flow again the two have to be paired anew."))
        if answer == QMessageBox.StandardButton.Yes and self.store.flow is not None:
            self.store.flow.unpair()

    def _new_key(self):
        if self.store.cfg["flow"]["key"]:
            answer = QMessageBox.question(
                self, APP_NAME, _("Replace the key? The other computer will need the new one before Flow works again."))
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.store.set("flow", "key", flow.new_key())

    def _pair(self, offer):
        field = self._codes.get(offer["id"])
        code = flow_pairing.clean_code(field.text()) if field is not None else ""
        if len(code) == flow_pairing.CODE_DIGITS and self.store.flow is not None:
            self.store.flow.pair(offer["address"], offer["port"], code)

    def showEvent(self, event):
        super().showEvent(event)
        if self.store.flow is not None:
            self.store.flow.watch_offers(True)

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.store.flow is not None:
            self.store.flow.watch_offers(False)

    # -- showing what is ----------------------------------------------------------------

    def _status_text(self, cfg, status, name):
        peer = status["peer"]
        channel = (peer or {}).get("channel")
        known = isinstance(channel, int) and not isinstance(channel, bool) and channel >= 0
        if not status["supported"]:
            return _("Flow needs Python 3.13 or newer on this computer.")
        if not status["paired"]:
            return _("Pair this computer with another one to begin.")
        if not cfg["enabled"]:
            return _("Flow is off.")
        if status["problem"]:
            return _("Cannot listen for the other computer: {}").format(status["problem"])
        if peer is None:
            return _("Looking for {}…").format(name)
        if not known:
            return _("Connected to {}. It has not seen the mouse yet: switch the mouse to it once with the Easy-Switch "
                     "button, and it will know its channel from then on.").format(name)
        if status["channel"] < 0:
            return _("Connected to {}. This computer has not seen the mouse yet: switch the mouse to it once with the "
                     "Easy-Switch button, and it will know its channel from then on.").format(name)
        if channel == status["channel"]:
            return _("Connected to {name}, but both computers believe they are on Easy-Switch channel {channel}. Switch "
                     "the mouse to each of them once with the Easy-Switch button.").format(name=name, channel=channel + 1)
        if not status["edges"]:
            return _("Connected to {}. The screen edge is only watched on KDE Plasma: here, give a button, a gesture "
                     "or a ring bubble the action “Move to the other computer (Flow)”.").format(name)
        return _("Connected to {}. Push the pointer past the lit edge to cross.").format(name)

    def _refresh(self):
        cfg, service = self.store.cfg["flow"], self.store.flow
        status = service.status() if service is not None else {
            "supported": flow.supported(), "on": False, "paired": flow.key_usable(cfg["key"]), "address": "", "problem": "",
            "peer": None, "channel": cfg["channel"], "here": bool(self.store.state["connected"]), "edges": True, "rich": True,
            "pairing": None, "offers": [], "pair_error": ""}
        peer = status["peer"]
        name = (peer or {}).get("name") or cfg["peer_name"] or _("the other computer")
        paired, pairing = status["paired"], status["pairing"]

        self.enabled.set_checked_silently(cfg["enabled"] and paired)
        self.enabled.setEnabled(status["supported"] and paired)
        self.head.note.setText(self._status_text(cfg, status, name))
        mouse = "here" if status["here"] else ("there" if peer and peer.get("present") else None)
        there = (peer or {}).get("name") or cfg["peer_name"] or _("The other one")
        self.map.set_state(cfg["edge"], cfg["corner"] / 100.0, socket.gethostname(), there, peer is not None, mouse,
                           status["supported"])

        transfer = status.get("transfer")
        self.head_card.set_row_visible(self.transfer_row, transfer is not None)
        if transfer is not None:
            title = _("Sending {count} file(s) to {name}") if transfer["sending"] else _("Receiving {count} file(s) from {name}")
            self.transfer_row.title.setText(title.format(count=transfer["count"], name=name))
            parts = [_("{done} of {total}").format(done=flow_files.human(transfer["done"]), total=flow_files.human(transfer["total"]))]
            if transfer["rate"]:
                parts.append(_("{}/s").format(flow_files.human(transfer["rate"])))
                parts.append(_time_left((transfer["total"] - transfer["done"]) / transfer["rate"]))
            self.transfer_row.note.setText("  ·  ".join(parts))

        # pairing
        self.pair_card.set_row_visible(self.paired_row, paired)
        self.paired_row.title.setText(_("Paired with {}").format(name))
        self.paired_row.note.setText(_("Connected, everything between the two is encrypted.") if peer is not None
                                     else _("Not connected right now."))
        self.pair_card.set_row_visible(self.start_row, not paired and pairing is None)
        self.show_code.setEnabled(status["supported"] and service is not None)
        error = {"code": _("That was not the code. Look at the other computer again."),
                 "closed": _("The other computer no longer shows a code. Press “Show a code” there again."),
                 "unreachable": _("The other computer did not answer. Is a firewall in the way?")}.get(status["pair_error"], "")
        here = status["address"]
        self.start_row.note.setText(error or (_("This computer's address: {}").format(here) if here else ""))
        self.start_row.note.setVisible(bool(self.start_row.note.text()))
        self.pair_card.set_row_visible(self.code_row, pairing is not None)
        if pairing is not None:
            code = pairing["code"]
            self.code.setText(f"{code[:3]} {code[3:]}")
            self.code_row.note.setText(
                _("On the other computer, open Flow in MX Options: this computer is listed there. The code is valid for "
                  "{seconds} more seconds; wrong tries left: {left}.").format(seconds=pairing["seconds"], left=pairing["left"]))
        offers = [] if paired else status["offers"]
        shown = tuple(offer["id"] for offer in offers)
        if shown != self._offers_shown:
            self._offers_shown = shown
            self._codes = {}
            while self.offers_layout.count():
                item = self.offers_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            for offer in offers:
                box = QWidget()
                line = QHBoxLayout(box)
                line.setContentsMargins(0, 0, 0, 0)
                line.setSpacing(8)
                field = QLineEdit()
                field.setPlaceholderText("000 000")
                field.setMaxLength(8)
                field.setFixedWidth(110)
                field.setAlignment(Qt.AlignmentFlag.AlignCenter)
                field.returnPressed.connect(lambda o=offer: self._pair(o))
                self._codes[offer["id"]] = field
                line.addWidget(field)
                button = QPushButton(_("Pair"))
                button.setObjectName("primary")
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(lambda _checked=False, o=offer: self._pair(o))
                line.addWidget(button)
                self.offers_layout.addWidget(Row(_("{} shows a pairing code").format(offer["name"] or offer["address"]),
                                                 _("Type the six digits it shows."), box))
        self.offers.setVisible(bool(offers))

        # the edge, and what goes along
        self.push.set_value(cfg["push_mm"])
        self.corner.set_value(cfg["corner"])
        self.fullscreen.set_checked_silently(cfg["fullscreen"])
        self.clipboard.set_checked_silently(cfg["clipboard"])
        self.files.set_checked_silently(cfg["files"] and status["rich"])
        self.files.setEnabled(status["rich"])
        self.files_row.note.setText("" if status["rich"] else _(
            "Only text can cross here: files and pictures need KDE's clipboard service (the Python bindings of KGuiAddons)."))
        self.files_row.note.setVisible(not status["rich"])
        self.limit.set_value(min((value for value, _label in FILE_LIMITS), key=lambda value: abs(value - cfg["files_max_mb"])))
        self.limit_row.setEnabled(cfg["files"] and status["rich"])

        # by hand
        for field, value in ((self.peer, cfg["peer"]), (self.key, cfg["key"])):
            if not field.hasFocus() and field.text() != value:  # never under the hands of someone typing
                field.setText(value)
        here = status["address"] + ("" if cfg["port"] == flow.PORT or not status["address"] else f":{cfg['port']}")
        self.peer_row.note.setText(_("This computer's address: {}").format(here) if here else "")
        self.peer_row.note.setVisible(bool(here))
