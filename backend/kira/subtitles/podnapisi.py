"""Podnapisi (podnapisi.net) — keyless JSON subtitle search. No signup.

  GET https://www.podnapisi.net/subtitles/search/advanced
      ?keywords=<title>&language=<2-letter>&movie_type=movie|tv-series
      &seasons=<n>&episodes=<n>&page=1
  Accept: application/json

Response (defensively parsed):
  { "data": [ { "id", "language", "title",
                "download": "/subtitles/<…>/download" } ] }

Each result downloads as a ZIP. Keyless → it's a sane default fallback; the
search is title+language based (no hash/id), so it leans on a clean query.
Best-effort: any unexpected shape yields [] (never a crash / corrupt sidecar).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from kira.download_guard import fetch_capped, looks_like_error_page
from kira.providers.base import KIRA_USER_AGENT
from kira.subtitles import _common
from kira.subtitles.embedded import normalize_lang

_log = logging.getLogger("kira.subtitles.podnapisi")

_BASE = "https://www.podnapisi.net"
_SEARCH = f"{_BASE}/subtitles/search/advanced"


def parse_results(payload: dict[str, Any], languages: list[str]) -> list[dict[str, Any]]:
    """Flatten a Podnapisi JSON response into candidates [{lang, download,
    title}], filtered to the wanted (normalized) languages. Pure."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    wanted = {normalize_lang(l) for l in languages}
    out: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        lang = normalize_lang(entry.get("language") or entry.get("lang"))
        if lang is None or lang not in wanted:
            continue
        # Download path: the entry's own link if present, else the canonical
        # /subtitles/<id>/download form.
        dl = entry.get("download")
        if not isinstance(dl, str) or not dl:
            sid = entry.get("id") or entry.get("pid")
            if not sid:
                continue
            dl = f"/subtitles/{sid}/download"
        out.append({
            "lang": lang,
            "download": dl,
            "title": entry.get("title") or "",
        })
    return out


def _abs(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return _BASE + (path if path.startswith("/") else "/" + path)


def available(prefs) -> bool:
    return True  # keyless


async def search(client: httpx.AsyncClient, ctx) -> list:
    """Structured search → SubtitleCandidate list. Title-based, per language."""
    from kira.subtitles.model import SubtitleCandidate
    if not ctx.query:
        return []
    out: list = []
    for lang in ctx.languages:
        params: dict[str, str] = {"keywords": ctx.query, "language": lang.lower(), "page": "1"}
        if ctx.media_type in ("tv", "anime"):
            params["movie_type"] = "tv-series"
            if ctx.season is not None:
                params["seasons"] = str(ctx.season)
            if ctx.episode is not None:
                params["episodes"] = str(ctx.episode)
        elif ctx.media_type == "movie":
            params["movie_type"] = "movie"
        try:
            r = await client.get(_SEARCH, params=params, timeout=20.0,
                                 headers={"User-Agent": KIRA_USER_AGENT, "Accept": "application/json"},
                                 follow_redirects=True)
            r.raise_for_status()
            for c in parse_results(r.json(), [lang]):
                out.append(SubtitleCandidate(
                    provider="podnapisi", language=c["lang"],
                    release_name=c.get("title") or "", download_ref=c["download"]))
        except Exception as e:
            _log.warning("podnapisi search failed (%s) for %s: %r", lang, ctx.query, e)
    return out


async def download(client: httpx.AsyncClient, cand, ctx) -> bytes | None:
    """Download one Podnapisi candidate → raw bytes (a ZIP)."""
    try:
        fetched = await fetch_capped(
            client, _abs(cand.download_ref),
            max_bytes=_common.MAX_ZIP_BYTES, timeout=30.0,
            headers={"User-Agent": KIRA_USER_AGENT},
        )
        if not fetched:
            return None
        content, ct = fetched
        return None if looks_like_error_page(content, ct) else content
    except Exception as e:
        _log.warning("podnapisi download failed: %r", e)
        return None
