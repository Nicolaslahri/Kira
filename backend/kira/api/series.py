"""Series episode-list endpoint — used by the CoverPopup to lazy-fetch
the provider's authoritative episode list (titles, air dates, overviews)
and overlay it on the rows synthesized from local files.

Without this, the popup can only show episodes for which the user has a
file (and titles only when the scan-time _match_cluster path populated
them). With this, even episodes the user is missing show up as blank-left
rows labeled with the real title — so "missing E13" reads as "missing
E13 — Frieren the Slayer" instead of disappearing.
"""

from __future__ import annotations

import logging

import asyncio
from typing import Any

import httpx
from cachetools import TTLCache
from fastapi import APIRouter, HTTPException

from kira.matcher.engine import registry_from_settings
from kira.providers.base import ProviderKey

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/series", tags=["series"])

# ── Bounded process-level cache (Autopsy 5) ────────────────────────────
# Previously a handwritten `dict[..., (timestamp, episodes)]` with TTL
# enforced *lazily* — entries only expired when re-requested. A user who
# browsed 500 shows would accumulate 500 episode-list payloads in process
# memory forever; for shows with hundreds of episodes that's tens to
# hundreds of MB of pure leak until OOM-killed.
#
# `cachetools.TTLCache` enforces BOTH:
#   - maxsize: hard upper bound on entry count. LRU eviction when full.
#   - ttl:     per-entry expiry; expired entries return None on .get().
# Together this gives proper "memory-bounded cache with stale eviction"
# semantics — the original intent of the timestamp-dict design.
#
# Tuning:
#   maxsize = 1024 series.   A library of 1024 unique (provider, id, season)
#                            combos at ~50 KB/episode-list each ≈ 50 MB
#                            ceiling, ~1.5 MB typical. Bounded.
#   ttl = 24h.               Same TTL we had — long enough that repeat
#                            popup opens are free, short enough that
#                            stale Fribb mappings self-heal within a day.
#
# The asyncio.Lock guards concurrent get/set, since async handlers can
# interleave and TTLCache.__setitem__ mutates internal state.
_episodes_cache: TTLCache[tuple[str, str, int | None], list[dict[str, Any]]] = TTLCache(
    maxsize=1024, ttl=24 * 3600,
)
_episodes_cache_lock = asyncio.Lock()


@router.get("/{provider}/{provider_id}/episodes")
async def list_series_episodes(
    provider: ProviderKey,
    provider_id: str,
    season: int | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return the provider's full episode list for one series / season.

    AniDB ignores `season` (it has no season concept; returns all regular
    episodes for the AID). TMDB / TVDB require a `season` and return that
    season's episodes only.

    `force_refresh=true` bypasses cache. Use when you suspect stale data
    (e.g. after a manual rematch, after a Fribb refresh).
    """
    cache_key = (provider, provider_id, season)
    if not force_refresh:
        async with _episodes_cache_lock:
            # TTLCache.get returns None when the entry is missing OR
            # when it's expired (it actively evicts expired entries on
            # access — no manual timestamp check needed).
            cached_eps = _episodes_cache.get(cache_key)
        if cached_eps is not None:
            return {
                "provider": provider, "provider_id": provider_id,
                "season": season, "episodes": cached_eps,
            }

    async with httpx.AsyncClient() as client:
        registry = await registry_from_settings(client)
        if not registry.has(provider):
            raise HTTPException(400, f"{provider} is not configured.")
        try:
            p = registry.build(provider)
        except (ValueError, NotImplementedError) as e:
            raise HTTPException(400, str(e)) from e
        if not hasattr(p, "get_episodes"):
            raise HTTPException(400, f"{provider} doesn't support episode listings.")

        # ── AniDB native FIRST, cross-ref FALLBACK ────────────────────
        # PRIOR strategy: TVDB cross-ref first (for English titles), fall
        # back to AniDB native. That worked for normal-length anime where
        # TVDB and AniDB agree on episode counts (Frieren has 28 eps in
        # both). It breaks catastrophically for long-runners where TVDB
        # models the show as ~21 short "seasons" by air arc:
        #   - One Piece AID 69: AniDB has S1E1..S1E1100+ as a flat list.
        #   - TVDB cross-ref for the same show, requested with season=23
        #     (because the user's folder is "Season 23"), returns
        #     [S23E1, S23E2, ..., S23E15] — 15 episodes.
        #   - User's file is "One Piece - S23E1158" using absolute
        #     numbering. parsed.episode=1158.
        #   - Pairing: file at episode=1158 cannot find any episode
        #     in [1..15]. Every file orphaned in the popup.
        #
        # New strategy: prefer AniDB native (preserves the user's absolute
        # episode numbering), fall back to cross-ref only if AniDB is
        # empty / errored / banned. Costs us English titles for shows
        # AniDB only has in romaji — acceptable trade since unpaired
        # files are a much worse UX than romaji-titled paired files.
        # Phase 2: when the popup asks for season 0 it wants the Specials
        # card. AniDB filters specials out by default; opt in here so its
        # native call returns type=2 episodes tagged season 0. (TVDB/TMDB
        # ignore the flag — their season=0 request already returns specials.)
        want_specials = season == 0
        results = []
        if provider == "anidb":
            try:
                results = await p.get_episodes(
                    provider_id,
                    season if season is not None else 1,
                    include_specials=want_specials,
                )
            except Exception as e:
                logger.warning(f"series anidb/{provider_id} native ep lookup failed: {e!r}")
                results = []
            # Cross-ref fallback covers AniDB ban + transient errors.
            if not results:
                results = await _anidb_episodes_via_cross_ref(
                    provider_id, season, registry, client,
                )
        else:
            try:
                results = await p.get_episodes(provider_id, season if season is not None else 1)
            except Exception as e:
                logger.warning(f"series {provider}/{provider_id} ep lookup failed: {e!r}")
                results = []

    out = [
        {
            "season":   ep.season,
            "episode":  ep.episode,
            # LOAD-BEARING for absolute-numbered anime on cross-ref providers.
            # TVDB/TMDB model a long-runner's "Season 4" as local E1..E30 but
            # carry the series-wide absolute number (AoT S4E1 = abs 60, ...
            # S4E30 = abs 89). The user's files are named by absolute number
            # ("Shingeki no Kyojin - 60"). Without this field the popup pairs
            # local-1..30 episodes against absolute-60..89 files and shows
            # every file as "orphaned" even though the match is correct.
            # Same class as the scan-path _to_dicts absolute_number fix.
            "absolute_number": getattr(ep, "absolute_number", None),
            "title":    ep.title,
            "air_date": ep.air_date,
            "overview": ep.overview,
            "runtime":  ep.runtime,
        }
        for ep in results
    ]

    # Drop placeholder/untitled episodes. TVDB pre-populates a season's
    # full planned episode list with scheduled-but-untitled entries for
    # every future air date — for an ongoing show that's mid-season, that
    # means dozens of "Episode N — no title yet" rows polluting the popup
    # alongside the actually-aired episodes. The user can't act on any
    # of them (no file matches, no real metadata), so they're pure noise.
    #
    # Filter: keep only episodes with a non-empty title. If a file on
    # disk corresponds to one of the dropped episodes (rare — would need
    # a leaker with a future episode), it falls through to the popup's
    # "orphan files" section at the bottom of the list, where the user
    # can manually re-match. Either way, the popup never shows blank
    # "Episode N — null" rows.
    out = [ep for ep in out if ep.get("title") and ep["title"].strip()]

    # Don't cache empty results — a transient failure (AniDB ban, network
    # blip, provider 5xx) shouldn't poison the cache for the whole
    # process lifetime. The cross-ref fallback above already handles the
    # ban case; if BOTH AniDB and the cross-ref returned nothing, retry
    # on the next call so we recover when conditions change.
    if out:
        async with _episodes_cache_lock:
            # TTLCache stamps each insert with the current time; expiry
            # + LRU eviction handled internally on next .get / .__setitem__.
            _episodes_cache[cache_key] = out
    return {"provider": provider, "provider_id": provider_id, "season": season, "episodes": out}


@router.post("/cache/clear")
async def clear_episodes_cache() -> dict[str, int]:
    """Admin: wipe the process-level episode cache.

    Useful after a Fribb refresh / AniDB unban / external metadata change
    that the in-process TTL hasn't caught up to. The frontend's series
    fetcher (`lib/episodes.ts`) ALSO caches per-tab — clients should
    refresh the page to invalidate that side.
    """
    async with _episodes_cache_lock:
        count = len(_episodes_cache)
        _episodes_cache.clear()
    return {"cleared": count}


async def _anidb_episodes_via_cross_ref(
    aid: str,
    season: int | None,
    registry,
    client: httpx.AsyncClient,
):
    """Fallback path when AniDB can't deliver episode titles (ban, error).

    Uses Fribb's anime-list cross-reference: AID → TVDB series id +
    season number → TVDB get_episodes. The cross-ref is already loaded
    in memory by the matcher's other consumers; this just reads it.

    Returns [] on any failure (no Fribb mapping, no TVDB key, TVDB call
    errors, etc.). Callers fall back to the AniDB result (which may be
    empty) without complaint.
    """
    try:
        from kira.providers.anime_mappings import AnimeMappings
        aid_i = int(aid)
    except (ValueError, TypeError):
        return []

    # Cross-ref order honors `matching.anime_crossref_order` (default TVDB-first
    # — richer titles + English by default; TMDB as backup).
    #
    # Earlier-bug-history note: this helper used to .model_copy() each
    # returned episode to force season=1 (matching AniDB's native
    # "no-season" contract). That was the WRONG layer to normalize — the
    # popup pairs files↔episodes by `(season, episode)` tuple, and files
    # for Fribb-mapped anime keep their REAL season number (4 for
    # Rent-a-Girlfriend S4, 2 for Frieren S2, etc.) because the matcher
    # canonicalizes via Fribb. Forcing pe.season=1 produced pairs of
    # (4, 1) files ↔ (1, 1) episodes → no pair → "File is orphaned"
    # on every row even though both sides existed. We now pass through
    # the provider's real season number; the frontend pairing logic
    # falls back to absolute-episode matching when (season, episode)
    # misses, so the AniDB-native case (no real season) still works.
    from kira.matcher.engine import resolve_anime_crossref_order, _load_db_settings
    order = resolve_anime_crossref_order(await _load_db_settings())
    # Fribb usually carries the season number; if not, fall back to the
    # season arg the caller passed (popup's seasonForFetch).
    cross_season = await AnimeMappings.tvdb_season(aid_i) or season or 1

    for key in order:
        try:
            if key == "tvdb":
                tvdb_id = await AnimeMappings.tvdb_id(aid_i)
                if tvdb_id and registry.has("tvdb"):
                    return await registry.build("tvdb").get_episodes(str(tvdb_id), cross_season)
            elif key == "tmdb":
                tmdb_id = await AnimeMappings.tmdb_tv_id(aid_i)
                if tmdb_id and registry.has("tmdb"):
                    return await registry.build("tmdb").get_episodes(str(tmdb_id), cross_season)
        except Exception as e:
            logger.warning(f"series cross-ref {key.upper()} failed for AID {aid}: {e!r}")

    return []


_episode_meta_cache: dict[tuple, list] = {}


async def resolve_episode_meta(selected, season, episode, registry, client):
    """Best-effort cross-ref `EpisodeResult` (title / overview / air_date) for a
    matched episode. AniDB anime carries no per-episode titles of its own, so we
    resolve through the Fribb TVDB/TMDB cross-ref (the same path the popup uses);
    a direct TVDB/TMDB match queries that provider. Cached per (provider, id,
    season) so a batch of one series' episodes shares a single provider fetch.
    Returns the matching EpisodeResult, or None on any miss."""
    if episode is None or selected is None or not selected.provider_id:
        return None
    prov = (selected.provider or "").lower()
    key = (prov, str(selected.provider_id), season)
    eps = _episode_meta_cache.get(key)
    if eps is None:
        try:
            if prov == "anidb":
                # AniDB's native episode list carries per-episode titles — use it
                # FIRST (exactly like the popup endpoint), so the rename/NFO get
                # the SAME title the popup shows. The TVDB/TMDB cross-ref is the
                # fallback for an AniDB ban / error.
                try:
                    if registry is not None and registry.has("anidb"):
                        eps = await registry.build("anidb").get_episodes(
                            str(selected.provider_id), season if season is not None else 1)
                except Exception:
                    eps = None
                if not eps:
                    eps = await _anidb_episodes_via_cross_ref(
                        selected.provider_id, season, registry, client)
            elif prov in ("tvdb", "tmdb") and registry is not None and registry.has(prov):
                eps = await registry.build(prov).get_episodes(
                    str(selected.provider_id), season if season is not None else 1)
            else:
                eps = []
        except Exception:
            eps = []
        if len(_episode_meta_cache) > 256:   # bounded — long sessions don't leak
            _episode_meta_cache.clear()
        _episode_meta_cache[key] = list(eps or [])
        eps = _episode_meta_cache[key]
    # `episode_number` is the matcher's AUTHORITATIVE number (absolute for anime,
    # season-local for ordinary TV). Match THAT — AniDB stores it as `episode`
    # (season always 1), TVDB/TMDB carry it as `absolute_number`. We must NEVER
    # match the cour-LOCAL number (e.g. 11) against `episode`: against AniDB's
    # absolute list that collides with absolute episode 11 (a 2000-era One Piece
    # episode) instead of absolute 1166 — which is exactly the "Captain Kuro"
    # title that landed in a 2026 episode's NFO.
    abs_no = getattr(selected, "episode_number", None)
    match = None
    if abs_no is not None:
        match = (next((e for e in eps if getattr(e, "episode", None) == abs_no), None)
                 or next((e for e in eps if getattr(e, "absolute_number", None) == abs_no), None))
    # Only when the match carries NO authoritative number do we fall back to the
    # (season, episode) tuple — season-guarded so AniDB's season-1 absolute list
    # can't masquerade as a season-relative hit.
    if match is None and abs_no is None and episode is not None and season is not None:
        match = next((e for e in eps
                      if getattr(e, "episode", None) == episode
                      and getattr(e, "season", None) == season), None)
    return match
