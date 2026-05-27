"""AnimeTVDBJPMetric — tier-2 boost for JP-origin TVDB results.

Replaces the old `_rerank_anime_tvdb` function. Fetches /series/{id}/extended
for each candidate (cached in ctx.enrich_cache so siblings share the data),
fires:
  - +0.15 if originalCountry == 'jpn' OR originalLanguage == 'jpn'
  - -0.20 if genres include live-action / reality / drama markers
  - +0.10 if an alias matches the parsed title better than the canonical
    display title (signals "this is the right show, just with a regional name")

Fires only on tvdb provider in anime context.
"""
from __future__ import annotations

from kira.matcher.cascade.types import (
    CascadeContext,
    MetricResult,
    MetricTier,
    clamp_to_tier,
)
from kira.matcher.similarity import trigram_similarity


class AnimeTVDBJPMetric:
    name = "anime_tvdb_jp"
    tier = MetricTier.SIMILARITY

    async def score(self, candidate, ctx: CascadeContext) -> MetricResult:
        if ctx.provider_key != "tvdb":
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="not tvdb",
            )
        provider = None
        try:
            from kira.matcher.engine import _global_registry_ref
            registry = _global_registry_ref.get()
            if registry is not None and registry.has("tvdb"):
                provider = registry.build("tvdb")
        except Exception:
            provider = None
        if provider is None or not hasattr(provider, "get_series_extended"):
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="no tvdb extended fetch available",
            )

        cache_key = (ctx.provider_key, candidate.provider_id)
        ext = ctx.enrich_cache.get(cache_key)
        if ext is None:
            try:
                ext = await provider.get_series_extended(candidate.provider_id)  # type: ignore[attr-defined]
                ctx.enrich_cache[cache_key] = ext or {}
            except Exception as e:
                return MetricResult(
                    metric=self.name, tier=self.tier,
                    raw=0.0, score=0.0, reason=f"extended fetch failed: {e!r}",
                )
        if not ext:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="no extended data",
            )

        raw = 0.0
        reasons: list[str] = []

        origin = (ext.get("original_country") or "").lower()
        lang = (ext.get("original_language") or "").lower()
        if origin in ("jpn", "jp") or lang in ("jpn", "ja"):
            raw += 0.5
            reasons.append("jp-origin")

        genres = {(g or "").lower() for g in (ext.get("genres") or [])}
        if genres & {"live action", "live-action", "reality", "talk show", "news"}:
            raw -= 0.6     # strong demotion; can bring this metric to -ish
            reasons.append("live-action penalty")

        # Alias-better-than-primary boost.
        needle = ctx.cluster_signal or ctx.parsed.title or ""
        if needle:
            best_alias_sim = max(
                (trigram_similarity(needle, a) for a in (ext.get("aliases") or []) if a),
                default=0.0,
            )
            primary_sim = trigram_similarity(needle, candidate.title or "")
            if best_alias_sim > primary_sim + 0.05:
                raw += 0.3
                reasons.append(f"alias bump (alias={best_alias_sim:.2f} > primary={primary_sim:.2f})")

        # Clip negatives to 0 — we don't veto here (the fribb filter
        # handles veto). Tier-2 metric, just contributes.
        clipped = max(0.0, min(1.0, raw))
        if clipped <= 0.0:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason=", ".join(reasons) or "no signal",
            )
        return MetricResult(
            metric=self.name, tier=self.tier,
            raw=clipped, score=clamp_to_tier(clipped, self.tier),
            reason=", ".join(reasons),
        )
