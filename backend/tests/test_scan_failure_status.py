"""A failed scan records its REASON (not a bare 'failed') on a FRESH session.

Regression guard for the boot-heal contention bug: a scan whose commit lost
SQLite's single write-lock (busy_timeout) raised `database is locked`, which
poisoned the worker session so the in-handler status write raised AGAIN
(PendingRollbackError) and escaped — surfacing a bare 'failed' (or a scan stuck
at 'scanning'). `_scan_worker_locked` now records the real reason on a fresh
session, and the inner handler rolls back + re-raises instead of touching the
poisoned one."""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api import scans
from kira.models import Scan


async def _fresh_db(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scanfail.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    monkeypatch.setattr(scans, "SessionLocal", sm)
    await db.init_db()
    return sm


async def test_worker_exception_records_real_reason(tmp_path, monkeypatch):
    sm = await _fresh_db(tmp_path, monkeypatch)
    async with sm() as s:
        scan = Scan(root_path="Z:\\", status="scanning", source="manual")
        s.add(scan)
        await s.commit()
        await s.refresh(scan)
        sid = scan.id

    async def _boom(scan_id, root_paths):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(scans, "_scan_worker", _boom)
    # _scan_worker_locked re-raises after recording (spawn_tracked logs it).
    with pytest.raises(RuntimeError):
        await scans._scan_worker_locked(sid, ["Z:\\"])

    async with sm() as s:
        row = await s.get(Scan, sid)
    assert row.status.startswith("failed:"), f"expected a reasoned failure, got {row.status!r}"
    assert "database is locked" in row.status
    assert row.completed_at is not None, "a failed scan must be stamped completed_at, not left open"

async def test_worker_exception_during_matching_phase_marks_failed(tmp_path, monkeypatch):
    """The critical gap: an exception escaping during Phase 2 (row already at
    'matching') used to leave the scan running forever — dead task, freed
    locks — so the next scan started fine and the DB held TWO live-looking
    rows (the reported 'scan runs 2 times'). The gate must cover every
    non-terminal status, not just 'scanning'."""
    from kira.models import MediaFile

    sm = await _fresh_db(tmp_path, monkeypatch)
    async with sm() as s:
        scan = Scan(root_path="Z:\\", status="scanning", source="manual")
        s.add(scan)
        # A file from an EARLIER scan left mid-match by the crash: must be
        # reset to 'discovered' (the resume query can't see 'matching').
        s.add(MediaFile(
            file_path="Z:\old\Show.S01E01.mkv", file_size=1,
            media_type="tv", status="matching", parsed_data={},
        ))
        await s.commit()
        await s.refresh(scan)
        sid = scan.id

    async def _boom_matching(scan_id, root_paths):
        # Simulate Phase 2: the worker has already flipped the row to
        # 'matching' when the exception escapes.
        async with sm() as s:
            row = await s.get(Scan, scan_id)
            row.status = "matching"
            await s.commit()
        raise RuntimeError("database is locked")

    monkeypatch.setattr(scans, "_scan_worker", _boom_matching)
    with pytest.raises(RuntimeError):
        await scans._scan_worker_locked(sid, ["Z:\\"])

    async with sm() as s:
        row = await s.get(Scan, sid)
        from sqlalchemy import select as _select
        stranded = (await s.scalars(
            _select(MediaFile).where(MediaFile.status == "matching")
        )).all()
    assert row.status.startswith("failed:"), (
        f"matching-phase crash must mark the scan failed, got {row.status!r}")
    assert row.completed_at is not None
    assert not stranded, "mid-'matching' files must be reset to 'discovered'"


async def test_cancelled_scan_resets_stranded_matching_files(tmp_path, monkeypatch):
    """Stop used to settle only the Scan row: files the cancelled cluster left
    at 'matching' stayed in the spinner forever (invisible to the next scan's
    resume query) until a full container restart."""
    import asyncio as _asyncio

    from kira.models import MediaFile

    sm = await _fresh_db(tmp_path, monkeypatch)
    async with sm() as s:
        scan = Scan(root_path="Z:\\", status="matching", source="manual")
        s.add(scan)
        s.add(MediaFile(
            file_path="Z:\Show.S01E02.mkv", file_size=1,
            media_type="tv", status="matching", parsed_data={},
        ))
        await s.commit()
        await s.refresh(scan)
        sid = scan.id

    async def _cancelled(scan_id, root_paths):
        raise _asyncio.CancelledError()

    monkeypatch.setattr(scans, "_scan_worker", _cancelled)
    with pytest.raises(_asyncio.CancelledError):
        await scans._scan_worker_locked(sid, ["Z:\\"])

    async with sm() as s:
        row = await s.get(Scan, sid)
        from sqlalchemy import select as _select
        stranded = (await s.scalars(
            _select(MediaFile).where(MediaFile.status == "matching")
        )).all()
    assert row.status == "cancelled"
    assert row.completed_at is not None
    assert not stranded, "cancel must reset mid-'matching' files to 'discovered'"
