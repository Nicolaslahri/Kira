"""Applying a pack: writing the authoritative Match row, isolation, override
scoping, and same-id fork separation. Uses an in-memory DB."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from kira.models import Base, Match, MediaFile
from kira.packs import loader as _loader
from kira.packs.apply import _series_group_id, try_pack_match, try_pack_override
from kira.packs.schema import PackBinding, parse_pack

PACK = parse_pack({
    "kira_pack": 1, "id": "one-pace", "name": "One Pace", "media_type": "anime",
    "show": {"title": "One Pace", "aliases": [], "year": 1999,
             "poster_url": "https://x/poster.jpg", "overview": "Fan re-edit."},
    "match": {"titles": ["One Pace"], "release_groups": ["One Pace"]},
    "episodes": [
        {"season": 1, "episode": 5, "title": "Romance Dawn 05",
         "overview": "RD05", "match": {"crc32": "a1b2c3d4"}},
    ],
})


async def _mem_sessionmaker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _add_file(Session, path, parsed):
    async with Session() as s:
        mf = MediaFile(file_path=path, parsed_data=parsed, media_type="anime",
                       status="no_match")
        s.add(mf)
        await s.flush()
        fid = mf.id
        await s.commit()
    return fid


def _one_pace_parsed():
    return {"original_filename": "[One Pace] Romance Dawn 05 [A1B2C3D4].mkv",
            "media_type": "anime", "title": "One Pace", "release_group": "One Pace",
            "episode": 5, "season": 1}


def _binding(**kw):
    base = dict(url="https://x/one-pace.json", id="one-pace", name="One Pace",
                subtitles=False)
    base.update(kw)
    return PackBinding(**base)


async def test_fallback_writes_authoritative_pack_match(monkeypatch):
    Session = await _mem_sessionmaker()
    fid = await _add_file(Session, "/anime/One Pace/rd05.mkv", _one_pace_parsed())

    async def _load(session):
        return [_binding()]

    async def _get(binding, **kw):
        return PACK

    monkeypatch.setattr(_loader, "load_bindings", _load)
    monkeypatch.setattr(_loader, "get_pack", _get)

    async with Session() as s:
        mf = await s.get(MediaFile, fid)
        assert await try_pack_match(s, fid, mf) is True
        await s.commit()

    async with Session() as s:
        rows = list(await s.scalars(select(Match).where(Match.media_file_id == fid)))
    assert len(rows) == 1
    r = rows[0]
    assert r.provider == "pack"
    assert r.match_type == "tv_episode"
    assert r.season_number == 1
    assert r.episode_number == 5
    assert r.title == "One Pace"
    assert r.episode_title == "Romance Dawn 05"
    assert r.is_selected is True
    # group id carries the url hash so a forked copy won't merge into this card
    assert r.series_group_id.startswith("pack:one-pace:")


async def test_isolation_unrelated_file_not_claimed(monkeypatch):
    Session = await _mem_sessionmaker()
    parsed = {"original_filename": "Breaking Bad S01E01.mkv", "media_type": "tv",
              "title": "Breaking Bad", "episode": 1, "season": 1}
    fid = await _add_file(Session, "/tv/Breaking Bad/x.mkv", parsed)

    async def _load(session):
        return [_binding()]

    async def _get(binding, **kw):
        return PACK

    monkeypatch.setattr(_loader, "load_bindings", _load)
    monkeypatch.setattr(_loader, "get_pack", _get)

    async with Session() as s:
        mf = await s.get(MediaFile, fid)
        assert await try_pack_match(s, fid, mf) is False
        await s.commit()

    async with Session() as s:
        rows = list(await s.scalars(select(Match).where(Match.media_file_id == fid)))
    assert rows == []


async def test_override_only_fires_for_override_bindings(monkeypatch):
    Session = await _mem_sessionmaker()
    fid = await _add_file(Session, "Z:/anime/One Pace/rd05.mkv", _one_pace_parsed())

    async def _get(binding, **kw):
        return PACK

    monkeypatch.setattr(_loader, "get_pack", _get)

    # A fallback binding → override pre-pass is a no-op.
    async def _load_fallback(session):
        return [_binding()]
    monkeypatch.setattr(_loader, "load_bindings", _load_fallback)
    async with Session() as s:
        assert await try_pack_override(s, fid, await s.get(MediaFile, fid)) is False

    # An override binding scoped to the file's folder → it fires.
    async def _load_override(session):
        return [_binding(authority="override", scope_paths=["Z:/anime/One Pace"])]
    monkeypatch.setattr(_loader, "load_bindings", _load_override)
    async with Session() as s:
        assert await try_pack_override(s, fid, await s.get(MediaFile, fid)) is True
        await s.commit()
    async with Session() as s:
        rows = list(await s.scalars(select(Match).where(Match.media_file_id == fid)))
    assert len(rows) == 1 and rows[0].provider == "pack"


async def test_override_no_op_outside_scope(monkeypatch):
    Session = await _mem_sessionmaker()
    fid = await _add_file(Session, "Z:/anime/Elsewhere/rd05.mkv", _one_pace_parsed())

    async def _get(binding, **kw):
        return PACK

    async def _load(session):
        return [_binding(authority="override", scope_paths=["Z:/anime/One Pace"])]

    monkeypatch.setattr(_loader, "get_pack", _get)
    monkeypatch.setattr(_loader, "load_bindings", _load)

    async with Session() as s:
        assert await try_pack_override(s, fid, await s.get(MediaFile, fid)) is False


def test_fork_packs_get_distinct_group_ids():
    a = _binding(url="https://a.com/one-pace.json")
    b = _binding(url="https://b.com/one-pace.json")
    assert _series_group_id(PACK, a) != _series_group_id(PACK, b)
    assert _series_group_id(PACK, a).startswith("pack:one-pace:")
