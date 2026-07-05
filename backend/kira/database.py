from collections.abc import AsyncIterator
import json
import logging
import re
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from kira.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# Fire-and-forget background tasks are tracked by the canonical helper in
# `kira.tasks` (a single strong-ref registry shared across the app, avoiding the
# asyncio weakref-GC trap). These names are kept as back-compat aliases so any
# existing importer of `database._spawn_tracked` / `database._BACKGROUND_TASKS`
# keeps working against the shared registry.
from kira.tasks import _BACKGROUND_TASKS, spawn_tracked as _spawn_tracked  # noqa: F401


# v0.4→v0.5 naming-engine migration. The renamer switched from a
# `str.replace("{token}", …)` loop to a Jinja2 SandboxedEnvironment (see
# renamer/templates.py). Built-in profiles were rewritten to `{{token}}` in
# code, but user-saved custom profiles live in the DB (`naming.custom.*`
# settings rows) and still hold single-brace `{token}` strings that Jinja
# renders literally. `_migrate_legacy_naming_templates` uses this regex to
# rewrite a bare `{token}` → `{{token}}` while leaving already-migrated
# `{{ … }}` (and any Jinja with spaces/filters) untouched, so the migration
# is idempotent and safe to run on every boot.
#   (?<!\{)   — the `{` isn't already part of an opening `{{`
#   \{(\w+)\} — a single brace wrapping a bare identifier (n, y, s2, e2, …)
#   (?!\})    — the `}` isn't already part of a closing `}}`
_LEGACY_TOKEN_RE = re.compile(r"(?<!\{)\{(\w+)\}(?!\})")


def _apply_connection_pragmas(dbapi_connection) -> None:
    """PRAGMAs every SQLite connection needs, run once per physical connection.

    Module-level (not a closure) so it's unit-testable against a raw sqlite3
    connection. These are the difference between a single-user app that quietly
    serializes and one that throws ``database is locked`` the moment two
    boot-time writers overlap — self-heal, the AniDB group backfill, and a
    manual scan can all hit the DB within the first second of startup.

    - foreign_keys: OFF by default in SQLite, so the ``ON DELETE SET NULL`` on
      ``RenameHistory.media_file_id`` is a no-op without it (H9).
    - journal_mode=WAL: readers never block the single writer (and vice-versa),
      so the Review page keeps rendering while a scan writes. Persistent (lives
      in the DB header) — a cheap no-op on every connection after the first.
    - busy_timeout: wait up to 15s for a held lock instead of erroring instantly.
      Raised from 5s: a scan that overlaps the boot-time auto-heal sweep — both
      commit to SQLite's single writer — used to time out at 5s → "database is
      locked", which poisoned the scan session and surfaced as a failed scan.
      15s lets the scan ride out heal's brief, gap-separated per-file commits.
    - synchronous=NORMAL: the recommended durability level under WAL — crash-safe
      for app crashes, only risks the last transaction on OS/power loss, and is
      markedly faster than FULL for our write-bursty backfills.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA busy_timeout = 15000")
        cursor.execute("PRAGMA synchronous = NORMAL")
    finally:
        cursor.close()


if "sqlite" in settings.database_url:
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_connect(dbapi_connection, _conn_record):
        _apply_connection_pragmas(dbapi_connection)


# PB-5: slow-query logger. SQLAlchemy event hooks let us instrument every
# query without touching call sites. Threshold = 50ms — Review-page hot
# paths should complete well under this; anything slower is a perf bug
# (most likely a missing index or N+1). Logs are JSON so they pipe into
# `jq` or any structured-log sink. Threshold tunable via env var; default
# is intentionally conservative for self-hosted single-user workload.
_SLOW_QUERY_MS = float(__import__("os").environ.get("KIRA_SLOW_QUERY_MS", "50"))
_query_log = logging.getLogger("kira.db.slow")
logger = logging.getLogger(__name__)


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


# Columns added after first ship, in the order they were introduced. Applied
# idempotently on every boot via `_ensure_column` (ADD COLUMN IF the PRAGMA
# says it's missing). SQLite ADD COLUMN with no/constant default is fast + safe.
_MIGRATION_COLUMNS: list[tuple[str, str, str]] = [
    ("media_files",   "series_key",      "VARCHAR"),
    ("matches",       "series_group_id", "VARCHAR"),
    ("scans",         "estimated_total", "INTEGER"),           # PB-4 scan ETA
    ("matches",       "is_manual",       "BOOLEAN DEFAULT 0"), # user-pinned matches
    ("media_files",   "variant_key",     "VARCHAR"),           # dual-audio/edition variants
    ("rename_history", "parent_id",      "INTEGER"),           # Tier 1.2 sidecar links
    ("scans",         "source",          "VARCHAR DEFAULT 'manual'"),  # watched-folders
    ("matches",       "collection_id",   "VARCHAR"),           # Pass 7 #14 movie collections
    ("matches",       "collection_name", "VARCHAR"),
    ("rename_history", "created_assets", "JSON"),              # #1 authoritative-undo asset provenance
]


# The first Alembic revision — captures the complete 0.5.0 schema. Pre-Alembic
# databases (which create_all + the ensure-column list already brought current)
# are adopted by stamping this revision, so only LATER revisions ever run on
# them. Keep in sync with migrations/versions/ when re-baselining (never).
_ALEMBIC_BASELINE = "7500d72e9360"


def _run_alembic_sync(async_url: str) -> None:
    """Bring the schema under Alembic and run pending revisions. Sync — call
    via asyncio.to_thread.

    Decision tree:
      - fresh DB (no tables)            → `upgrade head` builds everything
      - pre-Alembic DB (tables, no
        alembic_version)                → `stamp baseline`, then `upgrade head`
        (adopt: the legacy create_all + ensure-column path already matches the
        baseline, so only post-baseline revisions apply)
      - already stamped                 → `upgrade head`

    Runs BEFORE create_all so a future revision's CREATE TABLE can't collide
    with one create_all already made. Uses the LIVE engine's URL (not config)
    so tests with monkeypatched engines migrate their own temp databases.
    """
    from pathlib import Path

    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config

    sync_url = async_url.replace("+aiosqlite", "")
    # In the container the package lives in site-packages while alembic.ini +
    # migrations/ live at KIRA_ALEMBIC_DIR (/app/backend) — resolving relative
    # to __file__ there pointed INTO site-packages and raised every boot,
    # silently disabling migrations. Env wins; source-tree layout is the
    # dev/test fallback.
    import os as _os
    _env_dir = _os.environ.get("KIRA_ALEMBIC_DIR")
    backend_dir = Path(_env_dir) if _env_dir else Path(__file__).resolve().parent.parent
    if not (backend_dir / "alembic.ini").is_file():
        raise FileNotFoundError(f"alembic.ini not found under {backend_dir}")
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    cfg.set_main_option("sqlalchemy.url", sync_url)

    probe = sa.create_engine(sync_url)
    try:
        insp = sa.inspect(probe)
        has_alembic = insp.has_table("alembic_version")
        # ANY of our tables present = an existing (pre-Alembic) database that
        # must be ADOPTED, not built — running the baseline's CREATE TABLEs
        # against it would collide. Checking several tables (not just
        # media_files) keeps partial/hand-built DBs (tests, salvage) on the
        # adopt path too.
        has_core = any(insp.has_table(t) for t in (
            "media_files", "matches", "settings", "rename_history", "scans",
        ))
    finally:
        probe.dispose()

    if not has_alembic and has_core:
        command.stamp(cfg, _ALEMBIC_BASELINE)
    command.upgrade(cfg, "head")


async def init_db(defer_data_ops: bool = False) -> None:
    """Create tables + apply idempotent in-place migrations on startup.

    CRITICAL ORDERING (do not collapse back into one transaction): SCHEMA
    changes commit FIRST and in ISOLATION from data backfills. The old code ran
    `create_all` + every `ADD COLUMN` + every backfill inside ONE transaction —
    so if a backfill threw on a user's data, the whole transaction (including
    the column adds) rolled back, the ORM then selected a column the table
    lacked, and EVERY query 500'd until a manual DB reset. Now each column add
    and each data op is isolated + best-effort: a data-op failure logs and is
    skipped, and can never undo a schema change.
    """
    from kira import models  # noqa: F401 — register mappers

    # ── 0) Alembic — adopt / upgrade BEFORE create_all so future revisions'
    #        DDL can't collide with tables create_all already made. Non-fatal:
    #        a migration failure logs loudly but the legacy create_all +
    #        ensure-column path below still brings a DB to a bootable state.
    try:
        import asyncio as _asyncio
        url = engine.url.render_as_string(hide_password=False)
        await _asyncio.to_thread(_run_alembic_sync, url)
    except Exception as e:  # noqa: BLE001 — never let a migration crash boot
        logging.getLogger(__name__).error("init_db: alembic upgrade failed (continuing on legacy path): %r", e)

    # ── 1) Tables ────────────────────────────────────────────────────────
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # ── 2) Columns — each in its OWN transaction so one bad ALTER can't
    #        block the rest, and they're durably committed before any backfill.
    for table, column, ddl in _MIGRATION_COLUMNS:
        try:
            async with engine.begin() as conn:
                await _ensure_column(conn, table, column, ddl)
        except Exception as e:  # noqa: BLE001 — never let a migration crash boot
            logger.warning(f"init_db: ensure_column {table}.{column} failed (non-fatal): {e!r}")

    # ── 3) Data backfills + one-shot data migrations — EACH isolated and
    #        NON-FATAL. A failure here logs and is skipped; the schema above
    #        is already committed, so the app still starts cleanly.
    #
    #        With `defer_data_ops=True` (the production lifespan) only the
    #        CHEAP correctness op runs inline (the scan-lock row, needed
    #        before any scan); the heavy full-table backfills run via
    #        `run_deferred_data_ops()` in a background task — so the NAS
    #        serves its first request immediately instead of blocking boot
    #        on full-table SELECT sweeps of a big library.
    for name, fn in [("ensure_scan_lock_row", _ensure_scan_lock_row)]:
        try:
            async with engine.begin() as conn:
                await fn(conn)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"init_db: {name} failed (non-fatal): {e!r}")
    if not defer_data_ops:
        await run_deferred_data_ops()


async def run_deferred_data_ops() -> None:
    """The heavy, best-effort data backfills init_db used to run inline at
    boot. Every op is idempotent + isolated, so this is safe to run at any
    time; production schedules it as a background task right after serving
    starts (see main.lifespan)."""
    _data_ops = [
        ("backfill_series_keys", _backfill_series_keys),
        ("backfill_variant_keys", _backfill_variant_keys),
        ("backfill_series_group_ids", _backfill_series_group_ids),
        ("refold_tvdb_anime_groups", _refold_tvdb_anime_groups),
        ("migrate_legacy_naming_templates", _migrate_legacy_naming_templates),
        ("create_perf_indexes", _create_perf_indexes),
    ]
    for name, fn in _data_ops:
        try:
            async with engine.begin() as conn:
                await fn(conn)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"data_ops: {name} failed (non-fatal): {e!r}")


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
        # rename_history.media_file_id: bulk file delete runs an UPDATE per
        # deleted file against this column (files.py) — a 500-file purge over
        # a 50k-row history was 25M row scans without it. Same column drives
        # the undo→MediaFile sync.
        "CREATE INDEX IF NOT EXISTS ix_rh_media_file_id ON rename_history(media_file_id)",
        # rename_history.created_at: every History page load sorts on it,
        # and the retention prune filters on it.
        "CREATE INDEX IF NOT EXISTS ix_rh_created_at ON rename_history(created_at)",
        # media_files.created_at / updated_at: /files default ordering now,
        # delta-polling cursor later.
        "CREATE INDEX IF NOT EXISTS ix_mf_created_at ON media_files(created_at)",
        "CREATE INDEX IF NOT EXISTS ix_mf_updated_at ON media_files(updated_at)",
        # media_files.scan_id: FK with no index (SQLite doesn't auto-index
        # FKs); scan-failure cleanup DELETEs by it.
        "CREATE INDEX IF NOT EXISTS ix_mf_scan_id ON media_files(scan_id)",
        # matches(provider, provider_id): boot backfills / refolds / heal
        # passes all look matches up by provider identity.
        "CREATE INDEX IF NOT EXISTS ix_match_provider_pid ON matches(provider, provider_id)",
    ]
    for stmt in statements:
        await conn.execute(text(stmt))
    # PRAGMA optimize (SQLite's recommended maintenance call): analyzes only
    # the tables whose stats are stale — including the indexes just created —
    # so the query planner learns about them WITHOUT a full-database ANALYZE
    # stalling boot on a large library every start.
    await conn.execute(text("PRAGMA optimize"))


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


async def _migrate_legacy_naming_templates(conn) -> None:
    """Rewrite user-saved custom naming profiles from the old single-brace
    `{token}` syntax to Jinja2 `{{token}}` (v0.4→v0.5 engine switch).

    Built-in profiles (`DEFAULT_PROFILES`) were updated in code; only the
    custom profiles the user saved through Settings → Naming live in the DB,
    under `naming.custom.<name>` settings rows whose JSON value is a
    `{movie, tv, anime, music}` dict of template strings. Under the old
    `str.replace` engine those were `{token}`; Jinja renders them literally,
    so a profile like `{n} - S{s2}E{e2}` would produce the path
    `{n} - S{s2}E{e2}` verbatim. Rewrite each field in place.

    Idempotent: `_LEGACY_TOKEN_RE` skips already-`{{…}}` tokens, so a row
    that's already been migrated produces no change and no write.
    """
    from sqlalchemy import text

    rows = list(await conn.execute(
        text("SELECT key, value FROM settings WHERE key LIKE 'naming.custom.%'")
    ))
    for key, raw in rows:
        try:
            profile = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        if not isinstance(profile, dict):
            continue
        changed = False
        for field in ("movie", "tv", "anime", "music"):
            tmpl = profile.get(field)
            if isinstance(tmpl, str):
                migrated = _LEGACY_TOKEN_RE.sub(r"{{\1}}", tmpl)
                if migrated != tmpl:
                    profile[field] = migrated
                    changed = True
        if changed:
            await conn.execute(
                text("UPDATE settings SET value = :v WHERE key = :k"),
                {"v": json.dumps(profile), "k": key},
            )


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
        _spawn_tracked(
            _backfill_anidb_groups_async([r[0] for r in pending]),
            label="anidb_group_backfill",
        )


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


async def _refold_tvdb_anime_groups(conn) -> None:
    """One-shot: re-fold TVDB-matched anime into their AniDB franchise card.

    A long-runner whose files are pure-absolute-numbered (Attack on Titan's
    Final Season — "Shingeki no Kyojin - 60") can't sit in any single AniDB
    cour, so the matcher routes it to TVDB and the cheap Pass-1 backfill above
    stamps it `tvdb:<id>` — a SEPARATE card from the AniDB-matched siblings
    (`anidb:9541`). compute_series_group_id now folds TVDB anime through Fribb,
    but that only governs FRESH matches; existing rows keep their old
    `tvdb:<id>` group until a rescan. This recomputes the group for any
    still-`tvdb:`-grouped episode rows so the card merges on the next boot
    without a rescan.

    Deferred to a background task: resolving the franchise root can need a
    rate-limited AniDB relations walk for a TVDB id never matched via AniDB
    (instant when the chain is already disk-cached — as it is for any franchise
    the user already has AniDB seasons of). Idempotent: only ever rewrites
    `tvdb:%` episode groups, and only when the fold resolves to an `anidb:` id
    (live-action TVDB shows resolve back to `tvdb:<id>` and are left as-is).
    """
    from sqlalchemy import text

    pending = list(await conn.execute(text(
        "SELECT DISTINCT provider_id FROM matches "
        "WHERE provider='tvdb' AND match_type='tv_episode' "
        "AND series_group_id LIKE 'tvdb:%'"
    )))
    if pending:
        _spawn_tracked(
            _refold_tvdb_anime_groups_async([r[0] for r in pending]),
            label="tvdb_anime_refold",
        )


async def _refold_tvdb_anime_groups_async(tvdb_ids: list[str]) -> None:
    """Resolve each TVDB id's franchise fold + rewrite the group for its
    episode rows. Runs in the background after startup. See the sync sibling.

    compute_series_group_id does the Fribb gate: a known-anime TVDB id folds
    to `anidb:<root>`; a live-action id returns `tvdb:<id>` and is skipped, so
    no movie or non-anime card is ever disturbed.
    """
    import httpx
    from sqlalchemy import text
    from kira.matcher.engine import compute_series_group_id, registry_from_settings

    async with httpx.AsyncClient() as client:
        registry = await registry_from_settings(client)
        for tid in tvdb_ids:
            try:
                gid = await compute_series_group_id("tvdb", str(tid), registry)
            except Exception:
                continue
            if not gid.startswith("anidb:"):
                continue  # live-action / unmapped → leave the tvdb:<id> card
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE matches SET series_group_id = :gid "
                        "WHERE provider='tvdb' AND provider_id = :tid "
                        "AND match_type='tv_episode' AND series_group_id LIKE 'tvdb:%'"
                    ),
                    {"gid": gid, "tid": str(tid)},
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
