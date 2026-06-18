"""Reverse-proxy URL base must survive on EVERY Sonarr request (regression).

The bug: `_client` set `base_url=cfg.base_url.rstrip("/")` (no trailing slash)
and every call used a LEADING-slash path (`c.get("/api/v3/system/status")`).
httpx treats a leading-slash request path as ABSOLUTE and discards the
base_url's path component, so a reverse-proxy URL base like `/nickflix` was
silently dropped — Sonarr (configured with UrlBase=nickflix) then 302-redirected
the un-prefixed request and the connection test failed with
"HTTP 302 on /system/status".

The fix: base_url carries exactly one trailing slash and every request path is
RELATIVE (`c.get("api/v3/...")`), so httpx joins the path onto the full base
path and `/nickflix` is preserved.

These tests exercise the REAL `_client` + the REAL request-call strings: we
swap in an httpx MockTransport (via a thin AsyncClient subclass) that captures
the fully-resolved outgoing request URL, then assert its path. No production
seam is added — the transport is injected only under test.
"""
from __future__ import annotations

import httpx
import pytest

from kira.integrations import sonarr
from kira.integrations.sonarr import SonarrConfig


def _install_capturing_client(monkeypatch, responder):
    """Patch `sonarr.httpx.AsyncClient` so every client `_client()` builds is
    backed by an httpx MockTransport running `responder(request)`.

    Returns a list that the tests read the captured request URLs out of. The
    real `_client` still runs — its base_url normalization and the real
    request-path strings are what produce the URLs we capture, so this is a
    faithful end-to-end check of the join (not a re-implementation of it).
    """
    captured: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url)
        return responder(request)

    real_async_client = httpx.AsyncClient

    class CapturingAsyncClient(real_async_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            # Force our MockTransport in; keep base_url/headers/timeout that
            # _client passed so the URL join under test is the real one.
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(sonarr.httpx, "AsyncClient", CapturingAsyncClient)
    return captured


def _json_responder(payload):
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return responder


# ─────────────────────────────────────────────────────────────────────
# test_connection — the endpoint that surfaced the 302 bug
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connection_preserves_url_base(monkeypatch):
    captured = _install_capturing_client(
        monkeypatch, _json_responder({"version": "4.0.0"})
    )
    cfg = SonarrConfig(base_url="http://h:8989/nickflix", api_key="k")

    result = await sonarr.test_connection(cfg)

    assert result == {"version": "4.0.0"}
    assert len(captured) == 1
    # The url_base MUST be preserved — this is the whole bug.
    assert captured[0].path == "/nickflix/api/v3/system/status"
    # Full URL sanity (host + port intact too).
    assert str(captured[0]) == "http://h:8989/nickflix/api/v3/system/status"


@pytest.mark.asyncio
async def test_connection_no_url_base(monkeypatch):
    captured = _install_capturing_client(
        monkeypatch, _json_responder({"version": "4.0.0"})
    )
    # No url_base, no trailing slash on the host URL — the common case.
    cfg = SonarrConfig(base_url="http://h:8989", api_key="k")

    await sonarr.test_connection(cfg)

    assert len(captured) == 1
    assert captured[0].path == "/api/v3/system/status"
    assert str(captured[0]) == "http://h:8989/api/v3/system/status"


@pytest.mark.asyncio
async def test_connection_url_base_with_trailing_slash(monkeypatch):
    # _client does rstrip("/") + "/", so a base_url that already ends in a
    # slash must NOT produce a doubled slash before api/v3.
    captured = _install_capturing_client(
        monkeypatch, _json_responder({"version": "4.0.0"})
    )
    cfg = SonarrConfig(base_url="http://h:8989/nickflix/", api_key="k")

    await sonarr.test_connection(cfg)

    assert captured[0].path == "/nickflix/api/v3/system/status"


# ─────────────────────────────────────────────────────────────────────
# A second, different call site — proves the fix is not local to one path
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_quality_profiles_preserves_url_base(monkeypatch):
    captured = _install_capturing_client(
        monkeypatch, _json_responder([{"id": 1, "name": "HD-1080p"}])
    )
    cfg = SonarrConfig(base_url="http://h:8989/nickflix", api_key="k")

    profiles = await sonarr.list_quality_profiles(cfg)

    assert profiles == [{"id": 1, "name": "HD-1080p"}]
    assert captured[0].path == "/nickflix/api/v3/qualityprofile"


@pytest.mark.asyncio
async def test_quality_profiles_no_url_base(monkeypatch):
    captured = _install_capturing_client(
        monkeypatch, _json_responder([{"id": 1, "name": "HD-1080p"}])
    )
    cfg = SonarrConfig(base_url="http://h:8989", api_key="k")

    await sonarr.list_quality_profiles(cfg)

    assert captured[0].path == "/api/v3/qualityprofile"


# ─────────────────────────────────────────────────────────────────────
# A nested URL base (e.g. behind two proxy segments) must survive intact.
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connection_preserves_multi_segment_url_base(monkeypatch):
    captured = _install_capturing_client(
        monkeypatch, _json_responder({"version": "4.0.0"})
    )
    cfg = SonarrConfig(base_url="http://h:8989/apps/sonarr", api_key="k")

    await sonarr.test_connection(cfg)

    assert captured[0].path == "/apps/sonarr/api/v3/system/status"
