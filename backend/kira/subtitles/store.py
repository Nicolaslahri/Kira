"""Persistence for subtitle history + management — the DB layer over
SubtitleAsset. Keeps aggregate.py / scoring.py pure (no DB) while the backfill,
the manual-pick endpoint, and the history view all go through here.

A row per (file, language) download records the metric + provenance (provider,
release, score, sync, reasons). `active` flips false when the sidecar is
deleted; `blacklisted` rows additionally exclude that exact candidate from
future auto-picks for the file.
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kira.models import MediaFile, SubtitleAsset
from kira.parser.parser import ParsedFile
from kira.subtitles.model import SubtitleFetchResult

logger = logging.getLogger("kira.subtitles.store")


async def load_blacklist(session: AsyncSession, media_file_id: int | None) -> set:
    """{(provider, ref)} the user blacklisted for this file — fed into the
    aggregator so a binned sub is never re-picked."""
    if not media_file_id:
        return set()
    rows = await session.scalars(
        select(SubtitleAsset).where(
            SubtitleAsset.media_file_id == media_file_id,
            SubtitleAsset.blacklisted.is_(True),
        )
    )
    return {(r.provider, str(r.ref)) for r in rows if r.ref is not None}


async def record_results(
    session: AsyncSession, media_file_id: int | None, title: str | None,
    results: list[SubtitleFetchResult],
) -> None:
    """Persist freshly-saved subtitles as history rows. Supersedes any prior
    ACTIVE row for the same (file, language) so the list shows the current sub.
    Best-effort; never raises out."""
    if not results:
        return
    try:
        for r in results:
            # Deactivate the previous active asset for this file+language.
            if media_file_id:
                prev = await session.scalars(
                    select(SubtitleAsset).where(
                        SubtitleAsset.media_file_id == media_file_id,
                        SubtitleAsset.language == r.language,
                        SubtitleAsset.active.is_(True),
                    )
                )
                for p in prev:
                    p.active = False
            session.add(SubtitleAsset(
                media_file_id=media_file_id, language=r.language, provider=r.provider,
                release_name=r.release_name or None, ref=r.ref, score=r.score,
                sync=r.sync, reasons=r.reasons or None, path=r.path, title=title,
                active=True, blacklisted=False,
            ))
        await session.commit()
    except Exception as e:
        logger.warning(f"record_results failed (non-fatal): {e!r}")
        try:
            await session.rollback()
        except Exception:
            pass


async def _drop_sidecar_lang(session: AsyncSession, media_file_id: int | None, lang: str) -> None:
    """Remove `lang` from the file's parsed_data.sub_sidecars so coverage shows
    it missing again (and a re-fetch will look for it)."""
    if not media_file_id:
        return
    mf = await session.get(MediaFile, media_file_id)
    if mf is None or not isinstance(mf.parsed_data, dict):
        return
    # Mutate the dict directly (not via ParsedFile) — robust to partial
    # parsed_data, and we only touch the one key.
    sc = mf.parsed_data.get("sub_sidecars")
    if isinstance(sc, list) and lang in sc:
        parsed = dict(mf.parsed_data)
        parsed["sub_sidecars"] = [l for l in sc if l != lang]
        mf.parsed_data = parsed


async def remove_asset(session: AsyncSession, asset_id: int, *, blacklist: bool) -> dict:
    """Delete the on-disk sidecar + deactivate the asset (optionally blacklist
    its candidate). Returns {ok, deleted_file}. Never raises out."""
    asset = await session.get(SubtitleAsset, asset_id)
    if asset is None:
        return {"ok": False, "detail": "not found"}
    deleted_file = False
    if asset.path:
        try:
            if os.path.isfile(asset.path):
                os.remove(asset.path)
                deleted_file = True
        except OSError as e:
            logger.warning(f"remove_asset: unlink {asset.path} failed: {e!r}")
    asset.active = False
    asset.path = None
    if blacklist:
        asset.blacklisted = True
    await _drop_sidecar_lang(session, asset.media_file_id, asset.language)
    await session.commit()
    return {"ok": True, "deleted_file": deleted_file, "language": asset.language,
            "media_file_id": asset.media_file_id}
