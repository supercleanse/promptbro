"""Text normalization shared by the script side and the ASR-hypothesis side (DESIGN §6.2).

``spoken_forms`` maps one written token to the ways it may be spoken (primary first).
``normalize_hyp`` turns a recognizer string into a token stream aligned to the script.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

from num2words import num2words

if TYPE_CHECKING:  # pragma: no cover
    from .script import Script

Form = tuple[str, ...]

HARD_FILLERS = frozenset({"um", "uh", "er", "erm", "ah", "uhm", "hmm", "mm", "mhm"})
SOFT_FILLERS = frozenset(
    {"like", "so", "well", "okay", "ok", "right", "actually", "basically",
     "literally", "yeah", "you know", "i mean"}
)

STOPWORDS = frozenset(
    """a an the and or but of to in on at by for with from as is are was were be been am
    it its he she they we you i me my your his her their our this that these those not do
    does did have has had will would can could should there here than then so if us him
    them""".split()
)

_HOMOPHONE_GROUPS = [
    ("there", "their", "theyre"), ("write", "right", "rite"), ("to", "too", "two"),
    ("for", "four", "fore"), ("one", "won"), ("no", "know"), ("new", "knew"),
    ("by", "buy", "bye"), ("hear", "here"), ("see", "sea"), ("be", "bee"),
    ("its", "its"), ("your", "youre"), ("whose", "whos"), ("weather", "whether"),
    ("would", "wood"), ("which", "witch"), ("whole", "hole"), ("sale", "sail"),
    ("sun", "son"), ("week", "weak"), ("wait", "weight"), ("way", "weigh"),
    ("our", "hour"), ("in", "inn"), ("eight", "ate"), ("made", "maid"),
    ("meet", "meat"), ("peace", "piece"), ("plain", "plane"), ("principal", "principle"),
    ("read", "red"), ("role", "roll"), ("steal", "steel"), ("tale", "tail"),
    ("than", "then"), ("threw", "through"), ("wear", "where"), ("affect", "effect"),
    ("accept", "except"), ("brake", "break"), ("cell", "sell"), ("desert", "dessert"),
    ("flour", "flower"), ("grate", "great"), ("hi", "high"), ("mail", "male"),
    ("morning", "mourning"), ("pair", "pear"), ("rain", "reign"), ("seam", "seem"),
    ("some", "sum"), ("toe", "tow"), ("wait", "weight"), ("a", "uh"),
]
HOMOPHONES: dict[str, frozenset[str]] = {}
for _g in _HOMOPHONE_GROUPS:
    for _w in _g:
        HOMOPHONES[_w] = frozenset(HOMOPHONES.get(_w, frozenset()) | set(_g))

_ABBREV: dict[str, list[Form]] = {
    "dr": [("doctor",)], "mr": [("mister",)], "mrs": [("missus",)],
    "ms": [("miz",), ("ms",)], "st": [("saint",), ("street",)], "vs": [("versus",)],
    "etc": [("et", "cetera")], "e.g": [("for", "example"), ("e", "g")],
    "i.e": [("that", "is"), ("i", "e")], "&": [("and",)], "+": [("plus",)],
    "w/": [("with",)], "approx": [("approximately",)],
}

_PUNCT_EDGE = " \t\r\n\"'()[]{}<>,.;:!?…*_`~‐‑‒–—-"
_CURLY = {"‘": "'", "’": "'", "‛": "'", "“": '"', "”": '"'}


def _straighten(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return "".join(_CURLY.get(c, c) for c in s)


def _cardinal(n: int) -> Form:
    words = num2words(n).replace(",", " ").replace("-", " ").split()
    return tuple(w for w in words if w != "and")


def _digits(s: str) -> Form:
    return tuple(num2words(int(c)) for c in s)


def _decimal(int_part: str, frac: str) -> Form:
    return _cardinal(int(int_part.replace(",", ""))) + ("point",) + _digits(frac)


def _ordinal(n: int) -> Form:
    words = num2words(n, to="ordinal").replace(",", " ").replace("-", " ").split()
    return tuple(w for w in words if w != "and")


def _dedupe(forms: list[Form]) -> list[Form]:
    seen: set[Form] = set()
    out: list[Form] = []
    for f in forms:
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _year_forms(n: int) -> list[Form]:
    forms: list[Form] = []
    hi, lo = divmod(n, 100)
    if 2000 <= n <= 2009:
        forms.append(_cardinal(n))
        if lo:
            forms.append(("twenty", "oh") + _cardinal(lo))
            forms.append(("twenty", "o") + _cardinal(lo))
            forms.append(("two", "thousand", "and") + _cardinal(lo))
    elif lo == 0 and n % 1000 != 0:
        forms.append(_cardinal(hi) + ("hundred",))
        forms.append(_cardinal(n))
    else:
        if lo == 0:
            forms.append(_cardinal(n))
        elif lo < 10:
            forms.append(_cardinal(hi) + ("oh",) + _cardinal(lo))
            forms.append(_cardinal(hi) + ("o",) + _cardinal(lo))
        else:
            forms.append(_cardinal(hi) + _cardinal(lo))
        forms.append(_cardinal(n))
        if 2000 <= n <= 2099:
            forms.append(("two", "thousand", "and") + _cardinal(lo))
    return forms


def _number_forms(digits: str) -> list[Form]:
    """Forms for a plain integer string (commas already removed)."""
    if len(digits) > 1 and digits.startswith("0"):
        return [_digits(digits)]
    n = int(digits)
    if len(digits) == 4 and 1000 <= n <= 2099:
        return _year_forms(n)
    forms = [_cardinal(n)]
    if 1100 <= n <= 9900 and n % 100 == 0 and n % 1000 != 0:
        forms.insert(0, _cardinal(n // 100) + ("hundred",))
    return forms


_MONEY = re.compile(
    r"^\$(\d[\d,]*)(?:\.(\d+))?([kmbKMB])?(?:/(mo|mth|month|yr|year))?$")
_MAGNITUDE = {"k": "thousand", "m": "million", "b": "billion"}


def _money_forms(m: re.Match[str]) -> list[Form]:
    whole, frac, mag, per = m.group(1), m.group(2), m.group(3), m.group(4)
    n = int(whole.replace(",", ""))
    forms: list[Form] = []
    if mag:
        base = _decimal(whole, frac) if frac else _cardinal(n)
        forms.append(base + (_MAGNITUDE[mag.lower()], "dollars"))
    elif frac:
        cents = int(frac[:2].ljust(2, "0"))
        cw = _cardinal(cents) if cents >= 10 or cents == 0 else ("oh",) + _cardinal(cents)
        d = "dollar" if n == 1 else "dollars"
        forms.append(_cardinal(n) + cw)
        forms.append(_cardinal(n) + (d, "and") + _cardinal(cents) + ("cents",))
        forms.append(_cardinal(n) + (d,) + cw)
    else:
        forms.append(_cardinal(n) + ("dollar" if n == 1 else "dollars",))
        forms.append(_cardinal(n) + ("bucks",))
    if per:
        unit = "month" if per in ("mo", "mth", "month") else "year"
        forms = [f + s for f in forms for s in (("a", unit), ("per", unit))]
    return forms


_TIME = re.compile(r"^(\d{1,2}):(\d{2})\s*([ap]\.?m\.?)?$", re.I)
_TIME_AMPM = re.compile(r"^(\d{1,2})\s*([ap])\.?m\.?$", re.I)


def _time_forms(h: int, mnt: int | None, ap: str | None) -> list[Form]:
    hw = _cardinal(h)
    if mnt is None or mnt == 0:
        base = [hw] if mnt is None else [hw, hw + ("o", "clock")]
    elif mnt < 10:
        base = [hw + ("oh",) + _cardinal(mnt)]
    else:
        base = [hw + _cardinal(mnt)]
    if not ap:
        return base
    a = ap[0].lower()
    out: list[Form] = []
    for b in base:
        out.append(b + (a, "m"))
        out.append(b + (a + "m",))
    return out


_CONTR_IRREG = {"won't": [("will", "not")], "can't": [("can", "not"), ("cannot",)],
                "shan't": [("shall", "not")], "ain't": [("is", "not"), ("aint",)],
                "let's": [("let", "us")]}
_IS_STEMS = {"it", "he", "she", "that", "what", "there", "here", "who", "where",
             "how", "when", "why"}
_CONTR_SUFFIX = {"'ll": "will", "'re": "are", "'ve": "have", "'d": "would", "'m": "am"}


def _contraction_forms(word: str) -> list[Form]:
    """word is lowercase, contains an apostrophe."""
    stripped = "".join(c for c in word if c.isalnum())
    forms: list[Form] = [(stripped,)]
    if word in _CONTR_IRREG:
        forms += _CONTR_IRREG[word]
    elif word.endswith("n't"):
        forms.append((word[:-3], "not"))
    else:
        for suf, exp in _CONTR_SUFFIX.items():
            if word.endswith(suf):
                forms.append((word[: -len(suf)], exp))
                if suf == "'d":
                    forms.append((word[: -len(suf)], "had"))
                break
        else:
            if word.endswith("'s") and word[:-2] in _IS_STEMS:
                forms.append((word[:-2], "is"))
    return forms


_VOWELS = set("aeiou")


def _caps_forms(core: str) -> list[Form]:
    low = core.lower()
    spelled: Form = tuple(low)
    word: Form = (low,)
    if len(low) <= 3 or not (_VOWELS & set(low)):
        return [spelled, word]
    return [word, spelled]


def _plain_forms(base: str) -> list[Form]:
    s = "".join(c for c in base if c.isalnum())
    if not s:
        return []
    forms: list[Form] = [(s,)]
    folded = "".join(c for c in unicodedata.normalize("NFKD", s)
                     if not unicodedata.combining(c))
    if folded != s:
        forms.append((folded,))
    return forms


_SPLIT = re.compile(r"[-‐-—/]")
_NUM_RE = re.compile(r"^\d[\d,]*$")


def spoken_forms(raw_token: str) -> list[Form]:
    """Accepted spoken token tuples for one written token, primary first."""
    s = _straighten(raw_token).strip()
    if not s:
        return []
    if s.lower() in _ABBREV:  # "&", "+", "w/"
        return _dedupe(list(_ABBREV[s.lower()]))
    core = s.strip(_PUNCT_EDGE)
    if not core:
        return []
    low = core.lower()

    if low in _ABBREV:
        return _dedupe(list(_ABBREV[low]))
    m = _MONEY.match(core)
    if m:
        return _dedupe(_money_forms(m))
    m = re.match(r"^(\d[\d,]*)(?:\.(\d+))?%$", core)
    if m:
        w = _decimal(m.group(1), m.group(2)) if m.group(2) else _cardinal(int(m.group(1).replace(",", "")))
        return [w + ("percent",)]
    m = re.match(r"^#(\d+)$", core)
    if m:
        return [("number",) + f for f in _number_forms(m.group(1))]
    m = re.match(r"^(\d+)(st|nd|rd|th)$", low)
    if m:
        return [_ordinal(int(m.group(1)))]
    m = _TIME.match(core)
    if m:
        return _dedupe(_time_forms(int(m.group(1)), int(m.group(2)), m.group(3)))
    m = _TIME_AMPM.match(core)
    if m:
        return _dedupe(_time_forms(int(m.group(1)), None, m.group(2) + "m"))
    if _NUM_RE.match(core):
        if "," in core and not re.match(r"^\d{1,3}(,\d{3})+$", core):
            return _dedupe([f for part in core.split(",") for f in _number_forms(part)][:1])
        return _dedupe(_number_forms(core.replace(",", "")))
    m = re.match(r"^(\d[\d,]*)\.(\d+)$", core)
    if m:
        return [_decimal(m.group(1), m.group(2))]
    m = re.match(r"^(\d+)'s$", low)
    if m:  # "2027's" -> "twenty twenty sevens", or the number alone
        nf = _number_forms(m.group(1))
        return _dedupe([f[:-1] + (f[-1] + "s",) for f in nf] + nf)
    m = re.match(r"^([a-z]{1,3})(\d{1,4})$", low)
    if m and core[:len(m.group(1))].isupper():  # "Q4" -> "q four", "MP3" -> "m p three"
        nf = _number_forms(m.group(2))
        return _dedupe([tuple(m.group(1)) + nf[0], (low,)])
    if re.match(r"^[a-z0-9]+(\.[a-z0-9]+)+$", low) and re.search(r"[a-z]", low.split(".")[-1]):
        # a domain: "example.ai" -> "example dot ai"
        parts = low.split(".")
        dotted: Form = ()
        for i, part in enumerate(parts):
            dotted += (("dot",) if i else ()) + (spoken_forms(part)[:1] or [(part,)])[0]
        return _dedupe([dotted, ("".join(parts),)])

    if _SPLIT.search(core):
        parts = [p for p in _SPLIT.split(core) if p.strip(_PUNCT_EDGE)]
        if len(parts) >= 2:
            per_part = [spoken_forms(p)[:2] or [()] for p in parts]
            combos: list[Form] = [()]
            for opts in per_part:
                combos = [c + o for c in combos for o in opts][:8]
            if all(p.isalpha() for p in parts):
                combos.append(("".join(p.lower() for p in parts),))
                if all(p.isupper() for p in parts) and len("".join(parts)) <= 5:
                    combos.append(tuple("".join(parts).lower()))  # "V/TO" -> "v t o"
            return _dedupe(combos)

    if "'" in low:
        return _dedupe(_contraction_forms(low))
    if re.match(r"^[A-Z]{2,5}$", core) and low not in STOPWORDS and low not in _CAPS_WORDS:
        return _dedupe(_caps_forms(core))
    return _dedupe(_plain_forms(low))


_CAPS_WORDS = frozenset({"ok", "am", "pm", "tv", "no", "yes", "wow", "hey", "hi", "oh",
                         # short words written in capitals for emphasis ("ALL OF YOU")
                         "all", "now", "new", "big", "one", "two", "six", "ten", "off", "out",
                         "top", "any", "get", "how", "why", "who", "way", "day", "bad", "own",
                         "yet", "too", "see", "act", "go", "up", "so"})


@dataclass(frozen=True)
class Tok:
    text: str
    is_soft_filler: bool = False


_NONSPEECH = re.compile(r"\[[^\]]*\]|\([^)]*\)|<[^>]*>")


def _matches_window(form: Form, window: list) -> bool:
    if any(form in u.forms for u in window):
        return True
    flat = [t for u in window if u.matchable and u.forms for t in u.forms[0]]
    n = len(form)
    return any(tuple(flat[i:i + n]) == form for i in range(len(flat) - n + 1))


def normalize_hyp(text: str, script: "Script | None", cursor: int) -> list[Tok]:
    """Token stream for a recognizer string, filler-aware and script-aware."""
    text = _NONSPEECH.sub(" ", _straighten(text))
    words = text.split()
    window: list = []
    if script is not None:
        lo = max(0, cursor - 3)
        window = script.units[lo:cursor + 41]
    out: list[Tok] = []
    i = 0
    while i < len(words):
        w = words[i].strip(_PUNCT_EDGE).lower()
        nxt = words[i + 1].strip(_PUNCT_EDGE).lower() if i + 1 < len(words) else ""
        if f"{w} {nxt}" in SOFT_FILLERS:
            out += [Tok(w, True), Tok(nxt, True)]
            i += 2
            continue
        i += 1
        if not w or w in HARD_FILLERS:
            continue
        forms = spoken_forms(words[i - 1])
        if not forms:
            continue
        chosen = forms[0]
        if len(forms) > 1 and window:
            for f in forms:
                if _matches_window(f, window):
                    chosen = f
                    break
        soft = len(chosen) == 1 and chosen[0] in SOFT_FILLERS
        out += [Tok(t, soft) for t in chosen]
    return out
