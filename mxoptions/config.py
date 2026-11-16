"""Settings file (config.json in the place paths.py names: ~/.config/mxoptions on Linux): defaults, load, save;
and the settings as a file of their own, to export and import (export_data, import_data)."""

import contextlib
import copy
import json
import logging
import os
import re
import stat
import sys
import tempfile
import unicodedata

from . import device, environment, flow, gestures, paths
from .i18n import _
from .actions import DEFAULT, MACRO_STEPS, MAX_DELAY_MS, MAX_MACRO_STEPS, PRESET_BY_ID, SCROLLS, STEP_ACTIONS, dial_available, keys_label
from .keys import CODES as KEY_CODES
from .vinput import MOUSE_BUTTONS

log = logging.getLogger(__name__)

CONFIG_DIR = paths.CONFIG
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
BACKUP_NAME = "config.backup.json"  # in CONFIG_DIR: the settings as they were before the last import

# An exported settings file: {"format": SETTINGS_FORMAT, "version": 1, "config": the settings}
SETTINGS_FORMAT = "mxoptions-settings"
SETTINGS_FILE_MAX = 1 << 20  # bytes, exported or imported alike: whatever is exported can be imported again
SETTINGS_DEPTH_MAX = 32      # lists and dicts inside one another: far more than any setting needs
# One macro as a file of its own: {"format": MACRO_FORMAT, "version": 1, "macro": the macro}, at most
# SETTINGS_FILE_MAX bytes too. Exactly what the macro editor shows, nothing more (import_macro).
MACRO_FORMAT = "mxoptions-macro"
MACRO_NAME_MAX = 32767  # UTF-16 units: what the name line of the macro editor holds
_MACRO_FIELDS = frozenset({"type", "name", "repeat", "steps", "stop_on_press"})
_STEP_FIELDS = {"keys": "keys", "text": "text", "delay": "ms", "click": "button", "command": "command",
                "action": "action", "haptic": "waveform"}  # each step: "do" and this field, nothing else
_STEP_ACTION_FIELDS = {"keys": {"type", "keys"}, "button": {"type", "button"}, "shortcut": {"type", "component", "name"},
                       "desktop": {"type", "do"}, "command": {"type", "command"}, "script": {"type", "name", "script"},
                       "hook": {"type", "name"}}
# Bidirectional marks and overrides: a command holding one may run other than it reads
_HIDING = frozenset("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
# What stays on this computer, never exported nor imported: the pairing keys of Flow, what this computer goes by
# there, its Easy-Switch channel. Their values in an exported file.
LOCAL_FLOW = {"peers": [], "id": "", "channel": -1}

# Configurable buttons, in the order the UI lists them: key -> control ID
BUTTONS = {
    "middle": device.CID_MIDDLE,
    "back": device.CID_BACK,
    "forward": device.CID_FORWARD,
    "gesture": device.CID_GESTURE,
    "smartshift": device.CID_SMARTSHIFT,
    "panel": device.CID_HAPTIC_PANEL,
}
BUTTON_BY_CID = {cid: key for key, cid in BUTTONS.items()}

THUMB_FUNCTIONS = ("hscroll", "zoom", "volume", "tabs", "desktops", "brightness", "keys")
WHEEL_CLICK_MODES = ("off", "haptic", "sound", "both")
WHEEL_SOUNDS = ("tick", "clack", "relay", "soft", "custom")
WHEEL_MOMENTS = ("both", "free", "ratchet")


def _a(preset_id):
    return copy.deepcopy(PRESET_BY_ID[preset_id].action)


def _first(*preset_ids):
    """The first of these catalogue actions that this desktop can do (nothing, if it can do none of them)."""
    return next((_a(pid) for pid in preset_ids if PRESET_BY_ID[pid].available), {"type": "none"})


def default_gestures():
    return {
        "type": "gestures",
        "tap": _first("overview", "launcher"),
        "up": _first("all_windows", "win_fullscreen"),
        "down": _first("show_desktop", "win_minimize", "win_close"),
        "left": _first("desktop_left"),
        "right": _first("desktop_right"),
    }


# Spotlight effects of a button (action "spotlight"): in this order they are listed and switched through
EFFECTS = ("highlight", "magnify", "laser")
EFFECT_DEFAULTS = {"highlight": {"on": True, "size": 50, "color": "#000000"},
                   "magnify": {"on": False, "size": 50, "color": "#ffffff"},
                   "laser": {"on": False, "size": 50, "color": "#f2605d"}}
EFFECT_SIZE = (1, 100)
EFFECT_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")


def default_spotlight():
    return {"type": "spotlight", **copy.deepcopy(EFFECT_DEFAULTS)}


def spotlight(action):
    """A whole, valid spotlight action made from ``action``: each effect switched on or off, its size and colour,
    defaults where a value is missing or of the wrong kind, nothing else. At least one effect stays on. Never raises."""
    action = action if isinstance(action, dict) else {}
    clean = {"type": "spotlight"}
    for name in EFFECTS:
        given, default = action.get(name), EFFECT_DEFAULTS[name]
        given = given if isinstance(given, dict) else {}
        on, size, color = given.get("on"), given.get("size"), given.get("color")
        clean[name] = {"on": on if type(on) is bool else default["on"],
                       "size": min(EFFECT_SIZE[1], max(EFFECT_SIZE[0], size)) if type(size) is int else default["size"],
                       "color": color.lower() if type(color) is str and EFFECT_COLOR.match(color) else default["color"]}
    if not any(clean[name]["on"] for name in EFFECTS):
        clean["highlight"]["on"] = True
    return clean


MAX_BUBBLES = 8   # per ring level
MAX_RING_DEPTH = 3
_OLD_RING = ("copy", "paste", "screenshot_region", "play_pause", "overview", "emoji", "lock", "krunner")


def bubble(preset_id=None, action=None):
    """A ring bubble that runs an action. Empty label/icon/colour mean "derive from the action"."""
    return {"label": "", "icon": "", "color": "", "action": _a(preset_id) if preset_id else copy.deepcopy(action or {"type": "none"})}


def folder(label, children, icon=""):
    """A ring bubble that opens its own ring of bubbles."""
    return {"label": label, "icon": icon, "color": "", "children": children}


def dial(scroll_id):
    """A ring bubble that is adjusted with the scroll wheel (see actions.SCROLLS)."""
    return {"label": "", "icon": "", "color": "", "scroll": scroll_id}


def offered(bubbles):
    """``bubbles`` without the ones that could do nothing on this desktop, and without folders left empty by that."""
    kept = []
    for item in bubbles:
        if "children" in item:
            item = dict(item, children=offered(item["children"]))
            if not item["children"]:
                continue
        elif "scroll" in item:
            if not dial_available(item["scroll"]):
                continue
        elif not environment.available(item.get("action")):
            continue
        kept.append(item)
    return kept


def default_bubbles():
    return offered([
        bubble("copy"),
        bubble("paste"),
        bubble("screenshot_region"),
        folder("Media", [bubble(pid) for pid in ("play_pause", "next_track", "volume_up", "mute", "volume_down", "prev_track")],
               icon="applications-multimedia"),
        bubble(next(pid for pid in ("overview", "launcher", "terminal", "files") if PRESET_BY_ID[pid].available)),
        bubble("emoji" if PRESET_BY_ID["emoji"].available else "files"),
        bubble("lock"),
        bubble("krunner" if PRESET_BY_ID["krunner"].available else "terminal"),
    ])


def _ring(*items):
    """Bubbles from preset ids, as far as this desktop can do them. "~id" makes a dial, a (label, icon, [ids])
    tuple a folder."""
    def one(item):
        if isinstance(item, tuple):
            return folder(item[0], [one(child) for child in item[2]], icon=item[1])
        return dial(item[1:]) if item.startswith("~") else bubble(item)
    return offered([one(item) for item in items])


def _named(label, icon, action=None, preset=None, scroll=None):
    """A bubble with its own label and icon: for an action, a catalogue action, or a dial."""
    made = dial(scroll) if scroll else bubble(preset, action)
    made.update(label=label, icon=icon)
    return made


def _run(label, icon, command):
    return _named(label, icon, {"type": "command", "command": command})


def _kwin(label, icon, name):
    return _named(label, icon, {"type": "shortcut", "component": "kwin", "name": name})


def pro_bubbles():
    """A ring for a tiling KDE desktop: the dials used all the time on the ring itself, everything
    else one folder away. The Tiling folder drives the Krohnkite script: its layouts by name, and
    floating on and off."""
    spectacle = "org_kde_spectacle_desktop"
    return [
        dial("volume"),
        folder("Apps", [
            _run("Firefox", "firefox", "firefox"),
            _run("Terminal", "Alacritty", "alacritty"),
            _run("Files", "system-file-manager", "dolphin"),
            _run("VSCodium", "vscodium", "codium"),
            _run("Telegram", "telegram", "telegram-desktop"),
            _run("Discord", "discord", "discord"),
            _run("YouTube Music", "youtube-music", "flatpak run com.github.th_ch.youtube_music"),
            _run("Steam", "steam", "steam"),
        ], icon="applications-all"),
        dial("tabs"),
        folder("Tiling", [
            _kwin("BTree", "view-multiple-objects", "KrohnkiteBTreeLayout"),
            _kwin("Floating", "dialog-layers", "KrohnkiteFloatingLayout"),
            _kwin("Three columns", "page-3sides", "KrohnkiteTreeColumnLayout"),  # sic: the script's own name
            _kwin("Monocle", "draw-rectangle", "KrohnkiteMonocleLayout"),
            _kwin("Tile", "view-sidetree", "KrohnkiteTileLayout"),
            _kwin("Float all", "window-duplicate", "KrohnkiteFloatAll"),
            _kwin("Float window", "focus-windows", "KrohnkiteToggleFloat"),
            _named("All layouts", "view-grid", scroll="tile_layout"),
        ], icon="view-split-left-right"),
        dial("tracks"),
        folder("Capture", [
            bubble("screenshot_region"),
            bubble("screenshot_window"),
            bubble("screenshot_full"),
            _named("Record region", "media-record", {"type": "shortcut", "component": spectacle, "name": "RecordRegion"}),
            _named("Record screen", "camera-video", {"type": "shortcut", "component": spectacle, "name": "RecordScreen"}),
            _run("OBS Studio", "com.obsproject.Studio", "obs"),
        ], icon="camera-photo"),
        bubble("clipboard"),
        folder("System", [
            bubble("lock"),
            bubble("mic_mute"),
            _named("Keyboard layout", "input-keyboard", {
                "type": "shortcut", "component": "KDE_Keyboard_Layout_Switcher", "name": "Switch to Next Keyboard Layout"}),
            bubble("night_light"),
            bubble("dnd"),
            _run("System monitor", "utilities-system-monitor", "alacritty -e btop"),
            _run("System settings", "systemsettings", "systemsettings"),
            folder("More dials", [dial(name) for name in (
                "zoom", "undo", "seek", "history", "desktops", "win_desktop", "tile_width", "brightness")],
                   icon="preferences-other"),
        ], icon="preferences-system"),
    ]


# Ready-made rings, listed clockwise from 12 o'clock: (id, label, builder)
RING_PRESETS = [
    ("everyday", "Everyday", default_bubbles),
    *([("pro", "Pro", pro_bubbles)] if environment.current() == environment.KDE else []),  # KWin and its tiling script
    ("media", "Media", lambda: _ring(
        "play_pause", "~volume", "next_track", "~seek", "mute", "mic_mute", "prev_track", "~brightness")),
    ("browser", "Browser", lambda: _ring(
        "tab_new", "~tabs", "tab_next", "tab_reopen", "tab_close", "find", "tab_prev", "~zoom")),
    ("dials", "Dials", lambda: _ring("~volume", "~zoom", "~tabs", "~undo", "~brightness", "~seek", "~desktops", "~tracks")),
    ("windows", "Windows and desktops", lambda: _ring(
        "win_maximize", "desktop_right", "win_tile_right", "overview", "win_minimize", "win_close", "win_tile_left",
        "desktop_left")),
    ("editing", "Editing", lambda: _ring("copy", "paste", "redo", "save", "select_all", "find", "undo", "cut")),
    ("system", "System", lambda: _ring(
        ("Screenshot", "camera-photo", ["screenshot_region", "screenshot_window", "screenshot_full"]),
        "clipboard", "terminal", "files", "lock", "calculator", "krunner", "night_light")),
]
MODEL_STYLES = ("photo", "studio", "lowpoly", "outline", "wire")
TRAY_ICONS = ("outline", "solid", "master", "battery", "logo")  # see ui/tray_icon.py
MOUSE_COLORS = ("graphite", "pale-grey", "black")
GESTURE_BUTTONS = ("gesture", "panel")  # the two thumb buttons whose roles can be swapped
RING_STYLES = ("bubbles", "pie", "grid")
MAX_SAVED_RINGS = 16
# What a key of the keyboard can do: everything but what only makes sense on the mouse (its wheel, its pointer
# speed, gestures, Flow). Easy-Switch from a key moves the keyboard; the Actions Ring opens at the pointer.
KEYBOARD_ACTIONS = ("none", "keys", "button", "shortcut", "desktop", "command", "script", "macro", "host", "hook", "ring", "together")
MAX_KEYBOARD_KEYS = 128
KEY_ID = re.compile(r"0x[0-9a-f]{4}\Z")  # how the keys of the keyboard are named in the settings: key_id(control id)
MAX_COMBO = 16          # key names in one combination
MAX_KEY_TEXT = 65536    # characters in a text, command, script or name a key runs
MAX_PROFILES = 32       # apps with keys of their own
MAX_APP_ID = 128        # characters in the name an app goes by (front.py)
MAX_PROFILE_NAME = 64
NATIVE = {"type": "default"}  # in a profile: the key does what it always does in that app, whatever it does elsewhere


def key_id(cid):
    return f"0x{cid:04x}"


DEFAULTS = {
    "version": 6,
    "buttons": {
        "middle": DEFAULT,
        "back": DEFAULT,
        "forward": DEFAULT,
        "gesture": default_gestures(),
        "smartshift": DEFAULT,
        "panel": {"type": "ring"},
    },
    "pointer": {"dpi": 1000, "alt_dpi": 1600, "precision_dpi": 400},
    "wheel": {
        "smartshift": True,   # auto switch between ratchet and free spin
        "threshold": 12,      # 1 (shifts early) .. 50 (shifts late)
        "mode": "ratchet",    # fixed mode when smartshift is off: ratchet | freespin
        "torque": 75,         # ratchet force, 1..100
        "natural": False,
        "smooth": False,      # hi-res scrolling, routed through the app
        "speed": 1.0,
    },
    "thumbwheel": {"function": "hscroll", "invert": False, "speed": 1.0,
                   "ticks": True,  # a vibration tick for every step of a stepped function
                   "up": [], "down": []},  # "keys": the key combination for turning up, for turning down; [] none
    # What marks the wheel changing between ratchet and free spin, like the clutch of earlier MX Masters
    "wheel_click": {
        "mode": "both",       # off | haptic | sound | both
        "moments": "both",    # which of the two changes get a click: both | free | ratchet
        "free": 0x04,         # vibration pattern when it lets go into free spin (None: no vibration)
        "ratchet": 0x1B,      # ... and when the ratchet engages again
        "sound": "tick",      # tick | clack | relay | soft | custom
        "file": "",           # custom: path of a WAV file
        "volume": 0.3,        # 0..1 on the slider; the amplitude is its square
        "pitch": 1.0,         # tone of the synthesised sounds, 0.6 (lower) .. 1.8 (higher)
        "length": 1.0,        # how long they ring, 0.5 .. 2.5
    },
    "haptics": {"enabled": True, "level": 60, "force": None, "feedback": True, "battery_saving": True,
                "screens": False},
    # Rumble from games, played on the mouse (see rumble.py)
    "rumble": {
        "enabled": False,     # adds a virtual gamepad that games can vibrate
        "light": 0x1B,        # the pattern for a weak rumble, a medium and a strong one (None: no vibration)
        "medium": 0x04,
        "strong": 0x02,
        "interval": 110,      # ms between two pulses for as long as a rumble lasts
        "gain": 1.0,          # how strongly a game's rumble is taken: 0.5 (it takes more to feel it) .. 2.0
    },
    "ring": {"size": 1.0, "style": "bubbles", "bubbles": default_bubbles(), "saved": []},
    "gestures": {
        "trigger": "auto",    # release: judged when the button comes up | auto: a long clear swipe fires at once
                              # | repeat: like auto, and it fires again for every further step
        "min_mm": 2.5,        # shorter than this is a tap
        "far_mm": 16.0,       # travel after which a swipe fires while the button is still held
        "step_mm": 8.0,       # repeat: travel between two repeats
        "dead": 5,            # degrees on either side of the diagonal where a swipe is ignored
        "shape_mm": gestures.SHAPE_MM,  # shapes: a stroke shorter than this does not count as part of one
        "bias": 0,            # degrees: > 0 gives sideways swipes more room, < 0 up/down swipes
        "haptic": True,       # vibration during gestures, as set out in "cues"
        "cues": dict(gestures.CUE_PRESETS["normal"], up=0x04, down=0x04, left=0x04, right=0x04),
        "per_direction": False,  # use the up/down/left/right cues instead of the one "swipe" cue
        "move_speed": 1.0,     # pan and zoom: how far page moves for same hand movement
        "move_reverse": False,  # pan and zoom other way round
    },
    "app": {"language": "auto", "theme": "graphite", "accent": "#2fd6a7", "model": "photo", "mouse_color": "graphite",
            "tray_icon": "outline",  # the look of the icon in the system tray
            "api": True},  # let other programs talk to the app over D-Bus
    # One mouse on two or three computers, see flow.py
    "flow": {
        "enabled": False,
        "id": "",             # what this computer goes by among the others, made up once; a name may be shared, this is not
        # The other computers, two at most: {"key": the pairing key both hold, "id": what it goes by, "address":
        # where it was last reached (may be empty), "name": what it calls itself, "edge": the edge of this desktop
        # it is behind (left | right | top | bottom), "edge_at": when that was chosen (Unix time): the later choice of
        # two decides; "paused": true while it takes no part but stays paired (false, or not there: it takes part)}
        "peers": [],
        "port": flow.PORT,    # where this computer listens for the others
        "push_mm": 10.0,      # how far the mouse has to be pushed on, once the pointer is at the edge, to cross
        "corner": 10,         # percent of the edge at each of its ends where pushing does nothing (hot corners live there)
        "fullscreen": False,  # cross even while a full-screen window (a game, a film) is in front
        "hold_ctrl": False,   # cross at the edge only while a Ctrl key is held; where that cannot be told, never
        "clipboard": True,    # hand the copied text or picture over with the pointer
        "files": True,        # ... and copied files and folders
        "files_max_mb": 2048,  # at most this much in one go, sending or receiving
        "cue_arrived": 0x07,  # the vibration when copied files have arrived (None: none)
        "cue_failed": 0x06,   # ... and when they could not be brought over
        "channel": -1,        # this computer's Easy-Switch channel, noted whenever the mouse is here (-1: not seen yet)
        "mouse_follows_keyboard": False,  # the keyboard switched to another of these computers takes the mouse along
        "keyboard_follows_mouse": False,  # the mouse sent to another of these computers takes the keyboard along
    },
    # The keyboard: what its keys do, by key_id(control id). A key that is not listed does what it always does.
    # "profiles": [{"app", "name", "keys"}], the first one for the app in front changing what its keys do there
    # (NATIVE: what the key always does); a key a profile does not list does what "keys" says. "notices": say on
    # screen what the keyboard reports it changed (its brightness), never what made it change. "lock_notices": the
    # same when it reports a lock key (Caps Lock, Num Lock, Scroll Lock) on or off; "fn_notices": for its Fn lock.
    "keyboard": {"keys": {}, "profiles": [], "notices": False, "lock_notices": False, "fn_notices": False},
    # The mouse in single apps: "profiles": [{"app", "name", "buttons", "ring"}], first one for app in front wins.
    # "buttons": key -> action over "buttons" (NATIVE: button's own function); a button not listed does what
    # "buttons" says. "ring": bubbles of its own, or None for the ring of all apps. Everything else stays global.
    # "thumbwheel": [{"app", "function"}]: a thumb wheel function of its own (THUMB_FUNCTIONS) for an app with a
    # profile; an app not listed uses "thumbwheel"'s function. Direction, speed and steps stay global.
    # "thumbwheel_keys": [{"app", "up", "down"}]: what "keys" sends turning up and turning down in an app with a
    # profile, both its own ([]: nothing); an app not listed sends "thumbwheel"'s "up" and "down".
    # "gestures": [{"app", "buttons"}]: for an app with a profile, buttons with gestures of their own: key -> a
    # "gestures" action (app_gestures_action), for buttons its profile does not list. Recognition, speed, direction
    # and vibration ("gestures") stay global.
    "mouse": {"profiles": [], "thumbwheel": [], "thumbwheel_keys": [], "gestures": []},
}


def _merge(base, override):
    """Overlay ``override`` on a copy of ``base``. Action dicts (they carry "type") are replaced whole."""
    result = copy.deepcopy(base)
    if not isinstance(override, dict):
        return result
    for key, value in override.items():
        current = result.get(key)
        if isinstance(current, dict) and "type" not in current:
            result[key] = _merge(current, value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _clamp(cfg, section, key, low, high, kind=int):
    holder, default = cfg, DEFAULTS
    for part in section.split("."):
        holder, default = holder[part], default[part]
    value = holder.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        value = default[key]
    holder[key] = None if value is None else kind(max(low, min(high, value)))


def _waveform(value, default):
    """A vibration pattern: an id the HAPTIC feature knows, or None for "no vibration"."""
    known = isinstance(value, int) and not isinstance(value, bool) and value in device.WAVEFORMS
    return value if value is None or known else default


def _flow_peers(link):
    """The other computers of Flow, checked: a usable key each, no key twice, an edge of its own each."""
    found = link.get("peers") if isinstance(link.get("peers"), list) else []
    if not found and isinstance(link.get("key"), str) and link["key"].strip():  # settings from before there could be two
        found = [{"key": link["key"], "address": link.get("peer"), "name": link.get("peer_name"), "edge": link.get("edge"),
                  "edge_at": link.get("edge_at")}]
    peers, keys, edges = [], set(), set()
    for entry in found:
        key = entry.get("key").strip()[:200] if isinstance(entry, dict) and isinstance(entry.get("key"), str) else ""
        if not flow.key_usable(key) or flow.normalize_key(key) in keys or len(peers) >= flow.MAX_PEERS:
            continue
        keys.add(flow.normalize_key(key))
        edge = entry.get("edge") if entry.get("edge") in flow.EDGES and entry.get("edge") not in edges else \
            next(side for side in ("right", "left", "top", "bottom") if side not in edges)
        edges.add(edge)
        chosen = entry.get("edge_at")
        peers.append({
            "key": key,
            "id": entry["id"][:64] if isinstance(entry.get("id"), str) else "",
            "address": entry["address"].strip()[:200] if isinstance(entry.get("address"), str) else "",
            "name": entry["name"].strip()[:64] if isinstance(entry.get("name"), str) else "",
            "edge": edge,
            "edge_at": float(min(max(chosen, 0.0), 4.0e9)) if isinstance(chosen, (int, float)) and not isinstance(chosen, bool) else 0.0,
        })
        if type(entry.get("paused")) is bool:
            peers[-1]["paused"] = entry["paused"]  # not added where it never was: settings from before stay the same
    return peers


def _migrate_ring(ring):
    """Version 1 stored a flat list of eight slots."""
    slots = ring.get("slots")
    if not isinstance(slots, list):
        return ring.get("bubbles")
    actions = [environment.modernize(s.get("action")) for s in slots if isinstance(s, dict)]
    if actions == [_a(pid) for pid in _OLD_RING]:
        return default_bubbles()  # untouched defaults: move on to the new ones
    return [bubble(action=a) for a in actions if isinstance(a, dict)]


def _sanitize_bubbles(bubbles, depth):
    result = []
    for item in bubbles if isinstance(bubbles, list) else []:
        if not isinstance(item, dict):
            continue
        clean = {key: item[key] if isinstance(item.get(key), str) else "" for key in ("label", "icon", "color")}
        if item.get("scroll") in SCROLLS:
            clean["scroll"] = item["scroll"]
        elif isinstance(item.get("children"), list) and depth < MAX_RING_DEPTH:
            clean["children"] = _sanitize_bubbles(item["children"], depth + 1)
        elif isinstance(item.get("action"), dict) and "type" in item["action"]:
            clean["action"] = environment.modernize(item["action"])
        else:
            clean["action"] = {"type": "none"}
        result.append(clean)
    return result[:MAX_BUBBLES]


def _text(*values, empty=False):
    return all(isinstance(value, str) and (empty or value) and len(value) <= MAX_KEY_TEXT for value in values)


def _key_names(names, empty=False):
    """A key combination: a list of key and button names the virtual input knows (keys.CODES)."""
    return (isinstance(names, list) and (empty or bool(names)) and len(names) <= MAX_COMBO
            and all(isinstance(name, str) and name in KEY_CODES for name in names))


def thumb_combo(names):
    """Whether ``names`` is a combination the thumb wheel may send: keyboard keys only (_key_names also knows mouse
    buttons), none at all too."""
    return _key_names(names, empty=True) and all(name.startswith("KEY_") for name in names)


def macro_step(step):
    """Whether ``step`` is a macro step complete and well-formed (see actions.MACRO_STEPS)."""
    if not isinstance(step, dict):
        return False
    kind = step.get("do")
    if kind == "keys":
        return _key_names(step.get("keys"), empty=True)  # the macro editor starts a step empty: it taps nothing
    if kind == "text":
        return _text(step.get("text"), empty=True)
    if kind == "delay":
        ms = step.get("ms")
        return type(ms) is int and 0 <= ms <= MAX_DELAY_MS
    if kind == "click":
        return step.get("button") in MOUSE_BUTTONS
    if kind == "command":
        return _text(step.get("command"), empty=True)
    if kind == "action":  # the type first: a macro, however deep, is never looked into
        action = step.get("action")
        return isinstance(action, dict) and action.get("type") in STEP_ACTIONS and keyboard_action(action)
    if kind == "haptic":
        waveform = step.get("waveform")
        return type(waveform) is int and waveform in device.WAVEFORMS
    return False


def keyboard_action(action):
    """Whether ``action`` is one a key of the keyboard can run, complete and well-formed down to each macro
    step. What is not is never kept in the settings, and never diverts a key."""
    kind = action.get("type") if isinstance(action, dict) else None
    if kind == "keys":
        return _key_names(action.get("keys"))
    if kind == "button":
        return action.get("button") in MOUSE_BUTTONS
    if kind == "shortcut":
        return _text(action.get("component"), action.get("name"))
    if kind == "desktop":
        return isinstance(action.get("do"), str) and action["do"] in environment.WAYS
    if kind == "command":
        return _text(action.get("command"))
    if kind == "script":
        return _text(action.get("script")) and _text(action.get("name", ""), empty=True)
    if kind == "macro":
        steps = action.get("steps")
        return (isinstance(steps, list) and 0 < len(steps) <= MAX_MACRO_STEPS and _text(action.get("name", ""), empty=True)
                and isinstance(action.get("repeat", False), bool) and isinstance(action.get("stop_on_press", False), bool)
                and all(macro_step(step) for step in steps))
    if kind == "host":
        host = action.get("host")
        return (type(host) is int and 0 <= host <= 15 and set(action) <= {"type", "host", "both"}
                and action.get("both", True) is True)  # "both": the mouse and the keyboard go
    if kind == "together":
        return action == {"type": "together"}
    if kind == "hook":
        return _text(action.get("name"))
    if kind == "ring":
        return action == {"type": "ring"}
    return kind == "none"


def _keyboard_keys(keys):
    """The usable entries of ``keys``: named by key_id, with an action a key can run (see KEYBOARD_ACTIONS);
    "default" is dropped (a key not listed does what it always does), so is everything else. At most
    MAX_KEYBOARD_KEYS, the lowest ids first."""
    kept = {}
    for name in sorted(key for key in keys if isinstance(key, str)) if isinstance(keys, dict) else ():
        action = environment.modernize(keys[name])
        if KEY_ID.match(name) and keyboard_action(action):
            kept[name] = action
            if len(kept) >= MAX_KEYBOARD_KEYS:
                break
    return kept


def app_id(text):
    """Whether ``text`` is a name an app can go by in a profile: 1 to MAX_APP_ID characters, lowercase, none of
    them a control or format character."""
    return (isinstance(text, str) and 0 < len(text) <= MAX_APP_ID and text == text.lower()
            and not any(unicodedata.category(c).startswith("C") for c in text))


def keyboard_profile(profile):
    """The profile made from ``profile`` (see DEFAULTS): its keys as _keyboard_keys keeps them, NATIVE too. None
    if it is not one."""
    if not (isinstance(profile, dict) and set(profile) == {"app", "name", "keys"} and app_id(profile["app"])
            and isinstance(profile["name"], str) and len(profile["name"]) <= MAX_PROFILE_NAME
            and isinstance(profile["keys"], dict)):
        return None
    keys = {}
    for name in sorted(key for key in profile["keys"] if isinstance(key, str)):
        action = environment.modernize(profile["keys"][name])
        if KEY_ID.match(name) and (action == NATIVE or keyboard_action(action)):
            keys[name] = action
            if len(keys) >= MAX_KEYBOARD_KEYS:
                break
    return {"app": profile["app"], "name": profile["name"], "keys": keys}


def _keyboard_profiles(profiles):
    """The usable profiles of ``profiles``, in their order: at most MAX_PROFILES, one for each app (the first)."""
    kept = []
    for profile in profiles if isinstance(profiles, list) else ():
        profile = keyboard_profile(profile)
        if profile is not None and all(other["app"] != profile["app"] for other in kept):
            kept.append(profile)
            if len(kept) >= MAX_PROFILES:
                break
    return kept


# What a button may do in a mouse profile besides what a key may: the mouse's own actions, exactly as offered
MOUSE_ONLY = ({"type": "wheel_mode"}, {"type": "dpi_cycle"}, {"type": "dpi_shift"}, {"type": "flow"})


def profile_button_action(action):
    """Whether a button of a mouse profile may do ``action``: NATIVE, what a key may, or a mouse action. Never
    gestures (gesture settings stay global)."""
    return action == NATIVE or keyboard_action(action) or action in MOUSE_ONLY


# What the tap, a direction or a shape of an app's own gestures may do besides what a key may (but the ring): the
# mouse actions done in one go, exactly as offered for the gestures of all apps
GESTURE_SLOT_MOUSE = ({"type": "wheel_mode"}, {"type": "dpi_cycle"}, {"type": "flow"})


def gesture_slot_action(action):
    """Whether the tap, a direction or a shape of an app's own gestures may do ``action``."""
    return (keyboard_action(action) and action["type"] != "ring") or action in GESTURE_SLOT_MOUSE


def app_gestures_action(action):
    """The gestures of a button of one app made from ``action``: type "gestures", the tap and each direction what
    gesture_slot_action allows ({"type": "none"} where not), the shapes that have such an action, "mode" if
    gestures.hold_mode knows it; nothing else. None if ``action`` is no gestures action at all."""
    if not (isinstance(action, dict) and action.get("type") == "gestures"):
        return None
    clean = {"type": "gestures"}
    for direction in ("tap", "up", "down", "left", "right"):
        sub = environment.modernize(action.get(direction))
        clean[direction] = sub if gesture_slot_action(sub) else {"type": "none"}
    drawn = action.get("shapes")
    drawn = {name: environment.modernize(sub) for name, sub in (drawn.items() if isinstance(drawn, dict) else ())
             if name in gestures.SHAPES}
    drawn = {name: sub for name, sub in drawn.items() if gesture_slot_action(sub) and sub["type"] != "none"}
    if drawn:
        clean["shapes"] = drawn
    if gestures.hold_mode(action):
        clean["mode"] = action["mode"]
    return clean


def mouse_profile(profile):
    """The mouse profile made from ``profile`` (see DEFAULTS). Buttons it cannot have are dropped; None if it is
    not one at all."""
    if not (isinstance(profile, dict) and set(profile) == {"app", "name", "buttons", "ring"} and app_id(profile["app"])
            and isinstance(profile["name"], str) and len(profile["name"]) <= MAX_PROFILE_NAME
            and isinstance(profile["buttons"], dict) and (profile["ring"] is None or isinstance(profile["ring"], list))):
        return None
    buttons = {}
    for key in BUTTONS:
        if key in profile["buttons"]:
            action = environment.modernize(profile["buttons"][key])
            if profile_button_action(action):
                buttons[key] = action
    ring = _sanitize_bubbles(profile["ring"], 1) if profile["ring"] is not None else None
    return {"app": profile["app"], "name": profile["name"], "buttons": buttons, "ring": ring or None}


def _mouse_profiles(profiles):
    """The usable mouse profiles of ``profiles``, in their order: at most MAX_PROFILES, the first for each app."""
    kept = []
    for profile in profiles if isinstance(profiles, list) else ():
        profile = mouse_profile(profile)
        if profile is not None and all(other["app"] != profile["app"] for other in kept):
            kept.append(profile)
            if len(kept) >= MAX_PROFILES:
                break
    return kept


def mouse_profile_of(cfg, app):
    """The profile of ``app`` in "mouse.profiles", or None (app None: none)."""
    if app is None:
        return None
    return next((profile for profile in cfg["mouse"]["profiles"] if profile["app"] == app), None)


def _mouse_thumbwheel(entries, profiles):
    """The thumb wheel functions of single apps in ``entries``: {"app", "function"} each, only for apps with one of
    ``profiles``, the first for each app, in their order. Other keys of an entry are left out."""
    kept = []
    for entry in entries if isinstance(entries, list) else ():
        if not (isinstance(entry, dict) and isinstance(entry.get("app"), str)
                and entry.get("function") in THUMB_FUNCTIONS):
            continue
        app = entry["app"]
        if any(profile["app"] == app for profile in profiles) and all(other["app"] != app for other in kept):
            kept.append({"app": app, "function": entry["function"]})
    return kept


def _mouse_thumbwheel_keys(entries, profiles):
    """The thumb wheel keystrokes of single apps in ``entries``: {"app", "up", "down"} each, only for apps with one
    of ``profiles``, the first for each app, in their order. A direction that is not a combination the thumb wheel
    may send is [], as in "thumbwheel"; other keys of an entry are left out."""
    kept = []
    for entry in entries if isinstance(entries, list) else ():
        if not (isinstance(entry, dict) and isinstance(entry.get("app"), str)):
            continue
        app = entry["app"]
        if any(profile["app"] == app for profile in profiles) and all(other["app"] != app for other in kept):
            kept.append({"app": app, **{direction: entry[direction] if thumb_combo(entry.get(direction)) else []
                                        for direction in ("up", "down")}})
    return kept


def _mouse_gestures(entries, profiles):
    """The buttons of single apps with gestures of their own in ``entries``: {"app", "buttons"} each, only for apps
    with one of ``profiles``, the first for each app, in their order; of its buttons only those its profile does not
    list, each as app_gestures_action makes it (left out where that is None), and no entry without one. Other keys of
    an entry are left out."""
    kept = []
    for entry in entries if isinstance(entries, list) else ():
        if not (isinstance(entry, dict) and isinstance(entry.get("app"), str) and isinstance(entry.get("buttons"), dict)):
            continue
        profile = next((profile for profile in profiles if profile["app"] == entry["app"]), None)
        if profile is None or any(other["app"] == entry["app"] for other in kept):
            continue
        buttons = {key: app_gestures_action(entry["buttons"][key]) for key in BUTTONS
                   if key in entry["buttons"] and key not in profile["buttons"]}
        buttons = {key: action for key, action in buttons.items() if action is not None}
        if buttons:
            kept.append({"app": entry["app"], "buttons": buttons})
    return kept


def app_thumb_keys(cfg, app):
    """The keystrokes ``app`` has of its own for "keys" ("mouse.thumbwheel_keys"): {"up", "down"}, or None: those
    for all apps. None too for an app without a mouse profile."""
    if mouse_profile_of(cfg, app) is None:
        return None
    return next(({"up": entry["up"], "down": entry["down"]} for entry in cfg["mouse"]["thumbwheel_keys"]
                 if entry["app"] == app), None)


def app_thumb_function(cfg, app):
    """The thumb wheel function ``app`` has of its own ("mouse.thumbwheel"), or None: the one for all apps. None too
    for an app without a mouse profile."""
    if mouse_profile_of(cfg, app) is None:
        return None
    return next((entry["function"] for entry in cfg["mouse"]["thumbwheel"] if entry["app"] == app), None)


def thumbwheel(cfg, app):
    """The thumb wheel settings while ``app`` is in front: "thumbwheel", with the app's own function and keystrokes
    if it has them."""
    return dict(cfg["thumbwheel"], function=app_thumb_function(cfg, app) or cfg["thumbwheel"]["function"],
                **(app_thumb_keys(cfg, app) or {}))


def own_buttons(cfg, app):
    """What the buttons of ``app`` do of its own: its profile's buttons and its own gestures ({}: no profile)."""
    profile = mouse_profile_of(cfg, app)
    if profile is None:
        return {}
    drawn = next((entry["buttons"] for entry in cfg["mouse"]["gestures"] if entry["app"] == app), {})
    return {**drawn, **profile["buttons"]}


def mouse_buttons(cfg, app):
    """What the buttons do while ``app`` is in front: its own over "buttons"."""
    return {**cfg["buttons"], **own_buttons(cfg, app)}


def ring_bubbles(cfg, app):
    """The bubbles of the Actions Ring while ``app`` is in front: its own, else the ring for all apps."""
    profile = mouse_profile_of(cfg, app)
    return profile["ring"] if profile is not None and profile["ring"] is not None else cfg["ring"]["bubbles"]


def _once(*names):
    """A macro tapping the key combination ``names`` once per press, however long its button is held: held, the
    keys could repeat in the app and toggle again."""
    keys = list(names)
    return {"type": "macro", "name": keys_label(keys), "repeat": False, "steps": [{"do": "keys", "keys": keys}]}


# Suggested settings for one app, given only to an app with a mouse profile that the user picked, never by its
# name: id -> (label, {button: action}, thumb wheel function or None: the thumb wheel stays). The first three are
# own Linux and Windows choices after three rows of the predefined app-specific settings of Logi Options+
# (browser; Excel; Word and PowerPoint): panning while the middle button is held, which Logi Options+ does there,
# is left out (an app can be given Gestures with Pan by hand: "mouse.gestures"). The meeting sets are own choices
# from the default shortcuts Zoom and Microsoft list for their apps, not after Logi Options+. Keys are key
# positions, as everywhere.
APP_PRESETS = {
    "browser": ("Web browser", {"middle": NATIVE, "back": NATIVE, "forward": NATIVE}, "tabs"),
    "spreadsheet": ("Spreadsheet", {"back": {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_Z"]},
                                    "forward": {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_Y"]}}, "hscroll"),
    "documents": ("Documents and slides", {"back": {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_Z"]},
                                           "forward": {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_Y"]}}, "zoom"),
    "zoom": ("Zoom meetings", {"back": _once("KEY_LEFTALT", "KEY_A"), "forward": _once("KEY_LEFTALT", "KEY_V")}, None),
    "teams": ("Microsoft Teams meetings", {"back": _once("KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_M"),
                                           "forward": _once("KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_O")}, None),
    "photoshop": ("Photoshop", {"back": {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_Z"]},
                                "forward": {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_Z"]}}, "keys"),
    "premiere": ("Premiere", {"back": {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_Z"]},
                              "forward": {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_Z"]}}, "keys"),
}
# What "keys" sends turning up (the direction that scrolls right) and down in a set (mouse.thumbwheel_keys). Own
# choices after Windows shortcuts Adobe documents: Photoshop ] larger and [ smaller brush (named for Pattern Stamp
# and Spot Healing Brush), Premiere Page Down next and Page Up previous screen of the Timeline. Not Logi Options+'s.
APP_PRESET_KEYS = {
    "photoshop": {"up": ["KEY_RIGHTBRACE"], "down": ["KEY_LEFTBRACE"]},
    "premiere": {"up": ["KEY_PAGEDOWN"], "down": ["KEY_PAGEUP"]},
}
PRESET_SYSTEMS = {
    "teams": "win32",  # no Teams desktop app on Linux, and its web app has no video shortcut
    "photoshop": "win32", "premiere": "win32",  # Adobe makes these apps for Windows and macOS only
}


def offered_presets(platform=None):
    """The ids of APP_PRESETS offered on ``platform`` (sys.platform if None), in their order."""
    platform = platform or sys.platform
    return [preset for preset in APP_PRESETS if PRESET_SYSTEMS.get(preset, platform) == platform]


def preset_changes(cfg, app, preset):
    """What the suggested settings ``preset`` (APP_PRESETS) change for ``app``: ({button: action}, its thumb wheel
    function or None), each only where the app does as in all apps now; what it has of its own stays. ({}, None)
    for an app without a mouse profile."""
    profile = mouse_profile_of(cfg, app)
    if profile is None:
        return {}, None
    _label, buttons, function = APP_PRESETS[preset]
    own = own_buttons(cfg, app)
    return ({key: copy.deepcopy(action) for key, action in buttons.items() if key not in own},
            function if app_thumb_function(cfg, app) is None else None)


def preset_keys(cfg, app, preset):
    """The keystrokes the suggested settings ``preset`` give "keys" in ``app`` (APP_PRESET_KEYS): {"up", "down"},
    copies; None where the set has none, the app has no profile or keystrokes of its own, or its thumb wheel would
    do something else (a function of its own other than "keys")."""
    keys = APP_PRESET_KEYS.get(preset)
    if keys is None or mouse_profile_of(cfg, app) is None or app_thumb_keys(cfg, app) is not None:
        return None
    if (app_thumb_function(cfg, app) or APP_PRESETS[preset][2]) != "keys":
        return None
    return copy.deepcopy(keys)


def _sanitize(cfg):
    for key in BUTTONS:
        # The KDE shortcuts the catalogue held before it named things for every desktop become those names here.
        action = environment.modernize(cfg["buttons"].get(key))
        if not isinstance(action, dict) or "type" not in action:
            cfg["buttons"][key] = copy.deepcopy(DEFAULTS["buttons"][key])
        elif action["type"] == "gestures":
            clean = {"type": "gestures"}
            for direction in ("tap", "up", "down", "left", "right"):
                sub = environment.modernize(action.get(direction))
                clean[direction] = sub if isinstance(sub, dict) and "type" in sub else {"type": "none"}
            drawn = action.get("shapes")  # shape id -> action, for the shapes that have one
            drawn = {name: environment.modernize(sub) for name, sub in (drawn.items() if isinstance(drawn, dict) else ())
                     if name in gestures.SHAPES and isinstance(sub, dict) and sub.get("type") not in (None, "none")}
            if drawn:
                clean["shapes"] = drawn
            if gestures.hold_mode(action):  # pan or zoom while held; anything else: swipes
                clean["mode"] = action["mode"]
            cfg["buttons"][key] = clean
        elif action["type"] == "spotlight":
            cfg["buttons"][key] = spotlight(action)
        else:
            cfg["buttons"][key] = action
    cfg["ring"]["bubbles"] = _sanitize_bubbles(_migrate_ring(cfg["ring"]), 1) or default_bubbles()
    cfg["ring"].pop("slots", None)
    _clamp(cfg, "ring", "size", 0.7, 1.5, float)
    if cfg["ring"].get("style") not in RING_STYLES:
        cfg["ring"]["style"] = "bubbles"
    saved = cfg["ring"].get("saved")
    cfg["ring"]["saved"] = [
        {"name": entry["name"].strip()[:40], "bubbles": _sanitize_bubbles(entry.get("bubbles"), 1)}
        for entry in (saved if isinstance(saved, list) else [])
        if isinstance(entry, dict) and isinstance(entry.get("name"), str) and entry["name"].strip()
    ][:MAX_SAVED_RINGS]
    for key in ("theme", "accent", "language"):
        if not isinstance(cfg["app"].get(key), str):
            cfg["app"][key] = DEFAULTS["app"][key]
    if cfg["app"].get("model") == "solid":
        cfg["app"]["model"] = "studio"  # the plain 3D style was folded into the glossy one
    if cfg["app"].get("model") not in MODEL_STYLES:
        cfg["app"]["model"] = DEFAULTS["app"]["model"]
    if cfg["app"].get("tray_icon") not in TRAY_ICONS:
        cfg["app"]["tray_icon"] = DEFAULTS["app"]["tray_icon"]
    if cfg["app"].get("mouse_color") not in MOUSE_COLORS:
        cfg["app"]["mouse_color"] = DEFAULTS["app"]["mouse_color"]
    cfg["app"].pop("gesture_distance", None)
    if cfg["gestures"].get("trigger") not in gestures.TRIGGERS:
        cfg["gestures"]["trigger"] = DEFAULTS["gestures"]["trigger"]
    cfg["gestures"]["haptic"] = bool(cfg["gestures"].get("haptic"))
    cfg["gestures"]["per_direction"] = bool(cfg["gestures"].get("per_direction"))
    cfg["gestures"]["move_reverse"] = bool(cfg["gestures"].get("move_reverse"))
    _clamp(cfg, "gestures", "move_speed", 0.25, 4.0, float)
    cues = cfg["gestures"].get("cues")
    cues = cues if isinstance(cues, dict) else {}
    cfg["gestures"]["cues"] = {
        name: _waveform(cues.get(name, default), default) for name, default in DEFAULTS["gestures"]["cues"].items()
    }
    _clamp(cfg, "gestures", "min_mm", 1.0, 12.0, float)
    _clamp(cfg, "gestures", "far_mm", 6.0, 45.0, float)
    _clamp(cfg, "gestures", "step_mm", 2.0, 30.0, float)
    _clamp(cfg, "gestures", "dead", 0, 30)
    _clamp(cfg, "gestures", "shape_mm", 3.0, 20.0, float)
    _clamp(cfg, "gestures", "bias", -25, 25)
    cfg["gestures"]["far_mm"] = max(cfg["gestures"]["far_mm"], cfg["gestures"]["min_mm"] + 2.0)
    if cfg["thumbwheel"].get("function") not in THUMB_FUNCTIONS:
        cfg["thumbwheel"]["function"] = "hscroll"
    for direction in ("up", "down"):
        if not thumb_combo(cfg["thumbwheel"].get(direction)):
            cfg["thumbwheel"][direction] = []
    if cfg["wheel"].get("mode") not in ("ratchet", "freespin"):
        cfg["wheel"]["mode"] = "ratchet"
    for section, key in (("wheel", "smartshift"), ("wheel", "natural"), ("wheel", "smooth"), ("thumbwheel", "invert"),
                         ("thumbwheel", "ticks"), ("haptics", "enabled"), ("haptics", "feedback"), ("app", "api")):
        cfg[section][key] = bool(cfg[section].get(key))
    click, default_click = cfg["wheel_click"], DEFAULTS["wheel_click"]
    if click.get("mode") not in WHEEL_CLICK_MODES:
        click["mode"] = default_click["mode"]
    if click.get("sound") not in WHEEL_SOUNDS:
        click["sound"] = default_click["sound"]
    if click.get("moments") not in WHEEL_MOMENTS:
        click["moments"] = default_click["moments"]
    _clamp(cfg, "wheel_click", "pitch", 0.6, 1.8, float)
    _clamp(cfg, "wheel_click", "length", 0.5, 2.5, float)
    if not isinstance(click.get("file"), str):
        click["file"] = ""
    for key in ("free", "ratchet"):
        click[key] = _waveform(click.get(key, default_click[key]), default_click[key])
    _clamp(cfg, "wheel_click", "volume", 0.0, 1.0, float)
    link = cfg["flow"]
    for key in ("enabled", "clipboard", "files", "fullscreen", "mouse_follows_keyboard", "keyboard_follows_mouse"):
        link[key] = bool(link.get(key))
    link["hold_ctrl"] = link.get("hold_ctrl") is True  # anything but true: off, as when it is not there
    link["peers"] = _flow_peers(link)
    link["id"] = link["id"][:64] if isinstance(link.get("id"), str) else ""
    for old in ("key", "peer", "peer_name", "edge", "edge_at"):  # where the one other computer was kept before there could be two
        link.pop(old, None)
    for key in ("cue_arrived", "cue_failed"):
        link[key] = _waveform(link.get(key, DEFAULTS["flow"][key]), DEFAULTS["flow"][key])
    _clamp(cfg, "flow", "push_mm", 0.0, 60.0, float)
    _clamp(cfg, "flow", "corner", 0, 40)
    _clamp(cfg, "flow", "files_max_mb", 1, 65536)
    _clamp(cfg, "flow", "port", 1024, 65535)
    _clamp(cfg, "flow", "channel", -1, 15)
    for key in ("dpi", "alt_dpi", "precision_dpi"):
        _clamp(cfg, "pointer", key, 100, 32000)
    _clamp(cfg, "wheel", "threshold", 1, 50)
    _clamp(cfg, "wheel", "torque", 1, 100)
    _clamp(cfg, "wheel", "speed", 0.25, 4.0, float)
    _clamp(cfg, "thumbwheel", "speed", 0.25, 4.0, float)
    _clamp(cfg, "haptics", "level", 1, 100)
    if type(cfg["haptics"].get("battery_saving")) is not bool:
        cfg["haptics"]["battery_saving"] = DEFAULTS["haptics"]["battery_saving"]
    cfg["haptics"]["screens"] = cfg["haptics"].get("screens") is True
    rumble = cfg["rumble"]
    rumble["enabled"] = bool(rumble.get("enabled"))
    for key in ("light", "medium", "strong"):
        rumble[key] = _waveform(rumble.get(key, DEFAULTS["rumble"][key]), DEFAULTS["rumble"][key])
    _clamp(cfg, "rumble", "interval", 60, 300)
    _clamp(cfg, "rumble", "gain", 0.5, 2.0, float)
    if cfg["haptics"].get("force") is not None:
        _clamp(cfg, "haptics", "force", 0, 0xFFFF)
    cfg["keyboard"]["keys"] = _keyboard_keys(cfg["keyboard"].get("keys"))
    cfg["keyboard"]["profiles"] = _keyboard_profiles(cfg["keyboard"].get("profiles"))
    cfg["keyboard"]["notices"] = cfg["keyboard"].get("notices") is True
    cfg["keyboard"]["lock_notices"] = cfg["keyboard"].get("lock_notices") is True
    cfg["keyboard"]["fn_notices"] = cfg["keyboard"].get("fn_notices") is True
    cfg["mouse"]["profiles"] = _mouse_profiles(cfg["mouse"].get("profiles"))
    cfg["mouse"]["thumbwheel"] = _mouse_thumbwheel(cfg["mouse"].get("thumbwheel"), cfg["mouse"]["profiles"])
    cfg["mouse"]["thumbwheel_keys"] = _mouse_thumbwheel_keys(cfg["mouse"].get("thumbwheel_keys"),
                                                             cfg["mouse"]["profiles"])
    cfg["mouse"]["gestures"] = _mouse_gestures(cfg["mouse"].get("gestures"), cfg["mouse"]["profiles"])
    return cfg


def _migrate(data):
    """Settings written before version 4: one "gesture travel" factor, and a 3D model as the default look."""
    app = data.get("app") if isinstance(data.get("app"), dict) else {}
    travel = app.get("gesture_distance")
    if isinstance(travel, (int, float)) and not isinstance(travel, bool) and "gestures" not in data:
        data["gestures"] = {"min_mm": round(2.5 * travel, 1), "far_mm": round(16.0 * travel, 1)}
    if app.get("model") in ("solid", "studio"):
        app["model"] = "photo"  # those were only ever the default; the picture of the real mouse replaces them
    return data


def clean_bubbles(bubbles):
    """A valid list of ring bubbles made from ``bubbles`` (empty if there is nothing usable in it)."""
    return _sanitize_bubbles(bubbles, 1)


def clean(cfg):
    """A valid configuration made from ``cfg``: missing parts filled in, values checked and clamped."""
    return _sanitize(_merge(DEFAULTS, cfg))


def exists():
    return os.path.exists(CONFIG_PATH)


def load():
    data = {}
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        log.warning("could not read %s: %s", CONFIG_PATH, e)
    stored_version = data.get("version") if isinstance(data, dict) else None
    cfg = upgrade(data)
    if stored_version is not None and stored_version != DEFAULTS["version"]:
        try:
            save(cfg)  # keep the file in the current format once it has been migrated
        except OSError as e:
            log.warning("could not save the migrated settings: %s", e)
    return cfg


def upgrade(data):
    """The settings of this version made from ``data`` as read from a file: older ones migrated, missing parts
    filled in, values checked and clamped. Parts it does not know are kept, as clean() keeps them."""
    stored_version = data.get("version") if isinstance(data, dict) else None
    if isinstance(stored_version, int) and stored_version < 4:
        data = _migrate(data)
    if isinstance(stored_version, int) and stored_version < 5 and isinstance(data.get("wheel_click"), dict):
        # The first mode-switch click (a two-part relay sound and long vibration patterns) was too much:
        # everyone moves to the new single quiet tick, keeping only whether it was on and their own file.
        old = data["wheel_click"]
        data["wheel_click"] = {key: old[key] for key in ("mode", "file") if key in old}
        if old.get("sound") == "custom":
            data["wheel_click"]["sound"] = "custom"
    cfg = _sanitize(_merge(DEFAULTS, data))
    cfg["version"] = DEFAULTS["version"]
    return cfg


def save(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    # Readable by the user alone: the file holds the pairing key of Flow.
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        os.fchmod(f.fileno(), 0o600)  # a file left over from an earlier attempt keeps its old mode otherwise
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)


def to_bytes(cfg):
    """``cfg`` as the settings file holds it, for write_durable. ASCII only: read back the same whatever the
    encoding of the system."""
    return json.dumps(cfg, indent=2).encode("ascii")


def _too_big(size, macro=False):
    text = (_("It is {size} KB, more than the {limit} KB a macro file may have.") if macro
            else _("It is {size} KB, more than the {limit} KB a settings file may have."))
    return text.format(size=-(-size // 1024), limit=SETTINGS_FILE_MAX // 1024)


def _depth_ok(value, depth):
    """Whether ``value`` has no more than ``depth`` lists and dicts inside one another."""
    if isinstance(value, dict):
        value = list(value.values())
    elif not isinstance(value, list):
        return True
    return depth > 0 and all(_depth_ok(item, depth - 1) for item in value)


def _settings_in(raw):
    """The settings in an exported file (its bytes), checked as far as the file goes. Raises ValueError, with
    what to tell the user."""
    if len(raw) > SETTINGS_FILE_MAX:
        raise ValueError(_too_big(len(raw)))
    not_one = _("It is not a settings file of MX Options.")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):  # not UTF-8, not JSON, or nested past what the parser takes
        raise ValueError(not_one) from None
    if not (isinstance(document, dict) and set(document) == {"format", "version", "config"}
            and document["format"] == SETTINGS_FORMAT and isinstance(document["config"], dict)
            and type(document["version"]) is int and type(document["config"].get("version")) is int
            and _depth_ok(document, SETTINGS_DEPTH_MAX)):
        raise ValueError(not_one)
    if document["version"] > 1 or document["config"]["version"] > DEFAULTS["version"]:
        raise ValueError(_("It was made by a newer version of MX Options."))
    if document["version"] != 1:
        raise ValueError(not_one)
    return document["config"]


def export_data(cfg):
    """The settings ``cfg`` as a file of their own (bytes, UTF-8 JSON): all of them, commands and scripts too,
    but LOCAL_FLOW. Raises ValueError, with what to tell the user, for settings import_data would not take back."""
    cfg = copy.deepcopy(cfg)
    cfg["flow"].update(copy.deepcopy(LOCAL_FLOW))
    cannot = _("The settings hold something a settings file cannot.")
    try:
        raw = json.dumps({"format": SETTINGS_FORMAT, "version": 1, "config": cfg}, indent=2,
                         ensure_ascii=False).encode("utf-8")
    except (ValueError, RecursionError):  # nested too deep, or text that is no Unicode (a lone surrogate)
        raise ValueError(cannot) from None
    if len(raw) > SETTINGS_FILE_MAX:
        raise ValueError(_too_big(len(raw)))
    try:
        _settings_in(raw)  # the checks of the way back in: every file written here can be imported
    except ValueError:
        raise ValueError(cannot) from None
    return raw


def import_data(raw, current):
    """The settings to use from an exported file (its bytes), brought up to this version as load() does, with
    LOCAL_FLOW taken from ``current``, the settings in use. Raises ValueError, with what to tell the user."""
    cfg = upgrade(_settings_in(raw))
    for key in LOCAL_FLOW:
        cfg["flow"][key] = copy.deepcopy(current["flow"][key])
    return cfg


def _shell_text(text):
    """Whether ``text``, run by a shell, reads as it runs: no bidirectional mark, no control character but tab
    and newline."""
    return not any(c in _HIDING or (c < " " and c not in "\t\n") or c == "\x7f" for c in text)


def _macro_exact(macro):
    """Whether ``macro`` is one keyboard_action takes, with no field more at any level, and one the macro editor
    shows as it is: a name that fits its line with no space around it, commands of steps with none either (the
    editor trims both), shell text that reads as it runs."""
    if not (isinstance(macro, dict) and macro.get("type") == "macro" and set(macro) <= _MACRO_FIELDS
            and keyboard_action(macro)):  # first: every "do" and "type" below is then one of the tables
        return False
    name = macro.get("name", "")
    if name != name.strip() or len(name.encode("utf-16-le")) > 2 * MACRO_NAME_MAX:
        return False
    for step in macro["steps"]:
        if set(step) != {"do", _STEP_FIELDS[step["do"]]}:
            return False
        if step["do"] == "click" and step["button"] not in ("BTN_LEFT", "BTN_RIGHT", "BTN_MIDDLE"):
            return False
        if step["do"] == "command" and not (step["command"] == step["command"].strip() and _shell_text(step["command"])):
            return False
        if step["do"] == "action":
            action = step["action"]
            if not (set(action) <= _STEP_ACTION_FIELDS[action["type"]]
                    and all(_shell_text(action[field]) for field in ("command", "script") if field in action)):
                return False
    return True


def import_macro(raw):
    """The macro in a macro file (its bytes), checked whole before any of it is used. Raises ValueError, with what
    to tell the user. Duplicate names in the JSON: the last one counts, as json takes it."""
    if len(raw) > SETTINGS_FILE_MAX:
        raise ValueError(_too_big(len(raw), macro=True))
    not_one = _("It is not a macro file of MX Options.")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):  # not UTF-8, not JSON, or nested past what the parser takes
        raise ValueError(not_one) from None
    if not (isinstance(document, dict) and set(document) == {"format", "version", "macro"}
            and document["format"] == MACRO_FORMAT and type(document["version"]) is int):
        raise ValueError(not_one)
    if document["version"] > 1:
        raise ValueError(_("It was made by a newer version of MX Options."))
    try:
        json.dumps(document, ensure_ascii=False).encode("utf-8")
    except (ValueError, RecursionError):  # a lone surrogate, which is no Unicode: the editor would drop it
        raise ValueError(not_one) from None
    if document["version"] != 1 or not _macro_exact(document["macro"]):
        raise ValueError(not_one)
    return document["macro"]


def export_macro(action):
    """The macro ``action`` as a file of its own (bytes, UTF-8 JSON), its commands and texts as written. Raises
    ValueError, with what to tell the user, for a macro import_macro would not take back."""
    if not (isinstance(action, dict) and action.get("steps")):
        raise ValueError(_("The macro has no complete step yet."))
    cannot = _("The macro holds something a macro file cannot.")
    try:
        raw = json.dumps({"format": MACRO_FORMAT, "version": 1, "macro": action}, indent=2,
                         ensure_ascii=False).encode("utf-8")
    except (ValueError, RecursionError):
        raise ValueError(cannot) from None
    if len(raw) > SETTINGS_FILE_MAX:
        raise ValueError(_too_big(len(raw), macro=True))
    try:
        import_macro(raw)  # every file written here can be imported
    except ValueError:
        raise ValueError(cannot) from None
    return raw


def read_settings(path, macro=False):
    """The bytes of the file ``path``, refused (ValueError) when larger than SETTINGS_FILE_MAX: never more than
    that is read. ``macro``: told as a macro file. Raises OSError."""
    # Not blocking: a FIFO opens at once and is then refused as not a file, instead of waiting for a writer.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(_("It is not a macro file of MX Options.") if macro
                             else _("It is not a settings file of MX Options."))
        if info.st_size > SETTINGS_FILE_MAX:
            raise ValueError(_too_big(info.st_size, macro))
        raw = f.read(SETTINGS_FILE_MAX + 1)  # it may have grown since
    if len(raw) > SETTINGS_FILE_MAX:
        raise ValueError(_too_big(len(raw), macro))
    return raw


class NotDurable(OSError):
    """The new file is in its place, but the system could not say that its being there is on the disk: after a
    crash the file may be the one from before."""


def write_durable(data, path):
    """Put ``data`` (bytes) in the file ``path`` so that it outlasts a crash, readable by the user alone: written
    whole under a name of its own next to it, synced, put in its place, and that synced too. Raises OSError with
    ``path`` as it was; NotDurable once the new file is in place.

    Linux: fsync of the file, rename, fsync of the folder. Windows: no folder can be synced there; the file is put
    in its place by MoveFileEx with MOVEFILE_WRITE_THROUGH, which returns once the move is on the disk."""
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=folder)  # 0600, O_EXCL
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if sys.platform == "win32":
            from . import windows

            windows.replace_file(tmp, path)
        else:
            os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _sync_folder(folder)


def remove_durable(path):
    """Take the file ``path`` away, durably as write_durable puts one there. Raises OSError; NotDurable once it is gone."""
    os.unlink(path)
    _sync_folder(os.path.dirname(os.path.abspath(path)))


def _sync_folder(folder):
    if sys.platform == "win32":
        return  # see write_durable; a removal there is as durable as the system makes it
    try:
        fd = os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as e:
        raise NotDurable(e.errno, e.strerror) from e


def seed_from_device(cfg, mouse):
    """First run: start from what the mouse is currently set to, so nothing changes under the hand."""
    try:
        cfg["pointer"]["dpi"] = mouse.get_dpi()
        if mouse.smartshift:
            mode, threshold, torque = mouse.get_smartshift()
            cfg["wheel"]["smartshift"] = mode == device.WHEEL_RATCHET and 0 < threshold < device.ALWAYS_RATCHET
            cfg["wheel"]["mode"] = "freespin" if mode == device.WHEEL_FREESPIN else "ratchet"
            if 0 < threshold <= 50:
                cfg["wheel"]["threshold"] = threshold
            if torque:
                cfg["wheel"]["torque"] = torque
        if mouse.wheel:
            cfg["wheel"]["natural"] = mouse.get_wheel_mode()[2]
        if mouse.thumbwheel:
            cfg["thumbwheel"]["invert"] = mouse.get_thumbwheel()[1]
        if mouse.haptic_waveforms:
            cfg["haptics"]["enabled"], cfg["haptics"]["level"] = mouse.get_haptic()
        if mouse.force:
            cfg["haptics"]["force"] = mouse.get_force()
    except Exception as e:
        log.warning("could not read current device settings: %s", e)
    return cfg
