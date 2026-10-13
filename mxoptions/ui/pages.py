"""The pages of the main window."""

import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__, actions, api, config, desktop, device, firmware
from ..i18n import _
from .action_picker import ActionPicker
from . import mouse_photo
from .mouse_view import MouseView
from . import theme
from . import tray_icon
from .widgets import (
    Card, ElidedLabel, IconChoice, InfoButton, PatternRow, Row, Segmented, SliderRow, SwatchRow, Switch, hint_label, titled,
)

BUTTON_TITLES = {
    "middle": "Middle button",
    "smartshift": "Wheel mode button",
    "gesture": "Gesture button",
    "forward": "Forward button",
    "back": "Back button",
    "panel": "Haptic panel",
}
# The two main buttons: (title, what it does)
FIXED_BUTTONS = {"left": ("Left button", "Click"), "right": ("Right button", "Right click")}
BUTTON_HINTS = {
    "middle": "Press the scroll wheel.",
    "smartshift": "The button behind the scroll wheel. Default switches between ratchet and free spin.",
    "gesture": "Hold it and move the mouse, or just tap it.",
    "forward": "",
    "back": "",
    "panel": "The pressure-sensitive pad under your thumb.",
}
THUMB_LABELS = {
    "hscroll": "Horizontal scroll",
    "zoom": "Zoom",
    "volume": "Volume",
    "tabs": "Switch tabs",
    "desktops": "Switch desktops",
    "brightness": "Brightness",
}
HAPTIC_PRESETS = [(25, "Subtle"), (45, "Low"), (60, "Medium"), (100, "High")]
CLICK_MODE_LABELS = [("off", "Off"), ("haptic", "Vibration"), ("sound", "Sound"), ("both", "Both")]
CLICK_SOUND_LABELS = [("tick", "Tick"), ("clack", "Clack"), ("relay", "Relay"), ("soft", "Soft click"),
                      ("custom", "Your own file…")]
CLICK_MOMENT_LABELS = [("both", "Letting go and stopping"), ("free", "Letting go"), ("ratchet", "Stopping")]
MODEL_LABELS = [("photo", "Photo"), ("studio", "3D"), ("lowpoly", "Low-poly"), ("outline", "Outline"), ("wire", "Wireframe")]
COLOR_LABELS = [("graphite", "Graphite"), ("pale-grey", "Pale grey"), ("black", "Black")]
TRAY_LABELS = [("outline", "Outline"), ("solid", "Solid"), ("master", "MX Master"), ("battery", "Battery level"),
               ("logo", "App icon")]


def _header(title, about=None):
    """The title of a page, with what the page is for behind an info button."""
    label = QLabel(title)
    label.setObjectName("pageTitle")
    return titled(label, InfoButton(about))


class ScrollPage(QWidget):
    """Page whose cards scroll vertically."""

    def __init__(self, title, subtitle=None, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(34, 28, 34, 28)
        self.body.setSpacing(16)
        self.body.addLayout(_header(title, subtitle))
        scroll.setWidget(body)
        outer.addWidget(scroll)

    def finish(self):
        self.body.addStretch(1)


class ButtonsPage(QWidget):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._key = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        left = QVBoxLayout()
        left.setContentsMargins(34, 28, 24, 20)
        left.setSpacing(8)
        left.addLayout(_header(_("Buttons"), _("Pick a button to choose what it does.")))
        titles = {key: _(title) for key, (title, _value) in FIXED_BUTTONS.items()}
        titles.update((key, _(title)) for key, title in BUTTON_TITLES.items())
        self.view = MouseView(titles)
        left.addWidget(self.view, 1)
        # The picture of the real mouse belongs to Logitech and is fetched on request, not shipped.
        self.photo_offer = QWidget()
        offer = QHBoxLayout(self.photo_offer)
        offer.setContentsMargins(0, 0, 0, 0)
        offer.setSpacing(12)
        self.photo_text = hint_label("")
        offer.addWidget(self.photo_text, 1)
        self.photo_button = QPushButton(_("Download"))
        self.photo_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.photo_button.clicked.connect(self._download_photo)
        offer.addWidget(self.photo_button)
        left.addWidget(self.photo_offer)
        self.photos = mouse_photo.Downloader(self)
        self.photos.finished.connect(self._photo_downloaded)
        self._photo_error = ""
        layout.addLayout(left, 1)

        self.picker = ActionPicker(allow_button_actions=True)
        layout.addWidget(self.picker)

        self.view.selected.connect(self._select)
        self.picker.changed.connect(self._changed)
        store.changed.connect(self._refresh)
        store.state_changed.connect(self._state)
        store.pressed_changed.connect(self.view.set_pressed)
        self._refresh()
        self.view.select("gesture")

    def _select(self, key):
        self._key = key
        if key in FIXED_BUTTONS:
            title, value = FIXED_BUTTONS[key]
            self.picker.show_fixed(_(title), _(value), _(
                "The mouse handles its two main buttons itself and does not let any software take them over, "
                "so this one cannot be given another action."
            ) + "\n\n" + _("The other way round works: any other button can be made a left or right click in its own list."))
        else:
            self.picker.edit(_(BUTTON_TITLES[key]), self.store.cfg["buttons"][key], _(BUTTON_HINTS[key]) if BUTTON_HINTS[key] else "")

    def _changed(self, action):
        if self._key and self._key not in FIXED_BUTTONS:
            self.store.set_button(self._key, action)

    def _download_photo(self):
        self._photo_error = ""
        self.photos.start()
        self._refresh()

    def _photo_downloaded(self, error):
        self._photo_error = error
        self._refresh()

    def _refresh(self):
        self.view.set_style(self.store.cfg["app"]["model"], self.store.cfg["app"]["mouse_color"])
        family = device.family(self.store.state.get("name") or "")
        self.view.set_family(family)
        self.photo_offer.setVisible(self.store.cfg["app"]["model"] == "photo" and not mouse_photo.available() and family == "mx4")
        self.photo_button.setEnabled(not self.photos.running)
        if self.photos.running:
            self.photo_text.setText(_("Downloading the pictures…"))
        elif self._photo_error:
            self.photo_text.setText(_("Could not download the pictures: {}").format(self._photo_error))
        else:
            self.photo_text.setText(_(
                "Logitech's own picture of the mouse can be shown here instead of this model. It is not included "
                "with the program: download it from logitech.com (about 2.5 MB)."))
        for key in self.view.keys():
            if key in FIXED_BUTTONS:
                self.view.set_value(key, _(FIXED_BUTTONS[key][1]))
            else:
                self.view.set_value(key, actions.describe(self.store.cfg["buttons"][key]))

    def _state(self, state):
        caps = state.get("caps")
        if caps:
            self.view.set_visible_keys(set(caps["buttons"]))
            self._refresh()  # another mouse may need another picture


class PointScrollPage(ScrollPage):
    def __init__(self, store, parent=None):
        super().__init__(_("Point & scroll"), _("Pointer speed and how the wheels behave."), parent)
        self.store = store

        # pointer
        card = Card(_("Pointer"))
        self.dpi = card.add(SliderRow(_("Pointer speed"), 200, 8000, 50, "{:.0f} DPI"))
        self.alt_dpi = card.add(SliderRow(
            _("Second speed"), 200, 8000, 50, "{:.0f} DPI",
            hint=_("Used by the \"Switch pointer speed\" button action."),
        ))
        self.precision_dpi = card.add(SliderRow(
            _("Precision speed"), 200, 8000, 50, "{:.0f} DPI",
            hint=_("Used while a \"Precision mode\" button is held."),
        ))
        for key, slider in (("dpi", self.dpi), ("alt_dpi", self.alt_dpi), ("precision_dpi", self.precision_dpi)):
            slider.changed.connect(lambda value, k=key: self.store.set("pointer", k, int(value)))
        self.body.addWidget(card)

        # main wheel
        card = Card(_("Scroll wheel"))
        self.direction = Segmented([(False, _("Standard")), (True, _("Natural"))])
        self.direction.selected.connect(lambda value: self.store.set("wheel", "natural", value))
        card.add(Row(_("Scroll direction"), None, self.direction))

        self.smartshift = Switch()
        self.smartshift.toggled.connect(lambda on: self.store.set("wheel", "smartshift", on))
        self.smartshift_row = card.add(Row(
            _("SmartShift"), _("Spin fast and the wheel releases into free spin on its own."), self.smartshift,
        ))
        self.threshold = card.add(SliderRow(
            _("SmartShift sensitivity"), 1, 50, 1, "{:.0f}", low_label=_("Shifts early"), high_label=_("Shifts late"),
        ))
        self.threshold.changed.connect(lambda value: self.store.set("wheel", "threshold", int(value)))
        self.mode = Segmented([("ratchet", _("Ratchet")), ("freespin", _("Free spin"))])
        self.mode.selected.connect(lambda value: self.store.set("wheel", "mode", value))
        self.mode_row = card.add(Row(_("Wheel mode"), _("Used when SmartShift is off."), self.mode))
        self.torque = card.add(SliderRow(
            _("Ratchet force"), 1, 100, 1, "{:.0f}%", low_label=_("Soft"), high_label=_("Firm"),
        ))
        self.torque.changed.connect(lambda value: self.store.set("wheel", "torque", int(value)))

        self.smooth = Switch()
        self.smooth.toggled.connect(lambda on: self.store.set("wheel", "smooth", on))
        self.smooth_row = card.add(Row(
            _("Smooth scrolling"),
            _("High-resolution scrolling. The wheel is then handled by this app, so it only scrolls while the app runs."),
            self.smooth,
        ))
        self.wheel_speed = card.add(SliderRow(_("Scroll speed"), 0.25, 3.0, 0.05, "{:.2f}×"))
        self.wheel_speed.changed.connect(lambda value: self.store.set("wheel", "speed", round(value, 2)))
        self.wheel_card = card
        self.body.addWidget(card)

        # the wheel changing mode
        card = Card(_("Mode switch click"))
        self.click_mode = Segmented([(key, _(label)) for key, label in CLICK_MODE_LABELS])
        self.click_mode.selected.connect(lambda value: self.store.set("wheel_click", "mode", value))
        card.add(Row(
            _("When the wheel changes mode"),
            _("One short tick as SmartShift lets the wheel go and as it catches again, like the clutch of earlier MX Master mice."),
            self.click_mode,
        ))
        self.click_moments = Segmented([(key, _(label)) for key, label in CLICK_MOMENT_LABELS])
        self.click_moments.selected.connect(lambda value: self.store.set("wheel_click", "moments", value))
        self.click_moments_row = card.add(Row(
            _("Which moments"), _("A quick stroke that ends at once gets a single tick either way."), self.click_moments))
        self.click_patterns = QWidget()
        grid = QGridLayout(self.click_patterns)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(34)
        self.click_rows = {}
        for column, (key, title) in enumerate((("free", _("Into free spin")), ("ratchet", _("Back to ratchet")))):
            row = PatternRow(title)
            row.changed.connect(lambda waveform, k=key: self._click_pattern(k, waveform))
            row.played.connect(self.store.engine.play_haptic)
            self.click_rows[key] = row
            grid.addWidget(row, 0, column)
        card.add(self.click_patterns)
        sound = QWidget()
        sound_layout = QHBoxLayout(sound)
        sound_layout.setContentsMargins(0, 0, 0, 0)
        sound_layout.setSpacing(8)
        self.click_file = ElidedLabel(mode=Qt.TextElideMode.ElideMiddle)
        self.click_file.setObjectName("rowHint")
        self.click_file.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sound_layout.addWidget(self.click_file, 1)
        self.click_sound = QComboBox()
        for key, label in CLICK_SOUND_LABELS:
            self.click_sound.addItem(_(label), key)
        self.click_sound.activated.connect(lambda index: self._click_sound(self.click_sound.itemData(index)))
        sound_layout.addWidget(self.click_sound)
        self.click_play = QPushButton("▶")
        self.click_play.setObjectName("tool")
        self.click_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.click_play.setToolTip(_("Play"))
        self.click_play.clicked.connect(self._play_click)
        sound_layout.addWidget(self.click_play)
        self.click_sound_row = card.add(Row(_("Sound"), None, sound))
        self.click_sound_row.layout().setStretch(1, 1)
        self.click_volume = card.add(SliderRow(_("Volume"), 0, 100, 1, "{:.0f}%", low_label=_("Quieter"), high_label=_("Louder")))
        self.click_volume.changed.connect(lambda value: self.store.set("wheel_click", "volume", round(value / 100.0, 2)))
        self.click_volume.released.connect(lambda _value: self._play_click(both=False))
        self.click_pitch = card.add(SliderRow(_("Tone"), 0.6, 1.8, 0.05, "{:.0%}", low_label=_("Lower"), high_label=_("Higher")))
        self.click_pitch.changed.connect(lambda value: self.store.set("wheel_click", "pitch", round(value, 2)))
        self.click_pitch.released.connect(lambda _value: self._play_click(both=False))
        self.click_length = card.add(SliderRow(_("Length"), 0.5, 2.5, 0.1, "{:.0%}", low_label=_("Shorter"), high_label=_("Longer")))
        self.click_length.changed.connect(lambda value: self.store.set("wheel_click", "length", round(value, 1)))
        self.click_length.released.connect(lambda _value: self._play_click(both=False))
        self.click_card = card
        self.body.addWidget(card)

        # thumb wheel
        card = Card(_("Thumb wheel"))
        self.thumb_function = QComboBox()
        for key in config.THUMB_FUNCTIONS:
            self.thumb_function.addItem(_(THUMB_LABELS[key]), key)
        self.thumb_function.activated.connect(
            lambda index: self.store.set("thumbwheel", "function", self.thumb_function.itemData(index))
        )
        card.add(Row(_("Function"), None, self.thumb_function))
        self.thumb_invert = Switch()
        self.thumb_invert.toggled.connect(lambda on: self.store.set("thumbwheel", "invert", on))
        card.add(Row(_("Reverse direction"), None, self.thumb_invert))
        self.thumb_speed = card.add(SliderRow(_("Speed"), 0.25, 3.0, 0.05, "{:.2f}×"))
        self.thumb_speed.changed.connect(lambda value: self.store.set("thumbwheel", "speed", round(value, 2)))
        self.thumb_ticks = Switch()
        self.thumb_ticks.toggled.connect(lambda on: self.store.set("thumbwheel", "ticks", on))
        self.thumb_ticks_row = card.add(Row(
            _("Feel the steps"),
            _("This wheel turns smoothly, so each step of volume, tabs, desktops, brightness or zoom gets a tick."),
            self.thumb_ticks,
        ))
        self.thumb_card = card
        self.body.addWidget(card)
        self.finish()

        store.changed.connect(self._refresh)
        store.state_changed.connect(self._state)
        self._refresh()

    # -- the mode switch click ----------------------------------------------------

    def _click_pattern(self, key, waveform):
        self.store.set("wheel_click", key, waveform)
        if waveform is not None:
            self.store.engine.play_haptic(waveform)

    def _click_sound(self, sound):
        if sound == "custom":
            path, _filter = QFileDialog.getOpenFileName(
                self, _("Choose a sound"), self.store.cfg["wheel_click"]["file"] or "", _("Sounds (*.wav)"))
            if not path:
                self._refresh()  # put the previous choice back
                return
            self.store.set("wheel_click", "file", path)
        self.store.set("wheel_click", "sound", sound)
        self._play_click()

    def _play_click(self, both=True):
        """Let the user hear the pair: letting go, then catching again."""
        sounds = self.store.sounds
        if sounds is None:
            return
        sounds.play("freespin")
        if both:
            QTimer.singleShot(420, lambda: sounds.play("ratchet"))

    def _refresh(self):
        cfg = self.store.cfg
        click = cfg["wheel_click"]
        self.click_mode.set_value(click["mode"])
        for key, row in self.click_rows.items():
            row.set_value(click[key])
        self.click_patterns.setEnabled(click["mode"] in ("haptic", "both") and cfg["haptics"]["enabled"])
        self.click_sound.setCurrentIndex(max(0, self.click_sound.findData(click["sound"])))
        self.click_file.setText(click["file"] if click["sound"] == "custom" else "")
        heard = click["mode"] in ("sound", "both")
        self.click_moments.set_value(click["moments"])
        self.click_moments_row.setEnabled(click["mode"] != "off")
        self.click_sound_row.setEnabled(heard)
        self.click_volume.setEnabled(heard)
        self.click_volume.set_value(round(click["volume"] * 100))
        for slider, key in ((self.click_pitch, "pitch"), (self.click_length, "length")):
            slider.set_value(click[key])
            slider.setEnabled(heard and click["sound"] != "custom")  # a file of the user's own is played as it is
        self.thumb_ticks.set_checked_silently(cfg["thumbwheel"]["ticks"])
        self.thumb_ticks_row.setEnabled(cfg["thumbwheel"]["function"] != "hscroll" and cfg["haptics"]["enabled"])
        self.dpi.set_value(cfg["pointer"]["dpi"])
        self.alt_dpi.set_value(cfg["pointer"]["alt_dpi"])
        self.precision_dpi.set_value(cfg["pointer"]["precision_dpi"])
        wheel = cfg["wheel"]
        self.direction.set_value(wheel["natural"])
        self.smartshift.set_checked_silently(wheel["smartshift"])
        self.threshold.set_value(wheel["threshold"])
        self.threshold.setEnabled(wheel["smartshift"])
        self.mode.set_value(wheel["mode"])
        self.mode_row.setEnabled(not wheel["smartshift"])
        self.torque.set_value(wheel["torque"])
        self.smooth.set_checked_silently(wheel["smooth"])
        self.wheel_speed.set_value(wheel["speed"])
        self.wheel_speed.setEnabled(wheel["smooth"])
        thumb = cfg["thumbwheel"]
        self.thumb_function.setCurrentIndex(max(0, self.thumb_function.findData(thumb["function"])))
        self.thumb_invert.set_checked_silently(thumb["invert"])
        self.thumb_speed.set_value(thumb["speed"])

    def _state(self, state):
        caps = state.get("caps")
        if not caps:
            return
        if caps["dpi"]:
            low, high, step = caps["dpi"]
            for slider in (self.dpi, self.alt_dpi, self.precision_dpi):
                slider.set_range(low, high, step or 50)
            self._refresh()
        self.torque.setVisible(caps["torque"])
        self.smooth_row.setEnabled(state["uinput"])
        self.wheel_card.setVisible(caps["smartshift"] or caps["wheel"])
        self.click_card.setVisible(caps.get("ratchet_events", False))
        has_haptics = bool(caps["waveforms"])
        self.click_patterns.setVisible(has_haptics)
        self.click_mode.set_option_enabled("haptic", has_haptics)
        self.click_mode.set_option_enabled("both", has_haptics)
        for row in self.click_rows.values():
            row.set_waveforms(caps["waveforms"])
        self.thumb_ticks_row.setVisible(has_haptics)
        self.thumb_card.setVisible(caps["thumbwheel"])
        self._refresh()


class HapticsPage(ScrollPage):
    def __init__(self, store, parent=None):
        super().__init__(_("Haptics"), _("Vibration feedback from the thumb panel."), parent)
        self.store = store

        card = Card(_("Feedback"))
        self.enabled = Switch()
        self.enabled.toggled.connect(lambda on: self.store.set("haptics", "enabled", on))
        card.add(Row(_("Haptic feedback"), None, self.enabled))
        self.presets = Segmented([(value, _(label)) for value, label in HAPTIC_PRESETS])
        self.presets.selected.connect(self._preset)
        self.presets_row = card.add(Row(_("Intensity"), None, self.presets))
        self.level = card.add(SliderRow(_("Fine tune"), 1, 100, 1, "{:.0f}%"))
        self.level.changed.connect(lambda value: self.store.set("haptics", "level", int(value)))
        self.level.released.connect(lambda _value: self.store.engine.play_haptic(device.WAVE_STATE))
        self.feedback = Switch()
        self.feedback.toggled.connect(lambda on: self.store.set("haptics", "feedback", on))
        self.feedback_row = card.add(Row(
            _("Feedback for actions"), _("A tick for gestures, the Actions Ring and mode changes."), self.feedback,
        ))
        self.feedback_card = card
        self.body.addWidget(card)

        card = Card(_("Haptic panel"))
        self.force = card.add(SliderRow(
            _("Press force"), 0, 100, 1, "{:.0f}%", hint=_("How hard you have to press the thumb panel."),
            low_label=_("Light"), high_label=_("Firm"),
        ))
        self.force.changed.connect(self._force_changed)
        self.force_card = card
        self.body.addWidget(card)

        card = Card(_("Try a pattern"))
        self.patterns = QWidget()
        self.pattern_grid = QGridLayout(self.patterns)
        self.pattern_grid.setContentsMargins(0, 0, 0, 0)
        self.pattern_grid.setSpacing(8)
        card.add_plain(self.patterns)
        self.pattern_card = card
        self._pattern_ids = None
        self.body.addWidget(card)

        card = Card(_("Game rumble"), hint=_(
            "Games vibrate game controllers, not mice. With this on, MX Options adds a virtual gamepad to the system; "
            "what a game sends to it is played on the mouse. A game then sees a controller: some show gamepad prompts "
            "because of it, and some only vibrate a controller that is being played with. Switch it off if a game "
            "misbehaves."
        ))
        self.rumble = Switch()
        self.rumble.toggled.connect(lambda on: self.store.set("rumble", "enabled", on))
        card.add(Row(_("Vibrate with games"), None, self.rumble))
        self.rumble_rows = {}
        for key, title in (("light", _("Weak rumble")), ("medium", _("Medium rumble")), ("strong", _("Strong rumble"))):
            row = PatternRow(title)
            row.changed.connect(lambda waveform, k=key: self.store.set("rumble", k, waveform))
            row.played.connect(self.store.engine.play_haptic)
            self.rumble_rows[key] = card.add(row)
        self.rumble_interval = card.add(SliderRow(
            _("Pulse every"), 60, 300, 10, _("{:.0f} ms"),
            hint=_("The motor plays short patterns, not a hum: a rumble that lasts is a row of them, this far apart.")))
        self.rumble_interval.changed.connect(lambda value: self.store.set("rumble", "interval", int(value)))
        self.rumble_gain = card.add(SliderRow(
            _("Sensitivity"), 50, 200, 10, "{:.0f}%",
            hint=_("Higher, and a weaker rumble from the game already counts as medium or strong.")))
        self.rumble_gain.changed.connect(lambda value: self.store.set("rumble", "gain", value / 100.0))
        rumble_test = QPushButton(_("Play"))
        rumble_test.setCursor(Qt.CursorShape.PointingHandCursor)
        rumble_test.clicked.connect(lambda: self.store.engine.rumble_test())
        self.rumble_test_row = card.add(Row(_("Try it"), _("A rumble that grows from weak to strong, as a game would send it."), rumble_test))
        rumble_nudge = QPushButton(_("Move its stick"))
        rumble_nudge.setCursor(Qt.CursorShape.PointingHandCursor)
        rumble_nudge.clicked.connect(lambda: self.store.engine.rumble_nudge())
        self.rumble_nudge_row = card.add(Row(
            _("Make it noticed"),
            _("A gamepad tester in a browser, and some games, take no notice of a controller until something on it "
              "moves. This moves the virtual gamepad's left stick for a moment. Press it while the tester or the game "
              "is open."),
            rumble_nudge))
        self.rumble_card = card
        self.body.addWidget(card)

        self.missing = hint_label(_("This mouse has no haptic motor."))
        self.missing.hide()
        self.body.addWidget(self.missing)
        self.finish()

        store.changed.connect(self._refresh)
        store.state_changed.connect(self._state)
        self._force_range = None
        self._refresh()

    def _preset(self, value):
        self.store.set("haptics", "level", value)
        self.store.engine.play_haptic(device.WAVE_STATE)

    def _force_changed(self, percent):
        if self._force_range:
            low, high = self._force_range
            self.store.set("haptics", "force", int(round(low + (high - low) * percent / 100.0)))

    def _refresh(self):
        haptics = self.store.cfg["haptics"]
        self.enabled.set_checked_silently(haptics["enabled"])
        self.presets.set_value(haptics["level"])
        self.level.set_value(haptics["level"])
        self.feedback.set_checked_silently(haptics["feedback"])
        for widget in (self.presets_row, self.level, self.feedback_row, self.pattern_card):
            widget.setEnabled(haptics["enabled"])
        if self._force_range and haptics.get("force"):
            low, high = self._force_range
            self.force.set_value(round((haptics["force"] - low) * 100.0 / max(1, high - low)))
        rumble = self.store.cfg["rumble"]
        self.rumble.set_checked_silently(rumble["enabled"])
        for key, row in self.rumble_rows.items():
            row.set_value(rumble[key])
        self.rumble_interval.set_value(rumble["interval"])
        self.rumble_gain.set_value(round(rumble["gain"] * 100))
        for widget in (*self.rumble_rows.values(), self.rumble_interval, self.rumble_gain, self.rumble_nudge_row):
            widget.setEnabled(rumble["enabled"])

    def _state(self, state):
        caps = state.get("caps")
        if not caps:
            return
        has_haptics = bool(caps["waveforms"])
        self.feedback_card.setVisible(has_haptics)
        self.pattern_card.setVisible(has_haptics)
        self.rumble_card.setVisible(has_haptics)
        for row in self.rumble_rows.values():
            row.set_waveforms(caps["waveforms"] or list(device.WAVEFORMS))
        self.missing.setVisible(not has_haptics and not caps["force"])
        self.force_card.setVisible(bool(caps["force"]))
        if caps["force"]:
            self._force_range = (caps["force"]["min"], caps["force"]["max"])
        if has_haptics and self._pattern_ids != caps["waveforms"]:
            self._pattern_ids = list(caps["waveforms"])
            while self.pattern_grid.count():
                self.pattern_grid.takeAt(0).widget().deleteLater()
            for index, waveform in enumerate(self._pattern_ids):
                button = QPushButton(_(device.WAVEFORMS[waveform]))
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(lambda _checked=False, w=waveform: self.store.engine.play_haptic(w))
                self.pattern_grid.addWidget(button, index // 4, index % 4)
        self._refresh()


class FirmwareQuery(QObject):
    """Asks fwupd about the mouse off the GUI thread; ``answered`` carries (entries, was it a check for updates)."""

    answered = Signal(object, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = False

    def start(self, name, check):
        if self.running:
            return
        self.running = True

        def job():
            entries = firmware.check(name) if check else firmware.installed(name)
            self.running = False
            self.answered.emit(entries, check)

        threading.Thread(target=job, name="firmware", daemon=True).start()


class DevicePage(ScrollPage):
    def __init__(self, store, parent=None):
        super().__init__(_("Device & app"), None, parent)
        self.store = store

        card = Card(_("Easy-Switch"), hint=_(
            "Move the mouse to another paired computer. It disconnects from this one until you switch back "
            "with the button on its underside."
        ))
        self.hosts = QWidget()
        self.hosts_layout = QHBoxLayout(self.hosts)
        self.hosts_layout.setContentsMargins(0, 0, 0, 0)
        self.hosts_layout.setSpacing(10)
        card.add_plain(self.hosts)
        self.hosts_card = card
        self._hosts_shown = None
        self.body.addWidget(card)

        # firmware: read and updated through fwupd, never flashed by this program
        card = Card(_("Firmware"))
        self.firmware_check = QPushButton(_("Check for updates"))
        self.firmware_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.firmware_check.clicked.connect(lambda: self._ask_firmware(check=True))
        self.firmware_row = card.add(Row(_("Firmware"), None, self.firmware_check, note=" "))
        command = QWidget()
        command_layout = QHBoxLayout(command)
        command_layout.setContentsMargins(0, 0, 0, 0)
        command_layout.setSpacing(10)
        self.firmware_command = ElidedLabel(mode=Qt.TextElideMode.ElideMiddle)
        self.firmware_command.setObjectName("summary")
        self.firmware_command.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        command_layout.addWidget(self.firmware_command, 1)
        copy_command = QPushButton(_("Copy"))
        copy_command.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_command.clicked.connect(lambda: QApplication.clipboard().setText(self.firmware_command.full_text()))
        command_layout.addWidget(copy_command)
        self.firmware_command_row = card.add_plain(command)
        self.firmware_card = card
        self.firmware = FirmwareQuery(self)
        self.firmware.answered.connect(self._firmware_answered)
        self._firmware_entries, self._firmware_checked, self._firmware_for = [], False, None
        self.body.addWidget(card)

        card = Card(_("Appearance"))
        self.theme = Segmented([(key, _(label)) for key, label in theme.THEME_LABELS])
        self.theme.selected.connect(lambda value: self.store.set("app", "theme", value))
        card.add(Row(_("Theme"), None, self.theme))
        accent = QWidget()
        accent_layout = QHBoxLayout(accent)
        accent_layout.setContentsMargins(0, 0, 0, 0)
        accent_layout.setSpacing(8)
        self.accent = SwatchRow(theme.ACCENTS)
        self.accent.selected.connect(lambda color: self.store.set("app", "accent", color))
        accent_layout.addWidget(self.accent)
        custom = QPushButton(_("Custom…"))
        custom.setCursor(Qt.CursorShape.PointingHandCursor)
        custom.clicked.connect(self._custom_accent)
        accent_layout.addWidget(custom)
        card.add(Row(_("Accent colour"), None, accent))
        self.model = Segmented([(key, _(label)) for key, label in MODEL_LABELS])
        self.model.selected.connect(lambda value: self.store.set("app", "model", value))
        card.add(Row(_("Mouse model"), _("How the mouse is drawn on the Buttons page."), self.model))
        self.mouse_color = Segmented([(key, _(label)) for key, label in COLOR_LABELS])
        self.mouse_color.selected.connect(lambda value: self.store.set("app", "mouse_color", value))
        self.mouse_color_row = card.add(Row(_("Mouse colour"), None, self.mouse_color))
        self.tray = IconChoice()
        self.tray.selected.connect(lambda value: self.store.set("app", "tray_icon", value))
        self._tray_look = None
        card.add(Row(
            _("Tray icon"),
            _("The plain ones take the colour of your panel. They turn faint while no mouse is connected and get a red "
              "mark when its battery is low; “Battery level” fills up with the charge."),
            self.tray))
        self.body.addWidget(card)

        card = Card(_("Application"))
        self.autostart = Switch()
        self.autostart.set_checked_silently(desktop.autostart_enabled())
        self.autostart.toggled.connect(self._guard(desktop.set_autostart, self.autostart))
        card.add(Row(_("Start at login"), _("Runs quietly in the system tray."), self.autostart))
        self.launcher = Switch()
        self.launcher.set_checked_silently(desktop.launcher_enabled())
        self.launcher.toggled.connect(self._guard(desktop.set_launcher, self.launcher))
        launcher_row = card.add(Row(_("Show in the application menu"), None, self.launcher))
        launcher_row.setVisible(not desktop.launcher_managed())  # a system-wide installation has its own entry
        self.language = Segmented([("auto", _("Auto")), ("en", "English"), ("uk", "Українська")])
        self.language.selected.connect(self._language)
        self.language_row = card.add(Row(_("Language"), _("Takes effect after restarting the app."), self.language))
        reset = QPushButton(_("Restore defaults"))
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(self._reset)
        card.add(Row(_("Reset all settings"), None, reset))
        self.body.addWidget(card)

        card = Card(_("Integrations"))
        self.api = Switch()
        self.api.toggled.connect(lambda on: self.store.set("app", "api", on))
        card.add(Row(
            _("Let other programs use the mouse"),
            _("Scripts and apps can play vibrations, run actions, show their own ring, read and change settings "
              "and follow buttons, gestures and the wheel over D-Bus. See API.md in the documentation."),
            self.api,
        ))
        example = QWidget()
        example_layout = QHBoxLayout(example)
        example_layout.setContentsMargins(0, 0, 0, 0)
        example_layout.setSpacing(10)
        self.api_example = ElidedLabel(f"qdbus6 {api.SERVICE} {api.PATH} PlayHaptic knock", Qt.TextElideMode.ElideMiddle)
        self.api_example.setObjectName("summary")
        self.api_example.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        example_layout.addWidget(self.api_example, 1)
        copy_example = QPushButton(_("Copy"))
        copy_example.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_example.clicked.connect(lambda: QApplication.clipboard().setText(self.api_example.full_text()))
        example_layout.addWidget(copy_example)
        self.api_example_row = card.add_plain(example)
        self.body.addWidget(card)

        self.uinput_warning = QLabel(_(
            "No access to /dev/uinput: keyboard shortcuts, clicks and smooth scrolling cannot be sent. "
            "Add a udev rule granting your user access to uinput and log in again."
        ))
        self.uinput_warning.setObjectName("banner")
        self.uinput_warning.setWordWrap(True)
        self.uinput_warning.hide()
        self.body.addWidget(self.uinput_warning)

        self.about = hint_label(f"{APP_NAME} {__version__}  ·  " + _("written by Claude, Anthropic's AI model"))
        self.body.addWidget(self.about)
        self.finish()

        store.changed.connect(self._refresh)
        store.state_changed.connect(self._state)
        self._refresh()

    # -- firmware ------------------------------------------------------------

    def _ask_firmware(self, check=False):
        name = self.store.state.get("name")
        if firmware.available() and name and self.store.state["connected"]:
            self._firmware_for = name
            self.firmware.start(name, check)
            self._show_firmware()

    def _firmware_answered(self, entries, checked):
        self._firmware_entries, self._firmware_checked = entries, checked
        self._show_firmware()

    def _show_firmware(self):
        row, entries = self.firmware_row, self._firmware_entries
        offered = next((entry for entry in entries if entry["updates"]), None)
        self.firmware_check.setEnabled(firmware.available() and self.store.state["connected"] and not self.firmware.running)
        self.firmware_command_row.setVisible(offered is not None)
        row.title.setText("   ·   ".join(f"{entry['name']}  {entry['version']}" for entry in entries) or _("Firmware"))
        if not firmware.available():
            text = _("Install fwupd, the system's firmware updater, to see the firmware versions and check for updates.")
        elif self.firmware.running:
            text = _("Asking fwupd…")
        elif not entries:
            text = _("fwupd, the system's firmware updater, does not see the mouse right now.")
        elif offered is not None:
            text = _("{name}: version {version} is available. Install it with the command below, and keep the "
                     "mouse switched on and in range until it has finished.").format(
                         name=offered["name"], version=offered["updates"][0]["version"])
            self.firmware_command.setText(firmware.update_command(offered))
        elif self._firmware_checked:
            text = _("No newer firmware has been published for Linux for these devices.")
        else:
            text = _("Updates are installed by fwupd, the system's firmware updater, from the firmware Logitech "
                     "publishes for Linux. MX Options does not flash anything itself.")
        row.note.setText(text)

    def showEvent(self, event):
        super().showEvent(event)
        if self._firmware_for != self.store.state.get("name"):
            self._ask_firmware()  # once per mouse: what is installed
        self._show_firmware()

    def _guard(self, setter, switch):
        def apply(enabled):
            try:
                setter(enabled)
            except OSError as e:
                switch.set_checked_silently(not enabled)
                QMessageBox.warning(self, APP_NAME, str(e))
        return apply

    def _language(self, value):
        self.store.set("app", "language", value)

    def _custom_accent(self):
        color = QColorDialog.getColor(QColor(self.store.cfg["app"]["accent"]), self, _("Accent colour"))
        if color.isValid():
            self.store.set("app", "accent", color.name())

    def _reset(self):
        answer = QMessageBox.question(self, APP_NAME, _("Reset every setting to its default?"))
        if answer == QMessageBox.StandardButton.Yes:
            self.store.restore_defaults()

    def _refresh(self):
        app = self.store.cfg["app"]
        self.theme.set_value(app["theme"])
        self.accent.set_value(app["accent"])
        self.model.set_value(app["model"])
        self.mouse_color.set_value(app["mouse_color"])
        self.mouse_color_row.setEnabled(app["model"] == "photo")
        if self._tray_look != theme.REVISION:  # the little pictures are drawn in the colours of the theme
            self._tray_look = theme.REVISION
            self.tray.set_options([(value, _(label), tray_icon.preview(value, theme.TEXT)) for value, label in TRAY_LABELS])
        self.tray.set_value(app["tray_icon"])
        self.api.set_checked_silently(app["api"])
        self.api_example_row.setEnabled(app["api"])
        self.language.set_value(app["language"])

    def _state(self, state):
        self.uinput_warning.setVisible(not state["uinput"])
        if self.isVisible() and state["connected"] and self._firmware_for != state.get("name"):
            self._ask_firmware()
        self._show_firmware()
        hosts = state.get("hosts") or []
        self.hosts_card.setVisible(bool(hosts))
        signature = (state.get("current_host"), tuple((h["paired"], h["name"]) for h in hosts), state["connected"])
        if signature == self._hosts_shown:
            return
        self._hosts_shown = signature
        while self.hosts_layout.count():
            item = self.hosts_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for index, host in enumerate(hosts):
            name = host["name"] or (_("Paired") if host["paired"] else _("Empty"))
            button = QPushButton(f"{index + 1}  ·  {name}")
            button.setMinimumHeight(40)
            current = index == state.get("current_host")
            if current:
                button.setObjectName("primary")
            else:
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setEnabled(state["connected"])
                button.clicked.connect(lambda _checked=False, i=index: self._switch(i))
            self.hosts_layout.addWidget(button, 1)

    def _switch(self, index):
        answer = QMessageBox.question(
            self, APP_NAME,
            _("Switch the mouse to channel {}? It will stop working on this computer until you switch it back.").format(index + 1),
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.store.engine.switch_host(index)
