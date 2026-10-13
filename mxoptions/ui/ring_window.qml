import QtQuick
import QtQuick.Window
import MXOptions

// The ring in a see-through window of the ordinary kind, for where there is no layer above the
// windows to put it on (X11, and GNOME on Wayland). What kind of window exactly, how large and
// where, is set from Python (ring.py).
Window {
    color: "transparent"
    width: 800
    height: 600
    visible: false

    RingSurface { anchors.fill: parent }
}
