"""Sonarr/Radarr queue must fetch ALL pages, not just the first 200 records.

Regression: `get_queue` requested pageSize=200 with no page loop, so a queue
larger than 200 (season-pack backlogs, seedbox users) silently truncated —
library pills / stuck-import detection missing everything past record 200.
"""
from __future__ import annotations

import json

import httpx
import pytest

from kira.integrations import sonarr, radarr
from kira.integrations.sonarr import SonarrConfig
from kira.integrations.radarr import RadarrConfig


def _install(monkeypatch, module, pages):
    """Serve `/api/v3/queue` from `pages` (a list of records-lists), one per
    ?page= param, with a correct totalRecords envelope."""
    total = sum(len(p) for p in pages)
    calls = {"pages": []}

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        calls["pages"].append(page)
        recs = pages[page - 1] if 1 <= page <= len(pages) else []
        return httpx.Response(200, json={"page": page, "pageSize": 200,
                                         "totalRecords": total, "records": recs})

    real = httpx.AsyncClient

    class Cap(real):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **k)

    monkeypatch.setattr(module.httpx, "AsyncClient", Cap)
    return calls


def _sonarr_rec(tvdb, ep):
    return {"series": {"tvdbId": tvdb}, "episode": {"seasonNumber": 1, "episodeNumber": ep},
            "status": "downloading", "size": 100, "sizeleft": 50}


async def test_sonarr_queue_fetches_all_pages(monkeypatch):
    # 250 records = two pages (200 + 50).
    p1 = [_sonarr_rec(100 + i, i) for i in range(200)]
    p2 = [_sonarr_rec(400 + i, i) for i in range(50)]
    calls = _install(monkeypatch, sonarr, [p1, p2])
    items = await sonarr.get_queue(SonarrConfig(base_url="http://s/", api_key="k"))
    assert len(items) == 250, len(items)
    assert calls["pages"] == [1, 2]


async def test_sonarr_queue_single_page_stops(monkeypatch):
    calls = _install(monkeypatch, sonarr, [[_sonarr_rec(100 + i, i) for i in range(10)]])
    items = await sonarr.get_queue(SonarrConfig(base_url="http://s/", api_key="k"))
    assert len(items) == 10
    assert calls["pages"] == [1]   # short page → no second request


async def test_radarr_queue_fetches_all_pages(monkeypatch):
    p1 = [{"movie": {"tmdbId": 100 + i}, "status": "downloading", "size": 100, "sizeleft": 50} for i in range(200)]
    p2 = [{"movie": {"tmdbId": 400 + i}, "status": "downloading", "size": 100, "sizeleft": 50} for i in range(30)]
    calls = _install(monkeypatch, radarr, [p1, p2])
    items = await radarr.get_queue(RadarrConfig(base_url="http://r/", api_key="k"))
    assert len(items) == 230
    assert calls["pages"] == [1, 2]
