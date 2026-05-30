"""Provider abstraction with Kira Cloud hooks baked in from day one.

Each provider takes `base_url` + `auth` in its constructor so swapping to
Kira Cloud (a future hosted proxy) is a config flip, not a refactor. See
plan section 3e.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel

ProviderKey = Literal["tmdb", "tvdb", "anidb", "musicbrainz", "acoustid"]


# KI-13: shared User-Agent string. Originally lived in anidb.py because
# AniDB rejects the default `python-httpx/0.x` UA with a 403. We hit the
# same risk on GitHub's raw CDN (which serves the Fribb anime-list dump
# in anime_mappings.py) — raw.githubusercontent.com is known to
# rate-limit / 403 the default Python UA when aggregate traffic spikes.
# Hoisted here so every external HTTP client in providers/ can adopt the
# same defensive identifier without duplicating the string.
KIRA_USER_AGENT = "kira/0.5.0 (+https://github.com/Nicolaslahri/Kira)"


# ─────────────────────────────────────────────────────────────────────
# Exception hierarchy — providers raise these so the matcher can decide
# whether to retry or give up.
# ─────────────────────────────────────────────────────────────────────


class ProviderError(Exception):
    """Base for any provider-side failure."""


class ProviderTransientError(ProviderError):
    """Recoverable — caller should back off and retry.

    Use for: 5xx responses, timeouts, connection resets, 429 rate-limit,
    DNS hiccups, mid-flight network drops. The matcher retries with
    exponential backoff (1s/2s/4s + jitter). If all retries fail the
    file is still marked `no_match` but the retry buys us through
    transient AWS/TVDB blips that used to permanently brand files wrong.
    """


class ProviderPermanentError(ProviderError):
    """Don't retry — the request is structurally invalid.

    Use for: 4xx other than 429 (auth failure, invalid ID, malformed
    payload). Retrying won't help; the caller should fall through to the
    next provider or surface the error.
    """


class ProviderMode(str, Enum):
    DIRECT = "direct"  # User talks to provider API with their own key
    CLOUD = "cloud"    # User talks to Kira Cloud proxy with subscription token


class ProviderConfig(BaseModel):
    """Persisted per-provider config (stored as JSON in `settings` table)."""

    mode: ProviderMode = ProviderMode.DIRECT
    api_key: str | None = None         # used when mode == DIRECT
    cloud_token: str | None = None      # used when mode == CLOUD
    cloud_base_url: str | None = None   # override for self-hosted Kira Cloud, optional
    # AniDB-only: client name + version registered on anidb.net. Optional —
    # kept here (not on a per-provider subclass) so the factory has a single
    # config shape to read from. Other providers ignore these fields.
    anidb_client: str | None = None
    anidb_clientver: str | None = None


@dataclass(frozen=True)
class ProviderAuth:
    """How a provider authenticates requests. Set by the factory based on mode.

    Static auth: header_name/value or query_param/value. Used when the API
    accepts a key directly on every request (TMDB v3).

    Dynamic auth: credentials dict. The provider class reads it and runs
    whatever flow it needs (e.g. TVDB v4 login → bearer token cached).
    """

    header_name: str | None = None     # e.g. "Authorization"
    header_value: str | None = None    # e.g. "Bearer <token>"
    query_param: str | None = None     # e.g. "api_key" — TMDB v3 style
    query_value: str | None = None
    credentials: dict[str, str] | None = None  # provider-specific seed (e.g. {"apikey": "..."})


# ─────────────────────────────────────────────────────────────────────
# Result schemas — provider-agnostic. Each provider maps its native
# response into these so the matcher doesn't care where data came from.
# ─────────────────────────────────────────────────────────────────────


class MovieResult(BaseModel):
    provider: ProviderKey
    provider_id: str
    title: str
    year: int | None = None
    overview: str | None = None
    poster_url: str | None = None
    popularity: float | None = None
    aliases: list[str] | None = None  # alternate titles for disambiguation in Manual Search


class TVResult(BaseModel):
    provider: ProviderKey
    provider_id: str
    title: str
    year: int | None = None
    overview: str | None = None
    poster_url: str | None = None
    popularity: float | None = None
    # Optional disambiguation fields — used by the anime re-rank.
    # Only providers that have the data populate these (TVDB via /extended, AniDB always).
    aliases: list[str] | None = None
    original_language: str | None = None
    original_country: str | None = None


class EpisodeResult(BaseModel):
    provider: ProviderKey
    series_id: str
    season: int
    episode: int
    title: str | None = None
    air_date: str | None = None
    overview: str | None = None
    # Runtime in minutes for this specific episode. Surfaced on the popup
    # row next to the air date (e.g. "Jun 27, 2024 · 36 min"). All three
    # video providers expose this; we plumb it through verbatim.
    runtime: int | None = None
    # The provider's absolute episode number when distinct from the
    # season-local `episode` field. TVDB exposes this as `absoluteNumber`
    # for shows that maintain absolute numbering (One Piece, Naruto,
    # Detective Conan, Pokémon). AniDB doesn't need it because its native
    # season=1 schema makes `episode` already the absolute number. The
    # bipartite Pass 2 reads this to pair `parsed.absolute_episode=1158`
    # against TVDB's S23E5 row whose `absolute_number=1158` — without
    # this field, the only working absolute-numbered anime path was via
    # AniDB; during an AniDB ban the user would see mass orphans for
    # long-runners. None on providers that don't expose it.
    absolute_number: int | None = None


# ─────────────────────────────────────────────────────────────────────
# Abstract provider
# ─────────────────────────────────────────────────────────────────────


class MetadataProvider(ABC):
    """Base class for movie/TV/anime providers (text-based matching).

    Music uses a separate AudioProvider ABC because the matching model is
    different (fingerprint-based via AcoustID).
    """

    key: ProviderKey

    def __init__(self, base_url: str, auth: ProviderAuth, client: httpx.AsyncClient):
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.client = client

    def _auth_params(self) -> dict[str, str]:
        if self.auth.query_param and self.auth.query_value:
            return {self.auth.query_param: self.auth.query_value}
        return {}

    def _auth_headers(self) -> dict[str, str]:
        if self.auth.header_name and self.auth.header_value:
            return {self.auth.header_name: self.auth.header_value}
        return {}

    @abstractmethod
    async def search_movie(self, title: str, year: int | None = None) -> list[MovieResult]: ...

    @abstractmethod
    async def search_tv(self, title: str, year: int | None = None) -> list[TVResult]: ...

    @abstractmethod
    async def get_episodes(
        self, series_id: str, season: int, include_specials: bool = False,
        order: str = "default",
    ) -> list[EpisodeResult]: ...


# Default base URLs per provider. The factory uses these for DIRECT mode
# and substitutes the cloud URL for CLOUD mode.
DEFAULT_BASE_URLS: dict[ProviderKey, str] = {
    "tmdb":        "https://api.themoviedb.org/3",
    "tvdb":        "https://api4.thetvdb.com/v4",
    "anidb":       "http://api.anidb.net:9001/httpapi",
    "musicbrainz": "https://musicbrainz.org/ws/2",
    "acoustid":    "https://api.acoustid.org/v2",
}

DEFAULT_CLOUD_BASE_URL = "https://cloud.kira.app"  # placeholder until launch


class ProviderFactory(Protocol):
    """Type hint for the factory function."""

    def __call__(
        self, key: ProviderKey, config: ProviderConfig, client: httpx.AsyncClient
    ) -> MetadataProvider: ...
