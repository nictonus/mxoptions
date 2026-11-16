"""Typed access to the HID++ 2.0 features of an MX-class mouse."""

import hashlib
import logging
import struct

from .hidpp import HidppCancelled, HidppClosed, HidppError, HidppTimeout

log = logging.getLogger(__name__)

# Feature IDs
ROOT = 0x0000
FEATURE_SET = 0x0001
# Logitech's x0003 DeviceInformation: getDeviceInfo (function 0) answers the entity count (byte 0), the unit id
# (bytes 1-4: random, different for every unit of one model) and the model id (bytes 7-12). getFwInfo (function 1,
# the entity's index) answers its type (byte 0: 0 main application, 1 bootloader, 2 hardware), prefix (bytes 1-3,
# ASCII), number and revision (bytes 4 and 5) and build (bytes 6-7, big-endian), all three packed BCD, and in bit 0
# of byte 8 whether that entity is the one running.
DEVICE_INFO = 0x0003
MAX_FIRMWARE_ENTITIES = 8  # the most entities asked about; the document sets no limit
DEVICE_NAME = 0x0005
BATTERY_STATUS = 0x1000    # the battery of mice before the MX Master 3S: level and status, numbered as below
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
# Settings a keyboard keeps itself, named in Logitech's list of HID++ 2.0 features (github.com/Logitech/cpg-docs,
# hidpp20/README.rst); 0x1983, 0x1990 and 0x4531 only in Solaar's list of feature ids. That a keyboard lists one
# says it has the feature, not which values or effects it supports: they are shown as present, never changed.
BACKLIGHT_1 = 0x1981
BACKLIGHT_2 = 0x1982
BACKLIGHT_3 = 0x1983
ILLUMINATION = 0x1990
FN_INVERSION = 0x40A0
FN_INVERSION_DEFAULT = 0x40A2
FN_INVERSION_HOSTS = 0x40A3
LOCK_KEY_STATE = 0x4220
DISABLE_KEYS = 0x4521
DISABLE_KEYS_BY_USAGE = 0x4522
DUAL_PLATFORM = 0x4530
MULTI_PLATFORM = 0x4531
INTERNATIONAL_LAYOUTS = 0x4540
KEYBOARD_SETTINGS = (  # (name, features): a keyboard listing any of them has that setting
    ("fn", (FN_INVERSION, FN_INVERSION_DEFAULT, FN_INVERSION_HOSTS)),
    ("backlight", (BACKLIGHT_1, BACKLIGHT_2, BACKLIGHT_3, ILLUMINATION)),
    ("platform", (DUAL_PLATFORM, MULTI_PLATFORM, INTERNATIONAL_LAYOUTS)),
    ("disable", (DISABLE_KEYS, DISABLE_KEYS_BY_USAGE)),
    ("locks", (LOCK_KEY_STATE,)),
)

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

# What DEVICE_NAME says a device is (getDeviceType: 0 keyboard, 2 numpad, 3 mouse, 5 trackball, in the HID++ 2.0
# specification Logitech published)
KIND_KEYBOARD = 0
KIND_MOUSE = 3
KIND_TRACKBALL = 5

# Keyboard controls left to the keyboard itself: the three Easy-Switch keys and the ones of the Fn layer and its
# lock (Host_Switch_Channel_1..3, FN_Key, Fn_Inversion_Change, Fn_Inversion__Hot_Key and F_Lock in the list of
# control ids Solaar keeps).
CID_PROTECTED = frozenset({0x00D1, 0x00D2, 0x00D3, 0x0034, 0x00CD, 0x0101, 0x00DE})

# getCidReporting flags (Logitech's x1b04 SpecialKeysMseButtons): diverted, persistently diverted, raw XY diverted
REPORT_DIVERTED = 0x01
REPORT_PERSISTENT = 0x04
REPORT_RAW_XY = 0x10


def family(name):
    """Which picture fits a mouse called ``name``: "mx4" (the MX Master 4, also while no mouse is known),
    "master" (an earlier MX Master: the same build without the haptic panel) or "other"."""
    text = " ".join(name.lower().split())
    if not text or "mx master 4" in text:
        return "mx4"
    return "master" if "mx master" in text else "other"


class Control:
    def __init__(self, cid, tid, flags, pos=0):
        self.cid = cid
        self.tid = tid
        self.flags = flags
        self.pos = pos  # the F-key it is on (1 to 16), or 0: not said

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
        self._smartshift_feature = None  # SMART_SHIFT_ENHANCED, SMART_SHIFT or None
        self.wheel = None  # {"multiplier", "has_invert"}
        self.thumbwheel = None  # {"native_res", "diverted_res", "direction"}
        self.haptic_waveforms = []
        self.force = None  # {"min", "max", "default", "changeable"}
        self.host_count = 0
        self.pairing_read = False  # get_hosts() last read from the device itself which channels are paired
        self.firmware_entities = 0  # how many DEVICE_INFO tells the firmware of: byte 0 of its answer to identity()

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
                pos = info[5]  # getCidInfo: F-key position, 0 = not specified, 1 to 16 = F1 to F16
                self.controls[cid] = Control(cid, tid, info[4] | (info[8] << 8), pos if 1 <= pos <= 16 else 0)

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
            default = self.call(SMART_SHIFT, 0)[2]  # the mouse's own threshold; the ratchet force cannot be set
            self.smartshift = {"torque": False, "default_threshold": default if 0 < default <= 50 else 10, "default_torque": 0}

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

    def adopt(self, known):
        """Take over what was learned about this mouse on an earlier connection instead of asking for all
        of it again (dozens of requests, a second or two over Bluetooth). ``known`` is the Mouse loaded
        then. Returns False if this is not that mouse: its name or the number of its features differs."""
        t, d = self.transport, self.devidx
        try:
            feature_set = t.request(d, 0x00, 0, struct.pack("!H", FEATURE_SET))[0]
            name_index = known.features.get(DEVICE_NAME)
            if (name_index is None or feature_set != known.features.get(FEATURE_SET)
                    or t.request(d, feature_set, 0)[0] != len(known.features) - 1):
                return False
            length, name = t.request(d, name_index, 0)[0], b""
            while len(name) < length:
                chunk = t.request(d, name_index, 1, bytes([len(name)]))
                name += chunk[: length - len(name)]
            if name.decode("utf-8", "replace") != known.name:
                return False
        except (HidppError, IndexError):
            return False
        for field in ("features", "index_to_feature", "name", "kind", "controls", "dpi_range", "dpi_list", "dpi_default",
                      "smartshift", "_smartshift_feature", "wheel", "thumbwheel", "haptic_waveforms", "force", "host_count"):
            setattr(self, field, getattr(known, field))
        return True

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
        if self._smartshift_feature == SMART_SHIFT_ENHANCED:
            reply = self.call(SMART_SHIFT_ENHANCED, 1)
            return reply[0], reply[1], reply[2]
        reply = self.call(SMART_SHIFT, 0)
        return reply[0], reply[1], 0  # its third value is the default threshold, not a force

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

    def get_cid_reporting(self, cid):
        """How the control reports now: (flags, remap) of getCidReporting, see the REPORT_* flags. A remap
        other than 0 or the control's own id sends it as another control."""
        reply = self.call(REPROG_CONTROLS_V4, 2, *struct.pack("!H", cid))
        answered, flags, remap = struct.unpack("!HBH", reply[:5])
        if answered != cid:
            raise HidppError(f"reporting of control {answered:#06x} instead of {cid:#06x}")
        return flags, remap

    def identity(self):
        """What tells this unit from every other one, also of the same model, without saying what its ids are:
        16 hex digits of a digest of its model and unit id (DEVICE_INFO). None if the device does not say,
        or gives a unit id of all zeros or all ones."""
        if not self.has(DEVICE_INFO):
            return None
        try:
            reply = self.call(DEVICE_INFO, 0)
        except (HidppCancelled, HidppClosed):
            raise
        except HidppError as e:
            log.debug("no device information: %s", e)
            return None
        self.firmware_entities = reply[0] if len(reply) else 0
        unit, model = bytes(reply[1:5]), bytes(reply[7:13])
        if len(unit) != 4 or len(model) != 6 or unit in (b"\0" * 4, b"\xff" * 4):
            return None
        return hashlib.sha256(b"mxoptions keyboard\0" + model + unit).hexdigest()[:16]

    def get_firmware(self):
        """The version of the firmware running, as "71.3.21" (number, revision and build of the main application
        that says it is the active entity, see DEVICE_INFO), or None if the device does not say or an answer is not
        one. Asks about the entities identity() was told of. HidppCancelled and HidppClosed are passed on."""
        if not self.has(DEVICE_INFO):
            return None
        try:
            for index in range(min(self.firmware_entities, MAX_FIRMWARE_ENTITIES)):
                info = bytes(self.call(DEVICE_INFO, 1, index)[:9])
                digits = info[4:8].hex()
                if (len(info) == 9 and info[0] == 0 and info[8] & 0x01 and all(0x20 <= c <= 0x7E for c in info[1:4])
                        and digits.isdigit()):
                    return f"{int(digits[0:2])}.{int(digits[2:4])}.{int(digits[4:8])}"
        except (HidppCancelled, HidppClosed):
            raise
        except (HidppError, IndexError) as e:
            log.debug("no firmware version: %s", e)
        return None

    # -- haptics ----------------------------------------------------------

    def get_haptic(self):
        """Return (enabled, level 0-100)."""
        reply = self.call(HAPTIC, 1)
        return bool(reply[0] & 0x01), reply[1]

    def set_haptic(self, enabled, level):
        level = max(1, min(100, int(level)))
        self.call(HAPTIC, 2, 1 if enabled else 0, level)

    def play_haptic(self, waveform, wait=True):
        """Play a pattern. With ``wait`` off the mouse's answer is not waited for: for a row of
        pulses that must not hold anything else up when the mouse is slow to answer."""
        if waveform in self.haptic_waveforms:
            self.call(HAPTIC, 4, waveform, no_reply=not wait)

    def get_force(self):
        return struct.unpack("!H", self.call(FORCE_SENSING_BUTTON, 2, 0)[:2])[0]

    def set_force(self, value):
        value = max(self.force["min"], min(self.force["max"], int(value)))
        self.call(FORCE_SENSING_BUTTON, 3, *struct.pack("!BH", 0, value))
        return value

    # -- battery / hosts --------------------------------------------------

    def get_battery(self):
        """Return (percent, status)."""
        if self.has(UNIFIED_BATTERY):
            reply = self.call(UNIFIED_BATTERY, 1)
            return reply[0], reply[2]
        if self.has(BATTERY_STATUS):
            return battery_status(self.call(BATTERY_STATUS, 0))
        return None

    def get_hosts(self):
        """Return (current_host, [{"paired": bool, "name": str}, ...]). A channel the device does not say
        anything of counts as paired; ``pairing_read`` tells whether it said that of every one."""
        self.pairing_read = False
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
                    self.pairing_read = True
            except (HidppError, IndexError) as e:
                log.debug("host names unavailable: %s", e)
        return current, hosts

    def change_host(self, host):
        self.call(CHANGE_HOST, 1, host, no_reply=True)


def battery_status(data):
    """(percent, status) from what BATTERY_STATUS reports: [level, next level, status]. Its statuses
    0..4 are the BATTERY_* above; the rest are faults, shown as not charging."""
    return data[0], data[2] if data[2] <= BATTERY_SLOW_CHARGE else BATTERY_DISCHARGING


HAPTIC_SAVING = 10  # percent: at or below it battery saving keeps the mouse from vibrating; above it, it vibrates again


def saving_battery(enabled, battery):
    """Whether battery saving keeps the mouse from vibrating now: ``enabled`` (haptics.battery_saving) and the
    mouse's last ``battery`` (percent, status) at HAPTIC_SAVING percent or below, charging or not. A percent of 0
    tells no level, so it counts as unknown, as anything else that is no reading: vibration stays."""
    if enabled is not True or not isinstance(battery, (tuple, list)) or len(battery) != 2:
        return False
    return type(battery[0]) is int and 1 <= battery[0] <= HAPTIC_SAVING


def keyboard_settings(dev):
    """The KEYBOARD_SETTINGS names whose features ``dev`` lists, in that order. Asks the device nothing."""
    return [name for name, features in KEYBOARD_SETTINGS if any(dev.has(feature) for feature in features)]


def pointing(dev):
    """Whether a loaded device is a mouse or a trackball. Keyboards on the same receiver have reprogrammable
    keys too, so that alone does not say."""
    return dev.has(REPROG_CONTROLS_V4) and (dev.has(ADJUSTABLE_DPI) or dev.kind in (KIND_MOUSE, KIND_TRACKBALL))


def find_mouse(transport, known=None, hurry=False, others=None, skip=()):
    """Probe the transport's slots and return the first loaded HID++ 2.0 mouse, or None. ``known`` is
    a mouse loaded on an earlier connection: if it is the one found, what was learned then is reused.
    ``hurry``: the mouse is expected any moment, so do not wait long for an answer before asking again.
    ``others``: None, or a list that receives every other HID++ 2.0 device loaded on the way.
    ``skip``: slots not to ask at all (a keyboard is already known to be there)."""
    for devidx in transport.slots():
        if devidx in skip:
            continue
        version = transport.ping(devidx, timeout=0.4 if hurry else 1.5, retries=4 if hurry else 2)
        if not version or version[0] < 2:
            continue
        mouse = Mouse(transport, devidx)
        if known is not None and known.devidx == devidx and mouse.adopt(known):
            return mouse
        try:
            mouse.load()
        except (HidppError, IndexError, struct.error) as e:
            log.debug("slot %s did not load: %s", devidx, e)
            continue
        if pointing(mouse):
            return mouse
        if others is not None:
            others.append(mouse)
    return None


def probe_slot(transport, devidx):
    """Ask one slot once, without retries, what is there: ("empty" | "old" | "other" | "mouse" | "keyboard" |
    "asleep", the device loaded or None). "asleep": no answer in time, or a device that did not load; worth
    asking again later. HidppCancelled and HidppClosed are passed on."""
    try:
        reply = transport.request(devidx, 0x00, 1, bytes([0, 0, 0x5A]), timeout=1.0)  # ping, as Transport.ping
    except HidppTimeout:
        return "asleep", None
    except (HidppCancelled, HidppClosed):
        raise
    except HidppError:  # HidppOffline: nothing on that slot, or a HID++ 1.0 device
        return "empty", None
    if reply[0] < 2:
        return "old", None
    dev = Mouse(transport, devidx)
    try:
        dev.load()
    except (HidppCancelled, HidppClosed):
        raise
    except (HidppError, IndexError, struct.error) as e:
        log.debug("slot %s did not load: %s", devidx, e)
        return "asleep", None
    if pointing(dev):
        return "mouse", dev
    return ("keyboard" if dev.kind == KIND_KEYBOARD else "other"), dev
