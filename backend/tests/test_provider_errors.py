"""Scan.4 — the matcher records WHY a provider failed (so the scan worker can
surface it) instead of silently swallowing it into a no-match."""

from __future__ import annotations

import httpx

from kira.matcher.engine import MatchEngine, ProviderRegistry
from kira.parser import ParsedFile
from kira.providers.base import (
    ProviderConfig,
    ProviderMode,
    ProviderPermanentError,
    ProviderTransientError,
)


def _engine() -> tuple[MatchEngine, httpx.AsyncClient]:
    client = httpx.AsyncClient()
    reg = ProviderRegistry(
        configs={"tmdb": ProviderConfig(mode=ProviderMode.DIRECT, api_key="x")},
        client=client,
    )
    return MatchEngine(reg), client


def _movie() -> ParsedFile:
    return ParsedFile(original_filename="m.mkv", media_type="movie", title="Some Movie", year=2020)


async def test_permanent_error_recorded_as_auth(monkeypatch) -> None:
    eng, client = _engine()
    try:
        async def boom(key, parsed):
            raise ProviderPermanentError("401 invalid key")
        monkeypatch.setattr(eng, "_match_with", boom)
        res = await eng.match(_movie())
        assert res == []  # still no-match, but…
        assert "tmdb" in eng.provider_errors
        assert "authentication" in eng.provider_errors["tmdb"].lower()
    finally:
        await client.aclose()


async def test_transient_error_recorded_as_unreachable(monkeypatch) -> None:
    eng, client = _engine()
    try:
        async def boom(key, parsed):
            raise ProviderTransientError("timeout after retries")
        monkeypatch.setattr(eng, "_match_with", boom)
        await eng.match(_movie())
        assert "unreachable" in eng.provider_errors.get("tmdb", "").lower()
    finally:
        await client.aclose()


async def test_first_error_wins(monkeypatch) -> None:
    eng, client = _engine()
    try:
        async def boom(key, parsed):
            raise ProviderPermanentError("first")
        monkeypatch.setattr(eng, "_match_with", boom)
        await eng.match(_movie())
        await eng.match(_movie())  # second call shouldn't overwrite
        assert "first" in eng.provider_errors["tmdb"]
    finally:
        await client.aclose()


async def test_no_error_when_provider_unconfigured() -> None:
    # tvdb-only movie pref order; tmdb config absent → provider skipped, no error.
    client = httpx.AsyncClient()
    try:
        reg = ProviderRegistry(configs={}, client=client)  # nothing configured
        eng = MatchEngine(reg)
        res = await eng.match(_movie())
        assert res == []
        assert eng.provider_errors == {}  # skipped, not errored
    finally:
        await client.aclose()
