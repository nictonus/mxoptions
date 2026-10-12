"""The clipboard, as a program that stays in the background can reach it.

On Wayland an ordinary program sees the clipboard only while one of its windows has the focus.
KDE's own clipboard service does not have that limit, and its Python bindings (KGuiAddons, part
of KDE Frameworks) give all of it: text, pictures, and the files copied in a file manager. Where
those are missing only text is carried, through klipper's D-Bus interface or wl-clipboard."""

import hashlib
import logging
import os
import subprocess

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QMimeData, QUrl
from PySide6.QtGui import QClipboard, QImage

from . import actions

log = logging.getLogger(__name__)

try:
    from KGuiAddons import KSystemClipboard
except ImportError:  # not a KDE system, or its Python bindings are not installed
    KSystemClipboard = None

MODE = QClipboard.Mode.Clipboard
PNG = "image/png"


def rich():
    """Whether pictures and files can be read and put on the clipboard, not just text."""
    return KSystemClipboard is not None


def _system():
    try:
        return KSystemClipboard.instance() if KSystemClipboard is not None else None
    except Exception:  # no display to speak of
        log.exception("clipboard service unavailable")
        return None


def _png(image):
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def read(image_limit=None):
    """What is on the clipboard: {"files": [paths], "image": PNG bytes or None, "text": str}. Copied
    files come with their names as text too; a picture larger than ``image_limit`` bytes is left out."""
    content = {"files": [], "image": None, "text": ""}
    system = _system()
    data = system.mimeData(MODE) if system is not None else None
    if data is None:
        text = actions.klipper("getClipboardContents")
        if text is None:  # not KDE: wl-clipboard, where it is installed
            try:
                done = subprocess.run(["wl-paste", "--no-newline", "--type", "text/plain"], capture_output=True, text=True, timeout=2)
                text = done.stdout if done.returncode == 0 else ""
            except (OSError, subprocess.SubprocessError, ValueError):
                text = ""
        content["text"] = text if isinstance(text, str) else ""
        return content
    formats = data.formats()
    if data.hasUrls():
        content["files"] = [url.toLocalFile() for url in data.urls() if url.isLocalFile() and os.path.exists(url.toLocalFile())]
    if not content["files"]:
        if PNG in formats:
            image = bytes(data.data(PNG))
        elif data.hasImage():
            picture = data.imageData()
            image = _png(picture) if isinstance(picture, QImage) and not picture.isNull() else b""
        else:
            image = b""
        if image and (image_limit is None or len(image) <= image_limit):
            content["image"] = image
    text = data.text()
    if not text:
        for name in ("text/plain;charset=utf-8", "text/plain"):
            if name in formats:
                text = bytes(data.data(name)).decode("utf-8", "replace")
                break
    content["text"] = text
    return content


def write(text=None, image=None, files=None):
    """Put text, a picture (PNG bytes) or a list of files on the clipboard. True if that was possible."""
    system = _system()
    if system is None:
        if text is None:
            return False
        if actions.klipper("setClipboardContents", text) is None:
            try:  # wl-copy stays behind to serve the text: it must not keep a pipe of ours open
                subprocess.run(["wl-copy"], input=text, text=True, timeout=2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                return False
        return True
    data = QMimeData()
    if files:
        data.setUrls([QUrl.fromLocalFile(path) for path in files])
    elif image:
        data.setData(PNG, QByteArray(image))  # as it came: what is read back later is the very same bytes
        picture = QImage.fromData(QByteArray(image), "PNG")
        if not picture.isNull():
            data.setImageData(picture)
    elif text is not None:
        data.setText(text)
    else:
        return False
    system.setMimeData(data, MODE)
    return True


def signature(content=None, text=None, image=None, files=None):
    """A value that is equal for equal clipboard contents, to tell whether the other computer has them already."""
    if content is not None:
        text, image, files = content["text"], content["image"], content["files"]
    if files:
        stamps = []
        for path in files:
            try:
                info = os.stat(path)
                stamps.append((path, info.st_size, int(info.st_mtime)))
            except OSError:
                stamps.append((path, None, None))
        return ("files", tuple(stamps))
    if image:
        return ("image", hashlib.sha256(image).hexdigest())
    return ("text", text or "")
