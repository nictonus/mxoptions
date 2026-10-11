import QtQuick
import QtQuick.Window
import org.kde.layershell as LayerShell
import MXOptions

// Transparent full-screen overlay; the ring itself is positioned from Python.
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

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.AllButtons
        onEntered: overlay.pointerAt(mouseX, mouseY)
        onPositionChanged: (mouse) => overlay.pointerAt(mouse.x, mouse.y)
        onClicked: (mouse) => overlay.clickAt(mouse.x, mouse.y)
        // the thumb wheel scrolls sideways; rolling it forward (to the right) counts as "up"
        onWheel: (wheel) => overlay.wheelAt(wheel.x, wheel.y, wheel.angleDelta.y - wheel.angleDelta.x)
    }

    RingItem {
        id: ring
        objectName: "ring"
        property bool shown: false   // set from Python once the cursor position is known
        width: 380
        height: 380
        opacity: shown ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 90 } }
    }
}
