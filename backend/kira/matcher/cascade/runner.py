"""Cascade runner — evaluates every metric, aggregates into a CascadeTrace.

Tier aggregation rule (user-locked):
    final_score = max(tier_1_max, weighted_avg(tier_2_max, tier_3_max))

Tier-1 always wins when ANY tier-1 metric fires (because tier-1 lands in
[0.85, 1.00] and tier-2 caps below 0.85). Tier-2 and tier-3 contribute a
weighted average that can never overshadow a tier-1 hit but DO produce a
useful confidence when no tier-1 metric fired.

Veto handling: a metric returning raw=-1.0 vetoes the candidate
(final_score forced to 0.0). Used by FribbAidFilterMetric to drop
non-anime TVDB results from anime search results.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from kira.matcher.cascade.types import (
    TIER_BANDS,
    CascadeContext,
    CascadeTrace,
    Metric,
    MetricResult,
    MetricTier,
    clamp_to_tier,
)

_funnel_log = logging.getLogger("kira.matcher.funnel")


# Tied candidates within this delta at the top of the tier-1 band are
# flagged as ambiguous. 0.01 covers floating-point jitter from the
# clamping math + slight raw-score differences between metrics that
# both effectively "fired identically" (e.g. two AIDs that both score
# 1.0 substring + 1.0 folder identity).
_AMBIGUITY_EPSILON = 0.01

# Only ambiguity at the tier-1 ceiling matters for the matcher's
# "should I auto-commit this?" decision. A tier-2 tie just means two
# similarly-named shows scored similarly — the engine's existing
# rank tiebreak handles that fine. A tier-1 tie means two structurally
# identified candidates collide, which is the dangerous case (Bleach
# umbrella AID vs the correct cour AID before the Fribb veto landed).
_AMBIGUITY_FLOOR = TIER_BANDS[MetricTier.IDENTITY][0]


# ── M3 Observer Mode (Pattern A) ────────────────────────────────────────────
# Scoring changes touch every file, so instead of flipping the funnel blind we
# compute a CANDIDATE rebalanced score side-by-side and log where it would
# change the top pick — without ever using it. Enable with KIRA_FUNNEL_OBSERVER.
# Read per-call (cheap dict lookup) so tests + ops can toggle without reimport.
_OBSERVER_TRUTHY = {"1", "true", "yes", "on"}


def _observer_on() -> bool:
    return os.environ.get("KIRA_FUNNEL_OBSERVER", "").strip().lower() in _OBSERVER_TRUTHY


# String-distance metrics overlap (all measure edit distance over the same two
# strings), so the current MAX-per-tier is correct FOR THEM. The hypothesis the
# shadow tests: metrics from DIFFERENT families that independently agree should
# nudge confidence up — the reference renamer's funnel rewards corroboration, ours doesn't.
_STRING_FAMILY = {"trigram", "levenshtein", "lcs"}
_AGREEMENT_BONUS = 0.15  # fraction of the 2nd-best independent signal added


def _shadow_tier2_raw(results: list[MetricResult]) -> float:
    """Rebalanced tier-2 raw: the string-distance metrics collapse to one family
    vote; every other tier-2 metric is an independent vote. A second independent
    vote adds a bounded bonus. Output is still clamped below the tier-1 floor by
    the caller, so the tier hierarchy is preserved."""
    t2 = [r for r in results if r.tier == MetricTier.SIMILARITY and r.raw > 0]
    if not t2:
        return 0.0
    string_max = max((r.raw for r in t2 if r.metric in _STRING_FAMILY), default=0.0)
    votes = [r.raw for r in t2 if r.metric not in _STRING_FAMILY]
    if string_max > 0:
        votes.append(string_max)
    votes.sort(reverse=True)
    top = votes[0]
    second = votes[1] if len(votes) > 1 else 0.0
    return min(1.0, top + _AGREEMENT_BONUS * second)


def _shadow_final(results: list[MetricResult], tier_1_max: float, tier_3_max: float) -> float:
    """The score the rebalanced funnel WOULD produce. Tier-1 still wins outright
    (identity is identity); only the tier-2/3 blend changes via the agreement
    bonus. Never used to drive behavior — logged for comparison only."""
    if tier_1_max > 0:
        return min(1.0, tier_1_max)
    s_t2 = clamp_to_tier(_shadow_tier2_raw(results), MetricTier.SIMILARITY)
    if s_t2 > 0 or tier_3_max > 0:
        weighted = (0.7 * s_t2 + 0.3 * tier_3_max) if tier_3_max > 0 else s_t2
    else:
        weighted = 0.0
    return min(1.0, weighted)


@dataclass
class Cascade:
    """Ordered list of metrics. Evaluates every metric for every candidate."""

    metrics: list[Metric] = field(default_factory=list)

    async def score_one(
        self,
        candidate: Any,
        ctx: CascadeContext,
    ) -> CascadeTrace:
        """Evaluate every metric against one candidate. Always runs all
        metrics — observability beats microsecond savings.

        Returns a CascadeTrace with the final aggregated score AND each
        metric's individual contribution (for the popup hover / heal pass).
        """
        results: list[MetricResult] = []
        for m in self.metrics:
            try:
                r = await m.score(candidate, ctx)
            except Exception as e:
                # A metric raising must NEVER kill the whole cascade —
                # log and skip with a zero contribution. Provider HTTP
                # failures + transient errors are the realistic cause.
                _funnel_log.warning("metric %s raised: %r", m.name, e)
                r = MetricResult(
                    metric=m.name,
                    tier=m.tier,
                    raw=0.0,
                    score=0.0,
                    reason=f"error: {type(e).__name__}",
                )
            results.append(r)

        # Veto: any metric returning raw=-1.0 forces final to 0.0.
        # Filter metrics use this to drop non-anime TVDB results, live-
        # action remakes flagged by anime-TVDB enrichment, etc.
        vetoed = any(r.raw <= -0.99 for r in results)
        if vetoed:
            vetoer = next((r.metric for r in results if r.raw <= -0.99), "veto")
            return CascadeTrace(
                final_score=0.0,
                dominant_metric=vetoer,
                dominant_tier=MetricTier.IDENTITY,
                metrics=results,
            )

        # Aggregate by tier.
        tier_1_results = [r for r in results if r.tier == MetricTier.IDENTITY and r.score > 0]
        tier_2_results = [r for r in results if r.tier == MetricTier.SIMILARITY and r.score > 0]
        tier_3_results = [r for r in results if r.tier == MetricTier.CORROBORATION and r.score > 0]

        # Tier 1: the strongest identity signal wins outright.
        tier_1_max = max((r.score for r in tier_1_results), default=0.0)
        tier_1_dom = max(tier_1_results, key=lambda r: r.score, default=None)

        # Tier 2/3: weighted average (tier-2 weighs 0.7, tier-3 weighs 0.3).
        # Each tier contributes its MAX score, not sum, so multiple
        # similarity metrics don't double-count overlapping signals.
        tier_2_max = max((r.score for r in tier_2_results), default=0.0)
        tier_3_max = max((r.score for r in tier_3_results), default=0.0)
        if tier_2_max > 0 or tier_3_max > 0:
            # Weights chosen so a perfect tier-2 + perfect tier-3 land
            # exactly at the top of tier-2's band (≈0.85), preserving
            # the "tier-2 can't beat tier-1" guarantee.
            weighted = (0.7 * tier_2_max + 0.3 * tier_3_max) if tier_3_max > 0 else tier_2_max
        else:
            weighted = 0.0

        # Final: tier-1 always wins when it fired.
        if tier_1_max > 0:
            final = tier_1_max
            dom = tier_1_dom
        else:
            final = weighted
            # Pick the dominant metric by raw score across tier-2/3.
            non_t1 = [r for r in results if r.tier != MetricTier.IDENTITY and r.score > 0]
            dom = max(non_t1, key=lambda r: r.score, default=None)

        if dom is None:
            return CascadeTrace(
                final_score=0.0,
                dominant_metric="none",
                dominant_tier=MetricTier.CORROBORATION,
                metrics=results,
            )

        shadow = _shadow_final(results, tier_1_max, tier_3_max) if _observer_on() else None
        return CascadeTrace(
            final_score=min(1.0, final),
            dominant_metric=dom.metric,
            dominant_tier=dom.tier,
            metrics=results,
            shadow_score=shadow,
        )

    async def score_all(
        self,
        candidates: list[Any],
        ctx: CascadeContext,
    ) -> list[CascadeTrace]:
        """Score every candidate in parallel + flag tier-1 ties as ambiguous.

        Per-candidate cascade evaluation is independent, so we run them
        concurrently. Metrics that hit the same HTTP endpoint use the
        ctx.enrich_cache so the duplicate work coalesces.

        ── Tier-1 ambiguity detection ──────────────────────────────────
        After gathering all traces, find the top tier-1 score. If TWO OR
        MORE traces are within `_AMBIGUITY_EPSILON` of that top AND all
        sit at or above the tier-1 floor (≥ 0.85), mark every tied trace
        with `is_ambiguous=True`. The matcher engine reads this flag and
        can choose to flip the MediaFile's status to `needs_resolution`
        instead of silently committing one of the tied AIDs via the
        provider's non-deterministic candidate order.

        Previously the tie-breaker was pure candidate position from the
        provider's search response — which for AniDB can fluctuate
        based on title-dump iteration order, and for TVDB depends on
        their internal relevance score that we can't see. The result
        was a coin-flip pick that the user couldn't predict or explain.
        """
        ctx.candidates = candidates
        traces = await asyncio.gather(*(self.score_one(c, ctx) for c in candidates))

        # Find the top tier-1 score among non-vetoed traces.
        tier_1_scores = [
            t.final_score for t in traces
            if t.dominant_tier == MetricTier.IDENTITY
            and t.final_score >= _AMBIGUITY_FLOOR
        ]
        if len(tier_1_scores) >= 2:
            top = max(tier_1_scores)
            # Flag every trace whose tier-1 score is within epsilon of
            # the top. Two-or-more flagged → ambiguous.
            tied = [
                t for t in traces
                if t.dominant_tier == MetricTier.IDENTITY
                and t.final_score >= _AMBIGUITY_FLOOR
                and abs(t.final_score - top) <= _AMBIGUITY_EPSILON
            ]
            if len(tied) >= 2:
                for t in tied:
                    t.is_ambiguous = True

        # ── M3 Observer Mode: log when the rebalanced funnel WOULD have chosen
        # a different top candidate. Behavior is unchanged — `final_score`
        # still drives the ranking; we only record the disagreement so a future
        # flip can be justified by real data rather than a guess.
        if _observer_on() and len(traces) >= 2:
            try:
                cur_i = max(range(len(traces)), key=lambda i: traces[i].final_score)
                sh_i = max(
                    range(len(traces)),
                    key=lambda i: (
                        traces[i].shadow_score if traces[i].shadow_score is not None else -1.0
                    ),
                )
                if cur_i != sh_i and (traces[sh_i].shadow_score or 0.0) > 0.0:
                    cc, sc = candidates[cur_i], candidates[sh_i]
                    _funnel_log.info(
                        "funnel_diverge media=%s current=%s:%r(%.3f) "
                        "shadow=%s:%r(now=%.3f shadow=%.3f)",
                        getattr(ctx.parsed, "media_type", "?"),
                        getattr(cc, "provider_id", "?"), (getattr(cc, "title", "") or "")[:40],
                        traces[cur_i].final_score,
                        getattr(sc, "provider_id", "?"), (getattr(sc, "title", "") or "")[:40],
                        traces[sh_i].final_score, traces[sh_i].shadow_score or 0.0,
                    )
            except Exception:
                pass

        return traces


def build_default_cascade(provider_key: str, media_type: str, *, include_runtime: bool = False) -> Cascade:
    """Construct the cascade for one (provider, media_type) combo.

    Imported lazily so circular imports don't bite (metrics import from
    similarity, similarity is imported by parser, parser by matcher…).
    """
    # Local imports — keep the module-level fast-path clean for the heal
    # path which only needs the runner.
    from kira.matcher.cascade.metrics.substring import SubstringMetric
    from kira.matcher.cascade.metrics.folder_identity import FolderIdentityMetric
    from kira.matcher.cascade.metrics.fribb_authority import FribbAuthorityMetric
    from kira.matcher.cascade.metrics.fribb_aid_filter import FribbAidFilterMetric
    from kira.matcher.cascade.metrics.episode_count_sanity import EpisodeCountSanityMetric
    from kira.matcher.cascade.metrics.cluster_signal import ClusterSignalMetric
    from kira.matcher.cascade.metrics.anime_tvdb_jp import AnimeTVDBJPMetric
    from kira.matcher.cascade.metrics.anime_season_ordinal import AnimeSeasonOrdinalMetric
    from kira.matcher.cascade.metrics.trigram import TrigramMetric
    from kira.matcher.cascade.metrics.text_metrics import (
        LevenshteinMetric, LCSMetric, NumericDistanceMetric,
    )
    from kira.matcher.cascade.metrics.acronym import AcronymMetric
    from kira.matcher.cascade.metrics.episode_title import EpisodeTitleMetric
    from kira.matcher.cascade.metrics.corroboration import RuntimeCorroborationMetric
    from kira.matcher.cascade.metrics.year_rank import YearMetric, RankMetric

    metrics: list[Metric] = []

    # Filters first — vetoes drop candidates entirely before any scoring.
    if media_type == "anime":
        metrics.append(FribbAidFilterMetric())
        # Categorical-mismatch filter: 1-episode movie/OVA can't claim a
        # 40-file TV cluster. Catches the "One Piece → Adventure of Spiral
        # Island" + "Bleach → Movie 4" pattern. Reads disk-cached episode
        # counts, no HTTP.
        metrics.append(EpisodeCountSanityMetric())

    # Tier 1 — structural identity. Any one of these clinches the match.
    metrics.append(SubstringMetric())
    metrics.append(FolderIdentityMetric())
    # FribbAuthority self-gates on Fribb-mapping presence — registered
    # for every anime cascade regardless of provider. The metric returns
    # `raw=0.0` (abstain) when the candidate's numeric provider_id has
    # no AID entry in Fribb, so it stays a no-op for TVDB/TMDB anime
    # candidates while being available to any future AID-keyed provider.
    if media_type == "anime":
        metrics.append(FribbAuthorityMetric())

    # Tier 2 — strong similarity. The runner takes the MAX across these, so
    # the extra string metrics (Phase 7) only help when they detect a
    # similarity trigram missed (typos, word-order, numeric titles); they
    # never double-count or inflate.
    metrics.append(ClusterSignalMetric())
    metrics.append(TrigramMetric())
    metrics.append(LevenshteinMetric())
    metrics.append(LCSMetric())
    metrics.append(NumericDistanceMetric())
    metrics.append(AcronymMetric())
    if media_type in ("tv", "anime"):
        metrics.append(EpisodeTitleMetric())
    if media_type == "anime" and provider_key == "tvdb":
        metrics.append(AnimeTVDBJPMetric())
    if media_type == "anime":
        metrics.append(AnimeSeasonOrdinalMetric())

    # Tier 3 — weak corroboration.
    metrics.append(YearMetric())
    metrics.append(RankMetric())
    # M4: runtime corroboration — Labs opt-in (Settings → Labs). Only registered
    # when enabled; it needs file duration (MediaInfo) to do anything anyway, so
    # off by default it's pure dead weight. Self-gates to free/cached data even
    # when on (never fetches).
    if include_runtime:
        metrics.append(RuntimeCorroborationMetric())

    return Cascade(metrics=metrics)
