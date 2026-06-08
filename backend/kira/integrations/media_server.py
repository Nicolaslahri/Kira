"""Plex / Jellyfin library-refresh integration (Pass 6 #9).

After Kira renames a batch, the media server still shows the old paths until
its next scheduled scan. A one-shot refresh request makes the changes appear
immediately. Both are best-effort: a misconfigured or unreachable server logs
and moves on — a refresh failure must NEVER fail or delay a rename.

Settings (read via the standard Setting table):
  integrations.plex.url        e.g. "http://plex:32400"
  integrations.plex.token      X-Plex-Token (Settings → account → "Get token")
  integrations.jellyfin.url    e.g. "http://jellyfin:8096"
  integrations.jellyfin.api_key  Jellyfin API key (Dashboard → API Keys)

Per-call short-lived httpx clients (same discipline as integrations/sonarr.py):
a bad base_url can't poison a shared pool.
"""
from __future__ import annotations

import logging

import httpx

from kira.database import SessionLocal
from kira.models import Setting
from kira.settings_store import unwrap_str as _unwrap  # canonical settings-value unwrap
from kira.url_guard import is_safe_outbound_url

_log = logging.getLogger("kira.media_server")

_TIMEOUT = 8.0


async def refresh_plex(url: str, token: str) -> bool:
    """Trigger a full Plex library refresh (all sections). Returns True on 2xx.

    Uses the documented `GET /library/sections/all/refresh` with the token as a
    header. Never raises."""
    ok_url, reason = is_safe_outbound_url(url)
    if not ok_url:
        _log.warning("plex refresh: URL rejected by SSRF guard (%s)", reason)
        return False
    base = url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                f"{base}/library/sections/all/refresh",
                headers={"X-Plex-Token": token, "Accept": "application/json"},
            )
            ok = r.status_code < 400
            if not ok:
                _log.warning("plex refresh: HTTP %s", r.status_code)
            return ok
    except Exception as e:  # noqa: BLE001 — best-effort
        _log.warning("plex refresh failed: %r", e)
        return False


async def refresh_jellyfin(url: str, api_key: str) -> bool:
    """Trigger a full Jellyfin library refresh. Returns True on 2xx.

    `POST /Library/Refresh` with the key in the `X-Emby-Token` header (Jellyfin
    inherits Emby's auth header). Never raises."""
    ok_url, reason = is_safe_outbound_url(url)
    if not ok_url:
        _log.warning("jellyfin refresh: URL rejected by SSRF guard (%s)", reason)
        return False
    base = url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                f"{base}/Library/Refresh",
                headers={"X-Emby-Token": api_key, "Accept": "application/json"},
            )
            ok = r.status_code < 400
            if not ok:
                _log.warning("jellyfin refresh: HTTP %s", r.status_code)
            return ok
    except Exception as e:  # noqa: BLE001 — best-effort
        _log.warning("jellyfin refresh failed: %r", e)
        return False


async def refresh_all(session=None) -> list[str]:
    """Refresh every configured media server. Returns the names refreshed
    (for logging / notification). Best-effort: reads settings, fires whichever
    of Plex / Jellyfin are configured, never raises.

    `session` is optional — when omitted (e.g. called from a background task
    whose request session is gone) a fresh one is opened."""
    own = session is None
    if own:
        session = SessionLocal()
        await session.__aenter__()
    try:
        async def _get(key: str) -> str | None:
            row = await session.get(Setting, key)
            return _unwrap(row.value) if row is not None else None

        plex_url = await _get("integrations.plex.url")
        plex_token = await _get("integrations.plex.token")
        jf_url = await _get("integrations.jellyfin.url")
        jf_key = await _get("integrations.jellyfin.api_key")
    finally:
        if own:
            await session.__aexit__(None, None, None)

    refreshed: list[str] = []
    if plex_url and plex_token and await refresh_plex(plex_url, plex_token):
        refreshed.append("Plex")
    if jf_url and jf_key and await refresh_jellyfin(jf_url, jf_key):
        refreshed.append("Jellyfin")
    if refreshed:
        _log.info("media-server refresh fired: %s", ", ".join(refreshed))
    return refreshed
