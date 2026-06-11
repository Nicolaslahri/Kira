"""Unmapped anime cours inherit their prequel's TVDB season instead of Season 1.

Bleach: TYBW "The Calamity" (AID 19079) is so new that the Fribb mapping hasn't
linked it to TVDB Season 17 yet, so `tvdb_season(19079)` is None. The old fallback
(parsed_season → base franchise → Season 1) fractured the show. Now an unmapped AID
inherits the season of the nearest PREQUEL cour of the same TVDB series.
"""
from __future__ import annotations

import pytest

from kira.matcher.engine import resolve_canonical_season
from kira.providers.anime_mappings import AnimeMappings


@pytest.mark.asyncio
async def test_unmapped_cour_inherits_nearest_prequel_season(monkeypatch):
    # Fribb knows the TYBW cours (S17) + original Bleach (S1) but NOT the Calamity.
    seasons = {269: 1, 15449: 17, 17765: 17, 18220: 17, 19079: None}

    async def _season(aid):
        return seasons.get(int(aid))

    async def _tvdb(aid):
        return 74796  # Bleach

    async def _aids(tvdb_id):
        return [269, 15449, 17765, 18220, 19079]

    monkeypatch.setattr(AnimeMappings, "tvdb_season", _season)
    monkeypatch.setattr(AnimeMappings, "tvdb_id", _tvdb)
    monkeypatch.setattr(AnimeMappings, "aids_by_tvdb", _aids)

    # The unmapped Calamity inherits the nearest prequel's season (The Conflict=17),
    # NOT the parsed/base default of 1.
    assert await resolve_canonical_season("anidb", "19079", 1) == 17
    # A normally-mapped cour still uses its own mapped season (parsed hint ignored).
    assert await resolve_canonical_season("anidb", "18220", 5) == 17


@pytest.mark.asyncio
async def test_unknown_series_falls_back_to_parsed(monkeypatch):
    async def _none(_):
        return None

    monkeypatch.setattr(AnimeMappings, "tvdb_season", _none)
    monkeypatch.setattr(AnimeMappings, "tvdb_id", _none)

    # No mapping at all → keep whatever the filename parsed.
    assert await resolve_canonical_season("anidb", "99999", 3) == 3


@pytest.mark.asyncio
async def test_non_anidb_keeps_parsed_season():
    assert await resolve_canonical_season("tvdb", "12345", 4) == 4
