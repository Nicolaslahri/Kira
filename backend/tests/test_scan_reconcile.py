"""Orphaned-scan reconciliation on boot — a restart must settle mid-flight
scan rows so they don't show as perpetual "scanning" (and the frontend's
refresh-resume can't latch onto a dead scan)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from kira.api import scans as scans_mod
from kira.models import Base, MediaFile, Scan


async def _mem_sessionmaker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def test_reconcile_flips_only_inflight_rows(monkeypatch) -> None:
    Session = await _mem_sessionmaker()
    monkeypatch.setattr(scans_mod, "SessionLocal", Session)

    async with Session() as s:
        s.add_all([
            Scan(root_path="/a", status="matching"),     # orphaned → flip
            Scan(root_path="/b", status="scanning"),      # orphaned → flip
            Scan(root_path="/c", status="pending"),       # orphaned → flip
            Scan(root_path="/d", status="completed"),     # terminal → leave
        ])
        # Files left mid-scan: "matching"/"parsing" must reset; terminal stays.
        s.add_all([
            MediaFile(file_path="/m/a.mkv", status="matching"),
            MediaFile(file_path="/m/b.mkv", status="parsing"),
            MediaFile(file_path="/m/c.mkv", status="matched"),
            MediaFile(file_path="/m/d.mkv", status="no_match"),
        ])
        await s.commit()

    n_scans, n_files = await scans_mod.reconcile_orphaned_scans()
    assert n_scans == 3
    assert n_files == 2  # the matching + parsing files

    async with Session() as s:
        from sqlalchemy import select
        by_root = {r.root_path: r for r in await s.scalars(select(Scan))}
        by_path = {f.file_path: f for f in await s.scalars(select(MediaFile))}
    assert by_root["/a"].status.startswith("failed")
    assert by_root["/a"].completed_at is not None
    assert by_root["/b"].status.startswith("failed")
    assert by_root["/c"].status.startswith("failed")
    assert by_root["/d"].status == "completed"   # untouched
    # Stuck files reset → covers stop animating; terminal files untouched.
    assert by_path["/m/a.mkv"].status == "discovered"
    assert by_path["/m/b.mkv"].status == "discovered"
    assert by_path["/m/c.mkv"].status == "matched"
    assert by_path["/m/d.mkv"].status == "no_match"


async def test_reconcile_noop_when_nothing_inflight(monkeypatch) -> None:
    Session = await _mem_sessionmaker()
    monkeypatch.setattr(scans_mod, "SessionLocal", Session)
    async with Session() as s:
        s.add(Scan(root_path="/done", status="completed"))
        await s.commit()
    assert await scans_mod.reconcile_orphaned_scans() == (0, 0)


async def test_reconcile_skips_completed_with_status_left(monkeypatch) -> None:
    # A row already 'failed' (terminal) with completed_at set is left alone.
    Session = await _mem_sessionmaker()
    monkeypatch.setattr(scans_mod, "SessionLocal", Session)
    from datetime import datetime
    async with Session() as s:
        s.add(Scan(root_path="/x", status="failed: earlier", completed_at=datetime(2020, 1, 1)))
        await s.commit()
    assert await scans_mod.reconcile_orphaned_scans() == (0, 0)


async def test_match_singleton_movie_does_not_crash(monkeypatch) -> None:
    # Regression: _match_singleton must bind `ep_num` for NON-tv matches too.
    # A movie top-match skips the tv_episode episode-title branch, so `ep_num`
    # was left unbound and `resolve_canonical_season(..., episode=ep_num)` raised
    # "cannot access local variable 'ep_num'..." on EVERY movie scan.
    from kira.models import Match
    from sqlalchemy import select

    Session = await _mem_sessionmaker()
    monkeypatch.setattr(scans_mod, "SessionLocal", Session)

    async with Session() as s:
        mf = MediaFile(
            file_path="/m/Inception (2010).mkv",
            parsed_data={"original_filename": "Inception (2010).mkv",
                         "media_type": "movie", "title": "Inception", "year": 2010},
            media_type="movie", status="discovered",
        )
        s.add(mf)
        await s.flush()
        fid = mf.id
        await s.commit()

    class _Scored:
        provider = "tmdb"; provider_id = "27205"; match_type = "movie"
        confidence = 1.0; title = "Inception"; year = 2010
        poster_url = None; overview = None; raw = None

    class _Engine:
        registry = object()
        async def match(self, parsed, limit=5):
            return [_Scored()]

    async def _meta(*a, **k):
        return {}

    async def _gid(*a, **k):
        return None

    monkeypatch.setattr(scans_mod, "fetch_match_metadata", _meta)
    monkeypatch.setattr(scans_mod, "compute_series_group_id", _gid)

    async with Session() as s:
        # Must NOT raise (the bug raised UnboundLocalError on ep_num here).
        await scans_mod._match_singleton(s, _Engine(), fid)
        await s.commit()

    async with Session() as s:
        rows = list(await s.scalars(select(Match).where(Match.media_file_id == fid)))
    assert len(rows) == 1
    assert rows[0].match_type == "movie"
    assert rows[0].season_number is None        # movie → no season, no ScudLee
    assert rows[0].episode_number is None
