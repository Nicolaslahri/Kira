"""TVDB get_season_poster must never return a BANNER for a portrait card.

The Loki S1 black-card bug: the season's inline `image` pointed at a wide
landscape banner (`…/banners/…`), which was returned verbatim and rendered as
a black strip in the portrait poster slot. Season 2's inline image was a real
poster (`…/posters/…`), so it looked fine. The fix prefers poster-shaped art
(type-7 Season Poster) and falls back to the series poster rather than a banner.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from kira.providers.base import ProviderAuth
from kira.providers.tvdb import TVDBProvider

BANNER = "https://artworks.thetvdb.com/banners/v4/season/1869163/banners/aaa.jpg"
SEASON_POSTER = "https://artworks.thetvdb.com/banners/v4/season/1869163/posters/bbb.jpg"
SERIES_POSTER = "https://artworks.thetvdb.com/banners/v4/series/362472/posters/ccc.jpg"


def _provider() -> TVDBProvider:
    return TVDBProvider(
        base_url="https://api.thetvdb.com/v4",
        auth=ProviderAuth(credentials={"apikey": "k"}),
        client=httpx.AsyncClient(),
    )


def _season(num: int, sid: int, image: str) -> dict[str, Any]:
    return {"number": num, "id": sid, "image": image, "type": {"id": 1, "name": "Aired Order"}}


@pytest.mark.asyncio
async def test_skips_banner_and_returns_type7_poster() -> None:
    p = _provider()

    async def fake_ext(series_id: str):
        return {"image": SERIES_POSTER, "seasons": [_season(1, 1869163, BANNER)]}

    async def fake_get(path: str, params: Any = None):
        return {"data": {"artwork": [{"type": 7, "image": SEASON_POSTER}]}}

    p._get_extended_raw = fake_ext       # type: ignore[method-assign]
    p._get = fake_get                    # type: ignore[method-assign]

    url = await p.get_season_poster("362472", 1)
    assert url == SEASON_POSTER          # the real Season Poster, NOT the banner


@pytest.mark.asyncio
async def test_falls_back_to_series_poster_when_only_banner() -> None:
    p = _provider()

    async def fake_ext(series_id: str):
        return {"image": SERIES_POSTER, "seasons": [_season(1, 1869163, BANNER)]}

    async def fake_get(path: str, params: Any = None):
        # Season extended has only a non-poster (banner-type) artwork.
        return {"data": {"artwork": [{"type": 2, "image": BANNER}]}}

    p._get_extended_raw = fake_ext       # type: ignore[method-assign]
    p._get = fake_get                    # type: ignore[method-assign]

    url = await p.get_season_poster("362472", 1)
    assert url == SERIES_POSTER          # banner avoided; series poster (portrait) used


@pytest.mark.asyncio
async def test_poster_inline_used_directly_without_extra_fetch() -> None:
    p = _provider()
    calls: list[str] = []

    async def fake_ext(series_id: str):
        return {"image": SERIES_POSTER, "seasons": [_season(2, 2064090, SEASON_POSTER)]}

    async def fake_get(path: str, params: Any = None):
        calls.append(path)
        return {"data": {}}

    p._get_extended_raw = fake_ext       # type: ignore[method-assign]
    p._get = fake_get                    # type: ignore[method-assign]

    url = await p.get_season_poster("362472", 2)
    assert url == SEASON_POSTER
    assert calls == []                   # poster inline → no season-extended round-trip
