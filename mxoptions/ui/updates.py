"""The app's own update check on the Qt side: one at a time, off the GUI thread, on a daily timer."""

import logging
import threading
import time
import weakref

from PySide6.QtCore import QObject, QTimer, Signal

from .. import __version__, updates

log = logging.getLogger(__name__)
FIRST_DELAY = 60.0  # s after start before the first automatic check, when one is due


def status(release, error=""):
    """{"state", "version", "page", "error"} for the card and the tray, from the last release found (or None) and the
    last error ("none": GitHub has no release). Always against the version running now."""
    if error and release is None:
        return {"state": "none" if error == "none" else "failed", "version": "", "page": "", "error": error}
    if release is None:
        return {"state": "unknown", "version": "", "page": "", "error": ""}
    newer = updates.is_newer(release["version"], __version__)
    return {"state": "available" if newer else "current", "version": release["version"], "page": release["page"],
            "error": error}


class Checker(QObject):
    """Asks GitHub for the latest release: on demand (check_now) and, once start() was called and while the setting is
    on, once a day. Made by the program at start (started) or by a page on its own (never checks by itself)."""

    result = Signal(object)    # status(): after each check, and "checking" when one starts
    _done = Signal(object, str)

    def __init__(self, store, parent=None, clock=time.time):
        super().__init__(parent)
        self._store = weakref.ref(store)  # the store keeps this checker (store.updates): no cycle back to it
        self._clock = clock
        self._running = False
        self._started = False
        self.state = updates.load_state(clock())
        self._error = ""
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self._due)
        self._done.connect(self._finished)

    def _on(self):
        store = self._store()
        return store is not None and bool(store.cfg["app"].get("update_check", True))

    def start(self):
        """Check once a day from now on, while the setting is on."""
        if self._started:
            return
        self._started = True
        store = self._store()
        if store is not None:
            store.changed.connect(self._schedule)
        self._schedule()

    def last(self):
        if self._running:
            return {"state": "checking", "version": "", "page": "", "error": ""}
        return status(self.state["release"], self._error)

    def _schedule(self):
        if not self._started or not self._on():
            self._timer.stop()
            return
        checked = self.state["checked_at"]
        wait = FIRST_DELAY if checked is None else max(FIRST_DELAY, checked + updates.PERIOD - self._clock())
        if not self._timer.isActive() or self._timer.remainingTime() > wait * 1000:
            self._timer.start(int(wait * 1000))

    def _due(self):
        if self._on():
            self.check_now()
        else:
            self._timer.stop()

    def check_now(self):
        if self._running:
            return
        self._running = True
        self.result.emit(self.last())

        done = self._done  # the signal only: the worker keeps no reference to the checker itself

        def job():
            try:
                release, error = updates.fetch_latest(), ""
            except updates.NoRelease:
                release, error = None, "none"
            except Exception as e:  # anything at all (a cut-off reply, too): the check must end
                log.info("update check failed: %s", e)
                release, error = None, str(e) or type(e).__name__
            try:
                done.emit(release, error)  # queued: back on the GUI thread
            except RuntimeError:
                pass  # the checker is gone

        threading.Thread(target=job, name="update-check", daemon=True).start()

    def _finished(self, release, error):
        self._running = False
        self.state["checked_at"] = self._clock()
        if release is not None:
            self.state["release"] = release
        elif error == "none":
            self.state["release"] = None
        self._error = error if release is None else ""
        updates.save_state(self.state)
        self._timer.stop()
        self._schedule()
        self.result.emit(self.last())

    def mark_notified(self, version):
        """The tray said ``version`` is out: it is not said again."""
        self.state["notified"] = version
        updates.save_state(self.state)


def checker_for(store):
    """The store's checker, made on first use (the program makes it at start; a page alone in a test makes its own)."""
    if getattr(store, "updates", None) is None:
        store.updates = Checker(store, parent=store)  # a Qt child of the store: deleted with it, on its thread
    return store.updates
