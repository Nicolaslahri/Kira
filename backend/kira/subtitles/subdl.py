"""SubDL (subdl.com) — modern REST subtitle API. Key-gated.

  GET https://api.subdl.com/api/v1/subtitles
      ?api_key=…&tmdb_id=…&imdb_id=…&film_name=…
      &season_number=…&episode_number=…&type=movie|tv
      &languages=EN,ES&subs_per_page=30[&hi=1]

Response (defensively parsed — SubDL has tweaked field names over time):
  { "status": true,
    "subtitles": [ { "release_name", "name", "lang"/"language",
                     "url": "/subtitle/<id>.zip", "season", "episode", "hi" } ] }

`url` is a path to a ZIP (containing the .srt/.ass); the file host is
dl.subdl.com. Free key: subdl.com → panel → API. Best-effort: any unexpected
shape yields [] (never a crash, never a corrupt sidecar).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from kira.download_guard import fetch_capped, looks_like_error_page
from kira.providers.base import KIRA_USER_AGENT
from kira.subtitles import _common
from kira.subtitles.embedded import normalize_lang

_log = logging.getLogger("kira.subtitles.subdl")

_API = "https://api.subdl.com/api/v1/subtitles"
_DL_BASE = "https://dl.subdl.com"

# Our 2-letter code → SubDL's `languages` request token. SubDL mostly uses the
# uppercase 2-letter code; a few diverge (Brazilian Portuguese, simplified vs
# traditional Chinese). Unknown codes fall back to the uppercased 2-letter.
_REQ_CODE: dict[str, str] = {
    "pt": "PT", "zh": "ZH", "en": "EN", "es": "ES", "fr": "FR", "de": "DE",
    "it": "IT", "ja": "JA", "ko": "KO", "ar": "AR", "ru": "RU", "nl": "NL",
    "pl": "PL", "tr": "TR", "sv": "SV", "hu": "HU", "hi": "HI",
}


def _req_code(lang: str) -> str:
    return _REQ_CODE.get(lang.lower(), lang.upper())


def parse_subtitles(payload: dict[str, Any], languages: list[str]) -> list[dict[str, Any]]:
    """Flatten a SubDL response into candidates [{lang, url, release, hi}],
    filtered to the wanted (normalized) languages. Pure."""
    if not isinstance(payload, dict):
        return []
    subs = payload.get("subtitles")
    if not isinstance(subs, list):
        return []
    wanted = {normalize_lang(l) for l in languages}
    out: list[dict[str, Any]] = []
    for s in subs:
        if not isinstance(s, dict):
            continue
        # Language can arrive as `lang` (code) or `language` (name); normalize
        # whichever is present to our 2-letter key.
        lang = normalize_lang(s.get("lang") or s.get("language"))
        if lang is None or lang not in wanted:
            continue
        url = s.get("url") or s.get("download")
        if not isinstance(url, str) or not url:
            continue
        out.append({
            "lang": lang,
            "url": url,
            "release": s.get("release_name") or s.get("name") or "",
            "hi": bool(s.get("hi")),
        })
    return out


def download_url(url_field: str) -> str:
    """Absolute download URL from SubDL's `url` path."""
    if url_field.startswith("http://") or url_field.startswith("https://"):
        return url_field
    return _DL_BASE + (url_field if url_field.startswith("/") else "/" + url_field)


def available(prefs) -> bool:
    return bool(getattr(prefs, "subdl_api_key", None))


async def search(client: httpx.AsyncClient, ctx) -> list:
    """Structured search → SubtitleCandidate list (no download)."""
    from kira.subtitles.model import SubtitleCandidate
    if not ctx.subdl_api_key:
        return []
    if not (ctx.tmdb_id or ctx.imdb_id or ctx.query):
        return []
    params: dict[str, str] = {
        "api_key": ctx.subdl_api_key,
        "languages": ",".join(_req_code(l) for l in ctx.languages),
        "subs_per_page": "30",
    }
    if ctx.tmdb_id:
        params["tmdb_id"] = str(ctx.tmdb_id)
    if ctx.imdb_id:
        ttid = str(ctx.imdb_id).lower()
        params["imdb_id"] = ttid if ttid.startswith("tt") else f"tt{ttid}"
    if not (ctx.tmdb_id or ctx.imdb_id) and ctx.query:
        params["film_name"] = ctx.query
    if ctx.media_type in ("tv", "anime"):
        params["type"] = "tv"
    elif ctx.media_type == "movie":
        params["type"] = "movie"
    if ctx.season is not None:
        params["season_number"] = str(ctx.season)
    if ctx.episode is not None:
        params["episode_number"] = str(ctx.episode)
    if ctx.hearing_impaired == "only":
        params["hi"] = "1"
    try:
        r = await client.get(_API, params=params,
                             headers={"User-Agent": KIRA_USER_AGENT, "Accept": "application/json"},
                             timeout=20.0, follow_redirects=True)
        r.raise_for_status()
        raw = parse_subtitles(r.json(), ctx.languages)
    except Exception as e:
        _log.warning("subdl search failed for %s: %r", ctx.video_path, e)
        return []
    return [
        SubtitleCandidate(provider="subdl", language=c["lang"], release_name=c.get("release") or "",
                          download_ref=c["url"], hearing_impaired=c.get("hi", False))
        for c in raw
    ]


async def download(client: httpx.AsyncClient, cand, ctx) -> bytes | None:
    """Download one SubDL candidate → raw bytes (a ZIP)."""
    try:
        fetched = await fetch_capped(
            client, download_url(cand.download_ref),
            max_bytes=_common.MAX_ZIP_BYTES, timeout=30.0,
            headers={"User-Agent": KIRA_USER_AGENT},
        )
        if not fetched:
            return None
        content, ct = fetched
        return None if looks_like_error_page(content, ct) else content
    except Exception as e:
        _log.warning("subdl download failed: %r", e)
        return None
