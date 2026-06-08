"""Pass 6 #9 — Plex / Jellyfin library refresh."""

from __future__ import annotations

import httpx
import pytest

from kira.integrations import media_server as ms


class _Resp:
    def __init__(self, status: int) -> None:
        self.status_code = status


class _FakeClient:
    """Stand-in for httpx.AsyncClient — records the last call, returns _Resp."""
    last: dict = {}

    def __init__(self, status: int = 200, raise_exc: Exception | None = None) -> None:
        self._status = status
        self._raise = raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        _FakeClient.last = {"method": "GET", "url": url, "kw": kw}
        if self._raise:
            raise self._raise
        return _Resp(self._status)

    async def post(self, url, **kw):
        _FakeClient.last = {"method": "POST", "url": url, "kw": kw}
        if self._raise:
            raise self._raise
        return _Resp(self._status)


def _patch_client(monkeypatch, **kw):
    monkeypatch.setattr(ms.httpx, "AsyncClient", lambda *a, **k: _FakeClient(**kw))


class _FakeRow:
    def __init__(self, value):
        self.value = value


class _FakeSession:
    def __init__(self, data: dict):
        self._data = data

    async def get(self, _model, key):
        v = self._data.get(key)
        return _FakeRow(v) if v is not None else None


def test_unwrap_shapes() -> None:
    assert ms._unwrap("http://x") == "http://x"
    assert ms._unwrap({"value": "http://y"}) == "http://y"
    assert ms._unwrap("  ") is None
    assert ms._unwrap(None) is None
    assert ms._unwrap(123) is None


async def test_refresh_plex_success(monkeypatch) -> None:
    _patch_client(monkeypatch, status=200)
    assert await ms.refresh_plex("http://plex:32400/", "tok") is True
    assert _FakeClient.last["url"].endswith("/library/sections/all/refresh")
    assert _FakeClient.last["kw"]["headers"]["X-Plex-Token"] == "tok"


async def test_refresh_plex_http_error(monkeypatch) -> None:
    _patch_client(monkeypatch, status=401)
    assert await ms.refresh_plex("http://plex:32400", "bad") is False


async def test_refresh_plex_unreachable(monkeypatch) -> None:
    _patch_client(monkeypatch, raise_exc=httpx.ConnectError("no route"))
    assert await ms.refresh_plex("http://plex:32400", "tok") is False


async def test_refresh_jellyfin_success(monkeypatch) -> None:
    _patch_client(monkeypatch, status=204)
    assert await ms.refresh_jellyfin("http://jf:8096", "key") is True
    assert _FakeClient.last["method"] == "POST"
    assert _FakeClient.last["url"].endswith("/Library/Refresh")
    assert _FakeClient.last["kw"]["headers"]["X-Emby-Token"] == "key"


# ── SSRF guard (audit S5, media_server leg) ──────────────────────────────────

async def test_refresh_plex_blocks_metadata_url(monkeypatch) -> None:
    _patch_client(monkeypatch, status=200)
    _FakeClient.last = {}
    assert await ms.refresh_plex("http://169.254.169.254/latest/meta-data/", "tok") is False
    assert _FakeClient.last == {}                       # blocked BEFORE any request


async def test_refresh_jellyfin_blocks_metadata_ipv6(monkeypatch) -> None:
    _patch_client(monkeypatch, status=200)
    _FakeClient.last = {}
    assert await ms.refresh_jellyfin("http://[fd00:ec2::254]/", "key") is False
    assert _FakeClient.last == {}


async def test_refresh_plex_allows_lan_ip(monkeypatch) -> None:
    # A private LAN address passes the guard → the request proceeds.
    _patch_client(monkeypatch, status=200)
    _FakeClient.last = {}
    assert await ms.refresh_plex("http://192.168.1.50:32400", "tok") is True
    assert _FakeClient.last.get("method") == "GET"
    assert _FakeClient.last["url"].endswith("/library/sections/all/refresh")
    assert _FakeClient.last["kw"]["headers"]["X-Plex-Token"] == "tok"


async def test_refresh_all_fires_both(monkeypatch) -> None:
    sess = _FakeSession({
        "integrations.plex.url": "http://plex:32400",
        "integrations.plex.token": "ptok",
        "integrations.jellyfin.url": "http://jf:8096",
        "integrations.jellyfin.api_key": "jkey",
    })

    async def ok_plex(url, token):
        return True

    async def ok_jf(url, key):
        return True

    monkeypatch.setattr(ms, "refresh_plex", ok_plex)
    monkeypatch.setattr(ms, "refresh_jellyfin", ok_jf)
    assert await ms.refresh_all(sess) == ["Plex", "Jellyfin"]


async def test_refresh_all_none_configured(monkeypatch) -> None:
    sess = _FakeSession({})
    assert await ms.refresh_all(sess) == []


async def test_refresh_all_skips_partial_config(monkeypatch) -> None:
    # Plex URL but no token → skipped; Jellyfin fully set → fires.
    sess = _FakeSession({
        "integrations.plex.url": "http://plex:32400",
        "integrations.jellyfin.url": "http://jf:8096",
        "integrations.jellyfin.api_key": "jkey",
    })

    async def boom_plex(url, token):  # should never be called
        raise AssertionError("plex should be skipped without a token")

    async def ok_jf(url, key):
        return True

    monkeypatch.setattr(ms, "refresh_plex", boom_plex)
    monkeypatch.setattr(ms, "refresh_jellyfin", ok_jf)
    assert await ms.refresh_all(sess) == ["Jellyfin"]
