"""HID devices on Linux: hidraw nodes (see hidpp.py, which talks through them).

A hidraw node carries every report of its interface, whatever its report ID, so one node serves
for all of HID++; a mouse's ordinary reports are on the same node (Bluetooth, cable) or on a sibling
node of the same receiver, and reading them only copies them: the desktop still gets them.

A keyboard on Bluetooth can also be asked, through the node already open, whether any of its keys is down
right now (Port.keys_held), for the keyboard following the mouse to another computer. Nothing else of what
it answers is kept: the answer is one yes or no."""

import fcntl
import glob
import logging
import os
import select
import sys

log = logging.getLogger(__name__)

LOGITECH_VID = 0x046D
HIDRAW_SYS = "/sys/class/hidraw"
MAX_DESCRIPTOR = 4096  # HID_MAX_DESCRIPTOR_SIZE: the kernel refuses anything longer
MAX_USAGES = 12288  # HID_MAX_USAGES


def _identity(sys_path):
    """(bus, vendor, product, uevent) of a hidraw sysfs directory. Raises OSError or ValueError."""
    with open(os.path.join(sys_path, "device", "uevent"), encoding="utf-8", errors="replace") as f:
        uevent = dict(line.strip().split("=", 1) for line in f.read(4096).splitlines() if "=" in line)
    bus, vid, pid = (int(x, 16) for x in uevent.get("HID_ID", "0:0:0").split(":"))
    return bus, vid, pid, uevent


def _descriptor(sys_path):
    """At most MAX_DESCRIPTOR + 1 bytes, so that a longer one shows. Raises OSError."""
    with open(os.path.join(sys_path, "device", "report_descriptor"), "rb") as f:
        return f.read(MAX_DESCRIPTOR + 1)


def is_hidpp_mouse_ble(desc):
    """Return True only if desc is a well-formed report descriptor of a HID++ mouse.

    Every item must be one that the HID 1.11 specification defines, complete within desc, and
    within the limits that the kernel enforces (hid-core.c): report IDs 1 to 255, Report Size up
    to 256, Report Count and the usages of one main item up to 12288, Push at most 4 deep.
    Collections and Push/Pop must balance. There must be a Generic Desktop Mouse application
    collection and report ID 0x11 declared under usage page 0xFF43, and no application collection
    may be a Generic Desktop Keyboard or Keypad. Usages are resolved as the kernel resolves them
    (complete_usage, hid_concatenate_last_usage_page, open_collection), and every main item
    clears them, as hid_parser_main does.

    This is used where BlueZ has not yet given the vendor. It is what the device says about
    itself, not proof of what it is."""
    if not desc or len(desc) > MAX_DESCRIPTOR:
        return False
    page, stack, depth, usages, count, usage_min = 0, [], 0, [], 0, 0
    mouse = hidpp = keyboard = False
    i, n = 0, len(desc)
    while i < n:
        b = desc[i]
        size = (0, 1, 2, 4)[b & 3]
        if i + 1 + size > n:
            return False
        value = int.from_bytes(desc[i + 1:i + 1 + size], "little")
        kind, tag = b & 0x0C, b & 0xFC
        i += 1 + size
        if kind == 0x04:  # global items
            if tag == 0x04:  # Usage Page
                page = value
            elif tag == 0x74:  # Report Size
                if value > 256:
                    return False
            elif tag == 0x84:  # Report ID
                if not 0 < value < 256:
                    return False
                hidpp = hidpp or (value == 0x11 and page == 0xFF43)
            elif tag == 0x94:  # Report Count
                if value > MAX_USAGES:
                    return False
            elif tag == 0xA4:  # Push
                if len(stack) == 4:
                    return False
                stack.append(page)
            elif tag == 0xB4:  # Pop
                if not stack:
                    return False
                page = stack.pop()
            elif tag > 0xB4:  # not defined
                return False
        elif kind == 0x08:  # local items
            if tag in (0x08, 0x28):  # Usage; Usage Maximum adds Usage Minimum to Maximum
                added = 1
                if tag == 0x28:
                    if usage_min > value:
                        return False
                    added, value = value - usage_min + 1, usage_min
                count += added
                if count > MAX_USAGES:
                    return False
                # A range is kept as its first usage: all of its usages share one page and size.
                usages.append([value if size > 2 else (value & 0xFFFF) | (page & 0xFFFF) << 16, size])
            elif tag == 0x18:  # Usage Minimum
                usage_min = value
            elif tag == 0x68 or tag >= 0xA8:  # not defined; Delimiter, never seen on these mice
                return False
        elif tag in (0x80, 0x90, 0xA0, 0xB0, 0xC0):  # main items
            for u in reversed(usages):
                if u[1] > 2:
                    continue
                if u[0] >> 16 == page:
                    break
                u[0] = (u[0] & 0xFFFF) | (page & 0xFFFF) << 16
            if tag == 0xA0:
                depth += 1
                if value & 0xFF == 0x01:  # Application
                    usage = usages[0][0] if usages else 0
                    mouse = mouse or usage == 0x00010002
                    keyboard = keyboard or usage in (0x00010006, 0x00010007)
            elif tag == 0xC0:
                if depth == 0:
                    return False
                depth -= 1
            usages, count, usage_min = [], 0, 0
        else:  # a main item tag that is not defined, the reserved item type, or a long item (0xFE)
            return False
    return depth == 0 and not stack and mouse and hidpp and not keyboard


def zero_id_hidpp(sys_path):
    """Return True for a Bluetooth hidraw node whose vendor and product are still 0 (BlueZ created
    it before reading the PnP ID) and whose report descriptor passes is_hidpp_mouse_ble. This only
    reads sysfs."""
    try:
        bus, vid, pid, _ = _identity(sys_path)
        return bus == 0x0005 and vid == 0 and pid == 0 and is_hidpp_mouse_ble(_descriptor(sys_path))
    except (OSError, ValueError):
        return False


# Asking a keyboard which of its keys are down. Each ask is one read of a report over Bluetooth, which
# bluetoothd records for the life of the keyboard's input device and replays at every reconnect (BlueZ
# src/shared/uhid.c): so there is a limit to how often a node is asked.
KEY_REPORTS_MAX = 4      # most key reports read per check; a keyboard with more is not asked at all
IOCTLS_PER_PORT = 32     # most reads per open node, failed ones too; in memory only, a new Port starts again
_HIDPP_REPORTS = {0x10: 7, 0x11: 20}  # report id -> bytes with the id: the HID++ short and long reports (hidpp.py)
_HIDPP_PAGES = (0xFF00, 0xFF43)       # the vendor pages HID++ nodes are found by (find_hidpp_nodes)
_UNSET = object()


def HIDIOCGINPUT(length):  # noqa: N802 - the name of the request in linux/hidraw.h
    """The ioctl request that reads the input report byte 0 names into a buffer of ``length`` bytes."""
    return (3 << 30) | (length << 16) | (ord("H") << 8) | 0x0A


def _system_usages(usages):
    """Generic Desktop usages that are keys: System Control up to the System Display ones (0x80-0xB7)."""
    return bool(usages) and all(0x80 <= low <= high <= 0xB7 for low, high in usages)


def key_reports(desc):
    """The input reports of a keyboard's descriptor that carry its keys: ((report id, 0 when it has none;
    bytes of the report with its id; mask of the key bits after the id), ...) in the order they are
    described. None for anything not exactly understood, or with any input that is neither a key nor
    HID++: such a keyboard is not asked at all, as a key there could be down unseen.

    A key field is on the Keyboard (0x07) or Consumer (0x0C) page, or Generic Desktop 0x80-0xB7 (system
    keys). Each of its bits is 0 while no key is down: a one-bit field 0 to 1, or a list of keys down whose
    value 0 means "none" (HID 1.11, 6.2.2.5: a value out of the logical range is no control; usage 0 of
    pages 0x07 and 0x0C is reserved, HID Usage Tables 1.6, sections 10 and 15). A HID++ report (vendor page,
    id and length of hidpp.py's own) is left out: it carries this program's own requests and answers, and the
    keys diverted to it are followed there."""
    page = logical_min = logical_max = size = count = 0
    report, numbered, depth = None, False, 0
    usages, low, fields = [], None, []   # usages: [(first, last)] of the main item to come, in order
    i = 0
    while i < len(desc):
        head = desc[i]
        if head == 0xFE:
            return None  # long item
        length = (0, 1, 2, 4)[head & 3]
        kind, tag = (head >> 2) & 3, head >> 4
        if i + 1 + length > len(desc):
            return None
        data = desc[i + 1:i + 1 + length]
        value = int.from_bytes(data, "little")
        i += 1 + length
        if kind == 1:  # global
            if tag == 0:
                page = value
            elif tag == 1:
                logical_min = int.from_bytes(data, "little", signed=True)
            elif tag == 2:
                logical_max = value
            elif tag in (3, 4, 5, 6):  # physical range, unit exponent, unit
                pass
            elif tag == 7:
                size = value
            elif tag == 8:
                if not 1 <= value <= 255:
                    return None
                report, numbered = value, True
            elif tag == 9:
                count = value
            else:
                return None  # push, pop, reserved
        elif kind == 2:  # local
            if tag in (0, 1, 2):
                if length == 4:
                    return None  # a usage with its page in it
                if tag == 0:
                    usages.append((value, value))
                elif tag == 1:
                    if low is not None:
                        return None
                    low = value
                else:
                    if low is None or low > value:
                        return None
                    usages.append((low, value))
                    low = None
            elif tag not in (3, 4, 5, 7, 8, 9):  # designators and strings mean nothing here; a delimiter does
                return None
        elif kind == 0:  # main
            if low is not None:
                return None
            if tag == 8:  # input
                if not (1 <= size <= 32 and 1 <= count <= 255):
                    return None
                fields.append((report, size, count, value, page, logical_min, logical_max, usages))
            elif tag == 10:  # collection
                depth += 1
                if depth > 16:
                    return None
            elif tag == 12:  # end collection
                if depth == 0:
                    return None
                depth -= 1
            elif tag not in (9, 11):  # output and feature: not what is read
                return None
            usages = []
        else:
            return None  # reserved item type
    if depth or (numbered and any(field[0] is None for field in fields)):
        return None
    reports = {}  # report id -> what its input fields come to
    for rid, size, count, flags, page, logical_min, logical_max, usages in fields:
        found = reports.setdefault(rid or 0, {"bits": 0, "mask": 0, "keys": False, "hidpp": False, "keyboard": False})
        start, width = found["bits"], size * count
        found["bits"] += width
        if flags & 0x01:
            continue  # constant: padding
        if page in _HIDPP_PAGES:
            found["hidpp"] = True
            continue
        if page in (0x07, 0x0C) or (page == 0x01 and _system_usages(usages)):
            if flags & 0x02:  # variable: one bit a key, 1 while down
                if size != 1 or logical_min != 0 or logical_max != 1:
                    return None
            elif not (1 <= size <= 16 and (logical_min >= 1 or (
                    logical_min == 0 and page in (0x07, 0x0C) and usages and usages[0][0] == 0))):
                return None  # a list of the keys down where 0 could name a key, or a value below 0
            found["mask"] |= ((1 << width) - 1) << start
            found["keys"] = True
            found["keyboard"] = found["keyboard"] or page == 0x07
            continue
        return None  # a pointer, buttons, a battery, another vendor's report: not a keyboard to ask
    out = []
    for rid, found in reports.items():
        if found["hidpp"]:
            if found["keys"] or found["bits"] % 8 or _HIDPP_REPORTS.get(rid) != found["bits"] // 8 + 1:
                return None
            continue
        if not found["keys"]:
            continue  # padding only
        total = found["bits"] // 8 + (1 if numbered else 0)
        if found["bits"] % 8 or total > 64:
            return None
        out.append((rid, total, found["mask"], found["keyboard"]))
    if not any(entry[3] for entry in out) or len(out) > KEY_REPORTS_MAX:
        return None
    return tuple(entry[:3] for entry in out)


def _keys_layout(path):
    """key_reports() of a node, if it is a Bluetooth LE device that BlueZ runs through uhid: the one way
    whose answers to HIDIOCGINPUT are known to be the report exactly as described, with its id first when
    it has one (Linux drivers/hid/uhid.c and hidraw.c, BlueZ src/shared/hog.c). None otherwise."""
    device = os.path.join("/sys/class/hidraw", os.path.basename(path), "device")
    try:
        with open(os.path.join(device, "uevent")) as f:
            uevent = dict(line.strip().split("=", 1) for line in f if "=" in line)
        bus = int(uevent.get("HID_ID", "").split(":")[0], 16)
        real = os.path.realpath(device)
    except (OSError, ValueError):
        return None
    if bus != 0x0005 or "/devices/virtual/misc/uhid/" not in real + "/":
        return None
    return key_reports(read_descriptor(path))


def find_hidpp_nodes():
    """Return [(devnode, is_receiver)] for Logitech hidraw nodes that speak HID++ long reports."""
    nodes = []
    for sys_path in sorted(glob.glob(os.path.join(HIDRAW_SYS, "hidraw*"))):
        try:
            bus, vid, pid, uevent = _identity(sys_path)
            if vid != LOGITECH_VID and not (bus == 0x0005 and vid == 0 and pid == 0):
                continue
            desc = _descriptor(sys_path)
        except (OSError, ValueError):
            continue
        if vid != LOGITECH_VID:  # Bluetooth, before BlueZ has read the vendor: only the strict check
            if not is_hidpp_mouse_ble(desc):
                continue
        else:
            # Vendor usage page (FF00 on USB, FF43 on Bluetooth) carrying report ID 0x11.
            vendor_page = b"\x06\x00\xff" in desc or b"\x06\x43\xff" in desc
            if not vendor_page or b"\x85\x11" not in desc:
                continue
        is_receiver = bus == 0x0003 and "receiver" in uevent.get("HID_NAME", "").lower()
        nodes.append((os.path.join("/dev", os.path.basename(sys_path)), is_receiver))
    return nodes


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


def short_name(path):
    return os.path.basename(path)


class Port:
    """One open node. ``read()`` waits for the next report and gives b"" once the port is closed or the
    device is gone; ``write()`` raises OSError if the report cannot be sent."""

    def __init__(self, path, write=True):
        self._fd = os.open(path, (os.O_RDWR if write else os.O_RDONLY) | os.O_NONBLOCK)
        self._wake_r, self._wake_w = os.pipe()
        self._closed = False
        self._path = path
        self._layout = _UNSET  # keys_layout(), once looked up
        self._ioctls = 0       # HIDIOCGINPUT asked on this node so far (see IOCTLS_PER_PORT)
        self._capped = False   # ... and the limit was reached, which the log was told

    def keys_layout(self):
        """The key reports to read for keys_held(), or None when this node is not one to ask (see _keys_layout)."""
        if self._layout is _UNSET:
            self._layout = _keys_layout(self._path)
        return self._layout

    def keys_held(self, stop):
        """Whether a key of the keyboard is down now: True, False, or None when that cannot be told. Reads each of
        its key reports once, through this node (never another): stops with None as soon as ``stop()`` says so,
        before the next read. False only when every one was read in full and has no key down. Blocks for as long as
        the reads take, up to 5 s each in the kernel: never call it from a thread that must not wait. What is read
        is wiped at once; only the answer leaves."""
        reports = self.keys_layout()
        if reports is None:
            return None
        if self._ioctls + len(reports) > IOCTLS_PER_PORT:
            if not self._capped:
                self._capped = True
                log.info("%s: asked which keys are down %d times already: not asked again until it is opened anew",
                         short_name(self._path), self._ioctls)
            return None
        for rid, total, mask in reports:
            if stop() or self._closed:
                return None
            buf = bytearray(total + 1)  # one byte more than the report: a longer answer shows
            buf[0] = rid
            self._ioctls += 1  # counted before: a read that fails was still asked of the keyboard
            try:
                try:
                    got = fcntl.ioctl(self._fd, HIDIOCGINPUT(len(buf)), buf, True)
                except OSError:
                    return None
                if got != total or (rid and buf[0] != rid):
                    return None
                with memoryview(buf) as view:
                    down = int.from_bytes(view[1:total] if rid else view[:total], "little") & mask
                if down:
                    return True
            finally:
                buf[:] = bytes(len(buf))
        return False

    def read(self):
        while not self._closed:
            try:
                readable, _, _ = select.select([self._fd, self._wake_r], [], [])
                if self._closed:
                    break
                if self._fd in readable:
                    return os.read(self._fd, 64)
            except (OSError, ValueError):
                break
        return b""

    def write(self, packet):
        os.write(self._fd, packet)

    def wake(self):
        """Make a waiting read() return (called before close(), from another thread)."""
        self._closed = True
        try:
            os.write(self._wake_w, b"x")
        except OSError:
            pass

    def close(self):
        self._closed = True
        for fd in (self._fd, self._wake_r, self._wake_w):
            try:
                os.close(fd)
            except OSError:
                pass


if __name__ == "__main__":
    # The udev helper of 70-mxoptions.rules is a root-owned copy of this file, run as
    # "python3 -I -B <copy> /sys/<devpath of the hidraw node>". It reads sysfs and writes nothing.
    # Exit status 0 grants access; 1 does not.
    try:
        ok = len(sys.argv) == 2 and zero_id_hidpp(sys.argv[1])
    except Exception:
        ok = False
    sys.exit(0 if ok else 1)
