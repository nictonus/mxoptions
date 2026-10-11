"""The door for other programs: a D-Bus interface on the session bus (see API.md).

    service    org.mxoptions.MXOptions
    object     /org/mxoptions/Mouse
    interface  org.mxoptions.Mouse1

Everything structured travels as JSON text, so the interface stays usable from a shell
(qdbus, busctl, gdbus) as well as from any language with D-Bus bindings."""

import json
import logging

from PySide6.QtCore import ClassInfo, Property, QObject, Signal, Slot
from PySide6.QtDBus import QDBusConnection

from . import __version__, actions, config, device

log = logging.getLogger(__name__)

SERVICE = "org.mxoptions.MXOptions"
PATH = "/org/mxoptions/Mouse"
INTERFACE = "org.mxoptions.Mouse1"
API_VERSION = 1
# Actions another program may run through Trigger(); the rest only make sense on a physical button.
TRIGGERABLE = ("none", "keys", "button", "shortcut", "command", "script", "macro", "wheel_mode", "dpi_cycle", "host", "hook")
CHARGING = (device.BATTERY_CHARGING, device.BATTERY_ALMOST_FULL, device.BATTERY_SLOW_CHARGE, device.BATTERY_FULL)


def pattern_name(waveform):
    """The name a vibration pattern goes by on the bus: "Sharp collision" -> "sharp-collision"."""
    return device.WAVEFORMS[waveform].lower().replace(" ", "-")


def find_pattern(text):
    """Waveform id for a pattern given by name (any spelling of it) or number, or None."""
    wanted = text.strip().lower().replace("_", "-").replace(" ", "-")
    for waveform in device.WAVEFORMS:
        if pattern_name(waveform) == wanted:
            return waveform
    try:
        waveform = int(wanted, 0)
    except ValueError:
        return None
    return waveform if waveform in device.WAVEFORMS else None


def parse_action(text):
    """An action from what a caller sent: the id of a catalogue action ("copy") or an action as JSON."""
    text = text.strip()
    if not text.startswith("{"):
        preset = actions.PRESET_BY_ID.get(text)
        action = preset.action if preset else None
    else:
        try:
            action = json.loads(text)
        except ValueError:
            return None
    if not isinstance(action, dict) or action.get("type") not in TRIGGERABLE:
        return None
    return action


@ClassInfo({"D-Bus Interface": INTERFACE})
class MouseApi(QObject):
    """The exported object. Method, property and signal names are the D-Bus names."""

    ButtonPressed = Signal(str)        # a button the app handles went down: middle, back, forward, gesture, smartshift, panel
    ButtonReleased = Signal(str)
    Gesture = Signal(str)              # tap, up, down, left, right
    WheelModeChanged = Signal(str)     # ratchet, freespin
    Hook = Signal(str, str)            # name of a "signal to another program" action; press, release or trigger
    ConnectedChanged = Signal(bool)
    BatteryChanged = Signal(int, bool)  # percent, charging
    DpiChanged = Signal(int)
    ConfigChanged = Signal()

    def __init__(self, store, overlay, show_window, parent=None):
        super().__init__(parent)
        self._store = store
        self._overlay = overlay
        self._show_window = show_window
        self._wheel_mode = ""  # as last announced; SmartShift's own changes never reach the state
        self._seen = self._snapshot()
        # Through a plain function: a method connected directly would become a slot of this object,
        # and every slot of it is offered on the bus.
        store.state_changed.connect(lambda _state: self._state_changed())
        store.changed.connect(self.ConfigChanged)

    # -- state ----------------------------------------------------------------

    def _snapshot(self):
        state = self._store.state
        battery = state.get("battery") if state["connected"] else None
        return {
            "connected": bool(state["connected"]),
            "battery": (battery[0], battery[1] in CHARGING) if battery else (-1, False),
            "dpi": int(state.get("dpi") or 0),
        }

    def _state_changed(self):
        before, now = self._seen, self._snapshot()
        self._seen = now
        if now["connected"] != before["connected"]:
            self._wheel_mode = ""
            self.ConnectedChanged.emit(now["connected"])
        if now["battery"] != before["battery"]:
            self.BatteryChanged.emit(*now["battery"])
        if now["dpi"] != before["dpi"]:
            self.DpiChanged.emit(now["dpi"])

    def on_event(self, kind, payload):
        """Engine events (see engine.Listener.on_event), already on the GUI thread."""
        if kind == "button":
            (self.ButtonPressed if payload[1] else self.ButtonReleased).emit(payload[0])
        elif kind == "gesture":
            self.Gesture.emit(payload)
        elif kind == "wheel_mode":
            self._wheel_mode = payload[0]
            self.WheelModeChanged.emit(payload[0])
        elif kind == "hook":
            self.Hook.emit(*payload)

    ApiVersion = Property(int, lambda self: API_VERSION, constant=True)
    AppVersion = Property(str, lambda self: __version__, constant=True)
    Connected = Property(bool, lambda self: self._snapshot()["connected"], notify=ConnectedChanged)
    DeviceName = Property(str, lambda self: self._store.state.get("name") or "", notify=ConnectedChanged)
    Battery = Property(int, lambda self: self._snapshot()["battery"][0], notify=BatteryChanged)  # percent, -1 if unknown
    Charging = Property(bool, lambda self: self._snapshot()["battery"][1], notify=BatteryChanged)
    Dpi = Property(int, lambda self: self._snapshot()["dpi"], notify=DpiChanged)
    WheelMode = Property(str, lambda self: self._wheel_mode or self._store.state.get("wheel_mode") or "", notify=WheelModeChanged)

    # -- the mouse ---------------------------------------------------------------

    def _patterns(self):
        caps = self._store.state.get("caps") or {}
        return list(caps.get("waveforms") or [])

    @Slot(result="QStringList")
    def ListHaptics(self):
        """Names of the vibration patterns this mouse can play."""
        return [pattern_name(waveform) for waveform in self._patterns()]

    @Slot(str, result=bool)
    def PlayHaptic(self, pattern):
        """Play a vibration pattern, given by name ("knock") or number. False if the mouse cannot play it."""
        waveform = find_pattern(pattern)
        if waveform is None or waveform not in self._patterns() or not self._store.state["connected"]:
            return False
        self._store.engine.play_haptic(waveform)
        return True

    @Slot(int, result=bool)
    def SetDpi(self, dpi):
        """Pointer speed until further notice, without touching the user's setting; 0 gives it back."""
        if dpi < 0 or not self._store.state["connected"]:
            return False
        self._store.engine.set_dpi_override(dpi)
        return True

    @Slot(str, result=bool)
    def SetWheelMode(self, mode):
        """"ratchet", "freespin", "toggle", or "auto" to return to the user's setting."""
        if mode not in ("ratchet", "freespin", "toggle", "auto") or not self._store.state["connected"]:
            return False
        self._store.engine.set_wheel_mode(mode)
        return True

    @Slot(int, result=bool)
    def SwitchHost(self, channel):
        """Easy-Switch the mouse to another paired computer (channels count from 1). It leaves this one."""
        hosts = self._store.state.get("hosts") or []
        if not 1 <= channel <= len(hosts) or not self._store.state["connected"]:
            return False
        self._store.engine.switch_host(channel - 1)
        return True

    # -- actions and the ring ---------------------------------------------------------

    @Slot(result="QStringList")
    def ListActions(self):
        """Ids of the catalogue actions Trigger() accepts by name."""
        return [preset.id for preset in actions.PRESETS if preset.action.get("type") in TRIGGERABLE]

    @Slot(str, result=bool)
    def Trigger(self, action):
        """Run an action: a catalogue id ("copy", "play_pause") or JSON like {"type": "keys", "keys": ["KEY_F5"]}."""
        parsed = parse_action(action)
        if parsed is None:
            return False
        self._store.engine.trigger_action(parsed)
        return True

    @Slot(result=bool)
    def OpenRing(self):
        """Open the user's Actions Ring at the pointer; it waits for a click."""
        return self._overlay.open()

    @Slot(str, result=bool)
    def ShowRing(self, bubbles):
        """Open a ring of the caller's own for one use. ``bubbles`` is a JSON list in the format of
        "ring.bubbles" in the settings; give them "hook" actions to hear back which one was picked."""
        try:
            parsed = json.loads(bubbles)
        except ValueError:
            return False
        if not isinstance(parsed, list):
            return False
        usable = [b for b in parsed if isinstance(b, dict) and any(key in b for key in ("action", "children", "scroll"))]
        parsed = config.clean_bubbles(usable)
        return bool(parsed) and self._overlay.open(parsed)

    @Slot()
    def CloseRing(self):
        """Dismiss the ring if it is open."""
        self._overlay.close()

    # -- settings ---------------------------------------------------------------------

    @Slot(result=str)
    def GetConfig(self):
        """The whole configuration as JSON."""
        return json.dumps(self._store.cfg, ensure_ascii=False)

    @Slot(str, result=str)
    def Get(self, path):
        """One value as JSON, addressed like "pointer.dpi" or "buttons.back". Empty if there is no such path."""
        value = self._store.cfg
        try:
            for part in filter(None, path.split(".")):
                value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, ValueError, TypeError):
            return ""
        return json.dumps(value, ensure_ascii=False)

    @Slot(str, str, result=bool)
    def Set(self, path, value):
        """Change one existing value and save it, as if the user had done so in the window.
        ``value`` is JSON; text that is not valid JSON is taken as a plain string."""
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = value
        return self._store.set_path(path, parsed)

    @Slot()
    def ShowWindow(self):
        self._show_window()


class Service(QObject):
    """Puts the interface on the session bus and takes it off again."""

    def __init__(self, store, overlay, show_window, parent=None):
        super().__init__(parent)
        self.api = MouseApi(store, overlay, show_window, self)
        self.active = False

    def set_enabled(self, enabled):
        if enabled == self.active:
            return
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            log.warning("no session bus: other programs cannot reach the app")
            return
        if enabled:
            options = QDBusConnection.RegisterOption
            exported = options.ExportAllSlots | options.ExportAllSignals | options.ExportAllProperties
            if not bus.registerObject(PATH, self.api, exported) or not bus.registerService(SERVICE):
                log.warning("could not offer the D-Bus interface: %s", bus.lastError().message())
                bus.unregisterObject(PATH)
                return
        else:
            bus.unregisterService(SERVICE)
            bus.unregisterObject(PATH)
        self.active = enabled
