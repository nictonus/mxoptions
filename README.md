# MX Options

A settings app for Logitech MX Master mice on Linux, in the spirit of Logi Options+: buttons,
gestures, the Actions Ring, the wheels, haptics and Easy-Switch, with a tray icon, a background
service and a D-Bus API for other programs.

[Українською](README.uk.md)

> **Written by an AI.** Every part of this project (the code, the interface, the sounds, the tests
> and this documentation) was written by [Claude](https://claude.com/claude-code), Anthropic's AI
> model, working in Claude Code. A human, [nictonus](https://github.com/nictonus), directed the work
> and tried each build on a real MX Master 4. Read the code with that in mind, and see
> [Known limits](#known-limits) for what has and has not been checked on hardware.

> **Unofficial.** Not affiliated with or endorsed by Logitech. "Logitech", "MX Master", "Logi
> Options+", "Easy-Switch" and "SmartShift" are trademarks of Logitech.

![Actions Ring](docs/screenshots/ring.png)

| | |
|---|---|
| ![Gestures](docs/screenshots/gestures.png) | ![Buttons](docs/screenshots/buttons.png) |
| ![Wheel](docs/screenshots/wheel-click.png) | ![Haptics for gestures](docs/screenshots/gestures-haptics.png) |

## What it does

- **Buttons.** Middle, back, forward, gesture, wheel-mode button and the haptic panel each take
  any of about 70 ready actions, a keyboard shortcut, a KDE global shortcut, a command, a script,
  a macro, or a signal to another program. The mouse is drawn with the button you are pressing lit
  up.
- **Gestures.** Hold a button and swipe in four directions, or tap. A live compass shows how your
  movement is read. You choose when a swipe fires (on release, at once, or repeating as you keep
  moving), the distances in millimetres, a dead zone for diagonals, and a vibration pattern for each
  moment of a gesture.
- **Actions Ring.** A menu around the cursor, as bubbles, a pie or tiles. Bubbles run actions, open
  their own ring of bubbles (three levels deep), or are *dials* that you turn with the wheel or the
  thumb wheel without closing the menu: volume, tabs, zoom, undo, tracks and more. The cursor stays
  free while the ring is open. Presets are included and your own can be saved.
- **Wheels.** Pointer speed (two speeds and a precision speed), SmartShift and its threshold,
  ratchet force, natural direction, high-resolution scrolling, and what the thumb wheel does. An
  optional click, felt or heard, marks SmartShift letting the wheel go and catching it again.
- **Haptics.** Strength, the built-in patterns, and how hard the thumb panel has to be pressed.
- **Also:** Easy-Switch between computers, battery level in the tray, four themes with any accent
  colour, English and Ukrainian.
- **For other programs:** a [D-Bus API](docs/API.md) to play vibrations, run actions, show a ring of
  their own, change settings, and follow buttons, gestures and the wheel.

## Requirements

Developed and tested on **KDE Plasma 6 (Wayland)**, on an Arch-based distribution, with an **MX
Master 4 on a Bolt receiver**. Other MX mice use the same protocol and should largely work, but
none has been tried.

- Python 3.12 or newer
- PySide6 (Qt 6.6 or newer), with Qt Declarative and Qt Multimedia
- python-evdev
- layer-shell-qt (its QML module draws the ring over other windows)
- numpy, optional: the glossy 3D model and soft shadows

Use your distribution's PySide6. One installed with pip brings its own Qt, which cannot see the
system's layer-shell module, so the Actions Ring would not appear.

| Distribution | Packages |
|---|---|
| Arch, CachyOS, Manjaro | `pyside6 python-evdev qt6-declarative qt6-multimedia layer-shell-qt python-numpy` |

Outside KDE the mouse settings themselves work, but the ring needs a compositor with layer-shell,
and many ready actions are KDE shortcuts.

## Installing

### Arch Linux and derivatives

Download the package from the [latest release](https://github.com/nictonus/mxoptions/releases/latest)
and install it:

```sh
sudo pacman -U mxoptions-*-any.pkg.tar.zst
systemctl --user enable --now mxoptions.service
```

Or build the same package yourself:

```sh
git clone https://github.com/nictonus/mxoptions.git && cd mxoptions/packaging/aur
makepkg -si
```

### Any distribution, for your user only (no root)

Install the requirements above, then:

```sh
git clone https://github.com/nictonus/mxoptions.git && cd mxoptions
make install-user     # into ~/.local
make enable           # start now and at every login
```

If the app then reports that it cannot find or reach the mouse, see [Permissions](#permissions).

### Any distribution, for all users

```sh
sudo make install     # into /usr/local, device access rules included
sudo make udev        # apply those rules now, without replugging the receiver
systemctl --user enable --now mxoptions.service
```

### Without installing

```sh
python3 main.py
```

## Running as a service

An installation brings a systemd *user* service, `mxoptions.service`, tied to your graphical
session. It keeps the mouse configured in the background; the window is opened from the
application menu or the tray icon.

```sh
systemctl --user status mxoptions      # is it running, has it found the mouse
journalctl --user -u mxoptions -f      # its log
systemctl --user restart mxoptions
systemctl --user disable --now mxoptions
```

"Start at login" in the app's settings switches the same service on and off. Quitting from the tray
stops the service until the next login; while it is stopped the mouse behaves as it does without
any software.

## Permissions

The app talks to the mouse through its `hidraw` device and sends key presses, clicks and scrolling
through `/dev/uinput`. Neither is open to ordinary users by default. `data/70-mxoptions.rules`
grants both to whoever is logged in at the computer:

```sh
sudo make udev
```

The Arch package and `sudo make install` already include the rules. Many systems have equivalent
rules from Solaar, Steam or KDE Connect, in which case nothing more is needed.

## Other mouse software

Stop **logid** (logiops) and **Solaar** before using MX Options. Two programs redirecting the same
buttons undo each other's settings.

```sh
sudo systemctl disable --now logid
```

## The picture of the mouse

The Buttons page can show Logitech's own render of the MX Master 4, in three body colours. Those
pictures belong to Logitech and are not part of this repository: the app downloads them from
logitech.com when you press *Download* and keeps them in `~/.local/share/mxoptions/mouse`. Until
then, and for other mice, it draws its own 3D model.

## Files

| | |
|---|---|
| Settings | `~/.config/mxoptions/config.json` |
| Mouse pictures | `~/.local/share/mxoptions/mouse/` |
| Generated click sounds | `~/.cache/mxoptions/sounds/` |

## Known limits

- **Left and right buttons cannot be reassigned.** The mouse does not let software take them over.
- **SmartShift's own switching is inferred.** The mouse announces a mode change made with the
  button, not the one SmartShift makes at speed, so the app works that out from how fast the wheel
  turns. The click can be a notch or two away from the real moment.
- **Text in macros is pasted.** It goes through the clipboard and Ctrl+V, so it does not work in
  terminals.
- **Smooth scrolling passes through the app.** With it on, the wheel only scrolls while the app
  runs.
- **Checked on one mouse.** Everything was tried on a single MX Master 4. Synthetic tests cover the
  logic; the feel of gestures, vibrations and sounds was judged by one person.
- **One open question:** the haptic panel may not register while the left button is held down. If
  so that is the mouse's doing; it has not been confirmed either way.

## Development

```sh
make test                                  # unit tests for the logic (no mouse needed)
python3 tools/check_i18n.py                # untranslated, unused and duplicated strings
python3 tools/screenshots.py /tmp/shots    # render every page without a mouse or a screen
```

| | |
|---|---|
| `mxoptions/hidpp.py`, `device.py` | HID++ 2.0 transport and the features of the mouse |
| `mxoptions/engine.py` | the worker thread: applies settings, turns buttons, wheels and gestures into actions |
| `mxoptions/actions.py`, `vinput.py` | the action catalogue, its executor, the virtual keyboard and mouse |
| `mxoptions/config.py` | the settings file: defaults, validation, migration, ring presets |
| `mxoptions/api.py` | the D-Bus interface |
| `mxoptions/ui/` | the settings window, the ring overlay, sounds |
| `data/` | desktop entry, systemd unit, udev rules |

## Uninstalling

```sh
make uninstall-user          # or: sudo make uninstall, or: sudo pacman -R mxoptions
rm -r ~/.config/mxoptions ~/.local/share/mxoptions ~/.cache/mxoptions    # settings and downloads
```

## Licence

[MIT](LICENSE).
