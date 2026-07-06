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
