import QtQuick

// Harness-only stand-in for Omarchy's KeyboardPanel, which is a layer-shell
// PanelWindow and cannot load without a Wayland compositor. It exposes the
// properties and functions Widget.qml uses and shows nothing; the dropdown
// body is rendered separately from MenuContent.qml.
Item {
  property Item anchorItem: null
  property var owner: null
  property var bar: null
  property bool open: false
  property Item focusTarget: null
  property real contentWidth: 0
  property real contentHeight: 0
  default property alias contentItem: holder.children
  function fittedContentWidth(w) { return w }
  function fittedContentHeight(h, max) { return Math.min(h, max) }
  visible: false
  Item { id: holder }
}
