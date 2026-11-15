"""The few things asked of an X server directly, where the session is an X11 one.

Qt answers most questions about an X11 desktop; these it has no word for. Everything here says
"no" where there is no X server or no libX11 to ask it with."""

import ctypes
import ctypes.util
import logging
import os
import threading

log = logging.getLogger(__name__)

_lock = threading.Lock()
_lib = None
_display = None
_handler = None  # kept: Xlib calls it for as long as the program runs
CONTROL_MASK = 1 << 2  # ControlMask, X.h (Xlib manual, "Keyboard and Pointer Events")


def _open():
    """The connection to the X server (None if there is none)."""
    global _lib, _display, _handler
    if _display is not None or _lib is False:
        return _display
    _lib = False
    name = ctypes.util.find_library("X11")
    if not name or not os.environ.get("DISPLAY"):
        return None
    try:
        lib = ctypes.CDLL(name)
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XInternAtom.restype = ctypes.c_ulong
        lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XDefaultScreen.restype = ctypes.c_int
        lib.XDefaultScreen.argtypes = [ctypes.c_void_p]
        lib.XGetSelectionOwner.restype = ctypes.c_ulong
        lib.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        lib.XGetWindowProperty.restype = ctypes.c_int
        lib.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long, ctypes.c_int, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p)]
        lib.XFree.argtypes = [ctypes.c_void_p]
        lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        # Asking about a window that has just been closed is an error to Xlib, and its own answer to errors is
        # to end the program.
        kind = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        _handler = kind(lambda _display, _event: 0)
        lib.XSetErrorHandler.restype = ctypes.c_void_p
        lib.XSetErrorHandler.argtypes = [kind]
        lib.XSetErrorHandler(_handler)
        display = lib.XOpenDisplay(None)
        if not display:
            return None
        _lib, _display = lib, display
    except (OSError, AttributeError) as error:
        log.debug("no Xlib: %s", error)
    return _display


def _atom(name):
    return _lib.XInternAtom(_display, name.encode("ascii"), 0)


def _property(window, name):
    """The numbers in a property of a window (empty if it has none), whatever kind it says they are."""
    actual, size, count, left = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong()
    data = ctypes.c_void_p()
    status = _lib.XGetWindowProperty(_display, window, _atom(name), 0, 1024, 0, 0, ctypes.byref(actual), ctypes.byref(size),
                                     ctypes.byref(count), ctypes.byref(left), ctypes.byref(data))
    _lib.XSync(_display, 0)
    if status != 0 or not data.value:
        return []
    try:
        if size.value != 32:
            return []
        return list(ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong * count.value)).contents)  # "32 bits" come as longs
    finally:
        _lib.XFree(data)


def _string(window, name):
    """The bytes of a property of 8-bit units of a window (empty if it has none)."""
    actual, size, count, left = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong()
    data = ctypes.c_void_p()
    status = _lib.XGetWindowProperty(_display, window, _atom(name), 0, 1024, 0, 0, ctypes.byref(actual), ctypes.byref(size),
                                     ctypes.byref(count), ctypes.byref(left), ctypes.byref(data))
    _lib.XSync(_display, 0)
    if status != 0 or not data.value:
        return b""
    try:
        return ctypes.string_at(data, count.value) if size.value == 8 else b""
    finally:
        _lib.XFree(data)


def active_class():
    """The class of the application whose window has the focus: the second of the two strings in its WM_CLASS
    (ICCCM 4.1.2.5, "two consecutive null-terminated strings", type STRING), found through the root window's
    _NET_ACTIVE_WINDOW (EWMH). None where there is no X server, no such window or no class."""
    with _lock:
        if _open() is None:
            return None
        active = _property(_lib.XDefaultRootWindow(_display), "_NET_ACTIVE_WINDOW")
        if not active or not active[0]:
            return None
        parts = _string(active[0], "WM_CLASS").split(b"\0")
    return parts[1].decode("latin-1") if len(parts) > 1 and parts[1] else None


def compositing():
    """Whether windows can be see-through: a compositing manager is running."""
    with _lock:
        if _open() is None:
            return False
        return bool(_lib.XGetSelectionOwner(_display, _atom(f"_NET_WM_CM_S{_lib.XDefaultScreen(_display)}")))


def fullscreen_in_front():
    """Whether the window that has the focus fills the screen (a game, a film)."""
    with _lock:
        if _open() is None:
            return False
        active = _property(_lib.XDefaultRootWindow(_display), "_NET_ACTIVE_WINDOW")
        if not active or not active[0]:
            return False
        return _atom("_NET_WM_STATE_FULLSCREEN") in _property(active[0], "_NET_WM_STATE")


def available():
    """Whether there is an X server to ask."""
    with _lock:
        return _open() is not None


def ctrl_held():
    """Whether a Ctrl key is down now: ControlMask in what XQueryPointer says of the modifier keys (their logical
    state). None where there is no X server, or the pointer is on another screen. Every call into Xlib here holds
    _lock, so no two run at once from different threads, which the Xlib manual allows without XInitThreads."""
    with _lock:
        if _open() is None:
            return None
        query = _lib.XQueryPointer
        query.restype = ctypes.c_int
        query.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                          *[ctypes.POINTER(ctypes.c_int)] * 4, ctypes.POINTER(ctypes.c_uint)]
        windows = [ctypes.c_ulong() for _ in range(2)]
        places = [ctypes.c_int() for _ in range(4)]
        mask = ctypes.c_uint()
        same = query(_display, _lib.XDefaultRootWindow(_display), *map(ctypes.byref, windows + places), ctypes.byref(mask))
    return bool(mask.value & CONTROL_MASK) if same else None
