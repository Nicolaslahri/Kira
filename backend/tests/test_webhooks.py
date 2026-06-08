"""Pass 6 #8 — Sonarr/Radarr inbound webhook (pure helpers + token gate)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from kira.api import webhooks as wh


# ── extract_event_type ───────────────────────────────────────────────────────

def test_event_type() -> None:
    assert wh.extract_event_type({"eventType": "Download"}) == "Download"
    assert wh.extract_event_type({"eventType": "Test"}) == "Test"
    assert wh.extract_event_type({}) == ""
    assert wh.extract_event_type("nope") == ""


# ── extract_target_path ──────────────────────────────────────────────────────

def test_extract_sonarr_series_path() -> None:
    p = wh.extract_target_path({"series": {"path": "/tv/Breaking Bad"}})
    assert p == "/tv/Breaking Bad"


def test_extract_radarr_folder_path() -> None:
    p = wh.extract_target_path({"movie": {"folderPath": "/movies/Inception (2010)"}})
    assert p == "/movies/Inception (2010)"


def test_extract_none_when_absent() -> None:
    assert wh.extract_target_path({"eventType": "Test"}) is None
    assert wh.extract_target_path({}) is None
    assert wh.extract_target_path("bad") is None


# ── path_under_roots ─────────────────────────────────────────────────────────

def test_path_under_roots_exact_and_nested() -> None:
    roots = ["/media/tv", "/media/movies"]
    assert wh.path_under_roots("/media/tv", roots) is True
    assert wh.path_under_roots("/media/tv/Show", roots) is True
    assert wh.path_under_roots("/media/movies/Inception", roots) is True


def test_path_under_roots_rejects_outside() -> None:
    roots = ["/media/tv"]
    assert wh.path_under_roots("/etc/passwd", roots) is False
    assert wh.path_under_roots("/media/tv-other/x", roots) is False  # prefix-but-not-subdir
    assert wh.path_under_roots("", roots) is False


def test_path_under_roots_case_and_sep_insensitive() -> None:
    roots = ["C:\\Media\\TV"]
    assert wh.path_under_roots("c:/media/tv/Show/ep.mkv", roots) is True


# ── resolve_scan_paths ───────────────────────────────────────────────────────

def test_resolve_uses_targeted_path_when_inside() -> None:
    roots = ["/media/tv", "/media/movies"]
    assert wh.resolve_scan_paths("/media/tv/Show", roots) == ["/media/tv/Show"]


def test_resolve_falls_back_to_roots_when_outside() -> None:
    roots = ["/media/tv", "/media/movies"]
    # An attacker-supplied path outside the library is ignored → scan roots.
    assert wh.resolve_scan_paths("/etc", roots) == ["/media/tv", "/media/movies"]


def test_resolve_falls_back_when_no_path() -> None:
    roots = ["/media/tv"]
    assert wh.resolve_scan_paths(None, roots) == ["/media/tv"]


# ── path traversal hardening (audit S3) ──────────────────────────────────────

def test_path_under_roots_blocks_dotdot_traversal() -> None:
    roots = ["/media/tv"]
    # '..' that climbs OUT of the root must be rejected (string-prefix alone
    # would have let `/media/tv/../../etc/passwd` through as "under /media").
    assert wh.path_under_roots("/media/tv/../../etc/passwd", roots) is False
    assert wh.path_under_roots("/media/tv/../tv-other/x", roots) is False
    # '..' that resolves to a path still INSIDE the root is fine.
    assert wh.path_under_roots("/media/tv/sub/../Show/ep.mkv", roots) is True


def test_resolve_scan_paths_ignores_traversal_escape() -> None:
    roots = ["/media/tv"]
    # An escaping payload is ignored → fall back to scanning the roots only.
    assert wh.resolve_scan_paths("/media/tv/../../etc", roots) == ["/media/tv"]


# ── token gate ───────────────────────────────────────────────────────────────

def test_token_gate_no_token_configured_is_404() -> None:
    with pytest.raises(HTTPException) as ei:
        wh._check_token("anything", None)
    assert ei.value.status_code == 404


def test_token_gate_wrong_token_is_403() -> None:
    with pytest.raises(HTTPException) as ei:
        wh._check_token("wrong", "secret")
    assert ei.value.status_code == 403


def test_token_gate_missing_token_is_403() -> None:
    with pytest.raises(HTTPException) as ei:
        wh._check_token(None, "secret")
    assert ei.value.status_code == 403


def test_token_gate_correct_token_passes() -> None:
    # No exception raised.
    wh._check_token("secret", "secret")
