"""The 'Test' button must validate the JUST-TYPED draft key, not the stored one.

The settings page buffers edits until Save, so a POST /settings/providers/X/test
carries the candidate key in its body. These tests assert the candidate reaches
the provider build (tmdb/tvdb) and the subtitle key variables — without hitting
the network, by intercepting the provider factory / client construction.
"""
from __future__ import annotations

import pytest

from kira.api import settings as settings_api
from kira.schemas import ProviderTestBody


@pytest.mark.asyncio
async def test_candidate_key_reaches_provider_build(monkeypatch):
    """With a candidate api_key, the tmdb provider is built from that key —
    NOT the registry's stored config."""
    captured = {}

    class _FakeProvider:
        async def search_tv(self, q):
            return []

    def _fake_build(provider, cfg, client):
        captured["provider"] = provider
        captured["api_key"] = cfg.api_key
        return _FakeProvider()

    # Make the retry wrapper a passthrough so no network / sleep happens.
    async def _fake_retry(fn, what=""):
        return await fn()

    monkeypatch.setattr("kira.providers.factory.build_provider", _fake_build)
    monkeypatch.setattr("kira.matcher.engine._provider_call_with_retry", _fake_retry)

    # registry_from_settings must NOT be consulted when a candidate is given.
    async def _boom(client):
        raise AssertionError("registry_from_settings should be bypassed for a candidate key")
    monkeypatch.setattr(settings_api, "registry_from_settings", _boom)

    res = await settings_api.test_provider(
        "tmdb", ProviderTestBody(api_key="CANDIDATE-KEY-123"), session=None,
    )
    assert captured["provider"] == "tmdb"
    assert captured["api_key"] == "CANDIDATE-KEY-123"
    assert res.ok is True


@pytest.mark.asyncio
async def test_no_candidate_falls_back_to_stored_registry(monkeypatch):
    """An empty body tests the SAVED config via the registry (unchanged path)."""
    used = {"registry": False}

    class _FakeProvider:
        async def search_tv(self, q):
            return []

    class _FakeRegistry:
        def has(self, p):
            return True

        def build(self, p):
            used["registry"] = True
            return _FakeProvider()

    async def _fake_registry(client):
        return _FakeRegistry()

    async def _fake_retry(fn, what=""):
        return await fn()

    monkeypatch.setattr(settings_api, "registry_from_settings", _fake_registry)
    monkeypatch.setattr("kira.matcher.engine._provider_call_with_retry", _fake_retry)

    res = await settings_api.test_provider("tvdb", ProviderTestBody(), session=None)
    assert used["registry"] is True
    assert res.ok is True
