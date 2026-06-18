"""Subtitle reuse-cache (`kira/subtitles/subcache.py`).

Pins the contract the undo path + the fetch path depend on:

  • cache_subtitle MOVES a sidecar into a managed `.kira-subcache` dir under the
    library root and find_cached_subtitle gets it back (keyed by content hash);
  • find returns None for a video that was never cached;
  • the key is rename-stable — caching under one name, looking up under another
    (same bytes) still hits;
  • fetch_subtitles REUSES a cached sub before any provider runs (no network /
    no OpenSubtitles quota) and reports it as provider "cache";
  • sweep_expired drops an entry past the retention window and keeps a fresh one.

Follows the suite conventions: asyncio_mode=auto (no decorators), a temp SQLite
DB swapped in via monkeypatch on `kira.database.SessionLocal`, settings seeded
as `Setting` rows.
"""
from __future__ import annotations

import os
import time

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.models import Setting
from kira.subtitles import subcache


# Two 64 KiB chunks are the minimum the OSDb hash needs; make the fake video
# comfortably bigger so it's content-hashable (not the basename fallback).
_VIDEO_BYTES = b"\xab" * (200 * 1024)


async def _seed_root(tmp_path, monkeypatch, *, retention=None):
    """Temp DB + a `paths.library_root` pointing at `tmp_path/lib`, so the cache
    dir resolves to `<lib>/.kira-subcache`. Returns the library root path."""
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sc.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)   # subcache resolves root via this
    await db.init_db()

    lib = tmp_path / "lib"
    lib.mkdir()
    async with sm() as s:
        s.add(Setting(key="paths.library_root", value=str(lib)))
        if retention is not None:
            s.add(Setting(key="subtitles.cache_retention_days", value=retention))
        await s.commit()
    return lib


def _make_video(lib, name="Show - S01E01.mkv", *, salt=b""):
    # `salt` perturbs the bytes so two videos get DISTINCT content hashes (the
    # key is content-based, so identical bytes legitimately share a key).
    v = lib / name
    v.write_bytes(_VIDEO_BYTES + salt)
    return v


def _make_srt(lib, name="dropped.en.srt", text="1\n00:00:01,000 --> 00:00:02,000\nhi\n"):
    p = lib / name
    p.write_text(text, encoding="utf-8")
    return p


# ── key ────────────────────────────────────────────────────────────────────
def test_cache_key_includes_language_and_is_stable():
    # Same path, two languages → distinct keys; same path+lang → identical key.
    k_en = subcache.cache_key("/x/Show.mkv", "en")
    k_es = subcache.cache_key("/x/Show.mkv", "es")
    assert k_en != k_es
    assert k_en == subcache.cache_key("/x/Show.mkv", "EN")  # case-insensitive lang


def test_cache_key_content_hash_is_rename_stable(tmp_path):
    # A hashable file gets a content-hash ("h_") key that does NOT depend on the
    # filename — so the same bytes under a different name share a key.
    a = tmp_path / "Original.Name.mkv"
    b = tmp_path / "Renamed - S01E01.mkv"
    a.write_bytes(_VIDEO_BYTES)
    b.write_bytes(_VIDEO_BYTES)
    ka = subcache.cache_key(str(a), "en")
    kb = subcache.cache_key(str(b), "en")
    assert ka.startswith("h_")
    assert ka == kb


# ── cache_subtitle + find_cached_subtitle ───────────────────────────────────
async def test_cache_then_find_roundtrip(tmp_path, monkeypatch):
    lib = await _seed_root(tmp_path, monkeypatch)
    video = _make_video(lib)
    srt = _make_srt(lib)

    cached = await subcache.cache_subtitle(str(srt), video_path=str(video), language="en")
    assert cached is not None
    assert os.path.isfile(cached)
    # The file was MOVED, not copied — original sidecar is gone.
    assert not srt.exists()
    # It landed in the managed sibling dir under the library root.
    assert os.path.dirname(cached) == str(lib / ".kira-subcache")
    # A JSON sidecar with the provenance was written next to it.
    meta = cached[: -len(".srt")] + ".json"
    assert os.path.isfile(meta)

    found = await subcache.find_cached_subtitle(str(video), "en")
    assert found == cached


async def test_find_returns_none_for_unknown_video(tmp_path, monkeypatch):
    lib = await _seed_root(tmp_path, monkeypatch)
    _make_video(lib)  # cache something else? no — nothing cached at all.
    other = lib / "Never - S09E09.mkv"
    other.write_bytes(_VIDEO_BYTES + b"different-tail")
    assert await subcache.find_cached_subtitle(str(other), "en") is None


async def test_find_other_language_misses(tmp_path, monkeypatch):
    lib = await _seed_root(tmp_path, monkeypatch)
    video = _make_video(lib)
    srt = _make_srt(lib)
    await subcache.cache_subtitle(str(srt), video_path=str(video), language="en")
    # Cached EN must not satisfy an ES lookup.
    assert await subcache.find_cached_subtitle(str(video), "es") is None


# ── reuse-in-fetch (no network) ─────────────────────────────────────────────
async def test_fetch_reuses_cache_and_skips_network(tmp_path, monkeypatch):
    from kira.subtitles import aggregate
    from kira.subtitles.model import SearchContext

    lib = await _seed_root(tmp_path, monkeypatch)
    video = _make_video(lib)
    # Seed the cache as if a prior download had been kept on undo.
    srt = _make_srt(lib)
    await subcache.cache_subtitle(str(srt), video_path=str(video), language="en")

    # Any provider search blowing up the test means the network was hit despite
    # a cache entry — that's the regression this guards.
    async def _boom(*a, **k):
        raise AssertionError("network providers must not run for a cached language")
    monkeypatch.setattr(aggregate, "gather_candidates", _boom)
    # Embedded extraction off so the cache is the ONLY reason en is satisfied.
    monkeypatch.setattr("kira.subtitles.embedded.available", lambda: False)

    ctx = SearchContext(video_path=str(video), languages=["en"], media_type="tv")
    results = await aggregate.fetch_subtitles(None, ctx, enabled={"opensubtitles": True})

    assert [r.language for r in results] == ["en"]
    assert results[0].provider == "cache"
    # The sub was re-materialized as the sidecar beside the video.
    expected = lib / "Show - S01E01.en.srt"
    assert results[0].path == str(expected)
    assert expected.exists()


async def test_fetch_still_searches_uncached_language(tmp_path, monkeypatch):
    # A second wanted language with NO cache entry must still reach the providers
    # (the cache short-circuits ONLY the cached language).
    from kira.subtitles import aggregate
    from kira.subtitles.model import SearchContext

    lib = await _seed_root(tmp_path, monkeypatch)
    video = _make_video(lib)
    srt = _make_srt(lib)
    await subcache.cache_subtitle(str(srt), video_path=str(video), language="en")

    seen_langs: list[list[str]] = []

    async def _gather(client, ctx, enabled):
        seen_langs.append(list(ctx.languages))
        return []  # no candidates → es simply ends up unsatisfied, that's fine

    monkeypatch.setattr(aggregate, "gather_candidates", _gather)
    monkeypatch.setattr("kira.subtitles.embedded.available", lambda: False)

    ctx = SearchContext(video_path=str(video), languages=["en", "es"], media_type="tv")
    results = await aggregate.fetch_subtitles(None, ctx, enabled={"opensubtitles": True})

    # en came from the cache; the provider search ran for es only.
    assert any(r.provider == "cache" and r.language == "en" for r in results)
    assert seen_langs == [["es"]]


# ── sweep_expired ────────────────────────────────────────────────────────────
async def test_sweep_removes_expired_keeps_fresh(tmp_path, monkeypatch):
    lib = await _seed_root(tmp_path, monkeypatch, retention=7)
    # Distinct content → distinct cache keys (no collision/overwrite).
    video_old = _make_video(lib, "Old - S01E01.mkv", salt=b"old")
    video_new = _make_video(lib, "New - S01E02.mkv", salt=b"new")
    old = await subcache.cache_subtitle(
        str(_make_srt(lib, "o.en.srt")), video_path=str(video_old), language="en")
    new = await subcache.cache_subtitle(
        str(_make_srt(lib, "n.en.srt")), video_path=str(video_new), language="en")
    assert old and new

    # Backdate the old entry's mtime well past the 7-day window.
    stale = time.time() - 30 * 86400
    os.utime(old, (stale, stale))
    old_meta = old[: -len(".srt")] + ".json"
    if os.path.exists(old_meta):
        os.utime(old_meta, (stale, stale))

    removed = await subcache.sweep_expired()
    assert removed == 1
    assert not os.path.exists(old)          # expired one reaped
    assert not os.path.exists(old_meta)     # its sidecar too
    assert os.path.exists(new)              # fresh one kept


async def test_sweep_zero_retention_keeps_everything(tmp_path, monkeypatch):
    lib = await _seed_root(tmp_path, monkeypatch, retention=0)
    video = _make_video(lib)
    cached = await subcache.cache_subtitle(
        str(_make_srt(lib)), video_path=str(video), language="en")
    # Even an ancient entry survives when retention is 0 (keep forever).
    stale = time.time() - 9999 * 86400
    os.utime(cached, (stale, stale))

    assert await subcache.sweep_expired() == 0
    assert os.path.exists(cached)
    # And it must still be findable (0-retention never expires a hit).
    assert await subcache.find_cached_subtitle(str(video), "en") == cached
