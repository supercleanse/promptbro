"""Aligner corpus runner (DESIGN §6.8), invariants, property test, and perf test.

Case file format (``tests/cases/*.yaml``)::

    name: ...
    script: |            # text for script.load_text
    start_unit: 12       # committed = provisional = start_unit before the first event
    events:              # expanded in order into one stream
      - {t: 0.0, type: partial, text: "the"}          # a raw recognizer event
      - {speak: "the lazy dog", t0: 1.0, label: read}  # conftest.speak(); options: wps,
                                                       #   partial_every, revise, drop_last_in_final
      - {gap: 10, label: quiet}                        # silence: an empty partial every second
      - {key: line_prev}                               # a user key (optional ``line`` for goto_line)
    expect:
      - {after: 3, line: 1, unit: 12}      # state after expanded event #3 (0-based)
      - {at: read, unit: 34}               # state after the last event of a label ("end" = last)
      - {during: quiet, unit: 30}          # every event of a label
      - {during: read, word_units: [0, 1]} # a partial holding k words has unit word_units[k-1]
      - {at_word: 3, of: read, line: 4}    # state after the last partial of `read` holding
                                           #   exactly 3 of its words (re-acquire latency)
    # checkable fields: line, unit, unit_min, unit_max, committed, committed_min, state
    invariants: [no_backward_without_key, provisional_ge_committed]

``variants`` (a list of {events, expect}) runs the same script/start several ways.
Missing ``t``/``t0`` continue 0.3 s after the previous event.
"""

from __future__ import annotations

import random
import time
from pathlib import Path

import pytest
import yaml

from conftest import speak
from promptbro import aligner as aligner_mod
from promptbro.aligner import Aligner, Event, Params
from promptbro.script import load_text

CASES = sorted((Path(__file__).parent / "cases").glob("*.yaml"))
SAMPLE = Path(__file__).resolve().parent.parent / "promptbro" / "data" / "sample-script.md"


def _expand(specs):
    """-> list of (label, item) where item is an Event or ("key", key, line)."""
    out = []
    t = 0.0
    for spec in specs:
        label = spec.get("label")
        if "speak" in spec:
            t0 = float(spec.get("t0", t + 0.3 if out else 0.0))
            opts = {k: spec[k] for k in ("wps", "partial_every", "revise", "drop_last_in_final")
                    if k in spec}
            evs = speak(spec["speak"], t0=t0, **opts)
            out += [(label, e) for e in evs]
            t = evs[-1].t
        elif "gap" in spec:
            t0 = float(spec.get("t0", t))
            g = float(spec["gap"])
            k = 1
            while k <= g:
                out.append((label, Event("partial", t0 + k, "")))
                k += 1
            t = t0 + g
        elif "key" in spec:
            out.append((label, ("key", spec["key"], spec.get("line"))))
        else:
            ev = Event(spec.get("type", "partial"), float(spec.get("t", t + 0.3 if out else 0.0)),
                       spec.get("text", ""))
            out.append((label, ev))
            t = ev.t
    return out


def _check(pos, exp, where):
    for k, v in exp.items():
        if k in ("after", "at", "during", "word_units", "at_word", "of"):
            continue
        if k == "line":
            assert pos.line == v, f"{where}: line {pos.line} != {v} ({pos})"
        elif k == "unit":
            assert pos.unit == v, f"{where}: unit {pos.unit} != {v} ({pos})"
        elif k == "unit_min":
            assert pos.unit >= v, f"{where}: unit {pos.unit} < {v} ({pos})"
        elif k == "unit_max":
            assert pos.unit <= v, f"{where}: unit {pos.unit} > {v} ({pos})"
        elif k == "committed":
            assert pos.committed_unit == v, f"{where}: committed {pos.committed_unit} != {v}"
        elif k == "committed_min":
            assert pos.committed_unit >= v, f"{where}: committed {pos.committed_unit} < {v}"
        elif k == "state":
            assert pos.state == v, f"{where}: state {pos.state} != {v}"
        else:
            raise AssertionError(f"{where}: unknown expectation key {k!r}")


def run_stream(script, start_unit, stream, invariants=(), params=None):
    al = Aligner(script, params or Params(), start_unit=start_unit)
    positions = []
    prev = al.position
    for idx, (_, item) in enumerate(stream):
        if isinstance(item, tuple):
            pos = al.on_user_key(item[1], item[2])
            is_key = True
        else:
            pos = al.on_event(item)
            is_key = False
        if "provisional_ge_committed" in invariants:
            assert pos.unit >= pos.committed_unit, f"event {idx}: provisional < committed ({pos})"
        if "no_backward_without_key" in invariants and not is_key:
            assert pos.committed_unit >= prev.committed_unit, \
                f"event {idx}: committed went back {prev.committed_unit} -> {pos.committed_unit}"
            assert pos.unit >= prev.unit - al.params.revise_back_max, \
                f"event {idx}: provisional retreated {prev.unit} -> {pos.unit}"
        positions.append(pos)
        prev = pos
    return positions


def _run_case(case, events, expects):
    script = load_text(case["script"])
    stream = _expand(events)
    positions = run_stream(script, case.get("start_unit", -1), stream,
                           case.get("invariants", ["no_backward_without_key",
                                                   "provisional_ge_committed"]))
    labels = [lab for lab, _ in stream]
    for exp in expects:
        if "after" in exp:
            i = exp["after"]
            _check(positions[i], exp, f"after {i}")
        elif "at" in exp:
            lab = exp["at"]
            idx = len(stream) - 1 if lab == "end" else max(
                i for i, l in enumerate(labels) if l == lab)
            _check(positions[idx], exp, f"at {lab}")
        elif "at_word" in exp:
            lab, k = exp["of"], exp["at_word"]
            idxs = [i for i, (l, item) in enumerate(stream) if l == lab and isinstance(item, Event)
                    and item.type == "partial" and len(item.text.split()) == k]
            assert idxs, f"no partial of {lab} holds {k} words"
            _check(positions[idxs[-1]], exp, f"{lab} word {k}")
        elif "during" in exp:
            lab = exp["during"]
            idxs = [i for i, l in enumerate(labels) if l == lab]
            assert idxs, f"no events labeled {lab}"
            for i in idxs:
                _check(positions[i], exp, f"during {lab} #{i}")
            if "word_units" in exp:
                wu = exp["word_units"]
                for i in idxs:
                    item = stream[i][1]
                    if isinstance(item, Event) and item.type == "partial" and item.text:
                        k = len(item.text.split())
                        assert positions[i].unit == wu[k - 1], \
                            f"during {lab} #{i} ({item.text!r}): unit {positions[i].unit} != {wu[k - 1]}"


@pytest.mark.parametrize("path", CASES, ids=[p.stem for p in CASES])
def test_case(path):
    case = yaml.safe_load(path.read_text())
    variants = case.get("variants") or [{"events": case["events"], "expect": case["expect"]}]
    for v in variants:
        _run_case(case, v["events"], v["expect"])


def test_corpus_has_32_cases():
    # 24 from the T3 plan, restart_sound_alike (found by the T8 restart_sentence fixture), and
    # seven reacquire_* cases (going off script and coming back; BUILD_LOG "Re-acquisition")
    assert len(CASES) == 32


# ------------------------------------------------------------------ property test

def _random_stream(rng, script):
    words = [u.text for u in script.units]
    stream = []
    t = 0.0
    for _ in range(rng.randint(3, 10)):
        r = rng.random()
        if r < 0.08:
            stream.append((None, ("key", rng.choice(["line_prev", "line_next", "restart"]), None)))
            continue
        if r < 0.15:
            t += rng.uniform(2.0, 7.0)
            continue
        start = rng.randrange(len(words))
        seg = words[start:start + rng.randint(1, 12)]
        seg = [w for w in seg if rng.random() > 0.1]                     # skipped words
        if rng.random() < 0.3:
            seg.insert(rng.randint(0, len(seg)), rng.choice(["um", "like", "so", "honestly"]))
        if seg and rng.random() < 0.2:
            i = rng.randrange(len(seg))
            seg.insert(i, seg[i])                                         # stutter
        if not seg:
            continue
        evs = speak(" ".join(seg), t0=t, wps=rng.uniform(1.5, 4.0),
                    revise={rng.randrange(len(seg)): "blah"} if rng.random() < 0.3 else None,
                    drop_last_in_final=rng.random() < 0.2)
        stream += [(None, e) for e in evs]
        t = evs[-1].t + rng.uniform(0.1, 1.0)
    return stream


def test_property_random_streams():
    script = load_text(SAMPLE.read_text())
    rng = random.Random(1234)
    for _ in range(200):
        start = rng.randrange(-1, len(script.units))
        run_stream(script, start, _random_stream(rng, script),
                   ("no_backward_without_key", "provisional_ge_committed"))


# --------------------------------------------------------------------- misc checks

def test_no_gtk_and_no_clock():
    import ast
    tree = ast.parse(Path(aligner_mod.__file__).read_text())
    mods = set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    assert not mods & {"gi", "time", "datetime"}, mods
    assert not names & {"time", "perf_counter", "monotonic", "now"}, names


def test_goto_line_and_restart():
    script = load_text(yaml.safe_load(CASES[0].read_text())["script"])
    al = Aligner(script, start_unit=20)
    pos = al.on_user_key("goto_line", 5)
    assert (pos.line, pos.unit, pos.committed_unit) == (5, 34, 34)
    pos = al.on_user_key("restart")
    assert (pos.line, pos.unit, pos.committed_unit) == (0, -1, -1)
    pos = al.on_user_key("line_next")
    assert pos.line == 1 and pos.unit == 4


def test_params_from_config():
    from promptbro.config import AlignerConfig
    p = Params.from_config(AlignerConfig(tail_tokens=5, ahead_units=30))
    assert p.tail_tokens == 5 and p.ahead_units == 30 and p.match_sim == 0.75
    assert Params.from_config({"far_margin": 0.9}).far_margin == 0.9


# -------------------------------------------------------------------------- perf

def _generated_script(n_lines, words_per_line, seed=7):
    rng = random.Random(seed)
    syl = ["ka", "lo", "mi", "ter", "sun", "dra", "vel", "po", "rin", "tas", "bo", "gen",
           "shi", "mar", "ful", "ex", "ton", "ria", "pel", "cor"]
    vocab = ["".join(rng.choice(syl) for _ in range(rng.randint(1, 3))) for _ in range(1500)]
    vocab += ["the", "a", "and", "of", "to", "in", "we", "it"] * 40
    lines = [" ".join(rng.choice(vocab) for _ in range(words_per_line)) + "."
             for _ in range(n_lines)]
    return "\n".join(lines)


def _spoken_events(script, n_events, utt_words=8, seed=3):
    rng = random.Random(seed)
    words = [u.text.strip(".") for u in script.units]
    evs = []
    pos = 0
    t = 0.0
    while len(evs) < n_events and pos < len(words):
        seg = words[pos:pos + utt_words]
        pos += len(seg)
        e = speak(" ".join(seg), t0=t)
        evs += e
        t = e[-1].t + rng.uniform(0.2, 0.8)
    return evs[:n_events], pos


def test_perf_1000_lines_500_events():
    script = load_text(_generated_script(1000, 9))
    events, _ = _spoken_events(script, 500)
    assert len(events) == 500
    aligner_mod.token_sim.cache_clear()
    al = Aligner(script)
    t0 = time.perf_counter()
    for ev in events:
        pos = al.on_event(ev)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"500 events took {elapsed:.3f}s"
    spoken = sum(len(e.text.split()) for e in events if e.type == "final")
    assert pos.committed_unit >= spoken - 12, (pos, spoken)


# ------------------------------------------------------------- extra behaviors

_TWICE = """Welcome back to the show.
In 2026 we grew revenue 40% this year.
The plan costs $79 a month.
Their team shipped it right on time.
The quick brown fox jumps over the lazy dog.
Thanks for watching, and thanks for listening.
See you next week.
Some filler words go here for a while now.
More words that are not the same at all.
The quick brown fox jumps over the lazy dog.
Goodbye."""


def test_echo_guard_blocks_jump_to_later_copy():
    s = load_text(_TWICE)
    al = Aligner(s, start_unit=45)          # end of "See you next week."
    for e in speak("the quick brown fox jumps over the lazy dog"):
        pos = al.on_event(e)
        assert pos.unit == 45 and pos.committed_unit == 45


def test_far_jump_waits_for_confirmation():
    # Each target word occurs twice in the script (weight 0.675: strong, but evidence
    # for four of them is 2.7 < 3.5), so a far jump needs two agreeing updates. The
    # second copy sits beyond the 80-unit search window so it is not a rival.
    s = load_text("Start here now.\n" + "Filler text line.\n" * 10
                  + "Orange piano rocket garden meadow.\n" + "Filler text line.\n" * 40
                  + "Orange piano rocket garden meadow.\n")
    al = Aligner(s, start_unit=2)
    first = s.line_first_unit[11]
    pos = al.on_event(Event("partial", 0.0, "orange piano rocket garden"))
    assert pos.unit == 2                     # pending, not moved
    pos = al.on_event(Event("partial", 0.4, "orange piano rocket garden meadow"))
    assert pos.unit == first + 4


def test_skipped_words_and_filler_mid_sentence():
    s = load_text(yaml.safe_load(CASES[0].read_text())["script"])
    al = Aligner(s, start_unit=25)
    for e in speak("the quick fox jumps you know over the lazy dog"):
        pos = al.on_event(e)
    assert pos.unit == 34 and pos.committed_unit == 34


def test_equal_far_rivals_block_the_jump():
    s = load_text("Start here now.\n" + "Filler text line.\n" * 6
                  + "Orange piano rocket garden meadow.\n" + "Filler text line.\n"
                  + "Orange piano rocket garden meadow.\n")
    al = Aligner(s, start_unit=2)
    for e in speak("orange piano rocket garden meadow"):
        pos = al.on_event(e)
    assert pos.unit == 2


def test_new_utterance_never_drags_the_display_back():
    """The display stood far ahead of the committed cursor after an utterance (a preview jump
    the committed pass never confirmed, as in a real session). Words in the next
    utterance that match text between the two cursors must not walk the display back 3 words
    per update; only a revision inside one utterance may pull it back."""
    s = load_text((Path(__file__).parent / "data" / "reacquire.md").read_text())
    al = Aligner(s, start_unit=5)
    for e in speak("and our costs grew faster than our sales"):
        al.on_event(e)
    shown = al.position.unit
    assert shown == 35
    al.committed = 5                 # as if the committed pass had never followed the jump
    al.commit_pass.pending = al.preview_pass.pending = None
    for e in speak("i want to talk about where we are headed", t0=4.0):
        pos = al.on_event(e)
        assert pos.unit >= shown, pos


def test_aside_read_aloud_is_followed():
    """A short parenthetical is optional: read aloud, the cursor follows it (it used to be an
    ad-lib until the next line); skipped, it costs nothing."""
    text = ("We just created our plan for Q4\n(2027's plan is on the way) and\n"
            "as part of it, we rewrote our core values.")
    s = load_text(text)
    end_q4 = s.line_last_unit[0]
    al = Aligner(s, start_unit=end_q4)
    for e in speak("twenty twenty sevens plan is on the way"):
        pos = al.on_event(e)
    assert s.line_first_unit[1] + 3 <= pos.unit <= s.line_last_unit[1] - 1, pos
    assert pos.state == "tracking"
    al = Aligner(s, start_unit=end_q4)
    for e in speak("and as part of it we rewrote"):
        pos = al.on_event(e)
    assert pos.line == 2 and s.units[pos.unit].text == "rewrote"
