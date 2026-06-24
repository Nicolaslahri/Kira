"""After a library WIPE (media_files cleared but rename_history KEPT — the
NAS-disconnect prune case), a re-scan must RE-INGEST previously-renamed files.

The discovery drainer skips paths that are rename targets (so a renamed file
isn't re-discovered as a phantom duplicate). But it must skip them ONLY while
their MediaFile still exists; a wiped target (dangling / NULL media_file_id) has
to be re-ingested, not silently dropped forever (the Wednesday S02E01/E02 bug)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api import scans
from kira.models import MediaFile, RenameHistory


async def _session(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rt.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    await db.init_db()
    return sm


@pytest.mark.asyncio
async def test_active_rename_targets_skips_only_tracked(tmp_path, monkeypatch):
    sm = await _session(tmp_path, monkeypatch)
    async with sm() as s:
        mf = MediaFile(file_path=r"Z:\tv\tracked.mkv", media_type="tv",
                       status="matched", parsed_data={"title": "X"})
        s.add(mf)
        await s.commit()
        await s.refresh(mf)
        # tracked: live MediaFile → SKIP (phantom-duplicate prevention preserved).
        s.add(RenameHistory(media_file_id=mf.id, old_path=r"Z:\dl\tracked.mkv",
                            new_path=r"Z:\tv\tracked.mkv", operation="move"))
        # wiped: media_file_id NULL (the MediaFile was pruned) → must RE-INGEST.
        s.add(RenameHistory(media_file_id=None, old_path=r"Z:\dl\wiped.mkv",
                            new_path=r"Z:\tv\wiped.mkv", operation="move"))
        # dangling: media_file_id points to a now-deleted id → also re-ingest.
        s.add(RenameHistory(media_file_id=999999, old_path=r"Z:\dl\dangling.mkv",
                            new_path=r"Z:\tv\dangling.mkv", operation="move"))
        # undone: not an active target at all.
        s.add(RenameHistory(media_file_id=mf.id, old_path=r"Z:\dl\undone.mkv",
                            new_path=r"Z:\tv\undone.mkv", operation="move",
                            undone_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        await s.commit()

    async with sm() as s:
        targets = await scans._active_rename_targets(s)
    assert r"Z:\tv\tracked.mkv" in targets        # tracked → skip the phantom
    assert r"Z:\tv\wiped.mkv" not in targets       # wiped → re-ingest (THE FIX)
    assert r"Z:\tv\dangling.mkv" not in targets    # dangling → re-ingest
    assert r"Z:\tv\undone.mkv" not in targets      # undone → not an active target
