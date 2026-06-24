"""Radarr path relink — when Kira renames a movie FOLDER, push the new path to
Radarr (so its file doesn't orphan), then refresh. Undo passes the roots reversed.

The movie sibling of test_sonarr_relink. The pure path matrix lives in
test_arr_paths (shared); here `relink_movie` is exercised end-to-end against an
httpx MockTransport (GET /movie → PUT /movie/{id}?moveFiles=false → RefreshMovie)."""
from __future__ import annotations

import json

import httpx
import pytest

from kira.integrations import radarr
from kira.integrations.radarr import RadarrConfig


def _route(monkeypatch, *, movie, captured, put_status=200):
    """MockTransport serving GET /movie, PUT /movie/{id}, POST /command."""
    def handler(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if method == "GET" and path.endswith("/api/v3/movie"):
            return httpx.Response(200, json=[movie])
        if method == "PUT" and "/api/v3/movie/" in path:
            captured["put_body"] = json.loads(request.content.decode())
            captured["put_params"] = dict(request.url.params)
            return httpx.Response(put_status, json=captured["put_body"])
        if method == "POST" and path.endswith("/api/v3/command"):
            captured["refresh"] = json.loads(request.content.decode())
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404)

    real = httpx.AsyncClient

    class Capturing(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **k)

    monkeypatch.setattr(radarr.httpx, "AsyncClient", Capturing)


@pytest.mark.asyncio
async def test_relink_updates_path_then_refreshes(monkeypatch):
    captured: dict = {}
    _route(monkeypatch, captured=captured,
           movie={"id": 7, "tmdbId": 27205, "path": "/data/media/movies/Inception (2010)"})
    cfg = RadarrConfig(base_url="http://rad:7878", api_key="k")
    ok, changed, detail = await radarr.relink_movie(
        cfg, 27205,
        old_root="/media/movies/Inception (2010)",
        new_root="/media/movies/Inception (2010) [Bluray-1080p]",
    )
    assert ok is True and changed is True
    assert captured["put_body"]["path"] == "/data/media/movies/Inception (2010) [Bluray-1080p]"
    assert captured["put_params"]["moveFiles"] == "false"   # Kira already moved it
    assert captured["refresh"]["name"] == "RefreshMovie"
    assert captured["refresh"]["movieIds"] == [7]
    assert "path" in detail


@pytest.mark.asyncio
async def test_relink_no_path_change_just_refreshes(monkeypatch):
    # Folder unchanged → no PUT, only a refresh.
    captured: dict = {}
    _route(monkeypatch, captured=captured,
           movie={"id": 7, "tmdbId": 27205, "path": "/data/media/movies/Film"})
    cfg = RadarrConfig(base_url="http://rad:7878", api_key="k")
    ok, changed, detail = await radarr.relink_movie(
        cfg, 27205, old_root="/media/movies/Film", new_root="/media/movies/Film",
    )
    assert ok is True and changed is False
    assert "put_body" not in captured
    assert captured.get("refresh", {}).get("name") == "RefreshMovie"
    assert detail == "refreshed"


@pytest.mark.asyncio
async def test_relink_unmappable_path_still_refreshes(monkeypatch):
    # Cross-mount move we can't translate → leave path alone, still refresh.
    captured: dict = {}
    _route(monkeypatch, captured=captured,
           movie={"id": 7, "tmdbId": 27205, "path": "/data/media/movies/Film"})
    cfg = RadarrConfig(base_url="http://rad:7878", api_key="k")
    ok, changed, detail = await radarr.relink_movie(
        cfg, 27205, old_root="/media/movies/Film", new_root="/elsewhere/Film (2020)",
    )
    assert ok is True and changed is False
    assert "put_body" not in captured
    assert "couldn't map" in detail


@pytest.mark.asyncio
async def test_relink_movie_not_in_radarr(monkeypatch):
    # The tmdbId the hook asks for isn't the one Radarr returns → benign skip.
    captured: dict = {}
    _route(monkeypatch, captured=captured,
           movie={"id": 7, "tmdbId": 99999, "path": "/data/media/movies/Other"})
    cfg = RadarrConfig(base_url="http://rad:7878", api_key="k")
    ok, changed, detail = await radarr.relink_movie(
        cfg, 27205, old_root="/media/movies/X", new_root="/media/movies/Y",
    )
    assert ok is False and changed is False
    assert detail == "not in Radarr"
