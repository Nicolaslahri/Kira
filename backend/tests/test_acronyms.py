"""M2 — shared acronym helpers."""

from __future__ import annotations

from kira.matcher.acronyms import (
    KNOWN_ACRONYMS,
    acronym_forms,
    expand_known,
    is_acronym_shaped,
)


def test_is_acronym_shaped() -> None:
    assert is_acronym_shaped("aot")
    assert is_acronym_shaped("lotr")
    assert is_acronym_shaped("dbz")
    assert not is_acronym_shaped("attack on titan")  # has a space
    assert not is_acronym_shaped("a")                # too short
    assert not is_acronym_shaped("toolong")          # > 6 chars
    assert not is_acronym_shaped("")


def test_acronym_forms_all_words_and_no_stop() -> None:
    assert "aot" in acronym_forms("attack on titan")
    assert "tlotr" in acronym_forms("the lord of the rings")  # all words
    assert "lr" in acronym_forms("the lord of the rings")     # without stopwords
    # 'lotr' isn't derivable — that's exactly why it's in the curated map.
    assert "lotr" not in acronym_forms("the lord of the rings")


def test_acronym_forms_single_word_is_empty() -> None:
    assert acronym_forms("naruto") == set()


def test_expand_known() -> None:
    assert expand_known("aot") == "attack on titan"
    assert expand_known("jjk") == "jujutsu kaisen"
    assert expand_known("not-an-acronym") is None


def test_known_map_keys_are_acronym_shaped() -> None:
    # Every curated key must itself look like an acronym, or the metric /
    # search / ladder gates will never consult it.
    for k in KNOWN_ACRONYMS:
        assert is_acronym_shaped(k), f"{k!r} is not acronym-shaped"
