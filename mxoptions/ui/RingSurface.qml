import QtQuick
import MXOptions

// What the ring's window holds, whichever kind of window that is: the ring, positioned from Python,
// and what the pointer does, passed on to Python.
Item {
    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.AllButtons
        onEntered: overlay.pointerAt(mouseX, mouseY)
        onPositionChanged: (mouse) => overlay.pointerAt(mouse.x, mouse.y)
        onExited: overlay.pointerLeft()
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
