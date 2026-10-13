"""Action catalogue (what a button, gesture or ring slot can do) and its executor.

An action is a plain JSON-able dict:
    {"type": "default"}                      leave the control to the mouse firmware
    {"type": "none"}                         swallow the press
    {"type": "keys", "keys": ["KEY_..."]}    keyboard shortcut (held while the button is held)
    {"type": "button", "button": "BTN_..."}  mouse button
    {"type": "shortcut", "component": c, "name": n}   KDE global shortcut via kglobalaccel
    {"type": "desktop", "do": "overview"}    what the desktop does in its own way (environment.py): only named here
    {"type": "command", "command": "..."}    shell command
    {"type": "script", "name": n, "script": "..."}    multi-line script (bash unless it has a #! line)
    {"type": "macro", "name": n, "repeat": bool, "steps": [...]}   sequence of steps, see MACRO_STEPS
    {"type": "wheel_mode"}                   toggle ratchet / free spin
    {"type": "dpi_cycle"}                    toggle between the two pointer speeds
    {"type": "dpi_shift"}                    precision speed while held
    {"type": "host", "host": n}              Easy-Switch to channel n
    {"type": "flow"}                         move to the other computer of Flow, clipboard included
    {"type": "gestures", "tap": a, "up": a, "down": a, "left": a, "right": a}
    {"type": "ring"}                         open the Actions Ring
    {"type": "hook", "name": "..."}          only announce it on D-Bus: another program decides what happens
"""

import hashlib
import logging
import os
import queue
import subprocess
import sys
import threading
import time

from . import environment, paths
from .i18n import _

log = logging.getLogger(__name__)

DIRECTIONS = ("tap", "up", "down", "left", "right")


def keys(*names):
    return {"type": "keys", "keys": [f"KEY_{n}" for n in names]}


def shortcut(component, name):
    return {"type": "shortcut", "component": component, "name": name}


def button(name):
    return {"type": "button", "button": name}


def desktop(name):
    """Something every desktop does in its own way: see environment.WAYS."""
    return environment.wanted(name)


DEFAULT = {"type": "default"}
NONE = {"type": "none"}

# Macro steps: {"do": kind, ...}
#   keys    {"keys": ["KEY_..."]}   tap a key combination
#   text    {"text": "..."}         insert text (through the clipboard, so any layout works)
#   delay   {"ms": 250}             wait
#   click   {"button": "BTN_LEFT"}  mouse click
#   command {"command": "..."}      shell command
MACRO_STEPS = ("keys", "text", "delay", "click", "command")
MAX_MACRO_STEPS = 64
MAX_DELAY_MS = 10000

# (id, category, label, icon, action)
_PRESETS = [
    ("default", "mouse", "Default", "input-mouse", DEFAULT),
    ("left_click", "mouse", "Left click", "input-mouse", button("BTN_LEFT")),
    ("right_click", "mouse", "Right click", "input-mouse", button("BTN_RIGHT")),
    ("middle_click", "mouse", "Middle click", "input-mouse", button("BTN_MIDDLE")),
    ("back", "mouse", "Back", "go-previous", button("BTN_SIDE")),
    ("forward", "mouse", "Forward", "go-next", button("BTN_EXTRA")),
    ("gestures", "mouse", "Gestures", "transform-move", {"type": "gestures"}),
    ("ring", "mouse", "Actions Ring", "view-grid", {"type": "ring"}),
    ("wheel_mode", "mouse", "Switch wheel mode", "media-playlist-repeat", {"type": "wheel_mode"}),
    ("dpi_cycle", "mouse", "Switch pointer speed", "speedometer", {"type": "dpi_cycle"}),
    ("dpi_shift", "mouse", "Precision mode (hold)", "crosshairs", {"type": "dpi_shift"}),
    ("none", "mouse", "Do nothing", "dialog-cancel", NONE),

    ("overview", "desktop", "Overview", "view-app-grid", desktop("overview")),
    ("all_windows", "desktop", "All windows", "view-list-icons", desktop("all_windows")),
    ("desktop_grid", "desktop", "Desktop grid", "view-grid", desktop("desktop_grid")),
    ("show_desktop", "desktop", "Show desktop", "user-desktop", desktop("show_desktop")),
    ("desktop_left", "desktop", "Desktop to the left", "go-previous", desktop("desktop_left")),
    ("desktop_right", "desktop", "Desktop to the right", "go-next", desktop("desktop_right")),
    ("desktop_up", "desktop", "Desktop above", "go-up", desktop("desktop_up")),
    ("desktop_down", "desktop", "Desktop below", "go-down", desktop("desktop_down")),
    ("switch_window", "desktop", "Switch window", "preferences-system-windows", keys("LEFTALT", "TAB")),
    ("launcher", "desktop", "Application launcher", "start-here", desktop("launcher")),
    ("krunner", "desktop", "Search or run", "system-search", desktop("krunner")),

    ("win_close", "window", "Close window", "window-close", desktop("win_close")),
    ("win_maximize", "window", "Maximize window", "window-maximize", desktop("win_maximize")),
    ("win_minimize", "window", "Minimize window", "window-minimize", desktop("win_minimize")),
    ("win_fullscreen", "window", "Fullscreen", "view-fullscreen", desktop("win_fullscreen")),
    ("win_tile_left", "window", "Tile window left", "go-first", desktop("win_tile_left")),
    ("win_tile_right", "window", "Tile window right", "go-last", desktop("win_tile_right")),
    ("win_above", "window", "Keep window on top", "window-keep-above", desktop("win_above")),

    ("copy", "edit", "Copy", "edit-copy", keys("LEFTCTRL", "C")),
    ("paste", "edit", "Paste", "edit-paste", keys("LEFTCTRL", "V")),
    ("cut", "edit", "Cut", "edit-cut", keys("LEFTCTRL", "X")),
    ("undo", "edit", "Undo", "edit-undo", keys("LEFTCTRL", "Z")),
    ("redo", "edit", "Redo", "edit-redo", keys("LEFTCTRL", "LEFTSHIFT", "Z")),
    ("select_all", "edit", "Select all", "edit-select-all", keys("LEFTCTRL", "A")),
    ("find", "edit", "Find", "edit-find", keys("LEFTCTRL", "F")),
    ("save", "edit", "Save", "document-save", keys("LEFTCTRL", "S")),
    ("delete", "edit", "Delete", "edit-delete", keys("DELETE")),
    ("enter", "edit", "Enter", "keyboard-enter", keys("ENTER")),
    ("escape", "edit", "Escape", "dialog-close", keys("ESC")),

    ("tab_new", "browser", "New tab", "tab-new", keys("LEFTCTRL", "T")),
    ("tab_close", "browser", "Close tab", "tab-close", keys("LEFTCTRL", "W")),
    ("tab_reopen", "browser", "Reopen closed tab", "edit-undo", keys("LEFTCTRL", "LEFTSHIFT", "T")),
    ("tab_next", "browser", "Next tab", "go-next", keys("LEFTCTRL", "TAB")),
    ("tab_prev", "browser", "Previous tab", "go-previous", keys("LEFTCTRL", "LEFTSHIFT", "TAB")),
    ("reload", "browser", "Reload", "view-refresh", keys("F5")),
    ("zoom_in", "browser", "Zoom in", "zoom-in", keys("LEFTCTRL", "EQUAL")),
    ("zoom_out", "browser", "Zoom out", "zoom-out", keys("LEFTCTRL", "MINUS")),
    ("zoom_reset", "browser", "Reset zoom", "zoom-original", keys("LEFTCTRL", "0")),

    ("play_pause", "media", "Play / Pause", "media-playback-start", keys("PLAYPAUSE")),
    ("next_track", "media", "Next track", "media-skip-forward", keys("NEXTSONG")),
    ("prev_track", "media", "Previous track", "media-skip-backward", keys("PREVIOUSSONG")),
    ("volume_up", "media", "Volume up", "audio-volume-high", keys("VOLUMEUP")),
    ("volume_down", "media", "Volume down", "audio-volume-low", keys("VOLUMEDOWN")),
    ("mute", "media", "Mute", "audio-volume-muted", keys("MUTE")),
    ("mic_mute", "media", "Mute microphone", "microphone-sensitivity-muted", keys("MICMUTE")),
    ("brightness_up", "media", "Brightness up", "brightness-high", keys("BRIGHTNESSUP")),
    ("brightness_down", "media", "Brightness down", "brightness-low", keys("BRIGHTNESSDOWN")),

    ("screenshot_region", "system", "Screenshot (region)", "select-rectangular", desktop("screenshot_region")),
    ("screenshot_full", "system", "Screenshot (full screen)", "camera-photo", desktop("screenshot_full")),
    ("screenshot_window", "system", "Screenshot (window)", "window", desktop("screenshot_window")),
    ("lock", "system", "Lock screen", "system-lock-screen", desktop("lock")),
    ("clipboard", "system", "Clipboard history", "klipper", desktop("clipboard")),
    ("emoji", "system", "Emoji picker", "face-smile", desktop("emoji")),
    ("files", "system", "File manager", "system-file-manager", desktop("files")),
    ("terminal", "system", "Terminal", "utilities-terminal", desktop("terminal")),
    ("calculator", "system", "Calculator", "accessories-calculator", keys("CALC")),
    ("dnd", "system", "Do not disturb", "notifications-disabled", desktop("dnd")),
    ("night_light", "system", "Night light", "redshift-status-on", desktop("night_light")),

    ("host_1", "easyswitch", "Easy-Switch 1", "computer", {"type": "host", "host": 0}),
    ("host_2", "easyswitch", "Easy-Switch 2", "computer", {"type": "host", "host": 1}),
    ("host_3", "easyswitch", "Easy-Switch 3", "computer", {"type": "host", "host": 2}),
    ("flow", "easyswitch", "Move to the other computer (Flow)", "transform-move", {"type": "flow"}),
]

CATEGORIES = [
    ("mouse", "Mouse"),
    ("desktop", "Desktop"),
    ("window", "Windows"),
    ("edit", "Editing"),
    ("browser", "Browser"),
    ("media", "Media"),
    ("system", "System"),
    ("easyswitch", "Easy-Switch"),
]


# Dials for the Actions Ring: point at the bubble and turn the scroll wheel.
# id -> (label, icon, action for wheel up, action for wheel down, action for a click or None)
SCROLLS = {
    "volume": ("Volume", "audio-volume-high", keys("VOLUMEUP"), keys("VOLUMEDOWN"), keys("MUTE")),
    "brightness": ("Brightness", "brightness-high", keys("BRIGHTNESSUP"), keys("BRIGHTNESSDOWN"), None),
    "zoom": ("Zoom", "zoom-in", keys("LEFTCTRL", "EQUAL"), keys("LEFTCTRL", "MINUS"), keys("LEFTCTRL", "0")),
    "tabs": ("Switch tabs", "tab-new", keys("LEFTCTRL", "LEFTSHIFT", "TAB"), keys("LEFTCTRL", "TAB"), None),
    "tracks": ("Tracks", "media-skip-forward", keys("NEXTSONG"), keys("PREVIOUSSONG"), keys("PLAYPAUSE")),
    "seek": ("Seek", "media-seek-forward", keys("RIGHT"), keys("LEFT"), keys("SPACE")),
    "undo": ("Undo / redo", "edit-undo", keys("LEFTCTRL", "LEFTSHIFT", "Z"), keys("LEFTCTRL", "Z"), None),
    "history": ("Back / forward", "go-previous", keys("LEFTALT", "RIGHT"), keys("LEFTALT", "LEFT"), None),
    "desktops": ("Switch desktops", "user-desktop", desktop("desktop_previous"), desktop("desktop_next"), desktop("overview")),
    "win_desktop": ("Window to another desktop", "preferences-system-windows-move",
                    desktop("window_desktop_previous"), desktop("window_desktop_next"), None),
    # for the Krohnkite tiling script of KDE Plasma (they do nothing where it is not installed)
    "tile_layout": ("Tiling: layout", "view-grid", shortcut("kwin", "KrohnkitePreviousLayout"),
                    shortcut("kwin", "KrohnkiteNextLayout"), shortcut("kwin", "KrohnkiteMonocleLayout")),
    "tile_width": ("Tiling: width", "transform-scale", shortcut("kwin", "KrohnkitegrowWidth"),
                   shortcut("kwin", "KrohnkiteShrinkWidth"), shortcut("kwin", "KrohnkiteSetMaster")),
    "tile_focus": ("Tiling: focus", "focus-windows", shortcut("kwin", "KrohnkiteFocusPrev"),
                   shortcut("kwin", "KrohnkiteFocusNext"), shortcut("kwin", "KrohnkiteToggleFloat")),
}


class Preset:
    def __init__(self, pid, category, label, icon, action):
        self.id = pid
        self.category = category
        self.label = label
        self.icon = icon
        self.action = action

    @property
    def available(self):
        """Whether this desktop has a way of doing it."""
        return environment.available(self.action)


PRESETS = [Preset(*p) for p in _PRESETS]
PRESET_BY_ID = {p.id: p for p in PRESETS}


def dial_available(name):
    """Whether a ring dial can do anything on this desktop: turning it one way at least has to."""
    _label, _icon, up, down, _click = SCROLLS[name]
    return environment.available(up) and environment.available(down)


# Presets that only make sense on a physical button, not inside a gesture or ring slot.
BUTTON_ONLY = {"default", "gestures", "ring", "dpi_shift"}

_KEY_LABELS = {
    "LEFTCTRL": "Ctrl", "RIGHTCTRL": "Ctrl", "LEFTSHIFT": "Shift", "RIGHTSHIFT": "Shift",
    "LEFTALT": "Alt", "RIGHTALT": "AltGr", "LEFTMETA": "Meta", "RIGHTMETA": "Meta",
    "ESC": "Esc", "ENTER": "Enter", "BACKSPACE": "Backspace", "DELETE": "Del", "INSERT": "Ins",
    "PAGEUP": "PgUp", "PAGEDOWN": "PgDn", "SPACE": "Space", "TAB": "Tab", "EQUAL": "=", "MINUS": "-",
    "COMMA": ",", "DOT": ".", "SLASH": "/", "BACKSLASH": "\\", "SEMICOLON": ";", "APOSTROPHE": "'",
    "LEFTBRACE": "[", "RIGHTBRACE": "]", "GRAVE": "`", "SYSRQ": "PrtSc", "PLAYPAUSE": "Play/Pause",
    "NEXTSONG": "Next", "PREVIOUSSONG": "Previous", "VOLUMEUP": "Vol+", "VOLUMEDOWN": "Vol-",
    "LEFT": "←", "RIGHT": "→", "UP": "↑", "DOWN": "↓",
}


def keys_label(key_names):
    parts = []
    for name in key_names:
        short = name[4:] if name.startswith("KEY_") else name
        parts.append(_KEY_LABELS.get(short, short.capitalize() if len(short) > 1 else short))
    return " + ".join(parts)


def find_preset(action):
    """Return the catalogue preset equal to ``action`` (gestures match by type), or None."""
    if not action:
        return None
    if action.get("type") == "gestures":
        return PRESET_BY_ID["gestures"]
    for preset in PRESETS:
        if preset.action == action:
            return preset
    return None


def describe(action):
    """Human-readable label of an action."""
    preset = find_preset(action)
    if preset:
        return _(preset.label)
    kind = (action or {}).get("type")
    if kind == "keys":
        return keys_label(action.get("keys", [])) or _("Keyboard shortcut")
    if kind == "command":
        command = action.get("command", "").strip()
        return (command if len(command) <= 28 else command[:27] + "…") or _("Run command")
    if kind == "script":
        return action.get("name", "").strip() or _("Script")
    if kind == "macro":
        return action.get("name", "").strip() or _("Macro")
    if kind == "shortcut":
        return action.get("name", "")
    if kind == "desktop":
        return str(action.get("do", "")).replace("_", " ").capitalize()
    if kind == "button":
        return action.get("button", "")
    if kind == "hook":
        return action.get("name", "").strip() or _("Signal to another program")
    return _("Do nothing")


def icon_name(action):
    preset = find_preset(action)
    if preset:
        return preset.icon
    kind = (action or {}).get("type")
    return {"keys": "input-keyboard", "command": "system-run", "script": "text-x-script", "macro": "media-record",
            "hook": "network-connect"}.get(kind, "configure")


def describe_step(step):
    kind = step.get("do")
    if kind == "keys":
        return keys_label(step.get("keys", [])) or "…"
    if kind == "text":
        text = step.get("text", "")
        return text if len(text) <= 30 else text[:29] + "…"
    if kind == "delay":
        return f"{step.get('ms', 0)} ms"
    if kind == "click":
        return {"BTN_LEFT": _("Left click"), "BTN_RIGHT": _("Right click"), "BTN_MIDDLE": _("Middle click")}.get(step.get("button"), "")
    if kind == "command":
        return step.get("command", "")
    return ""


def invoke_kde_shortcut(component, name):
    try:
        from PySide6.QtDBus import QDBus, QDBusConnection, QDBusMessage

        message = QDBusMessage.createMethodCall(
            "org.kde.kglobalaccel", f"/component/{component}", "org.kde.kglobalaccel.Component", "invokeShortcut"
        )
        message.setArguments([name])
        QDBusConnection.sessionBus().call(message, QDBus.CallMode.NoBlock)
    except Exception:
        log.exception("could not invoke %s/%s", component, name)


def _spawn(argv, **kwargs):
    """Start a program that goes its own way: it neither ends with this one nor shares its console."""
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
    except OSError:
        log.exception("could not run %r", argv)


def run_command(command):
    if command.strip():
        _spawn(command, shell=True)


def run_script(script):
    """Run a multi-line script: with its own interpreter if it starts with #!, otherwise with bash. On Windows
    a script is PowerShell's."""
    text = script.strip()
    if not text:
        return
    if sys.platform == "win32":
        _spawn(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", text])
        return
    if not text.startswith("#!"):
        _spawn(["bash", "-c", text])
        return
    folder = paths.RUNTIME
    path = os.path.join(folder, "script-" + hashlib.sha1(text.encode()).hexdigest()[:16])
    try:
        os.makedirs(folder, mode=0o700, exist_ok=True)
        if not os.path.exists(path):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
            with os.fdopen(fd, "w") as f:
                f.write(text + "\n")
    except OSError:
        log.exception("could not prepare the script")
        return
    _spawn([path])


def insert_text(vinput, text):
    """Put ``text`` at the cursor: paste it through the clipboard, restoring what was there."""
    if not text or vinput is None:
        return
    from . import clipboard  # it needs Qt, which the rest of this module does without

    previous = clipboard.get_text()
    if previous is None or not clipboard.set_text(text):
        vinput.type_ascii(text)  # no way to the clipboard from here: plain typing, US layout only
        return
    time.sleep(0.07)
    vinput.tap(["KEY_LEFTCTRL", "KEY_V"])
    time.sleep(0.18)
    clipboard.set_text(previous)


def here(action):
    """``action``, or if it only names something the desktop does in its own way, what does that on this desktop
    (nothing, where the desktop has no way)."""
    if (action or {}).get("type") == "desktop":
        return environment.resolve(action.get("do")) or NONE
    return action


class MacroRunner:
    """Plays macros on its own thread so delays never stall button handling."""

    def __init__(self, vinput):
        self.vinput = vinput
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = None

    def run(self, action, held=None):
        """Queue a macro. With ``held`` (an Event) a repeating macro loops until it is cleared."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="macro", daemon=True)
            self._thread.start()
        if self._queue.qsize() < 4:  # a stuck macro must not pile up presses
            self._queue.put((action, held))

    def stop(self):
        self._stop.set()
        self._queue.put(None)

    def _loop(self):
        while not self._stop.is_set():
            job = self._queue.get()
            if job is None:
                return
            action, held = job
            steps = [s for s in action.get("steps", []) if isinstance(s, dict)][:MAX_MACRO_STEPS]
            try:
                while True:
                    for step in steps:
                        if self._stop.is_set():
                            return
                        self._step(step)
                    if not (action.get("repeat") and held is not None and held.is_set() and steps):
                        break
                    self._stop.wait(0.02)
            except Exception:
                log.exception("macro failed")

    def _step(self, step):
        kind = step.get("do")
        if kind == "delay":
            self._stop.wait(max(0, min(MAX_DELAY_MS, int(step.get("ms", 0)))) / 1000.0)
            return
        if kind == "keys" and self.vinput:
            self.vinput.tap(step.get("keys", []))
        elif kind == "click" and self.vinput:
            self.vinput.tap([step.get("button", "BTN_LEFT")])
        elif kind == "text":
            insert_text(self.vinput, step.get("text", ""))
        elif kind == "command":
            run_command(step.get("command", ""))
        self._stop.wait(0.02)  # let the application digest the event


class Executor:
    """Runs actions. ``device_action(action, phase)`` handles the ones that talk to the mouse and
    ``hook(name, phase)`` the ones that are only announced to other programs;
    phase is "press", "release" or "trigger"."""

    def __init__(self, vinput, device_action, hook=None):
        self.vinput = vinput
        self.device_action = device_action
        self.hook = hook or (lambda name, phase: None)
        self.macros = MacroRunner(vinput)
        self._macro_holds = {}  # id(action) -> Event set while its button is down

    def close(self):
        self.macros.stop()

    def press(self, action):
        if action.get("type") == "desktop":
            self.trigger(action)  # done once, however long the button is held: a held key combination would repeat
            return
        kind = action.get("type")
        if kind == "keys":
            if self.vinput:
                self.vinput.press(action.get("keys", []))
        elif kind == "button":
            if self.vinput:
                self.vinput.press([action["button"]])
        elif kind in ("wheel_mode", "dpi_cycle", "dpi_shift", "host", "flow"):
            self.device_action(action, "press")
        elif kind == "hook":
            self.hook(action.get("name", ""), "press")
        elif kind == "macro":
            held = threading.Event()
            held.set()
            self._macro_holds[id(action)] = held
            self.macros.run(action, held)
        else:
            self._oneshot(action)

    def release(self, action):
        kind = action.get("type")
        if kind == "keys":
            if self.vinput:
                self.vinput.release(action.get("keys", []))
        elif kind == "button":
            if self.vinput:
                self.vinput.release([action["button"]])
        elif kind == "dpi_shift":
            self.device_action(action, "release")
        elif kind == "hook":
            self.hook(action.get("name", ""), "release")
        elif kind == "macro":
            held = self._macro_holds.pop(id(action), None)
            if held:
                held.clear()

    def trigger(self, action):
        """Press and release in one go (gestures, ring bubbles, thumb wheel steps)."""
        action = here(action)
        kind = (action or {}).get("type")
        if kind == "keys":
            if self.vinput:
                self.vinput.tap(action.get("keys", []))
        elif kind == "button":
            if self.vinput:
                self.vinput.tap([action["button"]])
        elif kind in ("wheel_mode", "dpi_cycle", "host", "flow"):
            self.device_action(action, "trigger")
        elif kind == "hook":
            self.hook(action.get("name", ""), "trigger")
        elif kind == "macro":
            self.macros.run(action)
        elif kind:
            self._oneshot(action)

    def _oneshot(self, action):
        kind = action.get("type")
        if kind == "shortcut":
            invoke_kde_shortcut(action["component"], action["name"])
        elif kind == "command":
            run_command(action.get("command", ""))
        elif kind == "script":
            run_script(action.get("script", ""))
