"""Drop a pack's per-episode subtitles via the EXISTING subtitle pipeline.

A pack ships subtitles made for the exact cut it describes, so they are
sync-perfect — the one case where ``sync="guaranteed"`` is honestly earned for
an external source. We reuse the audited primitives rather than re-implement
them: ``download_guard.fetch_capped`` (SSRF + byte cap), ``_common.save_sidecar``
(atomic, symlink-safe, no-clobber), and ``store.record_results`` (history row).
"""
from __future__ import annotations

import logging

from kira.packs.schema import PackSub
from kira.subtitles import _common
from kira.subtitles import store as _store
from kira.subtitles.embedded import normalize_lang
from kira.subtitles.model import SubtitleFetchResult

logger = logging.getLogger("kira.packs.subs")

_ALLOWED_EXTS = ("srt", "ass", "ssa", "vtt", "sub")
PACK_SUB_SCORE = 95   # below an embedded track (100), above a guessed external sub


def _ext_of(fmt: str | None) -> str:
    e = (fmt or "srt").strip().lower().lstrip(".")
    return e if e in _ALLOWED_EXTS else "srt"


async def fetch_pack_subs(
    session, media_file_id: int | None, file_path: str, subs: list[PackSub],
    title: str | None,
) -> int:
    """Fetch + save each subtitle and record history rows. Skips a language that
    already has a sidecar on disk. Returns the number saved. Never raises out."""
    if not subs or not file_path:
        return 0
    from kira.download_guard import fetch_capped, looks_like_error_page
    from kira.url_guard import is_safe_outbound_url

    client = None
    own = False
    try:
        from kira import net

        client = net.shared_client()
    except Exception:
        import httpx

        client = httpx.AsyncClient()
        own = True

    results: list[SubtitleFetchResult] = []
    try:
        for s in subs:
            lang = normalize_lang(s.lang) or (s.lang or "").strip().lower()
            if not lang:
                continue
            if _common.has_sidecar(file_path, lang):
                continue
            ok, _ = is_safe_outbound_url(s.url)
            if not ok:
                logger.info("packs.subs: skipping unsafe sub URL for %s", lang)
                continue
            got = await fetch_capped(
                client, s.url, max_bytes=_common.MAX_SUB_BYTES,
                timeout=30.0, follow_redirects=True,
            )
            if got is None:
                continue
            data, ct = got
            if looks_like_error_page(data, ct):
                logger.info("packs.subs: %s URL returned an error page, skipping", lang)
                continue
            path = _common.save_sidecar(file_path, lang, data, ext=_ext_of(s.format))
            if not path:
                continue
            results.append(SubtitleFetchResult(
                language=lang, path=path, provider="pack",
                release_name=(title or "Kira pack"), ref=s.url,
                score=PACK_SUB_SCORE, sync=s.sync,
                reasons=["from Kira pack (sync guaranteed for this cut)"]
                if s.sync == "guaranteed" else ["from Kira pack"],
            ))
        if results:
            await _store.record_results(session, media_file_id, title, results)
        return len(results)
    except Exception as e:
        logger.warning("packs.subs: fetch failed (non-fatal): %r", e)
        return len(results)
    finally:
        if own and client is not None:
            await client.aclose()


async def fetch_pack_subs_bg(
    media_file_id: int, file_path: str, subs_data: list[dict], title: str | None,
) -> int:
    """Background entry point — opens its own DB session so it can be spawned
    fire-and-forget from the scan path without sharing the scan's session."""
    from kira.database import SessionLocal

    try:
        subs = [PackSub.model_validate(d) for d in subs_data]
    except Exception:
        return 0
    async with SessionLocal() as session:
        return await fetch_pack_subs(session, media_file_id, file_path, subs, title)
