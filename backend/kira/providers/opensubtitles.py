"""OpenSubtitles REST client — resolve a file's content hash to an identity.

Pairs with `_osdbhash`: hash the file, ask OpenSubtitles "what movie/episode is
this hash?", and get back a TMDB/IMDb id + title/year. That id then feeds the
existing embedded-ID match path, so a file with a completely garbage name still
lands on the right entry (Matching-completeness M5).

The modern REST API (`api.opensubtitles.com/api/v1`) requires an `Api-Key`
header. Everything here is **key-gated**: with no key configured the client is a
no-op (returns None), so nothing changes for users who don't opt in.

Response parsing is split into a pure `parse_identity()` so it can be tested
without any network.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from kira.providers.base import KIRA_USER_AGENT

_BASE_URL = "https://api.opensubtitles.com/api/v1"


def parse_identity(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a normalized identity from a `/subtitles` response.

    Prefers results where `moviehash_match` is true (the hash matched the exact
    release, so the identity is sync-guaranteed). Returns None when nothing
    usable is present.

    Normalized shape:
        {feature_type, title, year, imdb_id, tmdb_id, season_number, episode_number}
    """
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None

    def _details(entry: dict) -> dict | None:
        attrs = entry.get("attributes")
        if not isinstance(attrs, dict):
            return None
        fd = attrs.get("feature_details")
        return fd if isinstance(fd, dict) else None

    # Prefer an exact moviehash match; fall back to the first entry with details.
    chosen: dict | None = None
    for entry in data:
        if not isinstance(entry, dict):
            continue
        attrs = entry.get("attributes") or {}
        fd = _details(entry)
        if fd is None:
            continue
        if attrs.get("moviehash_match") is True:
            chosen = fd
            break
        if chosen is None:
            chosen = fd
    if chosen is None:
        return None

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    ident = {
        "feature_type": (chosen.get("feature_type") or "").lower() or None,
        "title": chosen.get("title") or chosen.get("parent_title"),
        "year": _int(chosen.get("year")),
        "imdb_id": _int(chosen.get("imdb_id")),
        "tmdb_id": _int(chosen.get("tmdb_id")),
        "season_number": _int(chosen.get("season_number")),
        "episode_number": _int(chosen.get("episode_number")),
    }
    # Require at least one resolvable id or a title — else it's useless.
    if not (ident["tmdb_id"] or ident["imdb_id"] or ident["title"]):
        return None
    return ident


class OpenSubtitlesClient:
    """Thin async client. No-op (returns None) when no api_key is configured."""

    def __init__(self, api_key: str | None, client: httpx.AsyncClient,
                 app_name: str | None = None):
        self.api_key = (api_key or "").strip()
        self.client = client
        # OpenSubtitles requires a descriptive, app-identifying User-Agent.
        self.app_name = app_name or KIRA_USER_AGENT

    async def identify_by_hash(self, moviehash: str, bytesize: int | None = None) -> dict[str, Any] | None:
        """Query `/subtitles?moviehash=...` and return a normalized identity.

        Returns None on: no api_key, network/HTTP error, or no usable result.
        Never raises.
        """
        if not self.api_key or not moviehash:
            return None
        params: dict[str, str] = {"moviehash": moviehash}
        if bytesize:
            params["moviebytesize"] = str(bytesize)
        try:
            r = await self.client.get(
                f"{_BASE_URL}/subtitles",
                params=params,
                headers={
                    "Api-Key": self.api_key,
                    # OpenSubtitles requires a descriptive, app-identifying UA.
                    "User-Agent": self.app_name,
                    "Accept": "application/json",
                },
                timeout=20.0,
            )
            r.raise_for_status()
            return parse_identity(r.json())
        except Exception as e:  # network / decode / HTTP — degrade gracefully
            print(f"opensubtitles: identify_by_hash failed: {e!r}")
            return None


async def identify_file_by_hash(
    path: str | os.PathLike,
    api_key: str | None,
    client: httpx.AsyncClient,
) -> dict[str, Any] | None:
    """End-to-end: hash the file, ask OpenSubtitles, return the identity (with
    the computed `moviehash` attached). None when the file can't be hashed, no
    key is set, or nothing matched."""
    from kira.providers._osdbhash import compute_osdb_hash

    moviehash = compute_osdb_hash(path)
    if not moviehash:
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    ident = await OpenSubtitlesClient(api_key, client).identify_by_hash(moviehash, size)
    if ident:
        ident["moviehash"] = moviehash
    return ident
