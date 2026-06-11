"""Unified-rename round-trip: synthetic season folders must not kill matching.

After renaming a franchise to one show folder ("Bleach - Thousand-Year Blood War"
with Season 01/02/03 subfolders), a DB reset + rescan parsed season=1/2/3 — but
every TYBW cour is TVDB season 17. The fribb_authority metric VETOED all correct
cours ("fribb season 17 != parsed 1"), the only unmapped cour won on title, failed
downstream, and the whole franchise went no_match at 0%.

Principle under test: a parsed season Fribb doesn't know for that series is a
HINT, not truth. The veto only stands when the parsed season is REAL (some
sibling AID maps to it — the "MHA S01 file vs S06 candidate" case), and cour
routing proceeds against the candidate's own Fribb season when the parsed one is
synthetic. All Fribb lookups are monkeypatched — no network or cached data.
"""
from __future__ import annotations

from types import SimpleNamespace

from kira.matcher.cascade.metrics.fribb_authority import FribbAuthorityMetric
from kira.matcher.cour_routing import build_cour_routing_table

_MAPPINGS = "kira.providers.anime_mappings.AnimeMappings"

# Bleach: cours 15449/17765/18220 → (74796, S17); umbrella 2369 → (74796, None).
_SEASONS = {15449: 17, 17765: 17, 18220: 17, 2369: None}
_BY_SEASON = {(74796, 17): [15449, 17765, 18220]}


def _patch_fribb(monkeypatch) -> None:
    async def tvdb_id(aid):
        return 74796 if int(aid) in _SEASONS else None

    async def tvdb_season(aid):
        return _SEASONS.get(int(aid))

    async def aids_by_tvdb_season(tvdb, season):
        return list(_BY_SEASON.get((tvdb, season), []))

    async def get(aid):
        return {"aid": aid} if int(aid) in _SEASONS else None

    monkeypatch.setattr(f"{_MAPPINGS}.tvdb_id", tvdb_id)
    monkeypatch.setattr(f"{_MAPPINGS}.tvdb_season", tvdb_season)
    monkeypatch.setattr(f"{_MAPPINGS}.aids_by_tvdb_season", aids_by_tvdb_season)
    monkeypatch.setattr(f"{_MAPPINGS}.get", get)


def _ctx(season: int, *aids: int):
    cands = [SimpleNamespace(provider_id=str(a)) for a in aids]
    return cands, SimpleNamespace(parsed=SimpleNamespace(season=season), candidates=cands)


async def test_synthetic_season_abstains_instead_of_vetoing(monkeypatch) -> None:
    # Parsed season 1 doesn't exist in Fribb for Bleach (no AID maps to
    # (74796, 1)) → the S17 cour must NOT be vetoed; the metric abstains and
    # the exact-title metrics get to pick it.
    _patch_fribb(monkeypatch)
    cands, ctx = _ctx(1, 15449)
    res = await FribbAuthorityMetric().score(cands[0], ctx)
    assert res.raw == 0.0, res.reason
    assert "abstain" in res.reason


async def test_real_season_mismatch_still_vetoes(monkeypatch) -> None:
    # Parsed season 17 IS a real Fribb season of this series — a candidate
    # pinned to a different real season would be genuinely wrong. Simulate by
    # adding a fake (74796, 2) sibling and scoring an S17 cour against parsed=2.
    _patch_fribb(monkeypatch)
    _BY_SEASON[(74796, 2)] = [99999]
    try:
        cands, ctx = _ctx(2, 15449)
        res = await FribbAuthorityMetric().score(cands[0], ctx)
        assert res.raw == -1.0, res.reason
        assert "veto" in res.reason
    finally:
        del _BY_SEASON[(74796, 2)]


async def test_matching_season_still_promotes(monkeypatch) -> None:
    # The happy path is untouched: parsed 17 + S17 cour → full promotion.
    _patch_fribb(monkeypatch)
    cands, ctx = _ctx(17, 15449)
    res = await FribbAuthorityMetric().score(cands[0], ctx)
    assert res.raw == 1.0, res.reason


async def test_cour_routing_builds_table_from_synthetic_season(monkeypatch) -> None:
    # Files in a synthetic "Season 02" folder (parsed 2, no Fribb AID at
    # (74796, 2)) still get the REAL S17 routing table, so continuous episode
    # numbers route to the right cour.
    _patch_fribb(monkeypatch)
    monkeypatch.setattr(
        "kira.providers.anidb.AniDBProvider._load_ep_count_cache",
        classmethod(lambda cls: {15449: 13, 17765: 13, 18220: 14}),
    )
    table = await build_cour_routing_table("anidb", "15449", 2)
    assert table == [(1, 13, 15449, 0), (14, 26, 17765, 13), (27, 40, 18220, 26)]


async def test_cour_routing_still_refuses_real_season_mismatch(monkeypatch) -> None:
    # When the parsed season IS real for this series but disagrees with the
    # candidate's, routing must refuse (stale-entry guard preserved).
    _patch_fribb(monkeypatch)
    _BY_SEASON[(74796, 2)] = [99999]
    try:
        monkeypatch.setattr(
            "kira.providers.anidb.AniDBProvider._load_ep_count_cache",
            classmethod(lambda cls: {15449: 13, 17765: 13, 18220: 14}),
        )
        assert await build_cour_routing_table("anidb", "15449", 2) is None
    finally:
        del _BY_SEASON[(74796, 2)]
