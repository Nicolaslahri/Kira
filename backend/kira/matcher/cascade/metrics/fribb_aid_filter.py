"""FribbAidFilterMetric — vetoes non-anime TVDB/TMDB results from anime
searches.

When matching an anime file, TVDB returns a flat result set that mixes
anime with live-action JP drama, US Western productions etc. The Fribb
anime-list dataset is the ground-truth catalogue of "what's anime"; if a
TVDB id has no Fribb entry pointing at it, it's not anime, drop it.

Returns raw=-1.0 (veto — drops the candidate entirely) when Fribb says
"not anime". Returns raw=0.0 (no contribution) when the candidate IS in
Fribb (let other metrics score it).

R2-C3 fallback: when the Fribb dump is empty/stale, return neutral
(don't veto) so the matcher doesn't wipe everything. The matcher's
existing language-based fallback path handles that case separately.
"""
from __future__ import annotations

from kira.matcher.cascade.types import (
    CascadeContext,
    MetricResult,
    MetricTier,
)


class FribbAidFilterMetric:
    name = "fribb_aid_filter"
    tier = MetricTier.IDENTITY   # filter result reported at tier-1 for the trace

    async def score(self, candidate, ctx: CascadeContext) -> MetricResult:
        # Only filter for anime + non-AniDB providers (AniDB candidates
        # ARE the Fribb source of truth — don't filter them).
        if ctx.parsed.media_type != "anime" or ctx.provider_key == "anidb":
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="not applicable",
            )

        from kira.providers.anime_mappings import AnimeMappings
        try:
            await AnimeMappings._ensure_loaded()
        except Exception:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="fribb load failed — filter disabled",
            )

        # Empty dump → fallback to neutral. The matcher's outer flow
        # has a language-based fallback that handles this case.
        if not getattr(AnimeMappings, "_by_aid", None):
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="fribb dump empty — filter disabled",
            )

        try:
            cand_id = int(candidate.provider_id)
        except (ValueError, TypeError):
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="non-numeric provider_id",
            )

        try:
            if ctx.provider_key == "tvdb":
                aid = await AnimeMappings.aid_by_tvdb(cand_id)
            elif ctx.provider_key == "tmdb":
                aid = await AnimeMappings.aid_by_tmdb_tv(cand_id)
            else:
                aid = None
        except Exception:
            aid = None

        if aid is None:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=-1.0, score=0.0,
                reason=f"no fribb AID for {ctx.provider_key}:{cand_id} (not anime)",
            )
        return MetricResult(
            metric=self.name, tier=self.tier,
            raw=0.0, score=0.0,
            reason=f"fribb maps {ctx.provider_key}:{cand_id} to AID {aid}",
        )
