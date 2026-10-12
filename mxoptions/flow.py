"""Flow: one mouse across two computers.

Both computers run MX Options and keep an encrypted link over the local network. When the pointer
is pushed against the chosen edge of the screen, the computer that has the mouse sends it to the
other one's Easy-Switch channel, says where the pointer left, and hands over the text that was
copied. The other computer puts its pointer where the first one's left off.

This module is the part that needs no desktop and no mouse: the pairing key, the link, and the
geometry of leaving one screen and entering another. flow_service.py ties it to both."""

import asyncio
import base64
import errno
import hashlib
import hmac
import json
import logging
import secrets
import socket
import ssl
import threading
import time

from . import flow_files, flow_pairing

log = logging.getLogger(__name__)

PORT = 47846                      # TCP: where a computer listens for the link (a setting)
DISCOVERY_PORT = PORT             # UDP, see "finding each other" below
PAIR_PORT = PORT + 1              # TCP, open only while a pairing code is on show
PROTOCOL = 2
EDGES = ("left", "right", "top", "bottom")
OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}
MIN_KEY = 12                      # characters a pairing key needs, not counting separators
MAX_LINE = 4 * 1024 * 1024        # bytes one message may take
MAX_CLIPBOARD = 1024 * 1024       # bytes of text handed over with the pointer; more stays behind
PING_INTERVAL = 4.0               # s of silence after which the other side is asked whether it is there
DEAD_AFTER = 12.0                 # s of silence after which the link counts as lost
RETRY = 3.0                       # s between two attempts to reach the other computer
HANDSHAKE = 5.0
ENTRY_INSET = 24                  # px the pointer is put in from the edge it arrives through
MAX_BLOB = 32 * 1024 * 1024       # bytes of anything sent in one piece (a copied picture, a piece of a file)
STREAM_TIMEOUT = 90.0             # s a file transfer may stand still before it is given up
PROGRESS_INTERVAL = 0.5           # s between two reports of how far a file transfer is
BLOB_TIMEOUT = 60.0               # s such a piece may take to arrive
HERE_INTERVAL = 4.0               # s between two "here I am" calls while the other computer is being looked for
OFFER_INTERVAL = 1.5              # s between two announcements of a pairing code on show
BEACON_AGE = 120                  # s a "here I am" may be old (clocks differ; replaying one gains nothing)
_APP = "mxoptions-flow"
# Both ends prove they hold the pairing key during the TLS handshake, with fresh keys for every
# connection, so nothing recorded on the network helps to read it later.
_CIPHERS = "ECDHE-PSK-CHACHA20-POLY1305:ECDHE-PSK-AES256-CBC-SHA384"
_IDENTITY = "mxoptions-flow"


def supported():
    """Whether this Python can open such a link at all (pre-shared keys arrived in 3.13)."""
    return hasattr(ssl.SSLContext, "set_psk_server_callback")


# -- pairing key ---------------------------------------------------------------


def new_key():
    """A fresh pairing key, in groups that are easy to read out and type: XXXX-XXXX-…"""
    text = base64.b32encode(secrets.token_bytes(15)).decode("ascii")  # 120 bits, 24 characters
    return "-".join(text[i:i + 4] for i in range(0, len(text), 4))


def normalize_key(key):
    """The key without what does not count: case, spaces and separators."""
    return "".join(ch for ch in str(key).upper() if ch.isalnum())


def key_usable(key):
    return len(normalize_key(key)) >= MIN_KEY


def _secret(key):
    return hashlib.sha256(b"mxoptions-flow-1:" + normalize_key(key).encode("utf-8")).digest()


def _context(key, server):
    secret = _secret(key)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
    if not server:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # there are no certificates: the key is what both sides prove
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(_CIPHERS)
    if server:
        context.set_psk_server_callback(lambda _identity: secret, _IDENTITY)
    else:
        context.set_psk_client_callback(lambda _hint: (_IDENTITY, secret))
    return context


# -- addresses -----------------------------------------------------------------


def split_address(text, default_port=PORT):
    """("host", port) from what the user typed: "host", "host:port", "[v6]" or "[v6]:port"."""
    text = text.strip()
    host, port = text, default_port
    if text.startswith("["):
        host, _bracket, rest = text[1:].partition("]")
        if rest.startswith(":"):
            port = rest[1:]
    elif text.count(":") == 1:
        host, _colon, port = text.partition(":")
    try:
        port = int(port)
    except ValueError:
        port = default_port
    return host.strip(), port if 0 < port < 65536 else default_port


def local_address():
    """This computer's address on the local network, as the other one has to be told it ("" if none)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # nothing is sent: this only asks the system which address it would use
        return probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()


# -- which side is which --------------------------------------------------------


def edge_to_adopt(mine, theirs):
    """Two computers side by side have to name opposite edges. ``mine`` and ``theirs`` are each
    (edge, when it was chosen, link id); returns the edge this computer should change to, or None
    if they agree already or it is the other one's turn to give way. The more recent choice wins,
    and where that does not decide it the ids do, so that exactly one of the two gives way."""
    my_edge, my_at, my_id = mine
    their_edge, their_at, their_id = theirs
    if their_edge not in OPPOSITE or my_edge == OPPOSITE[their_edge]:
        return None
    return OPPOSITE[their_edge] if (their_at, their_id) > (my_at, my_id) else None


# -- geometry ------------------------------------------------------------------
# Screens are (x, y, width, height) in the desktop's own coordinates.


def leave_fraction(edge, pos, screen):
    """How far along ``edge`` of ``screen`` the pointer at ``pos`` left: 0 at its top or left end, 1 at the other."""
    x, y, width, height = screen
    if edge in ("left", "right"):
        along = (pos[1] - y) / max(1, height - 1)
    else:
        along = (pos[0] - x) / max(1, width - 1)
    return min(1.0, max(0.0, along))


def bounds(screens):
    """The rectangle around all screens."""
    left = min(s[0] for s in screens)
    top = min(s[1] for s in screens)
    return left, top, max(s[0] + s[2] for s in screens) - left, max(s[1] + s[3] for s in screens) - top


def entry_point(edge, fraction, screens, inset=ENTRY_INSET):
    """Where the pointer comes in through ``edge`` of the desktop made of ``screens``, ``fraction`` of
    the way along the screens that form that edge. A little inside, so that arriving does not at once
    count as pushing against the edge to go back."""
    left, top, width, height = bounds(screens)
    upright = edge in ("left", "right")
    outer = {"left": left, "right": left + width, "top": top, "bottom": top + height}[edge]
    side = {"left": lambda s: s[0], "right": lambda s: s[0] + s[2], "top": lambda s: s[1], "bottom": lambda s: s[1] + s[3]}[edge]
    forming = [s for s in screens if side(s) == outer]
    start = min(s[1] if upright else s[0] for s in forming)
    end = max(s[1] + s[3] if upright else s[0] + s[2] for s in forming)
    along = start + min(1.0, max(0.0, fraction)) * (end - start - 1)

    def distance(s):  # how far ``along`` is from this screen's own stretch of the edge
        low, high = (s[1], s[1] + s[3] - 1) if upright else (s[0], s[0] + s[2] - 1)
        return max(low - along, 0, along - high)

    x, y, w, h = min(forming, key=distance)
    if upright:
        depth = min(inset, w - 1)
        return (x + depth if edge == "left" else x + w - 1 - depth), int(round(min(max(along, y), y + h - 1)))
    depth = min(inset, h - 1)
    return int(round(min(max(along, x), x + w - 1))), (y + depth if edge == "top" else y + h - 1 - depth)


# -- finding each other ---------------------------------------------------------
# Small UDP broadcasts on the local network. A computer showing a pairing code announces that; two
# paired computers that have lost each other (an address changed) call out, signed with their key.


def _here_tag(secret, link_id, port, when):
    return hmac.new(secret, f"here|{link_id}|{port}|{when}".encode(), hashlib.sha256).hexdigest()[:32]


def here_beacon(key, link_id, port, when=None):
    when = int(time.time() if when is None else when)
    return {"app": _APP, "t": "here", "id": link_id, "port": port, "ts": when, "tag": _here_tag(_secret(key), link_id, port, when)}


def offer_beacon(link_id, name, port):
    return {"app": _APP, "t": "pair", "id": link_id, "name": name, "port": port}


def read_beacon(data, key=None, now=None):
    """What a datagram says, or None if it is not one of ours. A "here" call counts only when it is
    signed with ``key`` and recent."""
    try:
        beacon = json.loads(data)
    except ValueError:
        return None
    if not isinstance(beacon, dict) or beacon.get("app") != _APP or not isinstance(beacon.get("id"), str):
        return None
    port = beacon.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        return None
    if beacon.get("t") == "pair":
        return {"t": "pair", "id": beacon["id"], "name": str(beacon.get("name") or "")[:64], "port": port}
    if beacon.get("t") == "here" and key:
        when, tag = beacon.get("ts"), beacon.get("tag")
        now = time.time() if now is None else now
        if (isinstance(when, int) and isinstance(tag, str) and abs(now - when) <= BEACON_AGE
                and hmac.compare_digest(tag, _here_tag(_secret(key), beacon["id"], port, when))):
            return {"t": "here", "id": beacon["id"], "port": port}
    return None


def broadcast_addresses():
    """Where to shout: everybody, and everybody on this computer's own network (assumed to be a /24)."""
    targets = ["255.255.255.255"]
    own = local_address()
    if own.count(".") == 3:
        targets.append(own.rsplit(".", 1)[0] + ".255")
    return targets


class _Datagrams(asyncio.DatagramProtocol):
    def __init__(self, on_datagram):
        self._on_datagram = on_datagram

    def datagram_received(self, data, addr):
        self._on_datagram(data, addr)

    def error_received(self, exc):
        log.debug("flow: datagram error: %s", exc)


# -- the link ------------------------------------------------------------------


class _Connection:
    def __init__(self, reader, writer, dialed):
        self.reader = reader
        self.writer = writer
        self.dialed = dialed  # we called the other computer (it called us otherwise)
        self.dialer = ""      # id of the side that called
        self.heard = time.monotonic()  # when the other side last said anything
        self.port = None               # where the other side listens, as it said in its hello

    def send(self, message, data=None):
        """One message; ``data`` (bytes) rides behind it as it is, announced by "n"."""
        if data is not None:
            message = dict(message, n=len(data))
        self.writer.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
        if data:
            self.writer.write(data)

    async def read(self):
        line = await self.reader.readline()  # ValueError if it is longer than MAX_LINE
        if not line:
            raise ConnectionError("closed by the other side")
        self.heard = time.monotonic()
        message = json.loads(line)
        if not isinstance(message, dict):
            raise ValueError("not a message")
        return message

    async def read_data(self, message):
        """The bytes riding behind ``message`` (b"" if it announces none)."""
        size = message.get("n")
        if size is None:
            return b""
        if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= MAX_BLOB:
            raise ValueError("bad size")
        data = await asyncio.wait_for(self.reader.readexactly(size), BLOB_TIMEOUT)
        self.heard = time.monotonic()
        return data

    def close(self):
        """Drop the connection at once: nothing still waiting to be sent matters to anyone by now."""
        try:
            self.writer.transport.abort()
        except Exception:  # the transport may already be gone
            pass


_LOST = (OSError, ssl.SSLError, ValueError, EOFError, asyncio.TimeoutError)


async def _line(reader, timeout=10.0):
    """One short JSON message of the pairing conversation."""
    line = await asyncio.wait_for(reader.readline(), timeout)
    message = json.loads(line) if line else None
    if not isinstance(message, dict):
        raise ValueError("not a message")
    return message


def _say(writer, message):
    writer.write(json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n")


class Link:
    """The connection to the other computer, kept up from a thread of its own.

    Both computers listen; each calls the other at the address it was given or heard a "here I
    am" from, and keeps calling until it answers. ``on_event(kind, payload)`` is called from the
    link's thread:

        "up"            the other computer's status ({"name", "channel", "present", ...}): the link is there
        "status"        its status changed
        "message"       anything else it sent, as a dict with "t" naming what it is ("data": bytes that came with it)
        "down"          the link is gone
        "problem"       text: why this computer cannot listen ("" once it can)
        "files-coming"  files are on their way here: {"count", "total"}
        "files-progress" how far a transfer is: {"done", "total", "count", "sending": this side sends}
        "files"         they have arrived: {"paths", "count", "total"};  "files-declined": why they did not
        "files-sent"    files went out: {"count", "total"};  "files-refused": why they did not
        "offer"         a computer nearby shows a pairing code: {"id", "name", "address", "port"}
        "paired"        pairing worked: {"key", "address", "name", "port", "host": this side showed the code}
        "pair-failed"   it did not, on the side that was given the code: "code", "closed" or "unreachable"
        "pairing"       on the side showing the code: wrong tries left (0: the code is withdrawn), None: it ran out"""

    def __init__(self, on_event, staging=flow_files.STAGING):
        self._on_event = on_event
        self.id = secrets.token_hex(8)
        self._settings = None  # (key, port, peer) while switched on
        self._status = {}
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="flow", daemon=True)
        self._server = None
        self._dialer = None
        self._conn = None
        self._closing = False
        self._found = None          # (address, port) a signed "here I am" came from
        self._udp = None
        self._udp_wanted = False
        self._caller = None         # the task that sends "here I am" and pairing offers
        self._watching = False      # report pairing offers heard
        self._pairing = None        # while a code is on show: {"code", "info", "left", "until", "server"}
        self._receiver = flow_files.Receiver(staging)
        self._receive_limit = None  # bytes of files accepted in one go (None: none)
        self._outgoing = None       # files offered and not yet sent: {"id", "items", "sources"}
        self._sending = None        # the task that sends them, on a connection of its own
        self._arriving = None       # the connection files are arriving on

    # -- thread-safe API ---------------------------------------------------------

    def start(self):
        self._thread.start()

    def stop(self):
        if self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._shutdown)
            self._thread.join(timeout=3.0)

    def configure(self, key=None, port=PORT, peer=""):
        """Switch the link on with these settings, or off (no key)."""
        settings = (key, int(port), peer.strip()) if key and key_usable(key) and supported() else None
        self._call(self._apply, settings)

    def set_status(self, status):
        """What the other computer should know about this one; sent now and whenever a link comes up."""
        self._call(self._set_status, dict(status))

    def send(self, message, data=None):
        self._call(self._send, dict(message), data)

    def accept_files(self, limit):
        """Take files of up to ``limit`` bytes in one go from the other computer (None: none at all)."""
        self._call(setattr, self, "_receive_limit", limit)

    def offer_files(self, items, sources):
        """Send the files listed by flow_files.collect(), if the other computer will have them."""
        self._call(self._offer_files, list(items), list(sources))

    def cancel_files(self):
        """Give up the file transfer in progress, whichever way it is going."""
        self._call(self._cancel_files)

    def watch_offers(self, wanted):
        """Report computers nearby that show a pairing code ("offer" events)."""
        self._call(self._watch_offers, bool(wanted))

    def open_pairing(self, code, info):
        """Show a pairing code: accept one computer that knows ``code``. ``info`` is {"name", "port"} of this one."""
        self._call(self._open_pairing, code, dict(info))

    def close_pairing(self):
        self._call(self._close_pairing)

    def pair_with(self, address, port, code, info):
        """Pair with the computer at ``address`` that shows ``code``."""
        self._call(lambda: self._loop.create_task(self._pair_client(address, port, code, dict(info))))

    # -- on the link's thread -------------------------------------------------------

    def _call(self, function, *args):
        try:
            self._loop.call_soon_threadsafe(function, *args)
        except RuntimeError:  # stopped
            pass

    def _run(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            tasks = asyncio.all_tasks(self._loop)
            for task in tasks:
                task.cancel()
            self._loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            self._loop.run_until_complete(asyncio.sleep(0.05))  # let the dropped connections finish closing
            self._loop.close()

    def _emit(self, kind, payload=None):
        if self._closing:
            return
        try:
            self._on_event(kind, payload)
        except Exception:
            log.exception("flow: handling %s failed", kind)

    def _shutdown(self):
        self._closing = True
        self._cancel_files()
        self._teardown()
        self._close_pairing()
        self._watching = False
        self._sync_udp()
        self._loop.stop()

    def _teardown(self):
        if self._dialer is not None:
            self._dialer.cancel()
            self._dialer = None
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._conn is not None:
            self._conn.close()

    def _apply(self, settings):
        if settings == self._settings:
            return
        self._teardown()
        self._settings = settings
        self._found = None
        self._sync_udp()
        if settings is None:
            return
        key, port, peer = settings
        self._loop.create_task(self._listen(key, port))
        self._dialer = self._loop.create_task(self._dial(key, port, peer))

    def _set_status(self, status):
        if status != self._status:
            self._status = status
            self._send(dict(status, t="status"))

    def _send(self, message, data=None):
        if self._conn is not None:
            try:
                self._conn.send(message, data)
            except _LOST as e:
                log.debug("flow: could not send: %s", e)

    async def _listen(self, key, port):
        settings = self._settings
        try:
            server = await asyncio.start_server(
                lambda reader, writer: self._session(reader, writer, dialed=False), host=None, port=port,
                ssl=_context(key, server=True), ssl_handshake_timeout=HANDSHAKE, limit=MAX_LINE)
        except (OSError, ssl.SSLError) as e:
            log.warning("flow: cannot listen on port %d: %s", port, e)
            self._emit("problem", str(e))
            return
        if settings is not self._settings:  # reconfigured while the port was being opened
            server.close()
            return
        self._server = server
        self._emit("problem", "")
        log.info("flow: listening on port %d", port)

    async def _dial(self, key, port, peer):
        given = split_address(peer, port) if peer else None
        context = _context(key, server=False)
        while True:
            for host, peer_port in [target for target in (given, self._found) if target and target[0]]:
                if self._conn is not None:
                    break
                try:
                    reader, writer = await asyncio.wait_for(asyncio.open_connection(
                        host, peer_port, ssl=context, ssl_handshake_timeout=HANDSHAKE, limit=MAX_LINE), HANDSHAKE + 3.0)
                except _LOST as e:
                    log.debug("flow: %s:%d not reachable: %s", host, peer_port, e)
                else:
                    await self._session(reader, writer, dialed=True)
            await asyncio.sleep(RETRY)

    def _adopt(self, conn):
        """Make ``conn`` the link in use, unless it is the loser of two calls that crossed."""
        current = self._conn
        if current is not None and current.dialed != conn.dialed and current.dialer < conn.dialer:
            return False  # each side called the other at once: both keep the call of the smaller id
        self._conn = conn
        if current is not None:
            current.close()
            conn.send(dict(self._status, t="status"))  # what was said on the old call since this one began
        return True

    async def _session(self, reader, writer, dialed):
        conn = _Connection(reader, writer, dialed)
        try:
            # The side that calls speaks first: it is either the link, or a file transfer on a connection of its own.
            if dialed:
                conn.send(self._hello())
            hello = await asyncio.wait_for(conn.read(), HANDSHAKE)
            if hello.get("v") != PROTOCOL or hello.get("id") == self.id:
                return  # another version of the protocol, or this computer reached itself
            if not dialed and hello.get("t") == "files-stream":
                await self._receive_files(conn, hello)
                return
            if hello.get("t") != "hello":
                return
            if not dialed:
                conn.send(self._hello())
            conn.dialer = self.id if dialed else str(hello.get("id"))
            port = hello.get("port")
            conn.port = port if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536 else None
            if not self._adopt(conn):
                return
            log.info("flow: linked with %s", hello.get("name") or "the other computer")
            self._emit("up", hello)
            while True:
                try:
                    message = await asyncio.wait_for(conn.read(), PING_INTERVAL)
                except asyncio.TimeoutError:
                    if time.monotonic() - conn.heard > DEAD_AFTER:
                        log.info("flow: the other computer went silent")
                        return
                    conn.send({"t": "ping"})
                    continue
                data = await conn.read_data(message)  # never cut short: what follows would make no sense
                kind = message.get("t")
                if kind == "ping":
                    conn.send({"t": "pong"})
                elif kind == "status":
                    self._emit("status", message)
                elif kind == "files":
                    self._offered(conn, message)
                elif kind in ("files-ok", "files-no"):
                    self._answered(conn, kind, message)
                elif kind != "pong":
                    if data:
                        message["data"] = data
                    self._emit("message", message)
        except _LOST as e:
            log.debug("flow: link lost: %s", e)
        finally:
            conn.close()
            if self._conn is conn:
                self._conn = None
                if self._receiver.busy and self._arriving is None:  # promised files that had not begun to arrive
                    self._receiver.abort()
                    self._emit("files-declined", "failed")
                self._outgoing = None
                log.info("flow: link down")
                self._emit("down")

    def _hello(self):
        settings = self._settings
        return dict(self._status, t="hello", v=PROTOCOL, id=self.id, port=settings[1] if settings else None)

    # -- files ------------------------------------------------------------------------
    # They are offered and accepted over the link, and then travel on a connection of their own, so
    # that a large file does not keep anything else waiting: the pointer crossing, the clipboard,
    # the link's own "are you there". A transfer under way outlives a link that drops.

    def _offered(self, conn, message):
        """The other computer wants to send files: say whether they will be taken."""
        transfer, items = message.get("id"), message.get("items")
        try:
            self._receiver.begin(transfer, items, self._receive_limit)
        except flow_files.Refused as e:
            log.info("flow: files not taken: %s", e)
            conn.send({"t": "files-no", "id": transfer, "why": str(e)})
            if str(e) not in ("off", "busy"):
                self._emit("files-declined", str(e))  # the user is at this computer now: this is where to say so
            return
        except OSError as e:
            log.warning("flow: could not make room for the files: %s", e)
            self._receiver.abort()
            conn.send({"t": "files-no", "id": transfer, "why": "no space"})
            self._emit("files-declined", "no space")
            return
        conn.send({"t": "files-ok", "id": transfer})
        self._emit("files-coming", {"count": self._receiver.count, "total": self._receiver.total})

    async def _receive_files(self, conn, hello):
        """The connection the promised files arrive on."""
        transfer = hello.get("transfer")
        if not self._receiver.busy or transfer != self._receiver.transfer or self._arriving is not None:
            return
        self._arriving = conn
        conn.cancelled = False  # set when this side gives the transfer up itself
        reported = 0.0
        why = "failed"
        try:
            while True:
                message = await asyncio.wait_for(conn.read(), STREAM_TIMEOUT)
                data = await conn.read_data(message)
                kind = message.get("t")
                if kind == "chunk":
                    self._receiver.chunk(transfer, message.get("i"), data)
                    now = time.monotonic()
                    if now - reported >= PROGRESS_INTERVAL:
                        reported = now
                        self._emit("files-progress", {"done": self._receiver.done, "total": self._receiver.total,
                                                      "count": self._receiver.count, "sending": False})
                elif kind == "files-end":
                    arrived = self._receiver.end(transfer)
                    conn.send({"t": "files-got"})
                    await conn.writer.drain()
                    log.info("flow: %d file(s) arrived, %d bytes", arrived["count"], arrived["total"])
                    self._emit("files", arrived)
                    return
                else:  # the sender gave up
                    why = "cancelled"
                    break
        except flow_files.Refused as e:
            log.info("flow: files not taken: %s", e)
        except _LOST as e:  # the connection, or the disk
            log.info("flow: the files did not arrive: %s", e)
            if getattr(e, "errno", None) == errno.ENOSPC:
                why = "no space"
        finally:
            if self._arriving is conn:
                self._arriving = None
        if self._receiver.transfer == transfer:
            self._receiver.abort()
            self._emit("files-declined", "cancelled" if conn.cancelled else why)

    def _offer_files(self, items, sources):
        if self._conn is None or self._outgoing is not None or self._sending is not None:
            return
        self._outgoing = {"id": secrets.token_hex(6), "items": items, "sources": sources}
        self._conn.send({"t": "files", "id": self._outgoing["id"], "items": items})

    def _answered(self, conn, kind, message):
        outgoing = self._outgoing
        if outgoing is None or message.get("id") != outgoing["id"]:
            return
        self._outgoing = None
        if kind == "files-no":
            log.info("flow: the other computer will not take the files: %s", message.get("why"))
            self._emit("files-refused", str(message.get("why") or ""))
            return
        peer = conn.writer.get_extra_info("peername")
        if self._settings is None or not peer or not conn.port:
            self._emit("files-refused", "failed")
            return
        self._sending = self._loop.create_task(self._send_files(peer[0], conn.port, self._settings[0], outgoing))

    async def _send_files(self, address, port, key, outgoing):
        transfer, total = outgoing["id"], flow_files.total_size(outgoing["items"])
        count = sum(1 for source in outgoing["sources"] if source is not None)
        writer, why = None, "failed"
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(
                address, port, ssl=_context(key, server=False), ssl_handshake_timeout=HANDSHAKE, limit=MAX_LINE), HANDSHAKE + 3.0)
            conn = _Connection(reader, writer, True)
            conn.send({"t": "files-stream", "v": PROTOCOL, "id": self.id, "transfer": transfer})
            done, reported = 0, 0.0
            for index, (item, source) in enumerate(zip(outgoing["items"], outgoing["sources"])):
                if source is None:
                    continue
                left = item["size"]
                with open(source, "rb") as handle:
                    while left:
                        data = handle.read(min(flow_files.CHUNK, left))
                        if not data:
                            raise OSError(f"{source} became shorter")
                        left -= len(data)
                        done += len(data)
                        conn.send({"t": "chunk", "i": index}, data)
                        await asyncio.wait_for(writer.drain(), STREAM_TIMEOUT)  # no faster than the network takes it
                        now = time.monotonic()
                        if now - reported >= PROGRESS_INTERVAL:
                            reported = now
                            self._emit("files-progress", {"done": done, "total": total, "count": count, "sending": True})
            conn.send({"t": "files-end"})
            answer = await asyncio.wait_for(conn.read(), STREAM_TIMEOUT)  # it says so when everything is in place
            if answer.get("t") != "files-got":
                raise OSError("the other computer did not confirm the files")
            log.info("flow: sent %d file(s)", count)
            self._emit("files-sent", {"count": count, "total": total})
            writer.close()
            return
        except asyncio.CancelledError:
            why = "cancelled"
            if writer is not None:
                try:
                    writer.write(b'{"t":"files-abort"}\n')
                except Exception:
                    pass
        except _LOST as e:
            log.warning("flow: sending the files failed: %s", e)
        finally:
            self._sending = None
        if writer is not None:
            writer.close()
        self._emit("files-refused", why)

    def _cancel_files(self):
        if self._sending is not None:
            self._sending.cancel()
        if self._arriving is not None:
            self._arriving.cancelled = True
            self._arriving.close()  # the loop reading from it ends, and what arrived so far is dropped
        elif self._receiver.busy:
            self._receiver.abort()
            self._emit("files-declined", "cancelled")

    # -- finding each other, and pairing -----------------------------------------------------

    def _sync_udp(self):
        """Have the UDP port open exactly while something needs it."""
        wanted = not self._closing and (self._settings is not None or self._watching or self._pairing is not None)
        if wanted and not self._udp_wanted:
            self._udp_wanted = True
            self._loop.create_task(self._open_udp())
        elif not wanted and self._udp_wanted:
            self._udp_wanted = False
            if self._caller is not None:
                self._caller.cancel()
                self._caller = None
            if self._udp is not None:
                self._udp.close()
                self._udp = None

    async def _open_udp(self):
        try:
            transport, _protocol = await self._loop.create_datagram_endpoint(
                lambda: _Datagrams(self._datagram), local_addr=("0.0.0.0", DISCOVERY_PORT), allow_broadcast=True,
                reuse_port=True)  # a second copy of the program (a test, a trial run) may listen as well
        except OSError as e:
            log.warning("flow: cannot look for other computers (UDP port %d): %s", DISCOVERY_PORT, e)
            return
        if not self._udp_wanted:
            transport.close()
            return
        self._udp = transport
        self._caller = self._loop.create_task(self._call_out())

    async def _call_out(self):
        """Announce a pairing code on show; call for the paired computer while the link is down."""
        last_here = 0.0
        while True:
            beacons = []
            if self._pairing is not None and self._pairing["server"] is not None:  # not before it can be answered
                beacons.append(offer_beacon(self.id, self._pairing["info"].get("name", ""), self._pairing["port"]))
            now = time.monotonic()
            if self._settings is not None and self._conn is None and now - last_here >= HERE_INTERVAL:
                last_here = now
                beacons.append(here_beacon(self._settings[0], self.id, self._settings[1]))
            for beacon in beacons:
                data = json.dumps(beacon, separators=(",", ":")).encode("utf-8")
                for address in broadcast_addresses():
                    try:
                        self._udp.sendto(data, (address, DISCOVERY_PORT))
                    except OSError as e:
                        log.debug("flow: could not call out to %s: %s", address, e)
            await asyncio.sleep(OFFER_INTERVAL)

    def _datagram(self, data, addr):
        beacon = read_beacon(data, self._settings[0] if self._settings else None)
        if beacon is None or beacon["id"] == self.id:
            return
        if beacon["t"] == "pair":
            if self._watching:
                self._emit("offer", {"id": beacon["id"], "name": beacon["name"], "address": addr[0], "port": beacon["port"]})
        elif self._conn is None and self.id < beacon["id"]:
            # Both hear each other's call; only one of the two acts on it, or their calls would cross every time.
            self._found = (addr[0], beacon["port"])

    def _watch_offers(self, wanted):
        self._watching = wanted
        self._sync_udp()

    def _open_pairing(self, code, info):
        self._close_pairing()
        self._pairing = {"code": code, "info": info, "left": flow_pairing.ATTEMPTS, "port": PAIR_PORT,
                         "until": time.monotonic() + flow_pairing.OPEN_FOR, "server": None}
        self._loop.create_task(self._pairing_server(self._pairing))
        self._sync_udp()

    def _close_pairing(self):
        pairing, self._pairing = self._pairing, None
        if pairing is not None and pairing["server"] is not None:
            pairing["server"].close()
        self._sync_udp()

    async def _pairing_server(self, pairing):
        try:
            server = await asyncio.start_server(lambda reader, writer: self._pair_host(pairing, reader, writer),
                                                host=None, port=pairing["port"], limit=16 * 1024)
        except OSError as e:
            log.warning("flow: cannot offer pairing on port %d: %s", pairing["port"], e)
            if self._pairing is pairing:
                self._close_pairing()
                self._emit("pairing", None)
            return
        if self._pairing is not pairing:
            server.close()
            return
        pairing["server"] = server
        await asyncio.sleep(max(0.0, pairing["until"] - time.monotonic()))
        if self._pairing is pairing:  # nobody came in time
            self._close_pairing()
            self._emit("pairing", None)

    async def _pair_host(self, pairing, reader, writer):
        """One computer trying the code that is on show here."""
        try:
            hello = await _line(reader)
            if self._pairing is not pairing or hello.get("t") != "pair" or hello.get("v") != 1:
                _say(writer, {"error": "closed"})
                return
            host = flow_pairing.Host(pairing["code"])
            _say(writer, {"salt": host.salt.hex(), "B": format(host.public, "x")})
            answer = await _line(reader)
            their_info = answer.get("info") if isinstance(answer.get("info"), dict) else {}
            try:
                shared = host.accept(int(str(hello.get("A")), 16), bytes.fromhex(str(answer.get("M1"))),
                                     flow_pairing.info_bytes(their_info))
            except (flow_pairing.Mismatch, ValueError):
                if self._pairing is pairing:
                    pairing["left"] -= 1
                    left = pairing["left"]
                    if left <= 0:
                        self._close_pairing()
                    self._emit("pairing", max(0, left))
                _say(writer, {"error": "code"})
                return
            if self._pairing is not pairing:
                _say(writer, {"error": "closed"})
                return
            _say(writer, {"M2": host.proof(flow_pairing.info_bytes(pairing["info"])).hex(), "info": pairing["info"]})
            await writer.drain()
            self._close_pairing()  # one code, one computer
            address = writer.get_extra_info("peername")[0]
            log.info("flow: paired with %s", their_info.get("name") or address)
            self._emit("paired", {"key": flow_pairing.pairing_key(shared), "address": address, "host": True,
                                  "name": str(their_info.get("name") or "")[:64], "port": their_info.get("port")})
        except _LOST as e:
            log.debug("flow: pairing attempt dropped: %s", e)
        finally:
            writer.close()

    async def _pair_client(self, address, port, code, info):
        writer = None
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(address, port, limit=16 * 1024), 6.0)
            client = flow_pairing.Client(code)
            _say(writer, {"t": "pair", "v": 1, "A": format(client.public, "x")})
            challenge = await _line(reader)
            if "error" in challenge:
                self._emit("pair-failed", "closed")
                return
            proof = client.answer(bytes.fromhex(str(challenge.get("salt"))), int(str(challenge.get("B")), 16),
                                  flow_pairing.info_bytes(info))
            _say(writer, {"M1": proof.hex(), "info": info})
            reply = await _line(reader)
            if "error" in reply:
                self._emit("pair-failed", "code" if reply["error"] == "code" else "closed")
                return
            their_info = reply.get("info") if isinstance(reply.get("info"), dict) else {}
            shared = client.confirm(bytes.fromhex(str(reply.get("M2"))), flow_pairing.info_bytes(their_info))
            log.info("flow: paired with %s", their_info.get("name") or address)
            self._emit("paired", {"key": flow_pairing.pairing_key(shared), "address": address, "host": False,
                                  "name": str(their_info.get("name") or "")[:64], "port": their_info.get("port")})
        except flow_pairing.Mismatch:
            self._emit("pair-failed", "code")
        except _LOST as e:
            log.info("flow: pairing with %s failed: %s", address, e)
            self._emit("pair-failed", "unreachable")
        finally:
            if writer is not None:
                writer.close()
