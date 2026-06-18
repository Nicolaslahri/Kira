"""POST /files/verify-exist — report which tracked files vanished from disk, so
the duplicate UI can drop a ghost row before offering to delete a real copy
(the One Piece S00E04/S23E04 phantom-duplicate guard).

Safety crux mirrors prune: a file is reported missing ONLY on a confirmed
FileNotFoundError, and the call is report-only (no row deletion — the row is
pruned by the next scan)."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api.files import VerifyExistIn, verify_exist
from kira.models import MediaFile


async def _session(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'verify.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    await db.init_db()
    return sm


@pytest.mark.asyncio
async def test_verify_reports_only_confirmed_missing(tmp_path, monkeypatch):
    sm = await _session(tmp_path, monkeypatch)
    present = tmp_path / "present.mkv"; present.write_text("x")
    gone = tmp_path / "gone.mkv"          # never created → renamed/deleted off disk

    async with sm() as s:
        for p in (present, gone):
            s.add(MediaFile(file_path=str(p), media_type="anime", status="renamed",
                            parsed_data={"title": "X"}))
        await s.commit()
        rows = {mf.file_path: mf.id for mf in (await s.scalars(select(MediaFile))).all()}

    async with sm() as s:
        out = await verify_exist(VerifyExistIn(ids=[rows[str(present)], rows[str(gone)]]), s)
    assert out == {"missing": [rows[str(gone)]]}   # only the vanished file

    # Report-only: BOTH rows still exist (the scan prunes the ghost, not this call).
    async with sm() as s:
        assert len((await s.scalars(select(MediaFile.id))).all()) == 2


@pytest.mark.asyncio
async def test_verify_empty_and_unknown_ids(tmp_path, monkeypatch):
    sm = await _session(tmp_path, monkeypatch)
    async with sm() as s:
        assert await verify_exist(VerifyExistIn(ids=[]), s) == {"missing": []}
        # ids not in the DB are simply not reported (no crash, no false positive).
        assert await verify_exist(VerifyExistIn(ids=[9999]), s) == {"missing": []}
