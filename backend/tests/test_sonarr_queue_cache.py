"""Sonarr /queue cache must be keyed by config, not a constant (audit).

The old constant `"queue"` key meant a queue fetched for one Sonarr instance
would be served to a different one (after a settings change, or a second
instance) for the cache TTL. Now it's keyed by (base_url, api_key).
"""
from __future__ import annotations

import kira.api.integrations as integ
from kira.integrations.sonarr import SonarrConfig


async def test_queue_cache_keyed_by_config(monkeypatch):
    calls: list[str] = []

    async def fake_get_queue(cfg):
        calls.append(cfg.base_url)
        return [cfg.base_url]  # config-specific sentinel

    monkeypatch.setattr(integ, "get_queue", fake_get_queue)
    integ._QUEUE_CACHE.clear()

    a = SonarrConfig(base_url="http://sonarr-a:8989", api_key="ka")
    b = SonarrConfig(base_url="http://sonarr-b:8989", api_key="kb")

    ra = await integ._get_cached_queue(a)
    rb = await integ._get_cached_queue(b)  # within TTL — must NOT serve A's cache

    assert ra == ["http://sonarr-a:8989"]
    assert rb == ["http://sonarr-b:8989"]              # the bug would make this == A's
    assert calls == ["http://sonarr-a:8989", "http://sonarr-b:8989"]  # both fetched


async def test_queue_cache_hits_within_ttl_for_same_config(monkeypatch):
    calls: list[int] = []

    async def fake_get_queue(cfg):
        calls.append(1)
        return ["x"]

    monkeypatch.setattr(integ, "get_queue", fake_get_queue)
    integ._QUEUE_CACHE.clear()

    a = SonarrConfig(base_url="http://s:8989", api_key="k")
    await integ._get_cached_queue(a)
    await integ._get_cached_queue(a)  # same cfg within 0.5s → served from cache

    assert len(calls) == 1  # only ONE network fetch
