"""fanart.tv — dedicated community ARTWORK source (clearlogo, clear art, banner,
disc art, character art, HD backgrounds), the things TMDB/TVDB posters miss.

Verified live 2026-06 against the official client's type defs + source
(github.com/fanart-tv/fanart.tv-api):
  • base `https://webservice.fanart.tv/v3`
  • auth `?api_key=<key>` (+ optional `&client_key=<personal>` for fresher data)
  • movies  `/movies/{tmdb_id | imdb_id}`  → movieposter, moviebackground,
            hdmovielogo/movielogo, hdmovieclearart/movieart, moviebanner,
            moviethumb, moviedisc
  • tv      `/tv/{thetvdb_id}`             → tvposter, showbackground,
            hdtvlogo/clearlogo, hdclearart/clearart, tvbanner, tvthumb,
            characterart
  • each entry: {id, url, lang ('en' / '00' = textless), likes (string)}

ARTWORK-ONLY: this is NOT a matcher (no search), so it lives outside the
ProviderRegistry and is called directly by the rename hook (like the subtitle
sources). It maps fanart.tv's keys → Kira's local-asset "kinds" (poster, fanart,
clearlogo, clearart, banner, landscape, disc, characterart) and picks the best
image per kind (language preference, then community `likes`). Best-effort:
any failure returns `{}` so a rename never breaks.
"""

from __future__ import annotations

import logging

import httpx

from kira.providers.base import KIRA_USER_AGENT

_log = logging.getLogger("kira.fanarttv")

_BASE = "https://webservice.fanart.tv/v3"
_UA = KIRA_USER_AGENT

# Local-asset extension per kind. Logos / clear art / disc / character art carry
# transparency → PNG (Kodi/Jellyfin convention); flat images → JPG.
EXT_FOR_KIND: dict[str, str] = {
    "poster": "jpg", "fanart": "jpg", "banner": "jpg", "landscape": "jpg",
    "clearlogo": "png", "clearart": "png", "disc": "png", "characterart": "png",
}

# kind → (fanart.tv keys in priority order, prefer_textless). Backgrounds want
# the TEXTLESS ('00') variant; logos/art/posters want a language match.
_MOVIE_MAP: list[tuple[str, list[str], bool]] = [
    ("poster",       ["movieposter"],                  False),
    ("fanart",       ["moviebackground"],              True),
    ("clearlogo",    ["hdmovielogo", "movielogo"],     False),
    ("clearart",     ["hdmovieclearart", "movieart"],  False),
    ("banner",       ["moviebanner"],                  False),
    ("landscape",    ["moviethumb"],                   True),
    ("disc",         ["moviedisc"],                    False),
]
_TV_MAP: list[tuple[str, list[str], bool]] = [
    ("poster",       ["tvposter"],                     False),
    ("fanart",       ["showbackground"],               True),
    ("clearlogo",    ["hdtvlogo", "clearlogo"],        False),
    ("clearart",     ["hdclearart", "clearart"],       False),
    ("banner",       ["tvbanner"],                     False),
    ("landscape",    ["tvthumb"],                      True),
    ("characterart", ["characterart"],                 False),
]

# Every kind this module can ever produce (used by callers for option lists).
ALL_KINDS: tuple[str, ...] = (
    "poster", "fanart", "clearlogo", "clearart", "banner", "landscape",
    "disc", "characterart",
)


def _likes(img: dict) -> int:
    try:
        return int(img.get("likes") or 0)
    except (TypeError, ValueError):
        return 0


def pick_best(images, *, languages: list[str] | None, prefer_textless: bool) -> str | None:
    """Best image URL from a fanart.tv type array: language preference first,
    then community `likes`. `prefer_textless` flips the language ranking for
    backgrounds (a clean '00' textless plate beats a localized one). Pure."""
    if not images:
        return None
    langs = [l.lower() for l in (languages or []) if l] or ["en"]

    def rank(img: dict) -> tuple[int, int]:
        lang = (img.get("lang") or "").lower()
        if prefer_textless:
            lang_score = 3 if lang in ("00", "") else (1 if lang in langs else 0)
        else:
            lang_score = 3 if lang in langs else (2 if lang == "en" else (1 if lang in ("00", "") else 0))
        return (lang_score, _likes(img))

    best = max(images, key=rank)
    url = best.get("url")
    return url if isinstance(url, str) and url else None


async def fetch_artwork(
    *,
    media_type: str,
    client: httpx.AsyncClient,
    api_key: str | None,
    tmdb_id=None,
    tvdb_id=None,
    imdb_id=None,
    client_key: str | None = None,
    languages: list[str] | None = None,
    wanted: set[str] | None = None,
) -> dict[str, str]:
    """Return `{kind: best_url}` for the wanted kinds from fanart.tv.

    Picks the endpoint by media type:
      • movie → `/movies/{tmdb_id | imdb_id}`
      • tv / anime → `/tv/{tvdb_id}` (anime resolves its TVDB id via the Fribb
        cross-ref upstream — fanart.tv keys TV by TheTVDB only).
    Returns `{}` (never raises) on: no api_key, no usable id, non-200, or any
    parse error. `wanted` filters to the kinds the user enabled; None = all.
    """
    if not api_key:
        return {}
    if media_type == "movie":
        mid = tmdb_id or imdb_id
        if not mid:
            return {}
        path = f"/movies/{mid}"
        mapping = _MOVIE_MAP
    else:  # tv + anime both use the TV endpoint (TVDB id)
        if not tvdb_id:
            return {}
        path = f"/tv/{tvdb_id}"
        mapping = _TV_MAP

    params: dict[str, str] = {"api_key": api_key}
    if client_key:
        params["client_key"] = client_key
    try:
        resp = await client.get(
            f"{_BASE}{path}", params=params, timeout=15.0,
            headers={"User-Agent": _UA},
        )
        if resp.status_code != 200:
            # 404 = no fanart for this id (common, not an error worth shouting).
            if resp.status_code not in (404,):
                _log.info("%s → HTTP %s", path, resp.status_code)
            return {}
        data = resp.json()
        if not isinstance(data, dict):
            return {}
    except Exception as e:
        _log.warning("fetch failed for %s: %r", path, e)
        return {}

    out: dict[str, str] = {}
    for kind, keys, textless in mapping:
        if wanted is not None and kind not in wanted:
            continue
        for key in keys:
            best = pick_best(data.get(key), languages=languages, prefer_textless=textless)
            if best:
                out[kind] = best
                break
    return out


async def test_key(api_key: str | None, client: httpx.AsyncClient) -> tuple[bool, str | None]:
    """Validate a fanart.tv API key for the Settings 'Test connection' button.

    Pings a known, well-arted movie (Inception, TMDB 27205): HTTP 200 = the key
    works, 401/403 = key rejected. Returns `(ok, detail)` — never raises. Unlike
    `fetch_artwork` (which returns {} for both a bad key AND a no-art id), this
    distinguishes auth failure from a reachable-but-empty response."""
    if not api_key or not api_key.strip():
        return (False, "No fanart.tv API key configured.")
    try:
        resp = await client.get(
            f"{_BASE}/movies/27205", params={"api_key": api_key.strip()},
            timeout=15.0, headers={"User-Agent": _UA},
        )
    except Exception as e:
        return (False, f"Couldn't reach fanart.tv: {e}")
    if resp.status_code == 200:
        return (True, None)
    if resp.status_code in (401, 403):
        return (False, "fanart.tv rejected the API key.")
    return (False, f"fanart.tv returned HTTP {resp.status_code}.")
