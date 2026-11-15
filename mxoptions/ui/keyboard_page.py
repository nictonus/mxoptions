"""The keyboard's two pages: what its keys do, and the keyboard itself (battery, Easy-Switch, firmware)."""

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .. import APP_NAME, actions, config, device, firmware, keyboard
from ..i18n import _
from . import theme
from .action_picker import ActionPicker
from .pages import FirmwareQuery, ScrollPage, _header
from .widgets import Card, Row, Segmented, SliderRow, Switch, hint_label

# What a key may not do: what only makes sense on the mouse (catalogue ids, see config.KEYBOARD_ACTIONS)
KEYBOARD_EXCLUDE = ("gestures", "wheel_mode", "dpi_cycle", "dpi_shift", "flow", "spotlight")
# Names for the keys whose control ids are known (Solaar's list of them): every other key goes by its id.
KEY_LABELS = {
    0x00D1: "Easy-Switch 1", 0x00D2: "Easy-Switch 2", 0x00D3: "Easy-Switch 3",
    0x0034: "Fn", 0x00CD: "Fn lock", 0x0101: "Fn lock key", 0x00DE: "F-Lock",
    0x00E2: "Backlight down", 0x00E3: "Backlight up",
}
# Why a key is left as it is (keyboard.KeyboardSide._keys)
KEY_REASONS = {
    "protected": ("Kept by the keyboard", "MX Options leaves the Easy-Switch keys and the keys of the Fn layer to the keyboard, "
                  "so that switching computers and the Fn keys always work."),
    "foreign": ("Changed by another program", "Another program has changed what this key sends, so MX Options leaves it alone."),
    "changed": ("Changed already", "This key was already changed when the keyboard was found, and MX Options cannot tell by whom, "
                "so it leaves the key alone."),
}
# Why no key is touched at all (state "reason")
BLOCK_REASONS = {
    "rivals": "Other software for Logitech devices is running, so the keys of the keyboard are left as they are.",
    "identity": "This keyboard does not say which one it is, so its keys are left as they are.",
    "journal": "The record of changed keys cannot be read: {}. Keys are left as they are.",
    "full": "The record of changed keys already lists 16 other keyboards, so the keys of this one are left as they are.",
}
# What went wrong last (state "failed")
FAILED = {
    "switch": "The keyboard was not moved to the other computer: a key MX Options changed could not be given back "
              "first. Its keys work as set here; try again in a moment.",
}
# The settings the keyboard keeps itself (device.KEYBOARD_SETTINGS): that it lists the feature says only that it has
# one, so each is said in general words, never which keys or values this keyboard uses for it.
SETTINGS = {
    "fn": ("Fn lock", "The keyboard can swap whether the top row gives F1–F12 or its other functions without Fn, "
           "as its guide describes. MX Options does not change this yet."),
    "backlight": ("Backlight", "The keyboard has a backlight with settings of its own, set on the keyboard as its guide "
                  "describes. MX Options does not change them yet."),
    "platform": ("Operating system layout", "The keyboard can be set up for the layout of another operating system, on "
                 "the keyboard itself as its guide describes. MX Options does not change this yet."),
    "disable": ("Disable keys", "The keyboard can turn some of its keys off. MX Options does not do this yet."),
    "locks": ("Lock keys", "The keyboard reports when Caps Lock, Num Lock or Scroll Lock turns on or off. MX Options can "
              "show a notice then; it never saves or logs their state."),
}
# How a change of a setting went, when not as asked (keyboard.KeyboardSide._change)
OUTCOMES = {
    "unknown": "The keyboard did not say how this is set.",
    "unsupported": "The keyboard does not let MX Options change this.",
    "not-applied": "The keyboard did not take the change.",
    "restored": "The keyboard did not take the change as asked; the earlier setting was put back.",
    "unconfirmed": "The keyboard did not confirm the change. Shown as the keyboard reports it.",
}
FN_HINT = ("On: the top row gives F1–F12, and Fn with a key gives its other function. Off: the other way round. "
           "The keyboard's own Fn lock shortcut changes it too.")
# The keys the keyboard can turn off (kbsettings.DISABLE_KEYS): the title of each one's row, and its keycap name
DISABLE_TITLES = (("caps_lock", "Caps Lock", "Caps Lock"), ("num_lock", "Num Lock", "Num Lock"),
                  ("scroll_lock", "Scroll Lock", "Scroll Lock"), ("insert", "Insert", "Insert"),
                  ("super", "Windows/Super key", "Windows/Super"))
DISABLE_NOTE = "Turn off keys you press by mistake. A key turned off does nothing; turn its switch off to enable it again."
DISABLE_HINT = ("The keyboard does this itself. Whether it keeps it after it is switched off is not known yet; "
                "MX Options shows what the keyboard reports.")
# Operating systems a layout is for (kbsettings.OS_BITS): their own names, the same in every language
OS_NAMES = {"windows": "Windows", "macos": "macOS", "linux": "Linux", "chromeos": "ChromeOS", "android": "Android",
            "ios": "iOS", "webos": "webOS", "windows_embedded": "Windows Embedded", "tizen": "Tizen"}
PLATFORM_HINT = ("The layout the keyboard uses for the operating system on this Easy-Switch channel; each channel keeps "
                 "its own. The keyboard's own shortcut for it, as its guide describes, changes it too.")
BACKLIGHT_HINT = ("The keyboard keeps these settings itself; its own backlight keys change them too. MX Options does not "
                  "store them.")
# What the Backlight row says while nothing else does (KeyboardPage._show_backlight), the first that applies
BACKLIGHT_NOTES = {
    "read-only": "MX Options does not change the backlight of this keyboard; its own keys do.",
    "keys": "Brightness is set with the keyboard's keys just now. Choose Automatic or Fixed to set it here.",
    "keys-only": "Brightness is set with the keyboard's keys.",
    "mode-unknown": "The keyboard uses a brightness setting MX Options does not know; it is left as it is.",
    "untimed": "Brightness and timing are set with the keyboard's keys; newer keyboard firmware may let MX Options set "
               "them too.",
}
LEVEL_HINT = "Steps 0 to {top}, as the keyboard counts them. In Automatic, the light around the keyboard sets the brightness."
POWER_SAVE_HINT = "Shown as the keyboard reports it. MX Options does not change this yet."
# The backlight's timers (kbsettings.TIMERS): the title of each one's row, its name for assistive technology, its hint
TIMER_ROWS = (
    ("hands_out", "Off after, hands away", "Backlight off after, hands away",
     "How long the backlight stays on after you type, once your hands move away."),
    ("hands_in", "Off after, hands near", "Backlight off after, hands near",
     "How long the backlight stays on after you type, while your hands stay near the keyboard."),
    ("powered", "Off after, plugged in", "Backlight off after, plugged in",
     "How long the backlight stays on after you type, while the keyboard is charging over USB."),
)
TIMER_STOPS = (5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200)  # s
CAP_W, CAP_H, CAP_GAP, PAD = 152, 54, 8, 18
CHARGING = (device.BATTERY_CHARGING, device.BATTERY_ALMOST_FULL, device.BATTERY_SLOW_CHARGE)


def layout_labels(choices):
    """What the layouts a keyboard offers are called: the systems each is for, numbered where that is not enough."""
    labels = [", ".join(OS_NAMES.get(name, name) for name in choice["os"]) or _("Layout {}").format(choice["index"] + 1)
              for choice in choices]
    return [label + " · " + _("Layout {}").format(choice["index"] + 1) if labels.count(label) > 1 else label
            for label, choice in zip(labels, choices)]


def duration_text(seconds):
    """A time in whole seconds in words: hours, minutes and seconds, each left out only where it is 0."""
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return " ".join(unit.format(count) for count, unit in ((hours, _("{} h")), (minutes, _("{} min")), (seconds, _("{} s")))
                    if count)


def battery_text(battery):
    """A battery reading (percent, status) in words, as the sidebar shows it for the mouse and the keyboard."""
    percent, status = battery
    if status == device.BATTERY_FULL:
        return _("Fully charged")
    if status in CHARGING:
        return _("Charging · {}%").format(percent)
    return f"{percent}%"


def key_title(key):
    label = KEY_LABELS.get(key["cid"])
    return _(label) if label else _("Key {}").format(key["id"])


def block_text(reason):
    text = _(BLOCK_REASONS[reason])
    return text.format(keyboard.JOURNAL) if reason == "journal" else text


def notice_text(found):
    """What the banner of the keyboard's pages says, or "" for none."""
    if found.get("reason"):
        return block_text(found["reason"])
    return _(FAILED[found["failed"]]) if found.get("failed") in FAILED else ""


def key_value(key, values):
    """What a keycap says under the key's name: what it does, or why the keyboard keeps it."""
    if key["protected"]:
        return _(KEY_REASONS["protected"][0])
    if key["native_reason"] in KEY_REASONS:
        return _(KEY_REASONS[key["native_reason"]][0])
    return values.get(key["id"], "")


class KeyCap(QAbstractButton):
    """One key of KeyStrip: a button, so that it can be reached with Tab and the arrow keys, picked with Space,
    and is told apart by screen readers like any other button."""

    def __init__(self, key, parent):
        super().__init__(parent)
        self.setCheckable(True)
        self.setAutoExclusive(True)  # the arrow keys move between the keys, and pick the one they move to
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(CAP_W, CAP_H)
        self.key, self.value = key, ""

    def show_key(self, key, value):
        self.key, self.value = key, value
        self.setAccessibleName(key_title(key))
        self.setAccessibleDescription(value)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        key, rect = self.key, QRectF(0, 0, CAP_W, CAP_H)
        chosen = self.isChecked()
        kept = key["protected"] or key["native_reason"] in KEY_REASONS
        painter.setPen(QPen(QColor(theme.ACCENT_INK), 2) if chosen else QPen(QColor(theme.BORDER), 1))
        painter.setBrush(QColor(theme.CARD_HOVER if chosen else theme.CARD))
        painter.drawRoundedRect(rect.adjusted(0, 0, 0, -3), 9, 9)
        if self.hasFocus():  # where the keyboard is: a second outline inside the first
            painter.setPen(QPen(QColor(theme.ACCENT), 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(3, 3, -3, -6), 7, 7)
        painter.setPen(QPen(QColor(theme.MODEL_EDGE), 1))  # the keycap's skirt
        painter.drawLine(rect.left() + 8, rect.bottom() - 1, rect.right() - 8, rect.bottom() - 1)
        text = rect.adjusted(12, 7, -12, -9)
        small = QFontMetrics(painter.font())
        painter.setPen(QColor(theme.MUTED if not kept else theme.DISABLED))
        painter.drawText(text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                         small.elidedText(key_title(key), Qt.TextElideMode.ElideRight, int(text.width())))
        font = painter.font()
        font.setBold(not kept)
        painter.setFont(font)
        painter.setPen(QColor(theme.DISABLED if kept else theme.TEXT))
        painter.drawText(text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                         QFontMetrics(font).elidedText(self.value, Qt.TextElideMode.ElideRight, int(text.width())))
        if key["active"]:  # the program has this key now
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.ACCENT))
            painter.drawEllipse(QRectF(rect.right() - 13, rect.top() + 8, 5, 5))


class KeyStrip(QWidget):
    """The keys that can be given an action, drawn as the keycaps of a keyboard: what each does under its
    name, the one being edited outlined, the ones the keyboard keeps greyed out."""

    selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._keys = []
        self._values = {}
        self._selected = None
        self.caps = {}  # key id -> KeyCap, in the order of the keys
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_keys(self, keys):
        ids = [key["id"] for key in keys]
        if ids != list(self.caps):  # other keys: new caps, in the new order (the Tab order follows)
            focused = next((key_id for key_id, cap in self.caps.items() if cap.hasFocus()), None)
            for cap in self.caps.values():
                cap.deleteLater()
            self.caps = {}
            for key in keys:
                cap = self.caps[key["id"]] = KeyCap(key, self)
                cap.clicked.connect(lambda _checked=False, key_id=key["id"]: self.select(key_id))
                cap.show()
            if focused in self.caps:
                self.caps[focused].setFocus()
        self._keys = list(keys)
        for key in keys:
            cap = self.caps[key["id"]]
            cap.show_key(key, key_value(key, self._values))
            cap.setChecked(key["id"] == self._selected)
        self._place()
        self.updateGeometry()
        self.update()

    def set_value(self, key_id, text):
        self._values[key_id] = text
        cap = self.caps.get(key_id)
        if cap is not None:
            cap.show_key(cap.key, key_value(cap.key, self._values))

    def select(self, key_id):
        self._selected = key_id
        cap = self.caps.get(key_id)
        if cap is not None:
            cap.setChecked(True)
        self.selected.emit(key_id)

    def _columns(self, width):
        return max(1, int((width - 2 * PAD + CAP_GAP) // (CAP_W + CAP_GAP)))

    def _height(self, width):
        rows = -(-len(self._keys) // self._columns(width)) if self._keys else 0
        return 2 * PAD + 14 + rows * (CAP_H + CAP_GAP)

    def _place(self):
        columns = self._columns(self.width())
        for index, cap in enumerate(self.caps.values()):
            row, column = divmod(index, columns)
            cap.move(PAD + column * (CAP_W + CAP_GAP), PAD + 14 + row * (CAP_H + CAP_GAP))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._height(width)

    def sizeHint(self):
        return QSize(4 * (CAP_W + CAP_GAP) + 2 * PAD, self._height(4 * (CAP_W + CAP_GAP) + 2 * PAD))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setMinimumHeight(self._height(self.width()))
        self._place()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        body = QRectF(0.5, 0.5, width - 1, self._height(width) - 1)
        # the case: a slab with a raised rim along its top edge, as on a low-profile keyboard
        painter.setPen(QPen(QColor(theme.MODEL_EDGE), 1.2))
        painter.setBrush(QColor(theme.MODEL_BASE))
        painter.drawRoundedRect(body, 16, 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.MODEL_DECAL))
        painter.drawRoundedRect(QRectF(body.left() + PAD, body.top() + 7, body.width() - 2 * PAD, 4), 2, 2)


class KeysPage(QWidget):
    """Laid out like the Buttons page: the keys on the left, the list of actions on the right."""

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._key = None
        self._keys = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        left = QVBoxLayout()
        left.setContentsMargins(34, 28, 24, 20)
        left.setSpacing(12)
        left.addLayout(_header(_("Keys"), _(
            "Pick a key to choose what it does. A key left at Default does what it always does; the keys you change "
            "go back to normal when MX Options quits.")))
        self.notice = QLabel()
        self.notice.setObjectName("banner")
        self.notice.setWordWrap(True)
        self.notice.hide()
        left.addWidget(self.notice)
        # Whose keys are shown: every app's, or those of one app while it is in front (an app profile)
        line = QHBoxLayout()
        line.setSpacing(8)
        label = QLabel(_("Keys for"))
        self.apps = QComboBox()
        self.apps.setAccessibleName(_("Keys for"))
        label.setBuddy(self.apps)
        self.add_app = QPushButton(_("Add app"))
        self.remove_app = QPushButton(_("Remove"))
        self.remove_app.setAccessibleName(_("Remove the keys of this app"))
        for button in (self.add_app, self.remove_app):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        line.addWidget(label)
        line.addWidget(self.apps, 1)
        line.addWidget(self.add_app)
        line.addWidget(self.remove_app)
        left.addLayout(line)
        self.apps_hint = hint_label("")
        left.addWidget(self.apps_hint)
        self._app = None  # the app whose keys are shown, None: every app's
        self.strip = KeyStrip()
        self.strip.setAccessibleName(_("Keys"))
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        inner = QVBoxLayout(holder)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addWidget(self.strip)
        inner.addStretch(1)
        area.setWidget(holder)
        left.addWidget(area, 1)
        self.empty = hint_label(_("Turn the keyboard on to see its keys here. Its receiver can be the one of the mouse."))
        left.addWidget(self.empty)
        layout.addLayout(left, 1)

        self.picker = ActionPicker(allow_button_actions=True, exclude=KEYBOARD_EXCLUDE)
        layout.addWidget(self.picker)

        self.strip.selected.connect(self._select)
        self.picker.changed.connect(self._changed)
        self.apps.currentIndexChanged.connect(self._app_chosen)
        self.add_app.clicked.connect(self._add_app)
        self.remove_app.clicked.connect(self._remove_app)
        store.changed.connect(self._refresh)
        store.changed.connect(self._show_apps)
        store.state_changed.connect(self._state)
        if store.front is not None:
            store.front.changed.connect(self._show_apps)
        self._show_apps()
        self._state(store.state)

    def showEvent(self, event):
        super().showEvent(event)
        if self.store.front is not None:
            self.store.front.want("page", True)  # to know which app to offer to add

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.store.front is not None:
            self.store.front.want("page", False)

    def _show_apps(self):
        """The list of apps with keys of their own, and what can be done with it."""
        front = self.store.front
        supported = front is None or front.supported
        profiles = self.store.cfg["keyboard"]["profiles"] if supported else []
        if self._app is not None and self.store.profile(self._app) is None or not supported:
            self._app = None
        entries = [(_("All apps"), None)] + [(profile["name"] or profile["app"], profile["app"]) for profile in profiles]
        if [(self.apps.itemText(i), self.apps.itemData(i)) for i in range(self.apps.count())] != entries:
            self.apps.blockSignals(True)
            self.apps.clear()
            for text, app in entries:
                self.apps.addItem(text, app)
            self.apps.blockSignals(False)
        self.apps.setCurrentIndex(next(i for i, (_text, app) in enumerate(entries) if app == self._app))
        self.apps.setEnabled(supported)
        last = front.last_other if front is not None else None
        self.add_app.setEnabled(bool(supported and last and self.store.profile(last) is None
                                     and len(profiles) < config.MAX_PROFILES))
        self.remove_app.setEnabled(self._app is not None)
        if not supported:
            text = _("This desktop does not tell other programs which app is in front, so keys of their own for one "
                     "app cannot work here. The keys do what is set for all apps.")
        elif self._app is not None:
            text = _("While {} is in front. \"As in all apps\": the key does what it does in every app. \"Default\": the "
                     "key does what it always does, here, even where it is changed for all apps.").format(self._app)
        elif last and self.store.profile(last) is None:
            text = _("To give an app keys of its own, add it: {} was in front last.").format(last)
        else:
            text = _("To give an app keys of its own, switch to it, then come back here and add it.")
        self.apps_hint.setText(text)

    def _app_chosen(self, index):
        app = self.apps.itemData(index)
        if app != self._app:
            self._app = app
            self._show_apps()
            self._refresh()
            if self._key is not None:
                self._select(self._key["id"])

    def _add_app(self):
        front = self.store.front
        app = front.last_other if front is not None else None
        if app and self.store.add_profile(app):
            self._app = app
            self._show_apps()
            self._refresh()
            if self._key is not None:
                self._select(self._key["id"])

    def _remove_app(self):
        profile = self.store.profile(self._app)
        if profile is None:
            return
        answer = QMessageBox.question(self, APP_NAME, _(
            "Remove the keys of {}? In that app the keys then do what they do in every app.").format(profile["name"] or profile["app"]))
        if answer == QMessageBox.StandardButton.Yes:
            self.store.remove_profile(profile["app"])

    def _select(self, key_id):
        self._key = next((key for key in self._keys if key["id"] == key_id), None)
        if self._key is None:
            return
        reason = "protected" if self._key["protected"] else self._key["native_reason"]
        if reason in KEY_REASONS:
            value, text = KEY_REASONS[reason]
            self.picker.show_fixed(key_title(self._key), _(value), _(text))
        else:
            hint = _("The keyboard has not said yet how this key reports; the action is applied once it does.") \
                if reason == "pending" else ""
            profile = self.store.profile(self._app)
            if profile is None:
                self.picker.edit(key_title(self._key), self.store.cfg["keyboard"]["keys"].get(key_id, actions.DEFAULT), hint)
            else:
                self.picker.edit(key_title(self._key), profile["keys"].get(key_id), hint, inherit=True)

    def _changed(self, action):
        if self._key is None:
            return
        if self.store.profile(self._app) is not None:
            self.store.set_profile_key(self._app, self._key["id"], action)
        elif action is not None:
            self.store.set_key(self._key["id"], action)

    def _refresh(self):
        keys = keyboard.effective_keys(self.store.cfg["keyboard"], self._app)
        for key in self._keys:
            self.strip.set_value(key["id"], actions.describe(keys.get(key["id"], actions.DEFAULT)))

    def _state(self, state):
        found = state.get("keyboard") or {}
        keys = found.get("keys") or []
        notice = notice_text(found)
        self.notice.setVisible(bool(notice))
        self.notice.setText(notice)
        self.empty.setVisible(not keys)
        if keys != self._keys:
            self._keys = keys
            self.strip.set_keys(keys)
            self._refresh()
            chosen = self._key["id"] if self._key else None
            ids = [key["id"] for key in keys]
            if ids:
                self.strip.select(chosen if chosen in ids else next(
                    (key["id"] for key in keys if not key["protected"]), ids[0]))


class KeyboardPage(ScrollPage):
    def __init__(self, store, parent=None):
        super().__init__(_("Keyboard"), None, parent)
        self.store = store
        self.notice = QLabel()
        self.notice.setObjectName("banner")
        self.notice.setWordWrap(True)
        self.notice.hide()
        self.body.addWidget(self.notice)

        card = Card(_("Status"))
        self.battery = QLabel()
        self.battery.setObjectName("valueLabel")
        card.add(Row(_("Battery"), None, self.battery))
        self.body.addWidget(card)

        card = Card(_("Easy-Switch"), hint=_(
            "Press an Easy-Switch key to move the keyboard to the computer on that channel. Hold one for three seconds "
            "to pair the keyboard with another computer on it."))
        self.hosts = QWidget()
        self.hosts_layout = QHBoxLayout(self.hosts)
        self.hosts_layout.setContentsMargins(0, 0, 0, 0)
        self.hosts_layout.setSpacing(10)
        card.add_plain(self.hosts)
        self.hosts_card = card
        self._hosts_shown = None
        self.body.addWidget(card)

        card = Card(_("Keyboard settings"), hint=_(
            "Settings the keyboard controls itself. Changes are made on the keyboard. Its own shortcuts may change "
            "them too."))
        self.fn_switch = Switch()
        self.fn_switch.setAccessibleName(_("Use F1–F12 without Fn"))
        self.fn_switch.toggled.connect(lambda on: self.store.engine.set_keyboard_setting("fn", on))
        self.platform_box = QComboBox()
        self.platform_box.setAccessibleName(_("Operating system layout"))
        self.platform_box.activated.connect(self._platform_chosen)
        self._choices = []  # the layouts in self.platform_box, as the keyboard offers them
        self.backlight_switch = Switch()
        self.backlight_switch.setAccessibleName(_("Backlight on"))
        self.backlight_switch.toggled.connect(lambda on: self._backlight_asks({"on": on}))
        self.lock_notices = Switch()  # a setting of this program's: the keyboard is never asked anything for it
        self.lock_notices.setAccessibleName(_("Show a notice when a lock key turns on or off"))
        self.lock_notices.toggled.connect(lambda on: self.store.set("keyboard", "lock_notices", on))
        controls = {"fn": self.fn_switch, "platform": self.platform_box, "backlight": self.backlight_switch,
                    "locks": self.lock_notices}
        self.settings_rows, self.disable_switches, self.disable_rows = {}, {}, {}
        for name, (title, text) in SETTINGS.items():
            self.settings_rows[name] = card.add(Row(_(title), None, controls.get(name), note=_(text)))
            if name == "fn":  # its notice, a setting of this program's, under it
                notice = _("Show a notice when Fn lock changes on the keyboard")
                self.fn_notices = Switch()
                self.fn_notices.setAccessibleName(notice)
                self.fn_notices.toggled.connect(lambda on: self.store.set("keyboard", "fn_notices", on))
                self.fn_notices_row = card.add(Row(notice, None, self.fn_notices))
            if name == "disable":  # a row of its own for each key it can turn off, under it
                for key, label, cap in DISABLE_TITLES:
                    switch = self.disable_switches[key] = Switch()
                    switch.setAccessibleName(_("Turn off the {} key").format(cap))
                    switch.toggled.connect(lambda on, key=key: self.store.engine.set_keyboard_setting("disable", {key: on}))
                    self.disable_rows[key] = card.add(Row(_(label), None, switch))
            if name == "backlight":
                self._add_backlight(card)
        self.settings_rows["platform"].title.setBuddy(self.platform_box)
        self.settings_card = card
        self.body.addWidget(card)

        card = Card(_("Firmware"))
        self.firmware_row = card.add(Row(_("Firmware"), None, None, note=" "))
        self.firmware = FirmwareQuery(self)
        self.firmware.answered.connect(self._firmware_answered)
        self._firmware_entries, self._firmware_for = [], None
        self.body.addWidget(card)

        card = Card(_("Keys"))
        reset = QPushButton(_("Restore keyboard defaults"))
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(self._reset)
        card.add(Row(_("Every key does what it always does"), None, reset))
        self.body.addWidget(card)
        self.finish()

        store.state_changed.connect(self._state)
        store.changed.connect(self._show_notice_switches)
        self._state(store.state)

    def _show_notice_switches(self):
        """The notice switches as the settings have them; showing them changes nothing."""
        cfg = self.store.cfg["keyboard"]
        self.notices.set_checked_silently(cfg.get("notices") is True)
        self.fn_notices.set_checked_silently(cfg.get("fn_notices") is True)
        self.lock_notices.set_checked_silently(cfg.get("lock_notices") is True)

    def _add_backlight(self, card):
        """The rows under the Backlight row: brightness, its level, the timers, low battery, the notice."""
        self.backlight_rows = {}
        self.brightness = Segmented([("auto", _("Automatic")), ("manual", _("Fixed"))])
        self.brightness.setAccessibleName(_("Brightness"))
        for button, name in zip(self.brightness.findChildren(QPushButton), (_("Automatic brightness"), _("Fixed brightness"))):
            button.setAccessibleName(name)
        self.brightness.selected.connect(lambda mode: self._backlight_asks({"mode": mode}))
        self.backlight_rows["mode"] = card.add(Row(_("Brightness"), None, self.brightness))
        self.level = SliderRow(_("Brightness level"), 0, 7, 1, low_label=_("Dimmer"), high_label=_("Brighter"))
        self.level.slider.setAccessibleName(_("Backlight brightness level"))
        # the arrow and Page keys, or a click beside the handle, ask at once; a drag only once let go
        self.level.changed.connect(lambda value: None if self.level.slider.isSliderDown() else self._level_chosen(value))
        self.level.released.connect(self._level_chosen)
        self._levels = None  # the number of levels the slider has
        self.backlight_rows["level"] = card.add(self.level)
        self.timer_boxes = {}
        for key, title, name, hint in TIMER_ROWS:
            box = self.timer_boxes[key] = QComboBox()
            box.setAccessibleName(_(name))
            box.activated.connect(lambda position, key=key: self._timer_chosen(key, position))
            self.backlight_rows[key] = card.add(Row(_(title), _(hint), box))
            self.backlight_rows[key].title.setBuddy(box)
        self.power_save = Switch()
        self.power_save.setAccessibleName(_("Off at low battery"))
        self.power_save.setEnabled(False)  # shown only
        self.backlight_rows["power_save"] = card.add(Row(_("Off at low battery"), _(POWER_SAVE_HINT), self.power_save))
        title = _("Show a notice when the brightness changes on the keyboard")
        self.notices = Switch()
        self.notices.setAccessibleName(title)
        self.notices.toggled.connect(lambda on: self.store.set("keyboard", "notices", on))
        self.backlight_rows["notices"] = card.add(Row(title, None, self.notices))

    def _backlight(self):
        return ((self.store.state.get("keyboard") or {}).get("controls") or {}).get("backlight") or {}

    def _backlight_asks(self, change):
        self.store.engine.set_keyboard_setting("backlight", change)

    def _level_chosen(self, position):
        control = self._backlight()
        level = int(position)  # the slider says a float; the keyboard takes whole levels only
        if level != (control.get("value") or {}).get("level") or control.get("busy"):  # busy: the last one counts
            self._backlight_asks({"level": level})

    def _timer_chosen(self, key, position):
        seconds = self.timer_boxes[key].itemData(position)
        if seconds is not None and seconds != (self._backlight().get("value") or {}).get(key):
            self._backlight_asks({key: seconds})

    def _show_note(self, name, control, hint, otherwise=""):
        """The note and hint of setting ``name``'s row: its general words while MX Options cannot change it, else how
        its last change went, or ``otherwise``."""
        row = self.settings_rows[name]
        if control is None:
            row.set_hint("")
            row.note.setText(_(SETTINGS[name][1]))
            row.note.show()
            return
        row.set_hint(_(hint))
        outcome = OUTCOMES.get(control.get("outcome"))
        if outcome is None and control.get("value") is None:
            outcome = OUTCOMES["unknown"]
        text = _(outcome) if outcome else otherwise
        row.note.setText(text)
        row.note.setVisible(bool(text))

    @staticmethod
    def _changeable(control):
        return control.get("value") is not None and bool(control.get("writable")) and not control.get("busy")

    def _show_fn(self, control):
        """The Fn lock row: a switch while the keyboard lets it be changed, its general hint otherwise. What it shows
        is always what the keyboard said last; one asked for is shown as such until the keyboard answers."""
        self.fn_switch.setVisible(control is not None)
        self._show_note("fn", control, FN_HINT)
        if control is None:
            return
        if not control.get("busy"):
            self.fn_switch.set_checked_silently(bool(control.get("value")))
        self.fn_switch.setEnabled(self._changeable(control))

    def _show_disable(self, control, shown):
        """The keys the keyboard can turn off, a switch each (on: turned off), as for Fn lock."""
        self._show_note("disable", control, DISABLE_HINT, _(DISABLE_NOTE))
        keys = (control or {}).get("keys") or []
        off = (control or {}).get("value") or []
        for key, switch in self.disable_switches.items():
            self.settings_card.set_row_visible(self.disable_rows[key], shown and key in keys)
            if control is None:
                continue
            if not control.get("busy"):
                switch.set_checked_silently(key in off)
            switch.setEnabled(self._changeable(control))

    def _show_backlight(self, control, shown, blocked):
        """The Backlight row and those under it, each where the keyboard has it, changeable as it lets it be. The slider
        stays where it is moved while the change is under way, and can still be moved; it asks for whole levels."""
        value, fields = (control or {}).get("value") or {}, (control or {}).get("fields") or []
        can = (control or {}).get("can") or [] if (control or {}).get("writable") else []
        mode, busy, levels = value.get("mode"), bool((control or {}).get("busy")), (control or {}).get("levels")
        note = ""
        if not value:
            pass
        elif not control.get("writable") and not blocked:
            note = "read-only"
        elif mode == "keys":
            note = "keys" if "mode" in fields else "keys-only"
        elif mode is None:
            note = "mode-unknown"
        elif not {"level", "hands_out", "hands_in", "powered"} & set(fields):
            note = "untimed"
        self.backlight_switch.setVisible(control is not None)
        self._show_note("backlight", control, BACKLIGHT_HINT, _(BACKLIGHT_NOTES[note]) if note else "")
        for key, row in self.backlight_rows.items():
            here = key in fields if key != "notices" else bool(value) and ("level" in fields or levels is not None)
            self.settings_card.set_row_visible(row, shown and here)
        if control is None:
            return
        self.notices.set_checked_silently(self.store.cfg["keyboard"].get("notices") is True)
        if not busy:
            self.backlight_switch.set_checked_silently(bool(value.get("on")))
            self.brightness.set_value(mode)  # none chosen while set with its keys, or not known
        self.backlight_switch.setEnabled(self._changeable(control) and "on" in can)
        self.brightness.setEnabled(self._changeable(control) and "mode" in can)
        self.power_save.set_checked_silently(bool(value.get("power_save")))
        if levels and levels != self._levels:
            self._levels = levels
            self.level.set_range(0, levels - 1, 1)
            self.level.info.set_text(_(LEVEL_HINT).format(top=levels - 1))
        if not busy and not self.level.slider.isSliderDown() and value.get("level") is not None:
            self.level.set_value(value["level"])
        self.level.setEnabled(bool(control.get("writable")) and "level" in can and mode == "manual")
        for key, box in self.timer_boxes.items():
            seconds = value.get(key)
            if seconds is None:
                items = [(_("Not known"), None)]
            else:
                items = [(duration_text(stop), stop) for stop in sorted(set(TIMER_STOPS) | {seconds})]
            if [(box.itemText(i), box.itemData(i)) for i in range(box.count())] != items:
                box.blockSignals(True)
                box.clear()
                for text, stop in items:
                    box.addItem(text, stop)
                box.blockSignals(False)
            if not busy:
                box.blockSignals(True)
                box.setCurrentIndex(box.findData(seconds) if seconds is not None else 0)
                box.blockSignals(False)
            box.setEnabled(self._changeable(control) and key in can)

    def _show_platform(self, control, blocked):
        """The layout for this computer's Easy-Switch channel, picked from those the keyboard offers."""
        self.platform_box.setVisible(control is not None)
        read_only = control is not None and control.get("value") is not None and not control.get("writable") and not blocked
        self._show_note("platform", control, PLATFORM_HINT, _(OUTCOMES["unsupported"]) if read_only else "")
        if control is None:
            return
        choices = control.get("choices") or []
        if choices != self._choices:
            self._choices = choices
            self.platform_box.blockSignals(True)
            self.platform_box.clear()
            self.platform_box.addItems(layout_labels(choices))
            self.platform_box.blockSignals(False)
        if not control.get("busy"):
            index = next((i for i, choice in enumerate(choices) if choice["index"] == control.get("value")), -1)
            self.platform_box.blockSignals(True)
            self.platform_box.setCurrentIndex(index)
            self.platform_box.blockSignals(False)
        self.platform_box.setEnabled(self._changeable(control) and bool(choices))

    def _platform_chosen(self, position):
        control = ((self.store.state.get("keyboard") or {}).get("controls") or {}).get("platform") or {}
        if 0 <= position < len(self._choices) and self._choices[position]["index"] != control.get("value"):
            self.store.engine.set_keyboard_setting("platform", self._choices[position]["index"])

    def _ask_firmware(self):
        found = self.store.state.get("keyboard") or {}
        if firmware.available() and found.get("connected") and found.get("name") and not self.firmware.running:
            self._firmware_for = found["name"]
            self.firmware.start(found["name"], False)
        self._show_firmware()

    def _firmware_answered(self, entries, _checked):
        if (self.store.state.get("keyboard") or {}).get("name") == self._firmware_for:
            self._firmware_entries = entries
        elif self.isVisible():  # an answer about the keyboard before: this one is asked about now
            self._ask_firmware()
        self._show_firmware()

    def _show_firmware(self):
        entries = self._firmware_entries
        found = self.store.state.get("keyboard") or {}
        version = found.get("firmware") if found.get("connected") else None
        title = _("Firmware {}").format(version) if version else _("Firmware")
        self.firmware_row.title.setText("   ·   ".join(f"{entry['name']}  {entry['version']}" for entry in entries) or title)
        if not firmware.available():
            text = _("Install fwupd, the system's firmware updater, to see the firmware versions and check for updates.")
        elif self.firmware.running:
            text = _("Asking fwupd…")
        elif not entries and version:
            text = _("Read from the keyboard. fwupd, the system's firmware updater, does not see it right now.")
        elif not entries:
            text = _("fwupd, the system's firmware updater, does not see the keyboard right now.")
        else:
            text = _("Updates are installed by fwupd, the system's firmware updater, from the firmware Logitech "
                     "publishes for Linux. MX Options does not flash anything itself.")
        self.firmware_row.note.setText(text)

    def showEvent(self, event):
        super().showEvent(event)
        if self._firmware_for != (self.store.state.get("keyboard") or {}).get("name"):
            self._ask_firmware()

    def _reset(self):
        answer = QMessageBox.question(self, APP_NAME, _(
            "Give every key of the keyboard back its own function, in every app? The keys set for single apps go too."))
        if answer == QMessageBox.StandardButton.Yes:
            self.store.restore_keyboard()

    def _state(self, state):
        found = state.get("keyboard") or {}
        connected = bool(found.get("connected"))
        notice = notice_text(found)
        self.notice.setVisible(bool(notice))
        self.notice.setText(notice)
        settings = found.get("settings") or []
        for name, row in self.settings_rows.items():
            self.settings_card.set_row_visible(row, name in settings)
        self.settings_card.setVisible(any(name in SETTINGS for name in settings))
        controls = (found.get("controls") or {}) if connected else {}
        self._show_fn(controls.get("fn"))
        self.settings_card.set_row_visible(self.fn_notices_row, "fn" in settings and controls.get("fn") is not None)
        self._show_notice_switches()
        self._show_disable(controls.get("disable"), "disable" in settings)
        self._show_backlight(controls.get("backlight"), "backlight" in settings, bool(found.get("reason")))
        self._show_platform(controls.get("platform"), bool(found.get("reason")))
        battery = found.get("battery") if connected else None
        self.battery.setText(battery_text(battery) if battery else _("Connected") if connected else _("Not connected"))
        hosts = found.get("hosts") or []
        self.hosts_card.setVisible(bool(hosts))
        signature = (found.get("current_host"), tuple((h["paired"], h["name"]) for h in hosts))
        if signature != self._hosts_shown:
            self._hosts_shown = signature
            while self.hosts_layout.count():
                item = self.hosts_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            for index, host in enumerate(hosts):
                name = host["name"] or (_("Paired") if host["paired"] else _("Empty"))
                chip = QPushButton(f"{index + 1}  ·  {name}")
                chip.setMinimumHeight(40)
                chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                if index == found.get("current_host"):
                    chip.setObjectName("primary")
                self.hosts_layout.addWidget(chip, 1)
        if found.get("name") != self._firmware_for:
            self._firmware_entries = []  # fwupd's answer about another keyboard
        if self.isVisible() and connected and self._firmware_for != found.get("name"):
            self._ask_firmware()
        self._show_firmware()
