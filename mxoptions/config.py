"""Settings file (~/.config/mxoptions/config.json): defaults, load, save."""

import copy
import json
import logging
import os

from . import APP_ID, device, flow, gestures
from .actions import DEFAULT, PRESET_BY_ID, SCROLLS

log = logging.getLogger(__name__)

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), APP_ID)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

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

THUMB_FUNCTIONS = ("hscroll", "zoom", "volume", "tabs", "desktops", "brightness")
WHEEL_CLICK_MODES = ("off", "haptic", "sound", "both")
WHEEL_SOUNDS = ("tick", "clack", "relay", "soft", "custom")
WHEEL_MOMENTS = ("both", "free", "ratchet")


def _a(preset_id):
    return copy.deepcopy(PRESET_BY_ID[preset_id].action)


def default_gestures():
    return {
        "type": "gestures",
        "tap": _a("overview"),
        "up": _a("all_windows"),
        "down": _a("show_desktop"),
        "left": _a("desktop_left"),
        "right": _a("desktop_right"),
    }


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


def default_bubbles():
    return [
        bubble("copy"),
        bubble("paste"),
        bubble("screenshot_region"),
        folder("Media", [bubble(pid) for pid in ("play_pause", "next_track", "volume_up", "mute", "volume_down", "prev_track")],
               icon="applications-multimedia"),
        bubble("overview"),
        bubble("emoji"),
        bubble("lock"),
        bubble("krunner"),
    ]


def _ring(*items):
    """Bubbles from preset ids. "~id" makes a dial, a (label, icon, [ids]) tuple a folder."""
    def one(item):
        if isinstance(item, tuple):
            return folder(item[0], [one(child) for child in item[2]], icon=item[1])
        return dial(item[1:]) if item.startswith("~") else bubble(item)
    return [one(item) for item in items]


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
    ("pro", "Pro", pro_bubbles),
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
MOUSE_COLORS = ("graphite", "pale-grey", "black")
GESTURE_BUTTONS = ("gesture", "panel")  # the two thumb buttons whose roles can be swapped
RING_STYLES = ("bubbles", "pie", "grid")
MAX_SAVED_RINGS = 16


DEFAULTS = {
    "version": 5,
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
                   "ticks": True},  # a vibration tick for every step of a stepped function
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
    "haptics": {"enabled": True, "level": 60, "force": None, "feedback": True},
    "ring": {"size": 1.0, "style": "bubbles", "bubbles": default_bubbles(), "saved": []},
    "gestures": {
        "trigger": "auto",    # release: judged when the button comes up | auto: a long clear swipe fires at once
                              # | repeat: like auto, and it fires again for every further step
        "min_mm": 2.5,        # shorter than this is a tap
        "far_mm": 16.0,       # travel after which a swipe fires while the button is still held
        "step_mm": 8.0,       # repeat: travel between two repeats
        "dead": 5,            # degrees on either side of the diagonal where a swipe is ignored
        "bias": 0,            # degrees: > 0 gives sideways swipes more room, < 0 up/down swipes
        "haptic": True,       # vibration during gestures, as set out in "cues"
        "cues": dict(gestures.CUE_PRESETS["normal"], up=0x04, down=0x04, left=0x04, right=0x04),
        "per_direction": False,  # use the up/down/left/right cues instead of the one "swipe" cue
    },
    "app": {"language": "auto", "theme": "graphite", "accent": "#2fd6a7", "model": "photo", "mouse_color": "graphite",
            "api": True},  # let other programs talk to the app over D-Bus
    # One mouse on two computers, see flow.py
    "flow": {
        "enabled": False,
        "peer": "",           # the other computer's address (name or IP, ":port" allowed); one of the two may leave it empty
        "key": "",            # pairing key, the same on both computers
        "port": flow.PORT,    # where this computer listens for the other one
        "edge": "right",      # the edge of this desktop the other computer is behind: left | right | top | bottom
        "edge_at": 0.0,       # when that was chosen (Unix time): of the two computers, the later choice decides
        "push_mm": 10.0,      # how far the mouse has to be pushed on, once the pointer is at the edge, to cross
        "corner": 10,         # percent of the edge at each of its ends where pushing does nothing (hot corners live there)
        "fullscreen": False,  # cross even while a full-screen window (a game, a film) is in front
        "clipboard": True,    # hand the copied text or picture over with the pointer
        "files": True,        # ... and copied files and folders
        "files_max_mb": 2048,  # at most this much in one go, sending or receiving
        "peer_name": "",      # what the other computer calls itself, noted when pairing and whenever it connects
        "channel": -1,        # this computer's Easy-Switch channel, noted whenever the mouse is here (-1: not seen yet)
    },
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


def _migrate_ring(ring):
    """Version 1 stored a flat list of eight slots."""
    slots = ring.get("slots")
    if not isinstance(slots, list):
        return ring.get("bubbles")
    actions = [s.get("action") for s in slots if isinstance(s, dict)]
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
            clean["action"] = item["action"]
        else:
            clean["action"] = {"type": "none"}
        result.append(clean)
    return result[:MAX_BUBBLES]


def _sanitize(cfg):
    for key in BUTTONS:
        action = cfg["buttons"].get(key)
        if not isinstance(action, dict) or "type" not in action:
            cfg["buttons"][key] = copy.deepcopy(DEFAULTS["buttons"][key])
        elif action["type"] == "gestures":
            clean = {"type": "gestures"}
            for direction in ("tap", "up", "down", "left", "right"):
                sub = action.get(direction)
                clean[direction] = sub if isinstance(sub, dict) and "type" in sub else {"type": "none"}
            cfg["buttons"][key] = clean
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
    if cfg["app"].get("mouse_color") not in MOUSE_COLORS:
        cfg["app"]["mouse_color"] = DEFAULTS["app"]["mouse_color"]
    cfg["app"].pop("gesture_distance", None)
    if cfg["gestures"].get("trigger") not in gestures.TRIGGERS:
        cfg["gestures"]["trigger"] = DEFAULTS["gestures"]["trigger"]
    cfg["gestures"]["haptic"] = bool(cfg["gestures"].get("haptic"))
    cfg["gestures"]["per_direction"] = bool(cfg["gestures"].get("per_direction"))
    cues = cfg["gestures"].get("cues")
    cues = cues if isinstance(cues, dict) else {}
    cfg["gestures"]["cues"] = {
        name: _waveform(cues.get(name, default), default) for name, default in DEFAULTS["gestures"]["cues"].items()
    }
    _clamp(cfg, "gestures", "min_mm", 1.0, 12.0, float)
    _clamp(cfg, "gestures", "far_mm", 6.0, 45.0, float)
    _clamp(cfg, "gestures", "step_mm", 2.0, 30.0, float)
    _clamp(cfg, "gestures", "dead", 0, 30)
    _clamp(cfg, "gestures", "bias", -25, 25)
    cfg["gestures"]["far_mm"] = max(cfg["gestures"]["far_mm"], cfg["gestures"]["min_mm"] + 2.0)
    if cfg["thumbwheel"].get("function") not in THUMB_FUNCTIONS:
        cfg["thumbwheel"]["function"] = "hscroll"
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
    for key in ("enabled", "clipboard", "files", "fullscreen"):
        link[key] = bool(link.get(key))
    for key in ("peer", "key", "peer_name"):
        link[key] = link[key].strip()[:200] if isinstance(link.get(key), str) else ""
    _clamp(cfg, "flow", "push_mm", 0.0, 60.0, float)
    _clamp(cfg, "flow", "corner", 0, 40)
    _clamp(cfg, "flow", "files_max_mb", 1, 65536)
    if link.get("edge") not in flow.EDGES:
        link["edge"] = DEFAULTS["flow"]["edge"]
    _clamp(cfg, "flow", "port", 1024, 65535)
    _clamp(cfg, "flow", "edge_at", 0.0, 4.0e9, float)
    _clamp(cfg, "flow", "channel", -1, 15)
    for key in ("dpi", "alt_dpi", "precision_dpi"):
        _clamp(cfg, "pointer", key, 100, 32000)
    _clamp(cfg, "wheel", "threshold", 1, 50)
    _clamp(cfg, "wheel", "torque", 1, 100)
    _clamp(cfg, "wheel", "speed", 0.25, 4.0, float)
    _clamp(cfg, "thumbwheel", "speed", 0.25, 4.0, float)
    _clamp(cfg, "haptics", "level", 1, 100)
    if cfg["haptics"].get("force") is not None:
        _clamp(cfg, "haptics", "force", 0, 0xFFFF)
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
    if stored_version is not None and stored_version != DEFAULTS["version"]:
        try:
            save(cfg)  # keep the file in the current format once it has been migrated
        except OSError as e:
            log.warning("could not save the migrated settings: %s", e)
    return cfg


def save(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    # Readable by the user alone: the file holds the pairing key of Flow.
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        os.fchmod(f.fileno(), 0o600)  # a file left over from an earlier attempt keeps its old mode otherwise
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)


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
