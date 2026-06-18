"""providers.tvdb.language — let non-English users get localized TVDB search
results. `_tvdb_language_code` maps the UI label (or a bare ISO 639-2 code) to
TVDB's `language` search param; the provider defaults to "eng" so the behavior
is unchanged when the setting is absent.
"""

from __future__ import annotations

from kira.matcher.engine import _tvdb_language_code


def test_label_maps_to_iso_639_2():
    assert _tvdb_language_code("English") == "eng"
    assert _tvdb_language_code("Français") == "fra"
    assert _tvdb_language_code("français") == "fra"   # case-insensitive
    assert _tvdb_language_code("Deutsch") == "deu"
    assert _tvdb_language_code("Español") == "spa"
    assert _tvdb_language_code("Italiano") == "ita"
    assert _tvdb_language_code("日本語") == "jpn"


def test_bare_639_2_code_passes_through():
    assert _tvdb_language_code("eng") == "eng"
    assert _tvdb_language_code("FRA") == "fra"   # normalized to lowercase


def test_unknown_or_empty_returns_none():
    # None → factory leaves the provider's "eng" default in place.
    assert _tvdb_language_code("") is None
    assert _tvdb_language_code("   ") is None
    assert _tvdb_language_code("Klingon") is None
    assert _tvdb_language_code(None) is None
    assert _tvdb_language_code(123) is None
    # BCP-47 ("en-US") is TMDB's shape, not TVDB's 639-2 — not accepted.
    assert _tvdb_language_code("en-US") is None
