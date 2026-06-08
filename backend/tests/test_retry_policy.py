"""Provider retry policy — connection blips retry FAST, rate-limits back off.

Regression guard for the "matching 20x slow" bug: a flaky-but-up provider
(TMDB dropping ~20% of connects) was hitting the 1s→2s→4s rate-limit backoff on
every dropped connection, turning each blip into a ~7s stall. Connection errors
must use the fast schedule instead.
"""

from __future__ import annotations

import httpx
import pytest

from kira.matcher import engine as eng
from kira.providers.base import ProviderPermanentError, ProviderTransientError


def _patch_sleep(monkeypatch) -> list[float]:
    """Replace asyncio.sleep with a no-op that records the requested delays.
    Must NOT call the real asyncio.sleep — we've patched it, so that recurses."""
    sleeps: list[float] = []

    async def _fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(eng.asyncio, "sleep", _fake_sleep)
    return sleeps


async def test_connect_error_uses_fast_backoff(monkeypatch) -> None:
    sleeps = _patch_sleep(monkeypatch)
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise httpx.ConnectError("")   # drop the first two connects
        return "ok"

    out = await eng._provider_call_with_retry(factory, what="tmdb.search")
    assert out == "ok"
    # Two retries happened, both on the FAST (connection) schedule (≤ ~1.1s),
    # never the 1/2/4s rate-limit schedule.
    assert len(sleeps) == 2
    assert all(s < eng._RETRY_BACKOFFS[0] for s in sleeps)  # < 1.0s each
    assert sleeps[0] < 0.5  # first retry near-immediate


async def test_rate_limit_uses_slow_backoff(monkeypatch) -> None:
    sleeps = _patch_sleep(monkeypatch)
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderTransientError("429 rate limited")
        return "ok"

    out = await eng._provider_call_with_retry(factory, what="tmdb.search")
    assert out == "ok"
    # The single retry used the SLOW (rate-limit) schedule.
    assert len(sleeps) == 1
    assert sleeps[0] >= eng._RETRY_BACKOFFS[0]  # >= 1.0s


async def test_permanent_error_not_retried(monkeypatch) -> None:
    _patch_sleep(monkeypatch)
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        raise ProviderPermanentError("401 bad key")

    with pytest.raises(ProviderPermanentError):
        await eng._provider_call_with_retry(factory, what="tmdb.search")
    assert calls["n"] == 1  # no retries


async def test_connect_error_exhausted_raises_transient(monkeypatch) -> None:
    _patch_sleep(monkeypatch)

    async def factory():
        raise httpx.ConnectError("")

    with pytest.raises(ProviderTransientError):
        await eng._provider_call_with_retry(factory, what="tmdb.search")


async def test_fast_path_worst_case_is_bounded(monkeypatch) -> None:
    # Even if every attempt fails, the connection schedule's total sleep is a
    # fraction of the rate-limit schedule's ~7s.
    sleeps = _patch_sleep(monkeypatch)

    async def factory():
        raise httpx.ConnectTimeout("")

    with pytest.raises(ProviderTransientError):
        await eng._provider_call_with_retry(factory, what="tmdb.search")
    assert sum(sleeps) < sum(eng._RETRY_BACKOFFS)  # well under the 7s rate-limit total
