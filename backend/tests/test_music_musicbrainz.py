"""MusicBrainz client — release-by-MBID parsing (the id-bypass path), album
search ranking, and best-effort error handling. httpx mocked; the 1 req/s gate
is neutralized for speed."""
from __future__ import annotations

import httpx
import pytest

import kira.music.musicbrainz as mb
from kira.music.musicbrainz import get_release, search_releases


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_get_release_parses_tracks(monkeypatch):
    monkeypatch.setattr(mb, "_MB_MIN_INTERVAL", 0)
    release_json = {
        "id": "rel-1", "title": "Discovery", "date": "2001-03-12",
        "artist-credit": [{"name": "Daft Punk", "joinphrase": "", "artist": {"id": "a1", "name": "Daft Punk"}}],
        "release-group": {"id": "rg-1"},
        "media": [
            {"position": 1, "tracks": [
                {"position": 1, "title": "One More Time", "length": 320000, "recording": {"id": "rec-1", "title": "One More Time"}},
                {"position": 2, "title": "Aerodynamic", "length": 207000, "recording": {"id": "rec-2"}},
            ]},
            {"position": 2, "tracks": [
                {"position": 1, "title": "Bonus", "length": 100000, "recording": {"id": "rec-9"}},
            ]},
        ],
    }

    def handler(req):
        assert "/release/rel-1" in req.url.path
        return httpx.Response(200, json=release_json)

    async with _client(handler) as c:
        rel = await get_release(c, "rel-1")
    assert rel is not None
    assert rel.title == "Discovery" and rel.artist == "Daft Punk" and rel.year == 2001
    assert rel.release_group_id == "rg-1" and rel.track_count == 3
    assert len(rel.tracks) == 3
    t0 = rel.tracks[0]
    assert (t0.disc, t0.position, t0.recording_id, t0.title, t0.length_ms) == (1, 1, "rec-1", "One More Time", 320000)
    assert rel.tracks[2].disc == 2 and rel.tracks[2].position == 1  # second medium
    assert rel.cover_art_front_url() == "https://coverartarchive.org/release/rel-1/front-500"


@pytest.mark.asyncio
async def test_search_releases_parses_and_queries(monkeypatch):
    monkeypatch.setattr(mb, "_MB_MIN_INTERVAL", 0)
    search_json = {"releases": [
        {"id": "rel-1", "title": "Discovery", "score": 100, "date": "2001", "track-count": 14,
         "artist-credit": [{"name": "Daft Punk"}]},
        {"id": "rel-2", "title": "Discovery (Deluxe)", "score": 80, "artist-credit": [{"name": "Daft Punk"}]},
    ]}

    def handler(req):
        q = req.url.params.get("query")
        assert "Discovery" in q and "Daft Punk" in q
        # track count is a RANKING signal (matcher side), deliberately NOT a query
        # filter — a deluxe/clean edition off by a track must still come back.
        assert "tracks:" not in q
        return httpx.Response(200, json=search_json)

    async with _client(handler) as c:
        hits = await search_releases(c, "Daft Punk", "Discovery", track_count=14)
    assert [h.id for h in hits] == ["rel-1", "rel-2"]
    assert hits[0].score == 100 and hits[0].track_count == 14 and hits[0].artist == "Daft Punk"


@pytest.mark.asyncio
async def test_search_recordings_parses(monkeypatch):
    monkeypatch.setattr(mb, "_MB_MIN_INTERVAL", 0)
    rec_json = {"recordings": [
        {"id": "rec-1", "title": "Yummy", "score": 100, "first-release-date": "2020-01-03",
         "artist-credit": [{"name": "Justin Bieber"}],
         "releases": [{"id": "rel-x", "title": "Changes", "date": "2020"}]},
    ]}

    def handler(req):
        assert "/recording" in req.url.path
        q = req.url.params.get("query")
        assert "Yummy" in q and "Justin Bieber" in q
        return httpx.Response(200, json=rec_json)

    async with _client(handler) as c:
        hits = await mb.search_recordings(c, "Justin Bieber", "Yummy")
    assert len(hits) == 1
    h = hits[0]
    assert h.recording_id == "rec-1" and h.title == "Yummy" and h.artist == "Justin Bieber"
    assert h.release_id == "rel-x" and h.year == 2020
    assert h.cover_art_front_url() == "https://coverartarchive.org/release/rel-x/front-500"


@pytest.mark.asyncio
async def test_http_error_is_best_effort(monkeypatch):
    monkeypatch.setattr(mb, "_MB_MIN_INTERVAL", 0)
    async with _client(lambda req: httpx.Response(503)) as c:
        assert await get_release(c, "x") is None
        assert await search_releases(c, "a", "b") == []
