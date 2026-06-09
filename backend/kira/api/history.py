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
from kira.models import Match, MediaFile, Notification, RenameHistory
from kira.renamer.operations import FileOp, undo_op
from kira.schemas import UtcDateTime

router = APIRouter(prefix="/history", tags=["history"])


async def prune_old_history(session: AsyncSession) -> int:
    """Delete rename-history rows older than the configured retention window
    (Settings → Advanced → History retention). Stored as a day count string;
    ``0`` / ``"forever"`` (or absent) means keep everything. Returns the number
    of rows removed. Best-effort — callers run it on startup and after scans so
    the log self-prunes without a separate scheduler."""
    from kira.models import Setting
    from kira.settings_store import unwrap

    row = await session.get(Setting, "history.retention_days")
    raw = unwrap(row.value) if row is not None else None
    if raw is None or (isinstance(raw, str) and raw.strip().lower() in ("", "forever")):
        return 0
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return 0
    if days <= 0:
        return 0
    cutoff = _utcnow_naive() - timedelta(days=days)
    result = await session.execute(
        delete(RenameHistory).where(RenameHistory.created_at < cutoff)
    )
    await session.commit()
    return result.rowcount or 0


class HistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    media_file_id: int | None
    old_path: str
    new_path: str
    operation: str
    media_type: str | None
    title: str | None
    # Episode name pulled from the linked Match (TV only). Surfaced so the
    # History page search can match on episode titles, not just the show
    # name + paths. Defaults to None so the undo endpoint — which serializes
    # a bare RenameHistory ORM row via from_attributes — doesn't choke on a
    # missing attribute.
    episode_title: str | None = None
    poster_url: str | None
    created_at: UtcDateTime
    undone_at: UtcDateTime | None


@router.get("", response_model=list[HistoryOut])
async def list_history(
    period: str = Query("all", description="today | week | all"),
    operation: str | None = None,
    limit: int = Query(500, ge=1, le=100_000),
    session: AsyncSession = Depends(get_session),
) -> list[HistoryOut]:
    """List rename history with fresh poster URLs.

    `RenameHistory.poster_url` is frozen at rename time — whatever the
    Match's poster_url was when the rename happened. But auto-heal /
    cross-ref enrichment runs AFTER renames too, so the linked Match
    row often has a richer/correct poster URL by the time the user
    browses History. We fall back to `Match.poster_url` when the
    history-row's own field is null so the cover grid stays complete
    instead of degrading to gradient-only placeholders for entries
    that landed before the poster fetch resolved.

    Order: prefer the frozen poster (it WAS the right one at rename
    time and might have been manually-picked), fall through to the
    Match's current one when frozen is null.
    """
    stmt = select(RenameHistory).order_by(RenameHistory.created_at.desc()).limit(limit)
    if period == "today":
        cutoff = _utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = stmt.where(RenameHistory.created_at >= cutoff)
    elif period == "week":
        cutoff = _utcnow_naive() - timedelta(days=7)
        stmt = stmt.where(RenameHistory.created_at >= cutoff)
    if operation:
        stmt = stmt.where(RenameHistory.operation == operation)
    rows = list(await session.scalars(stmt))

    # Batch-fetch the linked Match rows in one query so we don't N+1
    # for libraries with hundreds of history entries.
    match_ids = [r.match_id for r in rows if r.match_id is not None]
    matches_by_id: dict[int, Match] = {}
    if match_ids:
        matches = await session.scalars(
            select(Match).where(Match.id.in_(match_ids))
        )
        matches_by_id = {m.id: m for m in matches}

    out: list[HistoryOut] = []
    for r in rows:
        # Prefer the frozen poster (manual picks, scene-of-the-time
        # captures), fall back to the live Match poster (enrichment
        # may have populated it after the rename).
        effective_poster = r.poster_url
        match = matches_by_id.get(r.match_id) if r.match_id is not None else None
        if effective_poster is None and match is not None and match.poster_url:
            effective_poster = match.poster_url
        out.append(HistoryOut(
            id=r.id,
            media_file_id=r.media_file_id,
            old_path=r.old_path,
            new_path=r.new_path,
            operation=r.operation,
            media_type=r.media_type,
            title=r.title,
            episode_title=match.episode_title if match is not None else None,
            poster_url=effective_poster,
            created_at=r.created_at,
            undone_at=r.undone_at,
        ))
    return out


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

    Sidecar rows skip the MediaFile sync — only the parent video row
    represents the MediaFile's location on disk. The sidecar rows
    move alongside it but they aren't tracked in `media_files` as
    their own entities, so syncing MediaFile from them would
    incorrectly rewrite `file_path` to the sidecar's path.
    """
    if not entry.media_file_id:
        return
    if entry.parent_id is not None:
        # This is a sidecar — its MediaFile is the parent video, which
        # got synced when the parent was undone. Skip to avoid
        # clobbering the parent's freshly-restored file_path with a
        # sidecar path.
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


async def _undo_sidecar_children(
    session: AsyncSession, parent_entry: RenameHistory,
) -> tuple[int, int]:
    """Undo every non-undone RenameHistory row whose parent_id is the
    given parent's id. Used by both single-entry and bulk undo to keep
    sidecar files locked to the parent video's location.

    Returns `(succeeded, failed)` — best-effort; a missing-target
    sidecar (someone manually moved it) counts as failed but doesn't
    abort the rest. The parent's own undo is the caller's job; this
    function only touches children.
    """
    children = list(await session.scalars(
        select(RenameHistory).where(
            RenameHistory.parent_id == parent_entry.id,
            RenameHistory.undone_at.is_(None),
        )
    ))
    if not children:
        return 0, 0
    succeeded = 0
    failed = 0
    for child in children:
        try:
            await asyncio.to_thread(
                undo_op,
                FileOp(child.operation),
                Path(child.old_path),
                Path(child.new_path),
            )
            child.undone_at = _utcnow_naive()
            succeeded += 1
        except Exception as e:
            # A sidecar undo failure should NOT block the parent's undo
            # from being recorded. Log + count + continue.
            print(f"history: sidecar undo failed for entry {child.id}: {e!r}")
            failed += 1
    return succeeded, failed


async def _remove_orphaned_assets(video_new_path: str, roots: list[str]) -> int:
    """Delete the artwork + NFO sidecars Kira wrote beside a renamed video, so
    UNDOING the rename doesn't leave them orphaned.

    The rename writes `<stem>-<kind>.<ext>` per artwork kind (rename.py) and
    `<stem>.nfo`, all named after the rename TARGET's stem. Undo reverted the
    video but left these, so repeated rename→undo→rename piled up a fresh artwork
    set + NFO per attempt (each under that attempt's target name).

    We match on the video's EXACT stem prefix — `<stem>-*.{jpg,jpeg,png,webp}`
    plus `<stem>.nfo`. The stem is Kira's own generated filename, so these are
    unambiguously Kira's (no kind-name list to drift out of sync with), while the
    generic Kodi assets (`folder.jpg` / `backdrop.jpg`, which carry NO stem
    prefix) are deliberately left untouched.

    Best-effort + safe: removes only files that exist, carry the exact stem
    prefix + an image/`.nfo` extension, and sit under a managed library root.
    Never raises. Returns the count removed."""
    from pathlib import Path as _P
    from kira.api.webhooks import path_under_roots

    p = _P(video_new_path)
    parent, stem = p.parent, p.stem
    prefix = f"{stem}-"
    img_exts = {".jpg", ".jpeg", ".png", ".webp"}

    def _collect() -> list[_P]:
        out: list[_P] = []
        nfo = parent / f"{stem}.nfo"
        if nfo.is_file():
            out.append(nfo)
        try:
            for e in parent.iterdir():
                if (e.name.startswith(prefix)
                        and e.suffix.lower() in img_exts and e.is_file()):
                    out.append(e)
        except OSError:
            pass
        return out

    try:
        targets = await asyncio.to_thread(_collect)
    except Exception:
        return 0
    removed = 0
    for t in targets:
        try:
            if roots and not path_under_roots(str(t), roots):
                continue  # never delete outside a configured library root
            await asyncio.to_thread(t.unlink)
            removed += 1
        except Exception as e:
            print(f"_remove_orphaned_assets: {t} (non-fatal): {e!r}")
    return removed


async def _remove_recorded_assets(paths: list[str], roots: list[str]) -> int:
    """Delete an EXPLICITLY recorded set of asset paths (RenameHistory.created_assets
    — the NFO/artwork the rename actually wrote). Authoritative: no deriving names
    from a stem, so it can't drift from the writer. Best-effort + safe — removes
    only files that exist and sit under a managed library root. Never raises."""
    from pathlib import Path as _P
    from kira.api.webhooks import path_under_roots

    removed = 0
    for ps in paths or []:
        try:
            if roots and not path_under_roots(ps, roots):
                continue  # never delete outside a configured library root
            p = _P(ps)
            if await asyncio.to_thread(p.is_file):
                await asyncio.to_thread(p.unlink)
                removed += 1
        except Exception as e:
            print(f"_remove_recorded_assets: {ps} (non-fatal): {e!r}")
    return removed


async def _cleanup_entry_assets(entry: RenameHistory, roots: list[str]) -> int:
    """Remove the artwork/NFO a rename created, for undo. Prefers the AUTHORITATIVE
    list recorded on the row (`created_assets`); falls back to the stem-derived
    sweep for legacy rows written before that column existed."""
    recorded = getattr(entry, "created_assets", None)
    if recorded:
        return await _remove_recorded_assets(recorded, roots)
    return await _remove_orphaned_assets(entry.new_path, roots)


async def sweep_superseded_assets(
    session: AsyncSession, media_file_id: int, current_target: str, roots: list[str],
) -> int:
    """Forward-orphan cleanup, called from rename. When a file is re-renamed to a
    DIFFERENT target without an undo in between, the artwork/NFO the PRIOR rename
    wrote (named after the OLD target) is stranded. Each prior non-undone primary
    history row recorded exactly what it created, so delete that set authoritatively.

    Scoped tightly: same media_file, NOT undone, primary (parent_id IS NULL), and
    new_path != the current target (never touches the rename we're doing now).
    Returns the count removed."""
    from kira.renamer.operations import FileOp  # noqa: F401 — keep import graph stable
    rows = list(await session.scalars(
        select(RenameHistory).where(
            RenameHistory.media_file_id == media_file_id,
            RenameHistory.undone_at.is_(None),
            RenameHistory.parent_id.is_(None),
            RenameHistory.new_path != current_target,
        )
    ))
    removed = 0
    for r in rows:
        recorded = getattr(r, "created_assets", None)
        if recorded:
            removed += await _remove_recorded_assets(recorded, roots)
    return removed


async def _cleanup_undo_vacated_folders(session: AsyncSession, entry: RenameHistory) -> int:
    """After undo moves a video back, the destination Show/Season folders Kira
    created for the rename are left behind — empty, or holding only show-level
    artifacts (`tvshow.nfo`, `poster.jpg`) and the now-emptied season folder. Walk
    UP from the vacated `new_path` and remove folders that are empty or ENTIRELY
    media-server artifacts (allow-list only — a folder with any real content stops
    the walk), bounded by the managed library root, honoring the trash setting.

    Reuses the move-time `_cleanup_empty_source_parents` walker, just pointed at the
    undo-vacated destination instead of a move source. Best-effort; never raises."""
    try:
        from pathlib import Path as _P
        from kira.api.cleanup import _resolve_trash_root
        from kira.api.files import _managed_roots
        from kira.api.webhooks import _norm
        from kira.renamer.operations import _cleanup_empty_source_parents

        roots = await _managed_roots(session)
        vacated = _P(entry.new_path).parent
        # Find the managed root that contains the vacated path — the stop boundary
        # so the walk can never rmdir the library root itself or anything above it.
        np = _norm(str(vacated))
        stop_at: _P | None = None
        for r in roots:
            if not r:
                continue
            rn = _norm(r)
            if np == rn or np.startswith(rn + "/"):
                stop_at = _P(r)
                break
        if stop_at is None:
            return 0  # vacated path isn't under a managed root → don't touch it
        trash_root = await _resolve_trash_root(session, roots)
        return await asyncio.to_thread(
            _cleanup_empty_source_parents, vacated, stop_at, 3,
            sweep_artifacts=True, trash_root=trash_root,
        )
    except Exception as e:
        print(f"_cleanup_undo_vacated_folders: {e!r} (non-fatal)")
        return 0


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
    # Tier 1.2: cascade to sidecar children. Subtitle / sub files that
    # rode along with the video at rename time also ride back at undo
    # time — otherwise the user ends up with subs at the new location
    # and the video back at the old, breaking Plex/Jellyfin pairing.
    sub_ok, sub_failed = await _undo_sidecar_children(session, entry)
    # Clean up the artwork + NFO this rename wrote, so undo doesn't orphan them
    # (the rename→undo→rename pile-up). Primary video rows only — sidecar
    # children (subtitles) carry no artwork.
    assets_removed = 0
    folders_removed = 0
    if entry.parent_id is None:
        from kira.api.files import _managed_roots
        assets_removed = await _cleanup_entry_assets(entry, await _managed_roots(session))
        # Remove the now-empty Show/Season folders Kira created for this rename.
        folders_removed = await _cleanup_undo_vacated_folders(session, entry)
    body = f"Restored to {entry.old_path}"
    if sub_ok:
        body += f" (+ {sub_ok} sidecar{'s' if sub_ok != 1 else ''})"
    if sub_failed:
        body += f"; {sub_failed} sidecar{'s' if sub_failed != 1 else ''} failed to restore"
    if assets_removed:
        body += f"; removed {assets_removed} artwork/NFO file{'s' if assets_removed != 1 else ''}"
    if folders_removed:
        body += f"; cleaned {folders_removed} leftover folder/artifact{'s' if folders_removed != 1 else ''}"
    session.add(Notification(
        kind="info",
        title=f"Undone: {entry.title or Path(entry.old_path).name}",
        body=body,
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
    sidecar_succeeded = 0
    sidecar_failed = 0
    assets_removed = 0
    from kira.api.files import _managed_roots
    bulk_roots = await _managed_roots(session)
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
            # Tier 1.2: sidecar cascade. Sidecar children attached to
            # this video row also undo together. Sidecar failures are
            # logged but never bubble up as a parent failure — the
            # parent video itself has already been successfully undone.
            ok_subs, failed_subs = await _undo_sidecar_children(session, entry)
            sidecar_succeeded += ok_subs
            sidecar_failed += failed_subs
            # Clean the artwork/NFO this rename wrote (primary video rows only) +
            # the now-empty Show/Season folders Kira created for it.
            if entry.parent_id is None:
                assets_removed += await _cleanup_entry_assets(entry, bulk_roots)
                await _cleanup_undo_vacated_folders(session, entry)
        except Exception:
            failed += 1
    await session.commit()
    out = {"succeeded": succeeded, "failed": failed}
    if sidecar_succeeded or sidecar_failed:
        out["sidecars_undone"] = sidecar_succeeded
        out["sidecars_failed"] = sidecar_failed
    if assets_removed:
        out["assets_removed"] = assets_removed
    return out


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
