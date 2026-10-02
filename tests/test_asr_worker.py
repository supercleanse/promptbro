"""ASR worker tests. Model tests skip cleanly when the model is not downloaded; the unit tests
(watchdog, locking, backlog) use a fake pipeline and need neither the model nor GStreamer.
The microphone is never opened: live tests use a GStreamer test source through the mic code path."""
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from promptbro import asr_worker, models
from promptbro.config import ASRConfig

ROOT = Path(__file__).resolve().parent.parent
CFG = ASRConfig()
MODEL_DIR = Path(CFG.models_dir).expanduser() / CFG.model
WELCOME = ROOT / "tests" / "audio" / "keep" / "welcome.wav"

needs_model = pytest.mark.skipif(not models.verify(CFG.model, CFG.models_dir), reason="ASR model not downloaded")


def wer(ref: str, hyp: str) -> float:
    r, h = ref.lower().split(), hyp.lower().split()
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(len(r), 1)


def run_worker(wav, *extra, timeout=20):
    p = subprocess.run(
        [sys.executable, "-m", "promptbro.asr_worker", "--wav", str(wav), *extra],
        cwd=ROOT, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
    )
    return p.returncode, [json.loads(line) for line in p.stdout.splitlines() if line.strip()]


def check_stream(events, reference):
    assert events[0]["type"] == "status" and events[0]["state"] == "ready"
    assert any(e["type"] == "partial" for e in events)
    finals = [e for e in events if e["type"] == "final"]
    assert finals
    text = " ".join(f["text"] for f in finals)
    norm = lambda s: re.sub(r"[^a-z' ]", "", s.lower())
    assert wer(norm(reference), norm(text)) <= 0.25
    ts = [w["t"] for f in finals for w in f["words"]]
    assert ts == sorted(ts)


@needs_model
def test_model_wav_fast():
    wav = MODEL_DIR / "test_wavs" / "0.wav"
    ref = (MODEL_DIR / "test_wavs" / "trans.txt").read_text().splitlines()[0].split(" ", 1)[1]
    t0 = time.monotonic()
    rc, events = run_worker(wav, "--fast")
    assert rc == 0 and time.monotonic() - t0 < 10
    check_stream(events, ref)


@needs_model
@pytest.mark.skipif(not WELCOME.exists(), reason="tests/audio/keep/welcome.wav not generated (needs espeak-ng)")
def test_welcome_wav():
    t0 = time.monotonic()
    rc, events = run_worker(WELCOME, "--fast")
    assert rc == 0 and time.monotonic() - t0 < 10
    check_stream(events, "welcome back to the show")


@needs_model
def test_silence_yields_no_final(tmp_path):
    wav = tmp_path / "silence.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 32000)
    rc, events = run_worker(wav, "--fast")
    assert rc == 0
    assert events[0]["state"] == "ready"
    assert not [e for e in events if e["type"] == "final" and e["text"].strip()]


@needs_model
def test_pause_stops_events():
    wav = MODEL_DIR / "test_wavs" / "0.wav"
    p = subprocess.Popen(
        [sys.executable, "-m", "promptbro.asr_worker", "--wav", str(wav), "--realtime"],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        first = p.stdout.readline()
        assert json.loads(first)["state"] == "ready"
        p.stdin.write("pause\n")
        p.stdin.flush()
        seen_pause = False
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            line = p.stdout.readline()
            if json.loads(line).get("state") == "paused":
                seen_pause = True
                break
        assert seen_pause
        import select
        r, _, _ = select.select([p.stdout], [], [], 1.0)
        assert not r, "events arrived after pause: " + p.stdout.readline()
    finally:
        p.terminate()
        p.wait(timeout=5)


# ---- live (mic-mode) capture: pause/resume must bring audio back ------------------------------
# A live GStreamer test source goes through exactly the mic code path (pipeline states, sample
# callback, watchdog); only the source element differs from pipewiresrc.
LIVE = "audiotestsrc is-live=true wave=sine freq=440 volume=0.2"


class _Live:
    def __init__(self, *extra, source=LIVE):
        import queue
        self.p = subprocess.Popen(
            [sys.executable, "-m", "promptbro.asr_worker", "--live-source", source, *extra],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.q = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.p.stdout:
            self.q.put(json.loads(line))

    def send(self, cmd):
        self.p.stdin.write(cmd + "\n")
        self.p.stdin.flush()

    def wait_for(self, pred, timeout):
        import queue
        deadline = time.monotonic() + timeout
        seen = []
        while time.monotonic() < deadline:
            try:
                ev = self.q.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            seen.append(ev)
            if pred(ev):
                return ev, seen
        return None, seen

    def close(self):
        self.p.terminate()
        self.p.wait(timeout=5)


def _level(ev):
    return ev["type"] == "level"


@needs_model
def test_live_capture_resumes_after_pause():
    """Regression (live bug): after pause -> resume the capture must deliver audio again."""
    w = _Live()
    try:
        assert w.wait_for(lambda e: e.get("state") == "ready", 30)[0]
        assert w.wait_for(_level, 3)[0], "no audio before pause"
        w.send("pause")
        assert w.wait_for(lambda e: e.get("state") == "paused", 3)[0]
        ev, seen = w.wait_for(_level, 1.0)
        assert ev is None, "audio events while paused"
        for _ in range(2):  # twice: the second resume must work as well as the first
            w.send("resume")
            assert w.wait_for(lambda e: e.get("state") == "resumed", 3)[0]
            ev, seen = w.wait_for(_level, 2.0)
            assert ev is not None, f"no audio after resume: {seen}"
            assert ev["rms"] > 0.05
            w.send("pause")
            assert w.wait_for(lambda e: e.get("state") == "paused", 3)[0]
    finally:
        w.close()


@needs_model
def test_live_watchdog_restarts_a_stalled_capture():
    """A source that goes quiet while listening is restarted, and audio comes back."""
    w = _Live("--simulate-stall")
    try:
        assert w.wait_for(lambda e: e.get("state") == "ready", 30)[0]
        w.send("pause")
        assert w.wait_for(lambda e: e.get("state") == "paused", 3)[0]
        w.send("resume")  # after this the simulated source delivers nothing
        ev, seen = w.wait_for(lambda e: e.get("state") == "warning", 6)
        assert ev and "restarting capture" in ev["message"], seen
        assert w.wait_for(_level, 2.0)[0], "no audio after the watchdog restart"
    finally:
        w.close()


@needs_model
def test_live_source_without_audio_gives_up():
    """#6 regression: a live source that never produces samples is restarted NO_AUDIO_TRIES
    times, then the worker reports the error and exits 1 (so the controller can start a fresh
    process), instead of restarting the same pipeline forever."""
    silent = ("appsrc is-live=true format=time "
              "caps=audio/x-raw,format=F32LE,rate=16000,channels=1,layout=interleaved")
    w = _Live(source=silent)
    try:
        assert w.wait_for(lambda e: e.get("state") == "ready", 30)[0]
        ev, seen = w.wait_for(lambda e: e.get("state") == "error", 20)
        assert ev and ev["message"] == "no audio from the microphone", seen
        warnings = [e for e in seen if e.get("state") == "warning"]
        assert len(warnings) == asr_worker.NO_AUDIO_TRIES, seen
        assert w.p.wait(timeout=5) == 1
    finally:
        if w.p.poll() is None:
            w.close()


# ---- M2: user values never enter the GStreamer launch string -----------------------------------
@needs_model
def test_wav_path_with_quotes_and_bang(tmp_path):
    wav = tmp_path / 'a "b" ! fakesink name=evil c.wav'
    shutil.copy(MODEL_DIR / "test_wavs" / "0.wav", wav)
    rc, events = run_worker(wav, "--fast")
    assert rc == 0
    assert events[0]["type"] == "status" and events[0]["state"] == "ready"
    assert events[-1]["type"] == "status" and events[-1]["state"] == "eof"


def _gst_has(factory):
    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        return Gst.ElementFactory.find(factory) is not None
    except (ImportError, ValueError):
        return False


@pytest.mark.skipif(not _gst_has("pipewiresrc"), reason="pipewiresrc not installed")
def test_device_name_is_a_property_not_pipeline_text(monkeypatch):
    """The pipeline is only built, never started (a pipewiresrc in NULL state opens nothing)."""
    from promptbro import audiosel
    device = 'alsa_input.x" ! fakesink name=evil target-object="y'
    monkeypatch.setattr(audiosel, "select", lambda configured, *a, **k: (configured, "pinned"))
    w = asr_worker.Worker(asr_worker.parse_args(["--device", device]))
    w.build_pipeline()
    try:
        assert w.pipeline.get_by_name("mic").get_property("target-object") == device
        assert w.pipeline.get_by_name("evil") is None
    finally:
        w.pipeline.set_state(w.Gst.State.NULL)


# ---- unit: watchdog, lock ordering and backlog with a fake pipeline -----------------------------
class _FakeGst:
    class State:
        NULL, PAUSED, PLAYING = "NULL", "PAUSED", "PLAYING"


class _FakePipeline:
    def __init__(self):
        self.states = []

    def set_state(self, state):
        self.states.append(state)


def _unit_worker(*argv, source=True):
    args = asr_worker.parse_args((["--live-source", "fake"] if source else []) + list(argv))
    w = asr_worker.Worker(args)
    w.pipeline, w.Gst = _FakePipeline(), _FakeGst
    w.events = []
    w.status = lambda state, **kw: w.events.append((state, kw))
    clock = [1000.0]
    w.clock = lambda: clock[0]
    w.last_audio = w.last_restart = clock[0]
    return w, clock


def _run_silent(w, clock, seconds, step=0.1):
    """Tick the watchdog every `step` s of fake time with no samples; True if it asked to stop."""
    for _ in range(int(seconds / step)):
        clock[0] += step
        if w._watchdog_tick(clock[0]):
            return True
    return False


def _states(w, name):
    return [e for e in w.events if e[0] == name]


def test_watchdog_gives_up_after_consecutive_silent_restarts():
    """#6: the restart itself is not audio; four silent periods in a row -> error + stop."""
    w, clock = _unit_worker()
    assert _run_silent(w, clock, 20)
    assert len(_states(w, "warning")) == asr_worker.NO_AUDIO_TRIES
    assert _states(w, "error") == [("error", {"message": "no audio from the microphone"})]
    assert w.pipeline.states == ["NULL", "PLAYING"] * asr_worker.NO_AUDIO_TRIES


def test_watchdog_count_resets_when_samples_follow_a_restart():
    w, clock = _unit_worker()
    assert not _run_silent(w, clock, 3.5)  # first silent period -> restart 1
    assert not _run_silent(w, clock, 3.1)  # restart 2
    assert w.dead_tries == 2
    clock[0] += 0.05
    w.last_audio = clock[0]  # samples arrive after the restart
    assert not w._watchdog_tick(clock[0] + 0.05)
    assert w.dead_tries == 0
    # silent again: a full NO_AUDIO_TRIES restarts are allowed before giving up
    assert _run_silent(w, clock, 20)
    assert len(_states(w, "warning")) == 2 + asr_worker.NO_AUDIO_TRIES


def test_watchdog_steady_audio_never_restarts():
    w, clock = _unit_worker()
    for _ in range(200):
        clock[0] += 0.1
        w.last_audio = clock[0]
        assert not w._watchdog_tick(clock[0])
    assert w.pipeline.states == [] and w.events == []


def test_pause_racing_a_watchdog_restart_keeps_the_device_released():
    """#7: a pause that lands between the watchdog's check and its restart wins: the last state
    is NULL and nothing sets PLAYING after it. The warning is emitted before pipe_lock is taken,
    so the synchronous pause below would deadlock (non-reentrant lock) if it were not."""
    w, clock = _unit_worker()
    record = w.status

    def status(state, **kw):
        record(state, **kw)
        if state == "warning":
            w.handle_command("pause")

    w.status = status
    clock[0] += asr_worker.NO_AUDIO_S + 0.1
    t = threading.Thread(target=lambda: setattr(w, "stop", w._watchdog_tick(clock[0])), daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "watchdog deadlocked against pause"
    assert w.stop is False and w.paused
    assert w.pipeline.states == ["NULL"]
    assert [e[0] for e in w.events] == ["warning", "paused"]
    # while paused the watchdog stays quiet, however long the pause
    assert not _run_silent(w, clock, 30)
    assert w.pipeline.states == ["NULL"]


def test_resume_after_quit_does_not_restart_capture():
    w, clock = _unit_worker()
    w.handle_command("pause")
    w.quit.set()
    w.handle_command("resume")
    assert w.pipeline.states == ["NULL"] and w.paused


def test_resume_restarts_and_resets_the_watchdog():
    w, clock = _unit_worker()
    w.dead_tries = 2
    w.handle_command("pause")
    clock[0] += 60
    w.handle_command("resume")
    assert w.pipeline.states == ["NULL", "PLAYING"] and not w.paused
    assert w.dead_tries == 0 and w.last_restart == clock[0]
    assert not w._watchdog_tick(clock[0] + 1.0)  # a fresh NO_AUDIO_S window after resume


def test_live_backlog_is_capped_with_one_warning_per_episode():
    """#9: live input keeps at most MAX_BACKLOG_S of blocks queued, dropping the oldest."""
    w, _ = _unit_worker()
    cap = w.max_backlog_blocks
    assert cap == int(asr_worker.MAX_BACKLOG_S * 1000 / asr_worker.BLOCK_MS)
    w._enqueue(np.arange(w.block * (cap + 100), dtype=np.float32), 1.0)
    assert w.q.qsize() <= cap
    first = w.q.get_nowait()[0]
    assert first[0] == w.block * 100  # the oldest blocks were the ones dropped
    for _ in range(10):
        w._enqueue(np.zeros(w.block * 50, dtype=np.float32), 2.0)
        assert w.q.qsize() <= cap
    assert len(_states(w, "warning")) == 1
    w._drain_queue()
    w._enqueue(np.zeros(w.block, dtype=np.float32), 3.0)  # caught up: the episode ends
    w._enqueue(np.zeros(w.block * (cap + 10), dtype=np.float32), 4.0)
    assert len(_states(w, "warning")) == 2


def test_wav_backlog_is_unbounded():
    w, _ = _unit_worker("--wav", "x.wav", source=False)
    n = w.max_backlog_blocks + 100
    w._enqueue(np.zeros(w.block * n, dtype=np.float32), 1.0)
    assert w.q.qsize() == n and w.events == []
