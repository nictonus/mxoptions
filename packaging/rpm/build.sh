#!/bin/sh
# Runs inside Fedora (see tools/distro.py; "make rpm" does it): builds the RPM from the source tree
# as it is, puts it into dist/, installs it with everything it depends on and runs the tests
# against this system's Python and Qt. The source tree is the current directory.
set -e
dnf install -y -q rpm-build make python3 systemd-rpm-macros tar gzip 2>&1 | tail -2
top=/root/rpmbuild
srcdir=$(sed -n 's/^%global srcdir *//p' packaging/rpm/mxoptions.spec)
mkdir -p "$top/SOURCES" dist
# the archive the spec expects, made from the tree instead of fetched from the tag
tar --exclude=.git --exclude=dist --exclude=build --exclude=__pycache__ --transform "s|^\.|$srcdir|" -czf "$top/SOURCES/$srcdir.tar.gz" .
rpmbuild -bb packaging/rpm/mxoptions.spec 2>&1 | tail -3
cp "$top"/RPMS/noarch/mxoptions-*.rpm dist/
echo "=== installing the package and what it depends on"
dnf install -y -q --setopt=install_weak_deps=False "$top"/RPMS/noarch/mxoptions-*.rpm 2>&1 | tail -3
echo "=== installed: $(rpm -q mxoptions)"
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
