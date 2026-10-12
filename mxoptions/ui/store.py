"""Holds the configuration on the UI side: every change is pushed to the engine and saved."""

import copy
import logging

from PySide6.QtCore import QObject, QTimer, Signal

from .. import config

log = logging.getLogger(__name__)


def _kind(value):
    """What sort of value this is, for deciding whether one may replace another."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


class Store(QObject):
    changed = Signal()            # configuration changed
    state_changed = Signal(object)  # engine state (connection, battery, capabilities)
    pressed_changed = Signal(object)  # frozenset of buttons held down right now
    gesture_changed = Signal(object)  # a gesture in progress or just finished (see Listener.on_gesture)

    def __init__(self, cfg, engine, sounds=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.engine = engine
        self.sounds = sounds  # WheelSounds, for the pages that let the user hear a click
        self.flow = None      # flow_service.FlowService, once the program has made it
        self.state = {"connected": False, "known": False, "name": "", "uinput": True, "battery": None,
                      "current_host": 0, "hosts": [], "dpi": cfg["pointer"]["dpi"], "wheel_mode": "", "caps": None}
        self._save_timer = QTimer(self, singleShot=True, interval=500)
        self._save_timer.timeout.connect(self.flush)

    def set(self, section, key, value):
        if self.cfg[section].get(key) != value:
            self.cfg[section][key] = value
            self._push()

    def set_button(self, key, action):
        if self.cfg["buttons"].get(key) != action:
            self.cfg["buttons"][key] = copy.deepcopy(action)
            self._push()

    def set_section(self, section, values):
        if self.cfg[section] != values:
            self.cfg[section] = copy.deepcopy(values)
            self._push()

    def assign_role(self, kind, key):
        """Make thumb button ``key`` the one that does ``kind`` ("ring" or "gestures").

        If the other thumb button has that role now, the two simply trade places."""
        buttons = self.cfg["buttons"]
        other = next(k for k in config.GESTURE_BUTTONS if k != key)
        if buttons[key].get("type") == kind:
            return
        if buttons[other].get("type") == kind:
            buttons[key], buttons[other] = buttons[other], buttons[key]
        else:
            buttons[key] = {"type": "ring"} if kind == "ring" else config.default_gestures()
        self._push()

    def set_path(self, path, value):
        """Change one existing value addressed like "wheel.speed" or "buttons.back" (for other programs).

        Returns whether it was accepted: the path must exist and the value be of the kind that is there."""
        parts = [part for part in path.split(".") if part]
        if not parts or parts[0] == "version":
            return False
        cfg = copy.deepcopy(self.cfg)
        try:
            holder = cfg
            for part in parts[:-1]:
                holder = holder[int(part)] if isinstance(holder, list) else holder[part]
            key = int(parts[-1]) if isinstance(holder, list) else parts[-1]
            current = holder[key]
        except (KeyError, IndexError, ValueError, TypeError):
            return False
        if _kind(value) != _kind(current) and None not in (value, current):
            return False
        holder[key] = value
        cfg = config.clean(cfg)
        speeds = (self.state.get("caps") or {}).get("dpi")
        if speeds:  # keep pointer speeds within what this mouse can do, as the sliders would
            for name in ("dpi", "alt_dpi", "precision_dpi"):
                cfg["pointer"][name] = max(speeds[0], min(speeds[1], cfg["pointer"][name]))
        try:  # a text value the settings do not know is put back to its default by clean(): refuse it instead
            stored = cfg
            for part in parts:
                stored = stored[int(part)] if isinstance(stored, list) else stored[part]
        except (KeyError, IndexError, ValueError, TypeError):
            return False
        if isinstance(value, str) and stored != value:
            return False
        if cfg != self.cfg:
            self.replace(cfg)
        return True

    def replace(self, cfg, push=True):
        self.cfg = cfg
        if push:
            self._push()
        else:  # it came from the engine itself
            self._save_timer.start()
            self.changed.emit()

    def restore_defaults(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        for section in ("app", "flow"):  # the look of the program and its pairing with another computer stay
            cfg[section] = copy.deepcopy(self.cfg[section])
        caps = self.state.get("caps") or {}
        if caps.get("dpi_default"):
            cfg["pointer"]["dpi"] = caps["dpi_default"]
        if caps.get("force"):
            cfg["haptics"]["force"] = caps["force"]["default"]
        self.replace(cfg)

    def update_state(self, state):
        self.state = state
        self.state_changed.emit(state)

    def _push(self):
        self.engine.set_config(self.cfg)
        self._save_timer.start()
        self.changed.emit()

    def flush(self):
        self._save_timer.stop()
        try:
            config.save(self.cfg)
        except OSError as e:
            log.warning("could not save settings: %s", e)
