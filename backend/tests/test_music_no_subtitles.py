"""Music has no subtitle concept — the coverage gate (the CC badge / coverage
tile) and the backfill both skip it, while non-music files are unaffected."""
from __future__ import annotations

from kira.subtitles.backfill import needed_languages
from kira.subtitles.coverage import missing_languages


def test_music_never_reports_missing_subs():
    music = {"media_type": "music"}
    assert missing_languages(music, ["en", "es"]) is None   # → no CC badge, not in tile
    assert needed_languages(music, ["en", "es"]) == []       # → never targeted by backfill


def test_gate_is_music_specific_not_blanket():
    # A non-music file with no subtitles present still reports them missing — the
    # gate keys on media_type, it doesn't disable coverage wholesale.
    tv = {"media_type": "tv", "sub_langs": []}
    assert needed_languages(tv, ["en"]) == ["en"]
