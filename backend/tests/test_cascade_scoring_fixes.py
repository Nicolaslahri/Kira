"""Scoring fixes from the audit (§21 B1-B4) — pinned behavior.

B1  Tier-blend: corroboration BOOSTS tier-2 toward the band top instead of
    averaging it down (the old 0.7/0.3 capped no-tier-1 scores at 0.745,
    making anime — floor 0.80 — unmatchable on similarity alone).
B2  Trigram junk floor raised 0.30 → 0.45 (a 0.30-similar rank-0 candidate
    used to clear the 0.55 movie auto-commit).
B3  Equal-confidence ties break on YEAR CLOSENESS before provider-id string
    (the 2011 remake of "The Thing" used to beat 1982 because "60935" >
    "1091" lexicographically).
B4  The year-recombined query rung ("Blade Runner 2049") scores against the
    recombined text, not the peeled title.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from kira.matcher.cascade.runner import Cascade, _CORROBORATION_BOOST
from kira.matcher.cascade.types import (
    TIER_BANDS,
    CascadeContext,
    MetricResult,
    MetricTier,
    clamp_to_tier,
)
from kira.parser import parse


@dataclass
class _FixedMetric:
    """A metric that returns a fixed raw score in a fixed tier."""
    name: str
    tier: MetricTier
    raw: float

    async def score(self, candidate, ctx) -> MetricResult:
        return MetricResult(
            metric=self.name, tier=self.tier, raw=self.raw,
            score=clamp_to_tier(self.raw, self.tier), reason="fixed",
        )


def _ctx() -> CascadeContext:
    return CascadeContext(parsed=parse("Some Show S01E01.mkv"), candidates=[object()])


async def _final(*metrics: _FixedMetric) -> float:
    cascade = Cascade(metrics=list(metrics))
    trace = await cascade.score_one(object(), _ctx())
    return trace.final_score


T2_LO, T2_HI = TIER_BANDS[MetricTier.SIMILARITY]


# ── B1: the blend ─────────────────────────────────────────────────────────────

async def test_perfect_tier2_plus_perfect_tier3_hits_band_top():
    """The design comment's promise, now true: perfect similarity + perfect
    corroboration land at the TOP of the tier-2 band (≈0.85), not 0.745."""
    final = await _final(
        _FixedMetric("sim", MetricTier.SIMILARITY, 1.0),
        _FixedMetric("cor", MetricTier.CORROBORATION, 1.0),
    )
    assert final == pytest.approx(T2_HI, abs=1e-6)
    assert final < TIER_BANDS[MetricTier.IDENTITY][0]  # still can't beat tier-1


async def test_corroboration_never_lowers_a_lone_tier2():
    """The old average DRAGGED tier-2 down whenever tier-3 fired (and rank
    always fires). The boost must be monotonic: with-corroboration ≥ without."""
    alone = await _final(_FixedMetric("sim", MetricTier.SIMILARITY, 0.8))
    boosted = await _final(
        _FixedMetric("sim", MetricTier.SIMILARITY, 0.8),
        _FixedMetric("cor", MetricTier.CORROBORATION, 0.6),
    )
    assert boosted >= alone


async def test_anime_can_match_on_similarity_alone_now():
    """Trigram raw 0.80 + full corroboration must clear the 0.80 anime floor
    (was mathematically impossible: max 0.745)."""
    from kira.matcher.engine import MatchEngine
    final = await _final(
        _FixedMetric("sim", MetricTier.SIMILARITY, 0.80),
        _FixedMetric("cor", MetricTier.CORROBORATION, 1.0),
    )
    assert final >= MatchEngine.MIN_CONFIDENCE_ANIME


async def test_one_pace_stays_below_anime_floor():
    """'One Pace' vs 'One Piece' trigram ≈ 0.73 — the 0.80 anime floor exists
    to block exactly this; the boost must NOT push it over."""
    from kira.matcher.engine import MatchEngine
    final = await _final(
        _FixedMetric("sim", MetricTier.SIMILARITY, 0.73),
        _FixedMetric("cor", MetricTier.CORROBORATION, 1.0),  # rank-0, best case
    )
    assert final < MatchEngine.MIN_CONFIDENCE_ANIME


async def test_one_pace_real_pipeline_numbers_stay_below_floor():
    """The case the trigram-only calibration missed (found in production):
    LEVENSHTEIN scores 'One Pace' vs 'One Piece' at raw 0.778 — higher than
    trigram's 0.667 — and rank fired 1.0 because One Piece is trivially the
    top hit when searching AniDB for "One Pace". That combination scored
    0.802: over the 0.80 anime floor, over the AniDB early-exit, matched.

    Rank is SELF-REFERENTIAL (a candidate's position in the provider's own
    search for the query string is not independent evidence) so it must not
    fuel the corroboration boost. With real-pipeline numbers the score stays
    at the bare tier-2 value, under the floor → no_match, user decides."""
    from kira.matcher.engine import MatchEngine
    final = await _final(
        _FixedMetric("trigram", MetricTier.SIMILARITY, 0.667),
        _FixedMetric("levenshtein", MetricTier.SIMILARITY, 0.778),
        _FixedMetric("lcs", MetricTier.SIMILARITY, 0.778),
        _FixedMetric("rank", MetricTier.CORROBORATION, 1.0),
    )
    assert final < MatchEngine.MIN_CONFIDENCE_ANIME
    assert final < MatchEngine.ANIME_ANIDB_TRUST_FLOOR
    # Score equals bare tier-2 (rank added nothing).
    assert final == pytest.approx(clamp_to_tier(0.778, MetricTier.SIMILARITY), abs=1e-6)


async def test_rank_alone_does_not_boost_but_real_corroboration_does():
    """Same fuzzy similarity, two corroboration shapes: rank-only must add
    nothing; independent evidence (year match) still boosts. This is the
    line the fix draws — near-misses can't ride their own search position
    over a decision floor, but genuine agreement still counts."""
    sim = _FixedMetric("levenshtein", MetricTier.SIMILARITY, 0.778)
    rank_only = await _final(sim, _FixedMetric("rank", MetricTier.CORROBORATION, 1.0))
    with_year = await _final(
        sim,
        _FixedMetric("rank", MetricTier.CORROBORATION, 1.0),
        _FixedMetric("year", MetricTier.CORROBORATION, 1.0),
    )
    bare = await _final(sim)
    assert rank_only == pytest.approx(bare, abs=1e-9)
    assert with_year > bare


async def test_one_typo_romaji_still_clears_anime_floor_without_boost():
    """The regression the boost redesign originally fixed must SURVIVE the
    rank exclusion: a one-typo title (trigram ≈0.95) clears the 0.80 anime
    floor on similarity alone — no corroboration needed."""
    from kira.matcher.engine import MatchEngine
    final = await _final(
        _FixedMetric("trigram", MetricTier.SIMILARITY, 0.95),
        _FixedMetric("rank", MetricTier.CORROBORATION, 1.0),
    )
    assert final >= MatchEngine.MIN_CONFIDENCE_ANIME


async def test_tier1_still_wins_outright():
    final = await _final(
        _FixedMetric("id", MetricTier.IDENTITY, 0.9),
        _FixedMetric("sim", MetricTier.SIMILARITY, 1.0),
        _FixedMetric("cor", MetricTier.CORROBORATION, 1.0),
    )
    t1_lo, t1_hi = TIER_BANDS[MetricTier.IDENTITY]
    assert final == pytest.approx(t1_lo + 0.9 * (t1_hi - t1_lo), abs=1e-6)


async def test_weak_similarity_gains_little_from_corroboration():
    """The boost is scaled by tier-2's own within-band confidence, so weak
    similarity can't ride corroboration up by much."""
    weak = await _final(
        _FixedMetric("sim", MetricTier.SIMILARITY, 0.1),
        _FixedMetric("cor", MetricTier.CORROBORATION, 1.0),
    )
    t2 = clamp_to_tier(0.1, MetricTier.SIMILARITY)
    max_gain = _CORROBORATION_BOOST * 0.1 * 1.0 * (T2_HI - t2)
    assert weak <= t2 + max_gain + 1e-9


# ── B2: trigram junk floor ────────────────────────────────────────────────────

async def test_trigram_abstains_below_045():
    from kira.matcher.cascade.metrics.trigram import TrigramMetric

    class _Cand:
        title = "Completely Unrelated Thing"
        aliases: list = []

    ctx = CascadeContext(parsed=parse("Zorbulon Prime S01E01.mkv"), candidates=[])
    r = await TrigramMetric().score(_Cand(), ctx)
    assert r.raw == 0.0 and r.score == 0.0  # junk similarity → abstain


async def test_trigram_fires_on_genuine_similarity():
    from kira.matcher.cascade.metrics.trigram import TrigramMetric

    class _Cand:
        title = "Attack on Titan"
        aliases: list = []

    ctx = CascadeContext(parsed=parse("Attack on Titan S01E01.mkv"), candidates=[])
    r = await TrigramMetric().score(_Cand(), ctx)
    assert r.raw > 0.9 and r.score > 0.8


# ── B3: year tie-break ───────────────────────────────────────────────────────

def test_year_closeness_prefers_the_original_over_the_remake():
    from kira.matcher.engine import _year_closeness

    # "The.Thing.1983.mkv" (rip year off by one): 1982 (diff 1) must beat
    # 2011 (diff 28) on an equal-confidence tie.
    assert _year_closeness(1983, 1982) > _year_closeness(1983, 2011)
    # Exact match is best.
    assert _year_closeness(1982, 1982) > _year_closeness(1982, 1981)
    # No parsed year → neutral (tie-break falls through to provider id).
    assert _year_closeness(None, 1982) == _year_closeness(None, 2011)
    # Unknown candidate year loses to any known year when the file has one.
    assert _year_closeness(1983, None) < _year_closeness(1983, 2011)


def test_sort_tuple_resolves_the_thing_remake():
    """End-to-end on the sort key shape used in the engine: equal confidence,
    the year-closer candidate wins regardless of id-string ordering."""
    from kira.matcher.engine import _year_closeness

    orig = {"confidence": 1.0, "provider": "tmdb", "provider_id": "1091", "year": 1982}
    remake = {"confidence": 1.0, "provider": "tmdb", "provider_id": "60935", "year": 2011}
    ranked = sorted(
        [remake, orig],
        key=lambda s: (s["confidence"], _year_closeness(1983, s["year"]),
                       str(s["provider"]), str(s["provider_id"])),
        reverse=True,
    )
    assert ranked[0]["provider_id"] == "1091"  # the 1982 original wins


# ── B4: recombined-rung scoring needle ───────────────────────────────────────

def test_recombined_query_becomes_the_scoring_title():
    """Simulate the engine's B4 gate: when the successful rung queried
    '<title> <year>', the parsed copy handed to the cascade carries the
    recombined title (so 'Blade Runner 2049' exact-matches the 2017 film
    instead of losing to the 1982 'Blade Runner')."""
    import copy

    parsed = parse("Blade.Runner.2049.1080p.mkv")
    assert parsed.title == "Blade Runner" and parsed.year == 2049

    used_query = f"{parsed.title} {parsed.year}"
    scoring_parsed = parsed
    if used_query.strip() == f"{parsed.title} {parsed.year}".strip():
        scoring_parsed = copy.copy(parsed)
        scoring_parsed.title = used_query.strip()

    assert scoring_parsed.title == "Blade Runner 2049"
    assert parsed.title == "Blade Runner"          # original untouched

    # And the trigram needle actually prefers the 2049 film with that title:
    from kira.matcher.similarity import trigram_similarity
    sim_2049 = trigram_similarity("Blade Runner 2049", "Blade Runner 2049")
    sim_1982 = trigram_similarity("Blade Runner 2049", "Blade Runner")
    assert sim_2049 > sim_1982
