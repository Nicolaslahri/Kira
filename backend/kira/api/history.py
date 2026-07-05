"""History endpoints — list, undo, CSV export."""

from __future__ import annotations

import logging

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

# Subtitle sidecar extensions whose deletion is recoverable via the subtitle
# reuse-cache. Lowercased, leading dot. A subset of the renamer's _SIDECAR_EXTS
# — only the text-subtitle formats the subcache (and OpenSubtitles fetch) deal
# in; the binary blobs (.sub/.idx/.sup) still take the trash/delete path.
_CACHEABLE_SUB_EXTS = frozenset({".srt", ".ass", ".ssa", ".vtt"})

logger = logging.getLogger(__name__)

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
    # Provider identity of the linked Match. AniDB matches carry NO
    # poster_url (the title dump has no images) — the frontend resolves
    # their covers lazily via /search/anidb/picture/{aid}, exactly like
    # the library grid, and needs the aid to do it. Defaults None for the
    # same bare-ORM-row reason as episode_title.
    provider: str | None = None
    provider_id: str | None = None
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
            provider=match.provider if match is not None else None,
            provider_id=match.provider_id if match is not None else None,
            created_at=r.created_at,
            undone_at=r.undone_at,
        ))
    return out


@router.get("/counts")
async def history_counts(session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    """Counts for the filter pills — today, week, all."""
    today_cutoff = _utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)
    week_cutoff = _utcnow_naive() - timedelta(days=7)
    # COUNT in SQLite — don't materialize the entire rename_history table (every
    # column, incl. the created_assets JSON) into RAM just to count 3 buckets on
    # a hot poll. `>= cutoff` excludes NULL created_at exactly like the old
    # `r.created_at and …` did; `all` counts every row.
    from sqlalchemy import func
    base = select(func.count()).select_from(RenameHistory)
    today = await session.scalar(base.where(RenameHistory.created_at >= today_cutoff))
    week = await session.scalar(base.where(RenameHistory.created_at >= week_cutoff))
    total = await session.scalar(base)
    return {"today": today or 0, "week": week or 0, "all": total or 0}


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
            logger.warning(f"history: sidecar undo failed for entry {child.id}: {e!r}")
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
            logger.warning(f"_remove_orphaned_assets: {t} (non-fatal): {e!r}")
    return removed


def _verify_row_undoable_sync(
    new_path: str,
    old_path: str,
    provider: str | None,
    provider_id: str | None,
    operation: str = "move",
) -> tuple[bool, str]:
    """The pure, BLOCKING half of the undo-viability check (stat + xattr reads).
    Callers wrap it in `asyncio.to_thread` so the event loop stays responsive.

    Returns `(undoable, reason)`. `reason` is one of the exact strings the UI
    surfaces on the disabled Undo button — keep them in sync with the frontend
    (api.verifyUndoable / HistoryPage). Mirrors `undo_op`'s physical guards so
    a single, replaced, or relocated file is caught BEFORE any move happens.

    Checks, in order:
      • new_path missing                  → "Target missing"
      • new_path's Kira id-stamp present but mismatched → "File changed on disk"
      • old_path occupied by a DIFFERENT file        → "Original location occupied"
      • otherwise                          → undoable
    """
    from pathlib import Path as _P
    from kira import xattr_store
    from kira.renamer.operations import _same_inode

    new_p = _P(new_path)
    if not new_p.exists():
        # A SYMLINK/HARDLINK undo just unlinks new_path, which works even on a
        # BROKEN symlink (its target moved) — `exists()` is False for a broken
        # link but `is_symlink()` is True, and undo_op handles it. Only MOVE/COPY
        # genuinely need the target present to move/delete it.
        if operation in ("symlink", "hardlink") and new_p.is_symlink():
            return True, ""
        return False, "Target missing"

    # Identity stamp: the rename stamped the destination with the resolved
    # provider id (`xattr_store.write_ids(target, {provider: provider_id})`).
    # If the file the user is about to undo no longer carries the SAME id, it's
    # been edited/replaced on disk (or is a different file at the same path) —
    # refuse so undo can't move the wrong bytes back.
    #
    # `write_ids` only persists the _PERSISTABLE provider keys (tmdb/tvdb/
    # anidb/imdb), only when `rename.stamp_ids` is on, and only when the FS /
    # index can hold metadata. So:
    #   • a stamp that EXISTS but doesn't carry our provider id → changed.
    #   • a stamp that EXISTS and matches → genuine, proceed.
    #   • NO stamp at all → only suspicious when a stamp WOULD have been written
    #     (stamping enabled AND persistable provider AND this FS can stamp); a
    #     non-persistable provider, stamping-disabled, or an xattr-incapable FS
    #     can't be distinguished from "edited", so we DON'T false-flag.
    stamped = xattr_store.read_ids(new_path)
    # Only a PRESENT-but-mismatched stamp proves the file was REPLACED. An ABSENT
    # stamp is indistinguishable from a legitimately-unstamped file (renamed
    # before stamping shipped, renamed while `rename.stamp_ids` was off, or a
    # stamp stripped by a copy/restore), so refusing on it would block REAL undos
    # on exactly the filesystems that CAN stamp (ext4 / Docker) — as bad as the
    # bug this guards against. The physical undo_op guards (target-exists +
    # occupied-source) already prevent the data-loss case for unstamped files.
    if stamped and not (provider and provider_id and stamped.get(provider) == str(provider_id)):
        return False, "File changed on disk"

    # Original location now holding a DIFFERENT file → undo would clobber it
    # (same data-loss guard as undo_op's MOVE branch). Same-inode means it's
    # literally the same file (already restored / hardlink) → fine.
    #
    # MOVE ONLY: undo_op physically moves new_path back onto old_path only for
    # a MOVE. For COPY/SYMLINK/HARDLINK, undo just DELETES the destination and
    # never touches old_path — so old_path legitimately still holds the source
    # (that's the whole point of a copy/link). Applying this guard to those ops
    # rejected EVERY copy-mode undo, and every hardlink/symlink undo on a
    # zero-inode CIFS/SMB mount (where `_same_inode` can't prove sameness) —
    # i.e. undo of the DEFAULT op on the primary Docker deployment.
    if operation == "move":
        old_p = _P(old_path)
        try:
            if old_p.exists() and not _same_inode(old_p, new_p):
                return False, "Original location occupied"
        except OSError:
            # stat hiccup (NAS blip) — be conservative and allow; undo_op re-checks.
            pass
    return True, ""


async def _verify_row_undoable(
    session: AsyncSession, entry: RenameHistory,
) -> tuple[bool, str]:
    """Async wrapper around `_verify_row_undoable_sync` that resolves the row's
    provider identity (from its linked Match) + the `rename.stamp_ids` setting,
    then off-loads the disk I/O to a worker thread. Already-undone / FK-less rows
    short-circuit to a clear reason without touching the disk. READ-ONLY — never
    mutates the DB or filesystem."""
    if entry.undone_at is not None:
        return False, "Already undone"
    provider: str | None = None
    provider_id: str | None = None
    if entry.match_id is not None:
        match = await session.get(Match, entry.match_id)
        if match is not None:
            provider, provider_id = match.provider, match.provider_id
    return await asyncio.to_thread(
        _verify_row_undoable_sync,
        entry.new_path, entry.old_path, provider, provider_id, entry.operation,
    )


async def _drop_subtitle_asset(
    session: AsyncSession, entry: RenameHistory, sub_path: str,
) -> None:
    """Mark the `subtitle_assets` ledger row that points at `sub_path` inactive,
    so the Subtitles history doesn't keep showing a sidecar that undo just
    removed / moved to the reuse-cache. Matched on the row's MediaFile (the
    sidecar's parent video) + the on-disk path, falling back to the language
    token parsed from the filename. Best-effort — never fatal to the undo."""
    from kira.models import SubtitleAsset
    from kira.api.webhooks import _norm

    # The sidecar's MediaFile is the PARENT video. A sidecar history row carries
    # its own media_file_id (== the parent's); a primary row carries its own.
    media_file_id = entry.media_file_id
    if not media_file_id:
        return
    try:
        rows = list(await session.scalars(
            select(SubtitleAsset).where(
                SubtitleAsset.media_file_id == media_file_id,
                SubtitleAsset.active.is_(True),
            )
        ))
        if not rows:
            return
        want_path = _norm(sub_path)
        want_lang = _lang_from_sub_name(sub_path)
        matched = [r for r in rows if r.path and _norm(r.path) == want_path]
        if not matched:
            # Path didn't line up (the ledger stored a different spelling) —
            # fall back to the language token so we still retire the right row.
            matched = [r for r in rows if (r.language or "").lower() == want_lang]
        for r in matched:
            r.active = False
            r.path = None
    except Exception as e:
        logger.warning(f"_drop_subtitle_asset: {sub_path} (non-fatal): {e!r}")


def _lang_from_sub_name(path: str) -> str:
    """Pull the language token from a `<stem>.<lang>.<ext>` subtitle sidecar
    name. Returns "und" when the file is a bare `<stem>.<ext>` (no language
    segment) so callers never crash on a missing token. Pure."""
    from pathlib import Path as _P
    p = _P(path)
    # `Movie (2010).eng.srt` → stem "Movie (2010).eng", suffixes [".eng", ".srt"].
    # The language is the LAST dotted segment before the extension. A plain
    # `Movie (2010).srt` has only the extension → no language → "und".
    # Skip trailing forced/SDH/HI/CC markers AND numeric tokens (years) so
    # `<stem>.en.forced.srt` reads the LANGUAGE ("en"), not "forced" — otherwise
    # the sub caches under the wrong key and a later fetch (wanting "en") misses
    # it and re-downloads. `Movie.2010.srt` → "und" (no language segment).
    _MARKERS = {"forced", "sdh", "hi", "cc"}
    parts = [s.lstrip(".").strip().lower() for s in p.suffixes[:-1]]  # drop the extension
    for tok in reversed(parts):
        if not tok or tok.isdigit() or tok in _MARKERS:
            continue
        return tok
    return "und"


async def _try_cache_subtitle(sub_path: str, video_path: str) -> bool:
    """Move a subtitle sidecar into the reuse-cache instead of deleting it, so a
    later re-rename can reuse it without re-downloading. Returns True only when
    the file actually landed in the cache (and is therefore gone from its old
    spot). Any import / runtime failure returns False so the caller falls back to
    the trash/delete path. Never raises."""
    if not video_path:
        return False
    try:
        from kira.subtitles import subcache
    except Exception:
        return False
    lang = _lang_from_sub_name(sub_path)
    try:
        cached = await subcache.cache_subtitle(sub_path, video_path=video_path, language=lang)
    except Exception as e:
        logger.warning(f"_try_cache_subtitle: {sub_path} (non-fatal): {e!r}")
        return False
    return bool(cached)


async def _remove_recorded_assets(
    paths: list[str],
    roots: list[str],
    *,
    trash_root: "Path | None" = None,
    session: AsyncSession | None = None,
    entry: RenameHistory | None = None,
    video_path: str | None = None,
) -> int:
    """Remove an EXPLICITLY recorded set of asset paths (RenameHistory.created_assets
    — the NFO/artwork/subtitle sidecars the rename actually wrote). Authoritative:
    no deriving names from a stem, so it can't drift from the writer. Best-effort
    + safe — only touches files that exist and sit under a managed library root.
    Never raises.

    Deletions are made RECOVERABLE (no more hard `unlink` of everything):
      • A subtitle sidecar (`.srt`/`.ass`/`.ssa`/`.sub`/`.vtt`) is MOVED into the
        subtitle reuse-cache (`subtitles.subcache.cache_subtitle`, keyed by the
        video's content hash + parsed language) so a later re-rename reuses it
        instead of burning OpenSubtitles quota. On cache failure it falls through
        to the trash/delete path below.
      • Everything else (and the subtitle fallback): MOVED to Kira's managed
        trash via the same mechanism the folder sweep uses (`_move_to_trash`)
        when `rename.cleanup_trash` is on (signalled by a non-None `trash_root`);
        otherwise hard-unlinked exactly as before.

    `session` + `entry` (when provided) let a removed/cached `.srt` also retire
    its `subtitle_assets` ledger row so the Subtitles view doesn't show a gone
    file. `video_path` is the row's new_path — the subtitle cache key is derived
    from it (when absent, falls back to the entry's new_path).

    Counts an asset as "removed" whether it was cached, trashed, or unlinked."""
    from pathlib import Path as _P
    from kira.api.webhooks import path_under_roots
    from kira.renamer.operations import _move_to_trash

    vid = video_path or (entry.new_path if entry is not None else "")

    removed = 0
    for ps in paths or []:
        try:
            if roots and not path_under_roots(ps, roots):
                continue  # never delete outside a configured library root
            p = _P(ps)
            if not await asyncio.to_thread(p.is_file):
                continue

            is_sub = p.suffix.lower() in _CACHEABLE_SUB_EXTS

            # ── Subtitle sidecar → reuse-cache (recoverable, quota-saving) ──
            if is_sub and await _try_cache_subtitle(ps, vid):
                removed += 1
                if session is not None and entry is not None:
                    await _drop_subtitle_asset(session, entry, ps)
                continue
            # (a subtitle that couldn't be cached — no video_path / cache
            #  failure — falls through to the trash/delete path below.)

            # ── Everything else (and subtitle fallback): trash when enabled,
            #    else hard-unlink ──
            done = False
            if trash_root is not None:
                if await asyncio.to_thread(_move_to_trash, p, trash_root):
                    removed += 1
                    done = True
                # trash move failed (permission / cross-device) — fall through
                # to a plain unlink so the asset is still cleaned up.
            if not done:
                await asyncio.to_thread(p.unlink)
                removed += 1
            # A trashed/deleted subtitle still desyncs the ledger — retire it.
            if is_sub and session is not None and entry is not None:
                await _drop_subtitle_asset(session, entry, ps)
        except Exception as e:
            logger.warning(f"_remove_recorded_assets: {ps} (non-fatal): {e!r}")
    return removed


async def _cleanup_entry_assets(
    entry: RenameHistory,
    roots: list[str],
    *,
    trash_root: "Path | None" = None,
    session: AsyncSession | None = None,
) -> int:
    """Remove the artwork/NFO/subtitle sidecars a rename created, for undo. Prefers
    the AUTHORITATIVE list recorded on the row (`created_assets`); falls back to the
    stem-derived sweep for legacy rows written before that column existed.

    `trash_root` (when set — i.e. `rename.cleanup_trash` is on) routes deletions
    through Kira's recoverable trash; `session` lets a removed `.srt` retire its
    `subtitle_assets` ledger row. Both default to the legacy hard-delete behavior
    so older 2-arg callers (and tests) are unaffected. The legacy stem-derived
    fallback (`_remove_orphaned_assets`) keeps its plain unlink — it only ever
    matched image + bare `.nfo` files, never subtitles, and is the cold path."""
    recorded = getattr(entry, "created_assets", None)
    if recorded:
        return await _remove_recorded_assets(
            recorded, roots,
            trash_root=trash_root, session=session, entry=entry,
            # By cleanup time the video is back at old_path (undo moved it) or
            # gone (cleanup-orphans) — hash THAT for the sub-cache key; new_path
            # is empty on disk and would force an unreusable name-only key.
            video_path=entry.old_path,
        )
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
        from kira.api.files import _managed_roots_aliased
        from kira.api.webhooks import _norm
        from kira.renamer.operations import _cleanup_empty_source_parents

        # Alias-aware: the vacated path is stored RESOLVED (UNC on a mapped
        # drive) while a root may be the drive-letter form — without the alias
        # the root-match below fails, `stop_at` stays None, and we bail (the
        # leftover Season/Show folder is never swept).
        roots = await _managed_roots_aliased(session)
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
        logger.warning(f"_cleanup_undo_vacated_folders: {e!r} (non-fatal)")
        return 0


async def _relink_sonarr_after_undo(session: AsyncSession, entries: list[RenameHistory]) -> None:
    """Reverse of the rename hook: after undoing renames, re-point Sonarr at the
    RESTORED series folder so its files don't orphan. Resolves the series inline
    (fast DB reads) then fires the Sonarr calls as a BACKGROUND task, so a slow /
    unreachable Sonarr never delays the undo response. Best-effort, never raises.

    Only re-points when the renamed folder is now FULLY vacated — a partial undo
    that leaves some episodes in the new folder is left alone (re-pointing then
    would orphan the ones still there)."""
    try:
        from kira.api.integrations import _load_sonarr_config
        from kira.providers.anime_mappings import AnimeMappings
        from kira.renamer.nfo import series_root_for
        try:
            cfg = await _load_sonarr_config(session)
        except Exception:
            return  # Sonarr not configured — nothing to do
        # tvdb id → (Sonarr's CURRENT folder, the RESTORED folder). Roots are
        # reversed vs the forward hook: undo moved the file new_path → old_path.
        tvdb_roots: dict[int, tuple[str, str]] = {}
        for entry in entries:
            if entry is None or entry.parent_id is not None or entry.match_id is None:
                continue
            match = await session.get(Match, entry.match_id)
            if match is None or match.match_type != "tv_episode" or not match.provider_id:
                continue
            cur_root = str(series_root_for(Path(entry.new_path)))       # where Sonarr points now
            restored_root = str(series_root_for(Path(entry.old_path)))  # back to here
            if cur_root == restored_root:
                continue  # folder name didn't change → only the file moved
            if Path(cur_root).exists():
                # Renamed folder still holds files (a PARTIAL undo) — leave Sonarr
                # pointing there; only re-point once it's fully vacated.
                continue
            try:
                if match.provider == "tvdb":
                    tvdb_roots[int(match.provider_id)] = (cur_root, restored_root)
                elif match.provider == "anidb":
                    t = await AnimeMappings.tvdb_id(int(match.provider_id))
                    if t:
                        tvdb_roots[int(t)] = (cur_root, restored_root)
            except (TypeError, ValueError):
                continue
        if not tvdb_roots:
            return
        from kira.integrations import sonarr as sonarr_mod
        from kira.tasks import spawn_tracked

        async def _bg() -> None:
            for tid, (old_root, new_root) in tvdb_roots.items():
                try:
                    ok, _changed, detail = await sonarr_mod.relink_series(
                        cfg, tid, old_root=old_root, new_root=new_root)
                    # "not in Sonarr" is a benign skip (a series you didn't get via
                    # Sonarr) → log quietly; only real trouble warns.
                    _lvl = (logger.debug if detail == "not in Sonarr"
                            else logger.info if ok else logger.warning)
                    _lvl(f"undo: sonarr relink for tvdb {tid} — {detail}")
                except Exception as e:  # noqa: BLE001 — best-effort
                    logger.warning(f"undo: sonarr relink for tvdb {tid} failed: {e!r}")

        spawn_tracked(_bg(), "undo-sonarr-relink")
    except Exception as e:  # noqa: BLE001 — best-effort
        logger.warning(f"undo: sonarr relink prep failed (non-fatal): {e!r}")


async def _relink_radarr_after_undo(session: AsyncSession, entries: list[RenameHistory]) -> None:
    """Movie sibling of `_relink_sonarr_after_undo`: after undoing movie renames,
    re-point Radarr at the RESTORED movie folder so its files don't orphan. Same
    discipline — resolve inline (fast DB reads), fire the Radarr calls as a
    BACKGROUND task, and only re-point a folder that's now FULLY vacated (a
    partial undo is left alone). Radarr is TMDB-keyed. Best-effort, never raises."""
    try:
        from kira.api.integrations import _load_radarr_config
        try:
            cfg = await _load_radarr_config(session)
        except Exception:
            return  # Radarr not configured — nothing to do
        # tmdb id → (Radarr's CURRENT folder, the RESTORED folder). Roots reversed
        # vs the forward hook. A movie's folder is simply its file's parent dir.
        tmdb_roots: dict[int, tuple[str, str]] = {}
        for entry in entries:
            if entry is None or entry.parent_id is not None or entry.match_id is None:
                continue
            match = await session.get(Match, entry.match_id)
            if match is None or match.match_type != "movie" or not match.provider_id:
                continue
            if match.provider != "tmdb":
                continue  # Radarr only knows movies by TMDB id
            cur_root = str(Path(entry.new_path).parent)       # where Radarr points now
            restored_root = str(Path(entry.old_path).parent)  # back to here
            if cur_root == restored_root:
                continue  # folder name didn't change → only the file moved
            if Path(cur_root).exists():
                continue  # PARTIAL undo — folder still holds files; leave Radarr alone
            try:
                tmdb_roots[int(match.provider_id)] = (cur_root, restored_root)
            except (TypeError, ValueError):
                continue
        if not tmdb_roots:
            return
        from kira.integrations import radarr as radarr_mod
        from kira.tasks import spawn_tracked

        async def _bg() -> None:
            for tid, (old_root, new_root) in tmdb_roots.items():
                try:
                    ok, _changed, detail = await radarr_mod.relink_movie(
                        cfg, tid, old_root=old_root, new_root=new_root)
                    _lvl = (logger.debug if detail == "not in Radarr"
                            else logger.info if ok else logger.warning)
                    _lvl(f"undo: radarr relink for tmdb {tid} — {detail}")
                except Exception as e:  # noqa: BLE001 — best-effort
                    logger.warning(f"undo: radarr relink for tmdb {tid} failed: {e!r}")

        spawn_tracked(_bg(), "undo-radarr-relink")
    except Exception as e:  # noqa: BLE001 — best-effort
        logger.warning(f"undo: radarr relink prep failed (non-fatal): {e!r}")


async def _refresh_media_servers_after_undo(session: AsyncSession) -> None:
    """Nudge Plex/Jellyfin to re-scan after an undo, the same way the forward
    rename does — otherwise the media server keeps pointing at the NEW (now
    deleted) paths until its next scheduled scan. Backgrounded + best-effort;
    a refresh failure must never affect the undo itself."""
    try:
        from kira.integrations.media_server import refresh_all
        from kira.tasks import spawn_tracked

        async def _bg() -> None:
            try:
                from kira.database import SessionLocal
                async with SessionLocal() as s:
                    await refresh_all(s)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"undo: media-server refresh failed (non-fatal): {e!r}")

        spawn_tracked(_bg(), "undo-media-refresh")
    except Exception as e:  # noqa: BLE001 — best-effort
        logger.warning(f"undo: media-server refresh prep failed (non-fatal): {e!r}")


@router.post("/{entry_id}/undo", response_model=HistoryOut)
async def undo_entry(entry_id: int, session: AsyncSession = Depends(get_session)) -> RenameHistory:
    entry = await session.get(RenameHistory, entry_id)
    if entry is None:
        raise HTTPException(404, "History entry not found")
    if entry.undone_at is not None:
        raise HTTPException(400, "Already undone")
    # ── Identity gate (read-only) ──────────────────────────────────────
    # Before touching the disk, confirm the renamed file is still the SAME
    # file we renamed: target present, Kira id-stamp intact, original slot
    # free. A file the user edited/replaced/relocated must NOT be silently
    # moved back over the (different) bytes now at the old path. This is the
    # same logic /verify-undoable reports; the physical undo_op guards below
    # remain as defense-in-depth.
    undoable, reason = await _verify_row_undoable(session, entry)
    if not undoable:
        raise HTTPException(409, reason or "Cannot undo")
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
    # Re-key the portable-ID index (audit §19 m): it's PATH-keyed, so after
    # moving the file back its identity was stranded under the renamed path —
    # a re-scan on SMB (no xattr) lost the instant re-identification.
    try:
        from kira import xattr_store
        _ids = xattr_store.read_ids(entry.new_path)
        if _ids:
            xattr_store.write_ids(entry.old_path, _ids)
    except Exception:
        pass

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
        from kira.api.cleanup import _resolve_trash_root
        from kira.api.files import _managed_roots_aliased
        # Alias-aware roots: created_assets are persisted RESOLVED (UNC on a
        # mapped drive), so a drive-letter-only root set made path_under_roots
        # reject every recorded asset → undo orphaned the NFO/artwork/subs.
        cleanup_roots = await _managed_roots_aliased(session)
        # Recoverable deletes: when `rename.cleanup_trash` is on, the NFO/artwork
        # go to Kira's trash and any `.srt` goes to the subtitle reuse-cache;
        # otherwise we hard-delete as before. `session` lets a removed sub retire
        # its subtitle_assets ledger row so the Subtitles view stays in sync.
        trash_root = await _resolve_trash_root(session, cleanup_roots)
        assets_removed = await _cleanup_entry_assets(
            entry, cleanup_roots, trash_root=trash_root, session=session,
        )
        # Remove the now-empty Show/Season folders Kira created for this rename.
        folders_removed = await _cleanup_undo_vacated_folders(session, entry)
    # Re-point Sonarr/Radarr at the restored folder (reverse of the rename hook)
    # so an undone folder-rename doesn't orphan the files. Backgrounded + best-effort.
    await _relink_sonarr_after_undo(session, [entry])
    await _relink_radarr_after_undo(session, [entry])
    await _refresh_media_servers_after_undo(session)
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


@router.post("/undo-bulk", response_model=dict[str, object])
async def undo_bulk(
    payload: dict[str, list[int]],
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    raw_ids = payload.get("ids", [])
    if not isinstance(raw_ids, list):
        raise HTTPException(400, "'ids' must be a list of history entry ids.")
    # Dedup + cap the batch: each id triggers a stat + a potential cross-drive
    # shutil.move (offloaded per-id), so an unbounded list — a client POSTing
    # tens of thousands of ids — would grind one request for minutes. 2000 is far
    # above any real bulk-undo selection; a truncated batch simply re-runs.
    seen: set[int] = set()
    ids: list[int] = []
    for _i in raw_ids:
        if isinstance(_i, int) and _i not in seen:
            seen.add(_i)
            ids.append(_i)
    if len(ids) > 2000:
        logger.warning("undo_bulk: capping %d ids to 2000", len(ids))
        ids = ids[:2000]
    succeeded = 0
    failed = 0
    sidecar_succeeded = 0
    sidecar_failed = 0
    assets_removed = 0
    undone_parents: list[RenameHistory] = []
    from kira.api.cleanup import _resolve_trash_root
    from kira.api.files import _managed_roots_aliased
    # Alias-aware (drive-letter ↔ UNC) — same as single undo (undo_entry). Without
    # this, bulk undo skips created_assets stored under the RESOLVED spelling (the
    # `Z:\` ↔ `\\nas\share` case), leaving the NFO/poster/subs orphaned exactly the
    # way single undo did before the alias fix.
    bulk_roots = await _managed_roots_aliased(session)
    # Recoverable deletes for every entry's asset teardown — resolved once.
    bulk_trash_root = await _resolve_trash_root(session, bulk_roots)
    succeeded_ids: list[int] = []
    failed_ids: list[int] = []
    for entry_id in ids:
        entry = await session.get(RenameHistory, entry_id)
        if entry is None or entry.undone_at is not None:
            failed += 1
            failed_ids.append(entry_id)
            continue
        # Identity gate (read-only), per entry: a row whose file was edited /
        # replaced / whose original slot is now occupied is counted as failed
        # and SKIPPED — never moved back over different bytes. The batch keeps
        # going for the rest (best-effort, like the rest of bulk undo).
        undoable, _reason = await _verify_row_undoable(session, entry)
        if not undoable:
            failed += 1
            failed_ids.append(entry_id)
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
            # Re-key the portable-ID index (audit §19 m): it's PATH-keyed, so
            # after moving the file back its identity was stranded under the
            # renamed path — a re-scan on SMB (no xattr) lost the instant
            # re-identification. Copy the ids to the restored path.
            try:
                from kira import xattr_store
                _ids = xattr_store.read_ids(entry.new_path)
                if _ids:
                    xattr_store.write_ids(entry.old_path, _ids)
            except Exception:
                pass

            entry.undone_at = _utcnow_naive()
            succeeded += 1
            succeeded_ids.append(entry_id)
            # Tier 1.2: sidecar cascade. Sidecar children attached to
            # this video row also undo together. Sidecar failures are
            # logged but never bubble up as a parent failure — the
            # parent video itself has already been successfully undone.
            ok_subs, failed_subs = await _undo_sidecar_children(session, entry)
            sidecar_succeeded += ok_subs
            sidecar_failed += failed_subs
            # Clean the artwork/NFO this rename wrote (primary video rows only) +
            # the now-empty Show/Season folders Kira created for it. Trash-aware
            # + session-threaded so `.srt` removals cache + retire their ledger
            # row, exactly like single undo.
            if entry.parent_id is None:
                assets_removed += await _cleanup_entry_assets(
                    entry, bulk_roots, trash_root=bulk_trash_root, session=session,
                )
                await _cleanup_undo_vacated_folders(session, entry)
                undone_parents.append(entry)
            # Per-item durability: commit each successful undo IMMEDIATELY.
            # The old single commit-at-the-end meant a crash mid-batch left
            # files physically moved back on disk while the DB still said
            # "renamed" — and the identity verify gate then permanently
            # blocked re-undoing those rows. expire_on_commit=False, so the
            # ORM objects (undone_parents) stay usable after each commit.
            await session.commit()
        except Exception:
            failed += 1
            failed_ids.append(entry_id)
            # Drop any uncommitted state from the failed entry so it can't
            # leak into the next iteration's per-item commit.
            try:
                await session.rollback()
            except Exception:
                pass
    # Re-point Sonarr/Radarr at the restored folders for fully-undone titles
    # (reverse of the rename hook). Backgrounded + best-effort.
    await _relink_sonarr_after_undo(session, undone_parents)
    await _relink_radarr_after_undo(session, undone_parents)
    if succeeded:
        await _refresh_media_servers_after_undo(session)
    await session.commit()
    # Per-id outcomes: the frontend used to flag EVERY attempted id as
    # "Restored" because only counts came back — failed rows flashed the green
    # celebration then snapped back.
    out = {"succeeded": succeeded, "failed": failed,
           "succeeded_ids": succeeded_ids, "failed_ids": failed_ids}
    if sidecar_succeeded or sidecar_failed:
        out["sidecars_undone"] = sidecar_succeeded
        out["sidecars_failed"] = sidecar_failed
    if assets_removed:
        out["assets_removed"] = assets_removed
    return out


@router.post("/cleanup-orphans", response_model=dict[str, int])
async def cleanup_orphans(session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    """Sweep leftover assets that an OLD undo orphaned. For every UNDONE primary
    row (`undone_at IS NOT NULL`, `parent_id IS NULL`) that recorded assets
    (`created_assets`), delete any of those recorded files still present on disk.

    Authoritative + safe: reuses the exact `_cleanup_entry_assets` /
    `_remove_recorded_assets` path the undo flow uses — so it's containment-guarded
    (`path_under_roots`), alias-aware (drive-letter ↔ UNC), routes a `.srt` to the
    reuse-cache + retires its `subtitle_assets` row, and honors `rename.cleanup_trash`
    (NFO/artwork to trash) — never a hand-rolled unlink. Returns the total count
    removed. Undo already cleans these inline; this is the backstop for files an
    older Kira (before that inline cleanup) left behind."""
    from kira.api.cleanup import _resolve_trash_root
    from kira.api.files import _managed_roots_aliased

    rows = list(await session.scalars(
        select(RenameHistory).where(
            RenameHistory.undone_at.is_not(None),
            RenameHistory.parent_id.is_(None),
        )
    ))
    roots = await _managed_roots_aliased(session)
    trash_root = await _resolve_trash_root(session, roots)
    removed = 0
    for r in rows:
        if not getattr(r, "created_assets", None):
            continue
        removed += await _cleanup_entry_assets(
            r, roots, trash_root=trash_root, session=session,
        )
    await session.commit()
    return {"removed": removed}


class VerifyUndoableIn(BaseModel):
    # Cap the batch so a pathological client can't ask us to stat tens of
    # thousands of paths in one request (the History page only ever sends the
    # visible-and-not-yet-undone rows). 500 comfortably covers the default
    # 500-row history page.
    ids: list[int]


@router.post("/verify-undoable", response_model=dict[str, dict])
async def verify_undoable(
    payload: VerifyUndoableIn,
    session: AsyncSession = Depends(get_session),
) -> dict[str, dict]:
    """READ-ONLY undo-viability probe. For each history id, report whether the
    rename can still be safely undone — WITHOUT touching the disk beyond a stat +
    xattr read (off-loaded to a worker thread). The History page calls this for
    the visible not-yet-undone rows and disables the Undo button (with the
    `reason`) when a row comes back not-undoable.

    Response shape (keyed by the id as a STRING, mirroring /files/verify-exist):
        {"<id>": {"undoable": <bool>, "reason": "<str>"}}

    `reason` is one of the exact strings the UI renders:
      • ""                            — undoable
      • "Target missing"              — the renamed file is gone
      • "File changed on disk"        — the file was edited/replaced (id-stamp
                                        absent-where-expected or mismatched)
      • "Original location occupied"  — a DIFFERENT file now sits at old_path
      • "Already undone"              — already undone, or no such row
    """
    ids = list(dict.fromkeys(payload.ids or []))[:500]   # dedup + cap at 500
    out: dict[str, dict] = {}
    for entry_id in ids:
        entry = await session.get(RenameHistory, entry_id)
        if entry is None:
            out[str(entry_id)] = {"undoable": False, "reason": "Already undone"}
            continue
        undoable, reason = await _verify_row_undoable(session, entry)
        out[str(entry_id)] = {"undoable": undoable, "reason": reason}
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
