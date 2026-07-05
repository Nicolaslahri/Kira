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
from kira.schemas import UtcDateTime

router = APIRouter(tags=["system"])


# Optional confinement for the folder picker. The picker intentionally browses
# the filesystem (you can't pick a library root you can't see), so it's NOT
# locked to configured roots — that would break first-run setup. Instead, an
# admin can set KIRA_BROWSE_ROOT (e.g. the media volume in a Docker deploy:
# `KIRA_BROWSE_ROOT=/media`) to hard-confine browsing to that subtree. Unset =
# full browse, the desktop/dev default. resolve()-based containment also blocks
# `..` traversal out of the confinement root.
_BROWSE_ROOT = os.environ.get("KIRA_BROWSE_ROOT", "").strip()


def _confined_root() -> Path | None:
    if not _BROWSE_ROOT:
        return None
    try:
        return Path(_BROWSE_ROOT).resolve()
    except OSError:
        return None


def _within(path: Path, root: Path) -> bool:
    """True iff `path` resolves to `root` or a descendant of it."""
    try:
        rp = path.resolve()
    except OSError:
        return False
    return rp == root or root in rp.parents


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
    confine = _confined_root()

    if not path:
        # Confined: start at the permitted root (no drive/`/` enumeration).
        if confine is not None:
            entries = await asyncio.to_thread(_list_subdirs, confine)
            return FolderListing(path=str(confine), parent=None, entries=entries)
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
    # Confinement (+ traversal guard): a `..`-laden or out-of-root path is
    # refused before any disk access.
    if confine is not None and not _within(p, confine):
        raise HTTPException(403, "Path is outside the permitted browse root.")
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
    # Under confinement, never hand back a parent at/above the browse root —
    # the picker shouldn't offer an "up" link that escapes it.
    if confine is not None and parent is not None and not _within(Path(parent), confine):
        parent = None
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


@router.get("/activity")
async def get_activity() -> dict:
    """Background-activity snapshot for the frontend's activity indicator:
    the boot recovery summary plus any running heal / warm-up job. In-memory
    and best-effort — a restart clears it because the work restarts too."""
    from kira import activity
    return activity.snapshot()


@router.get("/ffmpeg")
async def get_ffmpeg_status() -> dict:
    """Is ffmpeg usable (system or Kira-managed), and can this platform
    one-click install it? Drives the Settings + Onboarding status rows."""
    from kira.ffmpeg_setup import ffmpeg_status
    return ffmpeg_status()


@router.post("/ffmpeg/install")
async def install_ffmpeg_endpoint() -> dict:
    """One-click managed ffmpeg: download a static build into Kira's own
    ./tools/ dir — no PATH edits, nothing system-wide. Fire-and-forget;
    progress + the final state narrate through /activity."""
    from kira.ffmpeg_setup import FFMPEG_INSTALL_JOB, ffmpeg_status, install_ffmpeg
    from kira.tasks import spawn_tracked
    status = ffmpeg_status()
    if status["available"]:
        return status
    if not status["installable"]:
        raise HTTPException(400, "No one-click ffmpeg build for this platform — install from ffmpeg.org.")
    if not status["installing"]:
        spawn_tracked(install_ffmpeg(), label=FFMPEG_INSTALL_JOB)
        status["installing"] = True
    return status


@router.get("/fpcalc")
async def get_fpcalc_status() -> dict:
    """Is fpcalc (Chromaprint) usable, and can this platform one-click install it?
    Drives the Settings → AcoustID status row (fingerprint matching needs it)."""
    from kira.fpcalc_setup import fpcalc_status
    return fpcalc_status()


@router.post("/fpcalc/install")
async def install_fpcalc_endpoint() -> dict:
    """One-click managed fpcalc: download the Chromaprint release into Kira's own
    ./tools/ dir — no PATH edits. Fire-and-forget; progress narrates via /activity."""
    from kira.fpcalc_setup import FPCALC_INSTALL_JOB, fpcalc_status, install_fpcalc
    from kira.tasks import spawn_tracked
    status = fpcalc_status()
    if status["available"]:
        return status
    if not status["installable"]:
        raise HTTPException(400, "No one-click fpcalc build for this platform — install chromaprint manually.")
    if not status["installing"]:
        spawn_tracked(install_fpcalc(), label=FPCALC_INSTALL_JOB)
        status["installing"] = True
    return status


@router.post("/matches/reset")
async def reset_matches(
    confirm: str = Query(..., description="Must equal 'RESET' to proceed"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Tier-2 reset: forget every identification. Deletes Match rows and
    flips files back to pending so the next scan / Re-identify re-matches
    from scratch. Files on disk, rename history, and settings all survive."""
    if confirm != "RESET":
        raise HTTPException(400, "Pass ?confirm=RESET to actually reset matches.")
    from sqlalchemy import update
    from kira.models import RenameHistory
    # Detach every rename-history back-reference BEFORE deleting matches. On a
    # legacy DB whose `rename_history.match_id` FK is still RESTRICT (created by
    # the old create_all path — SQLite can't ALTER a FK's ON DELETE in place),
    # deleting a Match a past rename points at raises "FOREIGN KEY constraint
    # failed" with foreign_keys=ON, 500ing the whole reset. Same discipline as
    # match_cleanup.detach_and_delete_matches, applied wholesale.
    await session.execute(
        update(RenameHistory).where(RenameHistory.match_id.isnot(None)).values(match_id=None)
    )
    res = await session.execute(delete(Match))
    # Reset EVERY file that carried match data back to pending — not just the
    # three "settled" statuses. A scan interrupted mid-match leaves files in
    # 'matching'/'parsed'/'discovered'; after deleting all matches those would
    # keep a non-pending status with zero match rows and never re-enter the
    # match queue (invisible orphans until a full re-scan). 'renamed' files are
    # physically done on disk, so they're left as-is.
    await session.execute(
        update(MediaFile)
        .where(MediaFile.status.notin_(("pending", "renamed")))
        .values(status="pending")
    )
    await session.commit()
    return {"matches_deleted": res.rowcount or 0}


@router.post("/history/reset")
async def reset_history(
    confirm: str = Query(..., description="Must equal 'RESET' to proceed"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Tier-1 reset: clear the rename log (and with it, undo). Nothing else
    is touched — files, matches, and settings all survive."""
    if confirm != "RESET":
        raise HTTPException(400, "Pass ?confirm=RESET to actually clear history.")
    res = await session.execute(delete(RenameHistory))
    await session.commit()
    return {"history_deleted": res.rowcount or 0}


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
        # Everything else goes — including the auth account, so a factory
        # reset returns the server to the first-run sign-up screen.
        await session.execute(
            delete(Setting).where(Setting.key != "system.heal_version")
        )
        from kira.api.auth import set_account_cache
        set_account_cache(None)
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
    created_at: UtcDateTime


notif_router = APIRouter(prefix="/notifications", tags=["notifications"])

# Keep at most this many notification rows. Nothing pruned these before, so a
# long-running instance (especially with a flapping integration health check)
# grew the table unbounded. Cap by COUNT (keep the newest) rather than age, so
# a quiet instance never loses recent history and a chatty one stays bounded.
_NOTIFICATION_KEEP = 500


async def prune_old_notifications(session: AsyncSession) -> int:
    """Delete all but the newest `_NOTIFICATION_KEEP` notifications. Called on
    the same recurring event as the history prune (post-scan). Returns removed
    count. Best-effort — never raises into the caller."""
    from sqlalchemy import select
    try:
        ids = list(await session.scalars(
            select(Notification.id).order_by(Notification.created_at.desc())
            .offset(_NOTIFICATION_KEEP)
        ))
        if not ids:
            return 0
        await session.execute(delete(Notification).where(Notification.id.in_(ids)))
        await session.commit()
        return len(ids)
    except Exception:
        return 0


@notif_router.get("", response_model=list[NotificationOut])
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=500),
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


class _MarkReadBody(BaseModel):
    ids: list[int]


@notif_router.post("/read", response_model=dict[str, int])
async def mark_read_bulk(body: _MarkReadBody, session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    """Mark a SET of notifications read in one request — the bell's group click
    (a collapsed run of N notifications) used to fire N parallel POSTs."""
    if not body.ids:
        return {"updated": 0}
    from sqlalchemy import update
    result = await session.execute(
        update(Notification).where(Notification.id.in_(body.ids)).values(read=True)
    )
    await session.commit()
    return {"updated": result.rowcount or 0}


@notif_router.post("/read-all", response_model=dict[str, int])
async def mark_all_read(session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    from sqlalchemy import select, update
    result = await session.execute(update(Notification).where(Notification.read.is_(False)).values(read=True))
    await session.commit()
    return {"updated": result.rowcount or 0}
