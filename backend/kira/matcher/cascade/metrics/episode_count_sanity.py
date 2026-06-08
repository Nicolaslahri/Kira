"""EpisodeCountSanityMetric — veto candidates that physically can't hold
the cluster's episode range, summing across Fribb-mapped sibling cours.

The structural problem this solves:
  - AniDB models a multi-cour TVDB season as N separate AIDs (Bleach
    TYBW Cour 1 / 2 / 3 → AID 15449 / 17849 / 18671).
  - TVDB lumps them as one Season 17.
  - User's cluster (40 files numbered E01-E40) physically spans all
    three cours.
  - Each cour's own episode count (13 / 13 / 14) is less than the
    cluster size — naive per-AID veto kills the legit cours and
    forces the matcher to fall back to AID 2369 (Original Bleach,
    366 eps, doesn't get vetoed).

The fix (summed-aggregate, after the user's analysis):
  1. Fast path: if the candidate's own episode count covers the
     cluster's max episode number, pass through.
  2. Aggregate path: when the candidate has a Fribb (tvdb_id, season)
     mapping, sum the cached episode counts of ALL AIDs Fribb pins
     to that same (tvdb_id, season) pair. If the sum covers the
     cluster, pass through.
  3. Coverage guard: if some Fribb-sibling AIDs have no cached
     episode count (we never fetched them), abstain rather than
     incorrectly veto on an understated sum.
  4. Veto (Autopsy 11): if the candidate reaches the bottom — own
     count is known AND short of the margin AND no Fribb aggregate
     rescued it — veto unconditionally. The margin check IS the
     floor. The prior `≤ 3 eps` carve-out only killed movies/OVAs;
     12-ep spin-offs were stealing 60-file clusters via raw title
     match (Substring 1.0). Once the math says short, the candidate
     cannot physically own this cluster regardless of identity.
     Sole exception: no cached count → abstain (can't decide on
     missing data, let other metrics speak).

Pure in-memory: `_ep_count_cache` on disk, Fribb dump in memory.
No HTTP, ban-safe.
"""
from __future__ import annotations

from kira.matcher.cascade.types import (
    CascadeContext,
    MetricResult,
    MetricTier,
)


# Margin: candidate aggregate must be >= cluster_max_episode * this.
# 0.9 tolerates a couple of specials interleaved with the main run
# without false-vetoing the cluster.
_COUNT_MARGIN = 0.9

# Cluster shape guards.
_MIN_CLUSTER = 3
_MIN_MAX_EP = 2


class EpisodeCountSanityMetric:
    name = "episode_count_sanity"
    tier = MetricTier.IDENTITY   # filter result reported at tier-1 for the trace

    async def score(self, candidate, ctx: CascadeContext) -> MetricResult:
        # Only AniDB candidates have per-AID episode counts cached.
        if ctx.provider_key != "anidb":
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="not anidb",
            )

        cluster_max_ep = getattr(ctx.parsed, "_cluster_max_episode", None)
        if cluster_max_ep is None:
            cluster_max_ep = ctx.parsed.episode or ctx.parsed.absolute_episode
        if cluster_max_ep is None or cluster_max_ep < _MIN_MAX_EP:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="no meaningful max episode",
            )

        cluster_size = getattr(ctx.parsed, "_cluster_size", None)
        if cluster_size is None or cluster_size < _MIN_CLUSTER:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="cluster too small to filter",
            )

        from kira.providers.anidb import AniDBProvider
        from kira.providers.anime_mappings import AnimeMappings

        try:
            aid = int(candidate.provider_id)
        except (ValueError, TypeError):
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="non-numeric AID",
            )

        count_cache = AniDBProvider._load_ep_count_cache()
        own_count = count_cache.get(aid)
        required = int(cluster_max_ep * _COUNT_MARGIN)

        # ── Fast path ──
        # Candidate's own count covers the cluster's max episode. Pass
        # without bothering with Fribb sibling lookup. This is the
        # 99% case (Bleach Cour 1 cluster with 13 files matches AID
        # 15449's 13 eps cleanly, no aggregate needed).
        if own_count is not None and own_count >= required:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0,
                reason=f"AID {aid} has {own_count} eps — covers max {cluster_max_ep}",
            )

        # ── Aggregate path ──
        # Candidate's own count is short OR not cached. Check whether
        # this AID is part of a multi-cour TVDB season; if so, sum the
        # siblings' counts. This is the Bleach TYBW case: AID 15449 has
        # 13 eps alone but the (tvdb=74796, season=17) aggregate is
        # 13+13+14 = 40, which covers a 40-file cluster.
        sibling_aids: list[int] = []
        cand_season: int | None = None
        cand_tvdb: int | None = None
        try:
            cand_tvdb = await AnimeMappings.tvdb_id(aid)
            cand_season = await AnimeMappings.tvdb_season(aid)
        except Exception:
            cand_tvdb = None
            cand_season = None

        # Only consult Fribb when the candidate has a mapping AND its
        # mapped season matches the user's parsed season. Otherwise the
        # candidate either isn't a cour (umbrella AID, Fribb season=None)
        # or maps to a different TVDB season entirely (off-by-one Fribb
        # data — let other metrics decide rather than auto-aggregate).
        if (
            cand_tvdb is not None
            and cand_season is not None
            and ctx.parsed.season is not None
            and cand_season == ctx.parsed.season
        ):
            try:
                sibling_aids = await AnimeMappings.aids_by_tvdb_season(
                    cand_tvdb, ctx.parsed.season,
                )
            except Exception:
                sibling_aids = []

        if sibling_aids:
            # Coverage guard: every sibling must have a cached count
            # for the sum to be trustworthy. If any sibling's count
            # isn't cached (never fetched), we'd UNDERSTATE the sum
            # and might wrongly veto a legitimate aggregate. Abstain
            # rather than risk a false-veto in that case.
            missing = [s for s in sibling_aids if s not in count_cache]
            if missing:
                return MetricResult(
                    metric=self.name, tier=self.tier,
                    raw=0.0, score=0.0,
                    reason=(
                        f"AID {aid} in fribb cour (tvdb={cand_tvdb}, s={cand_season}); "
                        f"{len(missing)} siblings missing count → abstain"
                    ),
                )
            sibling_sum = sum(count_cache[s] for s in sibling_aids)
            if sibling_sum >= required:
                return MetricResult(
                    metric=self.name, tier=self.tier,
                    raw=0.0, score=0.0,
                    reason=(
                        f"AID {aid} fribb-cour aggregate {sibling_sum} eps across "
                        f"{len(sibling_aids)} siblings covers max {cluster_max_ep}"
                    ),
                )
            # Aggregate STILL doesn't cover — fall through to the whole-
            # franchise check, then the veto. (Bizarre case: Fribb says this
            # is the right season but the cumulative episode count is still
            # short. Usually means Fribb mapping is wrong; we trust the math.)

        # ── Whole-franchise aggregate (absolute-numbered clusters) ──
        # A long-runner's files can be SERIES-ABSOLUTE numbered: AoT's Final
        # Season files are "- 60".."- 89", so cluster_max_episode (89) is an
        # index into the WHOLE franchise, NOT a count within one season. The
        # same-season cours (16+12+2 = 30 eps) can't reach it, but the
        # franchise's full absolute span does (S1..Final = 89). When THIS
        # candidate is a Fribb cour (has a tvdb mapping) of a multi-AID
        # franchise whose whole-tvdb-id aggregate covers the max, the cour
        # legitimately owns its slice — abstain instead of vetoing, and let
        # cour routing distribute the files across the parts. Tightly scoped:
        # only a Fribb-mapped member of a >1-AID franchise reaches the abstain;
        # a standalone OVA/movie (its tvdb_id maps to a single AID, or none)
        # still falls through to the veto below.
        if cand_tvdb is not None:
            try:
                franchise_aids = await AnimeMappings.aids_by_tvdb(cand_tvdb)
            except Exception:
                franchise_aids = []
            if len(franchise_aids) > 1:
                missing_fr = [a for a in franchise_aids if a not in count_cache]
                if missing_fr:
                    # Understated sum risk → abstain (never veto a real cour on
                    # missing data; the safe direction).
                    return MetricResult(
                        metric=self.name, tier=self.tier,
                        raw=0.0, score=0.0,
                        reason=(
                            f"AID {aid} is a fribb cour of tvdb={cand_tvdb}; "
                            f"{len(missing_fr)}/{len(franchise_aids)} franchise "
                            f"siblings missing count → abstain"
                        ),
                    )
                franchise_sum = sum(count_cache[a] for a in franchise_aids)
                if franchise_sum >= required:
                    return MetricResult(
                        metric=self.name, tier=self.tier,
                        raw=0.0, score=0.0,
                        reason=(
                            f"AID {aid} whole-franchise aggregate {franchise_sum} eps "
                            f"across {len(franchise_aids)} AIDs covers absolute max "
                            f"{cluster_max_ep} — abstain (cour routing distributes)"
                        ),
                    )

        # ── The Veto (Autopsy 11) ──
        # Reaching this point means:
        #   - own_count is short of the cluster's required margin AND
        #   - no Fribb sibling-cour aggregate could rescue it (no
        #     mapping, or aggregate also fell short)
        # The candidate physically cannot hold the files in this
        # cluster. Whether it's a 1-ep movie, a 12-ep cour spin-off,
        # or a 24-ep 2-cour show with no Fribb siblings — if the
        # math says short, the candidate is wrong for THIS cluster.
        # The previous `_MAX_COUNT_FOR_VETO = 3` floor only killed
        # movies/OVAs, leaving 12-ep cours to steal 60-file clusters
        # via raw text similarity (SubstringMetric scoring 1.0 for
        # a matching title). Drop the floor — the margin check IS
        # the floor.
        #
        # Sole exception: `own_count is None` (we never fetched this
        # AID's count) → abstain. We can't decide without the data;
        # leaving the candidate alive for other metrics is safer than
        # killing it on missing information.
        if own_count is None:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0,
                reason=f"no cached episode count for AID {aid} — abstain",
            )

        return MetricResult(
            metric=self.name, tier=self.tier,
            raw=-1.0, score=0.0,
            reason=(
                f"AID {aid} has {own_count} eps — short of {required} required, "
                f"no fribb cour to aggregate. Veto (cluster max {cluster_max_ep} "
                f"across {cluster_size} files)."
            ),
        )
