"""Token similarity (DESIGN §6.3)."""

from promptbro.aligner import double_metaphone_match, token_sim


def test_homophone():
    assert token_sim("write", "right") >= 0.82
    assert token_sim("there", "their") == 0.9


def test_rhyme_guard():
    assert token_sim("light", "night") == 0.0


def test_short_words_exact_only():
    assert token_sim("the", "then") == 0.0
    assert token_sim("the", "the") == 1.0


def test_inflection():
    assert token_sim("balancing", "balance") >= 0.8


def test_open_last_prefix():
    assert token_sim("bro", "brown", h_is_open_last=True) == 0.8
    assert token_sim("bro", "brown") == 0.0


def test_phonetic_pairs():
    for a, b in [("write", "right"), ("four", "for"), ("cite", "site"),
                 ("principal", "principle")]:
        assert double_metaphone_match(a, b), (a, b)
    assert token_sim("cite", "site") >= 0.82


def test_unrelated_words():
    assert token_sim("honestly", "thanks") == 0.0
    assert token_sim("quick", "watching") == 0.0
