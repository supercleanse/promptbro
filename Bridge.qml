import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

// The only file in the plugin that reads state or spawns a process. It
// watches status.json and recent.json (FileView, never polled), keeps the
// last `doctor --json` result, and wraps every CLI/GAction call. Widget.qml
// and MenuContent.qml stay free of Process and execDetached, so the offscreen
// harness can drive MenuContent with canned data. See CONTRACT.md.
Item {
  id: root

  readonly property string appId: "supercleanse.PromptBro"
  // The plugin's own launcher, by absolute path, so nothing needs to be on PATH.
  readonly property string cliPath: Qt.resolvedUrl("bin/promptbro").toString().replace(/^file:\/\//, "")
  readonly property string home: Quickshell.env("HOME") || ""
  readonly property string runtimeDir: (Quickshell.env("PROMPTBRO_RUNTIME_DIR")
    || ((Quickshell.env("XDG_RUNTIME_DIR") || "/run/user/1000") + "/promptbro"))
  readonly property string stateDir: (Quickshell.env("XDG_STATE_HOME") || (root.home + "/.local/state")) + "/promptbro"
  readonly property string statusPath: root.runtimeDir + "/status.json"
  readonly property string recentPath: root.stateDir + "/recent.json"

  property var st: Model.offlineStatus()
  property var recent: []
  property var doctor: null
  property double nowMs: Date.now()
  readonly property string uiState: Model.uiState(root.st, root.nowMs, root.doctor)
  readonly property bool running: root.uiState !== "offline" && root.uiState !== "setup"

  FileView {
    path: root.statusPath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: { root.st = Model.parseStatus(text()); root.nowMs = Date.now() }
    onLoadFailed: function(error) { root.st = Model.offlineStatus() }
  }

  FileView {
    path: root.recentPath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.recent = Model.parseRecent(text())
    onLoadFailed: function(error) { root.recent = [] }
  }

  // Only re-checks the status file's age (a crashed app stops heartbeating).
  Timer {
    interval: 5000
    running: true
    repeat: true
    onTriggered: root.nowMs = Date.now()
  }

  // Collects stdout and the exit code; onDone(code, stdout, stderr) always
  // fires, even when the process never starts.
  property Component _runner: Component {
    Process {
      id: proc
      property var onDone: null
      stdout: StdioCollector { id: out; waitForEnd: true }
      stderr: StdioCollector { id: err; waitForEnd: true }
      onExited: function(code) {
        var cb = proc.onDone
        var so = out.text
        var se = err.text
        proc.destroy()
        if (cb) cb(code, so, se)
      }
    }
  }

  function run(command, onDone) {
    var proc = root._runner.createObject(root, { command: command, onDone: onDone })
    if (!proc) { if (onDone) onDone(-1, "", "failed to start"); return }
    proc.running = true
  }

  // Fire-and-forget, detached from the shell so a shell restart never kills
  // the prompter.
  function cli(args) {
    Quickshell.execDetached([root.cliPath].concat(args))
  }

  // Activate a GAction on the running app (next, prev, restart, pause,
  // resume, show, hide, toggle, quit, opacity).
  function act(name, param) {
    Quickshell.execDetached(Model.activateArgs(root.appId, name, param))
  }

  function open(path) { root.cli(["open", path]) }
  function paste() { root.cli(["paste"]) }
  function showOrLaunch() {
    if (root.running) root.act("show")
    else root.cli(["show"])
  }

  // `promptbro pick-file`: cb(code, path). 0 chosen, 1 canceled, 2 no picker.
  function pickFile(cb) {
    root.run([root.cliPath, "pick-file"], function(code, so, se) {
      cb(code, String(so || "").trim())
    })
  }

  function refreshDoctor() {
    root.run([root.cliPath, "doctor", "--json"], function(code, so, se) {
      root.doctor = Model.parseDoctor(so)
    })
  }

  function runSetup() {
    Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation",
      Model.shellQuote(root.cliPath) + " setup"])
  }

  Component.onCompleted: root.refreshDoctor()
}
