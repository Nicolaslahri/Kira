"""Outbound-integration endpoints — currently just Sonarr.

Two endpoints per integration:
  1. POST /test — validate URL + API key + return user's quality
     profiles and root folders in ONE response. Saves the UI a
     follow-up call when the user opens Settings → Integrations.
  2. POST /send-missing — primary action. Takes a Match id and the
     list of missing episode numbers; backend resolves the TVDB id
     (cross-ref via Fribb for AniDB matches) and trips Sonarr's
     EpisodeSearch.

Settings keys this module reads:
  integrations.sonarr.url
  integrations.sonarr.api_key
  integrations.sonarr.quality_profile_id
  integrations.sonarr.root_folder_path

Nothing here writes settings — the existing PUT /settings endpoint
handles persistence.
"""
from __future__ import annotations

import logging

import asyncio
import time
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kira.database import get_session
from kira.integrations.sonarr import (
    SonarrConfig,
    SonarrError,
    SonarrQueueItem,
    get_queue,
    list_quality_profiles,
    list_root_folders,
    preview_manual_import,
    retry_manual_import,
    send_missing_episodes,
    test_connection,
)
from kira.integrations import radarr as radarr_mod
from kira.models import Match, Setting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


async def _resolve_setting(session: AsyncSession, key: str) -> Any:
    """Pull a Setting row by key, returning the JSON value (or None).

    Settings can be stored two ways depending on which endpoint wrote
    them — sometimes a bare value, sometimes wrapped in `{"value": X}`.
    This helper unwraps both shapes so callers don't have to care.
    """
    row = await session.get(Setting, key)
    if row is None:
        return None
    # Single source of truth for the {"value": …} shape (kira.settings_store) —
    # the old `len(v) == 1` guard diverged from it for dicts carrying a sibling key.
    from kira.settings_store import unwrap
    return unwrap(row.value)


async def _load_sonarr_config(
    session: AsyncSession, *, is_anime: bool = False,
) -> SonarrConfig:
    """Build a SonarrConfig from saved Settings. Raises HTTPException 400
    when URL or API key isn't configured — the UI surfaces that as
    "Configure Sonarr in Settings → Integrations."

    Quality profile + root folder come from per-series-type settings —
    user's Sonarr typically has separate Anime / TV quality profiles
    AND separate root folders (`/data/media/tv` vs `/data/media/anime`).
    When `is_anime` we pick the `.anime.` keys; otherwise the `.tv.`
    keys. Each falls back to the legacy un-prefixed key so users who
    configured before the split don't lose their settings.

    The test endpoint doesn't care about quality/folder selection
    (it only validates URL + API key + surfaces the option lists), so
    callers that don't need to make Sonarr decisions can ignore the
    `is_anime` argument entirely.
    """
    url = await _resolve_setting(session, "integrations.sonarr.url")
    api_key = await _resolve_setting(session, "integrations.sonarr.api_key")
    if not isinstance(url, str) or not url.strip():
        raise HTTPException(400, "Sonarr URL isn't configured.")
    if not isinstance(api_key, str) or not api_key.strip():
        raise HTTPException(400, "Sonarr API key isn't configured.")
    # Validate it's a sane http(s) endpoint before it becomes the base URL for
    # every Sonarr call — a garbage scheme (file://, javascript:) or a hostless
    # value fails loudly here ("invalid URL" in the UI) instead of surfacing as a
    # cryptic connection error deep in the integration. (Outbound subtitle/artwork
    # URLs already go through url_guard; the integration base never did.)
    from urllib.parse import urlparse
    _parsed = urlparse(url.strip())
    if _parsed.scheme not in ("http", "https") or not _parsed.netloc:
        raise HTTPException(400, "Sonarr URL must be a valid http(s):// address.")

    section = "anime" if is_anime else "tv"
    # Try section-specific first, then legacy un-prefixed for back-compat.
    qpid = await _resolve_setting(session, f"integrations.sonarr.{section}.quality_profile_id")
    if qpid is None:
        qpid = await _resolve_setting(session, "integrations.sonarr.quality_profile_id")
    rfp = await _resolve_setting(session, f"integrations.sonarr.{section}.root_folder_path")
    if rfp is None:
        rfp = await _resolve_setting(session, "integrations.sonarr.root_folder_path")

    # Series type per flavor — Sonarr supports standard / anime / daily.
    # Defaults: TV → standard, Anime → anime. User can override either
    # (e.g. a power user who hates Sonarr's absolute-number anime
    # naming convention can pick "standard" for the Anime flavor and
    # get SxxExx files instead).
    series_type = await _resolve_setting(session, f"integrations.sonarr.{section}.series_type")
    if not isinstance(series_type, str) or not series_type:
        series_type = "anime" if is_anime else "standard"

    # Global Sonarr behaviors (not per-flavor) — apply to every series
    # we add regardless of how it was matched.
    sf = await _resolve_setting(session, "integrations.sonarr.season_folders")
    season_folders = sf if isinstance(sf, bool) else True  # default ON
    mns = await _resolve_setting(session, "integrations.sonarr.monitor_new_seasons")
    if mns not in ("all", "future", "none"):
        mns = "all"

    # URL base for reverse-proxy users — appended to base_url so all
    # downstream calls hit the proxied path. We accept it as either a
    # leading-slash or no-slash form, normalize either way.
    url_base = await _resolve_setting(session, "integrations.sonarr.url_base")
    base_url = url.strip().rstrip("/")
    if isinstance(url_base, str) and url_base.strip():
        suffix = url_base.strip()
        if not suffix.startswith("/"):
            suffix = "/" + suffix
        base_url = base_url + suffix.rstrip("/")

    return SonarrConfig(
        base_url=base_url,
        api_key=api_key.strip(),
        quality_profile_id=int(qpid) if isinstance(qpid, (int, str)) and str(qpid).isdigit() else None,
        root_folder_path=rfp if isinstance(rfp, str) and rfp.strip() else None,
        series_type=series_type,
        season_folders=season_folders,
        monitor_new_seasons=mns,
    )


async def _load_radarr_config(session: AsyncSession) -> radarr_mod.RadarrConfig:
    """Build a RadarrConfig from saved Settings (`integrations.radarr.*`).
    Raises HTTPException 400 when URL or API key isn't configured.

    Simpler than the Sonarr loader: movies have no anime/TV flavor split, no
    series type, season folders, or new-season monitoring — just the connection,
    plus one quality profile + root folder kept for a future add-to-Radarr (the
    relink hooks don't read them). Mirrors `_load_sonarr_config`'s URL validation
    and reverse-proxy `url_base` handling.
    """
    url = await _resolve_setting(session, "integrations.radarr.url")
    api_key = await _resolve_setting(session, "integrations.radarr.api_key")
    if not isinstance(url, str) or not url.strip():
        raise HTTPException(400, "Radarr URL isn't configured.")
    if not isinstance(api_key, str) or not api_key.strip():
        raise HTTPException(400, "Radarr API key isn't configured.")
    from urllib.parse import urlparse
    _parsed = urlparse(url.strip())
    if _parsed.scheme not in ("http", "https") or not _parsed.netloc:
        raise HTTPException(400, "Radarr URL must be a valid http(s):// address.")

    qpid = await _resolve_setting(session, "integrations.radarr.quality_profile_id")
    rfp = await _resolve_setting(session, "integrations.radarr.root_folder_path")

    url_base = await _resolve_setting(session, "integrations.radarr.url_base")
    base_url = url.strip().rstrip("/")
    if isinstance(url_base, str) and url_base.strip():
        suffix = url_base.strip()
        if not suffix.startswith("/"):
            suffix = "/" + suffix
        base_url = base_url + suffix.rstrip("/")

    return radarr_mod.RadarrConfig(
        base_url=base_url,
        api_key=api_key.strip(),
        quality_profile_id=int(qpid) if isinstance(qpid, (int, str)) and str(qpid).isdigit() else None,
        root_folder_path=rfp if isinstance(rfp, str) and rfp.strip() else None,
    )


# ─────────────────────────────────────────────────────────────────────
# /health — background connection-health snapshot
# ─────────────────────────────────────────────────────────────────────
#
# Driven by the background `HealthMonitor` loop (kira.integrations.
# health_monitor), which probes each CONFIGURED integration every ~5 min
# with its existing connection test. This endpoint just SERVES the latest
# snapshot — it does NOT trigger a probe, so the Settings page can poll it
# every ~60s without generating any outbound HTTP to Sonarr/Plex/Jellyfin.
#
# Shape: { "<key>": {"ok": bool, "detail": str, "checked_at": iso} } for
# integrations that have been observed at least once. Configured-but-not-
# yet-checked (the first ~10s after boot) and unconfigured integrations are
# simply absent — the frontend renders a grey "unknown" dot for those.


@router.get("/health")
async def integrations_health() -> dict[str, dict[str, Any]]:
    """Latest background health-check results for configured integrations.

    Returns the in-memory snapshot the `HealthMonitor` maintains — no network
    I/O happens here, so it's safe to poll frequently. Keys present: whichever
    of sonarr / plex / jellyfin have been probed since boot. Absent key = either
    unconfigured or not yet checked; the UI treats both as "unknown" (grey)."""
    from kira.integrations.health_monitor import monitor
    return monitor.snapshot()


# ─────────────────────────────────────────────────────────────────────
# /test — validate connection + fetch dropdown options in one trip
# ─────────────────────────────────────────────────────────────────────


class SonarrTestRequest(BaseModel):
    """Optional inline overrides for the test call. If omitted, the
    saved settings values are used. Lets the UI test a freshly-typed
    URL+key BEFORE committing them to settings."""
    url: str | None = None
    api_key: str | None = None


class SonarrTestResponse(BaseModel):
    ok: bool
    detail: str | None = None
    version: str | None = None
    quality_profiles: list[dict[str, Any]] | None = None
    root_folders: list[dict[str, Any]] | None = None


@router.post("/sonarr/test", response_model=SonarrTestResponse)
async def sonarr_test(
    payload: SonarrTestRequest,
    session: AsyncSession = Depends(get_session),
) -> SonarrTestResponse:
    """Confirm Sonarr is reachable AND surface its quality profiles +
    root folders so the Settings UI can populate dropdowns from the
    user's actual Sonarr config (rather than have them paste numeric
    ids blind).

    Accepts inline `url` and `api_key` overrides — the Settings
    "Test connection" button posts what's currently in the form,
    BEFORE saving, so the user gets immediate feedback without having
    to commit a possibly-wrong config first.
    """
    # Build a config — prefer payload overrides, fall back to stored.
    if payload.url and payload.api_key:
        cfg = SonarrConfig(base_url=payload.url.strip(), api_key=payload.api_key.strip())
    else:
        try:
            cfg = await _load_sonarr_config(session)
        except HTTPException as e:
            return SonarrTestResponse(ok=False, detail=str(e.detail))

    try:
        status = await test_connection(cfg)
        # Don't fail the whole test if profiles or root folders fetch
        # hiccups — the connection IS proven by /system/status. We
        # surface what we got and let the UI handle empty dropdowns.
        qps: list[dict[str, Any]] = []
        rfs: list[dict[str, Any]] = []
        try:
            qps = await list_quality_profiles(cfg)
        except SonarrError:
            pass
        try:
            rfs = await list_root_folders(cfg)
        except SonarrError:
            pass
        return SonarrTestResponse(
            ok=True,
            version=status.get("version") if isinstance(status, dict) else None,
            # Trim each item to just the fields the dropdown needs —
            # full payloads include image URLs, schedule metadata, etc.
            quality_profiles=[{"id": q.get("id"), "name": q.get("name")} for q in qps if isinstance(q, dict)],
            root_folders=[{"path": r.get("path"), "freeSpace": r.get("freeSpace")} for r in rfs if isinstance(r, dict)],
        )
    except SonarrError as e:
        return SonarrTestResponse(ok=False, detail=str(e))
    except Exception as e:
        # Safety net: a "Test connection" must NEVER raise an uncaught 500. A
        # non-ASCII / malformed key, for instance, makes httpx fail to encode the
        # X-Api-Key header (UnicodeEncodeError) before any request goes out — and
        # an uncaught error, served cross-origin, reaches the browser WITHOUT
        # CORS headers, surfacing as a misleading "Failed to fetch" instead of a
        # real message. Return it as a normal failed test.
        return SonarrTestResponse(ok=False, detail=f"Sonarr test failed: {e}")


class RadarrTestResponse(BaseModel):
    ok: bool
    detail: str | None = None
    version: str | None = None
    quality_profiles: list[dict[str, Any]] | None = None
    root_folders: list[dict[str, Any]] | None = None


@router.post("/radarr/test", response_model=RadarrTestResponse)
async def radarr_test(
    payload: SonarrTestRequest,
    session: AsyncSession = Depends(get_session),
) -> RadarrTestResponse:
    """Confirm Radarr is reachable AND surface its quality profiles + root
    folders so the Settings UI can populate dropdowns (mirror of /sonarr/test).

    Accepts inline `url`/`api_key` overrides — the "Test connection" button
    posts the current form values BEFORE saving, so the user gets immediate
    feedback without committing a possibly-wrong config first.
    """
    if payload.url and payload.api_key:
        cfg = radarr_mod.RadarrConfig(base_url=payload.url.strip(), api_key=payload.api_key.strip())
    else:
        try:
            cfg = await _load_radarr_config(session)
        except HTTPException as e:
            return RadarrTestResponse(ok=False, detail=str(e.detail))

    try:
        status = await radarr_mod.test_connection(cfg)
        qps: list[dict[str, Any]] = []
        rfs: list[dict[str, Any]] = []
        try:
            qps = await radarr_mod.list_quality_profiles(cfg)
        except radarr_mod.RadarrError:
            pass
        try:
            rfs = await radarr_mod.list_root_folders(cfg)
        except radarr_mod.RadarrError:
            pass
        return RadarrTestResponse(
            ok=True,
            version=status.get("version") if isinstance(status, dict) else None,
            quality_profiles=[{"id": q.get("id"), "name": q.get("name")} for q in qps if isinstance(q, dict)],
            root_folders=[{"path": r.get("path"), "freeSpace": r.get("freeSpace")} for r in rfs if isinstance(r, dict)],
        )
    except radarr_mod.RadarrError as e:
        return RadarrTestResponse(ok=False, detail=str(e))
    except Exception as e:
        # Same safety net as sonarr_test: a "Test connection" must never raise an
        # uncaught 500 (cross-origin, that surfaces as a misleading "Failed to
        # fetch" without CORS headers). Return it as a normal failed test.
        return RadarrTestResponse(ok=False, detail=f"Radarr test failed: {e}")


class AddMovieRequest(BaseModel):
    tmdb_id: int


class AddMovieResponse(BaseModel):
    ok: bool
    added: bool = False   # True = fresh add; False = already in Radarr, just searched
    detail: str | None = None


@router.post("/radarr/add-movie", response_model=AddMovieResponse)
async def radarr_add_movie(
    payload: AddMovieRequest,
    session: AsyncSession = Depends(get_session),
) -> AddMovieResponse:
    """Add a movie to Radarr by TMDB id + trigger a search — the collection-
    completion "Get from Radarr" button. Synchronous: the add + search are a
    couple of quick calls (Radarr runs the actual grab server-side via
    `addOptions.searchForMovie`), so we return the outcome for an immediate toast.
    """
    try:
        cfg = await _load_radarr_config(session)
    except HTTPException as e:
        return AddMovieResponse(ok=False, detail=str(e.detail))
    try:
        ok, added, detail = await radarr_mod.add_movie(cfg, payload.tmdb_id)
        return AddMovieResponse(ok=ok, added=added, detail=detail)
    except radarr_mod.RadarrError as e:
        return AddMovieResponse(ok=False, detail=str(e))
    except Exception as e:
        return AddMovieResponse(ok=False, detail=f"Radarr add failed: {e}")


# ── Radarr live download queue (collection-ghost cover progress fills) ──────
_RADARR_QUEUE_CACHE: dict[str, tuple[list[Any], float]] = {}
_RADARR_QUEUE_LOCK = asyncio.Lock()
_RADARR_QUEUE_TTL_SEC = 1.0


async def _get_cached_radarr_queue(cfg: radarr_mod.RadarrConfig) -> list[Any]:
    """Cached Radarr queue fetch (mirror of `_get_cached_queue`) — coalesces the
    grid poll so rapid ticks don't hammer Radarr. Keyed by config identity."""
    now = time.monotonic()
    key = f"{cfg.base_url}\x00{cfg.api_key}"
    cached = _RADARR_QUEUE_CACHE.get(key)
    if cached is not None and (now - cached[1]) < _RADARR_QUEUE_TTL_SEC:
        return cached[0]
    async with _RADARR_QUEUE_LOCK:
        cached = _RADARR_QUEUE_CACHE.get(key)
        if cached is not None and (time.monotonic() - cached[1]) < _RADARR_QUEUE_TTL_SEC:
            return cached[0]
        items = await radarr_mod.get_queue(cfg)
        _RADARR_QUEUE_CACHE.clear()
        _RADARR_QUEUE_CACHE[key] = (items, time.monotonic())
        return items


class RadarrQueueItemOut(BaseModel):
    tmdb_id: int
    title: str | None = None
    status: str
    progress_pct: float
    eta_seconds: int | None = None
    release_title: str | None = None
    error_message: str | None = None


class RadarrQueueResponse(BaseModel):
    items: list[RadarrQueueItemOut]
    cached_at: float


@router.get("/radarr/queue", response_model=RadarrQueueResponse)
async def radarr_queue(session: AsyncSession = Depends(get_session)) -> RadarrQueueResponse:
    """Radarr's active download queue, keyed by tmdb id — drives the collection-
    ghost cover progress fills. Returns empty (never 4xx) when Radarr isn't
    configured or is unreachable, so the grid poll degrades silently."""
    try:
        cfg = await _load_radarr_config(session)
    except HTTPException:
        return RadarrQueueResponse(items=[], cached_at=time.monotonic())
    try:
        items = await _get_cached_radarr_queue(cfg)
    except radarr_mod.RadarrError:
        return RadarrQueueResponse(items=[], cached_at=time.monotonic())
    return RadarrQueueResponse(
        items=[RadarrQueueItemOut(
            tmdb_id=i.tmdb_id, title=i.title, status=i.status,
            progress_pct=i.progress_pct, eta_seconds=i.eta_seconds,
            release_title=i.release_title, error_message=i.error_message,
        ) for i in items],
        cached_at=time.monotonic(),
    )


# ─────────────────────────────────────────────────────────────────────
# /send-missing — primary action
# ─────────────────────────────────────────────────────────────────────


class SendMissingRequest(BaseModel):
    """Frontend sends:
      * `match_id`: which Match row defines the TVDB series + anime flag
      * `season`: Sonarr addresses episodes by (season, number)
      * `episode_numbers`: the missing eps (Sonarr-local — for AniDB
        matches the popup already merges in TVDB-shape episodes via
        the existing /series/{provider}/{id}/episodes endpoint)
    """
    match_id: int
    season: int
    episode_numbers: list[int]


class SendMissingResponse(BaseModel):
    ok: bool
    detail: str | None = None
    queued: int = 0
    series_was_added: bool = False
    sonarr_series_title: str | None = None
    skipped_episodes: list[int] | None = None
    # True when the Sonarr search was handed to a BACKGROUND task — the result
    # (queued / nothing / couldn't-reach) arrives via the activity pill, not in
    # this response. EpisodeSearch is normally quick, but a slow or unreachable
    # Sonarr would otherwise hang the request, so we never block on it.
    started: bool = False


async def _send_missing_bg(
    cfg: SonarrConfig, *, tvdb_id: int, season: int,
    episode_numbers: list[int], series_label: str,
) -> None:
    """Run the Sonarr search OFF the request thread, narrating via the activity
    pill. EpisodeSearch is normally quick, but a slow or unreachable Sonarr would
    block the HTTP request — which used to hang the button on "Sending…" and
    surface the timeout as a hard failure. The activity job is `begin()`-ed by
    the endpoint before this is scheduled."""
    from kira import activity
    n = len(episode_numbers)
    try:
        result = await send_missing_episodes(
            cfg, tvdb_id=tvdb_id, season=season, episode_numbers=episode_numbers)
        if result.queued > 0:
            detail = f"Queued {result.queued} of {n} to Sonarr"
            if result.message:
                detail += f" — {result.message}"
        else:
            detail = result.message or "Nothing to queue — already in Sonarr."
        activity.end("sonarr_search", ok=True, detail=detail)
    except SonarrError as e:
        activity.end("sonarr_search", ok=False, detail=str(e))
    except Exception as e:  # noqa: BLE001 — never let a bg task crash silently
        logger.warning("sonarr send-missing background task failed: %r", e)
        activity.end("sonarr_search", ok=False,
                     detail=f"Sonarr handoff failed ({type(e).__name__}).")


async def _resolve_tvdb_id_for_match(
    session: AsyncSession, match: Match,
) -> tuple[int, bool, int | None]:
    """Return (tvdb_id, is_anime, tvdb_season_override) for a Match, or
    raise HTTPException 400 with a user-readable reason.

    The third element matters for AniDB matches: AniDB AIDs map to a
    SPECIFIC TVDB season number via the Fribb cross-reference (e.g.
    Frieren S2's AID 18886 → TVDB series 366524 season 2). Sonarr
    addresses episodes by (TVDB season, episode number), so we need
    the TVDB-side season — NOT whatever season number the AniDB
    episode list happens to report (AniDB's native convention is
    "everything is season 1" because each season is its own AID).
    The frontend doesn't know about this mapping; the backend
    resolves it server-side and the API endpoint uses the override.

    Resolution order:
      * provider=tvdb → provider_id IS the TVDB id directly; season
        override is None (caller uses payload.season unchanged)
      * provider=anidb → cross-reference via Fribb to get TVDB id +
        TVDB season; is_anime=True so Sonarr adds with anime conventions
      * provider=tmdb → not supported (Sonarr's lookup endpoint can
        accept tmdb: prefix in principle but it's unreliable in
        practice; Kira's TMDB matches for TV are typically a fallback)
    """
    provider = (match.provider or "").lower()
    provider_id = (match.provider_id or "").strip()
    if not provider_id:
        raise HTTPException(400, "Match has no provider_id; can't resolve TVDB series.")
    if provider == "tvdb":
        try:
            return int(provider_id), False, None
        except ValueError as e:
            raise HTTPException(400, f"Match's TVDB provider_id isn't numeric: {provider_id}") from e

    if provider == "anidb":
        try:
            aid = int(provider_id)
        except ValueError as e:
            raise HTTPException(400, f"Match's AniDB provider_id isn't numeric: {provider_id}") from e
        from kira.providers.anime_mappings import AnimeMappings
        tvdb_id = await AnimeMappings.tvdb_id(aid)
        if tvdb_id is None:
            raise HTTPException(
                400,
                f"Couldn't cross-reference AniDB AID {aid} to a TVDB series. "
                f"This anime may not be in the Fribb mapping yet — try "
                f"re-identifying it manually against the TVDB entry first.",
            )
        # Fribb usually carries the TVDB season for the AID. If missing
        # (rare, but happens for fresh anime entries), fall back to
        # the Match's own season_number which the matcher set from
        # Fribb at cluster time. If THAT's also missing, return None
        # and let the caller's supplied season carry through.
        tvdb_season = await AnimeMappings.tvdb_season(aid)
        if tvdb_season is None and isinstance(match.season_number, int):
            tvdb_season = match.season_number
        return tvdb_id, True, tvdb_season

    if provider == "tmdb":
        raise HTTPException(
            400,
            "TMDB-only matches can't be sent to Sonarr yet. Re-identify "
            "the series against TVDB or AniDB first (Sonarr is TVDB-centric).",
        )
    raise HTTPException(400, f"Unknown provider for Sonarr handoff: {provider!r}")


@router.post("/sonarr/send-missing", response_model=SendMissingResponse)
async def sonarr_send_missing(
    payload: SendMissingRequest,
    session: AsyncSession = Depends(get_session),
) -> SendMissingResponse:
    """One-click handoff: ensure the series is in Sonarr, then trigger
    a per-episode search for the requested missing episodes.

    Frontend reads the response and toasts the outcome. The actual
    download work lives entirely in Sonarr from here on; Kira doesn't
    poll progress (that's Phase 2 — display Sonarr's `/queue` inline).
    """
    if not payload.episode_numbers:
        raise HTTPException(400, "No episode numbers supplied.")
    if payload.season < 0:
        raise HTTPException(400, f"Invalid season number: {payload.season}.")

    match = await session.get(Match, payload.match_id)
    if match is None:
        raise HTTPException(404, f"Match {payload.match_id} not found.")

    tvdb_id, is_anime, tvdb_season_override = await _resolve_tvdb_id_for_match(session, match)
    # AniDB → TVDB season override: Sonarr addresses episodes by their
    # TVDB-side season number, but the frontend's `payload.season` came
    # from `providerEpisodes[0].season` which for AniDB-direct fetches
    # is `1` (AniDB native). Without this override Sonarr would search
    # season 1 of the series instead of the actual TVDB season the
    # files belong to. Frontend deliberately doesn't compute the
    # mapping itself; the backend Match row + Fribb cross-ref already
    # have everything we need.
    effective_season = tvdb_season_override if tvdb_season_override is not None else payload.season

    # is_anime drives BOTH the Sonarr series-type tag (anime vs standard,
    # for series-add) AND which quality-profile + root-folder pair we
    # use (the user maintains a separate "Anime" profile + folder in
    # their Sonarr config; we mirror that split here).
    cfg = await _load_sonarr_config(session, is_anime=is_anime)

    # Hand the Sonarr search to a tracked BACKGROUND task and return now.
    # EpisodeSearch is normally quick, but a slow or unreachable Sonarr inside
    # the request hung the button on "Sending…" and turned an unreachable Sonarr
    # into a hard timeout. The activity pill narrates the outcome
    # (queued / nothing-to-do / couldn't-reach), and because that state lives in
    # the activity system it survives closing + reopening the popup.
    from kira import activity
    from kira.tasks import spawn_tracked
    series_label = match.series_name or match.title or "the series"
    activity.begin("sonarr_search", f"Searching Sonarr — {series_label}")   # register before responding
    spawn_tracked(
        _send_missing_bg(
            cfg, tvdb_id=tvdb_id, season=effective_season,
            episode_numbers=payload.episode_numbers, series_label=series_label),
        label="sonarr_search",
    )
    return SendMissingResponse(
        ok=True, started=True, queued=0,
        sonarr_series_title=series_label,
        detail="Searching Sonarr in the background — watch the activity indicator.",
    )


# ─────────────────────────────────────────────────────────────────────
# /queue — live download progress (Phase 2 of the Sonarr integration)
# ─────────────────────────────────────────────────────────────────────
#
# The popup polls this every ~4s while open; the LibraryGrid polls
# every ~12s globally to drive cover-card status pills. Both share the
# in-process cache below — Sonarr only ever gets one /queue call per
# 4-second window regardless of how many tabs / cards are watching.
#
# Why two layers: per-Match filtering happens in the endpoint (since the
# popup wants ONE series' items), but the underlying queue is global —
# fetching it 50 times per poll cycle for a library with 50 active
# clusters would hammer Sonarr. The cache lifts the de-dup into the
# endpoint so consumers can keep their per-Match URLs clean.

# Cache key is always the single string "queue" — we only have one
# Sonarr instance per Kira install. Stored as a tuple of
# (items_list, fetched_at_unix_seconds) so the staleness check is
# trivial.
_QUEUE_CACHE: dict[str, tuple[list[SonarrQueueItem], float]] = {}
# 0.5s TTL. Popup polls every 1.5s and uses rAF + Sonarr ETA to
# extrapolate position 60fps in between, so what matters most is that
# every poll returns ground truth fresh enough to keep the
# extrapolation on the rails — 0.5s staleness is invisible to the
# eye. Still de-duplicates when popup + library grid (8s poll) tick
# in the same window. Sonarr's /queue is an in-memory read so the
# tight TTL is cheap.
_QUEUE_CACHE_TTL_SEC = 0.5
# Single-flight lock — if two concurrent /queue requests both miss the
# cache, only ONE hits Sonarr; the second awaits the first. Prevents
# accidental thundering-herd from a popup-open coinciding with a
# library-grid poll tick.
_QUEUE_LOCK = asyncio.Lock()


async def _get_cached_queue(cfg: SonarrConfig) -> list[SonarrQueueItem]:
    """Return the queue, hitting Sonarr only if our cached copy is stale.

    Errors propagate (the endpoint translates them to a 4xx); we don't
    fall back to a stale cache on failure. Reason: if Sonarr is down,
    showing 30-second-old "downloading" rows that no longer reflect
    reality is more misleading than showing nothing.
    """
    now = time.monotonic()
    # Key the cache by the CONFIG identity, not a constant string — otherwise a
    # queue fetched for one Sonarr (url/key) would be served to a different one
    # after a settings change (or a hypothetical second instance). \x00 can't
    # occur in a URL or API key, so it's a safe field separator.
    cache_key = f"{cfg.base_url}\x00{cfg.api_key}"
    cached = _QUEUE_CACHE.get(cache_key)
    if cached is not None and (now - cached[1]) < _QUEUE_CACHE_TTL_SEC:
        return cached[0]
    async with _QUEUE_LOCK:
        # Re-check inside the lock — first request fills, second one
        # finds the cache warm and skips the network round-trip.
        cached = _QUEUE_CACHE.get(cache_key)
        if cached is not None and (time.monotonic() - cached[1]) < _QUEUE_CACHE_TTL_SEC:
            return cached[0]
        items = await get_queue(cfg)
        # Single Sonarr per install: drop any entry for a previous (url/key)
        # identity before inserting, so a settings change can't leave the old
        # config's items — and credentials — lingering in memory unbounded.
        _QUEUE_CACHE.clear()
        _QUEUE_CACHE[cache_key] = (items, time.monotonic())
        return items


class QueueItemOut(BaseModel):
    """One in-flight Sonarr download in Kira's shape.

    Mirrors SonarrQueueItem but as a Pydantic model for FastAPI's auto-
    schema. Field names use snake_case to match the rest of the API.

    `anidb_aid` is populated for items whose (tvdb_id, season) reverse-
    cross-refs to an AniDB AID via Fribb. Lets the frontend's library-
    grid cover-card status pills find AniDB cards too (they don't carry
    their own TVDB id on `item.providers`). Null when Fribb has no
    mapping or the show isn't anime.

    `needs_manual_import` flags the common "Downloaded - Unable to
    Import Automatically" stuck state. The popup renders a one-click
    "Force import" button when this is true; that button hits
    /integrations/sonarr/retry-import with the `download_id`.
    """
    tvdb_id: int
    anidb_aid: int | None = None
    season: int
    episode_number: int
    episode_title: str | None = None
    status: str
    progress_pct: float
    eta_seconds: int | None = None
    size_bytes: int | None = None
    size_left_bytes: int | None = None
    release_title: str | None = None
    protocol: str | None = None
    error_message: str | None = None
    download_client: str | None = None
    queue_id: int | None = None
    download_id: str | None = None
    needs_manual_import: bool = False


class QueueResponse(BaseModel):
    """Wrapped response so we can attach `cached_at` for the UI's
    "last refreshed N seconds ago" affordance (Phase B). The popup
    doesn't display it; the library-grid pill might, eventually.
    """
    items: list[QueueItemOut]
    cached_at: float   # Unix seconds — when this snapshot was fetched


@router.get("/sonarr/queue", response_model=QueueResponse)
async def sonarr_queue(
    match_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> QueueResponse:
    """Return Sonarr's active download queue.

    With no `match_id`, returns every queue item Sonarr knows about —
    used by the library-grid cover-card pill code that needs to know
    "any of my matches actively downloading?".

    With `match_id=N`, filters to just the items for that Match's
    resolved TVDB id + season. Used by the popup to populate
    download-progress rows for missing episodes. Backed by the same
    cached snapshot, so a popup opening doesn't double-pull Sonarr.

    Sonarr-not-configured returns 400 (same as send-missing); the
    frontend swallows that into "feature unavailable" and stops
    polling. We don't return ok=false here — a real configured
    Sonarr with an empty queue returns `items: []` which is the same
    success shape.
    """
    # is_anime doesn't affect /queue (it's a global endpoint, not per-
    # series), but _load_sonarr_config requires a flavor. Default to TV
    # since the quality profile / root folder fields are ignored here.
    is_anime_for_cfg = False
    target_tvdb: int | None = None
    target_season: int | None = None
    if match_id is not None:
        match = await session.get(Match, match_id)
        if match is None:
            raise HTTPException(404, f"Match {match_id} not found.")
        try:
            target_tvdb, is_anime_for_cfg, season_override = await _resolve_tvdb_id_for_match(session, match)
        except HTTPException:
            # If the match can't be resolved to a TVDB id (TMDB-only,
            # missing Fribb mapping, etc.) there's by definition nothing
            # to show in Sonarr — return empty rather than fail loudly.
            return QueueResponse(items=[], cached_at=time.time())
        if season_override is not None:
            target_season = season_override
        elif isinstance(match.season_number, int):
            target_season = match.season_number

    cfg = await _load_sonarr_config(session, is_anime=is_anime_for_cfg)
    try:
        items = await _get_cached_queue(cfg)
    except SonarrError as e:
        raise HTTPException(400, str(e)) from e

    if target_tvdb is not None:
        filtered = [it for it in items if it.tvdb_id == target_tvdb]
        if target_season is not None:
            filtered = [it for it in filtered if it.season == target_season]
        items = filtered

    # Reverse cross-ref each item's (tvdb_id, season) → AniDB AID so the
    # frontend's library-grid pills can find AniDB-only cards too. The
    # mapping is in-memory (Fribb data was loaded at startup); each call
    # is a dict lookup, so this is O(n_items) without any HTTP.
    #
    # Skipped when target_tvdb is set (popup mode) AND the user came in
    # via an AniDB match — the popup doesn't need the reverse cross-ref
    # (it already knows the AID). Faster fast path; harmless if we do
    # populate it anyway, so we always populate for consistency.
    from kira.providers.anime_mappings import AnimeMappings  # local import: avoid startup cycle
    out: list[QueueItemOut] = []
    for it in items:
        aid: int | None = None
        try:
            aid = await AnimeMappings.aid_by_tvdb_season(it.tvdb_id, it.season)
        except Exception:
            # If Fribb data isn't loaded (cold start, ban, network failure
            # at boot) we'd rather return tvdb-only items than 500 the
            # whole queue. The pill code degrades gracefully — TVDB cards
            # still get pills, AniDB cards just go without until Fribb
            # finishes loading.
            aid = None
        payload = asdict(it)
        payload["anidb_aid"] = aid
        out.append(QueueItemOut(**payload))

    return QueueResponse(
        items=out,
        cached_at=time.time(),
    )


# ─────────────────────────────────────────────────────────────────────
# /retry-import — force Sonarr past a "Unable to Import Automatically"
# ─────────────────────────────────────────────────────────────────────


class RetryImportRequest(BaseModel):
    """Body for the manual-import retry. `download_id` is Sonarr's
    own identifier (the torrent hash for torrent clients, an NZB id
    for usenet) — surfaced in the queue response. The frontend pulls
    it from the QueueItemOut that has `needs_manual_import=true`.

    `import_mode` controls source-side behaviour:
      * "Copy" (default): leaves the source file intact — safer
      * "Move": deletes source after copy — saves disk space but
        risks data loss on cross-device move failures (see AoT
        S01E05/E06 incident that prompted the default change)
      * "Hardlink": same-volume only, no extra disk space
    """
    download_id: str
    import_mode: str = "Copy"


class RetryImportResponse(BaseModel):
    ok: bool
    imported_count: int = 0
    command_id: int | None = None
    detail: str | None = None
    destinations: list[str] | None = None
    history_warning: str | None = None


class ManualImportPreviewItem(BaseModel):
    """One file Sonarr would import — surfaced before the user
    commits so the confirmation modal can show what's about to
    happen physically on disk."""
    source_path: str
    destination_root: str
    series_title: str
    series_id: int
    episode_labels: list[str]
    episode_ids: list[int]
    quality_name: str | None = None
    release_group: str | None = None
    rejection_reasons: list[str]


class ManualImportPreviewResponse(BaseModel):
    ok: bool
    candidates: list[ManualImportPreviewItem] = []
    detail: str | None = None


@router.get(
    "/sonarr/preview-import",
    response_model=ManualImportPreviewResponse,
)
async def sonarr_preview_import(
    download_id: str,
    session: AsyncSession = Depends(get_session),
) -> ManualImportPreviewResponse:
    """Show what Sonarr WOULD do for a stuck import without actually
    triggering it. The frontend confirmation modal calls this to
    populate source path, destination path, episode mapping, etc.
    so the user knows exactly what they're authorising.
    """
    if not download_id.strip():
        raise HTTPException(400, "download_id is required.")

    cfg = await _load_sonarr_config(session)
    try:
        cands = await preview_manual_import(cfg, download_id=download_id)
    except SonarrError as e:
        raise HTTPException(400, str(e)) from e

    if not cands:
        return ManualImportPreviewResponse(
            ok=False,
            detail=(
                "Sonarr has no candidates for this download — the files "
                "may have already been imported, deleted from the download "
                "client, or the queue entry is stale."
            ),
        )

    return ManualImportPreviewResponse(
        ok=True,
        candidates=[
            ManualImportPreviewItem(
                source_path=c.source_path,
                destination_root=c.destination_root,
                series_title=c.series_title,
                series_id=c.series_id,
                episode_labels=c.episode_labels,
                episode_ids=c.episode_ids,
                quality_name=c.quality_name,
                release_group=c.release_group,
                rejection_reasons=c.rejection_reasons,
            )
            for c in cands
        ],
    )


@router.post("/sonarr/retry-import", response_model=RetryImportResponse)
async def sonarr_retry_import(
    payload: RetryImportRequest,
    session: AsyncSession = Depends(get_session),
) -> RetryImportResponse:
    """Force Sonarr to import a stuck "Downloaded - Unable to Import
    Automatically" entry. Sonarr knows the right answer (grab history
    confirms the series); its safety check is just refusing to act
    on it. We call the manual-import API which is what the user would
    do in Sonarr's UI — Sonarr accepts the mapping it already computed.

    Returns ok=true with the count of files Sonarr accepted. ok=false
    when Sonarr rejected the import (file moved away, hardlink-only
    filesystem with copy mode mismatch, etc.) — the detail field has
    the specific reason.

    The CACHE-INVALIDATION concern: after this succeeds, the queue
    entry typically disappears within 1-2 seconds (Sonarr completes
    the import + cleans up). Our queue cache is 0.5s TTL so the
    popup picks up the new state on its next poll. No manual cache
    clear needed.
    """
    if not payload.download_id.strip():
        raise HTTPException(400, "download_id is required.")

    cfg = await _load_sonarr_config(session)
    try:
        result = await retry_manual_import(
            cfg,
            download_id=payload.download_id,
            import_mode=payload.import_mode,
        )
    except SonarrError as e:
        raise HTTPException(400, str(e)) from e

    return RetryImportResponse(
        ok=result.ok,
        imported_count=result.imported_count,
        command_id=result.command_id,
        detail=result.detail,
        destinations=result.destinations,
        history_warning=result.history_warning,
    )


# ─────────────────────────────────────────────────────────────────────
# /heal-unmatched — use Sonarr metadata to fix files Kira couldn't match
# ─────────────────────────────────────────────────────────────────────
#
# Kira's matcher can fail in real-world ways: AniDB banned, TVDB title
# search misses because the filename used a year that's wrong (e.g.
# "Frieren.S02E08.2026.mkv" when Frieren's TVDB year is 2024), or the
# filename uses a romanization the matcher's trigram doesn't see.
#
# But Sonarr ALREADY KNOWS what these files are — it just imported them
# with full TVDB metadata. So when Kira can't match a file, ask Sonarr.
# We resolve by checking if the file's path is under any Sonarr series'
# root folder; if so, that Sonarr series IS the answer.
#
# Triggered two ways:
#   1. Automatic — App.tsx fires this after `kira:request-rescan` (which
#      itself fires when a Sonarr download finishes). New imports get
#      pinned without any user click.
#   2. Manual — popup's "Sync from Sonarr" button on low-confidence
#      clusters lets the user heal existing stuck files.
#
# Falls through gracefully when Sonarr isn't configured (400 → empty
# heal). Doesn't override existing manual pins. Anime files prefer
# AniDB cross-ref via Fribb (the user's metadata stays anime-centric).


class SonarrHealRequest(BaseModel):
    """Optional scoping. With no body, heals every no_match + low-
    confidence file in the library. With `file_ids`, heals only those.
    """
    file_ids: list[int] | None = None
    # Confidence cutoff — files whose best Match scored below this AND
    # aren't user-pinned are eligible for heal. 0.50 is the same "needs
    # review" threshold the UI uses for its low-confidence banner. Clamped to
    # [0,1]: an unbounded value (e.g. 2.0) would make EVERY file "below
    # threshold" and re-pin the whole library to Sonarr metadata in one call.
    confidence_threshold: float = Field(0.50, ge=0.0, le=1.0)


class SonarrHealResponse(BaseModel):
    ok: bool
    healed: int = 0
    skipped: int = 0
    no_sonarr_match: int = 0
    series_pinned: int = 0  # distinct Sonarr series that supplied a heal
    detail: str | None = None


def _normalize_path(p: str) -> str:
    """Cross-platform path comparison. Sonarr's stored series.path uses
    whatever the Sonarr host's OS prefers (forward / on Linux, back \\
    on Windows); Kira's file_path uses the host OS Kira runs on. Both
    might point at the same logical location via different volume
    mounts. Normalize to lowercase + forward-slashes for prefix tests.
    """
    return p.replace("\\", "/").rstrip("/").lower()


@router.post("/sonarr/heal-unmatched", response_model=SonarrHealResponse)
async def sonarr_heal_unmatched(
    payload: SonarrHealRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> SonarrHealResponse:
    """For files Kira couldn't match (or matched at low confidence),
    use Sonarr's authoritative metadata to pin a high-confidence
    Match row.

    Algorithm:
      1. Fetch Sonarr's `/api/v3/series` — list of every series with
         path + tvdbId + title + year + images + seriesType.
      2. Index by normalized path (longest-first so nested series
         folders match before parent media roots).
      3. Find Kira's heal-candidate files (no_match OR low-confidence,
         excluding user-pinned).
      4. Group them by which Sonarr series owns their path.
      5. For each group: call bulk_select_manual_match with provider=
         tvdb (or anidb for anime via Fribb cross-ref), provider_id=
         tvdbId, and the resolved metadata. The bulk endpoint handles
         the per-file UNIQUE collision logic, marks each match
         is_manual=True (sticky across heal cycles), and triggers
         downstream enrichment via the auto-heal loop.
    """
    payload = payload or SonarrHealRequest()

    # 1. Load Sonarr config — graceful empty heal on misconfigure.
    try:
        cfg = await _load_sonarr_config(session)
    except HTTPException as e:
        return SonarrHealResponse(ok=False, detail=str(e.detail))

    # 2. Fetch Sonarr's series list (one HTTP call, no caching needed
    #    here — heal is invoked at most once every 5s via the rescan
    #    debounce, plus on explicit user click).
    from kira.integrations.sonarr import _client as _sonarr_client
    try:
        async with _sonarr_client(cfg) as c:
            # Relative path (no leading slash) so the client's base_url URL
            # base — e.g. "/nickflix" for reverse-proxy users — is preserved.
            # A leading slash would be treated as absolute and drop it.
            r = await c.get("api/v3/series")
            if r.status_code != 200:
                return SonarrHealResponse(
                    ok=False,
                    detail=f"Sonarr /series returned HTTP {r.status_code}.",
                )
            sonarr_series = r.json()
    except Exception as e:
        return SonarrHealResponse(ok=False, detail=f"Sonarr unreachable: {e}")

    if not isinstance(sonarr_series, list) or not sonarr_series:
        return SonarrHealResponse(
            ok=True, detail="Sonarr returned no series — nothing to match against."
        )

    # 3. Build (normalized_path, series_dict) sorted longest-first so
    #    nested paths win over parent roots when both could match.
    path_index: list[tuple[str, dict[str, Any]]] = []
    for s in sonarr_series:
        if not isinstance(s, dict):
            continue
        path = s.get("path")
        tvdb_id = s.get("tvdbId")
        if not isinstance(path, str) or not isinstance(tvdb_id, int) or tvdb_id <= 0:
            continue
        path_index.append((_normalize_path(path), s))
    path_index.sort(key=lambda x: len(x[0]), reverse=True)

    if not path_index:
        return SonarrHealResponse(
            ok=True,
            detail="Sonarr has no series with usable TVDB ids. Add or refresh series in Sonarr first.",
        )

    # 4. Query Kira's heal candidates. Excludes user-pinned (is_manual)
    #    matches — never override a user decision.
    from sqlalchemy import or_
    from sqlalchemy.orm import selectinload
    from kira.models import Match

    stmt = (
        select(MediaFile)
        .options(selectinload(MediaFile.matches))
    )
    if payload.file_ids:
        stmt = stmt.where(MediaFile.id.in_(payload.file_ids))
    else:
        # Broad sweep: no_match files OR files whose best match is
        # below the confidence threshold (and isn't a manual pin).
        stmt = stmt.where(
            or_(
                MediaFile.status == "no_match",
                MediaFile.status == "matched",  # may include low-conf
                MediaFile.status == "matching",
            )
        )
    all_files = list(await session.scalars(stmt))

    # Filter to actual heal candidates: skip files with a strong manual
    # pin, or files whose best auto-match already cleared the threshold.
    heal_candidates: list[MediaFile] = []
    for mf in all_files:
        has_manual_pin = any(m.is_selected and m.is_manual for m in mf.matches)
        if has_manual_pin:
            continue
        best_conf = max(
            (m.confidence for m in mf.matches if m.is_selected),
            default=0.0,
        )
        if mf.status == "no_match" or best_conf < payload.confidence_threshold:
            heal_candidates.append(mf)

    if not heal_candidates:
        return SonarrHealResponse(
            ok=True, detail="No unmatched / low-confidence files to heal."
        )

    # 5. Group candidates by which Sonarr series owns their path.
    groups: dict[int, dict[str, Any]] = {}  # tvdb_id → { series, file_ids, ... }
    no_match_count = 0
    for mf in heal_candidates:
        file_path_norm = _normalize_path(mf.file_path)
        owning_series: dict[str, Any] | None = None
        for spath, sdata in path_index:
            # Prefix match using a trailing "/" boundary so /media/anime
            # doesn't accidentally match /media/animals.
            if file_path_norm.startswith(spath + "/"):
                owning_series = sdata
                break
        if owning_series is None:
            no_match_count += 1
            continue
        tvdb_id = int(owning_series["tvdbId"])
        if tvdb_id not in groups:
            groups[tvdb_id] = {"series": owning_series, "file_ids": []}
        groups[tvdb_id]["file_ids"].append(mf.id)

    if not groups:
        return SonarrHealResponse(
            ok=True,
            no_sonarr_match=no_match_count,
            detail="No files were under a Sonarr-managed folder.",
        )

    # 6. For each Sonarr series group, build the manual-match payload
    #    and apply via bulk_select_manual_match. Prefer AniDB AID for
    #    anime series (better metadata: native titles, alt titles,
    #    cour routing) and fall back to TVDB.
    from kira.api.matches import BulkSelectManualPayload, bulk_select_manual_match
    from kira.providers.anime_mappings import AnimeMappings

    healed_total = 0
    series_pinned = 0
    for tvdb_id, group in groups.items():
        s = group["series"]
        file_ids: list[int] = group["file_ids"]
        series_type = (s.get("seriesType") or "").lower()
        is_anime = series_type == "anime"
        media_type = "anime" if is_anime else "tv"

        provider = "tvdb"
        provider_id: str = str(tvdb_id)
        if is_anime:
            # Cross-ref to AniDB so the user gets anime-native metadata.
            # We use the lowest-season AID we can find; bulk_select_manual_match
            # then fans across cours per file via the existing cour-routing
            # helper (Bleach S17 etc.).
            try:
                aid = await AnimeMappings.aid_by_tvdb(tvdb_id)
            except Exception:
                aid = None
            if aid is not None:
                provider = "anidb"
                provider_id = str(aid)

        # Extract poster URL from Sonarr's images array. Sonarr returns
        # an `images: [{coverType, url, remoteUrl}, ...]` array; we want
        # coverType=poster preferably. remoteUrl is the absolute CDN
        # URL; url is the Sonarr-relative path (only useful via their
        # proxy). Prefer remoteUrl.
        poster_url: str | None = None
        for img in s.get("images") or []:
            if not isinstance(img, dict):
                continue
            if img.get("coverType") == "poster":
                poster_url = img.get("remoteUrl") or img.get("url")
                if poster_url:
                    break

        title = s.get("title") or None
        year = s.get("year") if isinstance(s.get("year"), int) else None
        overview = s.get("overview") if isinstance(s.get("overview"), str) else None

        try:
            result = await bulk_select_manual_match(
                BulkSelectManualPayload(
                    file_ids=file_ids,
                    provider=provider,
                    provider_id=provider_id,
                    title=title,
                    year=year,
                    poster_url=poster_url,
                    overview=overview,
                    media_type=media_type,
                ),
                session=session,
            )
            updated = int(result.get("updated", 0))
            healed_total += updated
            if updated > 0:
                series_pinned += 1
        except Exception as e:
            # Don't fail the whole heal on one bad series — log and
            # continue with the rest. Common cause: cour-routing-table
            # build failure for AniDB AID; the bulk function handles
            # it gracefully but might still raise on edge cases.
            logger.warning(f"sonarr heal: failed to pin {len(file_ids)} files to {provider}:{provider_id}: {e!r}")
            continue

    return SonarrHealResponse(
        ok=True,
        healed=healed_total,
        no_sonarr_match=no_match_count,
        series_pinned=series_pinned,
    )
