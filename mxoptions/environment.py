"""Which desktop this is, and how to ask it for the things every desktop does in its own way.

"Show the overview" is a global shortcut of KWin on KDE Plasma, a key combination on GNOME, a
command for sway or Hyprland, and something else again elsewhere. The catalogue of actions only
names what is wanted, {"type": "desktop", "do": "overview"}; this module turns that into what
does it here. What a desktop has no way of doing is not offered there.

Only KDE Plasma was at hand when this was written. The ways of the other desktops are their
documented defaults: a key combination that has been rebound there will not do its job."""

import glob
import json
import logging
import os
import shutil
import socket
import struct
import sys

KDE, GNOME, SWAY, HYPRLAND, WINDOWS, OTHER = "kde", "gnome", "sway", "hyprland", "windows", "other"
DESKTOPS = (KDE, GNOME, SWAY, HYPRLAND, WINDOWS, OTHER)
log = logging.getLogger(__name__)
_current = None
_globals = None


def _find_sockets():
    """sway and Hyprland say where they are in a variable, which a program started as a service may not have been
    given. Their sockets are in a known place all the same: where there is exactly one, the variable is set
    from it, for this program and for the swaymsg and hyprctl it runs."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or ""
    if not runtime:
        return
    if not os.environ.get("SWAYSOCK"):
        found = glob.glob(os.path.join(runtime, f"sway-ipc.{os.getuid()}.*.sock"))
        if len(found) == 1:
            os.environ["SWAYSOCK"] = found[0]
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        found = glob.glob(os.path.join(runtime, "hypr", "*", ".socket.sock"))
        if len(found) == 1:
            os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = os.path.basename(os.path.dirname(found[0]))


def current():
    """The desktop this session runs. MXOPTIONS_DESKTOP overrides what the session says."""
    global _current
    if _current is None:
        forced = os.environ.get("MXOPTIONS_DESKTOP", "").strip().lower()
        names = os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":")
        if forced in DESKTOPS:
            _current = forced
        elif sys.platform == "win32":
            _current = WINDOWS
        elif "kde" in names:
            _current = KDE
        elif any(name in names for name in ("gnome", "unity", "ubuntu")):
            _current = GNOME
        else:
            _find_sockets()
            if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or "hyprland" in names:
                _current = HYPRLAND
            elif os.environ.get("SWAYSOCK") or "sway" in names:
                _current = SWAY
            else:
                _current = OTHER
    return _current


def session():
    """"wayland", "x11", or "windows"."""
    if sys.platform == "win32":
        return "windows"
    kind = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if kind in ("wayland", "x11"):
        return kind
    return "wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11"


def wayland_globals(timeout=1.0):
    """What the Wayland compositor of this session offers: the names of its global interfaces
    ("zwlr_layer_shell_v1", "ext_data_control_manager_v1", ...), empty where there is none to ask.
    Asked for over a connection of its own, in the protocol's own few bytes."""
    global _globals
    if _globals is None:
        _globals = frozenset()
        name = os.environ.get("WAYLAND_DISPLAY") or "wayland-0"
        path = name if os.path.isabs(name) else os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), name)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as link:
                link.settimeout(timeout)
                link.connect(path)
                # wl_display (object 1): get_registry as object 2, then sync as object 3, which answers once all is told
                link.sendall(struct.pack("=IHHI", 1, 1, 12, 2) + struct.pack("=IHHI", 1, 0, 12, 3))
                _globals = frozenset(_read_globals(link))
        except (OSError, struct.error, ValueError) as error:
            log.debug("no Wayland compositor to ask: %s", error)
    return _globals


def _read_globals(link):
    found, buffered = [], b""
    while True:
        while len(buffered) < 8:
            more = link.recv(65536)
            if not more:
                return found
            buffered += more
        target, opcode, size = struct.unpack_from("=IHH", buffered)
        if size < 8:
            raise ValueError("not the Wayland protocol")
        while len(buffered) < size:
            more = link.recv(65536)
            if not more:
                return found
            buffered += more
        body, buffered = buffered[8:size], buffered[size:]
        if target == 3 or (target == 1 and opcode == 0):  # the callback is done, or the display reports an error
            return found
        if target == 2 and opcode == 0:  # wl_registry.global: name, interface (a string with its length), version
            length = struct.unpack_from("=I", body, 4)[0]
            found.append(body[8:8 + length].rstrip(b"\0").decode("ascii", "replace"))


# Other programs that set a Logitech mouse up. Two doing that at once undo each other's work: each puts the
# buttons, the wheel and the vibration back the way it wants them. Name of the running program -> what it is called.
RIVALS = {"logioptionsplus_agent.exe": "Logi Options+"} if sys.platform == "win32" else {"logid": "logiops", "solaar": "Solaar"}


def _running():
    """The names of the programs that are running."""
    if sys.platform == "win32":
        from . import windows

        return windows.running_programs()
    names = set()
    for entry in glob.glob("/proc/[0-9]*/comm"):
        try:
            with open(entry) as f:
                names.add(f.read().strip())
        except OSError:
            pass
    return names


def rivals(running=None):
    """What other mouse software is running now, by the names people know it by."""
    try:
        running = _running() if running is None else running
    except Exception as error:  # never worth stopping for
        log.debug("cannot tell which programs are running: %s", error)
        return []
    return sorted(called for program, called in RIVALS.items() if program in running)


def places_pointer():
    """Whether the desktop lets a program put the pointer somewhere and say where it is: X11 and Windows do."""
    return session() in ("x11", "windows")


def _keys(*names):
    return {"type": "keys", "keys": [f"KEY_{name}" for name in names]}


def _kde(component, name):
    return {"type": "shortcut", "component": component, "name": name}


def _run(command):
    return {"type": "command", "command": command}


def _tool(*commands):
    """The first of ``commands`` whose program is installed here; none of them is a way if none is.
    A command names its program first, or after "needs" the programs it takes."""
    return {"type": "tool", "commands": [command if isinstance(command, tuple) else (command.split()[:1], command)
                                         for command in commands]}


def _toggle(schema, key):
    """A GNOME setting switched over."""
    return _run(f'[ "$(gsettings get {schema} {key})" = true ] && gsettings set {schema} {key} false '
                f'|| gsettings set {schema} {key} true')


_SHOT = '"$(xdg-user-dir PICTURES 2>/dev/null || echo "$HOME")/screenshot-$(date +%Y%m%d-%H%M%S).png"'
_LOCK = _run("loginctl lock-session")
_FILES = _run('xdg-open "$HOME"')
_TERMINAL = _tool("xdg-terminal-exec", "x-terminal-emulator", "ptyxis", "kgx", "gnome-terminal", "konsole", "foot",
                  "alacritty", "kitty", "wezterm", "xfce4-terminal", "xterm")
_LAUNCHER = _tool("fuzzel", "wofi --show drun", "rofi -show drun", "tofi-drun --drun-launch=true", "bemenu-run")
_QUIET = _tool("swaync-client -d -sw", "dunstctl set-paused toggle", "makoctl mode -t do-not-disturb")
_REGION = _tool((("grim", "slurp"), f'grim -g "$(slurp)" {_SHOT}'))
_SCREEN = _tool(f"grim {_SHOT}")
# What is wanted -> desktop -> the action that does it there. "*" stands for every desktop not named;
# None says that a desktop has no way of doing it. The key combinations are the ones a desktop comes
# with: GNOME's as upstream sets them, where it sets one (show desktop is Ubuntu's); Windows' are its own.
WAYS = {
    "overview": {KDE: _kde("kwin", "Overview"), GNOME: _keys("LEFTMETA", "S")},
    "all_windows": {KDE: _kde("kwin", "ExposeAll"), GNOME: _keys("LEFTMETA", "S")},
    "desktop_grid": {KDE: _kde("kwin", "Grid View")},
    "show_desktop": {KDE: _kde("kwin", "Show Desktop"), GNOME: _keys("LEFTMETA", "D"), SWAY: None, HYPRLAND: None,
                     "*": _keys("LEFTCTRL", "LEFTALT", "D")},
    "desktop_left": {KDE: _kde("kwin", "Switch One Desktop to the Left"), GNOME: _keys("LEFTMETA", "PAGEUP"),
                     SWAY: _run("swaymsg workspace prev_on_output"), HYPRLAND: _run("hyprctl dispatch workspace e-1"),
                     "*": _keys("LEFTCTRL", "LEFTALT", "LEFT")},
    "desktop_right": {KDE: _kde("kwin", "Switch One Desktop to the Right"), GNOME: _keys("LEFTMETA", "PAGEDOWN"),
                      SWAY: _run("swaymsg workspace next_on_output"), HYPRLAND: _run("hyprctl dispatch workspace e+1"),
                      "*": _keys("LEFTCTRL", "LEFTALT", "RIGHT")},
    "desktop_up": {KDE: _kde("kwin", "Switch One Desktop Up"), GNOME: None, SWAY: None, HYPRLAND: None,
                   "*": _keys("LEFTCTRL", "LEFTALT", "UP")},
    "desktop_down": {KDE: _kde("kwin", "Switch One Desktop Down"), GNOME: None, SWAY: None, HYPRLAND: None,
                     "*": _keys("LEFTCTRL", "LEFTALT", "DOWN")},
    "desktop_previous": {KDE: _kde("kwin", "Switch to Previous Desktop"), GNOME: _keys("LEFTMETA", "PAGEUP"),
                         SWAY: _run("swaymsg workspace prev_on_output"), HYPRLAND: _run("hyprctl dispatch workspace e-1"),
                         "*": _keys("LEFTCTRL", "LEFTALT", "LEFT")},
    "desktop_next": {KDE: _kde("kwin", "Switch to Next Desktop"), GNOME: _keys("LEFTMETA", "PAGEDOWN"),
                     SWAY: _run("swaymsg workspace next_on_output"), HYPRLAND: _run("hyprctl dispatch workspace e+1"),
                     "*": _keys("LEFTCTRL", "LEFTALT", "RIGHT")},
    "window_desktop_previous": {KDE: _kde("kwin", "Window to Previous Desktop"), GNOME: _keys("LEFTMETA", "LEFTSHIFT", "PAGEUP"),
                                SWAY: _run("swaymsg move container to workspace prev_on_output"),
                                HYPRLAND: _run("hyprctl dispatch movetoworkspace e-1")},
    "window_desktop_next": {KDE: _kde("kwin", "Window to Next Desktop"), GNOME: _keys("LEFTMETA", "LEFTSHIFT", "PAGEDOWN"),
                            SWAY: _run("swaymsg move container to workspace next_on_output"),
                            HYPRLAND: _run("hyprctl dispatch movetoworkspace e+1")},
    "launcher": {KDE: _kde("plasmashell", "activate application launcher"), GNOME: _keys("LEFTMETA", "A"), SWAY: _LAUNCHER,
                 HYPRLAND: _LAUNCHER, "*": _keys("LEFTMETA")},
    "krunner": {KDE: _kde("org_kde_krunner_desktop", "_launch"), SWAY: _LAUNCHER, HYPRLAND: _LAUNCHER,
                "*": _keys("LEFTALT", "F2")},
    "win_close": {KDE: _kde("kwin", "Window Close"), SWAY: _run("swaymsg kill"), HYPRLAND: _run("hyprctl dispatch killactive"),
                  "*": _keys("LEFTALT", "F4")},
    "win_maximize": {KDE: _kde("kwin", "Window Maximize"), SWAY: None, HYPRLAND: _run("hyprctl dispatch fullscreen 1"),
                     "*": _keys("LEFTALT", "F10")},
    "win_minimize": {KDE: _kde("kwin", "Window Minimize"), GNOME: _keys("LEFTMETA", "H"), SWAY: _run("swaymsg move scratchpad"),
                     HYPRLAND: None, "*": _keys("LEFTALT", "F9")},
    "win_fullscreen": {KDE: _kde("kwin", "Window Fullscreen"), SWAY: _run("swaymsg fullscreen toggle"),
                       HYPRLAND: _run("hyprctl dispatch fullscreen 0"), "*": _keys("F11")},
    "win_tile_left": {KDE: _kde("kwin", "Window Quick Tile Left"), SWAY: _run("swaymsg move left"),
                      HYPRLAND: _run("hyprctl dispatch movewindow l"), "*": _keys("LEFTMETA", "LEFT")},
    "win_tile_right": {KDE: _kde("kwin", "Window Quick Tile Right"), SWAY: _run("swaymsg move right"),
                       HYPRLAND: _run("hyprctl dispatch movewindow r"), "*": _keys("LEFTMETA", "RIGHT")},
    "win_above": {KDE: _kde("kwin", "Window Above Other Windows")},
    "screenshot_region": {KDE: _kde("org_kde_spectacle_desktop", "RectangularRegionScreenShot"), GNOME: _keys("SYSRQ"),
                          SWAY: _REGION, HYPRLAND: _REGION, "*": _keys("LEFTSHIFT", "SYSRQ")},
    "screenshot_full": {KDE: _kde("org_kde_spectacle_desktop", "FullScreenScreenShot"), GNOME: _keys("LEFTSHIFT", "SYSRQ"),
                        SWAY: _SCREEN, HYPRLAND: _SCREEN, "*": _keys("SYSRQ")},
    "screenshot_window": {KDE: _kde("org_kde_spectacle_desktop", "ActiveWindowScreenShot"), SWAY: None, HYPRLAND: None,
                          "*": _keys("LEFTALT", "SYSRQ")},
    "lock": {KDE: _kde("ksmserver", "Lock Session"), GNOME: _keys("LEFTMETA", "L"), "*": _LOCK},
    "clipboard": {KDE: _kde("plasmashell", "show-on-mouse-pos")},
    "emoji": {KDE: _kde("org_kde_plasma_emojier_desktop", "_launch")},
    "files": {KDE: _kde("org_kde_dolphin_desktop", "_launch"), "*": _FILES},
    "terminal": {KDE: _kde("org_kde_konsole_desktop", "_launch"), "*": _TERMINAL},
    "dnd": {KDE: _kde("plasmashell", "toggle do not disturb"), GNOME: _toggle("org.gnome.desktop.notifications", "show-banners"),
            "*": _QUIET},
    "night_light": {KDE: _kde("kwin", "Toggle Night Color"),
                    GNOME: _toggle("org.gnome.settings-daemon.plugins.color", "night-light-enabled")},
}
# Windows does them with key combinations of its own and a command or two; what is not listed it cannot do.
_WINDOWS = {
    "overview": _keys("LEFTMETA", "TAB"), "all_windows": _keys("LEFTMETA", "TAB"), "show_desktop": _keys("LEFTMETA", "D"),
    "desktop_left": _keys("LEFTMETA", "LEFTCTRL", "LEFT"), "desktop_right": _keys("LEFTMETA", "LEFTCTRL", "RIGHT"),
    "desktop_previous": _keys("LEFTMETA", "LEFTCTRL", "LEFT"), "desktop_next": _keys("LEFTMETA", "LEFTCTRL", "RIGHT"),
    "launcher": _keys("LEFTMETA"), "krunner": _keys("LEFTMETA", "S"),
    "win_close": _keys("LEFTALT", "F4"), "win_maximize": _keys("LEFTMETA", "UP"), "win_minimize": _keys("LEFTMETA", "DOWN"),
    "win_fullscreen": _keys("F11"), "win_tile_left": _keys("LEFTMETA", "LEFT"), "win_tile_right": _keys("LEFTMETA", "RIGHT"),
    "screenshot_region": _keys("LEFTMETA", "LEFTSHIFT", "S"), "screenshot_full": _keys("LEFTMETA", "SYSRQ"),
    "screenshot_window": _keys("LEFTALT", "SYSRQ"), "lock": _run("rundll32.exe user32.dll,LockWorkStation"),
    "clipboard": _keys("LEFTMETA", "V"), "emoji": _keys("LEFTMETA", "DOT"), "files": _keys("LEFTMETA", "E"),
    "terminal": _run("wt.exe || start cmd.exe"),
}
for _name, _ways in WAYS.items():
    _ways[WINDOWS] = _WINDOWS.get(_name)
# Settings written before the catalogue named things this way hold the KDE shortcut itself.
_LEGACY = {json.dumps(ways[KDE], sort_keys=True): name for name, ways in WAYS.items()}


def wanted(name):
    """The action that asks for ``name`` of whichever desktop is running."""
    return {"type": "desktop", "do": name}


def _installed(program):
    return shutil.which(program) is not None


def resolve(name, desktop=None, installed=_installed):
    """The action that does ``name`` on ``desktop`` (the current one if not given), or None."""
    ways = WAYS.get(name)
    if ways is None:
        return None
    desktop = desktop or current()
    way = ways[desktop] if desktop in ways else ways.get("*")
    if way is not None and way["type"] == "tool":
        return next((_run(command) for needs, command in way["commands"] if all(installed(tool) for tool in needs)), None)
    return way


def available(action, desktop=None):
    """Whether ``action`` can do anything on ``desktop``: a KDE global shortcut cannot elsewhere."""
    kind = (action or {}).get("type")
    if kind == "desktop":
        return resolve(action.get("do"), desktop) is not None
    if kind == "shortcut":
        return (desktop or current()) == KDE
    return True


def modernize(action):
    """``action``, or what the catalogue calls it now if it is one of the KDE shortcuts it used to hold."""
    if isinstance(action, dict) and action.get("type") == "shortcut":
        try:
            name = _LEGACY.get(json.dumps(action, sort_keys=True))
        except (TypeError, ValueError):
            name = None
        if name:
            return wanted(name)
    return action
