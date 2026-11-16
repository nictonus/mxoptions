"""Stand-ins for the mouse and for whatever runs the actions, so the engine's logic can be tested
without a device."""

import copy
import os
import queue
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mxoptions import config, device, engine, hidpp, vinput  # noqa: E402


class Recorder(engine.Listener):
    """Keeps what the engine tells the rest of the program."""

    def __init__(self):
        self.events = []    # (kind, payload) as passed on to other programs
        self.gestures = []  # live gesture updates for the settings page

    def on_event(self, kind, payload):
        self.events.append((kind, payload))

    def on_gesture(self, event):
        self.gestures.append(event)


class Actions:
    """Stands in for the executor: remembers which actions were run."""

    def __init__(self):
        self.ran = []

    def trigger(self, action):
        self.ran.append(action)

    def press(self, action):
        self.ran.append(action)

    def release(self, action):
        pass

    def reset(self):
        pass


class Mouse:
    """An MX Master 4 as far as the engine's logic needs one."""

    wheel = {"multiplier": 15, "has_invert": True, "has_ratchet": True, "notches": 24}
    smartshift = {"torque": True, "default_threshold": 10, "default_torque": 75}
    thumbwheel = {"native_res": 20, "diverted_res": 120, "direction": -1}
    haptic_waveforms = list(device.WAVEFORMS)
    controls = {cid: True for cid in config.BUTTONS.values()}
    dpi_range = (200, 8000, 50)

    def __init__(self):
        self.reporting = {}

    def set_cid_reporting(self, cid, divert, raw_xy=False):
        self.reporting[cid] = (divert, raw_xy)


def make_engine(**sections):
    """An engine with a mouse attached and the given settings laid over the defaults, e.g.
    ``make_engine(gestures={"trigger": "repeat"})``. Returns (engine, list of vibration names played)."""
    cfg = copy.deepcopy(config.DEFAULTS)
    for section, values in sections.items():
        cfg[section].update(values)
    made = engine.Engine(cfg, Recorder())
    made.executor = Actions()
    made.mouse = Mouse()
    made.online = True
    made.vinput = object()
    made._wheel_mode = "ratchet"
    made._monitor = True
    made._next_battery = float("inf")
    made._publish = lambda: None
    played = []
    made._play = lambda waveform, wait=True: played.append(device.WAVEFORMS[waveform])
    return made, played


class Clock:
    """Replaces time.monotonic while a test runs, so pauses do not have to be waited for."""

    def __init__(self, start=100.0):
        self.now = start

    def __enter__(self):
        self._real = time.monotonic
        time.monotonic = lambda: self.now
        return self

    def __exit__(self, *_exc):
        time.monotonic = self._real


def names(actions):
    """What the recorded actions were: what was asked of the desktop, a KDE shortcut's name, or a key list."""
    return [action.get("do") or action.get("name") or action.get("keys") for action in actions]


# -- a HID++ node without hardware ----------------------------------------------------------------------------------
# Everything below is made up for the tests: names, model and unit ids, control ids and their flags are synthetic,
# picked to exercise the code, and say nothing about any real device.

class FakeDevice:
    """A synthetic HID++ 2.0 device behind a FakePort slot. ``controls``: [(cid, tid, flags)] or [(cid, tid, flags, pos)], flags
    as getCidInfo gives them (byte 4 low, byte 8 high), pos its byte 5 (the F-key; 0 when left out). ``reporting``: cid -> [flags, remap] as getCidReporting answers.
    ``entities``: the getFwInfo replies of DEVICE_INFO, by index; ``extra``: further feature ids it lists, answered
    with an error, except for the settings in ``settings``: {FN_INVERSION_HOSTS: {host: [state, default]},
    FN_INVERSION_DEFAULT: [state, default], FN_INVERSION: [state], HOSTS_INFO: current host it says}, each
    answered (functions 0 and 1) and listed; also DISABLE_KEYS: {"caps": bits, "current": bits} (functions 0, 1, 2),
    MULTI_PLATFORM: {"info": function 0's answer, "descriptors": [function 1's answers]} (functions 0, 1, 3; 3
    leaves the platform in byte 6), DUAL_PLATFORM: [platform] (function 0) and BACKLIGHT_2: {"config": function 0's
    14 bytes, "info": function 2's answer} (function 1 takes 12 bytes, laid into the config as both sources read it:
    bytes 0, 1 and the level and timers, never the capabilities or effects; a fixed level is then the one function
    2 says is shown). ``versions``: {feature id: version},
    as Root function 0 answers it in byte 2 (0 for any other)."""

    def __init__(self, name="Synthetic keyboard", kind=device.KIND_KEYBOARD, controls=(), unit=b"\x5e\x01\x5e\x02",
                 model=b"\x0a\x0b\x0c\x0d\x0e\x0f", info=True, battery=(80, device.BATTERY_DISCHARGING), hosts=(3, 0),
                 entities=(), extra=(), settings=None):
        self.name = name
        self.kind = kind
        self.controls = list(controls)
        self.reporting = {control[0]: [0, 0] for control in self.controls}
        self.unit, self.model = unit, model
        self.battery = battery
        self.hosts = list(hosts)
        self.features = [device.ROOT, device.FEATURE_SET, device.DEVICE_NAME, device.REPROG_CONTROLS_V4,
                         device.UNIFIED_BATTERY, device.CHANGE_HOST, device.WIRELESS_DEVICE_STATUS]
        if info:
            self.features.append(device.DEVICE_INFO)
        self.features += list(extra)
        self.settings = copy.deepcopy(settings or {})
        self.features += [feature for feature in self.settings if feature not in self.features]
        self.entities = list(entities)
        if kind in (device.KIND_MOUSE, device.KIND_TRACKBALL):
            self.features = [device.ROOT, device.FEATURE_SET, device.DEVICE_NAME, device.REPROG_CONTROLS_V4]
        self.silent = False      # answers nothing (asleep, out of range)
        self.delay = 0.0         # s before each answer
        self.delays = {}         # feature id -> s before its answers, instead of delay
        self.failing = {}        # (feature id, function) -> HID++ 2.0 error code, or None for no answer
        self.echo = True         # setCidReporting answers with what it was sent
        self.sets = []           # (cid, flags) of every setCidReporting
        self.switched = []       # the hosts it was told to change to
        self.setting_writes = []  # (feature id, params) of every setter of a setting
        self.ignore_writes = False  # a setter is answered, but changes nothing
        self.taken = {}          # feature id -> [state, ...]: what the next setters leave instead of what they send
        self.replies = {}        # (feature id, function) -> payload answered instead
        self.versions = {}       # feature id -> version, as Root function 0 says it

    def index(self, feature):
        return self.features.index(feature)

    def answer(self, feat_idx, function, params):
        """Payload bytes, an int (HID++ 2.0 error code), or None (no answer)."""
        if self.silent or feat_idx >= len(self.features):
            return None if self.silent else 6
        feature = self.features[feat_idx]
        if (feature, function) in self.failing:
            return self.failing[(feature, function)]
        if (feature, function) in self.replies:
            return self.replies[(feature, function)]
        if feature in self.settings:
            return self._setting(feature, function, params)
        if feature == device.ROOT:
            if function == 1:
                return bytes([4, 2, params[2]])
            wanted = struct.unpack("!H", params[:2])[0]
            return bytes([self.features.index(wanted), 0, self.versions.get(wanted, 0)]) if wanted in self.features else bytes(3)
        if feature == device.FEATURE_SET:
            if function == 0:
                return bytes([len(self.features) - 1])
            return struct.pack("!HB", self.features[params[0]], 0) if params[0] < len(self.features) else 3
        if feature == device.DEVICE_NAME:
            name = self.name.encode()
            return {0: bytes([len(name)]), 1: name[params[0]:params[0] + 16], 2: bytes([self.kind])}.get(function, 7)
        if feature == device.REPROG_CONTROLS_V4:
            if function == 0:
                return bytes([len(self.controls)])
            if function == 1:
                cid, tid, flags, *pos = self.controls[params[0]]
                return struct.pack("!HHBBBBB", cid, tid, flags & 0xFF, pos[0] if pos else 0, 0, 0, flags >> 8)
            cid = struct.unpack("!H", params[:2])[0]
            if cid not in self.reporting:
                return 2
            if function == 2:
                flags, remap = self.reporting[cid]
                return struct.pack("!HBH", cid, flags, remap)
            if function == 3:
                flags = params[2]
                self.sets.append((cid, flags))
                if flags & 0x02:
                    self.reporting[cid][0] = (self.reporting[cid][0] & ~0x01) | (flags & 0x01)
                return bytes(params[:5]) if self.echo else struct.pack("!HBH", cid, 0, 0)
        if feature == device.DEVICE_INFO and function == 0:
            return bytes([len(self.entities)]) + self.unit + bytes([0, 0]) + self.model
        if feature == device.DEVICE_INFO and function == 1:
            return self.entities[params[0]] if params[0] < len(self.entities) else 2
        if feature == device.UNIFIED_BATTERY and function == 1:
            return bytes([self.battery[0], 0, self.battery[1]])
        if feature == device.CHANGE_HOST:
            if function == 0:
                return bytes(self.hosts)
            if function == 1:
                self.switched.append(params[0])
                return bytes()
        return 7

    def _setting(self, feature, function, params):
        value = self.settings[feature]
        if feature == device.HOSTS_INFO:
            return bytes([0, 0, self.hosts[0], value]) if function == 0 else 7
        if feature == device.DISABLE_KEYS:
            if function in (0, 1):
                return bytes([value["caps" if function == 0 else "current"]])
            if function != 2:
                return 7
            self.setting_writes.append((feature, bytes(params[:1])))
            if not self.ignore_writes:
                value["current"] = self.taken[feature].pop(0) if self.taken.get(feature) else params[0]
            return bytes(params[:1])
        if feature == device.MULTI_PLATFORM:
            if function == 0:
                return bytes(value["info"])
            if function == 1:
                return bytes(value["descriptors"][params[0]]) if params[0] < len(value["descriptors"]) else 2
            if function != 3:
                return 7
            self.setting_writes.append((feature, bytes(params[:2])))
            if not self.ignore_writes:
                info = bytearray(value["info"])
                info[6] = self.taken[feature].pop(0) if self.taken.get(feature) else params[1]
                value["info"] = bytes(info)
            return bytes(params[:2])
        if feature == device.BACKLIGHT_2:
            if function in (0, 2):
                return bytes(value["config" if function == 0 else "info"])
            if function != 1:
                return 7
            self.setting_writes.append((feature, bytes(params[:12])))
            if not self.ignore_writes:
                params = self.taken[feature].pop(0) if self.taken.get(feature) else bytes(params[:12])
                config = value["config"]
                config[0:2], config[5:14] = params[0:2], params[3:12]
                if params[1] >> 3 & 3 == 3 and len(value["info"]) > 1:  # a fixed level is the one shown
                    value["info"][1] = params[3]
            return bytes()
        if function not in (0, 1):
            return 7
        if feature == device.FN_INVERSION_HOSTS:
            host = params[0]
            if host not in value:
                return 2
            if function == 0:
                return bytes([host, *value[host]])
            self.setting_writes.append((feature, bytes(params[:2])))
            if not self.ignore_writes:
                value[host][0] = self.taken[feature].pop(0) if self.taken.get(feature) else params[1]
            return bytes(params[:2])
        if function == 0:
            return bytes(value)
        self.setting_writes.append((feature, bytes(params[:1])))
        if not self.ignore_writes:
            value[0] = self.taken[feature].pop(0) if self.taken.get(feature) else params[0]
        return bytes(params[:1])


class FakePort:
    """Stands in for hid.Port: a node with synthetic FakeDevices on its slots ({devidx: FakeDevice}). A receiver
    reports an empty slot with a HID++ 1.0 error at once; nothing else answers at all where there is no device."""

    def __init__(self, devices=None, receiver=True):
        self.devices = devices or {}
        self.receiver = receiver
        self.written = []          # every packet sent, in order
        self.writers = []          # the name of the thread each one was sent from
        self.fail_write = False    # the next write raises OSError
        self.in_flight = 0         # requests answered later, not delivered yet
        self.most_in_flight = 0
        self._out = queue.Queue()
        self._lock = threading.Lock()

    def write(self, packet):
        if self.fail_write:
            self.fail_write = False
            raise OSError("synthetic write failure")
        packet = bytes(packet)
        self.written.append(packet)
        self.writers.append(threading.current_thread().name)
        devidx, feat_idx, fn_byte = packet[1], packet[2], packet[3]
        dev = self.devices.get(devidx)
        if dev is None:
            reply = bytes([0x10, devidx, 0x8F, feat_idx, fn_byte, 0x08, 0]) if self.receiver else None
            delay = 0.0
        else:
            answer = dev.answer(feat_idx, fn_byte >> 4, packet[4:])
            delay = dev.delays.get(dev.features[feat_idx] if feat_idx < len(dev.features) else None, dev.delay)
            if answer is None:
                reply = None
            elif isinstance(answer, int):
                reply = bytes([0x11, devidx, 0xFF, feat_idx, fn_byte, answer]).ljust(20, b"\0")
            else:
                reply = bytes([0x11, devidx, feat_idx, fn_byte]) + bytes(answer).ljust(16, b"\0")
        if reply is None:
            return
        with self._lock:
            self.in_flight += 1
            self.most_in_flight = max(self.most_in_flight, self.in_flight)
        if delay:
            threading.Timer(delay, self._out.put, [("reply", reply)]).start()
        else:
            self._out.put(("reply", reply))

    def notify(self, data):
        """A report the device sends of its own accord."""
        self._out.put(("note", bytes(data)))

    def read(self):
        kind, data = self._out.get()
        if kind == "reply":
            with self._lock:
                self.in_flight -= 1
        return data

    def wake(self):
        self._out.put(("wake", b""))

    def close(self):
        pass

    def sent(self, devidx=None, feature_index=None, function=None):
        """The packets sent to a slot (and feature index, function)."""
        return [p for p in self.written if (devidx is None or p[1] == devidx) and (feature_index is None or p[2] == feature_index)
                and (function is None or p[3] >> 4 == function)]


def open_fake(port, path="/dev/synthetic0", receiver=True, on_notification=None, on_closed=None):
    """A real hidpp.Transport on a FakePort."""
    real = hidpp.hid.Port
    hidpp.hid.Port = lambda *_args, **_kwargs: port
    try:
        return hidpp.Transport(path, receiver, on_notification, on_closed)
    finally:
        hidpp.hid.Port = real


class FakeInput:
    """The virtual keyboard and mouse, as far as who holds what (vinput.Holds) and what reaches the system."""

    def __init__(self):
        self.sent = []  # (name, down)
        self._holds = vinput.Holds(lambda name, down: self.sent.append((name, down)))

    def press(self, names, owner="mouse"):
        self._holds.press(list(names), owner)

    def release(self, names, owner="mouse"):
        self._holds.release(list(names), owner)

    def tap(self, names):
        self._holds.tap(list(names), wait=0)

    def release_all(self, owner=None):
        self._holds.release_all(owner)

    def close(self):
        self.release_all()
