"""System endpoints — folder browsing for the path picker and database reset."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from kira.database import get_session
from kira.models import Match, MediaFile, Notification, RenameHistory, Scan, Setting

router = APIRouter(tags=["system"])


class FolderEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    file_count: int | None = None


class FolderListing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    parent: str | None
    entries: list[FolderEntry]


@router.get("/folders", response_model=FolderListing)
async def list_folder(path: str = Query("")) -> FolderListing:
    """List sub-directories under `path`. If empty, lists Windows drive roots
    (C:\\, D:\\, …) or POSIX root ("/").
    """
    if not path:
        # Drives on Windows; "/" on POSIX.
        if os.name == "nt":
            drives: list[FolderEntry] = []
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                root = f"{letter}:\\"
                if os.path.exists(root):
                    drives.append(FolderEntry(name=root, path=root, is_dir=True))
            return FolderListing(path="", parent=None, entries=drives)
        return FolderListing(
            path="/", parent=None,
            entries=[FolderEntry(name="/", path="/", is_dir=True)],
        )

    p = Path(path)
    if not p.exists():
        raise HTTPException(404, f"Path does not exist: {path}")
    if not p.is_dir():
        raise HTTPException(400, f"Not a directory: {path}")

    # Disk I/O runs in a worker thread — on a NAS, iterdir() across 50+
    # children can take seconds, and keeping it on the event loop blocks
    # every concurrent request (scan progress polls, WebSocket pings, etc).
    try:
        entries = await asyncio.to_thread(_list_subdirs, p)
    except PermissionError as e:
        raise HTTPException(403, f"Permission denied: {path}") from e

    parent = str(p.parent) if p.parent != p else None
    return FolderListing(path=str(p), parent=parent, entries=entries)


def _list_subdirs(p: Path) -> list[FolderEntry]:
    """Synchronous helper run inside a worker thread by list_folder."""
    entries: list[FolderEntry] = []
    for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        # Skip hidden / system entries to keep the picker readable.
        if child.name.startswith("."):
            continue
        try:
            is_dir = child.is_dir()
        except OSError:
            continue
        if not is_dir:
            continue
        # Cheap file count — only inspect immediate children, no recursion.
        try:
            file_count = sum(1 for _ in child.iterdir())
        except (PermissionError, OSError):
            file_count = None
        entries.append(FolderEntry(
            name=child.name,
            path=str(child),
            is_dir=True,
            file_count=file_count,
        ))
    return entries


@router.post("/database/reset")
async def reset_database(
    confirm: str = Query(..., description="Must equal 'RESET' to proceed"),
    wipe_settings: bool = Query(False, description="Also wipe Setting table (API keys, paths, naming profile). Default false — settings are user config, not scan data."),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Truncate Kira's scan-data tables. Renames already executed on disk are NOT undone.

    By default this wipes scan data (files, matches, history, notifications,
    scans) but PRESERVES user settings (API keys, library_root path, naming
    profile, rename defaults). Without this distinction, a "reset" forces
    the user to redo onboarding (re-enter TMDB/TVDB keys, re-set the media
    folder) every time they want a clean scan — which is almost never what
    they want.

    Pass `wipe_settings=true` to also clear the Setting table (e.g. for
    truly starting from scratch, or in tests).
    """
    if confirm != "RESET":
        raise HTTPException(400, "Pass ?confirm=RESET to actually wipe the DB.")
    # Order matters — children first to avoid FK violations.
    data_models = (Notification, RenameHistory, Match, MediaFile, Scan)
    for model in data_models:
        await session.execute(delete(model))
    if wipe_settings:
        # Preserve the heal-version row so the heal pass doesn't re-run
        # its (now-irrelevant) one-shot migrations on the empty DB.
        # Everything else goes.
        await session.execute(
            delete(Setting).where(Setting.key != "system.heal_version")
        )
    await session.commit()
    return {"ok": 1, "wiped_settings": 1 if wipe_settings else 0}


# ── Notifications ───────────────────────────────────────────────────────


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    title: str
    body: str | None
    read: bool
    created_at: datetime


notif_router = APIRouter(prefix="/notifications", tags=["notifications"])


@notif_router.get("", response_model=list[NotificationOut])
async def list_notifications(
    unread_only: bool = False,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
) -> list[Notification]:
    from sqlalchemy import select
    stmt = select(Notification).order_by(Notification.created_at.desc()).limit(limit)
    if unread_only:
        stmt = stmt.where(Notification.read.is_(False))
    return list(await session.scalars(stmt))


@notif_router.post("/{notif_id}/read", response_model=NotificationOut)
async def mark_read(notif_id: int, session: AsyncSession = Depends(get_session)) -> Notification:
    notif = await session.get(Notification, notif_id)
    if notif is None:
        raise HTTPException(404, "Notification not found")
    notif.read = True
    await session.commit()
    return notif


@notif_router.post("/read-all", response_model=dict[str, int])
async def mark_all_read(session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    from sqlalchemy import select, update
    result = await session.execute(update(Notification).where(Notification.read.is_(False)).values(read=True))
    await session.commit()
    return {"updated": result.rowcount or 0}
