"""Virtual keyboard, mouse and pointer on Linux: uinput devices (see vinput.py)."""

import threading
import time

from evdev import AbsInfo, UInput, ecodes as e

from .vinput import MOUSE_BUTTONS, ASCII_KEYS, _Wheel, key_code


class _Device(UInput):
    """A virtual device that is only written to. python-evdev would also open the new device's
    event node to read it back, and waits two seconds for the permission to do so, which an
    ordinary user never gets."""

    def __init__(self, *args, **kwargs):
        # No force feedback unless asked for: python-evdev would announce room for effects on every device,
        # and a device that claims force feedback is not taken for the plain keyboard or pointer it is.
        kwargs.setdefault("max_effects", 0)
        super().__init__(*args, **kwargs)

    def _find_device(self, *_args):
        return None


class VirtualInput:
    def __init__(self):
        keys = [code for code in e.keys if 0 < code < 0x100]  # everything below BTN_MISC
        self._kbd = _Device({e.EV_KEY: keys}, name="MX Options virtual keyboard", vendor=0x1D6B, product=0x4D58)
        self._mouse = _Device(
            {
                e.EV_KEY: [getattr(e, b) for b in MOUSE_BUTTONS],
                e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL, e.REL_WHEEL_HI_RES, e.REL_HWHEEL_HI_RES],
            },
            name="MX Options virtual mouse", vendor=0x1D6B, product=0x4D59,
        )
        self._lock = threading.Lock()
        self._vwheel = _Wheel(e.REL_WHEEL_HI_RES, e.REL_WHEEL)
        self._hwheel = _Wheel(e.REL_HWHEEL_HI_RES, e.REL_HWHEEL)
        self._held = set()

    def close(self):
        self.release_all()
        for dev in (self._kbd, self._mouse):
            try:
                dev.close()
            except OSError:
                pass

    def _set(self, names, value):
        for name in names:
            code = key_code(name)
            if code is None:
                continue
            dev = self._mouse if name in MOUSE_BUTTONS else self._kbd
            dev.write(e.EV_KEY, code, value)
            dev.syn()
            (self._held.add if value else self._held.discard)(name)

    def press(self, names):
        with self._lock:
            self._set(list(names), 1)

    def release(self, names):
        with self._lock:
            self._set(list(reversed(list(names))), 0)

    def tap(self, names):
        names = list(names)
        with self._lock:
            self._set(names, 1)
            time.sleep(0.012)
            self._set(list(reversed(names)), 0)

    def type_ascii(self, text):
        """Type text key by key. Only right on a US layout; unknown characters are skipped."""
        for char in text:
            entry = ASCII_KEYS.get(char)
            if entry is None:
                continue
            name = f"KEY_{entry[0]}"
            self.tap(["KEY_LEFTSHIFT", name] if entry[1] else [name])
            time.sleep(0.004)

    def release_all(self):
        with self._lock:
            if self._held:
                try:
                    self._set(list(self._held), 0)
                except OSError:
                    pass

    def scroll(self, delta):
        """Vertical scroll in hi-res units (120 = one detent, positive = up)."""
        self._emit(self._vwheel, delta)

    def hscroll(self, delta):
        """Horizontal scroll in hi-res units (positive = right)."""
        self._emit(self._hwheel, delta)

    def _emit(self, wheel, delta):
        delta = int(delta)
        if not delta:
            return
        with self._lock:
            for code, value in wheel.events(delta):
                self._mouse.write(e.EV_REL, code, value)
            self._mouse.syn()


class AbsolutePointer:
    """A virtual pointer that says where on the desktop it is instead of how far it moved, which
    is the one way a program can put the pointer somewhere on Wayland. The compositor spreads its
    range over the whole desktop, as it does for the pointer of a virtual machine."""

    RANGE = 0x10000

    def __init__(self):
        axis = AbsInfo(value=0, min=0, max=self.RANGE - 1, fuzz=0, flat=0, resolution=0)
        # The buttons are never pressed; they are what makes this a pointer in the eyes of the system. It takes
        # exactly these three: with fewer the kernel lists the device as a joystick, and gamepad testers show it.
        self._dev = _Device({e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE], e.EV_ABS: [(e.ABS_X, axis), (e.ABS_Y, axis)]},
                            name="MX Options virtual pointer", vendor=0x1D6B, product=0x4D5A)
        self._last = None

    def move(self, point, desktop):
        """Put the pointer at ``point`` (x, y) of the ``desktop`` (x, y, width, height), in its pixels."""
        left, top, width, height = desktop
        x = int((min(max(point[0] - left, 0), width - 1) + 0.5) * self.RANGE / width)
        y = int((min(max(point[1] - top, 0), height - 1) + 0.5) * self.RANGE / height)
        if (x, y) == self._last:  # the same place as last time is no event to the system: step aside first
            self._write(x + (1 if x < self.RANGE - 1 else -1), y)
        self._write(x, y)
        self._last = (x, y)

    def _write(self, x, y):
        self._dev.write(e.EV_ABS, e.ABS_X, x)
        self._dev.write(e.EV_ABS, e.ABS_Y, y)
        self._dev.syn()

    def close(self):
        try:
            self._dev.close()
        except OSError:
            pass
