"""fanart.tv artwork provider — kind mapping + best-image selection.

Pins the verified v3 contract (github.com/fanart-tv/fanart.tv-api): movies keyed
by TMDB/IMDb, TV by TheTVDB; typed arrays (hdmovielogo, moviebackground, …)
mapped to Kira's local-asset kinds (clearlogo, fanart, …); best image chosen by
language preference then `likes`. Fake client — no network.
"""
from __future__ import annotations

import pytest

from kira.providers import fanarttv


# ── fake httpx client ────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, resp: _Resp):
        self.resp = resp
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url, **kw):
        self.calls.append((url, kw.get("params", {})))
        return self.resp


_MOVIE_PAYLOAD = {
    "name": "Inception", "tmdb_id": "27205", "imdb_id": "tt1375666",
    "movieposter": [{"id": "1", "url": "https://f/poster-en.jpg", "lang": "en", "likes": "5"}],
    "moviebackground": [
        {"id": "2", "url": "https://f/bg-en.jpg", "lang": "en", "likes": "50"},
        {"id": "3", "url": "https://f/bg-textless.jpg", "lang": "00", "likes": "3"},
    ],
    "hdmovielogo": [{"id": "4", "url": "https://f/hdlogo.png", "lang": "en", "likes": "9"}],
    "movielogo": [{"id": "5", "url": "https://f/sdlogo.png", "lang": "en", "likes": "99"}],
    "hdmovieclearart": [{"id": "6", "url": "https://f/clearart.png", "lang": "en", "likes": "2"}],
    "moviedisc": [{"id": "7", "url": "https://f/disc.png", "lang": "en", "likes": "1", "disc_type": "bluray"}],
}

_TV_PAYLOAD = {
    "name": "Frieren", "thetvdb_id": "424536",
    "tvposter": [{"id": "1", "url": "https://f/tvposter.jpg", "lang": "en", "likes": "4"}],
    "showbackground": [{"id": "2", "url": "https://f/showbg.jpg", "lang": "00", "likes": "8"}],
    "hdtvlogo": [{"id": "3", "url": "https://f/hdtvlogo.png", "lang": "en", "likes": "7"}],
    "characterart": [{"id": "4", "url": "https://f/char.png", "lang": "00", "likes": "6"}],
}


# ── pick_best ────────────────────────────────────────────────────────────────
def test_pick_best_prefers_language_then_likes():
    imgs = [
        {"url": "a", "lang": "de", "likes": "100"},
        {"url": "b", "lang": "en", "likes": "5"},
        {"url": "c", "lang": "en", "likes": "40"},
    ]
    # en beats higher-liked de; among en, higher likes wins.
    assert fanarttv.pick_best(imgs, languages=["en"], prefer_textless=False) == "c"


def test_pick_best_textless_for_backgrounds():
    imgs = [
        {"url": "loc", "lang": "en", "likes": "99"},
        {"url": "plate", "lang": "00", "likes": "1"},
    ]
    # backgrounds want the clean textless plate even with far fewer likes.
    assert fanarttv.pick_best(imgs, languages=["en"], prefer_textless=True) == "plate"
    # but as a poster (not textless), the localized one wins.
    assert fanarttv.pick_best(imgs, languages=["en"], prefer_textless=False) == "loc"


def test_pick_best_empty_is_none():
    assert fanarttv.pick_best([], languages=["en"], prefer_textless=False) is None
    assert fanarttv.pick_best(None, languages=["en"], prefer_textless=False) is None


# ── fetch_artwork: movies ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_movie_maps_kinds_and_picks_best():
    client = _FakeClient(_Resp(payload=_MOVIE_PAYLOAD))
    art = await fanarttv.fetch_artwork(
        media_type="movie", client=client, api_key="k", tmdb_id="27205", languages=["en"],
    )
    assert art["poster"] == "https://f/poster-en.jpg"
    assert art["fanart"] == "https://f/bg-textless.jpg"      # textless beats liked en bg
    assert art["clearlogo"] == "https://f/hdlogo.png"         # HD key tried before SD
    assert art["clearart"] == "https://f/clearart.png"
    assert art["disc"] == "https://f/disc.png"
    # endpoint + auth
    url, params = client.calls[0]
    assert url.endswith("/v3/movies/27205")
    assert params == {"api_key": "k"}


@pytest.mark.asyncio
async def test_movie_falls_back_to_imdb_when_no_tmdb():
    client = _FakeClient(_Resp(payload=_MOVIE_PAYLOAD))
    await fanarttv.fetch_artwork(
        media_type="movie", client=client, api_key="k", imdb_id="tt1375666",
    )
    assert client.calls[0][0].endswith("/v3/movies/tt1375666")


# ── fetch_artwork: tv / anime ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_tv_uses_tvdb_endpoint_and_maps_tv_kinds():
    client = _FakeClient(_Resp(payload=_TV_PAYLOAD))
    art = await fanarttv.fetch_artwork(
        media_type="tv", client=client, api_key="k", tvdb_id="424536", languages=["en"],
    )
    assert client.calls[0][0].endswith("/v3/tv/424536")
    assert art["clearlogo"] == "https://f/hdtvlogo.png"
    assert art["fanart"] == "https://f/showbg.jpg"
    assert art["characterart"] == "https://f/char.png"
    assert "disc" not in art   # disc is movies-only


@pytest.mark.asyncio
async def test_anime_uses_tv_endpoint():
    # anime resolves to a TVDB id upstream (Fribb cross-ref) and uses /tv.
    client = _FakeClient(_Resp(payload=_TV_PAYLOAD))
    await fanarttv.fetch_artwork(
        media_type="anime", client=client, api_key="k", tvdb_id="424536",
    )
    assert client.calls[0][0].endswith("/v3/tv/424536")


# ── gating + filtering ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_no_api_key_is_noop():
    client = _FakeClient(_Resp(payload=_MOVIE_PAYLOAD))
    assert await fanarttv.fetch_artwork(media_type="movie", client=client, api_key="", tmdb_id="1") == {}
    assert client.calls == []


@pytest.mark.asyncio
async def test_no_usable_id_is_noop():
    client = _FakeClient(_Resp(payload=_MOVIE_PAYLOAD))
    assert await fanarttv.fetch_artwork(media_type="movie", client=client, api_key="k") == {}
    assert await fanarttv.fetch_artwork(media_type="tv", client=client, api_key="k") == {}
    assert client.calls == []


@pytest.mark.asyncio
async def test_wanted_filter_limits_kinds():
    client = _FakeClient(_Resp(payload=_MOVIE_PAYLOAD))
    art = await fanarttv.fetch_artwork(
        media_type="movie", client=client, api_key="k", tmdb_id="27205",
        wanted={"poster", "clearlogo"},
    )
    assert set(art) == {"poster", "clearlogo"}


@pytest.mark.asyncio
async def test_non_200_returns_empty():
    client = _FakeClient(_Resp(status=404))
    assert await fanarttv.fetch_artwork(media_type="movie", client=client, api_key="k", tmdb_id="1") == {}


@pytest.mark.asyncio
async def test_client_key_appended_when_present():
    client = _FakeClient(_Resp(payload=_MOVIE_PAYLOAD))
    await fanarttv.fetch_artwork(
        media_type="movie", client=client, api_key="k", tmdb_id="1", client_key="ck",
    )
    assert client.calls[0][1] == {"api_key": "k", "client_key": "ck"}


# ── test_key (Settings "Test connection") ─────────────────────────────────────
@pytest.mark.asyncio
async def test_test_key_distinguishes_outcomes():
    # No key → fail without a request; 200 → ok; 401/403 → "rejected"; other → fail.
    # Unlike fetch_artwork (which returns {} for both bad-key and no-art), this
    # must tell a bad key apart from a reachable response.
    assert await fanarttv.test_key("", _FakeClient(_Resp(200))) == (False, "No fanart.tv API key configured.")
    assert await fanarttv.test_key("k", _FakeClient(_Resp(200))) == (True, None)
    ok, detail = await fanarttv.test_key("k", _FakeClient(_Resp(401)))
    assert ok is False and "rejected" in detail.lower()
    ok2, detail2 = await fanarttv.test_key("k", _FakeClient(_Resp(500)))
    assert ok2 is False and "500" in detail2
