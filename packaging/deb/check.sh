#!/bin/sh
# Runs inside Debian (see tools/distro.py; "make deb-test" does it): installs the package built by
# "make deb" with everything it depends on, then tries the installed program and runs the tests
# against this system's Python and Qt. The source tree is the current directory.
set -e
apt-get update -q 2>&1 | tail -2
echo "=== installing the package and what it depends on"
apt-get install -y -q --no-install-recommends ./dist/mxoptions_*_all.deb 2>&1 | tail -4
echo "=== installed: $(dpkg -s mxoptions | grep -E '^(Status|Version)' | tr '\n' ' ')"
python3 --version
python3 -c "import PySide6.QtCore as c; print('PySide6', c.__version__, 'Qt', c.qVersion())"
echo "=== the installed program"
/usr/bin/mxoptions --version
python3 - <<'PY'
import sys
sys.path.insert(0, "/usr/lib/mxoptions")
from mxoptions import actions, api, clipboard, config, device, engine, flow, flow_service, rumble, vinput  # noqa: F401
from mxoptions.ui import main_window, ring, tray_icon  # noqa: F401
print("every part of the installed copy loads; rich clipboard:", clipboard.rich(), "| Flow:", flow.supported())
PY
echo "=== the tests"
python3 -m unittest discover -s tests 2>&1 | tail -4
