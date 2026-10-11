"""Settings page for the Actions Ring: the bubble preview and the editor for one bubble."""

import copy

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPainter
from PySide6.QtWidgets import (
    QButtonGroup, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu,
    QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import APP_NAME, actions, config
from ..i18n import _
from . import ring, theme
from .action_picker import ActionList
from .pages import BUTTON_TITLES
from .widgets import ElidedButton, Row, Segmented, SliderRow, SwatchRow, hint_label, ui_scale

ICON_CHOICES = (
    "folder", "folder-favorites", "starred", "emblem-favorite", "go-home", "user-identity",
    "internet-web-browser", "mail-message", "dialog-messages", "utilities-terminal", "system-file-manager",
    "accessories-text-editor", "accessories-calculator", "applications-development", "applications-graphics",
    "applications-multimedia", "applications-games", "applications-office", "camera-photo", "camera-video",
    "media-playback-start", "media-skip-forward", "audio-volume-high", "audio-headphones",
    "microphone-sensitivity-high", "video-display", "input-keyboard", "input-mouse", "preferences-system", "configure",
    "system-search", "system-lock-screen", "system-shutdown", "system-reboot", "view-refresh", "edit-copy",
    "edit-paste", "edit-cut", "edit-undo", "edit-delete", "document-new", "document-open", "document-save",
    "tab-new", "window-new", "view-fullscreen", "zoom-in", "zoom-out", "bookmarks", "view-calendar", "clock",
    "weather-clear", "network-wireless", "preferences-system-bluetooth", "color-picker", "draw-brush",
    "text-x-script", "media-record", "face-smile", "help-about", "dialog-ok", "list-add", "games-highscores", "love",
)


def _field_label(text):
    label = QLabel(text)
    label.setObjectName("fieldLabel")
    return label


class IconButton(QPushButton):
    """Shows a bubble's icon spec (theme icon, image or emoji)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(44, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._spec = ""
        self._fallback = ""

    def set_icon_spec(self, spec, fallback):
        self._spec, self._fallback = spec, fallback
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().toRectF().adjusted(10, 10, -10, -10)
        ring.draw_icon(painter, self._spec, rect, self._fallback, theme.TEXT)


class IconPicker(QFrame):
    """Popup with a grid of icons, a field for an emoji or icon name, and an image chooser."""

    picked = Signal(str)  # "" = automatic

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setObjectName("popup")
        outer.addWidget(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        grid = QGridLayout()
        grid.setSpacing(2)
        names = [name for name in ICON_CHOICES if QIcon.hasThemeIcon(name)]
        for index, name in enumerate(names):
            cell = QPushButton()
            cell.setObjectName("iconCell")
            cell.setIcon(QIcon.fromTheme(name))
            cell.setIconSize(QSize(22, 22))
            cell.setFixedSize(34, 34)
            cell.setCursor(Qt.CursorShape.PointingHandCursor)
            cell.setToolTip(name)
            cell.clicked.connect(lambda _checked=False, n=name: self._pick(n))
            grid.addWidget(cell, index // 9, index % 9)
        layout.addLayout(grid)
        line = QHBoxLayout()
        line.setSpacing(6)
        self.text = QLineEdit()
        self.text.setPlaceholderText(_("Emoji, letters or an icon name"))
        self.text.returnPressed.connect(lambda: self._pick(self.text.text().strip()))
        line.addWidget(self.text, 1)
        image = QPushButton(_("Image…"))
        image.clicked.connect(self._choose_image)
        auto = QPushButton(_("Automatic"))
        auto.clicked.connect(lambda: self._pick(""))
        for button in (image, auto):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            line.addWidget(button)
        layout.addLayout(line)

    def popup_at(self, widget, current):
        self.text.setText(current if current and not ring.themed_icon(current) else "")
        self.adjustSize()
        self.move(widget.mapToGlobal(QPoint(0, widget.height() + 6)))
        self.show()

    def _pick(self, spec):
        self.hide()
        self.picked.emit(spec)

    def _choose_image(self):
        self.hide()
        path, _filter = QFileDialog.getOpenFileName(
            self.parentWidget(), _("Choose an image"), "", _("Images") + " (*.png *.svg *.jpg *.jpeg *.webp *.ico)"
        )
        if path:
            self.picked.emit(path)


class BubbleEditor(QFrame):
    """Right-hand panel editing one bubble."""

    changed = Signal(str, object)   # field ("label", "icon", "color", "action", "scroll"), new value
    kind_changed = Signal(str)      # "action", "folder" or "scroll"
    removed = Signal()
    moved = Signal(int)             # -1 / +1
    opened = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("picker")
        self.setFixedWidth(int(320 * ui_scale()))
        self._bubble = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 20, 12, 14)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 16, 0)
        head.setSpacing(6)
        self.title = QLabel()
        self.title.setObjectName("pickerTitle")
        head.addWidget(self.title, 1)
        self.left = self._tool("←", _("Move one place back"), lambda: self.moved.emit(-1))
        self.right = self._tool("→", _("Move one place forward"), lambda: self.moved.emit(1))
        self.delete = self._tool("✕", _("Remove this bubble"), self.removed.emit)
        for button in (self.left, self.right, self.delete):
            head.addWidget(button)
        layout.addLayout(head)

        fields = QVBoxLayout()
        fields.setContentsMargins(0, 0, 16, 0)
        fields.setSpacing(6)
        fields.addWidget(_field_label(_("Name").upper()))
        self.name = QLineEdit()
        self.name.textEdited.connect(lambda text: self.changed.emit("label", text))
        fields.addWidget(self.name)
        fields.addSpacing(4)
        fields.addWidget(_field_label(_("Icon and colour").upper()))
        look = QHBoxLayout()
        look.setSpacing(10)
        self.icon = IconButton()
        self.icon.clicked.connect(self._pick_icon)
        look.addWidget(self.icon)
        self.colors = SwatchRow(theme.ACCENTS, allow_auto=True)
        self.colors.selected.connect(lambda color: self.changed.emit("color", color))
        look.addWidget(self.colors, 1)
        fields.addLayout(look)
        fields.addSpacing(4)
        self.kind = Segmented([("action", _("Action")), ("folder", _("Folder")), ("scroll", _("Dial"))])
        self.kind.selected.connect(self.kind_changed.emit)
        fields.addWidget(self.kind)
        layout.addLayout(fields)

        self.stack = QStackedWidget()
        self.actions = ActionList(exclude=frozenset({"default", "gestures", "ring", "dpi_shift"}))
        self.actions.picked.connect(lambda action: self.changed.emit("action", action))
        self.stack.addWidget(self.actions)
        folder = QWidget()
        folder_layout = QVBoxLayout(folder)
        folder_layout.setContentsMargins(0, 8, 16, 0)
        folder_layout.setSpacing(10)
        self.folder_hint = hint_label("")
        folder_layout.addWidget(self.folder_hint)
        open_button = QPushButton(_("Open folder"))
        open_button.setObjectName("primary")
        open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        open_button.clicked.connect(self.opened.emit)
        folder_layout.addWidget(open_button)
        folder_layout.addStretch(1)
        self.stack.addWidget(folder)

        dials = QWidget()
        dial_layout = QVBoxLayout(dials)
        dial_layout.setContentsMargins(0, 8, 16, 0)
        dial_layout.setSpacing(2)
        dial_layout.addWidget(hint_label(_("Point at the bubble and turn the scroll wheel. The ring stays open while you adjust.")))
        dial_layout.addSpacing(6)
        self._dial_group = QButtonGroup(self)
        self._dial_group.setExclusive(True)
        self._dials = {}
        for scroll_id, (label, icon, _up, _down, _click) in actions.SCROLLS.items():
            row = ElidedButton(_(label))
            row.setObjectName("actionRow")
            row.setCheckable(True)
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.setIcon(QIcon.fromTheme(ring.theme_icon_name(icon) or icon))
            row.setIconSize(QSize(18, 18))
            row.clicked.connect(lambda _checked=False, s=scroll_id: self.changed.emit("scroll", s))
            self._dial_group.addButton(row)
            self._dials[scroll_id] = row
            dial_layout.addWidget(row)
        dial_layout.addStretch(1)
        self.stack.addWidget(dials)
        layout.addWidget(self.stack, 1)

        self.icon_picker = IconPicker(self)
        self.icon_picker.picked.connect(lambda spec: self.changed.emit("icon", spec))

    def _tool(self, text, tooltip, callback):
        button = QPushButton(text)
        button.setObjectName("tool")
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        return button

    def _pick_icon(self):
        if self._bubble is not None:
            self.icon_picker.popup_at(self.icon, self._bubble.get("icon", ""))

    def show_bubble(self, bubble, index, count, may_be_folder):
        self._bubble = bubble
        folder = ring.is_folder(bubble)
        self.title.setText(_("Bubble {}").format(index + 1))
        automatic = ring.bubble_label({**bubble, "label": ""})
        self.name.setPlaceholderText(automatic)
        if not self.name.hasFocus() and self.name.text() != bubble.get("label", ""):
            self.name.setText(bubble.get("label", ""))
        self.icon.set_icon_spec(ring.bubble_icon(bubble), ring.bubble_label(bubble))
        self.colors.set_value(bubble.get("color", ""))
        dial = ring.is_dial(bubble)
        self.kind.set_value("folder" if folder else "scroll" if dial else "action")
        self.kind.set_option_enabled("folder", may_be_folder or folder)
        self.left.setEnabled(index > 0)
        self.right.setEnabled(index < count - 1)
        self.stack.setCurrentIndex(1 if folder else 2 if dial else 0)
        if folder:
            self.folder_hint.setText(_("This bubble opens its own ring. Bubbles inside: {}.").format(len(bubble["children"])))
        elif dial:
            self._dials[bubble["scroll"]].setChecked(True)
        else:
            self.actions.set_current(bubble.get("action"))


class RingPage(QWidget):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._path = []   # indices of the folders opened in the preview
        self._index = 0
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        left = QVBoxLayout()
        left.setContentsMargins(34, 28, 24, 20)
        left.setSpacing(8)
        heading = QHBoxLayout()
        title = QLabel(_("Actions Ring"))
        title.setObjectName("pageTitle")
        heading.addWidget(title, 1)
        self.style = Segmented([("bubbles", _("Bubbles")), ("pie", _("Pie")), ("grid", _("Tiles"))])
        self.style.selected.connect(lambda value: self.store.set("ring", "style", value))
        heading.addWidget(self.style, 0, Qt.AlignmentFlag.AlignBottom)
        presets = QPushButton(_("Presets"))  # Qt adds the drop-down arrow itself
        presets.setCursor(Qt.CursorShape.PointingHandCursor)
        self.presets_menu = QMenu(presets)
        self.presets_menu.aboutToShow.connect(self._fill_presets_menu)
        presets.setMenu(self.presets_menu)
        heading.addWidget(presets, 0, Qt.AlignmentFlag.AlignBottom)
        left.addLayout(heading)
        subtitle = QLabel(_("Bubbles around your cursor. A bubble runs an action or opens its own ring of bubbles."))
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        left.addWidget(subtitle)

        crumbs = QHBoxLayout()
        self.up = QPushButton("‹  " + _("Back"))
        self.up.setObjectName("flat")
        self.up.setCursor(Qt.CursorShape.PointingHandCursor)
        self.up.clicked.connect(self._go_up)
        crumbs.addWidget(self.up)
        self.crumbs = QLabel()
        self.crumbs.setObjectName("hint")
        crumbs.addWidget(self.crumbs, 1)
        left.addLayout(crumbs)

        self.preview = ring.RingPreview()
        left.addWidget(self.preview, 1)
        self.usage = hint_label(_(
            "Press the button and the ring opens around the cursor. Click a bubble, or keep the button held, "
            "point at a bubble and let go. Over a dial, turn the wheel or the thumb wheel."
        ))
        self.usage.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        left.addWidget(self.usage)
        self.size = SliderRow(_("Ring size"), 0.7, 1.5, 0.05, "{:.0%}")
        self.size.changed.connect(lambda value: self.store.set("ring", "size", round(value, 2)))
        left.addWidget(self.size)
        self.button = Segmented([(key, _(BUTTON_TITLES[key])) for key in config.GESTURE_BUTTONS])
        self.button.selected.connect(lambda key: self.store.assign_role("ring", key))
        left.addWidget(Row(_("The ring opens with"), _("Gestures move to the other thumb button."), self.button))
        self.unassigned = QLabel(_("No button opens the ring right now. Assign it on the Buttons page."))
        self.unassigned.setObjectName("banner")
        self.unassigned.setWordWrap(True)
        left.addWidget(self.unassigned)
        layout.addLayout(left, 1)

        self.editor = BubbleEditor()
        layout.addWidget(self.editor)

        self.preview.bubble_clicked.connect(self._select)
        self.preview.bubble_opened.connect(self._open)
        self.preview.add_clicked.connect(self._add)
        self.preview.center_clicked.connect(self._go_up)
        self.editor.changed.connect(self._field_changed)
        self.editor.kind_changed.connect(self._kind_changed)
        self.editor.removed.connect(self._remove)
        self.editor.moved.connect(self._move)
        self.editor.opened.connect(lambda: self._open(self._index))
        store.changed.connect(self._refresh)
        self._refresh()

    # -- navigating the nested list -------------------------------------------

    @staticmethod
    def _level_of(root, path):
        level = root
        for index in path:
            level = level[index]["children"]
        return level

    def _valid_path(self, root):
        """The stored path, cut where it no longer leads into a folder."""
        level, path = root, []
        for index in self._path:
            if not (0 <= index < len(level) and ring.is_folder(level[index])):
                break
            path.append(index)
            level = level[index]["children"]
        return path

    def _edit(self, change):
        """Apply ``change(level, root)`` to a copy of the ring and store it."""
        root = copy.deepcopy(self.store.cfg["ring"]["bubbles"])
        self._path = self._valid_path(root)
        change(self._level_of(root, self._path), root)
        self.store.set("ring", "bubbles", root)
        self._refresh()

    # -- preview events -----------------------------------------------------

    def _select(self, index):
        self._index = index
        self._refresh()

    def _open(self, index):
        root = self.store.cfg["ring"]["bubbles"]
        level = self._level_of(root, self._valid_path(root))
        if 0 <= index < len(level) and ring.is_folder(level[index]):
            self._path = self._valid_path(root) + [index]
            self._index = 0
            self._refresh()

    def _go_up(self):
        if self._path:
            self._index = self._path.pop()
            self._refresh()

    def _fill_presets_menu(self):
        menu = self.presets_menu
        menu.clear()
        for _preset_id, label, builder in config.RING_PRESETS:
            menu.addAction(_(label), lambda b=builder, n=label: self._apply_preset(_(n), b))
        saved = self.store.cfg["ring"]["saved"]
        if saved:
            menu.addSeparator()
            for entry in saved:
                menu.addAction(entry["name"], lambda e=entry: self._apply_preset(e["name"], lambda: copy.deepcopy(e["bubbles"])))
        menu.addSeparator()
        menu.addAction(_("Save the current ring as a preset…"), self._save_preset)
        if saved:
            remove = menu.addMenu(_("Delete a saved preset"))
            for entry in saved:
                remove.addAction(entry["name"], lambda n=entry["name"]: self._delete_preset(n))

    def _apply_preset(self, name, builder):
        answer = QMessageBox.question(
            self, APP_NAME, _("Replace the whole ring with the \"{}\" preset? Your current bubbles will be lost.").format(name)
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._path, self._index = [], 0
            self.store.set("ring", "bubbles", builder())
            self._refresh()

    def _save_preset(self):
        name, accepted = QInputDialog.getText(self, APP_NAME, _("Name for the preset:"))
        name = name.strip()[:40]
        if not accepted or not name:
            return
        saved = [entry for entry in self.store.cfg["ring"]["saved"] if entry["name"] != name]  # same name: replace
        saved.append({"name": name, "bubbles": copy.deepcopy(self.store.cfg["ring"]["bubbles"])})
        self.store.set("ring", "saved", saved[-config.MAX_SAVED_RINGS:])

    def _delete_preset(self, name):
        self.store.set("ring", "saved", [entry for entry in self.store.cfg["ring"]["saved"] if entry["name"] != name])

    def _add(self):
        def change(level, _root):
            if len(level) < config.MAX_BUBBLES:
                level.append(config.bubble())
                self._index = len(level) - 1
        self._edit(change)

    # -- editor events ------------------------------------------------------

    def _field_changed(self, field, value):
        def change(level, _root):
            if 0 <= self._index < len(level):
                level[self._index][field] = copy.deepcopy(value)
        self._edit(change)

    def _kind_changed(self, kind):
        root = self.store.cfg["ring"]["bubbles"]
        level = self._level_of(root, self._valid_path(root))
        if not 0 <= self._index < len(level):
            return
        current = level[self._index]
        now = "folder" if ring.is_folder(current) else "scroll" if ring.is_dial(current) else "action"
        if kind == now:
            return
        if now == "folder" and current["children"]:
            answer = QMessageBox.question(
                self, APP_NAME, _("Stop this bubble being a folder? The {} bubbles inside it will be removed.").format(len(current["children"]))
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._refresh()
                return

        def change(level, _root):
            bubble = level[self._index]
            action = bubble.pop("action", None)
            bubble.pop("children", None)
            bubble.pop("scroll", None)
            if kind == "folder":
                bubble["children"] = [config.bubble(action=action)] if action and action.get("type") != "none" else []
            elif kind == "scroll":
                bubble["scroll"] = next(iter(actions.SCROLLS))
            else:
                bubble["action"] = {"type": "none"}
        self._edit(change)

    def _remove(self):
        def change(level, root):
            if not 0 <= self._index < len(level) or (level is root and len(level) <= 1):
                return
            del level[self._index]
            self._index = max(0, min(self._index, len(level) - 1))
        self._edit(change)

    def _move(self, offset):
        def change(level, _root):
            target = self._index + offset
            if 0 <= self._index < len(level) and 0 <= target < len(level):
                level[self._index], level[target] = level[target], level[self._index]
                self._index = target
        self._edit(change)

    # -- display ------------------------------------------------------------

    def _refresh(self):
        cfg = self.store.cfg
        root = cfg["ring"]["bubbles"]
        self._path = self._valid_path(root)
        level = self._level_of(root, self._path)
        self._index = max(0, min(self._index, len(level) - 1)) if level else -1

        names, walk = [_("Ring")], root
        for index in self._path:
            names.append(ring.bubble_label(walk[index]))
            walk = walk[index]["children"]
        self.crumbs.setText("  ›  ".join(names) if self._path else "")
        self.up.setVisible(bool(self._path))
        self.preview.set_level(
            level, self._index, center_text=names[-1] if self._path else "", back=bool(self._path),
            ghost=len(level) < config.MAX_BUBBLES, style=cfg["ring"]["style"],
        )
        self.style.set_value(cfg["ring"]["style"])
        self.editor.setEnabled(self._index >= 0)
        if self._index >= 0:
            self.editor.show_bubble(
                level[self._index], self._index, len(level), may_be_folder=len(self._path) + 1 < config.MAX_RING_DEPTH
            )
            self.editor.delete.setEnabled(bool(self._path) or len(level) > 1)
        self.size.set_value(cfg["ring"]["size"])
        self.button.set_value(next((key for key in config.GESTURE_BUTTONS if cfg["buttons"][key].get("type") == "ring"), None))
        assigned = any(action.get("type") == "ring" for action in cfg["buttons"].values())
        self.unassigned.setVisible(not assigned)
