"""Bundled demo data. Only imported when --demo is passed."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SAMPLE = Path(__file__).parent / "data" / "sample-script.md"


@dataclass
class DemoLine:
    text: str


@dataclass
class DemoUnit:
    idx: int
    line: int
    char_start: int
    char_end: int


@dataclass
class DemoPosition:
    line: int
    unit: int
    state: str = "tracking"


class DemoScript:
    def __init__(self, lines: list[str]):
        self.lines = [DemoLine(t) for t in lines]
        self.units: list[DemoUnit] = []
        for li, t in enumerate(lines):
            for m in re.finditer(r"\S+", t):
                self.units.append(DemoUnit(len(self.units), li, m.start(), m.end()))

    def unit_at(self, line: int, frac: float) -> int:
        """Global index of the word about `frac` of the way through `line`."""
        idx = [u.idx for u in self.units if u.line == line]
        return idx[min(len(idx) - 1, int(frac * len(idx)))]


def load_demo_script() -> DemoScript:
    text = SAMPLE.read_text(encoding="utf-8")
    lines: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = re.sub(r"^#+\s*", "", para.strip())
        for piece in re.split(r"(?<=[.?!])\s+", para):
            piece = piece.strip()
            if piece:
                lines.append(piece)
    return DemoScript(lines)


def scripted_events(script: DemoScript) -> list[DemoPosition]:
    """Deterministic walk: half of line 0, then halfway through lines 1..4."""
    ev = [DemoPosition(0, script.unit_at(0, 0.5))]
    ev += [DemoPosition(i, script.unit_at(i, 0.5)) for i in range(1, 5)]
    return ev
