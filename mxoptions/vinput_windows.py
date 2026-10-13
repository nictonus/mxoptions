"""Virtual keyboard, mouse and pointer on Windows: SendInput (see vinput.py).

What is sent goes to whatever window has the focus on the desktop of the session this program runs
in, exactly like the uinput devices on Linux. Windows does not let a program reach windows of
programs that run with higher rights (an administrator's) than its own."""

import ctypes
import ctypes.wintypes as w
import threading
import time

from . import keys, windows
from .vinput import MOUSE_BUTTONS

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEY_EXTENDED, KEY_UP, KEY_UNICODE, KEY_SCANCODE = 0x1, 0x2, 0x4, 0x8
MOUSE_MOVE, MOUSE_WHEEL, MOUSE_HWHEEL, MOUSE_VIRTUAL_DESK, MOUSE_ABSOLUTE = 0x0001, 0x0800, 0x1000, 0x4000, 0x8000
# button name -> (flag when pressed, flag when released, which of the two extra buttons)
BUTTONS = {"BTN_LEFT": (0x0002, 0x0004, 0), "BTN_RIGHT": (0x0008, 0x0010, 0), "BTN_MIDDLE": (0x0020, 0x0040, 0),
           "BTN_SIDE": (0x0080, 0x0100, 1), "BTN_EXTRA": (0x0080, 0x0100, 2)}
BRIGHTNESS = {"KEY_BRIGHTNESSUP": windows.BRIGHTNESS_STEP, "KEY_BRIGHTNESSDOWN": -windows.BRIGHTNESS_STEP}
MARK = 0x4D584F50  # goes with everything sent from here ("MXOP"), so that it can be told from what a hand does


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD), ("dwFlags", w.DWORD), ("time", w.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", w.DWORD), ("u", _UNION)]


user32.SendInput.argtypes = [w.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
user32.SendInput.restype = w.UINT


def _send(*inputs):
    batch = (_INPUT * len(inputs))(*inputs)
    if user32.SendInput(len(inputs), batch, ctypes.sizeof(_INPUT)) != len(inputs):
        raise OSError(ctypes.get_last_error(), "Windows did not take the input (is this program on the desktop of a signed-in session?)")


def _key(name, down):
    """The input for a key or a mouse button going down or up, or None if Windows has no such key."""
    if name in BUTTONS:
        pressed, released, extra = BUTTONS[name]
        return _INPUT(INPUT_MOUSE, _UNION(mi=_MOUSEINPUT(0, 0, extra, pressed if down else released, 0, MARK)))
    how = keys.windows_key(name)
    if how is None:
        return None
    flags = 0 if down else KEY_UP
    if how[0] == "vk":
        return _INPUT(INPUT_KEYBOARD, _UNION(ki=_KEYBDINPUT(how[1], 0, flags, 0, MARK)))
    return _INPUT(INPUT_KEYBOARD, _UNION(ki=_KEYBDINPUT(0, how[1], flags | KEY_SCANCODE | (KEY_EXTENDED if how[2] else 0), 0, MARK)))


class VirtualInput:
    def __init__(self):
        self._lock = threading.Lock()
        self._held = set()
        _send(_INPUT(INPUT_MOUSE, _UNION(mi=_MOUSEINPUT(0, 0, 0, MOUSE_MOVE, 0, MARK))))  # a move by nothing: can anything be sent?

    def close(self):
        self.release_all()

    def _set(self, names, down):
        for name in names:
            if name in BRIGHTNESS:  # Windows has no key for it: the screens are told directly
                if down:
                    windows.brightness.change(BRIGHTNESS[name])
                continue
            made = _key(name, down)
            if made is None:
                continue
            _send(made)
            (self._held.add if down else self._held.discard)(name)

    def press(self, names):
        with self._lock:
            self._set(list(names), True)

    def release(self, names):
        with self._lock:
            self._set(list(reversed(list(names))), False)

    def tap(self, names):
        names = list(names)
        with self._lock:
            self._set(names, True)
            time.sleep(0.012)
            self._set(list(reversed(names)), False)

    def type_ascii(self, text):
        """Type text. Windows takes characters as such, so this is right on every layout and for every script."""
        units = text.replace("\r\n", "\n").encode("utf-16-le")
        with self._lock:
            for index in range(0, len(units), 2):
                unit = int.from_bytes(units[index:index + 2], "little")
                if unit == 0x0A:  # a new line is the Enter key, not a character
                    self._set(["KEY_ENTER"], True)
                    self._set(["KEY_ENTER"], False)
                else:
                    _send(_INPUT(INPUT_KEYBOARD, _UNION(ki=_KEYBDINPUT(0, unit, KEY_UNICODE, 0, MARK))),
                          _INPUT(INPUT_KEYBOARD, _UNION(ki=_KEYBDINPUT(0, unit, KEY_UNICODE | KEY_UP, 0, MARK))))
                time.sleep(0.002)

    def release_all(self):
        with self._lock:
            if self._held:
                try:
                    self._set(list(self._held), False)
                except OSError:
                    pass

    def scroll(self, delta):
        """Vertical scroll in hi-res units (120 = one detent, positive = up)."""
        self._wheel(MOUSE_WHEEL, delta)

    def hscroll(self, delta):
        """Horizontal scroll in hi-res units (positive = right)."""
        self._wheel(MOUSE_HWHEEL, delta)

    def _wheel(self, flag, delta):
        delta = int(delta)
        if delta:
            with self._lock:
                _send(_INPUT(INPUT_MOUSE, _UNION(mi=_MOUSEINPUT(0, 0, delta & 0xFFFFFFFF, flag, 0, MARK))))


class AbsolutePointer:
    """Puts the pointer somewhere on the desktop."""

    RANGE = 0x10000

    def move(self, point, desktop):
        """Put the pointer at ``point`` (x, y) of the ``desktop`` (x, y, width, height), in whatever units both are in."""
        left, top, width, height = desktop
        x = int((min(max(point[0] - left, 0), width - 1) + 0.5) * self.RANGE / width)
        y = int((min(max(point[1] - top, 0), height - 1) + 0.5) * self.RANGE / height)
        _send(_INPUT(INPUT_MOUSE, _UNION(mi=_MOUSEINPUT(x, y, 0, MOUSE_MOVE | MOUSE_ABSOLUTE | MOUSE_VIRTUAL_DESK, 0, MARK))))

    def close(self):
        pass


assert set(BUTTONS) == set(MOUSE_BUTTONS)
