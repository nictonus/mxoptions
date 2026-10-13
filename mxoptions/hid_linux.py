"""HID devices on Linux: hidraw nodes (see hidpp.py, which talks through them).

A hidraw node carries every report of its interface, whatever its report ID, so one node serves
for all of HID++; a mouse's ordinary reports are on the same node (Bluetooth, cable) or on a sibling
node of the same receiver, and reading them only copies them: the desktop still gets them."""

import glob
import os
import select

LOGITECH_VID = 0x046D


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
