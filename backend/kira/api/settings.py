"""Settings + provider connection-test endpoints."""

from __future__ import annotations

import logging

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
from kira.schemas import ProviderTestBody, ProviderTestResponse, SettingsBody

logger = logging.getLogger(__name__)

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


def _masked(raw: Any, *, fingerprint: bool = True) -> dict[str, Any]:
    """A masked stand-in that proves a secret is configured (and, by default,
    exposes only the last 4 chars as a fingerprint so the UI can show '…abcd' for
    an API key) without ever returning the plaintext. Shape matches what the
    frontend already renders for env-bootstrapped keys.

    Pass ``fingerprint=False`` for values where even a 4-char tail is too much —
    e.g. a password HASH, whose trailing base64 chars are a needless credential-
    adjacent leak (and let two installs be compared for a shared password)."""
    val = raw.get("value") if isinstance(raw, dict) else raw
    tail = val[-4:] if (fingerprint and isinstance(val, str) and len(val) >= 4) else ""
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
        row.key: (
            _masked(row.value, fingerprint="password_hash" not in row.key.lower())
            if _is_secret_key(row.key) else row.value
        )
        for row in rows
    }
    # Surface env-bootstrapped keys too (masked), so the UI can tell a provider
    # is configured without exposing the value.
    if app_settings.tmdb_api_key and "providers.tmdb.api_key" not in out:
        out["providers.tmdb.api_key"] = _masked(app_settings.tmdb_api_key)
    if app_settings.tvdb_api_key and "providers.tvdb.api_key" not in out:
        out["providers.tvdb.api_key"] = _masked(app_settings.tvdb_api_key)
    return out


@router.get("/persistence", response_model=dict[str, Any])
async def get_persistence(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Whether match-identity stamps can live ON the media files themselves
    (xattr / NTFS ADS) or fall back to Kira's portable index. Probes the
    configured library root with a throwaway temp file — cheap, no lasting
    effect. The UI surfaces this on Settings → Paths so 'stamping silently
    no-ops on this volume' is visible instead of a mystery."""
    import asyncio as _asyncio
    import tempfile

    from kira import xattr_store

    root_row = await session.get(Setting, "paths.library_root")
    root = root_row.value if root_row else None
    if isinstance(root, dict) and "value" in root:
        root = root["value"]
    if not isinstance(root, str) or not root.strip():
        return {"root": None, "native": False, "mode": "index"}

    def _probe() -> bool:
        try:
            with tempfile.NamedTemporaryFile(
                dir=root, prefix=".kira-probe-", delete=False
            ) as fh:
                probe_path = fh.name
            try:
                return xattr_store.supported(probe_path)
            finally:
                try:
                    import os as _os
                    _os.remove(probe_path)
                except OSError:
                    pass
        except OSError:
            return False

    native = await _asyncio.to_thread(_probe)
    return {"root": root, "native": native, "mode": "native" if native else "index"}


@router.put("", response_model=dict[str, int])
async def put_settings(
    payload: SettingsBody,
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Bulk upsert settings keys."""
    from kira.settings_store import unwrap_str
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
        # EXPLICIT clear sentinel: {"clear": true} deletes the stored row
        # entirely, reverting the key to its bundled/env fallback. This is the
        # deliberate, unambiguous way to blank a secret (a bare '' is still
        # refused below as a likely client accident) — and the only way to get
        # BACK to a bundled provider key after saving a bad personal one.
        if isinstance(value, dict) and value.get("clear") is True:
            if existing is not None:
                await session.delete(existing)
                n += 1
            continue
        # Refuse to clear a CONFIGURED secret with a BLANK value. A masked field's
        # editable value is '' (the plaintext never leaves the server), so a stray
        # empty onChange/blur on the client would otherwise persist '' and the next
        # GET would re-mask it as set=false — i.e. the key "disappears after a
        # refresh". Rotating/clearing still works via an explicit non-empty value.
        if (
            _is_secret_key(key)
            and unwrap_str(value) is None
            and existing is not None
            and unwrap_str(existing.value) is not None
        ):
            continue
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
            from kira.settings_store import unwrap
            v = unwrap(payload.values["network.force_ipv4"])
            # Coerce the common string-toggle shape ("true"/"false"/"1"/"0") too,
            # not just a literal bool — otherwise a string value committed the row
            # but silently skipped the live apply, breaking the "no restart" promise.
            if isinstance(v, str):
                v = v.strip().lower() in ("1", "true", "yes", "on")
            net.set_force_ipv4(bool(v))
        except Exception as e:
            logger.warning(f"settings: force_ipv4 apply failed: {e!r}")

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
            logger.warning(f"settings: watcher reconfigure failed: {e!r}")

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
            from kira.settings_store import unwrap

            read_now = await _read_mediainfo_setting(session)
            auth_now = await _read_mediainfo_authoritative_setting(session)
            # unwrap the prior value the same way the readers now do, so the
            # OFF→ON detection can't misfire on a wrapped {"value": …} shape.
            read_on = (
                "parsing.read_mediainfo" in _mi_old
                and read_now and not bool(unwrap(_mi_old["parsing.read_mediainfo"]))
            )
            auth_on = (
                "parsing.mediainfo_authoritative" in _mi_old
                and auth_now and not bool(unwrap(_mi_old["parsing.mediainfo_authoritative"]))
            )
            if read_now and (read_on or auth_on):
                all_ids = list((await session.scalars(select(MediaFile.id))).all())
                _spawn_mediainfo_enrich(all_ids, reason="settings")
        except Exception as e:
            logger.warning(f"settings: mediainfo backfill kick-off failed (non-fatal): {e!r}")

    return {"updated": n}


@router.post("/providers/{provider}/test", response_model=ProviderTestResponse)
async def test_provider(
    provider: Literal["tmdb", "tvdb", "anidb", "fanarttv", "opensubtitles", "subdl", "subsource", "musicbrainz", "acoustid"],
    body: ProviderTestBody | None = None,
    session: AsyncSession = Depends(get_session),
) -> ProviderTestResponse:
    """Actually call the provider with the configured credentials and report ok/error.

    For TMDB: hits /configuration. For TVDB: hits /login + /search?q=test.
    fanart.tv is artwork-only (not in the matcher registry), so it's tested by
    pinging its API with the saved key. (`provider` is a free string rather than
    the matcher `ProviderKey` enum precisely so artwork-only sources fit here.)

    A `body.api_key` (etc.) is the JUST-TYPED draft from the settings page — the
    page buffers edits until Save, so without this the Test button validated the
    STALE saved key (false pass/fail, then Save persists a broken key). Candidate
    creds win over the stored value; an empty body tests the saved config.
    """
    # Candidate (just-typed) credentials override the stored ones. Trimmed;
    # an empty string means "not provided" so it falls back to storage.
    cand_key = (body.api_key or "").strip() if body else ""
    cand_user = (body.username or "").strip() if body else ""
    cand_pass = (body.password or "") if body else ""
    # MusicBrainz — the music matcher's metadata source. KEYLESS, so "Test" is a
    # reachability ping (a tiny search via the matcher's own client); no creds.
    if provider == "musicbrainz":
        from kira.music import musicbrainz as mbz
        t0 = time.monotonic()
        async with httpx.AsyncClient() as client:
            hits = await mbz.search_releases(client, "Daft Punk", "Discovery", limit=1)
        if hits:
            return ProviderTestResponse(
                ok=True, detail="MusicBrainz reachable (keyless — no API key needed)",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
        return ProviderTestResponse(ok=False, detail="MusicBrainz unreachable — check your network connection")

    # OpenSubtitles — subtitle provider. Search validates the API key (a dead
    # key 403s → typed AuthRejected); when download creds are saved, login is
    # exercised too, since downloads are the part that actually needs them.
    if provider == "opensubtitles":
        from kira.providers.opensubtitles import OpenSubtitlesClient
        from kira.subtitles.errors import AuthRejected
        from kira.subtitles.prefs import load_subtitle_prefs
        prefs = await load_subtitle_prefs(session)
        os_key = cand_key or prefs.api_key
        if not os_key:
            return ProviderTestResponse(ok=False, detail="No API key configured")
        os_user = cand_user or prefs.username
        os_pass = cand_pass or prefs.password
        t0 = time.monotonic()
        async with httpx.AsyncClient() as client:
            osc = OpenSubtitlesClient(os_key, client)
            try:
                cands = await osc.search(query="inception", languages=["en"])
            except AuthRejected:
                return ProviderTestResponse(
                    ok=False,
                    detail="API key rejected — use the 32-character key from opensubtitles.com → API consumers",
                )
            if not cands:
                return ProviderTestResponse(ok=False, detail="Search returned nothing — check the API key")
            detail = "key OK"
            if os_user and os_pass:
                token = await osc.login(os_user, os_pass)
                detail = "key OK · login OK" if token else "key OK · login FAILED — check username/password"
                if not token:
                    return ProviderTestResponse(ok=False, detail=detail)
            else:
                detail += " · no login saved (needed for downloads)"
        return ProviderTestResponse(
            ok=True, detail=detail, latency_ms=int((time.monotonic() - t0) * 1000),
        )

    # SubDL — subtitle provider, key-gated. A search validates the key.
    if provider == "subdl":
        from kira.subtitles import subdl
        from kira.subtitles.prefs import load_subtitle_prefs
        prefs = await load_subtitle_prefs(session)
        subdl_key = cand_key or prefs.subdl_api_key
        if not subdl_key:
            return ProviderTestResponse(ok=False, detail="No API key configured")
        t0 = time.monotonic()
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://api.subdl.com/api/v1/subtitles",
                params={"api_key": subdl_key, "film_name": "inception", "languages": "EN"},
                headers={"Accept": "application/json"}, timeout=20.0, follow_redirects=True,
            )
        if r.status_code in (401, 403):
            return ProviderTestResponse(ok=False, detail="API key rejected")
        try:
            ok = bool(isinstance(r.json(), dict))
        except Exception:
            ok = False
        return ProviderTestResponse(
            ok=ok, detail="key OK" if ok else f"unexpected response (HTTP {r.status_code})",
            latency_ms=int((time.monotonic() - t0) * 1000) if ok else None,
        )

    # SubSource — subtitle provider, key-gated. A movie search validates the key
    # (the API is Cloudflare-fronted, so it needs the module's browser UA).
    if provider == "subsource":
        from kira.subtitles import subsource
        from kira.subtitles.prefs import load_subtitle_prefs
        prefs = await load_subtitle_prefs(session)
        subsource_key = cand_key or prefs.subsource_api_key
        if not subsource_key:
            return ProviderTestResponse(ok=False, detail="No API key configured")
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{subsource._BASE}/movies/search",
                    params={"searchType": "text", "q": "inception"},
                    headers={"X-API-Key": subsource_key,
                             "User-Agent": subsource._UA, "Accept": "application/json"},
                    timeout=20.0, follow_redirects=True,
                )
            if r.status_code in (401, 403):
                # 403 here is usually Cloudflare, not the key; say so.
                detail = ("API key rejected" if r.status_code == 401
                          else "blocked by Cloudflare (403) — try again shortly")
                return ProviderTestResponse(ok=False, detail=detail)
            if r.status_code >= 400:
                return ProviderTestResponse(ok=False, detail=f"HTTP {r.status_code}")
            ok = bool(isinstance(r.json(), dict) and r.json().get("success"))
            return ProviderTestResponse(
                ok=ok, detail="key OK" if ok else "unexpected response",
                latency_ms=int((time.monotonic() - t0) * 1000) if ok else None,
            )
        except Exception as e:
            return ProviderTestResponse(ok=False, detail=str(e))

    # fanart.tv — artwork provider, tested against its own API.
    if provider == "fanarttv":
        from kira.providers import fanarttv
        row = await session.get(Setting, "providers.fanarttv.api_key")
        key = row.value if row else None
        if isinstance(key, dict):           # tolerate a {"value": …} wrapper
            key = key.get("value")
        # candidate > saved > BUNDLED project key (same fallback the artwork
        # pipeline uses) — a fresh install's Test used to fail "No API key
        # configured" while artwork actually worked, contradicting the card.
        eff_key = cand_key or (key.strip() if isinstance(key, str) else "") or fanarttv.PROJECT_KEY
        t0 = time.monotonic()
        async with httpx.AsyncClient() as client:
            ok, detail = await fanarttv.test_key(eff_key, client)
        return ProviderTestResponse(
            ok=ok, detail=detail,
            latency_ms=int((time.monotonic() - t0) * 1000) if ok else None,
        )

    # AcoustID — audio-fingerprint matching. Kira ships an app key (a
    # `providers.acoustid.api_key` setting overrides), so the test validates THAT
    # key + the API reachability, then reports whether fpcalc (the local fingerprint
    # binary — the other half the feature needs) is installed.
    if provider == "acoustid":
        from kira.music import acoustid as _ac
        from kira import fpcalc_setup as _fp
        row = await session.get(Setting, "providers.acoustid.api_key")
        kv = row.value if row else None
        if isinstance(kv, dict):                 # tolerate a {"value": …} wrapper
            kv = kv.get("value")
        key = (kv if isinstance(kv, str) else "").strip() or _ac.PROJECT_KEY
        fp_ok = _fp.resolve_fpcalc() is not None
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    "https://api.acoustid.org/v2/lookup",
                    data={"client": key, "duration": "120", "fingerprint": "INVALID", "meta": "recordings"},
                    timeout=15.0,
                )
                d = r.json()
        except Exception as e:                    # noqa: BLE001
            return ProviderTestResponse(ok=False, detail=f"AcoustID unreachable — {e}")
        # An invalid API key → AcoustID error code 4. Any other outcome (status ok,
        # or the expected "invalid fingerprint" error) means the key was ACCEPTED.
        code = (d.get("error") or {}).get("code") if isinstance(d, dict) else None
        if code == 4:
            return ProviderTestResponse(ok=False, detail="AcoustID API key rejected")
        if not fp_ok:
            return ProviderTestResponse(
                ok=False,
                detail="API reachable, key OK — but fpcalc isn't installed yet. Click “Install for me” below to enable fingerprint matching.",
            )
        return ProviderTestResponse(
            ok=True, detail="AcoustID ready · key OK · fpcalc installed",
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async with httpx.AsyncClient() as client:
        if provider not in ("tmdb", "tvdb", "anidb"):
            # Other providers not implemented yet.
            return ProviderTestResponse(ok=False, detail=f"{provider} test not implemented yet")

        # A just-typed candidate key (tmdb/tvdb) builds a one-off provider so
        # Test validates THAT key, not the stored one. AniDB is keyless — its
        # candidate would be a client name/version, so it keeps the registry.
        if cand_key and provider in ("tmdb", "tvdb"):
            from kira.providers.base import ProviderConfig, ProviderMode
            from kira.providers.factory import build_provider
            cfg = ProviderConfig(mode=ProviderMode.DIRECT, api_key=cand_key)
            try:
                p = build_provider(provider, cfg, client)
            except ValueError as e:
                return ProviderTestResponse(ok=False, detail=str(e))
        else:
            registry = await registry_from_settings(client)
            if not registry.has(provider):
                return ProviderTestResponse(ok=False, detail=f"{provider} has no API key configured")
            try:
                p = registry.build(provider)
            except ValueError as e:
                return ProviderTestResponse(ok=False, detail=str(e))

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
