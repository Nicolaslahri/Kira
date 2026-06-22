"""SubSource (subsource.net) — REST subtitle API, X-API-Key auth. The spiritual
successor to Subscene; strong movie + TV catalogue.

Verified live 2026-06-13. The real API is a TWO-step flow behind Cloudflare
(needs a browser-like User-Agent on api.subsource.net):

  Base:  https://api.subsource.net/api/v1     Header: X-API-Key: <key>

  1. Resolve a movieId:
       GET /movies/search?searchType=imdb&imdb=tt1375666
       GET /movies/search?searchType=text&q=Inception
       → {"success":true,"data":[{"movieId":46839,"imdbId","tmdbId","type",
                                   "releaseYear","season",...}]}
  2. List subtitles for it (language is a FULL NAME, e.g. "english"):
       GET /subtitles?movieId=46839&language=english[&releaseInfo=…]
       → {"data":[{"subtitleId","language","releaseInfo":[…],"link",
                   "hearingImpaired","foreignParts",...}], "pagination":{…}}
  3. Download (ZIP):
       GET /subtitles/<subtitleId>/download   → application/zip

Best-effort: any unexpected shape / Cloudflare block yields [] (never a crash,
never a corrupt sidecar). Key: subsource.net → profile → API key.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from kira.download_guard import fetch_capped, looks_like_error_page
from kira.subtitles import _common
from kira.subtitles.embedded import normalize_lang
from kira.subtitles.pack import is_likely_pack as _is_likely_pack

_log = logging.getLogger("kira.subtitles.subsource")

_BASE = "https://api.subsource.net/api/v1"
# api.subsource.net sits behind Cloudflare; a bot UA gets a JS-challenge 403.
# A normal browser UA passes (the API itself still requires the X-API-Key).
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Our 2-letter code → SubSource's full-name language token.
_TO_NAME: dict[str, str] = {
    "en": "english", "es": "spanish", "fr": "french", "de": "german",
    "it": "italian", "pt": "portuguese", "ru": "russian", "ja": "japanese",
    "ko": "korean", "zh": "chinese", "ar": "arabic", "nl": "dutch",
    "pl": "polish", "tr": "turkish", "sv": "swedish", "hu": "hungarian",
    "hi": "hindi",
}
# SubSource full name → our 2-letter, for matching response items. Covers the
# regional variants the catalogue actually uses.
_FROM_NAME: dict[str, str] = {
    "english": "en", "spanish": "es", "french": "fr", "german": "de",
    "italian": "it", "portuguese": "pt", "brazilian_portuguese": "pt",
    "russian": "ru", "japanese": "ja", "korean": "ko", "chinese": "zh",
    "chinese_bg_code": "zh", "big_5_code": "zh", "arabic": "ar", "dutch": "nl",
    "polish": "pl", "turkish": "tr", "swedish": "sv", "hungarian": "hu",
    "hindi": "hi",
}


def _name_to_2(name: Any) -> str | None:
    if not isinstance(name, str):
        return None
    key = name.strip().lower()
    return _FROM_NAME.get(key) or normalize_lang(key)


def parse_movie_id(payload: Any, *, imdb_id: str | None, season: int | None,
                   year: int | None = None) -> int | None:
    """Pick the best movieId from a /movies/search response. Prefer an exact
    IMDb match; for TV the row whose `season` matches; then the release YEAR
    (disambiguates same-title films, e.g. Ballerina 2023 vs 2025); only then the
    first row. Pure."""
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    tt = (imdb_id or "").lower().lstrip("t")
    # exact imdb match wins
    if tt:
        for r in rows:
            rid = str(r.get("imdbId") or "").lower().lstrip("t")
            if rid and rid == tt and r.get("movieId"):
                return int(r["movieId"])
    # season match for TV
    if season is not None:
        for r in rows:
            if r.get("season") == season and r.get("movieId"):
                return int(r["movieId"])
    # release-year disambiguation — never silently take a wrong-year same-title
    # film when a text search returns several "Ballerina"s.
    if year is not None:
        for r in rows:
            ry = r.get("releaseYear")
            if ry and str(ry).strip() == str(year) and r.get("movieId"):
                return int(r["movieId"])
    first = rows[0]
    return int(first["movieId"]) if first.get("movieId") else None


def parse_subtitles(payload: Any, wanted: set[str], *, episode: int | None) -> list[dict[str, Any]]:
    """Flatten a /subtitles response into rich candidate dicts, filtered to the
    wanted 2-letter languages. Captures the signals the scorer uses (release,
    downloads, rating, HI, forced, pack). Pure."""
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        lang = _name_to_2(r.get("language"))
        sid = r.get("subtitleId")
        if lang is None or lang not in wanted or not sid:
            continue
        rel = r.get("releaseInfo")
        release = " ".join(rel) if isinstance(rel, list) else str(rel or "")
        rating = None
        rt = r.get("rating")
        if isinstance(rt, dict) and (rt.get("total") or 0) > 0:
            rating = max(0.0, min(1.0, (rt.get("good") or 0) / rt["total"]))
        out.append({
            "lang": lang, "subtitle_id": sid, "release": release,
            "downloads": int(r.get("downloads") or 0), "rating": rating,
            "hearing_impaired": bool(r.get("hearingImpaired")),
            "forced": bool(r.get("foreignParts")),
            "is_pack": _is_likely_pack(release, int(r.get("files") or 1)),
        })
    return out


def available(prefs) -> bool:
    return bool(getattr(prefs, "subsource_api_key", None))


def _headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key, "User-Agent": _UA, "Accept": "application/json"}


def _dl_headers(api_key: str) -> dict[str, str]:
    """Headers for the DOWNLOAD endpoint — must NOT advertise Accept: json (the
    file endpoint can honor it and hand back a JSON envelope instead of the
    archive). Accept anything; keep the browser UA for Cloudflare + the key."""
    return {"X-API-Key": api_key, "User-Agent": _UA, "Accept": "*/*"}


async def search(client: httpx.AsyncClient, ctx) -> list:
    """Structured search → SubtitleCandidate list. Two-step: resolve movieId,
    then list subtitles per wanted language. Best-effort; never raises."""
    from kira.subtitles.model import SubtitleCandidate
    if not ctx.subsource_api_key:
        return []
    ttid = None
    if ctx.imdb_id:
        s = str(ctx.imdb_id).lower()
        ttid = s if s.startswith("tt") else f"tt{s}"
    if not ttid and not ctx.query:
        return []
    H = _headers(ctx.subsource_api_key)
    # SubSource indexes TV by SEASON (each season its own movieId), so series
    # is fully supported.
    ss_type = ("series" if ctx.media_type in ("tv", "anime")
               else "movie" if ctx.media_type == "movie" else "all")
    try:
        params: dict[str, str] = {"type": ss_type}
        if ttid:
            params.update(searchType="imdb", imdb=ttid)
        else:
            params.update(searchType="text", q=ctx.query)
        if ctx.season is not None and ss_type != "movie":
            params["season"] = str(ctx.season)
        sr = await client.get(f"{_BASE}/movies/search", params=params,
                              headers=H, timeout=20.0, follow_redirects=True)
        sr.raise_for_status()
        movie_id = parse_movie_id(sr.json(), imdb_id=ttid, season=ctx.season, year=ctx.year)
    except Exception as e:
        _log.warning("subsource movie search failed for %s: %r", ctx.video_path, e)
        return []
    if not movie_id:
        return []

    wanted2 = {normalize_lang(l) for l in ctx.languages}
    out: list = []
    for lang in ctx.languages:
        name = _TO_NAME.get(lang.lower(), lang.lower())
        try:
            lr = await client.get(f"{_BASE}/subtitles",
                                  params={"movieId": str(movie_id), "language": name},
                                  headers=H, timeout=20.0, follow_redirects=True)
            lr.raise_for_status()
            for c in parse_subtitles(lr.json(), wanted2, episode=ctx.episode):
                out.append(SubtitleCandidate(
                    provider="subsource", language=c["lang"], release_name=c["release"],
                    download_ref=c["subtitle_id"], downloads=c["downloads"], rating=c["rating"],
                    hearing_impaired=c["hearing_impaired"], forced=c["forced"], is_pack=c["is_pack"]))
        except Exception as e:
            _log.warning("subsource list failed (%s) for movie %s: %r", lang, movie_id, e)
    return out


async def download(client: httpx.AsyncClient, cand, ctx) -> bytes | None:
    """Download one SubSource candidate → raw bytes (usually a ZIP). The
    download endpoint redirects to a file host, so we follow redirects here
    (the initial subsource.net host is still SSRF-validated)."""
    if not ctx.subsource_api_key:
        return None
    url = f"{_BASE}/subtitles/{cand.download_ref}/download"
    try:
        fetched = await fetch_capped(
            client, url, max_bytes=_common.MAX_ZIP_BYTES, timeout=45.0,
            headers=_dl_headers(ctx.subsource_api_key), follow_redirects=True,
        )
        if not fetched:
            _log.warning("subsource download %s returned nothing (redirect/oversize/error)", url)
            return None
        content, ct = fetched
        if looks_like_error_page(content, ct):
            _log.warning("subsource download %s looked like an error page (ct=%s)", url, ct)
            return None
        _log.info("subsource download %s ok: %d bytes, ct=%s, magic=%r",
                  url, len(content), ct, content[:4])
        return content
    except Exception as e:
        _log.warning("subsource download failed: %r", e)
        return None
