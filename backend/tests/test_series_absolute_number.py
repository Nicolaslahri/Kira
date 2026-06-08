"""GET /series/{provider}/{id}/episodes must surface absolute_number.

The Attack on Titan Final Season display bug: the user's files are named by
absolute number ("[MiniMTBB] Shingeki no Kyojin - 60".."- 89") and ARE matched
correctly to tvdb:267440 S4 in the DB. But the CoverPopup pairs files↔episodes
using the episode list from THIS endpoint, and the endpoint used to drop
EpisodeResult.absolute_number from its response dict.

TVDB returns S4E1..S4E30 carrying absolute 60..89 (verified live). Without the
absolute field the popup pairs local-1..30 episodes against absolute-60..89
files → every file shows "File is orphaned · no matching episode" even though
the match is correct and would rename fine. Same root class as the scan-path
_to_dicts absolute_number fix; this is the popup's /series path.
"""

from __future__ import annotations

import pytest

from kira.api import series as series_mod
from kira.api.series import list_series_episodes
from kira.providers.base import EpisodeResult


class _FakeTVDB:
    """AoT S4 shape: local E1..E3 carrying series-wide absolute 60..62."""

    async def get_episodes(self, provider_id, season, **kw):
        return [
            EpisodeResult(provider="tvdb", series_id=provider_id, season=4,
                          episode=1, title="The Other Side of the Sea",
                          absolute_number=60),
            EpisodeResult(provider="tvdb", series_id=provider_id, season=4,
                          episode=2, title="Midnight Train", absolute_number=61),
            EpisodeResult(provider="tvdb", series_id=provider_id, season=4,
                          episode=3, title="The Door of Hope", absolute_number=62),
        ]


class _FakeRegistry:
    def has(self, provider):
        return provider == "tvdb"

    def build(self, provider):
        return _FakeTVDB()


@pytest.mark.asyncio
async def test_series_episodes_expose_absolute_number(monkeypatch):
    async def _fake_reg(client):
        return _FakeRegistry()

    monkeypatch.setattr(series_mod, "registry_from_settings", _fake_reg)

    # force_refresh bypasses the process cache so the test is order-independent.
    out = await list_series_episodes("tvdb", "267440", season=4, force_refresh=True)
    eps = out["episodes"]

    assert len(eps) == 3
    # Local numbering preserved (Plex/Jellyfin season ordering) ...
    assert [e["episode"] for e in eps] == [1, 2, 3]
    # ... AND the absolute numbers the popup needs to pair "- 60".."- 62".
    assert [e["absolute_number"] for e in eps] == [60, 61, 62]
    assert all("absolute_number" in e for e in eps)


@pytest.mark.asyncio
async def test_series_episodes_absolute_none_when_provider_omits(monkeypatch):
    """A provider/episode without an absolute number yields null, not a crash
    (AniDB-native: episode already IS the absolute, so the popup falls back to
    .episode — the endpoint just passes None through)."""

    class _NoAbs:
        async def get_episodes(self, provider_id, season, **kw):
            return [EpisodeResult(provider="tvdb", series_id=provider_id,
                                  season=1, episode=5, title="Ep 5")]

    class _Reg:
        def has(self, provider):
            return True

        def build(self, provider):
            return _NoAbs()

    async def _fake_reg(client):
        return _Reg()

    monkeypatch.setattr(series_mod, "registry_from_settings", _fake_reg)

    out = await list_series_episodes("tvdb", "123", season=1, force_refresh=True)
    assert out["episodes"][0]["absolute_number"] is None
