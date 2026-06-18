"""Shared subtitle types — the structured candidate every provider produces, so
the scorer can rank them on equal footing and the UI can explain the pick.

The architecture this enables: each provider SEARCHES (returns candidates) and
DOWNLOADS (a candidate → bytes) as separate steps. The aggregator gathers
candidates from all enabled providers, scores them against the video, and
downloads only the winner — instead of each provider blindly taking its own
first result. That same candidate list powers the manual browse-and-pick modal
and the stored history (score + reason + sync confidence).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchContext:
    """Everything a provider needs to search for one video's subtitles. Built
    once per file by the aggregator and passed to every provider's search()."""
    video_path: str
    languages: list[str]                  # wanted, normalized 2-letter
    media_type: str | None = None         # movie | tv | anime
    query: str | None = None              # cleaned title
    tmdb_id: int | None = None
    imdb_id: Any = None
    anidb_id: int | None = None
    season: int | None = None
    episode: int | None = None
    # The matched provider's episode TITLE ("The Rains of Castamere") — used to
    # pull the right entry out of a season pack by name when the number is
    # absent/ambiguous inside the archive.
    episode_title: str | None = None
    parsed: dict | None = None            # the video's parsed_data, for release scoring
    # credentials (per provider)
    os_api_key: str | None = None
    os_user: str | None = None
    os_pw: str | None = None
    subdl_api_key: str | None = None
    subsource_api_key: str | None = None
    # variant prefs
    hearing_impaired: str = ""             # "" | include | exclude | only
    forced: str = ""
    # {(provider, ref)} the user blacklisted for THIS file — excluded from picks.
    blacklist: set = field(default_factory=set)
    # Minimum acceptable score (0 = no floor). A best candidate below this is
    # NOT saved — better no sub than a likely-wrong one.
    min_score: int = 0


@dataclass
class SubtitleCandidate:
    """One subtitle a provider offers, before download. `download_ref` is the
    provider-specific handle its `download()` needs (file_id / url / subtitleId
    / track index). Scorer fills `score` / `reasons` / `sync`."""
    provider: str                  # "embedded" | "opensubtitles" | "subdl" | …
    language: str                  # normalized 2-letter
    release_name: str = ""         # the sub's release string (for affinity scoring)
    download_ref: Any = None       # opaque, provider-specific

    downloads: int = 0             # community download count (trust signal)
    rating: float | None = None    # 0..1 normalized, when the provider gives it
    hash_match: bool = False       # OpenSubtitles moviehash — sync-guaranteed
    hearing_impaired: bool = False
    forced: bool = False
    is_pack: bool = False          # season pack → exact episode was guessed
    from_embedded: bool = False    # the file's OWN track — perfect sync

    # Filled by the scorer:
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    sync: str = "unknown"          # "guaranteed" | "likely" | "unknown"

    def public(self) -> dict[str, Any]:  # noqa: D401
        """JSON-safe view for the API / browse modal (no download_ref)."""
        return {
            "provider": self.provider,
            "language": self.language,
            "release_name": self.release_name,
            "downloads": self.downloads,
            "rating": self.rating,
            "hash_match": self.hash_match,
            "hearing_impaired": self.hearing_impaired,
            "forced": self.forced,
            "is_pack": self.is_pack,
            "from_embedded": self.from_embedded,
            "score": self.score,
            "reasons": self.reasons,
            "sync": self.sync,
        }


@dataclass
class SubtitleFetchResult:
    """One subtitle that actually landed on disk — what the aggregator returns
    so the caller can narrate + persist the pick (provider, score, sync, why)."""
    language: str
    path: str
    provider: str
    release_name: str = ""
    ref: str | None = None              # provider download handle (for blacklist)
    score: int = 0
    sync: str = "unknown"
    reasons: list[str] = field(default_factory=list)
