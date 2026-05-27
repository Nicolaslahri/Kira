"""Settings + provider connection-test endpoints."""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kira.config import settings as app_settings
from kira.database import get_session
from kira.matcher.engine import registry_from_settings
from kira.models import Setting
from kira.providers.base import ProviderKey
from kira.schemas import ProviderTestResponse, SettingsBody

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=dict[str, Any])
async def get_settings(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Return all stored settings as a flat dict. Sensitive keys (API keys) are
    masked client-side; full values stay server-only."""
    rows = list(await session.scalars(select(Setting)))
    out: dict[str, Any] = {row.key: row.value for row in rows}
    # Surface env-bootstrapped keys too, but masked, so the UI can tell whether
    # a provider has any key configured without exposing the actual value.
    if app_settings.tmdb_api_key and "providers.tmdb.api_key" not in out:
        out["providers.tmdb.api_key"] = {"masked": True, "tail": app_settings.tmdb_api_key[-4:]}
    if app_settings.tvdb_api_key and "providers.tvdb.api_key" not in out:
        out["providers.tvdb.api_key"] = {"masked": True, "tail": app_settings.tvdb_api_key[-4:]}
    return out


@router.put("", response_model=dict[str, int])
async def put_settings(
    payload: SettingsBody,
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Bulk upsert settings keys."""
    n = 0
    for key, value in payload.values.items():
        existing = await session.get(Setting, key)
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

    return {"updated": n}


@router.post("/providers/{provider}/test", response_model=ProviderTestResponse)
async def test_provider(provider: ProviderKey) -> ProviderTestResponse:
    """Actually call the provider with the configured credentials and report ok/error.

    For TMDB: hits /configuration. For TVDB: hits /login + /search?q=test.
    """
    async with httpx.AsyncClient() as client:
        registry = await registry_from_settings(client)
        if not registry.has(provider):
            return ProviderTestResponse(ok=False, detail=f"{provider} has no API key configured")
        try:
            p = registry.build(provider)
        except ValueError as e:
            return ProviderTestResponse(ok=False, detail=str(e))

        t0 = time.monotonic()
        try:
            # Cheap noop search — exercises auth + the search endpoint.
            if provider in ("tmdb", "tvdb"):
                await p.search_tv("test")
            elif provider == "anidb":
                await p.search_tv("test")
            else:
                # Other providers not implemented yet.
                return ProviderTestResponse(ok=False, detail=f"{provider} test not implemented yet")
        except httpx.HTTPStatusError as e:
            return ProviderTestResponse(ok=False, detail=f"HTTP {e.response.status_code}")
        except Exception as e:
            return ProviderTestResponse(ok=False, detail=str(e))
        latency_ms = int((time.monotonic() - t0) * 1000)
        return ProviderTestResponse(ok=True, latency_ms=latency_ms)
