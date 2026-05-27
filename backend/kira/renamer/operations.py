"""File operations — move / copy / symlink / hardlink, plus their inverse for undo.

All operations write to the target's parent dir, creating it if needed.
Failures raise — callers (the /rename endpoint) wrap them into per-file results.
"""

from __future__ import annotations

import os
import shutil
from enum import Enum
from pathlib import Path


class FileOp(str, Enum):
    MOVE = "move"
    COPY = "copy"
    SYMLINK = "symlink"
    HARDLINK = "hardlink"


def execute_op(
    op: FileOp,
    src: Path,
    dst: Path,
    *,
    overwrite: bool = False,
    cleanup_empty_source: bool = False,
    cleanup_stop_at: Path | None = None,
    cleanup_max_levels: int = 2,
) -> None:
    """Run the requested operation. Idempotent for already-correct hardlinks.

    ── R2-C5: Orphan-directory cleanup ─────────────────────────────────
    Cross-device renames can create destination directories that never
    receive a file: a copy half-fails (disk full / permission denied
    mid-write) → rollback removes the partial file, but `mkdir(parents=
    True)` already created the empty parent tree (e.g.
    `X:\\archive\\TV\\Show\\Season 01\\`). Over many failed batches the
    target volume fills with skeleton folder hierarchies.

    Fix: track which directories `mkdir` actually created (vs ones that
    pre-existed). If the file operation throws, roll back those created
    dirs deepest-first with `rmdir`. `rmdir` only removes empty
    directories so we can't accidentally nuke siblings.
    """
    if not src.exists():
        raise FileNotFoundError(f"Source does not exist: {src}")

    created_dirs = _mkdir_tracked(dst.parent)

    try:
        if dst.exists():
            if not overwrite:
                # If hardlink and they point at the same inode, treat as success.
                if op == FileOp.HARDLINK and _same_inode(src, dst):
                    return
                # MOVE + same-inode: the destination is already a hardlink
                # to the source. "Move" semantically means "destination
                # ends up at dst, source is gone" — which is satisfied by
                # just unlinking the source. The bytes stay on disk
                # (referenced by dst's hardlink). This is the common case
                # where a user did Hardlink earlier, switched the default
                # op to Move, and reapplied — they used to get a "already
                # exists" error here and couldn't progress without
                # manually deleting the hardlinks first.
                #
                # CATASTROPHIC BUG FIX: only unlink if src and dst are
                # DIFFERENT paths. _same_inode trivially returns True
                # when src == dst (literally the same file). Without
                # this guard, a self-rename (file already at target path,
                # template produces same output) DELETED THE ONLY
                # FILESYSTEM ENTRY for those bytes — actual data loss.
                # Now: when src.resolve() == dst.resolve(), this is a
                # true no-op (file already where it needs to be).
                if op == FileOp.MOVE and _same_inode(src, dst):
                    try:
                        src_resolved = src.resolve()
                        dst_resolved = dst.resolve()
                    except OSError:
                        # Path resolution failed — be conservative, treat as no-op.
                        return
                    if src_resolved == dst_resolved:
                        # Literal same path: nothing to do. File is already
                        # exactly where it should be.
                        return
                    # Two distinct paths sharing one inode — genuine hardlink
                    # case. Safe to unlink the source; bytes remain at dst.
                    src.unlink()
                    if cleanup_empty_source:
                        _cleanup_empty_source_parents(src.parent, cleanup_stop_at, cleanup_max_levels)
                    return
                # SYMLINK + dst is already a symlink to src: idempotent success.
                if op == FileOp.SYMLINK:
                    try:
                        if dst.is_symlink() and Path(os.readlink(str(dst))).resolve() == src.resolve():
                            return
                    except OSError:
                        pass
                raise FileExistsError(f"Destination already exists: {dst}")
            dst.unlink()

        if op == FileOp.MOVE:
            src_parent_before = src.parent
            _atomic_move(src, dst)
            # User-requested: walk up the source's parent chain and rmdir
            # each level that's now empty after the move. Safety: rmdir
            # refuses non-empty directories so we can't clobber siblings.
            # We stop at cleanup_stop_at (typically the library root) so
            # we never rmdir the user's media root itself.
            if cleanup_empty_source:
                _cleanup_empty_source_parents(src_parent_before, cleanup_stop_at, cleanup_max_levels)
        elif op == FileOp.COPY:
            shutil.copy2(str(src), str(dst))
        elif op == FileOp.SYMLINK:
            os.symlink(str(src), str(dst))
        elif op == FileOp.HARDLINK:
            os.link(str(src), str(dst))
        else:
            raise ValueError(f"Unknown FileOp: {op}")
    except Exception:
        # Operation failed AFTER we created destination dirs — roll them
        # back so the target volume doesn't accumulate orphan skeletons.
        # Best-effort: rmdir refuses non-empty dirs so this can't clobber
        # siblings that happened to live in the same path.
        _rmdir_tracked(created_dirs)
        raise


def _mkdir_tracked(dst_parent: Path) -> list[Path]:
    """Create `dst_parent` (recursive) and return the list of directories
    that didn't exist before this call. Callers pass the list to
    `_rmdir_tracked` to undo on operation failure.

    Returns an empty list when dst_parent already existed entirely —
    nothing to roll back in that case.
    """
    created: list[Path] = []
    # Walk up from dst_parent, recording any non-existing ancestors.
    # Stop at root or at the first existing ancestor.
    p = dst_parent
    while True:
        if p.exists():
            break
        created.append(p)
        if p.parent == p:  # filesystem root, can't go higher
            break
        p = p.parent
    # Now actually create them all (`exist_ok=True` makes this idempotent
    # under a race with another worker that beat us to it).
    dst_parent.mkdir(parents=True, exist_ok=True)
    return created


def _cleanup_empty_source_parents(start: Path, stop_at: Path | None, max_levels: int = 2) -> None:
    """User-requested: after a Move, walk UP the source's parent chain
    and rmdir each ancestor that's now empty. Saves the user from manually
    cleaning leftover `Show/Season 01/` shells after every file has been
    moved out.

    Safety:
      - rmdir refuses non-empty directories — siblings are never clobbered.
      - Stops at `stop_at` (typically the library root) so we never rmdir
        the user's media root itself, and never walk into system paths.
      - Stops at filesystem boundaries / drive root automatically because
        rmdir of `/` or `C:\\` returns OSError.
      - Best-effort: every OSError silently breaks the loop.

    `start` is the FIRST directory to attempt to remove (the file's
    immediate parent before the move). We walk parents upward.
    """
    if not start:
        return
    stop_abs = stop_at.resolve() if stop_at else None
    current = start
    # Reasonable upper bound on walk depth so a misconfiguration doesn't
    # walk all the way to the drive root. 6 levels is "anime/Show/Season"
    # + headroom. The stop_at check is the real guard; this is belt-and-
    # braces against a misconfigured stop_at.
    # Fix #7: per-call cap (default 2) replaces the previous hardcoded 6.
    # 6 levels was wildly excessive — TV/anime layout is at most
    # `<library>/<Show>/<Season X>/<file>` = 2 levels of show+season
    # folders to clean up. Music is `<Artist>/<Album>/<track>` = 2.
    # Movies are flat or `<Movie>/<file>` = 1. Callers pass max_levels
    # per media type; the default (2) covers the common case.
    for _ in range(max_levels):
        try:
            current_abs = current.resolve()
        except OSError:
            return
        # Never rmdir at or above the configured stop boundary.
        if stop_abs is not None:
            try:
                # If current is the same as or NOT strictly inside stop_at,
                # bail. .relative_to() raises if current isn't under stop_at.
                rel = current_abs.relative_to(stop_abs)
                # rel == Path('.') means current IS stop_at — also stop.
                if str(rel) in ('.', ''):
                    return
            except ValueError:
                # current is outside stop_at — definitely stop.
                return
        try:
            current.rmdir()
        except OSError:
            # Non-empty, missing, or permission denied. Stop here; parents
            # above are almost certainly non-empty too.
            return
        parent = current.parent
        if parent == current:
            return  # reached filesystem root
        current = parent


def _rmdir_tracked(dirs: list[Path]) -> None:
    """Roll back created directories deepest-first. `rmdir` only removes
    truly empty directories — if a sibling file got dropped in between
    create and rollback, we leave that path intact and stop walking up.

    Silent on permission denied / not-found — this is best-effort
    cleanup, not a guarantee.
    """
    # `dirs` is ordered shallowest-first from _mkdir_tracked (deeper paths
    # appended later). Walk in REVERSE so we delete leaves first; a
    # parent can only become removable once its child is gone.
    for d in reversed(dirs):
        try:
            d.rmdir()
        except OSError:
            # Non-empty (something else got put here), missing, or
            # permission denied. Stop walking — parents above this are
            # almost certainly non-empty too.
            break


def _atomic_move(src: Path, dst: Path) -> None:
    """Move semantics with explicit cross-device handling.

    ── H3: Cross-device move atomicity ──────────────────────────────
    `shutil.move` is `os.rename` (atomic) on same-FS and silently
    falls back to `copy2 + unlink` across filesystems (NOT atomic). On
    cross-FS the copy can fail half-way (disk full, network drop),
    leaving the source intact AND a partial destination — the rename
    appears to succeed in some code paths and fail in others depending
    on which step crashed.

    We do same-FS as before (`os.rename` is atomic and cheap). For
    cross-FS we do explicit copy → fsync → size verify → unlink, with
    rollback (delete the partial dst) if any step fails. Worst case:
    we either fully succeed OR leave the source untouched and the
    partial dst removed. No silent half-states.
    """
    try:
        os.rename(str(src), str(dst))
        return
    except OSError as e:
        # EXDEV (Unix) / WinError 17 ERROR_NOT_SAME_DEVICE — cross-device.
        # Other errors propagate (permission denied, target exists, etc.).
        is_cross_device = getattr(e, "errno", None) == 18 or getattr(e, "winerror", None) == 17
        if not is_cross_device:
            raise

    # Cross-device path. Copy with copy2 (preserves mtime + metadata),
    # verify size matches, then unlink source. On failure at any step
    # we remove the partial dst so the operation looks unstarted.
    src_size = src.stat().st_size
    try:
        shutil.copy2(str(src), str(dst))
        # Some filesystems (network) need an fsync to commit before we
        # trust the size check. Best-effort; ignore if the FS rejects it.
        try:
            with open(dst, "rb") as f:
                os.fsync(f.fileno())
        except OSError:
            pass
        dst_size = dst.stat().st_size
        if dst_size != src_size:
            raise OSError(
                f"Cross-device copy size mismatch: src={src_size} dst={dst_size}"
            )
        # All good — remove the source to complete the "move".
        src.unlink()
    except Exception:
        # Roll back partial destination so the next attempt isn't blocked
        # by a half-baked file and the source remains the canonical copy.
        try:
            if dst.exists():
                dst.unlink()
        except OSError:
            pass
        raise


def undo_op(op: FileOp, src: Path, dst: Path) -> None:
    """Reverse a previous `execute_op` call.

    - MOVE   → move dst back to src
    - COPY   → delete dst
    - SYMLINK/HARDLINK → unlink dst (source untouched)
    """
    if op == FileOp.MOVE:
        if not dst.exists():
            raise FileNotFoundError(f"Cannot undo move — {dst} no longer exists")
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dst), str(src))
    elif op in (FileOp.COPY, FileOp.SYMLINK, FileOp.HARDLINK):
        if dst.exists() or dst.is_symlink():
            dst.unlink()
    else:
        raise ValueError(f"Unknown FileOp: {op}")


def _same_inode(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_ino == b.stat().st_ino and a.stat().st_dev == b.stat().st_dev
    except OSError:
        return False
