"""Background engine: finds the mouse, pushes the settings to it and turns its
diverted buttons, wheels and gestures into actions.

Everything here runs on one worker thread. The UI talks to it through the thread-safe
methods of :class:`Engine` and hears back through a listener object."""

import collections
import copy
import logging
import math
import queue
import struct
import threading
import time

from . import config, device, gestures, hidpp, rumble, vinput
from .actions import Executor, keys
from .hidpp import HidppClosed, HidppError, HidppFeatureError

log = logging.getLogger(__name__)

SECTIONS = ("buttons", "pointer", "wheel", "thumbwheel", "haptics")
SCAN_INTERVAL = 3.0
OFFLINE_PING_INTERVAL = 5.0
BATTERY_INTERVAL = 300.0
# Gestures (the thresholds themselves are settings, see config.DEFAULTS["gestures"]). The direction
# is taken from the whole movement: the first millimetres of a swipe point anywhere (the thumb
# pressing the button nudges the mouse, the wrist moves in an arc), so deciding early picks wrong.
GESTURE_TRACE_INTERVAL = 0.03  # s between two live updates for the settings page
GESTURE_TRACE_POINTS = 600
GESTURE_CUE_GAP = 0.07         # s between two "direction changed" ticks at most
# SmartShift lets the wheel go when it is spun faster than its threshold and catches it again when it
# stops, but the mouse only announces a mode change made with the button. So the engine watches the
# wheel's speed itself to know when that happens.
SPIN_WINDOW = 0.15   # s of wheel movement the speed is measured over
SPIN_GAP = 0.10      # s without movement after which a new measurement starts
SPIN_STOP = 0.13     # s without movement after which a freely spinning wheel counts as stopped
SPIN_TRAVEL = 3.0    # notches that must be covered at speed before the wheel counts as let go
SPIN_SHORT = 0.30    # s: a wheel that stops this soon after letting go gets no second click for being caught
REPORT_INTERVAL = 0.006  # s: two reports cannot really be closer than this, whatever their arrival times say
# Flow (flow.py): the mouse moves on to another computer when the pointer is pushed against a screen edge.
FLOW_MOVED = 0.6     # s: the pointer reaching the edge counts only if this mouse moved that recently
PUSH_PAUSE = 0.30    # s without pushing after which the push against the edge starts over
PUSH_DWELL = 0.25    # where the mouse's movement cannot be followed, the pointer has to rest against the edge
PUSH_DWELL_PER_MM = 0.03  # ... this long, plus this much for every millimetre of push asked for
EDGE_AXES = {"left": (-1, 0), "right": (1, 0), "top": (0, -1), "bottom": (0, 1)}  # in the mouse's counts, down is positive
# Rumble from games (rumble.py): below the first strength it is "light", below the second "medium", else "strong".
RUMBLE_STEPS = ((0.34, "light"), (0.67, "medium"), (float("inf"), "strong"))
RUMBLE_TEST = ((0.0, 0.2), (0.45, 0.5), (0.9, 1.0), (1.5, 0.0))  # the test: (s from its start, strength)
EXPECT_FOR = 12.0    # s the mouse is looked for more often after the other computer sent it here
EXPECT_INTERVAL = 0.1
HID_BUTTONS = ("left", "right", "middle", "back", "forward")  # bit order of the receiver's mouse report

# thumb wheel function -> (wheel clicks per step, action forward, action backward)
THUMB_STEPS = {
    "volume": (1, keys("VOLUMEUP"), keys("VOLUMEDOWN")),
    "tabs": (3, keys("LEFTCTRL", "TAB"), keys("LEFTCTRL", "LEFTSHIFT", "TAB")),
    "desktops": (5, {"type": "shortcut", "component": "kwin", "name": "Switch One Desktop to the Right"},
                 {"type": "shortcut", "component": "kwin", "name": "Switch One Desktop to the Left"}),
    "brightness": (1, keys("BRIGHTNESSUP"), keys("BRIGHTNESSDOWN")),
    "zoom": (1, None, None),
}


class Listener:
    """Callbacks from the engine thread. Subclass and override."""

    def on_state(self, state):
        pass

    def on_config(self, cfg):
        """The engine changed the configuration itself (first-run seeding)."""

    def on_ring(self, event):
        """The button that opens the Actions Ring: event is "down", "up" or "cancel"."""

    def on_pressed(self, keys):
        """Buttons held down right now, as a frozenset of button keys (plus "left"/"right")."""

    def on_ring_turn(self, clicks):
        """The thumb wheel turned while the Actions Ring is open (only when the app handles that wheel)."""

    def on_event(self, kind, payload):
        """Something other programs may want to hear about (it is passed on over D-Bus):
        "button" (key, pressed), "gesture" direction, "wheel_mode" (mode, quiet), "hook" (name, phase).
        And for Flow: "flow" ("asked", None) when an action asks to move to the other computer,
        ("left", channel) once the mouse was sent there, ("refused", why) when it was not."""

    def on_gesture(self, event):
        """A gesture in progress or just finished, while the settings window is watching. ``event`` has
        "points" (the movement so far, in mm), "fired" (directions recognised so far) and, once the
        button is up, "result": a direction, "tap", "cancelled", "diagonal" or "repeat"."""


class Engine:
    def __init__(self, cfg, listener=None, seed=False):
        self.cfg = copy.deepcopy(cfg)
        self.listener = listener or Listener()
        self._seed = seed
        self._queue = queue.Queue()
        self._cfg_lock = threading.Lock()
        self._pending_cfg = None
        self._thread = threading.Thread(target=self._run, name="engine", daemon=True)
        self._running = False
        self._transports = {}  # path -> Transport
        self.mouse = None
        self.online = False
        self.vinput = None
        self.executor = None
        self._dirty = False
        self._next_scan = 0.0
        self._next_ping = 0.0
        self._next_battery = 0.0
        self._battery = None
        self._hosts = (0, [])
        self._pressed = set()
        self._held_actions = {}  # cid -> action being held
        self._gesture = None
        self._gesture_dry = False  # the gestures page is practising: recognise, but do not act
        self._raw_fresh = set()    # buttons whose raw XY reporting was (re)armed and not used since
        self._ring_cid = None
        self._ring_open = False
        self._dpi_alt = False
        self._dpi_shift = False
        self._dpi_override = None  # set by another program until it gives the speed back
        self._wheel_mode = ""      # "ratchet" or "freespin", as the mouse last reported it
        self._wheel_quiet = 0.0    # until then a mode change is our own doing (settings being applied): no click
        # the wheel as SmartShift sees it: recent movement [(when, notches)], its direction, and whether it has let go
        self._spin = {"events": collections.deque(), "sign": 0, "last": 0.0, "free": False, "since": 0.0}
        self._last_tick = 0.0
        self._wheel_fraction = 0.0
        self._thumb = {"acc": 0.0, "last": 0.0, "fraction": 0.0}
        self._monitor = False      # the settings window is open and wants to see button presses
        self._watcher = None       # follows the mouse's ordinary reports (hidpp.MouseReports)
        self._hid_buttons = 0
        self._reported = frozenset()
        self._expect_until = 0.0   # the other computer is sending the mouse over: look for it more often
        self._known_direct = None  # the mouse as last loaded over Bluetooth or a cable (see _scan)
        self._push = None          # Flow: the pointer rests against the edge that leads to the other computer
        self._rumble_pad = None    # the virtual gamepad games vibrate, while rumble is switched on
        self._rumble_mark = None
        self._rumble = {"level": 0.0, "next": 0.0}  # the strength a game asks for now, and when the next pulse is due

    # -- thread-safe API --------------------------------------------------

    def start(self):
        self._running = True
        self._thread.start()

    def stop(self):
        if self._running:
            self._running = False
            self._queue.put(("stop",))
            self._thread.join(timeout=4.0)

    def set_config(self, cfg):
        with self._cfg_lock:
            self._pending_cfg = copy.deepcopy(cfg)
        self._queue.put(("config",))

    def play_haptic(self, waveform):
        self._queue.put(("haptic", waveform, False))

    def feedback(self, waveform):
        """Haptic cue that respects the "feedback" setting."""
        self._queue.put(("haptic", waveform, True))

    def trigger_action(self, action):
        self._queue.put(("trigger", copy.deepcopy(action)))

    def switch_host(self, host):
        self._queue.put(("host", host))

    def refresh(self):
        self._queue.put(("refresh",))

    def set_monitor(self, enabled):
        """Report live button presses (for the preview) while the settings window is visible."""
        self._queue.put(("monitor", bool(enabled)))

    def set_dpi_override(self, dpi):
        """Pointer speed asked for by another program; 0 gives it back to the settings."""
        self._queue.put(("dpi", int(dpi)))

    def set_wheel_mode(self, mode):
        """"ratchet", "freespin", "toggle", or "auto" for what the settings say."""
        self._queue.put(("wheel_mode", mode))

    def set_ring_open(self, is_open):
        """While the Actions Ring is on screen the thumb wheel turns its dials."""
        self._queue.put(("ring_open", bool(is_open)))

    def set_gesture_practice(self, enabled):
        """Recognise gestures without running their actions (the gestures page is being tried out)."""
        self._queue.put(("practice", bool(enabled)))

    def flow_leave(self, channel, by_edge):
        """Flow: send the mouse to the computer on Easy-Switch ``channel``, unless a button is held or,
        when the pointer reaching a screen edge is the reason (``by_edge``), something else moved it there.
        The outcome comes back as a "flow" event."""
        self._queue.put(("flow", int(channel), bool(by_edge)))

    def rumble_test(self):
        """Play on the mouse what a game's rumble growing from weak to strong feels like."""
        def run():
            start = time.monotonic()
            for after, level in RUMBLE_TEST:
                time.sleep(max(0.0, start + after - time.monotonic()))
                self._queue.put(("rumble", "test", level))
        threading.Thread(target=run, name="rumble-test", daemon=True).start()

    def rumble_nudge(self):
        """Move a stick of the virtual gamepad for a moment, so that a browser or a game notices it."""
        self._queue.put(("rumble_nudge",))

    def flow_arm(self, channel, edge, push_mm):
        """Flow: the pointer has come to rest against ``edge``, behind which the computer on Easy-Switch
        ``channel`` is. Send the mouse there once it has been pushed ``push_mm`` millimetres further."""
        self._queue.put(("flow_arm", (int(channel), edge, float(push_mm))))

    def flow_disarm(self):
        """Flow: the pointer has left the edge again."""
        self._queue.put(("flow_arm", None))

    def expect_mouse(self):
        """Flow: the other computer is sending the mouse here, so look for it more often for a while."""
        self._queue.put(("expect",))

    # -- worker loop ------------------------------------------------------

    def _run(self):
        self.vinput = vinput.create()
        self.executor = Executor(self.vinput, self._device_action, lambda name, phase: self._emit("hook", (name, phase)))
        self._publish()
        while self._running:
            try:
                item = self._queue.get(timeout=self._idle_timeout())
            except queue.Empty:
                item = None
            try:
                if item is not None:
                    if item[0] == "stop":
                        break
                    self._handle(item)
                self._tick()
            except HidppClosed:
                pass  # the reader thread reports it through ("closed", transport)
            except Exception:
                log.exception("engine error")
        self._shutdown()

    def _idle_timeout(self):
        now = time.monotonic()
        if self.mouse is None:
            return max(0.05, self._next_scan - now)
        if not self.online or self._dirty:
            return max(0.05, self._next_ping - now)
        if self._spin["free"]:  # wake up in time to notice the wheel stopping
            return max(0.005, self._spin["last"] + SPIN_STOP - now)
        if self._rumble["level"] > 0:  # in time for the next pulse
            return max(0.005, self._rumble["next"] - now)
        if self._push is not None and self._push["deadline"] is not None:
            return max(0.005, self._push["deadline"] - now)
        return max(0.05, min(30.0, self._next_battery - now))

    def _handle(self, item):
        kind = item[0]
        if kind == "note":
            self._on_notification(*item[1:])
        elif kind == "config":
            with self._cfg_lock:
                cfg, self._pending_cfg = self._pending_cfg, None
            if cfg is not None:  # None: a burst of updates, already applied with the first marker
                self._apply_config(cfg)
        elif kind == "haptic":
            if not item[2] or self._feedback_enabled():
                self._play(item[1])
        elif kind == "trigger":
            self.executor.trigger(item[1])
        elif kind == "host":
            self._switch_host(item[1])
        elif kind == "closed":
            self._drop_transport(item[1])
        elif kind == "refresh":
            self._read_status()
            self._publish()
        elif kind == "monitor":
            self._monitor = item[1]
            self._sync_watcher()
        elif kind == "ring_open":
            self._ring_open = item[1]
            self._thumb.update(acc=0.0, fraction=0.0)
        elif kind == "dpi":
            self._set_dpi_override(item[1])
        elif kind == "wheel_mode":
            self._device_action({"type": "wheel_mode", "mode": item[1]}, "trigger")
        elif kind == "practice":
            self._gesture_dry = item[1]
        elif kind == "flow":
            self._flow_leave(item[1], item[2])
        elif kind == "rumble_nudge":
            if self._rumble_pad is not None:
                self._rumble_pad.nudge()
        elif kind == "rumble":
            if item[1] == "test" or (item[1] is not None and item[1] is self._rumble_mark):
                self._rumble_level(item[2])
        elif kind == "flow_arm":
            self._flow_arm(item[1])
        elif kind == "hid_motion":
            if item[1] is self._watcher:
                self._on_push(item[2], item[3], item[4])
        elif kind == "expect":
            self._expect_until = time.monotonic() + EXPECT_FOR
            self._next_scan = self._next_ping = 0.0
        elif kind == "hid_wheel":
            if item[1] is self._watcher and not self._wheel_diverted():
                self._on_spin(item[2], item[3])
        elif kind == "hid_buttons":
            if item[1] is self._watcher:
                if self._monitor and (item[2] ^ self._hid_buttons) & 0x03:  # for the log, while the window is open
                    log.info("main buttons held: %s", ", ".join(
                        name for bit, name in enumerate(HID_BUTTONS[:2]) if item[2] & (1 << bit)) or "none")
                self._hid_buttons = item[2]
                self._publish_pressed()

    def _tick(self):
        now = time.monotonic()
        expected = now < self._expect_until
        if self.mouse is None:
            if now >= self._next_scan:
                self._next_scan = now + (EXPECT_INTERVAL if expected else SCAN_INTERVAL)
                self._scan()
            return
        if not self.online or self._dirty:
            if now >= self._next_ping:
                self._next_ping = now + (EXPECT_INTERVAL if expected else OFFLINE_PING_INTERVAL)
                if self.mouse.transport.ping(self.mouse.devidx, timeout=1.2, retries=1):
                    self._configure()
            return
        if self._spin["free"] and now - self._spin["last"] >= SPIN_STOP:
            self._spin["free"] = False
            self._spin["events"].clear()
            # One quick stroke should be one click, not two: stay silent if it was over at once
            # (unless the catch is the only moment the user wants to hear).
            brief = (self._spin["last"] - self._spin["since"] < SPIN_SHORT
                     and self.cfg["wheel_click"]["moments"] == "both")
            self._wheel_changed("ratchet", "it stopped at once" if brief else "it stopped", quiet=brief)
        if self._rumble["level"] > 0 and now >= self._rumble["next"]:
            self._rumble_pulse(now)
        push = self._push
        if push is not None and push["deadline"] is not None and now >= push["deadline"]:
            self._flow_arm(None)
            self._flow_leave(push["channel"], True)
        if now >= self._next_battery:
            self._next_battery = now + BATTERY_INTERVAL
            self._read_status(hosts=False)
            self._publish()

    # -- discovery --------------------------------------------------------

    def _post_note(self, transport):
        return lambda devidx, sub_id, address, data: self._queue.put(("note", transport, devidx, sub_id, address, data))

    def _scan(self):
        nodes = dict(hidpp.find_hidpp_nodes())
        for path in [p for p, t in self._transports.items() if p not in nodes or t.closed]:
            self._transports.pop(path).close()
        for path, is_receiver in nodes.items():
            transport = self._transports.get(path)
            if transport is None:
                try:
                    transport = hidpp.Transport(path, is_receiver, on_closed=lambda t: self._queue.put(("closed", t)))
                except OSError as e:
                    log.debug("cannot open %s: %s", path, e)
                    continue
                transport.on_notification = self._post_note(transport)
                self._transports[path] = transport
            try:
                # A mouse on Bluetooth comes back as a new device every time it returns from another computer:
                # what it told about itself last time is still true.
                mouse = device.find_mouse(transport, None if is_receiver else self._known_direct,
                                          hurry=time.monotonic() < self._expect_until)
            except HidppClosed:
                continue
            if mouse:
                log.info("found %s on %s slot %s", mouse.name, path, mouse.devidx)
                if not is_receiver:
                    self._known_direct = mouse
                self.mouse = mouse
                self._configure()
                return

    def _drop_transport(self, transport):
        self._transports.pop(transport.path, None)
        transport.close()
        if self.mouse and self.mouse.transport is transport:
            self.mouse = None
            self._set_online(False)
            self._next_scan = time.monotonic() + 1.0

    def _set_online(self, online):
        if online != self.online:
            self.online = online
            if not online:
                self._reset_input_state()
                self._next_ping = time.monotonic() + OFFLINE_PING_INTERVAL
            self._sync_watcher()
            self._sync_rumble()
            self._publish()

    def _sync_rumble(self):
        """Have the virtual gamepad exactly while rumble is switched on and there is a mouse to play it on."""
        wanted = bool(self.cfg["rumble"]["enabled"] and self.mouse is not None and self.online and self.mouse.haptic_waveforms)
        if wanted and self._rumble_pad is None:
            mark = self._rumble_mark = object()  # what this pad's reports carry, so that a closed pad's are ignored
            self._rumble_pad = rumble.create(lambda level: self._queue.put(("rumble", mark, level)))
            if self._rumble_pad is not None:
                log.info("rumble: the virtual gamepad is there for games to vibrate")
        elif not wanted and self._rumble_pad is not None:
            pad, self._rumble_pad, self._rumble_mark = self._rumble_pad, None, None
            pad.close()
            self._rumble["level"] = 0.0

    def _rumble_level(self, level):
        """A game asks for this much rumble from now on (0: none)."""
        before = self._rumble["level"]
        self._rumble["level"] = min(1.0, max(0.0, level) * self.cfg["rumble"]["gain"])
        if bool(before) != bool(self._rumble["level"]):  # worth a line: it tells whether a game sends any rumble at all
            log.info("rumble: %s", "a game started vibrating" if self._rumble["level"] else "stopped")
        now = time.monotonic()
        if self._rumble["level"] > 0 and now >= self._rumble["next"]:
            self._rumble_pulse(now)

    def _rumble_pulse(self, now):
        """One beat of a rumble that lasts: the motor plays patterns, not a hum, so a rumble is a row of them."""
        level = self._rumble["level"]
        name = next(name for limit, name in RUMBLE_STEPS if level < limit)
        waveform = self.cfg["rumble"][name]
        if waveform is not None:
            self._play(waveform, wait=False)  # the next pulse and the game's next word are not kept waiting
        self._rumble["next"] = now + self.cfg["rumble"]["interval"] / 1000.0

    def _spin_wanted(self):
        """Whether anyone cares when SmartShift lets the wheel go: the click, or other programs."""
        wheel = self.cfg["wheel"]
        return bool(wheel["smartshift"] and self.mouse and self.mouse.smartshift and self.mouse.wheel
                    and (self.cfg["wheel_click"]["mode"] != "off" or self.cfg["app"]["api"]))

    def _sync_watcher(self):
        """Follow the mouse's ordinary reports only while that is needed (the window shows presses, the
        wheel's speed is being followed, or Flow has to know that the mouse is moving with no button
        held) and the mouse is there."""
        wanted = ((self._monitor or self._spin_wanted() or self.cfg["flow"]["enabled"]) and self.mouse is not None
                  and self.online)
        if wanted and self._watcher is None:
            transport = self.mouse.transport
            heard = (lambda watcher, bits: self._queue.put(("hid_buttons", watcher, bits)),
                     lambda watcher, notches, when: self._queue.put(("hid_wheel", watcher, notches, when)))
            if transport.is_receiver:  # the receiver sends them on a node of their own
                path = hidpp.find_mouse_report_node(transport.path)
                if path:
                    try:
                        self._watcher = hidpp.ButtonWatcher(path, *heard)
                    except OSError as e:
                        log.debug("cannot watch %s: %s", path, e)
            else:  # Bluetooth or a cable: they arrive where the HID++ traffic does
                self._watcher = hidpp.DirectWatcher(transport, *heard)
        elif not wanted and self._watcher is not None:
            watcher, self._watcher = self._watcher, None
            watcher.close()
            self._hid_buttons = 0
            self._publish_pressed()

    def _publish_pressed(self):
        keys = {config.BUTTON_BY_CID[cid] for cid in self._pressed if cid in config.BUTTON_BY_CID}
        if self._monitor:  # the main buttons are only of interest to the picture in the window
            keys.update(name for bit, name in enumerate(HID_BUTTONS) if self._hid_buttons & (1 << bit))
        keys = frozenset(keys)
        if keys != self._reported:
            self._reported = keys
            self.listener.on_pressed(keys)

    def _reset_input_state(self):
        if self.vinput:
            self.vinput.release_all()
        self._pressed.clear()
        self._held_actions.clear()
        if self._gesture is not None:
            gesture, self._gesture = self._gesture, None
            self._show_gesture(gesture, "cancelled")
        self._spin["free"] = False
        self._spin["events"].clear()
        self._dpi_shift = False
        self._flow_arm(None)
        self._rumble["level"] = 0.0
        if self._ring_cid is not None:
            self._ring_cid = None
            self.listener.on_ring("cancel")
        self._publish_pressed()

    # -- applying settings -------------------------------------------------

    def _configure(self):
        """Push the whole configuration (device just appeared, woke up or was power-cycled)."""
        mouse = self.mouse
        if self._seed:
            self._seed = False
            config.seed_from_device(self.cfg, mouse)
            self.listener.on_config(copy.deepcopy(self.cfg))
        self._reset_input_state()
        self._wheel_quiet = time.monotonic() + 1.0  # applying the settings may move the ratchet: not worth a click
        if self._apply(SECTIONS):
            self._read_status()
            self._next_battery = time.monotonic() + BATTERY_INTERVAL
        self._publish()

    def _apply_config(self, cfg):
        changed = [s for s in SECTIONS if cfg.get(s) != self.cfg.get(s)]
        if "pointer" in changed:
            self._dpi_override = None  # the user moved the slider: that wins
        self.cfg = cfg
        self._sync_watcher()  # the click or the API may have been switched on or off
        self._sync_rumble()
        if self.mouse and self.online and changed:
            self._apply(changed)

    def _apply(self, sections):
        """Returns True when the device took the settings."""
        appliers = {
            "buttons": self._apply_buttons, "pointer": self._apply_pointer, "wheel": self._apply_wheel,
            "thumbwheel": self._apply_thumbwheel, "haptics": self._apply_haptics,
        }
        try:
            for section in sections:
                try:
                    appliers[section]()
                except HidppFeatureError as e:
                    log.warning("%s settings rejected: %s", section, e)
        except HidppClosed:
            raise
        except HidppError as e:  # asleep or out of range: retry once it answers again
            log.info("device not reachable (%s), will retry", e)
            self._dirty = True
            self._set_online(False)
            return False
        self._dirty = False
        self._set_online(True)
        return True

    def _apply_buttons(self):
        mouse = self.mouse
        for key, cid in config.BUTTONS.items():
            if cid not in mouse.controls:
                continue
            kind = self.cfg["buttons"][key].get("type")
            needs_input = kind in ("keys", "button") and self.vinput is None
            divert = kind != "default" and not needs_input
            mouse.set_cid_reporting(cid, divert, raw_xy=kind == "gestures")  # raw XY freezes the cursor: gestures only
            if divert and kind == "gestures":
                self._raw_fresh.add(cid)
            else:
                self._raw_fresh.discard(cid)

    def _current_dpi(self):
        pointer = self.cfg["pointer"]
        if self._dpi_shift:
            return pointer["precision_dpi"]
        if self._dpi_override:
            return self._dpi_override
        return pointer["alt_dpi"] if self._dpi_alt else pointer["dpi"]

    def _set_dpi_override(self, dpi):
        mouse = self.mouse
        if dpi > 0 and mouse and mouse.dpi_range:
            low, high, step = mouse.dpi_range
            dpi = max(low, min(high, int(round(dpi / step)) * step))
        self._dpi_override = dpi if dpi > 0 else None
        if mouse and self.online and mouse.dpi_range:
            try:
                self._apply_pointer()
            except HidppClosed:
                raise
            except HidppError as e:
                log.warning("pointer speed not applied: %s", e)
        self._publish()

    def _apply_pointer(self):
        if self.mouse.dpi_range:
            self.mouse.set_dpi(self._current_dpi())

    def _wheel_diverted(self):
        return bool(self.cfg["wheel"]["smooth"] and self.vinput and self.mouse.wheel)

    def _apply_wheel(self, mode=None):
        mouse, wheel = self.mouse, self.cfg["wheel"]
        if mouse.smartshift:
            torque = wheel["torque"] if mouse.smartshift["torque"] else 0
            if mode is None:
                mode = device.WHEEL_FREESPIN if (not wheel["smartshift"] and wheel["mode"] == "freespin") else device.WHEEL_RATCHET
            if mode == device.WHEEL_FREESPIN:
                mouse.set_smartshift(device.WHEEL_FREESPIN, 0, torque)
            else:
                threshold = wheel["threshold"] if wheel["smartshift"] else device.ALWAYS_RATCHET
                mouse.set_smartshift(device.WHEEL_RATCHET, threshold, torque)
        if mouse.wheel:
            diverted = self._wheel_diverted()
            # When diverted the direction is flipped here, so the device stays un-inverted.
            mouse.set_wheel_mode(diverted, diverted, wheel["natural"] and not diverted and mouse.wheel["has_invert"])
            self._wheel_fraction = 0.0

    def _thumb_diverted(self):
        thumb = self.cfg["thumbwheel"]
        if thumb["function"] == "hscroll":
            return bool(self.vinput) and abs(thumb["speed"] - 1.0) > 0.01
        return thumb["function"] == "desktops" or bool(self.vinput)

    def _apply_thumbwheel(self):
        if not self.mouse.thumbwheel:
            return
        diverted = self._thumb_diverted()
        self.mouse.set_thumbwheel(diverted, self.cfg["thumbwheel"]["invert"] and not diverted)
        self._thumb.update(acc=0.0, fraction=0.0)

    def _apply_haptics(self):
        mouse, haptics = self.mouse, self.cfg["haptics"]
        if mouse.haptic_waveforms:
            mouse.set_haptic(haptics["enabled"], haptics["level"])
        if mouse.force and mouse.force["changeable"] and haptics.get("force"):
            mouse.set_force(haptics["force"])

    def _read_status(self, hosts=True):
        mouse = self.mouse
        if not mouse or not self.online:
            return
        try:
            self._battery = mouse.get_battery()
            if hosts and mouse.host_count:
                self._hosts = mouse.get_hosts()
            if mouse.wheel and mouse.wheel["has_ratchet"]:
                self._wheel_mode = "ratchet" if mouse.get_ratchet() else "freespin"
        except HidppClosed:
            raise
        except HidppError as e:
            log.debug("status read failed: %s", e)

    def _publish(self):
        mouse = self.mouse
        state = {
            "connected": bool(mouse and self.online),
            "known": mouse is not None,
            "name": mouse.name if mouse else "",
            "uinput": self.vinput is not None,
            "battery": self._battery if mouse else None,
            "current_host": self._hosts[0],
            "hosts": copy.deepcopy(self._hosts[1]) if mouse else [],
            "dpi": self._current_dpi(),
            "wheel_mode": self._wheel_mode if mouse and self.online else "",
            "caps": None,
        }
        if mouse:
            state["caps"] = {
                "buttons": [key for key, cid in config.BUTTONS.items() if cid in mouse.controls],
                "dpi": mouse.dpi_range,
                "dpi_default": mouse.dpi_default,
                "smartshift": mouse.smartshift is not None,
                "torque": bool(mouse.smartshift and mouse.smartshift["torque"]),
                "wheel": mouse.wheel is not None,
                "ratchet_events": bool(mouse.wheel and mouse.wheel["has_ratchet"] and mouse.smartshift),
                "thumbwheel": mouse.thumbwheel is not None,
                "waveforms": list(mouse.haptic_waveforms),
                "force": dict(mouse.force) if mouse.force and mouse.force["changeable"] else None,
            }
        self.listener.on_state(state)

    def _shutdown(self):
        """Hand every control back to the firmware so the mouse behaves normally without us."""
        mouse = self.mouse
        if mouse and self.online:
            try:
                for cid in config.BUTTONS.values():
                    mouse.set_cid_reporting(cid, False)
                if mouse.wheel:
                    mouse.set_wheel_mode(False, False, self.cfg["wheel"]["natural"] and mouse.wheel["has_invert"])
                if mouse.thumbwheel:
                    mouse.set_thumbwheel(False, self.cfg["thumbwheel"]["invert"])
                if mouse.dpi_range:
                    mouse.set_dpi(self.cfg["pointer"]["dpi"])
            except HidppError as e:
                log.debug("restore on exit failed: %s", e)
        if self._watcher is not None:
            self._watcher.close()
            self._watcher = None
        if self._rumble_pad is not None:
            self._rumble_pad.close()
            self._rumble_pad = None
        for transport in list(self._transports.values()):
            transport.close()
        self._transports.clear()
        if self.executor:
            self.executor.close()
        if self.vinput:
            self.vinput.close()

    # -- haptics / device actions -------------------------------------------

    def _feedback_enabled(self):
        haptics = self.cfg["haptics"]
        return haptics["enabled"] and haptics["feedback"]

    def _play(self, waveform, wait=True):
        if self.mouse and self.online and self.mouse.haptic_waveforms:
            try:
                self.mouse.play_haptic(waveform, wait)
            except HidppClosed:
                raise
            except HidppError as e:
                log.debug("haptic failed: %s", e)

    def _cue(self, waveform):
        if self._feedback_enabled():
            self._play(waveform)

    def _device_action(self, action, phase):
        mouse = self.mouse
        if not mouse or not self.online:
            return
        kind = action.get("type")
        try:
            if kind == "wheel_mode" and phase != "release" and mouse.smartshift:
                wanted = action.get("mode", "toggle")  # the button toggles; other programs may ask for one mode
                if wanted == "auto":
                    self._apply_wheel()
                else:
                    if wanted == "toggle":
                        wanted = "ratchet" if mouse.get_smartshift()[0] == device.WHEEL_FREESPIN else "freespin"
                    self._apply_wheel(mode=device.WHEEL_FREESPIN if wanted == "freespin" else device.WHEEL_RATCHET)
                if not self._wheel_click_felt():
                    self._cue(device.WAVE_STATE)  # otherwise the mode change itself is felt, see _on_ratchet
            elif kind == "dpi_cycle" and phase != "release":
                self._dpi_alt = not self._dpi_alt
                self._apply_pointer()
                self._cue(device.WAVE_STATE)
                self._publish()
            elif kind == "dpi_shift" and phase in ("press", "release"):
                self._dpi_shift = phase == "press"
                self._apply_pointer()
            elif kind == "host" and phase != "release":
                self._switch_host(action.get("host", 0))
            elif kind == "flow" and phase != "release":
                self._emit("flow", ("asked", None))  # the Flow service knows where the other computer is
        except HidppClosed:
            raise
        except HidppError as e:
            log.warning("%s failed: %s", kind, e)

    def _switch_host(self, host):
        mouse = self.mouse
        if not mouse or not self.online or not 0 <= host < mouse.host_count or host == self._hosts[0]:
            return
        try:
            mouse.change_host(host)
        except HidppError as e:
            log.warning("host switch failed: %s", e)

    def _moved_lately(self):
        """Whether the mouse itself was in use a moment ago; True as well where that cannot be known."""
        seen = self._watcher.last_input if self._watcher is not None else None
        return seen is None or time.monotonic() - seen < FLOW_MOVED

    def _flow_arm(self, armed):
        """Start (``armed`` = (channel, edge, push in mm)) or stop measuring the push against the edge."""
        if self._watcher is not None:
            self._watcher.on_motion = None
        self._push = None
        if armed is None or not self.mouse or not self.online:
            return
        channel, edge, push_mm = armed
        if push_mm <= 0 or edge not in EDGE_AXES:
            self._flow_leave(channel, True)  # no resistance asked for: touching the edge is enough
            return
        self._push = {"channel": channel, "axes": EDGE_AXES[edge], "need": push_mm * self._counts_per_mm(), "got": 0.0,
                      "last": 0.0, "deadline": None}
        if self._watcher is not None and self._watcher.tracks_motion:
            self._watcher.on_motion = lambda watcher, dx, dy, when: self._queue.put(("hid_motion", watcher, dx, dy, when))
        else:  # the hand cannot be followed here: resting against the edge for a while has to do
            self._push["deadline"] = time.monotonic() + PUSH_DWELL + push_mm * PUSH_DWELL_PER_MM

    def _on_push(self, dx, dy, when):
        """The mouse moved while the pointer rests against the edge: add up how far it was pushed on."""
        push = self._push
        if push is None:
            return
        if when - push["last"] > PUSH_PAUSE:
            push["got"] = 0.0  # it stopped in between: one continuous push is what counts
        push["last"] = when
        push["got"] = max(0.0, push["got"] + dx * push["axes"][0] + dy * push["axes"][1])
        if push["got"] >= push["need"]:
            self._flow_arm(None)
            self._flow_leave(push["channel"], True)

    def _flow_leave(self, channel, by_edge):
        mouse = self.mouse
        if not mouse or not self.online:
            why = "away"
        elif not 0 <= channel < mouse.host_count or channel == self._hosts[0]:
            why = "channel"
        elif self._pressed or self._hid_buttons:
            why = "held"   # in the middle of a drag: the button would stay down here for good
        elif by_edge and not self._moved_lately():
            why = "idle"   # a touchpad or another mouse pushed the pointer there
        else:
            why = None
            try:
                mouse.change_host(channel)
            except HidppClosed:
                raise
            except HidppError as e:
                log.warning("flow: host switch failed: %s", e)
                why = "failed"
        if why:
            log.info("flow: the mouse stays (%s)", why)
            self._emit("flow", ("refused", why))
        else:
            log.info("flow: sent the mouse to channel %d", channel + 1)
            self._emit("flow", ("left", channel))

    # -- notifications ----------------------------------------------------

    def _on_notification(self, transport, devidx, sub_id, address, data):
        mouse = self.mouse
        if sub_id == 0x41 and transport.is_receiver:  # device connection
            linked = not (data[0] & 0x40)
            if mouse is None:
                if linked:
                    self._next_scan = 0.0
            elif mouse.transport is transport and devidx == mouse.devidx:
                if linked:
                    self._configure()
                else:
                    self._set_online(False)
            return
        if mouse is None or mouse.transport is not transport or devidx != mouse.devidx:
            return
        feature = mouse.index_to_feature.get(sub_id)
        event = address >> 4
        if feature == device.REPROG_CONTROLS_V4:
            if event == 0:
                self._on_buttons({cid for cid in struct.unpack("!HHHH", data[:8]) if cid})
            elif event == 1:
                self._on_raw_xy(*struct.unpack("!hh", data[:4]))
        elif feature == device.HIRES_WHEEL and event == 0:
            self._on_wheel(data[0], struct.unpack("!h", data[1:3])[0])
        elif feature == device.HIRES_WHEEL and event == 1:
            self._on_ratchet(bool(data[0] & 0x01))
        elif feature == device.THUMB_WHEEL and event == 0:
            self._on_thumbwheel(struct.unpack("!h", data[:2])[0])
        elif feature == device.UNIFIED_BATTERY and event == 0:
            self._battery = (data[0], data[2])
            self._publish()
        elif feature == device.BATTERY_STATUS and event == 0:
            self._battery = device.battery_status(data)
            self._publish()
        elif feature == device.WIRELESS_DEVICE_STATUS and event == 0 and data[1] == 1:
            self._configure()  # power-cycled: volatile settings are gone

    def _on_buttons(self, pressed):
        for cid in pressed - self._pressed:
            self._log_button(cid, "down")
            self._button_down(cid)
            if cid in config.BUTTON_BY_CID:
                self._emit("button", (config.BUTTON_BY_CID[cid], True))
        for cid in self._pressed - pressed:
            self._log_button(cid, "up")
            self._button_up(cid)
            if cid in config.BUTTON_BY_CID:
                self._emit("button", (config.BUTTON_BY_CID[cid], False))
        self._pressed = pressed
        self._publish_pressed()

    def _log_button(self, cid, what):
        """One line per press of a button the app handles, with the main buttons held at that moment
        (known only while the settings window is open and watching them)."""
        if self._watcher is None:
            held = "main buttons not watched"
        else:
            held = "held: " + (", ".join(name for bit, name in enumerate(HID_BUTTONS) if self._hid_buttons & (1 << bit)) or "none")
        log.info("button %s %s (%s)", config.BUTTON_BY_CID.get(cid, hex(cid)), what, held)

    def _button_down(self, cid):
        key = config.BUTTON_BY_CID.get(cid)
        if key is None:
            return
        action = self.cfg["buttons"][key]
        kind = action.get("type")
        if kind == "gestures":
            # x, y: movement not yet used up by a recognised swipe; total: all of it, for the live view
            self._gesture = {"cid": cid, "action": action, "x": 0, "y": 0, "path": 0, "events": 0, "fired": [],
                             "axis": None, "done": False, "since": time.monotonic(), "skip": cid in self._raw_fresh,
                             "armed": False, "pointing": None, "cued": 0.0,
                             "shapes": bool(action.get("shapes")),  # the button has shapes to draw: judge on release
                             "total": [0, 0], "trace": [(0.0, 0.0)], "shown": 0.0}
            self._raw_fresh.discard(cid)
            self._show_gesture(self._gesture)
        elif kind == "ring":
            self._ring_cid = cid
            self.listener.on_ring("down")
        elif kind not in ("default", "none"):
            self._held_actions[cid] = action
            self.executor.press(action)

    def _button_up(self, cid):
        if self._gesture and self._gesture["cid"] == cid:
            gesture, self._gesture = self._gesture, None
            self._show_gesture(gesture, self._finish_gesture(gesture))
        elif self._ring_cid == cid:
            self._ring_cid = None
            self.listener.on_ring("up")
        else:
            action = self._held_actions.pop(cid, None)
            if action:
                self.executor.release(action)

    # -- gestures -----------------------------------------------------------

    def _counts_per_mm(self):
        """The raw movement of a held gesture button arrives in sensor counts."""
        return max(4.0, self._current_dpi() / 25.4)

    def _finish_gesture(self, gesture):
        """The button came up. Returns what the gesture turned out to be."""
        if gesture["fired"]:
            return gesture["fired"][-1] if len(gesture["fired"]) == 1 else "repeat"
        settings = self.cfg["gestures"]
        minimum = settings["min_mm"] * self._counts_per_mm()
        if gesture["shapes"]:
            drawn = gestures.strokes(gesture["trace"], settings["shape_mm"])
            if len(drawn) >= 2:  # it changed direction on the way: a shape, not a swipe
                name = gestures.shape(drawn)
                action = gesture["action"]["shapes"].get(name) if name else None
                if action is None:
                    log.info("gesture: shape %s (%s) has no action", name or "unknown", drawn)
                    self._gesture_cue("reject")
                    return "unknown"
                gesture["fired"].append(name)
                log.info("gesture: shape %s (%s, %d reports, %.2f s)", name, drawn, gesture["events"],
                         time.monotonic() - gesture["since"])
                self._gesture_cue("swipe")
                self._emit("gesture", name)
                if not self._gesture_dry:
                    self.executor.trigger(action)
                return name
        if math.hypot(gesture["x"], gesture["y"]) >= minimum:
            direction = gestures.direction(gesture["x"], gesture["y"], settings["dead"], settings["bias"])
            if direction is None:
                log.info("gesture: ignored, too diagonal (x=%d y=%d)", gesture["x"], gesture["y"])
                self._gesture_cue("reject")
                return "diagonal"
            self._fire_gesture(gesture, direction, "on release")
            return direction
        if gesture["path"] < 4 * minimum:
            self._fire_gesture(gesture, "tap", "on release")
            return "tap"
        # moved away and came back: a change of mind, not a tap
        log.info("gesture: cancelled (x=%d y=%d, path %d)", gesture["x"], gesture["y"], gesture["path"])
        self._gesture_cue("reject")
        return "cancelled"

    def _gesture_cue(self, name):
        """Vibration for one moment of a gesture (see gestures.CUES), if the user wants one there."""
        settings = self.cfg["gestures"]
        waveform = settings["cues"].get(name) if settings["haptic"] else None
        if waveform is not None:
            self._cue(waveform)

    def _fire_gesture(self, gesture, direction, when):
        gesture["fired"].append(direction)
        log.info("gesture: %s %s (x=%d y=%d, %d reports, %.2f s)", direction, when, gesture["x"], gesture["y"],
                 gesture["events"], time.monotonic() - gesture["since"])
        if direction == "tap":
            self._gesture_cue("tap")
        elif when == "repeated":
            self._gesture_cue("repeat")
        else:
            self._gesture_cue(direction if self.cfg["gestures"]["per_direction"] else "swipe")
        self._emit("gesture", direction)
        if not self._gesture_dry:
            self.executor.trigger(gesture["action"].get(direction))

    def _on_raw_xy(self, dx, dy):
        gesture = self._gesture
        if gesture is None or gesture["done"]:
            return
        now = time.monotonic()
        per_mm = self._counts_per_mm()
        if gesture["skip"]:
            # The first movement report after raw XY was armed for a button can carry movement from
            # before the press (seen right after reassigning the button; other software for this
            # mouse drops it as well). It is one report, a millimetre of travel at most otherwise.
            gesture["skip"] = False
            log.info("gesture: skipped the first report after arming (x=%d y=%d)", dx, dy)
            return
        if not gesture["events"] and math.hypot(dx, dy) > 5.0 * per_mm:
            # Kept: it may just as well be a quick swipe that the radio delivered in one piece.
            log.info("gesture: large first report (x=%d y=%d, %.0f ms after the press)", dx, dy, (now - gesture["since"]) * 1000)
        gesture["x"] += dx
        gesture["y"] += dy
        gesture["path"] += abs(dx) + abs(dy)
        gesture["events"] += 1
        gesture["total"][0] += dx
        gesture["total"][1] += dy
        if len(gesture["trace"]) < GESTURE_TRACE_POINTS:
            gesture["trace"].append((gesture["total"][0] / per_mm, gesture["total"][1] / per_mm))

        settings = self.cfg["gestures"]
        if gesture["axis"] is None and math.hypot(gesture["x"], gesture["y"]) >= settings["min_mm"] * per_mm:
            # Out of the press zone: let the hand feel that this is a swipe now, and where it points.
            pointing = gestures.direction(gesture["x"], gesture["y"], settings["dead"], settings["bias"])
            if not gesture["armed"]:
                gesture["armed"], gesture["pointing"] = True, pointing
                self._gesture_cue("armed")
            elif pointing and pointing != gesture["pointing"]:
                if gesture["pointing"] and now - gesture["cued"] >= GESTURE_CUE_GAP:
                    gesture["cued"] = now
                    self._gesture_cue("turn")
                gesture["pointing"] = pointing
        # A button that has shapes waits for the whole movement, or a shape would fire as a swipe half-way.
        if settings["trigger"] == "repeat" or (settings["trigger"] == "auto" and not gesture["shapes"]):
            if gesture["axis"] is None:
                # never on the strength of a single report: one alone says too little about the hand
                if gesture["events"] >= 2 and math.hypot(gesture["x"], gesture["y"]) >= settings["far_mm"] * per_mm:
                    direction = gestures.direction(gesture["x"], gesture["y"], max(settings["dead"], gestures.INSTANT_DEAD),
                                                   settings["bias"])
                    if direction:
                        self._fire_gesture(gesture, direction, "while held")
                        if settings["trigger"] == "repeat":
                            gesture["axis"] = 0 if direction in ("left", "right") else 1
                            gesture["x"] = gesture["y"] = 0
                        else:
                            gesture["done"] = True
            else:
                # Repeating: only travel along the axis of the first swipe counts from here on.
                axis, step = gesture["axis"], settings["step_mm"] * per_mm
                names = ("left", "right") if axis == 0 else ("up", "down")
                value = gesture["y" if axis else "x"]
                while abs(value) >= step:
                    self._fire_gesture(gesture, names[value > 0], "repeated")
                    value -= math.copysign(step, value)
                gesture["x"], gesture["y"] = (0, value) if axis else (value, 0)
        if now - gesture["shown"] >= GESTURE_TRACE_INTERVAL:
            self._show_gesture(gesture)

    def _show_gesture(self, gesture, result=None):
        """Feed the live view on the gestures page."""
        if not self._monitor:
            return
        gesture["shown"] = time.monotonic()
        self.listener.on_gesture({"points": list(gesture["trace"]), "fired": list(gesture["fired"]), "result": result,
                                  "held": result is None})

    def _emit(self, kind, payload):
        self.listener.on_event(kind, payload)

    # -- wheels -----------------------------------------------------------------

    def _wheel_click_felt(self):
        return self.cfg["wheel_click"]["mode"] in ("haptic", "both") and self.cfg["haptics"]["enabled"]

    def _on_ratchet(self, engaged):
        """The mouse says its wheel mode was switched (the mode button, a request, or a setting)."""
        mode = "ratchet" if engaged else "freespin"
        if mode == self._wheel_mode:
            return
        self._wheel_mode = mode
        was_free, self._spin["free"] = self._spin["free"], False
        self._spin["events"].clear()
        quiet = time.monotonic() < self._wheel_quiet
        if not (was_free and not engaged):  # already spinning freely: nothing changes for the hand
            self._wheel_changed(mode, "switched", quiet)
        self._publish()

    def _wheel_changed(self, mode, why, quiet=False):
        """The wheel let go or caught again: the click, and word to whoever listens."""
        log.info("wheel: %s (%s)", mode, "settings being applied" if quiet and why == "switched" else why)
        moments = self.cfg["wheel_click"]["moments"]
        quiet = quiet or moments == ("ratchet" if mode == "freespin" else "free")  # not a moment the user wants marked
        if not quiet and self._wheel_click_felt():
            waveform = self.cfg["wheel_click"]["ratchet" if mode == "ratchet" else "free"]
            if waveform is not None:
                self._play(waveform)
        self._emit("wheel_mode", (mode, quiet))

    def _on_spin(self, notches, when):
        """Wheel movement (in ratchet steps, fractions allowed): work out whether SmartShift has just
        let the wheel go. Its threshold is a speed, in quarter turns per second."""
        if not notches or not self._spin_wanted() or self._wheel_mode == "freespin":
            return  # nobody listens, or the wheel is free all the time anyway
        spin, events = self._spin, self._spin["events"]
        sign = 1 if notches > 0 else -1
        if events and (sign != spin["sign"] or when - spin["last"] > SPIN_GAP):
            events.clear()
        spin["sign"], spin["last"] = sign, when
        events.append((when, abs(notches)))
        while when - events[0][0] > SPIN_WINDOW:
            events.popleft()
        if spin["free"] or len(events) < 2:
            return
        travelled = sum(amount for _when, amount in events) - events[0][1]
        span = max(events[-1][0] - events[0][0], (len(events) - 1) * REPORT_INTERVAL)
        threshold = self.cfg["wheel"]["threshold"] / 4.0 * self.mouse.wheel["notches"]  # notches per second
        if travelled >= SPIN_TRAVEL and travelled / span >= threshold:
            spin["free"], spin["since"] = True, when
            self._wheel_changed("freespin", "spun at %.0f notches/s, threshold %.0f" % (travelled / span, threshold))

    def _on_wheel(self, flags, delta):
        if not self.vinput:
            return
        wheel = self.cfg["wheel"]
        self._on_spin(delta / self.mouse.wheel["multiplier"] if flags & 0x10 else delta, time.monotonic())
        per_unit = vinput.NOTCH / self.mouse.wheel["multiplier"] if flags & 0x10 else vinput.NOTCH
        value = delta * per_unit * wheel["speed"] * (-1 if wheel["natural"] else 1) + self._wheel_fraction
        whole = int(value)
        self._wheel_fraction = value - whole
        self.vinput.scroll(whole)

    def _on_thumbwheel(self, rotation):
        thumb, state = self.cfg["thumbwheel"], self._thumb
        info = self.mouse.thumbwheel
        # clicks > 0: rolled up, the direction that scrolls right
        clicks = rotation * info["direction"] * info["native_res"] / info["diverted_res"] * (-1 if thumb["invert"] else 1)
        if self._ring_open:
            self.listener.on_ring_turn(clicks)  # the ring decides whether a dial is under the pointer
            return
        if thumb["function"] == "hscroll":
            if self.vinput:
                value = clicks * vinput.NOTCH * thumb["speed"] + state["fraction"]
                whole = int(value)
                state["fraction"] = value - whole
                self.vinput.hscroll(whole)
            return
        now = time.monotonic()
        if now - state["last"] > 0.6 or (state["acc"] > 0) != (clicks > 0):
            state["acc"] = 0.0
        state["last"] = now
        state["acc"] += clicks * thumb["speed"]
        step, forward, backward = THUMB_STEPS[thumb["function"]]
        while abs(state["acc"]) >= step:
            positive = state["acc"] > 0
            state["acc"] -= step if positive else -step
            if thumb["function"] == "zoom":
                self._zoom(1 if positive else -1)
            else:
                self.executor.trigger(forward if positive else backward)
            if thumb["ticks"] and now - self._last_tick >= 0.04:  # the wheel itself has no detents: give it some
                self._last_tick = now
                self._cue(device.WAVE_TICK)

    def _zoom(self, direction):
        if not self.vinput:
            return
        self.vinput.press(["KEY_LEFTCTRL"])
        time.sleep(0.008)
        self.vinput.scroll(direction * vinput.NOTCH)
        time.sleep(0.008)
        self.vinput.release(["KEY_LEFTCTRL"])
