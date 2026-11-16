"""Self-check of ops/ship/hwgate.py and the real hardware-gates.json, in a throwaway git repo.

    python3 ops/ship/test_hwgate.py

CONTRACT is the hardware release contract the manifest must keep: per capability, a line that turns it on in
a real build and the checks a stable release then needs. Dropping a capability or one of its checks from the
manifest, or a probe that no longer sees its capability, fails here.
"""
import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hwgate  # noqa: E402

with open(os.path.join(hwgate.HERE, "hardware-gates.json")) as f:
    MANIFEST = json.load(f)

KEYBOARD = {"shared-receiver", "mixed-connections", "service-stop-restores-keys", "crash-recovery", "easy-switch-away",
            "sleep-wake", "receiver-replug", "keyboard-battery", "shared-receiver-mouse-latency", "keyboard-identity"}
CONTRACT = {  # group: (file, line copied from a build that has the capability, required checks, recorded checks)
    "keyboard": ("mxoptions/keyboard.py", "class KeyboardSide:", KEYBOARD,
                 {"top-row-fn-modes", "bluetooth-zero-id-access"}),
    "mouse-follows-keyboard": ("mxoptions/config.py", '        "mouse_follows_keyboard": False,  # takes the mouse along',
                               {"mouse-follows-keyboard"}, set()),
    "keyboard-follows-mouse": ("mxoptions/config.py", '        "keyboard_follows_mouse": False,  # takes the keyboard',
                               {"keyboard-follow-held-key-check", "keyboard-follow-held-key-at-switch",
                                "keyboard-follow-two-computers"}, set()),
    "keyboard-app-profiles": ("mxoptions/config.py", "def _keyboard_profiles(profiles):", {"app-profiles-desktops"}, set()),
    "fn-lock": ("mxoptions/kbsettings.py",
                "    features = (device.FN_INVERSION_HOSTS, device.FN_INVERSION_DEFAULT, device.FN_INVERSION)",
                {"fn-lock-setting"}, set()),
    "disable-keys": ("mxoptions/kbsettings.py", "    feature = device.DISABLE_KEYS", {"disable-keys-setting"}, set()),
    "os-layout": ("mxoptions/kbsettings.py", "    feature = device.MULTI_PLATFORM", {"os-layout-setting"}, set()),
    "backlight": ("mxoptions/kbsettings.py", "    feature = device.BACKLIGHT_2", {"backlight-settings"}, set()),
    "backlight-effects": ("mxoptions/ui/keyboard_page.py", '    EFFECTS = ("Static", "Breathing", "Reaction")',
                          {"backlight-effect-ids-observed"}, set()),
    "lock-key-notices": ("mxoptions/keyboard.py", "        if dev.has(device.LOCK_KEY_STATE):", {"lock-key-notices"}, set()),
    "mouse-battery-saving": ("mxoptions/config.py",
                             '    "haptics": {"enabled": True, "level": 60, "force": None, "feedback": True, "battery_saving": True,',
                             {"mouse-haptic-battery-saving", "mouse-low-battery-notice"}, set()),
    "screen-switch-haptics": ("mxoptions/config.py", '                "screens": False},', {"screen-switch-haptic"}, set()),
    "mouse-app-profiles": ("mxoptions/config.py",
                           '    "mouse": {"profiles": [], "thumbwheel": [], "thumbwheel_keys": [], "gestures": []},',
                           {"mouse-app-profiles-desktops", "app-suggestions-linux"}, set()),
    "pan-zoom": ("mxoptions/gestures.py", 'HOLD_MODES = {"pan": "Pan (hold and move)", "zoom": "Zoom (hold and move)"}',
                 {"pan-zoom"}, set()),
    "spotlight": ("mxoptions/config.py", 'EFFECTS = ("highlight", "magnify", "laser")', {"spotlight-effects-desktops"}, set()),
    "thumbwheel-keystrokes": ("mxoptions/config.py",
                              'THUMB_FUNCTIONS = ("hscroll", "zoom", "volume", "tabs", "desktops", "brightness", "keys")',
                              {"thumbwheel-keystrokes"}, set()),
    "macro-stop-on-press": ("mxoptions/config.py", '_MACRO_FIELDS = frozenset({"type", "name", "repeat", "steps", "stop_on_press"})',
                            {"macro-stop-on-press"}, set()),
    "macro-files": ("mxoptions/config.py", 'MACRO_FORMAT = "mxoptions-macro"', {"macro-file-dialogs"}, set()),
    "flow-peer-pause": ("mxoptions/config.py", '        if type(entry.get("paused")) is bool:', {"flow-peer-pause"}, set()),
    "flow-hold-ctrl": ("mxoptions/config.py", '        "hold_ctrl": False,   # cross at the edge only while a Ctrl key is held; '
                       'where that cannot be told, never', {"flow-hold-ctrl"}, set()),
    "keyboard-ring-key": ("mxoptions/config.py", 'KEYBOARD_ACTIONS = ("none", "keys", "button", "shortcut", "desktop", "command", '
                          '"script", "macro", "host", "hook", "ring")', {"keyboard-ring-key"}, set()),
    "fn-lock-notices": ("mxoptions/config.py", '    cfg["keyboard"]["fn_notices"] = cfg["keyboard"].get("fn_notices") is True',
                        {"fn-lock-notices"}, set()),
    "together-actions": ("mxoptions/ui/editors.py", "class EasySwitchDialog(QDialog):", {"together-actions"}, set()),
    "home-screen": ("mxoptions/ui/home_page.py", "class HomePage(QWidget):", {"home-screen"}, set()),
}
# Per capability of the Linux feature stack: the files whose code decides or carries out what its checks see. A change
# to any one must move the digest and so void that capability's results: "paths" names each, "ungated" none.
BEHAVIOUR = {
    "home-screen": ("mxoptions/ui/home_page.py", "mxoptions/ui/main_window.py", "mxoptions/engine.py", "mxoptions/keyboard.py"),
    "together-actions": ("mxoptions/actions.py", "mxoptions/config.py", "mxoptions/engine.py", "mxoptions/keyboard.py",
                         "mxoptions/api.py", "mxoptions/app.py", "mxoptions/flow_service.py", "mxoptions/ui/editors.py",
                         "mxoptions/ui/action_picker.py"),
    "mouse-battery-saving": ("mxoptions/config.py", "mxoptions/device.py", "mxoptions/engine.py", "mxoptions/api.py",
                             "mxoptions/app.py", "mxoptions/keyboard.py", "mxoptions/ui/pages.py"),
    "screen-switch-haptics": ("mxoptions/config.py", "mxoptions/engine.py", "mxoptions/flow_edges.py", "mxoptions/x11.py",
                              "mxoptions/ui/pages.py"),
    "mouse-app-profiles": ("mxoptions/config.py", "mxoptions/front.py", "mxoptions/engine.py", "mxoptions/app.py",
                           "mxoptions/ui/store.py", "mxoptions/ui/pages.py", "mxoptions/ui/ring_page.py",
                           "mxoptions/ui/gestures_page.py"),
    "pan-zoom": ("mxoptions/gestures.py", "mxoptions/engine.py", "mxoptions/actions.py", "mxoptions/vinput.py",
                 "mxoptions/vinput_uinput.py", "mxoptions/ui/action_picker.py", "mxoptions/ui/gestures_page.py"),
    "spotlight": ("mxoptions/config.py", "mxoptions/actions.py", "mxoptions/engine.py", "mxoptions/environment.py",
                  "mxoptions/x11.py", "mxoptions/ui/ring.py", "mxoptions/ui/ring_helper.py", "mxoptions/ui/RingSurface.qml",
                  "mxoptions/ui/ring.qml", "mxoptions/ui/ring_window.qml"),
    "thumbwheel-keystrokes": ("mxoptions/config.py", "mxoptions/engine.py", "mxoptions/vinput.py", "mxoptions/vinput_uinput.py",
                              "mxoptions/ui/pages.py", "mxoptions/ui/store.py"),
    "macro-stop-on-press": ("mxoptions/config.py", "mxoptions/actions.py", "mxoptions/engine.py", "mxoptions/keyboard.py",
                            "mxoptions/ui/editors.py"),
    "macro-files": ("mxoptions/config.py", "mxoptions/ui/editors.py"),
    "flow-peer-pause": ("mxoptions/config.py", "mxoptions/flow.py", "mxoptions/flow_files.py", "mxoptions/flow_service.py",
                        "mxoptions/flow_edges.py", "mxoptions/ui/flow_page.py"),
    "flow-hold-ctrl": ("mxoptions/config.py", "mxoptions/engine.py", "mxoptions/environment.py", "mxoptions/x11.py",
                       "mxoptions/flow_edges.py", "mxoptions/ui/flow_edge.qml", "mxoptions/ui/flow_page.py"),
    "keyboard-ring-key": ("mxoptions/config.py", "mxoptions/keyboard.py", "mxoptions/engine.py", "mxoptions/app.py",
                          "mxoptions/actions.py", "mxoptions/ui/ring.py", "mxoptions/ui/ring_helper.py",
                          "mxoptions/ui/RingSurface.qml"),
    "fn-lock-notices": ("mxoptions/config.py", "mxoptions/keyboard.py", "mxoptions/app.py", "mxoptions/ui/keyboard_page.py"),
}
# Version, translated text and docs: a change keeps every result.
TEXT = ("mxoptions/__init__.py", "mxoptions/i18n_uk.py", "README.md")
# Lines of current builds without device controls, which must not turn any group on.
BASE = {
    "mxoptions/__init__.py": '__version__ = "0.10.0"\n', "mxoptions/hidpp.py": "h\n", "mxoptions/gestures.py": "g\n",
    "mxoptions/device.py": 'FN_INVERSION = 0x40A0\nDISABLE_KEYS = 0x4521\nMULTI_PLATFORM = 0x4531\n'
                           'LOCK_KEY_STATE = 0x4220\nBACKLIGHT_2 = 0x1982\n'
                           '    ("backlight", (BACKLIGHT_1, BACKLIGHT_2, BACKLIGHT_3, ILLUMINATION)),\n    0x09: "Wave",\n',
    "mxoptions/i18n_uk.py": '    "Takes effect after restarting the app.": "x",\n    "Wave": "x",\n',
    "mxoptions/config.py": "DEFAULTS = {}\n", "README.md": "r\n",
}

REPO = tempfile.mkdtemp(prefix="mxh.")
os.chdir(REPO)
G = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def commit(at=None, **files):
    if at:
        subprocess.run(["git", "checkout", "-q", "--detach", at], check=True)
    for path, text in files.items():
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a" if path in BASE and at else "w") as f:
            f.write(text)
    subprocess.run([*G, "add", "-A"], check=True)
    subprocess.run([*G, "commit", "-qm", "c", "--allow-empty"], check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def entries(digest, gates, result="pass"):
    return [{"group": g, "gate": x, "digest": digest, "commit": "c", "result": result, "date": "d", "note": "n"}
            for g, x in gates]


def gates_of(manifest, *ids, kinds=("require", "record")):
    return [(g["id"], x) for g in manifest["groups"] if g["id"] in ids for k in kinds for x in g.get(k, [])]


def errors(manifest, base):
    """What the manifest lost of CONTRACT. Empty when it keeps all of it."""
    out = []
    groups = {g["id"]: g for g in manifest["groups"]}
    for gid, (path, line, require, record) in CONTRACT.items():
        g = groups.get(gid)
        if g is None:
            out.append(f"{gid}: group missing")
            continue
        out += [f"{gid}: {x} not required" for x in sorted(require - set(g.get("require", [])))]
        out += [f"{gid}: {x} not kept" for x in sorted(record - set(g.get("require", [])) - set(g.get("record", [])))]
        r = hwgate.check(commit(base, **{path: line + "\n"}), manifest, [])
        if not r["groups"][gid]["present"]:
            out.append(f"{gid}: probe does not see the capability")
    for g in manifest["groups"]:
        out += [f"{g['id']}: {x} has no check text" for k in ("require", "record") for x in g.get(k, [])
                if not manifest.get("checks", {}).get(x)]
    return out


def edit(at, path):
    """A commit on ``at`` with one more line in ``path``, a file ``at`` has."""
    subprocess.run(["git", "checkout", "-q", "--detach", at], check=True)
    with open(path, "a") as f:
        f.write("# changed\n")
    subprocess.run([*G, "commit", "-qam", "c"], check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def stale(manifest, base):
    """Where results would outlive a change: a BEHAVIOUR file whose change keeps the digest or the results, a TEXT file
    whose change voids them. Empty when none. Every BEHAVIOUR capability is on, with a pass for each of its checks."""
    out, lines = [], {}
    for gid in BEHAVIOUR:
        path, line = CONTRACT[gid][:2]
        lines[path] = lines.get(path, "") + line + "\n"
    for path in {p for files in BEHAVIOUR.values() for p in files} - set(lines) - set(BASE):
        lines[path] = "x = 1\n"
    stack = commit(base, **lines)
    d = hwgate.check(stack, manifest, [])["digest"]
    full = entries(d, gates_of(manifest, *BEHAVIOUR))
    r = hwgate.check(stack, manifest, full)
    if [g for g, v in r["groups"].items() if v["present"]] != list(BEHAVIOUR) or not r["stable"]:
        out.append(f"stack: not stable with every result: {r}")
    for path in TEXT:
        r = hwgate.check(edit(stack, path), manifest, full)
        if r["digest"] != d or not r["stable"]:
            out.append(f"{path}: change voids results")
    for path in sorted({p for files in BEHAVIOUR.values() for p in files}):
        if hwgate.matches(path, manifest["ungated"]):
            out.append(f"{path}: also ungated")
        r = hwgate.check(edit(stack, path), manifest, full)
        for gid, files in BEHAVIOUR.items():
            g = r["groups"].get(gid)  # a group the manifest lost: "stack" above says so
            if path in files and g and (r["digest"] == d or r["stable"] or not g["missing"]):
                out.append(f"{gid}: {path} change keeps old results")
    return out


subprocess.run(["git", "init", "-q"], check=True)
base = commit(**BASE)
# First, so a behaviour file missing from "paths" fails here, by what it does to results, before anything else.
s = stale(MANIFEST, base)
assert s == [], s
r = hwgate.check(base, MANIFEST, [])
assert r["stable"] and not r["unclassified"] and not any(g["present"] for g in r["groups"].values()), r

# The real manifest keeps the whole contract; each group's probe sees its capability alone.
assert errors(MANIFEST, base) == [], errors(MANIFEST, base)
for gid, (path, line, require, record) in CONTRACT.items():
    r = hwgate.check(commit(base, **{path: line + "\n"}), MANIFEST, [])
    assert [g for g, v in r["groups"].items() if v["present"]] == [gid] and not r["unclassified"], (gid, r)
    assert {m.split(":")[0] for m in r["groups"][gid]["missing"]} >= require | record, (gid, r)

# Losing any part of the contract is caught.
for cut in ("mouse-follows-keyboard", "keyboard-app-profiles", "backlight-effects"):
    m = copy.deepcopy(MANIFEST)
    m["groups"] = [g for g in m["groups"] if g["id"] != cut]
    assert f"{cut}: group missing" in errors(m, base), cut
m = copy.deepcopy(MANIFEST)
for g in m["groups"]:
    if g["id"] == "backlight-effects":  # a general getter dump in place of the observed effect ids
        g["require"] = ["backlight-getter-dump"]
assert "backlight-effects: backlight-effect-ids-observed not required" in errors(m, base)
m = copy.deepcopy(MANIFEST)
for g in m["groups"]:
    if g["id"] == "keyboard-app-profiles":
        g["require"] = []
        g["record"] = ["app-profiles-desktops"]  # recorded is not required
assert "keyboard-app-profiles: app-profiles-desktops not required" in errors(m, base)
m = copy.deepcopy(MANIFEST)
for g in m["groups"]:
    if g["id"] == "fn-lock":  # a probe on the file alone, or on a name the code does not use
        g["present"] = ["mxoptions/kbsettings.py:^class FnLockCodec\\b"]
assert "fn-lock: probe does not see the capability" in errors(m, base)
# A behaviour file out of "paths", ungated or named nowhere, is caught by the results it would keep; the version in
# "paths" by the results it would void.
for path, gid in (("mxoptions/flow_edges.py", "flow-hold-ctrl"), ("mxoptions/ui/RingSurface.qml", "spotlight")):
    for named in ([path], []):
        m = copy.deepcopy(MANIFEST)
        m["paths"] = [g for g in m["paths"] if not hwgate.matches(path, [g])]
        m["ungated"] += named
        assert f"{gid}: {path} change keeps old results" in stale(m, base), (path, named)
m = copy.deepcopy(MANIFEST)
m["paths"].append("mxoptions/__init__.py")
assert "mxoptions/__init__.py: change voids results" in stale(m, base)

# A build like the reviewed keyboard stack: keyboard, both follow settings and profiles present.
on = ("keyboard", "mouse-follows-keyboard", "keyboard-follows-mouse", "keyboard-app-profiles")
lines = {}
for gid in on:
    path, line = CONTRACT[gid][:2]
    lines[path] = lines.get(path, "") + line + "\n"
stack = commit(base, **lines)
r = hwgate.check(stack, MANIFEST, [])
assert [g for g, v in r["groups"].items() if v["present"]] == list(on), r
d = r["digest"]
everything = [(g["id"], x) for g in MANIFEST["groups"] for k in ("require", "record") for x in g.get(k, [])]

# Absent-only records never make a present capability stable.
absent = entries(d, everything, "absent")
r = hwgate.check(stack, MANIFEST, absent)
assert not r["stable"] and "mouse-follows-keyboard: no result for this build" in r["groups"]["mouse-follows-keyboard"]["missing"], r
assert "app-profiles-desktops: no result for this build" in r["groups"]["keyboard-app-profiles"]["missing"], r
full = entries(d, gates_of(MANIFEST, *on))
assert hwgate.check(stack, MANIFEST, full)["stable"]
for gid in on:  # each present capability alone shown absent: refused
    part = [e for e in full if e["group"] != gid] + entries(d, gates_of(MANIFEST, gid), "absent")
    assert not hwgate.check(stack, MANIFEST, part)["stable"], gid
assert not hwgate.check(stack, MANIFEST, [dict(e, note="") for e in full])["stable"]  # an entry without its source counts for nothing
pending = [dict(e, result="pending") if e["gate"] == "top-row-fn-modes" else e for e in full]
assert hwgate.check(stack, MANIFEST, pending)["stable"]  # a record gate may be pending with a reason
pending = [dict(e, result="pending") if e["gate"] == "sleep-wake" else e for e in full]
assert hwgate.check(stack, MANIFEST, pending)["groups"]["keyboard"]["missing"] == ["sleep-wake: pending"]
failed = full + entries(d, [("keyboard", "sleep-wake")], "fail")
assert hwgate.check(stack, MANIFEST, failed)["groups"]["keyboard"]["missing"] == ["sleep-wake: fail"]  # newest wins

# A version bump and docs keep the results; a device-side change voids them.
bump = commit(stack, **{"mxoptions/__init__.py": '__version__ = "0.10.0.post1"\n', "README.md": "r2\n",
                        "docs/notes.md": "n\n"})
assert hwgate.check(bump, MANIFEST, full)["stable"]
assert not hwgate.check(commit(bump, **{"mxoptions/hidpp.py": "h2\n"}), MANIFEST, full)["stable"]

# A control unit landing turns its group on over old results; kbsettings.py is classified, a new module is not.
fn_line = CONTRACT["fn-lock"][1] + "\n"
r = hwgate.check(commit(stack, **{"mxoptions/kbsettings.py": fn_line}), MANIFEST, full)
assert not r["stable"] and not r["unclassified"] and r["groups"]["fn-lock"]["present"], r
r = hwgate.check(commit(stack, **{"mxoptions/brand_new.py": fn_line}), MANIFEST, full)
assert r["unclassified"] == ["mxoptions/brand_new.py"] and r["groups"]["fn-lock"]["present"] and not r["stable"], r
for path in ("mxoptions/brand_new.py", "data/brand_new.rules"):  # unclassified alone blocks stable, whatever it holds
    r = hwgate.check(commit(stack, **{path: "x = 1\n"}), MANIFEST, full)
    assert r["unclassified"] == [path] and not any(g["missing"] for g in r["groups"].values()) and not r["stable"], r

with contextlib.redirect_stdout(io.StringIO()):
    assert hwgate.main(["check", base, os.path.join(REPO, "none.json")]) == 0
    assert hwgate.main(["check", stack, os.path.join(REPO, "none.json")]) == 1
subprocess.run(["rm", "-rf", REPO])
print("hwgate self-check: OK")
