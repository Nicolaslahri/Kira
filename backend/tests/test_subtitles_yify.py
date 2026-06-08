"""YIFY Subtitles scraper + the subtitle aggregator.

YIFY is the one HTML-scraper source (verified live against yifysubtitles.ch):
`/movie-imdb/tt<id>` → slug carries the language → `/subtitle/<slug>.zip`. These
pin the pure parse (imdb normalize, slug match) and the download→unzip→sidecar
flow with a real in-memory zip + a fake client — no network. The aggregator
tests pin source ORDER + gating (yify is opt-in and movies-only).
"""

from __future__ import annotations

import io
import zipfile

import pytest

from kira.subtitles import aggregate, yifysubtitles as yify


# ── fakes ──────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, status=200, text="", content=b"", ctype="text/html"):
        self.status_code = status
        self.text = text
        self.content = content
        self.headers = {"content-type": ctype}


class _StreamResp:
    """Minimal async-context-manager response for client.stream(...)."""
    def __init__(self, resp: _Resp):
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
    """Routes a GET to the first response whose key is a substring of the URL."""
    def __init__(self, routes: dict[str, _Resp]):
        self.routes = routes
        self.calls: list[str] = []

    def _match(self, url) -> _Resp:
        self.calls.append(url)
        for key, resp in self.routes.items():
            if key in url:
                return resp
        return _Resp(status=404)

    async def get(self, url, **kw):
        return self._match(url)

    def stream(self, method, url, **kw):
        return _StreamResp(self._match(url))


def _zip_with_srt(name="Inception.srt", body="1\n00:00:01,000 --> 00:00:02,000\nHi\n") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, body)
    return buf.getvalue()


# ── pure helpers ────────────────────────────────────────────────────────────
def test_norm_imdb():
    assert yify._norm_imdb("tt1375666") == "tt1375666"
    assert yify._norm_imdb("1375666") == "tt1375666"
    assert yify._norm_imdb(1375666) == "tt1375666"
    assert yify._norm_imdb("abc") is None
    assert yify._norm_imdb(None) is None


def test_find_slug():
    html = ('x <a href="/subtitles/inception-2010-arabic-yify-392064">a</a> '
            '<a href="/subtitles/inception-2010-english-yify-392189">e</a>')
    assert yify.find_slug(html, "english") == "inception-2010-english-yify-392189"
    assert yify.find_slug(html, "arabic") == "inception-2010-arabic-yify-392064"
    assert yify.find_slug(html, "klingon") is None


# ── fetch flow ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_downloads_and_writes_sidecar(tmp_path):
    video = str(tmp_path / "Inception (2010).mkv")
    slug = "inception-2010-english-yify-392189"
    client = _FakeClient({
        "/movie-imdb/tt1375666": _Resp(text=f'<a href="/subtitles/{slug}">EN</a>'),
        f"/subtitle/{slug}.zip": _Resp(content=_zip_with_srt(), ctype="application/zip"),
    })
    saved = await yify.fetch(video, ["en"], imdb_id="tt1375666", client=client)
    assert saved == [str(tmp_path / "Inception (2010).en.srt")]
    assert (tmp_path / "Inception (2010).en.srt").read_text().startswith("1")


@pytest.mark.asyncio
async def test_fetch_skips_existing_sidecar(tmp_path):
    video = str(tmp_path / "Inception (2010).mkv")
    (tmp_path / "Inception (2010).en.srt").write_text("already here")
    client = _FakeClient({})  # must never be hit
    saved = await yify.fetch(video, ["en"], imdb_id="tt1375666", client=client)
    assert saved == [] and client.calls == []   # skipped before any network


@pytest.mark.asyncio
async def test_fetch_no_imdb_is_noop():
    client = _FakeClient({})
    assert await yify.fetch("x.mkv", ["en"], imdb_id=None, client=client) == []
    assert client.calls == []


@pytest.mark.asyncio
async def test_fetch_unknown_language_skipped(tmp_path):
    # "kli" isn't in the YIFY language map → no request made.
    client = _FakeClient({"/movie-imdb/tt1": _Resp(text="x")})
    saved = await yify.fetch(str(tmp_path / "m.mkv"), ["kli"], imdb_id="tt1", client=client)
    assert saved == []


# ── aggregator order + gating ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_aggregate_runs_sources_in_order(monkeypatch):
    order: list[str] = []

    async def _emb(path, langs):
        order.append("embedded")
        return ["a.en.srt"]

    async def _os(path, **kw):
        order.append("opensubtitles")
        return ["b.en.srt"]

    async def _yi(path, langs, *, imdb_id, client):
        order.append("yify")
        return ["c.en.srt"]

    monkeypatch.setattr(aggregate._embedded, "available", lambda: True)
    monkeypatch.setattr(aggregate._embedded, "extract", _emb)
    monkeypatch.setattr(aggregate, "fetch_and_save_subtitles", _os)
    monkeypatch.setattr(aggregate._yify, "fetch", _yi)

    saved = await aggregate.fetch_subtitles(
        "v.mkv", ["en"], client=object(),
        enabled={"embedded": True, "opensubtitles": True, "yifysubtitles": True},
        os_api_key="k", imdb_id="tt1",
    )
    assert order == ["embedded", "opensubtitles", "yify"]
    assert saved == ["a.en.srt", "b.en.srt", "c.en.srt"]


@pytest.mark.asyncio
async def test_aggregate_gating(monkeypatch):
    called: list[str] = []

    async def _emb(p, l):
        called.append("embedded")
        return []

    async def _os(p, **k):
        called.append("os")
        return []

    async def _yi(p, l, **k):
        called.append("yify")
        return []

    monkeypatch.setattr(aggregate._embedded, "available", lambda: True)
    monkeypatch.setattr(aggregate._embedded, "extract", _emb)
    monkeypatch.setattr(aggregate, "fetch_and_save_subtitles", _os)
    monkeypatch.setattr(aggregate._yify, "fetch", _yi)

    # yify disabled → not called even with an imdb_id; no os key → os skipped.
    await aggregate.fetch_subtitles(
        "v.mkv", ["en"], client=object(),
        enabled={"embedded": True, "opensubtitles": True, "yifysubtitles": False},
        os_api_key=None, imdb_id="tt1",
    )
    assert called == ["embedded"]

    # yify enabled but NO imdb → still skipped (movies-only, needs imdb).
    called.clear()
    await aggregate.fetch_subtitles(
        "v.mkv", ["en"], client=object(),
        enabled={"embedded": False, "opensubtitles": True, "yifysubtitles": True},
        os_api_key="k", imdb_id=None,
    )
    assert called == ["os"]
