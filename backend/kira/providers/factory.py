"""Provider factory — reads a ProviderConfig and builds the right provider
with the right base_url + auth for either DIRECT or CLOUD mode.

This is the single seam through which the eventual Kira Cloud proxy will
be supported. The provider classes themselves never know whether they're
talking to TMDB directly or to our proxy.
"""

from __future__ import annotations

import httpx

from kira.providers.base import (
    DEFAULT_BASE_URLS,
    DEFAULT_CLOUD_BASE_URL,
    MetadataProvider,
    ProviderAuth,
    ProviderConfig,
    ProviderKey,
    ProviderMode,
)


def _direct_auth(key: ProviderKey, config: ProviderConfig) -> ProviderAuth:
    """Auth for talking directly to the provider with the user's own key.

    AniDB is special: read-only HTTP API needs only a client name/version,
    NOT a per-user API key. Pulls the user's registered identifiers from
    `ProviderConfig` so each instance uses the right credentials — no
    monkey-patching from the matcher.
    """
    api_key = config.api_key
    if key == "tmdb":
        # TMDB v3 uses query param `api_key`. api_key is guaranteed non-empty
        # by the build_provider precheck.
        return ProviderAuth(query_param="api_key", query_value=api_key)
    if key == "tvdb":
        # TVDB v4 needs a login exchange first — provider handles it lazily.
        return ProviderAuth(credentials={"apikey": api_key})
    if key == "anidb":
        # AniDB read-only HTTP API. Fall back to "kira"/"1" only if the user
        # hasn't registered their own client yet.
        client_name = config.anidb_client or "kira"
        client_ver = config.anidb_clientver or "1"
        return ProviderAuth(credentials={"client": client_name, "clientver": client_ver})
    # MusicBrainz / AcoustID — added when implemented.
    return ProviderAuth()


def _cloud_auth(cloud_token: str) -> ProviderAuth:
    """Auth for talking to Kira Cloud proxy. Single bearer token for all providers."""
    return ProviderAuth(header_name="Authorization", header_value=f"Bearer {cloud_token}")


def _resolve_base_url(key: ProviderKey, config: ProviderConfig) -> str:
    if config.mode == ProviderMode.CLOUD:
        base = config.cloud_base_url or DEFAULT_CLOUD_BASE_URL
        return f"{base.rstrip('/')}/{key}"
    return DEFAULT_BASE_URLS[key]


# Static map of which providers are wired into build_provider — used by the
# /providers discovery endpoint to know what's "implemented".
IMPLEMENTED_PROVIDERS: set[ProviderKey] = {"tmdb", "tvdb", "anidb"}

# Providers that work without any user-supplied key.
# - anidb: read-only HTTP API; client+ver are bundled.
# - musicbrainz: open API, only requires a descriptive User-Agent.
# AcoustID DOES need a per-app key, so it stays out of this set.
KEYLESS_PROVIDERS: set[ProviderKey] = {"anidb", "musicbrainz"}


def build_provider(
    key: ProviderKey,
    config: ProviderConfig,
    client: httpx.AsyncClient,
) -> MetadataProvider:
    """Construct a provider for the given key, wired for the configured mode."""
    base_url = _resolve_base_url(key, config)

    if config.mode == ProviderMode.CLOUD:
        if not config.cloud_token:
            raise ValueError(f"{key}: cloud mode selected but no cloud_token configured")
        auth = _cloud_auth(config.cloud_token)
    else:
        # Keyless providers (AniDB read-only, MusicBrainz) skip the api_key check.
        if key not in KEYLESS_PROVIDERS and not config.api_key:
            raise ValueError(f"{key}: direct mode selected but no api_key configured")
        auth = _direct_auth(key, config)

    # Lazy import to avoid pulling all providers into every call.
    if key == "tmdb":
        from kira.providers.tmdb import TMDBProvider
        return TMDBProvider(base_url=base_url, auth=auth, client=client)
    if key == "tvdb":
        from kira.providers.tvdb import TVDBProvider
        return TVDBProvider(base_url=base_url, auth=auth, client=client)
    if key == "anidb":
        from kira.providers.anidb import AniDBProvider
        return AniDBProvider(base_url=base_url, auth=auth, client=client)

    raise NotImplementedError(f"Provider not implemented yet: {key}")
