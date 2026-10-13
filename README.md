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
  any of about 70 ready actions (each done the way your desktop does it), a keyboard shortcut, a
  command, a script, a macro, or a signal to another program. The mouse is drawn with the button you are pressing lit
  up.
- **Gestures.** Hold a button and swipe in four directions, or tap. A live compass shows how your
  movement is read. You choose when a swipe fires (on release, at once, or repeating as you keep
  moving), the distances in millimetres, a dead zone for diagonals, and a vibration pattern for each
  moment of a gesture. The same button also reads sixteen shapes drawn with it held (corners,
  there and back, circles, shakes), each with an action of its own.
- **Actions Ring.** A menu around the cursor, as bubbles, a pie or tiles. Bubbles run actions, open
  their own ring of bubbles (three levels deep), or are *dials* that you turn with the wheel or the
  thumb wheel without closing the menu: volume, tabs, zoom, undo, tracks and more. The cursor stays
  free while the ring is open. Presets are included and your own can be saved.
- **Wheels.** Pointer speed (two speeds and a precision speed), SmartShift and its threshold,
  ratchet force, natural direction, high-resolution scrolling, and what the thumb wheel does. An
  optional click, felt or heard, marks SmartShift letting the wheel go and catching it again.
- **Haptics.** Strength, the built-in patterns, and how hard the thumb panel has to be pressed.
  Rumble from games can be played on the mouse: see [Rumble from games](#rumble-from-games).
- **Flow.** One mouse on two or three computers: push the pointer past the edge of the screen and
  the mouse carries on on the computer behind it, with the text, pictures and files you copied.
  They are paired with a six-digit code and talk over an encrypted link. See [Flow](#flow).
- **Also:** Easy-Switch between computers, a tray icon in five styles (plain ones in the colour of
  your panel that show a missing mouse and a low battery, one that fills with the charge), firmware
  versions with an update check through fwupd, four themes with any accent colour, English and
  Ukrainian.
- **For other programs:** a [D-Bus API](docs/API.md) to play vibrations, run actions, show a ring of
  their own, change settings, and follow buttons, gestures and the wheel.

## Requirements

Developed and tested on **KDE Plasma 6 (Wayland)**, on an Arch-based distribution, with an **MX
Master 4**, on a Bolt receiver and over Bluetooth.

Other Logitech mice that speak HID++ 2.0 are taken as they are: the app offers what the mouse
reports it has (its buttons, SmartShift old or new, thumb wheel, battery) and hides the rest. The
MX Masters before the 4 (3S, 3, 2S and the first) are drawn with the 3D model, any other mouse
with a plain drawing of its own buttons. None of these was tried by the author; a user reported
gestures working on a Signature M650 L.

- Python 3.12 or newer (3.13 or newer for Flow)
- PySide6 (Qt 6.6 or newer), with Qt Declarative and Qt Multimedia
- python-evdev
- layer-shell-qt (its QML module draws the ring over other windows on Wayland; a small library, not a
  desktop: it is wanted on sway and Hyprland as much as on KDE Plasma)
- numpy, optional: the glossy 3D model and soft shadows
- for what was copied to cross with Flow, optional: KGuiAddons with its Python bindings (KDE
  Frameworks 6), or wl-clipboard. On X11 neither is needed.

Use your distribution's PySide6. One installed with pip brings its own Qt, which cannot see the
system's layer-shell module, so the Actions Ring would not appear.

| Distribution | Packages |
|---|---|
| Arch, CachyOS, Manjaro | `pyside6 python-evdev qt6-declarative qt6-multimedia layer-shell-qt python-numpy kguiaddons` |
| Debian 13, Ubuntu 25.04 and newer | `python3-evdev python3-numpy qml6-module-qtquick qml6-module-qtquick-window qml6-module-org-kde-layershell liblayershellqtinterface6` and `python3-pyside6.qtcore`, `.qtgui`, `.qtwidgets`, `.qtdbus`, `.qtnetwork`, `.qtsvg`, `.qtqml`, `.qtquick`, `.qtmultimedia` |
| Fedora | `python3-pyside6 python3-evdev qt6-qtdeclarative qt6-qtmultimedia qt6-qtsvg layer-shell-qt python3-numpy` |

Older Ubuntu releases (24.04 and before) have no PySide6 package.

### Desktops

What talks to the mouse (buttons, gestures, the wheels, pointer speed, haptics, Easy-Switch, game
rumble) does not depend on the desktop. What talks to the desktop does, and is done in the way
each desktop allows:

| | KDE Plasma | sway, Hyprland and other compositors with layer-shell | GNOME on Wayland | X11 desktops (Xfce, Cinnamon, MATE, i3, …) |
|---|---|---|---|---|
| Ready actions for windows and the desktop | KDE's own shortcuts: all of them | `swaymsg`, `hyprctl`; a launcher, screenshots and a terminal from what is installed (fuzzel, wofi, rofi; grim and slurp) | GNOME's key combinations as it ships them | the usual key combinations (Alt+F4, Ctrl+Alt+arrows, Print, …) |
| Actions Ring | on a layer above the windows | the same | in a window on the session's X server (XWayland), which takes no focus; a little soft where the display is scaled by a fraction | in a window the window manager leaves alone; as a panel of the ring's size where nothing composites |
| Flow: the screen edge | a KWin script | a see-through line one pixel wide along the edge | not possible: use the *Move to the other computer (Flow)* action | the pointer's position is asked for |
| Flow: what was copied | KGuiAddons, else wl-clipboard | the same | wl-clipboard | Qt itself |

An action a desktop has no way of doing is not offered there, and settings made on KDE keep working:
*Overview* stays *Overview*, done as the desktop at hand does it. The key combinations are the
ones a desktop comes with; one you have rebound will not do its job, and then a keyboard shortcut of
your own in its place will.

**What was actually run.** Only KDE Plasma 6 on Wayland is used day to day. The X11 ways (the ring's
window, the pointer watching, full-screen detection, the clipboard) were run in a virtual X server
with no window manager. The line along the edge was run on KWin, where it hears the pointer and
passes clicks and scrolling on, and so was the ring through XWayland, which there comes up over
everything, is picked from and leaves the focus where it was. Nothing at all was run on GNOME,
sway, Hyprland or any X11 desktop itself: their commands and key combinations come from their
documentation. Reports are welcome.

On GNOME the ring is drawn by a small second program (it has to talk to the X server, which the
main one, a Wayland program, cannot). Where the session keeps no X server the ring falls back to an
ordinary window, which takes the focus while it is open.

`MXOPTIONS_DESKTOP` (`kde`, `gnome`, `sway`, `hyprland`, `other`), `MXOPTIONS_RING` (`layer`, `x11`,
`xwayland`, `window`), `MXOPTIONS_FLOW_EDGES` (`kwin`, `poll`, `strips`, `none`) and `MXOPTIONS_CLIPBOARD`
(`ksystem`, `qt`, `wl-clipboard`, `klipper`, `none`) override what the program works out for
itself, should it get one of them wrong.

## Windows (in progress)

The program is being ported to Windows. `python tools\build_windows.py` (with Python 3.13,
`pip install PySide6 numpy pyinstaller`, and Inno Setup for the last of the three) makes a folder
with `mxoptions.exe`, a zip of it, and an installer, `mxoptions-<version>-setup.exe`. The installer
puts the program into the user's own program folder, asks for no administrator, adds a Start menu
entry and, if wanted, starts the program at sign-in; it also updates and removes it. Neither the
program nor the installer is signed, so Windows asks before running them the first time. From the
source tree the program runs as `pythonw main.py`.

**Logi Options+ has to be out of the way**, or the two undo each other's settings of the mouse. Its
agent is started again by its service whenever it is closed, so both have to go, from an
administrator's PowerShell (until the next start of Windows, or for good by uninstalling it):

```powershell
Stop-Service OptionsPlusUpdaterService; Stop-Process -Name logioptionsplus_agent, logioptionsplus_appbroker -Force
```

MX Options says so in its window when it finds Options+ running.

What was run on Windows 11 with the mouse on Bluetooth, driven from another computer with nobody
at the mouse:

- the program, both from the tree and built, finds the mouse and reads and sets its battery,
  pointer speed, wheel mode, vibration and Easy-Switch channel;
- keys, text, clicks and scrolling are sent, the ready actions with Windows' own key combinations;
- the Actions Ring opens at the pointer, is picked from, and leaves the keyboard where it was;
- the settings window draws as on Linux, with Windows' own icon font standing in for icon names
  Windows lacks;
- Flow with a Linux computer, by the *Move to the other computer* action: the mouse goes across and
  back, and with it text, files and folders (names Windows cannot write are changed as little as
  will do). A computer with both systems keeps one pairing for the two;
- the mouse's own movement, buttons and wheel are read (through raw input) and told apart from
  other pointing devices, which is what crossing at the screen edge and the click of the wheel
  changing mode by itself go by;
- the screen brightness actions set the laptop's own panel through its display driver, and monitors
  that take it over their cable (the panel was tried, such a monitor was not);
- the installer installs, updates over a running copy, and lists the program among the installed
  ones; "Start at login" writes and removes its entry;
- `mxoptions --call` stands in for the D-Bus interface, which Windows does not have.

**Game rumble needs a driver on Windows.** Windows lets a program add a game controller only through
a driver made for that; the one used is [ViGEmBus](https://github.com/nefarius/ViGEmBus/releases),
which you install once (it is no longer developed, and still the one such tools use). The Windows
build carries its client library (ViGEmClient.dll, MIT licence, fetched and checked when the build is
made). Without the driver the settings say so; with it, rumble has not been run yet.

Not tried, because it takes a hand on the mouse: the buttons, gestures and wheels themselves (the
same code as on Linux), and pushing the pointer past the screen edge to cross. Not tried either: the
Bolt receiver (written, never plugged in there) and game rumble (see above). Not there yet: letting
Flow be found by other computers (Windows' firewall asks about that; the Windows side calls the
other computer itself, which needs no permission).

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

### Debian and Ubuntu

Download the `.deb` from the [releases](https://github.com/nictonus/mxoptions/releases) and
install it; apt fetches what it depends on:

```sh
sudo apt install ./mxoptions_*_all.deb
systemctl --user enable --now mxoptions.service
```

Or build the same package from the source tree, on any system that has Python: `make deb`.

### Fedora

There is a spec file, `packaging/rpm/mxoptions.spec`, to build the package with `rpmbuild` (its
first lines say how). Or install the packages listed above and use `make install-user` below.

The Debian package and the spec were made on Arch from the lists of packages those
distributions publish. Neither has been installed on its distribution by the author yet: please
report what does not fit. `make deb-test` and `make rpm` do that trial: they fetch the official
Debian or Fedora image, enter it without root or a container engine (`tools/distro.py`), install
the package with its dependencies and run the tests there. They download a few hundred megabytes.

### Windows

Take `mxoptions-<version>-setup.exe` from the [releases](https://github.com/nictonus/mxoptions/releases)
and run it; it installs for your user and asks for no administrator. It is not signed, so Windows
asks before running it ("More info", then "Run anyway"). Close Logi Options+ first, as set out under
[Windows (in progress)](#windows-in-progress), which also says what does and does not work there yet.

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

The service starts with systemd's `graphical-session.target`. KDE Plasma and GNOME reach it by
themselves. A compositor started on its own (sway, Hyprland, a window manager from `.xinitrc`)
does not, unless you have set that up; there, start the program from the compositor's own
configuration instead:

```
exec mxoptions --background          # sway, i3
exec-once = mxoptions --background   # Hyprland
```

Without a system tray the program has nowhere to sit behind its window: started in the background
it still runs, and starting it again from the menu opens the window.

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

## Flow

Flow moves the mouse between computers when the pointer is pushed past the edge of the screen, the
way Logitech's own Flow does, and takes along what you copied: text, pictures, files and folders.
It is this project's own implementation: it works between computers running MX Options, not with
Logi Options+. A computer can have another one on two of its sides, so three in a row share one
mouse (it has three Easy-Switch channels).

What it needs:

- MX Options running on every one of them, on the same network;
- the mouse paired with each, on an Easy-Switch channel of its own (Bolt receiver or Bluetooth);
- a desktop that lets the pointer be watched at the screen edge: all but GNOME on Wayland do (see
  [Desktops](#desktops)). There, and wherever you prefer it, a button, a gesture or a ring bubble
  with the action *Move to the other computer (Flow)* takes the place of the edge;
- for what you copied to cross: KGuiAddons with its Python bindings or wl-clipboard on Wayland,
  nothing more on X11.

Setting it up, on the *Flow* page:

1. On one computer press *Show a code*. It shows six digits.
2. On the other, open the same page. It finds the first computer by itself and asks for the code.
3. On the picture, click where the other computer stands. The other one takes the facing side by
   itself. For a third computer, repeat steps 1 and 2 with it and the one in the middle.
4. Use the mouse on each computer once (switch with the Easy-Switch button), so that both learn
   their channel. From then on, cross at the edge.

What can be tuned there:

- **Push past the edge:** how many millimetres the mouse has to keep moving once the pointer is at
  the edge, measured on the mouse itself. At 0 a touch is enough.
- **Keep the corners free:** the share of the edge, at each end, that does not lead across, so that
  hot corners keep working.
- **Full-screen windows:** by default a game or a film in full screen keeps the mouse.
- **What goes along:** text and pictures, files and folders, and the largest amount to send in one
  go. Files are sent when you cross and wait in `~/.cache/mxoptions/flow-files` for a day. The
  clipboard of the computer you arrive on is left as it is until all of them are there; then they
  take it over, and the mouse gives a vibration of your choice (another one if they could not be
  brought over). The Flow page shows how far a transfer is and can cancel it.

How it works, and how it is kept private:

- **Pairing.** The six digits are never sent. The two computers run SRP-6a (RFC 5054, 3072-bit
  group, SHA-256), a password-authenticated key exchange: somebody listening or sitting in between
  gets nothing to try codes against, a wrong code costs one of three tries, and the code is good
  for two minutes and one computer. Both sides end up with the same 120-bit key.
- **The link.** Everything afterwards travels over TLS 1.2 with that key (ECDHE-PSK, so a recording
  of the traffic stays unreadable even to someone who later learns the key).
- **Finding each other.** Small UDP broadcasts on the local network: a computer showing a code says
  so, and two paired computers that lost each other (an address changed) call out, signed with
  their key. No address has to be typed.
- **Ports.** TCP 47846 for the link (`flow.port` in the settings file), UDP 47846 for finding each
  other, TCP 47847 only while a code is on show. Open them in the firewall for the other computer.
- **Crossing.** The computer that has the mouse tells it to change channel over HID++, then tells
  the other one where the pointer left and what is on the clipboard. The other computer puts its
  pointer at the matching place of the facing edge.

The mouse is only sent away when the link is up and the other computer has reported its channel, so
it does not end up on a computer that is switched off. It is not sent while a button is held or
when a touchpad or another mouse moved the pointer to the edge. If it ever does end up on the wrong
channel, the Easy-Switch button on its underside brings it back.

How long crossing takes is mostly the mouse changing its radio channel: about half a second to a
computer with a Bolt receiver, one to two seconds to one on Bluetooth. Flow links computers that
run MX Options; it does not talk to Logi Options+ on Windows or macOS.

A keyboard does not follow unless it is switched by other means. For networks where the computers
cannot find each other, the address and the key can be entered by hand at the bottom of the page.

## Rumble from games

Games vibrate game controllers, not mice. With *Haptics → Game rumble* switched on, the app adds a
virtual gamepad to the system (it shows up as an Xbox 360 controller named "MX Options rumble
pad"). The rumble a game sends to it, through SDL, Steam Input or Proton, is played on the mouse's
haptic motor.

- The motor plays short patterns, not a continuous hum, so a rumble becomes a row of pulses: one
  pattern each for weak, medium and strong rumble, a chosen time apart.
- A game then sees a controller. Some show gamepad prompts because of it, and some only vibrate a
  controller that is actually being played with, in which case nothing reaches the mouse. Switch
  the option off if a game misbehaves; the virtual gamepad is gone at once.
- `journalctl --user -u mxoptions -f` shows "a game started vibrating" whenever rumble arrives,
  which tells whether a game sends any.

It needs a mouse with a haptic motor (the MX Master 4).

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
| Files that arrived through Flow | `~/.cache/mxoptions/flow-files/` |
| Tray icon drawings | `~/.local/share/icons/hicolor/scalable/status/mxoptions-tray-*.svg` |

## Known limits

- **Left and right buttons cannot be reassigned.** The mouse does not let software take them over.
- **SmartShift's own switching is inferred.** The mouse announces a mode change made with the
  button, not the one SmartShift makes at speed, so the app works that out from how fast the wheel
  turns. The click can be a notch or two away from the real moment.
- **Text in macros is pasted.** It goes through the clipboard and Ctrl+V, so it does not work in
  terminals.
- **Smooth scrolling passes through the app.** With it on, the wheel only scrolls while the app
  runs.
- **There is no firmware to update to.** The app shows the firmware versions and asks fwupd, the
  system's firmware updater, for updates. fwupd can flash both the mouse and the Bolt receiver, but
  Logitech has published no firmware for either of them for Linux, so the check finds nothing. The
  app does not flash anything itself.
- **Flow with three computers was never run on three.** It was written with two at hand: the
  tests put three copies of the program on one machine and cross between them, but no third
  computer has received the mouse.
- **Game rumble was not tried in a game.** A test program playing rumble on the virtual gamepad
  makes the mouse vibrate on time; which games send rumble to it has not been explored.
- **Run on one desktop.** KDE Plasma on Wayland is where the program is used. What it does on other
  desktops is set out under [Desktops](#desktops), with what was and was not run there. On
  compositors where a line along the edge watches for Flow, a drag that starts on that very line
  of pixels (a scroll bar at the edge of the screen) does not start; a click and scrolling there
  are passed on.
- **Checked on one mouse.** Everything was tried on a single MX Master 4. Synthetic tests cover the
  logic; the feel of gestures, vibrations and sounds was judged by one person.
- **The haptic panel does not answer while a main button is held.** With the left button down the
  mouse reports nothing for the panel, however hard it is pressed; the gesture button is reported
  all the same. So gestures made while a window is being dragged want the gesture button, not the
  panel. This was read off what the mouse sends, with nothing of this program in between.

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
| `mxoptions/rumble.py` | the virtual gamepad that games vibrate |
| `mxoptions/flow.py`, `flow_pairing.py`, `flow_files.py` | Flow: the link between two computers, pairing by code, copied files |
| `mxoptions/flow_service.py`, `clipboard.py` | Flow's ties to the mouse, the desktop and the clipboard |
| `mxoptions/ui/` | the settings window, the ring overlay, sounds |
| `data/` | desktop entry, systemd unit, udev rules |

## Uninstalling

```sh
make uninstall-user          # or: sudo make uninstall, or: sudo pacman -R mxoptions
rm -r ~/.config/mxoptions ~/.local/share/mxoptions ~/.cache/mxoptions    # settings and downloads
rm ~/.local/share/icons/hicolor/scalable/status/mxoptions-tray-*.svg     # the tray icon (after a system-wide install)
```

## Licence

[MIT](LICENSE).
