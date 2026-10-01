from promptbro.script import load_file, load_text

SCRIPT = """Welcome back to the show.
In 2026 we grew revenue 40% this year.
The plan costs $79 a month.
Their team shipped it right on time.
The quick brown fox jumps over the lazy dog.
Thanks for watching, and thanks for listening.
See you next week.
"""


def test_corpus_script():
    s = load_text(SCRIPT)
    assert len(s.lines) == 7
    assert len(s.units) == 46
    assert s.line_first_unit == [0, 5, 13, 19, 26, 35, 42]
    assert s.line_last_unit == [4, 12, 18, 25, 34, 41, 45]
    assert s.units[6].text == "2026" and s.units[10].text == "40%"
    assert s.units[29].text == "fox" and s.units[43].text == "you"
    assert s.df["thanks"] == 2
    assert s.units[6].number_derived
    assert 0.6 <= s.units[6].weight <= 1.0
    assert s.units[3].is_stop and s.units[3].weight < 0.3
    assert s.units[27].weight >= 0.6  # quick
    assert s.units[29].weight < 0.6  # fox: short token
    assert s.lines[1].text[s.units[6].char_start:s.units[6].char_end] == "2026"


def test_long_paragraph_splits_at_sentences():
    sentences = ["This is sentence number %d and it runs on for a good while." % i
                 for i in range(1, 4)]
    text = " ".join(sentences)
    assert len(text) > 150
    s = load_text(text)
    assert len(s.lines) >= 2
    assert all(len(line.text) <= 90 for line in s.lines)
    assert all(line.text.endswith(".") for line in s.lines)


def test_never_split_inside_number_or_hyphen():
    s = load_text("Revenue reached 1,500,000 dollars, a full-stack record, and more so we keep "
                  "going through 2026 with a very long clause that has no end in sight at all.",
                  max_line_chars=40)
    assert any("1,500,000" in line.text for line in s.lines)
    assert any("full-stack" in line.text for line in s.lines)


def test_markdown_stripping():
    s = load_text("# Heading\n\n**Bold** and _em_ text\n> quoted\n- bullet one\n1. numbered\n"
                  "[link text](http://x.y) <b>tag</b>\n---\n```\ncode here\n```\nlast")
    texts = [l.text for l in s.lines]
    assert texts == ["Heading", "Bold and em text", "quoted", "bullet one", "numbered",
                     "link text tag", "last"]


def test_stage_directions():
    s = load_text("(pause) Thanks for coming.")
    assert not s.units[0].matchable and s.units[0].weight == 0
    assert s.units[1].matchable
    assert s.lines[0].text.startswith("(pause)")
    long_paren = "(" + " ".join(f"w{i}" for i in range(12)) + ")"
    s = load_text(f"Start {long_paren} end")
    assert all(u.matchable for u in s.units)
    s = load_text("[beat]")
    assert not s.units[0].matchable


def test_paragraphs_and_load_file(tmp_path):
    s = load_text("one two\nthree\n\nfour")
    assert [l.para for l in s.lines] == [0, 0, 1]
    p = tmp_path / "a.md"
    p.write_text("# Hi there\n", encoding="utf-8")
    assert load_file(p).lines[0].text == "Hi there"


def test_weights_of_contractions_and_compound_forms():
    s = load_text("You're here. AI-native teams ship. That's it.")
    by = {u.text.strip(".,"): u for u in s.units}
    assert by["You're"].is_stop and by["You're"].weight < 0.3     # = "you are"
    assert by["That's"].is_stop
    assert by["AI-native"].weight >= 0.6                          # not judged by its first letter "a"


def test_asides_are_optional():
    s = load_text("We just built it (the plan is on the way) and shipped.")
    aside = [u for u in s.units if u.optional]
    assert [u.text for u in aside] == ["(the", "plan", "is", "on", "the", "way)"]
    assert all(not u.matchable and u.weight == 0 for u in aside)
    assert s.units[4].optional_weight > 0                          # "plan" can still match
