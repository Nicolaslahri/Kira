"""Pass 5 #4 — EpisodeTitleMetric cascade tests."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from kira.matcher.cascade.metrics.episode_title import EpisodeTitleMetric
from kira.matcher.cascade.types import CascadeContext, MetricTier
from kira.parser import ParsedFile


@dataclass
class _Cand:
    title: str
    provider_id: str = "12345"
    match_type: str = "tv_episode"
    aliases: list[str] = field(default_factory=list)


@dataclass
class _Ep:
    season: int
    episode: int
    title: str | None


def _ctx(
    episode_title_guess: str | None = None,
    season: int | None = 1,
    provider_key: str = "tvdb",
    episodes: list[_Ep] | None = None,
    candidate_id: str = "12345",
) -> CascadeContext:
    ctx = CascadeContext(
        parsed=ParsedFile(
            original_filename="x.mkv", media_type="tv",
            title="Game of Thrones", season=season,
            episode_title_guess=episode_title_guess,
        ),
        candidates=[],
        provider_key=provider_key,
    )
    if episodes is not None:
        cache_key = ("ep_titles", provider_key, candidate_id, season or 1)
        ctx.enrich_cache[cache_key] = episodes
    return ctx


async def test_strong_episode_title_match() -> None:
    m = EpisodeTitleMetric()
    ctx = _ctx(
        episode_title_guess="The Rains of Castamere",
        episodes=[
            _Ep(1, 1, "Winter Is Coming"),
            _Ep(1, 9, "The Rains of Castamere"),
        ],
    )
    r = await m.score(_Cand("Game of Thrones"), ctx)
    assert r.raw > 0.0
    assert r.tier == MetricTier.SIMILARITY
    assert r.score >= 0.50
    assert "episode title" in r.reason


async def test_no_fire_without_guess() -> None:
    m = EpisodeTitleMetric()
    ctx = _ctx(episode_title_guess=None, episodes=[_Ep(1, 1, "Pilot")])
    r = await m.score(_Cand("Some Show"), ctx)
    assert r.raw == 0.0
    assert "no episode title guess" in r.reason


async def test_no_fire_on_movie() -> None:
    m = EpisodeTitleMetric()
    ctx = _ctx(episode_title_guess="The Rains of Castamere", episodes=[_Ep(1, 1, "Pilot")])
    r = await m.score(_Cand("The Matrix", match_type="movie"), ctx)
    assert r.raw == 0.0
    assert "not a TV episode" in r.reason


async def test_no_fire_on_weak_match() -> None:
    m = EpisodeTitleMetric()
    ctx = _ctx(
        episode_title_guess="Completely Unrelated Words",
        episodes=[
            _Ep(1, 1, "Winter Is Coming"),
            _Ep(1, 2, "The Kingsroad"),
        ],
    )
    r = await m.score(_Cand("Game of Thrones"), ctx)
    assert r.raw == 0.0


async def test_short_guess_skipped() -> None:
    m = EpisodeTitleMetric()
    ctx = _ctx(episode_title_guess="Hi", episodes=[_Ep(1, 1, "Hi")])
    r = await m.score(_Cand("Some Show"), ctx)
    assert r.raw == 0.0
    assert "no episode title guess" in r.reason


async def test_empty_episode_list() -> None:
    m = EpisodeTitleMetric()
    ctx = _ctx(episode_title_guess="The Rains of Castamere", episodes=[])
    r = await m.score(_Cand("Game of Thrones"), ctx)
    assert r.raw == 0.0
    assert "no episode list" in r.reason


async def test_cache_reused_across_candidates() -> None:
    """Two candidates sharing the same provider+id+season should hit the cache."""
    m = EpisodeTitleMetric()
    eps = [_Ep(1, 1, "The Rains of Castamere")]
    ctx = _ctx(episode_title_guess="The Rains of Castamere", episodes=eps)
    r1 = await m.score(_Cand("GoT", provider_id="12345"), ctx)
    cache_key = ("ep_titles", "tvdb", "12345", 1)
    assert cache_key in ctx.enrich_cache
    r2 = await m.score(_Cand("GoT", provider_id="12345"), ctx)
    assert r1.raw == r2.raw


async def test_abstains_without_cached_list() -> None:
    """No pre-populated episode list → abstain (it must NOT fetch one)."""
    m = EpisodeTitleMetric()
    ctx = _ctx(episode_title_guess="The Rains of Castamere", episodes=None)
    r = await m.score(_Cand("Game of Thrones"), ctx)
    assert r.raw == 0.0
    assert "no episode list" in r.reason


def test_metric_source_never_fetches() -> None:
    """Regression guard for the AniDB-hammering bug: the cascade must stay
    pure/in-memory, so this metric must not CALL get_episodes or build a
    provider from the registry (docstrings may mention them)."""
    import inspect
    from kira.matcher.cascade.metrics import episode_title
    src = inspect.getsource(episode_title)
    assert ".get_episodes(" not in src
    assert "registry.build(" not in src


async def test_disambiguates_same_titled_shows() -> None:
    """The show with a matching episode title should score higher."""
    m = EpisodeTitleMetric()
    eps_got = [_Ep(1, 9, "The Rains of Castamere")]
    eps_clone = [_Ep(1, 1, "Pilot"), _Ep(1, 2, "The Clone Wars")]

    ctx_got = _ctx(
        episode_title_guess="The Rains of Castamere",
        episodes=eps_got, candidate_id="111",
    )
    ctx_got.enrich_cache[("ep_titles", "tvdb", "222", 1)] = eps_clone

    r_got = await m.score(_Cand("Game of Thrones", provider_id="111"), ctx_got)
    r_clone = await m.score(_Cand("Game of Clones", provider_id="222"), ctx_got)

    assert r_got.raw > 0.0
    assert r_clone.raw == 0.0
