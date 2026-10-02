import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "Model.js" as Model

// PromptBro bar widget: a glyph (plus an optional "12/48" label) and a
// dropdown. State comes from the Bridge, which watches status.json; this
// file spawns nothing itself. See PROMPTBRO_PLAN.md sections 4.5 and 5.
Panel {
  id: root
  moduleName: "supercleanse.promptbro"
  ipcTarget: "supercleanse.promptbro"

  readonly property bool vertical: bar ? bar.vertical : false
  readonly property int barSize: bar ? bar.barSize : Style.bar.sizeHorizontal

  readonly property color foreground: bar ? bar.barForeground : Color.foreground
  readonly property color panelForeground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.5)
  readonly property color amber: Model.AMBER
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property bool showProgress: root.setting("showProgress", true) !== false
  readonly property string uiState: bridge.uiState
  readonly property string glyphChar: Model.glyphFor(root.uiState)
  readonly property string labelText: root.showProgress ? Model.progressLabel(bridge.st) : ""
  readonly property color glyphColor: {
    var role = Model.colorRoleFor(root.uiState)
    return role === "accent" ? Color.accent
      : role === "amber" ? root.amber
      : role === "urgent" ? root.urgent
      : role === "fg" ? root.foreground
      : root.dim
  }

  // Set when the portal picker is unavailable (exit 2): the dropdown shows a
  // path field instead of "Open script...".
  property bool pickerFallback: false

  Bridge { id: bridge }

  // ---- reveal: copied from Spitball (which mirrors omarchy.indicators).
  // Collapses to zero width unless in use, the dropdown is open, this
  // widget is hovered, or the bar's center section is hover-revealed.
  property bool selfHovered: false
  property bool forceReveal: false   // test hook for the offscreen harness
  readonly property bool centerRevealed: !!bar && bar.centerSectionRevealHeld === true && bar.centerHoverRevealSuppressed !== true
  readonly property bool inUse: Model.inUse(bridge.st, root.uiState)
  readonly property bool shouldShow: inUse || opened || selfHovered || centerRevealed || forceReveal

  onOpenedChanged: if (root.opened) bridge.refreshDoctor()

  // The layering rule: the dropdown is a layer-shell surface with exclusive
  // keyboard focus, so it closes before a normal window (the file dialog)
  // opens.
  function pickScript() {
    root.close()
    bridge.pickFile(function(code, path) {
      if (code === 0 && path !== "") {
        bridge.open(path)
      } else if (code === 2) {
        root.pickerFallback = true
        root.open()
      } else if (code === 1) {
        root.open()
      }
    })
  }

  // Transport verbs keep the dropdown open so a click can repeat; every
  // other action closes it. The opacity slider also stays open.
  function runAction(name, param) {
    var keepOpen = ["prev", "next", "restart", "pause", "resume", "opacity"]
    if (name === "show") bridge.showOrLaunch()
    else bridge.act(name, param)
    if (keepOpen.indexOf(name) === -1) root.close()
  }

  visible: true
  clip: true
  implicitWidth: collapseArea.implicitWidth
  implicitHeight: collapseArea.implicitHeight

  // Outer item collapses (clipped); the button keeps its natural size so
  // the reveal is a clean width change, as in Spitball.
  Item {
    id: collapseArea
    clip: true
    implicitWidth: root.vertical ? Math.max(button.implicitWidth, root.barSize)
      : (root.shouldShow ? button.implicitWidth : 0)
    implicitHeight: root.vertical ? (root.shouldShow ? button.implicitHeight : 0)
      : Math.max(button.implicitHeight, root.barSize)
    width: implicitWidth
    height: implicitHeight

    HoverHandler {
      onHoveredChanged: root.selfHovered = hovered
    }

    WidgetButton {
      id: button
      anchors.centerIn: parent
      bar: root.bar
      text: " "
      labelVisible: false
      hasVisualContent: true
      tooltipText: Model.tooltipFor(bridge.st, root.uiState, bridge.doctor)
      keepSpace: false
      concealed: !root.shouldShow
      interactive: root.shouldShow
      dimmed: root.uiState === "offline"
      fixedWidth: vertical ? -1 : Math.round(barContent.implicitWidth + Style.spaceReal(7) * 2)
      fixedHeight: vertical ? Math.round(barContent.implicitHeight + Style.spaceReal(6) * 2) : -1

      // A bar click only opens the menu.
      onPressed: function(b) {
        if (b === Qt.LeftButton || b === Qt.RightButton) root.toggle()
      }

      Grid {
        id: barContent
        anchors.centerIn: parent
        columns: button.vertical ? 1 : 2
        rowSpacing: Style.space(2)
        columnSpacing: Style.space(5)
        horizontalItemAlignment: Grid.AlignHCenter
        verticalItemAlignment: Grid.AlignVCenter

        Text {
          textFormat: Text.PlainText
          text: root.glyphChar
          color: root.glyphColor
          font.family: root.fontFamily
          font.pixelSize: Style.bar.iconFont
          renderType: Text.NativeRendering

          // Gentle pulse while the model loads; every other state is steady.
          SequentialAnimation on opacity {
            loops: Animation.Infinite
            running: root.uiState === "starting"
            NumberAnimation { from: 1.0; to: 0.4; duration: 650; easing.type: Easing.InOutSine }
            NumberAnimation { from: 0.4; to: 1.0; duration: 650; easing.type: Easing.InOutSine }
          }
        }

        Text {
          textFormat: Text.PlainText
          visible: root.labelText !== ""
          text: root.labelText
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          renderType: Text.NativeRendering
        }
      }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(280))
    contentHeight: panel.fittedContentHeight(menu.implicitHeight, Style.space(520))

    FocusScope {
      id: keyCatcher
      anchors.fill: parent
      focus: true
      Keys.onEscapePressed: root.close()

      MenuContent {
        id: menu
        width: parent.width
        bar: root.bar
        st: bridge.st
        uiState: root.uiState
        recent: bridge.recent
        doctor: bridge.doctor
        pickerFallback: root.pickerFallback
        foreground: root.panelForeground
        fontFamily: root.fontFamily
        onOpenRequested: root.pickScript()
        onPasteRequested: { bridge.paste(); root.close() }
        onRecentRequested: function(path) { bridge.open(path); root.close() }
        onActionRequested: function(name, param) { root.runAction(name, param) }
        onSetupRequested: { root.close(); bridge.runSetup() }
        onPathEntered: function(path) { root.pickerFallback = false; bridge.open(path); root.close() }
      }
    }
  }
}
