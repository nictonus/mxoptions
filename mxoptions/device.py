"""Typed access to the HID++ 2.0 features of an MX-class mouse."""

import logging
import struct

from .hidpp import HidppError

log = logging.getLogger(__name__)

# Feature IDs
ROOT = 0x0000
FEATURE_SET = 0x0001
DEVICE_NAME = 0x0005
UNIFIED_BATTERY = 0x1004
HAPTIC = 0x19B0
FORCE_SENSING_BUTTON = 0x19C0
REPROG_CONTROLS_V4 = 0x1B04
CHANGE_HOST = 0x1814
HOSTS_INFO = 0x1815
WIRELESS_DEVICE_STATUS = 0x1D4B
SMART_SHIFT = 0x2110
SMART_SHIFT_ENHANCED = 0x2111
HIRES_WHEEL = 0x2121
THUMB_WHEEL = 0x2150
ADJUSTABLE_DPI = 0x2201

# Control IDs
CID_LEFT = 0x0050
CID_RIGHT = 0x0051
CID_MIDDLE = 0x0052
CID_BACK = 0x0053
CID_FORWARD = 0x0056
CID_GESTURE = 0x00C3
CID_SMARTSHIFT = 0x00C4
CID_HAPTIC_PANEL = 0x01A0

# getCidInfo capability bits (bytes 4 and 8 read as one field)
KEY_DIVERTABLE = 0x20
KEY_RAW_XY = 0x100

WHEEL_FREESPIN = 1
WHEEL_RATCHET = 2
ALWAYS_RATCHET = 0xFF

BATTERY_DISCHARGING = 0
BATTERY_CHARGING = 1
BATTERY_ALMOST_FULL = 2
BATTERY_FULL = 3
BATTERY_SLOW_CHARGE = 4

# Waveform IDs of the HAPTIC feature
WAVEFORMS = {
    0x00: "Sharp state change",
    0x01: "Damp state change",
    0x02: "Sharp collision",
    0x03: "Damp collision",
    0x04: "Subtle collision",
    0x05: "Happy alert",
    0x06: "Angry alert",
    0x07: "Completed",
    0x08: "Square",
    0x09: "Wave",
    0x0A: "Firework",
    0x0B: "Mad",
    0x0C: "Knock",
    0x0D: "Jingle",
    0x0E: "Ringing",
    0x1B: "Whisper collision",
}
WAVE_TICK = 0x1B
WAVE_SUBTLE = 0x04
WAVE_STATE = 0x01
WAVE_CONFIRM = 0x00
WAVE_COMPLETED = 0x07


class Control:
    def __init__(self, cid, tid, flags):
        self.cid = cid
        self.tid = tid
        self.flags = flags

    @property
    def divertable(self):
        return bool(self.flags & KEY_DIVERTABLE)

    @property
    def raw_xy(self):
        return bool(self.flags & KEY_RAW_XY)


class Mouse:
    """One HID++ 2.0 device reachable through ``transport`` at ``devidx``."""

    def __init__(self, transport, devidx):
        self.transport = transport
        self.devidx = devidx
        self.features = {}  # feature id -> index
        self.index_to_feature = {}
        self.name = "Logitech mouse"
        self.kind = None
        self.controls = {}  # cid -> Control
        self.dpi_range = None  # (min, max, step)
        self.dpi_list = None
        self.dpi_default = 1000
        self.smartshift = None  # {"torque": bool, "default_threshold", "default_torque"}
        self.wheel = None  # {"multiplier", "has_invert"}
        self.thumbwheel = None  # {"native_res", "diverted_res", "direction"}
        self.haptic_waveforms = []
        self.force = None  # {"min", "max", "default", "changeable"}
        self.host_count = 0

    # -- plumbing ---------------------------------------------------------

    def has(self, feature):
        return feature in self.features

    def call(self, feature, function, *params, no_reply=False, timeout=1.5):
        index = self.features[feature]
        return self.transport.request(self.devidx, index, function, bytes(params), timeout=timeout, no_reply=no_reply)

    def load(self):
        """Enumerate features and read the static capabilities."""
        t, d = self.transport, self.devidx
        feature_set = t.request(d, 0x00, 0, struct.pack("!H", FEATURE_SET))[0]
        count = t.request(d, feature_set, 0)[0]
        self.features = {ROOT: 0}
        for index in range(1, count + 1):
            reply = t.request(d, feature_set, 1, bytes([index]))
            self.features[struct.unpack("!H", reply[:2])[0]] = index
        self.index_to_feature = {index: fid for fid, index in self.features.items()}

        if self.has(DEVICE_NAME):
            length = self.call(DEVICE_NAME, 0)[0]
            name = b""
            while len(name) < length:
                chunk = self.call(DEVICE_NAME, 1, len(name))
                name += chunk[: length - len(name)]
            self.name = name.decode("utf-8", "replace")
            self.kind = self.call(DEVICE_NAME, 2)[0]

        if self.has(REPROG_CONTROLS_V4):
            for i in range(self.call(REPROG_CONTROLS_V4, 0)[0]):
                info = self.call(REPROG_CONTROLS_V4, 1, i)
                cid, tid = struct.unpack("!HH", info[:4])
                self.controls[cid] = Control(cid, tid, info[4] | (info[8] << 8))

        if self.has(ADJUSTABLE_DPI):
            self._load_dpi()

        self._smartshift_feature = next((f for f in (SMART_SHIFT_ENHANCED, SMART_SHIFT) if self.has(f)), None)
        if self._smartshift_feature == SMART_SHIFT_ENHANCED:
            caps = self.call(SMART_SHIFT_ENHANCED, 0)
            self.smartshift = {
                "torque": bool(caps[0] & 0x01),
                "default_threshold": caps[1],
                "default_torque": caps[2],
            }
        elif self._smartshift_feature == SMART_SHIFT:
            self.smartshift = {"torque": False, "default_threshold": 10, "default_torque": 0}

        if self.has(HIRES_WHEEL):
            caps = self.call(HIRES_WHEEL, 0)
            self.wheel = {"multiplier": caps[0] or 8, "has_invert": bool(caps[1] & 0x08),
                          "has_ratchet": bool(caps[1] & 0x04),  # it reports the mode being switched
                          "notches": caps[2] or 24}             # ratchet steps in one turn

        if self.has(THUMB_WHEEL):
            info = self.call(THUMB_WHEEL, 0)
            native, diverted = struct.unpack("!HH", info[:4])
            # direction: what to multiply a reported rotation by so that rolling the wheel up (which
            # scrolls right when the mouse handles it itself) comes out positive
            self.thumbwheel = {"native_res": native or 20, "diverted_res": diverted or 120,
                               "direction": 1 if info[4] else -1}

        if self.has(HAPTIC):
            mask = int.from_bytes(self.call(HAPTIC, 0)[4:8], "big")
            self.haptic_waveforms = [w for w in WAVEFORMS if mask & (1 << w)]

        if self.has(FORCE_SENSING_BUTTON) and self.call(FORCE_SENSING_BUTTON, 0)[0] >= 1:
            changeable, default, maximum, minimum = struct.unpack("!HHHH", self.call(FORCE_SENSING_BUTTON, 1, 0)[:8])
            self.force = {"changeable": bool(changeable & 1), "default": default, "min": minimum, "max": maximum}

        if self.has(CHANGE_HOST):
            self.host_count = self.call(CHANGE_HOST, 0)[0]
        return self

    def _load_dpi(self):
        raw = self.call(ADJUSTABLE_DPI, 1, 0)[1:]
        values, step = [], None
        for i in range(0, len(raw) - 1, 2):
            value = struct.unpack("!H", raw[i:i + 2])[0]
            if value == 0:
                break
            if value >> 13 == 0b111:
                step = value & 0x1FFF
            else:
                values.append(value)
        if step and len(values) >= 2:
            self.dpi_range = (values[0], values[-1], step)
        elif values:
            self.dpi_list = sorted(values)
            self.dpi_range = (self.dpi_list[0], self.dpi_list[-1], 0)
        reply = self.call(ADJUSTABLE_DPI, 2, 0)
        self.dpi_default = struct.unpack("!H", reply[3:5])[0] or 1000

    # -- pointer ----------------------------------------------------------

    def get_dpi(self):
        return struct.unpack("!H", self.call(ADJUSTABLE_DPI, 2, 0)[1:3])[0]

    def set_dpi(self, dpi):
        low, high, step = self.dpi_range
        dpi = max(low, min(high, int(dpi)))
        if step:
            dpi = low + round((dpi - low) / step) * step
        elif self.dpi_list:
            dpi = min(self.dpi_list, key=lambda v: abs(v - dpi))
        self.call(ADJUSTABLE_DPI, 3, 0, *struct.pack("!H", dpi))
        return dpi

    # -- main wheel -------------------------------------------------------

    def get_smartshift(self):
        """Return (mode, auto_disengage, torque)."""
        fn = 1 if self._smartshift_feature == SMART_SHIFT_ENHANCED else 0
        reply = self.call(self._smartshift_feature, fn)
        return reply[0], reply[1], reply[2]

    def set_smartshift(self, mode, auto_disengage=0, torque=0):
        fn = 2 if self._smartshift_feature == SMART_SHIFT_ENHANCED else 1
        if self._smartshift_feature == SMART_SHIFT_ENHANCED:
            self.call(self._smartshift_feature, fn, mode, auto_disengage, torque)
        else:
            self.call(self._smartshift_feature, fn, mode, auto_disengage)

    def get_wheel_mode(self):
        """Return (diverted, hires, inverted)."""
        mode = self.call(HIRES_WHEEL, 1)[0]
        return bool(mode & 0x01), bool(mode & 0x02), bool(mode & 0x04)

    def get_ratchet(self):
        """True while the wheel's ratchet is engaged, False in free spin."""
        return bool(self.call(HIRES_WHEEL, 3)[0] & 0x01)

    def set_wheel_mode(self, diverted, hires, inverted):
        self.call(HIRES_WHEEL, 2, (0x01 if diverted else 0) | (0x02 if hires else 0) | (0x04 if inverted else 0))

    # -- thumb wheel ------------------------------------------------------

    def get_thumbwheel(self):
        reply = self.call(THUMB_WHEEL, 1)
        return bool(reply[0] & 0x01), bool(reply[1] & 0x01)

    def set_thumbwheel(self, diverted, inverted):
        self.call(THUMB_WHEEL, 2, 1 if diverted else 0, 1 if inverted else 0)

    # -- buttons ----------------------------------------------------------

    def set_cid_reporting(self, cid, divert, raw_xy=False):
        control = self.controls.get(cid)
        if control is None or not control.divertable:
            return False
        flags = 0x02 | (0x01 if divert else 0)
        if control.raw_xy:
            flags |= 0x20 | (0x10 if divert and raw_xy else 0)
        self.call(REPROG_CONTROLS_V4, 3, *struct.pack("!HBH", cid, flags, 0))
        return True

    # -- haptics ----------------------------------------------------------

    def get_haptic(self):
        """Return (enabled, level 0-100)."""
        reply = self.call(HAPTIC, 1)
        return bool(reply[0] & 0x01), reply[1]

    def set_haptic(self, enabled, level):
        level = max(1, min(100, int(level)))
        self.call(HAPTIC, 2, 1 if enabled else 0, level)

    def play_haptic(self, waveform):
        if waveform in self.haptic_waveforms:
            self.call(HAPTIC, 4, waveform)

    def get_force(self):
        return struct.unpack("!H", self.call(FORCE_SENSING_BUTTON, 2, 0)[:2])[0]

    def set_force(self, value):
        value = max(self.force["min"], min(self.force["max"], int(value)))
        self.call(FORCE_SENSING_BUTTON, 3, *struct.pack("!BH", 0, value))
        return value

    # -- battery / hosts --------------------------------------------------

    def get_battery(self):
        """Return (percent, status)."""
        if not self.has(UNIFIED_BATTERY):
            return None
        reply = self.call(UNIFIED_BATTERY, 1)
        return reply[0], reply[2]

    def get_hosts(self):
        """Return (current_host, [{"paired": bool, "name": str}, ...])."""
        count, current = self.call(CHANGE_HOST, 0)[:2]
        hosts = [{"paired": True, "name": ""} for _ in range(count)]
        if self.has(HOSTS_INFO):
            try:
                if self.call(HOSTS_INFO, 0)[0] & 0x01:
                    for host in range(count):
                        info = self.call(HOSTS_INFO, 1, host)
                        hosts[host]["paired"] = bool(info[1])
                        length, name = info[4], b""
                        while len(name) < length:
                            piece = self.call(HOSTS_INFO, 3, host, len(name))[2:]
                            name += piece[: length - len(name)]
                        hosts[host]["name"] = name.decode("utf-8", "replace").strip("\0 ")
            except (HidppError, IndexError) as e:
                log.debug("host names unavailable: %s", e)
        return current, hosts

    def change_host(self, host):
        self.call(CHANGE_HOST, 1, host, no_reply=True)


def find_mouse(transport):
    """Probe the transport's slots and return the first loaded HID++ 2.0 mouse, or None."""
    for devidx in transport.slots():
        version = transport.ping(devidx, timeout=1.5, retries=2)
        if not version or version[0] < 2:
            continue
        mouse = Mouse(transport, devidx)
        try:
            mouse.load()
        except (HidppError, IndexError, struct.error) as e:
            log.debug("slot %s did not load: %s", devidx, e)
            continue
        if mouse.has(REPROG_CONTROLS_V4) and mouse.has(ADJUSTABLE_DPI):
            return mouse
    return None

