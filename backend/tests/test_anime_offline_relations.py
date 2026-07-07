"""Anime-speed Phase 2 — offline franchise resolution & count fallbacks.

The franchise walk was the single biggest cold-scan cost: one throttled
5-second AniDB call per franchise member. These tests pin the offline path:

  - index v2 build extracts entries (incl. ONGOING) + the relations graph
  - related_closure() BFS semantics (symmetrized edges, singleton, unknown)
  - get_related_aids serves offline closures with the Fribb same-series gate,
    queues verification, and never writes offline data to the authoritative
    relations cache
  - get_episode_count falls back to a stale offline count when live fails
"""
from __future__ import annotations

import json

import httpx
import pytest

from kira.providers import anime_offline_db as aodb
from kira.providers import build_provider
from kira.providers.anidb import AniDBProvider
from kira.providers.base import ProviderConfig, ProviderMode


# ── Fixtures ─────────────────────────────────────────────────────────────────

_DUMP = {
    "data": [
        # A 3-cour TV franchise: 100 → 101 → 102 (partially one-way edges on purpose)
        {"sources": ["https://anidb.net/anime/100"], "title": "Cour One",
         "type": "TV", "episodes": 12, "status": "FINISHED",
         "animeSeason": {"season": "SUMMER", "year": 2020},
         "relatedAnime": ["https://anidb.net/anime/101"]},
        {"sources": ["https://anidb.net/anime/101"], "title": "Cour Two",
         "type": "TV", "episodes": 12, "status": "FINISHED",
         "animeSeason": {"season": "WINTER", "year": 2021},
         "relatedAnime": ["https://anidb.net/anime/102"]},   # no back-edge to 100
        {"sources": ["https://anidb.net/anime/102"], "title": "Cour Three",
         "type": "TV", "episodes": 13, "status": "ONGOING",
         "animeSeason": {"season": "SUMMER", "year": 2025},
         "relatedAnime": []},
        # A side story linked into the franchise (own TVDB series in Fribb)
        {"sources": ["https://anidb.net/anime/200"], "title": "Side Story",
         "type": "TV", "episodes": 12, "status": "FINISHED",
         "animeSeason": {"season": "SPRING", "year": 2022},
         "relatedAnime": ["https://anidb.net/anime/100"]},
        # A singleton with no relations
        {"sources": ["https://anidb.net/anime/300"], "title": "Lone Movie",
         "type": "MOVIE", "episodes": 1, "status": "FINISHED",
         "animeSeason": {"season": "FALL", "year": 2019},
         "relatedAnime": []},
        # Not AniDB-linked — must be ignored entirely
        {"sources": ["https://myanimelist.net/anime/999"], "title": "MAL only",
         "type": "TV", "episodes": 24, "status": "FINISHED",
         "animeSeason": {}, "relatedAnime": []},
    ]
}


@pytest.fixture()
def offline_index(tmp_path, monkeypatch):
    """Build a real v2 index from a synthetic dump and point the module at it."""
    raw = tmp_path / "dump.json"
    raw.write_text(json.dumps(_DUMP), encoding="utf-8")
    built = aodb._build_index_from_raw(raw)
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({
        "v": aodb._INDEX_VERSION, "built_at": 0,
        "counts": {str(k): v for k, v in built["counts"].items()},
        "entries": {str(k): v for k, v in built["entries"].items()},
        "relations": {str(k): v for k, v in built["relations"].items()},
    }), encoding="utf-8")
    monkeypatch.setattr(aodb, "_INDEX_PATH", idx)
    monkeypatch.setattr(aodb, "_index", None)
    monkeypatch.setattr(aodb, "_entries", None)
    monkeypatch.setattr(aodb, "_relations", None)
    yield built
    # Reset the singletons so other tests never see this fixture's maps.
    aodb._index = None
    aodb._entries = None
    aodb._relations = None


# ── Index build ──────────────────────────────────────────────────────────────

def test_v2_build_extracts_counts_entries_relations(offline_index):
    built = offline_index
    # counts: FINISHED only (102 is ONGOING, 999 not AniDB-linked)
    assert built["counts"] == {100: 12, 101: 12, 200: 12, 300: 1}
    # entries include the ongoing cour with status char O
    assert built["entries"][102] == [13, "O", "T", 2025]
    assert built["entries"][300] == [1, "F", "M", 2019]
    assert 999 not in built["entries"]
    # relations keep only AniDB-linked edges
    assert built["relations"][100] == [101]
    assert built["relations"][200] == [100]


def test_related_closure_symmetrizes_one_way_edges(offline_index):
    # 101 only lists 102; the edge 100→101 is one-way. Closure from ANY member
    # must reach the whole component (including the side story, ungated here).
    assert aodb.related_closure(101) == [100, 101, 102, 200]
    assert aodb.related_closure(100) == [100, 101, 102, 200]


def test_related_closure_singleton_and_unknown(offline_index):
    assert aodb.related_closure(300) == [300]      # known, no edges
    assert aodb.related_closure(555) is None       # not in dump → live walk


def test_offline_count_includes_ongoing(offline_index):
    assert aodb.offline_count(102) == (13, "O")
    assert aodb.offline_count(100) == (12, "F")
    assert aodb.offline_count(555) is None


# ── Provider integration ─────────────────────────────────────────────────────

async def _provider():
    client = httpx.AsyncClient()
    return build_provider("anidb", ProviderConfig(mode=ProviderMode.DIRECT), client), client


async def test_get_related_aids_offline_with_fribb_gate(offline_index, monkeypatch, tmp_path):
    """Offline closure serves instantly, the side story (different TVDB
    series) is gated out, the seed queues for verification, and NOTHING is
    written to the authoritative relations cache."""
    from kira.providers.anime_mappings import AnimeMappings

    # Fribb: franchise cours map to TVDB series 7777; side story to 8888.
    async def _tvdb_id(aid: int):
        return {100: 7777, 101: 7777, 102: 7777, 200: 8888}.get(aid)
    monkeypatch.setattr(AnimeMappings, "tvdb_id", classmethod(
        lambda cls, aid: _tvdb_id(aid)))

    # Isolate the authoritative caches.
    monkeypatch.setattr(AniDBProvider, "_relations_cache", {})
    monkeypatch.setattr(AniDBProvider, "_relations_verify_pending", set())

    async def _boom(self, aid):  # live walk must NOT run
        raise AssertionError("live walk called on the offline path")
    monkeypatch.setattr(AniDBProvider, "_related_aids_live", _boom)

    prov, client = await _provider()
    try:
        group = await prov.get_related_aids("101")
        assert group == [100, 101, 102]                 # side story 200 gated out
        assert AniDBProvider._relations_verify_pending == {101}
        assert AniDBProvider._relations_cache == {}     # offline never persists
    finally:
        await client.aclose()


async def test_get_related_aids_authoritative_cache_wins(offline_index, monkeypatch):
    """A verified (cached) closure short-circuits BEFORE the offline path."""
    monkeypatch.setattr(AniDBProvider, "_relations_cache", {"101": [100, 101]})
    monkeypatch.setattr(AniDBProvider, "_relations_verify_pending", set())
    prov, client = await _provider()
    try:
        assert await prov.get_related_aids("101") == [100, 101]
        assert AniDBProvider._relations_verify_pending == set()
    finally:
        await client.aclose()


async def test_get_related_aids_unknown_falls_back_to_live(offline_index, monkeypatch):
    """An AID missing from the dump routes to the live walk (stubbed here)."""
    monkeypatch.setattr(AniDBProvider, "_relations_cache", {})
    monkeypatch.setattr(AniDBProvider, "_relations_verify_pending", set())

    async def _live(self, aid):
        return [555, 556]
    monkeypatch.setattr(AniDBProvider, "_related_aids_live", _live)

    prov, client = await _provider()
    try:
        assert await prov.get_related_aids("555") == [555, 556]
        assert AniDBProvider._relations_verify_pending == set()
    finally:
        await client.aclose()


async def test_episode_count_falls_back_to_stale_offline(offline_index, monkeypatch):
    """Live fetch fails (ban/error) → the ONGOING offline count serves as a
    stale hint, and is NOT persisted to the authoritative count cache."""
    monkeypatch.setattr(AniDBProvider, "_ep_count_cache", {})

    async def _dead(self, aid):
        return None
    monkeypatch.setattr(AniDBProvider, "_http_api", _dead)

    prov, client = await _provider()
    try:
        assert await prov.get_episode_count(102) == 13
        assert 102 not in AniDBProvider._ep_count_cache
    finally:
        await client.aclose()


# ── Phase 5: status-aware XML cache TTL ─────────────────────────────────────

def test_xml_cache_ttl_is_status_aware(offline_index, monkeypatch, tmp_path):
    """A 3-day-old cached XML is FRESH for a FINISHED show (30d TTL) but
    STALE for an ONGOING one (24h TTL) — finished episode data is immutable,
    airing shows must refresh daily."""
    import os
    import time

    def _fake_path(cls, aid):
        return tmp_path / f"{aid}.xml"
    monkeypatch.setattr(AniDBProvider, "_xml_cache_path", classmethod(_fake_path))

    three_days_ago = time.time() - 3 * 24 * 3600
    for aid in ("100", "102"):                     # 100=FINISHED, 102=ONGOING
        p = tmp_path / f"{aid}.xml"
        p.write_text("<anime id='%s'/>" % aid, encoding="utf-8")
        os.utime(p, (three_days_ago, three_days_ago))

    assert AniDBProvider._read_xml_cache("100") is not None   # finished → fresh
    assert AniDBProvider._read_xml_cache("102") is None       # ongoing → stale
