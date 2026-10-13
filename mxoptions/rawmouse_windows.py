"""A mouse's own movement, buttons and wheel on Windows (see hidpp.watch_mouse).

On Linux these are read along from the mouse's HID reports. Windows keeps that collection to
itself, but hands any program that asks the "raw input" of every mouse: what each device reported,
before pointer speed and acceleration are applied, and marked with the device it came from. That
is the same information. It is asked for through a window that is never shown, on a thread of its
own that runs that window's messages.

What is sent with SendInput (by this program or any other) comes marked as from no device, and is
left out: only what a hand does to the mouse counts."""

import ctypes
import ctypes.wintypes as w
import logging
import os
import struct
import sys
import threading
import time

log = logging.getLogger(__name__)

WM_INPUT, WM_QUIT = 0x00FF, 0x0012
RID_INPUT, RIDI_DEVICENAME, RIDEV_INPUTSINK, RIM_TYPEMOUSE = 0x10000003, 0x20000007, 0x00000100, 0
MOVE_ABSOLUTE = 0x01
WHEEL, HWHEEL = 0x0400, 0x0800
# usButtonFlags: for each button of ours (left, right, middle, back, forward) the bit that says it went down;
# the next higher bit says it came up.
BUTTON_DOWN = (0x0001, 0x0004, 0x0010, 0x0040, 0x0100)
NOTCH = 120
# A raw-input record of a mouse, on 64-bit Windows: its header (kind, size, the device, a parameter), then the
# mouse's part: flags, two bytes of nothing, button flags, button data, raw buttons, x, y, extra.
_HEADER = struct.Struct("<IIQQ")
_BODY = struct.Struct("<HxxHhIiiI")


def device_key(path):
    """What the collections of one device have in common in their paths: the part that names the device, up to
    where its interface or collection is told. Two collections belong to the same mouse (or to the same
    receiver) if this is equal."""
    lowered = path.lower()
    named = lowered.split("#")[1] if lowered.count("#") >= 2 else lowered
    for mark in ("&mi_", "&col"):
        named = named.split(mark)[0]
    return named


def parse(buffer):
    """What a raw-input record of a mouse says: (device handle, dx, dy, button flags, button data), or None if
    it is no mouse's record. A record that tells a position instead of a movement counts as no movement."""
    if len(buffer) < _HEADER.size + _BODY.size:
        return None
    kind, _size, device, _parameter = _HEADER.unpack_from(buffer)
    if kind != RIM_TYPEMOUSE:
        return None
    flags, button_flags, button_data, _raw, dx, dy, _extra = _BODY.unpack_from(buffer, _HEADER.size)
    if flags & MOVE_ABSOLUTE:
        dx = dy = 0
    return device, dx, dy, button_flags, button_data


if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


    class _WNDCLASS(ctypes.Structure):
        _fields_ = [("cbSize", w.UINT), ("style", w.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", w.HINSTANCE), ("hIcon", w.HANDLE), ("hCursor", w.HANDLE),
                    ("hbrBackground", w.HANDLE), ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR), ("hIconSm", w.HANDLE)]


    class _RAWINPUTDEVICE(ctypes.Structure):
        _fields_ = [("usUsagePage", w.USHORT), ("usUsage", w.USHORT), ("dwFlags", w.DWORD), ("hwndTarget", w.HWND)]


    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(_WNDCLASS)]
    user32.RegisterClassExW.restype = w.ATOM
    user32.CreateWindowExW.restype = w.HWND
    user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       w.HWND, w.HANDLE, w.HINSTANCE, ctypes.c_void_p]
    user32.DestroyWindow.argtypes = [w.HWND]
    user32.UnregisterClassW.argtypes = [w.LPCWSTR, w.HINSTANCE]
    user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(_RAWINPUTDEVICE), w.UINT, w.UINT]
    user32.GetRawInputData.restype = w.UINT
    user32.GetRawInputData.argtypes = [w.HANDLE, w.UINT, ctypes.c_void_p, ctypes.POINTER(w.UINT), w.UINT]
    user32.GetRawInputDeviceInfoW.restype = w.UINT
    user32.GetRawInputDeviceInfoW.argtypes = [w.HANDLE, w.UINT, ctypes.c_void_p, ctypes.POINTER(w.UINT)]
    user32.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
    user32.PostThreadMessageW.argtypes = [w.DWORD, w.UINT, w.WPARAM, w.LPARAM]
    kernel32.GetModuleHandleW.restype = w.HINSTANCE
    kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]


class RawMouse:
    """Follows the mouse whose HID++ collection is at ``path``: calls ``on_change(self, bitmask)`` when one of its
    buttons changes (bit 0 is the left button, then right, middle, back, forward) and, if asked,
    ``on_wheel(self, notches, when)`` for every notch of its wheel. While ``on_motion`` is set, it is called
    with (self, dx, dy, when) for every movement, in the mouse's own counts (right and down are positive)."""

    tracks_motion = True

    def __init__(self, path, on_change, on_wheel=None):
        self._key = device_key(path)
        self._on_change = on_change
        self._on_wheel = on_wheel
        self._any = bool(os.environ.get("MXOPTIONS_RAW_ANY"))  # take every mouse, and what programs send: for trying this out
        self._buttons = 0
        self._wheel = 0
        self._devices = {}   # device handle -> whether it is our mouse
        self.on_motion = None
        self.last_input = None
        self._thread_id = None
        self._ready = threading.Event()
        self._failed = None
        self._thread = threading.Thread(target=self._run, name="raw-mouse", daemon=True)
        self._thread.start()
        self._ready.wait(3.0)
        if self._failed:
            raise OSError(self._failed)

    def close(self):
        if self._thread_id is not None:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout=1.0)

    def _ours(self, device):
        if self._any:
            return True
        if not device:
            return False  # sent by a program, not moved by a hand
        known = self._devices.get(device)
        if known is None:
            size = w.UINT(0)
            user32.GetRawInputDeviceInfoW(device, RIDI_DEVICENAME, None, ctypes.byref(size))
            name = ctypes.create_unicode_buffer(size.value + 1)
            got = user32.GetRawInputDeviceInfoW(device, RIDI_DEVICENAME, name, ctypes.byref(size))
            known = self._devices[device] = bool(got and got != 0xFFFFFFFF and device_key(name.value) == self._key)
        return known

    def _record(self, buffer):
        seen = parse(buffer)
        if seen is None or not self._ours(seen[0]):
            return
        _device, dx, dy, flags, data = seen
        now = self.last_input = time.monotonic()
        buttons = self._buttons
        for bit, down in enumerate(BUTTON_DOWN):
            if flags & down:
                buttons |= 1 << bit
            if flags & (down << 1):
                buttons &= ~(1 << bit)
        if buttons != self._buttons:
            self._buttons = buttons
            self._on_change(self, buttons)
        if flags & WHEEL and self._on_wheel is not None:
            self._wheel += data  # smooth wheels send less than a notch at a time
            notches = int(self._wheel / NOTCH)
            if notches:
                self._wheel -= notches * NOTCH
                self._on_wheel(self, notches, now)
        on_motion = self.on_motion
        if on_motion is not None and (dx or dy):
            on_motion(self, dx, dy, now)

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        instance = kernel32.GetModuleHandleW(None)
        name = f"MXOptionsRawMouse{self._thread_id}"

        def procedure(window, message, wparam, lparam):
            if message == WM_INPUT:
                try:
                    size = w.UINT(0)
                    user32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size), _HEADER.size)
                    if 0 < size.value <= 4096:
                        buffer = ctypes.create_string_buffer(size.value)
                        if user32.GetRawInputData(lparam, RID_INPUT, buffer, ctypes.byref(size), _HEADER.size) == size.value:
                            self._record(buffer.raw)
                except Exception:
                    log.exception("raw input")
            return user32.DefWindowProcW(window, message, wparam, lparam)

        keep = WNDPROC(procedure)  # Windows calls it for as long as the window lives
        window_class = _WNDCLASS()
        window_class.cbSize = ctypes.sizeof(_WNDCLASS)
        window_class.lpfnWndProc = keep
        window_class.hInstance = instance
        window_class.lpszClassName = name
        window = None
        try:
            if not user32.RegisterClassExW(ctypes.byref(window_class)):
                raise OSError(ctypes.get_last_error(), "no window class")
            window = user32.CreateWindowExW(0, name, name, 0, 0, 0, 0, 0, w.HWND(-3), None, instance, None)  # a message-only window
            if not window:
                raise OSError(ctypes.get_last_error(), "no window")
            wanted = _RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, window)  # generic desktop, mouse; also while in the background
            if not user32.RegisterRawInputDevices(ctypes.byref(wanted), 1, ctypes.sizeof(wanted)):
                raise OSError(ctypes.get_last_error(), "raw input was not given")
        except OSError as error:
            self._failed = str(error)
            self._ready.set()
            if window:
                user32.DestroyWindow(window)
            return
        self._ready.set()
        message = w.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.DispatchMessageW(ctypes.byref(message))
        user32.DestroyWindow(window)
        user32.UnregisterClassW(name, instance)
