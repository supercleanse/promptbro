"use strict";
const test = require("node:test");
const strict = require("node:assert/strict");
// Values built inside the vm context have foreign prototypes; compare as plain JSON.
const plain = (x) => JSON.parse(JSON.stringify(x === undefined ? null : x));
const assert = Object.assign({}, strict, {
  deepEqual: (a, b, m) => strict.deepEqual(plain(a), plain(b), m),
});
const { loadModelJs } = require("./model_loader");

const Model = loadModelJs();
const NOW = 1790000000 * 1000;
const fresh = (o) => Object.assign({ state: "listening", title: "talk.md", line: 11, lines: 48, updated_at: 1790000000 - 1 }, o);

test("offlineStatus has the full default shape", () => {
  const s = Model.offlineStatus();
  assert.equal(s.state, "offline");
  assert.equal(s.lines, 0);
  assert.equal(s.opacity, 0.75);
  assert.equal(s.updated_at, 0);
});

test("parseStatus: missing, garbage and non-object text is offline", () => {
  for (const t of ["", "not json", undefined, null, "null", "42", "[]", '"x"']) {
    assert.deepEqual(Model.parseStatus(t), Model.offlineStatus());
  }
});

test("parseStatus: unknown state is offline; known states round-trip; fields filled", () => {
  assert.equal(Model.parseStatus('{"state":"bogus"}').state, "offline");
  for (const state of ["idle", "loaded", "starting", "listening", "paused", "no-audio", "error", "offline"]) {
    const s = Model.parseStatus(JSON.stringify({ state, title: "a.md" }));
    assert.equal(s.state, state);
    assert.equal(s.title, "a.md");
    assert.equal(s.lines, 0);
    assert.equal(s.opacity, 0.75);
  }
});

test("isStale: 15 s boundary, missing updated_at is stale", () => {
  assert.equal(Model.isStale(fresh({ updated_at: 1790000000 - 15 }), NOW), false);
  assert.equal(Model.isStale(fresh({ updated_at: 1790000000 - 15.5 }), NOW), true);
  assert.equal(Model.isStale(fresh({ updated_at: 0 }), NOW), true);
  assert.equal(Model.isStale(null, NOW), true);
});

test("uiState: stale and offline become offline; setup when doctor not ok", () => {
  assert.equal(Model.uiState(fresh(), NOW, null), "listening");
  assert.equal(Model.uiState(fresh({ updated_at: 1 }), NOW, null), "offline");
  assert.equal(Model.uiState(fresh({ state: "offline" }), NOW, { ok: true }), "offline");
  assert.equal(Model.uiState(fresh({ state: "offline" }), NOW, { ok: false }), "setup");
  assert.equal(Model.uiState(fresh({ updated_at: 1 }), NOW, { ok: false }), "setup");
  assert.equal(Model.uiState(fresh({ state: "paused" }), NOW, { ok: false }), "paused");
});

test("glyphFor and colorRoleFor match every row of the 5.1 table", () => {
  const rows = {
    offline: ["\u{F0BC3}", "dim"], setup: ["\u{F0493}", "amber"], idle: ["\u{F0BC3}", "fg"],
    loaded: ["\u{F0BC2}", "fg"], starting: ["\u{F1727}", "dim"], listening: ["\u{F1727}", "accent"],
    paused: ["\u{F1728}", "amber"], "no-audio": ["\u{F036D}", "urgent"], error: ["\u{F05D6}", "urgent"],
  };
  for (const [state, [g, c]] of Object.entries(rows)) {
    assert.equal(Model.glyphFor(state), g, state);
    assert.equal(Model.colorRoleFor(state), c, state);
  }
  assert.equal(Model.glyphFor("zzz"), "\u{F0BC3}");
  assert.equal(Model.colorRoleFor("zzz"), "dim");
});

test("progressLabel: line+1 over lines for script states, blank otherwise", () => {
  assert.equal(Model.progressLabel(fresh()), "12/48");
  assert.equal(Model.progressLabel(fresh({ state: "loaded" })), "12/48");
  assert.equal(Model.progressLabel(fresh({ state: "starting" })), "12/48");
  assert.equal(Model.progressLabel(fresh({ state: "paused" })), "12/48");
  assert.equal(Model.progressLabel(fresh({ state: "no-audio" })), "12/48");
  for (const state of ["offline", "idle", "error"]) assert.equal(Model.progressLabel(fresh({ state })), "");
  assert.equal(Model.progressLabel(fresh({ lines: 0 })), "");
  assert.equal(Model.progressLabel(fresh({ line: 99 })), "48/48");
});

test("headerTitle and headerSubtitle", () => {
  assert.equal(Model.headerTitle(fresh()), "talk.md");
  assert.equal(Model.headerTitle(fresh({ state: "idle" })), "No script loaded");
  assert.equal(Model.headerTitle(Model.offlineStatus()), "No script loaded");
  assert.equal(Model.headerSubtitle(fresh(), "listening"), "Line 12 of 48 · Listening");
  assert.equal(Model.headerSubtitle(fresh(), "paused"), "Line 12 of 48 · Paused");
  assert.equal(Model.headerSubtitle(fresh(), "offline"), "Not running");
  assert.equal(Model.headerSubtitle(fresh(), "setup"), "Not running");
  assert.equal(Model.headerSubtitle(fresh({ state: "idle" }), "idle"), "No script loaded");
  assert.equal(Model.headerSubtitle(fresh({ state: "error", message: "worker died" }), "error"), "worker died");
  assert.equal(Model.headerSubtitle(fresh({ state: "error", message: "" }), "error"), "Error");
});

test("tooltipFor", () => {
  assert.equal(Model.tooltipFor(fresh(), "listening", null), "PromptBro: talk.md, line 12 of 48, listening");
  assert.equal(Model.tooltipFor(fresh(), "paused", null), "PromptBro: talk.md, line 12 of 48, paused");
  assert.equal(Model.tooltipFor(Model.offlineStatus(), "offline", null), "PromptBro: not running");
  const d = { ok: false, problems: [{ id: "venv-missing", message: "PromptBro isn't set up yet" }] };
  assert.equal(Model.tooltipFor(Model.offlineStatus(), "setup", d), "PromptBro: PromptBro isn't set up yet");
  assert.equal(Model.tooltipFor(fresh({ message: "boom" }), "error", null), "PromptBro: boom");
});

test("canNavigate only while running with a script", () => {
  for (const s of ["loaded", "starting", "listening", "paused", "no-audio"]) assert.equal(Model.canNavigate(s), true, s);
  for (const s of ["offline", "setup", "idle", "error"]) assert.equal(Model.canNavigate(s), false, s);
});

test("parseRecent: shape, dedup, cap of 8, garbage", () => {
  const r = Model.parseRecent(JSON.stringify({ schema: 1, recent: [{ path: "/a/b/x.md", opened_at: 1 }, { path: "/c/y.txt" }] }));
  assert.deepEqual(r, [{ path: "/a/b/x.md", name: "x.md", dir: "/a/b" }, { path: "/c/y.txt", name: "y.txt", dir: "/c" }]);
  const dup = Model.parseRecent(JSON.stringify({ recent: [{ path: "/a/x.md" }, { path: "/a/x.md" }, { path: "/b/x.md" }] }));
  assert.equal(dup.length, 2);
  const many = { recent: Array.from({ length: 12 }, (_, i) => ({ path: "/d/f" + i + ".md" })) };
  const capped = Model.parseRecent(JSON.stringify(many));
  assert.equal(capped.length, 8);
  assert.equal(capped[0].name, "f0.md");
  for (const t of ["", "junk", undefined, "null", "{}", '{"recent":5}']) assert.deepEqual(Model.parseRecent(t), []);
  assert.deepEqual(Model.parseRecent(JSON.stringify({ recent: [null, 3, {}, { path: "" }, { path: 4 }, { path: "/ok.md" }] })).map((x) => x.path), ["/ok.md"]);
});

test("parseDoctor: normalizes, garbage is null", () => {
  const d = Model.parseDoctor(JSON.stringify({
    ok: false, problems: [{ id: "venv-missing", message: "m", fix: "setup" }],
    missing_packages: ["gtk4"], model: "m1", model_present: true, version: "0.3.0",
  }));
  assert.equal(d.ok, false);
  assert.deepEqual(d.problems, [{ id: "venv-missing", message: "m", fix: "setup" }]);
  assert.deepEqual(d.missing_packages, ["gtk4"]);
  assert.equal(d.model_present, true);
  assert.equal(Model.parseDoctor('{"ok":true,"problems":[]}').ok, true);
  assert.equal(Model.parseDoctor('{"ok":true,"problems":[{"id":"x"}]}').ok, false);
  for (const t of ["", "junk", undefined, "null", "7", "[]"]) assert.equal(Model.parseDoctor(t), null);
});

test("objectPathFor", () => {
  assert.equal(Model.objectPathFor("supercleanse.PromptBro"), "/supercleanse/PromptBro");
  assert.equal(Model.objectPathFor("a-b.c-d"), "/a_b/c_d");
});

test("activateArgs matches the argv in section 3.3", () => {
  assert.deepEqual(Model.activateArgs("supercleanse.PromptBro", "next"), [
    "gdbus", "call", "--session", "--dest", "supercleanse.PromptBro",
    "--object-path", "/supercleanse/PromptBro",
    "--method", "org.gtk.Actions.Activate", "next", "[]", "{}",
  ]);
  assert.equal(Model.activateArgs("supercleanse.PromptBro", "opacity", 0.8)[10], "[<0.8>]");
  assert.equal(Model.activateArgs("supercleanse.PromptBro", "opacity", 1)[10], "[<1.0>]");
  assert.equal(Model.activateArgs("supercleanse.PromptBro", "pause", undefined)[10], "[]");
});

test("shellQuote", () => {
  assert.equal(Model.shellQuote("a b"), "'a b'");
  assert.equal(Model.shellQuote("it's"), "'it'\\''s'");
  assert.equal(Model.shellQuote(""), "''");
});

test("clampOpacity", () => {
  assert.equal(Model.clampOpacity(0.1), 0.3);
  assert.equal(Model.clampOpacity(2), 1.0);
  assert.equal(Model.clampOpacity(0.5), 0.5);
  assert.equal(Model.clampOpacity("x"), 0.75);
});

test("inUse: only a visible running prompter, error, or setup keeps the widget revealed", () => {
  const st = (state, visible) => ({ state, visible });
  for (const s of ["starting", "listening", "paused", "no-audio"]) {
    assert.equal(Model.inUse(st(s, true), s), true, s + " visible");
    assert.equal(Model.inUse(st(s, false), s), false, s + " hidden");
  }
  assert.equal(Model.inUse(st("error", false), "error"), true);
  assert.equal(Model.inUse(st("offline", false), "setup"), true);
  assert.equal(Model.inUse(st("loaded", false), "loaded"), false);
  assert.equal(Model.inUse(st("idle", false), "idle"), false);
  assert.equal(Model.inUse(st("offline", false), "offline"), false);
  assert.equal(Model.inUse(null, "listening"), false);
});
