"""EpisodeTitleMetric — tier-2 boost when the filename's episode title
matches an episode in the candidate series' episode list.

Disambiguates same-titled shows: if "Game of Thrones - 3x09 - The Rains
of Castamere.mkv" scores two candidates equally on title similarity, the
candidate whose episode list contains "The Rains of Castamere" gets a
tier-2 boost.

CASCADE PURITY (load-bearing): this metric NEVER performs a network fetch.
The cascade runs inside `engine.match()` and must stay pure / in-memory —
fetching episode lists here (especially AniDB's rate-limited, ban-prone
`get_episodes`) serialized every cluster behind the 5s AniDB gate and risked
tripping the ban that the validation gate + cour routing carefully avoid. So
the metric reads ONLY an episode list already present in `ctx.enrich_cache`
(keyed `("ep_titles", provider, provider_id, season)`). When nothing is
cached it abstains — the episode-title *resolution* still happens ban-safely
in `bipartite.py` Pass 5 (the high-value half). The series-boost activates
only when a future caller pre-populates the cache from a ban-safe source.

Fires only when `parsed.episode_title_guess` is set AND the candidate is a TV
episode AND a cached episode list exists for it.
"""
from __future__ import annotations

from kira.matcher.cascade.types import (
    CascadeContext,
    MetricResult,
    MetricTier,
    clamp_to_tier,
)
from kira.matcher.similarity import trigram_similarity

_MIN_GUESS_LEN = 3
_MATCH_FLOOR = 0.55


class EpisodeTitleMetric:
    name = "episode_title"
    tier = MetricTier.SIMILARITY

    async def score(self, candidate, ctx: CascadeContext) -> MetricResult:
        guess = getattr(ctx.parsed, "episode_title_guess", None)
        if not guess or len(guess.strip()) < _MIN_GUESS_LEN:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="no episode title guess",
            )

        if getattr(candidate, "match_type", None) != "tv_episode":
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="not a TV episode candidate",
            )

        episodes = await self._get_episodes(candidate, ctx)
        if not episodes:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="no episode list available",
            )

        best_sim = 0.0
        best_title = ""
        for ep in episodes:
            title = ep.title if hasattr(ep, "title") else (ep.get("title") if isinstance(ep, dict) else None)
            if not title:
                continue
            sim = trigram_similarity(guess, title)
            if sim > best_sim:
                best_sim = sim
                best_title = title

        if best_sim < _MATCH_FLOOR:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0,
                reason=f"best episode title match {best_sim:.2f} < {_MATCH_FLOOR}",
            )

        raw = min(1.0, (best_sim - _MATCH_FLOOR) / (1.0 - _MATCH_FLOOR))
        return MetricResult(
            metric=self.name, tier=self.tier,
            raw=raw, score=clamp_to_tier(raw, self.tier),
            reason=f"episode title {best_sim:.2f} vs {best_title[:50]!r}",
        )

    async def _get_episodes(self, candidate, ctx: CascadeContext) -> list:
        """Episode list for this candidate IF already cached — NEVER fetched.

        Reading-only is deliberate: a network fetch here would break the
        cascade's pure/in-memory contract and re-introduce AniDB hammering.
        Returns [] (→ abstain) when no list is pre-populated in the context."""
        season = ctx.parsed.season if ctx.parsed.season is not None else 1
        cache_key = ("ep_titles", ctx.provider_key, candidate.provider_id, season)
        cached = ctx.enrich_cache.get(cache_key)
        return cached if cached is not None else []
