"""Cascade type definitions — Metric protocol, MetricResult, CascadeTrace.

Tier semantics (locked by user decision after the FileBot audit):

  Tier 1 (structural identity) → output band [0.85, 1.00]
      "This IS the show." Substring-of-alias, Fribb authority,
      folder-name identity, etc.

  Tier 2 (strong similarity) → output band [0.50, 0.85)
      "Probably right." Cluster common-sequence trigram, anime-TVDB JP
      enrichment, season-ordinal match.

  Tier 3 (weak corroboration) → output band [0.20, 0.50)
      "Could be right." Year match, rank position, popularity.

The runner clamps each metric's raw score into its tier's band so a
ranking glitch in a tier-3 metric can never overshadow a clean tier-1
signal. Final score = max(tier_1_max, weighted_avg(tier_2, tier_3)).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Awaitable, Callable, Protocol

from kira.parser import ParsedFile


class MetricTier(IntEnum):
    """Tier band. Lower numbers = stronger evidence."""
    IDENTITY = 1     # Structural ID: substring, Fribb authority, folder identity
    SIMILARITY = 2   # Strong similarity: trigram, cluster signal, anime JP
    CORROBORATION = 3  # Weak: year, rank, popularity


# Band edges for tier clamping. A tier-N metric returning raw score r is
# clamped into TIER_BANDS[N] = (lo, hi) so a tier-1 metric never falls
# below 0.85 (when it fires) and a tier-2 metric never reaches 0.85.
TIER_BANDS: dict[MetricTier, tuple[float, float]] = {
    MetricTier.IDENTITY:       (0.85, 1.00),
    MetricTier.SIMILARITY:     (0.50, 0.849999),  # epsilon under 0.85 so a perfect
                                                  # tier-2 stays below tier-1 floor
    MetricTier.CORROBORATION:  (0.20, 0.499999),  # epsilon under 0.50
}


@dataclass
class MetricResult:
    """One metric's contribution to scoring a single candidate.

    `raw` is the metric's natural output in [0.0, 1.0] (or [-1.0, 1.0]
    for filter metrics that veto candidates). `score` is `raw` clamped
    into the tier band — what the runner aggregates.

    A `raw` of 0.0 means "this metric didn't fire" (no contribution).
    A `raw` of -1.0 means "veto — drop this candidate entirely" (filter
    metrics only).
    """
    metric: str            # human-readable name, e.g. "substring", "trigram"
    tier: MetricTier
    raw: float             # the metric's own output [0.0, 1.0] or -1.0 for veto
    score: float           # raw clamped into tier band
    reason: str = ""       # short diagnostic, e.g. "matched alias 'Naruto'"

    @property
    def tier_confidence(self) -> float:
        """Within-tier confidence in [0.0, 1.0] for UI display.

        Inverse of `clamp_to_tier`: maps the tier-banded `score` back to
        a friendly 0-100% scale relative to this metric's tier band.
        A perfect trigram match (`raw=1.0`, `score=0.849999` in tier 2)
        returns ~1.0 — the UI can render "100% similarity confidence"
        even though `score` itself stays strictly below tier-1's 0.85
        floor (preserving the cascade's tier hierarchy invariant).

        Mathematically: `tier_confidence = (score - tier_lo) / (tier_hi - tier_lo)`.
        Computed from `score` (not `raw`) so the value still makes sense
        after deserialization round-trips where `raw` may be absent.
        Returns 0.0 when the metric didn't fire (score ≤ 0).
        """
        if self.score <= 0.0:
            return 0.0
        lo, hi = TIER_BANDS[self.tier]
        if hi <= lo:
            return 0.0
        return max(0.0, min(1.0, (self.score - lo) / (hi - lo)))


@dataclass
class CascadeTrace:
    """The full audit trail of one candidate's scoring.

    Persisted to Match.metadata_blob['cascade_trace'] so the frontend
    can render "why is this 65%?" on hover and the heal pass can rescore
    in place using cached signals.
    """
    final_score: float
    dominant_metric: str           # which metric drove the final score
    dominant_tier: MetricTier
    metrics: list[MetricResult]    # every metric's contribution, in order
    # Ambiguity flag — set by `Cascade.score_all` when this candidate
    # ties another candidate at the top of the tier-1 band (both within
    # `_AMBIGUITY_EPSILON` of each other AND ≥ tier-1 floor). The
    # cluster-isolation invariant + tie-break-on-rank previously picked
    # the first one by provider order, which is non-deterministic. With
    # this flag, the matcher engine can mark the file's status as
    # ambiguous and surface a "needs manual resolution" affordance to
    # the user instead of silently committing a coin-flip pick.
    is_ambiguous: bool = False

    @property
    def tier_confidence(self) -> float:
        """The dominant tier's within-tier confidence in [0.0, 1.0].

        Mirrors `MetricResult.tier_confidence` but computed from the
        cascade's aggregated `final_score` against the dominant tier's
        band. The UI uses this to show "Strong match · 100%" when a
        tier-2 metric scored perfectly within its band, instead of the
        less-intuitive raw `0.85` from `final_score`.
        """
        if self.final_score <= 0.0:
            return 0.0
        lo, hi = TIER_BANDS[self.dominant_tier]
        if hi <= lo:
            return 0.0
        return max(0.0, min(1.0, (self.final_score - lo) / (hi - lo)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_score": self.final_score,
            "dominant_metric": self.dominant_metric,
            "dominant_tier": int(self.dominant_tier),
            "tier_confidence": round(self.tier_confidence, 4),
            "is_ambiguous": self.is_ambiguous,
            "metrics": [
                {
                    "metric": m.metric,
                    "tier": int(m.tier),
                    "raw": round(m.raw, 4),
                    "score": round(m.score, 4),
                    "tier_confidence": round(m.tier_confidence, 4),
                    "reason": m.reason,
                }
                for m in self.metrics
            ],
        }


@dataclass
class CascadeContext:
    """Per-call shared context handed to every metric.

    Holds anything that's expensive to compute and that multiple metrics
    might want (extended series info, Fribb mappings already looked up,
    the cluster's parent folder name, the cluster's common-sequence
    title signal, etc.). Provider HTTP fetches are cached here so a
    metric can read them without re-fetching.
    """
    parsed: ParsedFile
    candidates: list[Any]          # list[ScoredMatch] — typed at call site
    # The first file's parent folder, walked up past `Season N` if present.
    # Used by FolderIdentityMetric. None means no usable folder anchor.
    series_folder_name: str | None = None
    # Cluster-wide title signal — longest contiguous word sequence shared
    # across every filename in the cluster. None for single-file clusters
    # (the parsed.title is used directly). Computed once by the cluster
    # aggregator and reused for every candidate's scoring pass.
    cluster_signal: str | None = None
    # Per-candidate extras (TVDB extended info, Fribb mappings) keyed by
    # `(provider, provider_id)`. Metrics populate this lazily so the
    # second metric to need the same data hits the cache, not the network.
    enrich_cache: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    # Provider key currently being matched against. Some metrics behave
    # differently per provider (FribbAuthorityMetric only fires on anidb,
    # AnimeTVDBJPMetric only fires on tvdb).
    provider_key: str = ""


# Metric protocol — implementations are async because some need provider HTTP
# (TVDB extended fetch). Pure-Python metrics return a completed coroutine.
class Metric(Protocol):
    """A scoring contribution. Async so HTTP-backed metrics fit cleanly."""

    name: str
    tier: MetricTier

    async def score(
        self,
        candidate: Any,                 # ScoredMatch — typed at call site
        ctx: CascadeContext,
    ) -> MetricResult: ...


# Helper for metrics that don't need HTTP — wraps a sync callable into
# the async Metric protocol. Lets simple metrics (Substring, Year, Rank)
# stay declarative without `async def` ceremony.
SyncScorer = Callable[[Any, CascadeContext], MetricResult]


@dataclass
class SyncMetric:
    """Adapter: wraps a sync scoring function as an async Metric."""
    name: str
    tier: MetricTier
    fn: SyncScorer

    async def score(self, candidate: Any, ctx: CascadeContext) -> MetricResult:
        return self.fn(candidate, ctx)


def clamp_to_tier(raw: float, tier: MetricTier) -> float:
    """Clamp a raw [0.0, 1.0] score into the tier's band.

    A raw of 0.0 → returns 0.0 (didn't fire). A raw of 1.0 → returns the
    top of the band. Linear interpolation between.
    """
    if raw <= 0.0:
        return 0.0
    lo, hi = TIER_BANDS[tier]
    # Clip raw to [0, 1] defensively, then map linearly into [lo, hi].
    r = max(0.0, min(1.0, raw))
    return lo + r * (hi - lo)
