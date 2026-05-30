"""KI-1 + KI-2: TVDB `_get_extended_raw` envelope validation + shared cache.

These tests lock in the Pattern D invariant — malformed-but-non-raising
responses (`{"data": null}`, missing `data` key, `data` is a list) must
NOT poison the cache. Pre-KI-2 behavior was to cache the resulting empty
dict forever, silently breaking every downstream consumer of
`get_series_extended` (aliases / originalLanguage / genres / seasons[])
until process restart.

The test runs without real HTTP by monkeypatching `_get` to return
canned responses; envelope validation is pure CPU and doesn't need a
real httpx round-trip.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from kira.providers.base import ProviderAuth
from kira.providers.tvdb import TVDBProvider


def _make_provider() -> TVDBProvider:
    """Construct a TVDBProvider with no-op auth + a real (unused) client.

    `_get_extended_raw` is monkey-patched through `_get` so the underlying
    HTTP path is never exercised. The auth object exists to satisfy the
    constructor's contract.
    """
    return TVDBProvider(
        base_url="https://api.thetvdb.com/v4",
        auth=ProviderAuth(credentials={"apikey": "test-key"}),
        client=httpx.AsyncClient(),
    )


def _clear_caches() -> None:
    """Clear both TVDB caches so each test starts from a known state.

    Tests share the ClassVar caches; without isolation a previous test's
    write would mask a later test's miss.
    """
    TVDBProvider._extended_raw_cache.clear()
    TVDBProvider._extended_cache.clear()


@pytest.mark.asyncio
async def test_envelope_null_data_returns_none_and_skips_cache() -> None:
    """`{"data": null}` is the canonical KI-2 trigger.

    Pre-KI-2: this got coerced through `payload = data.get("data", {}) or {}`
    to an empty `{}`, then cached forever.
    Post-KI-2: returns None, no cache write.
    """
    _clear_caches()
    p = _make_provider()
    calls: list[str] = []

    async def fake_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append(path)
        return {"data": None}

    p._get = fake_get  # type: ignore[method-assign]

    out = await p._get_extended_raw("123")
    assert out is None
    assert "123" not in TVDBProvider._extended_raw_cache
    # Next call retries (no cache shortcut). HTTP fires again.
    out2 = await p._get_extended_raw("123")
    assert out2 is None
    assert len(calls) == 2  # one per call, no cache hit


@pytest.mark.asyncio
async def test_envelope_missing_data_key_returns_none_and_skips_cache() -> None:
    """Some TVDB error paths return a response with no `data` key at all."""
    _clear_caches()
    p = _make_provider()

    async def fake_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"status": "error", "message": "Not Found"}

    p._get = fake_get  # type: ignore[method-assign]

    out = await p._get_extended_raw("404")
    assert out is None
    assert "404" not in TVDBProvider._extended_raw_cache


@pytest.mark.asyncio
async def test_envelope_data_is_list_returns_none_and_skips_cache() -> None:
    """Defensive: TVDB's other endpoints sometimes return `data` as a list,
    but /series/{id}/extended must be a dict. If TVDB ever drifts on this,
    treat it as transient — caching a structurally-wrong entry would
    break every downstream consumer that assumes dict access."""
    _clear_caches()
    p = _make_provider()

    async def fake_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"data": ["unexpected", "list"]}

    p._get = fake_get  # type: ignore[method-assign]

    out = await p._get_extended_raw("999")
    assert out is None
    assert "999" not in TVDBProvider._extended_raw_cache


@pytest.mark.asyncio
async def test_valid_envelope_caches_and_subsequent_calls_skip_http() -> None:
    """Happy path: valid response writes to cache; second call hits cache."""
    _clear_caches()
    p = _make_provider()
    calls: list[str] = []

    async def fake_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append(path)
        return {"data": {"id": 42, "name": "Test Series", "seasons": [{"number": 1}]}}

    p._get = fake_get  # type: ignore[method-assign]

    out1 = await p._get_extended_raw("42")
    assert out1 is not None
    assert out1["name"] == "Test Series"
    assert "42" in TVDBProvider._extended_raw_cache

    # Second call hits cache — HTTP not invoked again.
    out2 = await p._get_extended_raw("42")
    assert out2 == out1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_transient_http_exception_returns_none_no_cache() -> None:
    """An exception from `_get` is a transient failure. DO NOT cache —
    the next call must retry. This is the most important branch for
    KI-2; a misclassified transient that gets cached is exactly what
    poisons every downstream consumer."""
    _clear_caches()
    p = _make_provider()

    async def fake_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        raise httpx.ConnectError("simulated network drop")

    p._get = fake_get  # type: ignore[method-assign]

    out = await p._get_extended_raw("transient")
    assert out is None
    assert "transient" not in TVDBProvider._extended_raw_cache


@pytest.mark.asyncio
async def test_get_series_extended_propagates_transient_via_empty_dict() -> None:
    """`get_series_extended` returns {} on transient failure (existing
    contract — many callers expect dict, not None). Critically it must
    NOT cache the empty dict in the transformed cache."""
    _clear_caches()
    p = _make_provider()

    async def fake_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"data": None}  # KI-2 trigger

    p._get = fake_get  # type: ignore[method-assign]

    out = await p.get_series_extended("trans-ext")
    assert out == {}
    # Critically: the empty dict was NOT cached. Next call retries.
    assert "trans-ext" not in TVDBProvider._extended_cache
    assert "trans-ext" not in TVDBProvider._extended_raw_cache
