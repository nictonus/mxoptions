"""Files that cross with the pointer.

What was copied in the file manager of one computer is sent to the other when the pointer crosses,
kept there in a folder of the program's own, and put on that computer's clipboard, so that pasting
there works as if the files had been copied on the spot. This module lists what to send and
writes what arrives; flow.py carries the bytes."""

import logging
import os
import shutil
import stat
import time

log = logging.getLogger(__name__)

CHUNK = 64 * 1024        # bytes of a file sent in one piece
MAX_ENTRIES = 5000       # files and folders in one go; more is not something to send by accident
MAX_DEPTH = 40
KEEP_FOR = 24 * 3600.0   # s received files are kept waiting to be pasted
STAGING = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "mxoptions", "flow-files")


class Refused(Exception):
    """Not sent or not accepted; the text says why: "too big", "too many", "no space", "bad name", "busy", "off"."""


def _unique(name, taken):
    """``name``, or "name (2)" and so on if it is taken already."""
    if name not in taken:
        return name
    stem, extension = os.path.splitext(name)
    count = 2
    while f"{stem} ({count}){extension}" in taken:
        count += 1
    return f"{stem} ({count}){extension}"


def collect(paths, limit):
    """What to send for the copied ``paths`` (files and folders, taken with everything in them):
    (items, sources). ``items`` goes to the other computer: [{"path": "folder/file", "size": n,
    "mtime": t, "x": bool} or {"path": "folder", "dir": True}], folders before what is in them.
    ``sources`` has the file to read for each item (None for a folder). Symbolic links and anything
    that is not a plain file are left out. Raises Refused over ``limit`` bytes or MAX_ENTRIES."""
    items, sources, total, taken = [], [], 0, set()

    def add_file(path, relative):
        nonlocal total
        try:
            info = os.lstat(path)
        except OSError:
            return
        if not stat.S_ISREG(info.st_mode):
            return
        total += info.st_size
        if total > limit:
            raise Refused("too big")
        items.append({"path": relative, "size": info.st_size, "mtime": int(info.st_mtime), "x": bool(info.st_mode & 0o100)})
        sources.append(path)

    for path in paths:
        path = os.path.abspath(path)
        name = _unique(os.path.basename(path.rstrip("/")) or "file", taken)
        taken.add(name)
        if os.path.isdir(path) and not os.path.islink(path):
            for folder, folders, files in os.walk(path):
                inside = os.path.relpath(folder, path)
                relative = name if inside == "." else name + "/" + inside.replace(os.sep, "/")
                folders[:] = sorted(f for f in folders if not os.path.islink(os.path.join(folder, f)))
                if relative.count("/") >= MAX_DEPTH:
                    folders[:] = []
                items.append({"path": relative, "dir": True})
                sources.append(None)
                for file in sorted(files):
                    add_file(os.path.join(folder, file), relative + "/" + file)
                if len(items) > MAX_ENTRIES:
                    raise Refused("too many")
        else:
            add_file(os.path.realpath(path), name)  # a link to a file, copied as such, means the file
        if len(items) > MAX_ENTRIES:
            raise Refused("too many")
    return items, sources


def total_size(items):
    return sum(item.get("size", 0) for item in items)


def human(size):
    """A number of bytes as people say it: "850 kB", "12.4 MB", "1.2 GB"."""
    for unit, scale in (("GB", 1e9), ("MB", 1e6)):
        if size >= scale:
            return f"{size / scale:.1f} {unit}"
    return f"{max(0, round(size / 1e3))} kB"


def safe_parts(relative):
    """The components of a path received from the other computer, or None if it could lead anywhere
    but below the folder it is meant for."""
    if not isinstance(relative, str) or not relative or len(relative) > 4096:
        return None
    parts = relative.split("/")
    for part in parts:
        if part in ("", ".", "..") or "\0" in part or len(part.encode("utf-8", "surrogatepass")) > 255:
            return None
    return parts if len(parts) <= MAX_DEPTH + 2 else None


def sweep(base=STAGING, keep_for=KEEP_FOR):
    """Remove received files nobody pasted in time."""
    try:
        entries = os.listdir(base)
    except OSError:
        return
    now = time.time()
    for entry in entries:
        path = os.path.join(base, entry)
        try:
            if now - os.lstat(path).st_mtime > keep_for:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


class Receiver:
    """Writes one arriving set of files below ``base``. Used from the link's thread only."""

    def __init__(self, base=STAGING):
        self.base = base
        self._id = None
        self._folder = None
        self._items = []
        self._open = None      # (index, file object, bytes still to come)
        self._written = set()
        self.done = 0          # bytes written of the set that is arriving
        self.total = 0
        self.count = 0

    @property
    def busy(self):
        return self._id is not None

    @property
    def transfer(self):
        """The id of the set that is arriving (None if none is)."""
        return self._id

    def begin(self, transfer, items, limit):
        """Take in the list of what is coming; raises Refused if it will not be accepted."""
        if self.busy:
            raise Refused("busy")
        if limit is None:
            raise Refused("off")
        if not isinstance(items, list) or not items or len(items) > MAX_ENTRIES + 1:
            raise Refused("too many")
        checked, total = [], 0
        for item in items:
            parts = safe_parts(item.get("path")) if isinstance(item, dict) else None
            size = 0 if not isinstance(item, dict) or item.get("dir") else item.get("size")
            if parts is None or not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise Refused("bad name")
            total += size
            checked.append({"parts": parts, "dir": bool(item.get("dir")), "size": size,
                            "mtime": item.get("mtime") if isinstance(item.get("mtime"), int) else None, "x": bool(item.get("x"))})
        if total > limit:
            raise Refused("too big")
        sweep(self.base)
        os.makedirs(self.base, mode=0o700, exist_ok=True)
        if shutil.disk_usage(self.base).free < total + 64 * 1024 * 1024:
            raise Refused("no space")
        folder = os.path.join(self.base, time.strftime("%Y%m%d-%H%M%S-") + "".join(ch for ch in str(transfer) if ch.isalnum())[:16])
        os.makedirs(folder, mode=0o700)
        self._id, self._folder, self._items, self._written = transfer, folder, checked, set()
        self.done, self.total, self.count = 0, total, sum(1 for item in checked if not item["dir"])
        for item in checked:
            if item["dir"]:
                os.makedirs(os.path.join(folder, *item["parts"]), exist_ok=True)

    def chunk(self, transfer, index, data):
        """A piece of file number ``index`` of the list; pieces of one file come in order."""
        if transfer != self._id or not isinstance(index, int) or not 0 <= index < len(self._items) or self._items[index]["dir"]:
            raise Refused("bad name")
        if self._open is None or self._open[0] != index:
            self._finish_file()
            if index in self._written:
                raise Refused("bad name")
            item = self._items[index]
            path = os.path.join(self._folder, *item["parts"])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self._open = [index, open(path, "xb"), item["size"]]
        if len(data) > self._open[2]:
            raise Refused("too big")
        self._open[1].write(data)
        self._open[2] -= len(data)
        self.done += len(data)

    def _finish_file(self):
        if self._open is None:
            return
        index, handle, missing = self._open
        self._open = None
        handle.close()
        if missing:
            raise Refused("too big")  # it ended short of what was announced
        item = self._items[index]
        path = os.path.join(self._folder, *item["parts"])
        if item["x"]:
            os.chmod(path, os.stat(path).st_mode | 0o100)
        if item["mtime"] is not None:
            os.utime(path, (item["mtime"], item["mtime"]))
        self._written.add(index)

    def end(self, transfer):
        """Everything has arrived: returns the paths of what was copied, as they are here now."""
        if transfer != self._id:
            raise Refused("bad name")
        self._finish_file()
        for index, item in enumerate(self._items):
            if not item["dir"] and index not in self._written:
                if item["size"]:
                    raise Refused("too big")
                path = os.path.join(self._folder, *item["parts"])  # an empty file: no piece ever came for it
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "xb").close()
        top = []
        for item in self._items:
            if item["parts"][0] not in top:
                top.append(item["parts"][0])
        paths = [os.path.join(self._folder, name) for name in top]
        count = sum(1 for item in self._items if not item["dir"])
        total = sum(item["size"] for item in self._items)
        self._id = self._folder = None
        self._items = []
        return {"paths": paths, "count": count, "total": total}

    def abort(self):
        """Drop what has arrived so far (the sender gave up, the link broke, or something was wrong)."""
        if self._open is not None:
            try:
                self._open[1].close()
            except OSError:
                pass
            self._open = None
        if self._folder:
            shutil.rmtree(self._folder, ignore_errors=True)
        self._id = self._folder = None
        self._items = []
