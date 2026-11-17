"""Whether a newer MX Options has been released, and how to update this copy by hand.

Only stable releases of this fork count (GitHub's "latest", never a pre-release or a CI build). Nothing here
downloads or installs anything, or runs a program: it reads one small reply from GitHub and the files of this
installation. The last answer is kept in the data folder (update.json), read and written with care: it may have
been left there by anything."""

import json
import logging
import math
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__, paths
from .i18n import _

log = logging.getLogger(__name__)

REPO = "squarrelmingji/mxoptions"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"  # stable only: GitHub leaves pre-releases out
ALLOWED_HOSTS = {"api.github.com", "github.com"}
MAX_REPLY = 1 << 20      # bytes of the release reply read at most
MAX_STATE = 64 * 1024    # bytes of update.json read at most
STATE_FILE = "update.json"
PERIOD = 24 * 3600.0     # s between two automatic checks

_VERSION = re.compile(r"v?(\d{1,6})\.(\d{1,6})\.(\d{1,6})(?:(a|b|rc)(\d{1,6})|\.post(\d{1,6}))?\Z")
# the one shape of a release page offered or opened: no other path, no query, nothing encoded
_PAGE = re.compile(r"/squarrelmingji/mxoptions/releases/tag/v\d{1,6}\.\d{1,6}\.\d{1,6}((a|b|rc)\d{1,6}|\.post\d{1,6})?\Z")
_SAFE_PATH = re.compile(r"/[A-Za-z0-9._+/-]+\Z")  # a path that needs no quoting for the shell nor make


# -- versions ----------------------------------------------------------------------------------------------------

def parse_version(text):
    """A key that sorts versions: "0.10.0", "v0.10.0", "0.10.0.post2", "0.10.1b3", "0.10.1rc1"; None for anything
    else. A pre-release comes before its release, a post-release after it."""
    m = _VERSION.match(text) if isinstance(text, str) else None
    if not m:
        return None
    major, minor, micro, pre, pre_n, post = m.groups()
    if pre:
        stage = (-1, {"a": 0, "b": 1, "rc": 2}[pre], int(pre_n))
    elif post is not None:
        stage = (1, int(post), 0)
    else:
        stage = (0, 0, 0)
    return int(major), int(minor), int(micro), stage


def is_newer(candidate, current):
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


def debian_version(version):
    """The version of the .deb of ``version`` (the rule of tools/make_deb.py, kept the same by tests)."""
    return re.sub(r"(\d)(a|b|rc)(\d+)$", r"\1~\2\3", version) + "-1"


# -- what GitHub says ------------------------------------------------------------------------------------------------

def _allowed(url):
    try:
        parts = urllib.parse.urlsplit(url)
        return (parts.scheme == "https" and parts.hostname in ALLOWED_HOSTS and parts.username is None
                and parts.password is None and parts.port is None)
    except (ValueError, TypeError, AttributeError):  # a port that is no number, and the like
        return False


def release_page(url):
    """``url`` if it is exactly a release page of this fork on github.com (https://github.com/squarrelmingji/
    mxoptions/releases/tag/v<version>), else None."""
    if not isinstance(url, str) or len(url) > 200 or not _allowed(url):
        return None
    parts = urllib.parse.urlsplit(url)
    if parts.hostname != "github.com" or parts.query or parts.fragment or not _PAGE.match(parts.path):
        return None
    return url


def tag_page(tag):
    return f"https://github.com/{REPO}/releases/tag/{urllib.parse.quote(tag)}"


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to an allowed https host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed(newurl):
            raise urllib.error.URLError(f"redirect refused to {urllib.parse.urlsplit(newurl).hostname}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NoRelease(LookupError):
    """GitHub has no stable release of the fork yet."""


def fetch_latest(timeout=15.0):
    """{"version", "tag", "page"} of the latest stable release. NoRelease when there is none, OSError when GitHub
    cannot be reached, ValueError for a reply that is not what is expected."""
    if not _allowed(LATEST_URL):
        raise ValueError("update address not allowed")
    request = urllib.request.Request(LATEST_URL, headers={
        "User-Agent": f"mxoptions/{__version__}", "Accept": "application/vnd.github+json"})
    opener = urllib.request.build_opener(_Redirects)
    try:
        with opener.open(request, timeout=timeout) as reply:
            if not _allowed(reply.geturl()):
                raise ValueError("reply from a host not allowed")
            data = reply.read(MAX_REPLY + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NoRelease("no release published yet") from None
        raise OSError(f"GitHub answered {e.code}") from None
    if len(data) > MAX_REPLY:
        raise ValueError("reply too large")
    try:
        reply = json.loads(data)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise ValueError("reply is not JSON") from None
    tag = reply.get("tag_name") if isinstance(reply, dict) else None
    if not isinstance(tag, str) or not tag.startswith("v") or parse_version(tag[1:]) is None:
        raise ValueError("reply names no release version")
    return {"version": tag[1:], "tag": tag, "page": release_page(reply.get("html_url")) or tag_page(tag)}


def valid_release(release):
    """``release`` as fetch_latest makes it, checked again (it may come from the state file), or None."""
    if not isinstance(release, dict) or set(release) != {"version", "tag", "page"}:
        return None
    version, tag, page = release["version"], release["tag"], release["page"]
    if parse_version(version) is None or tag != "v" + str(version) or release_page(page) is None:
        return None
    return {"version": version, "tag": tag, "page": page}


# -- how this copy is updated -------------------------------------------------------------------------------------

def _package_dir():
    return os.path.realpath(os.path.dirname(os.path.abspath(__file__)))


def install_kind():
    """"deb", "user" (make install-user) or "other". Looks at files only; runs nothing."""
    if sys.platform == "win32" or getattr(sys, "frozen", False):
        return "other"
    here = _package_dir()
    if here == "/usr/lib/mxoptions/mxoptions" and os.path.isfile("/var/lib/dpkg/info/mxoptions.list"):
        return "deb"
    home = os.path.expanduser("~")
    if here == os.path.realpath(os.path.join(home, ".local", "lib", "mxoptions", "mxoptions")):
        return "user"
    return "other"


def _python():
    """This program's interpreter, when it can stand in a command as it is."""
    exe = sys.executable or ""
    return exe if _SAFE_PATH.match(exe) else None


def update_commands(kind, version):
    """The commands that update this copy to ``version``, never translated; "" when there are none."""
    if parse_version(version) is None:
        return ""
    restart = "systemctl --user restart mxoptions.service"
    if kind == "deb":  # each step only when the one before it worked
        return f"sudo apt install ./mxoptions_{debian_version(version)}_all.deb && \\\n{restart}"
    if kind == "user":
        python = _python()
        make = f"make install-user PYTHON={python}" if python else "make install-user"
        return f"tar xf mxoptions-{version}.tar.gz && \\\ncd mxoptions-{version} && \\\n{make} && \\\n{restart}"
    return ""


def update_text(kind, version):
    """What to do first, in words (translated), before update_commands."""
    if kind == "deb":
        return _("Download {name} from the release page, then in the folder it is in run:").format(
            name=f"mxoptions_{debian_version(version)}_all.deb")
    if kind == "user":
        text = _("Download \"Source code (tar.gz)\" from the release page, then in the folder it is in run:")
        if _python() is None and sys.executable:
            text += " " + _("Use the Python this app runs with: {path}").format(path=sys.executable)
        return text
    return _("Download it from the release page.")


# -- what was last found ----------------------------------------------------------------------------------------------

_warned = []


def _warn_once(text):
    if not _warned:
        _warned.append(text)
        log.info("update state not kept on disk: %s", text)


def _can_keep():
    return all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_NONBLOCK", "getuid")) and sys.platform != "win32"


def _data_dir_fd():
    """A descriptor of the data folder when it is safe to keep the state there (a folder of this user, not a link,
    not writable by others), else None."""
    if not _can_keep():
        _warn_once("this system lacks the file checks it needs")
        return None
    try:
        fd = os.open(paths.DATA, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        os.close(fd)
        _warn_once("the data folder is not this user's alone")
        return None
    return fd


def _fresh_time(value, now):
    try:
        return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
                and value <= now + 24 * 3600)
    except (OverflowError, ValueError, TypeError):
        return False


def load_state(now=None):
    """{"checked_at": float, "notified": str, "release": dict or None}, from update.json; empty values for anything
    missing, unreadable or not as expected. Never blocks, never raises."""
    now = time.time() if now is None else now
    state = {"checked_at": None, "notified": "", "release": None}
    fd = _data_dir_fd()
    if fd is None:
        return state
    try:
        try:
            file = os.open(STATE_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        except OSError:
            return state
        try:
            info = os.fstat(file)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STATE:
                return state
            data = os.read(file, MAX_STATE + 1)
        finally:
            os.close(file)
    finally:
        os.close(fd)
    try:
        saved = json.loads(data)
    except (ValueError, UnicodeDecodeError, RecursionError):  # deep nesting as well
        return state
    if not isinstance(saved, dict) or not set(saved) <= {"checked_at", "notified", "release"}:
        return state
    try:  # whatever is in the file, nothing of it may stop the program
        if _fresh_time(saved.get("checked_at"), now):
            state["checked_at"] = float(saved["checked_at"])
        if isinstance(saved.get("notified"), str) and parse_version(saved["notified"]) is not None:
            state["notified"] = saved["notified"]
        state["release"] = valid_release(saved.get("release"))
    except (ValueError, OverflowError, TypeError, RecursionError):
        return {"checked_at": None, "notified": "", "release": None}
    return state


def save_state(state):
    """Write ``state`` to update.json when that can be done safely; else keep nothing (logged once)."""
    fd = _data_dir_fd()
    if fd is None:
        return False
    data = json.dumps({"checked_at": state.get("checked_at"), "notified": state.get("notified") or "",
                       "release": state.get("release")}).encode()
    temp = f".{STATE_FILE}.{os.getpid()}.tmp"
    try:
        try:
            file = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        except OSError as e:
            log.info("could not keep the update state: %s", e)
            return False
        try:
            os.write(file, data)
            os.fsync(file)
        finally:
            os.close(file)
        os.replace(temp, STATE_FILE, src_dir_fd=fd, dst_dir_fd=fd)
        return True
    except OSError as e:
        log.info("could not keep the update state: %s", e)
        try:
            os.unlink(temp, dir_fd=fd)
        except OSError:
            pass
        return False
    finally:
        os.close(fd)
