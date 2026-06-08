"""Subtitle aggregator — the single place that runs the enabled subtitle SOURCES
for one video, cheapest/most-reliable first, each skipping languages already on
disk (so they compose without duplicating). The rename hook calls this once per
renamed file.

Order: embedded (free, offline) → OpenSubtitles.com (community API) → YIFY
(HTML scraper, movies, opt-in). Adding a source = one block here + its module.
Every source is wrapped so a failure in one never blocks the others or the
rename.
"""

from __future__ import annotations

import logging

import httpx

from kira.providers.opensubtitles import fetch_and_save_subtitles
from kira.subtitles import embedded as _embedded
from kira.subtitles import yifysubtitles as _yify

_log = logging.getLogger("kira.subtitles.aggregate")


async def fetch_subtitles(
    video_path: str,
    languages: list[str],
    *,
    client: httpx.AsyncClient,
    enabled: dict[str, bool],
    os_api_key: str | None = None,
    os_user: str | None = None,
    os_pw: str | None = None,
    tmdb_id: int | None = None,
    imdb_id=None,
    season: int | None = None,
    episode: int | None = None,
) -> list[str]:
    """Run the enabled sources in order; return the newly-saved sidecar paths.

    `enabled` maps source name → bool: "embedded" (default on), "opensubtitles"
    (on when an api_key is present), "yifysubtitles" (default OFF — it's a
    scraper). Each source self-skips a language already on disk, so order =
    priority and later sources only fill gaps.
    """
    saved: list[str] = []
    if not languages:
        return saved

    # 1) Embedded extraction — free, offline, no key; the release's own subs.
    if enabled.get("embedded", True) and _embedded.available():
        try:
            saved += await _embedded.extract(video_path, languages)
        except Exception as e:  # never let one source break the rest
            _log.warning("embedded failed for %s: %r", video_path, e)

    # 2) OpenSubtitles.com — the large community aggregator (clean REST API).
    if enabled.get("opensubtitles", True) and os_api_key:
        try:
            saved += await fetch_and_save_subtitles(
                video_path, api_key=os_api_key, client=client, languages=languages,
                username=os_user, password=os_pw, tmdb_id=tmdb_id, imdb_id=imdb_id,
                season=season, episode=episode,
            )
        except Exception as e:
            _log.warning("opensubtitles failed for %s: %r", video_path, e)

    # 3) YIFY — HTML scraper, movies only (needs imdb_id), opt-in. The fragile
    #    long-tail; runs last so it only fills what the durable sources missed.
    if enabled.get("yifysubtitles", False) and imdb_id:
        try:
            saved += await _yify.fetch(video_path, languages, imdb_id=imdb_id, client=client)
        except Exception as e:
            _log.warning("yifysubtitles failed for %s: %r", video_path, e)

    return saved
