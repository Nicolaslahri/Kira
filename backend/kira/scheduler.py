"""Scheduled full rescan — the "scan nightly at 04:00" toggle.

The watcher covers NEW files as they land; this covers everything else
(deleted files pruning, provider auto-heal, metadata refresh) on a clock.
Settings: `scanning.scheduled` (bool, default off) + `scanning.scheduled_time`
("HH:MM", default "04:00"). The loop wakes once a minute, re-reads the
settings (a toggle applies without restart), and fires at most once per day.
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger("kira.scheduler")

_last_run_day: str | None = None
_last_backup_day: str | None = None


async def _maybe_auto_backup(s) -> None:
    """Daily settings auto-backup (advanced.auto_backup, default off): writes
    the same JSON the manual Export produces into <config>/backups/, keeping
    the newest 14. One file per day; secrets included — same contract as the
    manual export (it's the user's own box)."""
    global _last_backup_day
    from kira.settings_store import get_raw, unwrap
    enabled = unwrap(await get_raw(s, "advanced.auto_backup"))
    if enabled is not True and str(enabled).strip().lower() not in ("true", "1", "on", "yes"):
        return
    today = time.strftime("%Y-%m-%d", time.localtime())
    if _last_backup_day == today:
        return
    _last_backup_day = today
    import json
    from kira.api.settings import export_settings_backup
    from kira.config import cache_dir
    data = await export_settings_backup(s)
    # cache_dir is the persisted volume (/config/.cache in Docker) — backups
    # live beside it so they survive container rebuilds.
    bdir = cache_dir().parent / "backups" if cache_dir().name == ".cache" else cache_dir() / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / f"kira-settings-{today}.json").write_text(json.dumps(data), encoding="utf-8")
    # Retention: newest 14 by name (dated names sort chronologically).
    for p in sorted(bdir.glob("kira-settings-*.json"))[:-14]:
        try:
            p.unlink()
        except OSError:
            pass
    logger.info("auto-backup written: kira-settings-%s.json", today)


async def scheduled_rescan_loop() -> None:
    global _last_run_day
    from kira.database import SessionLocal
    from kira.settings_store import get_raw, unwrap
    while True:
        try:
            await asyncio.sleep(60)
            async with SessionLocal() as s:
                enabled = unwrap(await get_raw(s, "scanning.scheduled"))
                at = unwrap(await get_raw(s, "scanning.scheduled_time"))
                # Piggyback the daily auto-backup on the same 60s clock —
                # cheap, and it can never collide with a mid-scan DB lock.
                try:
                    await _maybe_auto_backup(s)
                except Exception as e:  # noqa: BLE001 — backup must never kill the clock
                    logger.warning("auto-backup failed (non-fatal): %r", e)
            if enabled is not True and str(enabled).strip().lower() not in ("true", "1", "on", "yes"):
                continue
            hhmm = str(at or "04:00").strip()
            now = time.localtime()
            today = time.strftime("%Y-%m-%d", now)
            if f"{now.tm_hour:02d}:{now.tm_min:02d}" != hhmm or _last_run_day == today:
                continue
            _last_run_day = today
            from kira.api.scans import _start_scan
            from kira.api.webhooks import _configured_roots
            async with SessionLocal() as s:
                roots = await _configured_roots(s)
            if not roots:
                continue
            scan_id = await _start_scan(roots, source="scheduled")
            logger.info("scheduled rescan fired (%s): scan_id=%s", hhmm, scan_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — the clock must never die
            logger.warning("scheduled rescan loop error (non-fatal): %r", e)
