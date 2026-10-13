"""Rumble from games, played on the mouse's haptic motor.

Games vibrate game controllers, not mice. So, while this is switched on, the program adds a
virtual gamepad to the system whose only purpose is to be vibrated: what a game sends to it
(the rumble of an Xbox-style controller, through SDL, Steam Input or Proton) arrives here and is
turned into a strength between 0 and 1. The engine plays that on the mouse.

The pad has the buttons and sticks a game expects to see on a controller, and never touches them."""

import fcntl
import logging
import os
import select
import struct
import threading
import time

from evdev import AbsInfo, ecodes as e

from .vinput_uinput import _Device

log = logging.getLogger(__name__)

NAME = "MX Options rumble pad"
# Games recognise controllers by these numbers: an Xbox 360 pad is what every one of them knows.
VENDOR, PRODUCT, VERSION = 0x045E, 0x028E, 0x0110
MAX_EFFECTS = 16
WEAK_SHARE = 0.6   # the small, fast motor of a controller counts for this much next to the large one
NUDGE = (23000, 0.15)  # how far the left stick is moved to be noticed (of 32767), and for how long (s)
# The kernel hands every effect a game stores or drops to the pad's owner with these requests
# (struct uinput_ff_upload and uinput_ff_erase of <linux/uinput.h>, on 64-bit systems).
_BEGIN_UPLOAD, _END_UPLOAD, _UPLOAD_SIZE = 0xC06855C8, 0x406855C9, 104
_BEGIN_ERASE, _END_ERASE, _ERASE_SIZE = 0xC00C55CA, 0x400C55CB, 12
_EFFECT = 8        # where the effect starts in an upload request


def strength_of(effect):
    """How strong (0..1) a stored effect is: ``effect`` is a struct ff_effect as bytes."""
    kind = struct.unpack_from("H", effect, 0)[0]
    if kind == e.FF_RUMBLE:
        strong, weak = struct.unpack_from("HH", effect, 16)
        return min(1.0, max(strong, weak * WEAK_SHARE) / 65535.0)
    if kind == e.FF_PERIODIC:
        return min(1.0, abs(struct.unpack_from("h", effect, 20)[0]) / 32767.0)  # after waveform and period
    if kind == e.FF_CONSTANT:
        return min(1.0, abs(struct.unpack_from("h", effect, 16)[0]) / 32767.0)
    return 0.0


class Effects:
    """What a game has stored on the pad and what of it is playing: the bookkeeping, without the device."""

    def __init__(self):
        self._stored = {}    # effect id -> (strength, length in s or None for "until stopped")
        self._playing = {}   # effect id -> when it ends (None: when it is stopped)
        self.gain = 1.0

    def store(self, effect_id, effect):
        length = struct.unpack_from("H", effect, 10)[0]
        self._stored[effect_id] = (strength_of(effect), length / 1000.0 if length else None)

    def erase(self, effect_id):
        self._stored.pop(effect_id, None)
        self._playing.pop(effect_id, None)

    def play(self, effect_id, repeats, now):
        if repeats <= 0 or effect_id not in self._stored:
            self._playing.pop(effect_id, None)
            return
        length = self._stored[effect_id][1]
        self._playing[effect_id] = None if length is None else now + length * repeats

    def level(self, now):
        """The strength wanted right now, 0..1."""
        for effect_id in [i for i, end in self._playing.items() if end is not None and end <= now]:
            del self._playing[effect_id]
        strongest = max((self._stored[i][0] for i in self._playing if i in self._stored), default=0.0)
        return min(1.0, strongest * self.gain)

    def next_end(self):
        """When the next timed effect runs out (None if none does)."""
        return min((end for end in self._playing.values() if end is not None), default=None)


class RumblePad:
    """The virtual gamepad. ``on_level(strength)`` is called from the pad's own thread whenever the
    rumble a game asks for changes; 0 means silence."""

    def __init__(self, on_level):
        stick = AbsInfo(value=0, min=-32768, max=32767, fuzz=16, flat=128, resolution=0)
        trigger = AbsInfo(value=0, min=0, max=255, fuzz=0, flat=0, resolution=0)
        hat = AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0)
        self._dev = _Device({
            e.EV_KEY: [e.BTN_SOUTH, e.BTN_EAST, e.BTN_NORTH, e.BTN_WEST, e.BTN_TL, e.BTN_TR, e.BTN_SELECT, e.BTN_START,
                       e.BTN_MODE, e.BTN_THUMBL, e.BTN_THUMBR],
            e.EV_ABS: [(e.ABS_X, stick), (e.ABS_Y, stick), (e.ABS_RX, stick), (e.ABS_RY, stick), (e.ABS_Z, trigger),
                       (e.ABS_RZ, trigger), (e.ABS_HAT0X, hat), (e.ABS_HAT0Y, hat)],
            e.EV_FF: [e.FF_RUMBLE, e.FF_PERIODIC, e.FF_SINE, e.FF_SQUARE, e.FF_TRIANGLE, e.FF_CONSTANT, e.FF_GAIN],
        }, name=NAME, vendor=VENDOR, product=PRODUCT, version=VERSION, bustype=e.BUS_USB, max_effects=MAX_EFFECTS)
        self._on_level = on_level
        self._effects = Effects()
        self._level = 0.0
        self._wake_r, self._wake_w = os.pipe()
        self._closed = False
        self._thread = threading.Thread(target=self._loop, name="rumble", daemon=True)
        self._thread.start()

    def nudge(self):
        """Move the left stick aside for a moment and let it go. Browsers, and some games, take no notice of
        a controller until something on it moves; this is that something."""
        def run():
            try:
                for value in (NUDGE[0], 0):
                    self._dev.write(e.EV_ABS, e.ABS_X, value)
                    self._dev.syn()
                    time.sleep(NUDGE[1] if value else 0)
            except OSError:
                pass
        threading.Thread(target=run, name="rumble-nudge", daemon=True).start()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            os.write(self._wake_w, b"x")
        except OSError:
            pass
        self._thread.join(timeout=1.0)
        try:
            self._dev.close()
        except OSError:
            pass
        for fd in (self._wake_r, self._wake_w):
            try:
                os.close(fd)
            except OSError:
                pass

    def _loop(self):
        fd = self._dev.fd
        while not self._closed:
            end = self._effects.next_end()
            timeout = None if end is None else max(0.0, end - time.monotonic())
            try:
                readable, _, _ = select.select([fd, self._wake_r], [], [], timeout)
                if self._closed:
                    return
                if fd in readable:
                    for event in self._dev.read():
                        self._handle(fd, event)
            except (OSError, ValueError) as error:
                if not self._closed:
                    log.warning("rumble pad stopped: %s", error)
                return
            level = self._effects.level(time.monotonic())
            if level != self._level:
                self._level = level
                self._on_level(level)

    def _handle(self, fd, event):
        if event.type == e.EV_UINPUT and event.code == e.UI_FF_UPLOAD:
            # A game stores an effect and waits until this is answered.
            request = bytearray(_UPLOAD_SIZE)
            struct.pack_into("I", request, 0, event.value)
            fcntl.ioctl(fd, _BEGIN_UPLOAD, request)
            effect = bytes(request[_EFFECT:_EFFECT + 48])
            self._effects.store(struct.unpack_from("h", effect, 2)[0], effect)
            struct.pack_into("i", request, 4, 0)
            fcntl.ioctl(fd, _END_UPLOAD, request)
        elif event.type == e.EV_UINPUT and event.code == e.UI_FF_ERASE:
            request = bytearray(_ERASE_SIZE)
            struct.pack_into("I", request, 0, event.value)
            fcntl.ioctl(fd, _BEGIN_ERASE, request)
            self._effects.erase(struct.unpack_from("I", request, 8)[0])
            struct.pack_into("i", request, 4, 0)
            fcntl.ioctl(fd, _END_ERASE, request)
        elif event.type == e.EV_FF and event.code == e.FF_GAIN:
            self._effects.gain = max(0.0, min(1.0, event.value / 65535.0))
        elif event.type == e.EV_FF:
            self._effects.play(event.code, event.value, time.monotonic())


def create(on_level):
    """Return a RumblePad, or None when /dev/uinput is not usable."""
    try:
        return RumblePad(on_level)
    except Exception as error:
        log.warning("rumble pad unavailable: %s", error)
        return None
