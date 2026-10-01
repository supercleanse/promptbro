"""Voice-following aligner (DESIGN §6.3-6.6).

A pure module: ``Aligner(script, params).on_event(ev) -> Position`` and
``.on_user_key(key) -> Position``. No GTK, no GLib, no clock reads; every time comes from
the events, so a recorded event stream replays to the same positions every time.

Two cursors. ``committed`` only moves on words the recognizer has stopped revising
(LocalAgreement-2 on partials, everything on a final). ``provisional`` is the display
cursor: it is re-derived from ``committed`` on every event from the full hypothesis, may
run ahead, and may retreat at most ``revise_back_max`` units, never below ``committed``.
Only a user key moves either cursor backward.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields
from functools import lru_cache
from typing import Any

from metaphone import doublemetaphone
from rapidfuzz.distance import JaroWinkler, Levenshtein

from .normalize import HOMOPHONES, STOPWORDS, Tok, normalize_hyp
from .script import Script

HOMOPHONE_SIM = 0.90
PREFIX_SIM = 0.80
PHONETIC_SIM = 0.82
INFLECTION_SIM = 0.80

# Transition kinds recorded per DP cell.
_NONE, _MATCH, _HGAP_CHEAP, _HGAP, _SGAP, _MISMATCH = range(6)


# --------------------------------------------------------------------------- params

@dataclass
class Params:
    """Every §6 constant. Config may override any of them by its lower-case name."""

    # §6.4 band alignment
    tail_tokens: int = 6
    back_units: int = 3
    ahead_units: int = 40
    ahead_resume: int = 80
    match_sim: float = 0.75
    mismatch: float = -0.7
    gap_hyp: float = 0.6
    gap_hyp_soft: float = 0.1
    gap_script: float = 0.35
    max_gap_run: int = 2
    end_on_penult_penalty: float = 0.4
    split_min_len: int = 6
    strong_weight: float = 0.6
    # §6.5 decision
    follow_max: int = 3
    follow_min_score: float = 0.6
    near_max: int = 12
    # Jump tiers are judged on new evidence only (words matched after the cursor; see
    # Candidate.new_*). Lines are counted from the line due next (the next unmatched word's).
    # Near: within near_max units or near_lines lines. Needs near_min_matches new matches with
    # near_min_strong strong ones, or two strong ones.
    near_lines: int = 2
    near_min_matches: int = 3
    near_min_strong: int = 1
    near_min_evidence: float = 1.0
    near_margin: float = 0.4
    near_move_cost: float = 0.6
    # Mid: up to mid_lines lines ahead (the search window always reaches that far).
    mid_lines: int = 4
    mid_min_matches: int = 3
    mid_min_strong: int = 2
    mid_min_evidence: float = 1.8
    mid_margin: float = 0.6
    mid_sure_evidence: float = 2.8
    # Far: anything further in the window (§6.5 values; no line-start credit).
    far_min_matches: int = 4
    far_min_strong: int = 3
    far_min_evidence: float = 2.5
    far_margin: float = 0.6
    far_sure_evidence: float = 3.5
    far_move_cost: float = 1.2
    # Evidence credit (near and mid) when the new words start a line or sentence, where
    # people pick a script back up.
    line_start_bonus: float = 0.3
    # Word-for-word runs (near and mid): each new match that directly follows the previous one
    # in both the speech and the script adds run_bonus evidence, and a run of run_as_strong
    # words counts as one more strong word. Four common words in script order ("you know who
    # they are") are far less likely by chance than four scattered hits.
    run_bonus: float = 0.15
    run_as_strong: int = 4
    confirm_tolerance: int = 2
    rival_separation: int = 3
    echo_lookback: int = 60
    echo_margin: float = 0.2
    dist_cost_per_unit: float = 0.02
    # §6.6 event loop
    tail_tentative: int = 2
    revise_back_max: int = 3
    history_tokens: int = 6
    history_reset_s: float = 2.5
    history_drop_misses: int = 2     # a final this many misses into an ad-lib leaves no history
    resume_silence_s: float = 4.0
    adlib_after: int = 8
    # optional extras (off by default)
    display_coasting: bool = False
    line_complete_on_final: bool = False
    hotwords: bool = False

    @classmethod
    def from_config(cls, cfg: Any = None) -> "Params":
        """Build from ``cfg.aligner`` (a dataclass/object) or a plain dict.

        Keys that are not Params fields are ignored (config.py already warns about them).
        """
        p = cls()
        if cfg is None:
            return p
        get = cfg.get if isinstance(cfg, dict) else (lambda k, d=None: getattr(cfg, k, d))
        for f in fields(cls):
            v = get(f.name, None)
            if v is None:
                continue
            default = getattr(p, f.name)
            if isinstance(default, bool):
                v = bool(v)
            elif isinstance(default, int):
                v = int(v)
            elif isinstance(default, float):
                v = float(v)
            setattr(p, f.name, v)
        return p


DEFAULT_PARAMS = Params()


# ----------------------------------------------------------------- token similarity

@lru_cache(maxsize=1 << 14)
def _dm_codes(w: str) -> tuple[str, ...]:
    return tuple(c for c in doublemetaphone(w) if c and len(c) >= 2)


def double_metaphone_match(h: str, s: str) -> bool:
    """True when the two words share a Double Metaphone code of length >= 2."""
    a = _dm_codes(h)
    return bool(a) and any(c in a for c in _dm_codes(s))


def _cpl(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


@lru_cache(maxsize=1 << 17)
def token_sim(h: str, s: str, h_is_open_last: bool = False) -> float:
    """How well spoken token ``h`` stands for script token ``s`` (0..1), per §6.3."""
    if h == s:
        return 1.0
    if s in HOMOPHONES.get(h, ()):
        return HOMOPHONE_SIM
    L = min(len(h), len(s))
    if h_is_open_last and len(h) >= 3 and s.startswith(h) and h not in STOPWORDS:
        return PREFIX_SIM  # half-said last word (a whole common word like "the" is not half of "themselves")
    if L <= 3:
        return 0.0  # short words: exact or homophone only
    best = 0.0
    cpl = _cpl(h, s)
    if L >= 5 and cpl >= 4 and cpl / L >= 0.75:
        best = INFLECTION_SIM  # balance / balancing
    if h[0] == s[0] and L >= 5:  # rhyme guard: light / night stay 0
        best = max(best, Levenshtein.normalized_similarity(h, s))
        if JaroWinkler.similarity(h, s) >= 0.92:
            best = max(best, 0.80)
    if best < PHONETIC_SIM and double_metaphone_match(h, s):
        best = PHONETIC_SIM
    return best


def spell_sim(h: str, s: str) -> float:
    """Spelling-only similarity for joins and splits ("health care" / "healthcare"): a
    sound-alike is too loose once words are glued together ("skilled" ~ "so glad")."""
    if h == s:
        return 1.0
    v = Levenshtein.normalized_similarity(h, s)
    return v if v >= 0.9 else 0.0


# ------------------------------------------------------------------ band alignment

@dataclass
class Candidate:
    end_unit: int
    score: float
    matched: int
    strong: int
    evidence: float
    start_unit: int
    # The same three counts restricted to units after the cursor that the path did not reach
    # by reading straight on from it (the part that is news). A jump is judged on these, so
    # words already read, or the next words read before skipping, cannot vouch for it.
    new_matched: int = 0
    new_strong: int = 0
    new_evidence: float = 0.0
    new_start: int = -1          # first unit after the cursor that this path matched
    prev_unit: int = -1          # the unit this path matched before its last one
    new_run: int = 0             # longest run of new units matched word for word, no gaps
    after_cursor: int = 0        # matches after the cursor (new_* start further on for a jump)
    run_after: int = 0           # word-for-word run of matches after the cursor ending at end_unit
    delta: int = 0
    net: float = 0.0


def align_band(script: Script, hyp: list[Tok], lo: int, hi: int,
               open_last: bool = False, params: Params = DEFAULT_PARAMS,
               cursor: int | None = None) -> list[Candidate]:
    """Local (Smith-Waterman) alignment of ``hyp`` against units ``lo..hi`` (§6.4).

    Returns one candidate per distinct end unit (the best-scoring path ending there).
    ``cursor``: matches after it are counted in ``Candidate.after_cursor``; those that are
    not a straight continuation from the cursor are evidence for a jump (``Candidate.new_*``).
    None = everything counts as new.
    """
    units = script.units
    lo = max(0, lo)
    hi = min(len(units) - 1, hi)
    n = hi - lo + 1
    m = len(hyp)
    if n <= 0 or m == 0:
        return []
    p = params
    ms = p.match_sim
    cap = p.max_gap_run
    htxt = [t.text for t in hyp]
    last = m - 1

    # Cost of treating hyp token r-1 as an extra spoken word (row r).
    hcost = [0.0] * (m + 1)
    hcheap = [False] * (m + 1)
    for r in range(1, m + 1):
        i = r - 1
        w = htxt[i]
        cheap = (hyp[i].is_soft_filler
                 or (i >= 1 and w == htxt[i - 1])                      # stutter
                 or (i + 1 < m and htxt[i + 1].startswith(w)))         # fragment
        hcheap[r] = cheap
        hcost[r] = p.gap_hyp_soft if cheap else p.gap_hyp

    # Per-column unit data.
    band = units[lo:hi + 1]
    ucol = [None] + band
    scost = [0.0] * (n + 1)
    smatch = [True] * (n + 1)
    scount = [1] * (n + 1)
    strong = [0] * (n + 1)
    wcol = [0.0] * (n + 1)
    aftc = [False] * (n + 1)
    lead = [False] * (n + 1)      # at or before the first content word after the cursor
    after = -1 if cursor is None else cursor + 1
    fc = after
    if cursor is not None:
        while fc < len(units) and not (units[fc].matchable and units[fc].weight >= 0.3):
            fc += 1
    for c in range(1, n + 1):
        u = ucol[c]
        aftc[c] = u.idx >= after
        lead[c] = cursor is not None and u.idx <= fc
        wcol[c] = u.weight
        if not u.matchable:
            # a stage direction / aside: free to pass over; if it is read aloud it can match
            smatch[c] = bool(getattr(u, "optional", False) and u.forms)
            wcol[c] = getattr(u, "optional_weight", 0.0)
            scost[c] = 0.0
            scount[c] = 0
        else:
            scost[c] = p.gap_script * max(u.weight, 0.1)
            # Skipping a very weak word ("the") does not count toward the gap-run cap.
            scount[c] = 0 if u.weight < 0.3 else 1
            strong[c] = 1 if u.weight >= p.strong_weight else 0

    # Match table: mt[r][c] = list of (k hyp tokens, span units, gain, n_strong, evidence).
    mt: list[list[list | None]] = [[None] * (n + 1) for _ in range(m + 1)]
    for c in range(1, n + 1):
        if not smatch[c]:
            continue
        u = ucol[c]
        w = wcol[c]
        for f in u.forms:
            k = len(f)
            if k > m:
                continue
            flast = f[-1]
            for r in range(k, m + 1):
                s_last = token_sim(htxt[r - 1], flast, open_last and r - 1 == last)
                if s_last < ms:
                    continue
                tot = s_last
                ok = True
                for q in range(k - 1):
                    hi_i = r - k + q
                    sq = token_sim(htxt[hi_i], f[q], open_last and hi_i == last)
                    if sq < ms:
                        ok = False
                        break
                    tot += sq
                if not ok:
                    continue
                mean = tot / k
                entry = (k, 1, w * (1.0 + mean), strong[c], w * mean)
                cell = mt[r][c]
                if cell is None:
                    mt[r][c] = [entry]
                else:
                    for idx, e in enumerate(cell):
                        if e[0] == k and e[1] == 1:
                            if entry[2] > e[2]:
                                cell[idx] = entry
                            break
                    else:
                        cell.append(entry)
        # (b) join: two spoken tokens vs one long written token ("health care").
        if m >= 2:
            for f in u.forms:
                if len(f) != 1 or len(f[0]) < p.split_min_len:
                    continue
                for r in range(2, m + 1):
                    if len(htxt[r - 2]) < 2 or len(htxt[r - 1]) < 2:
                        continue
                    joined = htxt[r - 2] + htxt[r - 1]
                    if abs(len(joined) - len(f[0])) > 2:
                        continue
                    sj = spell_sim(joined, f[0])
                    if sj >= ms:
                        entry = (2, 1, w * (1.0 + sj), strong[c], w * sj)
                        cell = mt[r][c]
                        if cell is None:
                            mt[r][c] = [entry]
                        else:
                            cell.append(entry)
    # (c) split: one long spoken token vs two written units ("turbocharger").
    long_rows = [r for r in range(1, m + 1) if len(htxt[r - 1]) >= p.split_min_len]
    if long_rows:
        for c in range(2, n + 1):
            if not (smatch[c] and smatch[c - 1]):
                continue
            a, b = ucol[c - 1], ucol[c]
            if len(a.forms[0]) != 1 or len(b.forms[0]) != 1:
                continue
            if len(a.forms[0][0]) < 3 or len(b.forms[0][0]) < 3:
                continue  # "purpose" is not "purpose" + "I"
            joined = a.forms[0][0] + b.forms[0][0]
            for r in long_rows:
                h = htxt[r - 1]
                if abs(len(h) - len(joined)) > 2:
                    continue
                sj = spell_sim(h, joined)
                if sj >= ms:
                    wsum = wcol[c - 1] + wcol[c]
                    entry = (1, 2, wsum * (1.0 + sj), strong[c - 1] + strong[c], wsum * sj)
                    cell = mt[r][c]
                    if cell is None:
                        mt[r][c] = [entry]
                    else:
                        cell.append(entry)

    # DP. Row 0 and column 0 stay zero (local alignment).
    zeros_f = [0.0] * (n + 1)
    zeros_i = [0] * (n + 1)
    H = [zeros_f[:] for _ in range(m + 1)]
    M = [zeros_i[:] for _ in range(m + 1)]
    S = [zeros_i[:] for _ in range(m + 1)]
    E = [zeros_f[:] for _ in range(m + 1)]
    HG = [zeros_i[:] for _ in range(m + 1)]
    SG = [zeros_i[:] for _ in range(m + 1)]
    LT = [zeros_i[:] for _ in range(m + 1)]
    EU = [zeros_i[:] for _ in range(m + 1)]
    ST = [zeros_i[:] for _ in range(m + 1)]
    MN = [zeros_i[:] for _ in range(m + 1)]
    MC = [zeros_i[:] for _ in range(m + 1)]       # matches after the cursor
    RC = [zeros_i[:] for _ in range(m + 1)]       # run of word-for-word matches after the cursor
    CT = [zeros_i[:] for _ in range(m + 1)]       # 1: still reading straight on from the cursor
    SN = [zeros_i[:] for _ in range(m + 1)]
    EN = [zeros_f[:] for _ in range(m + 1)]
    FN = [[-1] * (n + 1) for _ in range(m + 1)]
    PV = [[-1] * (n + 1) for _ in range(m + 1)]   # unit matched just before the last match
    RN = [zeros_i[:] for _ in range(m + 1)]       # length of the run of new matches ending here
    RX = [zeros_i[:] for _ in range(m + 1)]       # longest such run on the path
    mismatch = p.mismatch

    for r in range(1, m + 1):
        Hr, Hp = H[r], H[r - 1]
        mtr = mt[r]
        hc = hcost[r]
        hkind = _HGAP_CHEAP if hcheap[r] else _HGAP
        Mr, Sr, Er, HGr, SGr, LTr, EUr, STr = M[r], S[r], E[r], HG[r], SG[r], LT[r], EU[r], ST[r]
        Mp, Sp, Ep, HGp, SGp, EUp, STp = M[r - 1], S[r - 1], E[r - 1], HG[r - 1], SG[r - 1], EU[r - 1], ST[r - 1]
        MNr, SNr, ENr, FNr, PVr, RNr, RXr, MCr = MN[r], SN[r], EN[r], FN[r], PV[r], RN[r], RX[r], MC[r]
        MNp, SNp, ENp, FNp, PVp, RXp, MCp = MN[r - 1], SN[r - 1], EN[r - 1], FN[r - 1], PV[r - 1], RX[r - 1], MC[r - 1]
        RCr, CTr = RC[r], CT[r]
        for c in range(1, n + 1):
            up = Hp[c]
            left = Hr[c - 1]
            diag = Hp[c - 1]
            matches = mtr[c]
            if matches is None and up <= 0.0 and left <= 0.0 and diag <= 0.0:
                continue
            best = 0.0
            kind = _NONE
            src_r = src_c = 0
            add_m = add_s = 0
            add_e = 0.0
            if matches is not None:
                for k, span, gain, ns, ev in matches:
                    pr, pc = r - k, c - span
                    if pc < 0:
                        continue
                    v = H[pr][pc] + gain
                    if v > best:
                        best, kind, src_r, src_c = v, _MATCH, pr, pc
                        add_m, add_s, add_e = span, ns, ev
            if diag > 0.0 and HGp[c - 1] < cap and SGp[c - 1] < cap:
                v = diag + mismatch
                if v > best:
                    best, kind, src_r, src_c = v, _MISMATCH, r - 1, c - 1
            if up > 0.0 and HGp[c] < cap:
                v = up - hc
                if v > best:
                    best, kind, src_r, src_c = v, hkind, r - 1, c
            if left > 0.0 and (SGr[c - 1] < cap or not scount[c]):
                v = left - scost[c]
                if v > best:
                    best, kind, src_r, src_c = v, _SGAP, r, c - 1
            if kind == _NONE or best <= 0.0:
                continue
            Hr[c] = best
            LTr[c] = kind
            if kind == _MATCH:
                pm = M[src_r][src_c]
                Mr[c] = pm + add_m
                Sr[c] = S[src_r][src_c] + add_s
                Er[c] = E[src_r][src_c] + add_e
                HGr[c] = 0
                SGr[c] = 0
                EUr[c] = lo + c - 1
                STr[c] = ST[src_r][src_c] if pm else lo + src_c  # first unit of this match
                PVr[c] = EU[src_r][src_c] if pm else -1
                pmn = MN[src_r][src_c]
                pmc = MC[src_r][src_c]
                MCr[c] = pmc + (add_m if aftc[c] else 0)
                cont = 0
                if aftc[c]:
                    contig_c = LT[src_r][src_c] == _MATCH and RC[src_r][src_c] > 0
                    RCr[c] = (RC[src_r][src_c] if contig_c else 0) + add_m
                    # Words read straight on from the cursor (nothing but weak words skipped)
                    # are following, not evidence for a jump further on: in "themselves [and
                    # it's] going", "themselves" must not vouch for the "going" two lines down.
                    cont = 1 if (lead[src_c + 1] if pmc == 0 else (contig_c and CT[src_r][src_c])) else 0
                    CTr[c] = cont
                if aftc[c] and not cont:
                    MNr[c] = pmn + add_m
                    SNr[c] = SN[src_r][src_c] + add_s
                    ENr[c] = EN[src_r][src_c] + add_e
                    FNr[c] = FN[src_r][src_c] if pmn else (
                        lo + src_c if aftc[src_c + 1] else lo + c - 1)
                    # contiguous: the previous step was a new match of the unit just before
                    contig = LT[src_r][src_c] == _MATCH and RN[src_r][src_c] > 0
                    RNr[c] = (RN[src_r][src_c] if contig else 0) + add_m
                    RXr[c] = max(RX[src_r][src_c], RNr[c])
                else:
                    MNr[c], SNr[c], ENr[c], FNr[c] = pmn, SN[src_r][src_c], EN[src_r][src_c], FN[src_r][src_c]
                    RXr[c] = RX[src_r][src_c]
            elif kind == _MISMATCH:
                Mr[c], Sr[c], Er[c] = Mp[c - 1], Sp[c - 1], Ep[c - 1]
                HGr[c], SGr[c] = HGp[c - 1] + 1, SGp[c - 1] + 1
                EUr[c], STr[c] = EUp[c - 1], STp[c - 1]
                MNr[c], SNr[c], ENr[c], FNr[c] = MNp[c - 1], SNp[c - 1], ENp[c - 1], FNp[c - 1]
                PVr[c], RXr[c], MCr[c] = PVp[c - 1], RXp[c - 1], MCp[c - 1]

            elif kind == _SGAP:
                Mr[c], Sr[c], Er[c] = Mr[c - 1], Sr[c - 1], Er[c - 1]
                HGr[c], SGr[c] = HGr[c - 1], SGr[c - 1] + scount[c]
                EUr[c], STr[c] = EUr[c - 1], STr[c - 1]
                MNr[c], SNr[c], ENr[c], FNr[c] = MNr[c - 1], SNr[c - 1], ENr[c - 1], FNr[c - 1]
                PVr[c], RXr[c], MCr[c] = PVr[c - 1], RXr[c - 1], MCr[c - 1]

            else:  # hyp gap
                Mr[c], Sr[c], Er[c] = Mp[c], Sp[c], Ep[c]
                HGr[c], SGr[c] = HGp[c] + 1, SGp[c]
                EUr[c], STr[c] = EUp[c], STp[c]
                MNr[c], SNr[c], ENr[c], FNr[c] = MNp[c], SNp[c], ENp[c], FNp[c]
                PVr[c], RXr[c], MCr[c] = PVp[c], RXp[c], MCp[c]


    # Candidates: last row ending on a match (or on a cheap stutter/filler after a match),
    # plus the second-to-last row ending on a match, penalized (last word may be half-said).
    out: dict[int, Candidate] = {}

    def add(r: int, c: int, score: float) -> None:
        if score <= 0.0 or M[r][c] <= 0:
            return
        end = EU[r][c]
        cur = out.get(end)
        if cur is None or score > cur.score:
            out[end] = Candidate(end, score, M[r][c], S[r][c], E[r][c], ST[r][c],
                                 MN[r][c], SN[r][c], EN[r][c], FN[r][c], PV[r][c], RX[r][c],
                                 MC[r][c], RC[r][c] if LT[r][c] == _MATCH else 0)

    Hm, LTm = H[m], LT[m]
    for c in range(1, n + 1):
        if Hm[c] > 0.0 and (LTm[c] == _MATCH or LTm[c] == _HGAP_CHEAP):
            add(m, c, Hm[c])
    if m >= 2:
        Hq, LTq = H[m - 1], LT[m - 1]
        pen = p.end_on_penult_penalty
        for c in range(1, n + 1):
            if Hq[c] > 0.0 and LTq[c] == _MATCH:
                add(m - 1, c, Hq[c] - pen)
    return list(out.values())


# ------------------------------------------------------------------------- locate

@dataclass
class Pending:
    end: int
    tail: tuple[str, ...]


@dataclass
class PassState:
    """Per-pass memory (committed pass and preview pass each keep one).

    ``outcome`` of the last locate: "moved", "at_cursor" (the tail ends exactly on the
    cursor, e.g. a repeated partial), or "miss".
    """
    pending: Pending | None = None
    outcome: str = "miss"


def move_cost(delta: int, p: Params = DEFAULT_PARAMS, near: bool | None = None) -> float:
    """Distance penalty. ``near`` (when known) says whether a jump is in the near tier, which
    is defined in lines as well as units (see ``_locate``)."""
    if delta <= 0:
        return 0.0
    d = p.dist_cost_per_unit * delta
    if delta <= p.follow_max:
        return d
    if near is None:
        near = delta <= p.near_max
    if near:
        return p.near_move_cost + d
    return p.far_move_cost + d


def _mcount(script: Script) -> list[int]:
    """Prefix counts of matchable units: out[i] = matchable units among units[:i]."""
    cached = getattr(script, "_aligner_mcount", None)
    if cached is not None and len(cached) == len(script.units) + 1:
        return cached
    out = [0]
    for u in script.units:
        out.append(out[-1] + (1 if u.matchable else 0))
    try:
        script._aligner_mcount = out  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover
        pass
    return out


def effective_delta(script: Script, cursor: int, end: int) -> int:
    """Distance in matchable units (stage directions are free to pass over)."""
    if end <= cursor:
        return end - cursor
    mc = _mcount(script)
    return mc[end + 1] - mc[cursor + 1]


def _next_matchable(script: Script, cursor: int) -> int | None:
    for j in range(cursor + 1, len(script.units)):
        if script.units[j].matchable:
            return j
    return None


def _heard_at(unit, tail: list[Tok], open_last: bool) -> bool:
    """Does one of the last two spoken words stand for this unit's final word, not just sound like it?"""
    lasts = {f[-1] for f in unit.forms}
    for i in (len(tail) - 1, len(tail) - 2):
        if i < 0:
            continue
        h = tail[i].text
        for w in lasts:
            if h == w or w in HOMOPHONES.get(h, ()):
                return True
            if open_last and i == len(tail) - 1 and len(h) >= 3 and w.startswith(h) and h not in STOPWORDS:
                return True
    return False


def locate(script: Script, tail: list[Tok], cursor: int, open_last: bool,
           state: PassState, ahead: int, params: Params = DEFAULT_PARAMS,
           allow_fast: bool = False, confirm: "tuple[Pending | None, ...]" = (),
           shown: int | None = None) -> int | None:
    """New cursor for ``tail`` read from ``cursor``, or None to hold (§6.5).

    ``confirm``: earlier agreeing evidence for a far jump (the other pass's pending jump),
    which may confirm this one. ``shown``: where the display cursor stands; when an earlier
    update already jumped it past ``cursor``, reading on from there is following, not a new jump.
    """
    state.outcome = "miss"
    r = _locate(script, tail, cursor, open_last, state, ahead, params, allow_fast, confirm, shown)
    if r is not None:
        state.outcome = "moved"
    return r


def _line_of(script: Script, unit: int) -> int:
    if unit < 0:
        return 0
    return script.units[min(unit, len(script.units) - 1)].line


def _ref_line(script: Script, cursor: int) -> int:
    """The line the speaker is due to read next: the line of the next matchable unit."""
    nxt = _next_matchable(script, cursor)
    return _line_of(script, cursor if nxt is None else nxt)


def _starts_phrase(script: Script, unit: int) -> bool:
    """Is ``unit`` where a reader would pick the script up: the start of a line or a sentence,
    ignoring weak words ("and", "the") and asides before it?"""
    units = script.units
    j = unit - 1
    first = script.line_first_unit[units[unit].line]
    while j >= first:
        u = units[j]
        if u.text.rstrip("\"')]”’").endswith((".", "?", "!", ":", ";")):
            return True
        if u.matchable and u.weight >= 0.3:
            return False
        j -= 1
    return True


def _band_hi(script: Script, cursor: int, ahead: int, p: Params) -> int:
    """Search window end: ``ahead`` units, but never less than ``mid_lines`` lines ahead (a
    script of short lines still gets a four-line window; long lines get the units)."""
    N = len(script.units)
    hi = cursor + ahead
    nl = len(script.lines)
    if nl and p.mid_lines > 0:
        ln = min(nl - 1, _ref_line(script, cursor) + p.mid_lines)
        hi = max(hi, min(script.line_last_unit[ln], cursor + 2 * ahead))
    return min(N - 1, hi)


def _locate(script, tail, cursor, open_last, state, ahead, params, allow_fast, confirm=None,
            shown=None):
    p = params
    units = script.units
    N = len(units)
    if not tail or N == 0:
        return None
    # Fast path: a lone closed word equal to the next word moves at once.
    if allow_fast and not open_last and len(tail) == 1:
        nxt = _next_matchable(script, cursor)
        if nxt is not None and (tail[0].text,) in units[nxt].forms:
            state.pending = None
            return nxt
    lo = max(0, cursor - p.back_units)
    hi = _band_hi(script, cursor, ahead, p)
    if hi <= cursor:
        return None
    cands = align_band(script, tail, lo, hi, open_last, p, cursor=cursor)
    if not cands:
        return None
    ref = _ref_line(script, cursor)
    for c in cands:
        c.delta = effective_delta(script, cursor, c.end_unit)
        near = None
        if c.delta > p.follow_max:
            start = c.new_start if c.new_start >= 0 else c.end_unit
            near = c.delta <= p.near_max or _line_of(script, start) - ref <= p.near_lines
        c.net = c.score - move_cost(c.delta, p, near)
    best = max(cands, key=lambda c: (c.net, -abs(c.end_unit - cursor)))
    delta = best.delta
    if delta == 0 and best.end_unit > cursor and best.matched >= 2 \
            and _heard_at(units[best.end_unit], tail, open_last):
        state.pending = None
        return best.end_unit  # reading an aside aloud: follow it (it costs no distance)
    if delta <= 0:
        if delta == 0:
            state.outcome = "at_cursor"
        return None  # best explanation is text already read: hold
    rivals = [c.net for c in cands if abs(c.end_unit - best.end_unit) > p.rival_separation]
    lead = best.net - max(rivals, default=0.0)

    # A move of two or more words must land on a word that was actually heard (exact, homophone, or the start of a
    # half-said word), not a sound-alike: "their team shipped their team" (a restart) must not
    # carry the cursor from "shipped" to "time" on the strength of team ~ time.
    if delta >= 2 and not _heard_at(units[best.end_unit], tail, open_last):
        return None

    # The landing word must earn the words it skips. A weak one ("and", "a", "I") never
    # carries the cursor over a content word, and any word that does needs a second new match
    # behind it: "you know what" must not skip "Outside of" to reach "what".
    if delta >= 2:
        pv = max(best.prev_unit, cursor)
        skipped_content = any(u.matchable and u.weight >= 0.3 for u in units[pv + 1:best.end_unit])
        if units[best.end_unit].weight < 0.3 and (best.after_cursor < 2 or skipped_content):
            return None
        if skipped_content and best.after_cursor < 2:
            return None

    # Straight reading: the path picks up right after the cursor and matches every content
    # word up to its end (a burst of words in one partial). That is following, however far.
    straight = (best.run_after >= 2 and best.run_after == best.after_cursor and not any(
        u.matchable and u.weight >= 0.3 for u in units[cursor + 1:best.end_unit - best.run_after + 1]))
    # Reading on from where the display already jumped (an earlier update accepted that jump;
    # the committed cursor catches up once its stable words agree).
    if shown is not None and effective_delta(script, cursor, shown) > p.follow_max \
            and best.after_cursor >= 2:
        d = effective_delta(script, shown, best.end_unit)
        if -p.confirm_tolerance <= d <= p.follow_max:
            state.pending = None
            return best.end_unit

    if delta <= p.follow_max or straight:
        need = 1 if delta == 1 else 2
        if best.matched >= need and best.score >= p.follow_min_score:
            state.pending = None
            return best.end_unit
        return None

    # A jump. Only words matched after the cursor count as evidence for it: in "a full
    # restructure and honestly I", the words already read must not vouch for a jump to the
    # next "I". The bar is lower where people resume (the start of a line or sentence) and
    # for the next couple of lines, higher further on.
    nm, ns, ne, start = best.new_matched, best.new_strong, best.new_evidence, best.new_start
    if start < 0:
        return None
    lines_ahead = _line_of(script, start) - ref
    bonus = p.line_start_bonus if _starts_phrase(script, start) else 0.0
    bonus += p.run_bonus * max(0, best.new_run - 1)
    ns_far = ns
    if best.new_run >= p.run_as_strong:
        ns += 1

    # Echo guard: if the phrase is explained about as well by text already read, hold.
    if cursor >= 0:
        back = align_band(script, tail, max(0, cursor - p.echo_lookback), cursor, open_last, p)
        if back and max(c.score for c in back) >= best.score - p.echo_margin:
            return None

    if delta <= p.near_max or lines_ahead <= p.near_lines:
        ok = ((nm >= p.near_min_matches and ns >= p.near_min_strong) or (ns >= 2 and nm >= 2)) \
            and ne + bonus >= p.near_min_evidence and lead >= p.near_margin
        if ok:
            state.pending = None
            return best.end_unit
        return None

    if lines_ahead <= p.mid_lines:
        ok = (nm >= p.mid_min_matches and ns >= p.mid_min_strong
              and ne + bonus >= p.mid_min_evidence and lead >= p.mid_margin)
        sure = ne + bonus >= p.mid_sure_evidence
    else:
        ok = (nm >= p.far_min_matches and ns_far >= p.far_min_strong
              and ne >= p.far_min_evidence and lead >= p.far_margin)
        sure = ne >= p.far_sure_evidence
    if not ok:
        return None
    if sure:
        state.pending = None
        return best.end_unit
    # Otherwise wait for a second, different tail that lands in the same place. The other
    # pass's pending counts too (the preview sees every partial, the committed pass only the
    # stable words; on a final both read the same text), and so does a display cursor that an
    # earlier update already moved there.
    key = tuple(t.text for t in tail)
    for pend in (state.pending, *(confirm or ())):
        if pend is not None and pend.tail != key and abs(pend.end - best.end_unit) <= p.confirm_tolerance:
            state.pending = None
            return best.end_unit
    state.pending = Pending(best.end_unit, key)
    return None


# --------------------------------------------------------------------- event loop

@dataclass
class Event:
    type: str            # "partial" | "final"
    t: float             # seconds (audio or wall time, from the recognizer)
    text: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        return cls(str(d.get("type", "partial")), float(d.get("t", 0.0)), str(d.get("text", "")))


@dataclass
class Position:
    line: int = 0
    unit: int = -1                 # provisional (display) cursor: last unit spoken
    committed_unit: int = -1
    line_progress: float = 0.0     # chars spoken / line chars
    state: str = "holding"         # tracking | holding | adlib | paused


def _lcp(a: list[Tok], b: list[Tok]) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i].text == b[i].text:
        i += 1
    return i


@dataclass
class Aligner:
    script: Script
    params: Params = field(default_factory=Params)
    start_unit: int = -1

    def __post_init__(self) -> None:
        self.paused = False
        self.reset(self.start_unit)

    # ------------------------------------------------------------------ state
    def reset(self, unit: int = -1) -> Position:
        n = len(self.script.units)
        unit = max(-1, min(unit, n - 1))
        self.committed = unit
        self.provisional = unit
        self.history: list[Tok] = []
        self.prev_partial: list[Tok] = []
        self.last_voice_t = -math.inf
        self.commit_pass = PassState()
        self.preview_pass = PassState()
        self.misses = 0
        self.ignore_before = 0
        self._utt_len = 0
        self._key_line: int | None = None
        self._last_had_speech = False
        self._utt_floor = unit
        return self.emit()

    def set_paused(self, paused: bool) -> Position:
        self.paused = bool(paused)
        return self.emit()

    # ------------------------------------------------------------------ events
    def on_event(self, ev: Event) -> Position:
        p = self.params
        final = ev.type == "final"
        silence = ev.t - self.last_voice_t
        if silence > p.history_reset_s:
            self.history = []
        ahead = p.ahead_resume if silence > p.resume_silence_s else p.ahead_units
        all_toks = normalize_hyp(ev.text, self.script, self.committed)
        self._utt_len = 0 if final else len(all_toks)
        toks = all_toks[self.ignore_before:]
        if not toks:
            self._last_had_speech = False
            if final:
                self.prev_partial = []
                self.ignore_before = 0
            return self.emit()
        self._last_had_speech = True
        self.last_voice_t = ev.t
        before = self.provisional

        prev = self.prev_partial
        if final:
            stable = toks
        else:
            stable = toks[:max(0, min(_lcp(prev, toks), len(toks) - p.tail_tentative))]
            self.prev_partial = toks
        extends = _lcp(prev, toks) == len(prev)

        # 1) committed pass: stable words only
        if stable:
            tail = (self.history + stable)[-p.tail_tokens:]
            r = locate(self.script, tail, self.committed, False, self.commit_pass, ahead, p,
                       allow_fast=final, confirm=self._anchors(self.preview_pass),
                       shown=self.provisional)
            if r is not None and r > self.committed:
                self.committed = r
        if final and p.line_complete_on_final and self.committed >= 0:
            ln = self.script.units[self.committed].line
            last = self.script.line_last_unit[ln]
            if 0 < last - self.committed <= 2:
                self.committed = last

        # 2) preview pass: full hypothesis, re-derived from committed every time
        tail = (self.history + toks)[-p.tail_tokens:]
        r = locate(self.script, tail, self.committed, not final, self.preview_pass, ahead, p,
                   allow_fast=final, confirm=self._anchors(self.commit_pass),
                   shown=self.provisional)
        if r is not None or self.preview_pass.outcome == "at_cursor":
            self.misses = 0
        else:
            self.misses += 1
        if r is not None:
            target = r
        else:
            # Deviation from §6.6 (see BUILD_LOG T3): with no fresh evidence, keep the
            # display where it is unless the recognizer revised earlier words.
            target = self.provisional if extends else self.committed
        if target < self.provisional:
            # A revision may pull the display back a little, but only within the utterance
            # that put it there: a new utterance never drags it back over what the last one
            # settled (it slid back word by word after a far preview jump otherwise).
            target = max(target, self.provisional - p.revise_back_max, self._utt_floor)
        self.provisional = max(self.committed, target)

        if final:
            # Carry the end of a read utterance into the next one, but not an ad-lib: words
            # that matched nothing only crowd the next tail.
            if self.misses >= p.history_drop_misses:
                self.history = []
            else:
                self.history = (self.history + toks)[-p.history_tokens:]
            self.prev_partial = []
            self.ignore_before = 0
            self._utt_floor = self.provisional
        if self.provisional != before:
            self._key_line = None
        return self.emit()

    def _anchors(self, other: PassState) -> tuple:
        """Earlier evidence that can confirm a far jump: the other pass's pending one."""
        return (other.pending,)

    # ------------------------------------------------------------------- keys
    def on_user_key(self, key: str, line: int | None = None) -> Position:
        """The only way to move backward. Keys: line_prev, line_next, restart, goto_line."""
        s = self.script
        nlines = len(s.lines)
        if nlines == 0:
            return self.emit()
        cur = self.emit().line
        if key == "line_prev":
            tgt = max(0, cur - 1)
            unit = s.line_first_unit[tgt] - 1
        elif key == "line_next":
            unit = s.line_last_unit[cur]
            tgt = min(nlines - 1, cur + 1)
        elif key == "restart":
            tgt, unit = 0, -1
        elif key == "goto_line":
            if line is None:
                raise ValueError("goto_line needs a line number")
            tgt = max(0, min(nlines - 1, int(line)))
            unit = s.line_first_unit[tgt] - 1
        else:
            raise ValueError(f"unknown key: {key!r}")
        self.committed = self.provisional = unit
        self._utt_floor = unit
        self._key_line = tgt
        self.history = []
        self.prev_partial = []
        self.commit_pass.pending = None
        self.preview_pass.pending = None
        self.misses = 0
        self.ignore_before = self._utt_len  # words heard before the key don't count
        return self.emit()

    # ------------------------------------------------------------------- emit
    def emit(self) -> Position:
        s = self.script
        u = self.provisional
        if self._key_line is not None:
            line, prog = self._key_line, 0.0
        elif u >= 0 and s.units:
            unit = s.units[u]
            line = unit.line
            text = s.lines[line].text if line < len(s.lines) else ""
            prog = min(1.0, unit.char_end / len(text)) if text else 1.0
        else:
            line, prog = 0, 0.0
        if self.paused:
            state = "paused"
        elif self.misses >= self.params.adlib_after:
            state = "adlib"
        elif self.misses == 0 and self._last_had_speech:
            state = "tracking"
        else:
            state = "holding"
        return Position(line, u, self.committed, prog, state)

    @property
    def position(self) -> Position:
        return self.emit()
