"""Firmware of the mouse and its receiver, as the system's firmware updater (fwupd) sees it.

MX Options flashes nothing itself. fwupd does that, with firmware the vendor has published to the
Linux Vendor Firmware Service and signed; this module only asks it what is installed and whether
anything newer is on offer."""

import json
import shutil
import subprocess

TOOL = "fwupdmgr"


def available():
    return shutil.which(TOOL) is not None


def _ask(*args, timeout=90):
    """Run fwupdmgr and return its JSON answer ({} when it has none or cannot be run)."""
    try:
        done = subprocess.run([TOOL, *args, "--json", "--no-unreported-check"], capture_output=True, text=True,
                              timeout=timeout)
        return json.loads(done.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def _entry(device):
    return {"name": device.get("Name", ""), "id": device.get("DeviceId", ""), "version": device.get("Version", ""),
            "bootloader": device.get("VersionBootloader", ""), "updatable": "updatable" in device.get("Flags", []),
            "updates": []}


def find(listing, name):
    """From fwupd's device listing pick the mouse called ``name`` and, if it has one, its receiver.
    Returns a list of entries, the mouse first; empty if fwupd does not know the mouse."""
    devices = listing.get("Devices", [])
    wanted = name.strip().lower()
    mouse = next((d for d in devices if wanted and d.get("Name", "").strip().lower() == wanted), None)
    if mouse is None:
        return []
    found = [_entry(mouse)]
    parent = next((d for d in devices if d.get("DeviceId") == mouse.get("ParentDeviceId")), None)
    if parent is not None:
        found.append(_entry(parent))
    return found


def installed(name):
    """What fwupd reports as installed on the mouse called ``name`` and on its receiver."""
    return find(_ask("get-devices"), name)


def check(name):
    """Like installed(), after refreshing the catalogue, with the newer versions on offer for each
    device under "updates" (newest first): [{"version", "summary"}]."""
    try:  # a catalogue fetched recently is left alone, which fwupdmgr reports as a failure: ignore it
        subprocess.run([TOOL, "refresh"], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        pass
    found = installed(name)
    for entry in found:
        answer = _ask("get-updates", entry["id"])
        for device in answer.get("Devices", []):
            if device.get("DeviceId") == entry["id"]:
                entry["updates"] = [{"version": release.get("Version", ""), "summary": release.get("Summary", "")}
                                    for release in device.get("Releases", [])]
    return found


def update_command(entry):
    """What to run in a terminal to install the update for one device."""
    return f"{TOOL} update {entry['id']}"
