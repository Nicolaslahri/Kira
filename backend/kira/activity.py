"""In-memory background-activity registry, surfaced via GET /api/v1/activity.

Gives the frontend a cheap signal for "the backend is doing background work"
— primarily the boot auto-heal sweep (which can re-match hundreds of stale
rows over several minutes) and the first-boot anime-mapping download. Without
it the UI looks idle while the library quietly heals itself, and a user who
just restarted has no idea anything is happening.

Best-effort by design: a progress HINT, not a durable job queue. A restart
clears it (correct — the work restarts too). No locking needed — the event
loop is single-threaded and every writer is a coroutine.

Crash-safety: `snapshot()` reports a job inactive once its last update is
older than ``stale_after`` seconds, so a task that dies without calling
``end()`` never pins a spinner in the UI forever.
"""

from __future__ import annotations

import time
from typing import Any

# name -> {label, active, done, total, updated_at}
_jobs: dict[str, dict[str, Any]] = {}
# One-shot boot recovery summary (set once by the lifespan reconcile).
_boot: dict[str, Any] = {}


def begin(name: str, label: str, total: int | None = None) -> None:
    _jobs[name] = {
        "label": label,
        "active": True,
        "done": 0,
        "total": total,
        "updated_at": time.time(),
    }


def progress(name: str, done: int, total: int | None = None) -> None:
    job = _jobs.get(name)
    if job is None:
        # progress without begin — synthesize one so the signal isn't lost.
        begin(name, name, total)
        job = _jobs[name]
    job["done"] = done
    if total is not None:
        job["total"] = total
    job["active"] = True
    job["updated_at"] = time.time()


def end(name: str) -> None:
    job = _jobs.get(name)
    if job is not None:
        job["active"] = False
        job["updated_at"] = time.time()


def set_boot_recovery(scans: int, files: int) -> None:
    """Record what a restart cleaned up so the UI can reassure the user
    ('recovered N stuck files') instead of silently swallowing a crash."""
    _boot["scans_reset"] = int(scans)
    _boot["files_reset"] = int(files)
    _boot["at"] = time.time()


def snapshot(*, stale_after: float = 120.0) -> dict[str, Any]:
    """Current activity. Jobs not updated within ``stale_after`` seconds are
    reported inactive even if a crash left their ``active`` flag set."""
    now = time.time()
    jobs: list[dict[str, Any]] = []
    for name, job in _jobs.items():
        active = bool(job["active"]) and (now - job["updated_at"] < stale_after)
        jobs.append({
            "name": name,
            "label": job["label"],
            "active": active,
            "done": job["done"],
            "total": job["total"],
        })
    return {
        "jobs": jobs,
        "active": any(j["active"] for j in jobs),
        "boot": dict(_boot) if _boot else None,
    }


def reset() -> None:
    """Clear all state — for tests."""
    _jobs.clear()
    _boot.clear()
