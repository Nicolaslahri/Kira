"""Sonarr "Test connection" endpoint robustness.

Regression for the "works → refresh → Failed to fetch" bug: after a refresh the
Settings API-key field shows a MASKED placeholder ('•••• •••• •••• abcd'); the
UI used to send that back as the key, and its non-ASCII bullets can't be encoded
into the X-Api-Key HTTP header → httpx raised UnicodeEncodeError BEFORE any
request. That error was uncaught → FastAPI 500 → and an uncaught error served
cross-origin carries no CORS headers, so the browser reported a misleading
"Failed to fetch" instead of the real failure.

The endpoint must NEVER raise: any failure (bad/non-encodable key, unreachable
host) returns a clean `ok=false` with a message. (The frontend separately stops
sending the mask at all — see runTest.)
"""
from __future__ import annotations

import pytest

from kira.api.integrations import SonarrTestRequest, sonarr_test


@pytest.mark.asyncio
async def test_masked_placeholder_key_returns_clean_fail_not_500():
    # The exact masked string strSetting() renders after a refresh. Non-ASCII
    # bullets → previously an uncaught UnicodeEncodeError → 500 → "Failed to
    # fetch". Inline path (url+key both present) doesn't touch the session.
    payload = SonarrTestRequest(url="http://127.0.0.1:8989", api_key="•••• •••• •••• abcd")
    resp = await sonarr_test(payload, session=None)  # must not raise
    assert resp.ok is False
    assert resp.detail  # carries a real message instead of crashing


@pytest.mark.asyncio
async def test_unreachable_host_returns_clean_fail():
    # A plausible ASCII key against a dead port: connection refused → SonarrError
    # → clean ok=false (the normal "can't reach Sonarr" outcome).
    payload = SonarrTestRequest(url="http://127.0.0.1:9", api_key="looksvalid123")
    resp = await sonarr_test(payload, session=None)
    assert resp.ok is False
    assert resp.detail
