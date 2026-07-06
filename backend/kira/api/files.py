import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from kira.api.webhooks import path_under_roots
from kira.database import get_session
from kira.models import MediaFile, RenameHistory, Setting
from kira.schemas import FileStatusUpdate, MediaFileOut
from kira.settings_store import unwrap_str as _unwrap_path  # canonical settings-value unwrap

logger = logging.getLogger(__name__)

VALID_STATUSES = {"pending", "matching", "matched", "approved", "rejected", "no_match", "discovered"}

router = APIRouter(prefix="/files", tags=["files"])


class VerifyExistIn(BaseModel):
    ids: list[int]


async def _managed_roots(session: AsyncSession) -> list[str]:
    """Every directory Kira legitimately manages — scan SOURCES (library_root,
    watch_folders) and rename DESTINATIONS (the named library_roots dict + the
    per-type targets). Used to refuse a disk delete whose DB path sits outside
    all of them (defence-in-depth against a corrupt/injected file_path)."""
    roots: list[str] = []
    single = await session.get(Setting, "paths.library_root")
    if single is not None and (p := _unwrap_path(single.value)):
        roots.append(p)
    watch = await session.get(Setting, "paths.watch_folders")
    if watch is not None and isinstance(watch.value, list):
        roots.extend(p.strip() for p in watch.value if isinstance(p, str) and p.strip())
    named = await session.get(Setting, "paths.library_roots")
    if named is not None and isinstance(named.value, dict):
        roots.extend(p.strip() for p in named.value.values() if isinstance(p, str) and p.strip())
    for mt in ("movie", "tv", "anime", "music"):
        tgt = await session.get(Setting, f"paths.targets.{mt}")
        if tgt is not None and (p := _unwrap_path(tgt.value)):
            roots.append(p)
    return roots


async def _managed_roots_aliased(session: AsyncSession) -> list[str]:
    """`_managed_roots` plus each root's RESOLVED spelling, so a containment
    check matches a path persisted under a different spelling of the same
    location. The rename engine stores resolved paths — on Windows a mapped
    drive (`Z:\\`) resolves to its UNC target (`\\\\192.168.0.63\\Data\\...`),
    so `RenameHistory.created_assets` is UNC while `paths.library_root` is the
    drive-letter form. Undo's asset + folder cleanup is gated by
    `path_under_roots`, which is purely lexical → it skipped EVERY recorded
    asset (UNC not under `Z:\\`), orphaning the NFO/artwork/subs it should
    delete. Mirrors the scan prune's drive-letter↔UNC bridge: resolve each root
    ONCE (a filesystem round-trip) — never per file. Best-effort; an
    unreachable root just contributes no alias."""
    roots = await _managed_roots(session)
    seen = {r.lower() for r in roots if r}

    def _resolved() -> list[str]:
        acc: list[str] = []
        for p in roots:
            try:
                rp = str(Path(p).resolve())
            except OSError:
                continue
            if rp and rp.lower() not in seen:
                seen.add(rp.lower())
                acc.append(rp)
        return acc

    return list(roots) + await asyncio.to_thread(_resolved)


@router.post("/verify-exist")
async def verify_exist(
    payload: VerifyExistIn,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Report which of the given tracked files no longer exist on disk.

    The duplicate-resolution UI calls this for the FEW files that collide on one
    episode, so it can drop a stale ghost row from the group BEFORE showing a
    "duplicate" — e.g. a file you renamed away on disk leaves a row pointing at
    the old path; without this it looks like a second copy and the modal would
    offer to delete your only real file. Report-only: the row itself is removed
    by the next scan's (drive-letter/UNC alias-aware) prune.

    Confirmed-gone == FileNotFoundError; a permission / NAS hiccup (OSError)
    counts as "present", so a momentary blip never hides a real file. The stat
    runs off the event loop. Bounded to 1000 ids (only collision candidates are
    ever sent, so this is tiny in practice)."""
    ids = list(dict.fromkeys(payload.ids))[:1000]
    if not ids:
        return {"missing": []}
    rows = (await session.execute(
        select(MediaFile.id, MediaFile.file_path).where(MediaFile.id.in_(ids))
    )).all()

    def _gone(p: str) -> bool:
        try:
            Path(p).stat()
            return False
        except FileNotFoundError:
            return True
        except OSError:
            return False

    missing: list[int] = []
    for fid, fp in rows:
        if fp and await asyncio.to_thread(_gone, fp):
            missing.append(fid)
    return {"missing": missing}


@router.post("/reconcile", response_model=dict[str, object])
async def reconcile_files(session: AsyncSession = Depends(get_session)) -> dict:
    """Walk-FREE deletion sweep: stat every pre-rename (review-stage) tracked file
    and drop the ones CONFIRMED gone from disk. The frontend calls this on page
    load, so a file you deleted clears on REFRESH — no scan needed, and immune to
    the scan's "one unreadable folder skips the whole sweep" fragility (this stats
    individual files, it never walks directories, so there's no all-or-nothing
    gate).

    NAS-blip-safe: only FileNotFoundError prunes; an OSError (permission / NAS
    hiccup) keeps the row — a momentary blip never hides a real file. The row +
    its Match go, RenameHistory is preserved (same as the scan prune / a manual
    delete with keep_on_disk). Returns the removed file ids."""
    rows = (await session.execute(
        select(MediaFile.id, MediaFile.file_path).where(MediaFile.status.in_(VALID_STATUSES))
    )).all()

    def _gone(p: str) -> bool:
        try:
            Path(p).stat()
            return False
        except FileNotFoundError:
            return True
        except OSError:
            return False  # permission / NAS blip → keep (never hide a real file)

    # Stat CONCURRENTLY (bounded) — the old one-at-a-time `to_thread` loop was
    # 10k sequential SMB round-trips on every Review mount: 20-100s of request
    # runtime holding a session. 24-wide keeps the NAS comfortable while
    # collapsing wall-clock ~20×. DB writes stay strictly on this coroutine
    # (the AsyncSession is not concurrency-safe).
    sem = asyncio.Semaphore(24)

    async def _check(fid: int, fp: str) -> int | None:
        if not fp:
            return None
        async with sem:
            return fid if await asyncio.to_thread(_gone, fp) else None

    gone_ids = [
        fid for fid in await asyncio.gather(*(_check(fid, fp) for fid, fp in rows))
        if fid is not None
    ]

    removed: list[int] = []
    for fid in gone_ids:
        mf = await session.get(MediaFile, fid)
        if mf is None:
            continue
        try:
            await _delete_one(session, mf, keep_on_disk=True, roots=[])
            removed.append(fid)
        except Exception as e:  # noqa: BLE001 — one bad row never blocks the rest
            logger.warning("reconcile: file %s failed (non-fatal): %r", fid, e)
    if removed:
        await session.commit()
    return {"removed": len(removed), "ids": removed}


@router.get("", response_model=list[MediaFileOut])
async def list_files(
    media_type: str | None = None,
    status: str | None = None,
    limit: int = Query(500, ge=1, le=100_000),
    session: AsyncSession = Depends(get_session),
) -> list[MediaFile]:
    stmt = (
        select(MediaFile)
        .options(selectinload(MediaFile.matches))
        .order_by(MediaFile.created_at.desc())
        .limit(limit)
    )
    if media_type is not None:
        stmt = stmt.where(MediaFile.media_type == media_type)
    if status is not None:
        stmt = stmt.where(MediaFile.status == status)
    result = await session.scalars(stmt)
    files = list(result)
    # Wanted subtitle languages, read once for the whole page. Coverage is a
    # pure read over each row's parsed_data (no disk I/O), attached as a
    # non-mapped attribute the MediaFileOut serializer picks up.
    from kira.subtitles.coverage import missing_languages
    from kira.subtitles.prefs import load_subtitle_prefs
    prefs = await load_subtitle_prefs(session)
    for f in files:
        # Sort matches per file by confidence desc — DB order isn't guaranteed.
        f.matches.sort(key=lambda m: m.confidence, reverse=True)
        # Wanted languages are per media type (anime may differ from movies).
        f.missing_subs = missing_languages(f.parsed_data, prefs.languages_for(f.media_type))
    return files


@router.get("/delta")
async def list_files_delta(
    since: str = Query(..., description="ISO timestamp from a previous response's `now`"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Incremental follow-up to GET /files for mid-scan refreshes: rows whose
    `updated_at` moved past `since`, plus the full current id set (so the
    client can drop deletions). The FULL fetch stays the source of truth for
    initial hydrate + scan completion; this keeps the 10s-poll cheap on
    libraries where shipping every row each tick was megabytes."""
    from datetime import datetime as _dt
    try:
        cutoff = _dt.fromisoformat(since.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(400, "`since` must be an ISO timestamp.")
    now = _dt.utcnow()

    changed = list(await session.scalars(
        select(MediaFile)
        .options(selectinload(MediaFile.matches))
        .where(MediaFile.updated_at.is_not(None), MediaFile.updated_at > cutoff)
        .order_by(MediaFile.created_at.desc())
        .limit(5000)
    ))
    from kira.subtitles.coverage import missing_languages
    from kira.subtitles.prefs import load_subtitle_prefs
    prefs = await load_subtitle_prefs(session)
    for f in changed:
        f.matches.sort(key=lambda m: m.confidence, reverse=True)
        f.missing_subs = missing_languages(f.parsed_data, prefs.languages_for(f.media_type))
    ids = [r[0] for r in (await session.execute(select(MediaFile.id))).all()]
    return {
        "now": now.isoformat(),
        "changed": [MediaFileOut.model_validate(f).model_dump(mode="json") for f in changed],
        "ids": ids,
    }


@router.patch("/{file_id}", response_model=MediaFileOut)
async def update_file(
    file_id: int,
    payload: FileStatusUpdate,
    session: AsyncSession = Depends(get_session),
) -> MediaFile:
    if payload.status not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of {VALID_STATUSES}")
    media_file = await session.scalar(
        select(MediaFile)
        .options(selectinload(MediaFile.matches))
        .where(MediaFile.id == file_id)
    )
    if media_file is None:
        raise HTTPException(404, "File not found")
    media_file.status = payload.status
    await session.commit()
    # `updated_at` has `onupdate=func.now()`, so SQLite recalculates it
    # server-side on every UPDATE. The Python in-memory value is now
    # stale, and SQLAlchemy marks it for refresh — when the Pydantic
    # serializer reads it, it triggers an implicit lazy-load that
    # raises `MissingGreenlet` in async context. Force the refresh now,
    # inside the async session, so the read later is a pure attribute lookup.
    await session.refresh(media_file, ["updated_at"])
    media_file.matches.sort(key=lambda m: m.confidence, reverse=True)
    return media_file


@router.delete("/{file_id}", response_model=dict[str, object])
async def delete_file(
    file_id: int,
    confirm: bool = Query(False, description="Must be true — guard against accidental deletion"),
    keep_on_disk: bool = Query(False, description="Drop the DB row but leave the .mkv alone"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Delete a MediaFile row AND remove the underlying file from disk.

    Used by the duplicate-resolution flow in CoverPopup — when two release
    groups of the same episode exist, the user picks one to keep and the
    other gets nuked here. Irreversible — `confirm=true` is required even
    though the UI also shows its own modal, so a careless curl can't
    silently wipe files.

    Cascade behavior:
      - Match rows for this file are deleted (SQLAlchemy `cascade="all,
        delete-orphan"` on MediaFile.matches).
      - RenameHistory rows are PRESERVED — we explicitly null their
        media_file_id so the History page keeps showing past renames even
        after the source file is gone.
    """
    if not confirm:
        raise HTTPException(400, "Pass ?confirm=true to actually delete.")

    media_file = await session.get(MediaFile, file_id)
    if media_file is None:
        raise HTTPException(404, "File not found")

    disk_path = media_file.file_path
    # Physical deletion happens FIRST inside _delete_one — if it fails
    # (permission denied, locked file, outside the managed roots), it raises and
    # we leave the DB row alone so the UI doesn't show a phantom-deleted entry
    # that still exists on disk. Shared with the bulk-delete path.
    roots = await _managed_roots(session)
    disk_status = await _delete_one(session, media_file, keep_on_disk=keep_on_disk, roots=roots)
    await session.commit()

    return {"deleted": file_id, "disk": disk_status, "path": disk_path}


def _safe_unlink(p: Path) -> bool:
    """Delete p if it exists; return True if a file was actually removed."""
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False


async def _delete_one(
    session: AsyncSession, media_file: MediaFile, *, keep_on_disk: bool, roots: list[str],
) -> str:
    """Delete one file from disk (unless keep_on_disk) + drop its row, preserving
    RenameHistory. Returns the disk status ("deleted"/"missing"/"skipped").
    Raises HTTPException on a guard/permission/OS failure (so the caller can
    record a per-file error). Does NOT commit — the caller controls the txn.

    Shared by the single `DELETE /{file_id}` and the `POST /bulk-delete` paths so
    the confinement + RenameHistory-preserve behavior can't drift between them."""
    disk_path = media_file.file_path
    disk_status = "skipped" if keep_on_disk else "missing"
    if not keep_on_disk and disk_path:
        # Defence-in-depth: only ever delete inside a configured library root.
        if roots and not path_under_roots(disk_path, roots):
            raise HTTPException(
                400,
                "Refusing to delete a file outside the configured library roots. "
                "Pass keep_on_disk=true to drop only the database row.",
            )
        try:
            existed = await asyncio.to_thread(_safe_unlink, Path(disk_path))
            disk_status = "deleted" if existed else "missing"
        except PermissionError as e:
            raise HTTPException(403, f"Couldn't delete on disk: {e}") from e
        except OSError as e:
            raise HTTPException(500, f"Disk delete failed: {e}") from e
    # Preserve RenameHistory by nulling the FK before the cascade nukes it.
    await session.execute(
        update(RenameHistory)
        .where(RenameHistory.media_file_id == media_file.id)
        .values(media_file_id=None)
    )
    await session.delete(media_file)
    return disk_status


@router.post("/bulk-delete", response_model=dict[str, object])
async def bulk_delete(
    payload: dict[str, object],
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Delete MANY files at once (the duplicate-resolution "keep best, delete the
    rest" flow). Body: {file_ids: int[], keep_on_disk?: bool}.

    Each file is processed and committed independently so one locked/permission-
    denied file can't abort the rest of the batch — the response reports exactly
    which ids were deleted and which failed (with a reason), letting the UI
    update optimistically for the successes and surface only the real failures.
    No `confirm` flag: a non-empty `file_ids` list IS the intent, and the UI
    shows a single batch confirmation before calling this.
    """
    raw_ids = payload.get("file_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(400, "Body must be {file_ids: int[], keep_on_disk?: bool}")
    try:
        file_ids = [int(x) for x in raw_ids]
    except (TypeError, ValueError):
        raise HTTPException(400, "file_ids must be integers")
    keep_on_disk = bool(payload.get("keep_on_disk", False))

    roots = await _managed_roots(session)
    deleted: list[int] = []
    failed: list[dict[str, object]] = []
    for fid in file_ids:
        media_file = await session.get(MediaFile, fid)
        if media_file is None:
            # Already gone (e.g. deleted in a prior partial run / concurrent
            # tab) — treat as success so the UI converges, not a hard error.
            deleted.append(fid)
            continue
        try:
            await _delete_one(session, media_file, keep_on_disk=keep_on_disk, roots=roots)
            await session.commit()
            deleted.append(fid)
        except HTTPException as e:
            await session.rollback()
            failed.append({"id": fid, "error": e.detail})
        except Exception as e:  # noqa: BLE001 — never let one file kill the batch
            await session.rollback()
            failed.append({"id": fid, "error": str(e)})

    return {"deleted": deleted, "failed": failed, "count": len(deleted)}


@router.post("/reparse-all", response_model=dict[str, int])
async def reparse_all(
    media_type: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Re-run the filename parser over every MediaFile and update parsed_data
    + series_key. Doesn't touch matches.

    Run this after a parser-pattern improvement ships (e.g. the WxH
    resolution fix) so existing rows pick up the new tokens without
    waiting for the next match cycle. Cheap — pure regex over names.

    Tech tags from real container metadata (resolution/codec/HDR/channels) are
    then refilled in the BACKGROUND when `parsing.read_mediainfo` is on — this
    handler never does the slow per-file container reads itself, so it returns
    immediately even over a NAS and even in authoritative mode.
    """
    from pathlib import Path as _Path
    from kira.parser import parse_filename
    from kira.api.scans import _compute_series_key, _spawn_mediainfo_enrich

    stmt = select(MediaFile)
    if media_type is not None:
        stmt = stmt.where(MediaFile.media_type == media_type)
    files = list(await session.scalars(stmt))

    changed = 0
    enrich_ids: list[int] = []
    for mf in files:
        if not mf.file_path:
            continue
        if mf.id is not None:
            enrich_ids.append(mf.id)
        parent = str(_Path(mf.file_path).parent)
        fresh = parse_filename(_Path(mf.file_path).name, parent_path=parent)
        # Pure regex reparse drops any prior MediaInfo enrichment (fresh is
        # filename-only); the background pass below re-applies it. With
        # read_mediainfo off, filename-only IS the intended result.
        new_data = fresh.to_dict()
        if new_data != mf.parsed_data:
            mf.parsed_data = new_data
            mf.media_type = fresh.media_type
            mf.series_key = _compute_series_key(fresh)
            changed += 1

    await session.commit()
    # Hand the container reads to the detached background pass (own session, off
    # the request path). No-op unless `parsing.read_mediainfo` is enabled.
    _spawn_mediainfo_enrich(enrich_ids)
    return {"scanned": len(files), "updated": changed}


@router.post("/bulk-status", response_model=dict[str, int])
async def bulk_status(
    payload: dict[str, list[int] | str],
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Update status for many files at once. Body: {ids: [1,2,3], status: 'approved'}."""
    ids = payload.get("ids")
    status = payload.get("status")
    if not isinstance(ids, list) or not isinstance(status, str):
        raise HTTPException(400, "Body must be {ids: int[], status: string}")
    if status not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of {VALID_STATUSES}")
    if len(ids) > 50_000:
        raise HTTPException(400, "Too many ids (max 50000)")
    # Chunk the IN list: a large batch in a single `.in_()` trips SQLite's
    # bound-variable limit (~32k) with an uncaught 500. Every other bulk
    # endpoint bounds this; this one had slipped through.
    updated = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        files = list(await session.scalars(select(MediaFile).where(MediaFile.id.in_(chunk))))
        for f in files:
            f.status = status
        updated += len(files)
    await session.commit()
    return {"updated": updated}
