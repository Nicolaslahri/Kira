"""TVDB anime folds into its AniDB franchise card (Attack on Titan Final Season).

A long-runner whose files are pure-absolute-numbered ("Shingeki no Kyojin - 60")
can't be placed in any single AniDB cour, so the matcher routes the Final Season
to TVDB → it landed on its OWN card (tvdb:267440) while the AniDB-cour siblings
(S1-S3) grouped under anidb:9541. compute_series_group_id now reverse-maps a
known-anime TVDB id through Fribb and resolves it THROUGH the AniDB franchise so
they share one card.

Guard rails pinned here:
  - known-anime TVDB id  → anidb:<franchise root>  (folds in)
  - live-action TVDB id  → tvdb:<id>               (no Fribb AID → unchanged)
  - plain AniDB id       → anidb:<root>             (existing behavior intact)
  - TMDB id              → tmdb:<id>                (NOT folded — anime movies
                                                     carry tmdb ids; must not
                                                     collapse into a TV group)
"""

from __future__ import annotations

import pytest

from kira.matcher.engine import compute_series_group_id
from kira.providers.anime_mappings import AnimeMappings


class _FakeAniDB:
    async def get_related_aids(self, aid):
        # Whole AoT franchise chain; canonical root = min = 9541.
        return {9541, 10944, 13241, 14444}


class _FakeRegistry:
    def has(self, p):
        return p == "anidb"

    def build(self, p):
        return _FakeAniDB()


def _patch_aid_by_tvdb(monkeypatch, mapping: dict[int, int | None]):
    async def _fake(cls, tvdb_id):
        return mapping.get(tvdb_id)
    monkeypatch.setattr(AnimeMappings, "aid_by_tvdb", classmethod(_fake))


@pytest.mark.asyncio
async def test_tvdb_anime_folds_to_anidb_franchise(monkeypatch):
    _patch_aid_by_tvdb(monkeypatch, {267440: 9541})
    gid = await compute_series_group_id("tvdb", "267440", _FakeRegistry())
    assert gid == "anidb:9541"  # folded into the franchise with S1-S3


@pytest.mark.asyncio
async def test_tvdb_anime_folds_via_non_root_aid(monkeypatch):
    """aid_by_tvdb may return ANY franchise member (dict order). The result
    must still resolve to the franchise ROOT via the sequel walk, so the
    Final Season lands in the same card regardless of which AID was seeded."""
    _patch_aid_by_tvdb(monkeypatch, {267440: 14444})  # a later cour, not S1
    gid = await compute_series_group_id("tvdb", "267440", _FakeRegistry())
    assert gid == "anidb:9541"


@pytest.mark.asyncio
async def test_live_action_tvdb_unchanged(monkeypatch):
    _patch_aid_by_tvdb(monkeypatch, {})  # no Fribb AID for this id
    gid = await compute_series_group_id("tvdb", "362472", _FakeRegistry())
    assert gid == "tvdb:362472"  # Loki stays put


@pytest.mark.asyncio
async def test_tmdb_never_folded(monkeypatch):
    """TMDB is deliberately excluded — anime movies carry tmdb ids and must
    not collapse into a TV franchise group. Even if aid_by_tvdb were somehow
    consulted, the tvdb-only branch never runs for tmdb."""
    _patch_aid_by_tvdb(monkeypatch, {500: 9541})
    gid = await compute_series_group_id("tmdb", "500", _FakeRegistry())
    assert gid == "tmdb:500"


@pytest.mark.asyncio
async def test_plain_anidb_unchanged(monkeypatch):
    _patch_aid_by_tvdb(monkeypatch, {})
    gid = await compute_series_group_id("anidb", "10944", _FakeRegistry())
    assert gid == "anidb:9541"  # sequel walk → root, unchanged behavior
