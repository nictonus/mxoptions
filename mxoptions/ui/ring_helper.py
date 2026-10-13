"""The Actions Ring overlay as a program of its own, for Wayland desktops without layers above the windows.

It is started by ring.RingProxy with Qt told to talk to the X server of the session, and is the
same RingOverlay the main program would have. What the main program would call on the overlay
arrives here as lines of JSON on the standard input ({"do": "handle", "event": "down"}), and the
overlay's signals leave as lines on the standard output ({"event": "triggered", "action": {...}}).
It ends when its input ends, which is when the main program does."""

import json
import logging
import os
import sys


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    from PySide6.QtCore import QSocketNotifier
    from PySide6.QtWidgets import QApplication

    from .. import APP_ID, config, i18n
    from . import ring, theme

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_ID + "-ring")
    app.setQuitOnLastWindowClosed(False)
    theme.ensure_icon_theme()
    theme.set_theme()
    overlay = ring.RingOverlay(app, kind=ring.XWAYLAND)
    out = sys.stdout.buffer

    def tell(event, **more):
        out.write(json.dumps(dict(more, event=event)).encode("utf-8") + b"\n")
        out.flush()

    overlay.triggered.connect(lambda action: tell("triggered", action=action))
    overlay.cue.connect(lambda wave: tell("cue", wave=int(wave)))
    overlay.opened.connect(lambda on: tell("opened", on=bool(on)))

    def told(message):
        do = message.get("do")
        if do == "look":
            name, accent, language = message["look"]
            i18n.setup(language)
            theme.set_theme(name, accent)
            ring.clear_icon_cache()
            overlay.refresh()
        elif do == "configure":
            overlay.configure(dict(message["ring"], bubbles=config.clean_bubbles(message["ring"].get("bubbles"))))
        elif do == "handle":
            overlay.handle(message.get("event"))
        elif do == "turn":
            overlay.turn(message.get("clicks", 0))
        elif do == "open":
            bubbles = message.get("bubbles")
            overlay.open(None if bubbles is None else config.clean_bubbles(bubbles))
        elif do == "close":
            overlay.close()

    pending = [b""]
    os.set_blocking(0, False)

    def read():
        try:
            data = os.read(0, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:  # the main program is gone
            notifier.setEnabled(False)
            overlay.close()
            app.quit()
            return
        *lines, pending[0] = (pending[0] + data).split(b"\n")
        for line in lines:
            try:
                message = json.loads(line)
                if isinstance(message, dict):
                    told(message)
            except (ValueError, KeyError, TypeError) as error:
                logging.getLogger(__name__).warning("ring: a line that could not be followed: %s", error)

    notifier = QSocketNotifier(0, QSocketNotifier.Type.Read, app)
    notifier.activated.connect(lambda *_args: read())
    tell("ready", available=overlay.available)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
