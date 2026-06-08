"""Pass 6 #6+#7 — auto-rename eligibility gate (pure logic)."""

from __future__ import annotations

from kira.watcher import compute_auto_rename_eligibility, merge_watch_config


def _cfg(folders: dict) -> dict:
    return merge_watch_config({"auto_scan": True, "folders": folders})


def test_auto_rename_folder_high_confidence_is_eligible() -> None:
    cfg = _cfg({"/media/downloads": {"mode": "auto_rename", "threshold": 0.9}})
    files = [(1, "/media/downloads/Show.S01E01.mkv", 0.95, True)]
    eligible, held = compute_auto_rename_eligibility(cfg, files)
    assert eligible == [1]
    assert held == 0


def test_below_threshold_is_held_not_renamed() -> None:
    cfg = _cfg({"/media/downloads": {"mode": "auto_rename", "threshold": 0.9}})
    files = [(1, "/media/downloads/Show.S01E01.mkv", 0.7, True)]
    eligible, held = compute_auto_rename_eligibility(cfg, files)
    assert eligible == []
    assert held == 1


def test_scan_only_folder_never_auto_renames() -> None:
    cfg = _cfg({"/media/tv": {"mode": "scan", "threshold": 0.5}})
    files = [(1, "/media/tv/Show.S01E01.mkv", 0.99, True)]
    eligible, held = compute_auto_rename_eligibility(cfg, files)
    assert eligible == []
    assert held == 0  # scan-only files are not "held" — they were never candidates


def test_no_match_in_auto_folder_is_held() -> None:
    cfg = _cfg({"/media/downloads": {"mode": "auto_rename", "threshold": 0.9}})
    files = [(1, "/media/downloads/mystery.mkv", None, False)]
    eligible, held = compute_auto_rename_eligibility(cfg, files)
    assert eligible == []
    assert held == 1


def test_path_prefix_match_inherits_folder_mode() -> None:
    # A file deep inside a watched root inherits that root's auto_rename mode.
    cfg = _cfg({"/media/downloads": {"mode": "auto_rename", "threshold": 0.85}})
    files = [(1, "/media/downloads/anime/Show/Show - 01.mkv", 0.9, True)]
    eligible, _ = compute_auto_rename_eligibility(cfg, files)
    assert eligible == [1]


def test_mixed_batch_partitions_correctly() -> None:
    cfg = _cfg({
        "/media/auto": {"mode": "auto_rename", "threshold": 0.85},
        "/media/manual": {"mode": "scan", "threshold": 0.5},
    })
    files = [
        (1, "/media/auto/A.S01E01.mkv", 0.95, True),   # eligible
        (2, "/media/auto/B.S01E01.mkv", 0.60, True),   # held (low conf)
        (3, "/media/auto/C.mkv", None, False),         # held (no match)
        (4, "/media/manual/D.S01E01.mkv", 0.99, True), # scan-only → ignored
    ]
    eligible, held = compute_auto_rename_eligibility(cfg, files)
    assert eligible == [1]
    assert held == 2


def test_threshold_boundary_is_inclusive() -> None:
    cfg = _cfg({"/media/auto": {"mode": "auto_rename", "threshold": 0.85}})
    files = [(1, "/media/auto/exact.mkv", 0.85, True)]
    eligible, _ = compute_auto_rename_eligibility(cfg, files)
    assert eligible == [1]  # meets_threshold uses >=


def test_empty_path_skipped() -> None:
    cfg = _cfg({"/media/auto": {"mode": "auto_rename", "threshold": 0.85}})
    files = [(1, "", 0.99, True)]
    eligible, held = compute_auto_rename_eligibility(cfg, files)
    assert eligible == []
    assert held == 0
