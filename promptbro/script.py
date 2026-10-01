"""Script model: text/Markdown -> display lines -> units (DESIGN §6.1)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import STOPWORDS, Form, spoken_forms

STAGE_MAX_WORDS = 8
STRONG_WEIGHT = 0.6


@dataclass
class Unit:
    idx: int
    line: int
    char_start: int
    char_end: int
    text: str
    forms: list[Form]
    weight: float = 1.0
    is_stop: bool = False
    matchable: bool = True
    number_derived: bool = False
    # A short bracketed aside ("(pause)", "(2027's plan is on the way)") is not required
    # reading: it is never a gap to skip, but when the speaker does read it aloud the aligner
    # may follow along with this weight instead of treating it as an ad-lib.
    optional: bool = False
    optional_weight: float = 0.0

    @property
    def strong(self) -> bool:
        return self.matchable and self.weight >= STRONG_WEIGHT


@dataclass
class DisplayLine:
    idx: int
    text: str
    para: int
    first_unit: int
    last_unit: int


@dataclass
class Script:
    lines: list[DisplayLine] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)
    line_first_unit: list[int] = field(default_factory=list)
    line_last_unit: list[int] = field(default_factory=list)
    df: Counter = field(default_factory=Counter)
    max_line_chars: int = 90


# ---------------------------------------------------------------- Markdown

_FENCE = re.compile(r"^\s*(```|~~~)")
_HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_TAG = re.compile(r"</?[A-Za-z][^>]*>|<!--.*?-->")


def _clean_line(line: str) -> str:
    line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
    line = re.sub(r"\s+#+\s*$", "", line)
    line = re.sub(r"^\s*(>\s?)+", "", line)
    line = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line)
    line = _LINK.sub(r"\1", line)
    line = _TAG.sub("", line)
    line = line.replace("`", "").replace("~~", "")
    line = re.sub(r"\*+", "", line)
    line = re.sub(r"(?<!\w)_+|_+(?!\w)", "", line)
    return re.sub(r"[ \t]+", " ", line).strip()


def _paragraph_lines(text: str) -> list[tuple[int, str]]:
    """(paragraph index, cleaned source line) for every non-empty line."""
    out: list[tuple[int, str]] = []
    para = 0
    pending_break = False
    in_fence = False
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _FENCE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if _HR.match(raw):
            cleaned = ""
        else:
            cleaned = _clean_line(raw)
        if not cleaned:
            pending_break = True
            continue
        if pending_break and out:
            para += 1
        pending_break = False
        out.append((para, cleaned))
    return out


# ---------------------------------------------------------- Stage directions

_STAGE = re.compile(r"\[[^\]]*\]|\([^)]*\)")


def _stage_spans(line: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _STAGE.finditer(line)
            if len(m.group(0)[1:-1].split()) <= STAGE_MAX_WORDS]


# ------------------------------------------------------------ Line splitting

_ABBREV_END = {"dr", "mr", "mrs", "ms", "st", "vs", "etc", "e.g", "i.e", "approx", "no"}
_SENT = re.compile(r"[.?!][\"')\]”’]*\s+")
_CLAUSE = re.compile(r"[;:,—]\s+")


def _bad_sentence_cut(text: str, start: int) -> bool:
    m = re.search(r"(\S+)$", text[:start])
    if not m:
        return True
    word = m.group(1).rstrip(".?!\"')]").lower()
    return word in _ABBREV_END or (len(word) == 1 and word.isalpha())


def _split(text: str, max_chars: int, level: int = 0) -> list[tuple[int, int]]:
    """Spans (start, end) of pieces of text, each <= max_chars where possible."""
    if len(text) <= max_chars or level > 1:
        return [(0, len(text))]
    pat = (_SENT, _CLAUSE)[level]
    cuts = [m.end() for m in pat.finditer(text)
            if level == 1 or not _bad_sentence_cut(text, m.start() + 1)]
    bounds = [0] + [c for c in cuts if 0 < c < len(text)] + [len(text)]
    segs = list(zip(bounds[:-1], bounds[1:]))
    if len(segs) == 1:
        return _split(text, max_chars, level + 1)
    packed: list[tuple[int, int]] = []
    cur_s, cur_e = segs[0]
    for s, e in segs[1:]:
        if e - cur_s <= max_chars:
            cur_e = e
        else:
            packed.append((cur_s, cur_e))
            cur_s, cur_e = s, e
    packed.append((cur_s, cur_e))
    out: list[tuple[int, int]] = []
    for s, e in packed:
        piece = text[s:e]
        if len(piece.rstrip()) > max_chars:
            out += [(s + a, s + b) for a, b in _split(piece, max_chars, level + 1)]
        else:
            out.append((s, e))
    return out


# --------------------------------------------------------------------- Build

def _make_units(piece: str, line_idx: int, stage: list[tuple[int, int]],
                offset: int, first_idx: int) -> list[Unit]:
    units: list[Unit] = []
    for m in re.finditer(r"\S+", piece):
        pos = m.start() + offset
        in_stage = any(s <= pos < e for s, e in stage)
        forms = spoken_forms(m.group(0))
        raw = m.group(0)
        units.append(Unit(
            idx=first_idx + len(units), line=line_idx, char_start=m.start(),
            char_end=m.end(), text=raw, forms=forms,
            matchable=bool(forms) and not in_stage,
            optional=bool(forms) and in_stage,
            number_derived=any(c.isdigit() or c in "$%" for c in raw)))
    return units


def load_text(text: str, max_line_chars: int = 90) -> Script:
    script = Script(max_line_chars=max_line_chars)
    for para, src in _paragraph_lines(text):
        stage = _stage_spans(src)
        for a, b in _split(src, max_line_chars):
            piece = src[a:b].strip()
            if not piece:
                continue
            a += len(src[a:b]) - len(src[a:b].lstrip())
            line_idx = len(script.lines)
            units = _make_units(piece, line_idx, stage, a, len(script.units))
            if not units:
                continue
            script.lines.append(DisplayLine(line_idx, piece, para,
                                            units[0].idx, units[-1].idx))
            script.line_first_unit.append(units[0].idx)
            script.line_last_unit.append(units[-1].idx)
            script.units.extend(units)

    multi: Counter = Counter()
    for u in script.units:
        if u.matchable:
            for tok in {f[0] for f in u.forms if len(f) == 1}:
                script.df[tok] += 1
            if len(u.forms[0]) > 1:
                multi[u.forms[0]] += 1
    for u in script.units:
        if not u.forms:
            u.weight = 0.0
            continue
        primary = u.forms[0]
        # a contraction of stopwords ("you're" = "you are", "that's") is a stopword too
        u.is_stop = (len(primary) == 1 and primary[0] in STOPWORDS) or (
            len(u.forms) > 1 and any(len(f) > 1 and all(t in STOPWORDS for t in f) for f in u.forms[1:])
            and len(primary) == 1)
        if u.number_derived:
            base = 1.0
        elif u.is_stop:
            base = 0.25
        elif len(primary) > 1:
            # "AI-native" -> (a, i, native): judge the whole spoken form, not its first letter
            base = 1.0 if len("".join(primary)) >= 4 else 0.5
        elif len(primary[0]) <= 3:
            base = 0.5
        else:
            base = 1.0
        df = max(1, multi[primary] if len(primary) > 1 else script.df.get(primary[0], 1))
        w = base * (0.35 + 0.65 / df)
        if u.matchable:
            u.weight = w
        else:
            u.weight = 0.0
            u.optional_weight = w if u.optional else 0.0
    return script


def load_file(path: str | Path, max_line_chars: int = 90) -> Script:
    p = Path(path)
    if p.suffix.lower() not in (".txt", ".md", ".markdown", ""):
        raise ValueError(f"unsupported script type: {p.suffix}")
    return load_text(p.read_text(encoding="utf-8"), max_line_chars)
