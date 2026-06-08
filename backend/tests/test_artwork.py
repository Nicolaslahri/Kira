"""Pass 7 #13 — artwork download."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from kira.api import rename as rn
from kira.providers.tmdb import _backdrop_url, _poster_url


# ── URL helpers ──────────────────────────────────────────────────────────

def test_backdrop_url_is_original_size() -> None:
    assert _backdrop_url("/abc.jpg") == "https://image.tmdb.org/t/p/original/abc.jpg"
    assert _backdrop_url(None) is None


def test_poster_vs_backdrop_size() -> None:
    assert "w500" in _poster_url("/p.jpg")
    assert "original" in _backdrop_url("/b.jpg")


# ── download helper ──────────────────────────────────────────────────────

@dataclass
class _Sel:
    poster_url: str | None = None


@dataclass
class _Parsed:
    media_type: str = "movie"


class _Resp:
    def __init__(self, status=200, content=b"\xff\xd8\xffjpeg"):
        self.status_code = status
        self.content = content
        self.headers = {"content-type": "image/jpeg"}


class _StreamResp:
    """Minimal async-context-manager response for client.stream(...)."""
    def __init__(self, resp: "_Resp"):
        self.status_code = resp.status_code
        self.headers = resp.headers
        self._content = resp.content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self):
        yield self._content


class _FakeClient:
    calls: list = []

    def __init__(self, status=200, content=b"\xff\xd8\xffjpeg", raise_exc=None):
        self._status, self._content, self._raise = status, content, raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        _FakeClient.calls.append(url)
        if self._raise:
            raise self._raise
        return _Resp(self._status, self._content)

    def stream(self, method, url, **kw):
        _FakeClient.calls.append(url)
        if self._raise:
            raise self._raise
        return _StreamResp(_Resp(self._status, self._content))


def _patch(monkeypatch, **kw):
    # _download_artwork_files does `import httpx` inside the function, so it
    # resolves the global module — patching httpx.AsyncClient is enough.
    _FakeClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(**kw))


async def test_downloads_poster_and_fanart(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch)
    target = tmp_path / "Inception (2010).mkv"
    sel = _Sel(poster_url="http://img/p.jpg")
    meta = {"fanart_url": "http://img/b.jpg"}
    await rn._download_artwork_files(target, _Parsed(), sel, meta)
    assert (tmp_path / "Inception (2010)-poster.jpg").exists()
    assert (tmp_path / "Inception (2010)-fanart.jpg").exists()
    assert len(_FakeClient.calls) == 2


async def test_no_urls_is_noop(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch)
    target = tmp_path / "m.mkv"
    await rn._download_artwork_files(target, _Parsed(), _Sel(poster_url=None), {})
    assert _FakeClient.calls == []


async def test_write_if_absent_skips_existing(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch)
    target = tmp_path / "m.mkv"
    existing = tmp_path / "m-poster.jpg"
    existing.write_bytes(b"old")
    await rn._download_artwork_files(target, _Parsed(), _Sel(poster_url="http://img/p.jpg"), {})
    # Existing poster left untouched, no fetch made.
    assert existing.read_bytes() == b"old"
    assert _FakeClient.calls == []


async def test_http_error_writes_nothing(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, raise_exc=httpx.ConnectError("down"))
    target = tmp_path / "m.mkv"
    # Must not raise.
    await rn._download_artwork_files(target, _Parsed(), _Sel(poster_url="http://img/p.jpg"), {})
    assert not (tmp_path / "m-poster.jpg").exists()


async def test_http_4xx_skips_write(monkeypatch, tmp_path) -> None:
    _patch(monkeypatch, status=404)
    target = tmp_path / "m.mkv"
    await rn._download_artwork_files(target, _Parsed(), _Sel(poster_url="http://img/p.jpg"), {})
    assert not (tmp_path / "m-poster.jpg").exists()


async def test_html_error_page_not_saved_as_jpg(monkeypatch, tmp_path) -> None:
    """A 200-OK HTML notice / JSON error must not be written as a .jpg (R6)."""
    _patch(monkeypatch, content=b"<!DOCTYPE html><html><body>rate limited</body></html>")
    target = tmp_path / "Inception (2010).mkv"
    await rn._download_artwork_files(target, _Parsed(), _Sel(poster_url="http://img/p.jpg"), {})
    assert not (tmp_path / "Inception (2010)-poster.jpg").exists()
