"""History endpoints — list, undo, CSV export."""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _utcnow_naive() -> datetime:
    """SQLAlchemy's DateTime column (with `server_default=func.now()`) stores
    timezone-NAIVE datetimes in SQLite. Cutoffs must be naive too — comparing
    a naive `row.created_at` to a `datetime.now(timezone.utc)` (aware) raises
    `TypeError: can't compare offset-naive and offset-aware datetimes` and
    500s the endpoint.

    Returns the current UTC time as a naive datetime, matching what
    SQLite stored when the row was inserted.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from kira.database import get_session
from kira.models import MediaFile, Notification, RenameHistory
from kira.renamer.operations import FileOp, undo_op

router = APIRouter(prefix="/history", tags=["history"])


class HistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    media_file_id: int | None
    old_path: str
    new_path: str
    operation: str
    media_type: str | None
    title: str | None
    poster_url: str | None
    created_at: datetime
    undone_at: datetime | None


@router.get("", response_model=list[HistoryOut])
async def list_history(
    period: str = Query("all", description="today | week | all"),
    operation: str | None = None,
    limit: int = 500,
    session: AsyncSession = Depends(get_session),
) -> list[RenameHistory]:
    stmt = select(RenameHistory).order_by(RenameHistory.created_at.desc()).limit(limit)
    if period == "today":
        cutoff = _utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = stmt.where(RenameHistory.created_at >= cutoff)
    elif period == "week":
        cutoff = _utcnow_naive() - timedelta(days=7)
        stmt = stmt.where(RenameHistory.created_at >= cutoff)
    if operation:
        stmt = stmt.where(RenameHistory.operation == operation)
    return list(await session.scalars(stmt))


@router.get("/counts")
async def history_counts(session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    """Counts for the filter pills — today, week, all."""
    today_cutoff = _utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)
    week_cutoff = _utcnow_naive() - timedelta(days=7)
    all_rows = list(await session.scalars(select(RenameHistory)))
    return {
        "today": sum(1 for r in all_rows if r.created_at and r.created_at >= today_cutoff),
        "week":  sum(1 for r in all_rows if r.created_at and r.created_at >= week_cutoff),
        "all":   len(all_rows),
    }


async def _sync_media_file_after_undo(
    session: AsyncSession, entry: RenameHistory,
) -> None:
    """Revert the linked MediaFile's file_path + status after a physical undo.

    Without this, the row stays pointing at the renamed-to path and the
    UI thinks the file still lives there — clicking play / re-rename /
    delete all hit "File Not Found" because the physical file is now
    back at `old_path`. We treat MediaFile as the central source of
    truth; the physical filesystem and this column must stay in lockstep.

    `entry.media_file_id` can be NULL (the cleanup endpoint in files.py
    explicitly allows wiping a MediaFile while keeping its history rows
    behind via FK ON DELETE SET NULL). When the FK is null we skip the
    sync silently — there's no MediaFile to update.

    Status "matched" puts the file back into the Review queue so the
    user can decide what to do with it next. "renamed" wouldn't make
    sense — by definition the rename has just been undone.
    """
    if not entry.media_file_id:
        return
    mf = await session.get(MediaFile, entry.media_file_id)
    if mf is None:
        return
    mf.file_path = entry.old_path
    # Only flip status when the row currently reflects the post-rename
    # state — don't clobber a more-specific status (e.g. the user
    # manually marked it `discarded`).
    if mf.status == "renamed":
        mf.status = "matched"


@router.post("/{entry_id}/undo", response_model=HistoryOut)
async def undo_entry(entry_id: int, session: AsyncSession = Depends(get_session)) -> RenameHistory:
    entry = await session.get(RenameHistory, entry_id)
    if entry is None:
        raise HTTPException(404, "History entry not found")
    if entry.undone_at is not None:
        raise HTTPException(400, "Already undone")
    op = FileOp(entry.operation)
    try:
        # ── Autopsy 13: offload blocking disk I/O to a worker thread.
        # `undo_op` calls `shutil.move` / `os.unlink` under the hood —
        # synchronous, C-level blocking primitives. Same hazard as the
        # forward-rename in `rename.py` (Autopsy 9): a cross-drive undo
        # of 30 GB of files would freeze the asyncio event loop for
        # MINUTES, dropping websockets, failing /health, and triggering
        # Docker container restarts mid-copy. `asyncio.to_thread`
        # punts the blocking work to a worker thread; the event loop
        # stays responsive for poll / health / other-tab requests.
        await asyncio.to_thread(
            undo_op, op, Path(entry.old_path), Path(entry.new_path),
        )
    except Exception as e:
        raise HTTPException(500, f"Undo failed: {e}") from e
    # Sync the database to match what just happened on disk. Must run
    # AFTER the physical undo succeeded — if `undo_op` raised, the file
    # is still at `new_path` and we'd corrupt the row by pointing it at
    # a path that doesn't exist.
    await _sync_media_file_after_undo(session, entry)
    entry.undone_at = _utcnow_naive()
    session.add(Notification(
        kind="info",
        title=f"Undone: {entry.title or Path(entry.old_path).name}",
        body=f"Restored to {entry.old_path}",
    ))
    await session.commit()
    return entry


@router.post("/undo-bulk", response_model=dict[str, int])
async def undo_bulk(
    payload: dict[str, list[int]],
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    ids = payload.get("ids", [])
    succeeded = 0
    failed = 0
    for entry_id in ids:
        entry = await session.get(RenameHistory, entry_id)
        if entry is None or entry.undone_at is not None:
            failed += 1
            continue
        try:
            # Autopsy 13: same thread-offload as `undo_entry` above —
            # without this, a bulk undo of 20 large cross-drive moves
            # serializes 20 multi-minute `shutil.move` calls on the
            # event loop, freezing the entire server until the whole
            # batch finishes. `asyncio.to_thread` keeps each individual
            # undo blocking-but-isolated, so /health and other endpoints
            # stay alive across the loop.
            await asyncio.to_thread(
                undo_op,
                FileOp(entry.operation),
                Path(entry.old_path),
                Path(entry.new_path),
            )
            # Sync MediaFile row only on a SUCCESSFUL physical undo —
            # see _sync_media_file_after_undo's docstring for the same
            # post-undo-only safety reasoning. Per-entry sync inside the
            # try block ensures a transient FS failure on one entry
            # doesn't corrupt the matching DB row.
            await _sync_media_file_after_undo(session, entry)
            entry.undone_at = _utcnow_naive()
            succeeded += 1
        except Exception:
            failed += 1
    await session.commit()
    return {"succeeded": succeeded, "failed": failed}


@router.get("/export.csv")
async def export_csv(session: AsyncSession = Depends(get_session)) -> StreamingResponse:
    """Download the full history as CSV."""
    rows = list(await session.scalars(
        select(RenameHistory).order_by(RenameHistory.created_at.desc())
    ))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "created_at", "operation", "media_type", "title",
        "old_path", "new_path", "undone_at",
    ])
    for r in rows:
        writer.writerow([
            r.id,
            r.created_at.isoformat() if r.created_at else "",
            r.operation,
            r.media_type or "",
            r.title or "",
            r.old_path,
            r.new_path,
            r.undone_at.isoformat() if r.undone_at else "",
        ])
    buf.seek(0)
    fname = f"kira-history-{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.delete("/cleanup", response_model=dict[str, int])
async def cleanup_old(
    days: int = Query(30, ge=1, description="Delete history older than N days"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Honor the retention setting — delete history entries older than `days`."""
    cutoff = _utcnow_naive() - timedelta(days=days)
    result = await session.execute(delete(RenameHistory).where(RenameHistory.created_at < cutoff))
    await session.commit()
    return {"deleted": result.rowcount or 0}
