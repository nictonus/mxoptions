import QtQuick
import QtQuick.Window
import org.kde.layershell as LayerShell

// A see-through line along one edge of a screen: Flow hears of the pointer through it where the
// compositor tells a program nothing else about the pointer (see flow_edges.py).
Window {
    id: line
    property string edge: "right"
    property int index: 0
    property int start: 0      // how far from the top or left end of the screen's side the line begins
    property bool above: false // over full-screen windows too
    readonly property bool upright: edge == "left" || edge == "right"

    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
    visible: false

    LayerShell.Window.layer: above ? LayerShell.Window.LayerOverlay : LayerShell.Window.LayerTop
    LayerShell.Window.anchors: edge == "right" ? (LayerShell.Window.AnchorRight | LayerShell.Window.AnchorTop)
                             : edge == "left" ? (LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorTop)
                             : edge == "top" ? (LayerShell.Window.AnchorTop | LayerShell.Window.AnchorLeft)
                             : (LayerShell.Window.AnchorBottom | LayerShell.Window.AnchorLeft)
    LayerShell.Window.margins.top: upright ? start : 0
    LayerShell.Window.margins.left: upright ? 0 : start
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone
    LayerShell.Window.exclusionZone: -1   // at the very edge, whatever panels have reserved
    LayerShell.Window.scope: "mxoptions-flow"

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.AllButtons
        onEntered: watcher.at(line.index, mouseX, mouseY)
        onPositionChanged: (mouse) => watcher.at(line.index, mouse.x, mouse.y)
        onExited: watcher.gone(line.index)
        onPressed: (mouse) => watcher.pressed(line.index, mouse.button, mouse.x, mouse.y, Date.now())
        onReleased: (mouse) => watcher.released(line.index, mouse.button, mouse.x, mouse.y, Date.now())
        onWheel: (wheel) => watcher.scrolled(line.index, wheel.angleDelta.x, wheel.angleDelta.y)
    }
}
