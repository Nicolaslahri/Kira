"""Provider discovery — tells the frontend which providers are implemented
and which have working credentials, so the Manual Search modal can render
disabled tabs with the right call-to-action.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from kira.matcher.engine import registry_from_settings
from kira.providers.factory import IMPLEMENTED_PROVIDERS, KEYLESS_PROVIDERS

router = APIRouter(prefix="/providers", tags=["providers"])

# Music providers whose feature IS live but is served by the standalone
# kira.music pipeline (match_album), NOT by build_provider — so they're absent
# from factory.IMPLEMENTED_PROVIDERS (that set means "build_provider supports
# it"). Listed here so the discovery endpoint reports them as implemented and
# their Connections cards read "Connected" instead of "Coming soon".
#  - musicbrainz: keyless (User-Agent only).
#  - acoustid: ships a bundled app key (acoustid.PROJECT_KEY), so it's always
#    usable out of the box; a personal providers.acoustid.api_key is optional.
_FEATURE_IMPLEMENTED: set[str] = {"musicbrainz", "acoustid"}


class ProviderInfo(BaseModel):
    key: str                  # 'tmdb' | 'tvdb' | 'anidb' | 'musicbrainz' | 'acoustid'
    name: str                 # display name
    implemented: bool         # backend can build + call this provider
    configured: bool          # has a working credential (or is keyless)
    keyless: bool             # true when no user key is needed (e.g. AniDB read-only)
    supports: list[str]       # ['movie' | 'tv' | 'anime' | 'music']
    note: str | None = None   # short user-facing description
    # Provider-specific status fields. All optional; only set when relevant.
    rate_limited: bool = False          # true when the provider is throttling us
    banned_until: float | None = None   # Unix timestamp of ban expiry, if known
    last_error: str | None = None       # most recent error string (diagnostics)
    fallback_chain: list[str] | None = None  # provider keys we fall back to when this one is unavailable


_CATALOGUE: list[dict] = [
    {"key": "tmdb",        "name": "TMDB",        "supports": ["movie", "tv"],
     "note": "Movies + TV. The gold standard for English-language libraries."},
    {"key": "tvdb",        "name": "TheTVDB",     "supports": ["tv", "anime"],
     "note": "Deep TV metadata with strong support for absolute episode numbering."},
    {"key": "anidb",       "name": "AniDB",       "supports": ["anime"],
     "note": "The canonical anime source — episodes, groups, alternate titles."},
    {"key": "musicbrainz", "name": "MusicBrainz", "supports": ["music"],
     "note": "Open music encyclopedia — artists, releases, recordings."},
    {"key": "acoustid",    "name": "AcoustID",    "supports": ["music"],
     "note": "Audio fingerprint matching for files with missing or wrong tags."},
]


# Fallback chain shown in the UI tooltip + used by the matcher's
# PROVIDER_PREFERENCE table. Mirrors what's actually wired in
# kira/matcher/engine.py:78 — keep them in sync.
_FALLBACK_CHAINS: dict[str, list[str]] = {
    "tmdb":  ["tvdb"],            # if TMDB is unreachable, try TVDB
    "tvdb":  ["tmdb"],            # if TVDB is unreachable, try TMDB
    "anidb": ["tvdb", "tmdb"],    # if AniDB is banned, fall through to TVDB then TMDB
}


@router.get("", response_model=list[ProviderInfo])
async def list_providers() -> list[ProviderInfo]:
    """Return one row per known provider with implementation + configuration status.

    For AniDB specifically, surfaces the ban-state countdown (so the
    Settings UI can show "Throttled — unbans in 2h 14m" instead of
    silently degrading). Other providers return the basic 3-field shape
    they always did.
    """
    out: list[ProviderInfo] = []
    # Use a dummy client just so registry.has() can do its check — we never
    # actually issue a request here.
    async with httpx.AsyncClient() as client:
        registry = await registry_from_settings(client)
        for entry in _CATALOGUE:
            key = entry["key"]
            implemented = key in IMPLEMENTED_PROVIDERS or key in _FEATURE_IMPLEMENTED
            if key == "acoustid":
                # Bundled app key → configured out of the box (personal key optional).
                configured = implemented
            elif key in KEYLESS_PROVIDERS:
                # No user credential needed → usable as soon as it's implemented.
                configured = implemented
            else:
                configured = implemented and registry.has(key)  # type: ignore[arg-type]
            info = ProviderInfo(
                key=key,
                name=entry["name"],
                implemented=implemented,
                configured=configured,
                keyless=key in KEYLESS_PROVIDERS,
                supports=entry["supports"],
                note=entry["note"],
                fallback_chain=_FALLBACK_CHAINS.get(key),
            )
            # AniDB-specific status enrichment: read the persisted ban
            # state. Pure file-read, no HTTP. Surfaces "we know it's
            # rate-limited" to the UI so the user sees a countdown
            # instead of mysteriously-broken episode lookups.
            if key == "anidb":
                try:
                    from kira.providers.anidb import AniDBProvider
                    AniDBProvider._load_ban_state()
                    if AniDBProvider.is_banned():
                        info.rate_limited = True
                        info.banned_until = AniDBProvider._banned_until
                    info.last_error = AniDBProvider._last_error
                except Exception:
                    pass
            out.append(info)
    return out
