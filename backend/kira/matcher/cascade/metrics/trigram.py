"""TrigramMetric — tier-2 character-trigram similarity.

This is the legacy "title similarity" signal, now without the M7 short-
title penalty (deleted because the cluster signal makes it obsolete).

Scores against display title + every alias, takes the max.

Guard (Autopsy 12): requires the needle to be ≥4 characters AFTER
normalization, mirroring `SubstringMetric`'s `_MIN_PARSED_LEN`. Character-
trigram Jaccard similarity collapses arithmetically on ultra-short
strings — the denominator (`|set_a ∪ set_b|`) is too small to be
informative, so "Re:" vs "Re:Zero" or "A" vs "The Apple" produces
wildly inflated similarities. Without this floor, every short-titled
clip / OVA / fan-edit could win a tier-2 score against unrelated long
titles. Mirroring Substring's guard keeps the two metrics in lockstep.
"""
from __future__ import annotations

from kira.matcher.cascade.types import (
    CascadeContext,
    MetricResult,
    MetricTier,
    clamp_to_tier,
)
from kira.matcher.similarity import normalize, trigram_similarity


# Same threshold + rationale as SubstringMetric._MIN_PARSED_LEN — kept
# named separately so each metric's guard is independently visible in
# its own file (a future tweak to one shouldn't silently drag the other).
_MIN_NEEDLE_LEN = 4


class TrigramMetric:
    name = "trigram"
    tier = MetricTier.SIMILARITY

    async def score(self, candidate, ctx: CascadeContext) -> MetricResult:
        needle = ctx.cluster_signal or ctx.parsed.title or ""
        if not needle:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="no parsed title",
            )

        # ── Short-title trigram guard (Autopsy 12) ────────────────────
        # Normalize first so leading articles / punctuation don't make a
        # genuinely-short title look longer than it really is. `"The A"`
        # → "a" after normalization → fails the length check and we
        # abstain rather than emit a noisy similarity score.
        needle_n = normalize(needle)
        if len(needle_n) < _MIN_NEEDLE_LEN:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0,
                reason=f"needle too short ({len(needle_n)} chars) for reliable trigrams",
            )

        haystacks: list[str] = []
        if candidate.title:
            haystacks.append(candidate.title)
        for a in (candidate.aliases or []):
            if a:
                haystacks.append(a)
        if not haystacks:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="no candidate titles",
            )

        best = 0.0
        best_text = ""
        for h in haystacks:
            sim = trigram_similarity(needle, h)
            if sim > best:
                best = sim
                best_text = h

        # Sub-floor trigrams contribute nothing. 0.45 (was 0.30): the tier-2
        # band floor is 0.50, so ANY firing trigram lands ≥ 0.50 — and with
        # rank-0 corroboration a 0.30-similar junk candidate cleared the 0.55
        # movie/TV auto-commit floor whenever the real title was absent from
        # the provider. 0.45 keeps genuinely-similar titles (typos, word-order
        # shuffles score well above it) while junk abstains entirely.
        if best < 0.45:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0,
                reason=f"best trigram {best:.2f} < 0.45",
            )

        return MetricResult(
            metric=self.name, tier=self.tier,
            raw=best, score=clamp_to_tier(best, self.tier),
            reason=f"trigram {best:.2f} vs {best_text[:50]!r}",
        )
