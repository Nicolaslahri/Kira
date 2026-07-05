"""OpenSubtitles REST client — resolve a file's content hash to an identity.

Pairs with `_osdbhash`: hash the file, ask OpenSubtitles "what movie/episode is
this hash?", and get back a TMDB/IMDb id + title/year. That id then feeds the
existing embedded-ID match path, so a file with a completely garbage name still
lands on the right entry (Matching-completeness M5).

The modern REST API (`api.opensubtitles.com/api/v1`) requires an `Api-Key`
header. Everything here is **key-gated**: with no key configured the client is a
no-op (returns None), so nothing changes for users who don't opt in.

Response parsing is split into a pure `parse_identity()` so it can be tested
without any network.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from kira.download_guard import fetch_capped, looks_like_error_page

from kira.providers.base import KIRA_USER_AGENT
from kira.subtitles.errors import AuthRejected, QuotaExceeded

# OpenSubtitles signals a spent daily download allowance with 406 Not
# Acceptable and rate-limits with 429 Too Many Requests. Both mean "stop the
# batch" rather than "this one file failed".
_QUOTA_STATUSES = (406, 429)


def _maybe_quota(exc: Exception) -> None:
    """Re-raise as QuotaExceeded / AuthRejected when an httpx error carries a
    batch-stopping status; otherwise return (caller logs + degrades). Pulls
    the `remaining` / `reset_time` hint from the JSON body when present."""
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None) if resp is not None else None
    if status in (401, 403):
        # A rejected key fails EVERY request identically — stop the batch.
        raise AuthRejected(f"OpenSubtitles rejected the API key (HTTP {status})")
    if resp is None or status not in _QUOTA_STATUSES:
        return
    remaining = None
    reset_hint = None
    try:
        body = resp.json()
        if isinstance(body, dict):
            remaining = body.get("remaining")
            reset_hint = body.get("reset_time") or body.get("reset_time_utc")
    except Exception:
        pass
    raise QuotaExceeded(remaining=remaining, reset_hint=reset_hint)

_log = logging.getLogger("kira.opensubtitles")

_BASE_URL = "https://api.opensubtitles.com/api/v1"

# A subtitle file is small text; cap the download so a hostile/oversized CDN
# payload can't exhaust memory or fill the disk (unbounded-download guard).
_MAX_SUB_BYTES = 8 * 1024 * 1024


def parse_identity(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a normalized identity from a `/subtitles` response.

    Prefers results where `moviehash_match` is true (the hash matched the exact
    release, so the identity is sync-guaranteed). Returns None when nothing
    usable is present.

    Normalized shape:
        {feature_type, title, year, imdb_id, tmdb_id, season_number, episode_number}
    """
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None

    def _details(entry: dict) -> dict | None:
        attrs = entry.get("attributes")
        if not isinstance(attrs, dict):
            return None
        fd = attrs.get("feature_details")
        return fd if isinstance(fd, dict) else None

    # Prefer an exact moviehash match; fall back to the first entry with details.
    chosen: dict | None = None
    for entry in data:
        if not isinstance(entry, dict):
            continue
        attrs = entry.get("attributes") or {}
        fd = _details(entry)
        if fd is None:
            continue
        if attrs.get("moviehash_match") is True:
            chosen = fd
            break
        if chosen is None:
            chosen = fd
    if chosen is None:
        return None

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    ident = {
        "feature_type": (chosen.get("feature_type") or "").lower() or None,
        "title": chosen.get("title") or chosen.get("parent_title"),
        "year": _int(chosen.get("year")),
        "imdb_id": _int(chosen.get("imdb_id")),
        "tmdb_id": _int(chosen.get("tmdb_id")),
        "season_number": _int(chosen.get("season_number")),
        "episode_number": _int(chosen.get("episode_number")),
    }
    # Require at least one resolvable id or a title — else it's useless.
    if not (ident["tmdb_id"] or ident["imdb_id"] or ident["title"]):
        return None
    return ident


def parse_subtitle_candidates(payload: dict[str, Any], languages: list[str] | None = None
                              ) -> list[dict[str, Any]]:
    """Flatten a `/subtitles` response into ranked download candidates.

    Each candidate: {file_id, language, moviehash_match, downloads, release}.
    Ranked best-first: an exact `moviehash_match` (sync-guaranteed) wins, then
    higher download count (community-vetted). When `languages` is given, only
    those (lowercased) survive. Pure — no I/O."""
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    langs = {l.lower() for l in languages} if languages else None
    out: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        attrs = entry.get("attributes") or {}
        if not isinstance(attrs, dict):
            continue
        lang = (attrs.get("language") or "").lower() or None
        if langs and lang not in langs:
            continue
        files = attrs.get("files")
        if not isinstance(files, list) or not files:
            continue
        fid = files[0].get("file_id") if isinstance(files[0], dict) else None
        if fid is None:
            continue
        # Film identity, so the aggregator can reject a wrong-movie match. For a
        # movie feature these are the film's own ids/year; episodes carry the
        # episode's (the gate is movies-only, so that's fine).
        feat = attrs.get("feature_details")
        feat = feat if isinstance(feat, dict) else {}
        out.append({
            "file_id": fid,
            "language": lang or "en",
            "moviehash_match": attrs.get("moviehash_match") is True,
            "downloads": _safe_int(attrs.get("download_count")) or 0,
            "release": attrs.get("release") or "",
            "hearing_impaired": attrs.get("hearing_impaired") is True,
            "forced": attrs.get("foreign_parts_only") is True,
            "imdb_id": feat.get("imdb_id"),
            "tmdb_id": _safe_int(feat.get("tmdb_id")),
            "year": _safe_int(feat.get("year")),
        })
    out.sort(key=lambda c: (c["moviehash_match"], c["downloads"]), reverse=True)
    return out


def pick_best_per_language(candidates: list[dict[str, Any]],
                           languages: list[str]) -> dict[str, dict[str, Any]]:
    """First (best-ranked) candidate per requested language. Pure."""
    best: dict[str, dict[str, Any]] = {}
    for lang in (l.lower() for l in languages):
        for c in candidates:
            if c["language"] == lang:
                best[lang] = c
                break
    return best


def parse_download_link(payload: dict[str, Any]) -> str | None:
    """Pull the temporary download URL from a `/download` response. Pure."""
    if not isinstance(payload, dict):
        return None
    link = payload.get("link")
    return link if isinstance(link, str) and link else None


def parse_login_token(payload: dict[str, Any]) -> str | None:
    """Pull the JWT from a `/login` response. Pure."""
    if not isinstance(payload, dict):
        return None
    tok = payload.get("token")
    return tok if isinstance(tok, str) and tok else None


# Canonical sidecar naming now lives in the neutral subtitles.naming module so
# the subtitle SOURCES don't depend on this provider for a generic path helper.
# Re-exported here for backward compatibility with existing importers.
from kira.subtitles.naming import subtitle_sidecar_name  # noqa: E402


def _safe_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class OpenSubtitlesClient:
    """Thin async client. No-op (returns None) when no api_key is configured."""

    def __init__(self, api_key: str | None, client: httpx.AsyncClient,
                 app_name: str | None = None):
        self.api_key = (api_key or "").strip()
        self.client = client
        # OpenSubtitles requires a descriptive, app-identifying User-Agent.
        self.app_name = app_name or KIRA_USER_AGENT

    async def identify_by_hash(self, moviehash: str, bytesize: int | None = None) -> dict[str, Any] | None:
        """Query `/subtitles?moviehash=...` and return a normalized identity.

        Returns None on: no api_key, network/HTTP error, or no usable result.
        Never raises.
        """
        if not self.api_key or not moviehash:
            return None
        params: dict[str, str] = {"moviehash": moviehash}
        if bytesize:
            params["moviebytesize"] = str(bytesize)
        try:
            r = await self.client.get(
                f"{_BASE_URL}/subtitles",
                params=params,
                headers={
                    "Api-Key": self.api_key,
                    # OpenSubtitles requires a descriptive, app-identifying UA.
                    "User-Agent": self.app_name,
                    "Accept": "application/json",
                },
                timeout=20.0,
            )
            r.raise_for_status()
            return parse_identity(r.json())
        except Exception as e:  # network / decode / HTTP — degrade gracefully
            _log.warning("identify_by_hash failed: %r", e)
            return None

    def _headers(self, token: str | None = None) -> dict[str, str]:
        h = {
            "Api-Key": self.api_key,
            "User-Agent": self.app_name,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    async def login(self, username: str, password: str) -> str | None:
        """Exchange username/password for a JWT (needed for downloads — they
        count against the user's daily quota). None on failure. Never raises."""
        if not self.api_key or not username or not password:
            return None
        try:
            r = await self.client.post(
                f"{_BASE_URL}/login",
                json={"username": username, "password": password},
                headers=self._headers(), timeout=20.0,
            )
            r.raise_for_status()
            return parse_login_token(r.json())
        except Exception as e:
            _log.warning("login failed: %r", e)
            return None

    async def search(self, *, moviehash: str | None = None, moviebytesize: int | None = None,
                     tmdb_id: int | None = None, imdb_id: int | None = None,
                     query: str | None = None, season: int | None = None,
                     episode: int | None = None, languages: list[str] | None = None,
                     hearing_impaired: str | None = None, forced: str | None = None,
                     ) -> list[dict[str, Any]]:
        """Search `/subtitles`. Hash-first when a moviehash is given, with
        id/name params layered on so the API can fall back. Returns ranked
        candidates (parse_subtitle_candidates). [] on any failure."""
        if not self.api_key:
            return []
        params: dict[str, str] = {}
        if moviehash:
            params["moviehash"] = moviehash
        if moviebytesize:
            params["moviebytesize"] = str(moviebytesize)
        if tmdb_id:
            params["tmdb_id"] = str(tmdb_id)
        if imdb_id:
            params["imdb_id"] = str(imdb_id)
        if query:
            # OpenSubtitles requires lowercase parameter values (and 301s
            # non-canonical requests — see the sort below).
            params["query"] = query.lower()
        if season is not None:
            params["season_number"] = str(season)
        if episode is not None:
            params["episode_number"] = str(episode)
        if languages:
            params["languages"] = ",".join(sorted(l.lower() for l in languages))
        # Variant preferences (Settings → Naming → Subtitles). The API accepts
        # include / exclude / only for both; anything else is left unset so the
        # server default ("include") applies.
        if hearing_impaired in ("include", "exclude", "only"):
            params["hearing_impaired"] = hearing_impaired
        if forced in ("include", "exclude", "only"):
            params["foreign_parts_only"] = forced
        if not params:
            return []
        # The API 301-redirects any request whose params aren't in canonical
        # (alphabetical) order, and httpx doesn't follow redirects by default —
        # so a non-canonical search silently returned nothing. Sort the params
        # AND follow the redirect as belt-and-braces.
        params = dict(sorted(params.items()))
        try:
            r = await self.client.get(
                f"{_BASE_URL}/subtitles", params=params,
                headers={"Api-Key": self.api_key, "User-Agent": self.app_name,
                         "Accept": "application/json"},
                timeout=20.0,
                follow_redirects=True,
            )
            r.raise_for_status()
            return parse_subtitle_candidates(r.json(), languages)
        except QuotaExceeded:
            raise
        except Exception as e:
            _maybe_quota(e)  # rate-limited search → stop the batch
            _log.warning("search failed: %r", e)
            return []

    async def download_link(self, file_id: int, token: str | None = None) -> str | None:
        """POST `/download` to get the temporary URL for a subtitle file. None
        on failure. Never raises."""
        if not self.api_key:
            return None
        try:
            r = await self.client.post(
                f"{_BASE_URL}/download", json={"file_id": file_id},
                headers=self._headers(token), timeout=20.0,
            )
            r.raise_for_status()
            return parse_download_link(r.json())
        except QuotaExceeded:
            raise
        except Exception as e:
            _maybe_quota(e)  # spent daily allowance → stop the batch
            _log.warning("download_link failed: %r", e)
            return None


async def fetch_and_save_subtitles(
    path: str | os.PathLike,
    *,
    api_key: str | None,
    client: httpx.AsyncClient,
    languages: list[str],
    username: str | None = None,
    password: str | None = None,
    tmdb_id: int | None = None,
    imdb_id: int | None = None,
    season: int | None = None,
    episode: int | None = None,
    query: str | None = None,
    hearing_impaired: str | None = None,
    forced: str | None = None,
    on_status=None,
) -> list[str]:
    """End-to-end subtitle fetch for one video. Hash-first search (falls back to
    tmdb/imdb id + season/episode), best candidate per language, download, write
    `<stem>.<lang>.srt` beside the video. Returns the saved sidecar paths.

    Best-effort and key/credential-gated: no key → []. Download needs login
    creds (OpenSubtitles quota); without them search still runs but nothing is
    saved. Never raises — a subtitle failure must not affect the rename."""
    import os as _os
    from pathlib import Path

    if not api_key or not languages:
        return []

    # Exists-before-search: drop languages whose sidecar is already on disk, and
    # if NONE remain, return BEFORE spending an OpenSubtitles search/quota (the
    # per-language dest check previously ran only AFTER search + download_link,
    # so a fully-subtitled file still burned a search request every scan).
    languages = [
        lang for lang in languages
        # Skip a language that already has ANY sidecar on disk — `.srt` from a
        # prior OpenSubtitles run, or `.ass`/`.vtt` from embedded extraction —
        # so the two sources compose without downloading a duplicate.
        if not any(
            Path(path).with_name(subtitle_sidecar_name(path, lang, ext=e)).exists()
            for e in ("srt", "ass", "vtt")
        )
    ]
    if not languages:
        return []

    def _say(msg: str) -> None:
        if on_status is not None:
            try:
                on_status(msg)
            except Exception:
                pass

    os_client = OpenSubtitlesClient(api_key, client)

    moviehash = None
    bytesize = None
    try:
        from kira.providers._osdbhash import compute_osdb_hash
        moviehash = compute_osdb_hash(path)
        bytesize = _os.path.getsize(path)
    except Exception:
        pass

    _say("searching OpenSubtitles")
    candidates = await os_client.search(
        moviehash=moviehash, moviebytesize=bytesize,
        tmdb_id=tmdb_id, imdb_id=imdb_id, season=season, episode=episode,
        # Title-query fallback: AniDB matches carry no TMDB/IMDb id, and a
        # file hash rarely matches fansub releases — without `query` those
        # files searched on hash alone and found NOTHING.
        query=query if not (tmdb_id or imdb_id) else None,
        languages=languages,
        hearing_impaired=hearing_impaired, forced=forced,
    )
    if not candidates:
        _say("no candidates found")
        return []
    best = pick_best_per_language(candidates, languages)
    if not best:
        return []
    _say(f"found {len(candidates)} · picking best of {len(best)} language(s)")

    token = await os_client.login(username, password) if (username and password) else None

    saved: list[str] = []
    for lang, cand in best.items():
        _say(f"downloading {lang.upper()} subtitles")
        link = await os_client.download_link(cand["file_id"], token)
        if not link:
            continue
        try:
            # The download link comes from OpenSubtitles' JSON, so route it
            # through the SSRF guard and stream it under a hard size cap.
            fetched = await fetch_capped(client, link, max_bytes=_MAX_SUB_BYTES, timeout=30.0)
            if not fetched:
                continue
            content, ct = fetched
            # Reject a 200-OK error page (OpenSubtitles / its CDN sometimes
            # serves an HTML notice or JSON error with status 200) — otherwise
            # it'd be written as a permanent, corrupt .srt that's never retried.
            if looks_like_error_page(content, ct):
                _log.info("%s download was a non-subtitle payload (HTML/JSON), skipping", lang)
                continue
            dest = Path(path).with_name(subtitle_sidecar_name(path, lang))
            # Don't clobber an existing sub, and never follow a symlink planted
            # at the sidecar path (could redirect the write outside the library).
            if dest.exists() or dest.is_symlink():
                continue
            # Atomic: write to a sibling .part then rename, so a crash mid-write
            # never leaves a half-written sidecar.
            tmp = dest.with_name(dest.name + ".part")
            try:
                if tmp.is_symlink():
                    tmp.unlink()
                tmp.write_bytes(content)
                _os.replace(tmp, dest)
            except Exception:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            saved.append(str(dest))
        except Exception as e:
            _log.warning("save %s failed: %r", lang, e)
    return saved


async def search(client: httpx.AsyncClient, ctx) -> list:
    """Structured search → SubtitleCandidate list (no download). Hash-first."""
    from kira.subtitles.model import SubtitleCandidate
    if not ctx.os_api_key:
        return []
    osc = OpenSubtitlesClient(ctx.os_api_key, client)
    moviehash = bytesize = None
    try:
        from kira.providers._osdbhash import compute_osdb_hash
        moviehash = compute_osdb_hash(ctx.video_path)
        bytesize = os.path.getsize(ctx.video_path)
    except Exception:
        pass
    tmdb_id = ctx.tmdb_id
    # Anime is usually indexed by ABSOLUTE episode (One Piece #1080), not the
    # cour-local SxxEyy AniDB carries — searching by the cour number misses. When
    # we have an absolute number for anime, query by it with no season. Guarded
    # by `absolute` presence (set only for genuinely absolute-numbered files), so
    # seasonal shows keep the normal season/episode path. (The moviehash search
    # still runs first and wins on an exact match regardless.)
    if ctx.media_type == "anime" and ctx.absolute is not None:
        os_season, os_episode = None, ctx.absolute
    else:
        os_season, os_episode = ctx.season, ctx.episode
    raw = await osc.search(
        moviehash=moviehash, moviebytesize=bytesize,
        tmdb_id=tmdb_id, imdb_id=ctx.imdb_id,
        season=os_season, episode=os_episode,
        query=ctx.query if not (tmdb_id or ctx.imdb_id) else None,
        languages=ctx.languages,
        hearing_impaired=ctx.hearing_impaired or None,
        forced=ctx.forced or None,
    )
    out = []
    for c in raw:
        out.append(SubtitleCandidate(
            provider="opensubtitles", language=c["language"],
            release_name=c.get("release") or "", download_ref=c["file_id"],
            downloads=c.get("downloads") or 0, hash_match=c.get("moviehash_match", False),
            hearing_impaired=c.get("hearing_impaired", False), forced=c.get("forced", False),
            imdb_id=c.get("imdb_id"), tmdb_id=c.get("tmdb_id"), year=c.get("year"),
        ))
    return out


# Login token cached per (api_key, user) as (token, expiry_monotonic). OS JWTs
# last ~24h; we re-login well before that. Caching "forever" meant a long-lived
# server process kept using an EXPIRED token — the download then 401'd and was
# misreported as "API key rejected" (aborting the whole backfill and telling
# the user to replace a perfectly valid key).
_token_cache: dict = {}
_TOKEN_TTL_SEC = 6 * 3600


def _cached_token(ck) -> str | None:
    import time
    entry = _token_cache.get(ck)
    if not entry:
        return None
    token, expiry = entry
    if time.monotonic() >= expiry:
        _token_cache.pop(ck, None)
        return None
    return token


def _store_token(ck, token: str) -> None:
    import time
    _token_cache[ck] = (token, time.monotonic() + _TOKEN_TTL_SEC)


async def download(client: httpx.AsyncClient, cand, ctx) -> bytes | None:
    """Download one OpenSubtitles candidate → raw bytes (the aggregator saves)."""
    if not ctx.os_api_key:
        return None
    osc = OpenSubtitlesClient(ctx.os_api_key, client)
    token = None
    if ctx.os_user and ctx.os_pw:
        ck = (ctx.os_api_key, ctx.os_user)
        token = _cached_token(ck)
        if token is None:
            token = await osc.login(ctx.os_user, ctx.os_pw)
            if token:
                _store_token(ck, token)
    link = await osc.download_link(cand.download_ref, token)
    if not link:
        return None
    fetched = await fetch_capped(client, link, max_bytes=_MAX_SUB_BYTES, timeout=30.0)
    if not fetched:
        return None
    content, ct = fetched
    if looks_like_error_page(content, ct):
        return None
    return content


async def identify_file_by_hash(
    path: str | os.PathLike,
    api_key: str | None,
    client: httpx.AsyncClient,
) -> dict[str, Any] | None:
    """End-to-end: hash the file, ask OpenSubtitles, return the identity (with
    the computed `moviehash` attached). None when the file can't be hashed, no
    key is set, or nothing matched."""
    from kira.providers._osdbhash import compute_osdb_hash

    moviehash = compute_osdb_hash(path)
    if not moviehash:
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    ident = await OpenSubtitlesClient(api_key, client).identify_by_hash(moviehash, size)
    if ident:
        ident["moviehash"] = moviehash
    return ident
