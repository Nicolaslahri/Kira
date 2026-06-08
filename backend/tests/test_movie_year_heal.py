"""Movie year-mismatch self-heal (_HEAL_VERSION 25).

A movie whose stored match year disagrees with the file's parsed year
(e.g. 'Nobody 2 (2025)' stuck on the 2021 'Nobody') is re-matched by nulling
metadata_blob to arm the BATCH-loop movie rematch — which re-runs the matcher
WITH the parsed year. The episode_number stays untouched (movies have none).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from kira.api.matches import _heal_movie_year_mismatch
from kira.models import Base, MediaFile, Match


async def _mem_sessionmaker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _mf(path: str, year) -> MediaFile:
    return MediaFile(file_path=path, media_type="movie",
                     parsed_data={"title": "Nobody 2", "year": year}, status="matched")


def _match(mf_id: int, year, *, is_manual=False) -> Match:
    return Match(media_file_id=mf_id, provider="tmdb", provider_id="615457",
                 is_selected=True, is_manual=is_manual, match_type="movie",
                 title="Nobody", year=year, metadata_blob="{}", confidence=0.6)


async def test_year_mismatch_armed_for_rematch() -> None:
    Session = await _mem_sessionmaker()
    async with Session() as s:
        mf = _mf("Z:/movies/Nobody 2 (2025)/Nobody.2.2025.mkv", 2025)
        s.add(mf)
        await s.flush()
        s.add(_match(mf.id, 2021))  # matched the 2021 original — wrong
        await s.commit()

        n = await _heal_movie_year_mismatch(s)
        await s.commit()
        assert n == 1
        m = await s.scalar(select(Match).where(Match.media_file_id == mf.id))
        assert m.metadata_blob is None  # armed for the BATCH-loop movie rematch


async def test_matching_years_left_alone() -> None:
    Session = await _mem_sessionmaker()
    async with Session() as s:
        mf = _mf("Z:/movies/Nobody (2021)/Nobody.2021.mkv", 2021)
        s.add(mf)
        await s.flush()
        s.add(_match(mf.id, 2021))
        await s.commit()
        assert await _heal_movie_year_mismatch(s) == 0
        m = await s.scalar(select(Match).where(Match.media_file_id == mf.id))
        assert m.metadata_blob == "{}"  # untouched


async def test_manual_pin_never_touched() -> None:
    Session = await _mem_sessionmaker()
    async with Session() as s:
        mf = _mf("Z:/movies/Nobody 2 (2025)/Nobody.2.2025.mkv", 2025)
        s.add(mf)
        await s.flush()
        s.add(_match(mf.id, 2021, is_manual=True))
        await s.commit()
        assert await _heal_movie_year_mismatch(s) == 0


async def test_no_parsed_year_skipped() -> None:
    Session = await _mem_sessionmaker()
    async with Session() as s:
        mf = _mf("Z:/movies/Nobody/Nobody.mkv", None)  # parser found no year
        s.add(mf)
        await s.flush()
        s.add(_match(mf.id, 2021))
        await s.commit()
        assert await _heal_movie_year_mismatch(s) == 0
