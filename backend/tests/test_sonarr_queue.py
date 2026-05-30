"""Unit tests for the Sonarr queue interpretation helpers.

`_normalize_status` collapses Sonarr's three status fields into Kira's
one canonical state. `_parse_timeleft` turns Sonarr's "HH:MM:SS" string
into seconds. Both are pure — no network, no DB. These tests are the
defensive net against future Sonarr API drift: if Sonarr renames a
status value, the existing-behavior test that locks the old name to
the right output will fail loudly and we'll see the case to add.

`get_queue` is integration-y (needs a real httpx.AsyncClient) but we
can still exercise the body-parsing logic by feeding mocked responses.
"""
from __future__ import annotations

import pytest

from kira.integrations.sonarr import _normalize_status, _parse_timeleft


# ─────────────────────────────────────────────────────────────────────
# _parse_timeleft
# ─────────────────────────────────────────────────────────────────────


def test_parse_timeleft_basic() -> None:
    assert _parse_timeleft("00:00:30") == 30
    assert _parse_timeleft("00:05:00") == 5 * 60
    assert _parse_timeleft("01:30:00") == 5400
    assert _parse_timeleft("12:00:00") == 12 * 3600


def test_parse_timeleft_with_seconds() -> None:
    # 1h 30m 45s
    assert _parse_timeleft("01:30:45") == 3600 + 30 * 60 + 45


def test_parse_timeleft_invalid() -> None:
    # Wrong format (Sonarr should never emit these, but defensive)
    assert _parse_timeleft(None) is None
    assert _parse_timeleft("") is None
    assert _parse_timeleft("not-a-time") is None
    assert _parse_timeleft("01:30") is None  # missing seconds slot
    assert _parse_timeleft("xx:yy:zz") is None
    assert _parse_timeleft("-1:00:00") is None  # negative
    assert _parse_timeleft(0) is None  # non-string


# ─────────────────────────────────────────────────────────────────────
# _normalize_status — the priority order matters
# ─────────────────────────────────────────────────────────────────────


def test_normalize_failed_hard() -> None:
    # status=failed beats anything else.
    assert _normalize_status({"status": "failed"}) == "failed"
    # trackedDownloadStatus=error → failed
    assert _normalize_status({"trackedDownloadStatus": "error"}) == "failed"
    # trackedDownloadState in {downloadfailed, failedpending} → failed
    assert _normalize_status({"trackedDownloadState": "downloadFailed"}) == "failed"
    assert _normalize_status({"trackedDownloadState": "failedPending"}) == "failed"


def test_normalize_importing() -> None:
    assert _normalize_status({"trackedDownloadState": "importing"}) == "importing"
    assert _normalize_status({"trackedDownloadState": "importPending"}) == "importing"


def test_normalize_completed() -> None:
    assert _normalize_status({"trackedDownloadState": "imported"}) == "completed"
    assert _normalize_status({"status": "completed"}) == "completed"


def test_normalize_warning_over_downloading() -> None:
    # A row that's downloading AND warning should surface as warning —
    # the warning is the user-actionable signal.
    rec = {"status": "downloading", "trackedDownloadStatus": "warning"}
    assert _normalize_status(rec) == "warning"


def test_normalize_downloading() -> None:
    assert _normalize_status({"status": "downloading"}) == "downloading"
    assert _normalize_status({"trackedDownloadState": "downloading"}) == "downloading"


def test_normalize_paused_states_are_warnings() -> None:
    # Sonarr's "paused" / "delay" / "downloadClientUnavailable" are not
    # "queued" — the user usually needs to know the download is stuck.
    for s in ("paused", "delay", "downloadClientUnavailable", "fallback"):
        assert _normalize_status({"status": s}) == "warning", s


def test_normalize_queued_default() -> None:
    # Empty / unknown → queued (the safest catch-all).
    assert _normalize_status({}) == "queued"
    assert _normalize_status({"status": ""}) == "queued"
    assert _normalize_status({"status": "queued"}) == "queued"
    assert _normalize_status({"status": "wat"}) == "queued"


def test_normalize_failed_takes_priority_over_warning() -> None:
    # If both are set, failed wins — wedging a downloadFailed item under
    # a warning trackedDownloadStatus shouldn't soften the failed signal.
    rec = {"status": "failed", "trackedDownloadStatus": "warning"}
    assert _normalize_status(rec) == "failed"


def test_normalize_case_insensitive() -> None:
    # Sonarr's casing varies between fields (camelCase for some, lowercase
    # for others). We lowercase everything before checking, so mixed
    # casing should resolve correctly.
    assert _normalize_status({"status": "DOWNLOADING"}) == "downloading"
    assert _normalize_status({"trackedDownloadStatus": "ERROR"}) == "failed"
    assert _normalize_status({"trackedDownloadState": "Importing"}) == "importing"
