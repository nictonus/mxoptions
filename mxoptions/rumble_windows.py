"""Rumble from games on Windows, played on the mouse's haptic motor (see rumble.py for the idea).

Windows lets a program add a game controller only through a driver made for that. The one used here
is ViGEmBus (https://github.com/nefarius/ViGEmBus), which the user installs once; the program talks
to it through its client library, ViGEmClient.dll, which comes with the Windows build. With both
there, a virtual Xbox 360 controller is added while rumble is switched on, and what a game sends to
its two motors arrives here as a strength between 0 and 1."""

import ctypes
import importlib.util
import logging
import os
import sys
import threading
import time

from . import paths

log = logging.getLogger(__name__)

LIBRARY = "ViGEmClient.dll"
DRIVER_PAGE = "https://github.com/nefarius/ViGEmBus/releases"
WEAK_SHARE = 0.6        # the small, fast motor of a controller counts for this much next to the large one
NUDGE = (23000, 0.15)   # how far the left stick is moved to be noticed (of 32767), and for how long (s)
_OK, _NO_BUS = 0x20000000, 0xE0000001
READY, NO_LIBRARY, NO_DRIVER = "ready", "no library", "no driver"


class _REPORT(ctypes.Structure):
    _fields_ = [("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short),
                ("sThumbRY", ctypes.c_short)]


def strength_of(large, small):
    """How strong (0..1) what a game asks of the two motors is: each from 0 to 255."""
    return min(1.0, max(large, small * WEAK_SHARE) / 255.0)


def library_path():
    """Where the client library is, or None: with the program (the Windows build carries it), where the program
    keeps its own files, in a source tree that has been built, or with the vgamepad package if that is installed."""
    here = os.path.dirname(os.path.abspath(__file__))
    places = [os.environ.get("MXOPTIONS_VIGEM") or "", os.path.join(here, "assets", LIBRARY), os.path.join(paths.DATA, LIBRARY),
              os.path.join(os.path.dirname(here), "build", "windows", LIBRARY)]
    try:
        found = importlib.util.find_spec("vgamepad")
        if found and found.origin:
            places.append(os.path.join(os.path.dirname(found.origin), "win", "vigem", "client", "x64", LIBRARY))
    except (ImportError, ValueError):
        pass
    return next((path for path in places if path and os.path.isfile(path)), None)


def _load():
    path = library_path()
    if path is None:
        return None
    lib = ctypes.CDLL(path)
    lib.vigem_alloc.restype = ctypes.c_void_p
    lib.vigem_free.argtypes = [ctypes.c_void_p]
    lib.vigem_connect.argtypes = [ctypes.c_void_p]
    lib.vigem_connect.restype = ctypes.c_uint
    lib.vigem_disconnect.argtypes = [ctypes.c_void_p]
    lib.vigem_target_x360_alloc.restype = ctypes.c_void_p
    lib.vigem_target_free.argtypes = [ctypes.c_void_p]
    lib.vigem_target_add.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.vigem_target_add.restype = ctypes.c_uint
    lib.vigem_target_remove.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.vigem_target_remove.restype = ctypes.c_uint
    lib.vigem_target_x360_update.argtypes = [ctypes.c_void_p, ctypes.c_void_p, _REPORT]
    lib.vigem_target_x360_update.restype = ctypes.c_uint
    lib.vigem_target_x360_register_notification.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    lib.vigem_target_x360_register_notification.restype = ctypes.c_uint
    lib.vigem_target_x360_unregister_notification.argtypes = [ctypes.c_void_p]
    return lib


def state():
    """Whether a controller can be added here: READY, NO_LIBRARY (the build is incomplete) or NO_DRIVER (ViGEmBus
    is not installed)."""
    try:
        lib = _load()
    except OSError:
        lib = None
    if lib is None:
        return NO_LIBRARY
    client = lib.vigem_alloc()
    try:
        answer = lib.vigem_connect(client)
        if answer == _OK:
            lib.vigem_disconnect(client)
            return READY
        return NO_DRIVER
    finally:
        lib.vigem_free(client)


class RumblePad:
    """The virtual controller. ``on_level(strength)`` is called from a thread of the driver's client whenever the
    rumble a game asks for changes; 0 means silence."""

    def __init__(self, on_level):
        self._lib = _load()
        if self._lib is None:
            raise OSError(f"{LIBRARY} is not there")
        self._on_level = on_level
        self._level = 0.0
        self._closed = False
        self._client = self._lib.vigem_alloc()
        answer = self._lib.vigem_connect(self._client)
        if answer != _OK:
            self._lib.vigem_free(self._client)
            raise OSError("the ViGEmBus driver is not installed" if answer == _NO_BUS else f"the controller driver said {answer:#x}")
        self._target = self._lib.vigem_target_x360_alloc()
        answer = self._lib.vigem_target_add(self._client, self._target)
        if answer != _OK:
            self._lib.vigem_target_free(self._target)
            self._lib.vigem_disconnect(self._client)
            self._lib.vigem_free(self._client)
            raise OSError(f"the controller could not be added ({answer:#x})")
        kind = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_void_p)
        self._heard = kind(self._motors)  # kept: the driver's client calls it for as long as the controller is there
        self._lib.vigem_target_x360_register_notification(self._client, self._target, self._heard, None)

    def _motors(self, _client, _target, large, small, _led, _data):
        level = strength_of(large, small)
        if level != self._level and not self._closed:
            self._level = level
            try:
                self._on_level(level)
            except Exception:
                log.exception("rumble")

    def nudge(self):
        """Move the left stick aside for a moment and let it go. Browsers, and some games, take no notice of
        a controller until something on it moves; this is that something."""
        def run():
            for value in (NUDGE[0], 0):
                if self._closed:
                    return
                self._lib.vigem_target_x360_update(self._client, self._target, _REPORT(0, 0, 0, value, 0, 0, 0))
                time.sleep(NUDGE[1] if value else 0)
        threading.Thread(target=run, name="rumble-nudge", daemon=True).start()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._lib.vigem_target_x360_unregister_notification(self._target)
        self._lib.vigem_target_remove(self._client, self._target)
        self._lib.vigem_target_free(self._target)
        self._lib.vigem_disconnect(self._client)
        self._lib.vigem_free(self._client)


def create(on_level):
    """Return a RumblePad, or None when a controller cannot be added (see state())."""
    if sys.platform != "win32":
        return None
    try:
        return RumblePad(on_level)
    except Exception as error:
        log.warning("rumble pad unavailable: %s", error)
        return None
