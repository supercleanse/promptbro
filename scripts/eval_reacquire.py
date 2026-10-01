#!/usr/bin/env python3
"""Measure how fast the aligner re-acquires the script after the speaker leaves it.

    eval_reacquire.py [SCRIPT ...] [--json] [--scenario NAME ...] [--max-words N]

For every line L of each script (default: the sample script and tests/data/skip.md) and each
scenario below, a speaker is simulated with conftest-style word-by-word partials at 2.5 words/s
(tests/conftest.py::speak). The cursor starts at the end of line L-1 (or in the middle of line L
for the mid-sentence scenarios). The scenario is spoken as one utterance, the way people read a
script: no pause at the point of departure.

Scenarios
  skip1_mid   read half of line L, then jump to the start of line L+1 and read on
  skip1       read line L, skip line L+1, read L+2 and on
  skip2       read line L, skip L+1 and L+2, read L+3 and on
  skip4       read line L, skip L+1..L+4, read L+5 and on
  paraphrase  read line L+1 with a word swapped and a word added, then read L+2 verbatim
  adlib_back  read line L, a 10-word ad-lib, then line L+1 (back at the cursor)
  adlib_skip2 read line L, a 10-word ad-lib, then line L+3 (two lines ahead of the cursor)
  pause_adlib read line L, pause, a 10-word ad-lib, pause, then line L+1 (three utterances)
  pause_skip2 the same, but rejoining at line L+3
  adlib_vocab read line L, then 10 words drawn at random from the script's own common words
              (a worst-case ad-lib), then line L+1
  reread      read line L, then read line L again, then L+1 (must hold through the repeat)
  restart     read line L, the first three words of L+1, then all of L+1
  clean       read lines L..L+3 verbatim (no departure; only overshoot/false matter)

Reported per scenario: the median and p90 number of words spoken after the speaker rejoins the
script until the display cursor has caught up (within one matchable unit behind, never ahead),
how many cases never caught up within 12 words ("lost"), and how many events showed the cursor
more than one unit past what had been said ("overshoot") or more than 3 units off after a move
("false"). The last two must stay 0.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from promptbro.aligner import Aligner, Event, Params, effective_delta  # noqa: E402
from promptbro.script import load_text  # noqa: E402

DEFAULT_SCRIPTS = [ROOT / "promptbro" / "data" / "sample-script.md", ROOT / "tests" / "data" / "skip.md"]
ADLIBS = [
    "and honestly I just want to say how much that means to me personally",
    "you know what I mean when I say this matters a lot to everybody",
    "let me just pause here for a second because this part is important",
    "and I know a lot of you have been asking me about exactly this lately",
]
SWAPS = ["really", "truly", "basically", "folks", "anyway", "simply", "certainly", "entirely"]
SCENARIOS = ["skip1_mid", "skip1", "skip2", "skip4", "paraphrase", "adlib_back", "adlib_skip2",
             "pause_adlib", "pause_skip2", "adlib_vocab", "reread", "restart", "clean"]
BREAK = ("|", None)   # an utterance break: the recognizer finalizes, then 0.8 s of silence


def speak(text, t0=0.0, wps=2.5, partial_every=0.2):
    words = text.split()
    n = len(words)
    dur = n / wps
    events = []
    k = 0
    while True:
        t = t0 + k * partial_every
        if t >= t0 + dur - 1e-9:
            break
        count = min(n, int((t - t0) * wps + 1e-9) + 1)
        events.append((count, Event("partial", round(t, 3), " ".join(words[:count]))))
        k += 1
    events.append((n, Event("final", round(t0 + dur + 0.3, 3), " ".join(words))))
    return events


def line_words(script, line, lo=0, hi=None):
    """[(spoken token, unit or None)] for units lo:hi of a line, primary spoken forms."""
    first, last = script.line_first_unit[line], script.line_last_unit[line]
    out = []
    for u in script.units[first:last + 1][lo:hi]:
        form = u.forms[0] if u.forms else (u.text.lower(),)
        for i, tok in enumerate(form):
            out.append((tok, u.idx if i == len(form) - 1 else None))
    return out


def tokens(text):
    return [(w, None) for w in text.split()]


def build(script, scen, L, rng):
    """-> (start_unit, words, rejoin_index) or None when the script is too short here."""
    nl = len(script.lines)
    lw = lambda i, *a: line_words(script, i, *a)  # noqa: E731
    prev_end = script.line_first_unit[L] - 1
    if scen == "skip1_mid":
        n = script.line_last_unit[L] - script.line_first_unit[L] + 1
        if L + 2 >= nl or n < 4:
            return None
        pre = lw(L, 0, n // 2)
        post = lw(L + 1) + lw(L + 2)
        return prev_end, pre + post, len(pre)
    skips = {"skip1": 1, "skip2": 2, "skip4": 4}
    if scen in skips:
        k = skips[scen]
        if L + k + 2 >= nl:
            return None
        pre = lw(L)
        post = lw(L + k + 1) + lw(L + k + 2)
        return prev_end, pre + post, len(pre)
    if scen == "paraphrase":
        if L + 2 >= nl:
            return None
        words = lw(L + 1)
        if len(words) < 4:
            return None
        words = list(words)
        i = rng.randrange(1, len(words))
        words[i] = (rng.choice(SWAPS), None)
        j = rng.randrange(1, len(words))
        words.insert(j, (rng.choice(SWAPS), None))
        pre = lw(L) + words
        post = lw(L + 2) + (lw(L + 3) if L + 3 < nl else [])
        return prev_end, pre + post, len(pre)
    if scen in ("adlib_back", "adlib_skip2"):
        tgt = L + 1 if scen == "adlib_back" else L + 3
        if tgt + 1 >= nl:
            return None
        pre = lw(L) + tokens(rng.choice(ADLIBS))
        post = lw(tgt) + lw(tgt + 1)
        return prev_end, pre + post, len(pre)
    if scen in ("pause_adlib", "pause_skip2"):
        tgt = L + 1 if scen == "pause_adlib" else L + 3
        if tgt + 1 >= nl:
            return None
        pre = lw(L) + [BREAK] + tokens(rng.choice(ADLIBS)) + [BREAK]
        post = lw(tgt) + lw(tgt + 1)
        return prev_end, pre + post, len(pre)
    if scen == "adlib_vocab":
        if L + 2 >= nl:
            return None
        common = [u.forms[0][0] for u in script.units if u.matchable and u.weight < 0.6 and u.forms]
        pre = lw(L) + [(rng.choice(common), None) for _ in range(10)]
        post = lw(L + 1) + lw(L + 2)
        return prev_end, pre + post, len(pre)
    if scen == "reread":
        if L + 1 >= nl:
            return None
        pre = lw(L) + [(w, None) for w, _ in lw(L)]
        return prev_end, pre + lw(L + 1), len(pre)
    if scen == "restart":
        if L + 2 >= nl or len(lw(L + 1)) < 5:
            return None
        pre = lw(L) + lw(L + 1)[:3]
        return prev_end, pre + lw(L + 1) + lw(L + 2), len(pre)
    if scen == "clean":
        if L + 3 >= nl:
            return None
        words = lw(L) + lw(L + 1) + lw(L + 2) + lw(L + 3)
        return prev_end, words, 0
    raise ValueError(scen)


def run_case(script, start, words, rejoin, params, max_words):
    rejoin -= sum(1 for w in words[:rejoin] if w == BREAK)
    al = Aligner(script, params, start_unit=start)
    spoken = [w for w in words if w != BREAK]
    truth_at = []
    tr = start
    for _, u in spoken:
        if u is not None:
            tr = u
        truth_at.append(tr)
    peak = [max(truth_at[:i + 1]) for i in range(len(truth_at))]
    overshoot = false = 0
    caught = None
    prev = al.position
    # split into utterances at BREAKs; `count` stays a global spoken-word count
    stream, t, done, utt = [], 0.0, 0, []
    for w in words + [BREAK]:
        if w == BREAK:
            if utt:
                evs = speak(" ".join(x for x, _ in utt), t0=t)
                stream += [(done + c, e) for c, e in evs]
                done += len(utt)
                t = evs[-1][1].t + 0.8
                utt = []
            continue
        utt.append(w)
    for count, ev in stream:
        pos = al.on_event(ev)
        tr = truth_at[count - 1]
        pk = peak[count - 1]
        if pos.unit > pk and effective_delta(script, pk, pos.unit) > 1:
            overshoot += 1
        moved = abs(effective_delta(script, min(pos.unit, prev.unit), max(pos.unit, prev.unit)))
        off = abs(effective_delta(script, min(pos.unit, tr), max(pos.unit, tr)))
        if moved > 3 and off > 3:
            false += 1
        prev = pos
        k = count - rejoin       # words spoken since rejoining the script
        if caught is None and k >= 1 and pos.unit <= tr + 1 and effective_delta(script, pos.unit, tr) <= 1:
            caught = k
    if caught is not None and caught > max_words:
        caught = None
    return caught, overshoot, false


def evaluate(paths, scenarios, params=None, max_words=12, seed=11):
    params = params or Params()
    res = {}
    for scen in scenarios:
        lat, lost, over, false, n = [], 0, 0, 0, 0
        for path in paths:
            script = load_text(Path(path).read_text())
            rng = random.Random(seed)
            for L in range(len(script.lines)):
                b = build(script, scen, L, rng)
                if b is None:
                    continue
                start, words, rejoin = b
                c, o, f = run_case(script, start, words, rejoin, params, max_words)
                n += 1
                over += o
                false += f
                if c is None:
                    lost += 1
                else:
                    lat.append(c)
        lat.sort()
        res[scen] = {
            "cases": n, "lost": lost, "overshoot": over, "false": false,
            "p50": statistics.median(lat) if lat else None,
            "p90": lat[min(len(lat) - 1, int(0.9 * len(lat)))] if lat else None,
            "mean": round(sum(lat) / len(lat), 2) if lat else None,
        }
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scripts", nargs="*", default=[str(p) for p in DEFAULT_SCRIPTS])
    ap.add_argument("--scenario", action="append", choices=SCENARIOS)
    ap.add_argument("--max-words", type=int, default=12)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = evaluate(a.scripts, a.scenario or SCENARIOS, max_words=a.max_words)
    if a.json:
        print(json.dumps(res, indent=1))
        return
    print(f"{'scenario':12} {'cases':>5} {'p50':>5} {'p90':>5} {'mean':>6} {'lost':>5} {'over':>5} {'false':>5}")
    for k, r in res.items():
        f = lambda v: "-" if v is None else str(v)  # noqa: E731
        print(f"{k:12} {r['cases']:5} {f(r['p50']):>5} {f(r['p90']):>5} {f(r['mean']):>6} "
              f"{r['lost']:5} {r['overshoot']:5} {r['false']:5}")


if __name__ == "__main__":
    main()
