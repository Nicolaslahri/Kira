from collections.abc import AsyncIterator
import json
import logging
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from kira.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# H9: SQLite ships with foreign-key enforcement OFF by default — including
# ON DELETE SET NULL. Without this listener, `RenameHistory.media_file_id`
# stays pointing at a deleted MediaFile.id (the constraint is a no-op).
# Enable the pragma on every new connection so the orphan-prevention rule
# we added to the model actually fires.
if "sqlite" in settings.database_url:
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fk_pragma(dbapi_connection, _conn_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()


# PB-5: slow-query logger. SQLAlchemy event hooks let us instrument every
# query without touching call sites. Threshold = 50ms — Review-page hot
# paths should complete well under this; anything slower is a perf bug
# (most likely a missing index or N+1). Logs are JSON so they pipe into
# `jq` or any structured-log sink. Threshold tunable via env var; default
# is intentionally conservative for self-hosted single-user workload.
_SLOW_QUERY_MS = float(__import__("os").environ.get("KIRA_SLOW_QUERY_MS", "50"))
_query_log = logging.getLogger("kira.db.slow")


def _install_slow_query_hook() -> None:
    """Idempotent — safe to call multiple times. Each engine event listener
    is keyed; SQLAlchemy de-dups by callable identity."""
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "before_cursor_execute")
    def _before(conn, cursor, statement, params, context, executemany):
        context._kira_started = time.monotonic()

    @event.listens_for(Engine, "after_cursor_execute")
    def _after(conn, cursor, statement, params, context, executemany):
        started = getattr(context, "_kira_started", None)
        if started is None:
            return
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms < _SLOW_QUERY_MS:
            return
        # Trim long statements + collapse whitespace for log readability.
        snippet = " ".join(statement.split())[:300]
        _query_log.warning(json.dumps({
            "evt": "slow_query",
            "elapsed_ms": int(elapsed_ms),
            "executemany": bool(executemany),
            "sql": snippet,
        }))


_install_slow_query_hook()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    # Dev convenience: create tables on startup. Production uses Alembic.
    from kira import models  # noqa: F401 — register mappers

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight in-place migrations for columns added after first ship.
        # SQLite is forgiving: ADD COLUMN with no default is fast and safe.
        await _ensure_column(conn, "media_files", "series_key", "VARCHAR")
        await _ensure_column(conn, "matches", "series_group_id", "VARCHAR")
        # PB-4: scan ETA — populated by scan worker at Phase 1 → Phase 2
        # transition so the frontend can render a real % + ETA banner
        # instead of "watch the file count climb forever".
        await _ensure_column(conn, "scans", "estimated_total", "INTEGER")
        # `is_manual` — user-pinned matches survive auto-heal/rematch.
        # SQLite stores Bool as INTEGER (0/1). Default 0 = matcher-picked.
        await _ensure_column(conn, "matches", "is_manual", "BOOLEAN DEFAULT 0")
        # `variant_key` — disambiguates same-episode files in different
        # flavors (audio language, edition, bit depth). See model docstring.
        # Default null means "no variant"; backfill happens lazily as files
        # are re-scanned or auto-healed.
        await _ensure_column(conn, "media_files", "variant_key", "VARCHAR")
        # Tier 1.2: `parent_id` on rename_history — links sidecar rows
        # (subtitles, etc.) back to their parent video's history row so
        # cascading undo restores the whole bundle together. Pre-1.2
        # rows stay NULL (interpreted as "standalone"), perfectly
        # backward-compatible.
        await _ensure_column(conn, "rename_history", "parent_id", "INTEGER")
        # Idempotent backfills: cheap when there's nothing to fix, so we run
        # them every boot rather than gating on "just-added the column".
        await _backfill_series_keys(conn)
        await _backfill_variant_keys(conn)
        await _backfill_series_group_ids(conn)
        # Autopsy 6: ensure the multi-worker scan lock row exists. Value
        # is an integer Unix-timestamp; 0 = idle, nonzero = scan started
        # at that timestamp. INSERT OR IGNORE — first writer wins, never
        # clobber a live lock held by another worker process.
        await _ensure_scan_lock_row(conn)
        # PB-5: hot-path indexes for Review-page filter queries. At 100k
        # files, unindexed `WHERE status=?` is a full table scan; the
        # composite (status, media_type) covers the filter-pill flow.
        # All idempotent — CREATE INDEX IF NOT EXISTS is a no-op when
        # the index already exists. Followed by ANALYZE so the query
        # planner actually picks the new indexes (without ANALYZE, fresh
        # indexes stay invisible to SQLite's planner until DB reopen).
        await _create_perf_indexes(conn)


async def _ensure_scan_lock_row(conn) -> None:
    """Create the multi-worker scan lock row if missing.

    Stored as `settings.value = 0` (idle). When a scan is claimed, the
    CAS in `create_scan` flips it to the start timestamp (Unix epoch
    seconds). Releasing flips it back to 0. A stale lock — left over
    from a worker that crashed mid-scan — auto-expires after
    MAX_SCAN_AGE_SEC because the CAS also accepts `value < (now - MAX)`.

    Never overwrites an existing value — that would break a scan that's
    currently running in another worker process.
    """
    from sqlalchemy import text
    await conn.execute(text(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('system.scan_running', '0')"
    ))


async def _create_perf_indexes(conn) -> None:
    """PB-5: install indexes on hot-path filter columns. Cheap (one b-tree
    per index) and the workload is read-heavy — scans happen weekly,
    Review-page renders happen constantly. Read-optimize."""
    from sqlalchemy import text
    statements = [
        "CREATE INDEX IF NOT EXISTS ix_mf_status ON media_files(status)",
        "CREATE INDEX IF NOT EXISTS ix_mf_media_type ON media_files(media_type)",
        # Composite for filter-pill queries: status + media_type together.
        "CREATE INDEX IF NOT EXISTS ix_mf_status_mediatype ON media_files(status, media_type)",
        # Series-key clustering queries — common in the auto-heal path.
        "CREATE INDEX IF NOT EXISTS ix_mf_series_key_status ON media_files(series_key, status)",
        # Match join: selectinload(MediaFile.matches) needs the FK side
        # indexed for fast lookup, plus is_selected covers the "give me
        # the picked match" hot query.
        "CREATE INDEX IF NOT EXISTS ix_match_mfid_selected ON matches(media_file_id, is_selected)",
    ]
    for stmt in statements:
        await conn.execute(text(stmt))
    # ANALYZE rebuilds SQLite's sqlite_stat1 table so the query planner
    # learns about the new indexes. Without it, the planner may keep
    # using sequential scans until the database is closed and reopened.
    await conn.execute(text("ANALYZE"))


async def _ensure_column(conn, table: str, column: str, ddl_type: str) -> bool:
    """Idempotent ADD COLUMN. SQLite doesn't support ADD COLUMN IF NOT EXISTS,
    so we inspect PRAGMA table_info first. Returns True if the column was added."""
    from sqlalchemy import text
    rows = list(await conn.execute(text(f"PRAGMA table_info({table})")))
    existing = {row[1] for row in rows}  # row[1] is the column name
    if column not in existing:
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
        await conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_{column} ON {table}({column})"))
        return True
    return False


async def _backfill_series_group_ids(conn) -> None:
    """Populate Match.series_group_id for any rows missing it.

    Two passes for cost:
      1. **Cheap pass** — TMDB / TVDB / MusicBrainz already have one ID per
         franchise, so `{provider}:{provider_id}` is correct. Pure SQL.
      2. **AniDB pass** — sequel chains require the relations API (rate-
         limited, 1 req / 4s). We do this in a background task so startup
         isn't blocked. Cache hits are instant; only fresh AIDs cost time.
    """
    from sqlalchemy import text

    # Pass 1: trivial echo for providers with umbrella IDs.
    await conn.execute(text("""
        UPDATE matches
           SET series_group_id = provider || ':' || provider_id
         WHERE series_group_id IS NULL
           AND provider IN ('tmdb', 'tvdb', 'musicbrainz')
    """))

    # Pass 2: AniDB. Defer to a background task so the relations walk doesn't
    # block app startup (each fresh AID costs 4s of rate-limited HTTP).
    pending = list(await conn.execute(text(
        "SELECT DISTINCT provider_id FROM matches "
        "WHERE provider='anidb' AND series_group_id IS NULL"
    )))
    if pending:
        import asyncio
        asyncio.create_task(_backfill_anidb_groups_async([r[0] for r in pending]))


async def _backfill_anidb_groups_async(aids: list[str]) -> None:
    """Resolve each AID's franchise group + write series_group_id for every
    Match row in that group. Runs in the background after app startup."""
    import httpx
    from sqlalchemy import text
    from kira.matcher.engine import registry_from_settings

    seen: set[str] = set()
    async with httpx.AsyncClient() as client:
        registry = await registry_from_settings(client)
        if not registry.has("anidb"):
            return
        provider = registry.build("anidb")
        for aid in aids:
            if aid in seen:
                continue
            try:
                group = await provider.get_related_aids(aid)  # type: ignore[attr-defined]
            except Exception:
                continue
            if not group:
                continue
            canonical = min(group)
            group_id = f"anidb:{canonical}"
            seen.update(str(m) for m in group)
            # Update all matches in this franchise. Build an inline IN list
            # — `IN :members` with expanding bindparam is awkward in plain
            # text() and we trust `group` (came from int parsing).
            members_sql = ",".join(f"'{int(m)}'" for m in group)
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        f"UPDATE matches SET series_group_id = :gid "
                        f"WHERE provider='anidb' AND provider_id IN ({members_sql}) "
                        f"AND series_group_id IS NULL"
                    ),
                    {"gid": group_id},
                )


async def _backfill_variant_keys(conn) -> None:
    """Compute variant_key for MediaFile rows that have parsed_data but no
    variant_key yet. Pure in-process — no provider HTTP. Idempotent.

    The variant key encodes "what makes this file different from another
    file claiming the same episode" — audio language, edition, bit depth.
    Files with none of those signals get an empty-string key so they
    explicitly mark "default variant, will collide with any other default"
    rather than NULL (which would hide them from variant-aware queries).
    """
    from sqlalchemy import text
    import json

    rows = list(await conn.execute(
        text("SELECT id, parsed_data FROM media_files WHERE variant_key IS NULL AND parsed_data IS NOT NULL")
    ))
    updates: list[dict[str, Any]] = []
    for row in rows:
        mf_id, parsed_raw = row
        try:
            parsed = json.loads(parsed_raw) if isinstance(parsed_raw, str) else (parsed_raw or {})
        except Exception:
            continue
        # `subtitles` field carries the language tag (JAP/ENG/FRE/…) even
        # though the format-stripper name is "subtitles" — see comment on
        # SUBTITLES list. We use them as audio-language signal.
        lang_tokens = [s for s in (parsed.get("subtitles") or []) if isinstance(s, str)]
        lang = next((t.lower() for t in lang_tokens if t.lower() in
                     ("jap", "eng", "fre", "ger", "ita", "spa")), "")
        edition_raw = parsed.get("edition") or ""
        edition = "".join(c.lower() if c.isalnum() else "-" for c in str(edition_raw)).strip("-")
        bit = (parsed.get("bit_depth") or "").lower()
        # Bit depth only contributes when it's non-default (10bit). 8bit is
        # the historical default and adding it to the variant_key would
        # mark every old file as a "variant" of itself.
        if bit == "8bit":
            bit = ""
        key = f"{lang}|{edition}|{bit}"
        # Compact form for display: collapse empty pipes.
        compact = "-".join(p for p in (lang, edition, bit) if p)
        updates.append({"k": compact, "i": mf_id})
    if updates:
        await conn.execute(
            text("UPDATE media_files SET variant_key = :k WHERE id = :i"),
            updates,
        )


async def _backfill_series_keys(conn) -> None:
    """Walk every MediaFile row that has parsed_data but no series_key and
    populate the key from its parsed title/season/media_type. One-shot, cheap.
    """
    from sqlalchemy import text
    import json
    from kira.matcher.similarity import normalize

    rows = list(await conn.execute(
        text("SELECT id, media_type, parsed_data FROM media_files WHERE series_key IS NULL AND parsed_data IS NOT NULL")
    ))
    # Collect updates in memory, then send a single executemany call to
    # SQLite. On a 15k-file library this is one round-trip instead of 15k,
    # and the event loop stays free during startup.
    updates: list[dict[str, Any]] = []
    for row in rows:
        mf_id, media_type, parsed_raw = row
        try:
            parsed = json.loads(parsed_raw) if isinstance(parsed_raw, str) else (parsed_raw or {})
        except Exception:
            continue
        key: str | None = None
        title = parsed.get("title") or ""
        if media_type in ("tv", "anime") and title:
            title_n = normalize(title)
            if title_n:
                season = "" if media_type == "anime" else (
                    str(parsed.get("season")) if parsed.get("season") is not None else ""
                )
                key = f"{media_type}|{title_n}|{season}"
        elif media_type == "music" and parsed.get("artist") and parsed.get("album"):
            key = f"music|{normalize(parsed['artist'])}|{normalize(parsed['album'])}"
        if key:
            updates.append({"k": key, "i": mf_id})
    if updates:
        await conn.execute(
            text("UPDATE media_files SET series_key = :k WHERE id = :i"),
            updates,
        )
