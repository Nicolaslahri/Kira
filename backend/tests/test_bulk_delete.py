"""POST /files/bulk-delete — the duplicate "keep best, delete the rest" flow.

One request deletes many files in a single confirmation. Each file is processed
independently so a locked/out-of-root file can't abort the batch; the response
reports deleted ids and per-file failures. Reuses the same managed-roots
containment guard as the single delete.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api.files import bulk_delete
from kira.models import Base, MediaFile, Setting


async def _setup(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bulk.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "SessionLocal", Session)
    return Session


@pytest.mark.asyncio
async def test_bulk_delete_removes_files_and_rows(tmp_path, monkeypatch):
    Session = await _setup(tmp_path, monkeypatch)
    root = tmp_path / "media"
    root.mkdir()
    ids: list[int] = []
    async with Session() as s:
        s.add(Setting(key="paths.library_root", value=str(root)))
        for i in range(3):
            p = root / f"ep{i}.mkv"
            p.write_bytes(b"x")
            mf = MediaFile(file_path=str(p), status="matched")
            s.add(mf)
            await s.flush()
            ids.append(mf.id)
        await s.commit()

    async with Session() as s:
        res = await bulk_delete({"file_ids": ids}, s)

    assert sorted(res["deleted"]) == sorted(ids)
    assert res["failed"] == []
    assert res["count"] == 3
    # Files gone from disk AND rows gone from the DB.
    for i in range(3):
        assert not (root / f"ep{i}.mkv").exists()
    async with Session() as s:
        remaining = [await s.get(MediaFile, fid) for fid in ids]
    assert all(r is None for r in remaining)


@pytest.mark.asyncio
async def test_bulk_delete_partial_failure_outside_root(tmp_path, monkeypatch):
    Session = await _setup(tmp_path, monkeypatch)
    root = tmp_path / "media"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    good = root / "keep_under_root.mkv"
    good.write_bytes(b"x")
    bad = outside / "outside.mkv"
    bad.write_bytes(b"x")
    async with Session() as s:
        s.add(Setting(key="paths.library_root", value=str(root)))
        gmf = MediaFile(file_path=str(good), status="matched")
        bmf = MediaFile(file_path=str(bad), status="matched")
        s.add_all([gmf, bmf])
        await s.flush()
        good_id, bad_id = gmf.id, bmf.id
        await s.commit()

    async with Session() as s:
        res = await bulk_delete({"file_ids": [good_id, bad_id]}, s)

    assert res["deleted"] == [good_id]
    assert len(res["failed"]) == 1 and res["failed"][0]["id"] == bad_id
    # The in-root file is gone; the out-of-root file is UNTOUCHED on disk + DB.
    assert not good.exists()
    assert bad.exists()
    async with Session() as s:
        assert await s.get(MediaFile, bad_id) is not None
        assert await s.get(MediaFile, good_id) is None


@pytest.mark.asyncio
async def test_bulk_delete_missing_id_counts_as_success(tmp_path, monkeypatch):
    Session = await _setup(tmp_path, monkeypatch)
    root = tmp_path / "media"
    root.mkdir()
    async with Session() as s:
        s.add(Setting(key="paths.library_root", value=str(root)))
        await s.commit()
    # Id that was never created (e.g. already deleted in a prior partial run).
    async with Session() as s:
        res = await bulk_delete({"file_ids": [9999]}, s)
    assert res["deleted"] == [9999]
    assert res["failed"] == []


@pytest.mark.asyncio
async def test_bulk_delete_rejects_empty_body(tmp_path, monkeypatch):
    Session = await _setup(tmp_path, monkeypatch)
    from fastapi import HTTPException
    async with Session() as s:
        with pytest.raises(HTTPException):
            await bulk_delete({"file_ids": []}, s)
