"""Re-identify re-derives a drifted episode number in-place (the One Piece
S23E1156→ep1 repair via the Re-identify button).

bulk_select_manual_match used to re-pin the stale stored episode_number. Now,
for a single-cour AniDB pick (no routing override) whose stored number matches
NEITHER parsed.episode NOR parsed.absolute_episode, it re-derives from the
file's own parsed number — touching ONLY episode_number (identity preserved).
The cour-routing helper is monkeypatched to None so these stay pure in-memory.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from kira.api.matches import BulkSelectManualPayload, bulk_select_manual_match
from kira.models import Base, Match, MediaFile


async def _mem():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _mf(path, parsed, media_type="anime"):
    return MediaFile(file_path=path, media_type=media_type, parsed_data=parsed, status="matched")


def _match(mf_id, *, provider="anidb", provider_id="69", episode_number, is_manual=True):
    return Match(
        media_file_id=mf_id, provider=provider, provider_id=provider_id,
        is_selected=True, is_manual=is_manual, match_type="tv_episode",
        episode_number=episode_number, season_number=23,
        episode_title="Stored", metadata_blob="{}", confidence=1.0,
        title="One Piece", series_name="One Piece",
    )


def _patch_no_cour(monkeypatch):
    # Single-cour series (One Piece AID 69) → no routing table. Patch the source
    # module since bulk_select_manual_match imports it locally at call time.
    import kira.matcher.cour_routing as cr
    async def _none(*a, **k):
        return None
    monkeypatch.setattr(cr, "build_cour_routing_table", _none)


async def test_reidentify_redrives_absolute_episode(monkeypatch):
    _patch_no_cour(monkeypatch)
    Session = await _mem()
    async with Session() as s:
        mf = _mf(r"Z:\media\anime\One Piece\Season 23\One Piece - S23E1156.mkv",
                 {"episode": 1156, "season": 23})
        s.add(mf)
        await s.flush()
        s.add(_match(mf.id, episode_number=1))        # the frozen 1156→1 row
        await s.commit()

        await bulk_select_manual_match(
            BulkSelectManualPayload(provider="anidb", provider_id="69",
                                    media_type="anime", title="One Piece",
                                    file_ids=[mf.id]),
            s,
        )
        m = await s.scalar(select(Match).where(
            Match.media_file_id == mf.id, Match.is_selected.is_(True)))
        assert m.episode_number == 1156      # re-derived (was 1)
        assert m.is_manual is True           # still a pin
        assert m.provider_id == "69"         # series identity preserved


async def test_reidentify_keeps_already_correct_number(monkeypatch):
    _patch_no_cour(monkeypatch)
    Session = await _mem()
    async with Session() as s:
        mf = _mf("/a/One Piece - S23E1156.mkv", {"episode": 1156, "season": 23})
        s.add(mf)
        await s.flush()
        s.add(_match(mf.id, episode_number=1156))     # already correct
        await s.commit()

        await bulk_select_manual_match(
            BulkSelectManualPayload(provider="anidb", provider_id="69",
                                    media_type="anime", title="One Piece",
                                    file_ids=[mf.id]),
            s,
        )
        m = await s.scalar(select(Match).where(
            Match.media_file_id == mf.id, Match.is_selected.is_(True)))
        assert m.episode_number == 1156      # not spuriously changed


async def test_reidentify_non_anidb_episode_untouched(monkeypatch):
    _patch_no_cour(monkeypatch)
    Session = await _mem()
    async with Session() as s:
        mf = _mf("/a/Show S01E05.mkv", {"episode": 5, "season": 1}, media_type="tv")
        s.add(mf)
        await s.flush()
        s.add(_match(mf.id, provider="tvdb", provider_id="123", episode_number=5))
        await s.commit()

        await bulk_select_manual_match(
            BulkSelectManualPayload(provider="tvdb", provider_id="123",
                                    media_type="tv", title="Show", file_ids=[mf.id]),
            s,
        )
        m = await s.scalar(select(Match).where(
            Match.media_file_id == mf.id, Match.is_selected.is_(True)))
        assert m.episode_number == 5         # redrive is AniDB-scoped → untouched
