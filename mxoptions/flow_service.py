"""Flow, tied to the mouse and the desktop (flow.py has the idea and the link).

Watching the edge of the screen is left to KWin: a few lines of script, loaded over D-Bus while
Flow is on, say when the pointer comes to rest against the chosen edge and when it leaves again.
How hard it is then pushed on is measured from the mouse itself, by the engine. Elsewhere there
is no such watcher, and the "Move to the other computer" action is the way across."""

import json
import logging
import os
import socket
import tempfile
import time

import collections

from PySide6.QtCore import ClassInfo, QObject, QTimer, Signal, Slot
from PySide6.QtDBus import QDBus, QDBusConnection, QDBusMessage, QDBusServiceWatcher
from PySide6.QtGui import QGuiApplication

from . import __version__, clipboard, flow, flow_files, flow_pairing, vinput
from .i18n import _

log = logging.getLogger(__name__)

SERVICE = "org.mxoptions.Flow"
PATH = "/org/mxoptions/Flow"
INTERFACE = "org.mxoptions.Flow1"
KWIN = "org.kde.KWin"
SCRIPT_NAME = "mxoptions-flow"
ARRIVAL_QUIET = 1.5  # s after the mouse arrived during which the edge does not send it back
ARRIVAL_WAIT = 12.0  # s the mouse may take to arrive once the other computer has sent it
OFFER_AGE = 4.0      # s after which a computer that stopped announcing its pairing code is forgotten
MIB = 1024 * 1024
ANNOUNCE_FROM = 5 * MIB  # files of this size and more are announced when they start coming, not only when they are here
RATE_WINDOW = 10.0       # s the speed of a file transfer is averaged over
# The script only looks at where the pointer is. It speaks up when the pointer reaches the outer
# edge (one no other screen continues behind), when it slides along it, and when it leaves.
_EDGE_SCRIPT = """\
// Loaded by MX Options while Flow is on: says when the pointer rests against the %(edge)s edge of the desktop.
var EDGE = "%(edge)s", at = false, last = -1e9;
// The screen the pointer is on and how far the pointer is from its EDGE side, if no other screen continues there.
function probe(pos) {
    var screens = workspace.screens;
    for (var i = 0; i < screens.length; i++) {
        var g = screens[i].geometry;
        if (pos.x < g.x || pos.x >= g.x + g.width || pos.y < g.y || pos.y >= g.y + g.height) continue;
        var away = EDGE == "right" ? g.x + g.width - 1 - pos.x : EDGE == "left" ? pos.x - g.x
                 : EDGE == "top" ? pos.y - g.y : g.y + g.height - 1 - pos.y;
        if (away > 6) return null;
        var bx = EDGE == "right" ? g.x + g.width + 1 : EDGE == "left" ? g.x - 2 : pos.x;
        var by = EDGE == "bottom" ? g.y + g.height + 1 : EDGE == "top" ? g.y - 2 : pos.y;
        for (var j = 0; j < screens.length; j++) {
            var o = screens[j].geometry;
            if (j != i && bx >= o.x && bx < o.x + o.width && by >= o.y && by < o.y + o.height) return null;
        }
        return {screen: g, away: away};
    }
    return null;
}
function tell(report) {
    callDBus("%(service)s", "%(path)s", "%(interface)s", "Edge", JSON.stringify(report));
}
workspace.cursorPosChanged.connect(function () {
    var pos = workspace.cursorPos;
    var hit = probe(pos);
    // Reaching the edge takes touching it; leaving it takes a few pixels, because an edge that the desktop
    // uses for something of its own pushes the pointer a pixel back every time it is touched.
    if (hit && (at || hit.away <= 1.5)) {
        var along = (EDGE == "left" || EDGE == "right") ? pos.y : pos.x;
        if (!at || Math.abs(along - last) >= 8) {
            var front = workspace.activeWindow, g = hit.screen;
            at = true;
            last = along;
            tell({at: true, edge: EDGE, x: pos.x, y: pos.y, screen: [g.x, g.y, g.width, g.height],
                  full: !!(front && front.fullScreen)});
        }
    } else if (at) {
        at = false;
        tell({at: false, edge: EDGE});
    }
});
"""


def _kwin(method, *args):
    """Call KWin's script loader; the reply value, or None when there is no KWin to ask."""
    message = QDBusMessage.createMethodCall(KWIN, "/Scripting", "org.kde.kwin.Scripting", method)
    message.setArguments(list(args))
    reply = QDBusConnection.sessionBus().call(message, QDBus.CallMode.Block, 2000)
    if reply.type() != QDBusMessage.MessageType.ReplyMessage:
        return None
    values = reply.arguments()
    return values[0] if values else True


def kwin_running():
    interface = QDBusConnection.sessionBus().interface()
    return bool(interface and interface.isServiceRegistered(KWIN).value())


@ClassInfo({"D-Bus Interface": INTERFACE})
class _EdgeSink(QObject):
    """What the KWin script calls."""

    reached = Signal(str)

    @Slot(str)
    def Edge(self, report):
        self.reached.emit(report)


class FlowService(QObject):
    changed = Signal()                 # what status() tells has changed
    _from_link = Signal(str, object)   # carries the link's events from its thread onto this one

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        # The program sets this to something that tells the user: ("coming", {"count", "total", "name"}) when a lot
        # of copied files start to arrive, ("files", {"count", "name"}) when copied files are here, ("skipped",
        # {"why", "name", "limit"}) when they were not brought over.
        self.notify = lambda kind, details: None
        self._peer = None          # the other computer's status while the link is up
        self._problem = ""         # why this computer cannot listen
        self._pointer = None       # vinput.AbsolutePointer while Flow is on
        self._applied = None       # the settings in force
        self._edge = None          # the edge KWin watches for us
        self._exported = False
        self._armed = False        # the engine is measuring the push against the edge
        self._along = None         # where along the edge the pointer is, 0..1
        self._leaving = None       # where the pointer is leaving, until the engine says the mouse has gone
        self._quiet_until = 0.0    # until then the edge does nothing: the mouse is arriving or has only just arrived
        self._arriving = False     # the other computer has sent the mouse and it is not here yet
        self._shared = None        # self.clipboard.signature() of what both computers have
        self._offered_mark = None  # ... and of the files being sent, which it will be once they have arrived
        self._transfer = None      # files on their way: {"done", "total", "count", "sending", "samples"}
        self._adopting = False     # the edge is being changed to match the other computer, not by the user
        self._pairing = None       # while this computer shows a code: {"code", "left", "until"}
        self._offers = {}          # computers nearby showing a code: id -> {"id", "name", "address", "port", "seen"}
        self._pair_error = ""      # why the last attempt to pair failed: "code", "closed", "unreachable"
        self._watching = False     # the settings page is open and lists computers showing a code
        self._sink = _EdgeSink(self)
        self._sink.reached.connect(self._edge_report)
        self._from_link.connect(self._on_link)
        self.clipboard = clipboard  # read(), write() and signature() of what is copied
        self.link = flow.Link(self._from_link.emit, staging=flow_files.STAGING)
        self.link.start()
        self._tick = QTimer(self, interval=1000)  # while a code is on show or offers are listed: keep them current
        self._tick.timeout.connect(self._second_passed)
        watcher = QDBusServiceWatcher(KWIN, QDBusConnection.sessionBus(),
                                      QDBusServiceWatcher.WatchModeFlag.WatchForRegistration, self)
        watcher.serviceRegistered.connect(lambda _name: self._kwin_started())
        store.changed.connect(self._configure)
        store.state_changed.connect(lambda _state: self._mouse_changed())
        self._configure()

    # -- for the settings page ---------------------------------------------------

    def status(self):
        cfg = self.store.cfg["flow"]
        pairing = None
        if self._pairing is not None:
            pairing = {"code": self._pairing["code"], "left": self._pairing["left"],
                       "seconds": max(0, int(self._pairing["until"] - time.monotonic()))}
        return {
            "supported": flow.supported(),
            "on": self._on(),
            "paired": flow.key_usable(cfg["key"]),
            "address": flow.local_address(),
            "problem": self._problem,
            "peer": dict(self._peer) if self._peer else None,
            "channel": cfg["channel"],
            "here": bool(self.store.state["connected"]),   # the mouse is on this computer
            "edges": self._edge is not None,                # the screen edge is being watched
            "rich": self.clipboard.rich(),                       # pictures and files can cross, not only text
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
                "rate": rate}

    def _progress(self, payload, sending=None):
        now = time.monotonic()
        if self._transfer is None:
            self._transfer = {"samples": collections.deque()}
        transfer = self._transfer
        transfer.update(done=payload.get("done", 0), total=payload["total"], count=payload["count"],
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

    def unpair(self):
        """Forget the other computer."""
        cfg = dict(self.store.cfg["flow"], enabled=False, key="", peer="", peer_name="")
        self.store.set_section("flow", cfg)

    def _info(self):
        return {"name": socket.gethostname(), "port": self.store.cfg["flow"]["port"]}

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
        cfg = self.store.cfg["flow"]
        on = cfg["enabled"] and flow.supported() and flow.key_usable(cfg["key"])
        wanted = (on, cfg["key"], cfg["port"], cfg["peer"], cfg["edge"])
        self.link.accept_files(cfg["files_max_mb"] * MIB if on and cfg["files"] and self.clipboard.rich() else None)
        if wanted == self._applied:
            return
        previous, self._applied = self._applied, wanted
        if previous is not None and previous[4] != cfg["edge"] and not self._adopting:
            self.store.set("flow", "edge_at", time.time())  # chosen here and now: the other computer follows
        if on:
            self.link.configure(cfg["key"], cfg["port"], cfg["peer"])
            if self._pointer is None:
                self._pointer = vinput.create_pointer()
        else:
            self.link.configure(None)
            self._problem = ""
            if self._pointer is not None:
                self._pointer.close()
                self._pointer = None
        self._disarm()
        self._watch_edge(cfg["edge"] if on else None)
        self._send_status()
        self.changed.emit()

    def _send_status(self):
        state, cfg = self.store.state, self.store.cfg["flow"]
        self.link.set_status({"name": socket.gethostname(), "app": __version__, "channel": cfg["channel"],
                              "present": bool(state["connected"]), "edge": cfg["edge"], "edge_at": cfg["edge_at"]})

    def _mouse_changed(self):
        state = self.store.state
        if state["connected"] and state.get("hosts"):
            # The channel the mouse is on while it is here is this computer's: the other one needs it to send the mouse back.
            if state["current_host"] != self.store.cfg["flow"]["channel"]:
                self.store.set("flow", "channel", state["current_host"])
        if not state["connected"]:
            self._armed = False  # the engine dropped the push when the mouse went
        elif self._arriving:
            # It is here. A hand that kept moving while the mouse was on its way must not push it straight back.
            self._arriving = False
            self._quiet_until = time.monotonic() + ARRIVAL_QUIET
        self._send_status()
        self.changed.emit()

    # -- the screen edge ---------------------------------------------------------------

    def _export(self, wanted):
        bus = QDBusConnection.sessionBus()
        if wanted and not self._exported:
            if not bus.registerObject(PATH, self._sink, QDBusConnection.RegisterOption.ExportAllSlots) or not bus.registerService(SERVICE):
                log.warning("flow: cannot be reached over D-Bus: %s", bus.lastError().message())
                bus.unregisterObject(PATH)
                return False
            self._exported = True
        elif not wanted and self._exported:
            bus.unregisterService(SERVICE)
            bus.unregisterObject(PATH)
            self._exported = False
        return self._exported

    def _watch_edge(self, edge):
        if edge == self._edge:
            return
        if self._edge is not None:
            _kwin("unloadScript", SCRIPT_NAME)
            self._edge = None
        if edge is None:
            self._export(False)
            return
        if not self._export(True) or not kwin_running():
            return
        path = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(), SCRIPT_NAME + ".js")
        try:
            with open(path, "w") as f:
                f.write(_EDGE_SCRIPT % {"edge": edge, "service": SERVICE, "path": PATH, "interface": INTERFACE})
        except OSError as e:
            log.warning("flow: could not write the edge script: %s", e)
            return
        _kwin("unloadScript", SCRIPT_NAME)  # one left behind by a copy of the program that did not exit cleanly
        loaded = _kwin("loadScript", path, SCRIPT_NAME)
        if loaded is None or loaded < 0:
            log.warning("flow: KWin did not take the edge script")
            return
        _kwin("start")  # runs what is loaded and not running yet
        self._edge = edge
        log.info("flow: watching the %s edge", edge)

    def _kwin_started(self):
        """KWin came up (again): what it had loaded for us is gone."""
        self._edge = None
        if self._on():
            self._watch_edge(self.store.cfg["flow"]["edge"])
            self.changed.emit()

    def _edge_report(self, report):
        """The pointer came to the edge, slid along it, or left it."""
        cfg = self.store.cfg["flow"]
        try:
            data = json.loads(report)
            if not data.get("at"):
                self._disarm()
                return
            edge, pos = data["edge"], (float(data["x"]), float(data["y"]))
            screen = tuple(float(value) for value in data["screen"])
            if edge != cfg["edge"] or len(screen) != 4:
                return
        except (ValueError, KeyError, TypeError):
            return
        self._along = flow.leave_fraction(edge, pos, screen)
        channel = self._target()
        corner = cfg["corner"] / 100.0
        wanted = (channel is not None and corner <= self._along <= 1.0 - corner
                  and (cfg["fullscreen"] or not data.get("full")) and time.monotonic() >= self._quiet_until)
        if wanted and not self._armed:
            self._armed = True
            self.store.engine.flow_arm(channel, edge, cfg["push_mm"])
        elif not wanted:
            self._disarm()

    def _disarm(self):
        if self._armed:
            self._armed = False
            self.store.engine.flow_disarm()

    # -- leaving and arriving ------------------------------------------------------------

    def _target(self):
        """The Easy-Switch channel to send the mouse to, or None if it would not be received there."""
        state, peer = self.store.state, self._peer
        if not self._on() or peer is None or not state["connected"]:
            return None
        channel = peer.get("channel")
        if not isinstance(channel, int) or isinstance(channel, bool) or channel < 0 or channel == state.get("current_host"):
            return None  # the other computer has not seen the mouse yet: nobody knows where to send it
        return channel

    def on_engine(self, payload):
        """The engine's side of a handover (see engine.Listener.on_event, "flow")."""
        what, _detail = payload
        if what == "asked":  # the action on a button, a gesture or the ring
            channel = self._target()
            if channel is not None and time.monotonic() >= self._quiet_until:
                self._along = None
                self.store.engine.flow_leave(channel, False)
        elif what == "refused":
            self._armed = False
        elif what == "left":
            self._armed = False
            edge = self.store.cfg["flow"]["edge"] if self._along is not None else None
            self.link.send({"t": "enter", "edge": edge, "at": self._along})
            self._along = None
            self._send_clipboard()

    def _send_clipboard(self):
        cfg = self.store.cfg["flow"]
        if not cfg["clipboard"] and not cfg["files"]:
            return
        content = self.clipboard.read(image_limit=flow.MAX_BLOB - 4096)
        mark = self.clipboard.signature(content)
        if mark == self._shared:
            return  # the other computer has it already
        if content["files"]:
            if not cfg["files"]:
                return
            try:
                items, sources = flow_files.collect(content["files"], cfg["files_max_mb"] * MIB)
            except flow_files.Refused as e:
                self.link.send({"t": "files-skipped", "why": str(e)})  # said over there: that is where the user is now
                return
            if not items:
                return
            self._offered_mark = mark  # the other computer has them only once they have all arrived
            self.link.offer_files(items, sources)
            return
        elif content["image"]:
            if not cfg["clipboard"]:
                return
            self.link.send({"t": "clipboard", "mime": clipboard.PNG}, content["image"])
        elif content["text"] and cfg["clipboard"] and len(content["text"].encode("utf-8")) <= flow.MAX_CLIPBOARD:
            self.link.send({"t": "clipboard", "text": content["text"]})
        else:
            return
        self._shared = mark

    def _on_link(self, kind, payload):
        if kind in ("up", "status"):
            link_id = payload.get("id") if kind == "up" else (self._peer or {}).get("id")
            self._peer = dict({key: payload.get(key) for key in ("name", "channel", "present", "edge", "edge_at")}, id=link_id)
            if kind == "up":
                self._shared = None
                name = str(payload.get("name") or "")[:64]
                if name and name != self.store.cfg["flow"]["peer_name"]:
                    self.store.set("flow", "peer_name", name)
            self._agree_on_edges()
        elif kind == "down":
            self._peer = None
            self._disarm()
        elif kind == "problem":
            self._problem = payload
        elif kind == "message":
            if self._on():
                self._on_message(payload)
            return
        elif kind == "files-coming":
            self._files_coming(payload)
        elif kind == "files-progress":
            self._progress(payload)
        elif kind == "files":
            self._transfer = None
            self._files_arrived(payload)
        elif kind == "files-declined":
            self._transfer = None
            if payload != "cancelled":
                self._not_brought(payload)
        elif kind == "files-sent":
            self._transfer = None
            self._shared, self._offered_mark = self._offered_mark, None
        elif kind == "files-refused":
            self._transfer = None
            self._offered_mark = None
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

    def _agree_on_edges(self):
        """If the other computer names an edge that does not face this one's, one of the two gives way."""
        cfg, peer = self.store.cfg["flow"], self._peer
        chosen = peer.get("edge_at")
        chosen = min(float(chosen), time.time() + 60.0) if isinstance(chosen, (int, float)) and not isinstance(chosen, bool) else 0.0
        edge = flow.edge_to_adopt((cfg["edge"], cfg["edge_at"], self.link.id), (peer.get("edge"), chosen, str(peer.get("id") or "")))
        if edge is not None:
            log.info("flow: the other computer is on the %s now", edge)
            self._adopting = True
            try:
                self.store.set("flow", "edge_at", chosen)
                self.store.set("flow", "edge", edge)
            finally:
                self._adopting = False

    def _paired(self, result):
        """A pairing by code worked: keep the key and the way to the other computer, and switch Flow on."""
        self._pairing = None
        self._offers = {}
        port = result.get("port")
        address = result["address"]
        if ":" in address:
            address = f"[{address}]"
        if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536 and port != self.store.cfg["flow"]["port"]:
            address = f"{address}:{port}"
        cfg = dict(self.store.cfg["flow"], enabled=True, key=result["key"], peer=address, peer_name=result.get("name") or "")
        # Where the two stand is taken from the computer that showed the code; the other one follows when they connect.
        cfg["edge_at"] = time.time() if result.get("host") else 0.0
        self.store.set_section("flow", cfg)

    def _on_message(self, message):
        kind = message.get("t")
        cfg = self.store.cfg["flow"]
        if kind == "enter":
            self._arriving = not self.store.state["connected"]
            self._quiet_until = time.monotonic() + (ARRIVAL_WAIT if self._arriving else ARRIVAL_QUIET)
            self._disarm()
            self.store.engine.expect_mouse()
            edge, along = message.get("edge"), message.get("at")
            if edge in flow.OPPOSITE and isinstance(along, (int, float)) and not isinstance(along, bool) and self._pointer:
                screens = [(area.x(), area.y(), area.width(), area.height())
                           for area in (screen.geometry() for screen in QGuiApplication.screens())]
                if screens:  # come in through the edge facing the one it left by
                    self._pointer.move(flow.entry_point(flow.OPPOSITE[edge], along, screens), flow.bounds(screens))
        elif kind == "clipboard" and cfg["clipboard"]:
            text, image = message.get("text"), message.get("data")
            if message.get("mime") == clipboard.PNG and isinstance(image, bytes) and image:
                if self.clipboard.write(image=image):
                    self._shared = self.clipboard.signature(image=image)
            elif isinstance(text, str) and text and len(text.encode("utf-8")) <= flow.MAX_CLIPBOARD:
                if self.clipboard.write(text=text):
                    self._shared = self.clipboard.signature(text=text)
        elif kind == "files-skipped":
            self._not_brought(str(message.get("why") or ""))

    def _files_coming(self, coming):
        """Copied files have started to arrive. Until they are here, pasting must not bring up whatever was
        copied on this computer before: a note takes its place (an empty clipboard would be refilled by the
        desktop with the previous entry)."""
        name = (self._peer or {}).get("name") or self.store.cfg["flow"]["peer_name"] or _("the other computer")
        note = _("MX Options is bringing {count} file(s) ({size}) over from {name}. Paste again when they have arrived.").format(
            count=coming["count"], size=flow_files.human(coming["total"]), name=name)
        if self.clipboard.write(text=note):
            self._shared = self.clipboard.signature(text=note)  # nothing to carry back if the pointer crosses again
        self._progress(dict(coming, done=0), sending=False)
        if coming["total"] >= ANNOUNCE_FROM:
            self.notify("coming", dict(coming, name=name))

    def _files_arrived(self, arrived):
        if not self.clipboard.write(files=arrived["paths"]):
            return
        self._shared = self.clipboard.signature(files=arrived["paths"])
        name = (self._peer or {}).get("name") or self.store.cfg["flow"]["peer_name"]
        self.notify("files", {"count": arrived["count"], "name": name})

    def _not_brought(self, why):
        name = (self._peer or {}).get("name") or self.store.cfg["flow"]["peer_name"]
        self.notify("skipped", {"why": why, "name": name, "limit": self.store.cfg["flow"]["files_max_mb"]})

    def close(self):
        self._tick.stop()
        self._disarm()
        self._watch_edge(None)
        self.link.stop()
        if self._pointer is not None:
            self._pointer.close()
            self._pointer = None
