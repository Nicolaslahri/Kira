"""Standalone library-artifact cleanup endpoint.

The move-time folder cleanup only removes folders that become empty/artifact-only
after a Move. Media servers (Jellyfin/Plex/Kodi) keep sprinkling artifacts
(`<episode>-thumb.jpg`, `poster.jpg`, `.tbn`, `.actors/`, …) into folders that
still hold your videos — AFTER Kira has organized them — so they accumulate with
no way to clear them. This endpoint sweeps the configured library roots for those
artifacts (allow-list only; videos/subtitles/user files are never touched),
previewing with `dry_run` before deleting and respecting the recoverable-trash
setting.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kira.api.files import _managed_roots
from kira.database import get_session
from kira.renamer.operations import sweep_artifacts

router = APIRouter(prefix="/cleanup", tags=["cleanup"])


class ArtifactSweepRequest(BaseModel):
    # Default to a PREVIEW — a destructive library-wide delete must be opt-in per call.
    dry_run: bool = True


class ArtifactSweepResult(BaseModel):
    removed: int
    items: list[str]
    dry_run: bool
    trashed: bool
    roots: list[str]


async def _resolve_trash_root(session: AsyncSession, roots: list[str]) -> Path | None:
    """Recoverable-trash target, reusing the same settings the rename cleanup uses
    (`rename.cleanup_trash` + `rename.trash_dir`, default `<root>/.kira-trash`).
    None → hard delete."""
    from kira.api.rename import _resolve_bool_setting, _resolve_str_setting
    if not await _resolve_bool_setting(session, "rename.cleanup_trash", False):
        return None
    override = await _resolve_str_setting(session, "rename.trash_dir", "")
    if override:
        return Path(override)
    if roots:
        return Path(roots[0]) / ".kira-trash"
    return None


@router.post("/artifacts", response_model=ArtifactSweepResult)
async def sweep_library_artifacts(
    payload: ArtifactSweepRequest,
    session: AsyncSession = Depends(get_session),
) -> ArtifactSweepResult:
    """Sweep (or preview) media-server artifacts across the managed library roots."""
    roots = await _managed_roots(session)
    trash_root = await _resolve_trash_root(session, roots)
    # The walk is blocking disk I/O over the whole library — offload it so the
    # event loop stays responsive (same reasoning as the rename move offload).
    removed, items = await asyncio.to_thread(
        sweep_artifacts, roots, dry_run=payload.dry_run, trash_root=trash_root,
    )
    if not payload.dry_run and removed:
        from kira.models import Notification
        session.add(Notification(
            kind="success",
            title=f"Cleaned {removed} leftover artifact{'' if removed == 1 else 's'}",
            body=("Moved to trash" if trash_root else "Deleted") + " from your library folders.",
        ))
        await session.commit()
    return ArtifactSweepResult(
        removed=removed, items=items, dry_run=payload.dry_run,
        trashed=trash_root is not None, roots=roots,
    )
