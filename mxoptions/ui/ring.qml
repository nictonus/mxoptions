import QtQuick
import QtQuick.Window
import org.kde.layershell as LayerShell
import MXOptions

// Transparent full-screen overlay on a layer above the windows, for Wayland compositors that have
// such layers; the ring itself is positioned from Python.
Window {
    id: win
    color: "transparent"
    flags: Qt.FramelessWindowHint
    width: 800
    height: 600
    visible: false

    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone
    LayerShell.Window.exclusionZone: -1
    LayerShell.Window.scope: "mxoptions-ring"

    // Open on the screen the pointer is on. The property for that was renamed in layer-shell-qt 6.6;
    // setting it from script keeps both the older and the newer versions working.
    Component.onCompleted: {
        const shell = win.LayerShell.Window
        if (shell.wantsToBeOnActiveScreen !== undefined)
            shell.wantsToBeOnActiveScreen = true
        else
            shell.screenConfiguration = LayerShell.Window.ScreenFromCompositor
    }

    RingSurface { anchors.fill: parent }
}
