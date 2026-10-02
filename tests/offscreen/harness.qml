import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Offscreen render harness -- run ONLY through tests/offscreen/render.sh
// (QT_QPA_PLATFORM=offscreen, fake CLI, fake status files). Never ships.
//
// 1. Widget.qml itself, one PNG per UI state (widget-<state>.png), driven by
//    a canned status.json in the harness runtime dir (the Bridge reads it the
//    way it reads the real one) and a fake `doctor` for the setup state.
// 2. MenuContent.qml (the dropdown body) inside a popup-styled card, one PNG
//    per scenario (dropdown-<scenario>.png), fed canned data directly. The
//    real dropdown is a layer-shell window that cannot map offscreen.
ShellRoot {
  id: shell

  readonly property string shotDir: Quickshell.env("PROMPTBRO_SHOT_DIR") || "/tmp"
  readonly property string runtimeDir: Quickshell.env("PROMPTBRO_RUNTIME_DIR")

  function status(state, line, extra) {
    var s = { schema: 1, state: state, path: "/home/demo/Scripts/keynote-draft.md",
      title: "keynote-draft.md", line: line, lines: 48, visible: state !== "loaded" && state !== "idle",
      message: "", opacity: 0.75, pid: 4242, version: "0.3.0", updated_at: Date.now() / 1000 }
    for (var k in extra) s[k] = extra[k]
    return s
  }

  readonly property var recentDemo: [
    { path: "/home/demo/Scripts/keynote-draft.md", name: "keynote-draft.md" },
    { path: "/home/demo/Scripts/podcast-intro.txt", name: "podcast-intro.txt" },
    { path: "/home/demo/Scripts/a-much-longer-script-name-for-the-quarterly-update.md", name: "a-much-longer-script-name-for-the-quarterly-update.md" }
  ]
  readonly property var badDoctor: ({ ok: false, problems: [{ id: "venv-missing", message: "PromptBro needs its speech engine" }] })

  // [file label, ui state, status object, doctor mode]
  readonly property var widgetShots: [
    { name: "setup", st: null, doctor: "bad" },
    // Not in use: the plain names render revealed (hovered); collapsed-* the same states at rest.
    { name: "offline", st: null, doctor: "ok", reveal: true },
    { name: "idle", st: status("idle", 0, { path: "", title: "", lines: 0 }), doctor: "ok", reveal: true },
    { name: "loaded", st: status("loaded", 11, {}), doctor: "ok", reveal: true },
    { name: "collapsed-offline", st: null, doctor: "ok" },
    { name: "collapsed-idle", st: status("idle", 0, { path: "", title: "", lines: 0 }), doctor: "ok" },
    { name: "collapsed-loaded", st: status("loaded", 11, {}), doctor: "ok" },
    { name: "collapsed-listening-hidden", st: status("listening", 11, { visible: false }), doctor: "ok" },
    { name: "starting", st: status("starting", 11, {}), doctor: "ok" },
    { name: "listening", st: status("listening", 11, {}), doctor: "ok" },
    { name: "paused", st: status("paused", 11, {}), doctor: "ok" },
    { name: "no-audio", st: status("no-audio", 11, {}), doctor: "ok" },
    { name: "error", st: status("error", 0, { message: "Model failed to load" }), doctor: "ok" }
  ]

  readonly property var dropdownShots: [
    { name: "listening", ui: "listening", st: status("listening", 11, {}), doctor: null, recent: recentDemo, fallback: false },
    { name: "paused", ui: "paused", st: status("paused", 11, {}), doctor: null, recent: recentDemo, fallback: false },
    { name: "no-audio", ui: "no-audio", st: status("no-audio", 11, {}), doctor: null, recent: recentDemo, fallback: false },
    { name: "idle", ui: "offline", st: Model.offlineStatus(), doctor: null, recent: recentDemo, fallback: false },
    { name: "setup", ui: "setup", st: Model.offlineStatus(), doctor: badDoctor, recent: [], fallback: false },
    { name: "fallback", ui: "offline", st: Model.offlineStatus(), doctor: null, recent: [], fallback: true }
  ]

  property int index: 0   // widget shots first, then dropdown
  readonly property int widgetCount: widgetShots.length

  FileView { id: statusFile; path: shell.runtimeDir + "/status.json"; printErrors: false }
  FileView { id: modeFile; path: shell.runtimeDir + "/doctor-mode"; printErrors: false }

  FloatingWindow {
    id: win
    visible: true
    implicitWidth: 460
    implicitHeight: 640
    color: Color.background

    // Bar strip holding the real Widget.qml.
    Item {
      id: barStage
      visible: shell.index < shell.widgetCount
      width: 260
      height: 64
      Rectangle { anchors.fill: parent; color: Color.background }
      Loader {
        id: widgetLoader
        anchors.centerIn: parent
        active: false
        source: "Widget.qml"
        onLoaded: item.forceReveal = !!shell.widgetShots[Math.min(shell.index, shell.widgetCount - 1)].reveal
      }
    }

    // Dropdown card.
    Item {
      id: cardStage
      visible: shell.index >= shell.widgetCount
      y: 0
      width: card.width + 40
      height: card.height + 40
      property var shot: shell.dropdownShots[Math.max(0, Math.min(shell.index - shell.widgetCount, shell.dropdownShots.length - 1))]

      Rectangle { anchors.fill: parent; color: Color.menu.scrim }
      BorderSurface {
        id: card
        anchors.centerIn: parent
        width: Style.space(280) + padding * 2 + 8
        height: menu.implicitHeight + contentTopInset + contentBottomInset
        radius: Style.cornerRadius
        color: Color.popups.background
        borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Math.max(1, Style.space(2)))
        padding: Style.spacing.popupPadding

        MenuContent {
          id: menu
          x: card.contentLeftInset
          y: card.contentTopInset
          width: Style.space(280)
          st: cardStage.shot.st
          uiState: cardStage.shot.ui
          doctor: cardStage.shot.doctor
          recent: cardStage.shot.recent
          pickerFallback: cardStage.shot.fallback
          foreground: Color.foreground
        }
      }
    }
  }

  function target() {
    var kind = shell.index < shell.widgetCount ? "widget-" + shell.widgetShots[shell.index].name
      : "dropdown-" + shell.dropdownShots[shell.index - shell.widgetCount].name
    return shell.shotDir + "/" + kind + ".png"
  }

  // One shot per cycle: arrange (write status, flip the fake doctor, reload
  // the widget), wait for the Bridge to read it, grab, move on.
  Timer {
    id: arrange
    interval: 600
    running: true
    onTriggered: {
      if (shell.index >= shell.widgetCount + shell.dropdownShots.length) { Qt.quit(); return }
      if (shell.index < shell.widgetCount) {
        var s = shell.widgetShots[shell.index]
        modeFile.setText(s.doctor)
        statusFile.setText(s.st ? JSON.stringify(s.st) : "")
        widgetLoader.active = false
        widgetLoader.active = true
      }
      settle.restart()
    }
  }
  Timer {
    id: settle
    interval: 800
    onTriggered: {
      var item = shell.index < shell.widgetCount ? barStage : cardStage
      var out = shell.target()
      item.grabToImage(function(result) {
        if (!result.saveToFile(out)) console.warn("harness: failed to save " + out)
        else console.info("harness: wrote " + out)
        shell.index += 1
        arrange.restart()
      })
    }
  }
}
