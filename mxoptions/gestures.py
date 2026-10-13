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


# -- shapes ----------------------------------------------------------------------
# A movement that changes direction on the way is a shape: a corner, there and back, a circle, a
# shake. It is read as its strokes, each one of "U", "D", "L", "R" (y grows downwards).

SHAPES = (
    "down_right", "down_left", "up_right", "up_left", "right_down", "right_up", "left_down", "left_up",  # corners
    "up_down", "down_up", "left_right", "right_left",                                                     # there and back
    "circle_cw", "circle_ccw", "zigzag_h", "zigzag_v",
)
SHAPE_MM = 6.0      # a stroke shorter than this is a wobble, not part of the shape
_STEP_MM = 2.0      # the movement is looked at in pieces of this length
_NAMES = {"U": "up", "D": "down", "L": "left", "R": "right"}
_CLOCKWISE = {"R": "D", "D": "L", "L": "U", "U": "R"}
_OPPOSITE = {"U": "D", "D": "U", "L": "R", "R": "L"}


def strokes(points, minimum=SHAPE_MM):
    """The strokes a movement is made of, as a string like "DR". ``points`` are the positions the
    mouse went through, in millimetres from where the button went down."""
    pieces, anchor = [], points[0] if points else (0.0, 0.0)
    for point in points[1:]:
        dx, dy = point[0] - anchor[0], point[1] - anchor[1]
        length = math.hypot(dx, dy)
        if length < _STEP_MM:
            continue
        heading = ("R" if dx > 0 else "L") if abs(dx) >= abs(dy) else ("D" if dy > 0 else "U")
        if pieces and pieces[-1][0] == heading:
            pieces[-1][1] += length
        else:
            pieces.append([heading, length])
        anchor = point
    while True:  # take out what is too short to be meant, joining what it stood between
        short = min(range(len(pieces)), key=lambda i: pieces[i][1], default=None)
        if short is None or pieces[short][1] >= minimum:
            break
        del pieces[short]
        if 0 < short < len(pieces) and pieces[short - 1][0] == pieces[short][0]:
            pieces[short - 1][1] += pieces[short][1]
            del pieces[short]
    return "".join(heading for heading, _length in pieces)


def shape(drawn):
    """Which of SHAPES a row of strokes is, or None."""
    if len(drawn) == 2:  # a corner, or there and back
        return f"{_NAMES[drawn[0]]}_{_NAMES[drawn[1]]}"
    if len(drawn) >= 3:
        turns = list(zip(drawn, drawn[1:]))
        if all(after == _CLOCKWISE[before] for before, after in turns):
            return "circle_cw"
        if all(before == _CLOCKWISE[after] for before, after in turns):
            return "circle_ccw"
        if all(after == _OPPOSITE[before] for before, after in turns):
            return "zigzag_h" if drawn[0] in "LR" else "zigzag_v"
    return None


def outline(name):
    """How a shape is drawn, for a little picture of it: points in a 1 x 1 square (y grows downwards),
    from where the movement starts to where it ends."""
    if name.startswith("circle"):
        points = [(0.5 + 0.36 * math.sin(a), 0.5 - 0.36 * math.cos(a)) for a in (math.radians(20 + 32 * i) for i in range(11))]
        return points if name == "circle_cw" else [(1.0 - x, y) for x, y in points]
    if name.startswith("zigzag"):
        points = [(0.15, 0.2), (0.85, 0.4), (0.15, 0.6), (0.85, 0.8)]
        return points if name == "zigzag_h" else [(y, x) for x, y in points]
    first, second = name.split("_")
    step = {"up": (0.0, -1.0), "down": (0.0, 1.0), "left": (-1.0, 0.0), "right": (1.0, 0.0)}
    (ax, ay), (bx, by) = step[first], step[second]
    if (ax, ay) == (-bx, -by):  # there and back: drawn as a narrow wedge, or the way back would hide the way there
        side = (ay * 0.28, ax * 0.28)
        points = [(-side[0], -side[1]), (ax, ay), side]
    else:
        points = [(0.0, 0.0), (ax, ay), (ax + bx, ay + by)]
    left, top = min(x for x, _y in points), min(y for _x, y in points)
    width = max(max(x for x, _y in points) - left, 1e-9)
    height = max(max(y for _x, y in points) - top, 1e-9)
    scale = 0.7 / max(width, height)
    return [(0.5 + (x - left - width / 2) * scale, 0.5 + (y - top - height / 2) * scale) for x, y in points]
