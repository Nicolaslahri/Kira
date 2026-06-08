"""Settings + provider connection-test endpoints."""

from __future__ import annotations

import time
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kira.config import settings as app_settings
from kira.database import get_session
from kira.matcher.engine import registry_from_settings
from kira.models import Setting
from kira.schemas import ProviderTestResponse, SettingsBody

router = APIRouter(prefix="/settings", tags=["settings"])


# Settings whose VALUE is a secret and must never leave the server in plaintext
# — API keys, passwords, shared tokens, client secrets. Matched as a substring
# of the (lower-cased) key name so every provider/integration variant is caught.
# Kept deliberately broad to catch future credential-bearing keys, but each
# marker is unambiguous enough not to false-positive on a NON-secret key (note
# we avoid bare "key"/"auth"/"pat" — "pat" would mask "paths.library_root").
_SECRET_MARKERS = (
    "api_key", "apikey", "password", "passwd", "secret", "token",
    "client_secret", "client_key", "cookie", "bearer", "credential",
    "private_key", "access_key",
)


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return any(marker in k for marker in _SECRET_MARKERS)


def _masked(raw: Any) -> dict[str, Any]:
    """A masked stand-in that proves a secret is configured (and exposes only
    the last 4 chars as a fingerprint) without ever returning the plaintext.
    Shape matches what the frontend already renders for env-bootstrapped keys."""
    val = raw.get("value") if isinstance(raw, dict) else raw
    tail = val[-4:] if isinstance(val, str) and len(val) >= 4 else ""
    return {"masked": True, "tail": tail, "set": bool(val)}


def _looks_like_mask(value: Any) -> bool:
    """True if an incoming PUT value is actually a mask, not a real secret — the
    {"masked": true,...} object OR the bullet placeholder string the UI shows
    (`•••• •••• •••• abcd`). Real keys are never a masked-dict and never contain
    the U+2022 bullet, so this safely rejects a settings round-trip that would
    otherwise clobber the stored secret with its own mask."""
    if isinstance(value, dict) and value.get("masked") is True:
        return True
    return isinstance(value, str) and "•" in value


@router.get("", response_model=dict[str, Any])
async def get_settings(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Return all stored settings as a flat dict. Secret values (API keys,
    passwords, tokens) are MASKED server-side — the plaintext never leaves the
    process. Internal consumers (matcher registry, webhook auth) read the raw
    rows directly, so masking the API response changes nothing functionally."""
    rows = list(await session.scalars(select(Setting)))
    out: dict[str, Any] = {
        row.key: (_masked(row.value) if _is_secret_key(row.key) else row.value)
        for row in rows
    }
    # Surface env-bootstrapped keys too (masked), so the UI can tell a provider
    # is configured without exposing the value.
    if app_settings.tmdb_api_key and "providers.tmdb.api_key" not in out:
        out["providers.tmdb.api_key"] = _masked(app_settings.tmdb_api_key)
    if app_settings.tvdb_api_key and "providers.tvdb.api_key" not in out:
        out["providers.tvdb.api_key"] = _masked(app_settings.tvdb_api_key)
    return out


@router.put("", response_model=dict[str, int])
async def put_settings(
    payload: SettingsBody,
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Bulk upsert settings keys."""
    n = 0
    # Capture the PRIOR value of the MediaInfo toggles (only when present in this
    # payload) so we can tell a genuine OFF→ON flip — which should backfill the
    # existing library — from a no-op re-save of an already-on setting.
    _mi_old: dict[str, Any] = {}
    for key, value in payload.values.items():
        # Never overwrite a stored secret with its own mask: a client that GETs
        # masked secrets and PUTs the whole settings object back would otherwise
        # clobber the real key with the bullet placeholder / {"masked": true}.
        if _is_secret_key(key) and _looks_like_mask(value):
            continue
        existing = await session.get(Setting, key)
        if key in ("parsing.read_mediainfo", "parsing.mediainfo_authoritative"):
            _mi_old[key] = existing.value if existing is not None else None
        if existing is None:
            session.add(Setting(key=key, value=value))
        else:
            existing.value = value
        n += 1
    await session.commit()

    # Invalidate the matcher's in-memory settings cache so the registry's
    # next build sees the new values (e.g. a freshly-pasted API key).
    from kira.matcher.engine import invalidate_settings_cache
    invalidate_settings_cache()

    # If the user just updated AniDB client/version, lift the "rejected"
    # short-circuit so the next picture request actually retries with the
    # new identifiers (instead of returning null cached from the prior
    # 302 response).
    anidb_touched = any(k in payload.values for k in (
        "providers.anidb.client", "providers.anidb.clientver",
    ))
    if anidb_touched:
        from kira.providers.anidb import AniDBProvider
        AniDBProvider.reset_rejection()

    # Force-IPv4 toggle: apply immediately so the user doesn't have to restart
    # to escape (or re-enter) IPv6 resolution.
    if "network.force_ipv4" in payload.values:
        try:
            from kira import net
            v = payload.values["network.force_ipv4"]
            if isinstance(v, dict):
                v = v.get("value")
            if isinstance(v, bool):
                net.set_force_ipv4(v)
        except Exception as e:
            print(f"settings: force_ipv4 apply failed: {e!r}")

    # Watched-folders: if the watch config or the scanned paths changed,
    # re-arm the daemon so the new settings take effect without a restart.
    watch_touched = any(k in payload.values for k in (
        "watch.config", "paths.library_root", "paths.watch_folders",
    ))
    if watch_touched:
        try:
            from kira.watcher import watcher
            await watcher.reconfigure()
        except Exception as e:
            print(f"settings: watcher reconfigure failed: {e!r}")

    # MediaInfo: turning the read on — or turning on authoritative while read is
    # already on — should enrich the EXISTING library, not just files found by
    # future scans. Otherwise the user flips the toggle and nothing visibly
    # happens. Kick off the detached background pass (paced; shows the activity
    # pill) over every current file. Fires ONLY on a real OFF→ON flip of a key
    # actually present in this payload, so an unrelated save / whole-object PUT
    # never re-triggers a full re-read.
    if _mi_old:
        try:
            from kira.api.scans import (
                _read_mediainfo_setting,
                _read_mediainfo_authoritative_setting,
                _spawn_mediainfo_enrich,
            )
            from kira.models import MediaFile

            read_now = await _read_mediainfo_setting(session)
            auth_now = await _read_mediainfo_authoritative_setting(session)
            read_on = (
                "parsing.read_mediainfo" in _mi_old
                and read_now and not bool(_mi_old["parsing.read_mediainfo"])
            )
            auth_on = (
                "parsing.mediainfo_authoritative" in _mi_old
                and auth_now and not bool(_mi_old["parsing.mediainfo_authoritative"])
            )
            if read_now and (read_on or auth_on):
                all_ids = list((await session.scalars(select(MediaFile.id))).all())
                _spawn_mediainfo_enrich(all_ids, reason="settings")
        except Exception as e:
            print(f"settings: mediainfo backfill kick-off failed (non-fatal): {e!r}")

    return {"updated": n}


@router.post("/providers/{provider}/test", response_model=ProviderTestResponse)
async def test_provider(
    provider: Literal["tmdb", "tvdb", "anidb", "fanarttv"],
    session: AsyncSession = Depends(get_session),
) -> ProviderTestResponse:
    """Actually call the provider with the configured credentials and report ok/error.

    For TMDB: hits /configuration. For TVDB: hits /login + /search?q=test.
    fanart.tv is artwork-only (not in the matcher registry), so it's tested by
    pinging its API with the saved key. (`provider` is a free string rather than
    the matcher `ProviderKey` enum precisely so artwork-only sources fit here.)
    """
    # fanart.tv — artwork provider, tested against its own API.
    if provider == "fanarttv":
        from kira.providers import fanarttv
        row = await session.get(Setting, "providers.fanarttv.api_key")
        key = row.value if row else None
        if isinstance(key, dict):           # tolerate a {"value": …} wrapper
            key = key.get("value")
        t0 = time.monotonic()
        async with httpx.AsyncClient() as client:
            ok, detail = await fanarttv.test_key(key if isinstance(key, str) else None, client)
        return ProviderTestResponse(
            ok=ok, detail=detail,
            latency_ms=int((time.monotonic() - t0) * 1000) if ok else None,
        )

    async with httpx.AsyncClient() as client:
        registry = await registry_from_settings(client)
        if not registry.has(provider):
            return ProviderTestResponse(ok=False, detail=f"{provider} has no API key configured")
        try:
            p = registry.build(provider)
        except ValueError as e:
            return ProviderTestResponse(ok=False, detail=str(e))

        if provider not in ("tmdb", "tvdb", "anidb"):
            # Other providers not implemented yet.
            return ProviderTestResponse(ok=False, detail=f"{provider} test not implemented yet")

        t0 = time.monotonic()
        try:
            # Cheap noop search — exercises auth + the search endpoint. Wrapped
            # in the SAME retry as real matching so a single flaky connect
            # (e.g. TMDB's ~10% IPv4 connect-drops) reports the true state
            # ("reachable") instead of a misleading one-shot "test failed".
            from kira.matcher.engine import _provider_call_with_retry
            await _provider_call_with_retry(lambda: p.search_tv("test"), what=f"{provider}.test")
        except httpx.HTTPStatusError as e:
            return ProviderTestResponse(ok=False, detail=f"HTTP {e.response.status_code}")
        except Exception as e:
            return ProviderTestResponse(ok=False, detail=str(e))
        latency_ms = int((time.monotonic() - t0) * 1000)
        return ProviderTestResponse(ok=True, latency_ms=latency_ms)
