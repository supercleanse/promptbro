"""T5: status.json writer, state mapping, GActions over D-Bus, and quit leaving `offline`."""
import gzip
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from promptbro import statusfile
from promptbro.config import Config
from promptbro.controller import Controller

ROOT = Path(__file__).resolve().parent.parent
SKIP_MD = ROOT / "tests" / "data" / "skip.md"
SKIP = SKIP_MD.read_text()
FIXTURE = ROOT / "tests" / "fixtures" / "clean_200wpm.json.gz"


def fake_ctl(**kw):
    d = dict(script=SimpleNamespace(lines=[1, 2, 3]), _override=None, worker_state="running",
             visible=True, paused=False, path="/x/talk.md", worker_error="", last_toast="",
             pos=SimpleNamespace(line=1, state="tracking"),
             cfg=SimpleNamespace(window=SimpleNamespace(opacity=0.75)))
    d.update(kw)
    return SimpleNamespace(**d)


@pytest.mark.parametrize("kw,expected", [
    ({"script": None}, "idle"),
    ({"_override": "error"}, "error"),
    ({"worker_state": "failed"}, "error"),
    ({"_override": "no-mic"}, "no-audio"),
    ({"_override": "no-audio"}, "no-audio"),
    ({"visible": False}, "loaded"),
    ({"paused": True}, "paused"),
    ({"worker_state": "starting"}, "starting"),
    ({"worker_state": "restarting"}, "starting"),
    ({}, "listening"),
    # first match wins
    ({"script": None, "_override": "error"}, "idle"),
    ({"_override": "error", "visible": False}, "error"),
    ({"_override": "no-mic", "worker_state": "failed"}, "error"),
    ({"visible": False, "paused": True}, "loaded"),
    ({"paused": True, "worker_state": "starting"}, "paused"),
])
def test_widget_state_mapping(kw, expected):
    assert statusfile.widget_state(fake_ctl(**kw)) == expected


def test_build_fields():
    b = statusfile.build(fake_ctl(), "9.9")
    assert b == {"schema": 1, "state": "listening", "detail": "tracking", "path": "/x/talk.md",
                 "title": "talk.md", "line": 1, "lines": 3, "visible": True, "message": "",
                 "opacity": 0.75, "pid": os.getpid(), "version": "9.9"}
    pasted = statusfile.build(fake_ctl(path=None, last_toast="Script loaded"))
    assert pasted["title"] == "Clipboard text" and pasted["path"] == "" and pasted["message"] == "Script loaded"
    err = statusfile.build(fake_ctl(_override="error", worker_error="boom", last_toast="x"))
    assert err["state"] == "error" and err["detail"] == "error" and err["message"] == "boom"
    idle = statusfile.build(fake_ctl(script=None, path=None))
    assert idle["lines"] == 0 and idle["title"] == "" and idle["state"] == "idle"


class Sched:
    """A fake clock plus timers, so coalescing is tested without a main loop."""
    def __init__(self):
        self.now, self.timers, self.n = 1000.0, {}, 0

    def add(self, ms, fn):
        self.n += 1
        self.timers[self.n] = (self.now + ms / 1000, fn, ms)
        return self.n

    def remove(self, sid):
        self.timers.pop(sid, None)

    def advance(self, to):
        while True:
            due = sorted((t, sid) for sid, (t, _f, _m) in self.timers.items() if t <= to)
            if not due:
                break
            t, sid = due[0]
            _t, fn, ms = self.timers.pop(sid)
            self.now = t
            if fn():  # repeating
                self.timers[sid] = (t + ms / 1000, fn, ms)
        self.now = to


def make_writer(tmp_path, body):
    s = Sched()
    w = statusfile.StatusWriter(lambda: dict(body), path=tmp_path / "run" / "status.json",
                                add_timeout=s.add, remove_timeout=s.remove,
                                clock=lambda: s.now, mono=lambda: s.now)
    return w, s


def test_file_modes_and_atomic_replace(tmp_path):
    body = {"state": "idle"}
    w, s = make_writer(tmp_path, body)
    w.start()
    f = tmp_path / "run" / "status.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert stat.S_IMODE(f.parent.stat().st_mode) == 0o700
    assert json.loads(f.read_text())["updated_at"] == 1000.0
    assert sorted(p.name for p in f.parent.iterdir()) == ["status.json"]  # no temp left behind


def test_unchanged_poke_does_not_write_and_heartbeat_does(tmp_path):
    body = {"state": "idle"}
    w, s = make_writer(tmp_path, body)
    w.start()
    assert w.writes == 1
    s.advance(1001.0)
    w.poke()
    assert w.writes == 1
    s.advance(1006.0)  # a heartbeat fired at +5 s
    assert w.writes == 2
    assert json.loads(w.path.read_text())["updated_at"] > 1000.0


def test_coalesces_to_one_write_per_100ms(tmp_path):
    body = {"state": "idle", "line": 0}
    w, s = make_writer(tmp_path, body)
    w.start()
    s.advance(1001.0)
    for i in range(1, 50):  # 49 changes within 98 ms
        body["line"] = i
        s.advance(1001.0 + i * 0.002)
        w.poke()
    assert w.writes == 2            # the immediate one, then nothing yet (one trailing is pending)
    s.advance(1001.2)
    assert w.writes == 3
    assert json.loads(w.path.read_text())["line"] == 49  # the trailing write carries the last value


def test_close_writes_offline_and_stops(tmp_path):
    body = {"state": "listening"}
    w, s = make_writer(tmp_path, body)
    w.start()
    w.close("1.2")
    got = json.loads(w.path.read_text())
    assert got["state"] == "offline" and got["version"] == "1.2" and got["lines"] == 0
    n = w.writes
    w.poke()
    s.advance(1100.0)
    assert w.writes == n and not s.timers


def test_refuses_to_write_under_test_without_runtime_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("PROMPTBRO_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("PROMPTBRO_TEST", "1")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    w = statusfile.StatusWriter(lambda: {"state": "idle"})
    w.start()
    w.close()
    assert not w.enabled and not (tmp_path / "promptbro").exists()


def test_statusfile_and_recent_import_stdlib_only():
    probe = ("import sys; sys.path.insert(0, %r); import promptbro.statusfile, promptbro.recent\n"
             "bad = sorted({m.split('.')[0] for m in sys.modules} - set(sys.stdlib_module_names)"
             " - {'promptbro', '__main__'})\nprint('NONSTDLIB', ','.join(m for m in bad if not m.startswith('_')))"
             % str(ROOT))
    r = subprocess.run(["/usr/bin/python3", "-I", "-c", probe], capture_output=True, text=True, timeout=30)
    assert r.stdout.strip() == "NONSTDLIB", r.stdout + r.stderr


def test_replay_run_never_writes_more_than_10_per_second(tmp_path, monkeypatch):
    """The real Controller over the synthetic replay, with a simulated clock and timers."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    class View:
        def set_script(self, s): pass
        def set_position(self, p): pass
        def jump_to(self, line): pass
        def set_status(self, s): pass
        def toast(self, t): pass

    with gzip.open(FIXTURE, "rt") as f:
        data = json.load(f)
    events = sorted(data["events"], key=lambda e: e.get("t", 0))
    c = Controller(View(), Config())
    c.load_text(data["script"], None)
    s = Sched()
    stamps = []
    w = statusfile.StatusWriter(c.widget_status, path=tmp_path / "run" / "status.json",
                                add_timeout=s.add, remove_timeout=s.remove,
                                clock=lambda: s.now, mono=lambda: s.now)
    orig = w._write
    w._write = lambda body: (stamps.append(s.now), orig(body))[1]
    c.status_writer = w
    w.start()
    c.set_visible(True)
    for ev in events:
        s.advance(1000.0 + float(ev.get("t", 0)))
        c.handle_event(ev)
    s.advance(s.now + 1.0)
    assert len(stamps) > 10   # many line changes plus 4 heartbeats
    for i, t in enumerate(stamps):
        assert sum(1 for u in stamps if t <= u < t + 1.0) <= 10, (i, t)
    got = json.loads(w.path.read_text())
    assert got["line"] == c.pos.line and got["lines"] == len(c.script.lines)
    assert got["state"] == "listening"


# ---------------------------------------------------------------- app level (private bus + Broadway)

pytestmark_app = pytest.mark.skipif(shutil.which("gtk4-broadwayd") is None
                                    or shutil.which("dbus-run-session") is None,
                                    reason="needs gtk4-broadwayd and dbus-run-session")

GD = ("gdbus call --session --dest supercleanse.PromptBro.test --object-path /supercleanse/PromptBro/test "
      "--method org.gtk.Actions.Activate")


@pytestmark_app
def test_app_status_actions_recents_and_quit(tmp_path):
    from test_app_cli import _session
    run = tmp_path / "run"
    script = (
        "send --demo & PRIMARY=$!\n"   # no input source, so no worker or model is needed
        "sleep 3\n"
        "send open " + str(SKIP_MD) + "; sleep 0.6\n"
        "echo F0 $(cat \"$PROMPTBRO_RUNTIME_DIR/status.json\")\n"
        "echo S0 $(send status)\n"
        f"{GD} next '[]' '{{}}' >/dev/null; sleep 0.6\n"
        "echo F1 $(cat \"$PROMPTBRO_RUNTIME_DIR/status.json\")\n"
        "echo S1 $(send status)\n"
        f"{GD} pause '[]' '{{}}' >/dev/null; sleep 0.6\n"
        "echo F2 $(cat \"$PROMPTBRO_RUNTIME_DIR/status.json\")\n"
        f"{GD} opacity '[<0.5>]' '{{}}' >/dev/null; sleep 0.6\n"
        "echo F3 $(cat \"$PROMPTBRO_RUNTIME_DIR/status.json\")\n"
        f"{GD} hide '[]' '{{}}' >/dev/null; sleep 0.6\n"
        "echo F4 $(cat \"$PROMPTBRO_RUNTIME_DIR/status.json\")\n"
        f"{GD} quit '[]' '{{}}' >/dev/null; wait $PRIMARY\n"
        "echo F5 $(cat \"$PROMPTBRO_RUNTIME_DIR/status.json\")\n"
        # an action with no instance must not start one
        f"{GD} next '[]' '{{}}' 2>&1 | head -c 200; echo; echo RC=$?\n"
        "owned() { gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus "
        "--method org.freedesktop.DBus.NameHasOwner supercleanse.PromptBro.test; }\n"
        "owned\n"
    )
    r = _session(tmp_path, script, 0, {"PROMPTBRO_RUNTIME_DIR": str(run)})
    got = {}
    for ln in r.stdout.splitlines():
        tag, _, rest = ln.partition(" ")
        if tag in ("F0", "F1", "F2", "F3", "F4", "F5", "S0", "S1"):
            got[tag] = json.loads(rest)
    assert set(got) == {"F0", "F1", "F2", "F3", "F4", "F5", "S0", "S1"}, (r.stdout, r.stderr)
    assert got["F0"]["lines"] == 7 and got["F0"]["path"] == str(SKIP_MD) and got["F0"]["title"] == "skip.md"
    assert got["F0"]["state"] == "listening" and got["F0"]["visible"] is True
    assert got["F0"]["pid"] > 0 and got["F0"]["version"]
    assert got["F1"]["line"] == got["S1"]["line"] == got["S0"]["line"] + 1  # Activate next advanced
    assert got["F2"]["state"] == "paused"
    assert got["F3"]["opacity"] == 0.5
    assert got["F4"]["state"] == "loaded" and got["F4"]["visible"] is False
    assert got["F5"]["state"] == "offline" and got["F5"]["lines"] == 0
    assert "(false,)" in r.stdout and "(true,)" not in r.stdout  # no instance was launched
    assert stat.S_IMODE((run / "status.json").stat().st_mode) == 0o600
    recent = json.loads((tmp_path / "state" / "promptbro" / "recent.json").read_text())
    assert [e["path"] for e in recent["recent"]] == [str(SKIP_MD)]
    log = (tmp_path / "state" / "promptbro" / "promptbro.log").read_text()
    for name in ("next", "pause", "opacity", "hide", "quit"):
        assert f"action {name} (dbus)" in log
