import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from kira.database import get_session
from kira.models import MediaFile, RenameHistory
from kira.schemas import FileStatusUpdate, MediaFileOut

VALID_STATUSES = {"pending", "matching", "matched", "approved", "rejected", "no_match", "discovered"}

router = APIRouter(prefix="/files", tags=["files"])


@router.get("", response_model=list[MediaFileOut])
async def list_files(
    media_type: str | None = None,
    status: str | None = None,
    limit: int = 500,
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
    # Sort matches per file by confidence desc — DB order isn't guaranteed.
    for f in files:
        f.matches.sort(key=lambda m: m.confidence, reverse=True)
    return files


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
    disk_status: str = "skipped" if keep_on_disk else "missing"

    # 1. Physical deletion FIRST — if it fails (permission denied, locked
    #    file), we abort and leave the DB row alone so the UI doesn't show
    #    a phantom-deleted entry that still exists on disk.
    if not keep_on_disk and disk_path:
        try:
            p = Path(disk_path)
            # Offload to a worker thread so large-network-share deletes
            # don't freeze the event loop.
            existed = await asyncio.to_thread(_safe_unlink, p)
            disk_status = "deleted" if existed else "missing"
        except PermissionError as e:
            raise HTTPException(403, f"Couldn't delete on disk: {e}") from e
        except OSError as e:
            raise HTTPException(500, f"Disk delete failed: {e}") from e

    # 2. Preserve RenameHistory by nulling the FK before the cascade nukes it.
    await session.execute(
        update(RenameHistory)
        .where(RenameHistory.media_file_id == file_id)
        .values(media_file_id=None)
    )

    # 3. Now safe to delete the row (Match rows cascade away).
    await session.delete(media_file)
    await session.commit()

    return {"deleted": file_id, "disk": disk_status, "path": disk_path}


def _safe_unlink(p: Path) -> bool:
    """Delete p if it exists; return True if a file was actually removed."""
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False


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
    """
    from pathlib import Path as _Path
    from kira.parser import parse_filename
    from kira.api.scans import (
        _compute_series_key,
        _maybe_enrich_mediainfo,
        _read_mediainfo_setting,
        _read_mediainfo_authoritative_setting,
    )

    stmt = select(MediaFile)
    if media_type is not None:
        stmt = stmt.where(MediaFile.media_type == media_type)
    files = list(await session.scalars(stmt))

    # Phase 16: apply the same MediaInfo enrichment the scan worker does, so a
    # reparse picks up tech tags (and authoritative overrides) without needing
    # a full rescan of the library.
    read_mi = await _read_mediainfo_setting(session)
    mi_authoritative = await _read_mediainfo_authoritative_setting(session)

    changed = 0
    for mf in files:
        if not mf.file_path:
            continue
        parent = str(_Path(mf.file_path).parent)
        fresh = parse_filename(_Path(mf.file_path).name, parent_path=parent)
        await _maybe_enrich_mediainfo(fresh, mf.file_path, read_mi, mi_authoritative)
        new_data = fresh.to_dict()
        if new_data != mf.parsed_data:
            mf.parsed_data = new_data
            mf.media_type = fresh.media_type
            mf.series_key = _compute_series_key(fresh)
            changed += 1

    await session.commit()
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
    files = list(await session.scalars(select(MediaFile).where(MediaFile.id.in_(ids))))
    for f in files:
        f.status = status
    await session.commit()
    return {"updated": len(files)}
