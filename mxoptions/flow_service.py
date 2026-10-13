"""Flow, tied to the mouse and the desktop (flow.py has the idea and the link).

Watching the edge of the screen is left to whatever this desktop offers for it (flow_edges.py):
it says when the pointer comes to rest against the chosen edge and when it leaves again. How
hard it is then pushed on is measured from the mouse itself, by the engine. Where the pointer
cannot be watched, the "Move to the other computer" action is the way across."""

import collections
import copy
import logging
import secrets
import socket
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication

from . import __version__, clipboard, environment, flow, flow_edges, flow_files, flow_pairing, vinput

log = logging.getLogger(__name__)

ARRIVAL_QUIET = 1.5  # s after the mouse arrived during which the edge does not send it back
ARRIVAL_WAIT = 12.0  # s the mouse may take to arrive once the other computer has sent it
OFFER_AGE = 4.0      # s after which a computer that stopped announcing its pairing code is forgotten
MIB = 1024 * 1024
RATE_WINDOW = 10.0       # s the speed of a file transfer is averaged over
RESEND_FOR = 120.0       # s after a crossing during which what was copied is sent again if the link had failed it


class FlowService(QObject):
    changed = Signal()                      # what status() tells has changed
    _from_link = Signal(str, object, str)   # carries the link's events from its thread onto this one

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._peers = {}           # pair -> what that computer says about itself, while its link is up
        self._problem = ""         # why this computer cannot listen
        self._pointer = None       # vinput.AbsolutePointer while Flow is on
        self._applied = None       # the settings in force
        self._watcher = None       # flow_edges: what watches the screen edges here (None: nothing can)
        self._armed = None         # the pairing whose edge the pointer rests against: the engine is measuring the push
        self._leaving = None       # the pairing whose computer the mouse is being sent to
        self._along = None         # where along the edge the pointer is, 0..1
        self._quiet_until = 0.0    # until then the edges do nothing: the mouse is arriving or has only just arrived
        self._arriving = False     # another computer has sent the mouse and it is not here yet
        self._shared = {}          # pair -> clipboard.signature() of what both computers have
        self._offered = {}         # pair -> ... of the files being sent, which it will be once they have arrived
        self._unconfirmed = {}     # pair -> (token, signature) of text or a picture sent and not yet confirmed
        self._crossed = ("", 0.0)  # the pairing the mouse last went to, and when: what was copied belongs there
        self._channels = {}        # pair -> the channel that computer last said it is on (kept while its link is down)
        self._transfer = None      # files on their way: {"done", "total", "count", "sending", "samples"}
        self._adopting = False     # an edge is being changed to match another computer, not by the user
        self._pairing = None       # while this computer shows a code: {"code", "left", "until"}
        self._offers = {}          # computers nearby showing a code: id -> {"id", "name", "address", "port", "seen"}
        self._pair_error = ""      # why the last attempt to pair failed: "code", "closed", "unreachable"
        self._watching = False     # the settings page is open and lists computers showing a code
        self._watcher = flow_edges.create(self, click=self._click_through, scroll=self._scroll_through)
        if self._watcher is not None:
            self._watcher.reported.connect(self._edge_report)
            self._watcher.changed.connect(self.changed.emit)
        self._from_link.connect(self._on_link)
        self.clipboard = clipboard  # read(), write() and signature() of what is copied
        self.link = flow.Link(self._from_link.emit, staging=flow_files.STAGING)
        self.link.start()
        self._tick = QTimer(self, interval=1000)  # while a code is on show or offers are listed: keep them current
        self._tick.timeout.connect(self._second_passed)
        if not store.cfg["flow"]["id"]:
            store.set("flow", "id", secrets.token_hex(8))
        store.changed.connect(self._configure)
        store.state_changed.connect(lambda _state: self._mouse_changed())
        self._configure()

    # -- the other computers, as the settings keep them -----------------------------------

    def _entries(self):
        """[(pair, entry)] for the other computers in the settings."""
        return [(flow.pair_id(entry["key"]), entry) for entry in self.store.cfg["flow"]["peers"]]

    def _entry(self, pair):
        return next((entry for found, entry in self._entries() if found == pair), None)

    def _change(self, pair, **values):
        """Change something about one of them in the settings."""
        peers = copy.deepcopy(self.store.cfg["flow"]["peers"])
        for entry in peers:
            if flow.pair_id(entry["key"]) == pair:
                entry.update(values)
        self.store.set("flow", "peers", peers)

    def _name(self, pair):
        return (self._peers.get(pair) or {}).get("name") or (self._entry(pair) or {}).get("name") or ""

    # -- for the settings page ---------------------------------------------------

    def status(self):
        cfg = self.store.cfg["flow"]
        pairing = None
        if self._pairing is not None:
            pairing = {"code": self._pairing["code"], "left": self._pairing["left"],
                       "seconds": max(0, int(self._pairing["until"] - time.monotonic()))}
        peers = []
        for pair, entry in self._entries():
            said = self._peers.get(pair)
            peers.append({"pair": pair, "name": self._name(pair), "edge": entry["edge"], "address": entry["address"],
                          "key": entry["key"], "linked": said is not None, "channel": (said or {}).get("channel"),
                          "present": bool((said or {}).get("present")), "quiet": bool((said or {}).get("quiet"))})
        return {
            "supported": flow.supported(),
            "on": self._on(),
            "peers": peers,
            "address": flow.local_address(),
            "problem": self._problem,
            "channel": cfg["channel"],
            "here": bool(self.store.state["connected"]),   # the mouse is on this computer
            "edges": bool(self._watcher and self._watcher.watching),  # the screen edges are being watched
            "edge_watch": self._watcher.name if self._watcher else "",  # by what (flow_edges.py)
            "copies": self.clipboard.available(),           # what was copied can cross at all
            "rich": self.clipboard.rich(),                  # pictures and files can cross, not only text
            "pairing": pairing,
            "transfer": self._transfer_status(),
            "offers": sorted(({key: offer[key] for key in ("id", "name", "address", "port")} for offer in self._offers.values()),
                             key=lambda offer: offer["name"]),
            "pair_error": self._pair_error,
        }

    def _transfer_status(self):
        """How far the file transfer is, with its speed in bytes per second (None while that is not known)."""
        transfer = self._transfer
        if transfer is None:
            return None
        samples, rate = transfer["samples"], None
        if len(samples) >= 2 and samples[-1][0] - samples[0][0] >= 1.0:
            rate = (samples[-1][1] - samples[0][1]) / (samples[-1][0] - samples[0][0])
        return {"done": transfer["done"], "total": transfer["total"], "count": transfer["count"], "sending": transfer["sending"],
                "rate": rate, "name": self._name(transfer["pair"])}

    def _progress(self, payload, pair, sending=None):
        now = time.monotonic()
        if self._transfer is None:
            self._transfer = {"samples": collections.deque()}
        transfer = self._transfer
        transfer.update(done=payload.get("done", 0), total=payload["total"], count=payload["count"], pair=pair,
                        sending=payload.get("sending", False) if sending is None else sending)
        transfer["samples"].append((now, transfer["done"]))
        while now - transfer["samples"][0][0] > RATE_WINDOW:
            transfer["samples"].popleft()

    def cancel_transfer(self):
        """Give up the file transfer in progress."""
        self.link.cancel_files()

    def watch_offers(self, wanted):
        """Look for computers nearby that show a pairing code (the settings page is open)."""
        self.link.watch_offers(wanted)
        if not wanted and self._offers:
            self._offers = {}
            self.changed.emit()
        self._sync_tick(wanted)

    def start_pairing(self):
        """Show a code here for another computer to be told."""
        code = flow_pairing.new_code()
        self._pairing = {"code": code, "left": flow_pairing.ATTEMPTS, "until": time.monotonic() + flow_pairing.OPEN_FOR}
        self._pair_error = ""
        self.link.open_pairing(code, self._info())
        self._sync_tick()
        self.changed.emit()

    def cancel_pairing(self):
        if self._pairing is not None:
            self._pairing = None
            self.link.close_pairing()
            self.changed.emit()

    def pair(self, address, port, code):
        """Pair with the computer at ``address`` that shows ``code``."""
        self._pair_error = ""
        self.link.pair_with(address, port, flow_pairing.clean_code(code), self._info())
        self.changed.emit()

    def unpair(self, pair):
        """Forget one of the other computers."""
        cfg = copy.deepcopy(self.store.cfg["flow"])
        cfg["peers"] = [entry for entry in cfg["peers"] if flow.pair_id(entry["key"]) != pair]
        cfg["enabled"] = cfg["enabled"] and bool(cfg["peers"])
        self.store.set_section("flow", cfg)

    def place(self, pair, edge):
        """Put one of the other computers behind ``edge``; one that stood there changes places with it."""
        peers = copy.deepcopy(self.store.cfg["flow"]["peers"])
        moved = next((entry for entry in peers if flow.pair_id(entry["key"]) == pair), None)
        if moved is None or edge not in flow.EDGES or moved["edge"] == edge:
            return
        for entry in peers:
            if entry is not moved and entry["edge"] == edge:
                entry["edge"] = moved["edge"]
        moved["edge"] = edge
        self.store.set("flow", "peers", peers)

    def _info(self):
        cfg = self.store.cfg["flow"]
        return {"name": socket.gethostname(), "port": cfg["port"], "id": cfg["id"]}

    def _sync_tick(self, watching=None):
        if watching is not None:
            self._watching = watching
        if self._pairing is not None or self._watching:
            self._tick.start()
        else:
            self._tick.stop()

    def _second_passed(self):
        now = time.monotonic()
        gone = [key for key, offer in self._offers.items() if now - offer["seen"] > OFFER_AGE]
        for key in gone:
            del self._offers[key]
        if gone or self._pairing is not None:
            self.changed.emit()

    # -- settings ---------------------------------------------------------------------

    def _on(self):
        return bool(self._applied and self._applied[0])

    def _configure(self):
        cfg, entries = self.store.cfg["flow"], self._entries()
        on = bool(cfg["enabled"] and flow.supported() and entries)
        wanted = (on, cfg["port"], tuple((entry["key"], entry["address"], entry["edge"]) for _pair, entry in entries))
        self.link.accept_files(cfg["files_max_mb"] * MIB if on and cfg["files"] and self.clipboard.rich() else None)
        if wanted == self._applied:
            self._watch_edges(on, entries)  # how it is watched may have changed,
            self._send_status()             # and when an edge was chosen, all the same
            return
        previous, self._applied = self._applied, wanted
        if previous is not None and not self._adopting:
            before = {key: edge for key, _address, edge in previous[2]}
            moved = {entry["key"] for _pair, entry in entries if before.get(entry["key"], entry["edge"]) != entry["edge"]}
            if moved:  # chosen here and now: the computers behind those edges follow
                peers = copy.deepcopy(cfg["peers"])
                for entry in peers:
                    if entry["key"] in moved:
                        entry["edge_at"] = time.time()
                self.store.set("flow", "peers", peers)
        if on:
            self.link.configure([{"key": entry["key"], "address": entry["address"]} for _pair, entry in entries], cfg["port"])
            if self._pointer is None and not environment.places_pointer():
                self._pointer = vinput.create_pointer()
        else:
            self.link.configure(None)
            self._problem = ""
            if self._pointer is not None:
                self._pointer.close()
                self._pointer = None
        self._disarm()
        self._watch_edges(on, entries)
        self._send_status()
        self.changed.emit()

    def _send_status(self):
        state, cfg = self.store.state, self.store.cfg["flow"]
        self.link.set_status(
            {"name": socket.gethostname(), "app": __version__, "channel": cfg["channel"], "present": bool(state["connected"])},
            {pair: {"edge": entry["edge"], "edge_at": entry["edge_at"]} for pair, entry in self._entries()})

    def _mouse_changed(self):
        state = self.store.state
        if state["connected"] and state.get("hosts"):
            # The channel the mouse is on while it is here is this computer's: the others need it to send the mouse back.
            if state["current_host"] != self.store.cfg["flow"]["channel"]:
                self.store.set("flow", "channel", state["current_host"])
        if not state["connected"]:
            self._armed = None  # the engine dropped the push when the mouse went
        elif self._arriving:
            # It is here. A hand that kept moving while the mouse was on its way must not push it straight back.
            self._arriving = False
            self._quiet_until = time.monotonic() + ARRIVAL_QUIET
        self._send_status()
        self.changed.emit()

    # -- the screen edges ---------------------------------------------------------------

    def _watch_edges(self, on, entries=()):
        if self._watcher is not None:
            cfg = self.store.cfg["flow"]
            was = self._watcher.watching
            self._watcher.watch(frozenset(entry["edge"] for _pair, entry in entries) if on else frozenset(),
                                corner=cfg["corner"] / 100.0, fullscreen=cfg["fullscreen"])
            if was != self._watcher.watching:
                self.changed.emit()

    def _click_through(self, button):
        """A click that landed on the watcher's line along the edge, repeated for what is under it."""
        keys = getattr(self.store.engine, "vinput", None)
        if keys is not None:
            keys.tap([button])

    def _scroll_through(self, across, up):
        keys = getattr(self.store.engine, "vinput", None)
        if keys is not None:
            keys.hscroll(across)
            keys.scroll(up)

    def _edge_report(self, report):
        """The pointer came to an edge, slid along it, or left it."""
        cfg = self.store.cfg["flow"]
        try:
            data = report
            edge = data["edge"]
            pair = next((found for found, entry in self._entries() if entry["edge"] == edge), None)
            if not data.get("at"):
                if pair is not None and pair == self._armed:
                    self._disarm()
                return
            pos = (float(data["x"]), float(data["y"]))
            screen = tuple(float(value) for value in data["screen"])
            if pair is None or len(screen) != 4:
                return
        except (ValueError, KeyError, TypeError):
            return
        along = flow.leave_fraction(edge, pos, screen)
        channel = self._target(pair)
        corner = cfg["corner"] / 100.0
        wanted = (channel is not None and corner <= along <= 1.0 - corner
                  and (cfg["fullscreen"] or not data.get("full")) and time.monotonic() >= self._quiet_until)
        if wanted:
            self._along = along
            if self._armed != pair:
                self._disarm()
                self._armed = pair
                self.store.engine.flow_arm(channel, edge, cfg["push_mm"])
        elif self._armed == pair:
            self._disarm()

    def _disarm(self):
        if self._armed is not None:
            self._armed = None
            self.store.engine.flow_disarm()

    # -- leaving and arriving ------------------------------------------------------------

    def _target(self, pair):
        """The Easy-Switch channel of the computer of ``pair``, or None if the mouse would not be received there."""
        state, peer = self.store.state, self._peers.get(pair)
        if not self._on() or peer is None or peer.get("quiet") or not state["connected"]:
            return None  # also while it does not answer: it may be asleep, and the mouse would be stranded there
        channel = peer.get("channel")
        if not isinstance(channel, int) or isinstance(channel, bool) or channel < 0 or channel == state.get("current_host"):
            return None  # that computer has not seen the mouse yet: nobody knows where to send it
        return channel

    def on_engine(self, payload):
        """The engine's side of a handover (see engine.Listener.on_event, "flow")."""
        what, _detail = payload
        if what == "asked":  # the action on a button, a gesture or the ring: to the first computer that can take the mouse
            if time.monotonic() < self._quiet_until:
                return
            for pair, _entry in self._entries():
                channel = self._target(pair)
                if channel is not None:
                    self._leaving, self._along = pair, None
                    self.store.engine.flow_leave(channel, False)
                    break
        elif what == "refused":
            self._armed = self._leaving = None
        elif what == "left":
            # which of the other computers it went to: the one on the channel the engine names
            pair = next((found for found, channel in self._channels.items() if channel == _detail and self._entry(found)),
                        self._armed or self._leaving)
            self._armed = self._leaving = None
            entry = self._entry(pair) if pair else None
            if entry is None:
                return
            self._crossed = (pair, time.monotonic())
            self.link.send({"t": "enter", "edge": entry["edge"] if self._along is not None else None, "at": self._along}, pair=pair)
            self._along = None
            self._send_clipboard(pair)

    def _send_clipboard(self, pair):
        cfg = self.store.cfg["flow"]
        if not cfg["clipboard"] and not cfg["files"]:
            return
        if self._transfer is not None and not self._transfer["sending"]:
            return  # files are on their way here and will replace this clipboard: what it holds now is not sent on
        content = self.clipboard.read(image_limit=flow.MAX_BLOB - 4096)
        mark = self.clipboard.signature(content)
        if mark == self._shared.get(pair):
            return  # that computer has it already
        if content["files"]:
            if not cfg["files"]:
                return
            try:
                items, sources = flow_files.collect(content["files"], cfg["files_max_mb"] * MIB)
            except flow_files.Refused:
                self.link.send({"t": "cue", "what": "failed"}, pair=pair)  # felt over there: that is where the hand is now
                return
            if not items:
                return
            self._offered[pair] = mark  # that computer has them only once they have all arrived
            self.link.offer_files(items, sources, pair)
            return
        token = secrets.token_hex(4)  # comes back when it has arrived: only then does that computer count as having it
        if content["image"]:
            if not cfg["clipboard"]:
                return
            self.link.send({"t": "clipboard", "mime": clipboard.PNG, "token": token}, content["image"], pair=pair)
        elif content["text"] and cfg["clipboard"] and len(content["text"].encode("utf-8")) <= flow.MAX_CLIPBOARD:
            self.link.send({"t": "clipboard", "text": content["text"], "token": token}, pair=pair)
        else:
            return
        self._unconfirmed[pair] = (token, mark)

    def _send_again(self, pair):
        """The link to that computer is back: if the mouse went there a moment ago and what was copied
        did not get through, send it now."""
        crossed, when = self._crossed
        if crossed == pair and time.monotonic() - when < RESEND_FOR and not self.store.state["connected"]:
            self._send_clipboard(pair)

    def _cue(self, what, pair):
        """Let the hand feel that copied files have arrived ("arrived") or could not be brought over
        ("failed"): on the mouse if it is here, or else on the computer of ``pair``, where it has gone."""
        waveform = self.store.cfg["flow"]["cue_arrived" if what == "arrived" else "cue_failed"]
        if self.store.state["connected"]:
            if waveform is not None:
                self.store.engine.play_haptic(waveform)
        elif pair:
            self.link.send({"t": "cue", "what": what}, pair=pair)

    def _on_link(self, kind, payload, pair):
        if kind in ("up", "status"):
            before = self._peers.get(pair) or {}
            link_id = payload.get("id") if kind == "up" else before.get("id")
            self._peers[pair] = dict({key: payload.get(key) for key in ("name", "channel", "present", "edge", "edge_at")},
                                     id=link_id, quiet=bool(before.get("quiet")) and kind == "status")
            channel = payload.get("channel")
            if isinstance(channel, int) and not isinstance(channel, bool) and channel >= 0:
                self._channels[pair] = channel
            if kind == "up":
                self._shared.pop(pair, None)
                self._unconfirmed.pop(pair, None)
                name = str(payload.get("name") or "")[:64]
                entry = self._entry(pair)
                if entry is not None and name and name != entry["name"]:
                    self._change(pair, name=name)
            self._agree_on_edges(pair)
            if kind == "up":
                self._send_again(pair)
        elif kind == "quiet":
            if pair in self._peers:
                self._peers[pair]["quiet"] = bool(payload)
            if payload and self._armed == pair:
                self._disarm()
        elif kind == "down":
            self._peers.pop(pair, None)
            if self._armed == pair:
                self._disarm()
        elif kind == "problem":
            self._problem = payload
        elif kind == "message":
            if self._on():
                self._on_message(payload, pair)
            return
        elif kind == "files-coming":
            self._progress(dict(payload, done=0), pair, sending=False)  # the clipboard here stays as it is until they are in
        elif kind == "files-progress":
            self._progress(payload, pair)
        elif kind == "files":
            self._transfer = None
            if self.clipboard.write(files=payload["paths"]):
                self._shared[pair] = self.clipboard.signature(files=payload["paths"])
            self._cue("arrived", pair)
        elif kind == "files-declined":
            self._transfer = None
            if payload != "cancelled":
                self._cue("failed", pair)
        elif kind == "files-sent":
            self._transfer = None
            if pair in self._offered:
                self._shared[pair] = self._offered.pop(pair)
        elif kind == "files-refused":
            self._transfer = None
            self._offered.pop(pair, None)
        elif kind == "offer":
            known = payload["id"] in self._offers
            self._offers[payload["id"]] = dict(payload, seen=time.monotonic())
            if known:
                return
        elif kind == "paired":
            self._paired(payload)
        elif kind == "pair-failed":
            self._pair_error = payload
        elif kind == "pairing":
            if payload is None or payload <= 0:
                self._pairing = None  # ran out, or too many wrong codes
            elif self._pairing is not None:
                self._pairing["left"] = payload
        else:
            return
        self.changed.emit()

    def _agree_on_edges(self, pair):
        """If that computer names an edge that does not face the one it stands behind here, one of the two gives way."""
        entry, peer = self._entry(pair), self._peers.get(pair)
        if entry is None or peer is None:
            return
        chosen = peer.get("edge_at")
        chosen = min(float(chosen), time.time() + 60.0) if isinstance(chosen, (int, float)) and not isinstance(chosen, bool) else 0.0
        edge = flow.edge_to_adopt((entry["edge"], entry["edge_at"], self.link.id), (peer.get("edge"), chosen, str(peer.get("id") or "")))
        if edge is None:
            return
        log.info("flow: %s is on the %s now", self._name(pair) or "the other computer", edge)
        peers = copy.deepcopy(self.store.cfg["flow"]["peers"])
        for other in peers:
            if flow.pair_id(other["key"]) == pair:
                other["edge"], other["edge_at"] = edge, chosen
            elif other["edge"] == edge:  # the one that stood there takes the place that came free, and its computer follows
                other["edge"], other["edge_at"] = entry["edge"], time.time()
        self._adopting = True
        try:
            self.store.set("flow", "peers", peers)
        finally:
            self._adopting = False

    def _paired(self, result):
        """A pairing by code worked: keep the key and the way to that computer, and switch Flow on."""
        self._pairing = None
        self._offers = {}
        cfg = copy.deepcopy(self.store.cfg["flow"])
        port, address, name = result.get("port"), result["address"], result.get("name") or ""
        if ":" in address:
            address = f"[{address}]"
        if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536 and port != cfg["port"]:
            address = f"{address}:{port}"
        their_id = str(result.get("id") or "")[:64]
        peers = [entry for entry in cfg["peers"] if not (their_id and entry["id"] == their_id)]  # paired anew: the old key goes
        peers = peers[-(flow.MAX_PEERS - 1):] if flow.MAX_PEERS > 1 else []  # no room for more: the oldest pairing goes
        taken = {entry["edge"] for entry in peers}
        peers.append({
            "key": result["key"], "id": their_id, "address": address, "name": name,
            "edge": next(side for side in ("right", "left", "top", "bottom") if side not in taken),
            # Where the two stand is taken from the computer that showed the code; the other one follows when they connect.
            "edge_at": time.time() if result.get("host") else 0.0,
        })
        cfg.update(enabled=True, peers=peers)
        self.store.set_section("flow", cfg)

    def _on_message(self, message, pair):
        kind = message.get("t")
        cfg = self.store.cfg["flow"]
        if kind == "enter":
            self._arriving = not self.store.state["connected"]
            self._quiet_until = time.monotonic() + (ARRIVAL_WAIT if self._arriving else ARRIVAL_QUIET)
            self._disarm()
            self.store.engine.expect_mouse()
            edge, along = message.get("edge"), message.get("at")
            if edge in flow.OPPOSITE and isinstance(along, (int, float)) and not isinstance(along, bool):
                screens = [(area.x(), area.y(), area.width(), area.height())
                           for area in (screen.geometry() for screen in QGuiApplication.screens())]
                if screens:  # come in through the edge facing the one it left by
                    self._put_pointer(flow.entry_point(flow.OPPOSITE[edge], along, screens), screens)
        elif kind == "clipboard" and cfg["clipboard"]:
            text, image = message.get("text"), message.get("data")
            if message.get("mime") == clipboard.PNG and isinstance(image, bytes) and image:
                mark = self.clipboard.signature(image=image)
                written = mark == self._shared.get(pair) or self.clipboard.write(image=image)  # twice the same: once is enough
            elif isinstance(text, str) and text and len(text.encode("utf-8")) <= flow.MAX_CLIPBOARD:
                mark = self.clipboard.signature(text=text)
                written = mark == self._shared.get(pair) or self.clipboard.write(text=text)
            else:
                return
            if written:
                self._shared[pair] = mark
                self.link.send({"t": "clipboard-ok", "token": message.get("token")}, pair=pair)
        elif kind == "clipboard-ok":
            token, mark = self._unconfirmed.get(pair, (None, None))
            if token is not None and message.get("token") == token:
                del self._unconfirmed[pair]
                self._shared[pair] = mark
        elif kind == "cue" and message.get("what") in ("arrived", "failed"):
            self._cue(message["what"], None)  # the mouse is here, if anywhere: play it, do not pass it on again

    def _put_pointer(self, point, screens):
        """Put the pointer at ``point`` of the desktop made of ``screens``."""
        if environment.places_pointer():
            QCursor.setPos(int(point[0]), int(point[1]))  # an X server and Windows let any program do that
        elif self._pointer is not None:
            self._pointer.move(point, flow.bounds(screens))

    def close(self):
        self._tick.stop()
        self._disarm()
        if self._watcher is not None:
            self._watcher.close()
        self.link.stop()
        if self._pointer is not None:
            self._pointer.close()
            self._pointer = None
