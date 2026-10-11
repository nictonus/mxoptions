"""Stand-ins for the mouse and for whatever runs the actions, so the engine's logic can be tested
without a device."""

import copy
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mxoptions import config, device, engine  # noqa: E402


class Recorder(engine.Listener):
    """Keeps what the engine tells the rest of the program."""

    def __init__(self):
        self.events = []    # (kind, payload) as passed on to other programs
        self.gestures = []  # live gesture updates for the settings page

    def on_event(self, kind, payload):
        self.events.append((kind, payload))

    def on_gesture(self, event):
        self.gestures.append(event)


class Actions:
    """Stands in for the executor: remembers which actions were run."""

    def __init__(self):
        self.ran = []

    def trigger(self, action):
        self.ran.append(action)

    def press(self, action):
        self.ran.append(action)

    def release(self, action):
        pass


class Mouse:
    """An MX Master 4 as far as the engine's logic needs one."""

    wheel = {"multiplier": 15, "has_invert": True, "has_ratchet": True, "notches": 24}
    smartshift = {"torque": True, "default_threshold": 10, "default_torque": 75}
    thumbwheel = {"native_res": 20, "diverted_res": 120, "direction": -1}
    haptic_waveforms = list(device.WAVEFORMS)
    controls = {cid: True for cid in config.BUTTONS.values()}
    dpi_range = (200, 8000, 50)

    def __init__(self):
        self.reporting = {}

    def set_cid_reporting(self, cid, divert, raw_xy=False):
        self.reporting[cid] = (divert, raw_xy)


def make_engine(**sections):
    """An engine with a mouse attached and the given settings laid over the defaults, e.g.
    ``make_engine(gestures={"trigger": "repeat"})``. Returns (engine, list of vibration names played)."""
    cfg = copy.deepcopy(config.DEFAULTS)
    for section, values in sections.items():
        cfg[section].update(values)
    made = engine.Engine(cfg, Recorder())
    made.executor = Actions()
    made.mouse = Mouse()
    made.online = True
    made.vinput = object()
    made._wheel_mode = "ratchet"
    made._monitor = True
    made._next_battery = float("inf")
    made._publish = lambda: None
    played = []
    made._play = lambda waveform: played.append(device.WAVEFORMS[waveform])
    return made, played


class Clock:
    """Replaces time.monotonic while a test runs, so pauses do not have to be waited for."""

    def __init__(self, start=100.0):
        self.now = start

    def __enter__(self):
        self._real = time.monotonic
        time.monotonic = lambda: self.now
        return self

    def __exit__(self, *_exc):
        time.monotonic = self._real


def names(actions):
    """What the recorded actions were, by KDE shortcut name or key list."""
    return [action.get("name") or action.get("keys") for action in actions]
