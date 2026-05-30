"""Phase 7 — pure string-distance helpers + cascade metric smoke tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from kira.matcher.cascade.metrics.text_metrics import (
    LCSMetric,
    LevenshteinMetric,
    NumericDistanceMetric,
)
from kira.matcher.cascade.types import CascadeContext, MetricTier
from kira.matcher.text_distance import (
    levenshtein_distance,
    levenshtein_ratio,
    lcs_length,
    lcs_ratio,
    numeric_similarity,
    numeric_tokens,
)
from kira.parser import ParsedFile


def test_levenshtein_distance() -> None:
    assert levenshtein_distance("kitten", "sitting") == 3
    assert levenshtein_distance("abc", "abc") == 0
    assert levenshtein_distance("", "abc") == 3


def test_levenshtein_ratio() -> None:
    assert levenshtein_ratio("abc", "abc") == 1.0
    assert levenshtein_ratio("", "") == 1.0
    # one substitution in 5 chars → 0.8
    assert abs(levenshtein_ratio("frier", "frien") - 0.8) < 1e-9


def test_lcs() -> None:
    assert lcs_length("ABCBDAB", "BDCAB") == 4
    assert lcs_ratio("abc", "abc") == 1.0
    assert lcs_ratio("ace", "abcde") == 3 / 5  # 'ace' subsequence of 'abcde'


def test_numeric_tokens_and_similarity() -> None:
    assert numeric_tokens("Mob Psycho 100 II") == {100}
    assert numeric_similarity("86", "86") == 1.0
    assert numeric_similarity("86", "91 days") == 0.0
    # No numbers either side → abstain.
    assert numeric_similarity("naruto", "bleach") is None


# ── Cascade metric smoke tests ─────────────────────────────────────────────


@dataclass
class _Cand:
    title: str
    aliases: list[str] = field(default_factory=list)


def _ctx(title: str) -> CascadeContext:
    pf = ParsedFile(original_filename="x.mkv", media_type="tv", title=title)
    return CascadeContext(parsed=pf, candidates=[])


async def test_levenshtein_metric_fires_on_typo() -> None:
    m = LevenshteinMetric()
    # one-char typo should still score high
    r = await m.score(_Cand("Attack on Titan"), _ctx("Attack on Titen"))
    assert r.raw > 0.8
    assert r.tier == MetricTier.SIMILARITY
    assert 0.5 <= r.score < 0.85  # banded into tier 2


async def test_lcs_metric_word_order() -> None:
    m = LCSMetric()
    r = await m.score(_Cand("Fullmetal Alchemist Brotherhood"),
                      _ctx("Fullmetal Alchemist Brotherhood"))
    assert r.raw == 1.0


async def test_numeric_metric_abstains_without_numbers() -> None:
    m = NumericDistanceMetric()
    r = await m.score(_Cand("Naruto"), _ctx("Bleach"))
    assert r.raw == 0.0  # abstained


async def test_numeric_metric_boosts_matching_number() -> None:
    m = NumericDistanceMetric()
    r = await m.score(_Cand("86"), _ctx("86"))
    assert r.raw == 1.0
    assert r.score >= 0.5
