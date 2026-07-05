"""Subtitle coverage + backfill endpoints.

  GET  /subtitles/coverage           library-wide coverage summary (dashboard tile)
  POST /subtitles/backfill           fetch missing subs for files / whole library

The backfill is fire-and-forget: it returns immediately with how many files it
queued, then narrates progress through the activity surface (GET /activity) —
the same pill the scan + MediaInfo passes use, so the frontend already polls it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.orm import selectinload

from kira.database import get_session
from kira.models import MediaFile, SubtitleAsset
from kira.subtitles.backfill import (
    build_context, needed_languages, spawn_subtitle_backfill, spawn_subtitle_upgrade,
)
from kira.subtitles.coverage import has_been_inspected, missing_languages
from kira.subtitles.prefs import load_subtitle_prefs

router = APIRouter(prefix="/subtitles", tags=["subtitles"])


class CoverageOut(BaseModel):
    wanted: list[str]            # the user's preferred languages (2-letter)
    enabled: bool                # at least one source is usable
    inspected: int               # files whose container we've read (coverage known)
    covered: int                 # inspected files with NO missing wanted language
    missing_files: int           # inspected files missing >=1 wanted language
    by_language: dict[str, int]  # language → how many files are missing it


class BackfillBody(BaseModel):
    # Explicit file set; omit + scope="library" to sweep everything missing subs.
    file_ids: list[int] = Field(default_factory=list, max_length=100_000)
    scope: str | None = None                 # "library" → all missing-sub files
    languages: list[str] | None = None       # override the wanted languages


class BackfillStarted(BaseModel):
    started: bool
    queued: int
    detail: str | None = None


@router.get("/coverage", response_model=CoverageOut)
async def coverage(session: AsyncSession = Depends(get_session)) -> CoverageOut:
    """Library-wide subtitle coverage against the wanted languages — drives the
    dashboard tile. Pure read over parsed_data; no disk I/O."""
    prefs = await load_subtitle_prefs(session)
    wanted = prefs.languages
    rows = (await session.execute(
        select(MediaFile.id, MediaFile.parsed_data, MediaFile.media_type))).all()
    inspected = covered = missing_files = 0
    by_language: dict[str, int] = {}
    for _id, parsed, media_type in rows:
        if not has_been_inspected(parsed):
            continue
        inspected += 1
        miss = missing_languages(parsed, prefs.languages_for(media_type)) or []
        if miss:
            missing_files += 1
            for lang in miss:
                by_language[lang] = by_language.get(lang, 0) + 1
        else:
            covered += 1
    return CoverageOut(
        wanted=wanted,
        enabled=prefs.any_source_enabled,
        inspected=inspected,
        covered=covered,
        missing_files=missing_files,
        by_language=by_language,
    )


@router.post("/backfill", response_model=BackfillStarted)
async def backfill(body: BackfillBody, session: AsyncSession = Depends(get_session)) -> BackfillStarted:
    """Queue a subtitle backfill. With `file_ids` it targets exactly those;
    with `scope="library"` it sweeps every file currently missing a wanted
    language. Returns immediately — progress streams via /activity."""
    prefs = await load_subtitle_prefs(session)
    if not prefs.any_source_enabled:
        raise HTTPException(
            400,
            "No subtitle source enabled — turn on embedded extraction or add an "
            "OpenSubtitles API key (Settings → Subtitles).",
        )
    wanted = [w.lower() for w in (body.languages or prefs.languages) if w]
    if not wanted:
        raise HTTPException(400, "No subtitle languages configured.")

    target_ids: list[int] = list(body.file_ids)
    if not target_ids and body.scope == "library":
        # Sweep: every file still missing a wanted language (per media type,
        # unless the caller forced an explicit language set).
        rows = (await session.execute(
            select(MediaFile.id, MediaFile.parsed_data, MediaFile.media_type)
        )).all()
        target_ids = [
            _id for _id, parsed, media_type in rows
            if needed_languages(parsed, body.languages or prefs.languages_for(media_type))
        ]
    if not target_ids:
        return BackfillStarted(started=False, queued=0, detail="Nothing to fetch.")

    spawned = spawn_subtitle_backfill(target_ids, language_override=body.languages)
    return BackfillStarted(
        started=spawned,
        queued=len(target_ids),
        detail=None if spawned else "Could not start (no event loop).",
    )


@router.post("/upgrade", response_model=dict)
async def trigger_upgrade(session: AsyncSession = Depends(get_session)) -> dict:
    """Kick off an upgrade-over-time sweep — re-check low-scoring subtitles for
    a better candidate and replace any that improve. Fire-and-forget; progress
    narrates through /activity."""
    prefs = await load_subtitle_prefs(session)
    if not prefs.upgrade:
        raise HTTPException(400, "Upgrade-over-time is off — enable it in Settings → Subtitles.")
    started = spawn_subtitle_upgrade()
    return {"started": started}


# ── Phase 2: subtitle history + management ───────────────────────────


class SubtitleAssetOut(BaseModel):
    id: int
    media_file_id: int | None
    language: str
    provider: str
    release_name: str | None
    score: int
    sync: str
    reasons: list | None
    hearing_impaired: bool
    forced: bool
    title: str | None
    active: bool
    blacklisted: bool
    created_at: str


@router.get("/history", response_model=list[SubtitleAssetOut])
async def subtitle_history(
    limit: int = 500, session: AsyncSession = Depends(get_session),
) -> list[SubtitleAssetOut]:
    """Every subtitle Kira fetched — provider, release, score, sync — newest
    first. The subtitle ledger (delete / blacklist act on these rows)."""
    rows = list(await session.scalars(
        select(SubtitleAsset).order_by(SubtitleAsset.created_at.desc()).limit(limit)
    ))
    return [SubtitleAssetOut(
        id=r.id, media_file_id=r.media_file_id, language=r.language, provider=r.provider,
        release_name=r.release_name, score=r.score, sync=r.sync, reasons=r.reasons,
        hearing_impaired=r.hearing_impaired, forced=r.forced, title=r.title,
        active=r.active, blacklisted=r.blacklisted,
        created_at=r.created_at.isoformat() if r.created_at else "",
    ) for r in rows]


@router.delete("/asset/{asset_id}", response_model=dict)
async def delete_subtitle_asset(
    asset_id: int, blacklist: bool = False, session: AsyncSession = Depends(get_session),
) -> dict:
    """Delete a subtitle sidecar (and mark its history row inactive). With
    `blacklist=true`, also exclude that exact candidate from future auto-picks
    for the file. Either way the file shows the language missing again."""
    from kira.subtitles import store
    result = await store.remove_asset(session, asset_id, blacklist=blacklist)
    if not result.get("ok"):
        raise HTTPException(404, "Subtitle record not found")
    return result


# ── Phase 3: manual browse & pick ────────────────────────────────────


class CandidateOut(BaseModel):
    provider: str
    language: str
    release_name: str
    downloads: int
    rating: float | None
    hash_match: bool
    hearing_impaired: bool
    forced: bool
    is_pack: bool
    from_embedded: bool
    score: int
    reasons: list
    sync: str
    ref: str   # opaque handle to pass back to /pick


class PickBody(BaseModel):
    file_id: int
    provider: str
    language: str
    ref: str


class PackExtractBody(BaseModel):
    file_id: int
    provider: str
    language: str
    ref: str
    entry: str   # the exact archive entry name the user chose


class PackHarvestBody(BaseModel):
    file_id: int      # the file we just picked for (anchors the series)
    provider: str
    ref: str
    language: str


class PackEntryOut(BaseModel):
    name: str
    score: int
    reasons: list
    guessed_episode: int | None = None


async def _file_with_matches(session: AsyncSession, file_id: int) -> MediaFile:
    mf = await session.scalar(
        select(MediaFile).options(selectinload(MediaFile.matches)).where(MediaFile.id == file_id))
    if mf is None:
        raise HTTPException(404, "File not found")
    if not mf.file_path:
        raise HTTPException(422, "File has no on-disk path")
    return mf


@router.get("/candidates", response_model=list[CandidateOut])
async def list_candidates(
    file_id: int, language: str | None = None, session: AsyncSession = Depends(get_session),
) -> list[CandidateOut]:
    """Scored subtitle candidates across all enabled providers for one file —
    the manual browse-and-pick list. No download happens; this is the menu."""
    from kira import net
    from kira.subtitles.aggregate import gather_candidates
    prefs = await load_subtitle_prefs(session)
    if not prefs.any_source_enabled:
        raise HTTPException(400, "No subtitle source enabled (Settings → Subtitles).")
    mf = await _file_with_matches(session, file_id)
    # Per-TYPE languages (audit §20 m): the browse menu used the global list,
    # so an anime with per-type ja+en prefs offered only the global languages.
    langs = [language.lower()] if language else prefs.languages_for(mf.media_type)
    ctx = await build_context(session, mf, prefs, langs)
    cands = await gather_candidates(net.shared_client(), ctx, prefs.sources_for(ctx.media_type))
    return [CandidateOut(ref=str(c.download_ref), **c.public()) for c in cands]


def _pick_error_message(outcome: dict, provider: str) -> str:
    """Turn a manual_pick failure code into a specific, actionable message —
    never the old generic 'Download failed'."""
    err = outcome.get("error")
    if err == "no_rar_tool":
        return (f"{provider} served a RAR archive and this machine has no RAR "
                "extractor Kira can use. Install 7-Zip, WinRAR, or unrar (or drop "
                "an `unrar`/`7z` binary in Kira's tools folder), then retry — "
                "ZIP and 7z packs work without anything extra.")
    if err == "rar_extract_failed":
        return (f"{provider} served a RAR archive but the available extractor "
                "couldn't read it (Windows' built-in tar often lacks RAR support). "
                "Install 7-Zip or WinRAR for full RAR support, then retry.")
    if err == "unsupported_archive":
        kind = (outcome.get("kind") or "").upper()
        return (f"{provider} served this subtitle as a {kind} archive, which Kira "
                "can't open yet — try another candidate (ZIP/7z/RAR work).")
    if err == "too_large":
        return (f"{provider}'s file was larger than Kira's 64 MB cap — try another candidate.")
    if err == "empty_archive":
        return (f"{provider}'s archive had no subtitle files Kira recognizes — try another candidate.")
    if err == "entry_not_found":
        return ("That entry is no longer in the pack (the download may have refreshed). "
                "Reopen the title and try again, or pick a different candidate.")
    if err == "save_failed":
        return "Couldn't write the subtitle next to your video (check folder permissions / disk space)."
    # download_failed / extract_failed / unknown
    return (f"Couldn't download from {provider} — it may have returned an error page "
            "or a redirect that failed. Try another candidate.")


async def _attach_pack_offer(session: AsyncSession, mf: MediaFile, chosen, language: str, resp: dict) -> None:
    """If the pick came from a season pack still warm in cache, count how many
    OTHER episodes it could fill and attach an opt-in offer to the response.
    Does NOT save anything — filling happens only if the user clicks."""
    from kira.subtitles import pack as _pack
    if _pack.get_cached_pack(chosen.provider, str(chosen.download_ref)) is None:
        return
    from kira.subtitles.backfill import count_missing_siblings
    more = await count_missing_siblings(session, mf, language)
    if more > 0:
        resp["pack_more"] = more
        resp["provider"] = chosen.provider
        resp["ref"] = str(chosen.download_ref)
        resp["language"] = language


async def _record_pick(session: AsyncSession, mf: MediaFile, res) -> dict:
    """Persist a freshly-saved subtitle to history + reflect it in coverage so
    the missing-sub chip clears without a rescan. Returns the API response."""
    from kira.subtitles import store
    from kira.subtitles.backfill import _record_langs
    title = next((m.title for m in mf.matches if m.is_selected and m.title), None)
    # Tag as a MANUAL pick so the upgrade sweep never overrides a deliberate
    # user choice (audit §20 m).
    if isinstance(res.reasons, list) and "manual pick" not in res.reasons:
        res.reasons = ["manual pick", *res.reasons]
    await store.record_results(session, mf.id, title, [res])
    await _record_langs(session, mf.id, [res.language])
    return {"ok": True, "language": res.language, "provider": res.provider,
            "score": res.score, "sync": res.sync, "reasons": res.reasons}


@router.post("/pick", response_model=dict)
async def pick_candidate(body: PickBody, session: AsyncSession = Depends(get_session)) -> dict:
    """Download a SPECIFIC candidate the user chose in the browse modal (by
    provider+ref), save it, and record it — overriding the auto-pick.

    For a single-episode subtitle this saves immediately. For a SEASON PACK it
    saves immediately IF Kira can identify the episode confidently (using the
    matched S/E, absolute number, episode title, runtime, and release group);
    when the pack is ambiguous it returns ``needs_choice`` + the ranked entries
    so the user picks the right one (the archive is cached for the follow-up)."""
    from kira import net
    from kira.subtitles.aggregate import gather_candidates, manual_pick
    prefs = await load_subtitle_prefs(session)
    mf = await _file_with_matches(session, file_id=body.file_id)
    ctx = await build_context(session, mf, prefs, [body.language.lower()])
    cands = await gather_candidates(net.shared_client(), ctx, prefs.sources_for(ctx.media_type))
    chosen = next((c for c in cands
                   if c.provider == body.provider and str(c.download_ref) == body.ref), None)
    if chosen is None:
        raise HTTPException(404, "That subtitle is no longer available — refresh the list.")
    outcome = await manual_pick(net.shared_client(), ctx, chosen)
    if outcome.get("ok"):
        resp = await _record_pick(session, mf, outcome["result"])
        if outcome.get("already_present"):
            resp["already_present"] = True
        # A single-episode pick affects ONLY this episode. If it came from a
        # season pack (still cached), OFFER to fill the rest — opt-in, never
        # silently mass-patching the library off one click.
        await _attach_pack_offer(session, mf, chosen, body.language.lower(), resp)
        return resp
    if outcome.get("needs_choice"):
        # Not a failure — the pack holds several episodes and we can't be sure
        # which is yours; let the user choose from the ranked contents.
        return {"ok": False, "needs_choice": True, "provider": chosen.provider,
                "ref": str(chosen.download_ref), "language": body.language.lower(),
                "episode": ctx.episode, "entries": outcome["entries"]}
    raise HTTPException(502, _pick_error_message(outcome, chosen.provider))


@router.post("/pack/extract", response_model=dict)
async def extract_pack_entry(body: PackExtractBody, session: AsyncSession = Depends(get_session)) -> dict:
    """Save the SPECIFIC entry a user chose from an ambiguous season pack (after
    /pick returned needs_choice). Reuses the cached archive when warm, else
    re-downloads. Records to history + clears the coverage chip."""
    from kira import net
    from kira.subtitles.aggregate import gather_candidates, save_pack_entry
    prefs = await load_subtitle_prefs(session)
    mf = await _file_with_matches(session, file_id=body.file_id)
    ctx = await build_context(session, mf, prefs, [body.language.lower()])
    cands = await gather_candidates(net.shared_client(), ctx, prefs.sources_for(ctx.media_type))
    chosen = next((c for c in cands
                   if c.provider == body.provider and str(c.download_ref) == body.ref), None)
    if chosen is None:
        raise HTTPException(404, "That subtitle is no longer available — refresh the list.")
    # If the language is already on disk, save_pack_entry would clobber-fail;
    # re-assert coverage and report it instead of erroring.
    from kira.subtitles._common import find_sidecar
    existing = find_sidecar(mf.file_path, body.language.lower())
    if existing:
        from kira.subtitles.model import SubtitleFetchResult
        res = SubtitleFetchResult(language=body.language.lower(), path=existing,
                                  provider=chosen.provider, ref=str(chosen.download_ref),
                                  score=chosen.score, sync=chosen.sync, reasons=chosen.reasons)
        resp = await _record_pick(session, mf, res)
        resp["already_present"] = True
        return resp
    outcome = await save_pack_entry(net.shared_client(), ctx, chosen, body.entry)
    if not outcome.get("ok"):
        raise HTTPException(502, _pick_error_message(outcome, chosen.provider))
    resp = await _record_pick(session, mf, outcome["result"])
    await _attach_pack_offer(session, mf, chosen, body.language.lower(), resp)
    return resp


@router.post("/pack/harvest", response_model=dict)
async def harvest_pack(body: PackHarvestBody, session: AsyncSession = Depends(get_session)) -> dict:
    """User OPTED IN to fill the rest of the season from the pack they just
    downloaded — extract every sibling episode's subtitle from the pack. Reuses
    the cached archive when warm; if it has aged out of the short-lived cache it
    is re-fetched ONCE (one download still covers the whole season). Returns how
    many were saved; coverage chips clear for all of them. Never mass-patches
    without this explicit call."""
    from kira import net
    from kira.subtitles.backfill import harvest_from_cached_pack
    mf = await _file_with_matches(session, body.file_id)
    saved = await harvest_from_cached_pack(
        session, mf, body.provider, body.ref, body.language.lower(),
        client=net.shared_client())
    return {"harvested": saved}
