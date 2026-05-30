"""Phase 12 — cluster signal drives the primary search query."""

from __future__ import annotations

from kira.matcher.engine import _query_ladder
from kira.parser import ParsedFile


def test_cluster_signal_is_first_query() -> None:
    pf = ParsedFile(
        original_filename="x.mkv", media_type="anime",
        title="Shingeki no Kyojin The Final Season Part 3",
    )
    pf._cluster_signal = "attack on titan"  # type: ignore[attr-defined]
    ladder = _query_ladder(pf)
    assert ladder[0][0] == "attack on titan"
    # The per-file title still appears later as a fallback rung.
    assert any("Shingeki" in q for q, _ in ladder)


def test_no_cluster_signal_uses_title() -> None:
    pf = ParsedFile(original_filename="x.mkv", media_type="tv", title="Breaking Bad")
    ladder = _query_ladder(pf)
    assert ladder[0][0] == "Breaking Bad"


def test_short_cluster_signal_skipped() -> None:
    """A degenerate 2-char signal is worse than the title — skip it."""
    pf = ParsedFile(original_filename="x.mkv", media_type="tv", title="Breaking Bad")
    pf._cluster_signal = "bb"  # type: ignore[attr-defined]
    ladder = _query_ladder(pf)
    assert ladder[0][0] == "Breaking Bad"


def test_acronym_title_adds_expansion_rung() -> None:
    """M2: a known acronym title gets its full expansion as a query rung so
    TMDB/TVDB (which can't expand initialisms) can resolve it."""
    pf = ParsedFile(original_filename="x.mkv", media_type="movie", title="LotR")
    ladder = _query_ladder(pf)
    queries = [q for q, _ in ladder]
    assert "lord of the rings" in queries
    # The raw acronym title still appears as a fallback rung.
    assert "LotR" in queries


def test_non_acronym_title_has_no_expansion() -> None:
    pf = ParsedFile(original_filename="x.mkv", media_type="tv", title="Breaking Bad")
    ladder = _query_ladder(pf)
    queries = [q for q, _ in ladder]
    # Nothing got expanded; the title leads.
    assert queries[0] == "Breaking Bad"
