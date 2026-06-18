"""Artwork download orchestration in rename.py — the fanart.tv glue.

Pins the new decision logic: artwork-kind resolution from settings, id
resolution (incl. anime → TVDB via the Fribb cross-ref), and the
`_download_artwork_files` flow — fanart.tv for the rich kinds, provider poster/
background fallback, correct `<stem>-<kind>.<ext>` filenames, and the per-batch
caches (one fanart.tv call + one image fetch per series, regardless of episode
count). httpx + fanart.tv are faked — no network.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from kira.api import rename as R


@pytest.fixture(autouse=True)
def _inline_to_thread(monkeypatch):
    """Run `_download_artwork_files`'s `asyncio.to_thread(_atomic_write, …)`
    inline instead of on the loop's executor. The file write is identical (these
    tests still assert the sidecars land), but skipping the executor keeps the
    test's event-loop teardown deterministic — otherwise the lingering executor
    raced aiosqlite's connection-worker thread at loop close and pytest reported
    a benign `Event loop is closed` thread-exception warning. Production is
    unaffected (one long-lived uvicorn loop, no per-call teardown)."""
    async def _inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(asyncio, "to_thread", _inline)


# ── fakes ─────────────────────────────────────────────────────────────────────
class _Sel:
    def __init__(self, provider, provider_id, poster_url=None):
        self.provider = provider
        self.provider_id = provider_id
        self.poster_url = poster_url


class _Parsed:
    def __init__(self, media_type):
        self.media_type = media_type


class _Row:
    def __init__(self, value):
        self.value = value


class _Session:
    def __init__(self, settings: dict):
        self._s = settings

    async def get(self, _model, key):
        return _Row(self._s[key]) if key in self._s else None


# ── _resolve_artwork_kinds ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_artwork_kinds_default_when_unset():
    kinds = await R._resolve_artwork_kinds(_Session({}))
    assert kinds == {"poster", "fanart", "clearlogo"}   # mirrors _ARTWORK_DEFAULTS


@pytest.mark.asyncio
async def test_artwork_kinds_explicit_override():
    s = _Session({"naming.artwork_types": {"clearlogo": False, "banner": True, "disc": True}})
    kinds = await R._resolve_artwork_kinds(s)
    # poster/fanart keep their default-on; clearlogo turned off; banner+disc on.
    assert kinds == {"poster", "fanart", "banner", "disc"}


@pytest.mark.asyncio
async def test_artwork_kinds_unwraps_value_wrapper():
    s = _Session({"naming.artwork_types": {"value": {"poster": False}}})
    kinds = await R._resolve_artwork_kinds(s)
    assert "poster" not in kinds and "fanart" in kinds


# ── _resolve_artwork_ids ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ids_movie_tmdb_match():
    tmdb, tvdb, imdb = await R._resolve_artwork_ids(_Sel("tmdb", "27205"), {}, "movie")
    assert (tmdb, tvdb, imdb) == ("27205", None, None)


@pytest.mark.asyncio
async def test_ids_tv_tvdb_match():
    tmdb, tvdb, imdb = await R._resolve_artwork_ids(_Sel("tvdb", "81797"), {}, "tv")
    assert (tmdb, tvdb, imdb) == (None, "81797", None)


@pytest.mark.asyncio
async def test_ids_anime_resolves_tvdb_via_crossref(monkeypatch):
    from kira.providers.anime_mappings import AnimeMappings

    async def _tvdb(cls, aid):
        return 81797 if aid == 69 else None

    monkeypatch.setattr(AnimeMappings, "tvdb_id", classmethod(_tvdb))
    tmdb, tvdb, imdb = await R._resolve_artwork_ids(_Sel("anidb", "69"), {}, "anime")
    assert tvdb == "81797"   # One Piece AniDB 69 → TVDB 81797 for fanart.tv /tv


@pytest.mark.asyncio
async def test_ids_imdb_and_crossref_from_meta():
    meta = {"imdbid": "tt1375666", "tmdb_id": 27205}
    tmdb, tvdb, imdb = await R._resolve_artwork_ids(_Sel("anidb", "999999"), meta, "movie")
    # no AniDB→tvdb here (movie); imdb + tmdb pulled from meta.
    assert imdb == "tt1375666" and tmdb == "27205"


# ── _download_artwork_files: filenames, fallback, caching ─────────────────────
class _Resp:
    def __init__(self, content=b"\x89PNG\r\n\x1a\n"):
        self.status_code = 200
        self.content = content
        self.headers = {"content-type": "image/png"}


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


class _FakeHttp:
    """Fake httpx.AsyncClient (async ctx mgr). Counts GETs across instances."""
    gets: list[str] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        _FakeHttp.gets.append(url)
        return _Resp()

    def stream(self, method, url, **kw):
        _FakeHttp.gets.append(url)
        return _StreamResp(_Resp())


@pytest.mark.asyncio
async def test_download_writes_correct_filenames_and_uses_fallback(tmp_path, monkeypatch):
    _FakeHttp.gets = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttp)
    monkeypatch.setattr("kira.download_guard.sniff_image", lambda b: "png")

    async def _fake_fetch(**kw):
        # fanart.tv supplies clearlogo; poster is left to the provider fallback.
        assert kw["media_type"] == "movie" and kw["tmdb_id"] == "27205"
        return {"clearlogo": "https://fanart/logo.png"}

    monkeypatch.setattr("kira.providers.fanarttv.fetch_artwork", _fake_fetch)

    target = tmp_path / "Inception (2010).mkv"
    sel = _Sel("tmdb", "27205", poster_url="https://tmdb/poster.jpg")
    await R._download_artwork_files(
        target, _Parsed("movie"), sel, {},
        kinds={"poster", "clearlogo"}, fanart_key="k",
        fanart_cache={}, img_cache={},
    )
    assert (tmp_path / "Inception (2010)-clearlogo.png").exists()   # fanart.tv, .png
    assert (tmp_path / "Inception (2010)-poster.jpg").exists()      # provider fallback, .jpg


@pytest.mark.asyncio
async def test_batch_caches_fanart_call_and_image_bytes(tmp_path, monkeypatch):
    _FakeHttp.gets = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttp)
    monkeypatch.setattr("kira.download_guard.sniff_image", lambda b: "png")

    calls = {"n": 0}

    async def _fake_fetch(**kw):
        calls["n"] += 1
        return {"clearlogo": "https://fanart/logo.png"}

    monkeypatch.setattr("kira.providers.fanarttv.fetch_artwork", _fake_fetch)

    fanart_cache: dict = {}
    img_cache: dict = {}
    sel = _Sel("tvdb", "81797")
    # Two "episodes" of the same series in one batch.
    for ep in ("Show - S01E01.mkv", "Show - S01E02.mkv"):
        await R._download_artwork_files(
            tmp_path / ep, _Parsed("tv"), sel, {},
            kinds={"clearlogo"}, fanart_key="k",
            fanart_cache=fanart_cache, img_cache=img_cache,
        )
    # fanart.tv hit ONCE despite two files; the logo image fetched ONCE.
    assert calls["n"] == 1
    assert _FakeHttp.gets == ["https://fanart/logo.png"]
    # …and show-level art is written ONCE into the series root (here tmp_path —
    # these episodes have no Season folder), NOT duplicated as a per-episode
    # sidecar. (Episode files want a -thumb still, not the show poster/logo.)
    assert (tmp_path / "clearlogo.png").exists()
    assert not (tmp_path / "Show - S01E01-clearlogo.png").exists()
    assert not (tmp_path / "Show - S01E02-clearlogo.png").exists()


@pytest.mark.asyncio
async def test_no_fanart_key_still_writes_provider_poster(tmp_path, monkeypatch):
    _FakeHttp.gets = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttp)
    monkeypatch.setattr("kira.download_guard.sniff_image", lambda b: "jpg")

    # fetch_artwork must NOT be called without a key.
    async def _boom(**kw):
        raise AssertionError("fanart.tv should not be called without a key")

    monkeypatch.setattr("kira.providers.fanarttv.fetch_artwork", _boom)

    target = tmp_path / "Movie (2021).mkv"
    sel = _Sel("tmdb", "1", poster_url="https://tmdb/p.jpg")
    await R._download_artwork_files(
        target, _Parsed("movie"), sel, {"backdrop_url": "https://tmdb/bg.jpg"},
        kinds={"poster", "fanart", "clearlogo"}, fanart_key="",
        fanart_cache={}, img_cache={},
    )
    # poster + fanart from the provider; clearlogo skipped (needs fanart.tv).
    assert (tmp_path / "Movie (2021)-poster.jpg").exists()
    assert (tmp_path / "Movie (2021)-fanart.jpg").exists()
    assert not (tmp_path / "Movie (2021)-clearlogo.png").exists()


# ── per-cour SEASON poster (anime) ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_anime_writes_per_cour_season_poster(tmp_path, monkeypatch):
    """An AniDB cour carries its OWN poster; cours are unified into one show with
    seasons, so the cour poster must land as `Season NN/poster.jpg` (not only the
    single show-root poster, which is first-cour-wins)."""
    _FakeHttp.gets = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttp)
    monkeypatch.setattr("kira.download_guard.sniff_image", lambda b: "jpg")

    async def _boom(**kw):
        raise AssertionError("no fanart key configured")

    monkeypatch.setattr("kira.providers.fanarttv.fetch_artwork", _boom)

    season = tmp_path / "Attack on Titan" / "Season 02"
    season.mkdir(parents=True)
    target = season / "Attack on Titan - S02E06 - Warrior.mkv"
    sel = _Sel("anidb", "17", poster_url="https://anidb/aot-s2.jpg")
    await R._download_artwork_files(
        target, _Parsed("anime"), sel, {},
        kinds={"poster"}, fanart_key="", fanart_cache={}, img_cache={},
    )
    # The cour's poster lands as the SEASON poster (where Plex/Jellyfin read it)…
    assert (season / "poster.jpg").exists()
    # …plus the show-root poster (existing behaviour).
    assert (tmp_path / "Attack on Titan" / "poster.jpg").exists()


@pytest.mark.asyncio
async def test_regular_tv_has_no_per_season_poster(tmp_path, monkeypatch):
    """Regular-TV seasons share the one show poster, so we DON'T duplicate it into
    each Season folder — only anime cours (distinct per-cour art) get one."""
    _FakeHttp.gets = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttp)
    monkeypatch.setattr("kira.download_guard.sniff_image", lambda b: "jpg")

    async def _boom(**kw):
        raise AssertionError("no fanart key configured")

    monkeypatch.setattr("kira.providers.fanarttv.fetch_artwork", _boom)

    season = tmp_path / "Breaking Bad" / "Season 01"
    season.mkdir(parents=True)
    target = season / "Breaking Bad - S01E01.mkv"
    sel = _Sel("tvdb", "81189", poster_url="https://tvdb/bb.jpg")
    await R._download_artwork_files(
        target, _Parsed("tv"), sel, {},
        kinds={"poster"}, fanart_key="", fanart_cache={}, img_cache={},
    )
    assert (tmp_path / "Breaking Bad" / "poster.jpg").exists()   # show poster
    assert not (season / "poster.jpg").exists()                  # NOT per-season
