"""M3 — metric-funnel Observer Mode (Pattern A).

The rebalanced funnel is computed in SHADOW and logged on divergence; it never
changes the score that drives ranking. These tests lock that contract:
  - the shadow rewards agreement across independent metric families,
  - tier-1 identity still wins outright,
  - the observer is OFF by default (no shadow, no behavior change),
  - when ON, a divergent top pick is logged but `final_score` is unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from kira.matcher.cascade.runner import (
    Cascade,
    _observer_on,
    _shadow_final,
    _shadow_tier2_raw,
)
from kira.matcher.cascade.types import (
    CascadeContext,
    MetricResult,
    MetricTier,
    clamp_to_tier,
)
from kira.parser import ParsedFile


def _r(metric: str, tier: MetricTier, raw: float) -> MetricResult:
    return MetricResult(metric, tier, raw, clamp_to_tier(raw, tier), "")


# ── Pure shadow math ───────────────────────────────────────────────────────


def test_string_family_alone_gets_no_bonus() -> None:
    # trigram + levenshtein + lcs all overlap → single family vote, no bonus.
    results = [
        _r("trigram", MetricTier.SIMILARITY, 0.70),
        _r("levenshtein", MetricTier.SIMILARITY, 0.65),
        _r("lcs", MetricTier.SIMILARITY, 0.60),
    ]
    assert _shadow_tier2_raw(results) == 0.70  # == max, no agreement bonus


def test_independent_agreement_adds_bonus() -> None:
    # trigram (string family) + acronym (independent) both fire → bonus.
    results = [
        _r("trigram", MetricTier.SIMILARITY, 0.70),
        _r("acronym", MetricTier.SIMILARITY, 0.70),
    ]
    shadow = _shadow_tier2_raw(results)
    assert shadow > 0.70                 # rewarded for corroboration
    assert abs(shadow - (0.70 + 0.15 * 0.70)) < 1e-9


def test_tier1_still_wins_in_shadow() -> None:
    results = [
        _r("substring", MetricTier.IDENTITY, 1.0),
        _r("trigram", MetricTier.SIMILARITY, 0.70),
        _r("acronym", MetricTier.SIMILARITY, 0.70),
    ]
    final = _shadow_final(results, tier_1_max=clamp_to_tier(1.0, MetricTier.IDENTITY), tier_3_max=0.0)
    assert final >= 0.85  # identity dominates regardless of the tier-2 bonus


# ── Observer flag gating ───────────────────────────────────────────────────


@dataclass
class _Cand:
    provider_id: str
    title: str = ""
    aliases: list | None = None
    match_type: str = "tv_episode"
    year: int | None = None
    confidence: float = 0.0


@dataclass
class _FakeMetric:
    name: str
    tier: MetricTier
    raw_by_id: dict

    async def score(self, candidate, ctx):
        raw = self.raw_by_id.get(candidate.provider_id, 0.0)
        return MetricResult(self.name, self.tier, raw, clamp_to_tier(raw, self.tier), "")


def _ctx() -> CascadeContext:
    return CascadeContext(
        parsed=ParsedFile(original_filename="x.mkv", media_type="anime", title="x"),
        candidates=[],
    )


async def test_observer_off_yields_no_shadow(monkeypatch) -> None:
    monkeypatch.delenv("KIRA_FUNNEL_OBSERVER", raising=False)
    assert _observer_on() is False
    casc = Cascade(metrics=[_FakeMetric("trigram", MetricTier.SIMILARITY, {"A": 0.7})])
    trace = await casc.score_one(_Cand("A"), _ctx())
    assert trace.shadow_score is None
    assert "shadow_score" not in trace.to_dict()


async def test_observer_on_records_shadow(monkeypatch) -> None:
    monkeypatch.setenv("KIRA_FUNNEL_OBSERVER", "1")
    assert _observer_on() is True
    casc = Cascade(metrics=[_FakeMetric("trigram", MetricTier.SIMILARITY, {"A": 0.7})])
    trace = await casc.score_one(_Cand("A"), _ctx())
    assert trace.shadow_score is not None
    assert "shadow_score" in trace.to_dict()


async def test_divergence_is_logged_without_changing_ranking(monkeypatch, caplog) -> None:
    """A has a single strong string signal; B has two agreeing independent
    signals. Current funnel keeps A on top; the shadow would prefer B. We log
    the disagreement but the returned ranking is unchanged."""
    monkeypatch.setenv("KIRA_FUNNEL_OBSERVER", "1")
    caplog.set_level(logging.INFO, logger="kira.matcher.funnel")

    casc = Cascade(metrics=[
        _FakeMetric("trigram", MetricTier.SIMILARITY, {"A": 0.80, "B": 0.70}),
        _FakeMetric("acronym", MetricTier.SIMILARITY, {"A": 0.0, "B": 0.70}),
    ])
    cands = [_Cand("A", "Show A"), _Cand("B", "Show B")]
    traces = await casc.score_all(cands, _ctx())

    # Behavior unchanged: A still scores higher than B on the live funnel.
    assert traces[0].final_score > traces[1].final_score
    # But the shadow would have preferred B (agreement bonus pushes it above A).
    assert (traces[1].shadow_score or 0.0) > (traces[0].shadow_score or 0.0)
    # And the divergence was logged.
    assert "funnel_diverge" in caplog.text
