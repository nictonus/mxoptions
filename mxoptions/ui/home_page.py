"""The start page, as Options+ has it: a greeting, then the mouse and the keyboard side by side, each with a badge of
its battery and how it is connected. A device opens its own pages (Buttons, Keys)."""

import time

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from .. import device
from ..i18n import _
from ..keyboard import LOW_BATTERY
from . import icons
from .keyboard_page import CHARGING, KeyboardPicture, battery_text
from .mouse_view import MouseView
from .widgets import BatteryIcon

DIM = 0.45  # opacity of the picture of a device that is not connected
CONNECTIONS = {False: ("preferences-system-bluetooth", "Bluetooth"), True: ("network-wireless", "Receiver")}


def greeting(hour):
    if 5 <= hour < 12:
        return _("Good morning")
    if 12 <= hour < 18:
        return _("Good afternoon")
    return _("Good evening")


def _reading(found):
    """(percent, charging or full) of a connected device with a battery level, else None. A percent of 0 tells no
    level (see app.mouse_messages)."""
    battery = found.get("battery") if found and found.get("connected") else None
    if not battery or not battery[0]:
        return None
    return battery[0], battery[1] in CHARGING or battery[1] == device.BATTERY_FULL


def badge_text(found):
    """What the badge under a device says: its battery, "Connected" without a level, or "Not connected"."""
    if not found or not found.get("connected"):
        return _("Not connected")
    reading = _reading(found)
    return f"{reading[0]}%" if reading else _("Connected")


class DeviceCard(QPushButton):
    """One device of the home page: its picture, and the badge under it. Click, Space or Enter opens its pages."""

    def __init__(self, picture, kind, parent=None):
        super().__init__(parent)
        self.setObjectName("homeDevice")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)  # as tall as the other: badges in line
        self.kind = kind  # "Mouse" or "Keyboard": its name while none is known
        self.picture = picture
        picture.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._fade = QGraphicsOpacityEffect(picture)
        self._fade.setOpacity(1.0)
        picture.setGraphicsEffect(self._fade)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        layout.addWidget(picture, 1, Qt.AlignmentFlag.AlignVCenter)
        badge = QWidget()
        badge.setObjectName("homeBadge")
        badge.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        line = QHBoxLayout(badge)
        line.setContentsMargins(10, 5, 10, 5)
        line.setSpacing(6)
        self.percent = QLabel()
        self.percent.setObjectName("homePercent")
        self.bolt = QLabel("⚡")  # charging, or charged full: as Options+ shows it
        self.bolt.setObjectName("homeBolt")
        self.battery = BatteryIcon()
        self.link_icon = QLabel()
        self.link_text = QLabel()
        self.link_text.setObjectName("homeLink")
        for widget in (self.percent, self.bolt, self.battery, self.link_icon, self.link_text):
            line.addWidget(widget)
        layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignHCenter)
        self.badge = badge

    def set_device(self, found):
        connected = bool(found and found.get("connected"))
        reading = _reading(found)
        self.percent.setText(badge_text(found))
        level = "off" if not connected else "low" if reading and reading[0] <= LOW_BATTERY and not reading[1] else \
            "charging" if reading and reading[1] else "ok"
        if self.percent.property("level") != level:
            self.percent.setProperty("level", level)
            self.percent.style().unpolish(self.percent)
            self.percent.style().polish(self.percent)
        self.bolt.setVisible(bool(reading and reading[1]))
        self.battery.set_level(*(reading if reading else (None, False)))
        self.battery.setVisible(bool(reading))
        name, label = CONNECTIONS.get(found.get("receiver") if connected else None, (None, None))
        has_icon = bool(name and icons.has(name))
        if has_icon:
            self.link_icon.setPixmap(icons.icon(name).pixmap(QSize(16, 16)))
        self.link_icon.setVisible(has_icon)
        self.link_text.setText(_(label) if label else "")
        self.link_text.setVisible(bool(label) and not has_icon)  # in words where the icon theme has no icon for it
        self._fade.setOpacity(1.0 if connected else DIM)
        status = battery_text(found["battery"]) if reading else badge_text(found)
        spoken = f"{(found or {}).get('name') or _(self.kind)}: {status}" + (f", {_(label)}" if label else "")
        self.setAccessibleName(spoken)
        self.setToolTip(spoken)

    # a QPushButton sizes itself by its text: this one by what its layout holds
    def sizeHint(self):
        return self.layout().sizeHint()

    def minimumSizeHint(self):
        return self.layout().totalMinimumSize()

    def hasHeightForWidth(self):
        return self.layout().hasHeightForWidth()

    def heightForWidth(self, width):
        return self.layout().totalHeightForWidth(width)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and not event.isAutoRepeat():
            self.click()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            return  # taken on the press already: QPushButton would click again on the release
        super().keyReleaseEvent(event)


class HomePage(QWidget):
    open_mouse = Signal()
    open_keyboard = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 30, 34, 30)
        layout.setSpacing(0)
        self.title = QLabel()
        self.title.setObjectName("homeTitle")
        layout.addWidget(self.title)
        layout.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(36)
        self.mouse_view = MouseView({})
        self.mouse = DeviceCard(self.mouse_view, "Mouse")
        self.keyboard_picture = KeyboardPicture()
        self.keyboard = DeviceCard(self.keyboard_picture, "Keyboard")
        row.addWidget(self.mouse, 2)
        row.addWidget(self.keyboard, 5)
        layout.addLayout(row)
        layout.addStretch(1)
        self.mouse.clicked.connect(self.open_mouse)
        self.keyboard.clicked.connect(self.open_keyboard)
        self.title.setText(greeting(time.localtime().tm_hour))
        store.state_changed.connect(self._refresh)
        store.changed.connect(self._refresh)
        self._refresh()

    def _refresh(self, *_args):
        state, cfg = self.store.state, self.store.cfg
        self.mouse_view.set_style(cfg["app"]["model"], cfg["app"]["mouse_color"])
        self.mouse_view.set_family(device.family(state.get("name") or ""))
        self.mouse.set_device(state)
        found = state.get("keyboard")
        self.keyboard.setVisible(found is not None or bool(cfg["keyboard"]["keys"]))  # as the sidebar's keyboard
        self.keyboard_picture.set_keyboard(found.get("keys", []) if found else [], found["name"] if found else None)
        self.keyboard.set_device(found)

    def showEvent(self, event):
        super().showEvent(event)
        self.title.setText(greeting(time.localtime().tm_hour))
