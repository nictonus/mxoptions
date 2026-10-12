"""HID++ 2.0 transport over hidraw: discovery, request/response matching, notifications."""

import glob
import logging
import os
import select
import threading
import time

log = logging.getLogger(__name__)

LOGITECH_VID = 0x046D
REPORT_SHORT = 0x10
REPORT_LONG = 0x11
SHORT_LEN = 7
LONG_LEN = 20
DIRECT_INDEX = 0xFF  # device index used by Bluetooth / wired devices
RECEIVER_SLOTS = (1, 2, 3, 4, 5, 6)
SWID = 0x0D

ERROR_NAMES = {
    1: "unknown", 2: "invalid argument", 3: "out of range", 4: "hardware error",
    5: "internal error", 6: "invalid feature index", 7: "invalid function",
    8: "busy", 9: "unsupported",
}


class HidppError(Exception):
    pass


class HidppTimeout(HidppError):
    pass


class HidppFeatureError(HidppError):
    def __init__(self, code):
        super().__init__(f"HID++ error {code} ({ERROR_NAMES.get(code, '?')})")
        self.code = code


class HidppOffline(HidppError):
    """The receiver answered that nothing is reachable on that slot."""


class HidppClosed(HidppError):
    """The hidraw node went away (receiver unplugged, Bluetooth dropped)."""


def find_hidpp_nodes():
    """Return [(devnode, is_receiver)] for Logitech hidraw nodes that speak HID++ long reports."""
    nodes = []
    for sys_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(os.path.join(sys_path, "device", "uevent")) as f:
                uevent = dict(line.strip().split("=", 1) for line in f if "=" in line)
            bus, vid, pid = (int(x, 16) for x in uevent.get("HID_ID", "0:0:0").split(":"))
            if vid != LOGITECH_VID:
                continue
            with open(os.path.join(sys_path, "device", "report_descriptor"), "rb") as f:
                desc = f.read()
        except (OSError, ValueError):
            continue
        # Vendor usage page (FF00 on USB, FF43 on Bluetooth) carrying report ID 0x11.
        vendor_page = b"\x06\x00\xff" in desc or b"\x06\x43\xff" in desc
        if not vendor_page or b"\x85\x11" not in desc:
            continue
        is_receiver = bus == 0x0003 and "receiver" in uevent.get("HID_NAME", "").lower()
        nodes.append((os.path.join("/dev", os.path.basename(sys_path)), is_receiver))
    return nodes


class Transport:
    """One open hidraw node. A reader thread routes replies to the pending request and
    hands everything else to ``on_notification(devidx, sub_id, address, data)``."""

    def __init__(self, path, is_receiver, on_notification=None, on_closed=None):
        self.path = path
        self.is_receiver = is_receiver
        self.on_notification = on_notification
        self.on_closed = on_closed
        self._fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self._wake_r, self._wake_w = os.pipe()
        self._request_lock = threading.Lock()
        self._pending = None  # (devidx, feat_idx, fn_byte)
        self._reply = None
        self._reply_ready = threading.Event()
        self._closed = False
        # A mouse connected directly (Bluetooth, cable) sends its ordinary reports on this node too:
        # ``on_report(data)`` gets them (see DirectWatcher). A receiver sends those on another node.
        self.on_report = None
        self._thread = threading.Thread(target=self._read_loop, name=f"hidpp-{os.path.basename(path)}", daemon=True)
        self._thread.start()

    @property
    def closed(self):
        return self._closed

    def slots(self):
        return RECEIVER_SLOTS if self.is_receiver else (DIRECT_INDEX,)

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            os.write(self._wake_w, b"x")
        except OSError:
            pass
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=1.0)
        for fd in (self._fd, self._wake_r, self._wake_w):
            try:
                os.close(fd)
            except OSError:
                pass
        self._reply_ready.set()

    def _read_loop(self):
        while not self._closed:
            try:
                readable, _, _ = select.select([self._fd, self._wake_r], [], [])
                if self._closed:
                    return
                if self._fd not in readable:
                    continue
                data = os.read(self._fd, 64)
            except (OSError, ValueError):
                data = b""
            if not data:
                if not self._closed:
                    log.info("%s went away", self.path)
                    self._closed = True
                    self._reply_ready.set()
                    if self.on_closed:
                        self.on_closed(self)
                return
            try:
                self._dispatch(data)
            except Exception:
                log.exception("failed to handle packet %s", data.hex(" "))

    def _dispatch(self, data):
        if data[0] not in (REPORT_SHORT, REPORT_LONG):
            on_report = self.on_report
            if on_report:
                on_report(data)
            return
        if len(data) < 4:
            return
        devidx, sub_id, address = data[1], data[2], data[3]
        pending = self._pending
        if pending and devidx == pending[0]:
            # HID++ 2.0 error: FF, feature index, function|swid, error code
            if sub_id == 0xFF and len(data) >= 6 and (data[3], data[4]) == pending[1:]:
                self._deliver(("error", data[5]))
                return
            # HID++ 1.0 error (how a receiver reports an empty/unreachable slot)
            if sub_id == 0x8F and len(data) >= 6 and (data[3], data[4]) == pending[1:]:
                self._deliver(("error1", data[5]))
                return
            if (sub_id, address) == pending[1:]:
                self._deliver(("ok", bytes(data[4:])))
                return
        if sub_id in (0x8F, 0xFF):
            return  # an error for someone else's request
        # Feature notifications carry software id 0; receiver notifications (>= 0x40) use
        # the address byte for other purposes.
        if sub_id >= 0x40 or (address & 0x0F) == 0:
            if self.on_notification:
                self.on_notification(devidx, sub_id, address, bytes(data[4:]))

    def _deliver(self, reply):
        self._reply = reply
        self._pending = None
        self._reply_ready.set()

    def request(self, devidx, feat_idx, function, params=b"", timeout=1.5, no_reply=False):
        """Send a feature request and return the reply payload (16 bytes for long reports)."""
        fn_byte = ((function & 0x0F) << 4) | SWID
        packet = bytes([REPORT_LONG, devidx, feat_idx, fn_byte]) + bytes(params)
        if len(packet) > LONG_LEN:
            raise ValueError("HID++ payload too long")
        packet = packet.ljust(LONG_LEN, b"\0")
        with self._request_lock:
            if self._closed:
                raise HidppClosed(self.path)
            self._reply = None
            self._reply_ready.clear()
            self._pending = None if no_reply else (devidx, feat_idx, fn_byte)
            try:
                os.write(self._fd, packet)
            except OSError as e:
                self._pending = None
                raise HidppClosed(f"{self.path}: {e}") from e
            if no_reply:
                return None
            if not self._reply_ready.wait(timeout):
                self._pending = None
                raise HidppTimeout(f"no reply from device {devidx} feature index {feat_idx:#04x} fn {function}")
            if self._closed and self._reply is None:
                raise HidppClosed(self.path)
            kind, value = self._reply
        if kind == "ok":
            return value
        if kind == "error1":
            raise HidppOffline(f"slot {devidx}: error {value:#04x}")
        raise HidppFeatureError(value)

    def ping(self, devidx, timeout=1.0, retries=0):
        """Return the (major, minor) HID++ protocol version, or None if nothing answers.

        A sleeping device often swallows the packet that wakes it, hence ``retries``;
        an empty receiver slot is reported at once and never retried."""
        for _attempt in range(retries + 1):
            mark = int(time.monotonic() * 1000) & 0xFF or 1
            try:
                reply = self.request(devidx, 0x00, 1, bytes([0, 0, mark]), timeout=timeout)
            except HidppTimeout:
                continue
            except (HidppOffline, HidppFeatureError):
                return None
            return reply[0], reply[1]
        return None


def find_mouse_report_node(hidpp_path):
    """The receiver exposes ordinary mouse reports on a sibling hidraw node; return its path or None."""
    def usb_device(name):
        return os.path.realpath(os.path.join("/sys/class/hidraw", name, "device", "..", ".."))

    own = os.path.basename(hidpp_path)
    try:
        parent = usb_device(own)
        for sys_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
            name = os.path.basename(sys_path)
            if name == own or usb_device(name) != parent:
                continue
            with open(os.path.join(sys_path, "device", "report_descriptor"), "rb") as f:
                desc = f.read()
            # Generic Desktop / Mouse collection that uses report ID 2 (the receivers' fixed layout)
            if desc.startswith(b"\x05\x01\x09\x02") and b"\x85\x02" in desc[:12]:
                return os.path.join("/dev", name)
    except OSError:
        pass
    return None


def read_descriptor(node):
    """The HID report descriptor of a hidraw node (b"" if it cannot be read)."""
    try:
        with open(os.path.join("/sys/class/hidraw", os.path.basename(node), "device", "report_descriptor"), "rb") as f:
            return f.read()
    except OSError:
        return b""


def mouse_layout(desc):
    """Where things sit in the mouse reports (report ID 2) a descriptor describes: {"wheel": index of
    the wheel's byte in a report, counting the report ID, "x" and "y": (first bit after the report
    ID, number of bits)}, each None if not found. Receivers and Bluetooth differ in how wide X and Y are."""
    found = {"wheel": None, "x": None, "y": None}
    page = size = count = report = 0
    usages, bits, i = [], {}, 0
    while i < len(desc):
        head = desc[i]
        length = (head & 3) if (head & 3) < 3 else 4
        value = int.from_bytes(desc[i + 1:i + 1 + length], "little")
        tag = head & 0xFC
        if tag == 0x04:
            page = value
        elif tag == 0x74:
            size = value
        elif tag == 0x94:
            count = value
        elif tag == 0x84:
            report = value
        elif tag == 0x08:
            usages.append(value)
        elif tag == 0x80:  # an input field: which of its items are X, Y and the wheel of Generic Desktop?
            start = bits.get(report, 0)
            for item in range(count):
                usage = usages[min(item, len(usages) - 1)] if usages else None
                offset = start + item * size
                if report != 2 or page != 0x01:
                    continue
                if usage == 0x38 and size == 8 and offset % 8 == 0 and found["wheel"] is None:
                    found["wheel"] = 1 + offset // 8
                elif usage in (0x30, 0x31) and 8 <= size <= 16:
                    axis = "x" if usage == 0x30 else "y"
                    if found[axis] is None:
                        found[axis] = (offset, size)
            bits[report] = start + count * size
        if tag in (0x80, 0x90, 0xB0, 0xA0, 0xC0):  # main items end the local usage list
            usages = []
        i += 1 + length
    return found


def wheel_index(mouse_node):
    """Where the scroll wheel sits in the mouse reports of ``mouse_node``: the index of its byte in a
    report (counting the report ID), or None."""
    return mouse_layout(read_descriptor(mouse_node))["wheel"]


def _signed(report, field):
    """The value of a (first bit, number of bits) field of a report, as the signed number it is."""
    offset, size = field
    value = (int.from_bytes(report[1:], "little") >> offset) & ((1 << size) - 1)
    return value - (1 << size) if value >> (size - 1) else value


class MouseReports:
    """Makes sense of the mouse's ordinary reports (report ID 2, which starts with the buttons): calls
    ``on_change(self, bitmask)`` when a button changes and, if asked, ``on_wheel(self, notches, when)``
    for every movement of the scroll wheel. Bit 0 is the left button, then right, middle, back, forward.
    While ``on_motion`` is set, it is called with (self, dx, dy, when) for every movement of the mouse,
    in its own counts (right and down are positive).

    ``node`` is the hidraw node the reports come from; its descriptor says where things are."""

    def __init__(self, node, on_change, on_wheel=None):
        layout = mouse_layout(read_descriptor(node))
        self._on_change = on_change
        self._on_wheel = on_wheel
        self._wheel = layout["wheel"] if on_wheel else None
        self._xy = (layout["x"], layout["y"]) if layout["x"] and layout["y"] else None
        self._buttons = 0
        self.on_motion = None
        self.last_input = None  # when the mouse last moved, clicked or scrolled (None until it does)

    @property
    def tracks_motion(self):
        return self._xy is not None

    def feed(self, data):
        if data[0] != 0x02 or len(data) < 3:
            return
        self.last_input = time.monotonic()
        buttons = data[1] | (data[2] << 8)
        if buttons != self._buttons:
            self._buttons = buttons
            self._on_change(self, buttons)
        if self._wheel is not None and len(data) > self._wheel and data[self._wheel]:
            notches = data[self._wheel]
            self._on_wheel(self, notches - 256 if notches > 127 else notches, self.last_input)
        on_motion = self.on_motion
        if on_motion is not None and self._xy is not None:
            dx, dy = _signed(data, self._xy[0]), _signed(data, self._xy[1])
            if dx or dy:
                on_motion(self, dx, dy, self.last_input)


class DirectWatcher(MouseReports):
    """The reports of a mouse connected over Bluetooth or a cable, taken from the transport that
    already reads its node."""

    def __init__(self, transport, on_change, on_wheel=None):
        super().__init__(transport.path, on_change, on_wheel)
        self._transport = transport
        transport.on_report = self.feed

    def close(self):
        if self._transport.on_report == self.feed:
            self._transport.on_report = None


class ButtonWatcher(MouseReports):
    """The reports of a mouse on a receiver, read from the receiver's mouse node. Reading hidraw
    only copies the reports; the desktop still receives them."""

    def __init__(self, path, on_change, on_wheel=None):
        super().__init__(path, on_change, on_wheel)
        self.path = path
        self._fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self._wake_r, self._wake_w = os.pipe()
        self._closed = False
        self._thread = threading.Thread(target=self._loop, name="buttons", daemon=True)
        self._thread.start()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            os.write(self._wake_w, b"x")
        except OSError:
            pass
        self._thread.join(timeout=1.0)
        for fd in (self._fd, self._wake_r, self._wake_w):
            try:
                os.close(fd)
            except OSError:
                pass

    def _loop(self):
        while not self._closed:
            try:
                readable, _, _ = select.select([self._fd, self._wake_r], [], [])
                if self._closed or self._fd not in readable:
                    continue
                data = os.read(self._fd, 64)
            except (OSError, ValueError):
                return
            if not data:
                return
            self.feed(data)
