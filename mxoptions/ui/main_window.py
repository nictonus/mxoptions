"""Main window: sidebar with the device status and navigation, pages on the right."""

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import APP_NAME, device
from ..i18n import _
from . import theme
from .flow_page import FlowPage
from .gestures_page import GesturesPage
from .pages import ButtonsPage, DevicePage, HapticsPage, PointScrollPage
from .ring_page import RingPage
from .widgets import ui_scale

CHARGING = (device.BATTERY_CHARGING, device.BATTERY_ALMOST_FULL, device.BATTERY_SLOW_CHARGE)


class BatteryIcon(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._percent = None
        self._charging = False
        self.setFixedSize(QSize(30, 16))

    def set_level(self, percent, charging):
        self._percent, self._charging = percent, charging
        self.update()

    def paintEvent(self, _event):
        if self._percent is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(1, 2, 24, 12)
        painter.setPen(QPen(QColor(theme.MUTED), 1.3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(body, 3.5, 3.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.MUTED))
        painter.drawRoundedRect(QRectF(26, 5.5, 2.5, 5), 1, 1)
        if self._charging or self._percent > 30:
            color = theme.ACCENT
        elif self._percent > 15:
            color = theme.WARN
        else:
            color = theme.DANGER
        painter.setBrush(QColor(color))
        width = max(2.0, (body.width() - 5) * min(100, self._percent) / 100.0)
        painter.drawRoundedRect(QRectF(body.left() + 2.5, body.top() + 2.5, width, body.height() - 5), 1.5, 1.5)


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
        side.addSpacing(22)

        self.stack = QStackedWidget()
        self._nav = QButtonGroup(self)
        self._nav.setExclusive(True)
        self.buttons_page = ButtonsPage(store)
        gestures_page = GesturesPage(store)
        gestures_page.edit_actions.connect(self._edit_button)
        pages = [
            (_("Buttons"), self.buttons_page),
            (_("Gestures"), gestures_page),
            (_("Point & scroll"), PointScrollPage(store)),
            (_("Haptics"), HapticsPage(store)),
            (_("Actions Ring"), RingPage(store)),
            (_("Flow"), FlowPage(store)),
            (_("Device & app"), DevicePage(store)),
        ]
        for index, (title, page) in enumerate(pages):
            button = QPushButton(title.replace("&", "&&"))
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, i=index: self.stack.setCurrentIndex(i))
            self._nav.addButton(button, index)
            side.addWidget(button)
            self.stack.addWidget(page)
        self._nav.button(0).setChecked(True)
        side.addStretch(1)
        widest = max(button.fontMetrics().horizontalAdvance(title) for button, (title, _page) in zip(self._nav.buttons(), pages))
        sidebar.setFixedWidth(max(214, int(widest * 1.1) + 72))  # the stylesheet font is a little larger
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
        self._state(store.state)

    def _state(self, state):
        connected = state["connected"]
        self.device_name.setText(state["name"] or _("No mouse"))
        battery = state.get("battery") if connected else None
        if battery:
            percent, status = battery
            charging = status in CHARGING or status == device.BATTERY_FULL
            self.battery.set_level(percent, charging)
            if status == device.BATTERY_FULL:
                text = _("Fully charged")
            elif status in CHARGING:
                text = _("Charging · {}%").format(percent)
            else:
                text = f"{percent}%"
            self.device_status.setText(text)
        else:
            self.battery.set_level(None, False)
            self.device_status.setText(_("Connected") if connected else _("Not connected"))
        self.battery.setVisible(bool(battery))
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
