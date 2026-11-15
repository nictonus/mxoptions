"""The settings as a file of their own: exported from the settings in use, imported in one step that never
leaves the program, its settings file and its engine apart. Everything here is made up: the settings, their
secrets, the folders (temporary ones) and every failure of the disk, which is caused on purpose."""

import contextlib
import copy
import errno
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

import tests.support  # noqa: F401,E402
import mxoptions  # noqa: E402
from mxoptions import actions, config, keyboard  # noqa: E402
from mxoptions.ui import pages  # noqa: E402
from mxoptions.ui.store import Store  # noqa: E402
from tests.test_keyboard import CTRL, Rig, keyboard_device, mouse_device  # noqa: E402

APP = QApplication.instance() or QApplication([])

SECRET = "SYNT-HETI-CPAI-RKEY-0001"   # a made-up pairing key
OTHER_SECRET = "SYNT-HETI-CPAI-RKEY-0002"
PEER_ID, LOCAL_ID, OTHER_ID = "synthetic-peer-7f3a", "synthetic-here-91c2", "synthetic-here-5d0e"
PEER_ADDRESS, PEER_NAME = "192.0.2.7", "Synthetic desk"  # an address for documentation (RFC 5737)
COMMAND = "echo synthetic-command"
SCRIPT = "#!/bin/sh\necho synthetic-script\n"

# Every part of the Flow settings: what may go to another computer and what never does.
FLOW_KEYS = {
    "enabled": "portable", "id": "local", "peers": "secret", "port": "portable", "push_mm": "portable",
    "corner": "portable", "fullscreen": "portable", "clipboard": "portable", "files": "portable",
    "files_max_mb": "portable", "cue_arrived": "portable", "cue_failed": "portable", "channel": "local",
    "mouse_follows_keyboard": "portable", "keyboard_follows_mouse": "portable",
    "hold_ctrl": "portable",
}


def settings(secret=SECRET, here=LOCAL_ID, channel=2):
    """Settings in use on one made-up computer: paired over Flow, a command and a script on keys, a profile."""
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["flow"].update(enabled=True, id=here, channel=channel, mouse_follows_keyboard=True, keyboard_follows_mouse=True, peers=[{
        "key": secret, "id": PEER_ID, "address": PEER_ADDRESS, "name": PEER_NAME, "edge": "left", "edge_at": 1.7e9}])
    cfg["keyboard"]["keys"] = {"0x0f01": {"type": "command", "command": COMMAND},
                               "0x0f02": {"type": "script", "script": SCRIPT, "name": "Synthetic"}}
    cfg["keyboard"]["profiles"] = [{"app": "synthetic.app", "name": "Synthetic app",
                                    "keys": {"0x0f01": config.NATIVE, "0x0f03": CTRL}}]
    cfg["buttons"]["back"] = {"type": "command", "command": COMMAND}
    cfg["pointer"]["dpi"] = 1800
    clean = config.clean(cfg)
    assert clean == cfg, "made-up settings that the program would change"
    return cfg


class Engine:
    """Stands in for the engine: keeps every configuration it is given."""

    def __init__(self):
        self.configs = []

    def set_config(self, cfg):
        self.configs.append(copy.deepcopy(cfg))

    def __getattr__(self, name):
        return lambda *args: None


class Folder(unittest.TestCase):
    """A settings folder of its own (temporary) with a recovery journal in it that nothing here may touch."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.config_path = os.path.join(self.folder, "config.json")
        self.backup_path = os.path.join(self.folder, config.BACKUP_NAME)
        self.journal = os.path.join(self.folder, "keyboard-diverted.json")
        with open(self.journal, "w") as f:
            json.dump({"version": 1, "keyboards": {"0123456789abcdef": ["0x0f01"]}}, f)
        for name, value in (("CONFIG_DIR", self.folder), ("CONFIG_PATH", self.config_path)):
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(keyboard, "JOURNAL", self.journal)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.outside = tempfile.mkdtemp()  # where the user keeps exported files
        self.addCleanup(shutil.rmtree, self.outside, True)

    def store(self, cfg=None, saved=True):
        store = Store(cfg if cfg is not None else settings(), Engine())
        if saved:
            store.flush()
        return store

    def file(self, raw, name="settings.json"):
        path = os.path.join(self.outside, name)
        with open(path, "wb") as f:
            f.write(raw)
        return path

    def exported(self, cfg, name="settings.json"):
        return self.file(config.export_data(cfg), name)

    def read(self, path):
        try:
            with open(path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def snapshot(self, store):
        """Everything an import that fails must leave as it was."""
        return (copy.deepcopy(store.cfg), list(store.engine.configs), store._save_timer.isActive(),
                self.read(self.config_path), self.read(self.journal), sorted(os.listdir(self.folder)))

    def mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)


class Export(Folder):
    def test_every_flow_setting_is_either_portable_or_stays_here(self):
        self.assertEqual(set(FLOW_KEYS), set(config.DEFAULTS["flow"]), "a new Flow setting: say whether it may leave")
        self.assertEqual({key for key, kind in FLOW_KEYS.items() if kind != "portable"}, set(config.LOCAL_FLOW))
        self.assertEqual(config.LOCAL_FLOW, {"peers": [], "id": "", "channel": -1})
        self.assertEqual(config.LOCAL_FLOW, {key: config.DEFAULTS["flow"][key] for key in config.LOCAL_FLOW})

    def test_the_file_holds_the_settings_in_use_but_what_stays_here(self):
        store = self.store(saved=False)
        store.set("pointer", "dpi", 2400)  # not saved yet: exported all the same
        path = os.path.join(self.outside, "out.json")
        self.assertIsNone(store.export_settings(path))
        document = json.loads(self.read(path).decode("utf-8"))
        self.assertEqual(set(document), {"format", "version", "config"})
        self.assertEqual((document["format"], document["version"]), ("mxoptions-settings", 1))
        expected = copy.deepcopy(store.cfg)
        expected["flow"].update(peers=[], id="", channel=-1)
        self.assertEqual(document["config"], expected)  # keyboard profiles, commands and scripts included
        self.assertEqual(document["config"]["pointer"]["dpi"], 2400)
        self.assertEqual(store.cfg["flow"]["peers"][0]["key"], SECRET)  # the settings in use keep theirs
        self.assertIsNone(self.read(self.backup_path))

    def test_no_secret_of_this_computer_is_in_the_file(self):
        raw = config.export_data(settings())
        for secret in (SECRET, SECRET.replace("-", ""), PEER_ID, LOCAL_ID, PEER_ADDRESS, PEER_NAME, "0123456789abcdef",
                       "keyboard-diverted", '"channel": 2'):
            with self.subTest(secret=secret):
                self.assertNotIn(secret.encode("utf-8"), raw)
        self.assertIn(b'"channel": -1', raw)
        for mine in (COMMAND, "synthetic-script", "synthetic.app"):  # the user's own: in it, as the page says
            self.assertIn(mine.encode("utf-8"), raw)

    def test_the_file_is_the_user_s_alone_and_replaces_one_there(self):
        path = self.file(b"old", "out.json")
        os.chmod(path, 0o644)
        self.assertIsNone(self.store().export_settings(path))
        self.assertEqual(self.mode(path), 0o600)
        self.assertTrue(self.read(path).startswith(b"{"))
        self.assertEqual(os.listdir(self.outside), ["out.json"])  # nothing left beside it

    def test_settings_too_large_are_not_written_and_at_the_limit_they_go_both_ways(self):
        cfg = settings()
        cfg["keyboard"]["keys"] = {config.key_id(0x0f10 + n): {"type": "script", "script": "x" * 60000}
                                   for n in range(17)}
        last = config.key_id(0x0f40)
        cfg["keyboard"]["keys"][last] = {"type": "script", "script": "x"}
        room = config.SETTINGS_FILE_MAX - len(config.export_data(cfg))
        cfg["keyboard"]["keys"][last]["script"] = "x" * (1 + room)
        self.assertEqual(config.clean(copy.deepcopy(cfg)), cfg)  # valid settings
        raw = config.export_data(cfg)
        self.assertEqual(len(raw), config.SETTINGS_FILE_MAX)
        target = self.store(settings(OTHER_SECRET, OTHER_ID))
        self.assertIsNone(target.import_settings(self.file(raw)))
        self.assertEqual(target.cfg["keyboard"], cfg["keyboard"])

        cfg["keyboard"]["keys"][last]["script"] += "x"
        path = self.file(b"before", "out.json")
        message = Store(cfg, Engine()).export_settings(path)
        self.assertIn("1025 KB, more than the 1024 KB", message)
        self.assertEqual(self.read(path), b"before")  # refused before the file is touched
        self.assertEqual(sorted(os.listdir(self.outside)), ["out.json", "settings.json"])

    def test_the_limit_counts_bytes_not_characters(self):
        cfg = settings()
        cfg["keyboard"]["keys"] = {config.key_id(0x0f10 + n): {"type": "command", "command": "ї" * config.MAX_KEY_TEXT}
                                   for n in range(9)}  # fewer characters than the limit, twice as many bytes
        self.assertEqual(config.clean(copy.deepcopy(cfg)), cfg)
        with self.assertRaisesRegex(ValueError, "more than the 1024 KB"):
            config.export_data(cfg)

    def test_what_could_not_be_read_back_is_not_exported(self):
        cfg = settings()
        nested = []
        for _level in range(config.SETTINGS_DEPTH_MAX):
            nested = [nested]
        cfg["unknown"] = nested  # from a hand-written file: kept, but too deep for a settings file
        with self.assertRaisesRegex(ValueError, "cannot"):
            config.export_data(cfg)
        cfg["unknown"] = "\ud800"  # no Unicode text
        with self.assertRaisesRegex(ValueError, "cannot"):
            config.export_data(cfg)

    def test_a_write_that_fails_is_told_and_leaves_no_file_behind(self):
        store = self.store()
        message = store.export_settings(os.path.join(self.outside, "missing-folder", "out.json"))
        self.assertIn("could not be written", message)
        path = os.path.join(self.outside, "out.json")
        with fault("dirsync", "out.json"):
            message = store.export_settings(path)
        self.assertIn("could not confirm", message)
        self.assertTrue(self.read(path).startswith(b"{"))  # it is there, only not known to be on the disk


class Reading(Folder):
    def refused(self, raw, says="not a settings file"):
        store = self.store()
        store.set("pointer", "dpi", 1234)  # a change waiting to be saved
        before = self.snapshot(store)
        message = store.import_settings(self.file(raw) if isinstance(raw, bytes) else raw)
        self.assertIsNotNone(message)
        self.assertIn(says, message)
        self.assertEqual(self.snapshot(store), before)
        self.assertTrue(store._save_timer.isActive())
        self.assertNotIn(config.BACKUP_NAME, os.listdir(self.folder))
        return message

    def document(self, **changes):
        document = {"format": "mxoptions-settings", "version": 1, "config": settings()}
        document.update(changes)
        return json.dumps(document).encode("utf-8")

    def test_what_is_not_a_settings_file_is_refused_and_changes_nothing(self):
        cfg = settings()
        for name, raw in (
                ("broken JSON", b'{"format": '), ("not UTF-8", b'\xff\xfe{}'), ("UTF-8 mark", b"\xef\xbb\xbf{}"),
                ("a list", b"[]"), ("a number", b"1"), ("empty", b""),
                ("other format", self.document(format="other")), ("no format", json.dumps({"version": 1, "config": cfg}).encode()),
                ("more at the top", self.document(extra=1)), ("version 0", self.document(version=0)),
                ("version true", self.document(version=True)), ("version text", self.document(version="1")),
                ("version 1.0", self.document(version=1.0)), ("config a list", self.document(config=[])),
                ("config without version", self.document(config={k: v for k, v in cfg.items() if k != "version"})),
                ("config version true", self.document(config=dict(cfg, version=True))),
                ("config version text", self.document(config=dict(cfg, version="6")))):
            with self.subTest(name=name):
                self.refused(raw)

    def test_a_file_from_a_newer_version_is_refused(self):
        self.refused(self.document(version=2), "newer version")
        self.refused(self.document(config=dict(settings(), version=config.DEFAULTS["version"] + 1)), "newer version")

    def test_nesting_too_deep_is_refused(self):
        deep = settings()
        nested = []
        for _level in range(config.SETTINGS_DEPTH_MAX):
            nested = [nested]
        deep["unknown"] = nested
        self.refused(self.document(config=deep))
        self.refused(b'{"format": "mxoptions-settings", "version": 1, "config": ' + b"[" * 200000 + b"]" * 200000 + b"}")

    def test_a_file_too_large_is_refused_by_its_size_and_by_what_is_read(self):
        message = self.refused(b" " * (config.SETTINGS_FILE_MAX + 1), "more than the 1024 KB")
        self.assertIn("1025 KB", message)
        path = self.file(b"")
        os.truncate(path, 64 << 20)  # sparse: refused by its size, before anything is read
        self.refused(path, "65536 KB")
        path = self.file(self.document() + b" " * config.SETTINGS_FILE_MAX)
        real = os.fstat

        def small(fd):  # a file that says it is small and then is not, as a growing one would
            info = real(fd)
            return os.stat_result((info.st_mode, *info[1:6], 10, *info[7:]))

        with mock.patch.object(config.os, "fstat", small):
            self.refused(path, "more than the 1024 KB")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX")
    def test_what_is_not_a_file_is_refused_without_waiting(self):
        path = os.path.join(self.outside, "fifo.json")
        os.mkfifo(path)
        self.refused(path)
        self.refused(os.path.join(self.outside, "missing.json"), "could not be read")


class Upgrade(Folder):
    FIXTURES = {
        3: {"version": 3, "app": {"gesture_distance": 2.0, "model": "studio"}, "pointer": {"dpi": 1200}},
        4: {"version": 4, "wheel_click": {"mode": "sound", "free": 0, "sound": "relay", "file": "", "volume": 1.0}},
        5: {"version": 5, "flow": {"key": SECRET, "peer": PEER_ADDRESS, "peer_name": PEER_NAME, "edge": "top"}},
        6: settings(),
    }

    def test_load_is_read_then_upgrade(self):
        for version, stored in self.FIXTURES.items():
            with self.subTest(version=version):
                with open(self.config_path, "w") as f:
                    json.dump(stored, f)
                loaded = config.load()
                self.assertEqual(loaded, config.upgrade(copy.deepcopy(stored)))
                self.assertEqual(json.loads(self.read(self.config_path)), loaded if version != 6 else stored)
        self.assertEqual(config.upgrade(copy.deepcopy(self.FIXTURES[3]))["gestures"]["far_mm"], 32.0)
        self.assertEqual(config.upgrade(copy.deepcopy(self.FIXTURES[4]))["wheel_click"]["sound"], "tick")
        self.assertEqual(config.upgrade(copy.deepcopy(self.FIXTURES[5]))["flow"]["peers"][0]["key"], SECRET)
        for odd in ([], "text", None, {"version": True}):
            with self.subTest(odd=odd):
                self.assertEqual(config.upgrade(copy.deepcopy(odd)), config.upgrade({}))

    def test_an_older_exported_file_is_brought_up_to_date(self):
        store = self.store()
        old = copy.deepcopy(self.FIXTURES[3])
        raw = json.dumps({"format": "mxoptions-settings", "version": 1, "config": old}).encode("utf-8")
        self.assertIsNone(store.import_settings(self.file(raw)))
        expected = config.upgrade(copy.deepcopy(old))
        expected["flow"].update({key: settings()["flow"][key] for key in config.LOCAL_FLOW})
        self.assertEqual(store.cfg, expected)

    def test_settings_of_the_wrong_kind_import_as_load_takes_them(self):
        store = self.store()
        for section in config.DEFAULTS:
            for odd in ([], "text", 7, None):
                with self.subTest(section=section, odd=odd):
                    cfg = dict(settings(), **{section: odd}) if section != "version" else settings()
                    raw = json.dumps({"format": "mxoptions-settings", "version": 1, "config": cfg}).encode("utf-8")
                    self.assertIsNone(store.import_settings(self.file(raw)))
                    self.assertEqual(config.clean(copy.deepcopy(store.cfg)), store.cfg)

    def test_parts_this_version_does_not_know_are_kept_as_load_keeps_them(self):
        cfg = settings()
        cfg["future"] = {"setting": [1, 2]}
        cfg["flow"]["future_flag"] = True
        cfg["keyboard"]["future"] = "kept"
        store = self.store(settings(OTHER_SECRET, OTHER_ID))
        self.assertIsNone(store.import_settings(self.exported(cfg)))
        self.assertEqual(store.cfg["future"], {"setting": [1, 2]})
        self.assertTrue(store.cfg["flow"]["future_flag"])
        self.assertEqual(store.cfg["keyboard"]["future"], "kept")
        with open(self.config_path, "w") as f:
            json.dump(cfg, f)
        self.assertEqual({k: v for k, v in config.load().items() if k != "flow"},
                         {k: v for k, v in store.cfg.items() if k != "flow"})
        self.assertTrue(json.loads(config.export_data(store.cfg))["config"]["flow"]["future_flag"])  # and go on


class Importing(Folder):
    def test_imported_settings_are_used_saved_and_keep_this_computer_s_pairing(self):
        source = settings()
        source["pointer"]["dpi"] = 3200
        store = self.store(settings(OTHER_SECRET, OTHER_ID, channel=1))
        local = json.dumps({key: store.cfg["flow"][key] for key in config.LOCAL_FLOW})
        self.assertIsNone(store.import_settings(self.exported(source)))
        expected = copy.deepcopy(source)
        expected["flow"].update(json.loads(local))
        self.assertEqual(store.cfg, expected)
        self.assertEqual(json.dumps({key: store.cfg["flow"][key] for key in config.LOCAL_FLOW}), local)
        self.assertEqual(store.engine.configs, [expected])
        self.assertFalse(store._save_timer.isActive())
        self.assertEqual(self.read(self.config_path), config.to_bytes(expected))
        self.assertEqual(config.load(), expected)
        for path in (self.config_path, self.backup_path):
            self.assertEqual(self.mode(path), 0o600)
        self.assertEqual(sorted(os.listdir(self.folder)), ["config.backup.json", "config.json", "keyboard-diverted.json"])

    def test_the_backup_holds_the_settings_in_use_with_changes_not_saved_yet(self):
        store = self.store()
        on_disk = self.read(self.config_path)
        store.set("pointer", "dpi", 1234)
        self.assertTrue(store._save_timer.isActive())
        in_use = copy.deepcopy(store.cfg)
        self.assertIsNone(store.import_settings(self.exported(settings(OTHER_SECRET))))
        self.assertFalse(store._save_timer.isActive())
        backup = json.loads(self.read(self.backup_path))
        self.assertEqual(backup, in_use)  # the change, and the pairing key of this computer
        self.assertNotEqual(config.to_bytes(in_use), on_disk)
        with open(self.config_path, "wb") as f:  # a plain settings file: load() takes it
            f.write(self.read(self.backup_path))
        self.assertEqual(config.load(), in_use)

    def test_first_run_without_a_settings_file(self):
        store = self.store(saved=False)
        self.assertIsNone(self.read(self.config_path))
        self.assertIsNone(store.import_settings(self.exported(settings(OTHER_SECRET))))
        self.assertEqual(config.load(), store.cfg)


@contextlib.contextmanager
def fault(stage, target, times=None):
    """An OSError at ``stage`` of config.write_durable while it writes a file named ``target``: "open" (making
    the file beside it), "write", "fsync" (of the file), "replace" (putting it in its place) or "dirsync" (the
    folder synced). ``times``: that many times, then it works again (None: always)."""
    state = {"writing": None, "left": times}

    def due(here):
        if here != stage or state["writing"] != target or state["left"] == 0:
            return False
        if state["left"] is not None:
            state["left"] -= 1
        return True

    real_mkstemp, real_fsync, real_replace, real_open = tempfile.mkstemp, os.fsync, os.replace, os.open

    def mkstemp(prefix="", suffix="", dir=None):
        state["writing"] = prefix[:-1]
        if due("open"):
            raise OSError(errno.ENOSPC, "No space left on device (synthetic)")
        fd, path = real_mkstemp(prefix=prefix, suffix=suffix, dir=dir)
        if due("write"):  # a real failing write: the file can only be read
            os.close(fd)
            fd = real_open(path, os.O_RDONLY)
        return fd, path

    def fsync(fd):
        here = "dirsync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "fsync"
        if due(here):
            raise OSError(errno.EIO, "Input/output error (synthetic)")
        return real_fsync(fd)

    def replace(source, destination):
        if os.path.basename(destination) == target and due("replace"):
            raise OSError(errno.EXDEV, "Invalid cross-device link (synthetic)")
        return real_replace(source, destination)

    def unlink_then(path, real=os.unlink):  # a removal is the last step of a first run gone back
        state["writing"] = os.path.basename(path)
        return real(path)

    with mock.patch.object(config.tempfile, "mkstemp", mkstemp), mock.patch.object(config.os, "fsync", fsync), \
            mock.patch.object(config.os, "replace", replace), mock.patch.object(config.os, "unlink", unlink_then):
        yield


class Faults(Folder):
    """One failure at each step of the import, of the backup and of the new settings file."""

    STAGES = ("open", "write", "fsync", "replace", "dirsync")

    def leftovers(self):
        return [name for name in os.listdir(self.folder) if name.endswith(".tmp")]

    def importing(self, first_run=False):
        store = self.store(saved=not first_run)
        store.set("pointer", "dpi", 1234)  # waiting to be saved
        return store, self.exported(settings(OTHER_SECRET)), self.snapshot(store)

    def unchanged_but_disk(self, store, before):
        cfg, configs, timer, _disk, journal, _names = before
        self.assertEqual(store.cfg, cfg)
        self.assertEqual(store.engine.configs, configs)
        self.assertEqual(store._save_timer.isActive(), timer)
        self.assertTrue(timer)
        self.assertEqual(self.read(self.journal), journal)
        self.assertEqual(self.leftovers(), [])

    def test_the_backup_failing_stops_the_import_at_every_step(self):
        for stage in self.STAGES:
            with self.subTest(stage=stage):
                if os.path.exists(self.backup_path):
                    os.unlink(self.backup_path)
                store, path, before = self.importing()
                with fault(stage, config.BACKUP_NAME):
                    message = store.import_settings(path)
                self.assertIn("could not be kept in a backup", message)
                self.unchanged_but_disk(store, before)
                self.assertEqual(self.read(self.config_path), before[3])
                self.assertEqual(self.read(self.backup_path) is not None, stage == "dirsync")

    def test_the_new_settings_failing_before_their_place_leave_the_file_as_it_was(self):
        for first_run in (False, True):
            for stage in self.STAGES[:-1]:
                with self.subTest(stage=stage, first_run=first_run):
                    if os.path.exists(self.config_path):
                        os.unlink(self.config_path)
                    store, path, before = self.importing(first_run)
                    with fault(stage, "config.json"):
                        message = store.import_settings(path)
                    self.assertIn("could not be saved", message)
                    self.assertIn("They are as they were", message)
                    self.unchanged_but_disk(store, before)
                    self.assertEqual(self.read(self.config_path), before[3])  # absent on a first run
                    self.assertEqual(json.loads(self.read(self.backup_path)), store.cfg)

    def test_the_folder_not_synced_puts_the_settings_in_use_back(self):
        for first_run in (False, True):
            with self.subTest(first_run=first_run):
                if os.path.exists(self.config_path):
                    os.unlink(self.config_path)
                store, path, before = self.importing(first_run)
                with fault("dirsync", "config.json", times=1):
                    message = store.import_settings(path)
                self.assertIn("could not confirm", message)
                self.assertIn("were put back", message)
                self.unchanged_but_disk(store, before)
                if first_run:
                    self.assertIsNone(self.read(self.config_path))  # no file, as before
                else:
                    self.assertEqual(self.read(self.config_path), config.to_bytes(store.cfg))  # with the change
                self.assertEqual(json.loads(self.read(self.backup_path)), store.cfg)

    def test_putting_back_failing_too_says_where_the_settings_in_use_are(self):
        for first_run in (False, True):
            with self.subTest(first_run=first_run):
                if os.path.exists(self.config_path):
                    os.unlink(self.config_path)
                store, path, before = self.importing(first_run)
                with fault("dirsync", "config.json"), self.assertLogs("mxoptions.ui.store", "WARNING"):
                    message = store.import_settings(path)
                self.assertIn("failed too", message)
                self.assertIn("may hold the imported settings", message)
                self.assertIn(self.backup_path, message)
                self.unchanged_but_disk(store, before)
                self.assertEqual(json.loads(self.read(self.backup_path)), store.cfg)  # usable: load() takes it

    def test_the_put_back_itself_can_fail_before_its_place(self):
        store, path, before = self.importing()
        calls = {"n": 0}
        real = config.write_durable

        def second_fails(data, target):
            calls["n"] += 1
            if calls["n"] == 2:
                raise config.NotDurable(errno.EIO, "Input/output error (synthetic)")
            if calls["n"] == 3:
                raise OSError(errno.ENOSPC, "No space left on device (synthetic)")
            return real(data, target)

        with mock.patch.object(config, "write_durable", second_fails), self.assertLogs("mxoptions.ui.store", "WARNING"):
            message = store.import_settings(path)
        self.assertIn("No space left on device", message)
        self.assertIn(self.backup_path, message)
        self.unchanged_but_disk(store, before)


class Windows(Folder):
    """write_durable as it goes on Windows, simulated here: MoveFileEx with write-through, no folder to sync."""

    def test_the_file_is_put_in_place_written_through_and_no_folder_is_synced(self):
        moved, synced, real_replace, real_fsync = [], [], os.replace, os.fsync

        def replace_file(source, target):
            moved.append((source, target))
            real_replace(source, target)

        def fsync(fd):
            synced.append(stat.S_ISDIR(os.fstat(fd).st_mode))
            real_fsync(fd)

        windows = mock.Mock(replace_file=replace_file)
        path = os.path.join(self.outside, "out.json")
        with mock.patch.object(config.sys, "platform", "win32"), \
                mock.patch.object(mxoptions, "windows", windows, create=True), \
                mock.patch.dict(sys.modules, {"mxoptions.windows": windows}), \
                mock.patch.object(config.os, "replace", side_effect=AssertionError("os.replace on Windows")), \
                mock.patch.object(config.os, "fsync", fsync):
            config.write_durable(b"synthetic", path)
            self.assertEqual(self.read(path), b"synthetic")
            self.assertEqual(synced, [False])  # the file, never a folder
            self.assertEqual(len(moved), 1)
            self.assertEqual(moved[0][1], path)
            windows.replace_file = mock.Mock(side_effect=OSError(errno.EACCES, "Access is denied (synthetic)"))
            with self.assertRaises(OSError) as raised:
                config.write_durable(b"other", path)
            self.assertNotIsInstance(raised.exception, config.NotDurable)
        self.assertEqual(self.read(path), b"synthetic")
        self.assertEqual(os.listdir(self.outside), ["out.json"])


class HeldKeys(unittest.TestCase):
    def test_an_import_lets_go_of_a_held_key_whose_action_it_changes(self):
        kb = keyboard_device()
        rig = Rig(self, {1: kb, 2: mouse_device()}, keys={"0x0f01": CTRL}).start()
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        with mock.patch.object(config, "CONFIG_DIR", folder), \
                mock.patch.object(config, "CONFIG_PATH", os.path.join(folder, "config.json")):
            store = Store(copy.deepcopy(rig.engine.cfg), rig.engine)
            rig.keys_down(1, kb, 0x0f01)
            self.assertEqual(rig.input.sent[-1], ("KEY_LEFTCTRL", True))
            journal = rig.saved()
            other = copy.deepcopy(store.cfg)
            other["keyboard"]["keys"]["0x0f01"] = actions.keys("LEFTALT")
            path = os.path.join(folder, "settings.json")
            with open(path, "wb") as f:
                f.write(config.export_data(other))
            self.assertIsNone(store.import_settings(path))
            rig.settle()
        self.assertEqual(rig.input.sent[-1], ("KEY_LEFTCTRL", False))
        rig.keys_down(1, kb)
        self.assertNotIn("KEY_LEFTALT", [name for name, _down in rig.input.sent])
        self.assertEqual(rig.saved(), journal)  # the same keys diverted: the journal as it was


class Page(Folder):
    def test_the_buttons_export_and_import_through_the_dialogs(self):
        store = self.store()
        page = pages.DevicePage(store)
        self.addCleanup(page.deleteLater)
        buttons = {button.text(): button for button in page.findChildren(QPushButton)}
        path = os.path.join(self.outside, "out.json")
        told = []
        with mock.patch.object(pages.QFileDialog, "getSaveFileName", return_value=(path, "")), \
                mock.patch.object(pages.QMessageBox, "information", lambda *args: told.append(args[2])):
            buttons["Export…"].click()
        self.assertEqual(told, ["The settings are in out.json."])
        store.set("pointer", "dpi", 1000)
        with mock.patch.object(pages.QFileDialog, "getOpenFileName", return_value=(path, "")), \
                mock.patch.object(pages.QMessageBox, "question", return_value=pages.QMessageBox.StandardButton.No):
            buttons["Import…"].click()
        self.assertEqual(store.cfg["pointer"]["dpi"], 1000)  # not asked for
        with mock.patch.object(pages.QFileDialog, "getOpenFileName", return_value=(path, "")), \
                mock.patch.object(pages.QMessageBox, "question", return_value=pages.QMessageBox.StandardButton.Yes), \
                mock.patch.object(pages.QMessageBox, "information", lambda *args: told.append(args[2])):
            buttons["Import…"].click()
        self.assertEqual(store.cfg["pointer"]["dpi"], 1800)
        self.assertIn("are in use", told[-1])
        with open(path, "wb") as f:
            f.write(b"[]")
        warned = []
        with mock.patch.object(pages.QFileDialog, "getOpenFileName", return_value=(path, "")), \
                mock.patch.object(pages.QMessageBox, "question", return_value=pages.QMessageBox.StandardButton.Yes), \
                mock.patch.object(pages.QMessageBox, "warning", lambda *args: warned.append(args[2])):
            buttons["Import…"].click()
        self.assertEqual(warned, ["out.json cannot be imported. It is not a settings file of MX Options."])


if __name__ == "__main__":
    unittest.main()
