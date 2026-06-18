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
