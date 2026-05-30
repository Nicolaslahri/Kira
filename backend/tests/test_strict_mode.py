"""Phase 20 — strict-mode gate tests."""

from __future__ import annotations

from kira.matcher.strict_mode import (
    DEFAULT_STRICT_THRESHOLD,
    MatchMode,
    meets_threshold,
    parse_mode,
)


def test_parse_mode_defaults_strict() -> None:
    assert parse_mode(None) is MatchMode.STRICT
    assert parse_mode("strict") is MatchMode.STRICT
    assert parse_mode("garbage") is MatchMode.STRICT
    assert parse_mode("opportunistic") is MatchMode.OPPORTUNISTIC
    assert parse_mode("OPPORTUNISTIC") is MatchMode.OPPORTUNISTIC


def test_strict_requires_threshold() -> None:
    assert meets_threshold(0.9, MatchMode.STRICT)
    assert meets_threshold(DEFAULT_STRICT_THRESHOLD, MatchMode.STRICT)
    assert not meets_threshold(0.84, MatchMode.STRICT)
    assert not meets_threshold(0.5, MatchMode.STRICT)


def test_opportunistic_acts_on_any_positive() -> None:
    assert meets_threshold(0.3, MatchMode.OPPORTUNISTIC)
    assert meets_threshold(0.01, MatchMode.OPPORTUNISTIC)
    assert not meets_threshold(0.0, MatchMode.OPPORTUNISTIC)


def test_no_match_never_acts() -> None:
    assert not meets_threshold(None, MatchMode.STRICT)
    assert not meets_threshold(None, MatchMode.OPPORTUNISTIC)
    assert not meets_threshold(0.0, MatchMode.STRICT)


def test_custom_threshold() -> None:
    assert meets_threshold(0.7, MatchMode.STRICT, threshold=0.65)
    assert not meets_threshold(0.6, MatchMode.STRICT, threshold=0.65)
