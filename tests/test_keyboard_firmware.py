"""What the keyboard says of itself: its firmware version (Logitech's x0003 DeviceInformation, getFwInfo) read after
it is found, without holding up its keys, and the settings it keeps itself, from the features it lists. Devices,
versions and ids are synthetic (tests/support.py)."""

import copy
import json
import logging
import os
import queue
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.support import Clock, FakeDevice  # noqa: E402
from tests.test_keyboard import COPY, MODEL, UNIT_A, UNIT_B, Rig, diverts, identity, keyboard_device, mouse_device  # noqa: E402
from mxoptions import device, hidpp, keyboard  # noqa: E402


def entity(kind=0, prefix=b"SYN", number=0x71, revision=0x03, build=b"\x00\x21", active=1):
    """A synthetic getFwInfo reply: type, prefix, number, revision, build (packed BCD), active bit, then transport
    PID and extra bytes, made up."""
    return bytes([kind]) + prefix + bytes([number, revision]) + build + bytes([active, 0x0e, 0x0f, 0, 0, 0, 0, 0])


MAIN = entity()                                   # "71.3.21"
BOOT = entity(kind=1, number=0x05, active=0)
OTHER = entity(number=0x12, revision=0x10, build=b"\x01\x02")  # "12.10.102"


class Answers:
    """A transport that answers a Mouse's requests from a table: (feature index, function) -> payload or exception."""

    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def request(self, devidx, index, function, params=b"", timeout=1.5, no_reply=False):
        self.asked.append((index, function, bytes(params)))
        answer = self.answers[(index, function)]
        answer = answer(params) if callable(answer) else answer
        if isinstance(answer, Exception):
            raise answer
        return answer


def with_info(entities, fn1, fn0=None):
    """A device listing DEVICE_INFO at index 3 that said it has ``entities`` (an int), or let identity() read that
    from ``fn0``; getFwInfo answered by ``fn1``."""
    dev = device.Mouse(Answers({(3, 0): fn0, (3, 1): fn1}), 1)
    dev.features = {device.ROOT: 0, device.DEVICE_INFO: 3}
    if fn0 is None:
        dev.firmware_entities = entities
    else:
        dev.identity()
    return dev


class Version(unittest.TestCase):
    def test_the_active_main_application_gives_the_version(self):
        for entities, expected in (([MAIN], "71.3.21"), ([BOOT, MAIN], "71.3.21"),
                                   ([entity(active=0), OTHER], "12.10.102"), ([entity(build=b"\x99\x99")], "71.3.9999")):
            with self.subTest(expected=expected):
                dev = with_info(len(entities), lambda params, e=entities: e[params[0]])
                self.assertEqual(dev.get_firmware(), expected)

    def test_anything_else_is_no_version(self):
        cases = {
            "bootloader only": [BOOT], "hardware": [entity(kind=2)], "not active": [entity(active=0)],
            "not packed BCD": [entity(number=0x7A)], "build not BCD": [entity(build=b"\x00\x2f")],
            "prefix not ASCII": [entity(prefix=b"S\x01N")], "short": [MAIN[:8]], "none": [],
        }
        for name, entities in cases.items():
            with self.subTest(name):
                dev = with_info(len(entities), lambda params, e=entities: e[params[0]])
                self.assertIsNone(dev.get_firmware())
        for name, fn1 in (("entity refused", hidpp.HidppFeatureError("synthetic")),
                          ("no answer", hidpp.HidppTimeout("synthetic"))):
            with self.subTest(name):
                self.assertIsNone(with_info(1, fn1).get_firmware())
        for name, fn0 in (("not said", hidpp.HidppError("synthetic")), ("empty", b"")):
            with self.subTest(name):
                dev = with_info(None, MAIN, fn0)
                self.assertIsNone(dev.get_firmware())
                self.assertEqual([asked[1] for asked in dev.transport.asked], [0])  # no entity asked about

    def test_the_count_comes_from_the_one_answer_that_says_which_unit_it_is(self):
        dev = with_info(None, MAIN, bytes([1]) + UNIT_A + bytes(2) + MODEL)
        self.assertEqual(dev.identity(), identity(UNIT_A))
        self.assertEqual(dev.get_firmware(), "71.3.21")
        self.assertEqual([asked[:2] for asked in dev.transport.asked], [(3, 0), (3, 0), (3, 1)])
        self.assertFalse(any(UNIT_A[:2] in bytes(value) for value in vars(dev).values()
                             if isinstance(value, (bytes, bytearray))))

    def test_without_the_feature_nothing_is_asked(self):
        dev = with_info(1, MAIN)
        dev.features = {device.ROOT: 0}
        self.assertIsNone(dev.get_firmware())
        self.assertEqual(dev.transport.asked, [])

    def test_at_most_eight_entities_are_asked_about(self):
        dev = with_info(200, BOOT)
        self.assertIsNone(dev.get_firmware())
        self.assertEqual(dev.transport.asked, [(3, 1, bytes([i])) for i in range(8)])

    def test_being_called_off_or_closed_is_passed_on(self):
        for error in (hidpp.HidppCancelled("synthetic"), hidpp.HidppClosed("synthetic")):
            with self.subTest(type(error).__name__):
                with self.assertRaises(type(error)):
                    with_info(1, error).get_firmware()
                with self.assertRaises(type(error)):
                    with_info(None, MAIN, error)


class Settings(unittest.TestCase):
    def test_named_from_the_features_listed_in_order(self):
        def listing(*features):
            dev = device.Mouse(None, 1)
            dev.features = {feature: index for index, feature in enumerate((device.ROOT,) + features)}
            return device.keyboard_settings(dev)
        self.assertEqual(listing(), [])
        self.assertEqual(listing(device.DEVICE_NAME, device.REPROG_CONTROLS_V4), [])
        for name, features in device.KEYBOARD_SETTINGS:
            for feature in features:
                with self.subTest(feature=hex(feature)):
                    self.assertEqual(listing(feature), [name])
        self.assertEqual(listing(device.LOCK_KEY_STATE, device.DISABLE_KEYS_BY_USAGE, device.INTERNATIONAL_LAYOUTS,
                                 device.ILLUMINATION, device.FN_INVERSION_HOSTS),
                         ["fn", "backlight", "platform", "disable", "locks"])


def fw_packets(rig, dev, devidx=1, function=1):
    return rig.port.sent(devidx, dev.index(device.DEVICE_INFO), function)


def until_firmware(rig, timeout=5.0):
    """Run the engine's side until the keyboard's firmware is being read."""
    rig.engine._scan()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        task = rig.kb._task
        if task is not None and task.__name__ == "_read_firmware":
            return
        try:
            rig.engine._handle(rig.engine._queue.get(timeout=0.05))
        except queue.Empty:
            pass
    raise AssertionError("the firmware was never read")


class Reading(unittest.TestCase):
    def test_read_after_the_keys_and_shown_with_what_the_keyboard_has(self):
        kb = keyboard_device(entities=[BOOT, MAIN], extra=[device.BACKLIGHT_2, device.FN_INVERSION])
        rig = Rig(self, {1: kb, 2: mouse_device()}, keys={"0x0f01": COPY}).start()
        state = rig.state()
        self.assertEqual(state["firmware"], "71.3.21")
        self.assertEqual(state["settings"], ["fn", "backlight"])
        self.assertTrue(rig.key(0x0F01)["active"])
        packets = rig.port.sent(1)
        divert = max(i for i, p in enumerate(packets) if p[2] == kb.index(device.REPROG_CONTROLS_V4) and p[3] >> 4 == 3)
        first = min(i for i, p in enumerate(packets) if p[2] == kb.index(device.DEVICE_INFO) and p[3] >> 4 == 1)
        self.assertLess(divert, first)  # the keys first
        self.assertEqual(len(fw_packets(rig, kb)), 2)  # up to the first that is the version
        self.assertEqual(len(fw_packets(rig, kb, function=0)), 1)  # which unit it is, and how many entities: once
        self.assertEqual(rig.states[-1]["firmware"], "71.3.21")  # published when it came

    def test_a_reply_that_is_not_one_leaves_the_keyboard_working(self):
        kb = keyboard_device(entities=[MAIN])
        kb.failing[(device.DEVICE_INFO, 1)] = 4
        rig = Rig(self, {1: kb, 2: mouse_device()}, keys={"0x0f01": COPY}).start()
        self.assertTrue(rig.state()["connected"])
        self.assertIsNone(rig.state()["firmware"])
        self.assertEqual(rig.kb._identity, identity(UNIT_A))
        rig.keys_down(1, kb, 0x0F01)
        self.assertEqual(rig.input.sent[-2:], [("KEY_LEFTCTRL", True), ("KEY_C", True)])

    def test_running_out_of_time_leaves_the_keyboard_working_and_is_not_tried_again(self):
        kb = keyboard_device(entities=[entity(active=0)] * 8)
        kb.delays = {device.DEVICE_INFO: 0.1}
        with mock.patch.object(keyboard, "REQUEST_BUDGET", 0.25):
            started = time.monotonic()
            rig = Rig(self, {1: kb, 2: mouse_device()}, keys={"0x0f01": COPY}).start()
            self.assertLess(time.monotonic() - started, 3.0)  # no wait of a look's budget on top
        self.assertTrue(rig.state()["connected"])
        self.assertTrue(rig.key(0x0F01)["active"])
        self.assertIsNone(rig.state()["firmware"])
        asked = len(fw_packets(rig, kb))
        self.assertLess(asked, 8)
        with Clock(time.monotonic()) as clock:
            for _ in range(120):
                clock.now += 5.0
                rig.engine._tick()
        rig.settle()
        self.assertEqual(len(fw_packets(rig, kb)), asked)  # not asked again while it stays
        rig.keys_down(1, kb, 0x0F01)
        self.assertEqual(rig.input.sent[-2:], [("KEY_LEFTCTRL", True), ("KEY_C", True)])

    def test_the_keys_never_wait_for_it(self):
        kb = keyboard_device(entities=[entity(active=0)] * 7 + [MAIN])
        kb.delays = {device.DEVICE_INFO: 0.15}
        rig = Rig(self, {1: kb, 2: mouse_device()})
        until_firmware(rig)
        new = copy.deepcopy(rig.engine.cfg)
        new["keyboard"]["keys"] = {"0x0f01": COPY}
        start = len(rig.port.written)
        rig.engine._apply_config(new)
        rig.settle()
        self.assertIn((0x0F01, True), diverts(kb))
        later = rig.port.written[start:]
        divert = next(i for i, p in enumerate(later) if p[1] == 1 and p[2] == kb.index(device.REPROG_CONTROLS_V4)
                      and p[3] >> 4 == 3)
        before = [p for p in later[:divert] if p[1] == 1 and p[2] == kb.index(device.DEVICE_INFO)]
        self.assertLessEqual(len(before), 1)  # the read under way was called off, at most its request in flight went
        self.assertEqual(rig.state()["firmware"], "71.3.21")  # and read again once the keys were done

    def test_what_was_read_of_one_keyboard_is_never_shown_for_the_next(self):
        old = keyboard_device(entities=[entity(active=0)] * 7 + [MAIN])
        old.delays = {device.DEVICE_INFO: 0.15}
        rig = Rig(self, {1: old, 2: mouse_device()})
        until_firmware(rig)
        rig.link(1, False)
        while rig.kb.device is not None:  # what was on its way to the engine before that, first
            rig.engine._handle(rig.engine._queue.get(timeout=2.0))
        self.assertFalse(rig.state()["connected"])
        self.assertIsNone(rig.state()["firmware"])
        unit_b = rig.port.devices[1] = keyboard_device(UNIT_B, entities=[OTHER])
        sent = len(rig.port.written)
        time.sleep(0.4)
        while not rig.engine._queue.empty():  # what the old read still had to say: dropped
            rig.engine._handle(rig.engine._queue.get())
        self.assertIsNone(rig.state()["firmware"])
        self.assertEqual([p for p in rig.port.written[sent:] if p[1] == 1], [])  # not a request more to its slot
        rig.link(1, True)
        rig.settle()
        self.assertEqual(rig.state()["firmware"], "12.10.102")
        self.assertEqual(rig.kb._identity, identity(UNIT_B))
        self.assertTrue(unit_b.entities)

    def test_away_it_keeps_its_settings_not_its_firmware(self):
        kb = keyboard_device(entities=[MAIN], extra=[device.LOCK_KEY_STATE])
        rig = Rig(self, {1: kb, 2: mouse_device()}).start()
        rig.link(1, False)
        state = rig.state()
        self.assertFalse(state["connected"])
        self.assertIsNone(state["firmware"])
        self.assertEqual(state["settings"], ["locks"])

    def test_the_unit_id_goes_nowhere(self):
        kb = keyboard_device(entities=[MAIN], extra=[device.DUAL_PLATFORM])
        with self.assertLogs("mxoptions", logging.DEBUG) as logs:
            logging.getLogger("mxoptions").debug("start")
            rig = Rig(self, {1: kb, 2: mouse_device()}, keys={"0x0f01": COPY}).start()
            rig.link(1, False)
            rig.link(1, True)
            rig.settle()
        self.assertEqual(rig.kb._identity, identity(UNIT_A))  # which unit it is: as before
        text = "\n".join(logs.output) + json.dumps(rig.state()) + json.dumps(rig.engine.cfg) + repr(rig.states)
        for needle in (UNIT_A.hex(), repr(UNIT_A), UNIT_A.hex(" "), MODEL.hex()):
            self.assertNotIn(needle, text)
        self.assertEqual(rig.state()["firmware"], "71.3.21")
        self.assertNotIn("unit", rig.state())

    def test_a_keyboard_without_the_feature_is_asked_nothing_about_it(self):
        kb = keyboard_device(info=False)
        rig = Rig(self, {1: kb, 2: mouse_device()}).start()
        self.assertIsNone(rig.state()["firmware"])
        self.assertEqual(rig.state()["settings"], [])
        self.assertNotIn(device.DEVICE_INFO, kb.features)


class Page(unittest.TestCase):
    """The Keyboard page: its settings card and firmware row (Qt offscreen)."""

    def setUp(self):
        from PySide6.QtWidgets import QApplication
        from mxoptions import config
        from mxoptions.ui import keyboard_page
        from mxoptions.ui.store import Store
        self.app = QApplication.instance() or QApplication([])

        class Engine:
            def __getattr__(self, name):
                return lambda *args: None
        patcher = mock.patch.object(keyboard_page.firmware, "available", lambda: True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = Store(copy.deepcopy(config.DEFAULTS), Engine())
        self.store.flush = lambda: None
        self.store._save_timer.timeout.disconnect()
        self.page = keyboard_page.KeyboardPage(self.store)
        self.addCleanup(self.page.deleteLater)
        self.asked = []
        self.page.firmware.start = lambda name, check: self.asked.append(name)
        self.page.resize(900, 700)
        self.page.show()

    def show(self, **found):
        keyboard = {"connected": True, "known": True, "name": "Synthetic keyboard", "battery": None, "current_host": 0,
                    "hosts": [], "keys": [], "reason": None, "failed": None, "firmware": None, "settings": []}
        keyboard.update(found)
        self.store.update_state(dict(self.store.state, keyboard=keyboard))

    def rows(self):
        return [name for name, row in self.page.settings_rows.items() if not row.isHidden()]

    def test_settings_card_shows_what_the_keyboard_has_and_nothing_else(self):
        self.show()
        self.assertTrue(self.page.settings_card.isHidden())
        self.show(settings=["backlight", "locks"])
        self.assertFalse(self.page.settings_card.isHidden())
        self.assertEqual(self.rows(), ["backlight", "locks"])
        self.assertEqual(self.page.settings_rows["locks"].title.text(), "Lock keys")
        for name, row in self.page.settings_rows.items():  # a hint, not a control; for the lock keys, only a notice
            self.assertIn("never saves or logs" if name == "locks" else "MX Options does not", row.note.text())
        self.show(settings=["fn", "unknown"])
        self.assertEqual(self.rows(), ["fn"])

    def test_firmware_read_from_the_keyboard_comes_and_goes_with_it(self):
        self.show()
        self.assertEqual(self.asked, ["Synthetic keyboard"])
        self.page._firmware_answered([], False)
        self.assertEqual(self.page.firmware_row.title.text(), "Firmware")
        self.show(firmware="71.3.21")  # read after the keyboard was found
        self.assertEqual(self.page.firmware_row.title.text(), "Firmware 71.3.21")
        self.assertIn("Read from the keyboard", self.page.firmware_row.note.text())
        self.show(firmware="71.3.21", connected=False)
        self.assertEqual(self.page.firmware_row.title.text(), "Firmware")
        self.show(name="Synthetic keyboard 2", firmware=None)  # another one: nothing of the first shown
        self.assertEqual(self.page.firmware_row.title.text(), "Firmware")
        self.assertEqual(self.asked, ["Synthetic keyboard", "Synthetic keyboard 2"])

    def test_fwupd_s_answer_comes_first_and_is_never_shown_for_another_keyboard(self):
        self.show(firmware="71.3.21")
        self.page._firmware_answered([{"name": "Synthetic keyboard", "version": "RQK 71.03"}], False)
        self.assertEqual(self.page.firmware_row.title.text(), "Synthetic keyboard  RQK 71.03")
        self.show(name="Synthetic keyboard 2", firmware="12.10.102")
        self.assertEqual(self.page.firmware_row.title.text(), "Firmware 12.10.102")
        self.page._firmware_for = "Synthetic keyboard"  # an answer about the first one, late
        self.page._firmware_answered([{"name": "Synthetic keyboard", "version": "RQK 71.03"}], False)
        self.assertEqual(self.page.firmware_row.title.text(), "Firmware 12.10.102")
        self.assertEqual(self.asked[-1], "Synthetic keyboard 2")  # asked about the one there now


if __name__ == "__main__":
    unittest.main()
