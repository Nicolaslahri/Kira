"""Persist anime poster URLs onto their matches so covers behave like TV/movies.

AniDB's title dump carries no image URLs, so anime poster art is resolved
through `get_picture_url` (AniDB HTTP API → TVDB/TMDB cross-ref) — a lookup
*globally throttled to 1 request / 4s* and ban-prone. Historically the result
lived only in the frontend's in-memory map (lost on every refresh) plus an
on-disk cache that can go cold, so each cold load re-resolved every cover at
1-per-4-seconds and most showed blank.

The fix: resolve once and STORE the URL on `Match.poster_url`, exactly like
TMDB/TVDB matches. Then the files API ships the URL, the grid renders it
directly (`item.posterUrl`), and the per-card live lookup is never needed for
already-resolved series. This sweep reads the on-disk picture cache FIRST (free,
no network), so warming everything already cached costs zero AniDB calls;
genuinely-unresolved AIDs do a throttled live lookup in the background.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from sqlalchemy import select, update

from kira import activity
from kira.database import SessionLocal
from kira.matcher.engine import registry_from_settings
from kira.models import Match, MediaFile

logger = logging.getLogger("kira.posters")

POSTER_WARMUP_JOB = "poster_warmup"


async def warm_anime_posters(*, narrate: bool = True) -> dict:
    """Resolve + PERSIST `poster_url` for anime matches that lack one. Cached
    AIDs cost nothing (disk cache); uncached ones do a throttled live lookup.
    Best-effort, never raises. Returns {aids, resolved, updated}."""
    summary = {"aids": 0, "resolved": 0, "updated": 0}
    started = False
    try:
        async with SessionLocal() as session:
            # Distinct AniDB ids that still have a NULL-poster selected anime match.
            rows = (await session.execute(
                select(Match.provider_id)
                .join(MediaFile, MediaFile.id == Match.media_file_id)
                .where(
                    MediaFile.media_type == "anime",
                    Match.provider == "anidb",
                    Match.is_selected.is_(True),
                    Match.poster_url.is_(None),
                )
                .distinct()
            )).all()
            aids = [str(r[0]) for r in rows if r[0]]
            summary["aids"] = len(aids)
            if not aids:
                return summary

            async with httpx.AsyncClient() as client:
                registry = await registry_from_settings(client)
                if not registry.has("anidb"):
                    return summary
                provider = registry.build("anidb")
                if narrate:
                    activity.begin(POSTER_WARMUP_JOB, "Loading anime covers", total=len(aids))
                    started = True
                done = 0
                for aid in aids:
                    done += 1
                    if narrate:
                        activity.progress(POSTER_WARMUP_JOB, done, len(aids))
                    try:
                        url = await provider.get_picture_url(aid)  # type: ignore[attr-defined]
                    except Exception as e:  # noqa: BLE001
                        logger.info("poster warmup: %s lookup failed (non-fatal): %r", aid, e)
                        url = None
                    if not url:
                        continue
                    summary["resolved"] += 1
                    # Stamp EVERY anime match for this AID (every episode → its
                    # cover), but only where still NULL so a manual pick wins.
                    res = await session.execute(
                        update(Match)
                        .where(
                            Match.provider == "anidb",
                            Match.provider_id == aid,
                            Match.poster_url.is_(None),
                        )
                        .values(poster_url=url)
                    )
                    summary["updated"] += res.rowcount or 0
                await session.commit()
    except Exception as e:  # noqa: BLE001 — surface, never crash a scan/startup
        logger.warning("warm_anime_posters aborted (non-fatal): %r", e)
        try:
            async with SessionLocal() as s:
                await s.rollback()
        except Exception:
            pass
    finally:
        if started:
            activity.end(POSTER_WARMUP_JOB, ok=True,
                         detail=f"loaded {summary['resolved']} anime cover(s)")
    if summary["updated"]:
        logger.info("poster warmup: stored %d anime poster URLs across %d series",
                    summary["updated"], summary["resolved"])
    return summary


async def warm_music_covers() -> dict:
    """Prefetch Cover Art Archive covers for selected music matches into the
    image-proxy disk cache (audit §9 M): CAA/IA cold fetches run 2-8s per
    cover, so without a warmup the first library paint always paid it live.
    No 1req/s policy applies to CAA — run a few concurrent. Best-effort."""
    import asyncio as _asyncio
    from sqlalchemy import select
    from kira.database import SessionLocal
    from kira.models import Match, MediaFile
    summary = {"urls": 0, "warmed": 0}
    try:
        async with SessionLocal() as session:
            rows = (await session.execute(
                select(Match.poster_url)
                .join(MediaFile, MediaFile.id == Match.media_file_id)
                .where(
                    MediaFile.media_type == "music",
                    Match.is_selected.is_(True),
                    Match.poster_url.is_not(None),
                )
                .distinct()
            )).all()
        urls = [r[0] for r in rows if r[0] and "coverartarchive.org" in r[0]]
        summary["urls"] = len(urls)
        if not urls:
            return summary
        from kira.api.images import prefetch_into_cache
        sem = _asyncio.Semaphore(6)

        async def _one(u: str) -> None:
            async with sem:
                try:
                    if await prefetch_into_cache(u):
                        summary["warmed"] += 1
                except Exception:
                    pass

        await _asyncio.gather(*(_one(u) for u in urls), return_exceptions=True)
        logger.info("music cover warmup: %d/%d cached", summary["warmed"], summary["urls"])
    except Exception as e:  # noqa: BLE001 — warmup must never break a scan
        logger.warning("warm_music_covers aborted (non-fatal): %r", e)
    return summary


def spawn_poster_warmup() -> bool:
    """Fire-and-forget the warm-up (after scans / on startup). No-op without a
    running loop. Returns True if spawned."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    from kira.tasks import spawn_tracked
    spawn_tracked(warm_anime_posters(), label=POSTER_WARMUP_JOB)
    spawn_tracked(warm_music_covers(), label="music_cover_warmup")
    # Weekly anime-offline-database refresh — prefills AniDB episode counts so
    # anime matching skips most throttled live calls (no-op when fresh).
    from kira.providers.anime_offline_db import refresh_if_stale
    spawn_tracked(refresh_if_stale(), label="anime_offline_db")
    return True
