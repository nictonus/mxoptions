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


class Holds:
    """Who holds which key. The mouse and the keyboard each hold keys through the one virtual keyboard, and may
    hold the same one (both a Ctrl shortcut): ``emit(name, down)`` sends a key down when the first holder presses
    it and up only when the last one lets go. A holder is any string ("mouse", "keyboard")."""

    def __init__(self, emit):
        self._emit = emit
        self._held = {}  # name -> {owner: how many presses of that owner hold it}

    def press(self, names, owner):
        """Hold ``names`` for ``owner``. A press that fails holds nothing: each key it may have put down is let go
        of again (see release), as are the counts it added, and its own error is raised."""
        names = list(names)
        done = 0
        try:
            for name in names:
                holders = self._held.setdefault(name, {})
                first = not holders
                holders[owner] = holders.get(owner, 0) + 1
                done += 1  # before: a down that failed may have reached the system all the same
                if first:
                    self._emit(name, True)
        except BaseException:
            try:
                self.release(names[:done], owner)
            except BaseException:
                pass  # the press's own error is the one told
            raise

    def release(self, names, owner):
        """Let go of ``names`` for ``owner``. Each key whose last holder this was is sent up once, the others even
        when one fails; the first error is raised once all were tried. A key whose up failed is no longer held."""
        failed = None
        for name in reversed(list(names)):
            holders = self._held.get(name)
            if not holders or owner not in holders:
                continue  # never pressed by this owner: someone else's hold stays
            holders[owner] -= 1
            if not holders[owner]:
                del holders[owner]
            if not holders:
                del self._held[name]
                try:
                    self._emit(name, False)
                except BaseException as err:  # an interrupt too: the keys after it still go up
                    if failed is None:
                        failed = err
        if failed is not None:
            raise failed

    def tap(self, names, wait=0.012):
        """Press and release at once. A key someone holds already is left as it is: neither down nor up.
        Whatever goes wrong, each key this tap may have put down is sent up once, the others even when one fails.
        An error of a down or the wait is raised; otherwise the first error of an up, once all were tried."""
        names = [name for name in names if name not in self._held]
        pressed, failed = [], None
        try:
            for name in names:
                pressed.append(name)  # before: a down that failed may have reached the system all the same
                self._emit(name, True)
            if pressed and wait:
                time.sleep(wait)
        finally:
            for name in reversed(pressed):
                try:
                    self._emit(name, False)
                except BaseException as err:  # an interrupt too: the keys after it still go up
                    if failed is None:
                        failed = err
        if failed is not None:
            raise failed

    def release_all(self, owner=None):
        """Let go of what ``owner`` holds; with None, of everything (closing down). An up the system refuses
        (OSError) is let be; any other error is raised once every key was tried."""
        failed = None
        for name in reversed(list(self._held)):
            holders = self._held[name]
            if owner is None:
                holders.clear()
            else:
                holders.pop(owner, None)
            if not holders:
                del self._held[name]
                try:
                    self._emit(name, False)
                except OSError:
                    pass
                except BaseException as err:  # the keys after it still go up
                    if failed is None:
                        failed = err
        if failed is not None:
            raise failed

    def held(self):
        return set(self._held)


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
