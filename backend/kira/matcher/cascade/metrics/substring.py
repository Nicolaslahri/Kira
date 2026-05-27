"""SubstringMetric — tier-1 identity hit when parsed title is a clean
word-boundary substring of a candidate alias.

Mirrors FileBot's SubstringMetric.java semantics: word boundaries on
both sides of the substring. Catches the trivial case ("Rent-a-Girlfriend"
appears verbatim in alias list) before trigram normalization can drift.

Guard: requires the parsed title to be ≥4 characters AFTER normalization.
Otherwise "The" / "A" / "S" trivially match every long title.
"""
from __future__ import annotations

import re

from kira.matcher.cascade.types import (
    CascadeContext,
    Metric,
    MetricResult,
    MetricTier,
    clamp_to_tier,
)
from kira.matcher.similarity import normalize


_MIN_PARSED_LEN = 4


class SubstringMetric:
    name = "substring"
    tier = MetricTier.IDENTITY

    async def score(self, candidate, ctx: CascadeContext) -> MetricResult:
        # Use cluster signal when available (cluster-level identity beats
        # per-file). Falls back to the file's own parsed title.
        needle = ctx.cluster_signal or ctx.parsed.title or ""
        needle_n = normalize(needle)
        if len(needle_n) < _MIN_PARSED_LEN:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0,
                reason=f"title too short ({len(needle_n)} chars)",
            )

        # Build the haystack: every known title for the candidate.
        haystacks: list[str] = []
        if candidate.title:
            haystacks.append(candidate.title)
        for a in (candidate.aliases or []):
            if a:
                haystacks.append(a)

        # Word-boundary substring match against any haystack.
        needle_pattern = re.compile(
            r"(?:^|\W)" + re.escape(needle_n) + r"(?:\W|$)",
            re.IGNORECASE,
        )
        for h in haystacks:
            h_n = normalize(h)
            if not h_n:
                continue
            if h_n == needle_n:
                return MetricResult(
                    metric=self.name, tier=self.tier,
                    raw=1.0, score=clamp_to_tier(1.0, self.tier),
                    reason=f"exact: {h[:50]!r}",
                )
            if needle_pattern.search(h_n):
                # Word-boundary substring. Slightly lower than exact so
                # ties go to exact matches.
                return MetricResult(
                    metric=self.name, tier=self.tier,
                    raw=0.95, score=clamp_to_tier(0.95, self.tier),
                    reason=f"substring of {h[:50]!r}",
                )

        return MetricResult(
            metric=self.name, tier=self.tier,
            raw=0.0, score=0.0, reason="no substring match",
        )
