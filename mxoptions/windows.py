"""The few things asked of Windows directly. Only ever imported there."""

import ctypes
import ctypes.wintypes as w
import logging
import os
import subprocess
import sys
import threading
import winreg

from . import APP_ID

log = logging.getLogger(__name__)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# What SHQueryUserNotificationState says when something fills the screen: a full-screen program, a game drawing
# with Direct3D, a presentation.
_FILLS_THE_SCREEN = (2, 3, 4)


def fullscreen_in_front():
    """Whether a program fills the screen (a game, a film), by what Windows itself goes by to hold back notifications."""
    state = ctypes.c_int()
    try:
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
    except (OSError, AttributeError):
        return False
    return state.value in _FILLS_THE_SCREEN


def background_command():
    """The command line that starts the program without a window of a console: the frozen program itself,
    or this source tree with the windowless Python."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    windowless = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    python = windowless if os.path.exists(windowless) else sys.executable
    home = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f'"{python}" "{os.path.join(home, "main.py")}" --background'


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_ID)
        return True
    except OSError:
        return False


def set_autostart(enabled):
    """Start when the user signs in from now on, or no longer: an entry among the user's own start-up programs."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, background_command())
        else:
            try:
                winreg.DeleteValue(key, APP_ID)
            except FileNotFoundError:
                pass


class _PROCESS(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong), ("th32ProcessID", ctypes.c_ulong),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_ulong), ("cntThreads", ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong), ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_ulong),
                ("szExeFile", ctypes.c_wchar * 260)]


def running_programs():
    """The names of the programs that are running, in small letters ("explorer.exe")."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESS)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # of the processes
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return set()
    names, entry = set(), _PROCESS()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            names.add(entry.szExeFile.lower())
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return names


# -- the brightness of the screens -------------------------------------------------------------------
# Windows has no key for it that a program could press. A laptop's own panel is set through the display driver
# (its "LCD" device), a monitor through the wire it is plugged in with (DDC/CI), which not every monitor heeds.
_LCD_LEVELS, _LCD_NOW, _LCD_SET = 0x230494, 0x230498, 0x23049C
BRIGHTNESS_STEP = 10  # percent a press of a brightness key changes it by


class _PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [("handle", w.HANDLE), ("description", w.WCHAR * 128)]


def _panel(change):
    """Change the brightness of the built-in panel by ``change`` percent. Returns the new level, or None if
    there is no such panel or its driver does not say."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = w.HANDLE
    kernel32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    kernel32.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.c_void_p, w.DWORD,
                                         ctypes.POINTER(w.DWORD), ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel32.CreateFileW("\\\\.\\LCD", 0xC0000000, 3, None, 3, 0, None)
    if handle == w.HANDLE(-1).value:
        return None
    try:
        got, levels, now = w.DWORD(), ctypes.create_string_buffer(256), ctypes.create_string_buffer(3)
        if not kernel32.DeviceIoControl(handle, _LCD_LEVELS, None, 0, levels, 256, ctypes.byref(got), None) or not got.value:
            return None
        known = sorted(set(levels.raw[:got.value]))
        if not kernel32.DeviceIoControl(handle, _LCD_NOW, None, 0, now, 3, ctypes.byref(got), None) or got.value < 3:
            return None
        current = now.raw[1] if now.raw[0] & 1 else now.raw[2]  # on the mains, or on the battery
        wanted = min(known, key=lambda level: abs(level - min(100, max(0, current + change))))
        if wanted == current and len(known) > 1:  # the levels are coarser than the step: the next one that way
            further = [level for level in known if (level > current) == (change > 0) and level != current]
            wanted = (min(further) if change > 0 else max(further)) if further else current
        asked = bytes([3, wanted, wanted])  # for the mains and for the battery alike
        if not kernel32.DeviceIoControl(handle, _LCD_SET, asked, 3, None, 0, ctypes.byref(got), None):
            return None
        return wanted
    finally:
        kernel32.CloseHandle(handle)


def _monitors(change):
    """Change the brightness of the monitors that take it over their cable. Returns how many did."""
    try:
        dxva2, user32 = ctypes.WinDLL("dxva2", use_last_error=True), ctypes.WinDLL("user32", use_last_error=True)
    except OSError:
        return 0
    found = []
    each = ctypes.WINFUNCTYPE(w.BOOL, w.HANDLE, w.HDC, ctypes.POINTER(w.RECT), w.LPARAM)(
        lambda monitor, _dc, _rect, _data: found.append(monitor) or True)
    user32.EnumDisplayMonitors.argtypes = [w.HDC, ctypes.c_void_p, ctypes.c_void_p, w.LPARAM]
    user32.EnumDisplayMonitors(None, None, each, 0)
    dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [w.HANDLE, w.DWORD, ctypes.c_void_p]
    dxva2.GetMonitorBrightness.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD)]
    dxva2.SetMonitorBrightness.argtypes = [w.HANDLE, w.DWORD]
    dxva2.DestroyPhysicalMonitors.argtypes = [w.DWORD, ctypes.c_void_p]
    changed = 0
    for monitor in found:
        count = w.DWORD()
        if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(monitor, ctypes.byref(count)) or not count.value:
            continue
        physical = (_PHYSICAL_MONITOR * count.value)()
        if not dxva2.GetPhysicalMonitorsFromHMONITOR(monitor, count, physical):
            continue
        try:
            for one in physical:
                low, now, high = w.DWORD(), w.DWORD(), w.DWORD()
                if dxva2.GetMonitorBrightness(one.handle, ctypes.byref(low), ctypes.byref(now), ctypes.byref(high)) and high.value > low.value:
                    step = max(1, round((high.value - low.value) * abs(change) / 100))
                    wanted = min(high.value, max(low.value, now.value + (step if change > 0 else -step)))
                    if dxva2.SetMonitorBrightness(one.handle, wanted):
                        changed += 1
        finally:
            dxva2.DestroyPhysicalMonitors(count, physical)
    return changed


def _panel_slowly(change):
    """The built-in panel through Windows' management interface: for display drivers that do not offer the
    faster way. It takes starting PowerShell, which is a third of a second."""
    script = ("$now = (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction Stop | Select-Object -First 1).CurrentBrightness; "
              f"$new = [Math]::Min(100, [Math]::Max(0, $now + ({int(change)}))); "
              "Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | "
              "Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{Timeout = [uint32]0; Brightness = [byte]$new} | Out-Null; $new")
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                              timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    return int(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip().isdigit() else None


def change_brightness(change):
    """Make the screens brighter (``change`` > 0, in percent) or dimmer. True if any of them took it."""
    panel = _panel(change)
    monitors = _monitors(change)
    if panel is None and not monitors:
        panel = _panel_slowly(change)
    return panel is not None or bool(monitors)


class _Brightness:
    """Changes the brightness off the thread that asked: a monitor may take a tenth of a second to answer, and
    presses that come faster than that are added up."""

    def __init__(self):
        self._lock = threading.Lock()
        self._owed = 0
        self._busy = False

    def change(self, percent):
        with self._lock:
            self._owed += percent
            if self._busy:
                return
            self._busy = True
        threading.Thread(target=self._work, name="brightness", daemon=True).start()

    def _work(self):
        while True:
            with self._lock:
                owed, self._owed = self._owed, 0
                if not owed:
                    self._busy = False
                    return
            try:
                change_brightness(owed)
            except Exception:
                log.exception("brightness")


brightness = _Brightness()
