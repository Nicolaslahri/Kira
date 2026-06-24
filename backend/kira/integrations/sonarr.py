"""Sonarr REST client — `/api/v3/` surface.

User-owned, runs in the same network as Kira (typical Docker stack:
`http://sonarr:8989`). Auth via the `X-Api-Key` header pulled from
Sonarr's Settings → General → Security page.

This module is a thin REST wrapper, NOT a metadata provider. It
PUSHES actions (add series, search episodes) rather than pulling
metadata. The matcher never calls this; only the `/integrations/sonarr`
API surface and (eventually) the auto-rescan webhook do.

Calls are scoped to a per-request `httpx.AsyncClient` so a misconfigured
Sonarr (wrong URL, unreachable host) can't poison a long-lived client
shared with anything else.
"""
from __future__ import annotations

import logging

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from kira.integrations.arr_paths import translate_path as _translate_path

logger = logging.getLogger(__name__)

# Sonarr's API timeout is conservatively short. Their endpoints typically
# respond in <500ms; if they're slower than 10s the user has bigger
# problems than us throttling them.
_DEFAULT_TIMEOUT = 10.0


@dataclass
class SonarrConfig:
    """Resolved Sonarr connection config. Caller builds this from
    settings (`integrations.sonarr.*`).

    `series_type` controls Sonarr's folder structure + episode-naming
    conventions for series we add: "standard" (S01E01) / "anime"
    (absolute numbering) / "daily" (yyyy-mm-dd). Picked per series-
    flavor — TV matches use the user's TV setting, AniDB matches use
    the user's Anime setting.

    `season_folders` toggles whether Sonarr creates per-season
    subfolders under the series root (`Season 01/`, `Season 02/`).

    `monitor_new_seasons` is Sonarr's behavior when a series we
    monitor airs a brand-new season: "all" (auto-monitor and search),
    "future" (monitor but don't auto-search), "none" (manual review).
    """
    base_url: str        # e.g. "http://sonarr:8989" or with URL base appended
    api_key: str
    quality_profile_id: int | None = None
    root_folder_path: str | None = None
    series_type: str = "standard"   # standard | anime | daily
    season_folders: bool = True
    monitor_new_seasons: str = "all"  # all | future | none


@dataclass
class SonarrSeriesType:
    """Type tag Sonarr stores on each series — drives folder structure
    and episode-naming defaults. Standard = SxxExx-named live-action /
    western TV; Anime = absolute-number-named, separate root folder."""
    value: Literal["standard", "anime", "daily"]


class SonarrError(Exception):
    """Raised when Sonarr returns a non-2xx or its response is unusable.

    Carries the upstream status code + body snippet so the UI can show
    something more helpful than "request failed."
    """
    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


def _client(cfg: SonarrConfig) -> httpx.AsyncClient:
    """Construct a per-call httpx client with auth + sane timeout.

    Per-call (not module-level) because misconfigured base_url shouldn't
    persistently break the rest of Kira's HTTP pool. The client is
    short-lived; `async with` cleanup happens at the call site.
    """
    from kira.url_guard import validate_outbound_url
    try:
        validate_outbound_url(cfg.base_url)  # SSRF guard (LAN URLs still allowed)
    except ValueError as e:
        raise SonarrError(f"Sonarr URL rejected: {e}") from e
    # Trailing slash is load-bearing: httpx joins a RELATIVE request path
    # (e.g. "api/v3/system/status") onto the base_url's full path, so a
    # reverse-proxy URL base ("/nickflix") survives. A LEADING-slash request
    # path would be treated as absolute and discard the base path entirely
    # (the UrlBase-302 bug). Every call site below uses the relative form.
    return httpx.AsyncClient(
        base_url=cfg.base_url.rstrip("/") + "/",
        headers={
            "X-Api-Key": cfg.api_key,
            "Accept": "application/json",
        },
        timeout=_DEFAULT_TIMEOUT,
    )


async def test_connection(cfg: SonarrConfig) -> dict[str, Any]:
    """Verify the URL + API key combo works. Returns Sonarr's
    `/system/status` payload (version, branch, runtime info).

    Raises SonarrError on any failure — the caller (the test endpoint
    in api/integrations.py) translates that into a 4xx for the UI.
    """
    async with _client(cfg) as c:
        try:
            r = await c.get("api/v3/system/status")
        except httpx.RequestError as e:
            raise SonarrError(f"Cannot reach Sonarr at {cfg.base_url}: {e}") from e
        if r.status_code == 401:
            raise SonarrError("Sonarr rejected the API key (401).", status=401, body=r.text[:200])
        if r.status_code != 200:
            raise SonarrError(
                f"Sonarr returned HTTP {r.status_code} on /system/status",
                status=r.status_code,
                body=r.text[:200],
            )
        try:
            return r.json()
        except ValueError as e:
            raise SonarrError(f"Sonarr returned non-JSON on /system/status: {e}") from e


async def rescan_series_by_tvdb(cfg: SonarrConfig, tvdb_id: int) -> bool:
    """Post-rename hook: tell Sonarr to re-scan one series' folder NOW.

    Why: when Kira renames/moves an episode file, Sonarr's next disk scan sees
    the OLD path gone, marks the episode file deleted, and — if the episode is
    monitored — may re-grab it. An immediate `RescanSeries` command closes that
    window: Sonarr re-parses the renamed files and re-links them, so nothing
    ever reads as deleted.

    Best-effort by design — returns True when the rescan command was accepted,
    False for "series not in Sonarr" or ANY error. Never raises: a Sonarr
    hiccup must not affect the rename result this hook runs after.
    """
    try:
        async with _client(cfg) as c:
            series = await _find_series_by_tvdb(c, int(tvdb_id))
            if not series or not series.get("id"):
                return False  # not a Sonarr-managed show — nothing to do
            cmd = await c.post(
                "api/v3/command",
                json={"name": "RescanSeries", "seriesId": series["id"]},
            )
            return cmd.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"sonarr: post-rename rescan for tvdb {tvdb_id} failed (non-fatal): {e!r}")
        return False


# `_translate_path` (the Kira↔*arr mount bridge) now lives in
# `kira.integrations.arr_paths.translate_path`, shared with Radarr, and is
# imported above as `_translate_path` for back-compat with this module's callers
# and tests.


async def relink_series(
    cfg: SonarrConfig,
    tvdb_id: int,
    *,
    old_root: str | None = None,
    new_root: str | None = None,
) -> tuple[bool, bool, str]:
    """Keep Sonarr's series path in sync with Kira's folder, THEN rescan.

    Supersedes a bare `rescan_series_by_tvdb` for the rename/undo hooks: when
    Kira renames a series FOLDER, Sonarr's stored path goes stale — its next
    scan finds the old path gone, marks every episode file deleted, and (if
    monitored) may re-grab them. We translate Kira's NEW folder into Sonarr's
    path namespace and `PUT` it with `moveFiles=false` (Kira already moved the
    files), so Sonarr re-links them in place. Undo passes the roots reversed.

    Returns (ok, changed, detail): `ok` = the rescan was accepted; `changed` =
    the stored path was actually updated; `detail` is a short human string for
    the notification. Best-effort — never raises."""
    try:
        async with _client(cfg) as c:
            series = await _find_series_by_tvdb(c, int(tvdb_id))
            if not series or not series.get("id"):
                return False, False, "not in Sonarr"
            changed = False
            note = ""
            arr_old = series.get("path") or ""
            if old_root and new_root and old_root != new_root and arr_old:
                arr_new = _translate_path(arr_old, old_root, new_root)
                if arr_new is None:
                    note = "couldn't map the new path"
                elif arr_new != arr_old:
                    series["path"] = arr_new
                    pr = await c.put(
                        f"api/v3/series/{series['id']}",
                        params={"moveFiles": "false"},
                        json=series,
                    )
                    if pr.status_code in (200, 202):
                        changed = True
                    else:
                        note = f"path update failed (HTTP {pr.status_code})"
            cmd = await c.post(
                "api/v3/command",
                json={"name": "RescanSeries", "seriesId": series["id"]},
            )
            ok = cmd.status_code in (200, 201)
            if changed:
                detail = f"path → {series['path']}"
            elif note:
                detail = f"{note}; rescanned"
            else:
                detail = "rescanned"
            return ok, changed, detail
    except Exception as e:
        logger.warning(f"sonarr: relink for tvdb {tvdb_id} failed (non-fatal): {e!r}")
        return False, False, f"error ({type(e).__name__})"


async def list_quality_profiles(cfg: SonarrConfig) -> list[dict[str, Any]]:
    """Fetch the user's Sonarr quality profiles so the UI can offer a
    real dropdown (instead of having them paste a numeric id blind).
    """
    async with _client(cfg) as c:
        r = await c.get("api/v3/qualityprofile")
        if r.status_code != 200:
            raise SonarrError(
                f"Sonarr /qualityprofile returned HTTP {r.status_code}",
                status=r.status_code,
                body=r.text[:200],
            )
        data = r.json()
        if not isinstance(data, list):
            raise SonarrError("Sonarr /qualityprofile returned non-list")
        return data


async def list_root_folders(cfg: SonarrConfig) -> list[dict[str, Any]]:
    """Fetch Sonarr's configured root folders (where new series are
    saved). Same UX rationale as quality profiles — surface real
    options instead of free-typed paths."""
    async with _client(cfg) as c:
        r = await c.get("api/v3/rootfolder")
        if r.status_code != 200:
            raise SonarrError(
                f"Sonarr /rootfolder returned HTTP {r.status_code}",
                status=r.status_code,
                body=r.text[:200],
            )
        data = r.json()
        if not isinstance(data, list):
            raise SonarrError("Sonarr /rootfolder returned non-list")
        return data


async def _find_series_by_tvdb(c: httpx.AsyncClient, tvdb_id: int) -> dict[str, Any] | None:
    """Look up an EXISTING series in the user's Sonarr by TVDB id.

    Sonarr's `/series` returns the full library; we filter in memory
    because there's no native filter-by-tvdb-id query param. Libraries
    of 500+ series resolve in a few KB of JSON, so this is fine.
    """
    r = await c.get("api/v3/series")
    if r.status_code != 200:
        raise SonarrError(
            f"Sonarr /series returned HTTP {r.status_code}",
            status=r.status_code,
            body=r.text[:200],
        )
    items = r.json()
    if not isinstance(items, list):
        return None
    for s in items:
        if isinstance(s, dict) and s.get("tvdbId") == tvdb_id:
            return s
    return None


async def _add_series(
    c: httpx.AsyncClient,
    cfg: SonarrConfig,
    tvdb_id: int,
) -> dict[str, Any]:
    """Add a new series to Sonarr using its TVDB id.

    Flow per Sonarr's documented API:
      1. POST `/series/lookup?term=tvdb:{id}` → returns the series'
         shape as if it WERE added (title, images, year, status, etc.)
      2. POST `/series` with that shape augmented with the user's
         quality profile + root folder + monitoring options.

    All series-type / season-folder / monitor behaviour comes from
    `cfg`, which the caller built from the per-flavor settings (TV or
    Anime). Earlier versions hardcoded `seriesType` from a bare
    `is_anime` flag and `monitor: all` for new-season behaviour;
    those are now config-driven so the user controls them in Settings.
    """
    # Step 1: lookup
    r = await c.get("api/v3/series/lookup", params={"term": f"tvdb:{tvdb_id}"})
    if r.status_code != 200:
        raise SonarrError(
            f"Sonarr /series/lookup returned HTTP {r.status_code}",
            status=r.status_code,
            body=r.text[:200],
        )
    matches = r.json()
    if not isinstance(matches, list) or not matches:
        raise SonarrError(f"Sonarr couldn't find TVDB id {tvdb_id} in its catalog.")
    series = matches[0]
    if not isinstance(series, dict):
        raise SonarrError(f"Sonarr lookup for TVDB id {tvdb_id} returned malformed data.")

    # Step 2: augment + POST
    payload = dict(series)
    payload["qualityProfileId"] = cfg.quality_profile_id
    payload["rootFolderPath"] = cfg.root_folder_path
    payload["monitored"] = True
    payload["seriesType"] = cfg.series_type
    payload["seasonFolder"] = cfg.season_folders
    payload["addOptions"] = {
        # Don't auto-search the entire backlog on add — Kira will
        # explicitly trigger per-episode searches for the missing
        # episodes only. Otherwise Sonarr would queue every episode
        # of every season, which is exactly the "blunt instrument"
        # behaviour the user is trying to avoid.
        "searchForMissingEpisodes": False,
        "searchForCutoffUnmetEpisodes": False,
        "monitor": cfg.monitor_new_seasons,
    }
    r2 = await c.post("api/v3/series", json=payload)
    if r2.status_code not in (200, 201):
        raise SonarrError(
            f"Sonarr /series (add) returned HTTP {r2.status_code}",
            status=r2.status_code,
            body=r2.text[:200],
        )
    added = r2.json()
    if not isinstance(added, dict):
        raise SonarrError("Sonarr /series (add) returned malformed data.")
    return added


async def _list_episodes(c: httpx.AsyncClient, series_id: int) -> list[dict[str, Any]]:
    """Pull every episode Sonarr knows about for a series. We need
    Sonarr's internal episode IDs to drive the EpisodeSearch command —
    EpisodeSearch takes episode IDs, not (season, number) pairs."""
    r = await c.get("api/v3/episode", params={"seriesId": series_id})
    if r.status_code != 200:
        raise SonarrError(
            f"Sonarr /episode returned HTTP {r.status_code}",
            status=r.status_code,
            body=r.text[:200],
        )
    eps = r.json()
    if not isinstance(eps, list):
        raise SonarrError("Sonarr /episode returned non-list")
    return eps


@dataclass
class SendMissingResult:
    """Structured outcome the API endpoint serialises to JSON for the
    frontend toast. `queued` is the number of distinct episode searches
    Sonarr accepted; `series_was_added` tells the UI whether a fresh
    series-add happened (which warrants a bigger toast)."""
    ok: bool
    queued: int
    series_was_added: bool
    sonarr_series_title: str | None = None
    skipped_episodes: list[int] | None = None  # asked for but not present in Sonarr's episode list
    message: str | None = None


async def send_missing_episodes(
    cfg: SonarrConfig,
    *,
    tvdb_id: int,
    season: int,
    episode_numbers: list[int],
) -> SendMissingResult:
    """One-shot: ensure series is in Sonarr, then trigger searches
    for the specified missing episodes.

    Returns SendMissingResult capturing what happened. Raises SonarrError
    only for hard failures (auth, network, Sonarr-side 5xx).

    Caller (api/integrations.py) is responsible for:
      * Resolving the TVDB id from Kira's Match row
      * De-duping / sorting episode_numbers
      * Translating SonarrError into a 4xx with the user-readable
        message embedded
    """
    if not episode_numbers:
        return SendMissingResult(
            ok=True, queued=0, series_was_added=False,
            message="No missing episodes to send.",
        )
    if cfg.quality_profile_id is None or not cfg.root_folder_path:
        raise SonarrError(
            "Sonarr default quality profile or root folder isn't set. "
            "Configure them in Settings → Integrations."
        )

    async with _client(cfg) as c:
        # Find or add the series.
        existing = await _find_series_by_tvdb(c, tvdb_id)
        if existing is not None:
            series = existing
            series_was_added = False
        else:
            series = await _add_series(c, cfg, tvdb_id)
            series_was_added = True

        series_id = series.get("id")
        if not isinstance(series_id, int):
            raise SonarrError("Sonarr series response lacked an 'id' field.")

        # Map Kira's (season, episode_number) tuples to Sonarr's
        # internal episode IDs. Sonarr just-added series may have a
        # short delay before /episode returns the list — but in
        # practice it's synchronous once /series POST returns 201.
        episodes = await _list_episodes(c, series_id)
        wanted = set(int(n) for n in episode_numbers)
        # Build a (season, number) → id index. Sonarr's anime series
        # use the same field shape (seasonNumber + episodeNumber); the
        # absolute-numbering convention lives in the file-naming stage,
        # not the episode-list shape.
        idx: dict[tuple[int, int], int] = {}
        for e in episodes:
            if not isinstance(e, dict):
                continue
            s = e.get("seasonNumber")
            n = e.get("episodeNumber")
            ep_id = e.get("id")
            if isinstance(s, int) and isinstance(n, int) and isinstance(ep_id, int):
                idx[(s, n)] = ep_id

        target_ids: list[int] = []
        skipped: list[int] = []
        for n in sorted(wanted):
            ep_id = idx.get((season, n))
            if ep_id is None:
                skipped.append(n)
            else:
                target_ids.append(ep_id)

        if not target_ids:
            return SendMissingResult(
                ok=False, queued=0,
                series_was_added=series_was_added,
                sonarr_series_title=series.get("title"),
                skipped_episodes=skipped,
                message=(
                    f"Sonarr knows the series but not the requested "
                    f"episode numbers {skipped} for season {season}. "
                    f"(Series may need a metadata refresh in Sonarr.)"
                ),
            )

        # Trigger Sonarr's auto-search for the missing episodes. WHICH release
        # gets grabbed — quality, and sub-vs-dub (via Custom Formats) — is
        # the job of the user's Sonarr quality profile, not Kira's. (Kira used
        # to run its own interactive search + guess sub/dub from release titles;
        # a fragile, slow heuristic that fought the profile, so it was removed.)
        cmd = await c.post(
            "api/v3/command",
            json={"name": "EpisodeSearch", "episodeIds": target_ids},
        )
        if cmd.status_code not in (200, 201):
            raise SonarrError(
                f"Sonarr /command (EpisodeSearch) returned HTTP {cmd.status_code}",
                status=cmd.status_code,
                body=cmd.text[:200],
            )
        return SendMissingResult(
            ok=True,
            queued=len(target_ids),
            series_was_added=series_was_added,
            sonarr_series_title=series.get("title"),
            skipped_episodes=skipped or None,
            message=None,
        )


# ─────────────────────────────────────────────────────────────────────
# Queue introspection — Phase 2: live download progress
# ─────────────────────────────────────────────────────────────────────
#
# Sonarr's `/api/v3/queue` is the source of truth for what's currently
# downloading. The endpoint returns paginated records with embedded
# series + episode metadata (when `includeSeries=true` + `includeEpisode
# =true`), so we get everything in one round-trip.
#
# Status normalization: Sonarr splits "what's happening" across THREE
# fields — `status` (queue-level), `trackedDownloadStatus` (success/
# warning/error verdict), and `trackedDownloadState` (download-client-
# level state). The popup just wants one user-readable word — we collapse
# the three into Kira's seven canonical states (queued, searching,
# downloading, importing, completed, failed, warning).


@dataclass
class SonarrQueueItem:
    """One in-flight Sonarr download, normalized for Kira's popup.

    `tvdb_id` + (season, episode_number) is the join key — Kira matches
    each item back to its episode in the popup's `providerEpisodes` list.
    The progress + ETA + release name drive the visible UI: a green
    progress-bar fill on the "missing" row, "47% · 12 min remaining ·
    [SubsPlease] Frieren - 06 [1080p].mkv" as the visible text.

    `queue_id` and `download_id` are Sonarr's identifiers we pass back
    to the retry-import endpoint. `needs_manual_import` flags the
    common "Downloaded - Unable to Import Automatically" trap so the
    popup can render a one-click fix button.
    """
    tvdb_id: int
    season: int
    episode_number: int
    episode_title: str | None
    # Normalized state — see _normalize_status for the mapping. UI maps
    # these to badge text + colors; backend doesn't decide presentation.
    status: str
    progress_pct: float       # 0..100; 0 when not yet downloading
    eta_seconds: int | None   # None when unknown (queued/searching) or done
    size_bytes: int | None
    size_left_bytes: int | None
    release_title: str | None  # The release name Sonarr's actively grabbing
    protocol: str | None       # "usenet" | "torrent"
    error_message: str | None  # Surfaces statusMessages[].messages when warn/fail
    download_client: str | None
    queue_id: int | None       # Sonarr's queue.id, needed to delete / retry
    download_id: str | None    # Sonarr's downloadId (hash from torrent client)
    needs_manual_import: bool  # Stuck: downloaded but Sonarr refuses to import


def _parse_timeleft(timeleft: Any) -> int | None:
    """Sonarr returns `timeleft` as `"HH:MM:SS"` (or omits it when
    unknown). Convert to plain seconds; bail on any malformed input
    rather than guessing. Used for the popup's "12 min remaining" hint.
    """
    if not isinstance(timeleft, str):
        return None
    parts = timeleft.split(":")
    if len(parts) != 3:
        return None
    try:
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    except (ValueError, TypeError):
        return None
    if h < 0 or m < 0 or s < 0:
        return None
    return h * 3600 + m * 60 + s


def _normalize_status(rec: dict[str, Any]) -> str:
    """Collapse Sonarr's three status fields into one Kira state.

    Priority order matters: a record that's both `status=downloading`
    AND `trackedDownloadStatus=warning` should surface as "warning"
    (the warning is the important signal — the download is happening
    but something is wrong, e.g. quality cutoff unmet, indexer reported
    a checksum mismatch). Failed states take priority over both.

    The seven Kira states the popup understands:
      * queued       — Sonarr accepted the search; waiting on download client
      * searching    — Sonarr is searching indexers (rarely seen in /queue;
                       most "searching" time happens BEFORE queue entry)
      * downloading  — Bytes are flowing; size_left > 0
      * importing    — Download finished; Sonarr is moving to library
      * completed    — Imported successfully; row will disappear next poll
      * failed       — Download or import broke; user intervention needed
      * warning      — Download in flight but Sonarr flagged a concern
    """
    status = (rec.get("status") or "").lower()
    track_status = (rec.get("trackedDownloadStatus") or "").lower()
    track_state = (rec.get("trackedDownloadState") or "").lower()

    # Hard failures first.
    if status == "failed" or track_status == "error" or track_state in ("downloadfailed", "failedpending"):
        return "failed"

    # Import lifecycle.
    if track_state in ("importpending", "importing"):
        return "importing"
    if track_state in ("imported",) or status == "completed":
        return "completed"

    # Warning (download IS happening but Sonarr flagged it).
    if track_status == "warning":
        return "warning"
    if status == "warning":
        return "warning"

    # Active download.
    if status == "downloading" or track_state == "downloading":
        return "downloading"

    # Held / paused states.
    if status in ("paused", "delay", "downloadclientunavailable", "fallback"):
        return "warning"

    # Default — Sonarr knows about it, waiting on something.
    return "queued"


async def get_queue(cfg: SonarrConfig) -> list[SonarrQueueItem]:
    """Fetch Sonarr's active download queue, normalized for Kira.

    Drops records that:
      * Are for series Sonarr doesn't have a tvdbId for (orphan queue
        items left over from a removed series — Sonarr does flush these
        but they linger briefly).
      * Don't have the embedded series/episode blocks (shouldn't happen
        with `includeSeries=true&includeEpisode=true` but defensive).

    No filtering by tvdb_id here — the API endpoint filters, since the
    same raw queue is useful for both the popup (one series) and the
    library-wide cover-card pills (every series). Caching is done at
    the endpoint layer.
    """
    async with _client(cfg) as c:
        try:
            r = await c.get("api/v3/queue", params={
                "pageSize": 200,
                "includeUnknownSeriesItems": "false",
                "includeSeries": "true",
                "includeEpisode": "true",
            })
        except httpx.RequestError as e:
            raise SonarrError(f"Cannot reach Sonarr at {cfg.base_url}: {e}") from e
        if r.status_code != 200:
            raise SonarrError(
                f"Sonarr /queue returned HTTP {r.status_code}",
                status=r.status_code,
                body=r.text[:200],
            )
        try:
            data = r.json()
        except ValueError as e:
            raise SonarrError(f"Sonarr /queue returned non-JSON: {e}") from e
        if not isinstance(data, dict):
            raise SonarrError("Sonarr /queue returned non-dict envelope.")
        records = data.get("records")
        if not isinstance(records, list):
            return []

        items: list[SonarrQueueItem] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            series = rec.get("series")
            episode = rec.get("episode")
            if not isinstance(series, dict) or not isinstance(episode, dict):
                continue
            tvdb_id = series.get("tvdbId")
            if not isinstance(tvdb_id, int) or tvdb_id <= 0:
                continue
            season = episode.get("seasonNumber")
            ep_num = episode.get("episodeNumber")
            if not isinstance(season, int) or not isinstance(ep_num, int):
                continue

            # Progress calc: Sonarr gives `size` (total bytes once known)
            # and `sizeleft` (remaining). Until the download starts they
            # can both be 0; in that case progress is 0.
            size = rec.get("size")
            size_left = rec.get("sizeleft")
            progress = 0.0
            if isinstance(size, (int, float)) and size > 0 and isinstance(size_left, (int, float)):
                # Clamp defensively — sizeleft > size is possible briefly
                # mid-grab as Sonarr re-estimates.
                progress = max(0.0, min(100.0, (float(size) - float(size_left)) / float(size) * 100.0))

            # Status messages — Sonarr surfaces things like "No files found
            # are eligible for import in ..." here. Joined into one line
            # so the popup can show a short tooltip.
            error_msg: str | None = None
            needs_manual_import = False
            sm = rec.get("statusMessages")
            if isinstance(sm, list) and sm:
                pieces: list[str] = []
                for entry in sm:
                    if not isinstance(entry, dict):
                        continue
                    msgs = entry.get("messages")
                    if isinstance(msgs, list):
                        for m in msgs:
                            if isinstance(m, str):
                                pieces.append(m)
                    elif isinstance(entry.get("title"), str):
                        pieces.append(entry["title"])
                if pieces:
                    error_msg = " · ".join(pieces[:3])
            # Detect the "stuck import" trap. Sonarr's specific behaviour:
            # the release was grabbed by series-ID (release title doesn't
            # contain the parseable series name), so on import Sonarr's
            # safety check refuses to auto-import even though grab history
            # confirms what it is. The file IS sitting in the download
            # client's completed folder. The fix is to call Sonarr's
            # manual-import API which already knows the mapping.
            #
            # CRITICAL: this is NOT the same as "No files found are
            # eligible for import." That message means Sonarr looked
            # and the files weren't there — they got moved/deleted
            # already, or Sonarr is pointing at the wrong path. Calling
            # /manualimport in that state returns nothing and the user
            # sees "Sonarr couldn't find the downloaded files anymore."
            # We deliberately exclude that case so Kira doesn't promise
            # a fix it can't deliver. The user gets the regular
            # Warning treatment for those entries instead.
            tracked_state_field = (rec.get("trackedDownloadState") or "").lower()
            haystack = (error_msg or "").lower()
            # Only the FIXABLE patterns. "Unable to Import Automatically"
            # is the canonical phrase Sonarr emits for the ID-grab
            # safety check. "Manual import required" appears in newer
            # Sonarr versions for the same situation.
            fixable_phrases = (
                "unable to import automatically",
                "manual import required",
            )
            if any(p in haystack for p in fixable_phrases):
                needs_manual_import = True
            # Belt-and-braces: Sonarr's `trackedDownloadState=importBlocked`
            # is the canonical machine-readable signal for the same
            # state on newer Sonarr versions. This bypasses the brittle
            # message-substring check entirely.
            if tracked_state_field == "importblocked":
                needs_manual_import = True

            # Status normalization with a post-pass for stuck imports.
            # Sonarr reports trackedDownloadState=importPending for items
            # that finished downloading but can't be auto-imported — the
            # same state it uses for items actively being imported. The
            # difference is in the status message: a true importing item
            # has no message OR a transient "moving file" note, while a
            # stuck item shouts "No files found are eligible for import"
            # or "Unable to import automatically". When the message
            # carries those signals, override "importing" → "warning"
            # so the UI doesn't shimmer green forever on entries that
            # will never advance without intervention.
            status = _normalize_status(rec)
            if status == "importing" and error_msg:
                err_lower = error_msg.lower()
                stuck_signals = (
                    "no files found",
                    "unable to import",
                    "no eligible",
                    "not eligible for import",
                    "manual import required",
                )
                if any(s in err_lower for s in stuck_signals):
                    status = "warning"
            items.append(SonarrQueueItem(
                tvdb_id=tvdb_id,
                season=season,
                episode_number=ep_num,
                episode_title=(
                    episode.get("title")
                    if isinstance(episode.get("title"), str) else None
                ),
                status=status,
                progress_pct=round(progress, 1),
                eta_seconds=_parse_timeleft(rec.get("timeleft")),
                size_bytes=int(size) if isinstance(size, (int, float)) and size >= 0 else None,
                size_left_bytes=int(size_left) if isinstance(size_left, (int, float)) and size_left >= 0 else None,
                release_title=(
                    rec.get("title") if isinstance(rec.get("title"), str) else None
                ),
                protocol=(
                    rec.get("protocol") if isinstance(rec.get("protocol"), str) else None
                ),
                error_message=error_msg,
                download_client=(
                    rec.get("downloadClient") if isinstance(rec.get("downloadClient"), str) else None
                ),
                queue_id=(
                    int(rec["id"]) if isinstance(rec.get("id"), (int, float)) else None
                ),
                download_id=(
                    rec.get("downloadId") if isinstance(rec.get("downloadId"), str) else None
                ),
                needs_manual_import=needs_manual_import,
            ))
        return items


# ─────────────────────────────────────────────────────────────────────
# Manual-import retry for stuck downloads
# ─────────────────────────────────────────────────────────────────────


@dataclass
class ManualImportCandidate:
    """One file Sonarr would import, surfaced via the preview API so
    the UI's confirmation modal can show source + destination before
    the user commits.

    `source_path` is where the file currently sits (download client's
    completed folder, usually). `destination_root` is the series'
    configured root — Sonarr's import will write under this path.
    The combination tells the user what's about to happen physically
    on disk; data-loss bugs in Sonarr's import are most often a
    surprise on the destination path.
    """
    source_path: str
    destination_root: str
    series_title: str
    series_id: int
    episode_labels: list[str]   # e.g. ["S01E05", "S01E06"]
    episode_ids: list[int]
    quality_name: str | None
    release_group: str | None
    rejection_reasons: list[str]   # non-empty → Sonarr says don't import


@dataclass
class ManualImportRetryResult:
    """Outcome of attempting to nudge Sonarr past a stuck import.

    `imported_count` is how many files Sonarr actually accepted. Often
    1 (one episode) but for season packs it can be higher. `command_id`
    is Sonarr's async-command handle — useful for the UI to poll if we
    want progress, though for v1 we just return after dispatch.

    `destinations` is what Sonarr's recent history reports happened
    AFTER the import command processed — the path the file landed at,
    one per episode. Populated by a follow-up history query so the
    user gets a concrete "where did my file go" answer in the toast
    rather than the silent void that caused the AoT S01 incident.
    """
    ok: bool
    imported_count: int = 0
    command_id: int | None = None
    detail: str | None = None
    destinations: list[str] | None = None
    history_warning: str | None = None


async def preview_manual_import(
    cfg: SonarrConfig,
    *,
    download_id: str,
) -> list[ManualImportCandidate]:
    """Fetch Sonarr's manualimport candidates WITHOUT triggering the
    import. The UI's confirmation modal shows the user exactly what
    Sonarr will do — source path, destination root, episode mapping,
    rejections — before they click Confirm.

    This is the v2 of "Force Import": the original v1 fired the
    import command immediately, which caused at least one data-loss
    incident (AoT S01E05+E06 vanished when Sonarr's move partially
    failed mid-flight on a cross-device move). Now the import is
    a two-step interaction: preview, then commit.
    """
    async with _client(cfg) as c:
        try:
            r = await c.get("api/v3/manualimport", params={"downloadId": download_id})
        except httpx.RequestError as e:
            raise SonarrError(f"Cannot reach Sonarr: {e}") from e
        if r.status_code != 200:
            raise SonarrError(
                f"Sonarr /manualimport returned HTTP {r.status_code}",
                status=r.status_code,
                body=r.text[:200],
            )
        candidates_raw = r.json()
        if not isinstance(candidates_raw, list):
            return []

        out: list[ManualImportCandidate] = []
        for cand in candidates_raw:
            if not isinstance(cand, dict):
                continue
            series = cand.get("series")
            episodes = cand.get("episodes") or []
            if not isinstance(series, dict):
                continue
            series_id = series.get("id")
            series_title = series.get("title") or "Unknown series"
            series_path = series.get("path") or ""
            if not isinstance(series_id, int):
                continue

            ep_ids: list[int] = []
            ep_labels: list[str] = []
            for e in episodes:
                if not isinstance(e, dict):
                    continue
                if isinstance(e.get("id"), int):
                    ep_ids.append(e["id"])
                s_no = e.get("seasonNumber")
                e_no = e.get("episodeNumber")
                if isinstance(s_no, int) and isinstance(e_no, int):
                    ep_labels.append(f"S{s_no:02d}E{e_no:02d}")

            quality_name: str | None = None
            quality = cand.get("quality")
            if isinstance(quality, dict):
                q_inner = quality.get("quality")
                if isinstance(q_inner, dict) and isinstance(q_inner.get("name"), str):
                    quality_name = q_inner["name"]

            rej_reasons: list[str] = []
            rejections = cand.get("rejections")
            if isinstance(rejections, list):
                for rej in rejections:
                    if isinstance(rej, dict) and isinstance(rej.get("reason"), str):
                        rej_reasons.append(rej["reason"])

            out.append(ManualImportCandidate(
                source_path=str(cand.get("path") or ""),
                destination_root=series_path,
                series_title=series_title,
                series_id=series_id,
                episode_labels=ep_labels,
                episode_ids=ep_ids,
                quality_name=quality_name,
                release_group=(cand.get("releaseGroup") or "") or None,
                rejection_reasons=rej_reasons,
            ))
        return out


async def _fetch_recent_import_history(
    cfg: SonarrConfig,
    *,
    episode_ids: list[int],
) -> tuple[list[str], str | None]:
    """After the import command, query Sonarr's history to see what
    ACTUALLY happened. Returns (destination_paths, warning_message).

    Sonarr's /history endpoint can be queried per-episode. For each
    episode we look at the most recent event AFTER our command fired
    and pull out the destination path from its data blob. Events with
    eventType=downloadFolderImported carry `data.importedPath` (the
    new library path) and `data.droppedPath` (the source). Events with
    eventType=downloadFailed indicate trouble.

    Best-effort: errors here don't fail the calling import — they
    just leave destination_paths empty so the toast says "Sonarr
    accepted; verify location in Sonarr's history".
    """
    destinations: list[str] = []
    warnings: list[str] = []
    try:
        async with _client(cfg) as c:
            for ep_id in episode_ids[:10]:   # cap to avoid hammering Sonarr
                try:
                    r = await c.get(
                        "api/v3/history",
                        params={
                            "episodeId": ep_id,
                            "pageSize": 5,
                            "sortKey": "date",
                            "sortDirection": "descending",
                        },
                    )
                except httpx.RequestError:
                    continue
                if r.status_code != 200:
                    continue
                try:
                    body = r.json()
                except ValueError:
                    continue
                records = body.get("records") if isinstance(body, dict) else None
                if not isinstance(records, list) or not records:
                    continue
                # Most recent event
                evt = records[0]
                if not isinstance(evt, dict):
                    continue
                etype = (evt.get("eventType") or "").lower()
                data = evt.get("data") if isinstance(evt.get("data"), dict) else {}
                if etype == "downloadfolderimported":
                    p = data.get("importedPath") or evt.get("sourceTitle")
                    if isinstance(p, str) and p:
                        destinations.append(p)
                elif etype == "downloadfailed":
                    msg = data.get("message") or "import failed"
                    warnings.append(f"Episode {ep_id}: {msg}")
    except Exception as e:
        warnings.append(f"history check failed: {e}")
    return destinations, ("; ".join(warnings) or None) if warnings else None


async def retry_manual_import(
    cfg: SonarrConfig,
    *,
    download_id: str,
    import_mode: str = "Copy",
) -> ManualImportRetryResult:
    """Force Sonarr to import a stuck "Downloaded - Unable to Import
    Automatically" entry.

    Algorithm:
      1. GET /api/v3/manualimport?downloadId=<id> — Sonarr returns
         the candidate files for that download with its best-guess
         (series, episodes, quality, languages, releaseGroup) already
         filled in. The reason auto-import refused was the safety
         check around release-vs-filename mismatch — Sonarr's parser
         already knows the right answer; it just won't act on it.
      2. Filter to files Sonarr marked importable (`rejections` is
         empty). If everything has rejections, bail and surface them.
      3. POST /api/v3/command with {name: "ManualImport", files: [...],
         importMode: "Move"} — Sonarr accepts the mapping verbatim.

    Returns ManualImportRetryResult. ok=False with a detail message
    when Sonarr couldn't resolve the files (different download id,
    files already moved out, etc.).
    """
    async with _client(cfg) as c:
        # Step 1: ask Sonarr what it sees in the download folder
        try:
            r = await c.get("api/v3/manualimport", params={"downloadId": download_id})
        except httpx.RequestError as e:
            raise SonarrError(f"Cannot reach Sonarr: {e}") from e
        if r.status_code != 200:
            raise SonarrError(
                f"Sonarr /manualimport returned HTTP {r.status_code}",
                status=r.status_code,
                body=r.text[:200],
            )
        try:
            candidates = r.json()
        except ValueError as e:
            raise SonarrError(f"Sonarr /manualimport returned non-JSON: {e}") from e
        if not isinstance(candidates, list) or not candidates:
            return ManualImportRetryResult(
                ok=False,
                detail=(
                    "Sonarr couldn't find the downloaded files anymore "
                    "(may have been moved, deleted, or the download "
                    "client purged the queue)."
                ),
            )

        # Step 2: collect importable items. Anything with `rejections`
        # is filtered out — Sonarr is refusing for a reason we can't
        # safely override (file too small, wrong format, etc.). The
        # specific "Unable to Import Automatically" trap typically
        # leaves rejections empty AND has series/episodes resolved.
        importable: list[dict[str, Any]] = []
        skipped: list[str] = []
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            rejections = cand.get("rejections")
            if isinstance(rejections, list) and rejections:
                reasons = [
                    r.get("reason") for r in rejections
                    if isinstance(r, dict) and r.get("reason")
                ]
                skipped.append("; ".join(r for r in reasons if r) or "unspecified")
                continue
            # Sonarr expects the manual-import command body to include
            # series.id, episodes[].id, quality, languages, etc. Pass
            # them through verbatim from the candidate response.
            series = cand.get("series")
            episodes = cand.get("episodes")
            if not isinstance(series, dict) or not isinstance(series.get("id"), int):
                skipped.append("series id missing")
                continue
            if not isinstance(episodes, list) or not episodes:
                skipped.append("no episodes mapped")
                continue
            file_payload: dict[str, Any] = {
                "path": cand.get("path"),
                "folderName": cand.get("folderName"),
                "seriesId": series["id"],
                "episodeIds": [
                    e["id"] for e in episodes
                    if isinstance(e, dict) and isinstance(e.get("id"), int)
                ],
                "quality": cand.get("quality"),
                "languages": cand.get("languages") or [],
                "releaseGroup": cand.get("releaseGroup") or "",
                "downloadId": download_id,
            }
            importable.append(file_payload)

        if not importable:
            return ManualImportRetryResult(
                ok=False,
                detail=(
                    "Sonarr rejected every candidate file: "
                    + " · ".join(skipped[:3])
                ),
            )

        # Step 3: trigger the import via Sonarr's command endpoint.
        # import_mode controls source-side behaviour:
        #   "Copy" — leaves the source file intact (DEFAULT, safer)
        #   "Move" — deletes the source after successful copy
        #   "Hardlink" — same volume only
        # Default changed to "Copy" after the AoT S01E05+E06 incident
        # where Move's copy-then-delete-source approach lost files
        # mid-flight on a cross-device move. Users who want Move-style
        # cleanup can pass it explicitly through the API.
        if import_mode not in ("Copy", "Move", "Hardlink", "Auto"):
            import_mode = "Copy"
        cmd = await c.post(
            "api/v3/command",
            json={
                "name": "ManualImport",
                "files": importable,
                "importMode": import_mode,
            },
        )
        if cmd.status_code not in (200, 201):
            raise SonarrError(
                f"Sonarr /command (ManualImport) returned HTTP {cmd.status_code}",
                status=cmd.status_code,
                body=cmd.text[:300],
            )
        try:
            cmd_body = cmd.json()
        except ValueError:
            cmd_body = {}
        command_id = cmd_body.get("id") if isinstance(cmd_body, dict) else None

        # Wait briefly for Sonarr to process the import, then check
        # its history to see where the file actually landed. Sonarr's
        # commands are async — a 200 from /command means "I accepted
        # the work order", NOT "the file is in the library now". The
        # 2-second wait is empirical: Sonarr usually finishes the
        # move + writes the history row within 1-1.5s on local
        # filesystem moves; cross-device can take longer but we don't
        # block forever (the user can navigate away).
        await asyncio.sleep(2.0)
        all_ep_ids: list[int] = []
        for f in importable:
            ep_ids = f.get("episodeIds")
            if isinstance(ep_ids, list):
                all_ep_ids.extend(int(x) for x in ep_ids if isinstance(x, int))
        destinations, history_warning = await _fetch_recent_import_history(
            cfg, episode_ids=all_ep_ids,
        )

        return ManualImportRetryResult(
            ok=True,
            imported_count=len(importable),
            command_id=int(command_id) if isinstance(command_id, int) else None,
            detail=(
                f"Sonarr accepted {len(importable)} file"
                f"{'' if len(importable) == 1 else 's'} for manual import "
                f"(mode: {import_mode})."
            ),
            destinations=destinations or None,
            history_warning=history_warning,
        )
