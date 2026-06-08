"""RuntimeCorroborationMetric — tier-3 (M4).

the reference renamer corroborates a match with the file's real runtime: a 22-minute file
is plausibly a TV episode, a 2-hour file a movie. Kira reads the true duration
from the container via MediaInfo (`ParsedFile.duration`, seconds) and compares
it to the candidate's expected runtime.

Bounded by design — it NEVER triggers a network fetch of its own. Expected
runtime is read only from data that's already on hand:
  1. `candidate.raw["runtime"]` — minutes, when a details fetch already stashed it.
  2. A cached episode list in `ctx.enrich_cache` (the `("ep_titles", …)` entry
     EpisodeTitleMetric populates) — `EpisodeResult.runtime` per episode.
When neither is available, or the file has no duration, the metric abstains
(raw=0.0). Tier-3, so it can only gently nudge — never overrides identity or
similarity. The active per-candidate runtime fetch (the case that would fire on
*every* movie) is a deliberate, documented tunable left out to avoid per-
candidate rate-limit pressure (see roadmap M4).
"""
from __future__ import annotations

from statistics import median

from kira.matcher.cascade.types import (
    CascadeContext,
    MetricResult,
    MetricTier,
    clamp_to_tier,
)
from kira.matcher.text_distance import runtime_similarity


class RuntimeCorroborationMetric:
    name = "runtime"
    tier = MetricTier.CORROBORATION

    async def score(self, candidate, ctx: CascadeContext) -> MetricResult:
        file_secs = getattr(ctx.parsed, "duration", None)
        if not file_secs or file_secs <= 0:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="no file duration",
            )

        expected = self._expected_minutes(candidate, ctx)
        if not expected:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="no expected runtime on hand",
            )

        sim = runtime_similarity(file_secs, expected)
        if sim is None or sim <= 0.0:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0,
                reason=f"runtime mismatch (file {file_secs // 60}m vs ~{expected}m)",
            )
        return MetricResult(
            metric=self.name, tier=self.tier,
            raw=sim, score=clamp_to_tier(sim, self.tier),
            reason=f"runtime ~{expected}m vs file {file_secs // 60}m (sim {sim:.2f})",
        )

    def _expected_minutes(self, candidate, ctx: CascadeContext) -> int | None:
        """Expected runtime in minutes from already-available data, else None."""
        # 1. Stashed on the candidate (a details fetch already ran).
        raw = getattr(candidate, "raw", None) or {}
        rt = raw.get("runtime")
        if isinstance(rt, (int, float)) and rt > 0:
            return int(rt)

        # 2. Cached episode list (EpisodeTitleMetric / validation gate). Use the
        #    parsed episode's runtime if we can find it, else the median episode
        #    runtime (a series' episodes are near-constant length).
        season = ctx.parsed.season if ctx.parsed.season is not None else 1
        cache_key = ("ep_titles", ctx.provider_key, candidate.provider_id, season)
        episodes = ctx.enrich_cache.get(cache_key)
        if not episodes:
            return None

        target_ep = ctx.parsed.episode if ctx.parsed.episode is not None else ctx.parsed.absolute_episode
        runtimes: list[int] = []
        exact: int | None = None
        for ep in episodes:
            r = getattr(ep, "runtime", None)
            if isinstance(r, (int, float)) and r > 0:
                runtimes.append(int(r))
                # Match the parsed number against EITHER the provider's local
                # `episode` OR its `absolute_number`: a long-runner file is
                # numbered by absolute (One Piece "1156" / absolute_episode),
                # which never equals a per-season local index (1..13). Checking
                # only `ep.episode` left the exact-runtime branch dead for every
                # absolute-numbered file; checking both finds it in either scheme.
                ep_no = getattr(ep, "episode", None)
                ep_abs = getattr(ep, "absolute_number", None)
                if target_ep is not None and (ep_no == target_ep or ep_abs == target_ep):
                    exact = int(r)
        if exact:
            return exact
        if runtimes:
            return int(round(median(runtimes)))
        return None
