"""AcronymMetric (Phase 13) — match acronym-named releases to full titles.

the reference renamer generates/matches initialisms so a file literally named `[AoT]` or
`JJK - 05` resolves to "Attack on Titan" / "Jujutsu Kaisen". Kira had the
AniDB title dump but no acronym reasoning, so an acronym-only filename
scored near-zero on every metric and orphaned.

Two paths, both tier-2 (strong-but-not-clinching):
  1. **Known fan-acronym list** — a curated map (AoT, JJK, SnK, FMA, MHA…).
     When the parsed title IS a known acronym and a candidate title/alias
     trigram-matches the expansion, fire high.
  2. **Generated initialism** — build the first-letter initialism of each
     candidate (both with-all-words "attack on titan"→"aot" and
     without-stopwords "lord of the rings"→"lotr") and match the parsed
     token against it.

Only fires when the parsed title is a single short token that looks like an
acronym — multi-word titles are left to the trigram/substring metrics.
"""
from __future__ import annotations

from kira.matcher.acronyms import (
    KNOWN_ACRONYMS as _KNOWN_ACRONYMS,
    acronym_forms as _acronyms,
    is_acronym_shaped,
)
from kira.matcher.cascade.types import (
    CascadeContext,
    MetricResult,
    MetricTier,
    clamp_to_tier,
)
from kira.matcher.similarity import normalize, trigram_similarity

# A curated-acronym expansion that trigram-matches a candidate this closely is
# identity-strength ("aot" → "attack on titan" vs the alias "Attack on Titan"
# scores 1.0). Promote it to tier-1 so an acronym-only anime file clears the
# 0.80 anime floor — a tier-2 hit tops out at ~0.73 weighted and would orphan.
_IDENTITY_TRIGRAM = 0.85


class AcronymMetric:
    name = "acronym"
    tier = MetricTier.SIMILARITY  # default; the curated-exact path returns IDENTITY

    async def score(self, candidate, ctx: CascadeContext) -> MetricResult:
        raw_needle = ctx.cluster_signal or ctx.parsed.title or ""
        needle = normalize(raw_needle)
        # Must be a single short token to look like an acronym. Multi-word
        # titles are handled by trigram/substring — don't double-fire here.
        if not is_acronym_shaped(needle):
            return MetricResult(self.name, self.tier, 0.0, 0.0, "needle not acronym-shaped")

        haystacks: list[str] = []
        if candidate.title:
            haystacks.append(candidate.title)
        for a in (candidate.aliases or []):
            if a:
                haystacks.append(a)

        # Path 1: known fan-acronym → trigram against the curated expansion.
        expansion = _KNOWN_ACRONYMS.get(needle)
        if expansion:
            best = max((trigram_similarity(expansion, h) for h in haystacks), default=0.0)
            if best >= _IDENTITY_TRIGRAM:
                # Near-exact expansion match → structural identity (tier-1).
                raw = 0.95
                return MetricResult(
                    self.name, MetricTier.IDENTITY, raw,
                    clamp_to_tier(raw, MetricTier.IDENTITY),
                    f"known acronym {needle!r}→{expansion!r} exact (trigram {best:.2f})",
                )
            if best >= 0.55:
                # Probable but not exact → strong similarity (tier-2).
                raw = 0.9
                return MetricResult(
                    self.name, MetricTier.SIMILARITY, raw,
                    clamp_to_tier(raw, MetricTier.SIMILARITY),
                    f"known acronym {needle!r}→{expansion!r} (trigram {best:.2f})",
                )

        # Path 2: generated initialism. Needs ≥3 chars so a 2-letter token
        # ("to", "bb") doesn't spuriously hit every two-word title. Stays
        # tier-2 — a generated initialism collision is far less certain than
        # a curated mapping, so it shouldn't auto-clinch a match.
        if len(needle) >= 3:
            for h in haystacks:
                if needle in _acronyms(normalize(h)):
                    raw = 0.7
                    return MetricResult(
                        self.name, MetricTier.SIMILARITY, raw,
                        clamp_to_tier(raw, MetricTier.SIMILARITY),
                        f"initialism {needle!r} == acronym({h[:40]!r})",
                    )

        return MetricResult(self.name, self.tier, 0.0, 0.0, f"no acronym match for {needle!r}")
