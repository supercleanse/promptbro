import QtQuick
import qs.Commons
import qs.Ui
import "Model.js" as Model

// The dropdown body only: no processes, no file reads. Everything it wants
// done goes out as a signal, so the offscreen harness can render it with
// canned data. Layout follows PROMPTBRO_PLAN.md section 5.2.
Item {
  id: root

  property var st: Model.offlineStatus()
  property string uiState: "offline"
  property var recent: []
  property var doctor: null
  property bool pickerFallback: false
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  property QtObject bar: null

  signal openRequested()
  signal pasteRequested()
  signal recentRequested(string path)
  signal actionRequested(string name, var param)
  signal setupRequested()
  signal pathEntered(string path)

  readonly property bool doctorOk: !root.doctor || root.doctor.ok !== false
  readonly property bool canNav: Model.canNavigate(root.uiState)
  readonly property bool running: root.uiState !== "offline" && root.uiState !== "setup"
  // Shape and brightness carry state here, never red versus green.
  readonly property bool prompterShown: root.uiState === "starting" || root.uiState === "listening"
    || root.uiState === "paused" || root.uiState === "no-audio" || root.uiState === "error"
  readonly property string problemText: {
    var p = root.doctor && root.doctor.problems && root.doctor.problems.length ? root.doctor.problems[0] : null
    if (!p) return ""
    var m = p.message || ""
    if (p.id === "venv-missing") m += " (downloads about 470 MB)"
    return m
  }

  implicitHeight: column.implicitHeight

  // Button has no elision, and a long recent name would run past the card's
  // edge. Monospace font, so a character budget is a width budget.
  function shortName(name) {
    var max = 30
    if (name.length <= max) return name
    var head = Math.ceil((max - 1) * 0.6)
    return name.slice(0, head) + "…" + name.slice(name.length - (max - 1 - head))
  }

  Column {
    id: column
    width: parent.width
    spacing: Style.space(2)

    // 1. Header
    Column {
      width: parent.width
      spacing: Style.space(1)
      bottomPadding: Style.space(3)
      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: Model.headerTitle(root.st)
        color: root.foreground
        elide: Text.ElideMiddle
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        font.bold: true
        renderType: Text.NativeRendering
      }
      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: Model.headerSubtitle(root.st, root.uiState)
        color: root.foreground
        opacity: 0.7
        elide: Text.ElideRight
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        renderType: Text.NativeRendering
      }
    }

    PanelSeparator { width: parent.width; foreground: root.foreground }

    // 2. Set up (only when doctor reports a problem)
    Button {
      width: parent.width
      visible: !root.doctorOk
      leftAlign: true
      iconText: "\u{F0493}"
      text: "Set up PromptBro…"
      foreground: root.foreground
      fontFamily: root.fontFamily
      onClicked: root.setupRequested()
    }
    Text {
      width: parent.width
      visible: !root.doctorOk && root.problemText !== ""
      textFormat: Text.PlainText
      text: root.problemText
      wrapMode: Text.WordWrap
      color: root.foreground
      opacity: 0.7
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      renderType: Text.NativeRendering
    }

    // 3-4. Open, paste
    Button {
      width: parent.width
      visible: !root.pickerFallback
      leftAlign: true
      iconText: "\u{F0770}"
      text: "Open script…"
      foreground: root.foreground
      fontFamily: root.fontFamily
      onClicked: root.openRequested()
    }

    Row {
      id: pathRow
      width: parent.width
      visible: root.pickerFallback
      spacing: Style.space(3)
      TextField {
        id: pathField
        width: parent.width - openPathButton.width - parent.spacing
        placeholderText: "Path to a .md or .txt file"
        foreground: root.foreground
        font.family: root.fontFamily
        onAccepted: if (text.trim() !== "") root.pathEntered(text.trim())
      }
      Button {
        id: openPathButton
        text: "Open"
        enabled: pathField.text.trim() !== ""
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.pathEntered(pathField.text.trim())
      }
    }

    Button {
      width: parent.width
      leftAlign: true
      iconText: "\u{F0192}"
      text: "Paste from clipboard"
      foreground: root.foreground
      fontFamily: root.fontFamily
      onClicked: root.pasteRequested()
    }

    // 5. Recent
    Text {
      width: parent.width
      visible: root.recent.length > 0
      topPadding: Style.space(2)
      textFormat: Text.PlainText
      text: "Recent"
      color: root.foreground
      opacity: 0.6
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      renderType: Text.NativeRendering
    }
    Repeater {
      model: root.recent
      delegate: Button {
        required property var modelData
        width: column.width
        leftAlign: true
        text: root.shortName(modelData.name)
        tooltipText: modelData.path
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.recentRequested(modelData.path)
      }
    }

    // 6. Transport (leaves the dropdown open)
    PanelSeparator { width: parent.width; foreground: root.foreground }

    Row {
      width: parent.width
      spacing: 0
      readonly property real cell: width / 4

      Button {
        width: parent.cell
        enabled: root.canNav
        opacity: enabled ? 1.0 : 0.4
        iconText: "\u{F0143}"
        tooltipText: "Previous line"
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.actionRequested("prev", undefined)
      }
      Button {
        width: parent.cell
        enabled: root.canNav
        opacity: enabled ? 1.0 : 0.4
        iconText: "\u{F0140}"
        tooltipText: "Next line"
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.actionRequested("next", undefined)
      }
      Button {
        width: parent.cell
        enabled: root.canNav
        opacity: enabled ? 1.0 : 0.4
        iconText: "\u{F0623}"
        tooltipText: "Restart from top"
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.actionRequested("restart", undefined)
      }
      Button {
        width: parent.cell
        enabled: root.canNav
        opacity: enabled ? 1.0 : 0.4
        iconText: root.uiState === "paused" ? "\u{F040A}" : "\u{F03E4}"
        tooltipText: root.uiState === "paused" ? "Resume" : "Pause"
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.actionRequested(root.uiState === "paused" ? "resume" : "pause", undefined)
      }
    }

    // 7. Show / hide
    Button {
      width: parent.width
      leftAlign: true
      iconText: root.prompterShown ? "\u{F0209}" : "\u{F0208}"
      text: root.prompterShown ? "Hide prompter" : "Show prompter"
      foreground: root.foreground
      fontFamily: root.fontFamily
      onClicked: root.actionRequested(root.prompterShown ? "hide" : "show", undefined)
    }

    // 8. Opacity. Sends on release; the app clamps to 0.3-1.0.
    Row {
      width: parent.width
      spacing: Style.space(4)
      Text {
        id: opacityLabel
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: "Opacity"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        renderType: Text.NativeRendering
      }
      PanelSlider {
        bar: root.bar
        width: parent.width - opacityLabel.width - parent.spacing
        anchors.verticalCenter: parent.verticalCenter
        minimum: 0.3
        maximum: 1.0
        step: 0.05
        value: Model.clampOpacity(root.st.opacity)
        onReleased: function(v) { root.actionRequested("opacity", Model.clampOpacity(v)) }
      }
    }

    // 9. Quit
    PanelSeparator { width: parent.width; foreground: root.foreground }

    Button {
      width: parent.width
      enabled: root.running
      opacity: enabled ? 1.0 : 0.4
      leftAlign: true
      iconText: "\u{F0425}"
      text: "Quit PromptBro"
      foreground: root.foreground
      fontFamily: root.fontFamily
      onClicked: root.actionRequested("quit", undefined)
    }
  }
}
