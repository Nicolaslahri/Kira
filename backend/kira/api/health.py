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

from kira import __version__ as _kira_version
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

    # Network diagnostics — ground truth from INSIDE this process. `force_ipv4`
    # is the runtime flag; `tmdb_families` is what address resolution actually
    # returns RIGHT NOW for TMDB. If force_ipv4 is true but tmdb_families still
    # contains AF_INET6, the IPv4 patch isn't taking effect in this process.
    force_ipv4 = None
    tmdb_families: list[str] = []
    try:
        import socket as _socket
        from kira import net as _net
        force_ipv4 = _net.force_ipv4_enabled()
        tmdb_families = sorted({ai[0].name for ai in _socket.getaddrinfo("api.themoviedb.org", 443)})
    except Exception as e:  # noqa: BLE001
        tmdb_families = [f"resolve-error:{type(e).__name__}"]

    return {
        "status": status,
        "db": "ok" if db_ok else "error",
        "db_error": db_err,
        "anidb_banned": anidb_banned,
        "anidb_circuit_open": anidb_circuit_open,
        "force_ipv4": force_ipv4,
        "tmdb_families": tmdb_families,
        "uptime_sec": int(time.monotonic() - _START_TIME),
        "version": _kira_version,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }
