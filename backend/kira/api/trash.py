"""Trash bin endpoints — browse, restore, and empty Kira's managed trash.

The folder-cleanup sweep moves artifacts here (when `rename.cleanup_trash` is
on) instead of deleting them. Trashed names are flattened
(`<parent>__<name>[.n]`), so per-item restore relies on the provenance
manifest `_move_to_trash` writes (one JSON line per item: name → original
path). Items that predate the manifest list with `original: null` — they can
be deleted from here but must be restored by hand.

Containment: every item is addressed by its bare NAME inside the trash root,
never a path. Names with separators / drive markers / `..` are rejected, and
the resolved target must stay inside the resolved trash root — same defense
as the /folders endpoints.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kira.database import get_session
from kira.models import Setting
from kira.renamer.operations import TRASH_MANIFEST

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trash", tags=["trash"])


async def _resolve_trash_root(session: AsyncSession) -> Path | None:
    """Primary trash root (override else `<library_root>/.kira-trash`)."""
    roots = await _resolve_trash_roots(session)
    return roots[0] if roots else None


async def _resolve_trash_roots(session: AsyncSession) -> list[Path]:
    """EVERY trash location the writers can install into, primary first: the
    `rename.trash_dir` override, `<library_root>/.kira-trash`, and one
    `.kira-trash` per multi-root entry in `paths.library_roots`. The writers
    (rename cleanup / cleanup API) trash into per-root dirs, so a UI that only
    looked at the primary root could neither list nor restore items swept on
    the other roots."""
    def _val(row):
        v = row.value if row else None
        if isinstance(v, dict) and "value" in v:
            v = v["value"]
        return v if isinstance(v, str) and v.strip() else None

    out: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        key = str(p).rstrip("\\/").lower()
        if key not in seen:
            seen.add(key)
            out.append(p)

    override = _val(await session.get(Setting, "rename.trash_dir"))
    if override:
        _add(Path(override))
    root = _val(await session.get(Setting, "paths.library_root"))
    if root:
        _add(Path(root) / ".kira-trash")
    multi = await session.get(Setting, "paths.library_roots")
    mv = multi.value if multi else None
    if isinstance(mv, dict) and "value" in mv:
        mv = mv["value"]
    if isinstance(mv, list):
        for r in mv:
            if isinstance(r, str) and r.strip():
                _add(Path(r) / ".kira-trash")
    return out


def _load_manifest(trash_root: Path) -> dict[str, dict]:
    """name → {original, at}. Later lines win (re-trash after restore)."""
    out: dict[str, dict] = {}
    try:
        with open(trash_root / TRASH_MANIFEST, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and isinstance(rec.get("name"), str):
                    out[rec["name"]] = rec
    except OSError:
        pass
    return out


def _drop_manifest_entry(trash_root: Path, name: str) -> None:
    """Rewrite the manifest without `name`. Best-effort."""
    path = trash_root / TRASH_MANIFEST
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
        kept = []
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("name") == name:
                continue
            kept.append(line)
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(kept)
        os.replace(tmp, path)
    except OSError:
        pass


def _contained_item(trash_root: Path, name: str) -> Path:
    """Resolve `name` inside the trash root or 400/404. Names only — any
    separator, drive marker, or traversal token is rejected outright."""
    if (not name or name in (".", "..") or "/" in name or "\\" in name
            or ":" in name or name == TRASH_MANIFEST):
        raise HTTPException(status_code=400, detail="Invalid trash item name.")
    target = (trash_root / name)
    try:
        root_r = trash_root.resolve()
        target_r = target.resolve()
        target_r.relative_to(root_r)
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid trash item name.")
    if not target.exists():
        raise HTTPException(status_code=404, detail="No such item in the trash.")
    return target


def _dir_size(path: str) -> int:
    """Recursive size via scandir — on Windows/SMB the directory enumeration
    itself carries each entry's attributes, so this costs ONE network call per
    directory instead of one stat round-trip per file (Path.rglob + stat took
    ~30s for a 140-entry trash over a NAS share; this takes milliseconds)."""
    total = 0
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_file(follow_symlinks=False):
                        total += e.stat(follow_symlinks=False).st_size
                    elif e.is_dir(follow_symlinks=False):
                        total += _dir_size(e.path)
                except OSError:
                    continue
    except OSError:
        pass
    return total


@router.get("")
async def list_trash(session: AsyncSession = Depends(get_session)) -> dict:
    roots = [r for r in await _resolve_trash_roots(session)]
    live_roots = [r for r in roots if r.exists()]
    if not live_roots:
        return {"root": str(roots[0]) if roots else None, "items": [], "total_bytes": 0}

    def _scan() -> dict:
        items = []
        total = 0
        for root in live_roots:
            manifest = _load_manifest(root)
            try:
                entries = list(os.scandir(str(root)))
            except OSError:
                entries = []
            for e in entries:
                if e.name == TRASH_MANIFEST or e.name.endswith(".tmp"):
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                    is_dir = e.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                size = _dir_size(e.path) if is_dir else st.st_size
                total += size
                rec = manifest.get(e.name) or {}
                items.append({
                    "name": e.name,
                    "is_dir": is_dir,
                    "size_bytes": size,
                    "trashed_at": rec.get("at"),
                    "mtime": st.st_mtime,
                    "original": rec.get("original"),
                    # Which trash dir this item lives in — informational for the
                    # UI (restore/delete locate it by name across all roots).
                    "trash_root": str(root),
                })
        # Newest first — that's what you're looking for after a sweep.
        items.sort(key=lambda i: i["mtime"], reverse=True)
        return {"root": str(live_roots[0]), "items": items, "total_bytes": total}

    return await asyncio.to_thread(_scan)


class TrashItemBody(BaseModel):
    name: str


async def _find_root_containing(session: AsyncSession, name: str) -> Path:
    """First trash root whose directory actually holds `name` — restore/delete
    must work for items in ANY per-root trash dir, not just the primary."""
    roots = await _resolve_trash_roots(session)
    if not roots:
        raise HTTPException(status_code=400, detail="No trash folder configured.")
    for r in roots:
        try:
            candidate = _contained_item(r, name)
        except HTTPException:
            continue
        if await asyncio.to_thread(candidate.exists):
            return r
    # Fall back to the primary root so the not-found error message points there.
    return roots[0]


@router.post("/restore")
async def restore_item(body: TrashItemBody, session: AsyncSession = Depends(get_session)) -> dict:
    root = await _find_root_containing(session, body.name)
    target = _contained_item(root, body.name)
    rec = _load_manifest(root).get(body.name)
    original = rec.get("original") if rec else None
    if not original:
        raise HTTPException(
            status_code=409,
            detail="This item predates the trash manifest — its original location "
                   "is unknown. Restore it by hand from the trash folder.",
        )
    dest = Path(original)

    def _restore() -> None:
        if dest.exists():
            raise HTTPException(
                status_code=409,
                detail=f"Something already exists at the original location: {dest}",
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(dest))
        _drop_manifest_entry(root, body.name)

    await asyncio.to_thread(_restore)
    logger.info("trash: restored %s -> %s", body.name, dest)
    return {"restored": body.name, "to": str(dest)}


@router.post("/delete")
async def delete_item(body: TrashItemBody, session: AsyncSession = Depends(get_session)) -> dict:
    root = await _find_root_containing(session, body.name)
    target = _contained_item(root, body.name)

    def _delete() -> None:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        _drop_manifest_entry(root, body.name)

    await asyncio.to_thread(_delete)
    logger.info("trash: permanently deleted %s", body.name)
    return {"deleted": body.name}


@router.post("/empty")
async def empty_trash(session: AsyncSession = Depends(get_session)) -> dict:
    # Empty EVERY trash root (override + per-library-root dirs) — matches what
    # list_trash shows, so "Empty trash" can't leave invisible leftovers.
    roots = [r for r in await _resolve_trash_roots(session) if r.exists()]
    if not roots:
        return {"deleted": 0}

    def _empty() -> int:
        # Parallel deletes — over an SMB share each unlink is a network
        # round-trip, so emptying hundreds of items serially took minutes.
        # 8 workers cuts that to seconds without hammering the NAS.
        from concurrent.futures import ThreadPoolExecutor

        def _rm(e: Path) -> int:
            try:
                if e.is_dir():
                    shutil.rmtree(e)
                else:
                    e.unlink()
                return 1
            except OSError as err:
                logger.warning("trash: empty failed for %s: %r", e.name, err)
                return 0

        entries: list[Path] = []
        for root in roots:
            entries.extend(root.iterdir())
        with ThreadPoolExecutor(max_workers=8) as ex:
            return sum(ex.map(_rm, entries))

    n = await asyncio.to_thread(_empty)
    logger.info("trash: emptied %d item(s) across %d root(s)", n, len(roots))
    return {"deleted": n}


def purge_old_trash(trash_root: Path, retention_days: int) -> int:
    """Delete trash items trashed more than `retention_days` ago. 0/negative =
    keep forever. Called from startup, mirroring the history prune. Returns
    the count removed. Sync — call via to_thread.

    Age is measured from when the item was TRASHED (the manifest's `at`
    timestamp), NOT the file's own mtime. `shutil.move` preserves mtime, so a
    2-year-old .nfo swept into trash today would otherwise be purged at the
    next boot — a zero-day recovery window for anything older than the
    retention setting (i.e. nearly every media file). Only when the manifest
    has no record for an item (predates the manifest, or a corrupt line) do we
    fall back to mtime."""
    if retention_days <= 0 or not trash_root.exists():
        return 0
    cutoff = time.time() - retention_days * 86400
    manifest = _load_manifest(trash_root)
    n = 0
    for e in list(trash_root.iterdir()):
        if e.name == TRASH_MANIFEST:
            continue
        try:
            trashed_at: float | None = None
            at_str = (manifest.get(e.name) or {}).get("at")
            if isinstance(at_str, str):
                try:
                    from datetime import datetime as _dt
                    trashed_at = _dt.fromisoformat(at_str).timestamp()
                except ValueError:
                    trashed_at = None
            if trashed_at is None:
                trashed_at = e.stat().st_mtime  # no manifest record → fall back
            if trashed_at < cutoff:
                if e.is_dir():
                    shutil.rmtree(e)
                else:
                    e.unlink()
                _drop_manifest_entry(trash_root, e.name)
                n += 1
        except OSError:
            continue
    return n
