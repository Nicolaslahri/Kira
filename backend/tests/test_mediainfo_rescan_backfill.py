"""Rescan tech-tag backfill (`_ids_missing_tech_tags`).

The scan-tail enrich used to cover ONLY this scan's newly-discovered files, so a
plain rescan that turned up nothing new left the pass with an empty set and it
silently no-op'd — which reads to the user as "the tech-tag scan didn't start".
The helper widens the set to any file whose container has never been read
(no `mi_stamp`), and is self-limiting: once a file is inspected it carries a
stamp and drops out, so it's never re-read on the next scan.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api.scans import _ids_missing_tech_tags
from kira.models import MediaFile


async def _sm(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mi.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    await db.init_db()
    return sm


@pytest.mark.asyncio
async def test_selects_only_files_missing_mi_stamp(tmp_path, monkeypatch):
    sm = await _sm(tmp_path, monkeypatch)
    async with sm() as s:
        # (1) already read — carries an mi_stamp → EXCLUDED (won't be re-read).
        read = MediaFile(
            file_path=str(tmp_path / "read.mkv"),
            parsed_data={"original_filename": "read.mkv", "mi_stamp": [123, 456], "mi_raw": {"width": 1920}},
            media_type="movie", status="matched",
        )
        # (2) never read — no mi_stamp → INCLUDED.
        unread = MediaFile(
            file_path=str(tmp_path / "unread.mkv"),
            parsed_data={"original_filename": "unread.mkv"},
            media_type="movie", status="matched",
        )
        # (3) empty parsed_data — json_extract is null → INCLUDED (the enrich
        #     pass skips it harmlessly: nothing to enrich into).
        empty = MediaFile(
            file_path=str(tmp_path / "empty.mkv"),
            parsed_data={}, media_type="movie", status="pending",
        )
        s.add_all([read, unread, empty])
        await s.commit()
        read_id, unread_id, empty_id = read.id, unread.id, empty.id

    async with sm() as s:
        missing = set(await _ids_missing_tech_tags(s))

    assert unread_id in missing, "a never-read file must be picked up by a rescan"
    assert empty_id in missing
    assert read_id not in missing, "an already-stamped file must NOT be re-read"


@pytest.mark.asyncio
async def test_limit_caps_the_result(tmp_path, monkeypatch):
    sm = await _sm(tmp_path, monkeypatch)
    async with sm() as s:
        for i in range(5):
            s.add(MediaFile(
                file_path=str(tmp_path / f"f{i}.mkv"),
                parsed_data={"original_filename": f"f{i}.mkv"},
                media_type="movie", status="pending",
            ))
        await s.commit()
    async with sm() as s:
        capped = await _ids_missing_tech_tags(s, limit=3)
    assert len(capped) == 3, "the bound must cap a huge untagged library per scan"
