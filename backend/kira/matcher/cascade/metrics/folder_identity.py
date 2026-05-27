"""FolderIdentityMetric — tier-1 boost when the parent folder name closely
matches a candidate's title or alias.

Plex/Jellyfin/Sonarr libraries shelve series in named folders
(`/Anime/Rent-a-Girlfriend/Season 2/...`). The folder name is a strong
identity signal Kira used to ignore. Now: when `series_folder_name` is
set on the cascade context AND its trigram similarity to a candidate
title/alias ≥ 0.7, fire tier-1.

Stays NEUTRAL when the folder name is generic (`Downloads`, `Season N`,
`anime`). Never penalizes — folder hygiene is rewarded; folder chaos is
not punished. Generic-folder stop-list catches the common cases.
"""
from __future__ import annotations

from kira.matcher.cascade.types import (
    CascadeContext,
    Metric,
    MetricResult,
    MetricTier,
    clamp_to_tier,
)
from kira.matcher.similarity import normalize, trigram_similarity


_GENERIC_FOLDER_NAMES = frozenset({
    "downloads", "download", "anime", "tv", "tv shows", "shows",
    "movies", "films", "cinema", "music", "media", "library",
    "videos", "video", "torrents", "completed", "incomplete",
    "new folder", "untitled folder", "rips", "rip", "audio",
})

_THRESHOLD = 0.7   # FileBot's SeriesNameMatcher threshold


class FolderIdentityMetric:
    name = "folder_identity"
    tier = MetricTier.IDENTITY

    async def score(self, candidate, ctx: CascadeContext) -> MetricResult:
        folder = ctx.series_folder_name
        if not folder:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason="no folder anchor",
            )
        folder_n = normalize(folder)
        if not folder_n or folder_n in _GENERIC_FOLDER_NAMES:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0, reason=f"generic folder: {folder!r}",
            )
        # Score against display title + every alias.
        candidates_text: list[str] = []
        if candidate.title:
            candidates_text.append(candidate.title)
        for a in (candidate.aliases or []):
            if a:
                candidates_text.append(a)

        best = 0.0
        best_text = ""
        for t in candidates_text:
            sim = trigram_similarity(folder, t)
            if sim > best:
                best = sim
                best_text = t

        if best < _THRESHOLD:
            return MetricResult(
                metric=self.name, tier=self.tier,
                raw=0.0, score=0.0,
                reason=f"best alias sim {best:.2f} < {_THRESHOLD} ({best_text[:40]!r})",
            )

        # Map [0.7, 1.0] → tier-1 band linearly.
        normalized_raw = (best - _THRESHOLD) / (1.0 - _THRESHOLD)
        return MetricResult(
            metric=self.name, tier=self.tier,
            raw=normalized_raw, score=clamp_to_tier(normalized_raw, self.tier),
            reason=f"folder {folder!r} ~ {best_text[:40]!r} ({best:.2f})",
        )
