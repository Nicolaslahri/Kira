"""Background-activity registry (surfaced at GET /api/v1/activity)."""

from __future__ import annotations

import time

from kira import activity


def setup_function() -> None:
    activity.reset()


def test_begin_progress_end_lifecycle() -> None:
    activity.begin("heal", "Healing library matches")
    snap = activity.snapshot()
    assert snap["active"] is True
    job = next(j for j in snap["jobs"] if j["name"] == "heal")
    assert job["label"] == "Healing library matches"
    assert job["done"] == 0

    activity.progress("heal", 12)
    job = next(j for j in activity.snapshot()["jobs"] if j["name"] == "heal")
    assert job["done"] == 12
    assert job["active"] is True

    activity.end("heal")
    assert activity.snapshot()["active"] is False


def test_progress_without_begin_synthesizes_job() -> None:
    activity.progress("rematch", 3, total=10)
    job = next(j for j in activity.snapshot()["jobs"] if j["name"] == "rematch")
    assert job["done"] == 3
    assert job["total"] == 10
    assert job["active"] is True


def test_stale_job_reported_inactive() -> None:
    """A task that died without end() must not pin a spinner forever."""
    activity.begin("heal", "Healing")
    # Force the updated_at far into the past.
    activity._jobs["heal"]["updated_at"] = time.time() - 999
    snap = activity.snapshot(stale_after=120.0)
    job = next(j for j in snap["jobs"] if j["name"] == "heal")
    assert job["active"] is False
    assert snap["active"] is False


def test_boot_recovery_summary() -> None:
    assert activity.snapshot()["boot"] is None
    activity.set_boot_recovery(2, 5)
    boot = activity.snapshot()["boot"]
    assert boot["scans_reset"] == 2
    assert boot["files_reset"] == 5
