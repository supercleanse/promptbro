import QtQuick
import Quickshell
import Quickshell.Io

// Bridge check -- run ONLY through render.sh (offscreen, fake data). Writes a
// canned status.json and recent.json into the harness runtime dir, then
// asserts the Bridge's parsed state follows them: listening, then paused, a
// stale file forced offline, garbage forced offline, and the recent list.
// Prints "BRIDGE_CHECK PASS" or "BRIDGE_CHECK FAIL: ..." and quits.
ShellRoot {
  id: shell

  readonly property string runtimeDir: Quickshell.env("PROMPTBRO_RUNTIME_DIR")
  readonly property string stateDir: Quickshell.env("XDG_STATE_HOME") + "/promptbro"
  property int step: 0
  property var failures: []

  function status(state, line, ageSec) {
    return JSON.stringify({ schema: 1, state: state, path: "/x/talk.md", title: "talk.md",
      line: line, lines: 48, visible: true, message: "", opacity: 0.8, pid: 1,
      version: "0", updated_at: Date.now() / 1000 - ageSec })
  }
  function expect(label, got, want) {
    if (got !== want) failures.push(label + ": got " + JSON.stringify(got) + ", wanted " + JSON.stringify(want))
  }

  Bridge { id: bridge }
  property var startFail: null
  FileView { id: statusFile; path: shell.runtimeDir + "/status.json"; }
  FileView { id: recentFile; path: shell.stateDir + "/recent.json"; }

  Timer {
    id: t
    interval: 700
    repeat: true
    running: true
    onTriggered: {
      var s = shell.step++
      if (s === 0) {
        shell.expect("no file -> offline", bridge.uiState, "offline")
        recentFile.setText(JSON.stringify({ recent: [{ path: "/a/one.md" }, { path: "/a/two.md" }, { path: "/a/one.md" }] }))
        statusFile.setText(shell.status("listening", 11, 0))
      } else if (s === 1) {
        shell.expect("listening state", bridge.uiState, "listening")
        shell.expect("line", bridge.st.line, 11)
        shell.expect("recent dedup", bridge.recent.length, 2)
        statusFile.setText(shell.status("paused", 12, 0))
      } else if (s === 2) {
        shell.expect("paused state", bridge.uiState, "paused")
        statusFile.setText(shell.status("listening", 12, 60))
        bridge.nowMs = Date.now()
      } else if (s === 3) {
        shell.expect("stale -> offline", bridge.uiState, "offline")
        statusFile.setText("not json {")
      } else if (s === 4) {
        shell.expect("garbage -> offline", bridge.uiState, "offline")
        shell.expect("doctor ok", bridge.doctor && bridge.doctor.ok, true)
        bridge.run(["/nonexistent/promptbro", "x"], function(code, so, se) { shell.startFail = code })
      } else if (s === 5) {
        shell.expect("failed start completes with -1", shell.startFail, -1)
        t.stop()
        if (shell.failures.length) console.log("BRIDGE_CHECK FAIL: " + shell.failures.join("; "))
        else console.log("BRIDGE_CHECK PASS")
        Qt.quit()
      }
    }
  }
}
