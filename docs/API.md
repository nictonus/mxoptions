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

## Without D-Bus: `mxoptions --call`

The methods and properties below can also be asked of the running program through the program
itself, which needs no D-Bus tool and is the way in on Windows, where there is no session bus:

```sh
mxoptions --call PlayHaptic knock     # true
mxoptions --call Battery              # 82
mxoptions --call Trigger play_pause
mxoptions --call Get pointer.dpi
```

The answer is printed (a list one item to a line). The exit status is 0 when the call was made, 2
when it could not be (no such call, a wrong argument, or the interface switched off in the
settings) and 3 when MX Options is not running. Signals are only on D-Bus. Run from a source tree
it is `python main.py --call …`.

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
| `PlayHaptic(s pattern)` | `b` | Plays a vibration pattern, given by name (`knock`, `sharp-collision`) or number (`12`, `0x0c`). `false` also while battery saving keeps the mouse still; the program checks again on the mouse's latest reading before it plays. |
| `ListHaptics()` | `as` | Names of the patterns the connected mouse can play. |
| `SetDpi(i dpi)` | `b` | Pointer speed until further notice. The user's setting is left alone; `0` returns to it, and so does the user moving the speed slider. |
| `SetWheelMode(s mode)` | `b` | `ratchet`, `freespin`, `toggle`, or `auto` to return to what the settings say. |
| `SwitchHost(i channel)` | `b` | Easy-Switch to another paired computer (channels count from 1). The mouse leaves this computer. |
| `Trigger(s action)` | `b` | Runs an action: a catalogue id (see `ListActions`) or an action as JSON (see below). False if it is not an action, or one this desktop has no way of doing. |
| `ListActions()` | `as` | Ids of the catalogue actions, e.g. `copy`, `play_pause`, `overview`. |
| `OpenRing()` | `b` | Opens the user's Actions Ring at the pointer; it waits for a click. True means the ring was asked for, not that it is on screen yet: on GNOME with Wayland it shows a moment later, if at all. False while a ring is open, while a Spotlight button is held (whether or not its effect shows), or with nothing to show. |
| `ShowRing(s bubbles)` | `b` | Opens a ring of your own for one use (see below); true and false as for `OpenRing`. |
| `CloseRing()` | | Dismisses the ring if it is open, and a Spotlight effect on screen too: a Spotlight button still held shows nothing more until it is pressed again. |
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
| `Gesture(s direction)` | A gesture was recognised: `tap`, `up`, `down`, `left`, `right`. A shape drawn with the button held is announced by its name: `down_right`, `up_down`, `circle_cw`, `zigzag_h` and so on. Pan and zoom announce no direction; a tap of a pan or zoom button is `tap`. |
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
| `{"type": "desktop", "do": "overview"}` | Something every desktop does in its own way, by name: a KDE shortcut on Plasma, a key combination on GNOME, `swaymsg` or `hyprctl` on sway and Hyprland. This is what the catalogue's window and desktop actions are; the names are the catalogue ids (`overview`, `win_close`, `desktop_left`, `screenshot_region`, `lock`, …) plus `desktop_previous`, `desktop_next`, `window_desktop_previous` and `window_desktop_next`. Does nothing where the desktop has no way of doing it. |
| `{"type": "shortcut", "component": "kwin", "name": "Overview"}` | A KDE global shortcut, by its kglobalaccel name. Does nothing on other desktops. |
| `{"type": "command", "command": "firefox"}` | Shell command. |
| `{"type": "script", "name": "VPN", "script": "..."}` | Multi-line script (bash unless it starts with `#!`). |
| `{"type": "wheel_mode"}`, `{"type": "dpi_cycle"}`, `{"type": "host", "host": 1}` | Toggle the wheel mode, switch between the two pointer speeds, Easy-Switch (channels here count from 0). |
| `{"type": "flow"}` | Send the mouse to the other computer of Flow, clipboard included (catalogue id `flow`). Nothing happens unless Flow is set up and the other computer is reachable. |
| `{"type": "hook", "name": "..."}` | Emit `Hook` (see above). |
| `{"type": "none"}` | Do nothing (a button set to this is still reported by `ButtonPressed`). |

Buttons additionally take `{"type": "default"}` (leave it to the mouse), `{"type": "gestures",
"tap": …, "up": …, "down": …, "left": …, "right": …}`, `{"type": "ring"}` and
`{"type": "dpi_shift"}` and `{"type": "spotlight", "highlight": e, "magnify": e, "laser": e}` with each
`e` `{"on": bool, "size": 1–100, "color": "#rrggbb"}` (Spotlight effects while the button is held; at
least one on; anything missing or of the wrong kind becomes its default); these cannot be passed to
`Trigger`. Keys and the buttons of `mouse.profiles` cannot have `spotlight`. Versions before it keep
such a button as it is and do nothing with it.

A `gestures` action may have `"mode": "pan"` or `"zoom"`: while held, movement scrolls the page (both
axes) or zooms (Ctrl and the wheel, up zooms in) instead of swipes; a press without movement still runs
`tap`; directions and shapes stay saved. Any other mode is left out: swipes. Versions before it ignore the
mode, and drop it when they save the settings.

## Settings worth knowing

`q GetConfig` shows everything. Some paths:

| Path | Value |
|---|---|
| `pointer.dpi`, `pointer.alt_dpi`, `pointer.precision_dpi` | Pointer speeds. |
| `wheel.smartshift`, `wheel.threshold`, `wheel.mode`, `wheel.torque`, `wheel.natural` | The scroll wheel. |
| `wheel_click.mode` | `off`, `haptic`, `sound` or `both`: the click when the wheel changes mode. |
| `thumbwheel.function` | `hscroll`, `zoom`, `volume`, `tabs`, `desktops`, `brightness`, `keys`. |
| `thumbwheel.up`, `thumbwheel.down` | What `keys` sends turning up and turning down: a list of up to 16 keyboard key names (`KEY_*`, no mouse buttons), `[]` (default) for nothing. One keystroke per wheel click at speed 1.0, at most one for each report of the wheel and 20 a second: fast turns may skip steps, and skipped steps never run later. `thumbwheel.invert` swaps the two. Keys a button or a key holds through MX Options stay held. A combination with a modifier the user holds down physically may leave that modifier reading released afterwards, depending on the system. `Set` refuses a list the settings would change. |
| `haptics.enabled`, `haptics.level` | Vibration on/off and strength (1–100). |
| `haptics.battery_saving` | `true` (default): no vibration at 10 % battery or less, charging or not; above 10 % it vibrates again. Never sent to the mouse; ignored by older versions. |
| `haptics.screens` | `false` (default). `true`: a short vibration when the pointer moves onto another screen of this computer, not when Flow takes it to another computer. Only where the pointer can be followed: KDE Plasma, X11 desktops, Windows. Never sent to the mouse; ignored by older versions. |
| `rumble.enabled`, `rumble.light`, `rumble.medium`, `rumble.strong`, `rumble.interval`, `rumble.gain` | Rumble from games on the mouse: on/off (a virtual gamepad appears while it is on), the pattern numbers for weak, medium and strong rumble, milliseconds between pulses, sensitivity (0.5–2.0). |
| `buttons.<button>` | The action of `middle`, `back`, `forward`, `gesture`, `smartshift`, `panel`. |
| `gestures.trigger`, `gestures.min_mm`, `gestures.far_mm`, `gestures.dead`, `gestures.bias` | How swipes are read. |
| `gestures.move_speed`, `gestures.move_reverse` | Pan and zoom: speed (0.25–4.0, 1.0 = one wheel detent for 8 mm of travel) and direction (`false`: the page follows the hand). Zoom steps come at most every 50 ms, several detents in one step when the hand moves fast. One scroll or zoom step carries at most 273 detents; more is dropped. How an app takes a zoom step of several detents is up to the app. |
| `ring.bubbles`, `ring.style`, `ring.size` | The user's Actions Ring. |
| `flow.enabled`, `flow.push_mm`, `flow.corner`, `flow.fullscreen` | Flow: on/off, millimetres to push past the edge, percent of the edge kept free at each end, whether to cross under a full-screen window. |
| `flow.hold_ctrl` | `true`: cross at the edge only while a Ctrl key is held; where the desktop cannot tell (Wayland), the edge never crosses while it is `true`. `false` (default): the edge alone is enough. The action and the Easy-Switch buttons do not need Ctrl. `null` sets `false`. Versions before it keep it and cross without Ctrl. |
| `flow.peers` | The other computers of Flow, two at most, each with `name`, `address`, `edge` (`left`, `right`, `top`, `bottom`: the side it stands on) and `key`, the pairing key: treat that as a password. A single field is addressed like `flow.peers.0.edge`. `paused`, where it is, is `true` while that computer is paused: it stays paired and in its place, but the link to it is down, so from then on neither the mouse nor what was copied nor the keyboard goes to it or comes from it (a switch already under way still finishes), and files on their way to or from it stop; `false`, or no `paused`, and it takes part. `flow.peers.0.paused` can be set once it is there (the Flow page writes it); before that, set the whole `flow.peers`. Versions before it drop it, and the computer takes part again. |
| `keyboard.keys` | What the keys of the keyboard do: an object from key id to action, the id being the key's control id as `0x` and four lowercase hex digits (`"0x00c7"`). A key that is not listed does what it always does. Keys take every action above except `wheel_mode`, `dpi_cycle`, `dpi_shift`, `flow` and `gestures`; `host` moves the keyboard, not the mouse; `ring` (exactly `{"type": "ring"}`) opens the Actions Ring at the pointer. At most 128. |
| `keyboard.profiles` | Keys of their own for single apps, at most 32: a list of `{"app", "name", "keys"}`. `app` is the name the app goes by on this desktop, lowercase, 1 to 128 characters (KDE: its window class; sway: its `app_id`, else its X11 class; Hyprland and other X11 desktops: its class; Windows: its program file, like `firefox.exe`), one profile each. `name` is shown in the window, at most 64 characters. `keys` is like `keyboard.keys`, plus `{"type": "default"}`: the key does what it always does in that app, even where `keyboard.keys` changes it. A key a profile does not list does what `keyboard.keys` says. While that app is in front, the first profile for it counts. GNOME on Wayland does not say which app is in front: there only `keyboard.keys` counts. Which app is in front is not offered here. |
| `mouse.profiles` | Buttons and Actions Ring of their own for single apps, at most 32: a list of `{"app", "name", "buttons", "ring"}`. `app` and `name` as in `keyboard.profiles`, one profile each, its own 32. `buttons` maps `middle`, `back`, `forward`, `gesture`, `smartshift`, `panel` to an action a key may have, or `wheel_mode`, `dpi_cycle`, `dpi_shift`, `flow` (exactly `{"type": ...}`), or `{"type": "default"}`: the button's own function in that app, even where `buttons` changes it. Never `gestures`: an app's own gestures are in `mouse.gestures`. A button not listed does what `buttons` says. `ring`: bubbles in the format of `ring.bubbles`, or `null` for the ring of all apps. The thumb wheel's function and keystrokes (`mouse.thumbwheel`, `mouse.thumbwheel_keys`) and a button's gestures (`mouse.gestures`) can differ by app; the thumb wheel's direction, speed and steps, how gestures are read and felt, pointer, scroll, SmartShift and haptics stay the same in every app. |
| `mouse.thumbwheel` | Thumb wheel functions of their own for apps with a mouse profile: a list of `{"app", "function"}`, `function` one of those of `thumbwheel.function`, one entry each. An app not listed uses `thumbwheel.function`. `Set` refuses an entry for an app without a profile, with another function or with other keys; loading settings keeps an entry's app and function only and leaves out the entries it cannot use. Removing a profile removes its entry. An entry is added by setting the whole list. `thumbwheel.invert`, `thumbwheel.speed` and `thumbwheel.ticks` count in every app. `keys` in an app sends its own keystrokes (`mouse.thumbwheel_keys`) if it has them, else `thumbwheel.up` and `thumbwheel.down`. |
| `mouse.thumbwheel_keys` | What `keys` sends in apps with a mouse profile: a list of `{"app", "up", "down"}`, one entry each; `up` and `down` as `thumbwheel.up` (up to 16 keyboard key names, `[]` sends nothing in that app). An app not listed sends `thumbwheel.up` and `thumbwheel.down`. An entry is kept whatever the app's function and counts while it is `keys`. `Set` refuses an entry for an app without a profile, without both directions, with a combination the thumb wheel cannot send or with other keys; loading settings makes such a direction `[]` and leaves out entries without a profile. Removing a profile removes its entry. |
| `mouse.gestures` | Gestures of their own for buttons of apps with a mouse profile: a list of `{"app", "buttons"}`, one entry each; `buttons` maps `middle`, `back`, `forward`, `gesture`, `smartshift`, `panel` to a `gestures` action (above, `mode` included). `tap`, `up`, `down`, `left`, `right` and each shape take what a key may except `ring`, or `wheel_mode`, `dpi_cycle`, `flow` (exactly `{"type": ...}`). A button the app's profile lists does what the profile says; a button in neither does what `buttons` says. `Set` refuses an entry for an app without a profile, a button the profile lists, an action that is not `gestures`, a slot or mode it cannot have, or other keys; loading settings makes such a slot `{"type": "none"}` and leaves out such a mode, button or entry. Removing a profile removes its entry. `gestures.*` (how swipes are read, Pan and Zoom speed and direction, vibrations) counts in every app. |
| `flow.clipboard`, `flow.files`, `flow.files_max_mb`, `flow.cue_arrived`, `flow.cue_failed` | Flow: whether copied text and pictures go along, whether files do, the most megabytes in one go, and the vibration patterns for files that have arrived and files that could not be brought over. |

`Set` changes only what is there, so a key is given an action, or its action taken away, by setting
the whole of `keyboard.keys` at once; the action of a key already listed can be set on its own:

```sh
q Set keyboard.keys '{"0x00c7": {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_C"]}}'
q Set keyboard.keys.0x00c7 '{"type": "keys", "keys": ["KEY_F5"]}'
q Set keyboard.keys '{}'
```

```sh
q Set keyboard.profiles '[{"app": "org.kde.kate", "name": "Kate", "keys": {"0x00c7": {"type": "default"}}}]'
q Set keyboard.profiles.0.keys.0x00c7 '{"type": "keys", "keys": ["KEY_F5"]}'
```

```sh
q Set mouse.profiles '[{"app": "org.kde.kate", "name": "Kate", "buttons": {"back": {"type": "default"}}, "ring": null}]'
q Set mouse.profiles.0.buttons.back '{"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_C"]}'
```

Under `keyboard` and `mouse`, `Set` returns `false` and changes nothing if any of what was sent would not be kept
as it is (an id in another form, an action a key cannot have, `{"type": "default"}` in `keyboard.keys`,
a profile with a field more or less, an app name in capitals, two profiles for one app). An action
is checked all the way down: key names the virtual keyboard knows, at most 16 to a combination; a
macro of 1 to 64 steps, each complete (a `delay` a whole number of `ms` from 0 to 10000, a `click`
a mouse button, a `text` or `command` text, an `action` one complete action of type `keys`, `button`,
`shortcut`, `desktop`, `command`, `script` or `hook`, a `haptic` a `waveform` the mouse's haptic
feature names); no empty command, script, shortcut or hook name.

A key set to `{"type": "ring"}` opens the Actions Ring as the mouse's ring button does: held, it runs
the bubble under the pointer when let go; tapped, the ring stays open for a click; pressed again, it
closes. Bubbles are picked with the pointer. The ring belongs to the button or key that opened it
last: only that one's release counts, and the keyboard going away never closes a ring the mouse
holds, nor the other way round. A held ring closes, running nothing, when the key's action changes:
new settings, or another app in front with a profile that changes that key. Where the ring is an
ordinary window (`MXOPTIONS_RING=window`, or a Wayland desktop with neither layer-shell nor an X
server for X11 programs), that window takes the focus and MX Options becomes the app in front: a
key that has the ring from an app's profile, held, closes it after a moment. Tap such a key there
instead, or give it the ring in `keyboard.keys`.

Versions of MX Options with keyboard support from before keys could open the ring do not keep `ring`
on a key: such a key does what it always does there, and the first setting they save takes it out
of `keyboard.keys` and of every profile for good. Before going back to one, export the settings
(*Device & app → Settings file*); importing that file again in this version brings those keys back.

### Mouse in single apps

While an app with a mouse profile is in front, its buttons, its ring and its thumb wheel function
count, 0.3 s after it came to the front; everywhere else the settings for all apps. `Get` and
`GetConfig` show saved settings only, never which app is in front or what its profile makes of a
button or the thumb wheel. Same limits as keyboard profiles: app matched by name only; KDE, X11,
sway, Hyprland and Windows only, GNOME on Wayland none (there the settings for all apps count). MX
Options' own window in front keeps the mouse as it was for the app before (the keyboard follows its
own window).

When the app in front changes a button: what it holds is let go of then, its release later does
nothing, and macros it started stop (a command already started keeps running; macros a gesture or
a ring bubble started play on). Unchanged buttons stay as they are. A gesture held on through the
change because the button does the same in both apps goes on, but pan or zoom movement made before
the change that has not scrolled or zoomed yet is dropped. When the ring of the new app
differs, an open ring closes and a bubble picked in it runs nothing, even after coming back to the
first app. A bubble already picked still runs when a ring closes for another reason (same ring in
the new app, its button changed, a device gone). Until a closed ring is off the screen, the thumb
wheel may still turn its dials for a moment. Only changed buttons whose reporting changes are
written to the mouse.

When the thumb wheel's function or keystrokes change with the app in front, a step begun before is
dropped (half a step towards the next tab does not carry over), and so is a part of a sideways
scroll; with the same function and keystrokes in both apps it goes on. The mouse is told only when
the thumb wheel changes between scrolling sideways by itself and going to MX Options; a turn made
just before that may still come in the old way. While the Actions Ring is open, the thumb wheel
turns its dials, whatever the app. Horizontal scroll is the thumb wheel's own function: in an app it
scrolls at the speed and in the direction set for all apps.

`OpenRing` shows the ring for all apps, `ShowRing` the caller's bubbles, whatever app is in front;
neither closes when the app in front changes, and their bubbles run.

Versions without mouse profiles ignore `mouse.profiles` and keep it as it is when they save,
export or import. "Restore defaults" removes them, in every version. Before going back, export the
settings; importing that file in this version brings the profiles back. Versions with mouse
profiles but without thumb wheel functions per app keep `mouse.thumbwheel` as it is and use the
function for all apps; they accept any value there from `Set`, and this version leaves out what it
cannot use when it loads. There, removing an app's profile and adding it again before this version
runs gives the app its earlier thumb wheel function back. To undo a function of one app, choose "As
in all apps" or remove the app.

Versions without custom keystrokes on the thumb wheel use Horizontal scroll where `keys` is set and
keep `thumbwheel.up` and `thumbwheel.down` as they are; the version just before them also leaves out
an app's `keys` entry of `mouse.thumbwheel`. Before going back, export the settings; importing that
file in this version brings the keystrokes back.

Versions without keystrokes per app keep `mouse.thumbwheel_keys` as it is and send the keystrokes
for all apps; they accept any value there from `Set`, and this version makes what it cannot use
`[]` when it loads. There, removing an app's profile and adding it again before this version runs
gives the app its earlier keystrokes back. Before going back, export the settings; importing that
file in this version brings each app's keystrokes back. To undo an app's keystrokes, use "Use the
keystrokes for all apps" or remove the app.

Versions without gestures per app keep `mouse.gestures` as it is and use the button as set for
all apps or in the profile; they accept any value there from `Set`, and this version leaves out
what it cannot use when it loads. A button such a version sets in the app's profile wins over the
app's gestures for it. There, removing an app's profile and adding it again before this version
runs gives the app its earlier gestures back. Before going back, export the settings; importing
that file in this version brings each app's gestures back. To undo an app's gestures on a button,
choose "As in all apps" for it or remove the app.

Macro steps, for a button or a key:

| Step | Meaning |
|---|---|
| `{"do": "action", "action": {"type": "hook", "name": "paint.brush"}}` | Do one action, as a gesture or the Actions Ring would. No macro inside a macro, and nothing that talks to a device (`host`, `flow`, `wheel_mode`, `dpi_cycle`). |
| `{"do": "haptic", "waveform": 4}` | Play a vibration on the mouse (waveform ids 0 to 14 and 27), if it is connected and has a haptic motor; otherwise nothing. |

An action or haptic step that is not complete is kept in the settings of a button but skipped when its macro plays.

A macro with `"stop_on_press": true` on a mouse button or a key stops when that same button or key
is pressed again while the macro plays or waits in the queue: dropped while queued, ended before its
next step, a wait cut short. That press starts nothing; the press after it starts the macro again. A
step already under way finishes, and a command the macro started keeps running. The macro counts as
playing until it is quite over, the short pause after its last step included: a press just as it
ends may only stop it. Only presses of the button or key that started it count: another button or
key with an equal macro, a gesture, a ring bubble and `Trigger` start it as before, queued behind.
Without the field, or with `false`, a press while it plays queues it once more, as before. Under
`keyboard` and `mouse.profiles` the field is `true` or `false`. Versions before it keep the field
but ignore it, and their macro editor drops it when the macro is saved there.

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
