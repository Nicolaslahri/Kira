"""API timestamps serialize as UTC ISO-8601 with a trailing 'Z'.

Regression for the "every notification shows 5 hours ago" bug: our timestamps
are stored NAIVE but mean UTC; emitted without a timezone, the browser's
`new Date("...")` parses them as LOCAL time, skewing every "x ago" by the
viewer's UTC offset. The `UtcDateTime` serializer stamps the 'Z'.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from kira.schemas import ScanOut, _utc_iso


def test_naive_is_treated_as_utc():
    assert _utc_iso(datetime(2026, 6, 5, 20, 0, 0)) == "2026-06-05T20:00:00Z"


def test_aware_is_converted_to_utc():
    # 15:00 at -05:00 is 20:00 UTC — must normalize, not just append.
    aware = datetime(2026, 6, 5, 15, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert _utc_iso(aware) == "2026-06-05T20:00:00Z"


def test_scan_out_emits_z_and_keeps_null_completed():
    s = ScanOut(id=1, root_path="/m", status="completed", file_count=0,
                created_at=datetime(2026, 6, 5, 20, 0, 0), completed_at=None)
    data = json.loads(s.model_dump_json())
    assert data["created_at"].endswith("Z")
    assert data["completed_at"] is None


def test_notification_out_emits_z():
    from kira.api.system import NotificationOut
    n = NotificationOut(id=1, kind="info", title="t", body=None, read=False,
                        created_at=datetime(2026, 6, 5, 20, 0, 0))
    assert json.loads(n.model_dump_json())["created_at"].endswith("Z")


def test_z_parses_back_to_same_instant():
    # The whole point: "Z" makes it unambiguous, so a round-trip matches the
    # original UTC instant (what the browser's Date() now does correctly).
    iso = _utc_iso(datetime(2026, 6, 5, 20, 0, 0))
    parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    assert parsed == datetime(2026, 6, 5, 20, 0, 0, tzinfo=timezone.utc)
