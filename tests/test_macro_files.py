"""One macro as a file of its own: exported from the macro editor, imported into it to look at before Save.
Everything here is made up: macros, folders (temporary), dialogs, failures of the disk. No command runs: the
runner of the Test button and every way to start a process are stand-ins that only count."""

import copy
import json
import os
import stat
import subprocess
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests.support  # noqa: F401,E402
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QWidget  # noqa: E402

from mxoptions import actions, config, device, i18n  # noqa: E402
from mxoptions.ui import editors  # noqa: E402
from mxoptions.ui.action_picker import ActionList  # noqa: E402
from tests.test_settings_file import Folder, fault  # noqa: E402

APP = QApplication.instance() or QApplication([])

NEVER = "mxoptions-synthetic-never-run"  # no such program
NOT_ONE = "It is not a macro file of MX Options."
LIMIT = config.SETTINGS_FILE_MAX


def macro(**changes):
    made = {"type": "macro", "name": "Synthetic", "repeat": False, "steps": [{"do": "text", "text": "synthetic"}]}
    made.update(changes)
    return made


def document(value, **top):
    """A macro file holding ``value``, ASCII escapes (lone surrogates too) as JSON allows them."""
    made = {"format": "mxoptions-macro", "version": 1, "macro": value}
    made.update(top)
    return json.dumps(made).encode("ascii")


def with_delay(literal):
    return ('{"format": "mxoptions-macro", "version": 1, "macro": {"type": "macro", "steps": '
            '[{"do": "delay", "ms": %s}]}}' % literal).encode("ascii")


PRESET_STEPS = [{"do": "action", "action": copy.deepcopy(p.action)} for p in actions.PRESETS
                if p.action.get("type") in actions.STEP_ACTIONS]
OWN_STEPS = [
    {"do": "keys", "keys": ["KEY_LEFTCTRL", "KEY_C"]}, {"do": "keys", "keys": []},
    {"do": "text", "text": "Привіт 🙂\n\ttab \u202e"}, {"do": "text", "text": " "},
    {"do": "delay", "ms": 0}, {"do": "delay", "ms": actions.MAX_DELAY_MS},
    *({"do": "click", "button": button} for button in ("BTN_LEFT", "BTN_RIGHT", "BTN_MIDDLE")),
    {"do": "command", "command": NEVER + " --flag 'a b'\tc\nd"}, {"do": "command", "command": ""},
    {"do": "action", "action": {"type": "shortcut", "component": "kwin", "name": "Synthetic"}},
    {"do": "action", "action": {"type": "command", "command": " " + NEVER + " \\ \n"}},  # as written: never trimmed
    {"do": "action", "action": {"type": "script", "name": "s", "script": "#!/bin/sh\n\techo synthetic\n"}},
    {"do": "action", "action": {"type": "script", "script": NEVER}},
    {"do": "action", "action": {"type": "hook", "name": "synthetic \u202e hook"}},
    *({"do": "haptic", "waveform": waveform} for waveform in device.WAVEFORMS),
]
ALL_STEPS = OWN_STEPS + PRESET_STEPS
FULL = {"type": "macro", "name": "Сигнал ✓ 🙂", "repeat": True, "steps": ALL_STEPS[:actions.MAX_MACRO_STEPS],
        "stop_on_press": True}


def refused(test, raw, says=NOT_ONE):
    with test.assertRaises(ValueError) as caught:
        config.import_macro(raw)
    test.assertIs(type(caught.exception), ValueError)  # what to tell the user, never an error of the code
    test.assertIn(says, str(caught.exception))
    return str(caught.exception)


def keys_anywhere(value):
    if isinstance(value, dict):
        return set(value) | set().union(*(keys_anywhere(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(keys_anywhere(v) for v in value))
    return set()


class Format(unittest.TestCase):
    def test_every_step_kind_goes_out_and_comes_back(self):
        self.assertEqual(len(FULL["steps"]), actions.MAX_MACRO_STEPS)
        self.assertEqual({step["do"] for step in FULL["steps"]}, set(actions.MACRO_STEPS))
        for start in range(0, len(ALL_STEPS), actions.MAX_MACRO_STEPS):
            made = dict(FULL, steps=ALL_STEPS[start:start + actions.MAX_MACRO_STEPS])
            with self.subTest(start=start):
                self.assertTrue(config.keyboard_action(made))
                self.assertEqual(config.import_macro(config.export_macro(copy.deepcopy(made))), made)
        covered = [step["action"] for step in ALL_STEPS if step["do"] == "action"]
        self.assertEqual({action["type"] for action in covered}, set(actions.STEP_ACTIONS))
        for preset in actions.PRESETS:
            if preset.action.get("type") in actions.STEP_ACTIONS:
                self.assertIn(preset.action, covered)

    def test_the_file_holds_the_macro_alone(self):
        raw = config.export_macro(macro(name="Сигнал"))
        made = json.loads(raw.decode("utf-8"))
        self.assertEqual(made, {"format": "mxoptions-macro", "version": 1, "macro": macro(name="Сигнал")})
        self.assertIn("Сигнал".encode("utf-8"), raw)  # as written, not escaped
        self.assertNotIn("stop_on_press", made["macro"])
        stopping = json.loads(config.export_macro(macro(stop_on_press=True)))["macro"]
        self.assertIs(stopping["stop_on_press"], True)
        for step in ALL_STEPS:
            with self.subTest(step=step):
                written = json.loads(config.export_macro(macro(steps=[step])))
                self.assertFalse(keys_anywhere(written) & {"flow", "peers", "id", "app", "channel", "device", "key"})

    def test_what_is_not_a_macro_file_is_refused(self):
        whole = config.export_macro(FULL)
        for name, raw in (
                ("broken JSON", b'{"format": '), ("cut in half", whole[:len(whole) // 2]), ("not UTF-8", b"\xff\xfe{}"),
                ("UTF-8 mark", b"\xef\xbb\xbf" + whole), ("a list", b"[]"), ("a number", b"1"), ("empty", b""),
                ("null", b"null"), ("a string", b'"macro"'),
                ("settings file", json.dumps({"format": "mxoptions-settings", "version": 1, "config": {}}).encode()),
                ("other format", document(macro(), format="other")), ("format a list", document(macro(), format=[])),
                ("no format", json.dumps({"version": 1, "macro": macro()}).encode()),
                ("no macro", json.dumps({"format": "mxoptions-macro", "version": 1}).encode()),
                ("more at the top", document(macro(), extra=1)), ("version 0", document(macro(), version=0)),
                ("version -1", document(macro(), version=-1)), ("version true", document(macro(), version=True)),
                ("version text", document(macro(), version="1")), ("version 1.0", document(macro(), version=1.0)),
                ("version null", document(macro(), version=None)), ("version list", document(macro(), version=[1])),
                ("macro a list", document([macro()])), ("macro null", document(None)), ("macro text", document("m")),
                ("nested past the parser", b'{"format": "mxoptions-macro", "version": 1, "macro": '
                 + b"[" * 200000 + b"]" * 200000 + b"}"),
                ("nested in a step", document(macro(steps=[{"do": "keys", "keys": json.loads("[" * 500 + "]" * 500)}]))),
                ("huge integer", b'{"format": "mxoptions-macro", "version": ' + b"1" * 5000 + b', "macro": {}}')):
            with self.subTest(name=name):
                refused(self, raw)

    def test_a_file_from_a_newer_version_is_refused(self):
        refused(self, document(macro(), version=2), "newer version")
        refused(self, document("whatever a later version holds", version=99), "newer version")

    def test_a_macro_with_more_than_it_may_hold_is_refused(self):
        too_many = [{"do": "text", "text": "t"}] * (actions.MAX_MACRO_STEPS + 1)
        for name, value in (
                ("other type", macro(type="keys")), ("no type", {k: v for k, v in macro().items() if k != "type"}),
                ("type a list", macro(type=["macro"])), ("extra field", macro(extra=1)), ("a trigger", macro(button="back")),
                ("no steps", {k: v for k, v in macro().items() if k != "steps"}), ("zero steps", macro(steps=[])),
                ("65 steps", macro(steps=too_many)), ("steps a dict", macro(steps={"0": {"do": "text", "text": "t"}})),
                ("name a number", macro(name=5)), ("name null", macro(name=None)), ("name a list", macro(name=["n"])),
                ("repeat 1", macro(repeat=1)), ("repeat text", macro(repeat="true")), ("repeat null", macro(repeat=None)),
                ("stop 1", macro(stop_on_press=1)), ("stop text", macro(stop_on_press="yes")),
                ("stop null", macro(stop_on_press=None))):
            with self.subTest(name=name):
                refused(self, document(value))
        self.assertEqual(config.import_macro(document(macro(steps=too_many[1:]))), macro(steps=too_many[1:]))

    def test_malformed_steps_are_refused(self):
        for step in (
                {"do": "jump"}, {"do": "text"}, {"do": "text", "text": "t", "extra": 1}, {"do": "delay", "delay": 250},
                {"do": "delay", "ms": 250, "text": "t"}, "text", None, [], 5, {"text": "t"},
                {"do": [], "text": "t"}, {"do": {}, "text": "t"}, {"do": True}, {"do": None},
                {"do": "keys", "keys": ["KEY_NOPE"]}, {"do": "keys", "keys": "KEY_A"}, {"do": "keys", "keys": [[]]},
                {"do": "keys", "keys": [{}]}, {"do": "keys", "keys": ["KEY_A"] * (config.MAX_COMBO + 1)},
                {"do": "text", "text": 5}, {"do": "text", "text": ["t"]}, {"do": "text", "text": "t" * (config.MAX_KEY_TEXT + 1)},
                {"do": "click", "button": "BTN_NOPE"}, {"do": "click", "button": []}, {"do": "click", "button": {}},
                {"do": "click", "button": True}, {"do": "haptic", "waveform": 999}, {"do": "haptic", "waveform": []},
                {"do": "haptic", "waveform": {}}, {"do": "command", "command": 5}, {"do": "command", "command": {}},
                {"do": "command", "command": "c" * (config.MAX_KEY_TEXT + 1)}, {"do": "delay", "ms": {}},
                {"do": "delay", "ms": "250"}, {"do": "action"}):
            with self.subTest(step=step):
                refused(self, document(macro(steps=[{"do": "text", "text": "fine"}, step])))

    def test_numbers_are_whole_finite_and_in_range(self):
        for literal in ("true", "false", "null", "250.0", "2.5e2", "NaN", "Infinity", "-Infinity", "1e999", "-0.0",
                        "1" + "0" * 40, "9" * 5000, "-1", str(actions.MAX_DELAY_MS + 1), "[250]", '"250"'):
            with self.subTest(literal=literal):
                refused(self, with_delay(literal))
        for value in (0, 1, actions.MAX_DELAY_MS):
            self.assertEqual(config.import_macro(with_delay(str(value)))["steps"], [{"do": "delay", "ms": value}])
        for literal in ("7.0", "NaN", "true", "-1", "1e1"):
            with self.subTest(waveform=literal):
                refused(self, document(macro(steps=[{"do": "haptic", "waveform": 0}])).replace(
                    b'"waveform": 0', b'"waveform": ' + literal.encode()))
        for literal in ("1.0", "NaN", "1e0"):
            with self.subTest(version=literal):
                refused(self, document(macro()).replace(b'"version": 1', b'"version": ' + literal.encode()))

    def test_step_actions_are_only_what_a_step_may_do(self):
        others = {p.action.get("type") for p in actions.PRESETS} - set(actions.STEP_ACTIONS)
        self.assertTrue({"host", "ring", "none", "default", "gestures", "flow", "wheel_mode"} <= others)
        for action in (
                *(p.action for p in actions.PRESETS if p.action.get("type") in others), macro(),
                {"type": "keys", "keys": ["KEY_A"], "extra": 1}, {"type": "command", "command": NEVER, "name": "n"},
                {"type": "script", "script": NEVER, "name": "s", "interpreter": "sh"},
                {"type": "desktop", "do": "overview", "how": "kde"}, {"type": "button", "button": "BTN_LEFT", "x": 0},
                {"type": "hook", "name": "h", "args": []}, {"type": "shortcut", "component": "kwin", "name": "n", "x": 1},
                {"type": "command"}, {"type": "shortcut", "component": "kwin"}, {"type": "keys", "keys": ["KEY_NOPE"]},
                {"type": "keys", "keys": []}, {"type": "desktop", "do": "nowhere"}, {"type": []}, {"type": {}},
                {"type": None}, {}, "keys", [], None, 5):
            with self.subTest(action=action):
                refused(self, document(macro(steps=[{"do": "action", "action": action}])))
        for step in PRESET_STEPS:
            self.assertEqual(config.import_macro(document(macro(steps=[step]))), macro(steps=[step]))

    def test_text_that_hides_what_runs_is_refused(self):
        shell = lambda text: [  # noqa: E731  every place a shell runs text from
            {"do": "command", "command": text},
            {"do": "action", "action": {"type": "command", "command": text}},
            {"do": "action", "action": {"type": "script", "script": text}}]
        for name, value in (
                ("surrogate name", macro(name="a\ud800b")), ("surrogate text", macro(steps=[{"do": "text", "text": "\udfff"}])),
                ("surrogate command", macro(steps=shell("a\ud83db")[:1])),
                ("surrogate nested command", macro(steps=shell("a\ud800")[1:2])),
                ("surrogate script", macro(steps=shell("\ud800")[2:])),
                ("surrogate script name", macro(steps=[{"do": "action", "action": {"type": "script", "name": "\ud800",
                                                                                     "script": NEVER}}])),
                ("surrogate hook", macro(steps=[{"do": "action", "action": {"type": "hook", "name": "\ud800"}}])),
                ("surrogate shortcut", macro(steps=[{"do": "action", "action": {"type": "shortcut", "component": "\ud800",
                                                                                  "name": "n"}}]))):
            with self.subTest(name=name):
                refused(self, document(value))
        for char in sorted(config._HIDING) + ["\x00", "\x01", "\x0b", "\x0c", "\r", "\x1b", "\x1f", "\x7f"]:
            for step in shell("a" + char + "b"):
                with self.subTest(char=hex(ord(char)), step=step["do"]):
                    refused(self, document(macro(steps=[step])))
        kept = macro(name="a\u202eb\x00", steps=[
            *shell("a\tb\nc"), {"do": "text", "text": "\u202e\x00\x1b\r"},
            {"do": "action", "action": {"type": "hook", "name": "a\u202e\x1bb"}},
            {"do": "action", "action": {"type": "script", "name": "\u202e", "script": "🙂"}}])
        self.assertEqual(config.import_macro(document(kept)), kept)
        self.assertEqual(config.import_macro(b'{"format": "mxoptions-macro", "version": 1, "macro": {"type": "macro", '
                                             b'"name": "\\ud83d\\ude00", "steps": [{"do": "text", "text": "t"}]}}')["name"],
                         "\U0001f600")  # a pair of surrogates is one character

    def test_the_tables_know_every_step_and_step_action(self):
        self.assertEqual(set(config._STEP_FIELDS), set(actions.MACRO_STEPS))
        self.assertEqual(set(config._STEP_ACTION_FIELDS), set(actions.STEP_ACTIONS))
        for kind, step in editors.STEP_DEFAULTS.items():
            if kind != "action":
                self.assertEqual(set(step), {"do", config._STEP_FIELDS[kind]})
                self.assertEqual(config.import_macro(document(macro(steps=[step]))), macro(steps=[step]))
        for preset in actions.PRESETS:
            if preset.action.get("type") in actions.STEP_ACTIONS:
                self.assertLessEqual(set(preset.action), config._STEP_ACTION_FIELDS[preset.action["type"]])

    def test_the_limit_counts_bytes_and_goes_both_ways(self):
        wide = [{"do": "text", "text": "é" * 32768} for _step in range(15)]  # 2 bytes each: 64 KB a step

        def sized(pad):
            return macro(steps=[*wide, {"do": "text", "text": "a" * pad}])

        pad = LIMIT - len(config.export_macro(sized(0)))
        self.assertTrue(0 < pad <= config.MAX_KEY_TEXT)
        self.assertTrue(config.keyboard_action(sized(pad + 1)))  # a macro the settings take, only too big for a file
        raw = config.export_macro(sized(pad))
        self.assertEqual(len(raw), LIMIT)
        self.assertLess(len(raw.decode("utf-8")), LIMIT)
        self.assertEqual(config.import_macro(raw), sized(pad))
        with self.assertRaises(ValueError) as caught:
            config.export_macro(sized(pad + 1))
        self.assertEqual(str(caught.exception), "It is 1025 KB, more than the 1024 KB a macro file may have.")
        message = refused(self, raw[:-1] + b" }", "a macro file may have")  # valid JSON, one byte over
        self.assertIn("1025 KB", message)

    def test_export_refuses_what_import_would_not_take(self):
        no_step, cannot = "The macro has no complete step yet.", "The macro holds something a macro file cannot."
        for value, says in (
                (macro(steps=[]), no_step), ({"type": "macro"}, no_step),
                (macro(name="a\ud800"), cannot), (macro(name="n" * 40000), cannot), (macro(name=" n"), cannot),
                (macro(steps=[{"do": "command", "command": NEVER + " "}]), cannot),
                (macro(steps=[{"do": "command", "command": NEVER + "\u202e"}]), cannot),
                (macro(steps=[{"do": "action", "action": {"type": "host", "host": 1}}]), cannot),
                (macro(extra=1), cannot)):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as caught:
                    config.export_macro(value)
                self.assertIs(type(caught.exception), ValueError)
                self.assertEqual(str(caught.exception), says)


class Reading(Folder):
    def test_a_file_is_read_with_the_macro_texts(self):
        path = os.path.join(self.outside, "macro.json")
        exact = document(macro())
        exact += b" " * (LIMIT - len(exact))
        with open(path, "wb") as f:
            f.write(exact)
        self.assertEqual(config.read_settings(path, macro=True), exact)
        self.assertEqual(config.import_macro(exact), macro())
        real = os.fstat

        def small(fd):  # a file that says it is small and then is not, as a growing one would
            info = real(fd)
            return os.stat_result((info.st_mode, *info[1:6], 10, *info[7:]))

        with open(path, "ab") as f:
            f.write(b" ")
        sparse = os.path.join(self.outside, "sparse.json")
        with open(sparse, "wb"):
            pass
        os.truncate(sparse, 64 << 20)
        fifo = os.path.join(self.outside, "fifo.json")
        os.mkfifo(fifo)
        for is_macro, kind in ((True, "macro"), (False, "settings")):  # without macro: the texts of before
            with self.subTest(macro=is_macro):
                read = (lambda p: config.read_settings(p, macro=True)) if is_macro else config.read_settings
                with self.assertRaises(ValueError) as caught:
                    read(sparse)
                self.assertEqual(str(caught.exception), f"It is 65536 KB, more than the 1024 KB a {kind} file may have.")
                with mock.patch.object(config.os, "fstat", small), self.assertRaises(ValueError) as caught:
                    read(path)
                self.assertEqual(str(caught.exception), f"It is 1025 KB, more than the 1024 KB a {kind} file may have.")
                with self.assertRaises(ValueError) as caught:
                    read(fifo)  # not waited on
                self.assertEqual(str(caught.exception), f"It is not a {kind} file of MX Options.")
                with self.assertRaises(FileNotFoundError):
                    read(os.path.join(self.outside, "missing.json"))
        self.assertEqual(config._too_big(LIMIT + 1), "It is 1025 KB, more than the 1024 KB a settings file may have.")


def window(waveforms=None):
    made = QWidget()
    made.store = mock.Mock(state={"caps": None if waveforms is None else {"waveforms": waveforms}})
    return made


class Dialog(Folder):
    """The macro editor with its Export… and Import… buttons, every dialog a stand-in."""

    def setUp(self):
        super().setUp()
        self.ran = []
        self.started = []
        for target, name in ((editors, "test_runner"),):
            patcher = mock.patch.object(target, name, self.ran.append)  # Test shown, never pressed
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, name in ((subprocess, "Popen"), (subprocess, "run"), (os, "system"), (os, "execvp"),
                             (os, "posix_spawn")):  # actions.subprocess is this module too
            patcher = mock.patch.object(target, name, side_effect=lambda *a, **k: self.started.append(a) or 1 / 0)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(lambda: self.assertEqual((self.ran, self.started), ([], [])))
        self.told = []
        self.config_before = (self.read(self.config_path), sorted(os.listdir(self.folder)))
        self.addCleanup(lambda: self.assertEqual((self.read(self.config_path), sorted(os.listdir(self.folder))),
                                                 self.config_before))

    def dialog(self, action=None, waveforms=None):
        parent = window(waveforms)
        self.addCleanup(parent.deleteLater)
        made = editors.MacroDialog(copy.deepcopy(action), parent)
        self.addCleanup(made.deleteLater)
        return made

    def file(self, raw, name="in.json"):
        return super().file(raw, name)

    def messages(self):
        return mock.patch.multiple(editors.QMessageBox, warning=lambda *a: self.told.append(("warning", a[2])),
                                   information=lambda *a: self.told.append(("information", a[2])))

    def importing(self, made, path):
        self.told.clear()
        with mock.patch.object(editors.QFileDialog, "getOpenFileName", return_value=(path, "")) as asked, \
                self.messages():
            made.import_button.click()
        if path:
            self.assertEqual(asked.call_args.args[1:], ("Import macro", "", "Macro (*.json)"))
        return list(self.told)

    def exporting(self, made, path):
        self.told.clear()
        with mock.patch.object(editors.QFileDialog, "getSaveFileName", return_value=(path, "")) as asked, \
                self.messages():
            made.export_button.click()
        return asked, list(self.told)

    def state(self, made):
        """Everything a refused or cancelled import must leave as it was."""
        return (made.action(), list(made._rows), [row.step() for row in made._rows], made.name.text(),
                made.repeat.isChecked(), made.again.isChecked(), made.imported.isHidden(), made.imported.text(),
                made.empty.isHidden(), made.result())

    def bottom_row(self, made):
        row = made.body.itemAt(made.body.count() - 1).layout()
        return [row.itemAt(i).widget() or "stretch" for i in range(row.count())]

    def test_the_buttons_are_there_with_names_and_only_in_the_macro_editor(self):
        for language, labels, names in (
                ("en", ("Test in 3 s", "Export…", "Import…", "Cancel", "Save"), ("Export macro", "Import macro")),
                ("uk", ("Тест через 3 с", "Експортувати…", "Імпортувати…", "Скасувати", "Зберегти"),
                 ("Експорт макросу", "Імпорт макросу"))):
            with self.subTest(language=language):
                self.addCleanup(i18n.setup, "en")
                i18n.setup(language)
                made = self.dialog()
                row = self.bottom_row(made)
                self.assertEqual(row[3], "stretch")
                del row[3]
                self.assertEqual(tuple(button.text() for button in row), labels)
                self.assertEqual(row[1:3], [made.export_button, made.import_button])
                self.assertEqual((made.export_button.accessibleName(), made.import_button.accessibleName()), names)
                self.assertFalse(made.test.isHidden())
                self.assertTrue(made.imported.isHidden())
                script = editors.ScriptDialog()
                self.addCleanup(script.deleteLater)
                run_now = "Запустити зараз" if language == "uk" else "Run now"
                self.assertEqual([b.text() for b in script.findChildren(QPushButton)], [run_now, *labels[3:]])
                self.assertFalse(hasattr(script, "export_button"))

    def test_an_import_fills_the_editor_and_runs_nothing(self):
        made = self.dialog({"type": "macro", "name": "old", "repeat": False,
                            "steps": [{"do": "text", "text": "old"}, {"do": "delay", "ms": 5}]})
        self.assertEqual(self.importing(made, self.file(config.export_macro(FULL))), [])
        self.assertEqual(made.action(), FULL)
        self.assertEqual(len(made._rows), actions.MAX_MACRO_STEPS)
        self.assertFalse(made.imported.isHidden())
        self.assertEqual(made.imported.text(), "Imported from in.json. A macro file can run commands: check each step "
                                               "before you save.")
        self.assertNotEqual(made.result(), QDialog.DialogCode.Accepted)
        self.assertFalse(made.test.isHidden())
        # What a file may leave out comes as the editor starts it; a name given twice shows its last value.
        self.importing(made, self.file(b'{"format": "mxoptions-macro", "version": 1, "macro": {"type": "macro", '
                                       b'"steps": [{"do": "keys", "keys": ["KEY_A"]}]}}'))
        self.assertEqual(made.action(), {"type": "macro", "name": "", "repeat": False,
                                         "steps": [{"do": "keys", "keys": ["KEY_A"]}]})
        self.importing(made, self.file(document(macro(repeat=False, stop_on_press=False))))
        self.assertEqual(made.action(), macro())
        self.importing(made, self.file(b'{"format": "mxoptions-macro", "version": 1, "macro": {"type": "macro", '
                                       b'"name": "first", "name": "last", "steps": [{"do": "text", "text": "t"}], '
                                       b'"steps": [{"do": "command", "command": "' + NEVER.encode() + b'"}]}}'))
        self.assertEqual(made.action(), macro(name="last", steps=[{"do": "command", "command": NEVER}]))
        self.assertEqual(self.told, [])

    def test_a_cancelled_or_refused_import_leaves_the_editor_as_it_was(self):
        made = self.dialog(macro(name="kept", steps=[{"do": "command", "command": NEVER}], stop_on_press=True))
        for already in (False, True):
            if already:  # a hint from an earlier import stays as it was too
                self.importing(made, self.file(document(macro(name="first")), "first.json"))
            before = self.state(made)
            self.assertEqual(self.importing(made, ""), [])
            self.assertEqual(self.state(made), before)
            fifo = os.path.join(self.outside, f"fifo{already}.json")
            os.mkfifo(fifo)
            text = "in.json cannot be imported. It is not a macro file of MX Options."
            for name, raw, says in (
                    ("in.json", b"[]", text), ("in.json", b"\xff", text),
                    ("in.json", document(macro(), version=2),
                     "in.json cannot be imported. It was made by a newer version of MX Options."),
                    ("in.json", document(macro(extra=1)), text),
                    ("in.json", document(macro(steps=[{"do": "text", "text": "t"}, {"do": "jump"}])), text),
                    ("in.json", document(macro(steps=[{"do": "text", "text": "t"}, {"do": "command", "command": "a\x1bb"}])),
                     text),
                    ("in.json", document(macro(steps=[{"do": "text", "text": "\ud800"}])), text),
                    ("in.json", with_delay("NaN"), text),
                    ("big.json", document(macro()) + b" " * LIMIT,
                     "big.json cannot be imported. It is 1025 KB, more than the 1024 KB a macro file may have."),
                    (fifo, None, f"fifo{already}.json cannot be imported. It is not a macro file of MX Options."),
                    (os.path.join(self.outside, "missing.json"), None,
                     "missing.json could not be read (No such file or directory).")):
                with self.subTest(already=already, says=says, raw=raw and raw[:80]):
                    path = self.file(raw, name) if raw is not None else name
                    self.assertEqual(self.importing(made, path), [("warning", says)])
                    self.assertEqual(self.state(made), before)
                    if raw is not None:
                        self.assertEqual(self.read(path), raw)
            if not already:
                self.assertTrue(made.imported.isHidden())

    def test_export_writes_what_the_editor_shows(self):
        made = self.dialog(macro(steps=[{"do": "command", "command": NEVER}]))
        made.name.setText("  Edited, not saved  ")
        made.again.setChecked(True)
        made.repeat.setChecked(True)
        made.add_step({"do": "text", "text": "added"})
        path = os.path.join(self.outside, "out.json")
        with open(path, "wb") as f:
            f.write(b"an older file")
        asked, told = self.exporting(made, path)
        shown = {"type": "macro", "name": "Edited, not saved", "repeat": True,
                 "steps": [{"do": "command", "command": NEVER}, {"do": "text", "text": "added"}], "stop_on_press": True}
        self.assertEqual(made.action(), shown)
        self.assertEqual(self.read(path), config.export_macro(shown))
        self.assertEqual(config.import_macro(self.read(path)), shown)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(told, [("information", "The macro is in out.json. Everything it types or runs is in the file "
                                                "as written.")])
        self.assertEqual(asked.call_args.args[1], "Export macro")
        self.assertEqual(os.path.basename(asked.call_args.args[2]), "mxoptions-macro.json")
        self.assertEqual(asked.call_args.args[3], "Macro (*.json)")
        self.assertEqual(sorted(os.listdir(self.outside)), ["out.json"])
        self.assertNotEqual(made.result(), QDialog.DialogCode.Accepted)
        os.unlink(path)
        asked, told = self.exporting(made, "")  # the save dialog cancelled
        self.assertEqual((told, os.listdir(self.outside)), ([], []))
        for action, says in (({}, "Nothing was exported. The macro has no complete step yet."),
                             (macro(steps=[{"do": "action", "action": {"type": "command", "command": "a\u202eb"}}]),
                              "Nothing was exported. The macro holds something a macro file cannot.")):
            asked, told = self.exporting(self.dialog(action), path)
            self.assertFalse(asked.called)  # told before a file is asked for
            self.assertEqual((told, os.listdir(self.outside)), ([("warning", says)], []))

    def test_a_failed_export_keeps_the_file_that_was_there(self):
        made = self.dialog(macro())
        path = os.path.join(self.outside, "out.json")
        for stage, reason in (("open", "No space left on device (synthetic)"), ("write", "Bad file descriptor"),
                              ("fsync", "Input/output error (synthetic)"), ("replace", "Invalid cross-device link (synthetic)"),
                              ("dirsync", "Input/output error (synthetic)")):
            with self.subTest(stage=stage):
                with open(path, "wb") as f:
                    f.write(b"an older file")
                with fault(stage, "out.json"):
                    _asked, told = self.exporting(made, path)
                self.assertEqual(sorted(os.listdir(self.outside)), ["out.json"])  # nothing left beside it
                if stage == "dirsync":  # in its place, perhaps not for good
                    self.assertEqual(self.read(path), config.export_macro(macro()))
                    self.assertEqual(told, [("warning", "The macro was written, but the system could not confirm that "
                                                        f"it is safely on the disk ({reason}).")])
                else:
                    self.assertEqual(self.read(path), b"an older file")
                    self.assertEqual(told, [("warning", f"The macro could not be written ({reason}).")])
        _asked, told = self.exporting(made, os.path.join(self.outside, "gone", "out.json"))
        self.assertEqual(told, [("warning", "The macro could not be written (No such file or directory).")])

    def test_the_picker_keeps_an_import_only_when_saved(self):
        current = macro(name="current", steps=[{"do": "text", "text": "current"}])
        path = self.file(config.export_macro(FULL))
        for saved in (1, 0):
            with self.subTest(saved=saved):
                listed = ActionList()
                self.addCleanup(listed.deleteLater)
                listed.set_current(copy.deepcopy(current))
                picked = []
                listed.picked.connect(picked.append)
                opened = []

                def run(dialog):
                    opened.append(dialog.action())
                    self.assertEqual(self.importing(dialog, path), [])
                    return saved

                with mock.patch.object(editors.MacroDialog, "exec", run):
                    listed.macro_row.click()
                self.assertEqual(opened, [current])  # the editor opened on the current macro
                self.assertEqual(picked, [FULL] if saved else [])
                self.assertEqual(listed._current, current)
        self.assertTrue(config.keyboard_action(FULL))
        self.assertTrue(config.profile_button_action(FULL))

    def test_only_what_the_editor_shows_as_it_is_is_imported(self):
        """The file holds no name or command the editor would cut or trim: refused whole, before any change."""
        steps = [{"do": "text", "text": "t"}]
        astral = "🙂" * 16383 + "n"  # 32767 UTF-16 units in 16384 characters
        made = self.dialog(macro(name="kept"))
        for name in ("n" * config.MACRO_NAME_MAX, astral, "a b\tc", ""):
            with self.subTest(name=len(name)):
                self.assertEqual(self.importing(made, self.file(document(macro(name=name, steps=steps)))), [])
                self.assertEqual(made.action(), macro(name=name, steps=steps))
        nested = {"do": "action", "action": {"type": "command", "command": " printf '%s' x\\ \n"}}  # as written
        self.importing(made, self.file(document(macro(name="kept", steps=[nested]))))
        self.assertEqual(made.action(), macro(name="kept", steps=[nested]))
        before = self.state(made)
        for name, value in (
                ("one unit over", macro(name="n" * (config.MACRO_NAME_MAX + 1))), ("40000", macro(name="n" * 40000)),
                ("astral over", macro(name="🙂" * 16384)), ("astral just over", macro(name="nn" + "🙂" * 16383)),
                ("space before", macro(name=" kept")), ("space after", macro(name="kept ")),
                ("tab before", macro(name="\tkept")), ("newline after", macro(name="kept\n")),
                ("escaped last blank", macro(steps=[{"do": "command", "command": "printf '%s' x\\ "}])),
                ("command space before", macro(steps=[{"do": "command", "command": " " + NEVER}])),
                ("command space after", macro(steps=[{"do": "command", "command": NEVER + " "}])),
                ("command newline before", macro(steps=[{"do": "command", "command": "\n" + NEVER}])),
                ("command newline after", macro(steps=[{"do": "command", "command": NEVER + "\n"}])),
                ("command tab after", macro(steps=[{"do": "command", "command": NEVER + "\t"}])),
                ("command blank only", macro(steps=[{"do": "command", "command": " "}]))):
            with self.subTest(name=name):
                self.assertTrue(config.keyboard_action(value))  # the settings would take it: only the file does not
                path = self.file(document(value))
                raw = self.read(path)
                self.assertEqual(self.importing(made, path),
                                 [("warning", "in.json cannot be imported. It is not a macro file of MX Options.")])
                self.assertEqual(self.state(made), before)
                self.assertEqual(self.read(path), raw)
        self.importing(made, self.file(document(macro(name="next"))))  # a valid file after them still goes in
        self.assertEqual(made.action(), macro(name="next"))

    def test_imported_clicks_show_the_target_or_leave_the_editor_unchanged(self):
        made = self.dialog(macro(name="kept"))
        for button in ("BTN_LEFT", "BTN_RIGHT", "BTN_MIDDLE"):
            with self.subTest(button=button):
                value = macro(steps=[{"do": "click", "button": button}])
                self.assertEqual(self.importing(made, self.file(document(value))), [])
                self.assertEqual(made.action(), value)
                choices = made._rows[0].editor._buttons
                self.assertEqual([key for key, choice in choices.items() if choice.isChecked()], [button])
        before = self.state(made)
        for button in ("BTN_SIDE", "BTN_EXTRA"):
            with self.subTest(button=button):
                value = macro(steps=[{"do": "text", "text": "must not replace rows"},
                                     {"do": "click", "button": button}])
                self.assertTrue(config.keyboard_action(value))
                path = self.file(document(value))
                raw = self.read(path)
                self.assertEqual(self.importing(made, path),
                                 [("warning", "in.json cannot be imported. It is not a macro file of MX Options.")])
                self.assertEqual(self.state(made), before)
                self.assertEqual(self.read(path), raw)
                with self.assertRaisesRegex(ValueError, "The macro holds something a macro file cannot."):
                    config.export_macro(value)
        value = macro(steps=[{"do": "click", "button": "BTN_LEFT"}])
        self.assertEqual(self.importing(made, self.file(document(value))), [])
        self.assertEqual(made.action(), value)


if __name__ == "__main__":
    unittest.main()
