"""ClusterSignalMetric — scores the cluster-wide common-sequence title.

For an N-file cluster the aggregator computes the longest contiguous
shared word sequence across all filenames and stores it on the cascade
context as `ctx.cluster_signal`. This metric scores THAT against each
candidate.

This is the One Pace fix at metric level: a cluster of `One Pace - S01EXX`
files has a cluster signal of "one pace". Trigram against "one piece" =
~0.67 → maps into mid tier-2 band (0.65ish). Token-set Jaccard 1/2 = 0.5.
The combined score lands well below 0.85 so it can't beat any genuine
tier-1 identity hit (and there isn't one for One Pace because it's not
in the providers).

Critically: this REPLACES the M7 short-title penalty inside
trigram_similarity. The penalty is deleted in the same commit. M7 was a
band-aid for missing cluster-level reasoning; ClusterSignalMetric makes
the cluster-level signal explicit instead.
"""
from __future__ import annotations

from kira.matcher.cascade.types import (
    CascadeContext,
    MetricResult,
    MetricTier,
    clamp_to_tier,
)
from kira.matcher.similarity import normalize, trigram_similarity


class ClusterSignalMetric:
    name = "cluster_signal"
    tier = MetricTier.SIMILARITY

    async def score(self, candidate, ctx: CascadeContext) -> MetricResult:
        signal = ctx.cluster_signal
        if not signal:
            # Single-file clusters fall back to the parsed title; that's
            # handled by TrigramMetric. Don't double-count.
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="no cluster signal (N<2)",
            )

        haystacks: list[str] = []
        if candidate.title:
            haystacks.append(candidate.title)
        for a in (candidate.aliases or []):
            if a:
                haystacks.append(a)

        # Two-part scoring: trigram + token-Jaccard. The token Jaccard
        # is the explicit "do the WORDS match?" check that catches
        # One Pace vs One Piece (1/2 tokens shared = 0.5).
        signal_tokens = set(normalize(signal).split())
        if not signal_tokens:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="empty signal tokens",
            )

        best_trigram = 0.0
        best_jaccard = 0.0
        best_text = ""
        for h in haystacks:
            h_tokens = set(normalize(h).split())
            if not h_tokens:
                continue
            tri = trigram_similarity(signal, h)
            jac = len(signal_tokens & h_tokens) / max(1, len(signal_tokens | h_tokens))
            # Combined: trigram weighted higher (it accounts for char
            # similarity) but token-Jaccard acts as a sanity cap.
            combined = 0.6 * tri + 0.4 * jac
            if combined > (0.6 * best_trigram + 0.4 * best_jaccard):
                best_trigram = tri
                best_jaccard = jac
                best_text = h

        if best_trigram < 0.3:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0,
                reason=f"weak trigram {best_trigram:.2f}",
            )

        raw = 0.6 * best_trigram + 0.4 * best_jaccard
        return MetricResult(
            metric=self.name, tier=self.tier,
            raw=raw, score=clamp_to_tier(raw, self.tier),
            reason=(
                f"cluster signal {signal!r} vs {best_text[:40]!r} "
                f"trigram={best_trigram:.2f} tokens={best_jaccard:.2f}"
            ),
        )
