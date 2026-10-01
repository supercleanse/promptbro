"""Controller logic without a display: event dispatch, voice flag, coasting, pause, replay files."""
import gzip
import json
import time
from pathlib import Path

from promptbro.config import Config
from promptbro.controller import Controller, load_replay

ROOT = Path(__file__).resolve().parent.parent
SKIP = (ROOT / "tests" / "data" / "skip.md").read_text()


class FakeView:
    def __init__(self):
        self.positions, self.jumps, self.status = [], [], None

    def set_script(self, s): self.script = s
    def set_position(self, p): self.positions.append(p)
    def jump_to(self, line): self.jumps.append(line)
    def set_status(self, s): self.status = s
    def toast(self, t): pass


def make(tmp_path, monkeypatch, **aligner):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    cfg = Config()
    for k, v in aligner.items():
        setattr(cfg.aligner, k, v)
    c = Controller(FakeView(), cfg)
    c.load_text(SKIP, None)
    return c


def test_events_move_view_and_record_latency(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch)
    c._anchor = time.monotonic()
    for i, text in enumerate(["welcome", "welcome back", "welcome back to the show"]):
        c.handle_event({"type": "partial" if i < 2 else "final", "t": 0.2 * i, "text": text})
    assert c.pos.line == 0 and c.pos.unit == 4 and c.pos.committed_unit == 4
    assert c.view.positions[-1].unit == 4
    assert len(c.align_ms) == 3 and c.status()["latency"]["align_ms_p90"] < 5
    c.handle_event({"type": "level", "rms": 0.2, "t": 1.0})
    assert c.voice_active
    c.handle_event({"type": "level", "rms": 0.001, "t": 1.25})
    assert not c.voice_active


def test_pause_ignores_speech_and_keys_still_work(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch)
    c.set_paused(True)
    assert c.status()["state"] == "paused"
    c.handle_event({"type": "final", "t": 0.5, "text": "welcome back to the show"})
    assert c.pos.unit == -1
    c.next()
    assert c.pos.line == 1
    c.set_paused(False)
    assert c.status()["state"] != "paused"


def test_error_override_and_ready_clears_it(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch)
    c._set_override("error")
    assert c.status()["state"] == "error"
    c.handle_event({"type": "status", "state": "ready", "t": 0.0})
    assert c.status()["state"] != "error"


def test_display_coasting(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch, display_coasting=True)
    c._anchor = time.monotonic()
    c.handle_event({"type": "final", "t": 0.0, "text": "welcome back to the show"})
    assert c.pos.unit == 4
    c._wps, c.voice_active, c._level_t = 2.5, True, time.monotonic()
    c._last_move_t = time.monotonic() - 2.0          # idle 2 s with voice: coast ~3 units
    c._coast_tick()
    assert 5 <= c.pos.unit <= c.aligner.committed + 8
    assert c.aligner.provisional == 4                 # display only
    c.voice_active = False
    u = c.pos.unit
    c._last_move_t = time.monotonic() - 5.0
    c._coast_tick()
    assert c.pos.unit == u                            # no voice: no coasting
    c.restart()
    assert c.pos.unit == -1                           # a key clears the coast


def test_load_replay_formats(tmp_path):
    evs = [{"type": "final", "t": 1.0, "text": "b"}, {"type": "partial", "t": 0.5, "text": "a"}]
    p = tmp_path / "r.json.gz"
    with gzip.open(p, "wt") as f:
        json.dump({"events": evs, "start_unit": 3}, f)
    d = load_replay(str(p))
    assert d["start_unit"] == 3 and [e["t"] for e in d["events"]] == [0.5, 1.0]
    q = tmp_path / "r.jsonl"
    q.write_text("\n".join(json.dumps(e) for e in evs))
    assert len(load_replay(str(q))["events"]) == 2


def test_no_audio_while_listening_never_shows_tracking(tmp_path, monkeypatch):
    """Live bug: after resume the capture stayed dead but the dot said "tracking"."""
    c = make(tmp_path, monkeypatch)
    c.source = "mic"
    c.handle_event({"type": "status", "state": "ready", "t": 0.0})
    c.handle_event({"type": "partial", "t": 0.1, "text": "welcome"})
    assert c.status()["state"] == "tracking"
    c._listen_since = c._level_t = time.monotonic() - 5.0   # no level event for 5 s
    c._capture_health()
    st = c.status()
    assert st["state"] == "no-audio" and st["audio_age_s"] > 4
    c.handle_event({"type": "level", "rms": 0.02, "t": 5.0})  # audio is back
    c._capture_health()
    assert c.status()["state"] != "no-audio" and c.status()["audio_age_s"] < 1
    c.set_paused(True)                                       # paused: no audio expected
    c._listen_since = c._level_t = time.monotonic() - 5.0
    c._capture_health()
    assert c.status()["state"] == "paused" and c.status()["audio_age_s"] is None
