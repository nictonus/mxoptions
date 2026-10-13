#!/usr/bin/env python3
"""Build the Debian package from a staged installation, with nothing but Python.

    make deb          stages the files and runs this; the package lands in dist/

A .deb is an "ar" archive of three members: the format version, the control files and the data.
Writing it here means the package can be built on any system, not only on one that has dpkg."""

import argparse
import io
import os
import re
import shutil
import sys
import tarfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mxoptions import __version__  # noqa: E402

DEPENDS = [
    "python3 (>= 3.12)", "python3-evdev",
    "python3-pyside6.qtcore", "python3-pyside6.qtgui", "python3-pyside6.qtwidgets", "python3-pyside6.qtdbus",
    "python3-pyside6.qtnetwork", "python3-pyside6.qtsvg", "python3-pyside6.qtqml", "python3-pyside6.qtquick",
    "python3-pyside6.qtmultimedia",
    "qml6-module-qtquick", "qml6-module-qtquick-window",                      # the Actions Ring is drawn with Qt Quick ...
    "qml6-module-org-kde-layershell", "liblayershellqtinterface6",            # ... on a layer above the windows
]
RECOMMENDS = ["python3-numpy", "fwupd",
              "wl-clipboard"]  # Flow reads what was copied through it where the desktop has no clipboard service of its own
CONTROL = """\
Package: mxoptions
Version: {version}
Architecture: all
Maintainer: nictonus <nictonus@users.noreply.github.com>
Installed-Size: {size}
Depends: {depends}
Recommends: {recommends}
Section: utils
Priority: optional
Homepage: https://github.com/nictonus/mxoptions
Description: Settings for Logitech MX Master mice
 Buttons, gestures, the Actions Ring, the wheels, haptics and Easy-Switch of
 Logitech MX mice, with a tray icon, a background service and a D-Bus
 interface for other programs. Flow moves the mouse between two computers.
 .
 Unofficial: not affiliated with or endorsed by Logitech.
"""
POSTINST = """\
#!/bin/sh
set -e
if [ "$1" = configure ]; then
    # the device access rules that came with the package: have them apply without replugging anything
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=hidraw 2>/dev/null || true
    udevadm trigger --sysname-match=uinput 2>/dev/null || true
    if [ -z "$2" ]; then
        echo "MX Options: start it now and at every login, as your own user (not root):"
        echo "  systemctl --user enable --now mxoptions.service"
    else
        echo "MX Options: restart it to run the new version:  systemctl --user restart mxoptions.service"
    fi
fi
"""


def debian_version(version):
    """0.10.0b1 -> 0.10.0~b1-1: a tilde sorts before the release it leads up to."""
    return re.sub(r"(\d)(a|b|rc)(\d+)$", r"\1~\2\3", version) + "-1"


def _tar(members):
    """A tar.xz of ``members``: (name in the archive, path on disk or None, bytes or None, mode). Owned by root."""
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:xz", format=tarfile.GNU_FORMAT) as archive:
        for name, path, content, mode in members:
            info = tarfile.TarInfo("./" + name if name else ".")
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mtime = int(time.time())
            if content is None and path is not None and os.path.islink(path):
                info.type, info.linkname, info.mode = tarfile.SYMTYPE, os.readlink(path), 0o777
                archive.addfile(info)
            elif content is None and (path is None or os.path.isdir(path)):
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                archive.addfile(info)
            else:
                if content is None:
                    with open(path, "rb") as f:
                        content = f.read()
                info.size, info.mode = len(content), mode
                archive.addfile(info, io.BytesIO(content))
    return data.getvalue()


def _ar(path, members):
    """Write an ar archive: a magic line, then for every member a 60-byte header and its bytes, padded to an even length."""
    with open(path, "wb") as f:
        f.write(b"!<arch>\n")
        for name, content in members:
            header = f"{name:<16}{int(time.time()):<12}{0:<6}{0:<6}{'100644':<8}{len(content):<10}`\n"
            f.write(header.encode("ascii") + content + (b"\n" if len(content) % 2 else b""))


def build(stage, out):
    for folder, _folders, _files in os.walk(stage):  # compiled for the Python of this system, not of the one it is installed on
        if os.path.basename(folder) == "__pycache__":
            shutil.rmtree(folder)
    data, size = [("", None, None, 0)], 0
    for folder, folders, files in os.walk(stage):
        folders.sort()
        relative = os.path.relpath(folder, stage)
        if relative != ".":
            data.append((relative, folder, None, 0))
        for name in sorted(files):
            path = os.path.join(folder, name)
            size += os.path.getsize(path) if not os.path.islink(path) else 0
            data.append((os.path.normpath(os.path.join(relative, name)), path, None,
                         0o755 if os.access(path, os.X_OK) else 0o644))
    version = debian_version(__version__)
    control = CONTROL.format(version=version, size=max(1, size // 1024), depends=", ".join(DEPENDS), recommends=", ".join(RECOMMENDS))
    os.makedirs(out, exist_ok=True)
    package = os.path.join(out, f"mxoptions_{version}_all.deb")
    _ar(package, [
        ("debian-binary", b"2.0\n"),
        ("control.tar.xz", _tar([("", None, None, 0), ("control", None, control.encode(), 0o644),
                                 ("postinst", None, POSTINST.encode(), 0o755)])),
        ("data.tar.xz", _tar(data)),
    ])
    return package


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("stage", help="where 'make DESTDIR=... PREFIX=/usr install' put the files")
    parser.add_argument("out", help="directory for the package")
    args = parser.parse_args()
    print(build(args.stage, args.out))


if __name__ == "__main__":
    main()
