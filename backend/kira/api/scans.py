"""Scan endpoints — scan + match runs as a background task so the frontend
polls /scans/{id} for live progress while rows appear in real time.
"""

import logging
import asyncio
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, or_, select
from sqlalchemy import func as _sql_func
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from kira import activity
from kira import scanner
from kira import xattr_store as _xattr_store
from kira.api.match_cleanup import detach_and_delete_matches
from kira.database import SessionLocal, get_session
from kira.matcher import MatchEngine
from kira.matcher.cour_routing import remap_umbrella_local_to_absolute
from kira.matcher.engine import compute_series_group_id, fetch_match_metadata, registry_from_settings, resolve_canonical_season
# CR-07: pure key logic moved to kira.matcher.keys. Re-exported under the old
# private names below as back-compat aliases (matches.py / files.py / tests do
# `from kira.api.scans import _compute_series_key, _compute_variant_key`).
from kira.matcher.keys import compute_series_key as _compute_series_key
from kira.matcher.keys import compute_variant_key as _compute_variant_key
# CR-09: shared media_type correction + key recompute helper.
from kira.matcher.media_type import apply_media_type_and_recompute_keys
from kira.models import Match, MediaFile, Scan
from kira.parser import ParsedFile, parse as parse_path
from kira.parser import mediainfo as _mediainfo
from kira.schemas import ScanCreate, ScanOut
# CR-11: canonical strong-ref fire-and-forget helper (replaces this module's
# old `_MI_ENRICH_TASKS` registry).
from kira.tasks import spawn_tracked

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scans", tags=["scans"])

# How often to commit during the walk — every N files, push a checkpoint
# so the polling client sees rows appear in real time. 100 (was 5): each WAL
# commit is an fsync (~5-30ms on NAS storage), so 5 meant 2,000 fsyncs per
# 10k-file ingest — 10-60s of pure commit overhead plus 2,000 write-lock
# acquisitions competing with API writes ("database is locked"). At walk
# speeds 100 still checkpoints multiple times per poll tick (800ms).
# Bounded width for the parallel (non-AniDB) match lane — see _match_phase.
_PARALLEL_MATCH_WIDTH = 4
SCAN_COMMIT_EVERY = 100
MATCH_COMMIT_EVERY = 3

# PERF (NAS responsiveness): how many directory entries the discovery walk
# pulls per hop into the worker thread before handing a batch back to the
# event loop. The walk + per-file stat() are blocking network round-trips on
# an SMB/NFS share; running them inline on the loop stalled every other HTTP
# request. We now drain the walk in a dedicated thread `SCAN_WALK_BATCH`
# entries at a time (see `_drain_walk_batch`). Bounded by entries *pulled*
# (not survivors) so an all-already-known re-scan can't become one giant
# uninterruptible hop — between batches the loop is free to serve requests
# and honor cancellation. The event loop still yields every SCAN_COMMIT_EVERY
# survivors via the commit checkpoint, so progress streams as before.
SCAN_WALK_BATCH = 256

# EE-3: Process-level lock around scan worker. Without this, two
# concurrent POST /scans calls (user double-clicks "Scan", or two browser
# tabs hit it within a second) spawn two background workers walking the
# SAME root. SQLite's UNIQUE(file_path) becomes a footgun rather than a
# safety net: worker B's INSERT raises mid-batch AFTER it has already
# committed earlier partial rows, leaving orphans that auto-heal later
# tries to match (firing duplicate AniDB calls + reawakening the ban).
#
# Autopsy 6: `asyncio.Lock` is per-process. In a multi-worker uvicorn
# deployment two workers can simultaneously see `_SCAN_LOCK.locked() ==
# False` and BOTH spawn scan workers walking the same disk. The fix is
# a DB-level CAS on the `system.scan_running` setting row — see
# `create_scan` for the claim and `_scan_worker_locked` for the release.
# We keep the in-process lock as a fast-fail belt for same-worker
# double-clicks (no DB roundtrip needed when the conflict is local).
_SCAN_LOCK = asyncio.Lock()

# Live scan-worker tasks keyed by scan id, so the Stop button can cancel the
# running scan: task.cancel() raises CancelledError at the worker's next await,
# the worker marks the scan 'cancelled' (KEEPING what it found) and frees the
# lock. In-process — Kira ships single-uvicorn (exe / docker); a multi-worker
# setup would need a DB signal instead.
_SCAN_TASKS: dict[int, asyncio.Task] = {}

# Auto-expire DB lock entries older than 6 hours. A worker that crashed
# mid-scan would otherwise leave the lock pinned forever; this lets a
# subsequent boot reclaim it cleanly without manual DB surgery. Real
# scans complete in minutes (even on 100k-file libraries); 6 h is wide
# enough that a slow scan never gets pre-empted but narrow enough that
# a crashed scan unblocks the next-day boot.
_SCAN_LOCK_MAX_AGE_SEC = 6 * 3600

# Heartbeat throttle for _touch_db_scan_lock (monotonic seconds).
_LAST_LOCK_TOUCH = 0.0


async def _touch_db_scan_lock() -> None:
    """Refresh the DB scan-lock timestamp (throttled to every 5 min). A legit
    scan that outlives _SCAN_LOCK_MAX_AGE_SEC used to become claimable by
    another process mid-run; the match phase calls this from its progress
    writes so a LIVE scan keeps re-asserting its claim. Best-effort."""
    global _LAST_LOCK_TOUCH
    import time as _t
    _now = _t.monotonic()
    if _now - _LAST_LOCK_TOUCH < 300:
        return
    _LAST_LOCK_TOUCH = _now
    try:
        from sqlalchemy import update as _sql_update
        from kira.models import Setting as _Setting
        async with SessionLocal() as _sess:
            await _sess.execute(
                _sql_update(_Setting)
                .where(_Setting.key == "system.scan_running")
                .where(_Setting.value != 0)
                .values(value=int(_t.time()))
            )
            await _sess.commit()
    except Exception:
        pass


async def _read_mediainfo_setting(session) -> bool:
    """Whether to backfill missing quality/codec/HDR from real file metadata
    (Phase 16) during a scan.

    Default FALSE. Reading a file's container headers opens the file and pulls
    bytes off disk — over a NAS that's a slow round-trip PER tag-less file,
    right on the matching critical path, and it's only a cosmetic win (quality
    chips for files whose filename lacks `1080p`/`x265`/etc.). Off by default
    keeps scans fast; opt in via Settings if you want chips on tag-less files
    and accept the per-file I/O. (No-op regardless if pymediainfo isn't installed.)"""
    try:
        from kira.models import Setting
        from kira.settings_store import unwrap
        row = await session.get(Setting, "parsing.read_mediainfo")
        if row is None:
            return False
        # unwrap the optional {"value": …} shape — every other reader does, and
        # `bool({"value": False})` is True (non-empty dict), which would read OFF
        # as ON. SSOT in kira.settings_store.
        return bool(unwrap(row.value))
    except Exception:
        return False


async def _read_mediainfo_authoritative_setting(session) -> bool:
    """Whether the file's REAL container metadata should OVERRIDE filename-derived
    tech tags (vs. only filling gaps). Default FALSE.

    When on, the background enrichment pass (`enrich_mediainfo_background`) trusts
    MediaInfo over the release name — so a file mislabelled `1080p` in its name but
    actually 720p on disk gets corrected, and EVERY file is read (not just tag-less
    ones). Off keeps the filename's explicit tags and only fills blanks.
    Independent of `parsing.read_mediainfo`, which gates reading at all.

    Key is `parsing.mediainfo_authoritative` — matches the Settings UI toggle
    ("Authoritative tech tags")."""
    try:
        from kira.models import Setting
        from kira.settings_store import unwrap
        row = await session.get(Setting, "parsing.mediainfo_authoritative")
        if row is None:
            return False
        # unwrap before bool() — see _read_mediainfo_setting.
        return bool(unwrap(row.value))
    except Exception:
        return False


async def _ids_missing_tech_tags(session, *, limit: int = 20000) -> list[int]:
    """File IDs whose container has NEVER been read (no `mi_stamp` in parsed_data).

    Lets a plain *rescan* dependably fill tech tags for files that pre-date the
    feature (or were added while it was off / never enriched), not only the files
    discovered THIS scan. Without this, a rescan that turns up nothing new leaves
    the enrich pass with an empty set and it silently no-ops — which reads to the
    user as "the tech-tag scan didn't start".

    Self-limiting: the enrich pass stamps `mi_stamp` on every file it inspects
    (even ones MediaInfo can't read — the stat still succeeds), so an inspected
    file drops out of this set and is never re-read on the next scan. Bounded by
    `limit` and fully best-effort — a query failure returns [] and the caller
    falls back to the new-files set, so this never breaks a scan."""
    try:
        from sqlalchemy import select, func
        stmt = (
            select(MediaFile.id)
            .where(func.json_extract(MediaFile.parsed_data, "$.mi_stamp").is_(None))
            .limit(limit)
        )
        res = await session.execute(stmt)
        return [int(r[0]) for r in res.all()]
    except Exception as e:
        logger.warning(f"_ids_missing_tech_tags: query failed (non-fatal): {e!r}")
        return []


async def _read_auto_approve_setting(session) -> tuple[bool, float]:
    """Auto-approve config for the scan match phase (Settings → Confidence).

    When enabled, a freshly-matched file whose SELECTED match scores at or above
    the threshold is marked ``approved`` straight out of matching instead of
    ``matched`` (held for review) — so high-confidence hits skip the Review queue.
    Approval only pre-clears the file for the user's rename action; it never
    moves anything on disk.

    Returns ``(enabled, threshold)`` where threshold is a 0..1 fraction.
    DEFAULT DISABLED — a fresh DB / reset must NOT auto-approve a freshly-scanned
    library out from under the user (they expect to review matches first); it's
    opt-in via Settings → Confidence. Threshold is 95% once enabled.
    ``matching.auto_threshold`` is stored as a 0-100 percent, so it's normalised
    here."""
    try:
        from kira.models import Setting
        from kira.settings_store import unwrap

        en_row = await session.get(Setting, "matching.auto_approve")
        th_row = await session.get(Setting, "matching.auto_threshold")
        enabled = False if en_row is None else bool(unwrap(en_row.value))
        raw_th = 95 if th_row is None else unwrap(th_row.value)
        try:
            th = float(raw_th)
        except (TypeError, ValueError):
            th = 95.0
        return enabled, max(0.0, min(100.0, th)) / 100.0
    except Exception:
        # Fail CLOSED. A transient DB error (e.g. `database is locked` while a
        # watcher scan overlaps a manual one) must NOT silently flip auto-approve
        # ON — the documented default is DISABLED and the user expects to review
        # matches. Leaving them pending is the safe failure.
        return False, 0.95


async def _apply_xattr_ids(parsed: ParsedFile, file_path: str | None) -> None:
    """At MATCH time, fill `parsed.provider_ids` from a Kira-stamped xattr / NTFS
    ADS id (set on a prior rename) when the filename didn't carry one.

    Done here, not in the discovery walk: this read sits right beside the network
    search it might replace, so a stamped file skips the search entirely and an
    unstamped one pays a single cheap read that's noise next to the search. Once
    per cluster/singleton (not per file) → cheap even on a NAS. Off the event
    loop; never raises."""
    if not file_path or getattr(parsed, "provider_ids", None):
        return
    try:
        stamped = await asyncio.to_thread(_xattr_store.read_ids, file_path)
        if stamped:
            parsed.provider_ids = stamped
    except Exception as e:
        logger.warning(f"_apply_xattr_ids: read failed for {file_path} (non-fatal): {e!r}")


# Phase 16 history: a `_maybe_enrich_mediainfo(parsed, path, …)` helper used to
# do one blocking read per call; `enrich_mediainfo_background` now inlines the
# read with bounded concurrency + a (size, mtime) stamp cache instead.

# CR-11: the strong-ref registry + done-callback fire-and-forget pattern now
# lives once in kira.tasks.spawn_tracked (imported at module top). This module
# used to keep its own `_MI_ENRICH_TASKS` set duplicating it.

# Stable activity-job name (reused per run, not accumulated) for the live
# "Reading file media info · N/total" pill at GET /api/v1/activity.
_MI_ENRICH_JOB = "mediainfo_enrich"


async def _post_notification(kind: str, title: str, body: str) -> None:
    """Write a durable Notification on its own short session (so it survives the
    transient activity pill and shows up in the popover + dashboard 'Recent
    activity'). Never raises."""
    try:
        from kira.models import Notification
        async with SessionLocal() as session:
            session.add(Notification(kind=kind, title=title, body=body))
            await session.commit()
    except Exception as e:
        logger.warning(f"_post_notification failed (non-fatal): {e!r}")


def _spawn_mediainfo_enrich(file_ids: list[int], *, reason: str | None = None) -> None:
    """Fire-and-forget the background tech-tag enrichment for `file_ids`.

    Detached on purpose: the caller (scan completion / reparse / a settings
    toggle) returns immediately and the slow per-file container reads happen
    afterwards, so nothing waits on MediaInfo. No-op without a running loop
    (e.g. a sync/test context) — enrichment is best-effort cosmetic.

    `reason="settings"` marks an explicit user action (just enabled the toggle):
    it gets a durable completion Notification, since the live pill alone is easy
    to miss on a fast pass."""
    if not file_ids:
        return
    # CR-11: keep the no-running-loop no-op (sync/test contexts) — enrichment is
    # best-effort cosmetic — then delegate the strong-ref/exception-logging
    # fire-and-forget bookkeeping to the shared kira.tasks.spawn_tracked.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    spawn_tracked(
        enrich_mediainfo_background(list(file_ids), reason=reason),
        label="mediainfo_enrich",
    )


async def enrich_mediainfo_background(file_ids: list[int], *, reason: str | None = None) -> int:
    """Read true container metadata for `file_ids` OFF any critical path and write
    it into each row's `parsed_data`.

    This is the slow half of MediaInfo — a NAS round-trip per file — deliberately
    detached from the scan so discovery/matching stay fast. The UI fills in the
    richer chips (and the dupe-ranker sharpens) on its next `/files` poll.

    Gated by `parsing.read_mediainfo`. Authoritative vs. fallback per
    `parsing.mediainfo_authoritative`. Best-effort, **paced**, and fully
    exception-isolated: a slow NAS or one bad file never blocks the others or
    raises out of the task. Returns the number of rows updated.

    Only `parsed_data` is written — the enriched fields (quality/codec/HDR/
    channels/audio/duration) don't feed `series_key`/`variant_key`/`media_type`,
    so there's nothing else to recompute and no UNIQUE-collision risk.

    Progress is published to the activity surface (GET /api/v1/activity) so the
    UI shows a live "Reading file media info · N/total" pill — the user can watch
    the pass churn through the library instead of guessing how far it's got.

    `reason="settings"` (the user just enabled the toggle) adds a durable
    completion Notification — and, if the native lib is missing, an explanatory
    one — so an explicit action is never met with silence."""
    if not file_ids:
        return 0
    if not _mediainfo.available():
        # An explicit toggle deserves an explanation, not silence: tell the user
        # WHY enabling it did nothing (no native lib → no reads possible).
        if reason == "settings":
            await _post_notification(
                "warning",
                "Can't read file metadata — MediaInfo not installed",
                "You enabled “Read file metadata”, but the native MediaInfo "
                "library (libmediainfo / pymediainfo) isn't available on the "
                "server, so no tech tags can be read from your files. The Docker "
                "image bundles it; on a bare install, install libmediainfo and "
                "pymediainfo, then toggle the setting again.",
            )
        return 0
    updated = 0
    started = False
    total = len(file_ids)
    try:
        async with SessionLocal() as session:
            if not await _read_mediainfo_setting(session):
                return 0
            authoritative = await _read_mediainfo_authoritative_setting(session)
            activity.begin(_MI_ENRICH_JOB, "Reading file media info", total=total)
            started = True

            # Phase A (serial): load each row's path + cached read stamp so the
            # reads can run without touching the session.
            work: list[tuple[int, str, list[int] | None, dict | None]] = []
            for fid in file_ids:
                mf = await session.get(MediaFile, fid)
                if mf is None or not mf.file_path or not mf.parsed_data:
                    continue
                # Defensive: parsed_data is occasionally malformed (and one bad
                # row must never abort the whole pass — the per-file isolation
                # lives in the consume loop, which will rollback just that row).
                pd = mf.parsed_data if isinstance(mf.parsed_data, dict) else {}
                work.append((fid, mf.file_path,
                             pd.get("mi_stamp"), pd.get("mi_raw")))

            # Phase B: the NAS round-trips run CONCURRENTLY (they're pure
            # latency, not CPU), bounded so we never hammer the share. Each
            # worker stats the file first — when (size, mtime) matches the
            # stored stamp it reuses the cached raw dict and skips the parse
            # entirely, which makes re-runs (rescans, toggling Authoritative
            # tech tags) near-free. DB writes stay STRICTLY in this coroutine:
            # the AsyncSession is not concurrency-safe.
            #
            # Width follows Settings → Advanced → "Concurrent sidecar fetches"
            # (`rename.concurrency`) — same knob, same kind of work (parallel
            # per-file I/O against the same share). Default 8 when unset.
            try:
                from kira.models import Setting
                _c_row = await session.get(Setting, "rename.concurrency")
                _c_val = _c_row.value if _c_row else None
                if isinstance(_c_val, dict) and "value" in _c_val:
                    _c_val = _c_val["value"]
                width = max(1, min(32, int(_c_val)))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                width = 4   # match the Settings UI default + rename.py's resolver
            sem = asyncio.Semaphore(width)

            # Sidecar subtitle languages already on disk — one directory listing
            # per parent folder (not per file), computed off the loop. Folded
            # into parsed_data so the coverage chip knows about subs we didn't
            # write ourselves (e.g. a release that shipped an external .srt).
            from kira.subtitles.coverage import scan_sidecar_langs
            try:
                sidecar_map = await asyncio.to_thread(
                    scan_sidecar_langs, [w[1] for w in work]
                )
            except Exception as e:  # never let a sidecar scan abort the enrich
                logger.warning(f"enrich_mediainfo_background: sidecar scan failed (non-fatal): {e!r}")
                sidecar_map = {}

            async def _read_one(fid: int, path: str,
                                stamp: list[int] | None, cached: dict | None):
                def _work():
                    # Stat is advisory: a failed stat (network blip, exotic FS)
                    # just means "no stamp this round" — we still attempt the
                    # parse, exactly like the pre-cache behaviour.
                    try:
                        st = os.stat(path)
                        cur = [int(st.st_size), int(st.st_mtime)]
                    except OSError:
                        cur = None
                    if cur is not None and stamp == cur and cached is not None:
                        return cached, cur  # unchanged file → cache hit, no parse
                    return _mediainfo.read_media_info(path), cur
                async with sem:
                    mi, cur = await asyncio.to_thread(_work)
                return fid, mi, cur

            path_by_fid = {w[0]: w[1] for w in work}
            done = 0
            # Commit in batches, not per changed file: a 10k-file backfill used
            # to fire 10k fsyncs exactly while subtitle backfill / poster warmup
            # / boot heal were also writing — the observed "database is locked"
            # neighborhood. A crash loses at most one batch, which just re-
            # enriches next pass (stamps weren't persisted).
            _COMMIT_EVERY = 50
            _dirty = 0
            # Process in bounded chunks so a large (up to 20k-file) backfill can't
            # materialize tens of thousands of Tasks at once. The `sem` still caps
            # CONCURRENT NAS reads to its width; this caps SCHEDULED coroutines.
            _CHUNK = 256
            for _i in range(0, len(work), _CHUNK):
                for fut in asyncio.as_completed([_read_one(*w) for w in work[_i:_i + _CHUNK]]):
                    try:
                        fid, mi, cur = await fut
                        done += 1
                        if mi is not None or cur is not None:
                            mf = await session.get(MediaFile, fid)
                            if mf is not None and mf.parsed_data:
                                parsed = ParsedFile(**mf.parsed_data)
                                changed = _mediainfo.enrich_parsed(parsed, mi, authoritative=authoritative)
                                if cur is not None and (parsed.mi_stamp != cur or parsed.mi_raw != mi):
                                    parsed.mi_stamp, parsed.mi_raw = cur, mi
                                    changed = True
                                # Record on-disk sidecar languages ([] = looked, none
                                # found) so coverage is accurate even for subs we
                                # didn't write. Always set it here — this pass IS the
                                # authoritative "we inspected this file" moment.
                                sidecars = sidecar_map.get(path_by_fid.get(fid, ""), [])
                                if parsed.sub_sidecars != sidecars:
                                    parsed.sub_sidecars = sidecars
                                    changed = True
                                if changed:
                                    mf.parsed_data = parsed.to_dict()
                                    updated += 1
                                    _dirty += 1
                                    if _dirty >= _COMMIT_EVERY:
                                        await session.commit()
                                        _dirty = 0
                    except Exception as e:
                        done += 1
                        logger.warning(f"enrich_mediainfo_background: file failed (non-fatal): {e!r}")
                        try:
                            await session.rollback()
                            _dirty = 0
                        except Exception:
                            pass
                    # Report after every completion: cheap in-memory write, and it
                    # keeps the job from being marked stale during a slow NAS read.
                    activity.progress(_MI_ENRICH_JOB, done, total)
            if _dirty:
                await session.commit()
    except Exception as e:
        logger.warning(f"enrich_mediainfo_background: aborted (non-fatal): {e!r}")
    finally:
        # Clear the pill even on early-abort. Guarded so a disabled/no-op run
        # (returned before begin) never flashes an empty job.
        if started:
            activity.end(_MI_ENRICH_JOB)
    if updated:
        logger.info(f"enrich_mediainfo_background: enriched {updated}/{len(file_ids)} file(s)")
    # Durable completion record for an explicit user action (just toggled it on).
    # Fires even when 0 changed — "checked N, updated 0" is the reassurance that
    # it ran and covered everything (their filenames simply already had the tags).
    # Per-scan / reparse runs stay quiet: they have their own completion signals
    # and would otherwise post a media-info notification on every single scan.
    if reason == "settings" and started:
        plural = "" if total == 1 else "s"
        if updated:
            body = (f"Read media info for {total} file{plural} and refreshed tech "
                    f"tags on {updated}. Re-open a title in Review to see the new chips.")
        else:
            body = (f"Read media info for {total} file{plural}; nothing changed — "
                    "your filenames already carried these tags. (Turn on "
                    "“Authoritative tech tags” to override them with the container's.)")
        await _post_notification("success", "Finished reading file media info", body)

    # Auto-backfill: once a file's container has been read, we KNOW which
    # wanted languages it's missing — so opt-in users get subs fetched
    # automatically without a manual click. Best-effort + gated; the backfill
    # itself filters to files actually missing a language and narrates its own
    # progress pill. Skipped silently when the toggle is off.
    try:
        async with SessionLocal() as _bf_session:
            from kira.subtitles.prefs import load_subtitle_prefs
            _prefs = await load_subtitle_prefs(_bf_session)
        if _prefs.backfill_after_scan and _prefs.any_source_enabled:
            from kira.subtitles.backfill import spawn_subtitle_backfill
            spawn_subtitle_backfill(list(file_ids))
        # Upgrade-over-time: also re-check low-scoring subs for a better one.
        if _prefs.upgrade and _prefs.any_source_enabled:
            from kira.subtitles.backfill import spawn_subtitle_upgrade
            spawn_subtitle_upgrade()
    except Exception as e:
        logger.warning(f"enrich_mediainfo_background: auto-backfill spawn failed (non-fatal): {e!r}")
    # Persist anime poster URLs onto their matches so covers render from a
    # stored URL (like TV/movies) instead of a throttled per-card AniDB lookup.
    try:
        from kira.posters import spawn_poster_warmup
        spawn_poster_warmup()
    except Exception as e:
        logger.warning(f"enrich_mediainfo_background: poster warmup spawn failed (non-fatal): {e!r}")
    return updated


async def _maybe_rescue_title_from_mediainfo(mf: MediaFile) -> bool:
    """Last-ditch identity for a file the FILENAME couldn't crack: read the
    container's embedded title and re-parse from it.

    Fires ONLY when the parse yielded no usable title or media_type 'unknown'
    — i.e. files the matcher would otherwise skip entirely (no title → []; no
    providers for 'unknown'). Because those files never match anyway, we read
    MediaInfo here even when the global MediaInfo-on-scan setting is OFF: the
    single bounded read is worth a shot at rescuing an otherwise-dead file.

    The embedded title is unreliable (often blank / release-name junk), so we
    adopt the re-parse only when it actually produces a title and doesn't
    regress a file that already had one. Best-effort; never raises. Returns
    True when the parse was rescued. (For truly nameless files the dependable
    path remains 'Identify by content' — the OSDb byte-hash.)"""
    if not mf.file_path or not _mediainfo.available():
        return False
    parsed_now = mf.parsed_data or {}
    has_title = bool((parsed_now.get("title") or "").strip())
    if has_title and mf.media_type != "unknown":
        return False  # filename already gave us something usable
    try:
        embedded = await asyncio.to_thread(_mediainfo.read_embedded_title, mf.file_path)
    except Exception:
        return False
    if not embedded or not embedded.strip():
        return False
    from pathlib import Path as _P
    from kira.parser import parse_filename
    src = _P(mf.file_path)
    fresh = parse_filename(f"{embedded.strip()}{src.suffix}", parent_path=str(src.parent))
    if not fresh.title:
        return False
    # Don't regress a file that already had a title into a vaguer 'unknown'.
    if has_title and fresh.media_type == "unknown":
        return False
    mf.parsed_data = fresh.to_dict()
    mf.media_type = fresh.media_type
    mf.series_key = _compute_series_key(fresh)
    mf.variant_key = _compute_variant_key(fresh)
    logger.info(f"title-rescue: {src.name!r} -> {fresh.title!r} ({fresh.media_type}) via embedded title")
    return True


async def _match_singleton(session, engine, fid: int) -> None:
    """Match one file independently — used for movies and unclustered files.

    For TV/anime singletons we also fetch the episode list so the row gets
    a real `episode_title` from the very first scan — without this, a
    one-off episode shows "Episode N" generic text until the auto-heal
    sweep runs on the next restart, which looks broken in the UI.
    """
    mf = await session.get(MediaFile, fid)
    if mf is None or not mf.parsed_data:
        return
    parsed = ParsedFile(**mf.parsed_data)
    # Identify-time xattr read: a file Kira stamped on a prior rename resolves
    # by its embedded id (zero search). One cheap read beside the search.
    await _apply_xattr_ids(parsed, mf.file_path)
    # Same exception-vs-empty discipline as _match_cluster: a TMDB outage
    # must NOT wipe the user's existing matches. Skip the file entirely
    # if the matcher throws; only the empty-result case clears Match rows.
    try:
        scored = await engine.match(parsed, limit=5)
    except Exception as e:
        logger.info(f"_match_singleton: matcher raised for file {fid}: {e!r}")
        return

    # Resolve an episode title for the top match before we touch the DB.
    # Episode-list fetch routes through `_fetch_episodes_for_match` which
    # prefers TVDB cross-ref over AniDB direct calls — AniDB-ban hardening.
    ep_title: str | None = None
    # Pre-init: only the tv_episode branch below assigns it, but the Match loop
    # (resolve_canonical_season(..., episode=ep_num)) runs for EVERY match —
    # incl. movies — so it must always be bound.
    ep_num: int | None = None
    # Stored episode_number — defaults to the file's own parsed episode. Bound
    # here (not only inside the tv_episode branch) so the Match write below is
    # always safe; movies leave it None (parsed.episode is None there anyway).
    stored_ep: int | None = parsed.episode
    if scored and scored[0].match_type == "tv_episode":
        _ep_dicts: list = []
        # Phase 4 validation gate: fetch the top's episode list and, for a
        # western-TV singleton whose TVDB/TMDB match doesn't contain the
        # file's episode, re-rank to a better-fitting alternate. No-op for
        # anime/AniDB. May reorder `scored`, so the metadata/poster fetch
        # below (which reads scored[0]) picks up the corrected top.
        try:
            scored, episodes_by_key, _ep_dicts = await _validate_and_rerank_by_episodes(
                scored, [(fid, parsed)], parsed.season, parsed.media_type, engine.registry,
            )
        except Exception as e:
            logger.warning(f"_match_singleton: episode validation failed for file {fid}: {e!r}")
            episodes_by_key = {}
        ep_num = parsed.absolute_episode if parsed.absolute_episode is not None else parsed.episode
        # ── Flat-umbrella episode consistency (parity with _match_cluster) ──
        # A flat-umbrella AniDB match (One Piece AID 69 → tvdb_season None) must
        # store the ABSOLUTE episode, exactly as the cluster path does. Without
        # this, a SINGLE-file scan of "One Piece S23E12 - 1167" kept its TVDB-
        # season-LOCAL number (12). The seasonal rename then feeds 12 to ScudLee,
        # which reads it as *absolute* episode 12 and misfiles the file into
        # "Season 01" — and the popup pairs it against the wrong episode (the
        # real ep 12, a 2000 episode) on top of its true 1167, rendering it
        # twice. A singleton has no cour routing, so routed_aid is always None.
        # Untouched for TV and per-season/cour anime (is_flat_umbrella False).
        if scored[0].provider == "anidb" and ep_num is not None:
            try:
                from kira.providers.anime_mappings import AnimeMappings
                _flat = (await AnimeMappings.tvdb_season(int(scored[0].provider_id))) is None
            except Exception:
                _flat = False
            if _flat:
                _a2l = {
                    d["absolute_number"]: d["episode"]
                    for d in _ep_dicts
                    if d.get("absolute_number") is not None and d.get("episode") is not None
                }
                # Same ambiguity guard as the cluster path: if two absolutes
                # share a local index, last-writer-wins could pick the wrong
                # season's absolute — drop ambiguous keys (file stays
                # un-remapped rather than mis-mapped).
                _l2a: dict[int, int] = {}
                _dropped: set[int] = set()
                for _ab, _loc in _a2l.items():
                    if _loc in _dropped:
                        continue
                    if _loc in _l2a and _l2a[_loc] != _ab:
                        del _l2a[_loc]
                        _dropped.add(_loc)
                    else:
                        _l2a[_loc] = _ab
                stored_ep = remap_umbrella_local_to_absolute(
                    ep_num, is_flat_umbrella=True, routed_aid=None, local_to_abs=_l2a,
                )
        if ep_num is not None and episodes_by_key:
            # When the absolute→AID reroute fired, the matcher stashed the
            # per-AID local episode on scored[0].raw. Pass it to tier 3.
            local_ep = (scored[0].raw or {}).get("local_episode") if scored[0].raw else None
            ep_title = _lookup_episode_title(
                episodes_by_key, scored[0].provider, parsed, ep_num,
                local_episode=local_ep,
            )

    # Rich metadata for the top match only (one extra call per file).
    top_metadata = None
    if scored:
        top = scored[0]
        top_metadata = await fetch_match_metadata(top.provider, top.provider_id, top.match_type, engine.registry)

    # Promote metadata's overview onto Match.overview when the search result
    # didn't include one (AniDB's case — its title-dump search returns no
    # description, but the TVDB/TMDB cross-ref does).
    top_overview_fallback = (top_metadata or {}).get("overview") if top_metadata else None

    # #14: a movie that belongs to a TMDB collection groups under one band in
    # the grid via series_group_id="tmdb-collection:<id>" (reusing the anime
    # franchise mechanism). Pulled from the selected match's metadata_blob.
    coll_id = (top_metadata or {}).get("collection_id") if top_metadata else None
    coll_name = (top_metadata or {}).get("collection_name") if top_metadata else None

    await detach_and_delete_matches(session, media_file_id=fid)
    for rank, m in enumerate(scored):
        gid = await compute_series_group_id(m.provider, m.provider_id, engine.registry)
        # For the selected movie match with a collection, the collection IS the
        # group so sibling films share a card-band.
        if rank == 0 and m.match_type == "movie" and coll_id:
            gid = f"tmdb-collection:{coll_id}"
        canonical_season = await resolve_canonical_season(m.provider, m.provider_id, parsed.season, episode=ep_num)
        row_overview = m.overview or (top_overview_fallback if rank == 0 else None)
        session.add(Match(
            media_file_id=fid,
            provider=m.provider, provider_id=m.provider_id,
            match_type=m.match_type, confidence=m.confidence,
            title=m.title, year=m.year,
            series_name=m.title if m.match_type == "tv_episode" else None,
            season_number=canonical_season, episode_number=stored_ep,
            episode_title=ep_title if rank == 0 else None,
            poster_url=m.poster_url, overview=row_overview,
            is_selected=(rank == 0),
            series_group_id=gid,
            collection_id=coll_id if rank == 0 else None,
            collection_name=coll_name if rank == 0 else None,
            metadata_blob=top_metadata if rank == 0 else None,
        ))


async def _match_cluster(session, engine, fids: list[int]) -> None:
    """Match a cluster of files sharing a series_key.

    Strategy: run engine.match() on the FIRST file to identify the series, then
    reuse the same scored candidates for every file in the cluster. Each file
    keeps its own episode_number/season_number from its own parsed data, but
    they all share the same provider/provider_id/title/year/poster.

    If the matched provider supports get_episodes(), fetch the season's
    episode list ONCE and use it to fill episode_title per file.
    """
    # Load all parsed data up front.
    files: list[tuple[int, ParsedFile]] = []
    for fid in fids:
        mf = await session.get(MediaFile, fid)
        if mf is None or not mf.parsed_data:
            continue
        files.append((fid, ParsedFile(**mf.parsed_data)))
    if not files:
        return

    # Pick the most "standard" episode as the cluster representative.
    # Otherwise os.walk's traversal order can hand us a Special, an OVA,
    # or a movie tie-in as files[0] and the search query for THAT skews
    # the entire cluster's match.
    def _rep_score(p: ParsedFile) -> tuple[int, int]:
        # Treat missing season/episode as "very high" so they sort LAST.
        s = p.season if p.season is not None else 999
        e = p.episode if p.episode is not None else (p.absolute_episode if p.absolute_episode is not None else 999)
        return (s, e)
    files_sorted = sorted(files, key=lambda f: _rep_score(f[1]))
    rep_parsed = files_sorted[0][1]

    # Cluster-level common-sequence title signal. Replaces the per-file
    # title in the cascade's scoring so a cluster of 26 "One Pace - S01EXX"
    # files scores against "one pace" (the cluster signal) not against
    # any single noisy filename. This is what kills the One Pace → ONE:
    # Kagayaku Kisetsu e false positive at the matcher level.
    from kira.matcher.cluster_signal import compute_cluster_signal
    titles_for_signal = [p.title for _, p in files if p.title]
    cluster_signal = compute_cluster_signal(titles_for_signal)
    # Stash the signal + parent path on the rep parsed object so the
    # cascade context can pick them up. Attribute-stash to avoid
    # threading new params through engine.match's existing signature.
    if cluster_signal:
        rep_parsed._cluster_signal = cluster_signal  # type: ignore[attr-defined]
    # Cluster max episode + size — used by EpisodeCountSanityMetric to
    # veto candidates that physically can't hold this many episodes
    # (1-episode movies/OVAs trying to match 40-file TV clusters).
    cluster_max_ep = max(
        (p.episode or p.absolute_episode or 0 for _, p in files),
        default=0,
    )
    rep_parsed._cluster_max_episode = cluster_max_ep  # type: ignore[attr-defined]
    rep_parsed._cluster_size = len(files)  # type: ignore[attr-defined]
    # Use the rep file's parent for folder identity. All cluster files
    # share a series_key so their parents are equivalent for our purposes.
    rep_mf = await session.get(MediaFile, files_sorted[0][0])
    if rep_mf and rep_mf.file_path:
        from pathlib import Path as _P
        rep_parsed._parent_path = str(_P(rep_mf.file_path).parent)  # type: ignore[attr-defined]

    # Identify-time xattr read for the cluster rep: a stamped id resolves the
    # whole cluster by id (no search). One read per cluster, not per file.
    await _apply_xattr_ids(rep_parsed, rep_mf.file_path if rep_mf else None)

    # Differentiate "API said no results" from "API call failed". On a
    # network failure we MUST NOT delete existing matches — a TMDB outage
    # would wipe the user's library otherwise.
    matched_ok = False
    scored: list = []
    try:
        scored = await engine.match(rep_parsed, limit=5)
        matched_ok = True
    except Exception as e:
        logger.info(f"_match_cluster: matcher raised for cluster of {len(files)} files: {e!r}")
        return  # leave existing Match rows untouched

    if not scored:
        # Successful call, zero results — that IS legitimate "no match", so
        # clear the cluster (it'll render as no_match in the UI).
        for fid, _ in files:
            await detach_and_delete_matches(session, media_file_id=fid)
        return

    top = scored[0]

    # Fetch the full episode list once for the matched series. Keyed by
    # (season, episode) so multi-season providers can't collide their
    # episode 1 from S01 with episode 1 from S02. AniDB returns season=1
    # for all entries (no season concept) which is fine — its AIDs are
    # per-season, so each cluster's get_episodes is already season-scoped.
    #
    # ── AniDB-ban-reduction: prefer TVDB cross-ref for episode list ───
    # AniDB's get_episodes hits the rate-limited HTTP API (1 call per 5s
    # min + 12h ban risk). For AniDB matches that have a Fribb mapping
    # to TVDB, fetch episodes from TVDB instead — same titles in better
    # English, no AniDB load. Falls back to AniDB direct only when no
    # Fribb cross-ref exists. This is the SAME pattern series.py uses
    # for the popup; doing it here means the scan-time matcher doesn't
    # burn AniDB calls just to populate episode_title fields.
    # Phase 4 validation gate: fetch the top's episode list AND, for western
    # TV with a poor-coverage TVDB/TMDB incumbent, re-rank to a better-fitting
    # alternate before we commit to `top`. No-op for anime/AniDB/cour paths
    # (handled by EpisodeCountSanityMetric + cour routing). The gate may
    # reorder `scored`, so re-read `top` afterwards — everything downstream
    # (cour routing, metadata, poster, group id) keys off the corrected top.
    episodes_by_key: dict[tuple[int, int], str | None] = {}
    ep_dicts: list[dict] = []
    if top.match_type == "tv_episode":
        try:
            scored, episodes_by_key, ep_dicts = await _validate_and_rerank_by_episodes(
                scored, files, rep_parsed.season, rep_parsed.media_type, engine.registry,
            )
            top = scored[0]
        except Exception as e:
            logger.warning(f"_match_cluster: episode validation/fetch failed: {e!r}")  # non-fatal

    # ── Per-file cour routing (Platinum solution) ──────────────────
    # When the cluster's top match is a Fribb-confirmed cour of a
    # multi-cour TVDB season, build a routing table mapping
    # absolute-episode-range → cour AID, so each file's Match row can
    # point at the AID that ACTUALLY owns its episode. Bleach S17
    # example:
    #   AID 15449 (Cour 1, 13 eps)  → files with parsed.episode in 1-13
    #   AID 17849 (Cour 2, 13 eps)  → files with parsed.episode in 14-26
    #   AID 18671 (Cour 3, 14 eps)  → files with parsed.episode in 27-40
    #
    # The user-facing card stays as ONE card because every file shares
    # the same series_group_id (= the franchise root, lowest AID). But
    # the per-file Match.provider_id reflects reality, so episode_title
    # comes from the right AniDB entry and renaming uses the right
    # canonical series name.
    #
    # Scope: ONLY fires for AniDB top match with Fribb-pinned cour AND
    # ≥2 sibling cours. Sets `cour_routing` to a list of
    # (start_ep, end_ep, aid, local_ep_offset) or None. Single-cour
    # anime / TV / movies → cour_routing stays None, original behavior.
    # Build the per-file cour routing table via the shared helper.
    # Single source of truth — `_rematch_one` calls the same builder so
    # scan-time and rematch-time routing decisions cannot diverge.
    cour_routing: list[tuple[int, int, int, int]] | None = None
    cour_episodes_by_aid: dict[int, list] = {}
    if top.match_type == "tv_episode":
        try:
            from kira.matcher.cour_routing import build_cour_routing_table
            cour_routing = await build_cour_routing_table(
                top.provider, top.provider_id, rep_parsed.season,
                registry=engine.registry,
            )
        except Exception as e:
            # Routing is best-effort. On ANY failure, fall back to
            # single-AID matching (no cour split).
            logger.warning(f"_match_cluster: cour routing build failed: {e!r}")
            cour_routing = None
        if cour_routing:
            logger.info(
                f"_match_cluster: cour routing for top AID {top.provider_id} "
                f"s={rep_parsed.season}: {cour_routing}"
            )
            # Pre-fetch each cour's episode list ONCE for the cluster.
            # Used as the per-file title-fallback when the top match's
            # episode list doesn't carry the lumped data (TVDB-split
            # case) — see the per-file fallback in the write loop below.
            for _start, _end, sib_aid, _off in cour_routing:
                try:
                    eps = await _fetch_episodes_for_match(
                        "anidb", str(sib_aid),
                        rep_parsed.season, engine.registry,
                    )
                    cour_episodes_by_aid[sib_aid] = list(eps)
                except Exception as e:
                    logger.info(
                        f"_match_cluster: cour {sib_aid} episode "
                        f"fetch failed: {e!r}"
                    )
                    cour_episodes_by_aid[sib_aid] = []

    # Compute the franchise identity once for the cluster. For AniDB this
    # walks the sequel chain (rate-limited first time, cached after).
    top_group_id = await compute_series_group_id(top.provider, top.provider_id, engine.registry)

    # Rich popup metadata — genres, cast, director, network, language,
    # country, runtime, native/romaji titles, etc. One extra call per
    # cluster (not per file). Saved on the TOP match's metadata_blob.
    top_metadata = await fetch_match_metadata(top.provider, top.provider_id, top.match_type, engine.registry)
    top_overview_fallback = (top_metadata or {}).get("overview") if top_metadata else None

    # Per-season poster art — multi-season TVDB/TMDB shows share ONE
    # provider_id across seasons but expose season-specific cover art
    # via `seasons[].image` (TVDB) or `/tv/{id}/season/{N}.poster_path`
    # (TMDB). The frontend splits these into per-season cards via the
    # `match|provider|provider_id|s<N>` grouping key; without this hook
    # every per-season card would carry the same series-level poster.
    # AniDB doesn't need this — each AID already has its own picture.
    if (
        top.match_type == "tv_episode"
        and top.provider in ("tvdb", "tmdb")
        and rep_parsed.season is not None
    ):
        try:
            poster_provider = engine.registry.build(top.provider)
            if hasattr(poster_provider, "get_season_poster"):
                season_url = await poster_provider.get_season_poster(
                    top.provider_id, rep_parsed.season,
                )
                if season_url:
                    top.poster_url = season_url
        except Exception as e:
            logger.warning(f"_match_cluster: per-season poster fetch failed: {e!r}")
    # Other-rank matches each get their own group id (they're alternate
    # candidates, not part of this series).
    other_group_ids: dict[int, str] = {}
    for i, m in enumerate(scored[1:], start=1):
        other_group_ids[i] = await compute_series_group_id(m.provider, m.provider_id, engine.registry)

    # Bipartite refinement: for clusters of ≥3 files, run the file-to-
    # episode assignment over the fetched episode list using multiple
    # metrics in order (exact → absolute → episode-number). This catches
    # the One Piece S23E1158 case where strict (season, episode) misses
    # but episode-number-alone hits AniDB's flat S1 list.
    from kira.matcher.bipartite import (
        assign_files_to_episodes,
        MIN_CLUSTER_FOR_BIPARTITE,
    )
    bipartite_assignments: dict[int, object] = {}
    if (
        top.match_type == "tv_episode"
        and len(files) >= MIN_CLUSTER_FOR_BIPARTITE
        and episodes_by_key
    ):
        # Use the rich episode dicts from the validation gate — they carry
        # air_date (Phase 9's bipartite air-date pass needs it), which the
        # title-only episodes_by_key map drops. Fall back to reconstructing
        # from episodes_by_key if the gate returned none.
        ep_list = ep_dicts or [
            {"season": s, "episode": e, "title": t}
            for (s, e), t in episodes_by_key.items()
        ]
        bipartite_assignments = assign_files_to_episodes(files, ep_list)

        # Phase 18: DVD-order retry. Anime fansubs sometimes follow DVD order,
        # which the aired-order episode list pairs wrong. For a TVDB-matched
        # anime cluster with files the aired pass left orphaned, fetch the DVD
        # ordering ONCE and re-pair only those files. Bounded + best-effort;
        # AniDB-matched clusters (the common case) never reach here.
        if (
            rep_parsed.media_type == "anime"
            and top.provider == "tvdb"
            and bipartite_assignments
        ):
            unpaired = [
                (fid, p) for fid, p in files
                if (fid not in bipartite_assignments
                    or bipartite_assignments[fid].matched_via == "unpaired")
            ]
            if unpaired and engine.registry.has("tvdb"):
                try:
                    tvdb = engine.registry.build("tvdb")
                    dvd_eps = await tvdb.get_episodes(
                        top.provider_id, rep_parsed.season or 1, order="dvd",
                    )
                    dvd_dicts = [
                        {"season": e.season, "episode": e.episode,
                         "title": e.title, "air_date": getattr(e, "air_date", None),
                         "absolute_number": getattr(e, "absolute_number", None)}
                        for e in dvd_eps
                    ]
                    if dvd_dicts:
                        for fid, a in assign_files_to_episodes(unpaired, dvd_dicts).items():
                            if a.matched_via != "unpaired":
                                bipartite_assignments[fid] = a
                except Exception as e:
                    logger.warning(f"_match_cluster: DVD-order retry failed: {e!r}")

    # ── Franchise rescue (episode-title + franchise-absolute) ─────────
    # Files every number pass left unpaired may belong to a DIFFERENT cour
    # of the same franchise — a prior rename can leave synthetic seasons +
    # franchise-continuous numbers ("AoT S05E61 - Midnight Train") whose
    # episode isn't in the matched AID's own list at all. Rescue via static
    # franchise metadata: the offsets table places the number (E61 → Final
    # Season local 2), and the filename's episode-title guess is matched
    # across the sibling cours' lists. Results flow through the same
    # per-file routed-AID plumbing as the cour table below.
    franchise_rescued: dict[int, tuple[int, int, str | None]] = {}
    if rep_parsed.media_type == "anime" and top.provider == "anidb":
        unpaired_files = [
            (fid, p) for fid, p in files
            if (fid not in bipartite_assignments
                or bipartite_assignments[fid].matched_via == "unpaired")
        ]
        if unpaired_files:
            try:
                franchise_rescued = await _franchise_rescue_unpaired(
                    unpaired_files, top.provider_id, engine.registry,
                )
                if franchise_rescued:
                    logger.info(f"_match_cluster: franchise rescue placed {len(franchise_rescued)} file(s)")
            except Exception as e:
                logger.warning(f"_match_cluster: franchise rescue failed (non-fatal): {e!r}")

    # ── Title arbitration: the file's own episode title outranks a lying
    # number. Old renames can stamp wrong SxE numbers ("S04E13 - The Town
    # Where Everything Began" is really S3E13) — the number passes then place
    # the file on the wrong episode at full confidence, and the REAL owner of
    # that slot shows up as a phantom duplicate. For every number-placed file
    # whose title guess CONTRADICTS the placed episode's title (trigram < 0.2),
    # search the franchise BY TITLE; a strong (≥0.6) hit elsewhere overrides
    # the number placement. No strong hit anywhere (e.g. romaji-vs-English
    # list) → the number stands, nothing degrades.
    title_overrides: dict[int, tuple[int, int, str | None]] = {}
    if rep_parsed.media_type == "anime" and top.provider == "anidb" and episodes_by_key:
        from kira.matcher.similarity import trigram_similarity
        challenged: list[tuple[int, object]] = []
        for fid, p in files:
            guess = getattr(p, "episode_title_guess", None)
            if not guess or fid in franchise_rescued:
                continue
            a = bipartite_assignments.get(fid)
            if a is not None and getattr(a, "matched_via", "") == "title":
                continue  # paired BY title — already in agreement
            placed_title = None
            if a is not None and getattr(a, "matched_via", "") != "unpaired":
                placed_title = a.episode_title
            elif p.episode is not None:
                placed_title = (
                    episodes_by_key.get((1, p.episode))
                    or episodes_by_key.get((p.season or 1, p.episode))
                )
            if placed_title and trigram_similarity(guess, placed_title) < 0.2:
                challenged.append((fid, p))
        if challenged:
            try:
                title_overrides = await _franchise_rescue_unpaired(
                    challenged, top.provider_id, engine.registry, title_only=True,
                )
                if title_overrides:
                    logger.info(f"_match_cluster: title arbitration overrode {len(title_overrides)} number placement(s)")
            except Exception as e:
                logger.warning(f"_match_cluster: title arbitration failed (non-fatal): {e!r}")

    # Absolute→season-local map for cour routing. Lets pure-absolute-numbered
    # files (AoT Final Season "- 60".."- 89") reach the season-local cour table:
    # 60→S4E1 … 89→S4E30 → routed to AID 14977 / 16177 / 17303. Built from the
    # fetched episode list's absolute_number↔episode pairs; empty (harmless) for
    # shows whose provider list carries no absolute numbers.
    abs_to_local: dict[int, int] = {
        d["absolute_number"]: d["episode"]
        for d in ep_dicts
        if d.get("absolute_number") is not None and d.get("episode") is not None
    }
    # ── Flat-umbrella detection (the One Piece "S23E04" → 1159 fix) ──
    # A FLAT umbrella is a single AniDB AID that holds the WHOLE long-runner as
    # one absolute list (One Piece 69, Naruto, Detective Conan — Fribb has NO
    # `season.tvdb`, so `tvdb_season() is None`). For such an AID the canonical
    # episode number IS the absolute, so a file that arrived in TVDB-season-
    # LOCAL form ("One Piece 1999 S23E04") must be remapped to its absolute
    # (1159) to line up with its absolute-numbered siblings ("S23E1159") — which
    # are in fact the SAME episode (the local file is a dup). `local_to_abs` is
    # the reverse of `abs_to_local`; the remap (in the write loop) only fires
    # when the bipartite-stored number IS a known local index whose absolute
    # DIFFERS — so absolute-named files (1159, not a local key) are untouched,
    # and an early-cour file where absolute == local (One Piece ep 4 in 1999)
    # remaps to itself (no-op).
    #
    # Per-SEASON AIDs are deliberately EXCLUDED: Frieren S2 (tvdb_season=2) and
    # AoT's Final-Season cours (tvdb_season=4) carry a real `season.tvdb`, and
    # their AniDB episode lists ARE local — remapping them to absolute would be
    # the bug, not the fix. Verified: tvdb_season(69)=None, (18886)=2, (14977)=4.
    is_flat_umbrella = False
    if top.match_type == "tv_episode" and top.provider == "anidb":
        try:
            from kira.providers.anime_mappings import AnimeMappings
            is_flat_umbrella = (await AnimeMappings.tvdb_season(int(top.provider_id))) is None
        except (ValueError, TypeError):
            is_flat_umbrella = False
        except Exception as e:
            logger.warning(f"_match_cluster: flat-umbrella check failed for {top.provider_id}: {e!r}")
            is_flat_umbrella = False
    # Reverse abs→local into local→abs for the flat-umbrella remap. `abs_to_local`
    # is keyed by absolute (unique), but the REVERSE can collide: if the episode
    # list spans seasons that reuse a local number, a naive dict comprehension
    # would let last-writer-wins pick an arbitrary — possibly wrong-season —
    # absolute. Drop any ambiguous local key (maps to >1 absolute) so its file is
    # simply left un-remapped rather than mis-mapped. For a true flat umbrella
    # (unique locals) this is identical to the old comprehension.
    local_to_abs: dict[int, int] = {}
    _ambiguous_locals: set[int] = set()
    for ab, loc in abs_to_local.items():
        if loc in _ambiguous_locals:
            continue
        if loc in local_to_abs and local_to_abs[loc] != ab:
            del local_to_abs[loc]
            _ambiguous_locals.add(loc)
        else:
            local_to_abs[loc] = ab

    # Write one Match per file. Same series identity for all; per-file
    # episode info from each file's own parsed_data OR the bipartite
    # assignment (which may have resolved a season/episode disagreement).
    for fid, parsed in files:
        await detach_and_delete_matches(session, media_file_id=fid)

        # ── Per-file cour routing (Platinum solution, step 2) ────────
        # When the cluster top match is a Fribb-pinned cour AND we built
        # a routing table above, find which cour's AID actually owns
        # this file's episode number. routed_aid stays None when:
        #   - no cour routing in scope (cour_routing is None)
        #   - file has no parsed.episode (and no absolute_episode)
        #   - episode falls outside every cour range (anomalous file)
        # In all those cases we preserve the original per-cluster
        # behavior (single AID for all files).
        routed_aid: int | None = None
        routed_local_ep: int | None = None
        if cour_routing is not None:
            # Prefer parsed.episode (season-local). cour_routing keys
            # are season-local episode numbers (1..N across the season).
            # Fall back to absolute_episode only if no season episode
            # was parsed (rare in multi-cour S-tagged clusters).
            file_ep_for_routing = (
                parsed.episode if parsed.episode is not None
                else parsed.absolute_episode
            )
            from kira.matcher.cour_routing import route_file_to_cour
            routed = route_file_to_cour(cour_routing, file_ep_for_routing, abs_to_local)
            if routed is not None:
                routed_aid, routed_local_ep = routed

        # Title arbitration outranks number routing — a lying number must not
        # beat the file's own episode title. Franchise rescue fills in when
        # nothing claimed the file. Both carry the cour-LOCAL episode number +
        # the (lumped-list-resolved) title.
        rescued_local_ep: int | None = None
        rescued_title: str | None = None
        _arb = title_overrides.get(fid)
        if _arb is None and routed_aid is None:
            _arb = franchise_rescued.get(fid)
        if _arb is not None:
            routed_aid, rescued_local_ep, rescued_title = _arb
            routed_local_ep = None

        # Prefer bipartite assignment when it found a real pair —
        # otherwise fall back to per-file parsed data.
        assignment = bipartite_assignments.get(fid)
        if assignment is not None and assignment.matched_via != "unpaired":
            ep_num = assignment.episode_number
            ep_title = assignment.episode_title
        else:
            # Default episode_number: the file's own parsed number (absolute
            # if present, else season-local). For cour-routed files this is
            # OVERRIDDEN below to the cour-local number so the stored Match
            # stays consistent with its cour AID's own episode list (which is
            # what the popup pairs against). The rename FILENAME is unaffected
            # either way — it renders from parsed.episode / {{absx}}.
            ep_num = parsed.absolute_episode if parsed.absolute_episode is not None else parsed.episode
            local_ep = (top.raw or {}).get("local_episode") if top.raw else None
            # First attempt: look up via the TOP match's episode list
            # using the season-absolute parsed.episode. Works when TVDB
            # lumps all cours under one season (most common case for
            # multi-cour anime — TVDB tends to model an anime "season"
            # as the full broadcast year, not the per-cour split AniDB
            # uses).
            ep_title = _lookup_episode_title(
                episodes_by_key, top.provider, parsed, ep_num,
                local_episode=local_ep,
            )
            # Second attempt (cour-routing fallback): when ep_title is
            # still None but cour routing assigned a local episode, the
            # TVDB-lumped lookup missed (TVDB-split case, or short TVDB
            # data). Try the routed AID's own episode list with the
            # cour-local number — for AID 18671's Cour 3 file with
            # routed_local_ep=1, this looks up "(1, 1)" in Cour 3's own
            # 14-episode list and finds the right title without needing
            # parsed.episode to land in the top's list.
            if (
                ep_title is None
                and routed_aid is not None
                and routed_local_ep is not None
            ):
                routed_eps = cour_episodes_by_aid.get(routed_aid, [])
                if routed_eps:
                    routed_eb_key: dict[tuple[int, int], str | None] = {
                        (ep.season, ep.episode): ep.title for ep in routed_eps
                    }
                    # The fetched list is the LUMPED season list (cross-ref) —
                    # look up by the lumped index, same convention as ep_num.
                    _r_off = next(
                        (off for (_s, _e, cid, off) in (cour_routing or []) if cid == routed_aid), 0,
                    )
                    ep_title = _lookup_episode_title(
                        routed_eb_key, "anidb", parsed, routed_local_ep + _r_off,
                    )
            # Franchise-rescued files carry the title resolved from the
            # sibling cour's own episode list during the rescue.
            if ep_title is None and rescued_title is not None:
                ep_title = rescued_title

        # ── Episode number MUST match the matched AID's own numbering ──
        # When cour routing / rescue / arbitration fires, the Match identity is
        # the cour AID, and the POPUP pairs against that AID's ANIDB-NATIVE
        # list (locals 1..N — native is preferred since the One Piece fix in
        # /series). So the stored number is the cour-LOCAL one. Titles are a
        # different story: the scan-side lists come from the TVDB cross-ref
        # (LUMPED per TVDB season), so title LOOKUPS convert local → lumped via
        # the cour's in-season offset — but the stored NUMBER never does. The
        # rename FILENAME is unaffected (renders from parsed.episode / {{absx}}).
        if routed_aid is not None and routed_local_ep is not None:
            ep_num = routed_local_ep
        elif rescued_local_ep is not None:
            ep_num = rescued_local_ep
            if fid in title_overrides:
                # Arbitration beats whatever the number-based lookups resolved:
                # the file's own title decided BOTH the number and the title.
                ep_title = rescued_title

        # ── Flat-umbrella absolute remap (the One Piece "S23E04" → 1159 fix) ──
        # A TVDB-season-LOCAL file ("One Piece 1999 S23E04" → bipartite pairs it
        # to the Elbaf cour's LOCAL episode 4 and stores 4) matched to the flat
        # AniDB umbrella (AID 69) must store the ABSOLUTE (1159) so it lines up
        # with its absolute-numbered siblings ("S23E1159" → 1159) and is seen as
        # the dup it is. The helper no-ops for absolute-named files, per-season
        # AIDs (Frieren S2 / AoT cours, is_flat_umbrella False), normal TV (empty
        # map), and early-cour self-maps. The rename FILENAME is independent.
        ep_num = remap_umbrella_local_to_absolute(
            ep_num,
            is_flat_umbrella=is_flat_umbrella,
            routed_aid=routed_aid,
            local_to_abs=local_to_abs,
        )

        # When cour routing fires, look up the cour AID's display
        # title from AniDB's in-memory cache so each cour-card carries
        # its OWN canonical title ("Bleach Cour 1" / "Bleach Cour 2" /
        # "Bleach Cour 3"). Falls back to the top match's title when
        # AniDB hasn't loaded the cache yet (cold start) or when the
        # AID isn't found in the dump.
        row_title_override: str | None = None
        if routed_aid is not None:
            try:
                from kira.providers.anidb import AniDBProvider
                row_title_override = AniDBProvider._pick_display_title(routed_aid)
            except Exception:
                row_title_override = None
        for rank, m in enumerate(scored):
            # Per-file cour routing intercepts the TOP candidate's
            # provider_id (rank == 0). Other candidates keep their
            # original provider_id so alternate-candidate UX still
            # works. This is the heart of the per-file routing:
            # Match.provider_id reflects which AID ACTUALLY owns this
            # file's episode, not the cluster-level top winner.
            row_provider_id = m.provider_id
            row_title = m.title
            if rank == 0 and routed_aid is not None:
                row_provider_id = str(routed_aid)
                if row_title_override:
                    row_title = row_title_override

            # Canonical season — for AniDB matches, Fribb mapping is the
            # ground truth (each AID = one TVDB season). Parser's guess
            # from the folder name is the fallback for other providers.
            # Use the (possibly-routed) row_provider_id so a sibling cour
            # AID's canonical season is consulted (still = parsed.season
            # since all cours of a TVDB season share one season number).
            canonical_season = await resolve_canonical_season(m.provider, row_provider_id, parsed.season, episode=ep_num)
            # For non-top candidates, preserve them so the user can override.
            # episode_title only meaningful on the selected (top) candidate.
            row_overview = m.overview or (top_overview_fallback if rank == 0 else None)
            # Merge the cascade trace (if present) into metadata_blob so
            # the frontend can render "why this confidence?" on hover.
            # Only the top candidate gets the rich top_metadata; alternates
            # get just their cascade_trace.
            row_metadata: dict | None = None
            cascade_trace = (m.raw or {}).get("cascade_trace") if m.raw else None
            if rank == 0:
                row_metadata = dict(top_metadata or {})
                if cascade_trace:
                    row_metadata["cascade_trace"] = cascade_trace
            elif cascade_trace:
                row_metadata = {"cascade_trace": cascade_trace}
            # Confidence honesty: the cascade score is the SHOW-level identity.
            # When we HAD an episode list yet every pairing avenue failed for
            # this file (no bipartite pair, no cour routing, no franchise
            # rescue, no episode title), displaying the show's 1.0 as "100%"
            # on an orphaned row is a lie — and dangerous around auto-approve.
            # Cap it visibly below every approval threshold; the file still
            # matches the show, but the row now SAYS the episode is unresolved.
            row_confidence = m.confidence
            if (
                rank == 0
                and top.match_type == "tv_episode"
                and episodes_by_key
                and ep_title is None
                and routed_aid is None
                and (assignment is None or assignment.matched_via == "unpaired")
            ):
                row_confidence = min(row_confidence, 0.49)
            session.add(Match(
                media_file_id=fid,
                provider=m.provider, provider_id=row_provider_id,
                match_type=m.match_type, confidence=row_confidence,
                title=row_title, year=m.year,
                series_name=row_title if m.match_type == "tv_episode" else None,
                season_number=canonical_season, episode_number=ep_num,
                episode_title=ep_title if rank == 0 else None,
                poster_url=m.poster_url, overview=row_overview,
                is_selected=(rank == 0),
                # series_group_id stays as the franchise root across ALL
                # routed cours so the visual franchise grouping in the
                # frontend stays intact (one heading, one card per cour).
                series_group_id=top_group_id if rank == 0 else other_group_ids.get(rank),
                metadata_blob=row_metadata,
            ))
    _ = matched_ok  # marker — keeps the success path readable


async def _try_cross_ref(provider, provider_id: str, season: int) -> tuple[bool, list]:
    """EE-1: Returns (ok, result).

    `ok=True` → the HTTP call completed cleanly, even if the response is
    an empty list. "0 episodes for this season" is a VALID answer (e.g.
    a future season that hasn't aired yet, or a stale Fribb mapping
    pointing at a deleted TVDB row — both terminal, no point falling
    through to another provider).

    `ok=False` → transient / auth / connectivity failure. Caller should
    fall through to the next cross-ref provider or AniDB direct.

    Without this distinction, `if result:` (falsiness check) treats an
    empty success the same as an exception, which silently fires a
    rate-limited AniDB call for every stale Fribb mapping in the library
    — exactly the ban cascade `_fetch_episodes_for_match` exists to avoid.
    """
    try:
        return True, await provider.get_episodes(provider_id, season)
    except Exception as e:
        logger.warning(f"_fetch_episodes cross-ref failed for {provider_id}/s{season}: {e!r}")
        return False, []


async def _franchise_rescue_unpaired(
    unpaired: list[tuple[int, object]],
    top_provider_id: str,
    registry,
    title_only: bool = False,
) -> dict[int, tuple[int, int, str | None]]:
    """Rescue number/title-mangled anime files by searching the WHOLE franchise.

    The bipartite passes pair files only against the matched AID's own episode
    list. But a previously-renamed library can carry synthetic seasons +
    franchise-continuous numbers ("Attack on Titan - S05E61 - Midnight Train":
    season 5 doesn't exist, E61 is franchise-continuous) — the episode simply
    isn't in that list, so every file orphans. Two static-metadata fallbacks:

      1. NUMERIC — `get_franchise_offsets` (official AniDB counts, cached,
         ban-safe; never disk-state) maps a franchise-continuous number to its
         owning sibling AID + local episode: E61 → Final Season AID, local 2.
      2. TITLE — the filename's episode-title guess is trigram-matched across
         the sibling cours' episode lists ("Midnight Train" → Final Season E2),
         rescuing files whose numbers are pure garbage. Same ≥0.6 floor as the
         bipartite title pass; episode lists fetched once per sibling via the
         ban-safe TVDB cross-ref, capped to 12 siblings.

    Returns {file_id: (aid, local_episode, episode_title|None)} for the files
    it could place; the write loop feeds these through the same per-file
    routing plumbing the cour table uses. Best-effort: any failure → {}.
    """
    if not unpaired:
        return {}
    try:
        from kira.providers.anidb import AniDBProvider
        if not registry.has("anidb"):
            return {}
        anidb = registry.build("anidb")
        offsets = await anidb.get_franchise_offsets(top_provider_id)
    except Exception as e:
        logger.warning(f"_franchise_rescue: offsets unavailable (non-fatal): {e!r}")
        return {}
    if not offsets or len(offsets) > 12:
        return {}

    # Each cour's offset WITHIN its TVDB season. The episode fetch below goes
    # through the TVDB cross-ref, which returns ONE LUMPED list for the whole
    # TVDB season (AoT S4 = 28+ entries) — the SAME list for every cour in it.
    # So the number we store (and look titles up by) must be the LUMPED index,
    # not the cour-local one: Part 2's local E6 is lumped E22 ("Thaw"), and
    # storing 6 made the popup display Part 1's E6 ("The War Hammer Titan").
    # in_season_off[aid] = sum of the official lengths of the PRIOR cours in
    # the same TVDB season (0 for the season's first cour; flat umbrellas too).
    from kira.providers.anime_mappings import AnimeMappings
    in_season_off: dict[int, int] = {}
    _season_of: dict[int, int | None] = {}
    for aid, lo, _hi in offsets:
        try:
            _season_of[aid] = await AnimeMappings.tvdb_season(aid)
        except Exception:
            _season_of[aid] = None
    _season_base: dict[int, int] = {}
    for aid, lo, _hi in offsets:
        s = _season_of.get(aid)
        if s is not None:
            _season_base[s] = min(_season_base.get(s, lo), lo)
    for aid, lo, _hi in offsets:
        s = _season_of.get(aid)
        in_season_off[aid] = (lo - _season_base[s]) if s is not None else 0

    # Lazily-fetched per-sibling episode lists, shared by both passes.
    ep_lists: dict[int, list] = {}

    async def _eps_for(aid: int) -> list:
        if aid not in ep_lists:
            try:
                ep_lists[aid] = list(await _fetch_episodes_for_match(
                    "anidb", str(aid), None, registry,
                ))
            except Exception:
                ep_lists[aid] = []
        return ep_lists[aid]

    rescued: dict[int, tuple[int, int, str | None]] = {}
    claimed: set[tuple[int, int]] = set()

    # The matched AID's own official length — the disambiguation gate for the
    # numeric pass. A number ≤ this could be a perfectly normal cour-LOCAL
    # episode (Frieren S2 E03), so reinterpreting it as franchise-continuous
    # would mis-route it; only numbers BEYOND the matched AID's own range are
    # provably not local and safe to place via the franchise offsets.
    try:
        _top_aid = int(top_provider_id)
    except (ValueError, TypeError):
        _top_aid = None
    top_count = next(
        (hi - lo + 1 for aid, lo, hi in offsets if aid == _top_aid), None,
    )

    # Pass 1 — numeric: episode falls inside a sibling's official absolute range.
    # Stored number = the cour-LOCAL episode (the popup pairs against the AID's
    # ANIDB-NATIVE 1..N list); the TITLE is read from the cross-ref list, which
    # is LUMPED per TVDB season, so the lookup converts local → lumped via the
    # cour's in-season offset. Skipped in title_only mode (arbitration: the
    # NUMBER is what's on trial).
    for fid, parsed in unpaired if not title_only else []:
        ep = parsed.episode if parsed.episode is not None else parsed.absolute_episode
        if ep is None:
            continue
        if top_count is not None and ep <= top_count:
            continue  # could be cour-local — leave it to the title pass
        for aid, lo, hi in offsets:
            if lo <= ep <= hi:
                local = ep - lo + 1
                if (aid, local) in claimed:
                    break
                lumped = local + in_season_off.get(aid, 0)
                title = None
                for e in await _eps_for(aid):
                    if getattr(e, "episode", None) == lumped:
                        title = getattr(e, "title", None)
                        break
                rescued[fid] = (aid, local, title)
                claimed.add((aid, local))
                break

    # Pass 2 — title: trigram the filename's episode-title guess across every
    # sibling's list. Only for files the numeric pass couldn't place. Because
    # cours of one TVDB season share the SAME lumped cross-ref list, an entry
    # only counts for the cour whose own stretch contains it — otherwise every
    # cour would "find" every title and claim the wrong AID. The stored number
    # is converted back to the cour-LOCAL one (lumped − in-season offset).
    from kira.matcher.similarity import trigram_similarity
    for fid, parsed in unpaired:
        if fid in rescued:
            continue
        guess = getattr(parsed, "episode_title_guess", None)
        if not guess:
            continue
        best: tuple[float, int, int, str | None] | None = None
        for aid, lo, hi in offsets:
            off = in_season_off.get(aid, 0)
            stretch_lo = off + 1
            stretch_hi = off + (hi - lo + 1)
            for e in await _eps_for(aid):
                t = getattr(e, "title", None)
                num = getattr(e, "episode", None)
                if not t or num is None:
                    continue
                if not (stretch_lo <= num <= stretch_hi):
                    continue  # entry belongs to a sibling cour's stretch
                local = num - off
                if (aid, local) in claimed:
                    continue
                sim = trigram_similarity(guess, t)
                if best is None or sim > best[0]:
                    best = (sim, aid, local, t)
        if best is not None and best[0] >= 0.6:
            rescued[fid] = (best[1], best[2], best[3])
            claimed.add((best[1], best[2]))
    return rescued


async def _fetch_episodes_for_match(
    provider_key: str,
    provider_id: str,
    season: int | None,
    registry,
):
    """Fetch a series' episode list, preferring TVDB cross-ref for AniDB.

    AniDB-ban hardening: AniDB's HTTP API is rate-limited (1 call per 5s
    minimum, with 12h IP-bans for violations). Every avoidable AniDB call
    is a small dent in our ban exposure. For AniDB-matched series that
    have a Fribb cross-reference to TVDB or TMDB (most of them do), we
    fetch the episode list from TVDB instead — which is:
      - not rate-limited at the same fragility
      - English by default (better UX than AniDB's romaji-by-default)
      - already loaded by other parts of the matcher

    Falls back to AniDB direct only when no cross-ref exists. Falls back
    to `[]` on any failure so the caller degrades gracefully to generic
    "Episode N" titles rather than crashing the cluster.

    For non-AniDB providers, this is a thin wrapper around
    `provider.get_episodes` that adds the same defensive try/except.

    EE-1 hardening: empty-success and exception-failure are now
    distinguished (see `_try_cross_ref`), and the AniDB circuit breaker
    is consulted BEFORE queuing a direct call so bulk workers don't
    enqueue 800 calls behind a 5s lock just to short-circuit them all.
    """
    # ── AniDB → TVDB / TMDB cross-ref preferred (saves rate-limited call)
    if provider_key == "anidb":
        try:
            from kira.providers.anime_mappings import AnimeMappings
            aid_i = int(provider_id)
        except (ValueError, TypeError):
            aid_i = None

        if aid_i is not None:
            tvdb_id = await AnimeMappings.tvdb_id(aid_i)
            if tvdb_id and registry.has("tvdb"):
                try:
                    tvdb = registry.build("tvdb")
                    # Fribb usually carries the canonical season number;
                    # fall back to the caller's season hint, then to 1.
                    cross_season = await AnimeMappings.tvdb_season(aid_i) or season or 1
                    ok, result = await _try_cross_ref(tvdb, str(tvdb_id), cross_season)
                    # EE-1: trust ANY successful response, even empty.
                    # An empty list is a valid answer; only transient
                    # failures (ok=False) justify falling through.
                    if ok:
                        # Bug-fix: preserve the AniDB contract that callers
                        # downstream expect — `_lookup_episode_title` has a
                        # documented tier "(1, ep_num) works for AniDB
                        # because AniDB always returns season=1". TVDB
                        # returns the REAL season (e.g. Frieren S2 →
                        # season=2), which made the lookup miss EVERY
                        # episode for AniDB-matched files. Rewrite the
                        # cross-ref response to season=1 before returning
                        # so downstream stays substrate-agnostic.
                        return [ep.model_copy(update={"season": 1}) for ep in result]
                except Exception as e:
                    logger.warning(f"_fetch_episodes cross-ref TVDB setup failed for AID {provider_id}: {e!r}")

            tmdb_id = await AnimeMappings.tmdb_tv_id(aid_i)
            if tmdb_id and registry.has("tmdb"):
                try:
                    tmdb = registry.build("tmdb")
                    cross_season = await AnimeMappings.tvdb_season(aid_i) or season or 1
                    ok, result = await _try_cross_ref(tmdb, str(tmdb_id), cross_season)
                    if ok:
                        # Same AniDB-contract normalization as TVDB above.
                        return [ep.model_copy(update={"season": 1}) for ep in result]
                except Exception as e:
                    logger.warning(f"_fetch_episodes cross-ref TMDB setup failed for AID {provider_id}: {e!r}")
        # Cross-ref unavailable / both providers transiently failed —
        # we're about to consider AniDB direct. EE-1: check the circuit
        # breaker BEFORE queuing, so a bulk worker fanning out 800 stale
        # mappings doesn't fight a 5s lock just to short-circuit them all.
        try:
            from kira.providers.anidb import AniDBProvider
            if AniDBProvider._circuit_open():
                return []
        except Exception:
            pass

    # ── Direct provider call (non-AniDB OR AniDB cross-ref unavailable)
    if not registry.has(provider_key):
        return []
    try:
        p = registry.build(provider_key)
    except (ValueError, NotImplementedError):
        return []
    if not hasattr(p, "get_episodes"):
        return []
    season_for_lookup = season if season is not None else 1
    try:
        return await p.get_episodes(provider_id, season_for_lookup)
    except Exception as e:
        logger.warning(f"_fetch_episodes direct {provider_key} failed: {e!r}")
        return []


async def _validate_and_rerank_by_episodes(
    scored: list,
    files: list[tuple[int, ParsedFile]],
    season: int | None,
    media_type: str,
    registry,
) -> tuple[list, dict[tuple[int, int], str | None], list[dict]]:
    """Phase 4 episode-list validation gate.

    Returns ``(possibly_reordered_scored, top_episodes_by_key)``. The top
    candidate's episode list is always fetched (the caller needs it for
    title lookup anyway), so this is a drop-in replacement for the old
    "fetch the top's episode list" block — with one addition:

    For a **western-TV** cluster (``media_type == "tv"``) whose TOP candidate
    is **TVDB/TMDB**, verify the cluster's episodes actually EXIST in that
    candidate's episode list. When coverage is very low AND an alternate
    TVDB/TMDB candidate covers materially better, promote the alternate to
    rank 0 and return ITS episode list.

    Deliberately scoped OUT of the anime / AniDB / cour paths: there,
    per-cour coverage is *legitimately* partial (a 13-ep cour against a
    40-file franchise cluster), and ``EpisodeCountSanityMetric`` + cour
    routing + the absolute→AID reroute already do the resolution. Running a
    naive coverage gate there would wrongly promote the umbrella AID. The
    gate also no-ops unless there are ≥2 candidates and the top actually
    returned a non-empty episode list.

    Ban-safe: ``_fetch_episodes_for_match`` prefers the TVDB/TMDB cross-ref
    and consults the AniDB circuit breaker, and alternates are only probed
    when the incumbent's coverage is already below the floor (rare).
    """
    from kira.matcher.episode_validation import coverage, should_promote

    def _to_dicts(eps) -> list[dict]:
        # Rich episode dicts for the bipartite pairing. air_date feeds Phase 9's
        # air-date pass; absolute_number is LOAD-BEARING for long-runners — a
        # provider per-season list numbers episodes LOCALLY (One Piece S23 →
        # episode 1..13) but carries absolute_number 1156..1168. Dropping it
        # disarmed bipartite's absolute passes, so the only thing left was the
        # title pass, which stored the LOCAL index (1156→1). Keep it.
        return [
            {"season": e.season, "episode": e.episode,
             "title": e.title, "air_date": getattr(e, "air_date", None),
             "absolute_number": getattr(e, "absolute_number", None)}
            for e in eps
        ]

    if not scored:
        return scored, {}, []
    top = scored[0]

    top_eps = await _fetch_episodes_for_match(top.provider, top.provider_id, season, registry)
    top_by_key: dict[tuple[int, int], str | None] = {
        (ep.season, ep.episode): ep.title for ep in top_eps
    }
    top_dicts = _to_dicts(top_eps)

    # Gate scope — only western TV with a TVDB/TMDB incumbent.
    if (
        top.match_type != "tv_episode"
        or media_type != "tv"
        or top.provider not in ("tvdb", "tmdb")
        or len(scored) < 2
        or not top_by_key
    ):
        return scored, top_by_key, top_dicts

    file_eps = [
        (p.season, (p.episode if p.episode is not None else p.absolute_episode))
        for _fid, p in files
    ]
    # strict_season: western TV has real seasons, so disable the (1, episode)
    # fallback that would otherwise let a wrong series' season 1 inflate coverage.
    top_cov = coverage(file_eps, top_by_key, strict_season=True)
    from kira.matcher.episode_validation import COVERAGE_FLOOR
    if top_cov >= COVERAGE_FLOOR:
        return scored, top_by_key, top_dicts  # incumbent fits — no probing

    # Incumbent is suspicious. Probe alternate TVDB/TMDB candidates.
    best_idx = 0
    best_cov = top_cov
    best_by_key = top_by_key
    best_eps = top_eps
    for i in range(1, len(scored)):
        alt = scored[i]
        if alt.match_type != "tv_episode" or alt.provider not in ("tvdb", "tmdb"):
            continue
        alt_eps = await _fetch_episodes_for_match(alt.provider, alt.provider_id, season, registry)
        alt_by_key = {(ep.season, ep.episode): ep.title for ep in alt_eps}
        if not alt_by_key:
            continue
        alt_cov = coverage(file_eps, alt_by_key, strict_season=True)
        if alt_cov > best_cov:
            best_cov, best_idx, best_by_key, best_eps = alt_cov, i, alt_by_key, alt_eps

    if best_idx != 0 and should_promote(top_cov, best_cov):
        promoted = scored[best_idx]
        logger.info(
            f"_validate: episode-coverage re-rank — promoted "
            f"{promoted.provider}:{promoted.provider_id} (cov {best_cov:.2f}) over "
            f"{top.provider}:{top.provider_id} (cov {top_cov:.2f})"
        )
        reordered = [scored[best_idx]] + [s for i, s in enumerate(scored) if i != best_idx]
        return reordered, best_by_key, _to_dicts(best_eps)

    return scored, top_by_key, top_dicts


def _lookup_episode_title(
    episodes_by_key: dict[tuple[int, int], str | None],
    provider: str,
    parsed: ParsedFile,
    ep_num: int | None,
    local_episode: int | None = None,
) -> str | None:
    """Pick the right episode title from a fetched (season, episode) → title map.

    Three lookups, in order:
      1. `(parsed.season, ep_num)` — works for TMDB/TVDB multi-season shows.
      2. `(1, ep_num)` — works for AniDB (no season concept; everything
         comes back as season=1) and for files whose folder-season the
         provider doesn't model.
      3. `(1, local_episode)` — kicks in when the matcher's franchise
         reroute supplied a derived local episode (e.g. a `My Hero - 014`
         file rerouted from S1 AID to S2 AID, with `local_episode=1`).
         AniDB returns S2's episodes numbered 1..12 (not 14..25 absolute),
         so the `(1, 1)` lookup is the right one for the rerouted file.

    `provider` is accepted for caller convenience but unused — the
    fallback hits the right key regardless of who the provider is.
    """
    del provider
    if ep_num is None and local_episode is None:
        return None
    if ep_num is not None:
        season_for_key = parsed.season if parsed.season is not None else 1
        hit = episodes_by_key.get((season_for_key, ep_num))
        if hit is not None:
            return hit
        hit = episodes_by_key.get((1, ep_num))
        if hit is not None:
            return hit
    if local_episode is not None:
        hit = episodes_by_key.get((1, local_episode))
        if hit is not None:
            return hit
    return None


async def _apply_folder_series_lock(session, all_new: list[int]) -> int:
    """Phase 11: pull outlier files into their leaf folder's majority series.

    One mangled filename parses to a different title than its folder-mates and
    splinters into its own cluster (or matches the franchise's base AID) — the
    Attack on Titan "Final Season Part 3-01" / "Special 05" scattering. We
    follow FileBot's "one folder = one series" rule, conservatively: within a
    leaf folder, if a strict majority of TV/anime files agree on a series, the
    outliers are relocked to it (title + disambig unified, each file's own
    season preserved). The pure decision lives in ``matcher/folder_lock.py``.

    Returns the number of files relocked. Movies / music are never touched.
    """
    from collections import defaultdict as _dd

    from kira.matcher.folder_lock import FolderFile, compute_relocks

    by_folder: dict[str, list[tuple[int, MediaFile, FolderFile]]] = _dd(list)
    for fid in all_new:
        mf = await session.get(MediaFile, fid)
        if mf is None or not mf.file_path or mf.media_type not in ("tv", "anime"):
            continue
        season: int | None = None
        if mf.parsed_data:
            try:
                season = ParsedFile(**mf.parsed_data).season
            except Exception:
                season = None
        folder = str(Path(mf.file_path).parent).lower()
        by_folder[folder].append(
            (fid, mf, FolderFile(fid=fid, media_type=mf.media_type,
                                 series_key=mf.series_key, season=season))
        )

    relocked = 0
    for _folder, members in by_folder.items():
        if len(members) < 2:
            continue
        relocks = compute_relocks([ff for _fid, _mf, ff in members])
        if not relocks:
            continue
        mf_by_fid = {fid: mf for fid, mf, _ff in members}
        for fid, new_key in relocks.items():
            mf = mf_by_fid.get(fid)
            if mf is not None and mf.series_key != new_key:
                logger.info(
                    f"_folder_lock: relock file {fid} "
                    f"{mf.series_key!r} → {new_key!r}"
                )
                mf.series_key = new_key
                relocked += 1
    if relocked:
        await session.commit()
    return relocked


# CR-07: `_compute_series_key` / `_compute_variant_key` are imported at module
# top from kira.matcher.keys (as back-compat aliases). Their old bodies lived
# here; see the top-of-file import.


async def _selected_match_is_anime(
    provider: str | None, provider_id, match_type: str | None
) -> bool:
    """True when the selected match identifies an ANIME — independent of the
    file's folder.

    Two authoritative signals:
      • AniDB is an anime-only source → an AniDB match IS anime.
      • A TVDB/TMDB *series* whose id cross-references to an AniDB id in Fribb
        is also anime. Fribb catalogs ONLY anime, so an AID hit is definitive —
        the same signal `fribb_aid_filter` trusts. This catches anime sitting
        in a generic `/tv/` folder (so it never queried AniDB), e.g. a usenet
        download whose category folder is "tv".

    Movies are deliberately excluded (anime films are handled as movies, not
    flipped onto the anime-series shelf)."""
    if provider == "anidb":
        return True
    if provider in ("tvdb", "tmdb") and match_type == "tv_episode":
        try:
            pid = int(provider_id)
        except (TypeError, ValueError):
            return False
        from kira.providers.anime_mappings import AnimeMappings
        aid = (
            await AnimeMappings.aid_by_tvdb(pid) if provider == "tvdb"
            else await AnimeMappings.aid_by_tmdb_tv(pid)
        )
        return aid is not None
    return False


async def _match_music(session, fids: list[int]) -> None:
    """Match a music cluster via the ISOLATED `kira.music` subsystem, then write
    Match rows. Consulted ONLY at the `_match_phase` seam below, gated on the
    `music.enabled` setting. Best-effort — any failure leaves the files `no_match`
    and never raises into the scan worker. Music never touches the movie/TV/anime
    cascade (and the cascade never processes music: engine.match short-circuits it).
    """
    import httpx
    from sqlalchemy import delete as _sql_delete

    from kira.music import matcher as _mm
    from kira.music.tags import MusicTags, read_tags

    inputs: list[_mm.MusicFile] = []
    mfs: dict[int, MediaFile] = {}
    for fid in fids:
        mf = await session.get(MediaFile, fid)
        if mf is None or not mf.file_path:
            continue
        mfs[fid] = mf
        tags = read_tags(mf.file_path) or MusicTags()
        parsed = mf.parsed_data or {}
        inputs.append(_mm.MusicFile(
            file_id=fid, tags=tags,
            fb_artist=parsed.get("artist"),
            fb_album=parsed.get("album"),
            fb_title=parsed.get("track_title") or parsed.get("title"),
            fb_track_no=parsed.get("track") or parsed.get("track_no"),
            path=mf.file_path,
        ))
    if not inputs:
        return

    # AcoustID fingerprint fallback config — opt-in via the `providers.acoustid.
    # auto_fingerprint` toggle (default OFF); gated on fpcalc being installed. The
    # shipped Kira app key works out of the box (a `providers.acoustid.api_key`
    # setting overrides it). None → the matcher skips the fallback entirely.
    from kira.music import acoustid as _ac
    from kira import fpcalc_setup as _fp
    from kira.settings_store import get_raw, unwrap
    _aid_key: str | None = None
    try:
        # NOT bool(): a legacy string "false" is truthy. Only a real True or a
        # truthy string token enables fingerprinting (parity with every other
        # bool reader in the codebase).
        _af_raw = unwrap(await get_raw(session, "providers.acoustid.auto_fingerprint"))
        _af_on = _af_raw is True or (
            isinstance(_af_raw, str) and _af_raw.strip().lower() in ("1", "true", "yes", "on")
        )
        if _af_on and _fp.resolve_fpcalc():
            _aid_key = unwrap(await get_raw(session, "providers.acoustid.api_key")) or _ac.PROJECT_KEY
    except Exception as e:  # noqa: BLE001 — config read must never crash a scan
        logger.warning(f"_match_music: AcoustID config read failed (skipping fingerprint): {e!r}")

    try:
        async with httpx.AsyncClient() as client:
            results = await _mm.match_album(client, inputs, acoustid_key=_aid_key)
    except Exception as e:
        logger.warning(f"_match_music: matcher failed for cluster {fids}: {e!r}")
        results = []

    for r in results:
        mf = mfs.get(r.file_id)
        if mf is None:
            continue
        await session.execute(_sql_delete(Match).where(Match.media_file_id == r.file_id))
        if r.matched_via == "unpaired" or r.confidence <= 0:
            mf.status = "no_match"
            continue
        session.add(Match(
            media_file_id=r.file_id,
            provider="musicbrainz",
            provider_id=str(r.recording_id or r.release_id),
            match_type="track",
            confidence=r.confidence,
            title=r.title,
            year=r.year,
            series_name=r.album,                       # album = the "show" equivalent
            season_number=r.disc_no,                   # disc → "season"
            episode_number=r.track_no,                 # track → "episode"
            poster_url=r.cover_art_url,
            series_group_id=f"musicbrainz:{r.release_id}",
            is_selected=True,
            # Non-NULL blob (carries the artist + corrected fields the rename uses)
            # — also keeps music matches out of the metadata-IS-NULL heal sweep.
            metadata_blob={
                "music": True, "artist": r.artist, "album": r.album, "title": r.title,
                "track_no": r.track_no, "disc_no": r.disc_no, "year": r.year,
                "release_id": r.release_id, "recording_id": r.recording_id,
                "matched_via": r.matched_via,
            },
        ))
        mf.status = "matched"
    if not results:   # matcher couldn't resolve the album → leave the cluster no_match
        for fid, mf in mfs.items():
            await session.execute(_sql_delete(Match).where(Match.media_file_id == fid))
            mf.status = "no_match"
    await session.commit()


async def _match_phase(session, engine, fids: list[int], scan_id: int) -> int:
    """Cluster `fids` by series_key, then match each cluster (≥2 files) or
    singleton, updating the Scan row's live progress. Returns the number of
    files that ended with at least one Match row.

    Shared by the scan worker (new files) and the re-parse worker (existing
    files) so both paths cluster + match identically.
    """
    # Auto-approve config (Settings → Confidence). Read once per phase — a file
    # whose selected match clears the threshold is approved straight out of
    # matching instead of being held in the Review queue.
    auto_enabled, auto_th = await _read_auto_approve_setting(session)

    clusters: dict[str | int, list[int]] = defaultdict(list)
    for fid in fids:
        mf = await session.get(MediaFile, fid)
        if mf is None or not mf.parsed_data:
            continue
        bucket = mf.series_key if mf.series_key else fid
        clusters[bucket].append(fid)

    # ── Kira Packs: OVERRIDE pre-pass ───────────────────────────────────────
    # A folder-scoped `override` pack may win over the providers, so it runs
    # BEFORE matching. Claimed files get their pack Match written here and are
    # excluded from provider dispatch (otherwise the dispatcher's
    # detach_and_delete_matches would wipe the pack row we just wrote). Skipped
    # entirely when no override pack is installed (the common case). The far
    # more common FALLBACK path runs later, only for files that ended no_match.
    overridden: set[int] = set()
    try:
        from kira.packs.apply import any_override_bindings, try_pack_override
        if await any_override_bindings(session):
            for fid in fids:
                mf = await session.get(MediaFile, fid)
                if mf is None or not mf.parsed_data:
                    continue
                try:
                    if await try_pack_override(session, fid, mf):
                        overridden.add(fid)
                except Exception as e:
                    logger.warning(f"_match_phase: pack override failed for {fid}: {e!r}")
            if overridden:
                await session.commit()
    except Exception as e:
        logger.warning(f"_match_phase: pack override pre-pass failed (non-fatal): {e!r}")

    # NOTE: tech-tag MediaInfo enrichment does NOT run here anymore. Reading a
    # file's container headers is a slow NAS round-trip per file; doing it on the
    # match critical path made matching crawl whenever `parsing.read_mediainfo`
    # was on. It's now deferred to `enrich_mediainfo_background`, kicked off
    # AFTER the scan completes (see the scan worker) — so quality/codec/HDR/
    # channels chips fill in shortly after results appear, never blocking them.
    # The title rescue below stays inline: it's matching-essential (a file with no
    # parseable title would otherwise never match at all) and bounded to those.

    # Music subsystem gate, read ONCE. Music is an isolated plugin consulted only
    # at the dispatch below; default OFF so it can't affect non-music users.
    from kira.settings_store import get_raw, unwrap
    _mval = unwrap(await get_raw(session, "music.enabled"))
    music_enabled = _mval is True or (isinstance(_mval, str) and _mval.strip().lower() in ("true", "1", "yes", "on"))

    matched = 0

    # ── Two-lane dispatch (audit §3 C1) ──────────────────────────────────
    # The match phase used to be FULLY serial across clusters, so on a mixed
    # library every TMDB/TVDB cluster queued behind AniDB's protocol-mandated
    # ~4s-per-request pacing (30–90+ min for 10k files). Now:
    #   • SERIAL lane — anime + music clusters (AniDB / MusicBrainz are hard
    #     rate-limited; their pacing is per protocol rules and must stay
    #     sequential). Runs on THIS session, exactly like the old loop.
    #   • PARALLEL lane — movie/TV/unknown clusters, bounded width. Each task
    #     gets its OWN SessionLocal session (AsyncSession is not concurrency-
    #     safe); SQLite WAL + busy_timeout=15s serializes the actual writes,
    #     and clusters touch disjoint file ids so there are no row conflicts.
    # Progress (Scan.matched_count / current_path) is written ONLY here on the
    # phase session, per completed cluster.
    # Anime and music are BOTH rate-limited (AniDB ~1 req/5s, MusicBrainz
    # ~1 req/s) so each stays internally serial — but their limits are
    # independent providers, so the two lanes run CONCURRENTLY instead of
    # music stacking behind every AniDB sleep (and vice versa).
    _anidb_lane: list[tuple[str | int, list[int]]] = []
    _music_lane: list[tuple[str | int, list[int]]] = []
    _parallel_lane: list[tuple[str | int, list[int]]] = []
    for bucket_key, cfids in clusters.items():
        _first = await session.get(MediaFile, cfids[0])
        _mt = _first.media_type if _first is not None else None
        lane = (_anidb_lane if _mt == "anime"
                else _music_lane if _mt == "music"
                else _parallel_lane)
        lane.append((bucket_key, cfids))

    async def _bump_progress(rep_path: str | None) -> None:
        await _touch_db_scan_lock()   # keep the >6h-stale lock claim fresh mid-scan
        _sc = await session.get(Scan, scan_id)
        if _sc is not None:
            _sc.matched_count = matched
            if rep_path:
                _sc.current_path = rep_path
        await session.commit()

    if _parallel_lane:
        from kira.database import SessionLocal
        _sem = asyncio.Semaphore(_PARALLEL_MATCH_WIDTH)

        async def _run_parallel(bkey, bfids) -> tuple[int, str | None]:
            async with _sem:
                async with SessionLocal() as s2:
                    try:
                        return await _process_match_cluster(
                            s2, engine, bkey, bfids,
                            overridden=overridden, music_enabled=music_enabled,
                            auto_enabled=auto_enabled, auto_th=auto_th,
                        )
                    except Exception as e:
                        logger.warning(
                            f"_match_phase: parallel cluster {bkey!r} failed (isolated): {e!r}")
                        # Never strand rows in 'matching' — resolve the failed
                        # cluster to no_match so the scan still completes and the
                        # user can manually match. (The old serial code would
                        # have failed the ENTIRE scan here.)
                        try:
                            await s2.rollback()
                            for _fid in bfids:
                                _mf2 = await s2.get(MediaFile, _fid)
                                if _mf2 is not None and _mf2.status == "matching":
                                    _mf2.status = "no_match"
                            await s2.commit()
                        except Exception:
                            pass
                        return 0, None

        _tasks = [asyncio.create_task(_run_parallel(k, v)) for k, v in _parallel_lane]
        try:
            for _fut in asyncio.as_completed(_tasks):
                _n, _rep = await _fut
                matched += _n
                await _bump_progress(_rep)
        finally:
            # Cancellation propagates (scan stop) — don't orphan running tasks.
            for _t in _tasks:
                if not _t.done():
                    _t.cancel()
        # The worker sessions committed status changes this session's identity
        # map may still hold stale copies of — expire so every later read in
        # this phase/worker refetches fresh rows.
        session.expire_all()

    # Music lane: its own worker session (the phase session is busy in the
    # anime loop below and AsyncSession is not concurrency-safe). Serial
    # internally; failures isolate per-cluster like the parallel lane.
    async def _run_music_lane() -> int:
        if not _music_lane:
            return 0
        from kira.database import SessionLocal
        _lane_matched = 0
        async with SessionLocal() as s3:
            for bkey, bfids in _music_lane:
                try:
                    _n, _ = await _process_match_cluster(
                        s3, engine, bkey, bfids,
                        overridden=overridden, music_enabled=music_enabled,
                        auto_enabled=auto_enabled, auto_th=auto_th,
                    )
                    _lane_matched += _n
                except Exception as e:
                    logger.warning(f"_match_phase: music cluster {bkey!r} failed (isolated): {e!r}")
                    try:
                        await s3.rollback()
                        for _fid in bfids:
                            _mf3 = await s3.get(MediaFile, _fid)
                            if _mf3 is not None and _mf3.status == "matching":
                                _mf3.status = "no_match"
                        await s3.commit()
                    except Exception:
                        pass
        return _lane_matched

    _music_task = asyncio.create_task(_run_music_lane()) if _music_lane else None
    try:
        for bucket_key, cfids in _anidb_lane:
            # Liveness pre-write: an AniDB cluster can take tens of seconds; show
            # WHICH cluster is resolving before dispatching it (PB-jank fix kept).
            _pre = await session.get(MediaFile, cfids[0])
            if _pre is not None and _pre.file_path:
                await _bump_progress(_pre.file_path)
            _n, _rep = await _process_match_cluster(
                session, engine, bucket_key, cfids,
                overridden=overridden, music_enabled=music_enabled,
                auto_enabled=auto_enabled, auto_th=auto_th,
            )
            matched += _n
            await _bump_progress(_rep)
        if _music_task is not None:
            matched += await _music_task
            # The music worker session committed rows this session may hold
            # stale copies of.
            session.expire_all()
            await _bump_progress(None)
    finally:
        if _music_task is not None and not _music_task.done():
            _music_task.cancel()
    return matched



async def _process_match_cluster(
    session, engine, bucket_key, cfids: list[int], *,
    overridden: set[int], music_enabled: bool,
    auto_enabled: bool, auto_th: float,
) -> tuple[int, str | None]:
    """Match ONE cluster on the GIVEN session: shimmer + title rescue →
    dispatch (music plugin / cluster / singleton) → resolve statuses
    (auto-approve, anime media_type correction, pack fallback). Commits its own
    file-row work but NEVER touches the Scan row — live progress belongs to the
    orchestrator in _match_phase, because this helper may run on a parallel
    worker session and two sessions must not fight over one Scan row.

    Returns (files matched in this cluster, representative path for progress).
    Extracted verbatim from the old serial loop so both lanes behave
    identically per-cluster; only the scheduling around it changed."""
    _cluster_matched = 0
    # Shimmer the cluster's rows while it resolves.
    _rep_path: str | None = None
    for fid in cfids:
        mf = await session.get(MediaFile, fid)
        if mf:
            mf.status = "matching"
            if _rep_path is None and mf.file_path:
                _rep_path = mf.file_path
            # Title rescue for files the filename couldn't identify — reads
            # the container's embedded title and re-parses. Bounded to files
            # with no usable title (they'd never match otherwise), so the one
            # read is worth it even on a NAS.
            try:
                await _maybe_rescue_title_from_mediainfo(mf)
            except Exception as e:
                logger.warning(f"_match_phase: title rescue failed for {fid}: {e!r}")
    # Progress (current_path / matched_count) belongs to the ORCHESTRATOR in
    # _match_phase — this helper may run on a parallel worker session, and two
    # sessions writing the same Scan row would fight. Shimmer commit only.
    await session.commit()
    await asyncio.sleep(0)

    # Files an override pack already claimed are NOT re-dispatched — the
    # dispatcher clears matches first, which would discard the pack row.
    dispatch_fids = [f for f in cfids if f not in overridden] if overridden else cfids
    if dispatch_fids:
        # Music → the ISOLATED kira.music plugin (ONE seam, gated on
        # music.enabled). It never reaches the movie/TV/anime cascade below
        # (engine.match short-circuits music), so this branch can't affect it.
        _first = await session.get(MediaFile, dispatch_fids[0])
        if _first is not None and _first.media_type == "music":
            if music_enabled:
                await _match_music(session, dispatch_fids)
            # else: music off → leave as no_match (the status sweep handles it)
        elif isinstance(bucket_key, str) and len(dispatch_fids) >= 2:
            await _match_cluster(session, engine, dispatch_fids)
        else:
            await _match_singleton(session, engine, dispatch_fids[0])

    # Resolve "which files got a match" and "their selected provider" in
    # TWO grouped queries for the whole cluster, instead of 1-2 SELECTs per
    # file inside the loop. (`session.get(MediaFile, fid)` below stays
    # per-file but is served from the identity map — the rows were just
    # loaded by the cluster matcher — so it costs no extra round-trip.)
    matched_fids = set((await session.scalars(
        select(Match.media_file_id).where(Match.media_file_id.in_(cfids))
    )).all())
    sel_rows = (await session.execute(
        select(Match.media_file_id, Match.provider, Match.confidence,
               Match.provider_id, Match.match_type).where(
            Match.media_file_id.in_(cfids), Match.is_selected.is_(True)
        )
    )).all()
    sel_provider_by_fid = {fid: prov for fid, prov, _, _, _ in sel_rows}
    sel_conf_by_fid = {fid: conf for fid, _, conf, _, _ in sel_rows}
    sel_pid_by_fid = {fid: pid for fid, _, _, pid, _ in sel_rows}
    sel_mtype_by_fid = {fid: mt for fid, _, _, _, mt in sel_rows}

    for fid in cfids:
        has_match = fid in matched_fids
        mf = await session.get(MediaFile, fid)
        if has_match:
            _cluster_matched += 1
            if mf and mf.status == "matching":
                # Auto-approve high-confidence hits past the threshold so they
                # skip Review; everything else stays "matched" for the user.
                sel_conf = sel_conf_by_fid.get(fid)
                if auto_enabled and sel_conf is not None and sel_conf >= auto_th:
                    mf.status = "approved"
                else:
                    mf.status = "matched"
            # Correct media_type from the matched provider. AniDB is an
            # anime-only source, so an AniDB match means this file IS anime
            # even when the parser guessed "tv" (e.g. the file lives outside
            # an /anime/ path, like a release-named download folder). Without
            # this the show lands in the "TV Series" group and splits from
            # its anime siblings. Recompute the series/variant keys off the
            # corrected media_type so it re-clusters under the anime identity.
            if mf and mf.media_type != "anime" and mf.parsed_data:
                # Anime detection independent of folder: a direct AniDB match
                # OR a TVDB/TMDB series whose id cross-refs to an AniDB id in
                # Fribb (so anime in a generic /tv/ folder is caught too).
                if await _selected_match_is_anime(
                    sel_provider_by_fid.get(fid),
                    sel_pid_by_fid.get(fid),
                    sel_mtype_by_fid.get(fid),
                ):
                    # CR-09: shared helper sets media_type FIRST then
                    # recomputes the keys, so even if the recompute raises
                    # the grouping fix ("at least set media_type=anime")
                    # still lands. Surrounding try/except preserves the
                    # original best-effort + log behavior.
                    try:
                        apply_media_type_and_recompute_keys(mf, "anime")
                    except Exception as e:
                        mf.media_type = "anime"  # at least fix the grouping
                        logger.warning(f"_match_phase: media_type correction key recompute failed for {fid}: {e!r}")
        elif mf and mf.status == "matching":
            # ── Kira Packs: FALLBACK rescue ─────────────────────────────
            # Last chance before no_match: a community pack (One Pace, etc.)
            # may claim this file the providers couldn't place. This is the
            # ONLY seam where a pack touches matching — so a pack can never
            # alter a title the providers already matched (isolation).
            rescued = False
            try:
                from kira.packs.apply import try_pack_match
                rescued = await try_pack_match(session, fid, mf)
            except Exception as e:
                logger.warning(f"_match_phase: pack fallback failed for {fid}: {e!r}")
            if rescued:
                _cluster_matched += 1
                # Pack matches are authoritative (confidence 1.0): respect
                # the same auto-approve threshold as a provider match.
                if auto_enabled and 1.0 >= auto_th:
                    mf.status = "approved"
                else:
                    mf.status = "matched"
            else:
                mf.status = "no_match"

    await session.commit()
    await asyncio.sleep(0)
    return _cluster_matched, _rep_path


async def _prune_missing_files(
    session, root_paths: list[str], walked_norm: set[str], norm_fn, *, error_paths: list[str] = (),
) -> int:
    """The OTHER half of a scan: drop tracked files that VANISHED from disk, so
    deleting a file (in Kira or your file manager) clears it from Review on the
    next scan instead of lingering forever.

    A row is pruned only when ALL hold:
      • its path is UNDER a root this scan walked (never touches libraries this
        scan didn't cover), AND
      • its path is NOT under an `error_paths` subtree — a dead root or a folder
        that raised a scandir error this scan. There "the walk didn't see it" is
        untrustworthy (a mid-scan NAS disconnect makes PRESENT files stat as
        gone), so we never prune inside it, AND
      • the walk didn't see it (fast pre-filter via `walked_norm`), AND
      • `stat()` raises FileNotFoundError — i.e. CONFIRMED gone. A permission /
        NAS error counts as "can't tell → keep", never as deleted.

    The row + its Match rows go (RenameHistory preserved, exactly like the manual
    delete via `_delete_one(keep_on_disk=True)`); nothing is removed from disk —
    the file's already gone.

    RESILIENT: pass the scan's `error_paths` (walk_errors + dead_roots) and this
    sweeps every CLEANLY-walked subtree while skipping only the unreadable ones,
    so one bad folder no longer blocks ALL deletion cleanup. With no error_paths
    it's the old "prune the whole walked scope" behavior. Returns rows pruned."""
    from kira.api.files import _delete_one

    def _confirmed_gone(p: str) -> bool:
        try:
            Path(p).stat()
            return False                 # still there
        except FileNotFoundError:
            return True                  # definitively gone → safe to prune
        except OSError:
            return False                 # permission / NAS hiccup → keep

    # Scope check, drive-letter/UNC AWARE. A row stored under one spelling of a
    # share — e.g. the UNC `\\192.168.0.63\Data\…` form an earlier rename wrote —
    # must still count as "under" a root scanned via its mapped-drive spelling
    # (`Z:\…`); otherwise the raw-string `path_under_roots` judges it out-of-scope
    # and the stale row can NEVER be pruned (this is the phantom-duplicate bug:
    # a file renamed away on disk leaves an unreachable ghost row that then looks
    # like a second copy of the episode). `norm_fn` already emits the alias-
    # swapped + both-slash forms (it carries the resolved root prefixes), so we
    # compare normalized prefixes instead of the raw string.
    root_prefixes = {f.rstrip("/\\") for r in root_paths for f in norm_fn(r)}
    # Subtrees the walk couldn't fully read this scan (scandir errors + dead
    # roots). A "missing" row inside one of these can't be trusted as deleted, so
    # we exclude it — but everything under a cleanly-walked subtree is still swept.
    error_prefixes = {f.rstrip("/\\") for e in error_paths for f in norm_fn(e)}

    # GUARD against the NAS-disconnect wipe: a root that walked ZERO files this
    # scan is almost certainly a DISCONNECTED mount (a populated library doesn't
    # spontaneously empty), NOT a user who deleted everything. A dropped mount can
    # present as an empty-but-walkable dir with no scandir error — so it dodges the
    # error_paths guard above, then every tracked file under it stat()s as
    # FileNotFoundError and the whole index gets pruned (the exact data-loss the
    # user hit). So treat a zero-file root exactly like an unreadable subtree:
    # never prune inside it. A genuinely-emptied library just keeps its now-stale
    # rows until a scan sees the folder non-empty — the SAFE direction to fail.
    empty_root_prefixes = {
        rp for rp in root_prefixes
        if not any(
            w == rp or w.startswith(rp + "/") or w.startswith(rp + "\\")
            for w in walked_norm
        )
    }
    if empty_root_prefixes:
        logger.warning(
            f"_prune_missing_files: {len(empty_root_prefixes)} scan root(s) walked ZERO "
            f"files — treating as disconnected mounts, NOT pruning under them "
            f"(reconnect + rescan to rebuild): {sorted(empty_root_prefixes)}"
        )

    def _under(p_norms: set[str], prefixes: set[str]) -> bool:
        for f in p_norms:
            for rp in prefixes:
                if f == rp or f.startswith(rp + "/") or f.startswith(rp + "\\"):
                    return True
        return False

    rows = (await session.execute(select(MediaFile.id, MediaFile.file_path))).all()
    candidates: list[int] = []
    for fid, fp in rows:
        if not fp:
            continue
        p_norms = norm_fn(fp)
        if not _under(p_norms, root_prefixes):
            continue                     # outside the scanned scope (alias-aware)
        if error_prefixes and _under(p_norms, error_prefixes):
            continue                     # inside an unreadable subtree → can't trust → keep
        if empty_root_prefixes and _under(p_norms, empty_root_prefixes):
            continue                     # its root walked 0 files → likely disconnected → keep
        if p_norms & walked_norm:
            continue                     # the walk saw it this scan → present
        if await asyncio.to_thread(_confirmed_gone, fp):
            candidates.append(fid)

    removed = 0
    for fid in candidates:
        mf = await session.get(MediaFile, fid)
        if mf is None:
            continue
        try:
            await _delete_one(session, mf, keep_on_disk=True, roots=[])
            removed += 1
        except Exception as e:
            logger.warning(f"_prune_missing_files: {fid} failed (non-fatal): {e!r}")

    if removed:
        from kira.models import Notification
        plural = "" if removed == 1 else "s"
        session.add(Notification(
            kind="info",
            title=f"Removed {removed} file{plural} no longer on disk",
            body=("These were deleted from your library folder, so Kira dropped "
                  "them from Review. Rename history is kept."),
        ))
        await session.commit()
        logger.info(f"_scan_worker: pruned {removed} file(s) gone from disk")
    return removed


async def _resilient_commit(session, pending: list) -> list[tuple[str, str]]:
    """Commit the session; if the batch commit fails, roll back and re-commit each
    pending row INDIVIDUALLY so a single bad row drops only ITSELF — not the whole
    SCAN_COMMIT_EVERY batch (the old behaviour silently lost up to 5 good files on
    one row's conflict). A row that still fails alone is left uncommitted (its `id`
    stays None → naturally excluded from the match set) and returned as
    `(file_path, error)` so the caller can surface it. Never raises."""
    try:
        await session.commit()
        return []
    except Exception as batch_err:  # noqa: BLE001
        logger.warning(
            f"_scan ingest: batch commit failed ({batch_err!r}) — retrying "
            f"{len(pending)} row(s) individually so good files aren't lost"
        )
        try:
            await session.rollback()
        except Exception:
            pass
        dropped: list[tuple[str, str]] = []
        for mf in pending:
            session.add(mf)
            try:
                await session.commit()
            except Exception as row_err:  # noqa: BLE001
                try:
                    await session.rollback()
                except Exception:
                    pass
                dropped.append((getattr(mf, "file_path", "?"), str(row_err)))
        return dropped


def _drain_walk_batch(
    walk_iter,
    batch_size: int,
    walked_norm: set[str],
    renamed_lc: set[str],
    existing_lc: set[str],
    norm_fn,
) -> tuple[list[tuple[Path, int | None]], bool]:
    """Pull up to `batch_size` entries from a (synchronous) `scanner.walk`
    iterator, applying dedup, and `stat()` only the survivors for their size.

    ALL blocking filesystem I/O — stepping `os.walk` via `next()` AND the
    per-file `stat()` — happens HERE, on the dedicated walk thread, so the
    event loop never blocks on a slow NAS round-trip. The caller awaits this
    via `run_in_executor` and consumes survivors (parse + ORM) on the loop.

    Dedup runs in-thread too, on purpose: the original loop stat()'d a file
    ONLY after it passed the already-walked / renamed / existing checks, so
    a re-scan of a known library skips thousands of stats. Moving dedup here
    keeps that optimization — we never stat a file we'd discard. `norm_fn`
    (`_norm`) is filesystem-free, so it's safe off the loop.

    Returns `(survivors, exhausted)`. `survivors` is a list of
    `(path, size_or_None)`; `exhausted` is True once the iterator is spent.
    The batch is bounded by entries PULLED, not survivors, so a batch where
    every entry is deduped still returns promptly (with an empty list and
    `exhausted=False`) rather than walking the whole tree in one hop —
    keeping cancellation latency and progress cadence bounded.

    NOTE: `walked_norm` is mutated here; that's safe because the walk runs on
    a single pinned thread and the caller only reads the set after the walk
    has fully drained (the prune sweep), so there's no concurrent access.
    """
    survivors: list[tuple[Path, int | None]] = []
    pulled = 0
    while pulled < batch_size:
        try:
            path = next(walk_iter)
        except StopIteration:
            return survivors, True
        pulled += 1
        # Skip-if-already-walked-this-scan. Lowercase + both slash forms so
        # trailing-slash / case / separator noise can't sneak duplicates past.
        spath_norm = str(path).lower().replace("/", "\\")
        if spath_norm in walked_norm:
            continue
        walked_norm.add(spath_norm)
        # Skip files that ARE the result of a previous rename (or are already
        # tracked as MediaFiles). The pre-normalized sets carry the lowercased
        # original AND resolved form, so a UNC-stored DB path matches the
        # drive-letter-walked filesystem path.
        spath_forms = norm_fn(str(path))
        if spath_forms & renamed_lc:
            continue
        if spath_forms & existing_lc:
            continue
        # PERF: one stat per SURVIVING file, for size only. (os.walk already
        # classified entries via scandir, so no is_file() round-trip.)
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        survivors.append((path, size))
    return survivors, False


async def _active_rename_targets(session) -> set[str]:
    """`new_path` of every NON-undone rename whose MediaFile STILL EXISTS — the
    set the discovery drainer skips so a renamed file isn't re-ingested as a
    phantom duplicate (which would then fail to rename: "source does not exist").

    Gated on a LIVE MediaFile: a wiped-but-renamed file (media_files cleared while
    rename_history was kept — the NAS-disconnect prune case) has a dangling/NULL
    `media_file_id`, so it's EXCLUDED here and gets re-ingested on the next scan
    instead of being silently dropped forever (the Wednesday S02E01/E02 bug)."""
    from kira.models import RenameHistory
    rows = (await session.scalars(
        select(RenameHistory.new_path).where(
            RenameHistory.undone_at.is_(None),
            RenameHistory.media_file_id.in_(select(MediaFile.id)),
        )
    )).all()
    return {p for p in rows if p}


async def _scan_worker(scan_id: int, root_paths: list[str] | str) -> list[int] | None:
    """Walk the tree, parse each file, then match each new file in turn.

    Two distinct phases, both reported via Scan.status:
      'scanning' → 'matching' → 'completed' (or 'failed: ...').

    Bug A: accepts a list of roots and walks each one sequentially in
    Phase 1, accumulating all discovered files into a single Phase 2
    matching pass. A bare string is promoted to `[string]` for
    back-compat with internal callers that haven't been updated.

    CR-10: returns the list of new MediaFile ids that should be auto-renamed
    (auto-source scan that found new files), or None. The auto-rename phase is
    deliberately NOT run here — `_scan_worker_locked` runs it AFTER releasing
    the scan lock so a slow rename can't block the next scan.
    """
    # Defensive normalization — `_scan_worker_locked` already promotes
    # single strings to lists, but a hand-spawned task or test could
    # still pass a string directly.
    if isinstance(root_paths, str):
        root_paths = [root_paths]
    async with SessionLocal() as session:
        # Track the MediaFile ORM objects directly instead of trying to
        # capture .id mid-flight (placeholders + re-query was a workaround
        # for the fact that .id is None until flush/commit). After the
        # final phase-1 commit below, every object has its real .id.
        new_files: list[MediaFile] = []
        count = 0
        # Pre-load every path that's already a rename TARGET (new_path
        # of a previous rename). Skip those during this scan — otherwise,
        # when the user's library_root contains both source and destination
        # (e.g. Z:\media holds both Z:\media\tv\... and Z:\media\TV\...),
        # the scanner re-discovers renamed files as "new" MediaFile rows.
        # Those phantoms then fail to rename ("source does not exist")
        # because the user already renamed them once.
        # Skip a rename target ONLY while its MediaFile still exists. After a
        # library WIPE (a NAS-disconnect prune cleared media_files but KEPT
        # rename_history) the renamed file is no longer tracked — its
        # media_file_id dangles / NULLs — so it MUST be re-ingested, not skipped.
        # Without this gate a re-scan permanently drops every previously-renamed
        # file after a wipe (the user's missing Wednesday S02E01/E02).
        renamed_paths_raw = await _active_rename_targets(session)
        # Path-normalization bug-fix: the rename engine writes RESOLVED
        # paths to MediaFile.file_path and RenameHistory.new_path. On
        # Windows with a mapped drive (Z:\ → \\nas\share), the stored
        # path is the UNC form (`\\192.168.0.63\Data\...`) while the
        # scanner walks the drive-letter path (`Z:\...`). String
        # comparison misses, so renamed files re-appear as "new" on the
        # next scan — leading to duplicate Match rows AND the "Resolve N
        # duplicates" footer button lighting up on rows the user JUST
        # finished renaming. We pre-compute lowercased + .resolve()'d
        # variants of every stored path so the per-file lookup below
        # can match against either form cheaply.
        # PERF (NAS walk speed): bridge drive-letter ↔ UNC ONCE per scan, not
        # per file. `Path.resolve()` is a filesystem round-trip (symlink
        # resolution); calling it inside `_norm` for every walked file is what
        # made scanning a network share crawl — thousands of extra round-trips.
        # Resolve each ROOT a single time; if its resolved form differs (mapped
        # drive → UNC), record the prefix pair so `_norm` can swap prefixes with
        # pure string ops and ZERO per-file filesystem access.
        _root_aliases: list[tuple[str, str]] = []
        for _r in root_paths:
            try:
                _resolved = str(Path(_r).resolve())
            except OSError:
                continue
            if _resolved and _resolved.lower() != str(_r).lower():
                _root_aliases.append((str(_r), _resolved))

        def _norm(p: str) -> set[str]:
            """Equivalent lower-cased string forms for `p` — FILESYSTEM-FREE.

            Emits both slash styles, and (when `p` sits under a root whose
            resolved form differs) the prefix-swapped variant so a drive-letter
            walk still matches a UNC-stored path. No per-file `resolve()`."""
            bases = {p}
            pl = p.lower()
            for raw, resolved in _root_aliases:
                if pl.startswith(raw.lower()):
                    bases.add(resolved + p[len(raw):])
                elif pl.startswith(resolved.lower()):
                    bases.add(raw + p[len(resolved):])
            forms: set[str] = set()
            for b in bases:
                bl = b.lower()
                forms.add(bl)
                forms.add(bl.replace("/", "\\"))
                forms.add(bl.replace("\\", "/"))
            return forms
        renamed_paths_lc: set[str] = set()
        for p in renamed_paths_raw:
            renamed_paths_lc |= _norm(p)
        # Same trick for existing MediaFile.file_path lookup — preload the
        # whole set so we don't pay a DB roundtrip per file.
        existing_lc: set[str] = set()
        for p in (await session.scalars(select(MediaFile.file_path))).all():
            existing_lc |= _norm(p)
        # CR-10: ids to auto-rename AFTER lock release (see return below). None
        # unless this is an auto-source scan that found new files.
        auto_rename_ids: list[int] | None = None
        try:
            # ── Phase 1: walk + parse ─────────────────────────────────────
            # EE-2: the thread-local walk-error list is reset BEFORE iteration
            # so this scan sees only its own scandir() failures — but the reset
            # runs on the WALK THREAD (where the thread-local actually lives),
            # not here. The reset, the whole walk, and the final
            # get_walk_errors() read ALL run on one pinned thread (see the walk
            # executor below). Resetting/reading on the loop thread would touch a
            # different thread-local than the walk populates and silently drop
            # every scandir failure — wrongly marking a partial NAS scan
            # 'completed' AND arming the prune sweep to delete present files.
            # User-defined ignore globs (Settings → Paths) — refreshed per
            # scan so edits apply to the very next run.
            try:
                from kira.models import Setting as _Setting
                _ig_row = await session.get(_Setting, "scanning.ignore_patterns")
                _ig = _ig_row.value if _ig_row else None
                if isinstance(_ig, dict) and "value" in _ig:
                    _ig = _ig["value"]
                scanner.set_user_ignores(_ig if isinstance(_ig, list) else None)
            except Exception as e:
                logger.warning(f"scan: ignore-patterns load failed (non-fatal): {e!r}")
                scanner.set_user_ignores(None)
            # Bug A: walk every configured root in sequence. All
            # discovered files land in the same `new_files` list →
            # one Phase 2 pass matches them all together so clusters
            # spanning multiple watch folders still resolve correctly
            # (e.g. half a season on the main library + the rest on
            # an external drive that's mounted as a watch folder).
            # Reset walk-errors stays outside this outer loop so a
            # single error list spans the whole scan.
            #
            # Bug A safety net: track every file path we've already
            # added in THIS scan. Without this, overlapping roots
            # (e.g. library_root=`Z:\media` AND watch_folder=`Z:\media`
            # with subtle string differences that defeat the upstream
            # dedup — trailing slash, case, forward vs backslash)
            # would cause the same file to enter `new_files` twice,
            # hit the UNIQUE constraint on `media_files.file_path` at
            # commit time, and trigger the outer scan-worker
            # exception handler which DELETES every row from this
            # scan. The user then sees "scan completed" and an empty
            # Review page. Path-set dedup keeps the inner loop
            # idempotent regardless of how many roots overlap.
            walked_paths_this_scan: set[str] = set()
            # A configured root that's gone/unmounted (NAS down, typo'd path)
            # must mark the scan completed_partial, not silently 'completed' with
            # 0 files — scanner.walk() returns empty for a dead root WITHOUT
            # recording a walk error, so we detect it here at the top level.
            dead_roots: list[str] = []
            # NOTE: MediaInfo enrichment is NOT read here anymore — it moved to
            # `_match_phase` so the discovery walk does no file-content reads.
            # The discovery walk runs on a DEDICATED single-thread executor,
            # NOT the event loop. scanner.walk()'s os.walk + the per-file stat()
            # are blocking network round-trips on a NAS; run inline on the loop
            # they stalled every other HTTP request between yields. We pull the
            # walk in bounded batches (`_drain_walk_batch`) off-thread and
            # consume survivors (parse + ORM) here, yielding control BETWEEN
            # batches and every SCAN_COMMIT_EVERY rows at the commit checkpoint.
            #
            # Why a private single-thread executor instead of asyncio.to_thread?
            # scanner records scandir() failures in a THREAD-LOCAL; to_thread's
            # shared pool would scatter successive batches across different
            # worker threads, losing those errors. Pinning the reset, every
            # batch, and the final get_walk_errors() read to ONE thread keeps
            # the walk-error accounting (→ completed_partial + prune guard)
            # correct. The executor is torn down in `finally` — including on
            # cancellation, where an in-flight batch is bounded so it drains
            # quickly and the loop is free again at once.
            walk_errors: list[str] = []
            # Per-batch ingest resilience: `_pending` holds the rows added since the
            # last commit; `ingest_dropped` collects any a resilient commit couldn't
            # save even alone (id stays None → excluded from the match set; surfaced
            # to the user at the end).
            _pending: list = []
            ingest_dropped: list[tuple[str, str]] = []
            loop = asyncio.get_running_loop()
            walk_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="kira-scan-walk"
            )
            try:
                # Reset the walk-error thread-local ON the walk thread — that's
                # where scanner.walk() will populate it.
                await loop.run_in_executor(walk_executor, scanner.reset_walk_errors)
                for root_path in root_paths:
                    # Off the event loop: a dead/unmounted NAS makes is_dir() block
                    # for the full SMB/network timeout, freezing the whole API. The
                    # walk thread already exists — run the reachability probe there.
                    reachable = await loop.run_in_executor(
                        walk_executor, _root_reachable, root_path
                    )
                    if not reachable:
                        dead_roots.append(str(root_path))
                        continue
                    # Lazy generator — every next() (os.walk stepping) and the
                    # survivor stats run on the walk thread inside the drainer.
                    walk_iter = scanner.walk(root_path)
                    while True:
                        survivors, exhausted = await loop.run_in_executor(
                            walk_executor,
                            _drain_walk_batch,
                            walk_iter,
                            SCAN_WALK_BATCH,
                            walked_paths_this_scan,
                            renamed_paths_lc,
                            existing_lc,
                            _norm,
                        )
                        for path, file_size in survivors:
                            parsed = parse_path(path)
                            # NOTE: NEITHER the xattr ID read NOR the MediaInfo
                            # header read happens here. The discovery walk stays
                            # fast — one stat (for size, taken off-loop above) +
                            # pure-string parse per file, nothing that opens the
                            # file or does an extra filesystem round-trip. Both
                            # moved to the MATCH phase (`_apply_xattr_ids`,
                            # `_enrich_mediainfo_phase`), off the discovery
                            # critical path — FileBot's "list fast, read at
                            # identify-time" model.
                            mf = MediaFile(
                                scan_id=scan_id,
                                file_path=str(path),
                                file_size=file_size,
                                media_type=parsed.media_type,
                                status="discovered",
                                parsed_data=parsed.to_dict(),
                                # EE-5: pass file_path so same-titled shows in
                                # different folders ("The Office UK" vs "The
                                # Office US") get distinct series_keys via the
                                # parent-folder fingerprint when the parser
                                # couldn't extract a year.
                                series_key=_compute_series_key(parsed, file_path=str(path)),
                                variant_key=_compute_variant_key(parsed),
                            )
                            session.add(mf)
                            new_files.append(mf)
                            _pending.append(mf)
                            count += 1
                            if count % SCAN_COMMIT_EVERY == 0:
                                scan = await session.get(Scan, scan_id)
                                if scan:
                                    scan.file_count = count
                                    scan.current_path = str(path)
                                # Resilient: one bad row drops only itself, never the
                                # whole batch of (up to) SCAN_COMMIT_EVERY good files.
                                ingest_dropped += await _resilient_commit(session, _pending)
                                _pending = []
                                await asyncio.sleep(0)
                        if exhausted:
                            break
                # Read walk errors FROM the walk thread before teardown — the
                # thread-local lives there, so the read must run on it too.
                walk_errors = await loop.run_in_executor(
                    walk_executor, scanner.get_walk_errors
                )
            finally:
                # Always release the walk thread — success, error, cancellation.
                # wait=False so a cancel isn't held up by an in-flight stat on a
                # wedged mount: the bounded batch finishes and the thread exits.
                walk_executor.shutdown(wait=False, cancel_futures=True)

            # After the final commit every mf.id is populated; no need to
            # re-query. This is also faster than running a SELECT over the
            # whole scan_id (the old "all_new" query scaled with library size).
            all_new = [mf.id for mf in new_files if mf.id is not None]

            # ── Sweep: prune files that vanished from disk ────────────────
            # The walk above is the "mark"; this is the "sweep". A tracked file
            # under a scanned root that the walk didn't find AND that stat()
            # confirms is gone gets dropped, so a deleted file auto-clears from
            # Review. RESILIENT: subtrees that errored this scan (a dead root or a
            # scandir failure) are EXCLUDED — there "not seen" ≠ "deleted" (the
            # NAS-blip guard) — but every cleanly-walked subtree is still swept, so
            # one unreadable folder no longer blocks ALL deletion cleanup the way
            # the old "skip the whole sweep on any error" gate did.
            try:
                await _prune_missing_files(
                    session, root_paths, walked_paths_this_scan, _norm,
                    error_paths=walk_errors + dead_roots,
                )
            except Exception as e:
                logger.warning(f"_scan_worker: prune-missing failed (non-fatal): {e!r}")

            # ── Surface ingest issues the user would otherwise never see ──────
            # `walk_errors` = folders scandir() couldn't read (already used above to
            # spare them from the prune); `ingest_dropped` = rows a resilient commit
            # couldn't save. Both leave files un-indexed silently, so tell the user
            # instead of swallowing it — they'll be retried on the next scan.
            if walk_errors or ingest_dropped:
                _wn, _dn = len(walk_errors), len(ingest_dropped)
                _bits: list[str] = []
                if _wn:
                    _bits.append(f"{_wn} folder{'s' if _wn != 1 else ''} couldn't be read")
                if _dn:
                    _bits.append(f"{_dn} file{'s' if _dn != 1 else ''} couldn't be indexed")
                _sample = (walk_errors[:2] + [p for p, _ in ingest_dropped[:2]])[:3]
                _body = " · ".join(_bits)
                if _sample:
                    _body += " — e.g. " + ", ".join(_sample)
                _body += ". They'll be retried on the next scan."
                await _post_notification("warning", "Scan finished with issues", _body)

            # RESUME: also match files a prior interrupted scan left in
            # "discovered" (boot reset stuck "matching" → "discovered"). The
            # walk's dedup skips re-adding existing files, so without this a
            # re-scan would finish in seconds and leave the leftover files
            # stuck/pending forever — exactly the "click Scan, nothing happens"
            # symptom after a kill. Merge them into the match set.
            # Scope the resume to files UNDER the roots being scanned NOW.
            # Without this, a targeted re-scan of one folder vacuums the ENTIRE
            # 'discovered' backlog (e.g. thousands of files a post-crash boot
            # reset left across the whole library), turning a quick scan into a
            # massive, ban-risky provider burst. A full-library scan still
            # resumes everything — every path is under its root.
            # CR-04: push the per-root path scoping into SQL instead of loading
            # EVERY 'discovered' row in the DB and filtering in Python. We OR
            # together a `file_path LIKE root%` clause per root. The filter is
            # deliberately COARSE/INCLUSIVE — for each root we emit BOTH the raw
            # stored form and a forward-slash-normalized form, with and without a
            # trailing separator, so Windows backslash vs POSIX slash and
            # trailing-sep differences can't make SQL drop a row the precise
            # Python `path_under_roots` check below would have accepted. (SQLite
            # LIKE is case-insensitive for ASCII, matching `_norm`'s case-fold.)
            # The Python check stays as the exact final safety net on the now
            # much-narrower result, so correctness can't regress.
            from kira.api.webhooks import path_under_roots
            _roots = list(root_paths)
            _new_set = set(all_new)

            def _like_escape(s: str) -> str:
                # Neutralize LIKE wildcards so a literal % or _ in a root path
                # can't widen the match. Paired with escape="\\" below.
                return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

            _like_clauses = []
            for _r in _roots:
                if not _r:
                    continue
                _variants = set()
                _fwd = _r.replace("\\", "/")
                for _base in (_r, _fwd):
                    _base = _base.rstrip("/\\")
                    if not _base:
                        continue
                    # Match the root itself AND anything beneath it (either sep).
                    _variants.add(_base)
                    _variants.add(_base + "/")
                    _variants.add(_base + "\\")
                for _v in _variants:
                    _like_clauses.append(
                        MediaFile.file_path.like(_like_escape(_v) + "%", escape="\\")
                    )

            _stmt = select(MediaFile.id, MediaFile.file_path).where(
                MediaFile.status == "discovered"
            )
            if _like_clauses:
                _stmt = _stmt.where(or_(*_like_clauses))
            _leftover = [
                i for (i, fp) in (await session.execute(_stmt)).all()
                if i not in _new_set and fp and path_under_roots(fp, _roots)
            ]

            # Smart rescan: ALSO retry files that previously ended `no_match` (the
            # "Needs matching" section). A rescan should pick up files that failed
            # to match before — a provider key added since, a transient provider
            # outage, a pack installed — WITHOUT touching already-matched files
            # (matched / approved / renamed are never put in `match_ids`, so their
            # match is never disturbed). This is "match the Needs-matching ones
            # while looking for new files", short of a full reparse-everything.
            _nm_stmt = select(MediaFile.id, MediaFile.file_path).where(
                MediaFile.status == "no_match"
            )
            if _like_clauses:
                _nm_stmt = _nm_stmt.where(or_(*_like_clauses))
            _no_match = [
                i for (i, fp) in (await session.execute(_nm_stmt)).all()
                if i not in _new_set and fp and path_under_roots(fp, _roots)
            ]
            match_ids = all_new + _leftover + _no_match
            if _leftover:
                logger.info(f"_scan_worker: resuming {len(_leftover)} leftover file(s) from a prior interrupted scan")
            if _no_match:
                logger.info(f"_scan_worker: retrying {len(_no_match)} previously-unmatched (Needs-matching) file(s)")

            scan = await session.get(Scan, scan_id)
            if scan:
                # file_count = the REAL work this scan does (new discoveries +
                # resumed leftovers + retried no_match), not just brand-new
                # survivors. On a re-scan of an indexed library `count` (new files)
                # is ~0, which read to the user as "0 files found" the whole scan.
                scan.file_count = max(count, len(match_ids))
                # PB-4: progress denominator = everything we'll match (new +
                # resumed leftover + no_match retries), so the bar reflects real
                # work, not 100%-in-2-seconds when only a few files remain.
                scan.estimated_total = len(match_ids)
                scan.current_path = None
                scan.status = "matching"
            await session.commit()

            # Phase 11: folder-level series lock. Pull outlier files (a
            # mangled "Final Season Part 3-01" / "Special 05") into their
            # leaf folder's majority series BEFORE clustering, so one bad
            # filename can't escape its season's card. No-op for folders
            # without a clear majority (mixed folders stay split).
            try:
                relocked = await _apply_folder_series_lock(session, all_new)
                if relocked:
                    logger.info(f"_scan_worker: folder-lock relocked {relocked} outlier file(s)")
            except Exception as e:
                logger.warning(f"_scan_worker: folder-lock pass failed (non-fatal): {e!r}")

            # ── Phase 2: match. Cluster by series_key first so a 26-episode
            # anime fires 2 API calls (one search + one episode list) instead
            # of 26. Singletons (movies, null series_key) take the per-file path.
            async with httpx.AsyncClient() as client:
                registry = await registry_from_settings(client)
                engine = MatchEngine(registry)

                # Cluster by series_key + match each cluster/singleton,
                # pushing live progress. Shared with the re-parse worker.
                # `match_ids` = this scan's new files + any leftover "discovered"
                # files resumed from a prior interrupted scan.
                matched = await _match_phase(session, engine, match_ids, scan_id)

            # Kira Packs: re-offer installed packs to the EXISTING no_match
            # backlog too — not just this scan's new files (those already got the
            # per-file fallback seam in `_match_phase`). A pack match is a cheap
            # LOCAL regex/title check with ZERO provider calls, so this is safe on
            # every scan: a pack you added AFTER files were already no_match now
            # clears them on the next scan, with no Packs-page trip. Skipped when
            # no pack is installed; isolation holds (only no_match files touched).
            try:
                from kira.packs.apply import apply_packs_to_no_match
                pack_rescued = await apply_packs_to_no_match(session)
                if pack_rescued:
                    matched += pack_rescued
                    await session.commit()
                    logger.info(f"_scan_worker: packs rescued {pack_rescued} backlog file(s)")
            except Exception as e:
                logger.warning(f"_scan_worker: pack backlog pass failed (non-fatal): {e!r}")

            # EE-2: did the directory walk hit any unreachable paths?
            # If so, the scan technically completed but is INCOMPLETE.
            # Mark it `completed_partial` (frontend can render a warning
            # badge instead of a green check) and notify so the user
            # knows to retry once the NAS/permissions are stable.
            # In-walk scandir failures PLUS any top-level root that was entirely
            # unreachable — both make the scan INCOMPLETE. `walk_errors` was
            # captured from the walk thread (where the thread-local lives).
            walk_failures = walk_errors + dead_roots

            scan = await session.get(Scan, scan_id)
            if scan:
                scan.status = "completed_partial" if walk_failures else "completed"
                scan.file_count = count
                scan.matched_count = matched
                scan.current_path = None
                # Naive UTC to match SQLite's storage of `server_default=func.now()`
                # — comparing aware vs naive 500s every downstream filter.
                scan.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            if walk_failures:
                from kira.models import Notification
                # Cap to 20 paths so the notification body doesn't bloat
                # the notifications popover. Backend logs have the full list.
                shown = walk_failures[:20]
                more = f"\n…and {len(walk_failures) - len(shown)} more" if len(walk_failures) > len(shown) else ""
                session.add(Notification(
                    kind="warning",
                    title=f"Scan finished with {len(walk_failures)} unreachable folder(s)",
                    body=(
                        "Some directories couldn't be read. Common causes: "
                        "NAS disconnect, permission change, drive ejected mid-scan. "
                        "Files you've deleted from INSIDE these folders stay listed "
                        "until they're readable again (the rest of the library still "
                        "clears). Re-scan once the filesystem is stable.\n\n"
                        + "\n".join(shown) + more
                    )[:1000],
                ))
            # Watched-folders: a daemon-triggered scan that actually found new
            # files gets a persistent notification, so the user knows files
            # appeared and were matched without them clicking anything. Manual
            # scans stay quiet (the user is already watching the UI). Zero-new
            # auto-scans (the common debounce/poll case) stay quiet too.
            if scan and getattr(scan, "source", "manual") == "auto" and count > 0:
                from kira.models import Notification as _Notif
                plural = "s" if count != 1 else ""
                _auto_title = f"Auto-scan: {count} new file{plural} found"
                session.add(_Notif(
                    kind="info",
                    title=_auto_title,
                    body=(
                        "New media appeared in a watched folder and was matched. "
                        "Open Review to approve the renames."
                    ),
                ))
                # #10: fan out to external sinks (best-effort, never blocks).
                try:
                    from kira import notify
                    await notify.fan_out("info", _auto_title,
                                         "New media was found in a watched folder and matched.")
                except Exception as e:
                    logger.warning(f"_scan_worker: notification fan-out failed (non-fatal): {e!r}")
            await session.commit()

            # Self-prune the rename log to the configured retention window.
            # Scans are the natural recurring event on a long-running instance,
            # so pruning here keeps the "pruned daily" promise without a
            # separate scheduler. Best-effort — never fails a completed scan.
            try:
                from kira.api.history import prune_old_history
                await prune_old_history(session)
            except Exception as e:
                logger.warning(f"_scan_worker: history prune failed (non-fatal): {e!r}")

            # Cap the notifications table (unbounded before — a flapping
            # integration health check could grow it forever).
            try:
                from kira.api.system import prune_old_notifications
                await prune_old_notifications(session)
            except Exception as e:
                logger.warning(f"_scan_worker: notification prune failed (non-fatal): {e!r}")

            # Reap expired subtitle reuse-cache entries on the same recurring
            # event (subtitles.cache_retention_days; 0 = keep forever). Riding
            # along with the history prune keeps the "self-prunes without a
            # scheduler" promise. Best-effort — never fails a completed scan.
            try:
                from kira.subtitles.subcache import sweep_expired
                await sweep_expired()
            except Exception as e:
                logger.warning(f"_scan_worker: subtitle-cache sweep failed (non-fatal): {e!r}")

            # CR-10: the per-folder auto_rename hook USED to run here, INSIDE
            # the `_SCAN_LOCK` + DB scan-lock. A slow rename (artwork download
            # over a NAS) therefore blocked the next scan from starting. We now
            # only DECIDE here whether it should run and hand the work back to
            # `_scan_worker_locked`, which fires it AFTER releasing both locks.
            # Gate unchanged: auto-source scans that actually found new files.
            if scan and getattr(scan, "source", "manual") == "auto" and all_new:
                auto_rename_ids = list(all_new)

            # Background tech-tag enrichment (ALL scan sources). Reads true
            # container metadata (resolution/codec/HDR/channels/audio) OFF the
            # critical path — detached so the scan is already "completed" and
            # results are on screen; the chips + dupe ranker just sharpen on the
            # next /files poll. No-op unless `parsing.read_mediainfo` is on (and
            # the native lib is present). (Fire-and-forget — doesn't hold the lock.)
            #
            # Covers this scan's NEW files PLUS any already-known file that has
            # never had its container read (`mi_stamp` is null) — so a plain
            # rescan dependably backfills tech tags for the whole library, not
            # just freshly-discovered files. The union is gated on the setting so
            # a feature-off scan stays a pure no-op; self-limiting otherwise.
            enrich_ids = list(all_new)
            try:
                if await _read_mediainfo_setting(session):
                    _seen = set(enrich_ids)
                    enrich_ids.extend(
                        i for i in await _ids_missing_tech_tags(session) if i not in _seen
                    )
            except Exception as e:
                logger.warning(f"_scan_worker: tech-tag backfill set failed (non-fatal): {e!r}")
            _spawn_mediainfo_enrich(enrich_ids)
        except Exception:
            # The worker session may be POISONED here: a commit that lost SQLite's
            # single write-lock (busy_timeout, under scan↔auto-heal contention)
            # leaves the session rollback-pending, so recording the failure ON IT
            # raises AGAIN (PendingRollbackError) and buries the real cause behind a
            # bare "failed". Roll back and re-raise — `_scan_worker_locked` records
            # the failure WITH its reason and scrubs partial rows on a FRESH session.
            try:
                await session.rollback()
            except Exception:
                pass
            raise
    # CR-10: returned to `_scan_worker_locked` so the auto-rename phase runs
    # OUTSIDE the scan lock. None ⇒ nothing to auto-rename (manual scan, no new
    # files, or the worker errored before deciding).
    return auto_rename_ids


async def _release_db_scan_lock() -> None:
    """Drop the DB-level scan lock unconditionally.

    Resets `settings.system.scan_running` to 0 regardless of current
    value. Called in the worker's finally block so a successful scan,
    a crashed scan, and a synchronous cancel all release the lock.
    Multi-worker uvicorn deployments need this — without it, the
    worker holding the lock can never tell the OTHER workers to allow
    a new scan once it finishes.
    """
    from sqlalchemy import update as sql_update
    from kira.models import Setting
    async with SessionLocal() as sess:
        await sess.execute(
            sql_update(Setting)
            .where(Setting.key == "system.scan_running")
            .values(value=0)
        )
        await sess.commit()


def _root_reachable(root: str | Path) -> bool:
    """A configured scan root is reachable only if it IS a listable directory.
    A dead/unmounted NAS root (or a typo'd path) returns False so the scan is
    marked `completed_partial` — with a notification naming the root — instead
    of a misleading `completed` with 0 files. A root that exists but degrades
    mid-walk is caught separately by scanner's onerror callback."""
    try:
        return Path(str(root)).is_dir()
    except OSError:
        return False


async def reconcile_orphaned_scans() -> tuple[int, int]:
    """Settle scan + file rows left mid-flight by a crash/restart.

    A scan runs as an in-process background task — a process restart means NO
    worker is driving any 'pending'/'scanning'/'matching' row anymore, yet the
    rows stay in that status forever. That leaves (a) the Scan row a perpetual
    "scanning" in history, (b) the frontend re-attaching to a dead scan, and
    (c) MediaFile covers stuck in the match animation. Called once on boot
    (alongside the scan-lock reset) to settle them. Returns
    ``(scans_failed, files_reset)``."""
    from datetime import datetime, timezone
    from sqlalchemy import or_, update as sql_update
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with SessionLocal() as sess:
        res = await sess.execute(
            sql_update(Scan)
            .where(Scan.completed_at.is_(None))
            .where(or_(Scan.status == "pending", Scan.status == "scanning", Scan.status == "matching"))
            .values(status="failed: interrupted (restart)", completed_at=now)
        )
        # ALSO reset MediaFile rows the killed scan left mid-flight. A file stuck
        # in "matching"/"parsing" animates its cover forever and the row never
        # reaches a terminal state; reset to "discovered" so (a) the cover stops
        # spinning and (b) the next scan re-matches it (see the scan worker's
        # leftover-files merge). This is the other half of crash recovery — the
        # Scan row alone wasn't enough.
        file_res = await sess.execute(
            sql_update(MediaFile)
            .where(or_(MediaFile.status == "matching", MediaFile.status == "parsing"))
            .values(status="discovered", updated_at=_sql_func.now())
        )
        n_files = file_res.rowcount or 0
        if n_files:
            from kira.models import Notification
            sess.add(Notification(
                kind="warning",
                title="Scan interrupted by a restart",
                body=(
                    f"{n_files} file(s) were mid-match when the backend stopped, and "
                    f"were reset to pending. Click Scan to finish matching them — it "
                    f"resumes the leftover files without re-walking your library."
                ),
            ))
        await sess.commit()
        return (res.rowcount or 0, n_files)


async def _scan_worker_locked(scan_id: int, root_paths: list[str] | str) -> None:
    """EE-3: Process-locked wrapper around `_scan_worker` with orphan cleanup.

    Holds `_SCAN_LOCK` for the duration so a second background task can't
    walk the same root concurrently. If `_scan_worker` raises ANYTHING
    that escapes its inner try/except (rare — but e.g. SQLAlchemy session
    corruption can do it), we scrub the MediaFile rows this scan inserted
    so the next scan isn't fighting orphans. Without this, partial commits
    from a failed worker leave discovered-but-unmatched rows that auto-heal
    later tries to rematch, firing duplicate AniDB calls.

    Autopsy 6: the finally block always releases the DB-level lock so
    sibling uvicorn workers can spawn a new scan once this one ends.

    Bug A: signature widened to accept a list of roots. A bare string is
    promoted to `[string]` for back-compat with any internal caller that
    hasn't been updated. The endpoint always passes a list now; this
    fallback is defensive for tests / hand-spawned tasks.

    CR-10: the post-scan per-folder auto_rename phase runs OUTSIDE the lock.
    `_scan_worker` no longer renames inline; it returns the new-file ids to
    auto-rename, and we invoke `maybe_auto_rename` only after BOTH `_SCAN_LOCK`
    and the DB scan-lock flag have been released — so a slow artwork-download
    rename can't block the next scan from starting.
    """
    if isinstance(root_paths, str):
        root_paths = [root_paths]
    auto_rename_ids: list[int] | None = None
    async with _SCAN_LOCK:
        try:
            try:
                auto_rename_ids = await _scan_worker(scan_id, root_paths)
            except asyncio.CancelledError:
                # User clicked Stop. Mark the scan 'cancelled' and KEEP whatever was
                # discovered so far (unlike the failure path below, which deletes the
                # scan's rows). The outer finally still releases the lock; re-raise so
                # the task ends cancelled.
                #
                # The status write is retried once on a FRESH session: the likeliest
                # failure here is SQLite "database is locked" — and Stop lands exactly
                # when write load is heaviest. A swallowed failure used to leave the
                # row at scanning/matching forever (a phantom "running" scan that made
                # the next real scan render as a double).
                for _attempt in (1, 2):
                    try:
                        async with SessionLocal() as _cx_sess:
                            _cx = await _cx_sess.get(Scan, scan_id)
                            if _cx is not None and _cx.status in ("scanning", "matching"):
                                _cx.status = "cancelled"
                                _cx.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                            # Files the cancelled cluster left mid-flight would
                            # otherwise be stranded: the resume logic only picks up
                            # 'discovered'/'no_match', so 'matching'/'parsing' rows
                            # stayed in the spinner forever (until a full restart's
                            # reconcile). Same repair boot reconcile does.
                            await _cx_sess.execute(
                                sa_update(MediaFile)
                                .where(or_(MediaFile.status == "matching",
                                           MediaFile.status == "parsing"))
                                .values(status="discovered", updated_at=_sql_func.now())
                            )
                            await _cx_sess.commit()
                        break
                    except Exception as _ce:
                        logger.warning(
                            f"_scan_worker_locked: marking cancelled failed "
                            f"(attempt {_attempt}): {_ce!r}")
                raise
            except Exception as e:
                async with SessionLocal() as cleanup:
                    # Delete dependent Match rows FIRST. The matches FK has no
                    # ON DELETE CASCADE on already-created DBs, so with
                    # foreign_keys=ON the bulk MediaFile delete below would be
                    # rejected with a constraint error (which would then mask
                    # the real exception we're trying to re-raise). A failed
                    # scan's rows rarely have matches yet, but be correct anyway.
                    mf_ids = select(MediaFile.id).where(MediaFile.scan_id == scan_id)
                    await cleanup.execute(
                        delete(Match).where(Match.media_file_id.in_(mf_ids))
                    )
                    await cleanup.execute(
                        delete(MediaFile).where(MediaFile.scan_id == scan_id)
                    )
                    # Mark the scan FAILED so it doesn't sit at "scanning" forever.
                    # Previously the row was left untouched on a worker exception —
                    # the lock released, but the status never flipped, so a crash
                    # before the first file showed in the UI as an eternal
                    # in-progress scan at 0 files (exactly what the user saw).
                    #
                    # The gate covers EVERY non-terminal status, not just
                    # "scanning": an exception escaping during Phase 2 (status
                    # "matching" — e.g. `database is locked` under write
                    # contention) used to leave the row running forever with a
                    # dead task and freed locks. The next scan then started
                    # fine, and the DB held TWO rows that both read as live —
                    # the reported "scan seems to run 2 times".
                    _failed = await cleanup.get(Scan, scan_id)
                    if _failed is not None and not str(_failed.status or "").startswith(
                        ("completed", "cancelled", "failed")
                    ):
                        # Record the REAL reason (e.g. "database is locked") instead
                        # of a bare "failed", so the UI + logs explain the failure.
                        _failed.status = f"failed: {e}"[:200]
                        _failed.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    # Files from EARLIER scans that this run resumed (leftover /
                    # no_match retries) aren't covered by the scan_id delete
                    # above — if the crash caught them mid-'matching' they'd be
                    # stranded in the spinner and invisible to the next scan's
                    # resume query. Reset them like boot reconcile does.
                    await cleanup.execute(
                        sa_update(MediaFile)
                        .where(or_(MediaFile.status == "matching",
                                   MediaFile.status == "parsing"))
                        .values(status="discovered", updated_at=_sql_func.now())
                    )
                    await cleanup.commit()
                logger.exception(f"_scan_worker_locked: scan {scan_id} failed — marked failed")
                raise
        finally:
            # Always drop the DB lock — success, crash, cancellation.
            # Wrapped in try/except so a release failure can't mask the
            # original exception bubbling up from _scan_worker.
            try:
                await _release_db_scan_lock()
            except Exception as e:
                logger.warning(f"_scan_worker_locked: failed to release scan lock: {e!r}")

    # CR-10: BOTH locks are now released (the `async with _SCAN_LOCK` block has
    # exited and its finally dropped the DB flag). Run the auto-rename phase
    # unlocked so the next scan's 409 concurrency guard is already clear. On the
    # error path `_scan_worker` re-raised above, so `auto_rename_ids` stays None
    # and we skip — never renaming a failed scan's rows.
    if auto_rename_ids:
        try:
            from kira.watcher import maybe_auto_rename
            await maybe_auto_rename(scan_id, auto_rename_ids)
        except Exception as e:
            logger.warning(f"_scan_worker_locked: auto_rename hook failed (non-fatal): {e!r}")


async def _reparse_worker(scan_id: int, media_type: str | None = None, file_ids: list[int] | None = None) -> None:
    """In-place re-parse of the EXISTING library — or a SCOPE of it.

    `file_ids` (one album/show/movie) or `media_type` ("music"/"anime"/"tv"/
    "movie") narrows the set; neither → the whole library. Everything below
    operates on the resulting `all_ids`, so re-parse + re-match + the MediaInfo
    re-enrich all stay confined to the scope (a per-album reparse re-reads only
    that album's container tags — seconds, not a whole-library pass).

    A normal re-scan SKIPS files already in the DB, so parser improvements
    (named-season parsing, specials, title cleanup) and folder-level series
    locking never reach an already-indexed library. This re-runs the parser
    on every stored MediaFile from its current path, re-applies the folder
    lock, then re-matches NON-manual files. Manual pins + rename history are
    preserved (manual files are excluded from the match phase). Reuses the
    Scan row for progress so the frontend's scan banner shows it like a scan.
    """
    async with SessionLocal() as session:
        try:
            _stmt = select(MediaFile.id)
            if file_ids:
                _stmt = _stmt.where(MediaFile.id.in_(file_ids))
            elif media_type:
                _stmt = _stmt.where(MediaFile.media_type == media_type)
            all_ids = list((await session.scalars(_stmt)).all())
            total = len(all_ids)
            scan = await session.get(Scan, scan_id)
            if scan:
                scan.status = "scanning"
                scan.file_count = 0
                scan.estimated_total = total
            await session.commit()

            # ── Re-parse every file in place. parse_path is pure string work
            # (name + parent), no disk I/O, so this is fast even for large
            # libraries. file_size already lives on the row — don't re-stat.
            for i, fid in enumerate(all_ids):
                mf = await session.get(MediaFile, fid)
                if mf is None or not mf.file_path:
                    continue
                try:
                    parsed = parse_path(mf.file_path)
                except Exception as e:
                    logger.warning(f"_reparse_worker: parse failed for {fid}: {e!r}")
                    continue
                mf.parsed_data = parsed.to_dict()
                mf.media_type = parsed.media_type
                mf.series_key = _compute_series_key(parsed, file_path=mf.file_path)
                mf.variant_key = _compute_variant_key(parsed)
                if (i + 1) % SCAN_COMMIT_EVERY == 0:
                    scan = await session.get(Scan, scan_id)
                    if scan:
                        scan.file_count = i + 1
                        scan.current_path = mf.file_path
                    await session.commit()
                    await asyncio.sleep(0)
            await session.commit()

            # ── Folder-level series lock (Phase 11) on the fresh parses ──
            try:
                relocked = await _apply_folder_series_lock(session, all_ids)
                if relocked:
                    logger.info(f"_reparse_worker: folder-lock relocked {relocked} file(s)")
            except Exception as e:
                logger.warning(f"_reparse_worker: folder-lock failed (non-fatal): {e!r}")

            # ── Re-match, preserving manual pins ──
            scan = await session.get(Scan, scan_id)
            if scan:
                scan.status = "matching"
                scan.current_path = None
            await session.commit()

            manual_fids = set((await session.scalars(
                select(Match.media_file_id).where(
                    Match.is_manual.is_(True), Match.is_selected.is_(True),
                )
            )).all())
            to_match = [fid for fid in all_ids if fid not in manual_fids]

            async with httpx.AsyncClient() as client:
                registry = await registry_from_settings(client)
                engine = MatchEngine(registry)
                await _match_phase(session, engine, to_match, scan_id)

            scan = await session.get(Scan, scan_id)
            if scan:
                scan.status = "completed"
                scan.current_path = None
                scan.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            from kira.models import Notification
            matched_count = len(to_match)
            session.add(Notification(
                kind="success",
                title=f"Re-parse complete: {total} file{'s' if total != 1 else ''}",
                body=f"Re-parsed all files and re-matched {matched_count} ({total - matched_count} manual pins preserved).",
            ))
            await session.commit()

            # Background tech-tag enrichment over the whole re-parsed library —
            # detached so re-parse reports "complete" immediately; chips fill in
            # after. No-op unless `parsing.read_mediainfo` is on. This is also
            # how an authoritative-mode re-parse applies container truth to every
            # file without blocking the request.
            _spawn_mediainfo_enrich(all_ids)
        except Exception as e:
            logger.warning(f"_reparse_worker failed: {e!r}")
            # Same poisoned-session guard as _scan_worker: a lost-write-lock commit
            # leaves this session rollback-pending, so stamp the failure on a FRESH
            # one (recording on the poisoned session would raise PendingRollbackError).
            try:
                await session.rollback()
            except Exception:
                pass
            try:
                async with SessionLocal() as _es:
                    scan = await _es.get(Scan, scan_id)
                    if scan:
                        scan.status = f"failed: {e}"[:200]
                        scan.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    await _es.commit()
            except Exception as _e2:
                logger.warning(f"_reparse_worker: recording failure status failed: {_e2!r}")


async def _reparse_worker_locked(scan_id: int, media_type: str | None = None, file_ids: list[int] | None = None) -> None:
    """Process-locked wrapper around `_reparse_worker`.

    Unlike `_scan_worker_locked` it NEVER deletes MediaFile rows on failure —
    re-parse runs against the EXISTING library, so a mid-run crash must leave
    the user's files + matches intact. Always releases the DB scan lock.
    """
    async with _SCAN_LOCK:
        try:
            await _reparse_worker(scan_id, media_type, file_ids)
        except asyncio.CancelledError:
            # User clicked Stop. Mark the scan 'cancelled' and KEEP whatever was
            # re-parsed / re-matched so far (re-parse never deletes rows). Re-raise
            # so the task ends cancelled; the finally still releases the lock.
            try:
                async with SessionLocal() as _cx_sess:
                    _cx = await _cx_sess.get(Scan, scan_id)
                    if _cx is not None and _cx.status in ("scanning", "matching"):
                        _cx.status = "cancelled"
                        _cx.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                        await _cx_sess.commit()
            except Exception as _ce:
                logger.warning(f"_reparse_worker_locked: marking cancelled failed: {_ce!r}")
            raise
        finally:
            try:
                await _release_db_scan_lock()
            except Exception as e:
                logger.warning(f"_reparse_worker_locked: failed to release scan lock: {e!r}")


class ReparseScope(BaseModel):
    """Narrow a reparse to a media TYPE (just music / anime / tv / movies) or a
    specific set of FILES (one album / show / movie). Both null → whole library."""
    media_type: str | None = None        # "music" | "anime" | "tv" | "movie"
    file_ids: list[int] | None = None


@router.post("/reparse", response_model=ScanOut, status_code=201)
async def reparse_library(
    scope: ReparseScope | None = None,
    session: AsyncSession = Depends(get_session),
) -> Scan:
    """Re-parse the EXISTING library in place and re-match it.

    A scan skips already-indexed files; this re-runs the parser on every
    stored file so parser + folder-lock improvements apply WITHOUT a
    destructive DB reset. Manual pins + rename history are preserved. Uses
    the same single-scan lock as create_scan (409 if a scan is running).
    """
    media_type = scope.media_type if scope else None
    file_ids = scope.file_ids if scope else None
    if media_type and media_type not in ("music", "anime", "tv", "movie"):
        raise HTTPException(400, f"invalid media_type {media_type!r} — must be music/anime/tv/movie")
    if _SCAN_LOCK.locked():
        raise HTTPException(409, "A scan is already running. Please wait for it to complete.")

    import time as _time
    from sqlalchemy import or_, update as sql_update
    from kira.models import Setting
    now_ts = int(_time.time())
    stale_cutoff = now_ts - _SCAN_LOCK_MAX_AGE_SEC
    res = await session.execute(
        sql_update(Setting)
        .where(Setting.key == "system.scan_running")
        .where(or_(Setting.value == 0, Setting.value < stale_cutoff))
        .values(value=now_ts)
    )
    await session.commit()
    if (res.rowcount or 0) == 0:
        raise HTTPException(409, "A scan is already running. Please wait for it to complete.")

    try:
        label = (
            f"(re-parse {media_type})" if media_type
            else "(re-parse selected files)" if file_ids
            else "(re-parse existing library)"
        )
        scan = Scan(root_path=label, status="scanning")
        session.add(scan)
        await session.commit()
        await session.refresh(scan)
        # Track the worker in _SCAN_TASKS (exactly like _start_scan) so the Stop
        # button can cancel it. FastAPI BackgroundTasks are NOT cancellable and
        # were never registered here — so cancel_scan fell through to its stale-
        # lock path, which 500'd on the SQLite write lock the live worker still
        # held AND never told the worker to stop (it kept re-stamping 'matching',
        # so the scan could neither stop nor let a new one start). spawn_tracked +
        # registration makes Stop hit the real task.cancel() path instead.
        task = spawn_tracked(
            _reparse_worker_locked(scan.id, media_type, file_ids), label="reparse_worker",
        )
        _SCAN_TASKS[scan.id] = task
        task.add_done_callback(lambda t, sid=scan.id: _SCAN_TASKS.pop(sid, None))
        return scan
    except Exception:
        try:
            await _release_db_scan_lock()
        except Exception as e:
            logger.warning(f"reparse_library: lock release after failure also failed: {e!r}")
        raise


async def _start_scan(paths: list[str], source: str = "manual") -> int | None:
    """Shared scan trigger: claim the locks, create a Scan row, launch the worker.

    Single source of truth for kicking off a scan — used by the manual POST
    /scans endpoint (source="manual") and the watched-folders daemon
    (source="auto"). Respects BOTH the process-level `_SCAN_LOCK` fast-fail and
    the multi-worker DB CAS on `system.scan_running` (same discipline as the
    original create_scan).

    `paths` must already be cleaned by the caller (the manual endpoint also
    validates existence and returns 400s; the watcher only passes existing
    dirs). Blank entries are defensively dropped + deduped here.

    Returns the new scan id, or ``None`` if a scan is already running (caller
    decides 409 vs silent skip). The launched worker owns the locks and
    releases them in its finally block.
    """
    # Clean + dedup, preserving order.
    seen: set[str] = set()
    effective_roots: list[str] = []
    for p in paths:
        if not isinstance(p, str):
            continue
        stripped = p.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        effective_roots.append(stripped)
    if not effective_roots:
        return None

    # Fast reject: this process is already running a scan.
    if _SCAN_LOCK.locked():
        return None

    # ── Autopsy 6: atomic DB-level scan lock (multi-worker safe) ──────
    # A single conditional UPDATE on the `system.scan_running` setting row.
    # The CAS allows the claim when the value is `0` (idle) OR a stale
    # timestamp from a crashed prior scan. Only the caller whose UPDATE
    # returns rowcount=1 proceeds.
    import time as _time
    from sqlalchemy import or_, update as sql_update
    from kira.models import Setting
    now_ts = int(_time.time())
    stale_cutoff = now_ts - _SCAN_LOCK_MAX_AGE_SEC
    async with SessionLocal() as session:
        res = await session.execute(
            sql_update(Setting)
            .where(Setting.key == "system.scan_running")
            .where(or_(Setting.value == 0, Setting.value < stale_cutoff))
            .values(value=now_ts)
        )
        await session.commit()
        if (res.rowcount or 0) == 0:
            return None

        # The lock is now claimed. Create the Scan row + hand off to the
        # worker (which releases the lock in its finally block). If anything
        # fails before hand-off, release the lock so the user can retry.
        try:
            scan = Scan(root_path=effective_roots[0], status="scanning", source=source)
            session.add(scan)
            await session.commit()
            await session.refresh(scan)
            scan_id = scan.id
        except Exception:
            try:
                await _release_db_scan_lock()
            except Exception as e:
                logger.warning(f"_start_scan: lock release after failure also failed: {e!r}")
            raise

    # Launch outside the session context. CR-11: route through spawn_tracked so
    # a strong reference is retained (asyncio only weakly refs a bare
    # create_task result — it could be GC'd / silently cancelled mid-scan) and
    # any escaping exception is logged rather than swallowed. The worker's
    # finally block still releases both locks.
    # NB: source is persisted on the Scan row (above); the worker reads it
    # back from the DB at completion to decide whether to fire the auto-scan
    # notification. _scan_worker_locked therefore takes only (id, roots).
    task = spawn_tracked(_scan_worker_locked(scan_id, effective_roots), label="scan_worker")
    _SCAN_TASKS[scan_id] = task
    task.add_done_callback(lambda t, sid=scan_id: _SCAN_TASKS.pop(sid, None))
    return scan_id


@router.post("", response_model=ScanOut, status_code=201)
async def create_scan(
    payload: ScanCreate,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> Scan:
    """Kick off a scan + match and return immediately. Frontend polls /scans/{id}.

    EE-3: Refuses to start a new scan while one is already running.
    Process-level lock catches the same-process double-click; DB-level
    check catches a different worker process (e.g. multi-worker gunicorn)
    that's already running a scan. 409 lets the frontend show a friendly
    "scan in progress" toast rather than silently spawning duplicates.
    """
    # Fast reject: this process is already running a scan. Saves the
    # DB roundtrip for the same-worker double-click case.
    if _SCAN_LOCK.locked():
        raise HTTPException(409, "A scan is already running. Please wait for it to complete.")

    # Bug A fix: resolve the effective root list. If the client passed
    # `root_paths`, walk all of them; otherwise fall back to the single
    # `root_path` (preserves the original API contract). Dedup + filter
    # empty paths so we don't walk the same dir twice.
    raw_roots: list[str] = (
        list(payload.root_paths) if payload.root_paths else [payload.root_path]
    )
    seen: set[str] = set()
    effective_roots: list[str] = []
    for p in raw_roots:
        if not isinstance(p, str):
            continue
        stripped = p.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        effective_roots.append(stripped)
    if not effective_roots:
        raise HTTPException(400, "No roots to scan. Set a library root in Settings → Paths.")

    # Pre-flight path check for EACH root. Most common silent failure after a
    # DB reset: the Setting table got wiped (clearing paths.library_root), the
    # frontend falls back to the '/media' default, and the scanner walks a
    # non-existent path returning zero files. Surface this as a 400 so the
    # frontend toast is honest rather than pretending the scan succeeded.
    from pathlib import Path as _Path
    for p in effective_roots:
        try:
            root = _Path(p).resolve()
        except (OSError, ValueError) as e:
            raise HTTPException(400, f"Invalid library path '{p}': {e}")
        if not root.exists():
            raise HTTPException(
                400,
                f"Library folder doesn't exist: {p}. Set it in Settings → Paths.",
            )
        if not root.is_dir():
            raise HTTPException(400, f"Library path is not a folder: {p}.")

    # Delegate to the shared trigger (claims locks + launches the worker).
    # None means another worker grabbed the lock between our fast-fail check
    # and the DB CAS → 409, identical to the original behaviour.
    scan_id = await _start_scan(effective_roots, source="manual")
    if scan_id is None:
        raise HTTPException(409, "A scan is already running. Please wait for it to complete.")
    scan = await session.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(500, "Scan row vanished after creation.")
    return scan


@router.post("/{scan_id}/cancel", response_model=dict)
async def cancel_scan(
    scan_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Stop a running scan (the Stop button).

    Cancels the worker at its next await — it marks the scan 'cancelled', KEEPS
    whatever it discovered, and frees the lock (bounded latency: a few seconds /
    one cluster). If there's no live task (a stale lock from a crashed worker —
    the "already scanning but nothing's showing" phantom), this also force-marks
    the scan cancelled and releases the lock so the UI unsticks immediately.
    """
    scan = await session.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(404, "Scan not found.")
    if scan.status not in ("scanning", "matching"):
        return {"ok": True, "status": scan.status, "already_done": True}

    task = _SCAN_TASKS.get(scan_id)
    if task is not None and not task.done():
        task.cancel()
        return {"ok": True, "status": "cancelling"}

    # No live task for a scan the DB still calls running → a stale lock. Force it
    # cancelled + release the lock so the user isn't stuck behind a phantom scan.
    scan.status = "cancelled"
    scan.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await session.commit()
    # Only release the DB lock when NO other scan task is live — force-cancelling
    # an old stuck row while a NEW scan runs must not yank the lock out from
    # under the live worker (its own finally releases it when it exits).
    _any_live = any(not t.done() for t in _SCAN_TASKS.values())
    if not _any_live:
        try:
            await _release_db_scan_lock()
        except Exception as e:
            logger.warning(f"cancel_scan: stale-lock release failed: {e!r}")
    return {"ok": True, "status": "cancelled", "forced": True}


@router.get("", response_model=list[ScanOut])
async def list_scans(session: AsyncSession = Depends(get_session)) -> list[Scan]:
    result = await session.scalars(select(Scan).order_by(Scan.created_at.desc()))
    return list(result)


@router.get("/watch/status")
async def watch_status() -> dict:
    """Current state of the watched-folders auto-scan daemon."""
    from kira.watcher import watcher  # lazy: avoid import cycle
    return watcher.status()


@router.get("/{scan_id}", response_model=ScanOut)
async def get_scan(scan_id: int, session: AsyncSession = Depends(get_session)) -> Scan:
    scan = await session.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(404, "Scan not found")
    return scan
