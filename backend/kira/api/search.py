"""Provider search endpoint — backs the Manual Search modal."""

from __future__ import annotations

from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException

from kira.matcher.engine import registry_from_settings
from kira.providers.base import ProviderKey

router = APIRouter(prefix="/search", tags=["search"])

SearchType = Literal["movie", "tv", "auto"]


@router.get("/anidb/picture/{aid}")
async def anidb_picture(aid: str) -> dict[str, str | None]:
    """Look up the AniDB CDN poster URL for one anime by AID.

    Rate-limited (1 req / 4s) on first lookup; cached on disk after.
    Frontend Manual Search fires one of these per visible result.

    Returns `{aid, picture_url, error}`. picture_url is null when the AniDB
    HTTP API rejects us (most often: the client/clientver pair isn't a
    registered AniDB client). `error` carries the AniDB-reported reason so
    the UI can show a one-time banner.
    """
    from kira.providers.anidb import AniDBProvider
    async with httpx.AsyncClient() as client:
        registry = await registry_from_settings(client)
        if not registry.has("anidb"):
            raise HTTPException(400, "AniDB is not configured.")
        p = registry.build("anidb")
        try:
            url = await p.get_picture_url(aid)  # type: ignore[attr-defined]
        except Exception as e:
            raise HTTPException(502, f"AniDB picture lookup failed: {e}") from e

    # Classify error state for the frontend banner. We ONLY report a
    # problem when the lookup actually failed (url is None). If the cross-
    # reference path returned a TVDB/TMDB poster, the user doesn't need to
    # know AniDB itself is banned — they're getting art either way.
    if url:
        return {"aid": aid, "picture_url": url, "error": None, "error_kind": None}

    error_msg = AniDBProvider._last_error
    error_kind: str | None = None
    if AniDBProvider.is_banned():
        error_kind = "banned"
    elif AniDBProvider._client_rejected:
        error_kind = "rejected"
    elif error_msg:
        error_kind = "error"
    return {
        "aid": aid,
        "picture_url": None,
        "error": error_msg,
        "error_kind": error_kind,
    }


@router.get("/{provider}")
async def search_provider(
    provider: ProviderKey,
    q: str = "",
    type: SearchType = "auto",
) -> dict[str, Any]:
    """Search a single provider for the given query.

    Returns a flat list of results in a frontend-agnostic shape so the
    Manual Search modal can render them per provider variant.
    """
    if not q.strip():
        return {"provider": provider, "results": []}

    # MusicBrainz is keyless and lives in its own client (kira.music.musicbrainz),
    # NOT the MetadataProvider registry — so route it directly instead of through
    # registry.build(), which would raise NotImplementedError. This is what made
    # the modal wrongly say "MusicBrainz needs an API key": /providers reported it
    # configured (keyless) but the search path couldn't build it.
    if provider == "musicbrainz":
        return await _search_musicbrainz(q.strip())

    async with httpx.AsyncClient() as client:
        registry = await registry_from_settings(client)
        if not registry.has(provider):
            raise HTTPException(
                400,
                f"{provider} is not configured. Add an API key in Settings → Connections.",
            )

        try:
            p = registry.build(provider)
        except (ValueError, NotImplementedError) as e:
            raise HTTPException(400, str(e)) from e

        results: list[dict[str, Any]] = []

        # Decide what to search. For "auto" we hit both endpoints for video
        # providers; music/anime providers only have one.
        try:
            if provider in ("tmdb", "tvdb") and type in ("auto", "tv"):
                tv = await p.search_tv(q)
                for r in tv:
                    results.append(_to_dict(r, "tv"))
            if provider in ("tmdb", "tvdb") and type in ("auto", "movie"):
                movie = await p.search_movie(q)
                for r in movie:
                    results.append(_to_dict(r, "movie"))
            if provider == "anidb" and type in ("auto", "tv"):
                tv = await p.search_tv(q)
                for r in tv:
                    results.append(_to_dict(r, "anime"))
        except Exception as e:
            raise HTTPException(502, f"{provider} request failed: {e}") from e

    return {"provider": provider, "results": results}


async def _search_musicbrainz(q: str) -> dict[str, Any]:
    """Free-text album/artist search against MusicBrainz for the Manual Search
    modal. Keyless — needs only a descriptive User-Agent (the shared client
    supplies it). Maps release hits into the modal's result shape; the album
    MBID rides in `provider_id` so a manual pick round-trips like any other."""
    from kira.music import musicbrainz as mb

    async with httpx.AsyncClient() as client:
        try:
            # The box is one free-text field, so search it as the album title
            # (artist empty) — MusicBrainz's own relevance handles "Artist Album".
            hits = await mb.search_releases(client, artist="", album=q, limit=12)
        except Exception as e:  # noqa: BLE001 — surface as a clean 502, never 500
            raise HTTPException(502, f"MusicBrainz request failed: {e}") from e

    results: list[dict[str, Any]] = []
    for h in hits:
        # Cover Art Archive front thumbnail (allow-listed host); 404s fall back
        # to the placeholder card client-side, so it's safe to always offer.
        poster = f"https://coverartarchive.org/release/{h.id}/front-250" if h.id else None
        results.append({
            "provider_id": h.id,
            "title": h.title,
            "year": _year_of(h.date),
            "overview": h.artist or None,
            "poster_url": poster,
            "popularity": h.score,
            "media_type": "music",
            "aliases": None,
        })
    return {"provider": "musicbrainz", "results": results}


def _year_of(date: str | None) -> int | None:
    if not date:
        return None
    try:
        return int(date[:4])
    except (ValueError, TypeError):
        return None


def _to_dict(r: Any, media_type: str) -> dict[str, Any]:
    # Cap aliases at the most-useful first few — Manual Search renders 2 max.
    aliases = getattr(r, "aliases", None)
    if aliases:
        aliases = list(aliases)[:5]
    return {
        "provider_id": r.provider_id,
        "title": r.title,
        "year": r.year,
        "overview": r.overview,
        "poster_url": r.poster_url,
        "popularity": getattr(r, "popularity", None),
        "media_type": media_type,
        "aliases": aliases,
    }
