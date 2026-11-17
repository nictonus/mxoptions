"""Holds the configuration on the UI side: every change is pushed to the engine and saved."""

import copy
import logging
import os

from PySide6.QtCore import QObject, QTimer, Signal

from .. import config
from ..i18n import _

log = logging.getLogger(__name__)


def _reason(error):
    return error.strerror or str(error)


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
    mouse_app_changed = Signal()  # mouse_app changed

    def __init__(self, cfg, engine, sounds=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.engine = engine
        self.sounds = sounds  # WheelSounds, for the pages that let the user hear a click
        self.flow = None      # flow_service.FlowService, once the program has made it
        self.front = None     # front.Front, once the program has made it: which app is in front
        self.updates = None   # ui.updates.Checker, once made (ui.updates.checker_for)
        self.mouse_app = None  # the app whose mouse settings the pages show; None: all apps. Never saved.
        self.state = {"connected": False, "known": False, "name": "", "uinput": True, "rivals": [], "battery": None,
                      "current_host": 0, "hosts": [], "receiver": None, "dpi": cfg["pointer"]["dpi"], "wheel_mode": "",
                      "caps": None, "keyboard": None}
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

    def set_key(self, key_id, action):
        """What a key of the keyboard does; the default action takes it out of the settings. False, and nothing
        changed, for a key id or an action the settings would not keep (config.keyboard_action)."""
        keys = self.cfg["keyboard"]["keys"]
        if not (isinstance(key_id, str) and config.KEY_ID.match(key_id) and isinstance(action, dict)):
            return False
        if action.get("type") == "default":
            if keys.pop(key_id, None) is not None:
                self._push()
            return True
        if not config.keyboard_action(action) or (key_id not in keys and len(keys) >= config.MAX_KEYBOARD_KEYS):
            log.warning("key %s: action refused", key_id)
            return False
        if keys.get(key_id) != action:
            keys[key_id] = copy.deepcopy(action)
            self._push()
        return True

    def profile(self, app):
        """The profile of ``app`` (see config.DEFAULTS), or None."""
        return next((profile for profile in self.cfg["keyboard"]["profiles"] if profile["app"] == app), None)

    def add_profile(self, app):
        """Give ``app`` keys of its own, none to begin with. False, and nothing changed, if it cannot have them."""
        profiles = self.cfg["keyboard"]["profiles"]
        if not config.app_id(app) or self.profile(app) is not None or len(profiles) >= config.MAX_PROFILES:
            return False
        profiles.append({"app": app, "name": app[:config.MAX_PROFILE_NAME], "keys": {}})
        self._push()
        return True

    def remove_profile(self, app):
        profiles = self.cfg["keyboard"]["profiles"]
        kept = [profile for profile in profiles if profile["app"] != app]
        if len(kept) != len(profiles):
            self.cfg["keyboard"]["profiles"] = kept
            self._push()

    def set_profile_key(self, app, key_id, action):
        """What a key does while ``app`` is in front: an action, config.NATIVE for what it always does there, or
        None for what it does in every app. False, and nothing changed, for what the settings would not keep."""
        profile = self.profile(app)
        if profile is None or not (isinstance(key_id, str) and config.KEY_ID.match(key_id)):
            return False
        keys = profile["keys"]
        if action is None:
            if keys.pop(key_id, None) is not None:
                self._push()
            return True
        if not (action == config.NATIVE or config.keyboard_action(action)) or (
                key_id not in keys and len(keys) >= config.MAX_KEYBOARD_KEYS):
            log.warning("key %s: action refused", key_id)
            return False
        if keys.get(key_id) != action:
            keys[key_id] = copy.deepcopy(action)
            self._push()
        return True

    def set_mouse_app(self, app):
        """Show the mouse settings of ``app`` on the Buttons, Ring and Point & Scroll pages (None: of all apps)."""
        if app != self.mouse_app:
            self.mouse_app = app
            self.mouse_app_changed.emit()

    def mouse_profile(self, app):
        """The mouse profile of ``app`` (see config.DEFAULTS), or None."""
        return config.mouse_profile_of(self.cfg, app)

    def add_mouse_profile(self, app):
        """Give ``app`` mouse settings of its own, none to begin with. False, and nothing changed, if it cannot."""
        profiles = self.cfg["mouse"]["profiles"]
        if not config.app_id(app) or self.mouse_profile(app) is not None or len(profiles) >= config.MAX_PROFILES:
            return False
        profiles.append({"app": app, "name": app[:config.MAX_PROFILE_NAME], "buttons": {}, "ring": None})
        self._push()
        return True

    def remove_mouse_profile(self, app):
        profiles = self.cfg["mouse"]["profiles"]
        kept = [profile for profile in profiles if profile["app"] != app]
        if len(kept) != len(profiles):
            self.cfg["mouse"]["profiles"] = kept
            for name in ("thumbwheel", "thumbwheel_keys", "gestures"):  # its function, keystrokes, gestures go with it
                self.cfg["mouse"][name] = [entry for entry in self.cfg["mouse"][name] if entry["app"] != app]
            self._push()
        if self.mouse_app == app:
            self.set_mouse_app(None)

    def set_profile_thumb(self, app, function):
        """The thumb wheel function while ``app`` is in front: one of config.THUMB_FUNCTIONS, or None for the one of
        all apps. False, and nothing changed, for an app without a profile or a function the settings would not keep."""
        if self.mouse_profile(app) is None:
            return False
        entries = self.cfg["mouse"]["thumbwheel"]
        entry = next((entry for entry in entries if entry["app"] == app), None)
        if function is None:
            if entry is not None:
                entries.remove(entry)
                self._push()
            return True
        if not (isinstance(function, str) and function in config.THUMB_FUNCTIONS):
            log.warning("thumb wheel function refused")
            return False
        if entry is None:
            entries.append({"app": app, "function": function})
        elif entry["function"] != function:
            entry["function"] = function
        else:
            return True
        self._push()
        return True

    def set_profile_thumb_keys(self, app, keys):
        """What "keys" sends while ``app`` is in front: {"up", "down"}, each a combination config.thumb_combo takes
        ([]: nothing), or None for the keystrokes of all apps. False, and nothing changed, for an app without a
        profile or keystrokes the settings would not keep."""
        if self.mouse_profile(app) is None:
            return False
        entries = self.cfg["mouse"]["thumbwheel_keys"]
        entry = next((entry for entry in entries if entry["app"] == app), None)
        if keys is None:
            if entry is not None:
                entries.remove(entry)
                self._push()
            return True
        if not (isinstance(keys, dict) and set(keys) == {"up", "down"} and all(map(config.thumb_combo, keys.values()))):
            log.warning("thumb wheel keystrokes refused")
            return False
        wanted = {"app": app, "up": list(keys["up"]), "down": list(keys["down"])}
        if entry == wanted:
            return True
        if entry is None:
            entries.append(wanted)
        else:
            entry.update(wanted)
        self._push()
        return True

    def apply_mouse_preset(self, app, preset):
        """Give ``app`` the suggested settings ``preset`` (config.APP_PRESETS), all in one go: exactly what
        config.preset_changes says and the thumb wheel keystrokes config.preset_keys says, so a button, function or
        keystrokes of its own stay. False, and nothing changed, for an app without a profile."""
        profile = self.mouse_profile(app)
        if profile is None:
            return False
        buttons, function = config.preset_changes(self.cfg, app, preset)
        keys = config.preset_keys(self.cfg, app, preset)
        profile["buttons"].update(buttons)
        if function is not None:
            self.cfg["mouse"]["thumbwheel"].append({"app": app, "function": function})
        if keys is not None:
            self.cfg["mouse"]["thumbwheel_keys"].append({"app": app, **keys})
        if buttons or function is not None or keys is not None:
            self._push()
        return True

    def set_profile_button(self, app, key, action):
        """What button ``key`` does while ``app`` is in front: an action (gestures too, kept in "mouse.gestures"),
        config.NATIVE for its own function, or None for what it does in every app. False, and nothing changed, for
        what the settings would not keep exactly as given."""
        profile = self.mouse_profile(app)
        if profile is None or key not in config.BUTTONS:
            return False
        drawn = isinstance(action, dict) and action.get("type") == "gestures"
        if action is not None and not (config.app_gestures_action(action) == action if drawn
                                       else config.profile_button_action(action)):
            log.warning("button %s: action refused", key)
            return False
        entries = self.cfg["mouse"]["gestures"]
        entry = next((entry for entry in entries if entry["app"] == app), None)
        own = entry["buttons"] if entry is not None else {}
        if (profile["buttons"].get(key), own.get(key)) == ((None, action) if drawn else (action, None)):
            return True
        profile["buttons"].pop(key, None)
        own.pop(key, None)
        if entry is not None and not own:
            entries.remove(entry)
        if drawn:
            if entry is None or not own:
                entry = {"app": app, "buttons": {}}
                entries.append(entry)
            entry["buttons"][key] = copy.deepcopy(action)
        elif action is not None:
            profile["buttons"][key] = copy.deepcopy(action)
        self._push()
        return True

    def set_profile_ring(self, app, bubbles):
        """The Actions Ring while ``app`` is in front: bubbles of its own, or None for the ring of all apps. False,
        and nothing changed, for no profile or nothing usable."""
        profile = self.mouse_profile(app)
        if profile is None:
            return False
        if bubbles is not None:
            bubbles = config.clean_bubbles(bubbles)
            if not bubbles:
                return False
        if profile["ring"] != bubbles:
            profile["ring"] = bubbles
            self._push()
        return True

    def restore_keyboard(self):
        """Every key does what it always does, in every app: no profile is left either. Notices stay as they are."""
        self.set_section("keyboard", dict(self.cfg["keyboard"], keys={}, profiles=[]))

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
        asked, cfg = cfg, config.clean(cfg)  # clean() leaves what it is given as it was
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
        if parts[0] in ("keyboard", "mouse") and stored != value:  # what a key or a profile cannot have: refused, not dropped
            return False
        thumb = asked.get("thumbwheel")
        if isinstance(thumb, dict) and any(d in thumb and thumb[d] != cfg["thumbwheel"][d] for d in ("up", "down")):
            return False  # a combination the thumb wheel cannot send: refused, not dropped
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

    def export_settings(self, path):
        """Write the settings in use, changes not saved yet too, to the file ``path`` (config.export_data).
        None, or what to tell the user."""
        try:
            config.write_durable(config.export_data(self.cfg), path)
        except ValueError as e:
            return _("Nothing was exported. {}").format(e)
        except config.NotDurable as e:
            return _("The settings were written, but the system could not confirm that they are safely on the "
                     "disk ({}).").format(_reason(e))
        except OSError as e:
            return _("The settings could not be written ({}).").format(_reason(e))
        return None

    def import_settings(self, path):
        """Use the settings in the file ``path``, which export_settings wrote. All in one go, on this thread:
        the file is read and checked, the settings in use (changes not saved yet too) are kept in the backup
        (config.BACKUP_NAME), the new ones are saved, and only then used. A step that fails stops it with the
        settings in use, the engine and a pending save as they were. None, or what to tell the user."""
        name = os.path.basename(path)
        try:
            cfg = config.import_data(config.read_settings(path), self.cfg)
        except ValueError as e:
            return _("{name} cannot be imported. {reason}").format(name=name, reason=e)
        except OSError as e:
            return _("{name} could not be read ({reason}).").format(name=name, reason=_reason(e))
        backup = os.path.join(config.CONFIG_DIR, config.BACKUP_NAME)
        current = config.to_bytes(self.cfg)
        try:
            os.makedirs(config.CONFIG_DIR, exist_ok=True)
            config.write_durable(current, backup)
        except OSError as e:  # NotDurable too: a backup that may not outlast a crash is not one to count on
            return _("Nothing was imported: the settings in use could not be kept in a backup first ({}).").format(
                _reason(e))
        had_file = config.exists()
        try:
            config.write_durable(config.to_bytes(cfg), config.CONFIG_PATH)
        except config.NotDurable as e:
            try:  # the imported settings are in the file, perhaps not for good: the ones in use go back in
                if had_file:
                    config.write_durable(current, config.CONFIG_PATH)
                else:
                    config.remove_durable(config.CONFIG_PATH)
            except OSError as again:
                log.warning("could not put the settings in use back: %s", again)
                return _("Nothing was imported: the system could not confirm that the new settings are safely on "
                         "the disk ({reason}), and putting the settings in use back in their file failed too "
                         "({again}). The file may hold the imported settings; the settings in use are kept in "
                         "{backup}.").format(reason=_reason(e), again=_reason(again), backup=backup)
            return _("Nothing was imported: the system could not confirm that the new settings are safely on the "
                     "disk ({}). The settings in use were put back.").format(_reason(e))
        except OSError as e:
            return _("Nothing was imported: the settings could not be saved ({}). They are as they were.").format(
                _reason(e))
        self._save_timer.stop()  # what it would save is in the backup; the file holds the imported settings
        self.cfg = cfg
        self.engine.set_config(cfg)
        self.changed.emit()
        return None

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
