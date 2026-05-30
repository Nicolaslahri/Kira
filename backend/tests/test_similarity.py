"""Phase 15 — normalization + similarity tests."""

from __future__ import annotations

from kira.matcher.similarity import normalize, trigram_similarity


def test_ampersand_folds_to_and() -> None:
    assert normalize("Rick & Morty") == normalize("Rick and Morty")


def test_roman_numerals_fold() -> None:
    assert normalize("Hellsing II") == normalize("Hellsing 2")
    assert normalize("Final Fantasy VII") == normalize("Final Fantasy 7")
    assert normalize("Rocky IV") == normalize("Rocky 4")


def test_single_letter_romans_NOT_folded() -> None:
    """i/v/x/l/c/d/m collide with real words — must stay literal."""
    assert "1" not in normalize("I Robot").split()       # 'I' not → 1
    assert "10" not in normalize("X Men").split()         # 'X' not → 10
    assert "5" not in normalize("V for Vendetta").split() # 'V' not → 5
    assert "1000" not in normalize("Malcolm M").split()


def test_ordinal_words_fold() -> None:
    assert normalize("Konosuba Second Season") == normalize("Konosuba 2 Season")
    assert normalize("The Third Man") == normalize("3 Man")  # article 'the' stripped


def test_numeric_ordinals_fold() -> None:
    assert normalize("2nd Season") == normalize("2 Season")
    assert normalize("Attack on Titan 3rd Season") == normalize("Attack on Titan 3 Season")


def test_season_ordinal_equivalence_via_normalize() -> None:
    """The whole point: II / 2nd / Second all reduce to the same string."""
    a = normalize("Spice and Wolf II")
    b = normalize("Spice and Wolf 2nd")
    c = normalize("Spice and Wolf Second")
    d = normalize("Spice & Wolf 2")
    assert a == b == c == d


def test_normalize_still_handles_existing_cases() -> None:
    # Regressions: diacritics, articles, acronym punctuation.
    assert normalize("Zürich") == "zurich"
    assert normalize("S.W.A.T.") == "swat"
    assert normalize("The Matrix") == "matrix"


def test_trigram_identity_unaffected() -> None:
    assert trigram_similarity("Attack on Titan", "Attack on Titan") == 1.0
    # II↔2 equivalence now scores a perfect match where it wouldn't before.
    assert trigram_similarity("Hellsing II", "Hellsing 2") == 1.0
