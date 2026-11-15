#!/usr/bin/env python3
"""Render the pages of the settings window to PNG files without a mouse or a screen.

The window is driven by a stand-in engine with a made-up MX Master 4, so this works anywhere and
touches neither the device nor the user's settings. It is how the pictures in the README are made,
and a quick way to look at a layout change in both languages and all themes.

    python3 tools/screenshots.py docs/screenshots
    python3 tools/screenshots.py /tmp/shots --lang uk --theme light --accent '#4c9bff' --font 'MesloLGM Nerd Font'
    python3 tools/screenshots.py /tmp/shots --keyboard   # a made-up keyboard as well, and its two pages
"""

import argparse
import copy
import math
import os
import sys
import time

if sys.platform != "win32":  # on Windows only the desktop's own platform has fonts; nothing is shown there either
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtGui import QFont, QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from mxoptions import config, i18n  # noqa: E402

PAGES = ("buttons", "gestures", "scroll", "haptics", "ring", "flow", "device")  # in the order of the sidebar
STATE = {
    "connected": True, "known": True, "name": "MX Master 4", "uinput": True, "battery": (82, 0), "current_host": 0,
    "wheel_mode": "ratchet", "dpi": 1000,
    "hosts": [{"paired": True, "name": "workstation"}, {"paired": True, "name": "laptop"}, {"paired": False, "name": ""}],
    "caps": {
        "buttons": list(config.BUTTONS), "dpi": (200, 8000, 50), "dpi_default": 1000, "smartshift": True, "torque": True,
        "wheel": True, "ratchet_events": True, "thumbwheel": True,
        "waveforms": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 27],
        "force": {"changeable": True, "default": 5477, "min": 4382, "max": 7284},
    },
}

# A made-up keyboard for --keyboard, with the control ids of a real MX keyboard's top row; it keeps the Easy-Switch
# keys and Fn.
KEYBOARD = {
    "connected": True, "known": True, "name": "MX Mechanical", "battery": (64, 0), "current_host": 0,
    "hosts": [{"paired": True, "name": "workstation"}, {"paired": True, "name": "laptop"}, {"paired": False, "name": ""}],
    "keys": [{"id": f"0x{cid:04x}", "cid": cid, "tid": cid, "protected": cid in (0x00D1, 0x00D2, 0x00D3, 0x0034),
              "active": cid in (0x00E0, 0x010A, 0x00E5), "native_reason": "protected" if cid in (0x00D1, 0x00D2, 0x00D3, 0x0034) else None}
             for cid in (0x00D1, 0x00D2, 0x00D3, 0x0034, 0x00C7, 0x00C8, 0x00E0, 0x0103, 0x0108, 0x010A, 0x011C, 0x00E5)],
    "reason": None, "firmware": "12.3.45", "settings": ["fn", "backlight", "platform", "disable", "locks"],
    "controls": {
        "disable": {"value": ["insert"], "keys": ["caps_lock", "num_lock", "scroll_lock", "insert", "super"],
                    "writable": True, "busy": False, "outcome": None},
        "platform": {"value": 0, "choices": [{"index": 0, "os": ["windows", "android"]}, {"index": 1, "os": ["macos", "ios"]},
                                             {"index": 2, "os": ["linux", "chromeos"]}],
                     "writable": True, "busy": False, "outcome": None},
        "backlight": {"value": {"on": True, "power_save": True, "mode": "manual", "level": 4, "hands_out": 60, "hands_in": 30,
                                "powered": 300},
                      "fields": ["on", "power_save", "mode", "level", "hands_out", "hands_in", "powered"],
                      "can": ["on", "mode", "level", "hands_out", "hands_in", "powered"], "levels": 8, "shown_level": 4,
                      "writable": True, "busy": False, "outcome": None},
    },
}
KEYBOARD_KEYS = {"0x00e0": {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_C"]}, "0x010a": {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_V"]},
                 "0x00e5": {"type": "host", "host": 1}}


class StandInEngine:
    """Accepts every call the window makes and does nothing."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("out", help="directory for the PNG files")
    parser.add_argument("--lang", default="en", choices=("en", "uk"))
    parser.add_argument("--theme", default="graphite")
    parser.add_argument("--accent", default="#2fd6a7")
    parser.add_argument("--font", help="application font family (default: the system's)")
    parser.add_argument("--icons", help="icon theme (default: breeze-dark, or breeze for the light theme)")
    parser.add_argument("--model", default="studio", choices=config.MODEL_STYLES, help="how the mouse is drawn")
    parser.add_argument("--ring", default="everyday", help="ring preset shown on the ring page")
    parser.add_argument("--size", default="1280x780", help="window size")
    parser.add_argument("--keyboard", action="store_true", help="with a keyboard as well, and its pages after the others")
    args = parser.parse_args()

    i18n.setup(args.lang)
    app = QApplication([])
    if args.font:
        app.setFont(QFont(args.font, 10))
    QIcon.setThemeSearchPaths([os.path.expanduser("~/.local/share/icons"), os.path.expanduser("~/.icons"), "/usr/share/icons"])
    QIcon.setThemeName(args.icons or ("breeze" if args.theme == "light" else "breeze-dark"))

    from mxoptions.ui import theme
    from mxoptions.ui.main_window import MainWindow
    from mxoptions.ui.store import Store

    app.setStyle("Fusion")
    theme.set_theme(args.theme, args.accent)
    theme.apply(app)

    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["app"].update(theme=args.theme, accent=args.accent, model=args.model)
    cfg["haptics"]["force"] = 5477
    cfg["ring"]["bubbles"] = dict((name, build) for name, _label, build in config.RING_PRESETS)[args.ring]()
    state = STATE
    if args.keyboard:
        cfg["keyboard"]["keys"] = copy.deepcopy(KEYBOARD_KEYS)
        state = dict(STATE, keyboard=KEYBOARD)
    store = Store(cfg, StandInEngine())
    store.flush = lambda: None  # never write the made-up settings anywhere
    store._save_timer.timeout.disconnect()
    window = MainWindow(store)
    width, height = map(int, args.size.split("x"))
    window.resize(width, height)
    window.show()
    store.update_state(state)

    def settle():
        """Let layouts, animations and the worker-thread model render finish."""
        view = window.buttons_page.view
        deadline = time.time() + 8.0
        while time.time() < deadline:
            for _ in range(6):
                app.processEvents()
            busy = view._studio() and (view._rendering or view._wanted or view._settle.isActive())
            if not busy and view._progress >= 1.0:
                break
            time.sleep(0.01)
        for _ in range(6):
            app.processEvents()

    def scroll(page, fraction):
        area = page.findChild(QScrollArea)
        area.verticalScrollBar().setValue(int(area.verticalScrollBar().maximum() * fraction))

    def save(name):
        settle()
        window.grab().save(os.path.join(args.out, f"{name}.png"))

    os.makedirs(args.out, exist_ok=True)
    for index, name in enumerate(PAGES + (("keyboard-keys", "keyboard") if args.keyboard else ())):
        window.stack.setCurrentIndex(index)
        window._nav.button(index).setChecked(True)
        page = window.stack.widget(index)
        if name == "gestures":  # with a swipe drawn on the compass
            settle()
            trace = [(-(t * 19.0) + math.sin(t * 3) * 0.6, -(t * t * 6.5)) for t in (i / 30 for i in range(31))]
            store.gesture_changed.emit({"points": trace, "fired": [], "result": "left", "held": False})
        save(name)
        if name == "gestures":
            scroll(page, 1.0)
            save("gestures-haptics")
        elif name == "scroll":
            scroll(page, 0.62)
            save("wheel-click")
        elif name == "ring":
            folder = next((i for i, bubble in enumerate(store.cfg["ring"]["bubbles"]) if "children" in bubble), None)
            if folder is not None:
                page._open(folder)
                save("ring-folder")
                page._path.clear()  # back out of the folder
                page._refresh()
            action = next((i for i, bubble in enumerate(store.cfg["ring"]["bubbles"]) if "action" in bubble), None)
            if action is not None:  # a bubble being made a dial: the list of what the wheel can turn
                page._select(action)
                page._kind_changed("scroll")
                save("ring-dial")
        elif name == "device":
            scroll(page, 1.0)
            save("device-integrations")
    print("written to", os.path.abspath(args.out))


if __name__ == "__main__":
    main()
