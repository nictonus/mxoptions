"""The keyboard: found next to the mouse (on the same receiver, or on a connection of its own), its
programmable keys turned into actions, its battery and Easy-Switch channels shown, and the settings it keeps
itself (kbsettings) read and, where asked, changed.

What is known of the keyboard is kept, and decided on, on the engine's thread, like the mouse. Every request
to the keyboard is made on a thread of its own, though, in the background: it gives way to every other
request (hidpp.ProbeTransport), so that a slow or sleeping keyboard never holds up the mouse. Looking for
the keyboard is done there in one go; the rest (its keys diverted or given back, Easy-Switch) is a task the
engine's thread runs one request at a time, going on with it when the answer comes back (KeyboardSide._step).

A key is only ever diverted with the temporary setting, never the persistent one the keyboard keeps, and
only keys this program diverted itself are given back. Which ones those are is written down before each
one is diverted, in a small file of this computer's (JOURNAL), so that a start after a crash knows them
again. What the keyboard says is what counts; the file only says what was meant. Where it cannot be told
(the keyboard does not say which unit it is, the file cannot be read, other software for the device is
running) no key is touched at all.

A setting the keyboard keeps is changed one at a time, after the keys, and only from what a fresh read of it
said a moment before: the change is read back, and where the keyboard took something else than asked, what it
had before is sent back once. Nothing of it is kept on this computer: the keyboard's own keys change the same
settings, and what it says is what is shown."""

import copy
import hashlib
import json
import logging
import os
import queue
import re
import struct
import threading
import time

from . import config, device, hidpp, kbsettings, paths
from .actions import Executor, here, host_target
from .hidpp import HidppCancelled, HidppClosed, HidppError, HidppTimeout

log = logging.getLogger(__name__)

# Not measured on a real keyboard yet: knobs to set from what a receiver shared with the mouse shows.
PROBE_TIMEOUT_SHARED = 0.25  # s for an answer, on a transport that carries the mouse too
PROBE_TIMEOUT_OWN = 1.0      # s for an answer, on one of the keyboard's own
PROBE_BUDGET = 20.0          # s one look may take in all (loading a keyboard is a couple of hundred requests)
ASLEEP_RETRY = 60.0          # s before slots that did not answer, or were not reached, are asked again
NODE_INTERVAL = 10.0         # s between two looks for new receivers and Bluetooth devices (no HID++ traffic)
RECOVERY_RETRY = 30.0        # s between two attempts at keys whose state could not be settled
REQUEST_BUDGET = 5.0         # s one request of a task may wait for its turn and its answer, in all
SHUTDOWN_BUDGET = 3.0        # s the keys may take to be given back on exit
SWITCH_GRACE = 5.0           # s after an Easy-Switch request before keys are taken again if the keyboard stayed
# The keyboard following the mouse (flow.keyboard_follows_mouse). Not measured with a real keyboard yet: knobs.
FOLLOW_SETTLE = 3.0  # s a mouse on Bluetooth may take to be gone before the keyboard is asked; too small gives "late"
FOLLOW_BUDGET = 5.0  # s from the mouse leaving to the keyboard's Easy-Switch request going out, at most
KEYS_FRESH = 0.3     # s from the first read of which keys are down to that request going out; too small gives "late"
LOW_BATTERY = 15             # percent: where the battery in the sidebar turns red
MAX_CONTROLS = 128
MAX_JOURNAL_KEYBOARDS = 16
JOURNAL = os.path.join(paths.DATA, "keyboard-diverted.json")
IDENTITY = re.compile(r"[0-9a-f]{16}\Z")


def flow_link(identity):
    """What Flow knows this keyboard by (see flow_service.py), or None for one that does not say which unit it is.
    Made from the identity, never the identity itself; it never leaves this program as it is."""
    return hashlib.sha256(b"mxoptions keyboard flow\0" + identity.encode("ascii")).hexdigest()[:16] if identity else None


class JournalUnavailable(Exception):
    """The journal is there but cannot be read, or is not in the one form this version knows."""


def read_journal(path=None):
    """The keys this program diverted, by keyboard: {identity: [key ids]}, {} if there is no journal.
    Raises JournalUnavailable for anything else: the file is then left exactly as it is."""
    path = path or JOURNAL
    try:
        with open(path, "rb") as f:
            data = json.loads(f.read())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise JournalUnavailable(str(e)) from None
    keyboards = data.get("keyboards") if isinstance(data, dict) else None
    if (not isinstance(keyboards, dict) or set(data) != {"version", "keyboards"} or type(data["version"]) is not int
            or data["version"] != 1 or len(keyboards) > MAX_JOURNAL_KEYBOARDS):
        raise JournalUnavailable("not a journal this version knows")
    for identity, ids in keyboards.items():
        if (not IDENTITY.match(identity) or not isinstance(ids, list) or not ids or len(ids) > config.MAX_KEYBOARD_KEYS
                or not all(isinstance(key, str) and config.KEY_ID.match(key) for key in ids) or ids != sorted(set(ids))):
            raise JournalUnavailable("not a journal this version knows")
    return {identity: list(ids) for identity, ids in keyboards.items()}


def write_journal(entries, path=None):
    """Replace the journal with ``entries`` ({identity: [key ids]}), readable by the user alone. Raises OSError."""
    path = path or JOURNAL
    data = {"version": 1, "keyboards": {identity: sorted(set(ids)) for identity, ids in sorted(entries.items()) if ids}}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def effective_keys(settings, app):
    """What the keys do while ``app`` is in front (None: no app known), ``settings`` being the "keyboard" part of the
    settings: its profile over the keys for all apps. A key left to the keyboard there is not in it."""
    keys = dict(settings["keys"])
    if app is not None:
        keys.update(next((profile["keys"] for profile in settings.get("profiles", ()) if profile["app"] == app), {}))
    return {name: action for name, action in keys.items() if action != config.NATIVE}


def low_battery(armed, battery):
    """Whether to say now that the battery is low: once when it runs down to LOW_BATTERY percent, and again only
    after it was charged in between. ``armed``: it may be said. Returns (armed, say it now)."""
    if not battery:
        return armed, False
    percent, status = battery
    if status != device.BATTERY_DISCHARGING or percent > LOW_BATTERY:
        return True, False
    return False, armed


def new_notice(seen, found):
    """What to say now of a change the keyboard reported: (``seen``, None), or (the notice's number, a copy of the
    notice, see KeyboardSide.state) once for each notice, and only while the keyboard is connected. ``seen``: the
    number last said (0 at first); ``found``: the keyboard's state (KeyboardSide.state), or None."""
    notice = found.get("notice") if found and found.get("connected") else None
    if notice is None or notice["seq"] <= seen:
        return seen, None
    return notice["seq"], dict(notice)


class Reported:
    """A state the keyboard reports changes of (its Fn lock, its lock keys), as the reads that count settle it: the
    state last settled (seen; None: none yet), the reports counted (event), how many of them are settled (handled),
    the count of reports a failed read was given its one read more for (retried; None: none since the last change
    sent), and whether a change this program sent is not read back yet (own). A read counts only if no report came
    while it was made, and only on the record it was started on: there is one for each connection and each turn of
    its notices on or off, and a read started on another settles nothing here."""

    __slots__ = ("seen", "event", "handled", "retried", "own")

    def __init__(self):
        self.seen, self.event, self.handled, self.retried, self.own = None, 0, 0, None, False

    def read(self, start, now):
        """A read that said ``now``, started once ``start`` reports were counted, settles it: True if that is a change
        to tell. A report while it was read comes first: then nothing settles, and the read that report made due says
        more. A change of this program's is settled by it without a word, as is the first state read."""
        if self.event != start:
            return False
        said = not self.own and self.seen is not None and start > self.handled and now != self.seen
        self.seen, self.handled, self.own = now, start, False
        return said

    def failed(self):
        """A read failed (neither the link gone nor unanswered): True, once for each report or change sent still to be
        settled, where it is to be read once more. Nothing else changes."""
        if (self.own or self.event > self.handled) and self.retried != self.event:
            self.retried = self.event
            return True
        return False

    def sent(self):
        """This program sends a change now: what is reported until a read settles it is taken as that change."""
        self.own, self.retried = True, None


def _set_reporting(dev, cid, divert):
    """Divert a key, or give it back: setCidReporting with the (temporary) divert bit and its "valid" bit alone,
    never the persistent ones. True if the keyboard echoed it back."""
    reply = dev.call(device.REPROG_CONTROLS_V4, 3, *struct.pack("!HBH", cid, 0x02 | (0x01 if divert else 0), 0))
    return struct.unpack("!H", reply[:2])[0] == cid and bool(reply[2] & 0x01) == divert


def _reporting_of(cids):
    """A task (see _run_here): what the keyboard says of these keys, {cid: "native" | "diverted" | "foreign"}.
    "foreign": diverted persistently, raw, or sent as another key, which is never this program's doing. A key
    it could not say anything about is left out (unknown); the first one it does not answer in time ends the
    asking."""
    seen = {}
    for cid in cids:
        try:
            flags, remap = yield (device.Mouse.get_cid_reporting, cid)
        except (HidppCancelled, HidppClosed):
            raise
        except HidppTimeout:
            break
        except (HidppError, IndexError, struct.error):
            continue
        if remap not in (0, cid) or flags & (device.REPORT_PERSISTENT | device.REPORT_RAW_XY):
            seen[cid] = "foreign"
        else:
            seen[cid] = "diverted" if flags & device.REPORT_DIVERTED else "native"
    return seen


# A task is a generator: each request it needs it yields as (function, *arguments), function(device, *arguments)
# being made on the device, and gets back what that returns, or has what it raises raised where it yielded.

def _run_here(task, call):
    """Run ``task`` to its end on this thread, each request made at once with ``call(request)``; its result."""
    reply = error = None
    while True:
        try:
            request = task.send(reply) if error is None else task.throw(error)
        except StopIteration as done:
            return done.value
        reply = error = None
        try:
            reply = call(request)
        except Exception as e:
            error = e


def _call(dev, request):
    """Make ``request`` (see _run_here) on ``dev``. One that gave way for too long counts as not answered."""
    try:
        return request[0](dev, *request[1:])
    except HidppCancelled as e:
        raise HidppTimeout(str(e)) from None


def _view(dev, transport, timeout, cancel, budget):
    """``dev`` making its requests in the background, see hidpp.ProbeTransport."""
    view = copy.copy(dev)  # its own transport; what it knows of itself is shared, and only read
    view.transport = hidpp.ProbeTransport(transport, timeout, cancel, time.monotonic() + budget)
    return view


def _stays(why):
    log.info("flow: the keyboard stays (%s)", why)


def _snap_switch(dev, host, deadline):
    """A request of the keyboard following the mouse (see KeyboardSide._follow_steps): see whether a key is down,
    and only if none is, send the keyboard to ``host``, all within KEYS_FRESH of the first look and before
    ``deadline`` (time.monotonic). Made on the keyboard's thread, ``dev`` making its requests in the background
    (_view): its cancel, once set, stops the asking and the request. "sent", or why not: "held", "unknown",
    "late", "cancelled". The request cannot be taken back once it has gone, whatever is called off after."""
    view = dev.transport
    first = time.monotonic()  # before the first read: no part of the answer is older
    fresh_by = min(deadline, first + KEYS_FRESH)
    held = view.keys_held(fresh_by)
    done = time.monotonic()
    if held is None:
        return "unknown"
    if held:
        return "held"
    if view.cancel.is_set():
        return "cancelled"
    if done > fresh_by:
        return "late"
    fresh = copy.copy(dev)
    fresh.transport = hidpp.ProbeTransport(view.transport, view.timeout, view.cancel, min(view.deadline, fresh_by))
    try:
        fresh.change_host(host)  # waits for its turn no later than fresh_by: then it is not sent
    except HidppCancelled:
        return "cancelled" if view.cancel.is_set() else "late"
    return "sent"


def _status(dev):
    """(battery, (current host, hosts)) of a keyboard, as far as it says. HidppCancelled and HidppClosed are passed on."""
    battery, hosts = None, (0, [])
    try:
        battery = dev.get_battery()
        if dev.host_count:
            hosts = dev.get_hosts()
    except (HidppCancelled, HidppClosed):
        raise
    except HidppError as e:
        log.debug("keyboard status read failed: %s", e)
    return battery, hosts


class KeyboardSide:
    """The keyboard part of the engine. Everything but _probe_loop is called on the engine's thread."""

    def __init__(self, engine):
        self.engine = engine
        # One keyboard at a time: a list of these once a second one has to work alongside.
        self.device = None      # a device.Mouse: that class speaks HID++ 2.0 to any device
        self.transport = None
        self.online = False
        self.executor = None
        self._identity = None
        self._journal = {}
        self._reporting = {}    # cid -> what the keyboard said of it last (see _reporting_of); missing: unknown
        self._active = set()    # keys diverted by this program now
        self._block = None      # why no key, and no setting, is touched: "rivals" | "identity" | "journal" | "full"
        self._said = None       # the block last written to the log
        self._stalled = False   # the keyboard stopped answering during this round of changes
        self._pressed = set()
        self._held = {}         # cid -> action being held
        self._actions = {}      # key name -> the action object its presses use (see _effective)
        self._front = None      # the app in front, whose profile counts (see effective_keys)
        self._battery = None
        self._hosts = (0, [])
        self._firmware = None   # its firmware version, once read (see _read_firmware)
        self._firmware_due = False  # it is still to be read, once the keys leave time for it
        self._controls = {}     # name -> {"value", "default", "writable"}: its settings as last read (see kbsettings)
        self._pending = {}      # name -> value asked for and not sent yet; the last one asked for counts
        self._outcomes = {}     # name -> how the last change went, when not as asked (see _change)
        self._settings_at = float("inf")  # time.monotonic from which its settings are to be read again
        self._committing = None  # the setting whose new value is on its way: that task is not called off
        self._notice_seq = 0    # notices of changes the keyboard reported, made so far (never counts down)
        self._level_reset()
        self._seen = None       # (name, keys, settings) of the keyboard last connected, while it is away
        self._slots = {}        # transport -> {devidx: what is there, see device.probe_slot; "pending" while looked at}
        self._generation = 0    # results of looks started before the last change of this number are dropped
        self._jobs = queue.Queue()  # for the keyboard's thread: looks, and the requests of tasks
        self._task = None       # the task under way (see _step), or None
        self._token = 0         # answers to requests of tasks before the last change of this number are dropped
        self._asked = None      # the request of the task on its way now
        self._called_off = threading.Event()  # set once the task under way is dropped: what it has not sent yet never goes
        self._wish = None       # what to do once the task under way is over: None | "configure" | ("host", n)
        #                         | ("follow", n, event, deadline): see follow()
        self._following = False  # the task under way is the keyboard following the mouse; _called_off is its event
        self._follow_wait = None  # (host, link, event, settle_by, deadline) of one waiting for the mouse to be gone
        self._go_along = None   # the event of the follow an action asked for (go_along): Flow going off leaves it be
        self._reading = threading.Event()  # the keyboard's thread is seeing whether a key is down (see reading_keys)
        self._failed = None     # "switch": the keyboard could not be moved to another computer last time
        self._leaving = 0.0     # time.monotonic until which the keyboard is on its way to another computer
        self._sent_at = None    # time.monotonic of the last Easy-Switch request this program made, while it may be going
        self.link = None        # flow_link() of the keyboard last found, kept after it is gone (engine thread only)
        self.last_drop = None   # (time.monotonic, sent away by this program) of its going, while it is away
        self._cancel = threading.Event()
        self._thread = None
        self._next_nodes = 0.0
        self._next_look = 0.0
        self._next_recovery = float("inf")

    # -- the engine's side ---------------------------------------------------

    def start(self):
        engine = self.engine
        self.executor = Executor(engine.vinput, self.device_action, lambda name, phase: engine._emit("hook", (name, phase)),
                                 owner="keyboard", haptic=engine.play_haptic)
        self._thread = threading.Thread(target=self._probe_loop, name="keyboard", daemon=True)
        self._thread.start()

    def owns(self, transport, devidx):
        return self.device is not None and transport is self.transport and devidx == self.device.devidx

    def skip(self, transport):
        """Slots of ``transport`` the mouse need not be looked for on: the keyboard is there, or being looked at."""
        states = self._slots.get(transport, {})
        return tuple(devidx for devidx in transport.slots() if states.get(devidx) == "pending" or self.owns(transport, devidx))

    def offer(self, dev):
        """A device the engine loaded while looking for the mouse: taken if it is a keyboard and none is held."""
        states = self._slots.setdefault(dev.transport, {})
        if dev.kind != device.KIND_KEYBOARD:
            states[dev.devidx] = "other"
            return False
        if self.device is not None or states.get(dev.devidx) == "pending":
            return False
        self._queue(dev.transport, [dev.devidx], {dev.devidx: dev})
        return True

    def after_scan(self, transport, mouse_devidx, loaded):
        """The mouse was found: take what was loaded on the way and look at every slot that was not."""
        self._slots.setdefault(transport, {})[mouse_devidx] = "mouse"
        for dev in loaded:
            self.offer(dev)
        self._next_look = time.monotonic() + ASLEEP_RETRY
        self._look()

    def found(self, generation, transport, slots, prepared, states):
        """A look at ``slots`` of ``transport`` is over. ``states``: what each slot it got to holds; ``prepared``:
        None, or the keyboard found (device, identity, {cid: reporting}, (battery, hosts)), already read off the
        engine's thread."""
        if generation != self._generation or transport.closed or transport is not self.engine._transports.get(transport.path):
            return
        mine = self._slots.setdefault(transport, {})
        for devidx in slots:
            if devidx in states:
                mine[devidx] = states[devidx]
            elif mine.get(devidx) == "pending":
                del mine[devidx]  # not reached: asked again later
        if prepared is None:
            return
        dev, identity, reporting, (battery, hosts) = prepared
        mouse = self.engine.mouse
        if self.device is not None or (mouse is not None and mouse.transport is transport and mouse.devidx == dev.devidx):
            mine.pop(dev.devidx, None)  # looked at again once this keyboard is gone
            return
        self.device, self.transport, self.online = dev, transport, True
        self._identity = identity
        self._reporting = dict(reporting)
        self._active.clear()
        self._pressed.clear()
        self._held.clear()
        self._battery, self._hosts, self._failed, self._leaving = battery, hosts, None, 0.0
        self._firmware, self._firmware_due = None, True
        self._controls, self._pending, self._outcomes, self._settings_at = {}, {}, {}, 0.0
        self._level_reset()
        self._sent_at, self.link, self.last_drop = None, flow_link(identity), None
        log.info("found %s on %s slot %s", dev.name, transport.path, dev.devidx)
        self._tell_flow()
        self._invalidate()  # one keyboard at a time: looks still to come no longer count, and go before its requests
        self.configure()

    def on_link(self, transport, devidx, linked):
        """The receiver says a device came or went on ``devidx``, which is not the keyboard's or the mouse's."""
        states = self._slots.get(transport)
        if states is None:
            return
        if states.pop(devidx, None) == "pending":
            self._invalidate()
        if linked and self.device is None and self.engine.mouse is not None:
            self._queue(transport, [devidx])
        self._look()

    def on_notification(self, sub_id, address, data):
        """A notification from the keyboard (see owns)."""
        dev = self.device
        if sub_id == 0x41:  # gone, or back without having been seen to go: loaded again either way
            self._again(not (data[0] & 0x40))
            return
        feature = dev.index_to_feature.get(sub_id)
        event = address >> 4
        if feature == device.REPROG_CONTROLS_V4 and event == 0:
            self._on_keys({cid for cid in struct.unpack("!HHHH", data[:8]) if cid})
        elif feature == device.UNIFIED_BATTERY and event == 0:
            self._battery = (data[0], data[2])
            self.engine._publish()
        elif feature == device.BATTERY_STATUS and event == 0:
            self._battery = device.battery_status(data)
            self.engine._publish()
        elif feature == device.WIRELESS_DEVICE_STATUS and event == 0 and data[1] == 1:
            self._again(True)  # power-cycled: loaded again, and what its keys report read afresh
        elif feature == device.LOCK_KEY_STATE:
            if self._watch_locks():  # a lock key turned on or off, it says: a fresh read says which (see _read_locks)
                self._locks.event += 1
                self._settings_at = 0.0
                if self._task is None:
                    self._next_task()
        elif any(feature in codec.features for codec in kbsettings.CODECS.values()):
            if feature == device.BACKLIGHT_2 and event == 0:
                self._level_event += 1  # its brightness changed, it says: a fresh read compares (see _brightness)
            if feature in kbsettings.FnCodec.features:
                self._fn.event += 1  # its Fn lock changed, maybe: a fresh read compares (see _read_one)
            self._settings_at = 0.0  # changed on the keyboard, maybe: read again, never taken from the report
            if self._task is None:
                self._next_task()

    def transport_gone(self, transport):
        if self._slots.pop(transport, None) is not None:
            self._invalidate()
        if transport is self.transport:
            self._drop()
        self._look()

    def config_changed(self, old_cfg, new_cfg):
        old, new = self._effective(old_cfg), self._effective(new_cfg)  # first: calling a follow off settles the keys
        if not new_cfg["flow"]["keyboard_follows_mouse"]:               # anew (_wanted), which reads them itself
            self.follow_cancel(None)
        self._remap(old, new)
        was, now, read = old_cfg["keyboard"], new_cfg["keyboard"], False
        if (was.get("fn_notices") is True) != (now.get("fn_notices") is True):
            own, self._fn = self._fn.own, Reported()  # a record of its own: reads started before settle nothing in it
            self._fn.own = own  # a change sent and not read back yet stays this program's
            read = now.get("fn_notices") is True and self.device is not None and kbsettings.FnCodec.supported(self.device)
        if (was.get("lock_notices") is True) != (now.get("lock_notices") is True):
            self._locks = Reported()  # off: forgotten; on: settled afresh
            read = read or self._watch_locks()
        if read:
            self._read_soon()  # what it is now, settled without a word before a change can be told

    def front_changed(self, app):
        """Another app is in front: ``app`` (see config.app_id), or None when that cannot be told."""
        app = app if config.app_id(app) else None
        if app == self._front:
            return
        old, self._front = self._effective(), app
        log.debug("the app in front changed")  # which one never goes to the log
        self._remap(old, self._effective())

    def _effective(self, cfg=None):
        """What the keys do (see effective_keys), by ``cfg`` or the settings in use. A key keeps the very object it
        had while its action stays equal, however often the settings were copied: the macros its presses started
        are that object's (Executor.cancel). A changed one gets a copy of its own."""
        kept, self._actions = self._actions, {}
        for name, action in effective_keys((cfg or self.engine.cfg)["keyboard"], self._front).items():
            self._actions[name] = kept[name] if kept.get(name) == action else copy.deepcopy(action)
        return self._actions

    def _remap(self, old, new):
        """The keys do ``new`` instead of ``old`` (see effective_keys). A key whose action changed lets go of what it
        held, and the macros it started stop; every other key, and what it started, carries on."""
        changed = {name for name in old.keys() | new.keys() if old.get(name) != new.get(name)}
        if not changed:
            return
        for cid, action in list(self._held.items()):  # a key held down lets go of what it did: its key-up does nothing
            if config.key_id(cid) in changed:
                del self._held[cid]
                if action.get("type") == "ring":
                    self.engine._ring_event(("keyboard", cid), "cancel")  # closes, running nothing
                else:
                    self.executor.release(action)
        for name in changed & old.keys():  # every macro its presses started: playing, queued or turned away
            self.executor.cancel(old[name])
        if self._task is not None and self._task.__name__ == "_configure":  # brings the keys in line with the old
            wish = self._wish or "configure"                                # settings: called off, and started over
            self._stop_task()
            self._wish = wish
            self._next_task()
        else:
            self.configure()

    def due(self, now):
        """How long until tick() has something to do."""
        idle = self.device is not None and self.online and self._task is None
        moments = [self._next_recovery, self._settings_moment()] if idle else []
        if self.device is None and self.engine.mouse is not None:
            moments += [self._next_nodes, self._next_look]
        if self._follow_wait is not None:
            moments.append(self._follow_wait[3])
        return min(moments, default=float("inf")) - now

    def tick(self, now):
        if self._follow_wait is not None:
            self._settled(now)
        if self.device is None and self.engine.mouse is not None:
            if now >= self._next_nodes:
                self._next_nodes = now + NODE_INTERVAL
                self._new_nodes()
            if now >= self._next_look:
                self._next_look = now + ASLEEP_RETRY
                self._look()
        if self.device is not None and self.online and self._task is None and now >= self._next_recovery:
            self._leaving = 0.0  # still here: see _switch
            self.configure()
        elif self.device is not None and self.online and self._task is None and now >= self._settings_moment():
            self._next_task()

    # -- the keyboard following the mouse ----------------------------------------

    def follow(self, host, link, event, asked=False, wait=True):
        """Flow: the mouse has just been sent to the computer that has this keyboard (``link``, see flow_link) on
        channel ``host``. Send the keyboard after it, once, if all is as it should be; otherwise it stays, and the
        log says why. ``event`` (a threading.Event) set calls it off, up to the moment the request goes.
        ``asked``: an action asked for it (go_along), whatever Flow says; ``wait``: for a mouse on Bluetooth to be gone."""
        why = self._follow_refusal(host, link, event, asked)
        if why:
            _stays(why)
            return
        if asked:
            self._go_along = event
        now = time.monotonic()
        deadline = now + FOLLOW_BUDGET
        if wait and self._mouse_on_its_way():
            self._follow_wait = (host, link, event, now + FOLLOW_SETTLE, deadline)
        else:
            self._want(("follow", host, event, deadline))

    def _follow_refusal(self, host, link, event, asked):
        """Why the keyboard would not follow the mouse to ``host`` now (see follow), or None."""
        dev = self.device
        current, hosts = self._hosts
        if event is not None and event.is_set():
            return "cancelled"
        if not asked and not self.engine.cfg["flow"]["keyboard_follows_mouse"]:
            return "off"
        if dev is None or not self.online or (link is None and not asked) or self.link != link:
            return "not-here"
        if self._busy(time.monotonic()):
            return "busy"
        if not (dev.pairing_read is True and len(hosts) == dev.host_count and 0 <= current < dev.host_count
                and 0 <= host < dev.host_count and host != current and hosts[host]["paired"] is True):
            return "pairing"  # only to a channel the keyboard itself said is paired
        if not self.transport.can_tell_keys():
            return "unsupported"  # it could not be told whether a key is down there
        if asked and (self._pressed or self._held):
            return "held"  # a key this program follows is down
        return None

    def go_along_refusal(self, host):
        """Why the keyboard would not go along with the mouse to ``host`` (go_along) now, or None; "not-here" when
        there is no keyboard to go."""
        return self._follow_refusal(host, self.link, None, True)

    def go_along(self, host, mouse_sent=True):
        """An action has just sent the mouse to channel ``host`` (or would have, ``mouse_sent`` False: no mouse is
        here): the keyboard follows it as over Flow (follow), with or without Flow."""
        self.follow(host, self.link, threading.Event(), asked=True, wait=mouse_sent)

    def _busy(self, now):
        return (self._follow_wait is not None or isinstance(self._wish, tuple) or now < self._leaving
                or (self._task is not None and self._task.__name__ == "_switch"))

    def _mouse_on_its_way(self):
        """The mouse, sent off on a connection of its own (Bluetooth), is still there. While a key is being looked
        at, its node going would wait on the kernel's (it holds one lock for every hidraw node, up to 5 s: Linux
        drivers/hid/hidraw.c), and the engine with it: so the keyboard waits for the mouse to be gone first."""
        mouse = self.engine.mouse
        return mouse is not None and not mouse.transport.is_receiver

    def _settled(self, now):
        """tick(): the follow waiting for the mouse to be gone goes on, or ends."""
        host, link, event, settle_by, deadline = self._follow_wait
        if event.is_set():
            why = "cancelled"
        elif self.device is None or not self.online or self.link != link:
            why = "not-here"
        elif not self._mouse_on_its_way():
            self._follow_wait = None
            if self._busy(now):
                _stays("busy")
            else:
                self._want(("follow", host, event, deadline))
            return
        elif now > settle_by:
            why = "late"
        else:
            return
        self._follow_wait = None
        _stays(why)

    def follow_cancel(self, event):
        """Call off the keyboard following the mouse that ``event`` belongs to (None: any). One whose request has
        gone already is not called back."""
        def ours(other):  # None: any but the one an action asked for
            return other is event if event is not None else other is not self._go_along
        wish = self._wish
        if self._follow_wait is not None and ours(self._follow_wait[2]):
            self._follow_wait[2].set()
            self._follow_wait = None
        elif isinstance(wish, tuple) and wish[0] == "follow" and ours(wish[2]):
            wish[2].set()
            self._wish = None
            self.configure()
        elif self._task is not None and self._following and ours(self._called_off):
            self._stop_task()  # what it gave back is taken again, as the settings say
            self.configure()
        else:
            return
        _stays("cancelled")

    def reading_keys(self):
        """Whether the keyboard's thread is, or is about to be, seeing whether a key is down: the engine opens no
        node meanwhile (see _mouse_on_its_way)."""
        asked = self._asked
        return self._reading.is_set() or (asked is not None and asked[0] is _snap_switch)

    def _paired_channel(self):
        """The channel the keyboard is on here, if it said itself that this one is paired; None otherwise."""
        dev = self.device
        if dev is None or not self.online or dev.pairing_read is not True:
            return None
        current, hosts = self._hosts
        if len(hosts) != dev.host_count or not 0 <= current < dev.host_count or hosts[current]["paired"] is not True:
            return None
        return current

    def shutdown(self):
        """Hand back the keys this program diverted, and stop looking (the engine closes the transports after)."""
        self._cancel.set()
        self._generation += 1
        self._jobs.put(None)
        self._stop_task()
        if self.executor:
            self.executor.reset()
        self.engine._ring_gone("keyboard")
        if self.device is not None and self.online:  # here, on this thread, within SHUTDOWN_BUDGET
            dev = _view(self.device, self.transport, self._timeout(self.transport), threading.Event(), SHUTDOWN_BUDGET)
            try:
                _run_here(self._give_back(), lambda request: _call(dev, request))
            except HidppError as e:
                log.debug("keyboard restore on exit failed: %s", e)
        if self.executor:
            self.executor.close()
        if self._thread is not None:
            self._thread.join(timeout=PROBE_TIMEOUT_OWN + 0.5)

    def state(self):
        """What the window shows of the keyboard, or None when none was ever seen. Never says which unit it is."""
        dev = self.device
        if dev is not None:
            self._seen = (dev.name, self._keys(), device.keyboard_settings(dev))
        if self._seen is None:
            return None
        connected = dev is not None and self.online
        return {"connected": connected, "known": True, "name": self._seen[0], "battery": self._battery if connected else None,
                "current_host": self._hosts[0], "hosts": copy.deepcopy(self._hosts[1]) if connected else [],
                "receiver": self.transport.is_receiver if connected else None,  # False: Bluetooth (or a cable)
                "keys": copy.deepcopy(self._seen[1]), "reason": self._block if dev is not None else None,
                "failed": self._failed if connected else None, "firmware": self._firmware if connected else None,
                "settings": list(self._seen[2]), "controls": self._shown_controls(),
                "notice": dict(self._notice) if connected and self._notice else None}

    def _shown_controls(self):
        shown = {}
        for name, control in self._controls.items():
            public = getattr(kbsettings.CODECS.get(name), "public", None)  # what of a reading is shown, where not all
            if public is not None:
                public = public(control) if control["value"] is not None else {"value": None}
            shown[name] = dict(public or control, writable=control["writable"] and self._block is None,
                               busy=name in self._pending or name == self._committing, outcome=self._outcomes.get(name))
        return shown

    # -- settings the keyboard keeps -----------------------------------------------

    def set_setting(self, name, value):
        """Have setting ``name`` (see kbsettings.CODECS) changed to ``value`` on the keyboard. Asked from outside the
        engine: whatever does not fit a setting the keyboard has, and was read, is dropped."""
        codec = kbsettings.CODECS.get(name) if type(name) is str else None
        control = self._controls.get(name) if codec is not None else None
        if (control is None or not codec.valid(value) or not control["writable"] or control["value"] is None
                or self._block is not None):
            log.debug("keyboard setting change ignored")
            return
        merge = getattr(codec, "merge", None)  # one asked for before, and not sent yet, is replaced, or laid under
        self._pending[name] = merge(self._pending.get(name), value) if merge else value
        self._outcomes[name] = None
        self.engine._publish()
        if self._task is None:
            self._next_task()

    def _settings_moment(self):
        """From when (time.monotonic) a settings task may run: a change once no key this program follows is down,
        a read when one is due; neither while the keyboard may be on its way elsewhere. inf: none to run."""
        at = 0.0 if self._pending and not (self._pressed or self._held) else self._settings_at
        return max(at, self._leaving) if at < float("inf") else at

    def _settings_held(self):
        """Whether no setting may be changed now: for the reasons no key may be (see _blocked, read afresh), shown
        as the state's "reason". Then what was asked for and not sent yet is dropped: never sent later."""
        self._block = self._blocked()
        if self._block is not None:
            self._pending.clear()
        return self._block is not None

    def _read_one(self, name, codec, host=None):
        """A task: read setting ``name`` afresh into self._controls; returns (host, what was read). Where it fails,
        the setting is unknown, and the error is passed on."""
        fn = self._fn  # the Fn lock's record this read is made for (see _say)
        try:
            if host is None and codec.needs_host(self.device):
                host = yield (kbsettings.current_host, self._hosts[0])
            events = self._level_event  # the brightness changes reported before this read (see _brightness)
            fn_start = fn.event
            read = yield (codec.read, host)
        except Exception as e:
            self._controls[name] = {"value": None, "default": None, "writable": False}
            if name == kbsettings.BACKLIGHT:
                self._level_read = None
                if not isinstance(e, HidppClosed) and (self._level_own or self._level_event > self._level_handled):
                    self._level_recheck()  # a report or a change of its own is still to be settled: read later
            if (name == kbsettings.FN and not isinstance(e, (HidppClosed, HidppTimeout)) and fn is self._fn
                    and self.engine.cfg["keyboard"].get("fn_notices") is True and fn.failed()):
                self._level_recheck()  # one read more for what is still to be settled
            raise
        self._controls[name] = dict(read, writable=read.get("writable", True) is True)  # where it says, it counts
        if name == kbsettings.BACKLIGHT:
            self._level_read = (read, events)
            self._brightness(read, events)
        if (name == kbsettings.FN and fn is self._fn and fn.read(fn_start, read["value"])
                and self.engine.cfg["keyboard"].get("fn_notices") is True):
            self._say({"kind": "fn", "value": read["value"]})
        return host, read

    # The Fn lock and the lock keys the keyboard reports changed are told the same way: the report only
    # says that something changed, a read afterwards says to what, and its record (Reported) compares that with what
    # was settled before. A read counts only where no report came while it was made and its record is still the one
    # in use: a new connection, and each turn of the notices on or off, makes a new one. The first read of a record
    # is settled without a word, as is each one from this program's Fn change until a read after it. A read that fails
    # for another reason than the link going or no answer is tried once more after RECOVERY_RETRY, for each report or
    # change still to be settled; if that fails too, the change is told only once a later read counts, or not at all.
    # Nothing here holds up a change, sends a setter, or says what made the change.

    def _say(self, notice):
        """Make ``notice`` the newest one (state()["notice"]), numbered on from the last."""
        self._notice_seq += 1
        self._notice = dict(notice, seq=self._notice_seq)
        self.engine._publish()

    def _watch_locks(self):
        """Whether the lock keys are read and told: the settings ask for it and the keyboard has them."""
        return (self.device is not None and self.engine.cfg["keyboard"].get("lock_notices") is True
                and self.device.has(device.LOCK_KEY_STATE))

    def _read_soon(self):
        """Have the settings read now, or once the task under way is over."""
        if self.device is not None and self.online:
            self._settings_at = 0.0
            if self._task is None:
                self._next_task()

    def _read_locks(self):
        """A task: which lock keys are on, told where they changed since the last read that counts (see _say).
        What is read is only compared, never kept anywhere else or written to the log."""
        locks = self._locks  # replaced meanwhile (turned off or on, gone): this read settles nothing
        start = locks.event
        try:
            now = yield (kbsettings.lock_state,)
        except HidppClosed:
            raise
        except HidppTimeout:
            self._settings_at = time.monotonic() + RECOVERY_RETRY  # as for the settings
            return
        except (HidppError, kbsettings.BadReply, IndexError) as e:
            log.debug("keyboard lock state not read: %s", type(e).__name__)
            if locks is self._locks and locks.failed():
                self._level_recheck()
            return
        old = locks.seen
        if locks is self._locks and locks.read(start, now):
            self._say({"kind": "locks", "keys": {name: bool(now & bit) for name, bit in kbsettings.LOCK_KEYS
                                                 if (now ^ old) & bit}})

    # The brightness the keyboard reports changed (BACKLIGHT_2 event 0), told as a notice where the settings ask for
    # one. The report only says that it changed: a fresh read says to what, and is compared with the level last
    # settled (_level_seen). A report is settled (_level_handled reaching _level_event) only by a read made wholly
    # after it that says the level for certain; until then it stays to be settled, whatever else is read or changed.
    # From the moment this program sends a change of the backlight (_level_own) until a read after that change says
    # the level for certain, what is reported is taken as that change, and settled without a word: what changed it
    # cannot be told apart then, so a change made on the keyboard in that time, or reported before and not settled
    # yet, may go unsaid; it never holds up the change. Nothing here says what changed it, only to what level.

    def _level_reset(self):
        self._level_seen = None   # the level last settled, None: none yet on this connection
        self._level_event = 0     # brightness changes reported on this connection
        self._level_handled = 0   # of those, settled
        self._level_own = False   # a change of the backlight sent by this program is not settled yet
        self._level_read = None   # (the last backlight read, _level_event before it), None: it failed
        self._notice = None       # the notice last made (see state)
        self._fn = Reported()     # the Fn lock as reported and read (see _say)
        self._locks = Reported()  # the lock keys as reported and read, while they are told

    @staticmethod
    def _level_certain(read):
        """Whether ``read`` says the level for certain."""
        return read is not None and read["levels"] is not None and read["shown_level"] is not None

    @classmethod
    def _level_comparable(cls, read):
        """Whether ``read`` says enough to compare the level: for certain, and a mode it knows while on."""
        return cls._level_certain(read) and not (read["value"]["on"] and read["value"]["mode"] is None)

    def _level_recheck(self):
        """Have the backlight read again after RECOVERY_RETRY, unless a read is due before that."""
        self._settings_at = min(self._settings_at, time.monotonic() + RECOVERY_RETRY)

    def _brightness(self, read, events):
        """A fresh backlight read ``read``, made after ``events`` reports: what it settles, and the notice it makes."""
        if self._level_own:
            if self._committing != kbsettings.BACKLIGHT:  # the change is over: a read now settles it, if it can
                self._own_settled(read, events)
            return
        if self._level_event != events:
            return  # reported again while this was read: the read due says more
        if not self._level_comparable(read):
            if events > self._level_handled:
                self._level_recheck()
            return
        level, value = read["shown_level"], read["value"]
        if (self._level_seen is not None and events > self._level_handled and level != self._level_seen
                and value["on"] and value["mode"] in ("none", "keys", "manual")
                and self.engine.cfg["keyboard"].get("notices") is True):
            self._say({"kind": "backlight", "level": level, "top": read["levels"] - 1})
        self._level_seen, self._level_handled = level, events

    def _own_settled(self, read, events):
        """The change this program sent is settled by ``read`` (made after ``events`` reports), without a word, if
        it says the level for certain and nothing was reported meanwhile; otherwise read again later."""
        if self._level_certain(read) and self._level_event == events:
            self._level_seen, self._level_handled, self._level_own = read["shown_level"], events, False
        else:
            self._level_recheck()

    def _level_again(self):
        """Whether the backlight is to be read once more before its setter goes: a change was reported while it was
        read, which a read wholly after it can still tell as a notice before this program's change hides it."""
        read, events = self._level_read
        return (not self._level_own and self._level_event != events and self._level_comparable(read)
                and self.engine.cfg["keyboard"].get("notices") is True)

    def _read_settings(self):
        """A task: what the keyboard says of each setting it has that kbsettings knows. One left unanswered ends the
        reading, which is tried again after RECOVERY_RETRY; one that does not fit is shown as unknown."""
        for name, codec in kbsettings.CODECS.items():
            if not codec.supported(self.device):
                continue
            try:
                yield from self._read_one(name, codec)
            except HidppClosed:
                raise
            except HidppTimeout:
                self._settings_at = time.monotonic() + RECOVERY_RETRY
                break
            except (HidppError, kbsettings.BadReply, kbsettings.Unsupported, IndexError) as e:
                log.debug("keyboard setting %s not read: %s", name, e)
        else:  # ponytail: a lock report reads every setting; use a lock-only read if traffic becomes noticeable
            if self._watch_locks():
                yield from self._read_locks()
        self.engine._publish()

    def _write_setting(self, name):
        """A task: change setting ``name`` to the value last asked for (self._pending), see _change."""
        self._outcomes[name] = None
        self.engine._publish()
        try:
            self._outcomes[name] = yield from self._change(name, kbsettings.CODECS[name])
            if self._committing == kbsettings.BACKLIGHT:  # sent: the last read settles it, if it can (_brightness)
                self._own_settled(*(self._level_read or (None, None)))
        finally:
            self._committing = None
        self.engine._publish()

    def _change(self, name, codec, again=True):
        """A task: read the setting; if it is not the value asked for (laid over that reading, where the setting
        has kbsettings' ``target``), and the keyboard lets it be changed, send that once and read it back. Where the
        keyboard then has something else again, send back what it had and read that. Returns None when the
        keyboard has the value asked for, or why not: "unknown" (not read: nothing sent), "unsupported",
        "not-applied" (it kept what it had), "restored" (it took another value, and the earlier one was sent back),
        "unconfirmed" (it did not say what it has now, read again after RECOVERY_RETRY; or it took another value
        and, as read back, does not let the earlier be set or no longer has it: nothing sent back). Until the setter
        is on its way, the task may be called off (see _want) and the value asked for stays to be sent; from then on it
        is seen to its end, unless no setting may be changed any more (see rivals_changed): then nothing more is
        sent, as before the setter and before sending back what it had (see _settings_held). A setting with
        kbsettings' ``fields`` is asked for some fields only: only they count, and what it had before is sent back
        laid over what the keyboard has then. ``again``: the backlight may be read once more first (_level_again)."""
        try:
            host, before = yield from self._read_one(name, codec)
        except HidppClosed:
            raise
        except kbsettings.Unsupported:
            self._pending.pop(name, None)
            return "unsupported"
        except (HidppError, kbsettings.BadReply, IndexError) as e:
            log.debug("keyboard setting %s not read: %s", name, e)
            self._pending.pop(name, None)
            if isinstance(e, HidppTimeout):
                self._settings_at = time.monotonic() + RECOVERY_RETRY
            return "unknown"
        request = self._pending.get(name)
        if request is None:
            return None
        fields = getattr(codec, "fields", None)

        def seen(read):  # what counts of a reading: the fields asked for, or the whole value
            return fields(read, request) if fields else read["value"]
        old = seen(before)
        try:  # what to send, from what was just read: a whole value laid over by what was asked of it, or it alone
            if not self._controls[name]["writable"]:
                raise kbsettings.Unsupported("the keyboard does not let it be changed")
            if fields and old == request:
                target = None  # so already: nothing to send
            else:
                target = codec.target(before, request) if hasattr(codec, "target") else request
        except kbsettings.Unsupported:
            self._pending.pop(name, None)
            return "unsupported"
        wanted = request if fields else target
        if old == wanted:
            self._pending.pop(name, None)
            return None
        if self._settings_held():
            return None
        if self._pressed or self._held or time.monotonic() < self._leaving:
            return None  # a key is down, or the keyboard is on its way: asked again once that is over
        if again and name == kbsettings.BACKLIGHT and self._level_again():
            return (yield from self._change(name, codec, False))  # all of it once more, on that read
        del self._pending[name]  # from here on what is asked for after this is a change of its own
        self._committing = name
        if name == kbsettings.BACKLIGHT:
            self._level_own = True  # what the keyboard reports from now on may be this change (see _brightness)
        outcome = yield from self._send(name, codec, host, target)
        if outcome is not None or seen(self._controls[name]) == wanted:
            return outcome
        if seen(self._controls[name]) == old:
            log.info("keyboard setting %s: the keyboard did not take the change", name)
            return "not-applied"
        if self._settings_held():
            return "unconfirmed"  # shown as the keyboard has it: nothing sent back
        now, allows = self._controls[name], getattr(codec, "allows", None)  # as read back: may it have the earlier?
        try:
            if not now["writable"] or (allows is not None and not allows(now, old)):
                raise kbsettings.Unsupported("the earlier value no longer")
            back = codec.target(now, old) if fields else old  # laid over what it has now: nothing else changes
        except kbsettings.Unsupported:
            log.warning("keyboard setting %s: the keyboard took another value, and no longer lets the earlier be", name)
            return "unconfirmed"  # shown as the keyboard has it: nothing sent back
        log.warning("keyboard setting %s: the keyboard took another value; the earlier one is sent back", name)
        outcome = yield from self._send(name, codec, host, back)
        return outcome or ("restored" if seen(self._controls[name]) == old else "unconfirmed")

    def _send(self, name, codec, host, value):
        """A task: send ``value`` and read the setting back into self._controls. "unconfirmed" when it could not be
        read back, else None."""
        if name == kbsettings.FN:
            self._fn.sent()  # what it reports from now on may be this change (see _say)
        try:
            yield (codec.write, host, value)
        except HidppClosed:
            raise
        except HidppError as e:  # it may have gone through all the same: the reading says
            log.debug("keyboard setting %s: %s", name, e)
        try:
            yield from self._read_one(name, codec, host)
        except HidppClosed:
            raise
        except (HidppError, kbsettings.BadReply, kbsettings.Unsupported, IndexError) as e:
            log.info("keyboard setting %s: the change was not confirmed (%s)", name, type(e).__name__)
            self._settings_at = time.monotonic() + RECOVERY_RETRY
            return "unconfirmed"
        return None

    # -- actions -------------------------------------------------------------

    def device_action(self, action, phase):
        """Easy-Switch from a key moves the keyboard; nothing else a key does is the keyboard's business."""
        dev = self.device
        if action.get("type") == "together" and phase != "release":
            self.engine._emit("flow", ("together", None))  # see engine._device_action
            return
        target = host_target(action) if action.get("type") == "host" else None
        if (target is None or target[1] is not None  # with the keyboard's channel it goes on the key's release: _on_keys
                or phase == "release" or dev is None or not self.online):
            return
        host = target[0]
        if not 0 <= host < dev.host_count or host == self._hosts[0]:
            return
        self._want(("host", host))

    def _switch(self, host, follow=None):
        """A task: move the keyboard to ``host``, only once every key this program diverted is given back (it
        comes back as new, and its keys are diverted again then). Where that cannot be done, or the keyboard
        does not go, it stays here with its keys as the settings say. ``follow``: (event, deadline) when it is
        to follow the mouse (see _follow_steps)."""
        if follow is not None:
            yield from self._follow_steps(host, *follow)
            return
        self._sent_at = None
        if not (yield from self._give_back()):
            log.warning("keyboard not moved to channel %s: a key could not be given back first", host + 1)
            self._failed = "switch"
            yield from self._configure()
            return
        self.executor.reset()
        self.engine._ring_gone("keyboard")
        self._sent_at = time.monotonic()  # it may be gone before the answer is back: then it went because of this
        try:
            yield (device.Mouse.change_host, host)
        except HidppClosed:
            raise
        except HidppError as e:
            log.warning("keyboard host switch failed: %s", e)
            self._sent_at = time.monotonic() if isinstance(e, HidppTimeout) else None
            self._failed = "switch"
            yield from self._configure()
            return
        self._failed, self._wish = None, None
        self._sent_at = time.monotonic()
        self._leaving = self._next_recovery = self._sent_at + SWITCH_GRACE  # still here then: it did not go,
        self.engine._publish()                                                 # and its keys are taken again

    def _follow_steps(self, host, event, deadline):
        """_switch() after the mouse: as from a key, but only while ``event`` is not set and ``deadline`` has not
        passed, and only if no key is down. Each of these is looked at once; whatever stops it, the keyboard stays,
        its keys taken again, and the log says why. It is not tried again."""
        if event.is_set() or time.monotonic() > deadline:
            yield from self._stay("cancelled" if event.is_set() else "late")
            return
        self._sent_at = None
        if not (yield from self._give_back()):
            self._failed = "switch"
            yield from self._stay("give-back")
            return
        if self._pressed or self._held:
            yield from self._stay("held")  # a key this program follows is down: it would stay down here
            return
        self.executor.reset()
        self._sent_at = time.monotonic()  # it may be gone before the answer is back: then it went because of this
        try:
            outcome = yield (_snap_switch, host, deadline)
        except HidppClosed:
            raise
        except HidppError as e:  # nothing was sent: _snap_switch says so itself where it may have been
            log.debug("keyboard not asked which keys are down: %s", e)
            outcome = "unknown"
        if outcome != "sent":
            self._sent_at = None
            yield from self._stay(outcome)
            return
        self._follow_over()
        self._failed, self._wish = None, None
        self._sent_at = time.monotonic()
        self._leaving = self._next_recovery = self._sent_at + SWITCH_GRACE
        log.info("flow: the keyboard follows the mouse to channel %d", host + 1)
        self.engine._publish()

    def _follow_over(self):
        """What the following still asks of the keyboard is ordinary from now on: calling it off no longer stops it."""
        self._called_off = threading.Event()
        self._following = False
        self._go_along = None

    def _stay(self, why):
        """A task: the keyboard does not follow the mouse (``why``); its keys are taken again."""
        self._follow_over()
        _stays(why)
        yield from self._configure()

    def _on_keys(self, pressed):
        keys = self._effective()
        failed = None  # the first error of this report: raised once every key of it was handled
        along = None   # the channel a key let go of sends the mouse and the keyboard to, once the report is handled
        for cid in sorted(pressed - self._pressed):
            self._pressed = self._pressed | {cid}  # done once, even when it fails: never done again by a later report
            action = keys.get(config.key_id(cid)) if cid in self._active else None
            log.info("key %s down", config.key_id(cid))
            if action and config.keyboard_action(action) and action.get("type") != "none":
                self._held[cid] = action
                try:
                    if action.get("type") == "ring":
                        self.engine._ring_event(("keyboard", cid), "down")
                    else:
                        self.executor.press(action)
                except BaseException as err:
                    if action.get("type") in ("keys", "button"):  # their press that failed holds nothing: the up must not let go
                        self._held.pop(cid, None)
                    if not isinstance(err, Exception):
                        raise  # an interrupt: nothing more of the report is done
                    if failed is None:
                        failed = err
        for cid in sorted(self._pressed - pressed):
            self._pressed = self._pressed - {cid}
            action = self._held.pop(cid, None)
            if action:
                log.info("key %s up", config.key_id(cid))
                target = host_target(action) if action.get("type") == "host" else None
                if target is not None and target[1] is not None:
                    along = target
                    continue
                try:
                    if action.get("type") == "ring":
                        self.engine._ring_event(("keyboard", cid), "up")
                    else:
                        self.executor.release(action)
                except Exception as err:
                    if failed is None:
                        failed = err
        self._pressed = pressed
        if along is not None:
            try:
                self.engine._switch_host(*along)  # every key of the report let go of first: see go_along_refusal
            except Exception as err:
                if failed is None:
                    failed = err
        if self._pending and not (pressed or self._held) and self._task is None:
            try:
                self._next_task()  # a change of a setting waited for the key to be let go
            except Exception as err:
                if failed is None:
                    failed = err
        if failed is not None:
            raise failed

    # -- the keys ------------------------------------------------------------

    def configure(self):
        """Have the keys brought in line with the settings (see _configure): now, or once the task under way is over."""
        self._want("configure")

    # -- tasks -------------------------------------------------------------------

    def _want(self, wish):
        """Have a task run (see self._wish), now if none is under way. A switch to another computer goes before
        bringing the keys in line, which it does itself where the keyboard stays."""
        if self.device is None or not self.online:
            return
        if wish == "configure" and (self._wish is not None or time.monotonic() < self._leaving):
            return  # leaving: taken up at self._next_recovery, should it stay
        running = self._task.__name__ if self._task is not None else None
        if running in ("_read_firmware", "_read_settings") or (running == "_write_setting" and self._committing is None):
            self._stop_task()  # never holds up the keys: called off, and done again after them; a change not sent
            if running == "_read_firmware":  # yet stays asked for (self._pending)
                self._firmware_due = True
            elif running == "_read_settings":
                self._settings_at = 0.0
        if isinstance(self._wish, tuple) and self._wish[0] == "follow":  # an Easy-Switch from a key goes instead
            _stays("busy")
        self._wish = wish
        if self._task is None:
            self._next_task()

    def _next_task(self):
        wish, self._wish = self._wish, None
        if self.device is None or not self.online:
            return
        follow = None
        if wish is not None:
            self._next_recovery, self._leaving = float("inf"), 0.0
            if wish == "configure":
                self._task = self._configure()
            else:
                follow = wish[2:] if wish[0] == "follow" else None
                self._task = self._switch(wish[1], follow)
        elif self._settings_moment() <= time.monotonic():
            if self._pending and not (self._pressed or self._held):
                self._task = self._write_setting(next(iter(self._pending)))
            else:
                self._settings_at = float("inf")
                self._task = self._read_settings()
        elif self._firmware_due and time.monotonic() >= self._leaving:  # not while it may be on its way elsewhere
            self._firmware_due = False
            self._task = self._read_firmware()
        else:
            return
        self._token += 1
        self._called_off = follow[0] if follow else threading.Event()  # the follow's own: Flow calls it off with it
        self._following = follow is not None
        self._step(None, None)

    def _step(self, reply, error):
        """Take the task under way on to its next request, sent to the keyboard's thread, or to its end."""
        try:
            request = self._task.send(reply) if error is None else self._task.throw(error)
        except StopIteration:
            self._task = self._asked = None
            self._next_task()
            return
        except Exception as e:
            self._task = self._asked = None
            if not isinstance(e, HidppClosed):  # closed: the transport's going is on its way, and handled then
                log.exception("keyboard task failed")
            return
        self._asked = request
        self._jobs.put(("ask", self._token, self.device, self.transport, self._timeout(self.transport), request,
                        self._called_off))

    def answered(self, token, reply, error):
        """The keyboard's thread made the request a task asked for (see _step)."""
        if token == self._token and self._task is not None:
            self._asked = None
            self._step(reply, error)

    def _stop_task(self):
        """Drop the task under way, and what was wished for after it. A request of it not sent yet never is
        (see _probe_loop); one sent already may have reached the keyboard, so what it did is asked afresh."""
        self._called_off.set()
        if self._task is not None:
            if self._asked is not None and self._asked[0] is _set_reporting:
                self._reporting.pop(self._asked[1], None)  # whether it went through is for the keyboard to say
            self._task.close()
        self._task = self._asked = self._wish = None
        self._following = False
        self._token += 1

    def _timeout(self, transport):
        mouse = self.engine.mouse
        return PROBE_TIMEOUT_SHARED if mouse is not None and mouse.transport is transport else PROBE_TIMEOUT_OWN

    def _owned(self):
        """The keys the journal says this program diverted on this keyboard."""
        return {int(key, 16) for key in self._journal.get(self._identity, ())}

    def _configure(self):
        """A task: bring the keys in line with the settings, as far as is safe (see the description of this
        module); keys not known yet are asked first. Returns True when nothing is left to settle."""
        self._stalled = False
        self._block = self._blocked()
        unknown = sorted((self._owned() | self._wanted()) - set(self._reporting))
        self._reporting.update((yield from _reporting_of(unknown)))
        settled = self._block is None and (yield from self._settle(self._wanted()))
        retry = not settled and self._block not in ("identity", "full")
        self._next_recovery = time.monotonic() + RECOVERY_RETRY if retry else float("inf")
        self.engine._publish()
        return settled

    def _wanted(self):
        """The keys the settings give an action that the keyboard lets this program have."""
        dev, wanted = self.device, set()
        for name, action in self._effective().items():
            if not config.KEY_ID.match(name) or not config.keyboard_action(action):
                continue
            cid, does = int(name, 16), here(action).get("type")
            control = dev.controls.get(cid)
            if (control is not None and control.divertable and cid not in device.CID_PROTECTED
                    and not (does in ("keys", "button") and self.engine.vinput is None)):
                wanted.add(cid)
        return wanted

    def _blocked(self):
        """Why no key may be touched now, or None. Reads the journal afresh."""
        reason = why = None
        if self.engine._rivals:
            reason, why = "rivals", "other software for the device is running"
        elif self._identity is None:
            reason, why = "identity", "the keyboard does not say which one it is"
        else:
            try:
                self._journal = read_journal()
            except JournalUnavailable as e:
                reason, why = "journal", f"{JOURNAL} cannot be read ({e})"
            else:
                if self._identity not in self._journal and len(self._journal) >= MAX_JOURNAL_KEYBOARDS:
                    reason, why = "full", f"{JOURNAL} already lists {MAX_JOURNAL_KEYBOARDS} other keyboards"
        if reason != self._said:
            self._said = reason
            if why:
                log.warning("keyboard keys left as they are: %s", why)
        return reason

    def _record(self, owned):
        """Write the journal with ``owned`` as this keyboard's keys. False if that could not be done: what is
        known of the file stays what it holds."""
        entries = dict(self._journal)
        entries[self._identity] = sorted(config.key_id(cid) for cid in owned)
        if not entries[self._identity]:
            del entries[self._identity]
        if entries == self._journal:
            return True
        if len(owned) > config.MAX_KEYBOARD_KEYS:
            return False
        try:
            write_journal(entries)
        except OSError as e:
            log.warning("could not write %s: %s", JOURNAL, e)
            return False
        self._journal = entries
        return True

    def _write(self, cid, divert):
        """A task: divert ``cid`` or give it back; returns what the keyboard says of it afterwards (None: unknown)."""
        if self._stalled:
            return None
        try:
            if (yield (_set_reporting, cid, divert)):
                self._reporting[cid] = "diverted" if divert else "native"
                return self._reporting[cid]
        except HidppClosed:
            raise
        except HidppTimeout:
            self._stalled = True
        except (HidppError, IndexError, struct.error) as e:
            log.debug("key %s: %s", config.key_id(cid), e)
        self._reporting.pop(cid, None)  # whether it went through is for the keyboard to say
        if not self._stalled:
            self._reporting.update((yield from _reporting_of([cid])))
            self._stalled = cid not in self._reporting
        return self._reporting.get(cid)

    def _settle(self, wanted):
        """A task: divert the ``wanted`` keys and give back every other one this program diverted, as the keyboard
        and the journal allow. Returns True when every key is settled."""
        owned = self._owned()
        settled = True
        for cid in sorted(owned | wanted | self._active):
            state = self._reporting.get(cid)
            if cid in owned:
                if state is None:  # unknown: nothing done until the keyboard says
                    settled = False
                    continue
                if state == "diverted":
                    if cid in wanted:
                        self._active.add(cid)  # left on by an earlier run, or now: it stays
                        continue
                    state = yield from self._write(cid, False)
                    if state in ("diverted", None):
                        settled = False
                        continue
                owned.discard(cid)  # native (back, or the keyboard was reset), or someone else's now
                self._active.discard(cid)
            if cid in wanted and state == "native":
                if not self._record(owned | {cid}):  # written down first, or not diverted at all
                    settled = False
                    continue
                owned.add(cid)
                state = yield from self._write(cid, True)
                if state == "diverted":
                    self._active.add(cid)
                elif state is None:
                    settled = False
                else:
                    owned.discard(cid)
            elif cid not in owned:
                self._active.discard(cid)  # not in the journal: not this program's to keep
                if cid in wanted and state is None:
                    settled = False
        return self._record(owned) and settled

    def _give_back(self):
        """A task: give back every key this program diverted. True when the keyboard says none is left diverted
        and the journal says so too."""
        self._stalled = False
        if self._blocked() is None:
            unknown = sorted(self._owned() - set(self._reporting))
            self._reporting.update((yield from _reporting_of(unknown)))
            yield from self._settle(set())
        return not self._active and not self._owned()

    def _read_firmware(self):
        """A task: the keyboard's firmware version, read once after it is found, when nothing else is to be done.
        It is only shown: where it is not read in time, or a reply is not one, it stays unknown."""
        try:
            self._firmware = yield (device.Mouse.get_firmware,)
        except HidppClosed:
            raise
        except HidppError as e:  # out of time: asked again only when the keyboard is found again
            log.debug("keyboard firmware not read: %s", e)
        self.engine._publish()

    def _keys(self):
        keys = []
        for cid, control in sorted(self.device.controls.items()):
            protected = cid in device.CID_PROTECTED
            if not (control.divertable or protected):
                continue
            state = self._reporting.get(cid)
            if protected:
                reason = "protected"
            elif cid in self._active or state == "native":
                reason = None
            else:  # someone else's, or diverted by a run of this program it cannot tell, or not known yet
                reason = {"foreign": "foreign", "diverted": "changed"}.get(state, "pending")
            keys.append({"id": config.key_id(cid), "cid": cid, "tid": control.tid, "protected": protected,
                         "active": cid in self._active, "native_reason": reason, "pos": getattr(control, "pos", 0)})
            if len(keys) >= MAX_CONTROLS:
                break
        return keys

    # -- the device coming and going -------------------------------------------

    def _drop(self):
        """The keyboard is gone. What it had diverted stays in the journal: settled when it is back."""
        if self.device is None:
            return
        now = time.monotonic()
        sent = self._sent_at is not None and (
            now - self._sent_at < SWITCH_GRACE or self._task is not None and self._task.__name__ == "_switch")
        self._stop_task()
        if self.executor:
            self.executor.reset()
        self.engine._ring_gone("keyboard")
        self._slots.get(self.transport, {}).pop(self.device.devidx, None)
        self._seen = (self.device.name, self._keys(), device.keyboard_settings(self.device))
        self.last_drop = (now, sent) if self.link else None
        self._sent_at = None
        self.device, self.transport, self.online = None, None, False
        if self._follow_wait is not None:
            self._follow_wait = None
            _stays("not-here")
        self._go_along = None
        self._identity = None
        self._active.clear()
        self._pressed.clear()
        self._held.clear()
        self._battery = self._firmware = None
        self._firmware_due = False
        self._controls, self._pending, self._outcomes, self._settings_at = {}, {}, {}, float("inf")
        self._level_reset()
        self._block = self._failed = None
        self._next_recovery = float("inf")
        self.engine._publish()
        self._tell_flow()

    def rivals_changed(self):
        """Other software for the device started or stopped (engine._rivals). Once a setting may not be changed, a
        change of it not sent yet is called off; one that may be on its way already is read again, never sent
        back or on (see _settings_held)."""
        if self.device is None or not self.online or not self._settings_held():
            return
        if self._task is not None and self._task.__name__ == "_write_setting":
            name, wish = self._committing, self._wish
            self._stop_task()  # what it has not sent never goes; what was wished for after it still is
            self._wish = wish
            if name is not None:
                self._outcomes[name] = "unconfirmed"
                self._settings_at = 0.0
            self._next_task()

    def _tell_flow(self):
        """Flow's side of the keyboard coming and going (flow_service.FlowService.on_engine, "keyboard"). An event
        of the engine's that stays in this program: the link is not in the state, which the window and others see."""
        self.engine._emit("flow", ("keyboard", {"link": self.link, "here": self.device is not None and self.online,
                                                "drop": self.last_drop, "channel": self._paired_channel()}))

    def _again(self, linked):
        transport, devidx = self.transport, self.device.devidx
        self._drop()
        if linked:
            self._queue(transport, [devidx])

    # -- looking for the keyboard ------------------------------------------------

    def _invalidate(self):
        """Looks under way no longer count: the slots they are at are unknown again."""
        self._generation += 1
        for states in self._slots.values():
            for devidx in [devidx for devidx, what in states.items() if what == "pending"]:
                del states[devidx]

    def _queue(self, transport, slots, loaded=None):
        states = self._slots.setdefault(transport, {})
        for devidx in slots:
            states[devidx] = "pending"
        mouse = self.engine.mouse
        timeout = PROBE_TIMEOUT_SHARED if mouse is not None and mouse.transport is transport else PROBE_TIMEOUT_OWN
        self._jobs.put(("look", self._generation, transport, list(slots), loaded or {}, timeout))

    def _look(self):
        """Ask every slot nothing is known of yet, or that did not answer last time."""
        if self.device is not None or self.engine.mouse is None:
            return
        for transport in list(self.engine._transports.values()):
            states = self._slots.setdefault(transport, {})
            wanted = [devidx for devidx in transport.slots() if states.get(devidx) in (None, "asleep")]
            if wanted and not transport.closed:
                self._queue(transport, wanted)

    def _new_nodes(self):
        """Receivers and Bluetooth devices that came since the mouse was found."""
        for path, is_receiver in hidpp.find_hidpp_nodes():
            if path not in self.engine._transports:
                transport = self.engine._transport(path, is_receiver)
                if transport is not None:
                    self._queue(transport, transport.slots())

    def _probe_loop(self):
        while True:
            job = self._jobs.get()
            if job is None or self._cancel.is_set():
                return
            if job[0] == "ask":  # a request of a task (see KeyboardSide._step)
                _ask, token, dev, transport, timeout, request, called_off = job
                if called_off.is_set():
                    continue  # its task was dropped (keyboard gone or replaced, settings changed, exit): never sent
                reply = error = None
                reading = request[0] is _snap_switch
                if reading:
                    self._reading.set()
                try:  # called off while waiting for its turn, or just before it goes: not sent either
                    reply = _call(_view(dev, transport, timeout, called_off, REQUEST_BUDGET), request)
                except Exception as e:
                    error = e
                finally:
                    if reading:
                        self._reading.clear()
                self.engine._queue.put(("kb_answer", token, reply, error))
                continue
            _look, generation, transport, slots, loaded, timeout = job
            if generation != self._generation:
                continue
            view = hidpp.ProbeTransport(transport, timeout, self._cancel, time.monotonic() + PROBE_BUDGET)
            states, prepared = {}, None
            try:
                for devidx in slots:
                    if generation != self._generation:  # no longer counts: the next job may
                        break
                    dev = loaded.get(devidx)
                    if dev is None:
                        what, dev = device.probe_slot(view, devidx)
                    else:
                        what = "keyboard"
                    if what == "keyboard":
                        dev.transport = view
                        cids = [cid for cid, control in sorted(dev.controls.items()) if control.divertable][:MAX_CONTROLS]
                        prepared = (dev, dev.identity(), _run_here(_reporting_of(cids), lambda request: request[0](dev, *request[1:])),
                                    _status(dev))
                        dev.transport = transport
                        states[devidx] = what
                        break
                    states[devidx] = what
            except (HidppCancelled, HidppClosed) as e:
                log.debug("look at %s ended: %s", transport.path, e)
                prepared = None
            except Exception:
                log.exception("looking for the keyboard failed")
                prepared = None
            self.engine._queue.put(("kb_found", generation, transport, slots, prepared, states))
