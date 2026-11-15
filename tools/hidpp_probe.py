#!/usr/bin/env python3
"""List what the Logitech HID++ 2.0 devices within reach say about themselves, without changing anything.

For each device: its name, what kind of device it is, the features it has, its reprogrammable controls with
the way each one is reported now, the firmware it runs and, for a keyboard, the settings it keeps itself.

Only questions are asked; nothing on a device is changed. Every request is checked against a short list of
reads (see RULES), by feature, function and the exact parameters that read takes, before it is sent, so a
mistake here cannot turn into a setting. Of each keyboard setting only the bytes its source names as the
setting are shown; the rest of the answer is dropped. Serial numbers, unit IDs, host names and the reports a
keyboard sends while typing are never read or shown.

Quit MX Options and other programs that talk to the devices (Solaar, logiops, Logi Options+) first: they
would see the answers too, and this would see theirs.

    python3 tools/hidpp_probe.py
    python3 tools/hidpp_probe.py --json > devices.json
"""

import argparse
import json
import os
import struct
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mxoptions import environment, hidpp  # noqa: E402
from mxoptions.device import (  # noqa: E402
    BACKLIGHT_2, BACKLIGHT_3, DEVICE_INFO, DEVICE_NAME, DISABLE_KEYS, DUAL_PLATFORM, FEATURE_SET, FN_INVERSION,
    FN_INVERSION_DEFAULT, FN_INVERSION_HOSTS, HOSTS_INFO, INTERNATIONAL_LAYOUTS, LOCK_KEY_STATE, MAX_FIRMWARE_ENTITIES,
    MULTI_PLATFORM, REPROG_CONTROLS_V4)
from mxoptions.device import ROOT as ROOT_FEATURE  # noqa: E402

REQUEST_TIMEOUT = 1.0  # s for one answer; a sleeping device may need a second ask (see ping)
BUDGET = 60.0          # s for the whole run, whatever the devices do
MAX_NODES = 16
MAX_FEATURES = 64      # the feature count is one byte; real devices have a few dozen
MAX_CONTROLS = 128
MAX_NAME = 64
MAX_PLATFORMS = 16     # platform descriptors asked about; the count is one byte


# Checks of the parameters of one request: (params, facts) -> bool. ``facts`` are what this run's own answers
# from the same device said: "versions" {feature id: version} from Root getFeature, "host" from HostsInfo
# function 0 and "platforms" (the descriptor count) from MultiPlatform function 0.
def _empty(params, facts):
    return params == b""


def _two_bytes(params, facts):
    return len(params) == 2


def _ping(params, facts):
    return len(params) == 3 and params[:2] == b"\0\0"


def _index(limit):
    return lambda params, facts: len(params) == 1 and params[0] < limit


def _current_host(params, facts):
    return "host" in facts and params == bytes([facts["host"]])


def _platform(params, facts):
    return len(params) == 1 and params[0] < min(facts.get("platforms", 0), MAX_PLATFORMS)


# The requests that may be sent: (feature ID, function) -> check of its parameters. Each one only reads.
# Function numbers are the HID++ 2.0 function index; Solaar writes them shifted left by four (0x10 is 1).
# Sources: Logitech's HID++ 2.0 specification and feature documents x0000 Root, x0003 DeviceInformation and
# x1b04 SpecialKeysMseButtons; the keyboard feature names in github.com/Logitech/cpg-docs (hidpp20/README.rst),
# which gives no layouts; for the keyboard reads, the way Solaar 1.1.20 (lib/logitech_receiver/
# settings_templates.py and hidpp20.py) and OpenLogi v0.9.0 (crates/openlogi-hidpp/src/feature/) read each
# setting, restated here as facts. Where they differ, the bytes both name are shown, so a run tells which is right.
RULES = {
    (ROOT_FEATURE, 0): _two_bytes,                  # getFeature(feature ID); answer byte 2 is its version
    (ROOT_FEATURE, 1): _ping,                       # ping: 0, 0, a byte echoed back
    (FEATURE_SET, 0): _empty,                       # getCount
    (FEATURE_SET, 1): _index(MAX_FEATURES + 1),     # getFeatureID(index)
    (DEVICE_NAME, 0): _empty,                       # getCount
    (DEVICE_NAME, 1): _index(MAX_NAME),             # getDeviceName(first character)
    (DEVICE_NAME, 2): _empty,                       # getDeviceType
    (REPROG_CONTROLS_V4, 0): _empty,                # getCount
    (REPROG_CONTROLS_V4, 1): _index(MAX_CONTROLS),  # getCidInfo(index)
    (REPROG_CONTROLS_V4, 2): _two_bytes,            # getCidReporting(control ID)
    (DEVICE_INFO, 1): _index(MAX_FIRMWARE_ENTITIES),  # getFwInfo(entity); never function 0, which has the unit ID
    (HOSTS_INFO, 0): _empty,                        # feature information; byte 3 is the current host
    (FN_INVERSION, 0): _empty,                      # Fn inversion: byte 0, 1 when swapped
    (FN_INVERSION_DEFAULT, 0): _empty,              # the same, for the feature with a default state
    (FN_INVERSION_HOSTS, 0): _current_host,         # the same per host: asked for the current host; byte 0 the
                                                    # host, 1 the value, 2 the default, 3 capability flags
    (BACKLIGHT_2, 0): _empty,                       # backlight configuration, bytes 0-13
    (BACKLIGHT_2, 2): _empty,                       # backlight information: level count, level, status, effect,
                                                    # default durations; answered from feature version 2 on too
    (BACKLIGHT_3, 1): _empty,                       # backlight time, bytes 0-1
    (DISABLE_KEYS, 0): _empty,                      # which keys can be disabled, byte 0
    (DISABLE_KEYS, 1): _empty,                      # which keys are disabled, byte 0
    (DUAL_PLATFORM, 0): _empty,                     # platform, byte 0: 0 iOS and macOS, 1 Android and Windows
    (MULTI_PLATFORM, 0): _empty,                    # bytes 0-1 flags, 2 platform count, 3 descriptor count,
                                                    # 4 host count, 5 current host, 6 the platform
    (MULTI_PLATFORM, 1): _platform,                 # platform descriptor(index), bytes 0-7
    (LOCK_KEY_STATE, 0): _empty,                    # which lock keys are on, byte 0
    (INTERNATIONAL_LAYOUTS, 0): _empty,             # the layout of the keys themselves (a HID country code), byte 0
}

# Of the answers to the keyboard reads, the bytes shown: (feature ID, function) -> positions. Backlight
# configuration bytes are shown as they are, so that a run settles what the sources leave open.
SHOWN = {
    (FN_INVERSION, 0): (0,), (FN_INVERSION_DEFAULT, 0): (0, 1), (FN_INVERSION_HOSTS, 0): (0, 1, 2, 3),
    (BACKLIGHT_2, 0): tuple(range(14)), (BACKLIGHT_2, 2): tuple(range(12)), (BACKLIGHT_3, 1): (0, 1),
    (DISABLE_KEYS, 0): (0,), (DISABLE_KEYS, 1): (0,), (DUAL_PLATFORM, 0): (0,),
    (MULTI_PLATFORM, 0): tuple(range(7)), (MULTI_PLATFORM, 1): tuple(range(8)),
    (LOCK_KEY_STATE, 0): (0,), (INTERNATIONAL_LAYOUTS, 0): (0,),
}
FIRMWARE_TYPES = {0: "application", 1: "bootloader", 2: "hardware"}  # getFwInfo byte 0

# What DEVICE_NAME getDeviceType answers
KINDS = {0: "keyboard", 1: "remote control", 2: "numpad", 3: "mouse", 4: "touchpad", 5: "trackball",
         6: "presenter", 7: "receiver"}
# getCidInfo capability bits (byte 4, and bit 0 of byte 8 as 0x100) and getCidReporting flags (byte 2)
CAPABILITIES = {0x01: "mouse", 0x02: "fkey", 0x04: "hotkey", 0x08: "fntoggle", 0x10: "reprog", 0x20: "divert",
                0x40: "persist", 0x80: "virtual", 0x100: "rawxy"}
REPORTING = {0x01: "diverted", 0x04: "persistently diverted", 0x10: "raw xy diverted"}


class Refused(hidpp.HidppError):
    """A request that is not on the read-only list; it was not sent."""


class OutOfTime(hidpp.HidppError):
    """The time for the whole run is up."""


class ReadOnly:
    """``transport`` as the probe sees it: a request goes out only if the feature at that index, as the
    device itself said, has that function in RULES and its parameters pass that rule. Indexes are learned
    from the answers to Root getFeature and FeatureSet getFeatureID; index 0 is always Root. Each answer is
    waited for at most ``timeout`` s, and none at all once ``deadline`` (time.monotonic) has passed."""

    def __init__(self, transport, timeout=REQUEST_TIMEOUT, deadline=None):
        self.transport = transport
        self.timeout = timeout
        self.deadline = time.monotonic() + BUDGET if deadline is None else deadline
        self._features = {}  # devidx -> {index: feature id}
        self._facts = {}     # devidx -> facts, see RULES

    def slots(self):
        return self.transport.slots()

    def request(self, devidx, feat_idx, function, params=b"", timeout=None):
        params = bytes(params)
        if devidx not in self.transport.slots():
            raise Refused(f"device {devidx} is not on this node")
        known = self._features.setdefault(devidx, {0: ROOT_FEATURE})
        facts = self._facts.setdefault(devidx, {"versions": {}})
        feature = known.get(feat_idx)
        check = RULES.get((feature, function))
        if check is None or not check(params, facts):
            raise Refused(f"function {function} of feature index {feat_idx:#04x} with {len(params)} parameter "
                          "bytes is not a known read")
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise OutOfTime("time is up")
        timeout = min(self.timeout if timeout is None else timeout, self.timeout, left)
        reply = self.transport.request(devidx, feat_idx, function, params, timeout=timeout)
        if feature == ROOT_FEATURE and function == 0 and reply[0]:
            wanted = struct.unpack("!H", params)[0]
            self._learn(known, reply[0], wanted)
            if len(reply) > 2:
                facts["versions"][wanted] = reply[2]
        elif feature == FEATURE_SET and function == 1:
            self._learn(known, params[0], struct.unpack("!H", reply[:2])[0])
        elif feature == HOSTS_INFO and function == 0 and len(reply) > 3:
            facts["host"] = reply[3]
        elif feature == MULTI_PLATFORM and function == 0 and len(reply) > 3:
            facts["platforms"] = reply[3]  # byte 3 in two of the three sources; byte 2 is the platform count
        return reply

    @staticmethod
    def _learn(known, index, feature):
        if index == 0 or known.get(index, feature) != feature:
            raise hidpp.HidppError(f"the device names two features at index {index:#04x}")
        known[index] = feature

    ping = hidpp.Transport.ping  # it only asks through self.request


def _flag_names(value, names):
    return [name for bit, name in names.items() if value & bit]


def _feature_index(port, devidx, feature):
    return port.request(devidx, 0x00, 0, struct.pack("!H", feature))[0]


def _device_error(error):
    if isinstance(error, hidpp.HidppClosed):
        return "the device went away"
    if isinstance(error, (IndexError, struct.error)):
        return "invalid reply"
    return str(error)


def _firmware_entity(index, info):
    """One getFwInfo answer, decoded: type, prefix (ASCII), version (packed BCD number, revision and build) and
    whether it is the one running. What does not decode is None; bytes 9 on are never looked at."""
    digits = info[4:8].hex()
    return {"index": index, "type": info[0] if info[0] in FIRMWARE_TYPES else None,
            "prefix": info[1:4].decode("ascii") if all(0x20 <= c <= 0x7E for c in info[1:4]) else None,
            "version": f"{int(digits[0:2])}.{int(digits[2:4])}.{int(digits[4:8])}" if digits.isdigit() else None,
            "active": bool(info[8] & 0x01)}


def _keyboard_reads(port, devidx, indexes):
    """(firmware, settings) of the device at ``devidx``, from the features in ``indexes`` (feature ID -> index).

    firmware: the entities getFwInfo tells of, from index 0 up to MAX_FIRMWARE_ENTITIES, ending at the first
    that does not answer. The count is in getDeviceInfo, never asked: its answer holds the unit ID.
    settings: {"0x40a0": {"0": {"bytes": hex} | {"error": text}}, ...}, the bytes in SHOWN only. A read that
    fails is kept as its error and the next is asked; a read left unanswered also leaves the rest of its
    feature. HidppClosed, OutOfTime and Refused are passed on."""
    firmware = []
    if indexes.get(DEVICE_INFO):
        for entity in range(MAX_FIRMWARE_ENTITIES):
            try:
                info = bytes(port.request(devidx, indexes[DEVICE_INFO], 1, bytes([entity])))
            except (hidpp.HidppFeatureError, hidpp.HidppTimeout):
                break  # the document names no error: any ends the list
            if len(info) < 9:
                break
            firmware.append(_firmware_entity(entity, info))

    settings = {}

    def note(feature, key, answer):
        settings.setdefault(f"0x{feature:04x}", {})[key] = answer

    def ask(feature, function, params=b"", key=None):
        key = str(function) if key is None else key
        try:
            reply = port.request(devidx, indexes[feature], function, params)
            note(feature, key, {"bytes": bytes(reply[i] for i in SHOWN[feature, function]).hex()})
            return reply
        except hidpp.HidppTimeout:
            note(feature, key, {"error": "no answer"})
            raise
        except (hidpp.HidppFeatureError, IndexError, struct.error) as error:
            note(feature, key, {"error": _device_error(error)})
        return None

    def needed(feat_idx, function, params=b""):  # a read only to know how to ask another; None if no answer
        try:
            return port.request(devidx, feat_idx, function, params)
        except (hidpp.HidppFeatureError, hidpp.HidppTimeout, IndexError):
            return None

    def fn_inversion_hosts():
        host = None
        if indexes.get(HOSTS_INFO):
            reply = needed(indexes[HOSTS_INFO], 0)
            host = reply[3] if reply is not None and len(reply) > 3 else None
        if host is None:
            note(FN_INVERSION_HOSTS, "0", {"error": "not read"})
        else:
            ask(FN_INVERSION_HOSTS, 0, bytes([host]))

    def backlight_2():
        reply = needed(0x00, 0, struct.pack("!H", BACKLIGHT_2))  # which controls it has depends on its version
        ask(BACKLIGHT_2, 0)
        ask(BACKLIGHT_2, 2)
        note(BACKLIGHT_2, "version", {"bytes": f"{reply[2]:02x}"} if reply is not None and len(reply) > 2
             else {"error": "not read"})

    def multi_platform():
        reply = ask(MULTI_PLATFORM, 0)
        for index in range(min(reply[3], MAX_PLATFORMS) if reply is not None else 0):
            ask(MULTI_PLATFORM, 1, bytes([index]), key=f"1:{index}")

    for feature, read in ((FN_INVERSION, lambda: ask(FN_INVERSION, 0)),
                          (FN_INVERSION_DEFAULT, lambda: ask(FN_INVERSION_DEFAULT, 0)),
                          (FN_INVERSION_HOSTS, fn_inversion_hosts),
                          (BACKLIGHT_2, backlight_2),
                          (BACKLIGHT_3, lambda: ask(BACKLIGHT_3, 1)),
                          (DISABLE_KEYS, lambda: (ask(DISABLE_KEYS, 0), ask(DISABLE_KEYS, 1))),
                          (DUAL_PLATFORM, lambda: ask(DUAL_PLATFORM, 0)),
                          (MULTI_PLATFORM, multi_platform),
                          (LOCK_KEY_STATE, lambda: ask(LOCK_KEY_STATE, 0)),
                          (INTERNATIONAL_LAYOUTS, lambda: ask(INTERNATIONAL_LAYOUTS, 0))):
        if indexes.get(feature):
            try:
                read()
            except hidpp.HidppTimeout:
                pass  # kept as "no answer"; the next feature is still asked
    return firmware, settings


def probe_device(port, devidx):
    """What the device at ``devidx`` says about itself, or None if nothing answers there."""
    try:
        version = port.ping(devidx, timeout=port.timeout, retries=1)
    except (OutOfTime, Refused):
        raise
    except (hidpp.HidppError, IndexError, struct.error) as error:
        return {"slot": devidx, "protocol": None, "error": _device_error(error)}
    if version is None:
        return None
    found = {"slot": devidx, "protocol": f"{version[0]}.{version[1]}"}
    if version[0] < 2:
        return found
    found.update(name=None, kind=None, features=[], controls=[], firmware=[], settings={}, truncated=[],
                 error=None)
    try:
        feature_set = _feature_index(port, devidx, FEATURE_SET)
        indexes = {ROOT_FEATURE: 0}
        if feature_set:
            count = port.request(devidx, feature_set, 0)[0]
            if count > MAX_FEATURES:
                found["truncated"].append("features")
            for index in range(1, min(count, MAX_FEATURES) + 1):
                feature = struct.unpack("!H", port.request(devidx, feature_set, 1, bytes([index]))[:2])[0]
                indexes.setdefault(feature, index)
                found["features"].append({"index": index, "id": f"0x{feature:04x}"})

        name_index = indexes.get(DEVICE_NAME)
        if name_index:
            length, name = port.request(devidx, name_index, 0)[0], b""
            if length > MAX_NAME:
                found["truncated"].append("name")
            while len(name) < min(length, MAX_NAME):
                piece = port.request(devidx, name_index, 1, bytes([len(name)]))[: min(length, MAX_NAME) - len(name)]
                if not piece:
                    raise hidpp.HidppError("the name stopped coming")
                name += piece
            found["name"] = name.decode("utf-8", "replace").strip("\0 ")
            found["kind"] = port.request(devidx, name_index, 2)[0]

        reprog = indexes.get(REPROG_CONTROLS_V4)
        if reprog:
            count = port.request(devidx, reprog, 0)[0]
            if count > MAX_CONTROLS:
                found["truncated"].append("controls")
            for i in range(min(count, MAX_CONTROLS)):
                info = port.request(devidx, reprog, 1, bytes([i]))
                cid, tid = struct.unpack("!HH", info[:4])
                flags = info[4] | (info[8] & 0x01) << 8
                control = {"cid": f"0x{cid:04x}", "tid": f"0x{tid:04x}", "flags": f"0x{flags:04x}",
                           "reporting": None, "remap": None}
                try:
                    reply = port.request(devidx, reprog, 2, struct.pack("!H", cid))
                    if struct.unpack("!H", reply[:2])[0] == cid:
                        control["reporting"] = f"0x{reply[2]:02x}"
                        control["remap"] = f"0x{struct.unpack('!H', reply[3:5])[0]:04x}"
                except hidpp.HidppFeatureError:
                    pass  # some controls have no reporting to tell
                found["controls"].append(control)

        found["firmware"], found["settings"] = _keyboard_reads(port, devidx, indexes)
    except (OutOfTime, Refused):
        raise
    except (hidpp.HidppError, IndexError, struct.error) as e:
        found["error"] = _device_error(e)
    return found


def probe(port):
    """Every device ``port`` (a ReadOnly) reaches, in slot order."""
    return [device for device in (probe_device(port, devidx) for devidx in port.slots()) if device]


def run(nodes=None, open_transport=hidpp.Transport):
    """Probe every HID++ node: [{"node": n, "receiver": bool, "devices": [...], "error": str | None}]."""
    nodes = hidpp.find_hidpp_nodes() if nodes is None else nodes
    deadline = time.monotonic() + BUDGET
    results = []
    for number, (path, is_receiver) in enumerate(nodes[:MAX_NODES], 1):
        result = {"node": number, "receiver": is_receiver, "devices": [], "error": None}
        results.append(result)
        try:
            transport = open_transport(path, is_receiver)
        except OSError as e:
            result["error"] = f"cannot be opened: {e.strerror or e}"
            continue
        try:
            result["devices"] = probe(ReadOnly(transport, deadline=deadline))
        except OutOfTime:
            result["error"] = "time ran out"
            break
        finally:
            transport.close()
    return results


def describe(results):
    """``results`` as lines of text."""
    lines = []
    for node in results:
        lines.append(f"Node {node['node']} ({'receiver' if node['receiver'] else 'direct'})"
                     + (f": {node['error']}" if node["error"] else ""))
        if not node["devices"] and not node["error"]:
            lines.append("  nothing answers")
        for dev in node["devices"]:
            if "features" not in dev:
                if dev.get("error"):
                    lines.append(f"  slot {dev['slot']}: stopped: {dev['error']}")
                else:
                    lines.append(f"  slot {dev['slot']}: HID++ {dev['protocol']}, not looked at")
                continue
            kind = KINDS.get(dev["kind"], f"kind {dev['kind']}") if dev["kind"] is not None else "kind unknown"
            lines.append(f"  slot {dev['slot']}: {dev['name'] or 'no name'} ({kind}), HID++ {dev['protocol']}")
            lines.append("    features: " + (" ".join(f"{f['index']}={f['id']}" for f in dev["features"]) or "none"))
            for c in dev["controls"]:
                caps = ", ".join(_flag_names(int(c["flags"], 16), CAPABILITIES)) or "none"
                now = "unknown"
                if c["reporting"] is not None:
                    now = ", ".join(_flag_names(int(c["reporting"], 16), REPORTING)) or "native"
                remap = f", remapped to {c['remap']}" if c["remap"] not in (None, "0x0000") else ""
                lines.append(f"    control {c['cid']} task {c['tid']}: {caps}; now {now}{remap}")
            for f in dev["firmware"]:
                kind = FIRMWARE_TYPES.get(f["type"], "unknown type")
                lines.append(f"    firmware {f['index']}: {kind} {f['prefix'] or '?'} {f['version'] or '?'}"
                             + (", running" if f["active"] else ""))
            for feature, answers in dev["settings"].items():
                lines.append(f"    setting {feature}: " + ", ".join(
                    f"function {key} {answer.get('bytes') or answer.get('error')}" for key, answer in answers.items()))
            if dev["truncated"]:
                lines.append("    cut short: " + ", ".join(dev["truncated"]))
            if dev["error"]:
                lines.append(f"    stopped: {dev['error']}")
    return lines or ["No HID++ device found."]


def _app_running():
    from mxoptions.app import _notify_running_instance
    return _notify_running_instance(False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--json", action="store_true", help="print the findings as JSON")
    args = parser.parse_args(argv)
    rivals = environment.rivals()
    if _app_running():
        rivals.insert(0, "MX Options")
    if rivals:
        print(f"{', '.join(rivals)} is running; quit it before probing devices.", file=sys.stderr)
        return 2
    try:
        results = run()
    except KeyboardInterrupt:
        return 130
    print(json.dumps(results, indent=2) if args.json else "\n".join(describe(results)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
