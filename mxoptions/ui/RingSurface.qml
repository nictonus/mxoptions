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

    // A Spotlight effect, placed from Python; around Highlight the rest of the screen darkens
    Item {
        id: effects
        objectName: "effects"
        anchors.fill: parent
        property bool dims: false
        property color dim: "transparent"
        visible: dims && effect.visible
        Rectangle { color: effects.dim; x: 0; y: 0; width: parent.width; height: Math.max(0, effect.y) }
        Rectangle { color: effects.dim; x: 0; y: effect.y + effect.height; width: parent.width; height: Math.max(0, parent.height - y) }
        Rectangle { color: effects.dim; x: 0; y: effect.y; width: Math.max(0, effect.x); height: effect.height }
        Rectangle { color: effects.dim; x: effect.x + effect.width; y: effect.y; width: Math.max(0, parent.width - x); height: effect.height }
    }

    EffectItem {
        id: effect
        objectName: "effect"
        visible: false
    }
}
