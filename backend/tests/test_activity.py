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


def test_stale_job_dropped_from_snapshot() -> None:
    """A task that died without end() must not pin a spinner forever — it
    disappears from the snapshot entirely (no zombie pill, no false 'done')."""
    activity.begin("heal", "Healing")
    # Force the updated_at far into the past.
    activity._jobs["heal"]["updated_at"] = time.time() - 999
    snap = activity.snapshot(stale_after=120.0)
    assert not any(j["name"] == "heal" for j in snap["jobs"])
    assert snap["active"] is False


def test_end_done_state_lingers_then_drops() -> None:
    """A finished job stays visible (state=done + detail) for a grace window
    so a poll that missed the active phase still sees the outcome."""
    activity.begin("subtitle_backfill", "Finding subtitles", total=3)
    activity.end("subtitle_backfill", ok=True, detail="Saved 2 subtitles · 1 not found")
    job = next(j for j in activity.snapshot()["jobs"] if j["name"] == "subtitle_backfill")
    assert job["active"] is False
    assert job["state"] == "done"
    assert job["detail"] == "Saved 2 subtitles · 1 not found"
    # ...and drops once the linger window passes.
    activity._jobs["subtitle_backfill"]["ended_at"] = time.time() - 999
    assert not any(j["name"] == "subtitle_backfill" for j in activity.snapshot()["jobs"])


def test_end_error_state_lingers_longer() -> None:
    """Errors must outlive the short 'done' beat — the user needs time to
    read what went wrong (a sub-second failure would otherwise vanish)."""
    activity.begin("subtitle_backfill", "Finding subtitles")
    activity.end("subtitle_backfill", ok=False, detail="OpenSubtitles rejected the API key")
    job = next(j for j in activity.snapshot()["jobs"] if j["name"] == "subtitle_backfill")
    assert job["state"] == "error"
    # Still visible after the done-linger would have expired…
    activity._jobs["subtitle_backfill"]["ended_at"] = time.time() - 60
    assert any(j["name"] == "subtitle_backfill" for j in activity.snapshot()["jobs"])
    # …gone after the error linger.
    activity._jobs["subtitle_backfill"]["ended_at"] = time.time() - 9999
    assert not any(j["name"] == "subtitle_backfill" for j in activity.snapshot()["jobs"])


def test_rerun_after_error_resets_state() -> None:
    activity.begin("job", "x")
    activity.end("job", ok=False, detail="boom")
    activity.begin("job", "x again")
    job = next(j for j in activity.snapshot()["jobs"] if j["name"] == "job")
    assert job["state"] == "running" and job["active"] is True


def test_boot_recovery_summary() -> None:
    assert activity.snapshot()["boot"] is None
    activity.set_boot_recovery(2, 5)
    boot = activity.snapshot()["boot"]
    assert boot["scans_reset"] == 2
    assert boot["files_reset"] == 5
