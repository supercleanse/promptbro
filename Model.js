.pragma library

// Pure helpers for the PromptBro bar widget and dropdown. No QML types in
// here, so status parsing and formatting stay easy to test apart from the
// layout. Mirrors CONTRACT.md (status.json, doctor --json, recent.json).

var STALE_MS = 15000
var MAX_RECENT = 8
var AMBER = "#d9a441"

var KNOWN_STATES = ["idle", "loaded", "starting", "listening", "paused", "no-audio", "error", "offline"]

// UI states are the status states plus "setup" (offline while doctor is not ok).
var GLYPHS = {
  "offline": "\u{F0BC3}",   // md-script_text_outline
  "setup": "\u{F0493}",     // md-cog
  "idle": "\u{F0BC3}",      // md-script_text_outline
  "loaded": "\u{F0BC2}",    // md-script_text
  "starting": "\u{F1727}",  // md-script_text_play
  "listening": "\u{F1727}", // md-script_text_play
  "paused": "\u{F1728}",    // md-script_text_play_outline
  "no-audio": "\u{F036D}",  // md-microphone_off
  "error": "\u{F05D6}"      // md-alert_circle_outline
}

var COLOR_ROLES = {
  "offline": "dim", "setup": "amber", "idle": "fg", "loaded": "fg",
  "starting": "dim", "listening": "accent", "paused": "amber",
  "no-audio": "urgent", "error": "urgent"
}

var STATE_LABELS = {
  "offline": "Not running", "setup": "Not set up", "idle": "No script loaded",
  "loaded": "Hidden", "starting": "Starting", "listening": "Listening",
  "paused": "Paused", "no-audio": "No audio", "error": "Error"
}

function offlineStatus() {
  return {
    schema: 1, state: "offline", detail: "", path: "", title: "", line: 0,
    lines: 0, visible: false, message: "", opacity: 0.75, pid: 0,
    version: "", updated_at: 0
  }
}

// Parses status.json text. A missing, empty, unparseable or non-object file
// is offline; an unknown state is offline; missing fields are filled from the
// offline default so callers can bind straight to fields.
function parseStatus(text) {
  var base = offlineStatus()
  try {
    var s = JSON.parse(String(text || ""))
    if (!s || typeof s !== "object" || Array.isArray(s)) return base
    for (var k in base) if (s[k] === undefined || s[k] === null) s[k] = base[k]
    if (KNOWN_STATES.indexOf(s.state) === -1) s.state = "offline"
    return s
  } catch (e) {
    return base
  }
}

// A crashed app stops heartbeating; treat a file older than 15 s as stale.
function isStale(st, nowMs) {
  var u = Number(st && st.updated_at) || 0
  if (u <= 0) return true
  return (nowMs - u * 1000) > STALE_MS
}

function doctorOk(doctor) {
  return !doctor || doctor.ok !== false
}

// The state the widget draws: the file's state, forced offline when missing
// or stale, and "setup" when offline while doctor reports problems.
function uiState(st, nowMs, doctor) {
  var state = (!st || st.state === "offline" || isStale(st, nowMs)) ? "offline" : st.state
  if (state === "offline" && !doctorOk(doctor)) return "setup"
  return state
}

function glyphFor(state) {
  return GLYPHS[state] || GLYPHS["offline"]
}

function colorRoleFor(state) {
  return COLOR_ROLES[state] || "dim"
}

var PROGRESS_STATES = ["loaded", "starting", "listening", "paused", "no-audio"]

// "12/48" (line is 0-based in the file); blank when there is no script or
// the state has no label.
function progressLabel(st) {
  if (!st || PROGRESS_STATES.indexOf(st.state) === -1) return ""
  var lines = Number(st.lines) || 0
  if (lines <= 0) return ""
  var line = Math.min(Math.max(Number(st.line) || 0, 0), lines - 1)
  return (line + 1) + "/" + lines
}

function headerTitle(st) {
  if (!st || st.state === "offline" || st.state === "idle") return "No script loaded"
  return st.title || "No script loaded"
}

function headerSubtitle(st, state) {
  if (state === "offline" || state === "setup") return "Not running"
  var label = STATE_LABELS[state] || ""
  if (state === "error") return (st && st.message) || label
  var lines = Number(st && st.lines) || 0
  if (lines > 0 && PROGRESS_STATES.indexOf(state) !== -1) {
    var line = Math.min(Math.max(Number(st.line) || 0, 0), lines - 1)
    return "Line " + (line + 1) + " of " + lines + " · " + label
  }
  return label
}

function firstProblem(doctor) {
  if (doctor && Array.isArray(doctor.problems) && doctor.problems.length) {
    var p = doctor.problems[0]
    if (p && p.message) return String(p.message)
  }
  return ""
}

function tooltipFor(st, state, doctor) {
  if (state === "setup") return "PromptBro: " + (firstProblem(doctor) || "needs setup")
  if (state === "offline") return "PromptBro: not running"
  if (state === "idle") return "PromptBro: no script loaded"
  if (state === "error") return "PromptBro: " + ((st && st.message) || "error")
  var lines = Number(st && st.lines) || 0
  var line = Math.min(Math.max(Number(st && st.line) || 0, 0), Math.max(lines - 1, 0))
  var where = (st && st.title) ? st.title : "script"
  var label = (STATE_LABELS[state] || state).toLowerCase()
  if (lines > 0) return "PromptBro: " + where + ", line " + (line + 1) + " of " + lines + ", " + label
  return "PromptBro: " + where + ", " + label
}

var ACTIVE_STATES = ["starting", "listening", "paused", "no-audio"]

// Whether the bar widget stays revealed on its own (Spitball pattern: only
// "quiet" states collapse until hover). In use = the prompter window is up
// (status.visible) in a running state, or the app is in error, or setup is
// needed (a fresh install must never be invisible, like Spitball's gear).
// Hidden (loaded), idle with no script, and offline are not in use.
function inUse(st, state) {
  if (state === "error" || state === "setup") return true
  if (ACTIVE_STATES.indexOf(state) === -1) return false
  return !!(st && st.visible === true)
}

// Transport buttons work only while running with a script.
function canNavigate(state) {
  return PROGRESS_STATES.indexOf(state) !== -1
}

function basename(p) {
  var s = String(p || "").replace(/\/+$/, "")
  var i = s.lastIndexOf("/")
  return i === -1 ? s : s.slice(i + 1)
}

function dirname(p) {
  var s = String(p || "").replace(/\/+$/, "")
  var i = s.lastIndexOf("/")
  return i <= 0 ? (i === 0 ? "/" : "") : s.slice(0, i)
}

// recent.json text -> [{path, name, dir}], max 8, deduplicated by path,
// garbage-tolerant.
function parseRecent(text) {
  var out = []
  try {
    var r = JSON.parse(String(text || ""))
    var list = (r && typeof r === "object" && Array.isArray(r.recent)) ? r.recent : []
    var seen = {}
    for (var i = 0; i < list.length && out.length < MAX_RECENT; i++) {
      var e = list[i]
      var p = (e && typeof e === "object") ? e.path : ""
      if (typeof p !== "string" || !p || seen[p]) continue
      seen[p] = true
      out.push({ path: p, name: basename(p), dir: dirname(p) })
    }
  } catch (e) {
    return []
  }
  return out
}

// doctor --json text -> normalized object, or null when it can't be parsed
// (callers treat null as "unknown", not as a problem).
function parseDoctor(text) {
  try {
    var d = JSON.parse(String(text || ""))
    if (!d || typeof d !== "object" || Array.isArray(d)) return null
    var problems = []
    if (Array.isArray(d.problems)) {
      for (var i = 0; i < d.problems.length; i++) {
        var p = d.problems[i]
        if (p && typeof p === "object" && p.id) {
          problems.push({ id: String(p.id), message: String(p.message || ""), fix: String(p.fix || "") })
        }
      }
    }
    return {
      ok: d.ok === true && problems.length === 0,
      problems: problems,
      missing_packages: Array.isArray(d.missing_packages) ? d.missing_packages.map(String) : [],
      model: typeof d.model === "string" ? d.model : "",
      model_present: d.model_present === true,
      version: typeof d.version === "string" ? d.version : ""
    }
  } catch (e) {
    return null
  }
}

// "supercleanse.PromptBro" -> "/supercleanse/PromptBro"
function objectPathFor(appId) {
  return "/" + String(appId || "").replace(/\./g, "/").replace(/-/g, "_")
}

// Full gdbus argv for Gio.SimpleAction activation. A number param becomes a
// double variant, e.g. [<0.8>]; whole numbers get ".0" so GVariant parses a
// double, not an int32.
function activateArgs(appId, name, param) {
  var params = "[]"
  if (typeof param === "number" && isFinite(param)) {
    var s = String(param)
    if (/^-?\d+$/.test(s)) s += ".0"
    params = "[<" + s + ">]"
  }
  return [
    "gdbus", "call", "--session", "--dest", appId,
    "--object-path", objectPathFor(appId),
    "--method", "org.gtk.Actions.Activate", name, params, "{}"
  ]
}

function shellQuote(s) {
  return "'" + String(s).replace(/'/g, "'\\''") + "'"
}

// Clamp an opacity slider value to the allowed 0.3-1.0 range.
function clampOpacity(v) {
  var n = Number(v)
  if (!isFinite(n)) return 0.75
  return Math.min(1.0, Math.max(0.3, n))
}
