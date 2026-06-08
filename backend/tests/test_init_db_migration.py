"""init_db migration resilience — the "breaks until DB reset" bug.

Schema (ADD COLUMN) must commit independently of, and before, data backfills.
If a backfill throws on a user's data it must NOT roll back the column adds —
otherwise the ORM selects a column the table lacks and every query 500s until a
manual reset. These tests pin that isolation.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db


async def _temp_engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'kira_test.db'}")
    return eng


async def test_backfill_failure_does_not_roll_back_columns(monkeypatch, tmp_path) -> None:
    eng = await _temp_engine(tmp_path)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", async_sessionmaker(eng, expire_on_commit=False))

    # Pre-create an OLD `matches` table that predates the migration columns —
    # so create_all (which skips existing tables) leaves the column adds to the
    # migration step, exactly like a real upgraded DB.
    async with eng.begin() as c:
        await c.execute(text(
            "CREATE TABLE matches (id INTEGER PRIMARY KEY, media_file_id INTEGER, "
            "provider TEXT, provider_id TEXT, match_type TEXT, confidence REAL)"
        ))

    # Make a data backfill explode — under the OLD single-transaction code this
    # rolled back the collection columns too.
    async def _boom(conn):
        raise RuntimeError("simulated bad data in backfill")
    monkeypatch.setattr(db, "_backfill_series_keys", _boom)

    # Must NOT raise — the failing backfill is non-fatal.
    await db.init_db()

    async with eng.begin() as c:
        cols = {r[1] for r in await c.execute(text("PRAGMA table_info(matches)"))}
    # The schema landed despite the backfill blowing up.
    assert "collection_id" in cols
    assert "collection_name" in cols
    assert "series_group_id" in cols
    assert "is_manual" in cols
    await eng.dispose()


async def test_init_db_is_idempotent(monkeypatch, tmp_path) -> None:
    eng = await _temp_engine(tmp_path)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", async_sessionmaker(eng, expire_on_commit=False))

    # Fresh DB: create_all builds everything; running twice must be clean.
    await db.init_db()
    await db.init_db()  # second boot — every migration is a no-op

    async with eng.begin() as c:
        cols = {r[1] for r in await c.execute(text("PRAGMA table_info(matches)"))}
    assert "collection_id" in cols and "collection_name" in cols
    await eng.dispose()


async def test_single_bad_column_does_not_block_others(monkeypatch, tmp_path) -> None:
    eng = await _temp_engine(tmp_path)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", async_sessionmaker(eng, expire_on_commit=False))

    real_ensure = db._ensure_column

    async def _flaky_ensure(conn, table, column, ddl):
        if column == "series_group_id":
            raise RuntimeError("simulated ALTER failure")
        return await real_ensure(conn, table, column, ddl)

    monkeypatch.setattr(db, "_ensure_column", _flaky_ensure)
    await db.init_db()  # must not raise; later columns still applied

    async with eng.begin() as c:
        cols = {r[1] for r in await c.execute(text("PRAGMA table_info(matches)"))}
    # series_group_id failed, but collection_* (added later) still landed.
    assert "collection_id" in cols
    assert "collection_name" in cols
    await eng.dispose()
