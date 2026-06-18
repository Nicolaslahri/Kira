"""Episode/tvshow NFO must name the UNIFIED show (the folder), not the per-cour
AniDB title.

AniDB gives every cour its own title ("Attack on Titan Season 2"), so the rename
unifies the FOLDER to the franchise ("Attack on Titan"). The NFO used to take
`Match.series_name` (the cour title) for <showtitle>, stamping "Attack on Titan
Season 2" into the file and splitting the show in Plex/Jellyfin away from its own
folder. `_write_nfo_files` now honors the unified `library_title`, passed as
`series_name_override`, for BOTH <showtitle> and the tvshow <title>.
"""
from __future__ import annotations

import pytest

from kira.api.rename import _write_nfo_files
from kira.models import Match
from kira.parser.parser import ParsedFile


def _episode_target(tmp_path):
    d = tmp_path / "Attack on Titan" / "Season 02"
    d.mkdir(parents=True)
    return d / "Attack on Titan - S02E06 - Warrior.mkv"


def _parsed(name):
    return ParsedFile(original_filename=name, media_type="anime",
                      title="Attack on Titan Season 2", season=2, episode=6)


def _selected():
    # An AniDB cour match: title + series_name carry the "Season 2" qualifier.
    return Match(
        provider="anidb", provider_id="1", is_selected=True, is_manual=False,
        match_type="tv_episode", episode_number=6, season_number=2,
        episode_title="Warrior", metadata_blob="{}", confidence=1.0, year=2017,
        title="Attack on Titan Season 2", series_name="Attack on Titan Season 2",
    )


@pytest.fixture(autouse=True)
def _offline_episode_meta(monkeypatch):
    # Keep _write_nfo_files fully offline — no provider round-trip for per-episode
    # title resolution (we assert on the SHOW title, not the episode title).
    import kira.matcher.engine as engine
    import kira.api.series as series

    async def _reg(*a, **k):
        return {}

    async def _meta(*a, **k):
        return None

    monkeypatch.setattr(engine, "registry_from_settings", _reg)
    monkeypatch.setattr(series, "resolve_episode_meta", _meta)


async def test_showtitle_uses_unified_override(tmp_path):
    target = _episode_target(tmp_path)
    await _write_nfo_files(
        target, _parsed(target.name), _selected(), {},
        series_name_override="Attack on Titan",
    )
    ep = target.with_suffix(".nfo").read_text(encoding="utf-8")
    assert "<showtitle>Attack on Titan</showtitle>" in ep
    assert "Season 2" not in ep, "the cour qualifier must not leak into the NFO"
    # The shared tvshow.nfo names the same unified show.
    tv = (tmp_path / "Attack on Titan" / "tvshow.nfo").read_text(encoding="utf-8")
    assert "<title>Attack on Titan</title>" in tv


async def test_without_override_falls_back_to_series_name(tmp_path):
    # Documents that the override IS the fix: with none, the old source
    # (Match.series_name) is used verbatim.
    target = _episode_target(tmp_path)
    await _write_nfo_files(target, _parsed(target.name), _selected(), {})
    ep = target.with_suffix(".nfo").read_text(encoding="utf-8")
    assert "<showtitle>Attack on Titan Season 2</showtitle>" in ep
