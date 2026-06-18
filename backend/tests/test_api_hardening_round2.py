"""Round-2 audit fixes: reset_matches FK-detach (no 500 on legacy DBs) and the
history_counts COUNT refactor (no full-table materialize)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api.history import history_counts, _utcnow_naive
from kira.api.system import reset_matches
from kira.models import Match, MediaFile, RenameHistory


async def _fresh(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'round2.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    await db.init_db()
    return sm


@pytest.mark.asyncio
async def test_reset_matches_detaches_history_then_deletes(tmp_path, monkeypatch) -> None:
    # A RenameHistory row points at a Match. reset_matches must NULL that back-ref
    # FIRST (so a legacy RESTRICT FK + foreign_keys=ON can't 500 the reset), delete
    # the match, and leave the history row intact (just detached).
    sm = await _fresh(tmp_path, monkeypatch)
    async with sm() as s:
        mf = MediaFile(file_path="/x/a.mkv", parsed_data={"title": "A"}, media_type="movie", status="matched")
        s.add(mf)
        await s.flush()
        m = Match(media_file_id=mf.id, provider="tmdb", provider_id="1", match_type="movie",
                  confidence=0.9, title="A", is_selected=True)
        s.add(m)
        await s.flush()
        s.add(RenameHistory(media_file_id=mf.id, match_id=m.id,
                            old_path="/x/a.mkv", new_path="/x/A.mkv", operation="move"))
        await s.commit()

    async with sm() as s:
        res = await reset_matches(confirm="RESET", session=s)
    assert res["matches_deleted"] == 1

    async with sm() as s:
        assert (await s.scalar(select(func.count()).select_from(Match))) == 0
        rows = list(await s.scalars(select(RenameHistory)))
        assert len(rows) == 1, "history row must SURVIVE the reset"
        assert rows[0].match_id is None, "history.match_id must be detached, not orphaned"
        mf = await s.get(MediaFile, rows[0].media_file_id)
        assert mf.status == "pending"


@pytest.mark.asyncio
async def test_history_counts_buckets(tmp_path, monkeypatch) -> None:
    sm = await _fresh(tmp_path, monkeypatch)
    now = _utcnow_naive()
    async with sm() as s:
        s.add(RenameHistory(old_path="/a", new_path="/A", operation="move", created_at=now))
        s.add(RenameHistory(old_path="/b", new_path="/B", operation="move", created_at=now - timedelta(days=3)))
        s.add(RenameHistory(old_path="/c", new_path="/C", operation="move", created_at=now - timedelta(days=40)))
        await s.commit()
    async with sm() as s:
        counts = await history_counts(session=s)
    assert counts["all"] == 3
    assert counts["week"] == 2     # today + 3-days-ago
    assert counts["today"] == 1    # only the one from after midnight
