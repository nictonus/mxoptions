#!/usr/bin/env python3
"""Build MX Options for Windows: a folder with mxoptions.exe and everything it needs, and a zip of it.

Run on Windows, in the source tree, with Python, PySide6 and PyInstaller installed:

    python tools\\build_windows.py

The result is dist\\mxoptions (start mxoptions.exe in it), dist\\mxoptions-<version>-windows.zip, and,
if Inno Setup is installed, dist\\mxoptions-<version>-setup.exe (see packaging\\windows\\mxoptions.iss).
The folder can be put anywhere: "Start at login" in the settings points Windows at wherever it is."""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mxoptions import APP_ID, __version__  # noqa: E402

# Parts of Qt the program never loads, which PyInstaller would bring along with the rest of Qt Quick.
UNUSED = ("PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.Qt3DCore",
          "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtQuick3D", "PySide6.QtGraphs",
          "PySide6.QtLocation", "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtBluetooth", "PySide6.QtNfc",
          "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
          "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtWebSockets", "PySide6.QtWebChannel")


# ... and what comes along all the same, as files other parts of Qt name: taken out of the built folder again. The
# first of them alone is a web browser of some 200 MB.
LEFT_OUT = ("Qt6WebEngine*", "QtWebEngine*", "Qt6Pdf*", "QtPdf*", "Qt6Quick3D*", "Qt63D*", "Qt6Charts*", "Qt6DataVisualization*",
            "Qt6Graphs*", "Qt6Location*", "Qt6Positioning*", "Qt6Sensors*", "Qt6Bluetooth*", "Qt6Nfc*", "Qt6Designer*",
            "Qt6Sql*", "Qt6Test*", "Qt6WebSockets*", "Qt6WebChannel*", "Qt6WebView*", "Qt6RemoteObjects*", "Qt6Scxml*",
            "Qt6SerialPort*", "Qt6TextToSpeech*", "Qt6VirtualKeyboard*", "Qt6SpatialAudio*", "resources",
            os.path.join("translations", "qtwebengine_locales"),
            *(os.path.join("qml", name) for name in (
                "Qt3D", "QtCharts", "QtDataVisualization", "QtGraphs", "QtLocation", "QtPositioning", "QtQuick3D", "QtWebEngine",
                "QtWebView", "QtWebChannel", "QtWebSockets", "QtSensors", "QtTest", "QtRemoteObjects", "QtScxml",
                "QtTextToSpeech", "QtBluetooth", "QtNfc", "Qt5Compat", "QtPdf", os.path.join("QtQuick", "VirtualKeyboard"),
                os.path.join("QtQuick", "Pdf"), os.path.join("QtQuick", "Scene2D"), os.path.join("QtQuick", "Scene3D"))))


def leave_out(folder):
    """Remove from the built ``folder`` what the program never loads. Returns the bytes that freed."""
    import glob

    freed = 0
    for pattern in LEFT_OUT:
        for path in glob.glob(os.path.join(folder, "_internal", "PySide6", pattern)):
            if os.path.isdir(path):
                freed += sum(os.path.getsize(os.path.join(base, name)) for base, _dirs, names in os.walk(path) for name in names)
                shutil.rmtree(path)
            else:
                freed += os.path.getsize(path)
                os.remove(path)
    return freed


# The client library of the controller driver that game rumble needs on Windows (mxoptions/rumble_windows.py). It is
# MIT-licensed; the copy taken is the one the vgamepad package on PyPI carries, checked against these sums.
VIGEM = {
    "package": "https://files.pythonhosted.org/packages/8a/54/0eaddc33f84247963af078f364b37153d09fcd6cdc398f243ec3e8842c56/vgamepad-0.1.0.tar.gz",
    "package_sha256": "57f6bd01aec0c172947517fb782d150ef9b285f7f4d524c317374fa5c24a89de",
    "member": "vgamepad-0.1.0/vgamepad/win/vigem/client/x64/ViGEmClient.dll",
    "sha256": "2bf0cb1d809039573c922737d298a1653d4dbc61408060ff45a9bcfde82e97d2",
    "licence": "https://raw.githubusercontent.com/nefarius/ViGEmClient/master/LICENSE",
}


def fetch_vigem(work):
    """The client library and its licence, fetched once into ``work``: (library path, licence path or None), or
    (None, None) if it could not be had, in which case the program is built without game rumble."""
    import hashlib
    import tarfile
    import urllib.request

    library, licence = os.path.join(work, "ViGEmClient.dll"), os.path.join(work, "ViGEmClient-LICENSE.txt")
    try:
        if not os.path.isfile(library) or hashlib.sha256(open(library, "rb").read()).hexdigest() != VIGEM["sha256"]:
            packed = os.path.join(work, "vgamepad.tar.gz")
            urllib.request.urlretrieve(VIGEM["package"], packed)
            if hashlib.sha256(open(packed, "rb").read()).hexdigest() != VIGEM["package_sha256"]:
                raise OSError("the package is not the one expected")
            with tarfile.open(packed) as archive:
                data = archive.extractfile(VIGEM["member"]).read()
            if hashlib.sha256(data).hexdigest() != VIGEM["sha256"]:
                raise OSError("the library is not the one expected")
            with open(library, "wb") as f:
                f.write(data)
            os.remove(packed)
        if not os.path.isfile(licence):
            try:
                urllib.request.urlretrieve(VIGEM["licence"], licence)
            except OSError:
                licence = None
    except (OSError, KeyError, AttributeError) as error:
        print(f"no game rumble in this build: the controller driver's client library could not be fetched ({error})")
        return None, None
    return library, licence


def make_icon(path):
    """The program's drawing as a Windows icon file."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    _app = QGuiApplication.instance() or QGuiApplication([])  # Qt needs one to draw; kept until the picture is saved
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    QSvgRenderer(os.path.join(ROOT, "mxoptions", "assets", "icon.svg")).render(painter)
    painter.end()
    if not image.save(path, "ICO"):
        raise SystemExit("could not write the icon")


def main():
    if sys.platform != "win32":
        raise SystemExit("this builds the Windows program and has to be run on Windows")
    work = os.path.join(ROOT, "build", "windows")
    dist = os.path.join(ROOT, "dist")
    os.makedirs(work, exist_ok=True)
    icon = os.path.join(work, "icon.ico")
    make_icon(icon)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", APP_ID, "--icon", icon,
               "--collect-data", "mxoptions", "--distpath", dist, "--workpath", os.path.join(work, "pyinstaller"),
               "--specpath", work]
    vigem, vigem_licence = fetch_vigem(work)
    if vigem:
        command += ["--add-binary", f"{vigem}{os.pathsep}{os.path.join('mxoptions', 'assets')}"]
    for module in UNUSED:
        command += ["--exclude-module", module]
    subprocess.run(command + [os.path.join(ROOT, "main.py")], check=True, cwd=ROOT)
    folder = os.path.join(dist, APP_ID)
    print(f"left out {leave_out(folder) / 1e6:.0f} MB of Qt that the program does not use")
    for name in ("README.md", "README.uk.md", "LICENSE"):
        shutil.copy(os.path.join(ROOT, name), folder)
    if vigem_licence:
        shutil.copy(vigem_licence, folder)
    shutil.copy(os.path.join(ROOT, "docs", "API.md"), folder)
    packed = shutil.make_archive(os.path.join(dist, f"{APP_ID}-{__version__}-windows"), "zip", dist, APP_ID)
    size = sum(os.path.getsize(os.path.join(base, name)) for base, _dirs, names in os.walk(folder) for name in names)
    print(f"built {folder}: {size / 1e6:.0f} MB; packed as {packed}: {os.path.getsize(packed) / 1e6:.0f} MB")
    make_installer(folder, dist, icon)


def make_installer(folder, dist, icon):
    """The installer, if Inno Setup is on this machine (winget install JRSoftware.InnoSetup)."""
    places = [os.path.join(os.environ.get(name, ""), "Inno Setup 6", "ISCC.exe") for name in ("ProgramFiles(x86)", "ProgramFiles")]
    places.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"))
    compiler = next((path for path in places if os.path.isfile(path)), None) or shutil.which("ISCC")
    if compiler is None:
        print("no installer made: Inno Setup is not here (winget install JRSoftware.InnoSetup)")
        return
    script = os.path.join(ROOT, "packaging", "windows", "mxoptions.iss")
    subprocess.run([compiler, "/Q", f"/DAppVersion={__version__}", f"/DSource={folder}", f"/DOutput={dist}", f"/DIcon={icon}", script],
                   check=True)
    made = os.path.join(dist, f"{APP_ID}-{__version__}-setup.exe")
    print(f"installer: {made}: {os.path.getsize(made) / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
