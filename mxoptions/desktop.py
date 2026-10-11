"""Desktop integration: the application-menu entry, and starting with the session.

An installed copy (see the Makefile) brings its own menu entry and a systemd user service; then
"start at login" switches that service on and off. Run from a source tree, the program writes the
menu entry and an XDG autostart entry itself."""

import os
import shlex
import subprocess
import sys

from . import APP_ID, APP_NAME

ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.svg")
SERVICE = f"{APP_ID}.service"
_CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
_DATA_HOME = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
AUTOSTART_PATH = os.path.join(_CONFIG_HOME, "autostart", f"{APP_ID}.desktop")
LAUNCHER_PATH = os.path.join(_DATA_HOME, "applications", f"{APP_ID}.desktop")
_SYSTEM_LAUNCHERS = tuple(os.path.join(prefix, "share", "applications", f"{APP_ID}.desktop") for prefix in ("/usr", "/usr/local"))


def launch_command():
    """The command that starts this program again: the installed launcher if that is what is
    running, otherwise this source tree."""
    launcher = os.environ.get("MXOPTIONS_LAUNCHER")  # set by the launcher script of an installed copy
    if launcher and os.access(launcher, os.X_OK):
        return [launcher]
    program = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if os.path.basename(program) == APP_ID and os.access(program, os.X_OK):
        return [program]  # the console script of a pip install
    main = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    return [sys.executable, main] if os.path.exists(main) else [sys.executable, "-m", APP_ID]


def _entry(background):
    command = " ".join(shlex.quote(part) for part in launch_command() + (["--background"] if background else []))
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        f"Name={APP_NAME}",
        "Comment=Buttons, gestures, scrolling and haptics for Logitech MX mice",
        "Comment[uk]=Кнопки, жести, прокрутка та вібровідгук для мишей Logitech MX",
        f"Exec={command}",
        f"Icon={ICON_PATH}",
        "Terminal=false",
        "Categories=Settings;HardwareSettings;",
        f"StartupWMClass={APP_ID}",
        "",
    ])


def _set(path, enabled, background):
    if enabled:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(_entry(background))
    elif os.path.exists(path):
        os.remove(path)


def _systemctl(*args):
    """Run ``systemctl --user`` and return (exit status, output); (1, "") where there is no systemd."""
    try:
        done = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return done.returncode, done.stdout.strip()


def service_installed():
    """Whether the systemd user service of an installed copy is there to be used."""
    return _systemctl("cat", SERVICE)[0] == 0


def autostart_enabled():
    if service_installed():
        return _systemctl("is-enabled", SERVICE)[1] == "enabled"
    return os.path.exists(AUTOSTART_PATH)


def set_autostart(enabled):
    """Start with the session from now on, or no longer. The running program is left as it is."""
    if service_installed():
        status, output = _systemctl("enable" if enabled else "disable", SERVICE)
        if status != 0:
            raise OSError(output or f"systemctl could not {'enable' if enabled else 'disable'} {SERVICE}")
        _set(AUTOSTART_PATH, False, background=True)  # never both, or the program is started twice
    else:
        _set(AUTOSTART_PATH, enabled, background=True)


def launcher_managed():
    """True when the menu entry belongs to a system-wide installation and is not ours to remove."""
    return any(os.path.exists(path) for path in _SYSTEM_LAUNCHERS)


def launcher_enabled():
    return launcher_managed() or os.path.exists(LAUNCHER_PATH)


def set_launcher(enabled):
    _set(LAUNCHER_PATH, enabled, background=False)
