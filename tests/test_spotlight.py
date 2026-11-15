"""Spotlight effects of a mouse button: the settings, the engine that shows and ends them, the overlay that holds the
screen for them (here and in a program of its own) and the picker. Screens, the pointer and every picture of the
screen are stand-ins made here: nothing is captured, and no desktop is needed."""

import ast
import copy
import json
import os
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QProcess, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication  # noqa: E402

from mxoptions import actions, api, config, engine, i18n_uk  # noqa: E402
from mxoptions.ui import action_picker, editors, keyboard_page, pages, ring, ring_page  # noqa: E402
from mxoptions.ui.store import Store  # noqa: E402
from tests.test_mouse_profiles import FakeFront, _Engine, drain, front, gone, make, no_configure_writes, profile, set_config  # noqa: E402

APP = QApplication.instance() or QApplication([])
BACK, FORWARD, PANEL = config.BUTTONS["back"], config.BUTTONS["forward"], config.BUTTONS["panel"]
KATE, KONSOLE = "org.kde.kate", "org.kde.konsole"
COPY = actions.keys("LEFTCTRL", "C")
KEY = ("keyboard", 0x0f01)  # a key of the keyboard that opens the ring
HIGHLIGHT = {"effect": "highlight", "size": 50, "color": "#000000"}
LASER = {"effect": "laser", "size": 20, "color": "#f2605d"}
MAGNIFY = {"effect": "magnify", "size": 10, "color": "#ffffff"}


def bubble(name):
    return {"label": name, "icon": "", "color": "", "action": {"type": "hook", "name": name}}


RING = [bubble("a")]
OTHER_RING = [bubble("b")]


def settle(seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        APP.processEvents()
        time.sleep(0.01)


def spotlight(**effects):
    """A spotlight action with these effects changed, e.g. laser={"on": True}."""
    action = config.default_spotlight()
    for name, values in effects.items():
        action[name].update(values)
    return action


def spotlit(test, back=None, profiles=(), to=None, **buttons):
    """An engine (on the test's thread, synthetic mouse) whose Back button shows Spotlight effects. What it tells about
    them is in ``.effects``, and goes on to the overlay ``to`` as well."""
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["buttons"]["back"] = back or config.default_spotlight()
    cfg["buttons"].update(copy.deepcopy(buttons))
    cfg["ring"]["bubbles"] = copy.deepcopy(RING)
    cfg["mouse"]["profiles"] = copy.deepcopy(list(profiles))
    made = make(test, real=False, cfg=cfg)
    made.effects = []

    def on_effect(event, look=None):
        made.effects.append((event, look))
        if to is not None:
            to.effect(event, look)
    made.listener.on_effect = on_effect
    return made


def overlay(test, kind=ring.XWAYLAND):
    made = ring.RingOverlay(APP, kind=kind)
    test.addCleanup(made.deleteLater)
    test.addCleanup(made.close)
    made.configure({"size": 1.0, "style": "bubbles", "bubbles": RING})
    made.heard = []
    made.opened.connect(made.heard.append)
    made.picks = []
    made.triggered.connect(lambda action, session: made.picks.append(action))
    return made


def service(shown_on):
    """The interface other programs use, in front of ``shown_on``."""
    return api.MouseApi(Store(copy.deepcopy(config.DEFAULTS), _Engine()), shown_on, lambda: None)


def refused(test, shown_on):
    """No ring can be had: neither asked for here nor by another program."""
    other = service(shown_on)
    test.assertFalse(shown_on.open())
    test.assertFalse(shown_on.open(RING))
    test.assertFalse(other.OpenRing())
    test.assertFalse(other.ShowRing(json.dumps(RING)))


def patterned(width, height):
    """A made-up picture of a screen: every pixel tells where it is (x, y), blue 77."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    for y in range(height):
        for x in range(width):
            image.setPixelColor(x, y, QColor(x % 256, y % 256, 77))
    return QPixmap.fromImage(image)


class Geometry:
    """What a stand-in screen says of its size, any values at all."""

    def __init__(self, width, height):
        self._width, self._height = width, height

    def size(self):
        return self

    def width(self):
        return self._width

    def height(self):
        return self._height


class Screen:
    """A screen whose picture is ``picture`` (anything a grab could give back); counts the grabs."""

    def __init__(self, picture=None, geometry=QRect(0, 0, 200, 100), ratio=1.0):
        self.picture, self._geometry, self.ratio, self.grabs = picture, geometry, ratio, 0

    def geometry(self):
        return self._geometry

    def devicePixelRatio(self):
        return self.ratio

    def grabWindow(self, window):
        self.grabs += 1
        return self.picture


class BigPicture:
    """What a grab gave back that is far larger than the screen said: never kept."""

    def isNull(self):
        return False

    def width(self):
        return 100000

    def height(self):
        return 100000

    def depth(self):
        return 32

    def setDevicePixelRatio(self, ratio):
        pass


class Gui:
    """QGuiApplication as the overlay sees it, with one stand-in screen everywhere (None: none under the pointer)."""

    def __init__(self, screen):
        self.screen = screen

    def screenAt(self, where):
        return self.screen

    def primaryScreen(self):
        return self.screen or Screen()

    def screens(self):
        return [self.primaryScreen()]

    def __getattr__(self, name):
        return getattr(QGuiApplication, name)


class Desktop:
    """An X11 desktop with a compositing manager (or not), the pointer at ``where`` and the screen ``screen``."""

    def __init__(self, test, screen, where=QPoint(120, 40), compositing=True):
        cursor = mock.Mock()
        cursor.pos.return_value = where
        for name, value in (("QGuiApplication", Gui(screen)), ("QCursor", cursor)):
            patcher = mock.patch.object(ring, name, value)
            patcher.start()
            test.addCleanup(patcher.stop)
        patcher = mock.patch.object(ring.x11, "compositing", lambda: compositing)
        patcher.start()
        test.addCleanup(patcher.stop)


def painted(item):
    image = QImage(int(item.width()), int(item.height()), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    item.paint(painter)
    painter.end()
    return image


# -- settings ----------------------------------------------------------------------------------------------------------

class SpotlightSettings(unittest.TestCase):

    def test_an_action_is_cleaned_to_whole_valid_settings(self):
        self.assertEqual(config.default_spotlight(), {
            "type": "spotlight", "highlight": {"on": True, "size": 50, "color": "#000000"},
            "magnify": {"on": False, "size": 50, "color": "#ffffff"}, "laser": {"on": False, "size": 50, "color": "#f2605d"}})
        self.assertIsNot(config.default_spotlight()["laser"], config.EFFECT_DEFAULTS["laser"])
        for given in (None, [], "spotlight", {"type": "spotlight"}, {"type": "spotlight", "highlight": [1]},
                      {"type": "spotlight", "magnify": "x", "laser": None}):
            with self.subTest(given=given):
                self.assertEqual(config.spotlight(given), config.default_spotlight())
        for size, kept in ((True, 50), (False, 50), (2.0, 50), ("7", 50), (None, 50), ([3], 50), (0, 1), (-5, 1),
                           (101, 100), (10 ** 30, 100), (1, 1), (100, 100), (37, 37)):
            with self.subTest(size=size):
                self.assertEqual(config.spotlight(spotlight(laser={"size": size}))["laser"]["size"], kept)
        for color, kept in (("#ABCDEF", "#abcdef"), ("#a1b2c3", "#a1b2c3"), ("abcdef", "#f2605d"), ("#abcde", "#f2605d"),
                            ("#abcdefa", "#f2605d"), ("#abcdef\n", "#f2605d"), ("#ggggggg", "#f2605d"), (["#000000"], "#f2605d"),
                            ({"#000000": 1}, "#f2605d"), (None, "#f2605d"), (0x123456, "#f2605d"), (True, "#f2605d")):
            with self.subTest(color=color):
                self.assertEqual(config.spotlight(spotlight(laser={"color": color}))["laser"]["color"], kept)
        for on, kept in ((1, False), ("true", False), ([True], False), (None, False), (0, False), (True, True)):
            with self.subTest(on=on):
                self.assertIs(config.spotlight(spotlight(laser={"on": on}))["laser"]["on"], kept)
        extra = spotlight(highlight={"extra": 2})
        extra["x"] = 1
        cleaned = config.spotlight(extra)
        self.assertEqual(set(cleaned), {"type", *config.EFFECTS})
        self.assertEqual(set(cleaned["highlight"]), {"on", "size", "color"})
        self.assertEqual(config.spotlight(spotlight(highlight={"on": False})),
                         spotlight())  # none on: Highlight is
        self.assertEqual(config.spotlight(spotlight(highlight={"on": False}, laser={"on": True})),
                         spotlight(highlight={"on": False}, laser={"on": True}))

    def test_the_settings_file_the_interface_and_an_import_clean_it(self):
        messy = spotlight(laser={"on": 1, "size": 400, "color": "#FFFFFF"})
        messy["junk"] = [1, 2]
        expected = config.spotlight(messy)
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["buttons"]["back"] = messy
        self.assertEqual(config.clean(cfg)["buttons"]["back"], expected)
        self.assertEqual(config.import_data(config.export_data(cfg), config.clean(cfg))["buttons"]["back"], expected)
        store = Store(config.clean(copy.deepcopy(config.DEFAULTS)), _Engine())
        store.flush = lambda: None
        store._save_timer.timeout.disconnect()
        self.assertTrue(store.set_path("buttons.forward", messy))
        self.assertEqual(store.cfg["buttons"]["forward"], expected)

    def test_keys_and_app_profiles_cannot_have_it_and_a_ring_bubble_runs_nothing(self):
        action = config.default_spotlight()
        self.assertFalse(config.keyboard_action(action))
        self.assertFalse(config.profile_button_action(action))
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["keyboard"]["keys"] = {"0x0f01": action, "0x0f02": COPY}
        cfg["mouse"]["profiles"] = [profile(KATE, back=action, forward=COPY)]
        cfg["ring"]["bubbles"] = [{"label": "", "icon": "", "color": "", "action": action}]
        cleaned = config.clean(cfg)
        self.assertEqual(cleaned["keyboard"]["keys"], {"0x0f02": COPY})
        self.assertEqual(cleaned["mouse"]["profiles"][0]["buttons"], {"forward": COPY})
        self.assertEqual(cleaned["ring"]["bubbles"][0]["action"], action)  # kept, as any action a ring has
        made = make(self)
        made.ring_action(action, None)
        drain(made)
        made.executor.trigger(action)
        self.assertEqual((made.vinput.sent, made._held_actions), ([], {}))

    def test_a_settings_file_keeps_it_and_one_without_it_is_as_before(self):
        cfg = config.clean(copy.deepcopy(config.DEFAULTS))
        plain = config.import_data(config.export_data(cfg), cfg)
        self.assertEqual(plain, cfg)
        cfg["buttons"]["middle"] = spotlight(magnify={"on": True, "size": 3})
        again = config.import_data(config.export_data(cfg), cfg)
        self.assertEqual(again["buttons"]["middle"], cfg["buttons"]["middle"])
        self.assertEqual(again, cfg)

    def test_it_is_named_and_drawn_as_the_catalogue_says(self):
        custom = spotlight(laser={"on": True, "color": "#123456"})
        self.assertEqual(actions.find_preset(custom).id, "spotlight")
        self.assertEqual(actions.describe(custom), "Spotlight effects")
        self.assertEqual(actions.icon_name(custom), "view-preview")
        self.assertIn("spotlight", actions.BUTTON_ONLY)
        for text in ("Spotlight effects", "Highlight", "Still Magnify", "Digital Laser", "Not available on Wayland desktops."):
            self.assertIn(text, i18n_uk.STRINGS)


# -- engine ------------------------------------------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SpotlightFromAButton(unittest.TestCase):

    def clock(self):
        clock = Clock()
        patcher = mock.patch.object(engine.time, "monotonic", clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        return clock

    def test_a_press_shows_the_first_effect_switched_on_and_the_release_ends_it(self):
        made = spotlit(self, spotlight(highlight={"on": False}, magnify={"on": True, "size": 7}, laser={"on": True}))
        made._button_down(BACK)
        self.assertEqual(made.effects, [("down", {"effect": "magnify", "size": 7, "color": "#ffffff"})])
        made._button_down(FORWARD)
        made._button_up(FORWARD)
        self.assertEqual(len(made.effects), 1)
        made._button_up(BACK)
        self.assertEqual(made.effects[1:], [("up", None)])
        made._button_up(BACK)
        self.assertEqual(len(made.effects), 2)
        self.assertEqual((made.executor.ran, made._held_actions, made.listener.rings), ([], {}, []))

    def press(self, made, clock, at, held):
        clock.now = at
        made._button_down(BACK)
        clock.now = at + held
        made._button_up(BACK)
        return made.effects[-2][1]["effect"]

    def test_pressed_again_right_after_a_short_press_it_shows_the_next_effect(self):
        clock = self.clock()
        made = spotlit(self, spotlight(laser={"on": True}))
        self.assertEqual(self.press(made, clock, 0.0, 0.2), "highlight")
        self.assertEqual(self.press(made, clock, 0.59, 0.1), "laser")      # 0.39 s after the release
        self.assertEqual(self.press(made, clock, 1.10, 0.1), "laser")      # 0.41 s after: the same one
        self.assertEqual(self.press(made, clock, 2.0, 0.5), "laser")       # a long press
        self.assertEqual(self.press(made, clock, 2.6, 0.1), "laser")       # 0.1 s after a long one: the same
        self.assertEqual(self.press(made, clock, 2.9, 0.1), "highlight")   # wraps
        three = spotlit(self, spotlight(laser={"on": True}, magnify={"on": True}))
        seen = [self.press(three, clock, 10.0 + step * 0.3, 0.1) for step in range(5)]
        self.assertEqual(seen, ["highlight", "magnify", "laser", "highlight", "magnify"])
        alone = spotlit(self)
        self.assertEqual([self.press(alone, clock, 20.0 + step * 0.3, 0.1) for step in range(3)], ["highlight"] * 3)
        set_config(three, dict(copy.deepcopy(three.cfg), buttons=dict(three.cfg["buttons"], back=spotlight(
            laser={"on": True, "size": 9}, magnify={"on": True}))))
        self.assertEqual(self.press(three, clock, 40.0, 0.1), "highlight")  # changed: from the first again

    def test_one_effect_at_a_time_the_first_button_keeps_it(self):
        made = spotlit(self, forward=spotlight(laser={"on": True}, highlight={"on": False}))
        made._button_down(BACK)
        made._button_down(FORWARD)
        made._button_up(FORWARD)
        self.assertEqual([event for event, _look in made.effects], ["down"])
        made._button_up(BACK)
        made._button_down(FORWARD)
        self.assertEqual(made.effects[1:], [("up", None), ("down", {"effect": "laser", "size": 50, "color": "#f2605d"})])

    def test_its_button_changed_while_held_ends_it_and_others_leave_it(self):
        made = spotlit(self, profiles=[profile(KATE, back=COPY), profile(KONSOLE, forward=COPY)])
        made._button_down(BACK)
        front(made, KONSOLE)  # another button changes there
        self.assertEqual([event for event, _look in made.effects], ["down"])
        front(made, KATE)     # this one does
        self.assertEqual(made.effects[1:], [("cancel", None)])
        made._button_up(BACK)
        self.assertEqual(len(made.effects), 2)
        self.assertEqual(made.executor.ran, [])
        front(made, KONSOLE)
        made._button_down(BACK)
        set_config(made, dict(copy.deepcopy(made.cfg), buttons=dict(made.cfg["buttons"], back=COPY)))
        self.assertEqual(made.effects[2:], [("down", HIGHLIGHT), ("cancel", None)])

    def test_the_mouse_going_or_starting_over_ends_it(self):
        for how in ("offline", "configure"):
            with self.subTest(how=how):
                made = spotlit(self)
                no_configure_writes(made)
                made._button_down(BACK)
                if how == "offline":
                    made._set_online(False)
                else:
                    made._configure()
                self.assertEqual(made.effects, [("down", HIGHLIGHT), ("cancel", None)])
                made._button_up(BACK)
                self.assertEqual(len(made.effects), 2)

    def run_presses(self, with_spotlight):
        made = spotlit(self, profiles=[profile(KATE, OTHER_RING)], forward=COPY)
        macro = {"type": "macro", "name": "", "repeat": False, "steps": [{"do": "keys", "keys": ["KEY_A"]}]}
        steps = [lambda: made._button_down(PANEL), "down", lambda: made._button_up(PANEL), "up",
                 lambda: made._ring_event(KEY, "down"), "down", lambda: made._button_down(FORWARD),
                 lambda: made._button_up(FORWARD), lambda: made._ring_event(KEY, "up"), "up",
                 lambda: made._button_down(PANEL), "down", lambda: front(made, KATE), lambda: made._button_up(PANEL), "up",
                 lambda: made.trigger_action(macro), lambda: drain(made), "down", lambda: made._ring_event(KEY, "down"),
                 lambda: made._ring_gone("keyboard"), "up"]
        for step in steps:
            if step == "down":
                if with_spotlight:
                    made._button_down(BACK)
            elif step == "up":
                if with_spotlight:
                    made._button_up(BACK)
            else:
                step()
        return made, (made.listener.rings, made._ring_by, made._ring_session, made._ring_floor, made._held_actions,
                      made.executor.ran, made.vinput.sent, made.listener.turns)

    def test_rings_keys_buttons_and_macros_go_as_they_do_without_it(self):
        made, seen = self.run_presses(True)
        _plain, expected = self.run_presses(False)
        self.assertEqual(seen, expected)
        self.assertEqual([event for event, _look in made.effects], ["down", "up"] * 4)

    def test_the_thumb_wheel_does_what_it_does_without_it(self):
        def turned(with_spotlight):
            made = spotlit(self)
            made.vinput = mock.Mock()
            if with_spotlight:
                made._button_down(BACK)
            made._on_thumbwheel(-6)
            return made, made.vinput.mock_calls
        made, calls = turned(True)
        self.assertEqual(calls, turned(False)[1])
        self.assertTrue(any(call[0] == "hscroll" for call in calls))
        self.assertEqual(made.listener.turns, [])

    def test_no_other_way_runs_it(self):
        self.assertFalse(service(None).Trigger('{"type": "spotlight"}'))
        self.assertIsNone(api.parse_action("spotlight"))

    def test_the_program_passes_it_on_to_the_overlay(self):
        from mxoptions import app
        bridge = app.Bridge()
        heard = []
        bridge.effect.connect(lambda *args: heard.append(args))
        bridge.on_effect("down", HIGHLIGHT)
        bridge.on_effect("up")
        self.assertEqual(heard, [("down", HIGHLIGHT), ("up", None)])
        with open(app.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        connects = {(ast.unparse(node.func.value), ast.unparse(node.args[0])) for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "connect"}
        self.assertIn(("bridge.effect", "overlay.effect"), connects)


# -- the overlay, here -----------------------------------------------------------------------------------------------

class SpotlightOnScreen(unittest.TestCase):

    def test_it_follows_the_pointer_until_released_and_says_nothing_of_a_ring(self):
        made = overlay(self)
        made.effect("down", HIGHLIGHT)
        item, radius = made._effect_item, ring.effect_radius(HIGHLIGHT)
        self.assertTrue(made._window.isVisible())
        self.assertFalse(item.isVisible())  # not until the pointer shows where it is
        made.pointerAt(300, 200)
        self.assertTrue(item.isVisible())
        self.assertEqual((item.x(), item.y(), item.width()), (300 - radius, 200 - radius, 2 * radius))
        self.assertTrue(made._dim.property("dims"))
        made.pointerAt(310, 205)
        self.assertEqual((item.x(), item.y()), (310 - radius, 205 - radius))
        self.assertEqual(made._selected, -1)
        made.clickAt(310, 205)
        made.wheelAt(310, 205, 120)
        made.pointerLeft()
        self.assertFalse(item.isVisible())
        made.effect("up")
        settle(0.1)
        self.assertFalse(made._window.isVisible())
        self.assertFalse(made._dim.property("dims"))
        self.assertEqual((made.heard, made.picks, made._open), ([], [], False))

    def test_a_ring_and_an_effect_never_share_the_screen(self):
        made = overlay(self)
        made.handle("down", 1, RING)
        made.effect("down", LASER)
        self.assertIsNone(made._effect_look)
        self.assertTrue(made._open)
        made.effect("up")
        made.close()
        made.effect("down", LASER)
        self.assertEqual(made._effect_look, LASER)
        made.handle("down", 2, RING)
        self.assertFalse(made._open)
        self.assertFalse(made.open())
        made.handle("up")
        made.effect("up")
        made.handle("down", 3, RING)
        self.assertTrue(made._open)
        self.assertEqual(made.heard, [True, False, True])

    def test_closing_ends_it_and_closing_the_ring_alone_does_not(self):
        made = overlay(self)
        made.effect("down", HIGHLIGHT)
        made.pointerAt(50, 50)
        made._close_ring()
        self.assertEqual((made._effect_look, made._effect_item._look, made._effect_held), (HIGHLIGHT, HIGHLIGHT, True))
        self.assertTrue(made._window.isVisible())
        self.assertTrue(made._effect_item.isVisible())
        made.close()
        self.assertEqual((made._effect_look, made._effect_item._look, made._effect_held, made._snapshot), (None, None, False, None))
        self.assertFalse(made._window.isVisible())

    def test_still_magnify_shows_the_screen_as_it_was_at_the_press(self):
        for ratio in (1.0, 2.0):
            with self.subTest(ratio=ratio):
                side = int(ratio)
                screen = Screen(patterned(200 * side, 100 * side), ratio=ratio)
                Desktop(self, screen)
                made = overlay(self, ring.X11)
                made.effect("down", MAGNIFY)
                self.assertEqual(screen.grabs, 1)
                item, radius = made._effect_item, ring.effect_radius(MAGNIFY)
                self.assertIs(made._snapshot, item._snapshot)
                self.assertTrue(made._window.isVisible())
                self.assertTrue(item.isVisible())
                self.assertEqual((item.x(), item.y()), (120 - radius, 40 - radius))
                image = painted(item)
                middle = image.pixelColor(int(radius), int(radius))
                self.assertEqual((middle.red(), middle.green(), middle.blue()), ((120 * side) % 256, 40 * side, 77))
                rim = image.pixelColor(int(radius), 1)
                self.assertGreater(min(rim.red(), rim.green(), rim.blue()), 200)
                self.assertEqual(image.pixelColor(0, 0).alpha(), 0)  # outside the lens: nothing
                made.effect("cancel" if side == 2 else "up")
                self.assertEqual((made._snapshot, item._snapshot, made._effect_look), (None, None, None))
                self.assertEqual(screen.grabs, 1)

    def test_still_magnify_needs_a_picture_this_desktop_can_give(self):
        self.assertEqual(ring.effects_here(ring.LAYER), ("highlight", "laser"))
        self.assertEqual(ring.effects_here(ring.XWAYLAND), ("highlight", "laser"))
        self.assertEqual(ring.effects_here(ring.WINDOW), ())
        for kind in (ring.XWAYLAND, ring.WINDOW):
            with self.subTest(kind=kind):
                screen = Screen(patterned(20, 10))
                Desktop(self, screen)
                made = overlay(self, kind)
                made.effect("down", MAGNIFY)
                self.assertEqual((screen.grabs, made._effect_look, made._snapshot), (0, None, None))
                self.assertFalse(made._window.isVisible())
                made.effect("up")

    def test_a_picture_too_large_or_none_at_all_is_never_kept(self):
        nan, inf = float("nan"), float("inf")
        before = [("too large", QRect(0, 0, 5000, 5000), 2.0), ("ratio nan", QRect(0, 0, 200, 100), nan),
                  ("ratio inf", QRect(0, 0, 200, 100), inf), ("ratio 0", QRect(0, 0, 200, 100), 0.0),
                  ("ratio negative", QRect(0, 0, 200, 100), -1.0), ("ratio bool", QRect(0, 0, 200, 100), True),
                  ("width 0", QRect(0, 0, 0, 100), 1.0), ("width negative", QRect(0, 0, -5, 100), 1.0),
                  ("width nan", Geometry(nan, 100), 1.0), ("height inf", Geometry(200, inf), 1.0),
                  ("overflowing", Geometry(1e308, 1e308), 2.0), ("no screen", None, 1.0)]
        for name, geometry, ratio in before:
            with self.subTest(name):
                screen = None if geometry is None else Screen(patterned(20, 10), geometry, ratio)
                Desktop(self, screen)
                made = overlay(self, ring.X11)
                made.effect("down", MAGNIFY)
                self.assertEqual(screen.grabs if screen else 0, 0)
                self.rejected(made)
        for name, picture in (("larger than it said", BigPicture()), ("null", QPixmap())):
            with self.subTest(name):
                screen = Screen(picture)
                Desktop(self, screen)
                made = overlay(self, ring.X11)
                made.effect("down", MAGNIFY)
                self.assertEqual(screen.grabs, 1)
                self.rejected(made)
        at_most = Screen(patterned(20, 10), QRect(0, 0, 4096, 4096), 1.0)  # exactly SNAPSHOT_MAX
        Desktop(self, at_most)
        self.assertIsNotNone(ring.RingOverlay._grab(QPoint(1, 1)))
        self.assertEqual(at_most.grabs, 1)

    def rejected(self, made):
        self.assertEqual((made._snapshot, made._effect_item._snapshot, made._effect_look), (None, None, None))
        self.assertFalse(made._window.isVisible())
        self.assertTrue(made._effect_held)
        made.effect("up")
        self.assertFalse(made._effect_held)

    def test_a_look_from_anywhere_is_checked(self):
        self.assertEqual(ring.effect_look(dict(LASER, color="#F2605D")), LASER)
        bad = [None, [], "x", 5, [LASER], {}, {"effect": "laser", "size": 20}, dict(LASER, extra=1),
               dict(LASER, size=True), dict(LASER, size=0), dict(LASER, size=101), dict(LASER, size=2.0),
               dict(LASER, size="5"), dict(LASER, color="red"), dict(LASER, color="#12345"), dict(LASER, color=["#000000"]),
               dict(LASER, color={"a": 1}), dict(LASER, color=True), dict(LASER, color=None), dict(LASER, effect="zoom"),
               dict(LASER, effect=["laser"]), dict(LASER, effect={"laser": 1}), dict(LASER, effect=True), dict(LASER, effect=None)]
        for look in bad:
            with self.subTest(look=look):
                self.assertIsNone(ring.effect_look(look))

    def test_without_compositing_there_is_nothing_to_show(self):
        Desktop(self, Screen(patterned(20, 10)), compositing=False)
        self.assertEqual(ring.effects_here(ring.X11), ())
        made = overlay(self, ring.X11)
        made.effect("down", HIGHLIGHT)
        self.assertIsNone(made._effect_look)
        self.assertFalse(made._window.isVisible())

    def test_highlight_darkens_around_a_clear_circle_and_laser_is_a_dot(self):
        item = ring.EffectItem()
        self.addCleanup(item.deleteLater)
        item.set_look(HIGHLIGHT)
        item.set_center(200, 200)
        image = painted(item)
        radius = int(ring.effect_radius(HIGHLIGHT))
        self.assertEqual(image.pixelColor(radius, radius).alpha(), 0)
        corner = image.pixelColor(1, 1)
        self.assertEqual((corner.alpha(), corner.red()), (ring.DIM_ALPHA, 0))
        item.set_look(LASER)
        item.set_center(50, 50)
        image = painted(item)
        radius = int(ring.effect_radius(LASER))
        self.assertEqual(image.pixelColor(radius, radius).name(), "#f2605d")
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        item.set_look(None)
        self.assertFalse(item.isVisible())


# -- who holds the screen: the overlay here ------------------------------------------------------------------------

class SpotlightOwnsTheOverlay(unittest.TestCase):

    def test_a_shown_effect_keeps_every_ring_out_until_it_ends(self):
        for end in ("up", "cancel", "close"):
            with self.subTest(end=end):
                made = overlay(self)
                self.assertTrue(made.open())  # as it is without one
                made.close()
                made.effect("down", HIGHLIGHT)
                refused(self, made)
                made.handle("down", 1, RING)
                made.handle("up")
                self.assertEqual((made._open, made.heard), (False, [True, False]))
                made.close() if end == "close" else made.effect(end)
                self.assertFalse(made._effect_held)
                self.assertTrue(made.open())
                self.assertEqual(made.heard, [True, False, True])

    def test_an_effect_that_cannot_show_still_holds_the_screen_until_it_ends(self):
        made = overlay(self)
        made.handle("down", 1, RING)
        made.effect("down", HIGHLIGHT)  # a ring is open
        self.assertEqual((made._effect_look, made._effect_held), (None, True))
        made.effect("up")
        self.assertTrue(made._open)
        self.assertTrue(made._window.isVisible())  # its release leaves the ring
        made.close()
        self.assertTrue(made.open())
        made.close()
        for name, look, screen in (("magnify here", MAGNIFY, None), ("bad look", {"effect": "laser"}, None),
                                   ("null picture", MAGNIFY, Screen(QPixmap()))):
            with self.subTest(name):
                if screen is not None:
                    Desktop(self, screen)
                shown_on = overlay(self, ring.X11 if screen is not None else ring.XWAYLAND)
                shown_on.effect("down", look)
                self.assertIsNone(shown_on._effect_look)
                refused(self, shown_on)
                shown_on.effect("up")
                self.assertTrue(shown_on.open())
        unavailable = overlay(self)
        unavailable.available = False
        unavailable.effect("down", HIGHLIGHT)
        unavailable.available = True
        self.assertFalse(unavailable._effect_held)
        self.assertTrue(unavailable.open())

    def rig(self, spotlit_too=True):
        shown_on = overlay(self)
        made = spotlit(self, profiles=[profile(KATE, OTHER_RING)], to=shown_on)
        made.rings = []

        def on_ring(*args):
            made.rings.append(args)
            shown_on.handle(*args)
        made.listener.on_ring = on_ring
        return made, shown_on

    UNRELATED = {
        "keyboard reset": lambda made: (made._ring_event(KEY, "down"), made._ring_gone("keyboard")),
        "keyboard remap": lambda made: (made._ring_event(KEY, "down"), made._ring_event(KEY, "cancel")),
        "keyboard up": lambda made: (made._ring_event(KEY, "down"), made._ring_event(KEY, "up")),
        "mouse ring gone": lambda made: (made._button_down(PANEL), made._ring_gone("mouse")),
        "mouse ring remapped": lambda made: (made._button_down(PANEL), set_config(made, dict(
            copy.deepcopy(made.cfg), buttons=dict(made.cfg["buttons"], panel=COPY)))),
        "mouse ring up": lambda made: (made._button_down(PANEL), made._button_up(PANEL)),
        "ring of the app in front changed": lambda made: (made._button_down(PANEL), front(made, KATE)),
    }

    def test_rings_of_other_buttons_and_keys_never_touch_a_shown_effect(self):
        for name, unrelated in self.UNRELATED.items():
            with self.subTest(name):
                made, shown_on = self.rig()
                made._button_down(BACK)
                unrelated(made)
                self.assertEqual((shown_on._open, shown_on.heard), (False, []))
                self.assertEqual((shown_on._effect_look, shown_on._effect_item._look, shown_on._effect_held),
                                 (HIGHLIGHT, HIGHLIGHT, True))
                self.assertTrue(shown_on._window.isVisible())
                refused(self, shown_on)
                seen = (made.rings, made._ring_by, made._ring_session, made._ring_floor)
                plain, _plain_shown = self.rig()
                unrelated(plain)
                self.assertEqual(seen, (plain.rings, plain._ring_by, plain._ring_session, plain._ring_floor))
                made._button_up(BACK)
                self.assertFalse(shown_on._window.isVisible())
                made._button_up(PANEL)
                made._ring_event(KEY, "up")
                shown_on.handle("down", 99, RING)
                self.assertEqual(shown_on.heard, [True])
                self.assertEqual(made.effects, [("down", HIGHLIGHT), ("up", None)])

    def test_the_mouse_starting_over_ends_its_effect_by_itself(self):
        made, shown_on = self.rig()
        made._button_down(BACK)
        made._button_down(PANEL)
        made._reset_input_state()
        self.assertEqual(made.effects, [("down", HIGHLIGHT), ("cancel", None)])
        self.assertEqual((shown_on._effect_held, shown_on._effect_look), (False, None))
        self.assertFalse(shown_on._window.isVisible())
        self.assertTrue(shown_on.open())

    DISMISSALS = {
        "pressed again": ("tap", lambda made, on: made._ring_event(KEY, "down")),
        "keyboard reset": ("key", lambda made, on: made._ring_gone("keyboard")),
        "its button remapped": ("button", lambda made, on: set_config(made, dict(
            copy.deepcopy(made.cfg), buttons=dict(made.cfg["buttons"], panel=COPY)))),
        "ring of the app in front changed": ("button", lambda made, on: front(made, KATE)),
        "ring settings applied": ("tap", lambda made, on: on.configure({"size": 1.0, "style": "bubbles", "bubbles": RING})),
        "picked": ("tap", lambda made, on: on._choose(0)),
        "released over a bubble": ("button", lambda made, on: (setattr(on, "_selected", 0), made._button_up(PANEL))),
        "click elsewhere": ("tap", lambda made, on: on.clickAt(-5000, -5000)),
        "left untouched": ("tap", lambda made, on: (on._idle_timer.start(0), settle(0.1))),
        "pointer away": ("tap", lambda made, on: (on._away_timer.start(0), settle(0.1))),
    }

    def open_ring(self, made, how):
        if how == "tap":
            made._ring_event(KEY, "down")
            made._ring_event(KEY, "up")
        elif how == "key":
            made._ring_event(KEY, "down")
        else:
            made._button_down(PANEL)

    def test_a_ring_going_its_own_ways_keeps_an_effect_that_could_not_show(self):
        ends = {"up": lambda made, on: made._button_up(BACK),
                "cancel": lambda made, on: set_config(made, dict(copy.deepcopy(made.cfg), buttons=dict(made.cfg["buttons"], back=COPY))),
                "CloseRing": lambda made, on: service(on).CloseRing()}
        for name, (how, dismiss) in self.DISMISSALS.items():
            for end_name, end in ends.items():
                with self.subTest(name, end=end_name):
                    made, shown_on = self.rig()
                    self.open_ring(made, how)
                    made._button_down(BACK)
                    self.assertEqual((shown_on._effect_look, shown_on._effect_held), (None, True))
                    dismiss(made, shown_on)
                    self.assertEqual(shown_on.heard, [True, False])
                    self.assertFalse(shown_on._window.isVisible())
                    self.assertEqual((shown_on._effect_item._look, shown_on._effect_held), (None, True))
                    refused(self, shown_on)
                    shown_on.handle("down", 99, RING)
                    self.assertEqual(shown_on.heard, [True, False])
                    end(made, shown_on)
                    self.assertFalse(shown_on._effect_held)
                    self.assertTrue(shown_on.open())
                    self.assertEqual(shown_on.heard, [True, False, True])
            with self.subTest(name, spotlight=False):
                made, shown_on = self.rig()
                self.open_ring(made, how)
                dismiss(made, shown_on)
                self.assertEqual(shown_on.heard, [True, False])
                self.assertTrue(shown_on.open())

    def test_closing_from_outside_ends_the_ring_and_the_effect(self):
        made, shown_on = self.rig()
        made._button_down(BACK)
        service(shown_on).CloseRing()
        self.assertEqual((shown_on._effect_look, shown_on._effect_held), (None, False))
        self.assertFalse(shown_on._window.isVisible())
        self.assertTrue(shown_on.open())
        made._button_up(BACK)
        self.assertTrue(shown_on._open)
        self.assertTrue(shown_on._window.isVisible())
        shown_on.close()
        made._button_down(BACK)
        self.assertEqual(shown_on._effect_look, HIGHLIGHT)  # the next press shows
        made._button_up(BACK)
        rejected, on = self.rig()
        self.open_ring(rejected, "button")
        rejected._button_down(BACK)
        service(on).CloseRing()
        self.assertEqual((on._open, on._effect_held, on.heard), (False, False, [True, False]))
        self.assertTrue(on.open())


# -- who holds the screen: the overlay in a program of its own --------------------------------------------------------

class SpotlightAcrossThePipe(unittest.TestCase):

    def setUp(self):
        self.addCleanup(setattr, ring, "HELPER_PLATFORM", ring.HELPER_PLATFORM)
        ring.HELPER_PLATFORM = "offscreen"
        self.proxy = ring.RingProxy()
        self.addCleanup(self.proxy.shutdown)
        self.heard = []
        self.proxy.opened.connect(self.heard.append)
        self.proxy.configure({"size": 1.0, "style": "bubbles", "bubbles": RING})
        self.assertTrue(self.wait(lambda: self.proxy.available), "the other program never said it was ready")

    def wait(self, condition, seconds=15.0):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(0.01)
        return condition()

    def opened_and_closed(self):
        """A ring can be had: asked for, it shows; closed, it goes."""
        start = len(self.heard)
        self.assertTrue(self.proxy.open())
        self.assertTrue(self.wait(lambda: self.heard[start:] == [True]))
        self.proxy.close()
        self.assertTrue(self.wait(lambda: self.heard[start:] == [True, False]))

    def held_there(self):
        """The other program holds the screen for an effect: a ring asked for past this side's own check stays out.
        Let go of there by an explicit close, and here by the release, the next ring is the only one heard of."""
        start = len(self.heard)
        self.proxy._send("open")
        self.proxy._send("close")
        self.proxy.effect("up")
        self.assertTrue(self.proxy.open())
        self.assertTrue(self.wait(lambda: True in self.heard[start:]))
        self.wait(lambda: False, 0.5)  # whatever else it says about those lines has arrived by now
        self.assertEqual(self.heard[start:], [True])
        self.proxy.close()
        self.assertTrue(self.wait(lambda: self.heard[start:] == [True, False]))

    def test_a_spotlight_press_keeps_every_ring_out_there_too(self):
        self.opened_and_closed()
        self.proxy.effect("down", HIGHLIGHT)
        self.assertTrue(self.proxy._effect)
        refused(self, self.proxy)
        self.held_there()
        for end in ("up", "cancel", "close", "restart"):
            with self.subTest(end=end):
                self.proxy.effect("down", HIGHLIGHT)
                refused(self, self.proxy)
                if end == "close":
                    service(self.proxy).CloseRing()
                elif end == "restart":
                    self.proxy._process.kill()
                    self.assertTrue(self.wait(lambda: not self.proxy.available))
                    self.assertTrue(self.wait(lambda: self.proxy.available), "it did not come back")
                else:
                    self.proxy.effect(end)
                self.assertFalse(self.proxy._effect)
                self.opened_and_closed()

    def test_an_effect_sent_while_a_ring_is_on_its_way_waits_for_the_release(self):
        self.assertTrue(self.proxy.open())
        self.proxy.effect("down", LASER)  # before the other program said the ring opened
        self.assertFalse(self.proxy.open())
        self.assertTrue(self.wait(lambda: self.heard == [True]))
        self.proxy.effect("up")
        self.wait(lambda: False, 0.4)
        self.assertEqual(self.heard, [True])  # the release leaves the ring
        self.proxy.close()
        self.assertTrue(self.wait(lambda: self.heard == [True, False]))
        self.opened_and_closed()

    def test_what_it_sends_is_the_look_only_and_a_bad_one_is_dropped_there(self):
        sent = []
        real = self.proxy._send
        with mock.patch.object(self.proxy, "_send", lambda do, **more: (sent.append(dict(more, do=do)), real(do, **more))):
            self.proxy.effect("down", LASER)
            self.proxy.effect("down", LASER)   # held already: not sent again
            self.proxy.effect("sideways")      # not an event: not sent
            self.proxy.effect("up")
            self.proxy.effect("down", "not a look")
        self.assertEqual(sent, [{"do": "effect", "event": "down", "look": LASER}, {"do": "effect", "event": "up", "look": None},
                                {"do": "effect", "event": "down", "look": "not a look"}])
        self.assertTrue(self.proxy.available)
        self.held_there()  # held there, though nothing could be shown
        self.opened_and_closed()

    def test_ring_events_keep_the_hold_there(self):
        self.proxy.effect("down", HIGHLIGHT)  # shown there
        self.proxy.handle("down", 5, RING)
        self.proxy.handle("cancel")
        self.proxy.handle("cancel", 5)
        self.proxy.handle("up")
        self.proxy.configure({"size": 1.0, "style": "bubbles", "bubbles": RING})
        self.assertTrue(self.proxy._effect)
        refused(self, self.proxy)
        self.held_there()
        dismissals = {"pressed again": lambda: self.proxy.handle("down", 8, RING), "cancel": lambda: self.proxy.handle("cancel"),
                      "numbered cancel": lambda: self.proxy.handle("cancel", 7),
                      "ring settings": lambda: self.proxy.configure({"size": 1.0, "style": "bubbles", "bubbles": RING})}
        for name, dismiss in dismissals.items():
            with self.subTest(name):
                start = len(self.heard)
                self.proxy.handle("down", 7, RING)
                self.assertTrue(self.wait(lambda: self.heard[start:] == [True]))
                self.proxy.handle("up")
                self.proxy.effect("down", HIGHLIGHT)  # cannot show there: the ring is open
                dismiss()
                self.assertTrue(self.wait(lambda: self.heard[start:] == [True, False]))
                self.assertTrue(self.proxy._effect)
                refused(self, self.proxy)
                self.held_there()
        for end in ("up", "cancel", "close"):
            with self.subTest(end=end):
                start = len(self.heard)
                self.proxy.handle("down", 9, RING)
                self.assertTrue(self.wait(lambda: self.heard[start:] == [True]))
                self.proxy.handle("up")
                self.proxy.effect("down", HIGHLIGHT)
                self.proxy.handle("cancel")
                self.assertTrue(self.wait(lambda: self.heard[start:] == [True, False]))
                self.proxy.close() if end == "close" else self.proxy.effect(end)
                self.opened_and_closed()

    def test_closing_from_outside_ends_both_there(self):
        self.proxy.effect("down", HIGHLIGHT)
        service(self.proxy).CloseRing()
        self.assertFalse(self.proxy._effect)
        start = len(self.heard)
        self.assertTrue(self.proxy.open())
        self.assertTrue(self.wait(lambda: self.heard[start:] == [True]))
        self.proxy.effect("up")  # the late release leaves that ring
        self.wait(lambda: False, 0.4)
        self.assertEqual(self.heard[start:], [True])
        self.proxy.close()
        self.assertTrue(self.wait(lambda: self.heard[start:] == [True, False]))


class SpotlightLinesThere(unittest.TestCase):
    """Lines no proxy would send, written to the other program as they are."""

    def setUp(self):
        self.addCleanup(setattr, ring, "HELPER_PLATFORM", ring.HELPER_PLATFORM)
        ring.HELPER_PLATFORM = "offscreen"
        with mock.patch.object(ring.RingProxy, "_start", lambda proxy: None):
            self.proxy = ring.RingProxy()
        self.proxy._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)  # its log, here
        self.proxy._start()
        self.addCleanup(self.proxy.shutdown)
        self.heard = []
        self.proxy.opened.connect(self.heard.append)
        self.proxy.configure({"size": 1.0, "style": "bubbles", "bubbles": RING})

    def wait(self, condition, seconds=15.0):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(0.01)
        return condition()

    def test_anything_in_an_effect_line_is_dropped_without_a_word(self):
        self.assertTrue(self.wait(lambda: self.proxy.available))
        looks = [[1], {"a": 1}, True, None, "laser", 7, dict(LASER, effect=["laser"]), dict(LASER, effect={"x": 1}),
                 dict(LASER, size=True), dict(LASER, color=["#000000"]), dict(LASER, color={"x": 1}), dict(LASER, color=True)]
        for look in looks:
            self.proxy._send("effect", event="down", look=look)
            self.proxy._send("effect", event="up", look=None)
        for event in ([1], {"a": 1}, None, 5):
            self.proxy._send("effect", event=event, look=LASER)
        self.proxy._send("effect", event="up")
        self.assertTrue(self.proxy.open())
        self.assertTrue(self.wait(lambda: self.heard == [True]), "it no longer answers")
        self.proxy.close()
        self.assertTrue(self.wait(lambda: self.heard == [True, False]))
        log = bytes(self.proxy._process.readAllStandardError()).decode("utf-8", "replace")
        self.assertNotIn("could not be followed", log)
        self.assertNotIn("Traceback", log)


class SpotlightAcrossThePipeLate(unittest.TestCase):
    """What the other program says reaches this side late, out of step, or not at all."""

    def setUp(self):
        with mock.patch.object(ring.RingProxy, "_start", lambda proxy: None):
            self.proxy = ring.RingProxy()
        self.addCleanup(self.proxy.deleteLater)
        self.sent, self.heard = [], []
        self.proxy._send = lambda do, **more: self.sent.append(do)
        self.proxy.opened.connect(self.heard.append)
        self.proxy.configure({"bubbles": RING})
        self.proxy._closing = True  # nothing is started again here

    def say(self, *lines):
        data = b"".join(json.dumps(line).encode("utf-8") + b"\n" for line in lines)
        with mock.patch.object(self.proxy._process, "readAllStandardOutput", lambda: data):
            self.proxy._read()

    def test_word_of_rings_never_moves_the_hold(self):
        self.proxy.effect("down", HIGHLIGHT)  # not there yet: nothing is held
        self.assertFalse(self.proxy._effect)
        self.say({"event": "ready", "available": True})
        self.assertTrue(self.proxy.open())
        self.proxy.close()
        self.proxy.effect("down", HIGHLIGHT)
        self.say({"event": "opened", "on": True}, {"event": "opened", "on": False})  # of the ring before
        self.assertTrue(self.proxy._effect)
        self.assertFalse(self.proxy.open())
        self.proxy.effect("up")
        self.assertTrue(self.proxy.open())
        self.say({"event": "opened", "on": True}, {"event": "opened", "on": False}, {"event": "opened", "on": True})
        self.assertFalse(self.proxy._effect)
        self.say({"event": "opened", "on": False})
        self.proxy.effect("down", HIGHLIGHT)
        self.proxy._ended()  # the other program ended while the effect showed there
        self.assertFalse(self.proxy._effect)
        self.assertEqual(self.heard, [True, False, True, False, True, False])
        self.assertEqual((self.sent.count("open"), self.sent.count("effect")), (2, 3))


# -- the picker --------------------------------------------------------------------------------------------------------

class SpotlightPicker(unittest.TestCase):

    def picker(self, **kwargs):
        made = action_picker.ActionPicker(**kwargs)
        self.addCleanup(made.deleteLater)
        made.resize(320, 700)
        made.show()
        made.emitted = []
        made.changed.connect(made.emitted.append)
        return made

    def kind(self, kind, compositing=True):
        for target, name, value in ((ring, "overlay_kind", lambda: kind), (ring.x11, "compositing", lambda: compositing)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_mouse_button_offers_it_and_sets_up_each_effect(self):
        self.kind(ring.XWAYLAND)
        made = self.picker(allow_button_actions=True)
        made.edit("Back", {"type": "default"})
        row = made.list._rows["spotlight"]
        self.assertFalse(row.isHidden())
        self.assertTrue(made.spotlight_block.isHidden())
        row.click()
        self.assertEqual(made.emitted, [config.default_spotlight()])
        self.assertFalse(made.spotlight_block.isHidden())
        row.click()  # picked again: kept as it is
        self.assertEqual(len(made.emitted), 1)
        chips = {name: chip for name, (chip, _value) in made._effect_rows.items()}
        self.assertEqual(chips["laser"].accessibleName(), "Digital Laser: Off")
        chips["highlight"].click()
        self.assertEqual(made.stack.currentIndex(), 4)
        self.assertFalse(made.effect_switch.isEnabled())  # the one effect on stays on
        chips["laser"].click()
        self.assertEqual(made.effect_title.text(), "Digital Laser")
        self.assertTrue(made.effect_switch.isEnabled())
        QTest.mouseClick(made.effect_switch, Qt.MouseButton.LeftButton)
        self.assertEqual(made.emitted[-1], spotlight(laser={"on": True}))
        made.effect_size.slider.setValue(made.effect_size.slider.value() + 7)
        self.assertEqual(made.emitted[-1], spotlight(laser={"on": True, "size": 57}))
        swatch = next(s for s in made.effect_color.findChildren(QAbstractButton) if s.color == "#4c9bff")
        self.assertEqual(swatch.accessibleName(), "#4c9bff")
        swatch.click()
        self.assertEqual(made.emitted[-1], spotlight(laser={"on": True, "size": 57, "color": "#4c9bff"}))
        self.assertEqual(chips["laser"].accessibleName(), "Digital Laser: On")
        self.assertTrue(made.effect_note.isHidden())
        chips["magnify"].click()
        self.assertEqual((made.effect_title.text(), made.effect_note.text()), ("Still Magnify", "Not available on Wayland desktops."))
        made.edit("Forward", {"type": "default"})
        self.assertEqual(made.stack.currentIndex(), 0)
        self.assertTrue(made.spotlight_block.isHidden())

    def test_the_note_says_what_this_desktop_shows(self):
        for kind, compositing, magnify in ((ring.X11, True, "Shows the screen as it was when you pressed"),
                                            (ring.X11, False, "Not available on this desktop."),
                                            (ring.WINDOW, True, "Not available on Wayland desktops.")):
            with self.subTest(kind=kind, compositing=compositing):
                self.kind(kind, compositing)
                made = self.picker()
                made.edit("Back", config.default_spotlight())
                made._effect_rows["magnify"][0].click()
                self.assertTrue(made.effect_note.text().startswith(magnify))
                made._effect_rows["highlight"][0].click()
                self.assertEqual(made.effect_note.isHidden(), compositing and kind == ring.X11)

    def test_keys_profiles_ring_bubbles_macro_steps_and_gestures_do_not_offer_it(self):
        keyboard = self.picker(allow_button_actions=True, exclude=keyboard_page.KEYBOARD_EXCLUDE)
        self.assertNotIn("spotlight", keyboard.list._rows)
        steps = self.picker(allow_button_actions=False, exclude=editors.STEP_EXCLUDE)
        self.assertNotIn("spotlight", steps.list._rows)
        self.assertNotIn("spotlight", steps.sub_list._rows)
        editor = ring_page.BubbleEditor()
        self.addCleanup(editor.deleteLater)
        self.assertNotIn("spotlight", editor.actions._rows)
        store = Store(copy.deepcopy(config.DEFAULTS), _Engine())
        store.flush = lambda: None
        store._save_timer.timeout.disconnect()
        store.front = FakeFront()
        page = pages.ButtonsPage(store)
        self.addCleanup(gone, page)
        page.resize(1100, 700)
        page.show()
        page.view.select("back")
        self.assertFalse(page.picker.list._rows["spotlight"].isHidden())
        store.front.last_other = KATE
        store.front.changed.emit()
        QTest.mouseClick(page.apps.add_app, Qt.MouseButton.LeftButton)
        self.assertEqual(store.mouse_app, KATE)
        self.assertTrue(page.picker.list._rows["spotlight"].isHidden())


if __name__ == "__main__":
    unittest.main()
