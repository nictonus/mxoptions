"""Where the program keeps its files: the places each system sets aside for that."""

import os
import sys
import tempfile

from . import APP_ID

if sys.platform == "win32":
    _roaming = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    _local = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    CONFIG = os.path.join(_roaming, APP_ID)          # the settings: they follow the user between computers
    DATA = os.path.join(_local, APP_ID)              # what was fetched or made for this computer
    CACHE = os.path.join(_local, APP_ID, "cache")    # what can be made again
    RUNTIME = os.path.join(_local, APP_ID, "run")    # what only matters while the program runs
else:
    CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), APP_ID)
    DATA = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), APP_ID)
    CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), APP_ID)
    RUNTIME = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(), APP_ID)
