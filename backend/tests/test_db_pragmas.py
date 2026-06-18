"""SQLite connection PRAGMAs (audit finding R1).

Without WAL + busy_timeout, overlapping boot-time writers (self-heal, the
AniDB group backfill, a manual scan) hit ``database is locked`` instantly.
We test the exact function the connect-listener runs, against a real
file-based sqlite3 connection so WAL actually engages.
"""
from __future__ import annotations

import sqlite3

from kira.database import _apply_connection_pragmas


def test_connection_pragmas_applied(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    try:
        _apply_connection_pragmas(conn)
        cur = conn.cursor()
        assert cur.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert cur.execute("PRAGMA busy_timeout").fetchone()[0] == 15000
        assert cur.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert cur.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    finally:
        conn.close()


def test_pragmas_are_idempotent(tmp_path):
    """Re-running on the same DB (WAL already set in the header) must not error
    and must leave the settings intact — the listener fires on every connect."""
    db = str(tmp_path / "t.db")
    first = sqlite3.connect(db)
    try:
        _apply_connection_pragmas(first)
    finally:
        first.close()

    second = sqlite3.connect(db)
    try:
        _apply_connection_pragmas(second)
        cur = second.cursor()
        assert cur.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert cur.execute("PRAGMA busy_timeout").fetchone()[0] == 15000
    finally:
        second.close()
