"""Radarr REST client — `/api/v3/` surface.

The movie sibling of `sonarr.py`. User-owned, runs in the same network as Kira
(typical Docker stack: `http://radarr:7878`). Auth via the `X-Api-Key` header
from Radarr's Settings → General → Security.

A thin REST wrapper, NOT a metadata provider — the matcher never calls this.
Radarr is **TMDB-centric**, which is exactly how Kira identifies movies, so a
movie Match's `(provider="tmdb", provider_id)` maps straight onto Radarr's
`tmdbId` with no cross-reference needed (unlike Sonarr, which is TVDB-centric).

v1 scope: connection test + **relink on folder move** (the post-rename / undo
hook that keeps Radarr tracking a movie after Kira reorganizes its folder). The
queue-introspection / manual-import machinery in `sonarr.py` is intentionally
NOT ported — that's download management, not relink.

Calls are scoped to a per-request `httpx.AsyncClient` so a misconfigured Radarr
can't poison a long-lived client shared with anything else.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from kira.integrations.arr_paths import translate_path

logger = logging.getLogger(__name__)

# Radarr's endpoints typically respond in <500ms; 10s is a generous ceiling.
_DEFAULT_TIMEOUT = 10.0


@dataclass
class RadarrConfig:
    """Resolved Radarr connection config. Caller builds this from settings
    (`integrations.radarr.*`). `quality_profile_id` / `root_folder_path` are
    only needed for adding movies (not used by the relink hooks) — kept so the
    Test endpoint can surface real options and a future "add to Radarr" reuses
    them."""
    base_url: str        # e.g. "http://radarr:7878" or with URL base appended
    api_key: str
    quality_profile_id: int | None = None
    root_folder_path: str | None = None


class RadarrError(Exception):
    """Raised when Radarr returns a non-2xx or its response is unusable.

    Carries the upstream status code + body snippet so the UI can show something
    more helpful than "request failed."
    """
    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


def _client(cfg: RadarrConfig) -> httpx.AsyncClient:
    """Construct a per-call httpx client with auth + sane timeout.

    Per-call (not module-level) because a misconfigured base_url shouldn't
    persistently break the rest of Kira's HTTP pool. The trailing slash is
    load-bearing: httpx joins a RELATIVE request path onto the base_url's full
    path, so a reverse-proxy URL base survives (see the UrlBase-302 note in
    sonarr.py). Every call site below uses the relative form.
    """
    from kira.url_guard import validate_outbound_url
    try:
        validate_outbound_url(cfg.base_url)  # SSRF guard (LAN URLs still allowed)
    except ValueError as e:
        raise RadarrError(f"Radarr URL rejected: {e}") from e
    return httpx.AsyncClient(
        base_url=cfg.base_url.rstrip("/") + "/",
        headers={
            "X-Api-Key": cfg.api_key,
            "Accept": "application/json",
        },
        timeout=_DEFAULT_TIMEOUT,
    )


async def test_connection(cfg: RadarrConfig) -> dict[str, Any]:
    """Verify the URL + API key combo works. Returns Radarr's `/system/status`
    payload. Raises RadarrError on any failure — the test endpoint translates
    that into a 4xx for the UI."""
    async with _client(cfg) as c:
        try:
            r = await c.get("api/v3/system/status")
        except httpx.RequestError as e:
            raise RadarrError(f"Cannot reach Radarr at {cfg.base_url}: {e}") from e
        if r.status_code == 401:
            raise RadarrError("Radarr rejected the API key (401).", status=401, body=r.text[:200])
        if r.status_code != 200:
            raise RadarrError(
                f"Radarr returned HTTP {r.status_code} on /system/status",
                status=r.status_code,
                body=r.text[:200],
            )
        try:
            return r.json()
        except ValueError as e:
            raise RadarrError(f"Radarr returned non-JSON on /system/status: {e}") from e


async def list_quality_profiles(cfg: RadarrConfig) -> list[dict[str, Any]]:
    """Fetch the user's Radarr quality profiles so the UI can offer a real
    dropdown instead of a blind numeric id."""
    async with _client(cfg) as c:
        r = await c.get("api/v3/qualityprofile")
        if r.status_code != 200:
            raise RadarrError(
                f"Radarr /qualityprofile returned HTTP {r.status_code}",
                status=r.status_code,
                body=r.text[:200],
            )
        data = r.json()
        if not isinstance(data, list):
            raise RadarrError("Radarr /qualityprofile returned non-list")
        return data


async def list_root_folders(cfg: RadarrConfig) -> list[dict[str, Any]]:
    """Fetch Radarr's configured root folders (where new movies are saved)."""
    async with _client(cfg) as c:
        r = await c.get("api/v3/rootfolder")
        if r.status_code != 200:
            raise RadarrError(
                f"Radarr /rootfolder returned HTTP {r.status_code}",
                status=r.status_code,
                body=r.text[:200],
            )
        data = r.json()
        if not isinstance(data, list):
            raise RadarrError("Radarr /rootfolder returned non-list")
        return data


async def _find_movie_by_tmdb(c: httpx.AsyncClient, tmdb_id: int) -> dict[str, Any] | None:
    """Look up an EXISTING movie in the user's Radarr by TMDB id.

    Radarr's `/movie` supports a native `tmdbId` filter, so this is a single
    targeted query (no full-library pull like Sonarr's by-tvdb scan). Returns
    None when the movie isn't in Radarr — the common, benign "you didn't get
    this movie via Radarr" case the relink hook skips quietly.
    """
    r = await c.get("api/v3/movie", params={"tmdbId": tmdb_id})
    if r.status_code != 200:
        raise RadarrError(
            f"Radarr /movie returned HTTP {r.status_code}",
            status=r.status_code,
            body=r.text[:200],
        )
    items = r.json()
    if not isinstance(items, list):
        return None
    # The tmdbId filter should return only matches, but verify defensively —
    # an older Radarr that ignores the param would return the whole library.
    for m in items:
        if isinstance(m, dict) and m.get("tmdbId") == tmdb_id:
            return m
    return None


async def relink_movie(
    cfg: RadarrConfig,
    tmdb_id: int,
    *,
    old_root: str | None = None,
    new_root: str | None = None,
) -> tuple[bool, bool, str]:
    """Keep Radarr's movie path in sync with Kira's folder, THEN refresh.

    When Kira renames a movie FOLDER, Radarr's stored path goes stale — its next
    disk scan finds the old path gone, marks the movie file deleted, and (if
    monitored) may re-grab it. We translate Kira's NEW folder into Radarr's path
    namespace and `PUT` it with `moveFiles=false` (Kira already moved the file),
    then issue a `RefreshMovie` so Radarr re-links it in place. Undo passes the
    roots reversed.

    Returns (ok, changed, detail): `ok` = the refresh was accepted; `changed` =
    the stored path was actually updated; `detail` is a short human string for
    the notification. Best-effort — never raises (a Radarr hiccup must not affect
    the rename/undo this hook runs after)."""
    try:
        async with _client(cfg) as c:
            movie = await _find_movie_by_tmdb(c, int(tmdb_id))
            if not movie or not movie.get("id"):
                return False, False, "not in Radarr"
            changed = False
            note = ""
            arr_old = movie.get("path") or ""
            if old_root and new_root and old_root != new_root and arr_old:
                arr_new = translate_path(arr_old, old_root, new_root)
                if arr_new is None:
                    note = "couldn't map the new path"
                elif arr_new != arr_old:
                    movie["path"] = arr_new
                    pr = await c.put(
                        f"api/v3/movie/{movie['id']}",
                        params={"moveFiles": "false"},
                        json=movie,
                    )
                    if pr.status_code in (200, 202):
                        changed = True
                    else:
                        note = f"path update failed (HTTP {pr.status_code})"
            cmd = await c.post(
                "api/v3/command",
                json={"name": "RefreshMovie", "movieIds": [movie["id"]]},
            )
            ok = cmd.status_code in (200, 201)
            if changed:
                detail = f"path → {movie['path']}"
            elif note:
                detail = f"{note}; refreshed"
            else:
                detail = "refreshed"
            return ok, changed, detail
    except Exception as e:
        logger.warning(f"radarr: relink for tmdb {tmdb_id} failed (non-fatal): {e!r}")
        return False, False, f"error ({type(e).__name__})"


async def add_movie(cfg: RadarrConfig, tmdb_id: int) -> tuple[bool, bool, str]:
    """Add a movie to Radarr by TMDB id and trigger a search (find-or-add).

    Powers the collection-completion "Get from Radarr" button. If the movie is
    already in Radarr, just searches for it; otherwise looks up its addable shape
    and POSTs it with the user's quality profile + root folder, monitored, with
    `searchForMovie` so Radarr grabs it immediately.

    Returns (ok, added, detail): `added` distinguishes a fresh add from a search
    of an existing movie (drives the toast wording). Raises RadarrError when the
    quality profile / root folder isn't configured (Radarr needs them to add) or
    on a hard Radarr failure — the caller turns that into a user-readable message.
    """
    if cfg.quality_profile_id is None or not cfg.root_folder_path:
        raise RadarrError(
            "Radarr's default quality profile or root folder isn't set. "
            "Configure them in Settings → Integrations."
        )
    async with _client(cfg) as c:
        existing = await _find_movie_by_tmdb(c, int(tmdb_id))
        if existing is not None and existing.get("id"):
            # Already tracked — just (re)search for a release.
            cmd = await c.post(
                "api/v3/command",
                json={"name": "MoviesSearch", "movieIds": [existing["id"]]},
            )
            if cmd.status_code not in (200, 201):
                raise RadarrError(
                    f"Radarr /command (MoviesSearch) returned HTTP {cmd.status_code}",
                    status=cmd.status_code, body=cmd.text[:200],
                )
            return True, False, "already in Radarr — searching"

        # Not in Radarr → look up its addable shape, then POST it.
        lr = await c.get("api/v3/movie/lookup", params={"term": f"tmdb:{int(tmdb_id)}"})
        if lr.status_code != 200:
            raise RadarrError(
                f"Radarr /movie/lookup returned HTTP {lr.status_code}",
                status=lr.status_code, body=lr.text[:200],
            )
        matches = lr.json()
        if not isinstance(matches, list) or not matches or not isinstance(matches[0], dict):
            raise RadarrError(f"Radarr couldn't find TMDB id {tmdb_id} in its catalog.")
        payload = dict(matches[0])
        payload["qualityProfileId"] = cfg.quality_profile_id
        payload["rootFolderPath"] = cfg.root_folder_path
        payload["monitored"] = True
        payload["minimumAvailability"] = "released"
        # searchForMovie kicks off the grab on add — no separate command needed.
        payload["addOptions"] = {"searchForMovie": True}
        pr = await c.post("api/v3/movie", json=payload)
        if pr.status_code not in (200, 201):
            raise RadarrError(
                f"Radarr /movie (add) returned HTTP {pr.status_code}",
                status=pr.status_code, body=pr.text[:300],
            )
        return True, True, "added to Radarr — searching"


@dataclass
class RadarrQueueItem:
    """One in-flight Radarr download, normalized for Kira's grid.

    The movie sibling of `SonarrQueueItem` (much leaner — a movie is a single
    file, no episode breakdown). `tmdb_id` is the join key: a collection ghost
    card looks itself up by it to paint a download-progress fill on the cover.
    `status` reuses Sonarr's 7 canonical states (`_normalize_status`)."""
    tmdb_id: int
    title: str | None
    status: str
    progress_pct: float          # 0..100; 0 until bytes flow
    eta_seconds: int | None
    release_title: str | None    # the release Radarr is grabbing
    error_message: str | None


async def get_queue(cfg: RadarrConfig) -> list[RadarrQueueItem]:
    """Fetch Radarr's active download queue, normalized for Kira's ghost cards.

    Reuses Sonarr's status/timeleft normalizers (the queue record shape is the
    same across *arrs). `includeMovie=true` embeds each record's movie so we get
    its `tmdbId` in one round-trip. Drops records without a usable tmdb id."""
    from kira.integrations.sonarr import _normalize_status, _parse_timeleft
    _PAGE_SIZE = 200
    _MAX_PAGES = 25   # 5,000 records — hard stop against a broken API
    records: list = []
    async with _client(cfg) as c:
        page = 1
        while page <= _MAX_PAGES:
            try:
                r = await c.get("api/v3/queue", params={
                    "page": page,
                    "pageSize": _PAGE_SIZE,
                    "includeMovie": "true",
                    "includeUnknownMovieItems": "false",
                })
            except httpx.RequestError as e:
                raise RadarrError(f"Cannot reach Radarr at {cfg.base_url}: {e}") from e
            if r.status_code != 200:
                raise RadarrError(
                    f"Radarr /queue returned HTTP {r.status_code}",
                    status=r.status_code, body=r.text[:200],
                )
            try:
                data = r.json()
            except ValueError as e:
                raise RadarrError(f"Radarr /queue returned non-JSON: {e}") from e
            page_records = data.get("records") if isinstance(data, dict) else None
            if not isinstance(page_records, list) or not page_records:
                break
            records.extend(page_records)
            total = data.get("totalRecords") if isinstance(data, dict) else None
            if len(page_records) < _PAGE_SIZE or (isinstance(total, int) and len(records) >= total):
                break
            page += 1

        if not records:
            return []

        items: list[RadarrQueueItem] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            movie = rec.get("movie")
            tmdb_id = movie.get("tmdbId") if isinstance(movie, dict) else None
            if not isinstance(tmdb_id, int) or tmdb_id <= 0:
                continue
            size = rec.get("size")
            size_left = rec.get("sizeleft")
            progress = 0.0
            if isinstance(size, (int, float)) and size > 0 and isinstance(size_left, (int, float)):
                progress = max(0.0, min(100.0, (float(size) - float(size_left)) / float(size) * 100.0))
            error_msg: str | None = None
            sm = rec.get("statusMessages")
            if isinstance(sm, list) and sm:
                pieces: list[str] = []
                for entry in sm:
                    if isinstance(entry, dict):
                        msgs = entry.get("messages")
                        if isinstance(msgs, list):
                            pieces.extend(m for m in msgs if isinstance(m, str))
                if pieces:
                    error_msg = " · ".join(pieces[:3])
            items.append(RadarrQueueItem(
                tmdb_id=tmdb_id,
                title=movie.get("title") if isinstance(movie.get("title"), str) else None,
                status=_normalize_status(rec),
                progress_pct=round(progress, 1),
                eta_seconds=_parse_timeleft(rec.get("timeleft")),
                release_title=rec.get("title") if isinstance(rec.get("title"), str) else None,
                error_message=error_msg,
            ))
        return items
