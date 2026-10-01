from promptbro.normalize import HARD_FILLERS, Tok, normalize_hyp, spoken_forms
from promptbro.script import load_text

SCRIPT = """Welcome back to the show.
In 2026 we grew revenue 40% this year.
The plan costs $79 a month.
Their team shipped it right on time.
The quick brown fox jumps over the lazy dog.
Thanks for watching, and thanks for listening.
See you next week.
"""


def test_money():
    assert spoken_forms("$79")[0] == ("seventy", "nine", "dollars")
    assert ("seventy", "nine", "bucks") in spoken_forms("$79")
    f = spoken_forms("$79.99")
    assert ("seventy", "nine", "ninety", "nine") in f
    assert ("seventy", "nine", "dollars", "and", "ninety", "nine", "cents") in f
    assert ("seventy", "nine", "dollars", "a", "month") in spoken_forms("$79/mo")
    assert ("two", "million", "dollars") in spoken_forms("$2M")


def test_years_and_numbers():
    f = spoken_forms("2026")
    assert f[0] == ("twenty", "twenty", "six")
    assert ("two", "thousand", "twenty", "six") in f
    assert ("twenty", "oh", "five") in spoken_forms("2005")
    assert ("fifteen", "hundred") in spoken_forms("1500")
    assert ("nineteen", "hundred") in spoken_forms("1900")
    for raw in ("79", "250", "1500", "100", "1,234"):
        assert all("and" not in form for form in spoken_forms(raw)), raw
    assert spoken_forms("3.14") == [("three", "point", "one", "four")]
    assert spoken_forms("3rd") == [("third",)]
    assert ("three", "thirty") in spoken_forms("3:30")
    assert ("ten", "a", "m") in spoken_forms("10am")


def test_percent():
    assert spoken_forms("40%") == [("forty", "percent")]
    assert spoken_forms("4.5%") == [("four", "point", "five", "percent")]


def test_words():
    f = spoken_forms("API")
    assert ("a", "p", "i") in f and ("api",) in f
    assert spoken_forms("NASA")[0] == ("nasa",)
    f = spoken_forms("full-stack")
    assert ("full", "stack") in f and ("fullstack",) in f
    f = spoken_forms("it's")
    assert ("its",) in f and ("it", "is") in f
    assert ("do", "not") in spoken_forms("don't")
    assert ("doctor",) in spoken_forms("Dr.")
    assert spoken_forms("Watching,") == [("watching",)]
    assert spoken_forms("“Hello”") == [("hello",)]
    assert spoken_forms("—") == []


def test_hyp_fillers_and_nonspeech():
    toks = normalize_hyp("um the plan uh costs", None, 0)
    assert [t.text for t in toks] == ["the", "plan", "costs"]
    assert "um" in HARD_FILLERS
    assert [t.text for t in normalize_hyp("[BLANK_AUDIO] (music) hello", None, 0)] == ["hello"]


def test_hyp_soft_fillers():
    toks = normalize_hyp("you know the plan", None, 0)
    assert toks[:2] == [Tok("you", True), Tok("know", True)]
    assert not toks[2].is_soft_filler
    assert normalize_hyp("like", None, 0)[0].is_soft_filler


def test_hyp_number_choice():
    script = load_text(SCRIPT)
    toks = normalize_hyp("in 2026 we", script, cursor=4)
    assert [t.text for t in toks] == ["in", "twenty", "twenty", "six", "we"]
    s2 = load_text("In two thousand twenty six we grew")
    toks = normalize_hyp("in 2026 we", s2, cursor=0)
    assert [t.text for t in toks] == ["in", "two", "thousand", "twenty", "six", "we"]


def test_forms_found_in_a_real_script():
    # tokens from a real video script that had no spoken form a recognizer gives
    assert ("q", "four") in spoken_forms("Q4") and ("q4",) in spoken_forms("Q4")
    assert ("v", "t", "o") in spoken_forms("V/TO")
    assert spoken_forms("example.ai")[0] == ("example", "dot", "ai")
    assert ("exampleai",) in spoken_forms("example.ai")
    assert ("wordpress", "dot", "org") in spoken_forms("WordPress.org.")
    assert ("twenty", "twenty", "sevens") in spoken_forms("(2027's")
    assert ("twenty", "twenty", "seven") in spoken_forms("2027's")
    assert spoken_forms("ALL")[0] == ("all",)      # emphasis capitals, not an acronym
    assert spoken_forms("API")[0] == ("a", "p", "i")
