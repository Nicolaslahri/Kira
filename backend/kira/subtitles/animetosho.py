"""AnimeTosho (animetosho.org) — keyless, ANIME-only subtitle attachments
extracted from fansub releases. Best coverage for niche anime that embedded
tracks + the general aggregators miss. Fires only for files matched to AniDB.

  GET https://feed.animetosho.org/json?aid=<anidb_anime_id>[&eid=<episode_id>]

STATUS (verified live 2026-06-13): the feed.animetosho.org/json API returns
TORRENT / NZB release entries — it does NOT expose the extracted subtitle
files in the JSON. AnimeTosho does extract + host subs, but only on each
release's VIEW PAGE (animetosho.org/view/…), which needs HTML scraping +
following the per-attachment storage links. So this provider currently finds
nothing through the clean API (a safe no-op) and is marked EXPERIMENTAL in the
UI. `parse_subtitle_links` already scans defensively for sub URLs, so if/when
a release-page scraper is added it slots straight in. Default OFF.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from kira.download_guard import fetch_capped, looks_like_error_page
from kira.providers.base import KIRA_USER_AGENT
from kira.subtitles import _common

_log = logging.getLogger("kira.subtitles.animetosho")

_API = "https://feed.animetosho.org/json"

_SUB_EXTS = (".srt", ".ass", ".ssa", ".vtt", ".sub", ".zip", ".7z", ".xz")


def _looks_like_sub_url(value: Any) -> bool:
    return isinstance(value, str) and value.lower().split("?")[0].endswith(_SUB_EXTS) \
        and (value.startswith("http://") or value.startswith("https://"))


def parse_subtitle_links(payload: Any, episode: int | None) -> list[str]:
    """Best-effort scan of an AnimeTosho JSON response for subtitle-file URLs.

    AnimeTosho returns a list of release entries; subtitle attachments live in
    fields whose exact name varies. We walk each entry and collect any string
    value that resolves to a subtitle/zip URL, preferring entries whose title
    mentions the wanted episode number. Pure. ⚠ VERIFY shape against live feed.
    """
    entries: list[Any]
    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        entries = payload.get("entries") or payload.get("data") or payload.get("results") or []
    else:
        return []
    if not isinstance(entries, list):
        return []

    def _ep_token(n: int) -> tuple[str, ...]:
        return (f"{n:02d}", f" - {n} ", f"e{n:02d}", f"ep{n:02d}")

    scored: list[tuple[int, str]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        title = str(e.get("title") or e.get("torrent_name") or "")
        ep_match = 0
        if episode is not None and any(tok in title.lower() for tok in _ep_token(episode)):
            ep_match = 1
        # Collect subtitle-looking URLs anywhere in the entry (top level + one
        # level of nested dicts/lists — attachments are usually one of those).
        def _walk(obj: Any, depth: int = 0) -> None:
            if _looks_like_sub_url(obj):
                scored.append((ep_match, obj))
                return
            if depth > 4:
                return
            if isinstance(obj, dict):
                for v in obj.values():
                    _walk(v, depth + 1)
            elif isinstance(obj, list):
                for v in obj:
                    _walk(v, depth + 1)
        _walk(e)

    # Episode-matched URLs first, de-duplicated, order-preserving.
    seen: set[str] = set()
    ordered = [u for _, u in sorted(scored, key=lambda t: -t[0])]
    return [u for u in ordered if not (u in seen or seen.add(u))]


def available(prefs) -> bool:
    return True  # keyless


async def search(client: httpx.AsyncClient, ctx) -> list:
    """Structured search → SubtitleCandidate list. Anime-only (needs AniDB id).
    NOTE: the JSON feed exposes releases, not sub files, so this normally yields
    nothing (see module header) — kept defensive for when a scraper is added."""
    from kira.subtitles.model import SubtitleCandidate
    if not ctx.anidb_id or ctx.media_type != "anime":
        return []
    try:
        r = await client.get(_API, params={"aid": str(ctx.anidb_id)}, timeout=20.0,
                             headers={"User-Agent": KIRA_USER_AGENT, "Accept": "application/json"},
                             follow_redirects=True)
        r.raise_for_status()
        links = parse_subtitle_links(r.json(), ctx.episode)
    except Exception as e:
        _log.warning("animetosho search failed for aid=%s: %r", ctx.anidb_id, e)
        return []
    # Attachments aren't reliably language-tagged → offer under the first wanted
    # language. download_ref carries the URL.
    lang = ctx.languages[0] if ctx.languages else "en"
    return [SubtitleCandidate(provider="animetosho", language=lang,
                              release_name="AnimeTosho attachment", download_ref=u)
            for u in links[:5]]


async def download(client: httpx.AsyncClient, cand, ctx) -> bytes | None:
    """Download one AnimeTosho candidate → raw bytes (zip or srt)."""
    try:
        fetched = await fetch_capped(client, cand.download_ref,
                                     max_bytes=_common.MAX_ZIP_BYTES, timeout=30.0,
                                     headers={"User-Agent": KIRA_USER_AGENT})
        if not fetched:
            return None
        content, ct = fetched
        return None if looks_like_error_page(content, ct) else content
    except Exception as e:
        _log.warning("animetosho download failed: %r", e)
        return None
