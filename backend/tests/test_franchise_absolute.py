"""franchise_absolute — AID-local episode → franchise-absolute number.

The rename-output half of the absolute↔local bridge: a locally-named anime file
(`AoT S4E01`) matched to a per-cour AniDB AID gets its franchise-absolute number
(60) for the `{{absx}}` template token. Pure arithmetic over the offset table.
"""
from __future__ import annotations

from kira.matcher.cour_routing import franchise_absolute


# Attack-on-Titan-shaped franchise: 4 seasons/cours, 25/12/22/28 eps.
_AOT = [(100, 1, 25), (200, 26, 37), (300, 38, 59), (400, 60, 87)]


def test_final_season_first_episode_is_franchise_absolute():
    # S4E01 (local ep 1 of cour 400) → episode 60 of the franchise.
    assert franchise_absolute(_AOT, 400, 1) == 60


def test_mid_cour_episode():
    assert franchise_absolute(_AOT, 400, 5) == 64   # 60 + 5 - 1
    assert franchise_absolute(_AOT, 300, 1) == 38    # season 3 ep 1
    assert franchise_absolute(_AOT, 200, 12) == 37   # last ep of season 2


def test_first_cour_is_identity():
    assert franchise_absolute(_AOT, 100, 1) == 1
    assert franchise_absolute(_AOT, 100, 25) == 25


def test_local_ep_past_cour_span_refuses_to_guess():
    # Cour 400 owns [60, 87] = 28 eps; local 29 would be 88 → outside → None.
    assert franchise_absolute(_AOT, 400, 29) is None
    # Cour 100 owns [1, 25]; local 26 → 26 → outside → None.
    assert franchise_absolute(_AOT, 100, 26) is None


def test_unknown_aid_returns_none():
    assert franchise_absolute(_AOT, 999, 1) is None


def test_degenerate_inputs_return_none():
    assert franchise_absolute(None, 400, 1) is None
    assert franchise_absolute([], 400, 1) is None
    assert franchise_absolute(_AOT, None, 1) is None
    assert franchise_absolute(_AOT, 400, None) is None
    assert franchise_absolute(_AOT, 400, 0) is None
    assert franchise_absolute(_AOT, 400, -3) is None


def test_single_season_franchise():
    solo = [(500, 1, 12)]
    assert franchise_absolute(solo, 500, 7) == 7
    assert franchise_absolute(solo, 500, 13) is None


# ── _resolve_franchise_absolute (the rename-path glue) ───────────────────────
import pytest


class _FakeAnidb:
    def __init__(self, offsets):
        self._offsets = offsets

    async def get_franchise_offsets(self, _aid):
        return self._offsets


class _Sel:
    def __init__(self, provider_id, episode_number):
        self.provider_id = provider_id
        self.episode_number = episode_number


class _Parsed:
    def __init__(self, episode):
        self.episode = episode


@pytest.mark.asyncio
async def test_resolve_uses_match_episode_number():
    from kira.api.rename import _resolve_franchise_absolute
    anidb = _FakeAnidb([(400, 60, 87)])
    # Match.episode_number is the AID-local ep AFTER cour routing — use it.
    got = await _resolve_franchise_absolute(anidb, _Sel("400", 1), _Parsed(1))
    assert got == 60


@pytest.mark.asyncio
async def test_resolve_falls_back_to_parsed_episode():
    from kira.api.rename import _resolve_franchise_absolute
    anidb = _FakeAnidb([(400, 60, 87)])
    # No episode_number on the match → fall back to parsed.episode.
    got = await _resolve_franchise_absolute(anidb, _Sel("400", None), _Parsed(3))
    assert got == 62


@pytest.mark.asyncio
async def test_resolve_none_when_offsets_empty():
    # Banned / unresolvable franchise → get_franchise_offsets returns [] → None
    # → the filename keeps its SxE fallback (ban-safe).
    from kira.api.rename import _resolve_franchise_absolute
    got = await _resolve_franchise_absolute(_FakeAnidb([]), _Sel("400", 1), _Parsed(1))
    assert got is None


@pytest.mark.asyncio
async def test_resolve_none_on_bad_provider_id():
    from kira.api.rename import _resolve_franchise_absolute
    anidb = _FakeAnidb([(400, 60, 87)])
    got = await _resolve_franchise_absolute(anidb, _Sel("not-an-int", 1), _Parsed(1))
    assert got is None
