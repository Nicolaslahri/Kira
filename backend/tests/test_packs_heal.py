"""Pack matches are USER-AUTHORITATIVE and must survive the boot heal / any
re-match path — the "One Pace keeps going unmatched at random times" bug.

Root cause: pack matches legitimately carry a NULL `metadata_blob`, which made
them match the heal sweep's stale-row filter (`tv_episode` AND metadata IS NULL).
Heal then ran `_rematch_one`, which re-discovered via the providers — and the
providers deliberately floor fan-edits like One Pace to no_match, wiping the pack
match. (A manual scan re-applies the pack via apply_packs_to_no_match, which is
why scanning "fixed" it, but heal never re-applied → it kept reverting.)

`_rematch_one` now short-circuits a selected pack match without touching the
providers — defense in depth behind the heal query's own `provider != 'pack'`."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from kira.api.matches import _rematch_one
from kira.models import Base, Match, MediaFile


async def _mem_sessionmaker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


class _BoomEngine:
    """Any `.match()` call means the pack guard FAILED to short-circuit."""
    async def match(self, *a, **k):
        raise AssertionError("pack match must never be re-discovered via providers")


@pytest.mark.asyncio
async def test_rematch_one_preserves_pack_match():
    Session = await _mem_sessionmaker()
    async with Session() as s:
        mf = MediaFile(
            file_path="/anime/One Pace/Romance Dawn 06.mkv",
            parsed_data={"title": "One Pace", "episode": 6},
            media_type="anime", status="matched",
        )
        s.add(mf)
        await s.flush()
        fid = mf.id
        s.add(Match(
            media_file_id=fid, provider="pack", provider_id="one-pace:1:6",
            match_type="tv_episode", confidence=1.0, is_selected=True,
            series_group_id="pack:one-pace:abc12345",
        ))  # metadata_blob stays NULL — the exact shape that tripped the heal
        await s.commit()

    async with Session() as s:
        mf = await s.get(MediaFile, fid)
        n = await _rematch_one(mf, _BoomEngine(), s)   # raises if it re-discovers
        assert n == 0
        rows = (await s.execute(select(Match).where(Match.media_file_id == fid))).scalars().all()
        assert len(rows) == 1
        assert rows[0].provider == "pack"
        assert rows[0].is_selected is True
