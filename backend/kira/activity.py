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


def set_label(name: str, label: str) -> None:
    """Update a running job's human label WITHOUT touching its done/total —
    for live per-item narration (e.g. 'Show S01E07 · downloading EN'). Keeps
    the job marked active + fresh so it isn't reported stale mid-step."""
    job = _jobs.get(name)
    if job is None:
        begin(name, label)
        return
    job["label"] = label
    job["active"] = True
    job["updated_at"] = time.time()


def end(name: str, *, ok: bool = True, detail: str | None = None) -> None:
    """Finish a job with a FINAL state the UI can render after the spinner:
    `ok=True` → a green "done" beat with `detail` as the summary line;
    `ok=False` → a sticky red error card with `detail` as the explanation.

    The final state matters because a job can fail in under a poll interval
    (e.g. a rejected API key kills a subtitle batch on file 1) — without it
    the frontend never even sees the job, and the user learns nothing.
    `snapshot()` keeps finished jobs visible for a grace window."""
    job = _jobs.get(name)
    if job is not None:
        job["active"] = False
        job["state"] = "done" if ok else "error"
        job["detail"] = detail
        job["ended_at"] = time.time()
        job["updated_at"] = time.time()


def set_boot_recovery(scans: int, files: int) -> None:
    """Record what a restart cleaned up so the UI can reassure the user
    ('recovered N stuck files') instead of silently swallowing a crash."""
    _boot["scans_reset"] = int(scans)
    _boot["files_reset"] = int(files)
    _boot["at"] = time.time()


# How long a FINISHED job stays in the snapshot, so a poll that missed the
# active phase still sees the outcome. Errors linger much longer than the
# green "done" beat — the user must get a chance to read what went wrong.
_DONE_LINGER = 15.0
_ERROR_LINGER = 600.0


def snapshot(*, stale_after: float = 120.0) -> dict[str, Any]:
    """Current activity. Jobs not updated within ``stale_after`` seconds are
    reported inactive even if a crash left their ``active`` flag set.
    Finished jobs ride along (state done/error + detail) for a linger window
    so even a sub-second failure reaches the UI."""
    now = time.time()
    jobs: list[dict[str, Any]] = []
    for name, job in _jobs.items():
        active = bool(job["active"]) and (now - job["updated_at"] < stale_after)
        state = "running" if active else job.get("state")
        if not active:
            ended = job.get("ended_at")
            if state not in ("done", "error") or ended is None:
                continue  # stale-crashed or pre-final-state legacy job — hide
            linger = _ERROR_LINGER if state == "error" else _DONE_LINGER
            if now - ended > linger:
                continue
        jobs.append({
            "name": name,
            "label": job["label"],
            "active": active,
            "state": state,
            "detail": job.get("detail"),
            "ended_at": job.get("ended_at"),
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
