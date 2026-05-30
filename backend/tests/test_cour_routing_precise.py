"""Pass 5.1 (corrected) — cour routing is summed-count-FIRST; ScudLee only
fills episodes the contiguous table can't place.

Regression: ScudLee's flat mapping fallback returns the first cour's AID for
every episode of a multi-cour TVDB season, which collapsed all of Bleach/AoT
onto Cour 1 and orphaned everything past it. Table-first prevents that.

All ScudLee/Fribb lookups are monkeypatched — no network or cached data needed.
"""

from __future__ import annotations

from kira.matcher.cour_routing import route_file_to_cour_precise

# Bleach S17 (TYBW) summed-count table: 3 contiguous cours, 13/13/14 = eps 1-40.
_TABLE = [(1, 13, 15449, 0), (14, 26, 17849, 13), (27, 40, 18671, 26)]


def _patch(monkeypatch, *, tvdb_id, resolve):
    async def fake_tvdb_id(aid):
        return tvdb_id

    async def fake_resolve(tvdb, season, episode):
        return resolve(tvdb, season, episode)

    monkeypatch.setattr("kira.providers.anime_mappings.AnimeMappings.tvdb_id", fake_tvdb_id)
    monkeypatch.setattr("kira.providers.anime_lists.resolve_tvdb_to_anidb", fake_resolve)


async def test_contiguous_cours_route_via_table_not_scudlee(monkeypatch) -> None:
    """THE regression guard. ScudLee's buggy flat answer (everything → Cour 1)
    must be ignored for in-range episodes; the table routes each to its cour."""
    calls = {"n": 0}

    async def fake_tvdb_id(aid):
        return 74796

    async def fake_resolve(t, s, e):
        calls["n"] += 1
        return (15449, e)  # the buggy flat fallback: always Cour 1

    monkeypatch.setattr("kira.providers.anime_mappings.AnimeMappings.tvdb_id", fake_tvdb_id)
    monkeypatch.setattr("kira.providers.anime_lists.resolve_tvdb_to_anidb", fake_resolve)

    async def route(ep):
        return await route_file_to_cour_precise(
            _TABLE, ep, provider="anidb", top_provider_id="15449", parsed_season=17,
        )

    assert await route(13) == (15449, 13)   # Cour 1
    assert await route(14) == (17849, 1)    # Cour 2 ep 1 — NOT (15449, 14)
    assert await route(27) == (18671, 1)    # Cour 3 ep 1 — NOT (15449, 27)
    assert await route(40) == (18671, 14)   # Cour 3 last
    assert calls["n"] == 0                  # in-range never consulted ScudLee


async def test_in_range_ignores_scudlee(monkeypatch) -> None:
    _patch(monkeypatch, tvdb_id=74796, resolve=lambda t, s, e: (15449, 99))
    res = await route_file_to_cour_precise(
        _TABLE, 15, provider="anidb", top_provider_id="15449", parsed_season=17,
    )
    assert res == (17849, 2)  # table wins; ScudLee's garbage ignored


async def test_out_of_range_filled_by_scudlee(monkeypatch) -> None:
    # ep 41 is beyond every contiguous cour → table can't place → ScudLee fills
    # (only if it lands on a sibling cour).
    _patch(monkeypatch, tvdb_id=74796, resolve=lambda t, s, e: (18671, 14))
    res = await route_file_to_cour_precise(
        _TABLE, 41, provider="anidb", top_provider_id="15449", parsed_season=17,
    )
    assert res == (18671, 14)


async def test_out_of_range_nonsibling_scudlee_rejected(monkeypatch) -> None:
    _patch(monkeypatch, tvdb_id=74796, resolve=lambda t, s, e: (99999, 1))
    res = await route_file_to_cour_precise(
        _TABLE, 41, provider="anidb", top_provider_id="15449", parsed_season=17,
    )
    assert res is None


async def test_out_of_range_no_scudlee(monkeypatch) -> None:
    _patch(monkeypatch, tvdb_id=74796, resolve=lambda t, s, e: None)
    res = await route_file_to_cour_precise(
        _TABLE, 41, provider="anidb", top_provider_id="15449", parsed_season=17,
    )
    assert res is None


async def test_no_table_returns_none() -> None:
    res = await route_file_to_cour_precise(
        None, 5, provider="anidb", top_provider_id="15449", parsed_season=17,
    )
    assert res is None
