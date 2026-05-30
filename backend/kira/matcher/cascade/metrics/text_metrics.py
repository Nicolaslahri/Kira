"""Phase 7 — extra tier-2 similarity metrics: Levenshtein, LCS, Numeric.

These complement TrigramMetric. The runner takes the MAX across tier-2
metrics (not the sum), so adding more similarity measures can only RAISE a
candidate's tier-2 score when one of them detects a similarity the others
missed — it never double-counts. Each has a floor so weak similarities
contribute nothing (no noise). All compare the cluster signal (or the
parsed title) against the candidate's display title + aliases.
"""

from __future__ import annotations

from kira.matcher.cascade.types import (
    CascadeContext,
    MetricResult,
    MetricTier,
    clamp_to_tier,
)
from kira.matcher.similarity import normalize
from kira.matcher.text_distance import (
    levenshtein_ratio,
    lcs_ratio,
    numeric_similarity,
)

# Same short-needle guard as TrigramMetric — sub-4-char normalized needles
# produce unreliable scores across all string metrics.
_MIN_NEEDLE_LEN = 4
# Tier-2 floor: a similarity below this adds nothing. Higher than trigram's
# 0.3 because edit-distance / LCS ratios run higher for unrelated strings.
_FLOOR = 0.5


def _needle(ctx: CascadeContext) -> str | None:
    raw = ctx.cluster_signal or ctx.parsed.title or ""
    n = normalize(raw)
    return n if len(n) >= _MIN_NEEDLE_LEN else None


def _haystacks(candidate) -> list[str]:
    out: list[str] = []
    if candidate.title:
        out.append(candidate.title)
    for a in (candidate.aliases or []):
        if a:
            out.append(a)
    return out


class LevenshteinMetric:
    name = "levenshtein"
    tier = MetricTier.SIMILARITY

    async def score(self, candidate, ctx: CascadeContext) -> MetricResult:
        needle = _needle(ctx)
        if not needle:
            return MetricResult(self.name, self.tier, 0.0, 0.0, "needle too short")
        best = 0.0
        best_text = ""
        for h in _haystacks(candidate):
            r = levenshtein_ratio(needle, normalize(h))
            if r > best:
                best, best_text = r, h
        if best < _FLOOR:
            return MetricResult(self.name, self.tier, 0.0, 0.0, f"edit-ratio {best:.2f} < {_FLOOR}")
        return MetricResult(
            self.name, self.tier, best, clamp_to_tier(best, self.tier),
            f"edit-ratio {best:.2f} vs {best_text[:40]!r}",
        )


class LCSMetric:
    name = "lcs"
    tier = MetricTier.SIMILARITY

    async def score(self, candidate, ctx: CascadeContext) -> MetricResult:
        needle = _needle(ctx)
        if not needle:
            return MetricResult(self.name, self.tier, 0.0, 0.0, "needle too short")
        best = 0.0
        best_text = ""
        for h in _haystacks(candidate):
            r = lcs_ratio(needle, normalize(h))
            if r > best:
                best, best_text = r, h
        if best < _FLOOR:
            return MetricResult(self.name, self.tier, 0.0, 0.0, f"lcs-ratio {best:.2f} < {_FLOOR}")
        return MetricResult(
            self.name, self.tier, best, clamp_to_tier(best, self.tier),
            f"lcs-ratio {best:.2f} vs {best_text[:40]!r}",
        )


class NumericDistanceMetric:
    name = "numeric"
    tier = MetricTier.SIMILARITY

    async def score(self, candidate, ctx: CascadeContext) -> MetricResult:
        # NumericDistance deliberately SKIPS the _MIN_NEEDLE_LEN guard — a
        # 2-char numeric title ("86") is exactly the case it exists for, and
        # it scores on number tokens, not char trigrams.
        needle = normalize(ctx.cluster_signal or ctx.parsed.title or "")
        if not needle:
            return MetricResult(self.name, self.tier, 0.0, 0.0, "no needle")
        best = 0.0
        best_text = ""
        fired = False
        for h in _haystacks(candidate):
            sim = numeric_similarity(needle, normalize(h))
            if sim is None:
                continue  # neither side has numbers — no signal
            fired = True
            if sim > best:
                best, best_text = sim, h
        if not fired:
            return MetricResult(self.name, self.tier, 0.0, 0.0, "no numbers in either title")
        # Only a perfect-or-near number agreement is worth contributing —
        # a partial overlap (one of two numbers) is too weak to band into
        # tier-2 on its own. Abstain below the floor.
        if best < _FLOOR:
            return MetricResult(self.name, self.tier, 0.0, 0.0, f"numeric overlap {best:.2f} < {_FLOOR}")
        return MetricResult(
            self.name, self.tier, best, clamp_to_tier(best, self.tier),
            f"numeric overlap {best:.2f} vs {best_text[:40]!r}",
        )
