# MX Options D-Bus API

MX Options offers one D-Bus interface on the **session bus** so that scripts and other programs
can use the mouse: play its vibrations, run actions, show a ring of their own, read and change
settings, and follow buttons, gestures and the wheel.

| | |
|---|---|
| Service | `org.mxoptions.MXOptions` |
| Object | `/org/mxoptions/Mouse` |
| Interface | `org.mxoptions.Mouse1` |

The interface exists while MX Options is running and *Device & app → Integrations → Let other
programs use the mouse* is on (it is by default). `ApiVersion` is `1`; names in `Mouse1` will not
change meaning, and anything incompatible would arrive as `Mouse2`.

Everything structured (actions, bubbles, settings) travels as **JSON text**, so the interface is as
usable from a shell as from a language binding. Methods report failure by returning `false` or an
empty string, never by a D-Bus error.

## Quick start

```sh
q() { qdbus6 org.mxoptions.MXOptions /org/mxoptions/Mouse "$@"; }

q PlayHaptic knock            # true
q ListHaptics                 # the pattern names this mouse knows
q Get pointer.dpi             # 1000
q Trigger play_pause          # run a catalogue action
q SetWheelMode freespin       # and "auto" to hand it back
```

The same call with other tools:

```sh
busctl --user call org.mxoptions.MXOptions /org/mxoptions/Mouse org.mxoptions.Mouse1 PlayHaptic s knock
gdbus call --session --dest org.mxoptions.MXOptions --object-path /org/mxoptions/Mouse \
      --method org.mxoptions.Mouse1.PlayHaptic knock
```

Watch what the mouse does:

```sh
dbus-monitor --session "type='signal',interface='org.mxoptions.Mouse1'"
```

## Methods

| Method | Returns | What it does |
|---|---|---|
| `PlayHaptic(s pattern)` | `b` | Plays a vibration pattern, given by name (`knock`, `sharp-collision`) or number (`12`, `0x0c`). |
| `ListHaptics()` | `as` | Names of the patterns the connected mouse can play. |
| `SetDpi(i dpi)` | `b` | Pointer speed until further notice. The user's setting is left alone; `0` returns to it, and so does the user moving the speed slider. |
| `SetWheelMode(s mode)` | `b` | `ratchet`, `freespin`, `toggle`, or `auto` to return to what the settings say. |
| `SwitchHost(i channel)` | `b` | Easy-Switch to another paired computer (channels count from 1). The mouse leaves this computer. |
| `Trigger(s action)` | `b` | Runs an action: a catalogue id (see `ListActions`) or an action as JSON (see below). |
| `ListActions()` | `as` | Ids of the catalogue actions, e.g. `copy`, `play_pause`, `overview`. |
| `OpenRing()` | `b` | Opens the user's Actions Ring at the pointer; it waits for a click. |
| `ShowRing(s bubbles)` | `b` | Opens a ring of your own for one use (see below). |
| `CloseRing()` | | Dismisses the ring if it is open. |
| `Get(s path)` | `s` | One setting as JSON, e.g. `pointer.dpi`, `buttons.back`, `ring.bubbles.0.label`. Empty if there is no such path. |
| `Set(s path, s value)` | `b` | Changes one existing setting and saves it, exactly as if the user had done so in the window. `value` is JSON; text that is not valid JSON is taken as a plain string. |
| `GetConfig()` | `s` | The whole configuration as JSON. |
| `ShowWindow()` | | Brings up the settings window. |

`Set` only accepts paths that exist and values of the kind that is already there (a number for a
number, an action for an action). Numbers are clamped to their range; unknown words are refused.

## Properties

`ApiVersion` (i), `AppVersion` (s), `Connected` (b), `DeviceName` (s), `Battery` (i, percent,
`-1` if unknown), `Charging` (b), `Dpi` (i, the speed in effect now), `WheelMode` (s: `ratchet`,
`freespin`, or empty if unknown).

## Signals

| Signal | When |
|---|---|
| `ButtonPressed(s button)`, `ButtonReleased(s button)` | A button MX Options handles went down or up: `middle`, `back`, `forward`, `gesture`, `smartshift`, `panel`. Buttons left on *Default* are handled by the mouse itself and are not reported, nor are the two main buttons. |
| `Gesture(s direction)` | A gesture was recognised: `tap`, `up`, `down`, `left`, `right`. A shape drawn with the button held is announced by its name: `down_right`, `up_down`, `circle_cw`, `zigzag_h` and so on. |
| `WheelModeChanged(s mode)` | The wheel went to `freespin` or back to `ratchet`: by the mode button, by a request, or by SmartShift letting it go at speed and catching it when it stops. The mouse does not announce SmartShift's own changes, so those are worked out from the wheel's speed and can be off by a notch or two. |
| `Hook(s name, s phase)` | A "Signal to another program" action ran (see below). |
| `ConnectedChanged(b)`, `BatteryChanged(i percent, b charging)`, `DpiChanged(i)`, `ConfigChanged()` | The corresponding state changed. |

## Hooks: letting the user bind the mouse to your program

In the action list of any button, gesture direction or ring bubble the user can pick **Signal to
another program** and type a name. MX Options then does nothing itself: it emits
`Hook(name, phase)` and your program decides what happens.

`phase` is `press` and `release` for a button, and `trigger` for a gesture, a ring bubble or a
call to `Trigger`. As an action it is `{"type": "hook", "name": "myapp.something"}`; prefix names
with your program's name to keep them apart from others'.

To have a button all to yourself without asking the user to click through the window:

```sh
q Set buttons.back '{"type": "hook", "name": "myapp.back"}'
```

## A ring of your own

`ShowRing` takes a JSON list of up to 8 bubbles, in the format of `ring.bubbles` in the settings.
A bubble has an optional `label`, `icon` (a freedesktop icon name, an emoji or an image path) and
`color` (`#rrggbb`), and exactly one of:

- `action`: what it runs when picked;
- `children`: a list of bubbles, which makes it a folder (up to 3 levels);
- `scroll`: the id of a dial turned with the wheel (`volume`, `brightness`, `zoom`, `tabs`,
  `tracks`, `seek`, `undo`, `history`, `desktops`, `win_desktop`, and for the Krohnkite tiling
  script `tile_layout`, `tile_width`, `tile_focus`).

```sh
q ShowRing '[
  {"label": "Brush",  "icon": "draw-brush",  "action": {"type": "hook", "name": "paint.brush"}},
  {"label": "Eraser", "icon": "draw-eraser", "action": {"type": "hook", "name": "paint.eraser"}},
  {"label": "Undo",   "action": {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_Z"]}}
]'
```

The pick comes back as `Hook("paint.brush", "trigger")`. The ring closes by itself after a pick, a
click elsewhere or 20 seconds without movement.

## Actions as JSON

| Action | Meaning |
|---|---|
| `{"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_C"]}` | Keyboard shortcut (Linux input key names). |
| `{"type": "button", "button": "BTN_LEFT"}` | Mouse click: `BTN_LEFT`, `BTN_RIGHT`, `BTN_MIDDLE`, `BTN_SIDE`, `BTN_EXTRA`. |
| `{"type": "shortcut", "component": "kwin", "name": "Overview"}` | A KDE global shortcut, by its kglobalaccel name. |
| `{"type": "command", "command": "konsole"}` | Shell command. |
| `{"type": "script", "name": "VPN", "script": "..."}` | Multi-line script (bash unless it starts with `#!`). |
| `{"type": "wheel_mode"}`, `{"type": "dpi_cycle"}`, `{"type": "host", "host": 1}` | Toggle the wheel mode, switch between the two pointer speeds, Easy-Switch (channels here count from 0). |
| `{"type": "flow"}` | Send the mouse to the other computer of Flow, clipboard included (catalogue id `flow`). Nothing happens unless Flow is set up and the other computer is reachable. |
| `{"type": "hook", "name": "..."}` | Emit `Hook` (see above). |
| `{"type": "none"}` | Do nothing (a button set to this is still reported by `ButtonPressed`). |

Buttons additionally take `{"type": "default"}` (leave it to the mouse), `{"type": "gestures",
"tap": …, "up": …, "down": …, "left": …, "right": …}`, `{"type": "ring"}` and
`{"type": "dpi_shift"}`; these cannot be passed to `Trigger`.

## Settings worth knowing

`q GetConfig` shows everything. Some paths:

| Path | Value |
|---|---|
| `pointer.dpi`, `pointer.alt_dpi`, `pointer.precision_dpi` | Pointer speeds. |
| `wheel.smartshift`, `wheel.threshold`, `wheel.mode`, `wheel.torque`, `wheel.natural` | The scroll wheel. |
| `wheel_click.mode` | `off`, `haptic`, `sound` or `both`: the click when the wheel changes mode. |
| `thumbwheel.function` | `hscroll`, `zoom`, `volume`, `tabs`, `desktops`, `brightness`. |
| `haptics.enabled`, `haptics.level` | Vibration on/off and strength (1–100). |
| `rumble.enabled`, `rumble.light`, `rumble.medium`, `rumble.strong`, `rumble.interval`, `rumble.gain` | Rumble from games on the mouse: on/off (a virtual gamepad appears while it is on), the pattern numbers for weak, medium and strong rumble, milliseconds between pulses, sensitivity (0.5–2.0). |
| `buttons.<button>` | The action of `middle`, `back`, `forward`, `gesture`, `smartshift`, `panel`. |
| `gestures.trigger`, `gestures.min_mm`, `gestures.far_mm`, `gestures.dead`, `gestures.bias` | How swipes are read. |
| `ring.bubbles`, `ring.style`, `ring.size` | The user's Actions Ring. |
| `flow.enabled`, `flow.push_mm`, `flow.corner`, `flow.fullscreen` | Flow: on/off, millimetres to push past the edge, percent of the edge kept free at each end, whether to cross under a full-screen window. |
| `flow.peers` | The other computers of Flow, two at most, each with `name`, `address`, `edge` (`left`, `right`, `top`, `bottom`: the side it stands on) and `key`, the pairing key: treat that as a password. A single field is addressed like `flow.peers.0.edge`. |
| `flow.clipboard`, `flow.files`, `flow.files_max_mb`, `flow.cue_arrived`, `flow.cue_failed` | Flow: whether copied text and pictures go along, whether files do, the most megabytes in one go, and the vibration patterns for files that have arrived and files that could not be brought over. |

## Example: a Python client

Needs `dbus-python` and PyGObject.

```python
import dbus
import dbus.mainloop.glib
from gi.repository import GLib

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()
mouse = dbus.Interface(bus.get_object("org.mxoptions.MXOptions", "/org/mxoptions/Mouse"), "org.mxoptions.Mouse1")

def on_hook(name, phase):
    if name == "myapp.confirm" and phase in ("press", "trigger"):
        mouse.PlayHaptic("completed")

mouse.connect_to_signal("Hook", on_hook)
mouse.connect_to_signal("WheelModeChanged", lambda mode: print("wheel is now in", mode))
GLib.MainLoop().run()
```

## Example: vibrate when a long command finishes

```sh
make -j8; qdbus6 org.mxoptions.MXOptions /org/mxoptions/Mouse PlayHaptic completed
```

## Notes

- The session bus is per user: any program of the same user can call the interface, and through
  `Trigger` and `Set` that includes running commands. Switch the interface off in the settings if
  that is not wanted.
- Calls that need the mouse return `false` while it is asleep or out of range. A mouse that has
  just woken up can take a second to answer the first request.
- `Gesture` is emitted in the practice mode of the gestures page too, where the gesture's own
  action is not run.
