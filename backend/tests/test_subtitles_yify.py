"""YIFY scraper helpers + the (scored) subtitle aggregator.

YIFY is the one HTML-scraper source: `/movie-imdb/tt<id>` → slug carries the
language → `/subtitle/<slug>.zip`. These pin the pure parse (imdb normalize,
slug match) and its search()/download() shape. The aggregator tests pin the
gather → score → best-pick flow: embedded wins outright (perfect sync), and
across external providers the highest-scored candidate is the one downloaded.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from kira.subtitles import aggregate, subsource, yifysubtitles as yify
from kira.subtitles.model import SearchContext, SubtitleCandidate


# ── fakes ──────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, status=200, text="", content=b"", ctype="text/html"):
        self.status_code = status
        self.text = text
        self.content = content
        self.headers = {"content-type": ctype}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        import json
        return json.loads(self.text or "{}")


class _StreamResp:
    """Minimal async-context-manager response for client.stream(...)."""
    def __init__(self, resp: _Resp):
        self.status_code = resp.status_code
        self.headers = resp.headers
        self._content = resp.content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self, *a, **k):
        yield self._content


class _FakeClient:
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


def _zip_pack(names: list[str]) -> bytes:
    """A multi-entry archive (a season pack) — each entry's body encodes its
    own name so the test can assert WHICH episode was extracted."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in names:
            zf.writestr(n, f"1\n00:00:01,000 --> 00:00:02,000\n{n}\n")
    return buf.getvalue()


# ── season-pack extraction (episode-aware pick + wrong-episode guard) ─────────
def test_pack_extracts_matching_episode():
    from kira.subtitles._common import subtitle_from_zip
    pack = _zip_pack(["Nana - 05.srt", "Nana - 06.srt", "Nana - 07.srt"])
    data, ext = subtitle_from_zip(pack, season=1, episode=6)
    assert ext == "srt" and b"Nana - 06.srt" in data   # picked E06, not the first


def test_pack_prefers_sxxexx_over_bare_number():
    from kira.subtitles._common import subtitle_from_zip
    pack = _zip_pack(["Show.106.srt", "Show.S01E06.srt"])
    data, _ = subtitle_from_zip(pack, season=1, episode=6)
    assert b"S01E06" in data   # SxxEyy is the strongest signal


def test_pack_without_wanted_episode_refuses_to_guess():
    from kira.subtitles._common import subtitle_from_zip
    # A pack of 1-3 but we want episode 6 — saving E01 as E06 would be a lie.
    pack = _zip_pack(["Nana - 01.srt", "Nana - 02.srt", "Nana - 03.srt"])
    assert subtitle_from_zip(pack, season=1, episode=6) is None


def test_single_entry_zip_taken_regardless_of_episode():
    from kira.subtitles._common import subtitle_from_zip
    # One file in the archive is unambiguous — take it even if the name carries
    # no episode signal at all.
    one = _zip_with_srt("subtitle.srt")
    data, ext = subtitle_from_zip(one, season=1, episode=6)
    assert ext == "srt" and data


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


# ── yify search + download ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_yify_search_returns_candidate():
    slug = "inception-2010-english-yify-392189"
    client = _FakeClient({"/movie-imdb/tt1375666": _Resp(text=f'<a href="/subtitles/{slug}">EN</a>')})
    ctx = SearchContext(video_path="Inception.mkv", languages=["en"], imdb_id="tt1375666")
    cands = await yify.search(client, ctx)
    assert len(cands) == 1
    assert cands[0].provider == "yifysubtitles" and cands[0].download_ref == slug


@pytest.mark.asyncio
async def test_yify_search_no_imdb_is_noop():
    client = _FakeClient({})
    cands = await yify.search(client, SearchContext(video_path="x.mkv", languages=["en"], imdb_id=None))
    assert cands == [] and client.calls == []


@pytest.mark.asyncio
async def test_yify_download_returns_zip_bytes():
    slug = "s"
    client = _FakeClient({f"/subtitle/{slug}.zip": _Resp(content=_zip_with_srt(), ctype="application/zip")})
    cand = SubtitleCandidate(provider="yifysubtitles", language="en", download_ref=slug)
    raw = await yify.download(client, cand, SearchContext(video_path="x.mkv", languages=["en"]))
    assert raw and raw[:2] == b"PK"


# ── aggregator: embedded-first, then scored best-pick ────────────────────────
@pytest.mark.asyncio
async def test_aggregate_embedded_wins_and_skips_external(tmp_path, monkeypatch):
    video = str(tmp_path / "Movie (2020).mkv")
    searched: list[str] = []

    async def _emb(path, langs, forced=""):
        # extract writes the sidecar; return its path
        p = str(tmp_path / "Movie (2020).en.srt")
        open(p, "w").write("sub")
        return [p]

    monkeypatch.setattr(aggregate._embedded, "available", lambda: True)
    monkeypatch.setattr(aggregate._embedded, "extract", _emb)

    async def _ss_search(client, ctx):
        searched.append("subsource")
        return [SubtitleCandidate(provider="subsource", language="en", download_ref=1)]
    monkeypatch.setattr(aggregate._subsource, "search", _ss_search)

    ctx = SearchContext(video_path=video, languages=["en"], media_type="movie")
    results = await aggregate.fetch_subtitles(object(), ctx,
                                              enabled={"embedded": True, "subsource": True})
    assert [r.provider for r in results] == ["embedded"]
    assert results[0].sync == "guaranteed" and results[0].score == 100
    assert searched == []   # embedded covered EN → no external search


@pytest.mark.asyncio
async def test_aggregate_picks_highest_scored_external(tmp_path, monkeypatch):
    video = str(tmp_path / "Show - S01E01 [Moozzi2] BluRay 1080p.mkv")
    monkeypatch.setattr(aggregate._embedded, "available", lambda: False)

    # Two candidates: a hash-match (should win) vs a title-only one.
    async def _os_search(client, ctx):
        return [SubtitleCandidate(provider="opensubtitles", language="en",
                                  release_name="random", hash_match=True, download_ref=9)]

    async def _ss_search(client, ctx):
        return [SubtitleCandidate(provider="subsource", language="en", release_name="", download_ref=1)]

    downloaded: list[str] = []

    async def _os_dl(client, cand, ctx):
        downloaded.append("opensubtitles")
        return b"1\n00:00 --> 00:01\nhi\n"   # raw srt (not a zip)

    async def _ss_dl(client, cand, ctx):
        downloaded.append("subsource")
        return b"x"

    monkeypatch.setattr(aggregate._opensubtitles, "search", _os_search)
    monkeypatch.setattr(aggregate._opensubtitles, "download", _os_dl)
    monkeypatch.setattr(aggregate._subsource, "search", _ss_search)
    monkeypatch.setattr(aggregate._subsource, "download", _ss_dl)

    ctx = SearchContext(video_path=video, languages=["en"], media_type="movie",
                        parsed={"release_group": "Moozzi2", "quality": "1080p", "source": "BluRay"})
    results = await aggregate.fetch_subtitles(object(), ctx,
                                              enabled={"opensubtitles": True, "subsource": True})
    assert [r.provider for r in results] == ["opensubtitles"]   # hash-match scored highest
    assert results[0].sync == "guaranteed"
    assert downloaded == ["opensubtitles"]   # only the winner is downloaded


@pytest.mark.asyncio
async def test_aggregate_min_score_floor_skips_weak_pick(tmp_path, monkeypatch):
    video = str(tmp_path / "Show.mkv")
    monkeypatch.setattr(aggregate._embedded, "available", lambda: False)
    dl: list[str] = []

    async def _ss(client, ctx):
        # title-only, no release affinity → a low score (~30)
        return [SubtitleCandidate(provider="subsource", language="en", release_name="", download_ref=1)]

    async def _ss_dl(client, cand, ctx):
        dl.append("x"); return b"sub"

    monkeypatch.setattr(aggregate._subsource, "search", _ss)
    monkeypatch.setattr(aggregate._subsource, "download", _ss_dl)

    ctx = SearchContext(video_path=video, languages=["en"], media_type="movie", min_score=80)
    results = await aggregate.fetch_subtitles(object(), ctx, enabled={"subsource": True})
    assert results == []        # best candidate below the 80 floor → nothing saved
    assert dl == []             # and never even downloaded


@pytest.mark.asyncio
async def test_aggregate_skips_disabled_providers(tmp_path, monkeypatch):
    monkeypatch.setattr(aggregate._embedded, "available", lambda: False)
    searched: list[str] = []

    async def _ss(client, ctx):
        searched.append("subsource"); return []

    async def _yi(client, ctx):
        searched.append("yify"); return []

    monkeypatch.setattr(aggregate._subsource, "search", _ss)
    monkeypatch.setattr(aggregate._yify, "search", _yi)

    ctx = SearchContext(video_path=str(tmp_path / "v.mkv"), languages=["en"])
    await aggregate.fetch_subtitles(object(), ctx,
                                    enabled={"subsource": True, "yifysubtitles": False})
    assert searched == ["subsource"]   # yify disabled → not searched
