#!/usr/bin/env python3
"""Record replay fixtures: TTS a misread variant of the test script, run it through the real
ASR worker, and save the raw event stream plus ground-truth word times (DESIGN 6.8).

    record_fixture.py [--only NAME ...] [--out-dir tests/fixtures] [--audio-dir tests/audio]
    record_fixture.py --mic NAME [--script tests/data/skip.md] [--seconds 60]

Synthetic mode (default) writes tests/fixtures/<name>.json.gz for each variant:
clean_160wpm, clean_200wpm, filler, repeat, skip_line, restart_sentence, silence_2s, and the
re-acquisition set on tests/data/reacquire.md (reacq_*: skip a line mid-sentence, skip two lines,
paraphrase, ad-lib and rejoin at the cursor or two lines ahead, a line that shares a phrase).
Each variant is a list of spoken segments. A segment is spoken text plus, per word, the
script unit it covers (or None for a filler/stumble). The default TTS is the sherpa-onnx piper
voice amy-low (scripts/tts_wav.py; `--engine espeak` uses espeak-ng en-us+m2 instead, but the
NeMo model garbles most of its speech). Each segment gets 300 ms of leading silence, segments are
joined with silence, and the WAV is deterministic (same engine + voice -> byte-identical WAV). Truth word
times come from the segment boundaries: the speech bounds inside each segment are found by
energy, and the words are spread across them by letter count. The event stream comes from
`asr_worker --wav --fast`, so its `t` is audio time; it can differ slightly between runs and
is re-recorded only on purpose.

--mic is the hook for a human recording (needs the speaker's consent; never run by CI): it
runs the worker on the default microphone while you read the script and saves the events to
tests/fixtures/private/<NAME>.json.gz (git-ignored). Such a file has no `truth`; add one by
hand (same shape as below) before tests/test_fixtures_replay.py will score it.

Fixture format (gzip JSON, mtime 0):
  {"name", "source", "script", "start_unit", "tts": {...},
   "events": [{"type","utt","t","text"}...],          # partial and final only
   "truth":  [{"unit","start","end"}...]}             # seconds of audio, script units spoken
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from promptbro.script import load_text  # noqa: E402

RATE = 16000
VOICE = "en-us+m2"            # espeak-ng voice (--engine espeak)
PIPER_VOICE = Path.home() / ".local/share/promptbro/tts/vits-piper-en_US-amy-low"
LEAD_MS = 300
ENGINE = "piper"              # piper transcribes far better than espeak's garbled m2 voice
# natural amy-low pace is about 150 wpm on this script; speed scales it to the target rate
PIPER_BASE_WPM = 150.0
_TTS = None
SCRIPT_PATH = ROOT / "tests" / "data" / "skip.md"
PY = sys.executable

# ---------------------------------------------------------------- variants
# Segment = (text, [unit or None per word]). Lines are whitespace-word == unit, asserted below.


REACQ_PATH = ROOT / "tests" / "data" / "reacquire.md"


def _line(i: int, lo: int = 0, hi: int | None = None, path: Path = SCRIPT_PATH):
    """Words lo:hi of script line i, as a segment."""
    text = path.read_text().splitlines()[i]
    words = text.split()
    sc = load_text(path.read_text())
    first = sc.line_first_unit[i]
    n = sc.line_last_unit[i] - first + 1
    assert len(words) == n, f"line {i}: {len(words)} words vs {n} units"
    hi = n if hi is None else hi
    return " ".join(words[lo:hi]), [first + k for k in range(lo, hi)]


def _with_fillers(i: int, fillers: dict[int, str]):
    """Line i with a filler word spoken before word k (k -> 'um')."""
    text, units = _line(i)
    words, out_w, out_u = text.split(), [], []
    for k, w in enumerate(words):
        if k in fillers:
            out_w.append(fillers[k])
            out_u.append(None)
        out_w.append(w)
        out_u.append(units[k])
    return " ".join(out_w), out_u


def variants() -> dict[str, dict]:
    all_lines = [_line(i) for i in range(7)]
    v = {
        "clean_160wpm": dict(rate=160, gaps=0.6, segs=all_lines),
        "clean_200wpm": dict(rate=200, gaps=0.6, segs=all_lines),
        # um / uh in the middle of lines 1, 3 and 5
        "filler": dict(rate=160, gaps=0.6, segs=[
            all_lines[0], _with_fillers(1, {4: "um"}), all_lines[2], _with_fillers(3, {3: "uh"}),
            all_lines[4], _with_fillers(5, {3: "um"}), all_lines[6]]),
        # the speaker reads line 4 twice
        "repeat": dict(rate=160, gaps=0.6, segs=all_lines[:5] + [all_lines[4]] + all_lines[5:]),
        # the speaker skips line 2 ("The plan costs $79 a month.")
        "skip_line": dict(rate=160, gaps=0.6, segs=all_lines[:2] + all_lines[3:]),
        # starts line 3, stumbles after three words, and restarts the sentence
        "restart_sentence": dict(rate=160, gaps=0.6, segs=all_lines[:3] + [
            _line(3, 0, 3), all_lines[3]] + all_lines[4:]),
        # a 2 s pause in the middle of line 4
        "silence_2s": dict(rate=160, gaps=0.6, segs=all_lines[:4] + [
            _line(4, 0, 5), _line(4, 5)] + all_lines[5:], long_gap_before=5, long_gap=2.0),
    }
    v.update(reacquire_variants())
    return v


def _cat(*segs):
    """Several segments spoken as one, without a pause (the speaker skips mid-sentence)."""
    return " ".join(t for t, _ in segs), [u for _, us in segs for u in us]


def _say(text: str):
    """Words that are not in the script (an ad-lib or a paraphrase's extra words)."""
    return text, [None] * len(text.split())


def reacquire_variants() -> dict[str, dict]:
    """Going off script and coming back (tests/data/reacquire.md). ``rejoin_unit`` is the
    first script word spoken after the departure; the replay test counts the words spoken
    from there until the cursor gets to it."""
    L = lambda i, lo=0, hi=None: _line(i, lo, hi, REACQ_PATH)  # noqa: E731
    sc = load_text(REACQ_PATH.read_text())
    first = sc.line_first_unit
    para_text, para_units = L(9)                 # "Our customers will feel the difference first."
    w = para_text.split()
    paraphrase = (" ".join(w[:2] + ["are", "going", "to"] + w[3:]),
                  para_units[:2] + [None, None, None] + para_units[3:])
    adlib = _say("And honestly that was a really tough call for all of us.")
    base = dict(rate=160, gaps=0.6, script=REACQ_PATH)
    return {
        # mid-way through line 3 the speaker drops the rest of it and line 4, and reads line 5
        "reacq_skip1_mid": dict(base, start_unit=first[2] - 1, rejoin_unit=first[5],
                                segs=[L(2), _cat(L(3, 0, 3), L(5)), L(6)]),
        # after line 1, lines 2 and 3 are skipped
        "reacq_skip2": dict(base, start_unit=-1, rejoin_unit=first[4],
                            segs=[L(0), L(1), L(4), L(5)]),
        # line 9 paraphrased, then line 10 as written
        "reacq_paraphrase": dict(base, start_unit=first[8] - 1, rejoin_unit=first[10],
                                 segs=[L(8), paraphrase, L(10), L(11)]),
        # a 10-word ad-lib, then back at the cursor
        "reacq_adlib_rejoin": dict(base, start_unit=first[3] - 1, rejoin_unit=first[5],
                                   segs=[L(3), L(4), adlib, L(5), L(6)]),
        # an ad-lib, then two lines ahead of the cursor
        "reacq_adlib_skip2": dict(base, start_unit=first[3] - 1, rejoin_unit=first[7],
                                  segs=[L(3), L(4), adlib, L(7), L(8)]),
        # "We are building" from line 6, then line 7, which starts with the same three words
        "reacq_shared_phrase": dict(base, start_unit=first[5] - 1, rejoin_unit=first[7] + 3,
                                    segs=[L(5), _cat(L(6, 0, 3), L(7)), L(8)]),
    }


# ---------------------------------------------------------------- TTS


def segment_audio(text: str, rate: int, tmp: Path) -> np.ndarray:
    """One segment -> float32 mono 16 kHz (300 ms lead-in included), from the chosen engine."""
    if ENGINE == "piper":
        global _TTS
        import tts_wav
        if _TTS is None:
            _TTS = tts_wav.load_tts(PIPER_VOICE)
        a = _TTS.generate(text, sid=0, speed=rate / PIPER_BASE_WPM)
        x = tts_wav.resample(np.asarray(a.samples, dtype=np.float32), a.sample_rate, RATE)
        return np.concatenate([np.zeros(int(LEAD_MS / 1000 * RATE), dtype=np.float32), x])
    return espeak_segment(text, rate, tmp)


def espeak_segment(text: str, rate: int, tmp: Path) -> np.ndarray:
    """One segment via espeak-ng -> float32 mono 16 kHz (300 ms lead-in included)."""
    ssml = f'<speak><break time="{LEAD_MS}ms"/>{text}</speak>'
    raw = tmp / "seg.wav"
    subprocess.run(["espeak-ng", "-v", VOICE, "-s", str(rate), "-m", "-w", str(raw), ssml],
                   check=True, capture_output=True)
    with wave.open(str(raw)) as w:
        sr, data = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    x = data.astype(np.float32) / 32768.0
    if sr != RATE:
        n = int(round(len(x) * RATE / sr))
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return x


def speech_bounds(x: np.ndarray, thresh: float = 0.01) -> tuple[float, float]:
    """(start, end) seconds of the non-silent part, by 10 ms RMS frames."""
    hop = RATE // 100
    n = len(x) // hop
    rms = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1))
    idx = np.nonzero(rms > thresh)[0]
    if len(idx) == 0:
        return 0.0, len(x) / RATE
    return idx[0] * hop / RATE, (idx[-1] + 1) * hop / RATE


def _weight(w: str) -> float:
    letters = sum(c.isalpha() for c in w)
    other = sum(not c.isalpha() and c.isalnum() for c in w)   # digits are spoken as several words
    return max(3.0, letters + 2.5 * other + (3.0 if "$" in w or "%" in w else 0.0))


def render(spec: dict, tmp: Path) -> tuple[np.ndarray, list[dict]]:
    parts: list[np.ndarray] = []
    truth: list[dict] = []
    t = 0.0
    for si, (text, units) in enumerate(spec["segs"]):
        x = segment_audio(text, spec["rate"], tmp)
        s0, s1 = speech_bounds(x)
        words = text.split()
        assert len(words) == len(units)
        wts = np.array([_weight(w) for w in words])
        edges = s0 + (s1 - s0) * np.concatenate([[0.0], np.cumsum(wts) / wts.sum()])
        for k, u in enumerate(units):
            if u is not None:
                truth.append({"unit": u, "start": round(t + edges[k], 3), "end": round(t + edges[k + 1], 3)})
        parts.append(x)
        t += len(x) / RATE
        if si < len(spec["segs"]) - 1:
            gap = spec["gaps"]
            if spec.get("long_gap_before") == si + 1:
                gap = spec["long_gap"]
            parts.append(np.zeros(int(gap * RATE), dtype=np.float32))
            t += int(gap * RATE) / RATE
    parts.append(np.zeros(int(0.5 * RATE), dtype=np.float32))
    return np.concatenate(parts), truth


def write_wav(x: np.ndarray, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


# ---------------------------------------------------------------- worker + output


def run_worker(args: list[str], stdin=None, timeout=None) -> list[dict]:
    p = subprocess.run([PY, "-m", "promptbro.asr_worker", *args], cwd=ROOT, capture_output=True,
                       text=True, timeout=timeout, stdin=stdin)
    ev = [json.loads(line) for line in p.stdout.splitlines() if line.startswith("{")]
    if not any(e.get("type") == "status" and e.get("state") == "ready" for e in ev):
        raise SystemExit(f"worker never became ready:\n{p.stderr[-800:]}")
    return ev


def slim(events: list[dict]) -> list[dict]:
    return [{"type": e["type"], "utt": e.get("utt", 0), "t": e["t"], "text": e["text"]}
            for e in events if e.get("type") in ("partial", "final")]


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0, filename="") as g:
        g.write(json.dumps(data, separators=(",", ":")).encode())


def record_synthetic(names: list[str] | None, out_dir: Path, audio_dir: Path, model: str | None = None) -> None:
    import tempfile
    specs = variants()
    for name, spec in specs.items():
        if names and name not in names:
            continue
        with tempfile.TemporaryDirectory() as td:
            x, truth = render(spec, Path(td))
        wav = audio_dir / f"fx_{name}.wav"
        digest = write_wav(x, wav)
        raw = run_worker(["--wav", str(wav), "--fast", *(["--model", model] if model else [])])
        asr = next(e.get("model") for e in raw if e.get("type") == "status" and e.get("state") == "ready")
        events = slim(raw)
        script = Path(spec.get("script", SCRIPT_PATH)).read_text()
        data = {"name": name, "source": "scripts/record_fixture.py", "script": script,
                "start_unit": spec.get("start_unit", -1), "tts": {"engine": ENGINE, "voice": VOICE if ENGINE == "espeak" else PIPER_VOICE.name, "rate": spec["rate"],
                                           "lead_ms": LEAD_MS, "wav_sha256_16": digest},
                "asr": asr, "events": events, "truth": truth}
        if "rejoin_unit" in spec:
            data["rejoin_unit"] = spec["rejoin_unit"]
        path = out_dir / f"{name}.json.gz"
        save(path, data)
        print(f"{name}: {len(x) / RATE:.1f} s audio, {len(events)} events, wav {digest}, "
              f"{path.stat().st_size} bytes")


def record_mic(name: str, script: Path, seconds: float) -> None:
    print(f"Recording {seconds:.0f} s from the default microphone. Read {script} now. "
          "Stops on its own (or Ctrl-C).", file=sys.stderr)
    try:
        ev = run_worker(["--device", ""], stdin=subprocess.DEVNULL, timeout=seconds)
    except subprocess.TimeoutExpired as e:
        ev = [json.loads(x) for x in (e.stdout or b"").decode().splitlines() if x.startswith("{")]
    data = {"name": name, "source": "mic", "script": script.read_text(), "start_unit": -1,
            "events": slim(ev)}
    path = ROOT / "tests" / "fixtures" / "private" / f"{name}.json.gz"
    save(path, data)
    print(f"{path} ({len(data['events'])} events; no truth - add one before scoring)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", nargs="*", help="variant names to record (default: all)")
    ap.add_argument("--out-dir", default=str(ROOT / "tests" / "fixtures"))
    ap.add_argument("--audio-dir", default=str(ROOT / "tests" / "audio"))
    ap.add_argument("--engine", choices=["piper", "espeak"], default="piper")
    ap.add_argument("--mic", metavar="NAME", help="record a human reading from the microphone")
    ap.add_argument("--script", default=str(SCRIPT_PATH))
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--model", help="ASR model dir (default: the configured default model)")
    a = ap.parse_args(argv)
    global ENGINE
    ENGINE = a.engine
    if a.mic:
        record_mic(a.mic, Path(a.script), a.seconds)
    else:
        record_synthetic(a.only, Path(a.out_dir), Path(a.audio_dir), a.model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
