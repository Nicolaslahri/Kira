"""FribbAuthorityMetric — tier-1 promotion via Fribb cross-reference.

Replaces the old `_rerank_anime_by_fribb_season` function. When the parser
knows a TVDB season number (e.g. "Bleach.S17E27"), boost any AID whose
Fribb mapping confirms it IS that season of that TVDB series.

User-locked decisions baked in:
  - No PROMOTION_MIN_CONF gate. The cluster signal upstream (and the
    cluster isolation invariant) prevents the One Pace failure mode
    structurally; we don't need an extra threshold here.
  - Pure in-memory (Fribb dict pre-loaded). Safe during AniDB ban.

Fires ONLY when:
  - provider is anidb
  - parsed.season is set
  - the candidate's Fribb mapping has tvdb_season == parsed.season
  - no HIGHER-confidence candidate in the cluster maps to a DIFFERENT
    tvdb_id (would mean the search clearly wanted a different show)
"""
from __future__ import annotations

from kira.matcher.cascade.types import (
    CascadeContext,
    Metric,
    MetricResult,
    MetricTier,
    clamp_to_tier,
)


class FribbAuthorityMetric:
    name = "fribb_authority"
    tier = MetricTier.IDENTITY

    async def score(self, candidate, ctx: CascadeContext) -> MetricResult:
        parsed_season = ctx.parsed.season
        if parsed_season is None:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="no parsed season",
            )

        from kira.providers.anime_mappings import AnimeMappings

        try:
            aid = int(candidate.provider_id)
        except (ValueError, TypeError):
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="provider_id not numeric (not an AID)",
            )

        # ── Provider-agnostic gating (Autopsy 13/replacement) ────────
        # Previously this metric short-circuited on `provider_key !=
        # "anidb"`. That coupled the metric to one specific provider
        # name — a future Kitsu/MAL-direct provider that uses AniDB
        # AIDs as its IDs would have been silently disabled, with no
        # cascade contribution at all. Gate on the DATA, not the
        # provider name: if the candidate's numeric provider_id has
        # no entry in the Fribb cross-reference dict, this metric
        # has nothing useful to say. Abstain cleanly.
        #
        # Side effect for TVDB/TMDB candidates: their provider_ids
        # are numeric too, so they pass the int() check. But
        # `AnimeMappings.get()` keys on AID — TVDB id 74796 isn't an
        # AID, so the lookup misses and we abstain. Behavior is
        # identical to the prior `provider_key` gate; the difference
        # is provider-name-portable.
        if await AnimeMappings.get(aid) is None:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0,
                reason=f"AID {aid} has no fribb mapping — abstain",
            )

        # The candidate's Fribb mapping.
        try:
            cand_season = await AnimeMappings.tvdb_season(aid)
            cand_tvdb = await AnimeMappings.tvdb_id(aid)
        except Exception as e:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason=f"fribb error: {e!r}",
            )

        if cand_season != parsed_season or cand_tvdb is None:
            # Umbrella demotion: when this candidate has Fribb season=None
            # (it's the franchise umbrella entry, e.g. AID 2369 = "Bleach"
            # which Fribb pins to tvdb=74796 but with no specific season),
            # AND another candidate in the list has Fribb season matching
            # parsed_season under the SAME tvdb_id, the umbrella is
            # categorically wrong. The specific cour-AID is what the user's
            # file belongs to. VETO the umbrella so it can't win the tie at
            # the tier-1 ceiling.
            #
            # Real example: parsed.season=17, candidates = [AID 2369
            # (Bleach umbrella, season=None), AID 15449 (TYBW Cour 1,
            # season=17)]. Without this veto, SubstringMetric fires 1.0
            # for AID 2369 (exact title match "Bleach"), FribbAuthority
            # fires 1.0 for AID 15449. Tied at 1.0. AID 2369 wins on
            # candidate order. With this veto, AID 2369 is dropped from
            # the candidate set entirely, AID 15449 becomes the sole
            # tier-1 winner.
            if cand_season is None and cand_tvdb is not None:
                for other in ctx.candidates:
                    if other is candidate:
                        continue
                    try:
                        other_aid = int(other.provider_id)
                        other_tvdb = await AnimeMappings.tvdb_id(other_aid)
                        other_season = await AnimeMappings.tvdb_season(other_aid)
                    except Exception:
                        continue
                    if (
                        other_tvdb == cand_tvdb
                        and other_season == parsed_season
                    ):
                        return MetricResult(
                            metric=self.name, tier=self.tier,
                            raw=-1.0, score=0.0,
                            reason=(
                                f"umbrella AID {aid} demoted: "
                                f"sibling AID {other_aid} is fribb-pinned "
                                f"to (tvdb={cand_tvdb}, season={parsed_season})"
                            ),
                        )
            # ── Explicit-contradiction VETO (Autopsy 10) ─────────────
            # When Fribb has an EXPLICIT (non-None) `season.tvdb` for
            # this AID AND it disagrees with the user's parsed season,
            # the candidate is factually wrong for this file. The
            # community-curated cross-ref is ground truth — overruling
            # it via raw text similarity (SubstringMetric scores 1.0
            # for "My Hero Academia" S01 vs the same-title S06 cluster)
            # is exactly how the cascade was previously selecting the
            # wrong season for every sequel in the library.
            #
            # Returning `raw=-1.0` here puts the candidate into the
            # cluster-isolation invariant's veto bucket. It cannot be
            # rescued by Substring / Trigram / Folder / any tier-2 or
            # tier-3 metric — the cascade drops it from the candidate
            # set entirely. The correct sibling AID (which DOES match
            # parsed.season) wins by survivorship.
            if cand_season is not None and cand_season != parsed_season:
                # The veto presumes parsed.season is TVDB truth. That holds when
                # the season came from a real layout ("Bleach.S17E27"), but NOT
                # when it's synthetic — e.g. a library renamed to a unified show
                # folder with "Season 01/02/03" subfolders, where Bleach TYBW
                # files parse as S1 even though every cour is TVDB S17. Vetoing
                # on a season Fribb doesn't even know for this series zeroed ALL
                # the correct cours and the whole franchise went no_match after
                # a DB reset. So: veto ONLY when parsed_season is a REAL Fribb
                # season of this series (some sibling AID maps to it — then this
                # candidate is genuinely the wrong cour, the MHA S01-vs-S06 case).
                # Otherwise the season hint is unreliable → abstain and let the
                # title metrics decide.
                try:
                    season_is_real = bool(
                        await AnimeMappings.aids_by_tvdb_season(cand_tvdb, parsed_season)
                    )
                except Exception:
                    season_is_real = False
                if season_is_real:
                    return MetricResult(
                        metric=self.name, tier=self.tier,
                        raw=-1.0, score=0.0,
                        reason=f"fribb season {cand_season} != parsed {parsed_season} (veto)",
                    )
                return MetricResult(
                    metric=self.name, tier=self.tier,
                    raw=0.0, score=0.0,
                    reason=(
                        f"parsed season {parsed_season} unknown to fribb for "
                        f"tvdb {cand_tvdb} — season hint unreliable, abstain"
                    ),
                )
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0,
                reason=f"no fribb season match (fribb={cand_season}, parsed={parsed_season})",
            )

        # Check contradictor: any other higher-ranked candidate with a
        # DIFFERENT tvdb_id mapping (= search clearly wanted a different
        # show). The cluster signal upstream usually prevents this case,
        # but belt-and-braces.
        cand_idx = next(
            (i for i, c in enumerate(ctx.candidates) if c is candidate),
            -1,
        )
        for i, other in enumerate(ctx.candidates):
            if i >= cand_idx:
                break
            try:
                other_aid = int(other.provider_id)
                other_tvdb = await AnimeMappings.tvdb_id(other_aid)
            except Exception:
                continue
            if other_tvdb is not None and other_tvdb != cand_tvdb:
                return MetricResult(
                    metric=self.name, tier=self.tier,
                    raw=0.0, score=0.0,
                    reason=f"contradictor at rank {i} has tvdb={other_tvdb}",
                )

        return MetricResult(
            metric=self.name, tier=self.tier,
            raw=1.0, score=clamp_to_tier(1.0, self.tier),
            reason=f"fribb confirms AID {aid} = tvdb {cand_tvdb} S{parsed_season}",
        )
