"""The Flow page: where the other computer stands, pairing with it, and how crossing behaves."""

import socket

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget

from .. import APP_NAME, flow, flow_files, flow_pairing
from ..i18n import _
from . import theme
from .pages import ScrollPage
from .widgets import Card, PatternRow, Row, Segmented, SliderRow, Switch

FILE_LIMITS = [(100, "100 MB"), (1024, "1 GB"), (2048, "2 GB"), (10240, "10 GB"), (65536, "No limit")]


def _time_left(seconds):
    if seconds < 60:
        return _("less than a minute left")
    minutes = round(seconds / 60)
    if minutes < 60:
        return _("about {} min left").format(minutes)
    return _("about {hours} h {minutes} min left").format(hours=minutes // 60, minutes=minutes % 60)


class FlowMap(QWidget):
    """The computers as they stand: this one in the middle, the others on the sides their edges lead
    to. A click on one of the others picks it; a click on another side then puts it there. The
    stretch of each edge that leads across is lit; its dark ends are the corners kept free."""

    picked = Signal(str)       # the pairing of the computer clicked
    moved = Signal(str, str)   # the pairing picked, and the side it is to stand on

    SCREEN = (124.0, 80.0)
    GAP = 16.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(int(3 * self.SCREEN[1] + 2 * self.GAP + 24))
        self.setMouseTracking(True)
        self._state = ("", (), 0.1, False, True, "")  # this one's name, the others, corners, the mouse is here, enabled, picked
        self._hover = None

    def set_state(self, here, peers, corner, mouse_here, enabled, selected):
        """``peers``: [{"pair", "edge", "name", "linked", "mouse": the mouse is on it}]."""
        state = (here, tuple(tuple(sorted(peer.items())) for peer in peers), corner, mouse_here, enabled, selected)
        if state != self._state:
            self._state = state
            self.update()

    def _peers(self):
        return [dict(peer) for peer in self._state[1]]

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
        hover = self._slot_at(event.position()) if self._state[4] and self._state[1] else None
        if hover != self._hover:
            self._hover = hover
            self.setCursor(Qt.CursorShape.PointingHandCursor if hover else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, _event):
        if self._hover is not None:
            self._hover = None
            self.update()

    def mousePressEvent(self, event):
        edge = self._slot_at(event.position()) if self._state[4] else None
        if not edge:
            return
        there = next((peer for peer in self._peers() if peer["edge"] == edge), None)
        selected = self._state[5]
        if there is not None and there["pair"] != selected:
            self.picked.emit(there["pair"])
        elif there is None and selected:
            self.moved.emit(selected, edge)

    def _computer(self, painter, rect, name, caption, lit, has_mouse, picked=False):
        accent, text, muted = QColor(theme.ACCENT), QColor(theme.TEXT), QColor(theme.MUTED)
        painter.setPen(QPen(accent if (has_mouse or picked) else QColor(theme.BORDER), 1.6 if has_mouse else 1.2,
                            Qt.PenStyle.SolidLine if has_mouse or not picked else Qt.PenStyle.DotLine))
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
        here_name, _peers, corner, mouse_here, enabled, selected = self._state
        peers = self._peers()
        here, slots = self._rects()
        taken = {peer["edge"] for peer in peers}
        for edge, rect in slots.items():  # the places another computer could stand in
            if edge in taken:
                continue
            hovered = edge == self._hover and bool(selected)
            colour = QColor(theme.ACCENT if hovered else theme.MUTED)
            colour.setAlphaF(0.95 if hovered else 0.42)
            painter.setPen(QPen(colour, 1.3, Qt.PenStyle.DashLine))
            fill = QColor(theme.ACCENT)
            fill.setAlphaF(0.10 if hovered else 0.0)
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, 9, 9)
        for peer in peers:  # the edge that leads across, between this computer and that one
            edge = peer["edge"]
            if edge in ("left", "right"):
                x = here.right() + self.GAP / 2 if edge == "right" else here.left() - self.GAP / 2
                start, end = QPointF(x, here.top() + 4), QPointF(x, here.bottom() - 4)
            else:
                y = here.bottom() + self.GAP / 2 if edge == "bottom" else here.top() - self.GAP / 2
                start, end = QPointF(here.left() + 4, y), QPointF(here.right() - 4, y)
            painter.setPen(QPen(QColor(theme.BORDER), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(start, end)
            if enabled:
                lit = QColor(theme.ACCENT if peer["linked"] else theme.MUTED)
                painter.setPen(QPen(lit, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawLine(start + (end - start) * corner, start + (end - start) * (1.0 - corner))
        self._computer(painter, here, here_name, _("This computer"), True, mouse_here)
        for peer in peers:
            caption = _("Connected") if peer["linked"] else _("Not connected")
            self._computer(painter, slots[peer["edge"]], peer["name"] or _("The other one"), caption, peer["linked"], peer["mouse"],
                           picked=len(peers) > 1 and peer["pair"] == selected)


class FlowPage(ScrollPage):
    def __init__(self, store, parent=None):
        super().__init__(_("Flow"), _(
            "One mouse on two or three computers. Push the pointer past the edge of the screen and the mouse moves on "
            "to the computer behind it, together with what you copied: text, pictures and files. Every computer runs "
            "MX Options, they are on the same network, and the mouse is paired with each on an Easy-Switch channel "
            "of its own."
        ), parent)
        self.store = store
        self._offers_shown = None
        self._paired_shown = None
        self._codes = {}      # offer id -> the line edit its code is typed into
        self._selected = ""   # the pairing of the computer the picture and the fields below are about

        # where they stand, and whether Flow is on
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
        self.map.picked.connect(self._pick)
        self.map.moved.connect(lambda pair, edge: self.store.flow and self.store.flow.place(pair, edge))
        card.add_plain(self.map)
        self.map_hint = QLabel()
        self.map_hint.setObjectName("rowHint")
        self.map_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.add_plain(self.map_hint)
        self.body.addWidget(card)

        # pairing
        card = Card(_("Pairing"), hint=_(
            "One computer shows a six-digit code, you type it on the other, and the two agree on a key that everything "
            "between them is encrypted with. The code itself is never sent, and three wrong tries withdraw it. A "
            "computer can be paired with two others, one on each side."
        ))
        self.paired = QWidget()
        self.paired_layout = QVBoxLayout(self.paired)
        self.paired_layout.setContentsMargins(0, 0, 0, 0)
        self.paired_layout.setSpacing(12)
        card.add_plain(self.paired)
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
              "other computer for a day. Until they have all arrived, the clipboard there stays as it was."),
            self.files, note=" "))
        self.limit = Segmented([(value, _(label) if label == "No limit" else label) for value, label in FILE_LIMITS])
        self.limit.selected.connect(lambda value: self.store.set("flow", "files_max_mb", value))
        self.limit_row = card.add(Row(
            _("Largest amount in one go"),
            _("More than this stays behind, so that a huge folder left on the clipboard is not sent by accident."),
            self.limit))
        self.cues = {}
        for key, title in (("cue_arrived", _("Vibration when the files have arrived")),
                           ("cue_failed", _("Vibration when they could not be brought over"))):
            row = PatternRow(title)
            row.changed.connect(lambda waveform, k=key: self.store.set("flow", k, waveform))
            row.played.connect(self.store.engine.play_haptic)
            self.cues[key] = card.add(row)
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
        self.peer.editingFinished.connect(lambda: self._by_hand(address=self.peer.text().strip()))
        self.peer_row = card.add(Row(_("Address of the other computer"), None, self.peer, note=" "))
        key = QWidget()
        key_layout = QHBoxLayout(key)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(8)
        self.key = QLineEdit()
        self.key.setPlaceholderText("XXXX-XXXX-XXXX-XXXX-XXXX-XXXX")
        self.key.setMinimumWidth(310)
        self.key.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self.key.editingFinished.connect(lambda: self._by_hand(key=self.key.text().strip()))
        key_layout.addWidget(self.key)
        for label, slot in ((_("Generate"), self._new_key), (_("Copy"), lambda: QApplication.clipboard().setText(self.key.text()))):
            button = QPushButton(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(slot)
            key_layout.addWidget(button)
        self.key_row = card.add(Row(_("Key"), _("The same on both computers. Pairing by code sets it for you."), key))
        self.body.addWidget(card)
        self.finish()

        store.changed.connect(self._refresh)
        store.state_changed.connect(lambda _state: self._refresh())
        if store.flow is not None:
            store.flow.changed.connect(self._refresh)
        self._refresh()

    # -- actions -----------------------------------------------------------------

    def _pick(self, pair):
        self._selected = pair
        self._refresh()

    def _forget(self, pair, name):
        answer = QMessageBox.question(self, APP_NAME, _("Forget {}? To use Flow with it again the two have to be paired anew.").format(name))
        if answer == QMessageBox.StandardButton.Yes and self.store.flow is not None:
            self.store.flow.unpair(pair)

    def _entry(self):
        """The settings of the computer picked, or None."""
        return next((entry for entry in self.store.cfg["flow"]["peers"] if flow.pair_id(entry["key"]) == self._selected), None)

    def _by_hand(self, key=None, address=None):
        """The key or the address typed in: for the computer picked, or for a new one if there is none yet."""
        peers = [dict(entry) for entry in self.store.cfg["flow"]["peers"]]
        entry = next((entry for entry in peers if flow.pair_id(entry["key"]) == self._selected), None)
        if entry is None:
            if not key or not flow.key_usable(key) or len(peers) >= flow.MAX_PEERS:
                return
            taken = {entry["edge"] for entry in peers}
            entry = {"key": key, "id": "", "address": self.peer.text().strip(), "name": "", "edge_at": 0.0,
                     "edge": next(side for side in ("right", "left", "top", "bottom") if side not in taken)}
            peers.append(entry)
        elif key is not None:
            if not flow.key_usable(key):
                return
            entry["key"] = key
        if address is not None:
            entry["address"] = address
        self._selected = flow.pair_id(entry["key"])
        self.store.set("flow", "peers", peers)

    def _new_key(self):
        if self._entry() is not None:
            answer = QMessageBox.question(
                self, APP_NAME, _("Replace the key? The other computer will need the new one before Flow works again."))
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._by_hand(key=flow.new_key())

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

    def _peer_text(self, peer, status):
        """What stands in the way of crossing to one of the other computers, or that nothing does."""
        name = peer["name"] or _("the other computer")
        channel = peer["channel"]
        known = isinstance(channel, int) and not isinstance(channel, bool) and channel >= 0
        if not peer["linked"]:
            return _("Looking for {}…").format(name)
        if peer.get("quiet"):
            return _("{} is not answering right now: the network between the two stalls, or it is asleep. The mouse "
                     "stays here until it answers.").format(name)
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
            return _("Connected to {}. This desktop does not let a program watch the screen edge: here, give a button, "
                     "a gesture or a ring bubble the action “Move to the other computer (Flow)”.").format(name)
        if status.get("edge_watch") == "strips":
            return _("Connected to {}. Push the pointer past the lit edge to cross. A line one pixel wide along that "
                     "edge is what feels the pointer here; a click on it is passed on to what is under it.").format(name)
        return _("Connected to {}. Push the pointer past the lit edge to cross.").format(name)

    def _status_text(self, cfg, status):
        if not status["supported"]:
            return _("Flow needs Python 3.13 or newer on this computer.")
        if not status["peers"]:
            return _("Pair this computer with another one to begin.")
        if not cfg["enabled"]:
            return _("Flow is off.")
        if status["problem"]:
            return _("Cannot listen for the other computer: {}").format(status["problem"])
        return "\n".join(self._peer_text(peer, status) for peer in status["peers"])

    def _refresh(self):
        cfg, service = self.store.cfg["flow"], self.store.flow
        if service is not None:
            status = service.status()
        else:  # the page on its own, as when it is drawn for the documentation
            status = {"supported": flow.supported(), "on": False, "address": "", "problem": "", "channel": cfg["channel"],
                      "here": bool(self.store.state["connected"]), "edges": True, "edge_watch": "", "copies": True, "rich": True,
                      "pairing": None,
                      "transfer": None, "offers": [], "pair_error": "",
                      "peers": [{"pair": flow.pair_id(entry["key"]), "name": entry["name"], "edge": entry["edge"], "address": entry["address"],
                                 "key": entry["key"], "linked": False, "channel": None, "present": False, "quiet": False}
                                for entry in cfg["peers"]]}
        peers, pairing = status["peers"], status["pairing"]
        if self._selected not in [peer["pair"] for peer in peers]:
            self._selected = peers[0]["pair"] if peers else ""
        picked = next((peer for peer in peers if peer["pair"] == self._selected), None)

        self.enabled.set_checked_silently(cfg["enabled"] and bool(peers))
        self.enabled.setEnabled(status["supported"] and bool(peers))
        self.head.note.setText(self._status_text(cfg, status))
        self.map.set_state(socket.gethostname(), [
            {"pair": peer["pair"], "edge": peer["edge"], "name": peer["name"], "linked": peer["linked"],
             "mouse": peer["present"] and not status["here"]} for peer in peers],
            cfg["corner"] / 100.0, status["here"], status["supported"], self._selected)
        if len(peers) > 1:
            self.map_hint.setText(_("Click one of the other computers, then the place next to this one where it stands."))
        elif peers:
            self.map_hint.setText(_("Click a place next to this computer to put the other one there. It follows by itself."))
        self.map_hint.setVisible(bool(peers))

        transfer = status.get("transfer")
        self.head_card.set_row_visible(self.transfer_row, transfer is not None)
        if transfer is not None:
            title = _("Sending {count} file(s) to {name}") if transfer["sending"] else _("Receiving {count} file(s) from {name}")
            self.transfer_row.title.setText(title.format(count=transfer["count"], name=transfer.get("name") or _("the other computer")))
            parts = [_("{done} of {total}").format(done=flow_files.human(transfer["done"]), total=flow_files.human(transfer["total"]))]
            if transfer["rate"]:
                parts.append(_("{}/s").format(flow_files.human(transfer["rate"])))
                parts.append(_time_left((transfer["total"] - transfer["done"]) / transfer["rate"]))
            self.transfer_row.note.setText("  ·  ".join(parts))

        # pairing
        shown = tuple((peer["pair"], peer["name"], peer["linked"]) for peer in peers)
        if shown != self._paired_shown:
            self._paired_shown = shown
            while self.paired_layout.count():
                item = self.paired_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            for peer in peers:
                name = peer["name"] or _("the other computer")
                forget = QPushButton(_("Forget"))
                forget.setCursor(Qt.CursorShape.PointingHandCursor)
                forget.clicked.connect(lambda _checked=False, p=peer["pair"], n=name: self._forget(p, n))
                self.paired_layout.addWidget(Row(
                    _("Paired with {}").format(name), None, forget,
                    note=_("Connected, everything between the two is encrypted.") if peer["linked"] else _("Not connected right now.")))
        self.paired.setVisible(bool(peers))
        room = len(peers) < flow.MAX_PEERS
        self.pair_card.set_row_visible(self.start_row, room and pairing is None)
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
        offers = status["offers"] if room else []
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
        copies = status.get("copies", True)
        self.clipboard.set_checked_silently(cfg["clipboard"] and copies)
        self.clipboard.setEnabled(copies)
        self.files.set_checked_silently(cfg["files"] and status["rich"])
        self.files.setEnabled(status["rich"])
        self.files_row.note.setText(
            "" if status["rich"]
            else _("Only text can cross here. For files and pictures install wl-clipboard.") if copies
            else _("This desktop does not let a program in the background see what was copied. Install wl-clipboard, "
                   "then start MX Options again."))
        self.files_row.note.setVisible(not status["rich"])
        self.limit.set_value(min((value for value, _label in FILE_LIMITS), key=lambda value: abs(value - cfg["files_max_mb"])))
        self.limit_row.setEnabled(cfg["files"] and status["rich"])
        waveforms = (self.store.state.get("caps") or {}).get("waveforms")
        for key, row in self.cues.items():
            if waveforms:
                row.set_waveforms(waveforms)
            row.set_value(cfg[key])
            row.setEnabled(cfg["files"] and status["rich"])

        # by hand: the computer picked, or a new one
        for field, value in ((self.peer, picked["address"] if picked else ""), (self.key, picked["key"] if picked else "")):
            if not field.hasFocus() and field.text() != value:  # never under the hands of someone typing
                field.setText(value)
        self.peer_row.title.setText(_("Address of {}").format(picked["name"]) if picked and picked["name"] else _("Address of the other computer"))
        here = status["address"] + ("" if cfg["port"] == flow.PORT or not status["address"] else f":{cfg['port']}")
        self.peer_row.note.setText(_("This computer's address: {}").format(here) if here else "")
        self.peer_row.note.setVisible(bool(here))
