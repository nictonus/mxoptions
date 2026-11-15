"""Main window: sidebar with the device status and navigation, pages on the right."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import APP_NAME, device
from ..i18n import _
from . import icons
from .flow_page import FlowPage
from .gestures_page import GesturesPage
from .keyboard_page import CHARGING, KeyboardPage, KeysPage, battery_text
from .pages import ButtonsPage, DevicePage, HapticsPage, PointScrollPage
from .ring_page import RingPage
from .widgets import BatteryIcon, ui_scale

NAV_ICON = 18  # px: the pages' icons in the sidebar (freedesktop names), shown where the icon theme has them


class MainWindow(QMainWindow):
    def __init__(self, store, hide_on_close=True):
        super().__init__()
        self.store = store
        self.hide_on_close = hide_on_close
        self.setWindowTitle(APP_NAME)
        scale = ui_scale()
        self.setMinimumSize(int(1180 * scale), 700)
        self.resize(int(1280 * scale), 780)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 24, 16, 16)
        side.setSpacing(4)
        side.addWidget(self._kind(_("Mouse")))
        self.device_name = QLabel(_("No mouse"))
        self.device_name.setObjectName("deviceName")
        self.device_name.setWordWrap(True)
        side.addWidget(self.device_name)
        status_line = QHBoxLayout()
        status_line.setSpacing(8)
        self.battery = BatteryIcon()
        status_line.addWidget(self.battery)
        self.device_status = QLabel()
        self.device_status.setObjectName("deviceStatus")
        status_line.addWidget(self.device_status, 1)
        side.addLayout(status_line)
        side.addSpacing(14)

        self.stack = QStackedWidget()
        self._nav = QButtonGroup(self)
        self._nav.setExclusive(True)
        self.buttons_page = ButtonsPage(store)
        gestures_page = GesturesPage(store)
        gestures_page.edit_actions.connect(self._edit_button)
        pages = [
            (_("Buttons"), self.buttons_page, "input-mouse"),
            (_("Gestures"), gestures_page, "transform-move"),
            (_("Point & scroll"), PointScrollPage(store), "input-touchpad"),
            (_("Haptics"), HapticsPage(store), "audio-volume-medium"),
            (_("Actions Ring"), RingPage(store), "view-grid"),
            (_("Flow"), FlowPage(store), "network-connect"),
            (_("Device & app"), DevicePage(store), "configure"),  # the app's own settings: at the foot of the sidebar
        ]
        for index, (title, page, icon) in enumerate(pages):
            if index < len(pages) - 1:  # the last one's button is made last, so that Tab reaches it last as well
                side.addWidget(self._nav_button(title, index, icon))
            self.stack.addWidget(page)
        self._nav.button(0).setChecked(True)
        # The keyboard, under the mouse: shown once one was seen, or its keys have settings.
        self.keyboard_block = QWidget()
        block = QVBoxLayout(self.keyboard_block)
        block.setContentsMargins(0, 22, 0, 0)
        block.setSpacing(4)
        block.addWidget(self._kind(_("Keyboard")))
        self.keyboard_name = QLabel()
        self.keyboard_name.setObjectName("deviceName")
        self.keyboard_name.setWordWrap(True)
        block.addWidget(self.keyboard_name)
        status_line = QHBoxLayout()
        status_line.setSpacing(8)
        self.keyboard_battery = BatteryIcon()
        status_line.addWidget(self.keyboard_battery)
        self.keyboard_status = QLabel()
        self.keyboard_status.setObjectName("deviceStatus")
        status_line.addWidget(self.keyboard_status, 1)
        block.addLayout(status_line)
        block.addSpacing(14)
        for title, page, icon in ((_("Keys"), KeysPage(store), "input-keyboard"),
                                  (_("Keyboard"), KeyboardPage(store), "preferences-desktop-keyboard")):
            block.addWidget(self._nav_button(title, self.stack.count(), icon))
            self.stack.addWidget(page)
        self._keyboard_pages = range(len(pages), self.stack.count())
        self.keyboard_block.hide()
        side.addWidget(self.keyboard_block)
        side.addStretch(1)
        line = QFrame()
        line.setObjectName("separator")
        side.addWidget(line)
        side.addSpacing(6)
        settings = len(pages) - 1
        side.addWidget(self._nav_button(pages[settings][0], settings, pages[settings][2]))
        widest = max(self._nav.button(index).fontMetrics().horizontalAdvance(title)
                     for index, (title, _page, _icon) in enumerate(pages))
        with_icons = any(not button.icon().isNull() for button in self._nav.buttons())
        if with_icons:  # a page the icon theme has no icon for keeps the room of one, so that the names line up
            blank = QPixmap(NAV_ICON, NAV_ICON)
            blank.fill(Qt.GlobalColor.transparent)
            for button in self._nav.buttons():
                if button.icon().isNull():
                    button.setIcon(QIcon(blank))
                    button.setIconSize(QSize(NAV_ICON, NAV_ICON))
        # the stylesheet font is a little larger; an icon takes its width and a gap
        sidebar.setFixedWidth(max(214, int(widest * 1.1) + 72 + (NAV_ICON + 8 if with_icons else 0)))
        layout.addWidget(sidebar)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.banner_box = QWidget()
        banner_layout = QVBoxLayout(self.banner_box)
        banner_layout.setContentsMargins(34, 16, 34, 0)
        self.banner = QLabel()
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        banner_layout.addWidget(self.banner)
        self.rival_banner = QLabel()
        self.rival_banner.setObjectName("banner")
        self.rival_banner.setWordWrap(True)
        banner_layout.addWidget(self.rival_banner)
        right.addWidget(self.banner_box)
        right.addWidget(self.stack, 1)
        layout.addLayout(right, 1)

        store.state_changed.connect(self._state)
        store.changed.connect(self._show_keyboard)
        self._state(store.state)

    @staticmethod
    def _kind(text):
        """The small heading over a device in the sidebar: what kind of device it is."""
        label = QLabel(text.upper())
        label.setObjectName("deviceKind")
        return label

    def _nav_button(self, title, index, icon):
        """The sidebar's button for page ``index``, with ``icon`` where the icon theme has it."""
        button = QPushButton(title.replace("&", "&&"))
        button.setObjectName("navButton")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if icons.has(icon):
            button.setIcon(icons.icon(icon))
            button.setIconSize(QSize(NAV_ICON, NAV_ICON))
        button.clicked.connect(lambda _checked=False, i=index: self.stack.setCurrentIndex(i))
        self._nav.addButton(button, index)
        return button

    def _show_keyboard(self):
        found = self.store.state.get("keyboard")
        shown = found is not None or bool(self.store.cfg["keyboard"]["keys"])
        self.keyboard_block.setVisible(shown)
        if not shown and self.stack.currentIndex() in self._keyboard_pages:
            self._nav.button(0).setChecked(True)
            self.stack.setCurrentIndex(0)
        if not shown:
            return
        connected = bool(found and found["connected"])
        self.keyboard_name.setText(found["name"] if found else _("Keyboard"))
        battery = found.get("battery") if connected else None
        self.keyboard_battery.set_level(*(battery[0], battery[1] in CHARGING or battery[1] == device.BATTERY_FULL)
                                        if battery else (None, False))
        self.keyboard_battery.setVisible(bool(battery))
        self.keyboard_status.setText(battery_text(battery) if battery else _("Connected") if connected else _("Not connected"))

    def _state(self, state):
        connected = state["connected"]
        self.device_name.setText(state["name"] or _("No mouse"))
        battery = state.get("battery") if connected else None
        if battery:
            percent, status = battery
            charging = status in CHARGING or status == device.BATTERY_FULL
            self.battery.set_level(percent, charging)
            self.device_status.setText(battery_text(battery))
        else:
            self.battery.set_level(None, False)
            self.device_status.setText(_("Connected") if connected else _("Not connected"))
        self.battery.setVisible(bool(battery))
        self._show_keyboard()
        rivals = state.get("rivals") or []
        self.banner_box.setVisible(not connected or bool(rivals))
        self.banner.setVisible(not connected)
        self.rival_banner.setVisible(bool(rivals))
        if rivals:
            self.rival_banner.setText(
                _("{} is running too. Two programs that set the same mouse up undo each other's work: quit it while "
                  "you use MX Options.").format(_(" and ").join(rivals))
                + (" " + _("On Windows its service (OptionsPlusUpdaterService) starts it again unless that is stopped as well.")
                   if "Logi Options+" in rivals else "")
                + (" " + _("logiops is a system service: sudo systemctl disable --now logid") if "logiops" in rivals else ""))
        if not connected:
            self.banner.setText(
                _("{} is asleep or out of range. Move it to wake it up; your settings are applied automatically.").format(state["name"])
                if state["known"] else
                _("Looking for your mouse… Turn it on and make sure its receiver is plugged in.")
            )

    def _edit_button(self, key):
        """Open the Buttons page on one button."""
        self._nav.button(0).setChecked(True)
        self.stack.setCurrentIndex(0)
        self.buttons_page.view.select(key)

    def show_and_raise(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def showEvent(self, event):
        super().showEvent(event)
        self.store.engine.refresh()  # battery and hosts may have changed while hidden
        self.store.engine.set_monitor(True)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.store.engine.set_monitor(False)
        self.store.pressed_changed.emit(frozenset())

    def closeEvent(self, event):
        if self.hide_on_close:
            event.ignore()
            self.hide()
        else:
            super().closeEvent(event)
