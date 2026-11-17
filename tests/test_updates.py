"""The app's own update notice: versions, the reply GitHub gives and what is refused, how this copy would be updated,
the state kept between runs, the daily check and the card on Device & app. No network: every request goes through
a patched opener, and a socket that tries to connect fails the test."""

import glob
import io
import json
import os
import socket
import stat
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import tests.support  # noqa: F401,E402
from mxoptions import __version__, updates  # noqa: E402
from mxoptions.ui import updates as updates_ui  # noqa: E402
from tests.test_keyboard_tabs import gone, make_store, settle, window  # noqa: E402

APP = QApplication.instance() or QApplication([])
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = "https://github.com/squarrelmingji/mxoptions/releases/tag/v9.0.0"


def reply(body, url=updates.LATEST_URL):
    class Reply(io.BytesIO):
        def geturl(self):
            return url

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()
    return Reply(body if isinstance(body, bytes) else json.dumps(body).encode())


def opener(answer):
    """build_opener stand-in whose open() returns ``answer`` (a reply) or raises it (an exception)."""
    def build(*_handlers):
        class Opener:
            def open(self, request, timeout=None):
                if isinstance(answer, BaseException):
                    raise answer
                return answer
        return Opener()
    return build


class NoNetwork(unittest.TestCase):
    def setUp(self):
        def refuse(*_args, **_kwargs):
            raise AssertionError("a test opened a network connection")
        patcher = mock.patch.object(socket.socket, "connect", refuse)
        patcher.start()
        self.addCleanup(patcher.stop)


class Versions(NoNetwork):
    def test_order(self):
        for newer, older in (("0.10.1", "0.10.0"), ("0.10.0.post2", "0.10.0.post1"), ("0.10.0.post1", "0.10.0"),
                             ("0.10.0", "0.10.0rc1"), ("0.10.0rc1", "0.10.0b3"), ("0.10.0b3", "0.10.0a9"),
                             ("1.0.0", "0.99.99"), ("v0.10.1", "0.10.0")):
            with self.subTest(newer=newer, older=older):
                self.assertTrue(updates.is_newer(newer, older))
                self.assertFalse(updates.is_newer(older, newer))
        self.assertFalse(updates.is_newer("0.10.0", "0.10.0"))
        for bad in ("", "1.0", "1.0.0.0", "1.0.0-beta.1", "latest", None, 3, "1.0.0post1"):
            self.assertIsNone(updates.parse_version(bad), bad)
            self.assertFalse(updates.is_newer(bad, "0.0.1"))

    def test_debian_version_is_the_packaging_rule(self):
        sys.path.insert(0, os.path.join(ROOT, "ops", "ship"))
        self.addCleanup(sys.path.remove, os.path.join(ROOT, "ops", "ship"))
        from tools import make_deb
        import release
        for version in ("0.10.0", "0.10.0.post2", "0.10.1b3", "0.10.1rc1", "1.2.3a4"):
            self.assertEqual(updates.debian_version(version), make_deb.debian_version(version), version)
            self.assertEqual(updates.debian_version(version), release.debian_version(version), version)


class Fetch(NoNetwork):
    def fetch(self, answer):
        with mock.patch.object(updates.urllib.request, "build_opener", opener(answer)):
            return updates.fetch_latest()

    def test_a_good_reply(self):
        got = self.fetch(reply({"tag_name": "v9.0.0", "html_url": PAGE, "body": "x"}))
        self.assertEqual(got, {"version": "9.0.0", "tag": "v9.0.0", "page": PAGE})

    def test_a_foreign_page_falls_back_to_the_tag_page(self):
        got = self.fetch(reply({"tag_name": "v9.0.0", "html_url": "https://evil.example/releases/1"}))
        self.assertEqual(got["page"], updates.tag_page("v9.0.0"))

    def test_no_release_yet(self):
        error = urllib.error.HTTPError(updates.LATEST_URL, 404, "Not Found", {}, None)
        with self.assertRaises(updates.NoRelease):
            self.fetch(error)

    def test_what_is_refused(self):
        cases = {
            "too large": reply(b"{" + b" " * updates.MAX_REPLY + b"}"),
            "not json": reply(b"<html>"),
            "no tag": reply({"html_url": PAGE}),
            "bad tag": reply({"tag_name": "release-9"}),
            "not an object": reply([1, 2]),
            "foreign final host": reply({"tag_name": "v9.0.0"}, url="https://evil.example/x"),
        }
        for name, answer in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                self.fetch(answer)
        with self.assertRaises(OSError):
            self.fetch(urllib.error.HTTPError(updates.LATEST_URL, 500, "x", {}, None))
        with self.assertRaises(OSError):
            self.fetch(urllib.error.URLError("offline"))

    def test_redirects_only_to_allowed_https_hosts(self):
        handler = updates._Redirects()
        request = updates.urllib.request.Request(updates.LATEST_URL)
        for url in ("http://api.github.com/x", "https://evil.example/x", "https://user@github.com/x",
                    "https://github.com:8443/x"):
            with self.subTest(url), self.assertRaises(urllib.error.URLError):
                handler.redirect_request(request, None, 302, "Found", {}, url)
        self.assertIsNotNone(handler.redirect_request(request, None, 302, "Found", {}, "https://github.com/x"))

    def test_release_pages(self):
        self.assertEqual(updates.release_page(PAGE), PAGE)
        for url in ("https://github.com/squarrelmingji/mxoptions/releases/%2e%2e/%2e%2e/%2e%2e/attacker/repo/releases/tag/v9.0.0",
                    "https://github.com/squarrelmingji/mxoptions/releases/../../../attacker/repo/releases/tag/v9.0.0",
                    "https://github.com/squarrelmingji/mxoptions/releases/tag/v9.0.0?x=1",
                    "https://github.com/squarrelmingji/mxoptions/releases/tag/v9.0.0#x",
                    "https://github.com/squarrelmingji/mxoptions/releases/tag/v9.0.0/../../x",
                    "https://github.com:bad/squarrelmingji/mxoptions/releases/tag/v9.0.0",
                    "http://github.com/squarrelmingji/mxoptions/releases/tag/v1.0.0",
                    "https://github.com/nictonus/mxoptions/releases/tag/v1.0.0",
                    "https://github.com/squarrelmingji/mxoptions/pulls", "https://a@github.com/squarrelmingji/mxoptions/releases/x",
                    "javascript:alert(1)", None, 5):
            self.assertIsNone(updates.release_page(url), url)


class HowToUpdate(NoNetwork):
    def kind(self, here, deb=False, platform="linux", frozen=False):
        with mock.patch.object(updates, "_package_dir", lambda: here), \
                mock.patch.object(updates.os.path, "isfile", lambda path: deb and path == "/var/lib/dpkg/info/mxoptions.list"), \
                mock.patch.object(updates.sys, "platform", platform), \
                mock.patch.object(updates.os.path, "expanduser", lambda _p: "/home/u"), \
                mock.patch.object(updates.os.path, "realpath", lambda p: p):
            if frozen:
                with mock.patch.object(updates.sys, "frozen", True, create=True):
                    return updates.install_kind()
            return updates.install_kind()

    def test_kinds_from_files_only(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("ran a program")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("ran a program")):
            self.assertEqual(self.kind("/usr/lib/mxoptions/mxoptions", deb=True), "deb")
            self.assertEqual(self.kind("/usr/lib/mxoptions/mxoptions", deb=False), "other")  # AUR, RPM
            self.assertEqual(self.kind("/home/u/.local/lib/mxoptions/mxoptions"), "user")
            for other in ("/usr/local/lib/mxoptions/mxoptions", "/home/u/venv/lib/python3.12/site-packages/mxoptions",
                          "/home/u/src/mxoptions/mxoptions", "/nix/store/abc-mxoptions/lib/mxoptions"):
                self.assertEqual(self.kind(other), "other", other)
            self.assertEqual(self.kind("/home/u/.local/lib/mxoptions/mxoptions", platform="win32"), "other")
            self.assertEqual(self.kind("/home/u/.local/lib/mxoptions/mxoptions", frozen=True), "other")

    def test_commands(self):
        with mock.patch.object(updates.sys, "executable", "/usr/bin/python3"):
            self.assertEqual(updates.update_commands("deb", "0.11.0b1"),
                             "sudo apt install ./mxoptions_0.11.0~b1-1_all.deb && \\\nsystemctl --user restart mxoptions.service")
            self.assertEqual(updates.update_commands("user", "0.11.0"),
                             "tar xf mxoptions-0.11.0.tar.gz && \\\ncd mxoptions-0.11.0 && \\\n"
                             "make install-user PYTHON=/usr/bin/python3 && \\\nsystemctl --user restart mxoptions.service")
        self.assertEqual(updates.update_commands("other", "0.11.0"), "")
        self.assertEqual(updates.update_commands("user", "0.11.0; rm -rf ~"), "")

    def test_an_odd_python_path_stays_out_of_the_commands(self):
        for path in ("/opt/Python Env/bin/python3", "/opt/$HOME/python3", "/opt/a'b/python3", "/opt/a\\b/python3"):
            with self.subTest(path), mock.patch.object(updates.sys, "executable", path):
                commands = updates.update_commands("user", "0.11.0")
                self.assertIn("make install-user && ", commands)
                self.assertNotIn(path, commands)
                self.assertIn(path, updates.update_text("user", "0.11.0"))

    def test_a_failed_step_stops_the_rest(self):
        bin_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: os.system(f"rm -rf '{bin_dir}'"))
        for name, code in (("tar", 1), ("sudo", 1), ("make", 0), ("systemctl", 0)):
            with open(os.path.join(bin_dir, name), "w") as f:
                f.write(f"#!/bin/sh\necho {name} >> '{bin_dir}/ran'\nexit {code}\n")
            os.chmod(os.path.join(bin_dir, name), 0o755)
        for kind in ("user", "deb"):
            with self.subTest(kind=kind), mock.patch.object(updates.sys, "executable", "/usr/bin/python3"):
                os.system(f"rm -f '{bin_dir}/ran'")
                commands = updates.update_commands(kind, "0.11.0")
                import subprocess
                subprocess.run(["/bin/sh", "-c", commands], cwd=bin_dir, env={"PATH": bin_dir}, capture_output=True)
                with open(os.path.join(bin_dir, "ran")) as f:
                    self.assertEqual(f.read().split(), ["tar" if kind == "user" else "sudo"])  # nothing after it

    def test_commands_are_the_same_in_every_language(self):
        from mxoptions import i18n
        self.addCleanup(i18n.setup, "en")
        seen = {}
        for language in ("en", "uk"):
            i18n.setup(language)
            seen[language] = (updates.update_text("deb", "0.11.0"), updates.update_commands("deb", "0.11.0"))
        self.assertNotEqual(seen["en"][0], seen["uk"][0])
        self.assertEqual(seen["en"][1], seen["uk"][1])


class State(NoNetwork):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp()
        os.chmod(self.dir, 0o700)
        self.addCleanup(lambda: os.system(f"rm -rf '{self.dir}'"))
        patcher = mock.patch.object(updates.paths, "DATA", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        updates._warned.clear()

    def path(self):
        return os.path.join(self.dir, updates.STATE_FILE)

    def write(self, data):
        with open(self.path(), "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def test_round_trip(self):
        release = {"version": "9.0.0", "tag": "v9.0.0", "page": PAGE}
        self.assertTrue(updates.save_state({"checked_at": 1000.0, "notified": "9.0.0", "release": release}))
        self.assertEqual(stat.S_IMODE(os.stat(self.path()).st_mode), 0o600)
        self.assertEqual(updates.load_state(2000.0), {"checked_at": 1000.0, "notified": "9.0.0", "release": release})

    def test_what_is_not_trusted(self):
        cases = {
            "nan": ({"checked_at": float("nan")}, "checked_at", None),
            "future": ({"checked_at": 1e12}, "checked_at", None),
            "bool": ({"checked_at": True}, "checked_at", None),
            "bad notified": ({"notified": "x; rm"}, "notified", ""),
            "foreign page": ({"release": {"version": "9.0.0", "tag": "v9.0.0", "page": "https://evil.example/"}}, "release", None),
            "tag mismatch": ({"release": {"version": "9.0.0", "tag": "v8.0.0", "page": PAGE}}, "release", None),
            "bad version": ({"release": {"version": "9", "tag": "v9", "page": PAGE}}, "release", None),
        }
        for name, (saved, key, want) in cases.items():
            with self.subTest(name):
                self.write(json.dumps(saved))
                self.assertEqual(updates.load_state(2000.0)[key], want)
        for junk in ("{", "[]", json.dumps({"other": 1})):
            self.write(junk)
            self.assertEqual(updates.load_state(), {"checked_at": None, "notified": "", "release": None})

    def test_values_that_would_raise_never_stop_the_start(self):
        for saved in ('{"checked_at": 1' + "0" * 400 + '}', '{"checked_at": 1e400}',
                      json.dumps({"release": {"version": "9.0.0", "tag": "v9.0.0", "page": "https://github.com:bad/x"}}),
                      json.dumps({"notified": "1" * 5000 + ".0.0"}),
                      json.dumps({"release": {"version": "1" * 5000 + ".0.0", "tag": "v" + "1" * 5000 + ".0.0", "page": PAGE}}),
                      "[" * 100000 + "]" * 100000):
            with self.subTest(saved=saved[:40]):
                self.write(saved[:updates.MAX_STATE])
                state = updates.load_state(2000.0)
                self.assertIsNone(state["release"])
                self.assertIsNone(state["checked_at"])

    def test_a_fifo_or_a_huge_file_is_ignored_without_blocking(self):
        os.mkfifo(self.path())
        start = time.monotonic()
        self.assertEqual(updates.load_state()["release"], None)
        self.assertLess(time.monotonic() - start, 1.0)
        os.unlink(self.path())
        self.write("{" + " " * (updates.MAX_STATE + 10) + "}")
        self.assertEqual(updates.load_state()["checked_at"], None)

    def test_links_and_shared_folders_are_not_written(self):
        elsewhere = tempfile.mkdtemp()
        self.addCleanup(lambda: os.system(f"rm -rf '{elsewhere}'"))
        os.symlink(os.path.join(elsewhere, "target.json"), self.path())  # a link where the file goes
        self.assertEqual(updates.load_state(), {"checked_at": None, "notified": "", "release": None})  # not followed
        self.assertTrue(updates.save_state({"checked_at": 1.0}))  # the link itself is replaced, never written through
        self.assertFalse(os.path.islink(self.path()))
        self.assertFalse(os.path.exists(os.path.join(elsewhere, "target.json")))
        os.unlink(self.path())
        link = self.dir + "-link"
        os.symlink(elsewhere, link)
        self.addCleanup(os.unlink, link)
        with mock.patch.object(updates.paths, "DATA", link):  # the data folder itself a link
            self.assertFalse(updates.save_state({"checked_at": 1.0}))
        os.chmod(self.dir, 0o777)  # writable by others
        self.assertFalse(updates.save_state({"checked_at": 1.0}))
        self.assertEqual(os.listdir(elsewhere), [])

    def test_without_the_file_checks_nothing_is_kept(self):
        with mock.patch.object(updates, "_can_keep", lambda: False):
            self.assertFalse(updates.save_state({"checked_at": 1.0}))
            self.assertEqual(updates.load_state()["checked_at"], None)
        self.assertFalse(os.path.exists(self.path()))


class Checks(NoNetwork):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp()
        os.chmod(self.dir, 0o700)
        self.addCleanup(lambda: os.system(f"rm -rf '{self.dir}'"))
        patcher = mock.patch.object(updates.paths, "DATA", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def checker(self, now=100000.0, answer=None, store=None):
        store = store or make_store()
        self.calls = []

        def fetch():
            self.calls.append(1)
            if isinstance(answer, BaseException):
                raise answer
            return answer or {"version": "9.0.0", "tag": "v9.0.0", "page": PAGE}
        patcher = mock.patch.object(updates, "fetch_latest", fetch)
        patcher.start()
        self.addCleanup(patcher.stop)
        checker = updates_ui.Checker(store, clock=lambda: now)
        checker.start()
        self.addCleanup(checker.deleteLater)
        self.results = []
        checker.result.connect(self.results.append)
        return checker

    def wait(self, checker):
        end = time.monotonic() + 3
        while checker._running and time.monotonic() < end:
            APP.processEvents()
            time.sleep(0.01)
        APP.processEvents()

    def test_a_check_and_what_it_leaves(self):
        checker = self.checker()
        checker.check_now()
        checker.check_now()  # one at a time
        self.wait(checker)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([r["state"] for r in self.results], ["checking", "available"])
        self.assertEqual(updates.load_state(100000.0)["checked_at"], 100000.0)

    def test_a_cut_off_reply_ends_the_check_and_the_next_one_works(self):
        import http.client
        answers = [http.client.IncompleteRead(b"{"), {"version": "9.0.0", "tag": "v9.0.0", "page": PAGE}]

        def fetch():
            answer = answers.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        checker = self.checker()
        with mock.patch.object(updates, "fetch_latest", fetch):
            checker.check_now()
            self.wait(checker)
            self.assertEqual(self.results[-1]["state"], "failed")
            checker.check_now()
            self.wait(checker)
        self.assertEqual(self.results[-1]["state"], "available")

    def test_failures_and_no_release(self):
        for answer, state in ((updates.NoRelease("none"), "none"), (OSError("offline"), "failed"), (ValueError("x"), "failed")):
            with self.subTest(state=state):
                os.system(f"rm -f '{self.dir}/{updates.STATE_FILE}'")
                checker = self.checker(answer=answer)
                checker.check_now()
                self.wait(checker)
                self.assertEqual(self.results[-1]["state"], state)
                self.assertEqual(updates.load_state(100000.0)["checked_at"], 100000.0)  # tried: noted either way

    def test_a_kept_release_is_judged_against_this_version(self):
        for version, state in (("99.0.0", "available"), (__version__, "current"), ("0.0.1", "current")):
            with self.subTest(version=version):
                updates.save_state({"checked_at": 1.0, "notified": "", "release": {
                    "version": version, "tag": "v" + version, "page": updates.tag_page("v" + version)}})
                self.assertEqual(self.checker().last()["state"], state)

    def test_schedule(self):
        cases = ((None, 60), (100000.0 - 3600, 23 * 3600), (100000.0 - 30 * 3600, 60))
        for checked, wait in cases:
            with self.subTest(checked=checked):
                updates.save_state({"checked_at": checked, "notified": "", "release": None})
                checker = self.checker()
                self.assertTrue(checker._timer.isActive())
                self.assertAlmostEqual(checker._timer.interval() / 1000, wait, delta=1)

    def test_off_means_no_automatic_check_but_check_now_works(self):
        store = make_store()
        store.cfg["app"]["update_check"] = False
        checker = self.checker(store=store)
        self.assertFalse(checker._timer.isActive())
        checker._due()
        self.assertEqual(self.calls, [])
        checker.check_now()
        self.wait(checker)
        self.assertEqual(len(self.calls), 1)


class Lifetime(NoNetwork):
    def test_a_page_s_checker_never_checks_by_itself_and_goes_with_its_store(self):
        import gc
        import threading
        from PySide6.QtCore import QEvent, qInstallMessageHandler
        store = make_store()
        checker = updates_ui.checker_for(store)
        self.assertFalse(checker._timer.isActive())  # made by a page: never checks on its own
        messages = []
        old = qInstallMessageHandler(lambda _kind, _context, text: messages.append(text))
        self.addCleanup(qInstallMessageHandler, old)
        store.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        del store, checker
        collector = threading.Thread(target=gc.collect)
        collector.start()
        collector.join()
        APP.processEvents()
        self.assertEqual([m for m in messages if "another thread" in m], [])


class Card(NoNetwork):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp()
        os.chmod(self.dir, 0o700)
        self.addCleanup(lambda: os.system(f"rm -rf '{self.dir}'"))
        patcher = mock.patch.object(updates.paths, "DATA", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def page(self, kind="user"):
        store = make_store()
        win = window(store)
        self.addCleanup(gone, win)
        win.resize(1180, 700)
        win.open_page(win.device_page)
        settle()
        patcher = mock.patch.object(updates, "install_kind", lambda: kind)
        patcher.start()
        self.addCleanup(patcher.stop)
        return win.device_page

    def test_every_state(self):
        page = self.page()
        texts = {
            "current": f"MX Options {__version__} is up to date.", "none": "No release has been published yet.",
            "failed": "Could not check for updates.", "checking": "Checking…", "unknown": "Not checked yet.",
        }
        for state, text in texts.items():
            with self.subTest(state=state):
                page._show_update({"state": state, "version": "", "page": "", "error": "boom"})
                self.assertEqual(page.update_status.text(), text)
                self.assertTrue(page.update_notes.isHidden() and page.update_download.isHidden())
                self.assertTrue(page.update_steps_row.isHidden())
        page._show_update({"state": "checking", "version": "", "page": "", "error": ""})
        self.assertFalse(page.update_now.isEnabled())
        page._show_update({"state": "failed", "version": "", "page": "", "error": "boom"})
        self.assertTrue(page.update_now.isEnabled())
        self.assertEqual(page.update_status.toolTip(), "boom")

    def test_a_newer_release(self):
        page = self.page("user")
        with mock.patch.object(updates.sys, "executable", "/usr/bin/python3"):
            page._show_update({"state": "available", "version": "9.0.0", "page": PAGE, "error": ""})
        settle()
        self.assertEqual(page.update_status.text(), f"Version 9.0.0 is available (you have {__version__}).")
        self.assertFalse(page.update_notes.isHidden() or page.update_download.isHidden() or page.update_steps_row.isHidden())
        page.update_copy.click()
        self.assertEqual(QApplication.clipboard().text(), page.update_commands.text())
        self.assertIn("make install-user PYTHON=/usr/bin/python3", QApplication.clipboard().text())
        with mock.patch("mxoptions.ui.pages.QDesktopServices.openUrl") as opened:
            page.update_notes.click()
        self.assertEqual(opened.call_args[0][0].toString(), PAGE)

    def test_a_foreign_link_is_never_opened_or_offered(self):
        page = self.page("other")
        page._show_update({"state": "available", "version": "9.0.0", "page": "https://evil.example/", "error": ""})
        self.assertTrue(page.update_notes.isHidden())
        with mock.patch("mxoptions.ui.pages.QDesktopServices.openUrl") as opened:
            page._open_release()
        opened.assert_not_called()

    def test_other_kinds_get_the_page_only(self):
        page = self.page("other")
        page._show_update({"state": "available", "version": "9.0.0", "page": PAGE, "error": ""})
        self.assertFalse(page.update_download.isHidden())
        self.assertTrue(page.update_steps_row.isHidden())
        self.assertEqual(page.update_steps.text(), "Download it from the release page.")

    def test_the_switch_is_the_setting(self):
        page = self.page()
        page.update_switch.click()
        self.assertFalse(page.store.cfg["app"]["update_check"])


class Classified(unittest.TestCase):
    def test_every_module_is_classified_for_the_hardware_gate(self):
        sys.path.insert(0, os.path.join(ROOT, "ops", "ship"))
        self.addCleanup(sys.path.remove, os.path.join(ROOT, "ops", "ship"))
        import hwgate
        with open(os.path.join(ROOT, "ops", "ship", "hardware-gates.json")) as f:
            manifest = json.load(f)
        for path in sorted(glob.glob(os.path.join(ROOT, "mxoptions", "**", "*.py"), recursive=True)):
            name = os.path.relpath(path, ROOT)
            self.assertTrue(hwgate.matches(name, manifest["paths"]) or hwgate.matches(name, manifest["ungated"]), name)


if __name__ == "__main__":
    unittest.main()
