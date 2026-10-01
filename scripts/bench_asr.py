#!/usr/bin/env python3
"""Benchmark streaming ASR models for the promptbro: first-emission latency, partial cadence,
WER and steady CPU, on identical WAV input (DESIGN 5.1, BUILD_LOG "Snappiness").

    bench_asr.py --model DIR [--model DIR ...] [--block-ms 20] [--threads 1] [--realtime]
                 [--set fixtures|human|all] [--json OUT]

Input sets (all 16 kHz mono, never the live mic):
  fixtures  tests/audio/fx_*.wav, the T8 piper recordings of tests/data/skip.md
            (scripts/record_fixture.py regenerates them byte-identically)
  human     LibriSpeech clips shipped in the models' test_wavs/ (real read speech)

Word onsets come from one offline reference model with timestamps (parakeet TDT 0.6b v2,
full context), computed once and cached next to this script's output. Every candidate is
scored against the same onsets, so the comparison is fair even though the absolute onsets
are only good to about one encoder frame (80 ms).

Feeding: blocks of --block-ms arrive at the moment their last sample would have been
captured live. Default is virtual real time (no sleeping; a block is processed at
max(arrival, previous block done) and its events are stamped at that time plus the measured
compute), which is exact for a single decode thread and fast to run. --realtime really sleeps,
which also captures CPU frequency scaling while idle between blocks.

Metrics per model:
  first_ms   word onset -> first event (partial or final) that shows the complete word
  prefix_ms  word onset -> first event that shows at least its first three letters (the
             aligner already accepts a half-said last word, token_sim PREFIX_SIM)
  cadence    median gap between partial updates while speaking
  wer        word error rate of the finals (both sides through normalize_hyp)
  cpu        process CPU seconds per second of audio after model load (1.0 = one core)
"""
from __future__ import annotations

import argparse
import difflib
import glob
import hashlib
import json
import resource
import statistics
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from promptbro.normalize import normalize_hyp  # noqa: E402
from promptbro.script import load_text  # noqa: E402

RATE = 16000
MODELS = Path("~/.local/share/promptbro/models").expanduser()
REF_MODEL = MODELS / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
CACHE = Path(__file__).resolve().parent / "__pycache__" / "bench_ref_onsets.json"
# English LibriSpeech/JFK clips shipped with these models (fixed, so runs stay comparable)
HUMAN_FROM = ("fast-conformer-transducer-en-80ms", "zipformer-en-2023-06-26", "parakeet-tdt-0.6b-v2")
PAD_S = 1.5
DUMP: Path | None = None  # --dump DIR: save each clip's event stream (for aligner analysis)   # silence after each clip: a live mic keeps sending audio, so endpoints can fire


# ------------------------------------------------------------------ input
def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != RATE:
        m = int(round(len(x) * RATE / sr))
        x = np.interp(np.linspace(0, len(x) - 1, m), np.arange(len(x)), x).astype(np.float32)
    return x


def clip_set(which: str) -> list[dict]:
    clips = []
    if which in ("fixtures", "all"):
        for p in sorted((ROOT / "tests" / "audio").glob("fx_*.wav")):
            clips.append({"name": p.stem, "path": p, "set": "fixtures", "ref": None})
    if which in ("human", "all"):
        seen = set()
        for tw in sorted(glob.glob(str(MODELS / "*" / "test_wavs"))):
            if not any(k in tw for k in HUMAN_FROM):
                continue
            trans = {}
            tf = Path(tw) / "trans.txt"
            if tf.exists():
                for ln in tf.read_text().splitlines():
                    if " " in ln:
                        k, v = ln.split(" ", 1)
                        trans[k] = v
            for p in sorted(Path(tw).glob("*.wav")):
                h = hashlib.sha256(read_wav(p).tobytes()).hexdigest()[:12]
                key = trans.get(p.name) or h
                if h in seen or key in seen:
                    continue
                seen.update((h, key))
                clips.append({"name": f"{Path(tw).parent.name[:18]}/{p.stem}", "path": p, "set": "human",
                              "ref": trans.get(p.name)})
    return clips


# ------------------------------------------------------------------ recognizers
def transducer_files(d: Path) -> dict:
    def pick(stem):
        c = sorted(d.glob(f"{stem}*.int8.onnx")) or sorted(d.glob(f"{stem}*.onnx"))
        if not c:
            raise FileNotFoundError(f"no {stem}*.onnx in {d}")
        return str(c[0])
    return {"encoder": pick("encoder"), "decoder": pick("decoder"), "joiner": pick("joiner"),
            "tokens": str(d / "tokens.txt")}


def online(d: Path, threads: int, rule2: float = 1.0):
    import sherpa_onnx
    f = transducer_files(d)
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        **f, num_threads=threads, sample_rate=RATE, feature_dim=80, provider="cpu",
        decoding_method="greedy_search", enable_endpoint_detection=True,
        rule1_min_trailing_silence=2.4, rule2_min_trailing_silence=rule2,
        rule3_min_utterance_length=20.0)


def words_of(text: str) -> list[str]:
    out = []
    for w in text.split():
        w = w.strip(" \t\"'()[]{},.;:!?…-").lower()
        if w:
            out.append(w)
    return out


# ------------------------------------------------------------------ reference onsets
def ref_onsets(clips: list[dict]) -> dict:
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = [c for c in clips if str(c["path"]) not in cache]
    if todo:
        import sherpa_onnx
        f = transducer_files(REF_MODEL)
        rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            **f, num_threads=4, sample_rate=RATE, feature_dim=80, model_type="nemo_transducer",
            decoding_method="greedy_search")
        for c in todo:
            x = read_wav(c["path"])
            s = rec.create_stream()
            s.accept_waveform(RATE, x)
            rec.decode_stream(s)
            r = s.result
            ws, cur, t0 = [], "", None
            for tok, t in zip(r.tokens, r.timestamps):
                if tok.startswith("▁") or tok.startswith(" ") or t0 is None:
                    if cur:
                        ws.append((cur, t0))
                    cur, t0 = tok.lstrip("▁ "), float(t)
                else:
                    cur += tok
            if cur:
                ws.append((cur, t0))
            cache[str(c["path"])] = [(w2, t) for w, t in ws for w2 in words_of(w)[:1]]
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(cache))
    return cache


# ------------------------------------------------------------------ one run
def run_clip(rec, x: np.ndarray, block: int, realtime: bool) -> tuple[list[dict], float]:
    """Feed x in live-like blocks; return (events, cpu seconds). Event t = seconds from audio start."""
    stream = rec.create_stream()
    x = np.concatenate([x, np.zeros(int(PAD_S * RATE), dtype=np.float32)])
    evs, utt, last = [], 0, ""
    busy = 0.0
    cpu0 = time.process_time()
    wall0 = time.perf_counter()
    for k in range(0, len(x) - block + 1, block):
        arrive = (k + block) / RATE
        if realtime:
            d = wall0 + arrive - time.perf_counter()
            if d > 0:
                time.sleep(d)
            start = time.perf_counter() - wall0
        else:
            start = max(arrive, busy)
        c0 = time.perf_counter()
        stream.accept_waveform(RATE, x[k:k + block])
        while rec.is_ready(stream):
            rec.decode_stream(stream)
        text = rec.get_result(stream)
        endp = rec.is_endpoint(stream)
        dt = time.perf_counter() - c0
        now = (time.perf_counter() - wall0) if realtime else start + dt
        busy = now
        if text and text != last:
            evs.append({"type": "partial", "utt": utt, "t": now, "text": text})
            last = text
        if endp:
            if text.strip():
                evs.append({"type": "final", "utt": utt, "t": now, "text": text})
                utt += 1
            last = ""
            rec.reset(stream)
    text = rec.get_result(stream)
    if text.strip():
        evs.append({"type": "final", "utt": utt, "t": busy, "text": text})
    return evs, time.process_time() - cpu0


def wer_tokens(ref: list[str], hyp: list[str]) -> tuple[int, int]:
    d = list(range(len(hyp) + 1))
    for i in range(1, len(ref) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(hyp) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (ref[i - 1] != hyp[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(hyp)], len(ref)


def fixture_ref(clip) -> tuple[str, object]:
    import gzip
    fx = json.load(gzip.open(ROOT / "tests" / "fixtures" / (clip["name"][3:] + ".json.gz")))
    sc = load_text(fx["script"])
    sys.path.insert(0, str(ROOT / "scripts"))
    import record_fixture as rf
    spec = rf.variants()[clip["name"][3:]]
    return " ".join(seg[0] for seg in spec["segs"]), sc


def seg_first_truth(clip) -> list[tuple[str, float]]:
    """Fixture words that start a spoken segment: their onset is exact (energy), not interpolated."""
    import gzip
    fx = json.load(gzip.open(ROOT / "tests" / "fixtures" / (clip["name"][3:] + ".json.gz")))
    sc = load_text(fx["script"])
    out, prev_end = [], -1.0
    for t in fx["truth"]:
        if t["start"] - prev_end > 0.25:
            out.append((sc.units[t["unit"]].forms[0][0], t["start"]))
        prev_end = t["end"]
    return out


def score(clip, evs, onsets) -> dict:
    finals = [e for e in evs if e["type"] == "final"]
    seg_first = seg_first_truth(clip) if clip["set"] == "fixtures" else []
    # WER through the app's own normalizer (numbers, money, fillers)
    if clip["set"] == "fixtures":
        ref_text, sc = fixture_ref(clip)
        norm = lambda s: [t.text for t in normalize_hyp(s, sc, -1)]  # noqa: E731
    else:
        ref_text = clip["ref"] or ""
        norm = lambda s: [t.text for t in normalize_hyp(s, None, -1)]  # noqa: E731
    if ref_text:
        errs, n = wer_tokens(norm(ref_text), norm(" ".join(f["text"] for f in finals)))
    else:
        errs, n = 0, 0  # no transcript: latency only
    # latency: align final words to reference-onset words
    hyp = [(f["utt"], i, w) for f in finals for i, w in enumerate(words_of(f["text"]))]
    ref = onsets
    sm = difflib.SequenceMatcher(a=[h[2] for h in hyp], b=[r[0] for r in ref], autojunk=False)
    by_utt: dict[int, list] = {}
    for e in evs:
        by_utt.setdefault(e["utt"], []).append((e["t"], words_of(e["text"])))
    first, prefix, exact = [], [], []
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            u, pos, w = hyp[blk.a + k]
            onset = ref[blk.b + k][1]
            tf = tp = None
            for t, ws in by_utt.get(u, []):
                if len(ws) > pos:
                    if tp is None and (ws[pos] == w or (len(w) >= 4 and ws[pos][:3] == w[:3])
                                       or (len(w) < 4 and ws[pos] == w)):
                        tp = t
                    if ws[pos] == w:
                        tf = t
                        break
            if tf is not None:
                first.append(tf - onset)
                for sw, st in seg_first:
                    if sw == w and abs(st - onset) < 0.4:
                        exact.append(tf - st)
            if tp is not None:
                prefix.append(tp - onset)
    rp = None
    if clip["set"] == "fixtures":
        # end to end through the real aligner, scored like tests/test_fixtures_replay.py
        # (lag = spoken word end -> cursor reaches it), with events at their emission time
        import gzip
        sys.path.insert(0, str(ROOT / "tests"))
        from test_fixtures_replay import replay
        fx = json.load(gzip.open(ROOT / "tests" / "fixtures" / (clip["name"][3:] + ".json.gz")))
        rp = replay({**fx, "events": [{"type": e["type"], "t": e["t"], "text": e["text"]} for e in evs]})
    parts = [e["t"] for e in evs if e["type"] == "partial"]
    gaps = [b - a for a, b in zip(parts, parts[1:]) if b - a < 0.8]
    return {"errs": errs, "n": n, "first": first, "prefix": prefix, "gaps": gaps, "exact": exact, "replay": rp}


def pct(xs, q):
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def bench_model(d: Path, clips, onsets, block_ms: int, threads: int, realtime: bool, rule2: float) -> dict:
    t0 = time.perf_counter()
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rec = online(d, threads, rule2)
    load_s = time.perf_counter() - t0
    block = int(RATE * block_ms / 1000)
    # warm-up so first-inference allocation doesn't count
    run_clip(rec, np.zeros(RATE, dtype=np.float32), block, False)
    agg = {"errs": 0, "n": 0, "first": [], "prefix": [], "gaps": [], "exact": [], "replay": [], "cpu": 0.0, "audio": 0.0}
    per = {}
    for c in clips:
        x = read_wav(c["path"])
        evs, cpu = run_clip(rec, x, block, realtime)
        if DUMP:
            DUMP.mkdir(parents=True, exist_ok=True)
            (DUMP / f"{d.name[:40]}__{block_ms}__{c['name'].replace('/', '_')}.json").write_text(json.dumps(evs))
        s = score(c, evs, onsets[str(c["path"])])
        for k in ("errs", "n"):
            agg[k] += s[k]
        for k in ("first", "prefix", "gaps", "exact"):
            agg[k] += s[k]
        if s["replay"]:
            agg["replay"].append(s["replay"])
        agg["cpu"] += cpu
        agg["audio"] += len(x) / RATE + PAD_S
        per[c["name"]] = {"wer": round(s["errs"] / max(1, s["n"]), 3) if s["n"] else None,
                          "first_p50": round(pct(s["first"], 0.5) * 1000) if s["first"] else None}
        if s["replay"]:
            per[c["name"]]["replay"] = {k: (round(v, 3) if isinstance(v, float) else v)
                                        for k, v in s["replay"].items()}
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "model": d.name, "block_ms": block_ms, "threads": threads, "realtime": realtime,
        "load_s": round(load_s, 2), "rss_mb": round((rss - rss0) / 1024),
        "first_p50_ms": round(pct(agg["first"], 0.5) * 1000), "first_p90_ms": round(pct(agg["first"], 0.9) * 1000),
        "prefix_p50_ms": round(pct(agg["prefix"], 0.5) * 1000),
        "segstart_p50_ms": round(pct(agg["exact"], 0.5) * 1000), "segstart_n": len(agg["exact"]),
        "cadence_ms": round(statistics.median(agg["gaps"]) * 1000) if agg["gaps"] else None,
        "wer": round(agg["errs"] / max(1, agg["n"]), 3), "words": len(agg["first"]),
        "cpu_cores": round(agg["cpu"] / agg["audio"], 3),
        "replay_lag_p50_ms": round(statistics.median(r["lag_p50"] for r in agg["replay"]) * 1000) if agg["replay"] else None,
        "replay_lag_p90_ms": round(max(r["lag_p90"] for r in agg["replay"]) * 1000) if agg["replay"] else None,
        "replay_bad": sum(r["overshoot"] + r["false_jumps"] + (r["final_error"] > 2) for r in agg["replay"]),
        "per_clip": per,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="append", required=True, help="model dir (name under the models dir or a path)")
    ap.add_argument("--block-ms", type=int, action="append", help="block size(s) in ms (default 20)")
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--rule2", type=float, default=1.0)
    ap.add_argument("--realtime", action="store_true")
    ap.add_argument("--set", default="all", choices=("fixtures", "human", "all"))
    ap.add_argument("--json", help="append result rows (JSON lines) here")
    ap.add_argument("--clips", help="comma-separated substrings: only clips whose name matches one")
    ap.add_argument("--dump", help="write each clip's events (emission-time stamped) into this dir")
    a = ap.parse_args(argv)
    global DUMP
    DUMP = Path(a.dump) if a.dump else None
    clips = clip_set(a.set)
    if a.clips:
        clips = [c for c in clips if any(k in c["name"] for k in a.clips.split(","))]
    onsets = ref_onsets(clips)
    for m in a.model:
        d = Path(m) if "/" in m else MODELS / m
        for b in a.block_ms or [20]:
            r = bench_model(d, clips, onsets, b, a.threads, a.realtime, a.rule2)
            row = {k: v for k, v in r.items() if k != "per_clip"}
            print(json.dumps(row), flush=True)
            if a.json:
                with open(a.json, "a") as f:
                    f.write(json.dumps(r) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
