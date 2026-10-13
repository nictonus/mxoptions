"""The clipboard, as a program that stays in the background can reach it.

On Wayland an ordinary program sees the clipboard only while one of its windows has the focus;
on X11 any program may read it at any time. So there are several ways in, and the first one that
works here is used:

  ksystem       the clipboard service of KDE Frameworks, through its Python bindings (KGuiAddons).
                It speaks the data-control protocol, which KWin and the wlroots compositors have:
                text, pictures, and the files copied in a file manager.
  qt            Qt's own clipboard: all of the same, on X11.
  wl-clipboard  the wl-copy and wl-paste programs: all of the same on Wayland, for where the
                bindings are missing, and on GNOME, which has no data-control protocol.
  klipper       Plasma's clipboard over D-Bus: text only.

MXOPTIONS_CLIPBOARD names one of them to use instead of the choice made here."""

import hashlib
import logging
import os
import shutil
import subprocess
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QMimeData, QUrl
from PySide6.QtGui import QClipboard, QGuiApplication, QImage

from . import environment

log = logging.getLogger(__name__)

try:
    from KGuiAddons import KSystemClipboard
except ImportError:  # KDE Frameworks or its Python bindings are not installed
    KSystemClipboard = None

MODE = QClipboard.Mode.Clipboard
PNG = "image/png"
URIS = "text/uri-list"
TEXT_TYPES = ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING", "STRING", "TEXT")
DATA_CONTROL = ("ext_data_control_manager_v1", "zwlr_data_control_manager_v1")
TOOL_TIMEOUT = 3  # s a clipboard program gets to answer
_chosen = False   # False: not looked for yet


def _empty():
    return {"files": [], "image": None, "text": ""}


def _png(image):
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def _as_png(image):
    """Picture bytes of whatever kind as PNG bytes (empty if they are no picture)."""
    picture = QImage.fromData(QByteArray(image))
    return b"" if picture.isNull() else _png(picture)


def klipper(method, *args):
    """Call Plasma's clipboard service; returns the reply value, or None when it is unavailable."""
    try:
        from PySide6.QtDBus import QDBus, QDBusConnection, QDBusMessage

        message = QDBusMessage.createMethodCall("org.kde.klipper", "/klipper", "org.kde.klipper.klipper", method)
        message.setArguments(list(args))
        reply = QDBusConnection.sessionBus().call(message, QDBus.CallMode.Block, 700)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage:
            return None
        values = reply.arguments()
        return values[0] if values else True
    except Exception:
        log.exception("clipboard call failed")
        return None


def _tool(argv, data=None):
    """Run a clipboard program: its output as bytes, or None if it failed. One that stays behind to hand
    out what it was given (wl-copy, xclip) must not keep a pipe of ours open, so its output is not read."""
    try:
        if data is None:
            done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  timeout=TOOL_TIMEOUT)
        else:
            done = subprocess.run(argv, input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TOOL_TIMEOUT)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return (done.stdout or b"") if done.returncode == 0 else None


def _local_files(uri_list):
    """The files of a text/uri-list that are on this computer and exist."""
    files = []
    for line in uri_list.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = urlsplit(line)
        if parts.scheme == "file" and parts.netloc in ("", "localhost"):
            path = unquote(parts.path)
            if os.path.exists(path):
                files.append(path)
    return files


class _MimeData:
    """A clipboard that hands out and takes Qt's QMimeData. Used from the main thread only."""

    rich = True

    def _get(self):
        raise NotImplementedError

    def _set(self, data):
        raise NotImplementedError

    def read(self, image_limit=None):
        content = _empty()
        data = self._get()
        if data is None:
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
            for name in TEXT_TYPES[:2]:
                if name in formats:
                    text = bytes(data.data(name)).decode("utf-8", "replace")
                    break
        content["text"] = text
        return content

    def write(self, text=None, image=None, files=None):
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
        self._set(data)
        return True


class _KSystem(_MimeData):
    name = "ksystem"

    def __init__(self):
        self._system = KSystemClipboard.instance()
        if self._system is None:
            raise RuntimeError("no clipboard service")

    def _get(self):
        return self._system.mimeData(MODE)

    def _set(self, data):
        self._system.setMimeData(data, MODE)


class _Qt(_MimeData):
    name = "qt"

    def __init__(self):
        self._clipboard = QGuiApplication.clipboard()
        if self._clipboard is None:
            raise RuntimeError("no application")

    def _get(self):
        return self._clipboard.mimeData(MODE)

    def _set(self, data):
        self._clipboard.setMimeData(data, MODE)


class _WlClipboard:
    name = "wl-clipboard"
    rich = True

    def __init__(self):
        if not (shutil.which("wl-copy") and shutil.which("wl-paste")):
            raise RuntimeError("wl-clipboard is not installed")

    @staticmethod
    def _paste(kind):
        return _tool(["wl-paste", "--no-newline", "--type", kind])

    def read(self, image_limit=None):
        content = _empty()
        listed = _tool(["wl-paste", "--list-types"])  # fails when nothing is copied
        kinds = listed.decode("utf-8", "replace").split("\n") if listed else []
        if URIS in kinds:
            content["files"] = _local_files((self._paste(URIS) or b"").decode("utf-8", "replace"))
        if not content["files"]:
            kind = PNG if PNG in kinds else next((name for name in kinds if name.startswith("image/")), None)
            image = self._paste(kind) if kind else b""
            if image and kind != PNG:
                image = _as_png(image)
            if image and (image_limit is None or len(image) <= image_limit):
                content["image"] = image
        kind = next((name for name in TEXT_TYPES if name in kinds), None)
        if kind:
            content["text"] = (self._paste(kind) or b"").decode("utf-8", "replace")
        return content

    def write(self, text=None, image=None, files=None):
        if files:
            listed = "".join(bytes(QUrl.fromLocalFile(path).toEncoded()).decode("ascii") + "\r\n" for path in files)
            return _tool(["wl-copy", "--type", URIS], listed.encode("ascii")) is not None
        if image:
            return _tool(["wl-copy", "--type", PNG], image) is not None
        if text is not None:
            return _tool(["wl-copy", "--type", TEXT_TYPES[0]], text.encode("utf-8")) is not None
        return False


class _Klipper:
    name = "klipper"
    rich = False

    def __init__(self):
        if klipper("getClipboardContents") is None:
            raise RuntimeError("no klipper")

    def read(self, image_limit=None):
        content = _empty()
        text = klipper("getClipboardContents")
        content["text"] = text if isinstance(text, str) else ""
        return content

    def write(self, text=None, image=None, files=None):
        return text is not None and klipper("setClipboardContents", text) is not None


_MAKERS = {"ksystem": _KSystem, "qt": _Qt, "wl-clipboard": _WlClipboard, "klipper": _Klipper}


def _order():
    """The ways in worth trying here, best first."""
    forced = os.environ.get("MXOPTIONS_CLIPBOARD", "").strip().lower()
    if forced in _MAKERS:
        return [forced]
    if forced == "none":
        return []
    if environment.session() != "wayland":
        return ["qt", "klipper"]
    # The KDE service needs the compositor's data-control protocol. Without it (GNOME) it would quietly be Qt's
    # own clipboard, which sees nothing from the background.
    control = any(name in environment.wayland_globals() for name in DATA_CONTROL)
    return (["ksystem"] if KSystemClipboard is not None and control else []) + ["wl-clipboard", "klipper"]


def backend():
    """The way to the clipboard that is used here (None if there is none)."""
    global _chosen
    if _chosen is False:
        _chosen = None
        for name in _order():
            try:
                _chosen = _MAKERS[name]()
                break
            except Exception as error:
                log.debug("clipboard through %s: %s", name, error)
        log.info("clipboard through %s", _chosen.name if _chosen else "nothing")
    return _chosen


def reset():
    """Forget the choice made (for tests)."""
    global _chosen
    _chosen = False


def available():
    """Whether the clipboard can be reached at all from the background."""
    return backend() is not None


def rich():
    """Whether pictures and files can be read and put on the clipboard, not just text."""
    way = backend()
    return way is not None and way.rich


def read(image_limit=None):
    """What is on the clipboard: {"files": [paths], "image": PNG bytes or None, "text": str}. Copied
    files come with their names as text too; a picture larger than ``image_limit`` bytes is left out."""
    way = backend()
    if way is None:
        return _empty()
    try:
        return way.read(image_limit)
    except Exception:
        log.exception("could not read the clipboard")
        return _empty()


def write(text=None, image=None, files=None):
    """Put text, a picture (PNG bytes) or a list of files on the clipboard. True if that was possible."""
    way = backend()
    if way is None or (not way.rich and text is None):
        return False
    try:
        return bool(way.write(text=text, image=image, files=files))
    except Exception:
        log.exception("could not write the clipboard")
        return False


def _text_tools():
    """(read, write) command lines of the clipboard programs installed here, for text, best first."""
    wayland = environment.session() == "wayland"
    if wayland and shutil.which("wl-copy") and shutil.which("wl-paste"):
        yield ["wl-paste", "--no-newline", "--type", "text/plain"], ["wl-copy"]
    if shutil.which("xclip"):
        yield ["xclip", "-selection", "clipboard", "-o"], ["xclip", "-selection", "clipboard", "-i"]
    if shutil.which("xsel"):
        yield ["xsel", "--clipboard", "--output"], ["xsel", "--clipboard", "--input"]


def get_text():
    """The text on the clipboard, from any thread: "" if there is none, None if it cannot be reached that way."""
    text = klipper("getClipboardContents")
    if isinstance(text, str):
        return text
    for paste, _copy in _text_tools():
        found = _tool(paste)
        return "" if found is None else found.decode("utf-8", "replace")  # failing is how they say "nothing is copied"
    return None


def set_text(text):
    """Put text on the clipboard, from any thread. True if that was possible."""
    if klipper("setClipboardContents", text) is not None:
        return True
    return any(_tool(copy, text.encode("utf-8")) is not None for _paste, copy in _text_tools())


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
