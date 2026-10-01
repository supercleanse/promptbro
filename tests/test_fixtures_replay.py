"""Replay recorded real-ASR event streams through the aligner and score them (DESIGN 6.8).

The fixtures in tests/fixtures/*.json.gz come from scripts/record_fixture.py: espeak-ng reads the
test script with a misread (filler, repeat, skipped line, restart, pause), the real ASR worker
transcribes it, and `truth` holds when each spoken script word ended. No model, mic or display is
needed here. Hard limits: overshoot == 0, false_jumps == 0, final_error <= 2 units. Lag and stall
are printed (run with -s) and only bounded loosely.
"""
import gzip
import json
import sys
from pathlib import Path

import pytest

from promptbro.aligner import Aligner, Event
from promptbro.script import load_text

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
# tests/fixtures/*.json.gz: recorded with the default model (Nemotron 0.6B 160 ms);
# tests/fixtures/fc80/: the same audio through the former default (NeMo fast-conformer 80 ms).
# Both sets must pass: the aligner has to hold up on either engine's output.
SETS = ["", "fc80"]
NAMES = ["clean_160wpm", "clean_200wpm", "filler", "repeat", "skip_line", "restart_sentence",
         "silence_2s"]
CLEAN = {"clean_160wpm", "clean_200wpm"}
# Going off script and coming back (tests/data/reacquire.md, default model only). Each carries
# `rejoin_unit`: the first script word spoken after the departure.
REACQ = ["reacq_skip1_mid", "reacq_skip2", "reacq_paraphrase", "reacq_adlib_rejoin",
         "reacq_adlib_skip2", "reacq_shared_phrase"]
REACQ_MAX_WORDS = 5     # the author: catch up within about three to five spoken words


def load(path: Path) -> dict:
    with gzip.open(path, "rt") as f:
        return json.load(f)


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float("nan")


def replay(data: dict) -> dict:
    """Run the events through the aligner; return the section 6.8 metrics."""
    script = load_text(data["script"])
    al = Aligner(script, start_unit=data.get("start_unit", -1))
    truth = sorted(data["truth"], key=lambda r: r["end"])
    ends = [r["end"] for r in truth]

    def truth_unit(t):          # furthest script unit whose spoken end is <= t (-1 before any)
        u = data.get("start_unit", -1)
        for r in truth:
            if r["end"] <= t:
                u = max(u, r["unit"])
            else:
                break
        return u

    track = []                  # (t, provisional, committed, truth)
    overshoot = false_jumps = 0
    prev = al.position
    for e in data["events"]:
        if e["type"] not in ("partial", "final"):
            continue
        pos = al.on_event(Event.from_dict(e))
        tr = truth_unit(e["t"])
        track.append((e["t"], pos.unit, pos.committed_unit, tr))
        if pos.unit > tr + 1:
            overshoot += 1
        # a move of more than 3 units that does not land within 3 units of the truth
        if abs(pos.unit - prev.unit) > 3 and abs(pos.unit - tr) > 3:
            false_jumps += 1
        assert pos.unit >= pos.committed_unit
        assert pos.committed_unit >= prev.committed_unit
        prev = pos

    # lag: word end -> the first event at which the cursor has reached that word
    lags = []
    for r in truth:
        reach = next((t for t, u, _, _ in track if u >= r["unit"] and t >= r["start"]), None)
        lags.append(None if reach is None else reach - r["end"])
    reached = [x for x in lags if x is not None]

    # stall: longest stretch trailing the truth by more than 5 units
    stall, since = 0.0, None
    for t, u, _, tr in track:
        if tr - u > 5:
            since = t if since is None else since
            stall = max(stall, t - since)
        else:
            since = None

    # re-acquire: script words spoken from rejoin_unit on before the cursor got there
    rejoin_words = None
    rj = data.get("rejoin_unit")
    if rj is not None:
        reach = next((t for t, u, _, _ in track if u >= rj), None)
        rejoin_words = sum(1 for r in truth if r["unit"] >= rj and (reach is None or r["start"] < reach))

    final_truth = max(r["unit"] for r in truth)
    return {"rejoin_words": rejoin_words,"overshoot": overshoot, "false_jumps": false_jumps,
            "final_error": abs(al.position.unit - final_truth),
            "final_unit": al.position.unit, "final_truth": final_truth,
            "lag_p50": _pct(reached, 0.5), "lag_p90": _pct(reached, 0.9),
            "unreached": len(lags) - len(reached), "words": len(lags), "stall": stall}


def test_fixture_files_present_and_small():
    for n in [f"{d}/{n}" if d else n for d in SETS for n in NAMES] + REACQ:
        p = FIXTURES / f"{n}.json.gz"
        assert p.exists(), p
        assert p.stat().st_size < 100_000
        d = load(p)
        assert d["script"] and d["events"] and d["truth"]


@pytest.mark.parametrize("fset", SETS)
@pytest.mark.parametrize("name", NAMES)
def test_replay(name, fset, capsys):
    m = replay(load(FIXTURES / fset / f"{name}.json.gz"))
    with capsys.disabled():
        print(f"\n[{fset or 'default'}/{name}] lag p50 {m['lag_p50']:.2f} s  p90 {m['lag_p90']:.2f} s  "
              f"stall {m['stall']:.1f} s  overshoot {m['overshoot']}  false_jumps {m['false_jumps']}  "
              f"final {m['final_unit']}/{m['final_truth']}  unreached {m['unreached']}/{m['words']}")
    assert m["overshoot"] == 0
    assert m["false_jumps"] == 0
    assert m["final_error"] <= 2
    if name in CLEAN:
        assert m["lag_p50"] < 0.6


@pytest.mark.parametrize("name", REACQ)
def test_reacquire_replay(name, capsys):
    """Real-ASR recordings of going off script: the hard limits above, plus the cursor reaches
    the rejoin point within REACQ_MAX_WORDS spoken words."""
    d = load(FIXTURES / f"{name}.json.gz")
    m = replay(d)
    with capsys.disabled():
        print(f"\n[{name}] rejoin after {m['rejoin_words']} words  lag p50 {m['lag_p50']:.2f} s  "
              f"stall {m['stall']:.1f} s  overshoot {m['overshoot']}  false_jumps {m['false_jumps']}  "
              f"final {m['final_unit']}/{m['final_truth']}")
    assert m["overshoot"] == 0
    assert m["false_jumps"] == 0
    assert m["final_error"] <= 2
    assert m["rejoin_words"] is not None and m["rejoin_words"] <= REACQ_MAX_WORDS


def test_private_fixtures_with_truth_are_scored():
    """A human recording (tests/fixtures/private/, git-ignored) is scored if it carries `truth`."""
    for p in sorted((FIXTURES / "private").glob("*.json.gz")):
        d = load(p)
        if not d.get("truth"):
            continue
        m = replay(d)
        assert m["overshoot"] == 0 and m["false_jumps"] == 0 and m["final_error"] <= 2, (p.name, m)


@pytest.mark.skipif(not (Path.home() / ".local/share/promptbro/tts/vits-piper-en_US-amy-low").exists(),
                    reason="needs the piper voice (see scripts/tts_wav.py)")
def test_recording_is_deterministic(tmp_path):
    """Re-running the recorder's TTS gives a byte-identical WAV."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import record_fixture as rf
    spec = rf.variants()["skip_line"]
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    xa, ta = rf.render(spec, a)
    xb, tb = rf.render(spec, b)
    da, db = rf.write_wav(xa, a / "x.wav"), rf.write_wav(xb, b / "x.wav")
    assert (a / "x.wav").read_bytes() == (b / "x.wav").read_bytes()
    assert da == db and ta == tb
    assert da == load(FIXTURES / "skip_line.json.gz")["tts"]["wav_sha256_16"]
