"""AnimeSeasonOrdinalMetric — tier-2 boost when a candidate alias carries
the parsed.season number in roman/ordinal form.

Replaces `_rerank_anime_by_season`. AniDB titles often encode season as
"2nd Season" / "Season 3" / "III". When parser detected `season=3` in the
filename, prefer candidates whose aliases match — Rent-a-Girlfriend S3
("Kanojo, Okarishimasu 3rd Season") beats S1 ("Kanojo, Okarishimasu")
even though S1's bare alias trigrams higher.

Fires only when parsed.season >= 2 (S1 = bare title, no ordinal expected).
"""
from __future__ import annotations

import re

from kira.matcher.cascade.types import (
    CascadeContext,
    MetricResult,
    MetricTier,
    clamp_to_tier,
)


_ROMAN = {2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI",
          7: "VII", 8: "VIII", 9: "IX", 10: "X"}
_ORDINAL = {1: "1st", 2: "2nd", 3: "3rd"}


def _ordinal(n: int) -> str:
    return _ORDINAL.get(n, f"{n}th")


class AnimeSeasonOrdinalMetric:
    name = "anime_season_ordinal"
    tier = MetricTier.SIMILARITY

    async def score(self, candidate, ctx: CascadeContext) -> MetricResult:
        n = ctx.parsed.season
        if n is None or n < 2:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="no season hint or S1",
            )

        # Patterns that signal "this title carries season N".
        patterns = [
            rf"\b{n}(?:st|nd|rd|th)\s+season\b",
            rf"\bseason\s+{n}\b",
            rf"\bs{n}\b",
            rf"\b{_ordinal(n)}\s+season\b",
        ]
        if n in _ROMAN:
            patterns.append(rf"\b{_ROMAN[n]}\b")
            patterns.append(rf"\b{_ROMAN[n].lower()}\b")
        pat = re.compile("|".join(patterns), re.IGNORECASE)

        all_titles: list[str] = []
        if candidate.title:
            all_titles.append(candidate.title)
        for a in (candidate.aliases or []):
            if a:
                all_titles.append(a)
        for t in all_titles:
            if pat.search(t):
                return MetricResult(
                    metric=self.name, tier=self.tier,
                    raw=1.0, score=clamp_to_tier(1.0, self.tier),
                    reason=f"season {n} ordinal in {t[:50]!r}",
                )

        # No ordinal in this candidate. Don't penalize — bare titles
        # for S1 AIDs are legitimate (Fribb authority handles those).
        return MetricResult(
            metric=self.name, tier=self.tier,
            raw=0.0, score=0.0,
            reason=f"no S{n} ordinal in candidate titles",
        )
