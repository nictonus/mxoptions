"""How a movement made with the gesture button held becomes a direction.

Shared by the engine (which decides) and the settings page (which draws the same zones)."""

import math

TRIGGERS = ("release", "auto", "repeat")
DIRECTIONS = ("up", "down", "left", "right")
# Moments of a gesture that can each have their own vibration pattern (a waveform id, or None for nothing):
#   armed  - the movement has left the press zone: from here on it is a swipe
#   turn   - the direction the movement points at has changed
#   swipe  - a swipe was recognised (or one pattern per direction, see "per_direction")
#   repeat - every further repeat of it
#   tap    - a plain press
#   reject - the gesture was ignored or cancelled
CUES = ("armed", "turn", "swipe", "repeat", "tap", "reject")
# Ready-made sets, by waveform id (see device.WAVEFORMS)
CUE_PRESETS = {
    "quiet": {"armed": None, "turn": None, "swipe": 0x1B, "repeat": 0x1B, "tap": None, "reject": None},
    "normal": {"armed": None, "turn": None, "swipe": 0x04, "repeat": 0x1B, "tap": None, "reject": None},
    "rich": {"armed": 0x1B, "turn": 0x1B, "swipe": 0x02, "repeat": 0x04, "tap": 0x04, "reject": 0x06},
}
# A swipe may fire before the button is released only when it is at least this many degrees clear of
# the diagonal: a movement that is still ambiguous is better judged as a whole, on release.
INSTANT_DEAD = 20.0


def angle(x, y):
    """Degrees between the movement and the horizontal axis: 0 is sideways, 90 straight up or down."""
    return math.degrees(math.atan2(abs(y), abs(x)))


def split(bias):
    """The angle where horizontal turns into vertical. ``bias`` > 0 gives sideways swipes more room."""
    return 45.0 + bias


def direction(x, y, dead=0.0, bias=0.0):
    """"left", "right", "up" or "down" for a movement (y grows downwards), or None when it falls
    into the dead zone of ``dead`` degrees on either side of the diagonal."""
    if not x and not y:
        return None
    theta, boundary = angle(x, y), split(bias)
    if theta <= boundary - dead:
        return "right" if x > 0 else "left"
    if theta >= boundary + dead:
        return "down" if y > 0 else "up"
    return None
