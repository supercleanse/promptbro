"""ASR worker tests. Skip cleanly when the model is not downloaded. The mic path is not tested here."""
import json
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

import pytest

from promptbro import models
from promptbro.config import ASRConfig

ROOT = Path(__file__).resolve().parent.parent
CFG = ASRConfig()
MODEL_DIR = Path(CFG.models_dir).expanduser() / CFG.model
WELCOME = ROOT / "tests" / "audio" / "keep" / "welcome.wav"

pytestmark = pytest.mark.skipif(not models.verify(CFG.model, CFG.models_dir), reason="ASR model not downloaded")


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


def test_model_wav_fast():
    wav = MODEL_DIR / "test_wavs" / "0.wav"
    ref = (MODEL_DIR / "test_wavs" / "trans.txt").read_text().splitlines()[0].split(" ", 1)[1]
    t0 = time.monotonic()
    rc, events = run_worker(wav, "--fast")
    assert rc == 0 and time.monotonic() - t0 < 10
    check_stream(events, ref)


@pytest.mark.skipif(not WELCOME.exists(), reason="tests/audio/keep/welcome.wav not generated (needs espeak-ng)")
def test_welcome_wav():
    t0 = time.monotonic()
    rc, events = run_worker(WELCOME, "--fast")
    assert rc == 0 and time.monotonic() - t0 < 10
    check_stream(events, "welcome back to the show")


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
    def __init__(self, *extra):
        import queue
        import threading
        self.p = subprocess.Popen(
            [sys.executable, "-m", "promptbro.asr_worker", "--live-source", LIVE, *extra],
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
