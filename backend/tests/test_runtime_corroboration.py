"""M4 — runtime corroboration: pure helper + cascade metric."""

from __future__ import annotations

from dataclasses import dataclass, field

from kira.matcher.text_distance import runtime_similarity
from kira.matcher.cascade.metrics.corroboration import RuntimeCorroborationMetric
from kira.matcher.cascade.types import CascadeContext, MetricTier
from kira.parser import ParsedFile
from kira.parser.mediainfo import duration_to_seconds


# ── duration_to_seconds (MediaInfo ms → whole seconds) ──────────────────

def test_duration_ms_to_seconds() -> None:
    assert duration_to_seconds(1_320_000) == 1320     # 22 min
    assert duration_to_seconds("7200000") == 7200     # 2 h, string form
    assert duration_to_seconds(1_320_500.0) == 1320   # rounds


def test_duration_invalid_or_zero_is_none() -> None:
    assert duration_to_seconds(None) is None
    assert duration_to_seconds(0) is None
    assert duration_to_seconds("nonsense") is None
    assert duration_to_seconds(-5) is None


# ── runtime_similarity ──────────────────────────────────────────────────

def test_runtime_exact_match() -> None:
    assert runtime_similarity(1320, 22) == 1.0        # 22 min file vs 22 min ep


def test_runtime_within_tolerance() -> None:
    # 24-min file vs 22-min episode → within ±20% band → 1.0
    assert runtime_similarity(24 * 60, 22) == 1.0


def test_runtime_gross_mismatch_is_zero() -> None:
    # 90-min file vs 22-min episode → way outside → 0.0
    assert runtime_similarity(90 * 60, 22) == 0.0


def test_runtime_partial_decay() -> None:
    # A moderate overshoot lands strictly between 0 and 1.
    sim = runtime_similarity(35 * 60, 22)
    assert 0.0 < sim < 1.0


def test_runtime_abstains_on_missing() -> None:
    assert runtime_similarity(None, 22) is None
    assert runtime_similarity(1320, None) is None
    assert runtime_similarity(0, 22) is None
    assert runtime_similarity(1320, 0) is None


def test_runtime_short_floor() -> None:
    # 4-min OP/ED: a 5-min file should still match thanks to the absolute floor.
    assert runtime_similarity(5 * 60, 4) == 1.0


# ── RuntimeCorroborationMetric ──────────────────────────────────────────

@dataclass
class _Cand:
    provider_id: str = "111"
    match_type: str = "tv_episode"
    raw: dict = field(default_factory=dict)


@dataclass
class _Ep:
    episode: int
    runtime: int | None


def _ctx(duration=None, season=1, provider_key="tvdb", episodes=None,
         candidate_id="111", episode=None) -> CascadeContext:
    ctx = CascadeContext(
        parsed=ParsedFile(
            original_filename="x.mkv", media_type="tv", title="Show",
            season=season, episode=episode, duration=duration,
        ),
        candidates=[],
        provider_key=provider_key,
    )
    if episodes is not None:
        ctx.enrich_cache[("ep_titles", provider_key, candidate_id, season)] = episodes
    return ctx


async def test_metric_abstains_without_duration() -> None:
    m = RuntimeCorroborationMetric()
    r = await m.score(_Cand(), _ctx(duration=None, episodes=[_Ep(1, 22)]))
    assert r.raw == 0.0
    assert "no file duration" in r.reason


async def test_metric_abstains_without_expected() -> None:
    m = RuntimeCorroborationMetric()
    r = await m.score(_Cand(), _ctx(duration=1320, episodes=None))
    assert r.raw == 0.0
    assert "no expected runtime" in r.reason


async def test_metric_fires_from_cached_episode_runtime() -> None:
    m = RuntimeCorroborationMetric()
    ctx = _ctx(duration=22 * 60, episode=5,
               episodes=[_Ep(5, 22), _Ep(6, 23)])
    r = await m.score(_Cand(), ctx)
    assert r.raw == 1.0
    assert r.tier == MetricTier.CORROBORATION
    assert 0.20 <= r.score < 0.50  # tier-3 band


async def test_metric_uses_median_when_episode_not_found() -> None:
    m = RuntimeCorroborationMetric()
    # parsed episode 99 not in list → median of [22,24,23] = 23 → 22-min file fits
    ctx = _ctx(duration=22 * 60, episode=99,
               episodes=[_Ep(1, 22), _Ep(2, 24), _Ep(3, 23)])
    r = await m.score(_Cand(), ctx)
    assert r.raw > 0.0


async def test_metric_reads_runtime_from_candidate_raw() -> None:
    m = RuntimeCorroborationMetric()
    cand = _Cand(raw={"runtime": 120})  # a 2h movie runtime stashed by details
    ctx = _ctx(duration=118 * 60)
    r = await m.score(cand, ctx)
    assert r.raw == 1.0


async def test_metric_penalizes_gross_mismatch() -> None:
    m = RuntimeCorroborationMetric()
    # A 2h file scored against a 22-min episode list → mismatch → abstains (0).
    ctx = _ctx(duration=120 * 60, episode=1, episodes=[_Ep(1, 22)])
    r = await m.score(_Cand(), ctx)
    assert r.raw == 0.0
    assert "mismatch" in r.reason
