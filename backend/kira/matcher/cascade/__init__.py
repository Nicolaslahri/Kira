"""MetricCascade — Kira's tiered scoring pipeline.

Replaces `score_match`'s weighted sum + the chain of post-hoc `_rerank_anime_*`
functions with a composable pipeline that:

  1. Evaluates EVERY metric (no short-circuit). Observability beats the
     microsecond compute saved by early-exit — the cascade trace tells
     the user why a confidence is what it is, which closes the "why is
     this 65%?" loop and gives the heal pass enough signal to rescore
     in place without rerunning the matcher.

  2. Uses tier-banded magnitudes so a single tier-1 identity signal
     cannot be drowned by tier-2 similarity noise. Mathematically: a
     tier-1 hit lands in [0.85, 1.00], tier-2 in [0.50, 0.85), tier-3
     in [0.20, 0.50). Final = max(tier_1_max, weighted_avg(tier_2_3)).

  3. Stores the full trace on Match.metadata_blob['cascade_trace'] so
     the frontend can render "why this confidence?" on hover.

See the reference renamer's MetricCascade.java + EpisodeMetrics.java for prior art
(GPL-2 — we read the algorithms, wrote Python from scratch).
"""
from kira.matcher.cascade.types import (
    Metric,
    MetricResult,
    MetricTier,
    CascadeTrace,
    CascadeContext,
)
from kira.matcher.cascade.runner import Cascade, build_default_cascade

__all__ = [
    "Metric",
    "MetricResult",
    "MetricTier",
    "CascadeTrace",
    "CascadeContext",
    "Cascade",
    "build_default_cascade",
]
