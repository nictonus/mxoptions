"""Virtual keyboard and mouse used to inject shortcuts, clicks and scrolling.

What is asked of them is the same on every system: keys and buttons by their Linux names
(keys.py), scrolling in units of 1/120 of a wheel detent, and a pointer that can be put
somewhere. How it is done is the system's own: uinput devices on Linux (vinput_uinput.py),
SendInput on Windows (vinput_windows.py)."""

import logging
import sys
import time

from . import keys

log = logging.getLogger(__name__)

MOUSE_BUTTONS = ("BTN_LEFT", "BTN_RIGHT", "BTN_MIDDLE", "BTN_SIDE", "BTN_EXTRA")
MODIFIERS = (
    "KEY_LEFTCTRL", "KEY_RIGHTCTRL", "KEY_LEFTSHIFT", "KEY_RIGHTSHIFT",
    "KEY_LEFTALT", "KEY_RIGHTALT", "KEY_LEFTMETA", "KEY_RIGHTMETA",
)
NOTCH = 120  # hi-res units per wheel detent

# US-layout typing table: character -> (key name, needs shift)
_PLAIN = "`1234567890-=[]\\;',./"
_SHIFTED = "~!@#$%^&*()_+{}|:\"<>?"
_PLAIN_KEYS = ("GRAVE", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "MINUS", "EQUAL", "LEFTBRACE", "RIGHTBRACE",
               "BACKSLASH", "SEMICOLON", "APOSTROPHE", "COMMA", "DOT", "SLASH")
ASCII_KEYS = {" ": ("SPACE", False), "\n": ("ENTER", False), "\t": ("TAB", False)}
ASCII_KEYS.update({c: (c.upper(), False) for c in "abcdefghijklmnopqrstuvwxyz"})
ASCII_KEYS.update({c.upper(): (c.upper(), True) for c in "abcdefghijklmnopqrstuvwxyz"})
ASCII_KEYS.update({c: (k, False) for c, k in zip(_PLAIN, _PLAIN_KEYS)})
ASCII_KEYS.update({c: (k, True) for c, k in zip(_SHIFTED, _PLAIN_KEYS)})


def key_code(name):
    return keys.code(name)


class _Wheel:
    """Turns hi-res deltas into REL_*_HI_RES plus whole-detent REL_* events.

    Mirrors the kernel's hid-logitech-hidpp scroll counter: the low-res click fires
    half-way to the next detent, and the remainder is dropped when the direction
    flips or the wheel has been still for a while."""

    def __init__(self, hi_axis, lo_axis):
        self.hi_axis = hi_axis
        self.lo_axis = lo_axis
        self.remainder = 0
        self.direction = 0
        self.last = 0.0

    def events(self, delta):
        now = time.monotonic()
        direction = 1 if delta > 0 else -1
        if direction != self.direction or now - self.last > 1.0:
            self.remainder = 0
        self.direction, self.last = direction, now
        out = [(self.hi_axis, delta)]
        self.remainder += delta
        if abs(self.remainder) >= NOTCH // 2:
            clicks = int(self.remainder / NOTCH) or direction
            out.append((self.lo_axis, clicks))
            self.remainder -= clicks * NOTCH
        return out


def _backend():
    if sys.platform == "win32":
        from . import vinput_windows as backend
    else:
        from . import vinput_uinput as backend
    return backend


def create_pointer():
    """Return a pointer that can be put somewhere on the desktop (see the backends), or None when it cannot be had."""
    try:
        return _backend().AbsolutePointer()
    except Exception as err:
        log.warning("no pointer of the program's own: %s", err)
        return None


def create():
    """Return a VirtualInput, or None when keys and clicks cannot be sent (on Linux: no access to /dev/uinput)."""
    try:
        return _backend().VirtualInput()
    except Exception as err:  # evdev raises UInputError / PermissionError / OSError
        log.warning("virtual input unavailable: %s", err)
        return None
