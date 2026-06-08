"""media_type-from-provider heal (_HEAL_VERSION 25).

An AniDB match is authoritative for "this is anime" (AniDB only catalogues
anime). The parser only types files "anime" by /anime/ path or fansub group,
so an AniDB-matched copy scanned from a release-named folder came out "tv" and
landed in the TV Series group. The heal corrects media_type and recomputes the
series key so it re-clusters under its anime identity.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from kira.api.matches import _apply_media_type_for_manual_pick, _heal_media_type_from_provider
from kira.models import Base, MediaFile, Match
from kira.parser import parse_filename


async def _mem_sessionmaker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _mf(name: str, parent: str, media_type_override: str | None = None) -> MediaFile:
    """Build a MediaFile with REAL parsed_data (so the heal's ParsedFile rebuild
    + key recompute behaves exactly as in production)."""
    pd = parse_filename(name, parent_path=parent).to_dict()
    return MediaFile(file_path=f"{parent}/{name}", media_type=media_type_override or pd["media_type"],
                     parsed_data=pd, status="matched")


def _match(mf_id: int, provider: str, mtype: str = "tv_episode") -> Match:
    return Match(media_file_id=mf_id, provider=provider, provider_id="123",
                 is_selected=True, is_manual=False, match_type=mtype, confidence=1.0)


async def test_anidb_match_flips_tv_to_anime_and_rekeys() -> None:
    Session = await _mem_sessionmaker()
    async with Session() as s:
        # Scanned from a release-named download folder → parser typed it "tv".
        mf = _mf("Kanojo.Okarishimasu.2023.S03E12.1080p.WEB-DL.mkv", "Z:/downloads/complete")
        assert mf.media_type == "tv"  # precondition: parser said tv (no /anime/)
        s.add(mf)
        await s.flush()
        s.add(_match(mf.id, "anidb"))
        await s.commit()

        n = await _heal_media_type_from_provider(s)
        await s.commit()
        assert n == 1

        fixed = await s.scalar(select(MediaFile).where(MediaFile.id == mf.id))
        assert fixed.media_type == "anime"
        assert (fixed.series_key or "").startswith("anime|")  # re-clustered under anime


async def test_tvdb_match_left_alone() -> None:
    """Only AniDB is anime-authoritative; a TVDB 'tv' match must not be touched."""
    Session = await _mem_sessionmaker()
    async with Session() as s:
        mf = _mf("Loki.S01E01.1080p.WEB-DL.mkv", "Z:/media/tv/Loki/Season 1")
        s.add(mf)
        await s.flush()
        s.add(_match(mf.id, "tvdb"))
        await s.commit()
        assert await _heal_media_type_from_provider(s) == 0
        unchanged = await s.scalar(select(MediaFile).where(MediaFile.id == mf.id))
        assert unchanged.media_type == "tv"


async def test_already_anime_not_recounted() -> None:
    Session = await _mem_sessionmaker()
    async with Session() as s:
        mf = _mf("Kanojo.Okarishimasu.2023.S03E01.mkv", "Z:/media/anime/Rent-a-Girlfriend/Season 3")
        assert mf.media_type == "anime"  # /anime/ path already typed it anime
        s.add(mf)
        await s.flush()
        s.add(_match(mf.id, "anidb"))
        await s.commit()
        assert await _heal_media_type_from_provider(s) == 0


# ── manual-pick media_type reconciliation (sync helper) ──────────────────────
def test_manual_pick_anidb_forces_anime_even_if_payload_says_tv() -> None:
    mf = _mf("Kanojo.Okarishimasu.2023.S03E12.WEB-DL.mkv", "Z:/usenet/complete/tv")
    assert mf.media_type == "tv"
    _apply_media_type_for_manual_pick(mf, "anidb", "tv")  # AniDB is anime-only → wins
    assert mf.media_type == "anime"
    assert (mf.series_key or "").startswith("anime|")


def test_manual_pick_honors_chosen_result_type() -> None:
    mf = _mf("Some.Thing.S01E01.WEB-DL.mkv", "Z:/downloads/complete")  # parser → tv
    _apply_media_type_for_manual_pick(mf, "tmdb", "movie")  # user picked a movie
    assert mf.media_type == "movie"


def test_manual_pick_noop_when_unchanged() -> None:
    mf = _mf("Loki.S01E01.1080p.WEB-DL.mkv", "Z:/media/tv/Loki/Season 1")  # tv
    before = mf.series_key
    _apply_media_type_for_manual_pick(mf, "tvdb", "tv")  # already tv
    assert mf.media_type == "tv"
    assert mf.series_key == before  # unchanged → no key churn
