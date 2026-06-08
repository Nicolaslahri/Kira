"""Absolute-numbered anime → AniDB cour routing (AoT Final Season).

AoT's Final Season files are SERIES-ABSOLUTE numbered ("- 60".."- 89") and span
THREE AniDB cours (14977 Part 1 / 16177 2022 / 17303 Final Chapters). Two gaps
forced them onto the single TVDB block instead:

  1. route_file_to_cour was keyed in TVDB-LOCAL space (1..30); a file numbered 60
     fell outside every range → no route.
  2. EpisodeCountSanityMetric compared the cluster's max (89, an ABSOLUTE index)
     against each cour's own/own-season count (16 / 12 / 2 / 30) → vetoed all
     three → only TVDB survived → cour routing never ran.

These tests lock BOTH fixes AND the protections that must stay:
  - Bleach TYBW (cour-LOCAL numbered 1..40) still routes directly.
  - a standalone OVA (no multi-AID franchise) is STILL vetoed.
"""

from __future__ import annotations

import pytest

from kira.matcher.cour_routing import route_file_to_cour

# ── AoT Final Season: TVDB S4 local eps 1-30 → 3 AniDB cours; absolute 60-89. ──
_AOT_TABLE = [(1, 16, 14977, 0), (17, 28, 16177, 16), (29, 30, 17303, 28)]
_AOT_ABS2LOCAL = {a: a - 59 for a in range(60, 90)}   # 60→1 … 89→30

# Bleach TYBW: cour-local numbered 1..40, no absolute bridge needed.
_BLEACH_TABLE = [(1, 13, 15449, 0), (14, 26, 17849, 13), (27, 40, 18671, 26)]


# ── 1. The absolute→local routing bridge ──────────────────────────────────
def test_absolute_file_bridges_into_correct_cour():
    assert route_file_to_cour(_AOT_TABLE, 60, _AOT_ABS2LOCAL) == (14977, 1)
    assert route_file_to_cour(_AOT_TABLE, 75, _AOT_ABS2LOCAL) == (14977, 16)
    assert route_file_to_cour(_AOT_TABLE, 76, _AOT_ABS2LOCAL) == (16177, 1)
    assert route_file_to_cour(_AOT_TABLE, 87, _AOT_ABS2LOCAL) == (16177, 12)
    assert route_file_to_cour(_AOT_TABLE, 88, _AOT_ABS2LOCAL) == (17303, 1)
    assert route_file_to_cour(_AOT_TABLE, 89, _AOT_ABS2LOCAL) == (17303, 2)


def test_without_map_absolute_file_misses():
    # Pre-fix behavior: absolute 60 isn't in the local 1..30 table → no route.
    # Proves the bridge is what rescues it (and that it's opt-in via the map).
    assert route_file_to_cour(_AOT_TABLE, 60) is None
    assert route_file_to_cour(_AOT_TABLE, 60, None) is None


def test_direct_local_hit_wins_over_bridge():
    # Cour-local numbering routes directly; the bridge is a fallback only on a
    # direct miss — so Bleach-style numbering is untouched even if a map leaks in.
    assert route_file_to_cour(_BLEACH_TABLE, 14) == (17849, 1)
    assert route_file_to_cour(_BLEACH_TABLE, 27) == (18671, 1)
    assert route_file_to_cour(_BLEACH_TABLE, 5, {5: 999}) == (15449, 5)


def test_absolute_outside_franchise_is_none():
    assert route_file_to_cour(_AOT_TABLE, 200, _AOT_ABS2LOCAL) is None


# ── 2. EpisodeCountSanity whole-franchise abstain (the veto gate) ──────────
_AOT_COURS = [14977, 16177, 17303]
_AOT_ALL = [9541, 10944, 13241, 14444, 14977, 16177, 17303]
_COUNTS = {9541: 25, 10944: 12, 13241: 12, 14444: 10, 14977: 16, 16177: 12, 17303: 2}


def _parsed(season=4, episode=60, absolute=60, max_ep=89, size=30):
    from kira.parser import ParsedFile
    p = ParsedFile(original_filename="f.mkv", media_type="anime",
                   title="Attack on Titan", season=season, episode=episode,
                   absolute_episode=absolute)
    p._cluster_max_episode = max_ep   # type: ignore[attr-defined]
    p._cluster_size = size            # type: ignore[attr-defined]
    return p


def _patch_fribb(monkeypatch, *, counts, tvdb_of, season_of, by_season, by_tvdb):
    from kira.providers.anidb import AniDBProvider
    from kira.providers.anime_mappings import AnimeMappings
    monkeypatch.setattr(AniDBProvider, "_load_ep_count_cache",
                        classmethod(lambda cls: dict(counts)))

    async def _tvdb(cls, aid):
        return tvdb_of.get(aid)

    async def _season(cls, aid):
        return season_of.get(aid)

    async def _by_season(cls, tvdb, season):
        return list(by_season.get((tvdb, season), []))

    async def _by_tvdb(cls, tvdb):
        return list(by_tvdb.get(tvdb, []))

    monkeypatch.setattr(AnimeMappings, "tvdb_id", classmethod(_tvdb))
    monkeypatch.setattr(AnimeMappings, "tvdb_season", classmethod(_season))
    monkeypatch.setattr(AnimeMappings, "aids_by_tvdb_season", classmethod(_by_season))
    monkeypatch.setattr(AnimeMappings, "aids_by_tvdb", classmethod(_by_tvdb))


def _ctx(parsed):
    from kira.matcher.cascade.types import CascadeContext
    return CascadeContext(parsed=parsed, candidates=[], provider_key="anidb")


def _cand(aid):
    return type("C", (), {"provider_id": str(aid)})()


@pytest.mark.asyncio
async def test_final_season_cour_not_vetoed_via_whole_franchise(monkeypatch):
    """AID 14977 (16 eps) for an absolute cluster maxing at 89: same-season
    aggregate (30) is short, but the WHOLE franchise (89) covers it → abstain,
    NOT veto. This is what lets the cours win `top` so cour routing can run."""
    from kira.matcher.cascade.metrics.episode_count_sanity import EpisodeCountSanityMetric
    _patch_fribb(
        monkeypatch, counts=_COUNTS,
        tvdb_of={a: 267440 for a in _AOT_ALL},
        season_of={**{a: 4 for a in _AOT_COURS}, 9541: 1, 10944: 2, 13241: 3, 14444: 3},
        by_season={(267440, 4): _AOT_COURS},
        by_tvdb={267440: _AOT_ALL},
    )
    res = await EpisodeCountSanityMetric().score(_cand(14977), _ctx(_parsed()))
    assert res.raw == 0.0, f"expected abstain, got veto: {res.reason}"


@pytest.mark.asyncio
async def test_standalone_ova_still_vetoed(monkeypatch):
    """The protection that must STAY: a 2-ep standalone OVA (its tvdb_id maps to
    a single AID — not a multi-cour franchise) matched to a 30-file cluster
    maxing at 89 is still vetoed. The whole-franchise abstain is NOT a blanket
    pass — it only spares members of a real >1-AID franchise."""
    from kira.matcher.cascade.metrics.episode_count_sanity import EpisodeCountSanityMetric
    _patch_fribb(
        monkeypatch, counts={88888: 2},
        tvdb_of={88888: 555}, season_of={88888: 4},
        by_season={(555, 4): [88888]},      # only itself
        by_tvdb={555: [88888]},             # single-AID tvdb → no franchise
    )
    res = await EpisodeCountSanityMetric().score(_cand(88888), _ctx(_parsed()))
    assert res.raw == -1.0, f"standalone OVA should be vetoed, got: {res.reason}"


@pytest.mark.asyncio
async def test_bleach_cour_passes_via_same_season_aggregate(monkeypatch):
    """Bleach TYBW Cour 1 (13 eps) for a 40-file cluster maxing at 40: the
    SAME-season aggregate (13+13+14=40) already covers it — must pass without
    needing the new whole-franchise path (i.e. behavior preserved)."""
    from kira.matcher.cascade.metrics.episode_count_sanity import EpisodeCountSanityMetric
    bleach = [15449, 17849, 18671]
    _patch_fribb(
        monkeypatch, counts={15449: 13, 17849: 13, 18671: 14},
        tvdb_of={a: 74796 for a in bleach},
        season_of={a: 17 for a in bleach},
        by_season={(74796, 17): bleach},
        by_tvdb={74796: bleach},
    )
    p = _parsed(season=17, episode=14, absolute=None, max_ep=40, size=40)
    res = await EpisodeCountSanityMetric().score(_cand(15449), _ctx(p))
    assert res.raw == 0.0, f"Bleach cour should pass, got veto: {res.reason}"
