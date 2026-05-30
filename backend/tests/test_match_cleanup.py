"""Regression test for the auto-heal FK crash.

Reproduces an OLD database whose ``rename_history.match_id`` FK predates the
``ON DELETE SET NULL`` clause (so it's the default RESTRICT), with
``PRAGMA foreign_keys = ON`` like production. Proves:
  1. a raw ``DELETE FROM matches`` of a previously-renamed file's match
     raises (the reported `auto_heal: file N failed: IntegrityError`), and
  2. ``detach_and_delete_matches`` succeeds and leaves the history row intact
     with its ``match_id`` nulled.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from kira.api.match_cleanup import detach_and_delete_matches

# Minimal tables with the column names the ORM models use. The rename_history
# FK is the OLD shape: `REFERENCES matches(id)` with NO on-delete action →
# SQLite defaults to RESTRICT.
_OLD_SCHEMA = [
    "CREATE TABLE media_files (id INTEGER PRIMARY KEY, file_path TEXT UNIQUE)",
    "CREATE TABLE matches (id INTEGER PRIMARY KEY, "
    "media_file_id INTEGER REFERENCES media_files(id), is_manual BOOLEAN DEFAULT 0)",
    "CREATE TABLE rename_history (id INTEGER PRIMARY KEY, "
    "match_id INTEGER REFERENCES matches(id), old_path TEXT, new_path TEXT)",
]


async def _seed_old_schema_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared in-memory DB across the test
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_connection, _rec):  # noqa: ANN001
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        cur.close()

    async with engine.begin() as conn:
        for ddl in _OLD_SCHEMA:
            await conn.execute(text(ddl))
        await conn.execute(text("INSERT INTO media_files (id, file_path) VALUES (1, 'f1')"))
        await conn.execute(
            text("INSERT INTO matches (id, media_file_id, is_manual) VALUES (10, 1, 0)")
        )
        # A past rename points at match 10 — this is what makes a raw delete fail.
        await conn.execute(
            text("INSERT INTO rename_history (id, match_id, old_path, new_path) "
                 "VALUES (100, 10, 'a', 'b')")
        )
    return engine


async def test_raw_delete_blocked_by_restrict_fk() -> None:
    """Confirms the bug: a raw delete trips the RESTRICT FK."""
    engine = await _seed_old_schema_engine()
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            with pytest.raises(IntegrityError):
                await s.execute(text("DELETE FROM matches WHERE id = 10"))
                await s.commit()
    finally:
        await engine.dispose()


async def test_detach_and_delete_succeeds_on_restrict_fk() -> None:
    """The helper deletes the match AND preserves the history row (nulled)."""
    engine = await _seed_old_schema_engine()
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            n = await detach_and_delete_matches(s, media_file_id=1)
            await s.commit()
            assert n == 1
            assert (await s.execute(text("SELECT COUNT(*) FROM matches"))).scalar() == 0
            # History survives; its dangling match_id is nulled.
            row = (await s.execute(
                text("SELECT match_id FROM rename_history WHERE id = 100")
            )).scalar()
            assert row is None
    finally:
        await engine.dispose()


async def test_detach_and_delete_by_match_ids() -> None:
    engine = await _seed_old_schema_engine()
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            n = await detach_and_delete_matches(s, match_ids=[10])
            await s.commit()
            assert n == 1
            assert (await s.execute(text("SELECT COUNT(*) FROM matches"))).scalar() == 0
    finally:
        await engine.dispose()


async def test_detach_and_delete_empty_is_noop() -> None:
    engine = await _seed_old_schema_engine()
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            assert await detach_and_delete_matches(s, match_ids=[]) == 0
            # Nothing deleted.
            assert (await s.execute(text("SELECT COUNT(*) FROM matches"))).scalar() == 1
    finally:
        await engine.dispose()
