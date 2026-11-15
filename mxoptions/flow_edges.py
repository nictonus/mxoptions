"""Watching the edges of the desktop for Flow.

Flow needs to hear when the pointer comes to rest against an edge another computer is behind,
when it slides along that edge, and when it leaves again. How hard it is then pushed on is
measured from the mouse itself, by the engine. No two kinds of desktop let a program see where
the pointer is in the same way, so there are three watchers:

  kwin    KDE Plasma, on Wayland or X11: a few lines of script loaded into KWin over D-Bus.
  poll    Any other X11 desktop, and Windows: the system is asked where the pointer is, sixty times
          a second.
  strips  Wayland compositors with the layer-shell protocol (sway, Hyprland, river, Wayfire, labwc,
          niri, COSMIC): a see-through line one pixel wide along the edge, which hears of the
          pointer only while the pointer is on it. That is all a Wayland program is ever told. The
          line takes the clicks and the scrolling meant for what is under it, so it steps aside
          and repeats them the moment one arrives.

GNOME on Wayland offers none of the three: there the "Move to the other computer" action, on a
button, a gesture or the ring, is the way across.

Each watcher reports through ``reported``: {"at": True, "edge": ..., "x": ..., "y": ..., "screen":
[x, y, width, height], "full": bool} when the pointer reaches an edge or has slid along it, and
{"at": False, "edge": ...} when it leaves. MXOPTIONS_FLOW_EDGES names a watcher to use (or "none")
instead of the choice made here.

Asked to ``follow``, kwin and poll also tell through ``screen_seen`` which screen of this computer the pointer
is on, for the tick on switching screens; with Flow off too. Strips cannot: a line hears only of itself."""

import json
import logging
import os
import sys
import tempfile

from PySide6.QtCore import ClassInfo, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtDBus import QDBus, QDBusConnection, QDBusMessage, QDBusServiceWatcher
from PySide6.QtGui import QCursor, QGuiApplication

from . import environment, flow, x11

log = logging.getLogger(__name__)

SERVICE = "org.mxoptions.Flow"
PATH = "/org/mxoptions/Flow"
INTERFACE = "org.mxoptions.Flow1"
KWIN = "org.kde.KWin"
SCRIPT_NAME = "mxoptions-flow"
LAYER_SHELL = "zwlr_layer_shell_v1"
REACH, LEAVE, SLIDE = 1.5, 6, 8  # px: reaching an edge takes touching it, leaving it a few pixels; a slide worth telling
POLL_INTERVAL = 16               # ms between two looks at the pointer
SETTLE_LOOKS = 3                 # looks in a row that must agree before poll tells which screen the pointer is on
THICKNESS = int(os.environ.get("MXOPTIONS_FLOW_LINE", "1") or 1)  # px of the line along an edge
CLICK_WITHIN = (0.45, 4)         # a press on a strip is a click to repeat if it ends within this many s and px
ASIDE_CLICK, ASIDE_SCROLL, ASIDE_HELD = 700, 1200, 2500  # ms a strip stays out of the way after each
# The script only looks at where the pointer is. For every edge that leads to another computer it
# speaks up when the pointer reaches it (the outer edge: one no other screen continues behind), when
# it slides along it, and when it leaves.
_EDGE_SCRIPT = """\
// Loaded by MX Options: says when the pointer rests against an edge of the desktop that another
// computer is behind (EDGES), and which screen the pointer is on (SCREENS).
var EDGES = %(edges)s, SCREENS = %(screens)s, at = {}, last = {}, shown = "";
// The screen the pointer is on and how far the pointer is from its EDGE side, if no other screen continues there.
function probe(pos, EDGE) {
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
// Screen the pointer is on (first that holds it; null between screens) and all screens, told when either changes.
function follow(pos) {
    var screens = workspace.screens, all = [], on = null;
    for (var i = 0; i < screens.length; i++) {
        var g = screens[i].geometry;
        all.push([g.x, g.y, g.width, g.height]);
        if (on === null && pos.x >= g.x && pos.x < g.x + g.width && pos.y >= g.y && pos.y < g.y + g.height) on = all[i];
    }
    var key = JSON.stringify([on, all]);
    if (key != shown) {
        shown = key;
        tell({on: on, screens: all});
    }
}
workspace.cursorPosChanged.connect(function () {
    var pos = workspace.cursorPos;
    for (var k = 0; k < EDGES.length; k++) {
        var EDGE = EDGES[k];
        var hit = probe(pos, EDGE);
        // Reaching the edge takes touching it; leaving it takes a few pixels, because an edge that the desktop
        // uses for something of its own pushes the pointer a pixel back every time it is touched.
        if (hit && (at[EDGE] || hit.away <= 1.5)) {
            var along = (EDGE == "left" || EDGE == "right") ? pos.y : pos.x;
            if (!at[EDGE] || Math.abs(along - last[EDGE]) >= 8) {
                var front = workspace.activeWindow, g = hit.screen;
                at[EDGE] = true;
                last[EDGE] = along;
                tell({at: true, edge: EDGE, x: pos.x, y: pos.y, screen: [g.x, g.y, g.width, g.height],
                      full: !!(front && front.fullScreen)});
            }
        } else if (at[EDGE]) {
            at[EDGE] = false;
            tell({at: false, edge: EDGE});
        }
    }
    if (SCREENS) follow(pos);
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


def _fullscreen_in_front():
    if sys.platform == "win32":
        from . import windows

        return windows.fullscreen_in_front()
    return x11.fullscreen_in_front()


def _screens():
    return [(area.x(), area.y(), area.width(), area.height()) for area in (screen.geometry() for screen in QGuiApplication.screens())]


class Watcher(QObject):
    reported = Signal(object)  # a report, as set out at the top
    changed = Signal()         # whether the edges are watched has changed by itself
    screen_seen = Signal(object, object)  # (screen or None, all screens) as tuples, when either changes, while following

    name = ""
    takes_clicks = False       # a line along the edge is in the way of clicks there
    follows_screens = False    # can tell which screen the pointer is on

    def __init__(self, parent=None, click=None, scroll=None):
        super().__init__(parent)
        self.watching = False

    def watch(self, edges, corner=0.0, fullscreen=False):
        """Watch these edges (none: stop). ``corner`` is the share of an edge at each of its ends that leads
        nowhere, and ``fullscreen`` whether to watch while a full-screen window is in front: a watcher that
        cannot tell the service those things itself keeps to them."""
        raise NotImplementedError

    def follow(self, on):
        """Tell (True) or stop telling (False) through screen_seen which screen the pointer is on."""

    def close(self):
        self.follow(False)
        self.watch(frozenset())


@ClassInfo({"D-Bus Interface": INTERFACE})
class _EdgeSink(QObject):
    """What the KWin script calls."""

    reached = Signal(str)

    @Slot(str)
    def Edge(self, report):
        self.reached.emit(report)


class KWin(Watcher):
    name = "kwin"
    follows_screens = True
    _NOTHING = (frozenset(), False)

    def __init__(self, parent=None, click=None, scroll=None):
        super().__init__(parent)
        self._loaded = self._NOTHING  # (edges, follow) KWin runs the script for
        self._wanted = self._NOTHING
        self._exported = False
        self._folder = None           # tempfile.TemporaryDirectory of the script: this program's alone, made when needed
        self._sink = _EdgeSink(self)
        self._sink.reached.connect(self._heard)
        watcher = QDBusServiceWatcher(KWIN, QDBusConnection.sessionBus(), QDBusServiceWatcher.WatchModeFlag.WatchForRegistration, self)
        watcher.serviceRegistered.connect(lambda _name: self._started())

    def _heard(self, report):
        try:
            data = json.loads(report)
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        if "screens" in data:
            seen = flow.screen_report(data) if self._wanted[1] else None  # not followed now: an old word, dropped
            if seen is not None:
                self.screen_seen.emit(*seen)
        else:
            self.reported.emit(data)

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

    def watch(self, edges, corner=0.0, fullscreen=False):
        self._load(frozenset(edges), self._wanted[1])

    def follow(self, on):
        self._load(self._wanted[0], bool(on))

    def _load(self, edges, follow):
        """Have KWin run the script for these edges and for following the screens; for neither, none of it."""
        self._wanted = (edges, follow)
        if not edges and not follow:
            # Before any shortcut: an attempt that failed half way may have left this program on the bus.
            if self._loaded != self._NOTHING or self._exported:
                _kwin("unloadScript", SCRIPT_NAME)
            self._loaded = self._NOTHING
            self.watching = False
            self._export(False)
            return
        if self._wanted == self._loaded:
            return
        before = self._loaded
        if before != self._NOTHING:
            _kwin("unloadScript", SCRIPT_NAME)
            self._loaded = self._NOTHING
            self.watching = False
        if not self._export(True) or not kwin_running():
            return
        try:
            if self._folder is None:
                self._folder = tempfile.TemporaryDirectory(prefix=SCRIPT_NAME + "-")
            path = os.path.join(self._folder.name, SCRIPT_NAME + ".js")
            with open(path, "w") as f:
                f.write(_EDGE_SCRIPT % {"edges": json.dumps(sorted(edges)), "screens": json.dumps(follow), "service": SERVICE,
                                        "path": PATH, "interface": INTERFACE})
        except OSError as e:
            log.warning("flow: could not write the edge script: %s", e)
            return
        _kwin("unloadScript", SCRIPT_NAME)  # one left behind by a copy of the program that did not exit cleanly
        loaded = _kwin("loadScript", path, SCRIPT_NAME)
        if loaded is None or loaded < 0:
            log.warning("flow: KWin did not take the edge script")
            return
        _kwin("start")  # runs what is loaded and not running yet
        self._loaded = (edges, follow)
        self.watching = bool(edges)
        if edges:
            log.info("flow: KWin watches the %s edge", " and the ".join(sorted(edges)))
        if follow and not before[1]:
            log.info("flow: KWin says which screen the pointer is on")

    def _started(self):
        """KWin came up (again): what it had loaded for us is gone."""
        self._loaded = self._NOTHING
        self.watching = False
        if self._wanted != self._NOTHING:
            self._load(*self._wanted)
        self.changed.emit()

    def close(self):
        self._load(frozenset(), False)  # both at once: no reload for the edges alone on the way
        if self._folder is not None:
            self._folder.cleanup()
            self._folder = None


class Poll(Watcher):
    name = "poll"
    follows_screens = True

    def __init__(self, parent=None, click=None, scroll=None):
        super().__init__(parent)
        self._edges = frozenset()
        self._at = {}    # edge -> the pointer rests against it
        self._last = {}  # edge -> where along it the pointer was when that was last told
        self._follow = False
        self._seen = (None, 0)  # (screen, screens) of the last look, and how many looks in a row saw it
        self._told = None       # (screen, screens) told last through screen_seen
        self._timer = QTimer(self, interval=POLL_INTERVAL)
        self._timer.timeout.connect(self._look)

    def watch(self, edges, corner=0.0, fullscreen=False):
        if edges == self._edges:
            return
        self._edges = edges
        self._at.clear()
        self.watching = bool(edges)
        self._sync()
        if edges:
            log.info("flow: asking the system where the pointer is, for the %s edge", " and the ".join(sorted(edges)))

    def follow(self, on):
        on = bool(on)
        if on == self._follow:
            return
        self._follow, self._seen, self._told = on, (None, 0), None
        self._sync()
        if on:
            log.info("flow: asking the system where the pointer is, for the screen it is on")

    def _sync(self):
        if self._edges or self._follow:
            self._timer.start()
        else:
            self._timer.stop()

    def _look(self):
        where = QCursor.pos()
        self.look((where.x(), where.y()), _screens())

    def look(self, pos, screens):
        for edge in self._edges:
            hit = flow.at_edge(edge, pos, screens)
            if hit is not None and hit[1] <= LEAVE and (self._at.get(edge) or hit[1] <= REACH):
                along = pos[1] if edge in ("left", "right") else pos[0]
                if not self._at.get(edge) or abs(along - self._last[edge]) >= SLIDE:
                    self._at[edge], self._last[edge] = True, along
                    self.reported.emit({"at": True, "edge": edge, "x": pos[0], "y": pos[1], "screen": list(hit[0]),
                                        "full": _fullscreen_in_front()})
            elif self._at.get(edge):
                self._at[edge] = False
                self.reported.emit({"at": False, "edge": edge})
        if self._follow:
            # Told once SETTLE_LOOKS looks agree: the system may move the pointer before Qt knows of new screens.
            answer = (flow.screen_at(pos, screens), tuple(tuple(screen) for screen in screens))
            count = self._seen[1] + 1 if answer == self._seen[0] else 1
            self._seen = (answer, count)
            if count >= SETTLE_LOOKS and answer != self._told:
                self._told = answer
                self.screen_seen.emit(*answer)


def strip_places(edges, screens, corner):
    """Where the lines go: [(edge, screen, start, length)], the stretch of each watched edge of each screen that
    no other screen continues behind, without ``corner`` (a share of that side of the screen) at its ends."""
    places = []
    for edge in sorted(edges):
        for screen in screens:
            span = flow.outer_span(edge, screen, screens)
            if span is None:
                continue
            side = screen[3] if edge in ("left", "right") else screen[2]
            start = max(span[0], int(round(side * corner)))
            end = min(span[0] + span[1], side - int(round(side * corner)))
            if end - start >= 2:
                places.append((edge, screen, start, end - start))
    return places


class Strips(Watcher):
    name = "strips"
    takes_clicks = True

    def __init__(self, parent=None, click=None, scroll=None):
        super().__init__(parent)
        from PySide6.QtQml import QQmlComponent, QQmlEngine

        self._click = click or (lambda button: None)    # repeat a click of "BTN_LEFT", ... for what is under the line
        self._scroll = scroll or (lambda dx, dy: None)  # ... and scrolling, in wheel units
        self._wanted = (frozenset(), 0.0, False)
        self._lines = []      # [{"window", "edge", "screen", "start"}]
        self._at = {}         # index of a line -> where along it the pointer was when that was last told
        self._pressed = None  # (index, button, x, y, when) of a press on a line
        self._aside = QTimer(self, singleShot=True)
        self._aside.timeout.connect(self._come_back)
        self._engine = QQmlEngine(self)
        self._engine.warnings.connect(lambda warnings: [log.warning("flow: %s", w.toString()) for w in warnings])
        self._engine.rootContext().setContextProperty("watcher", self)
        self._component = QQmlComponent(self._engine, QUrl.fromLocalFile(os.path.join(os.path.dirname(__file__), "ui", "flow_edge.qml")))
        if not self._component.isReady():
            raise RuntimeError(self._component.errorString().strip() or "the line along the edge could not be made")
        app = QGuiApplication.instance()
        for signal in (app.screenAdded, app.screenRemoved):
            signal.connect(lambda _screen: QTimer.singleShot(300, self._lay))  # once the screens have settled
        app.primaryScreenChanged.connect(lambda _screen: QTimer.singleShot(300, self._lay))

    def watch(self, edges, corner=0.0, fullscreen=False):
        wanted = (edges, corner, bool(fullscreen))
        if wanted == self._wanted:
            return
        self._wanted = wanted
        self._lay()
        if edges:
            log.info("flow: a line along the %s edge hears of the pointer", " and the ".join(sorted(edges)))

    def _clear(self):
        for index in list(self._at):
            self._gone(index)
        for line in self._lines:
            line["window"].setVisible(False)
            line["window"].deleteLater()
        self._lines = []
        self._pressed = None

    def _lay(self):
        """Put the lines where they belong, afresh."""
        edges, corner, above = self._wanted
        self._clear()
        screens = QGuiApplication.screens()
        areas = _screens()
        for edge, area, start, length in strip_places(edges, areas, corner):
            upright = edge in ("left", "right")
            window = self._component.createWithInitialProperties({
                "edge": edge, "index": len(self._lines), "start": start, "above": above,
                "width": THICKNESS if upright else length, "height": length if upright else THICKNESS})
            if window is None:
                log.warning("flow: %s", self._component.errorString().strip())
                continue
            window.setScreen(screens[areas.index(area)])
            self._lines.append({"window": window, "edge": edge, "screen": area, "start": start})
            window.setVisible(not self._aside.isActive())
        self.watching = bool(self._lines)

    def _step_aside(self, duration):
        """Get out of the way of what is under the lines for a while."""
        for index in list(self._at):
            self._gone(index)
        for line in self._lines:
            line["window"].setVisible(False)
        self._aside.start(max(duration, self._aside.remainingTime()))

    def _come_back(self):
        for line in self._lines:
            line["window"].setVisible(True)

    def _gone(self, index):
        if self._at.pop(index, None) is not None and index < len(self._lines):
            self.reported.emit({"at": False, "edge": self._lines[index]["edge"]})

    # -- what the lines hear (QML) ----------------------------------------------------

    @Slot(int, float, float)
    def at(self, index, x, y):
        if not 0 <= index < len(self._lines) or self._aside.isActive():
            return
        line = self._lines[index]
        left, top, width, height = line["screen"]
        if line["edge"] in ("left", "right"):
            pos = (left if line["edge"] == "left" else left + width - 1, top + line["start"] + y)
            along = pos[1]
        else:
            pos = (left + line["start"] + x, top if line["edge"] == "top" else top + height - 1)
            along = pos[0]
        if index not in self._at or abs(along - self._at[index]) >= SLIDE:
            self._at[index] = along
            # A full-screen window covers the line unless the line was put above it: it is never both here and in front.
            self.reported.emit({"at": True, "edge": line["edge"], "x": pos[0], "y": pos[1], "screen": list(line["screen"]),
                                "full": False})

    @Slot(int)
    def gone(self, index):
        self._gone(index)

    @Slot(int, int, float, float, float)
    def pressed(self, index, button, x, y, when):
        self._pressed = (index, button, x, y, when)

    @Slot(int, int, float, float, float)
    def released(self, index, button, x, y, when):
        pressed, self._pressed = self._pressed, None
        name = {1: "BTN_LEFT", 2: "BTN_RIGHT", 4: "BTN_MIDDLE"}.get(button)
        clicked = (pressed is not None and pressed[:2] == (index, button) and name is not None
                   and when - pressed[4] <= CLICK_WITHIN[0] * 1000
                   and abs(x - pressed[2]) + abs(y - pressed[3]) <= CLICK_WITHIN[1])
        # The click was meant for what is under the line. Step aside and click there in its stead; something
        # held or dragged cannot be repeated, but the next try will find the line gone.
        self._step_aside(ASIDE_CLICK if clicked else ASIDE_HELD)
        if clicked:
            QTimer.singleShot(40, lambda: self._click(name))  # once the line is off the screen

    @Slot(int, float, float)
    def scrolled(self, index, dx, dy):
        self._step_aside(ASIDE_SCROLL)
        QTimer.singleShot(40, lambda: self._scroll(dx, dy))

    def close(self):
        self._aside.stop()
        self._wanted = (frozenset(), 0.0, False)
        self._clear()
        self.watching = False


_MAKERS = {"kwin": KWin, "poll": Poll, "strips": Strips}


def _choice():
    forced = os.environ.get("MXOPTIONS_FLOW_EDGES", "").strip().lower()
    if forced in _MAKERS:
        return forced
    if forced == "none":
        return None
    if environment.session() == "windows":
        return "poll"
    if environment.current() == environment.KDE or kwin_running():
        return "kwin"  # KWin may come up after this program does: it is watched for
    if environment.session() == "x11":
        return "poll"
    if LAYER_SHELL in environment.wayland_globals():
        return "strips"
    return None


def create(parent=None, click=None, scroll=None):
    """The watcher for this desktop, or None where the pointer cannot be watched."""
    name = _choice()
    if name is None:
        log.info("flow: this desktop gives no way of watching the screen edges")
        return None
    try:
        return _MAKERS[name](parent, click=click, scroll=scroll)
    except Exception as error:
        log.warning("flow: the screen edges cannot be watched (%s): %s", name, error)
        return None
