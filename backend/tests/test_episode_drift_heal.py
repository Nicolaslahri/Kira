"""Episode-drift self-heal (_HEAL_VERSION 24).

The One Piece stale-match class: files parse as episodes 1156-1160 but the
selected Match rows are stuck on episodes 1-5 from an earlier scan. The
regular heal loop misses these (they HAVE an episode_title + metadata_blob,
just pointing at the wrong episode), so `_heal_episode_number_drift` flags
them and arms the BATCH-loop trigger by nulling enrichment — WITHOUT
deciding the episode itself (the real, ban-aware matcher re-decides).

These tests pin the detection rules:
  - drift (number matches neither parsed.episode nor parsed.absolute) → armed
  - episode_number LEFT INTACT (so a deferred re-match doesn't blank a row)
  - correct rows (local OR absolute agree) → untouched
  - manual pins → never touched
  - files with no parsed episode at all → skipped (that's _reparse's job)
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from kira.api.matches import _heal_episode_number_drift
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


def _mf(path: str, parsed: dict | None, media_type: str = "anime") -> MediaFile:
    return MediaFile(file_path=path, media_type=media_type, parsed_data=parsed,
                     status="matched")


def _match(mf_id: int, *, episode_number, is_manual: bool = False,
           match_type: str = "tv_episode") -> Match:
    return Match(
        media_file_id=mf_id,
        provider="anidb",
        provider_id="69",
        is_selected=True,
        is_manual=is_manual,
        match_type=match_type,
        episode_number=episode_number,
        episode_title="Stored Title",
        metadata_blob="{}",
        confidence=1.0,
    )


async def test_drift_is_armed_for_rematch_but_episode_number_kept() -> None:
    """episode_number=1 vs parsed.episode=1156 → enrichment nulled, number kept."""
    Session = await _mem_sessionmaker()
    async with Session() as s:
        mf = _mf("/m/One Piece - 1156.mkv", {"episode": 1156})
        s.add(mf)
        await s.flush()
        s.add(_match(mf.id, episode_number=1))
        await s.commit()

        n = await _heal_episode_number_drift(s)
        await s.commit()
        assert n == 1

        m = await s.scalar(select(Match).where(Match.media_file_id == mf.id))
        # Armed: BATCH loop trigger is "episode_title IS NULL OR blob IS NULL".
        assert m.episode_title is None
        assert m.metadata_blob is None
        # Deliberately KEPT so a ban-deferred re-match doesn't blank the row.
        assert m.episode_number == 1


async def test_correct_local_match_untouched() -> None:
    """episode_number == parsed.episode → not drift, left alone."""
    Session = await _mem_sessionmaker()
    async with Session() as s:
        mf = _mf("/m/Show - S01E05.mkv", {"episode": 5}, media_type="tv")
        s.add(mf)
        await s.flush()
        s.add(_match(mf.id, episode_number=5))
        await s.commit()

        assert await _heal_episode_number_drift(s) == 0
        m = await s.scalar(select(Match).where(Match.media_file_id == mf.id))
        assert m.episode_title == "Stored Title"  # untouched


async def test_absolute_match_untouched() -> None:
    """episode_number matches the parsed ABSOLUTE episode → not drift."""
    Session = await _mem_sessionmaker()
    async with Session() as s:
        mf = _mf("/m/One Piece - 1156.mkv", {"episode": None, "absolute_episode": 1156})
        s.add(mf)
        await s.flush()
        s.add(_match(mf.id, episode_number=1156))
        await s.commit()

        assert await _heal_episode_number_drift(s) == 0
        m = await s.scalar(select(Match).where(Match.media_file_id == mf.id))
        assert m.episode_title == "Stored Title"


async def test_manual_pin_never_touched() -> None:
    """A drifted row that is a manual pin must be left exactly as-is."""
    Session = await _mem_sessionmaker()
    async with Session() as s:
        mf = _mf("/m/One Piece - 1156.mkv", {"episode": 1156})
        s.add(mf)
        await s.flush()
        s.add(_match(mf.id, episode_number=1, is_manual=True))
        await s.commit()

        assert await _heal_episode_number_drift(s) == 0
        m = await s.scalar(select(Match).where(Match.media_file_id == mf.id))
        assert m.episode_title == "Stored Title"
        assert m.episode_number == 1


async def test_no_parsed_episode_skipped() -> None:
    """No parsed episode at all → _reparse_missing_episodes' job, not ours."""
    Session = await _mem_sessionmaker()
    async with Session() as s:
        mf = _mf("/m/mystery.mkv", {"title": "Mystery"})  # no episode/absolute
        s.add(mf)
        await s.flush()
        s.add(_match(mf.id, episode_number=3))
        await s.commit()

        assert await _heal_episode_number_drift(s) == 0
        m = await s.scalar(select(Match).where(Match.media_file_id == mf.id))
        assert m.episode_title == "Stored Title"


async def test_movie_match_skipped() -> None:
    """Only tv_episode rows are in scope; a movie match is ignored."""
    Session = await _mem_sessionmaker()
    async with Session() as s:
        mf = _mf("/m/Film (2020).mkv", {"episode": 7}, media_type="tv")
        s.add(mf)
        await s.flush()
        s.add(_match(mf.id, episode_number=1, match_type="movie"))
        await s.commit()

        assert await _heal_episode_number_drift(s) == 0
