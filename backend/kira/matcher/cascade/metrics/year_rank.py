"""YearMetric + RankMetric — tier-3 weak corroborating signals."""
from __future__ import annotations

import math

from kira.matcher.cascade.types import (
    CascadeContext,
    MetricResult,
    MetricTier,
    clamp_to_tier,
)


class YearMetric:
    name = "year"
    tier = MetricTier.CORROBORATION

    async def score(self, candidate, ctx: CascadeContext) -> MetricResult:
        py = ctx.parsed.year
        cy = candidate.year
        if py is None or cy is None:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="missing year",
            )
        diff = abs(py - cy)
        if diff == 0:
            raw = 1.0
        elif diff == 1:
            raw = 0.6
        elif diff == 2:
            raw = 0.2
        else:
            raw = 0.0

        if raw <= 0.0:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason=f"year diff {diff} too large",
            )
        return MetricResult(
            metric=self.name, tier=self.tier,
            raw=raw, score=clamp_to_tier(raw, self.tier),
            reason=f"year diff {diff} ({py} vs {cy})",
        )


class RankMetric:
    name = "rank"
    tier = MetricTier.CORROBORATION

    async def score(self, candidate, ctx: CascadeContext) -> MetricResult:
        idx = next(
            (i for i, c in enumerate(ctx.candidates) if c is candidate),
            -1,
        )
        if idx < 0:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="not in candidate list",
            )
        # rank=0 → 1.0, rank=1 → 0.63, rank=4 → 0.39
        raw = 1.0 / math.log2(idx + 2)
        return MetricResult(
            metric=self.name, tier=self.tier,
            raw=raw, score=clamp_to_tier(raw, self.tier),
            reason=f"rank {idx}",
        )
