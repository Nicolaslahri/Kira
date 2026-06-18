"""YIFY Subtitles (yifysubtitles.ch) — community MOVIE subtitles, IMDb-indexed.
The one HTML-scraper source built into Kira (verified live 2026-06), for the
movie tail that embedded extraction + OpenSubtitles miss.

HONEST CAVEAT: this is a SCRAPER, not an API. It parses yifysubtitles.ch and
WILL break if the site changes markup or adds anti-bot. Opt-in
(`subtitles.yifysubtitles`, default off), best-effort — failure never affects the
rename. Movies only (no-op without an imdb_id).

Verified flow: `GET /movie-imdb/tt<id>` → the listing links each subtitle as
`/subtitles/<slug>` where the slug embeds the language
(`inception-2010-english-yify-392189`); the file is at `/subtitle/<slug>.zip`
(a ZIP containing the `.srt`). We match by the language word in the slug, take
the first (the listing is rating-sorted), download + unzip the first `.srt`. No
HTML parser needed — the slug carries everything.
"""

from __future__ import annotations

import io
import logging
import os
import re
import zipfile
from pathlib import Path

import httpx

from kira.download_guard import fetch_capped, looks_like_error_page
from kira.providers.base import KIRA_USER_AGENT
from kira.subtitles.naming import subtitle_sidecar_name

_log = logging.getLogger("kira.subtitles.yify")

_BASE = "https://yifysubtitles.ch"
_UA = KIRA_USER_AGENT

# Subtitle ZIPs are tiny; cap both the compressed download and the single
# decompressed .srt so a hostile/oversized payload can't exhaust memory or
# disk (zip-bomb / unbounded-download guard).
_MAX_ZIP_BYTES = 2 * 1024 * 1024     # 2 MiB compressed
_MAX_SRT_BYTES = 8 * 1024 * 1024     # 8 MiB decompressed

# 2-letter code → the full language word YIFY uses in its slugs.
_LANG_NAME: dict[str, str] = {
    "en": "english", "ja": "japanese", "es": "spanish", "fr": "french",
    "de": "german", "it": "italian", "pt": "portuguese", "ru": "russian",
    "zh": "chinese", "ko": "korean", "ar": "arabic", "nl": "dutch",
    "pl": "polish", "tr": "turkish", "sv": "swedish", "hu": "hungarian",
}


def _norm_imdb(imdb_id) -> str | None:
    """Normalize to `tt#######`. Accepts 'tt123', '123', 123. None if unusable."""
    if imdb_id is None:
        return None
    s = str(imdb_id).strip().lower()
    digits = s[2:] if s.startswith("tt") else s
    return f"tt{digits}" if digits.isdigit() else None


def find_slug(html: str, lang_name: str) -> str | None:
    """First `/subtitles/<slug>` whose slug ends `-<lang_name>-yify-<id>` — the
    listing is rating-sorted so the first match is the best-rated. Pure."""
    m = re.search(rf"/subtitles/([a-z0-9-]+-{re.escape(lang_name)}-yify-\d+)", html, re.I)
    return m.group(1) if m else None


async def search(client: httpx.AsyncClient, ctx) -> list:
    """Structured search → SubtitleCandidate list (movies, IMDb-indexed)."""
    from kira.subtitles.model import SubtitleCandidate
    ttid = _norm_imdb(ctx.imdb_id)
    if not ttid:
        return []
    wanted = [l for l in ctx.languages if l.lower() in _LANG_NAME]
    if not wanted:
        return []
    try:
        resp = await client.get(f"{_BASE}/movie-imdb/{ttid}", timeout=20.0,
                                headers={"User-Agent": _UA})
        if resp.status_code != 200 or not resp.text:
            return []
        html = resp.text
    except Exception as e:
        _log.warning("yify listing fetch failed for %s: %r", ttid, e)
        return []
    out: list = []
    for lang in wanted:
        slug = find_slug(html, _LANG_NAME[lang.lower()])
        if slug:
            out.append(SubtitleCandidate(provider="yifysubtitles", language=lang.lower(),
                                         release_name=slug, download_ref=slug))
    return out


async def download(client: httpx.AsyncClient, cand, ctx) -> bytes | None:
    """Download one YIFY candidate → raw ZIP bytes (aggregator extracts)."""
    fetched = await fetch_capped(
        client, f"{_BASE}/subtitle/{cand.download_ref}.zip",
        max_bytes=_MAX_ZIP_BYTES, timeout=30.0, headers={"User-Agent": _UA},
    )
    if not fetched:
        return None
    content, ct = fetched
    return None if looks_like_error_page(content, ct) else content


async def _download_srt(client: httpx.AsyncClient, slug: str) -> bytes | None:
    """Download `/subtitle/<slug>.zip`, return the first `.srt`'s bytes (the
    ZIP's only real payload). None on any failure / non-zip / HTML error page.
    Both the compressed download and the decompressed entry are size-capped."""
    fetched = await fetch_capped(
        client, f"{_BASE}/subtitle/{slug}.zip",
        max_bytes=_MAX_ZIP_BYTES, timeout=30.0, headers={"User-Agent": _UA},
    )
    if not fetched:
        return None
    content, ct = fetched
    if looks_like_error_page(content, ct):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            srt = next((n for n in zf.namelist() if n.lower().endswith(".srt")), None)
            if not srt:
                return None
            # Reject a zip-bomb BEFORE decompressing: trust the central-directory
            # size, then re-check the actual read.
            if zf.getinfo(srt).file_size > _MAX_SRT_BYTES:
                _log.warning("zip entry %s exceeds %d bytes, skipping", srt, _MAX_SRT_BYTES)
                return None
            data = zf.read(srt)
            return data if len(data) <= _MAX_SRT_BYTES else None
    except Exception as e:
        _log.warning("unzip failed for %s: %r", slug, e)
        return None
