"""PB-5: liveness + light readiness probe.

Designed to answer in under 50ms with NO provider HTTP calls so container
orchestrators (Docker HEALTHCHECK, k8s liveness/readiness probes) can hit
it as fast as their probe interval allows without burning rate-limit
budgets on external services.

Reports:
  - `status`: "ok" (everything reachable) | "degraded" (db down or anidb banned)
  - `db`: did a trivial SELECT 1 succeed
  - `anidb_banned`: is AniDB currently in our ban window
  - `anidb_circuit_open`: did the error-rate breaker trip
  - `uptime_sec`: seconds since process start
  - `version`: app version from config
  - `latency_ms`: how long THIS health check took (sanity self-check)
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kira.config import settings
from kira.database import get_session

router = APIRouter(tags=["health"])

# Captured at module import — gives us a stable process start time.
_START_TIME = time.monotonic()


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    started = time.monotonic()

    # DB liveness — cheap SELECT 1. If sqlite is locked or the file is
    # gone we want to know.
    db_ok = True
    db_err: str | None = None
    try:
        await session.scalar(select(1))
    except Exception as e:  # noqa: BLE001 — health probe must be defensive
        db_ok = False
        db_err = repr(e)[:120]

    # AniDB ban + circuit state — both purely in-memory / disk-cached,
    # no HTTP. Safe to call even when AniDB itself is unreachable.
    anidb_banned = False
    anidb_circuit_open = False
    try:
        from kira.providers.anidb import AniDBProvider
        anidb_banned = AniDBProvider.is_banned()
        anidb_circuit_open = AniDBProvider._circuit_open()
    except Exception:
        pass

    status = "ok"
    if not db_ok:
        status = "degraded"   # critical — orchestrators should restart us
    elif anidb_banned or anidb_circuit_open:
        status = "degraded"   # non-critical — app still serves the UI

    return {
        "status": status,
        "db": "ok" if db_ok else "error",
        "db_error": db_err,
        "anidb_banned": anidb_banned,
        "anidb_circuit_open": anidb_circuit_open,
        "uptime_sec": int(time.monotonic() - _START_TIME),
        "version": getattr(settings, "version", "0.0.0"),
        "latency_ms": int((time.monotonic() - started) * 1000),
    }
