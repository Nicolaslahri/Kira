"""Phase 4 — pure episode-validation helper tests (no network)."""

from __future__ import annotations

from kira.matcher.episode_validation import (
    COVERAGE_FLOOR,
    COVERAGE_PROMOTE,
    coverage,
    episode_exists,
    should_promote,
)


def _list(*pairs: tuple[int, int]) -> dict[tuple[int, int], str | None]:
    return {p: f"Ep {p[1]}" for p in pairs}


def test_episode_exists_exact_and_season_fallback() -> None:
    by_key = _list((2, 5), (2, 6))
    assert episode_exists(by_key, 2, 5)
    assert not episode_exists(by_key, 2, 99)
    # season-agnostic (1, ep) fallback
    flat = _list((1, 5))
    assert episode_exists(flat, 4, 5)   # (4,5) misses, (1,5) hits
    assert episode_exists(flat, None, 5)
    assert not episode_exists(by_key, 2, None)


def test_coverage_full_and_partial() -> None:
    by_key = _list((1, 1), (1, 2), (1, 3), (1, 4))
    full = [(1, 1), (1, 2), (1, 3)]
    assert coverage(full, by_key) == 1.0
    half = [(1, 1), (1, 99)]
    assert coverage(half, by_key) == 0.5
    # episode-less files are ignored; an all-episode-less cluster → 1.0
    assert coverage([(1, None), (None, None)], by_key) == 1.0


def test_coverage_zero_when_nothing_matches() -> None:
    by_key = _list((1, 1), (1, 2))
    assert coverage([(5, 17), (5, 18), (5, 19)], by_key) == 0.0


def test_should_promote_requires_floor_promote_and_margin() -> None:
    # Incumbent terrible (0.1), alternate strong (0.9) → promote.
    assert should_promote(0.1, 0.9)
    # Incumbent already decent (above floor) → never probe/promote.
    assert not should_promote(0.5, 1.0)
    # Alternate below PROMOTE bar → no.
    assert not should_promote(0.1, COVERAGE_PROMOTE - 0.01)
    # Alternate clears PROMOTE but doesn't beat incumbent by the margin.
    assert not should_promote(COVERAGE_FLOOR - 0.01, COVERAGE_FLOOR - 0.01 + 0.1)


def test_should_promote_boundary() -> None:
    # Exactly at floor is NOT below floor → no promotion.
    assert not should_promote(COVERAGE_FLOOR, 1.0)
