"""Radarr add_movie — the collection-completion "Get from Radarr" action.

find-or-add: if the movie is already in Radarr, just search it; otherwise look up
its addable shape and POST it with the user's quality profile + root folder. Needs
both configured (Radarr can't add without them). Exercised against an httpx
MockTransport (same harness as test_radarr_relink)."""
from __future__ import annotations

import json

import httpx
import pytest

from kira.integrations import radarr
from kira.integrations.radarr import RadarrConfig, RadarrError


def _route(monkeypatch, *, existing, captured):
    """MockTransport: GET /movie (existing?), GET /movie/lookup, POST /movie, POST /command."""
    def handler(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if method == "GET" and path.endswith("/api/v3/movie"):
            return httpx.Response(200, json=existing)
        if method == "GET" and path.endswith("/api/v3/movie/lookup"):
            captured["lookup_term"] = request.url.params.get("term")
            return httpx.Response(200, json=[{"tmdbId": 27205, "title": "Inception", "year": 2010}])
        if method == "POST" and path.endswith("/api/v3/movie"):
            captured["add_body"] = json.loads(request.content.decode())
            return httpx.Response(201, json={"id": 42})
        if method == "POST" and path.endswith("/api/v3/command"):
            captured["command"] = json.loads(request.content.decode())
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404)

    real = httpx.AsyncClient

    class Capturing(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **k)

    monkeypatch.setattr(radarr.httpx, "AsyncClient", Capturing)


def _cfg() -> RadarrConfig:
    return RadarrConfig(base_url="http://rad:7878", api_key="k",
                        quality_profile_id=4, root_folder_path="/movies")


@pytest.mark.asyncio
async def test_add_new_movie_posts_with_profile_and_searches(monkeypatch):
    captured: dict = {}
    _route(monkeypatch, existing=[], captured=captured)  # not in Radarr yet
    ok, added, detail = await radarr.add_movie(_cfg(), 27205)
    assert ok is True and added is True
    assert captured["lookup_term"] == "tmdb:27205"
    body = captured["add_body"]
    assert body["qualityProfileId"] == 4
    assert body["rootFolderPath"] == "/movies"
    assert body["monitored"] is True
    assert body["addOptions"] == {"searchForMovie": True}
    assert "searching" in detail


@pytest.mark.asyncio
async def test_add_existing_movie_just_searches(monkeypatch):
    captured: dict = {}
    _route(monkeypatch, existing=[{"id": 99, "tmdbId": 27205}], captured=captured)
    ok, added, detail = await radarr.add_movie(_cfg(), 27205)
    assert ok is True and added is False
    assert "add_body" not in captured             # no POST /movie
    assert captured["command"]["name"] == "MoviesSearch"
    assert captured["command"]["movieIds"] == [99]
    assert "already in Radarr" in detail


@pytest.mark.asyncio
async def test_add_movie_requires_profile_and_root():
    cfg = RadarrConfig(base_url="http://rad:7878", api_key="k")  # no profile/root
    with pytest.raises(RadarrError, match="quality profile or root folder"):
        await radarr.add_movie(cfg, 27205)
