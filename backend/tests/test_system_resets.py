"""Tiered resets — history-only, matches-only, and the factory wipe."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api.system import reset_database, reset_history, reset_matches
from kira.models import Match, MediaFile, RenameHistory, Setting


async def _seeded(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'resets.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    await db.init_db()
    async with sm() as s:
        mf = MediaFile(file_path=str(tmp_path / "a.mkv"), parsed_data={"original_filename": "a.mkv", "media_type": "movie", "title": "A"}, media_type="movie", status="matched")
        s.add(mf)
        await s.flush()
        s.add(Match(media_file_id=mf.id, provider="tmdb", provider_id="1", match_type="movie", confidence=0.9, title="A", is_selected=True))
        s.add(RenameHistory(media_file_id=mf.id, old_path="x", new_path="y", operation="move"))
        s.add(Setting(key="naming.profile", value="Plex"))
        await s.commit()
    return sm


@pytest.mark.asyncio
async def test_reset_history_only(tmp_path, monkeypatch):
    sm = await _seeded(tmp_path, monkeypatch)
    async with sm() as s:
        r = await reset_history(confirm="RESET", session=s)
    assert r["history_deleted"] == 1
    async with sm() as s:
        assert (await s.scalars(select(RenameHistory))).first() is None
        assert (await s.scalars(select(Match))).first() is not None       # survives
        assert (await s.scalars(select(MediaFile))).first() is not None   # survives


@pytest.mark.asyncio
async def test_reset_matches_flips_files_to_pending(tmp_path, monkeypatch):
    sm = await _seeded(tmp_path, monkeypatch)
    async with sm() as s:
        r = await reset_matches(confirm="RESET", session=s)
    assert r["matches_deleted"] == 1
    async with sm() as s:
        assert (await s.scalars(select(Match))).first() is None
        mf = (await s.scalars(select(MediaFile))).first()
        assert mf is not None and mf.status == "pending"                  # re-matchable
        assert (await s.scalars(select(RenameHistory))).first() is not None  # survives


@pytest.mark.asyncio
async def test_factory_reset_wipes_settings_and_account_cache(tmp_path, monkeypatch):
    from kira.api import auth as auth_mod
    sm = await _seeded(tmp_path, monkeypatch)
    monkeypatch.setattr(auth_mod, "_account_cache", ("nico", "hash"))
    async with sm() as s:
        await reset_database(confirm="RESET", wipe_settings=True, session=s)
    async with sm() as s:
        assert (await s.scalars(select(MediaFile))).first() is None
        keys = [row.key for row in (await s.scalars(select(Setting)))]
        assert "naming.profile" not in keys
    # Account cache cleared → next /auth/status reports the sign-up window.
    assert auth_mod._account_cache is None


@pytest.mark.asyncio
async def test_resets_demand_confirmation(tmp_path, monkeypatch):
    sm = await _seeded(tmp_path, monkeypatch)
    for fn in (reset_history, reset_matches):
        with pytest.raises(HTTPException) as ei:
            async with sm() as s:
                await fn(confirm="nope", session=s)
        assert ei.value.status_code == 400
    # Nothing was deleted by the refused calls.
    async with sm() as s:
        assert (await s.scalars(select(Match))).first() is not None
        assert (await s.scalars(select(RenameHistory))).first() is not None
