"""Widget that records a key combination as evdev key names."""

from evdev import ecodes
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QLabel

from .. import actions
from ..i18n import _
from ..vinput import MODIFIERS


def _evdev_name(scan_code):
    """Qt reports the XKB keycode on Linux, which is the evdev code + 8."""
    names = ecodes.KEY.get(scan_code - 8)
    if names is None:
        return None
    if isinstance(names, str):
        names = [names]
    return next((n for n in names if n.startswith("KEY_") and n != "KEY_MIN_INTERESTING"), None)


class ShortcutRecorder(QLabel):
    """Click it, press a key combination, get evdev key names."""

    recorded = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("recorder")
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._keys = []
        self._modifiers = []
        self._refresh()

    def set_keys(self, key_names):
        self._keys = list(key_names)
        self._refresh()

    def _refresh(self):
        if self.hasFocus():
            partial = actions.keys_label(self._modifiers)
            self.setText(f"{partial} + …" if partial else _("Press the shortcut…"))
        else:
            self.setText(actions.keys_label(self._keys) if self._keys else _("Click to record a shortcut"))

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._modifiers = []
        self._refresh()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._modifiers = []
        self._refresh()

    def event(self, event):
        # Tab normally moves focus; while recording it is just another key.
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.keyPressEvent(event)
            return True
        if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.isAutoRepeat():
            return
        name = _evdev_name(event.nativeScanCode())
        if name is None:
            return
        if name in MODIFIERS:
            if name not in self._modifiers:
                self._modifiers.append(name)
            self._refresh()
            return
        self._finish(self._modifiers + [name])

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat() or not self._modifiers:
            return
        if _evdev_name(event.nativeScanCode()) in self._modifiers:
            self._finish(list(self._modifiers))  # a bare modifier combination

    def _finish(self, key_names):
        self._keys = key_names
        self._modifiers = []
        self.clearFocus()
        self._refresh()
        self.recorded.emit(key_names)
