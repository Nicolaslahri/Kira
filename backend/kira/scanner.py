"""Directory walker — finds candidate media files. No parsing yet."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path

VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".ts", ".m2ts", ".mpg", ".mpeg", ".vob",
}
AUDIO_EXTENSIONS = {
    ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma", ".alac",
}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS


_IGNORED_PREFIXES = (".", "@", "$", "__MACOSX", "#recycle")
# Exact-match dirs we always skip — common NAS thumbnail/trash folders
# whose names don't share a single prefix character.
_IGNORED_NAMES = frozenset({"System Volume Information", "Thumbs.db", "lost+found"})

# Windows file attribute: FILE_ATTRIBUTE_REPARSE_POINT (0x400) marks
# junctions, symlinks, and mount points. Used by R2-C6 to detect
# junctions which `Path.is_symlink()` doesn't catch on older Pythons.
_WIN_REPARSE_POINT = 0x400


# EE-2: thread-local store for OSError paths surfaced by os.walk's
# onerror callback. Per-thread so concurrent walks (rare, but possible
# in tests) don't trample each other. Cleared at the start of each walk()
# call. _scan_worker reads `get_walk_errors()` after Phase 1 to mark the
# scan as `completed_partial` if any directory was unreachable.
_walk_errors = threading.local()


def _walk_onerror(err: OSError) -> None:
    """`os.walk` delivers scandir() failures here when `onerror` is set.

    Default behavior (onerror=None) is SILENT skip — catastrophic for NAS
    scans where a 90-second router reboot can hide 5,800 files from view
    forever (the user sees scan.status='completed' and trusts it).

    Log so the failure shows in backend logs, AND record so the scan
    worker can write `completed_partial` instead of `completed` plus
    raise a UI notification telling the user which paths were missed.
    """
    filename = getattr(err, "filename", "<unknown>") or "<unknown>"
    print(f"[SCAN] walk OSError on {filename!r}: {err!r}")
    if not hasattr(_walk_errors, "paths"):
        _walk_errors.paths = []
    _walk_errors.paths.append(str(filename))


def get_walk_errors() -> list[str]:
    """Return the list of paths that failed to scandir() during the most
    recent walk() call on this thread. Empty list means the walk was
    fully traversable. Caller (the scan worker) uses this to decide
    between scan.status = 'completed' vs 'completed_partial'.
    """
    return list(getattr(_walk_errors, "paths", []))


def reset_walk_errors() -> None:
    """Clear the thread-local walk-error list. Caller should invoke this
    BEFORE starting a fresh top-level walk; we deliberately don't reset
    inside walk() itself because walk() recurses on symlinked dirs via
    `yield from walk(child)` and a per-call reset would wipe the parent's
    error accumulation when the recursion fires.
    """
    _walk_errors.paths = []


def _is_reparse_or_symlink(p: Path) -> bool:
    """R2-C6: True if `p` is a symlink (POSIX) OR a junction / reparse
    point (Windows).

    `Path.is_symlink()` is unreliable on Windows for junctions — in
    Python ≤3.11 it returns False for NTFS junction points even though
    they behave like directory symlinks (and can form loops). We check
    `st_file_attributes` for the reparse-point bit as a belt-and-braces
    cross-platform guard.
    """
    try:
        if p.is_symlink():
            return True
    except OSError:
        return False
    if os.name == "nt":
        try:
            st = p.stat(follow_symlinks=False)
            attrs = getattr(st, "st_file_attributes", 0) or 0
            if attrs & _WIN_REPARSE_POINT:
                return True
        except (OSError, AttributeError):
            pass
    return False


def walk(root: str | Path) -> Iterator[Path]:
    """Yield every media file under `root` (recursive).

    Skips:
      - hidden dirs (`.git`, `.AppleDouble`)
      - NAS indexing dirs (Synology `@eaDir`, QNAP `@__thumb`)
      - Windows / macOS junk (`$RECYCLE.BIN`, `__MACOSX`, `System Volume Information`)
      - Synology recycle bin (`#recycle`)
      - **directory symlinks AND symlink loops**. The previous `rglob("*")`
        implementation followed dir symlinks unbounded — `Z:\\media\\tv\\loop`
        pointing back to `Z:\\media` would freeze the scan worker for
        minutes before OOM-killing the process. We now walk via `os.walk`
        with `followlinks=False`, then optionally descend symlinked dirs
        ONCE per (st_dev, st_ino) so an intentional library symlink works
        but a cycle terminates.
    Without these, scans on mounted NAS shares end up parsing thousands of
    thumbnail/index files and waste minutes per scan.
    """
    root_path = Path(root)
    # EE-2: do NOT reset _walk_errors here — walk() recurses on symlinks
    # via `yield from walk(child)`, and a per-call reset would erase the
    # parent walk's accumulated errors. Caller (the scan worker) must
    # call reset_walk_errors() explicitly before the top-level walk.
    if not root_path.exists():
        return
    seen_dirs: set[tuple[int, int]] = set()
    # Seed the seen-set with the root itself so even a `root → root` self-loop
    # via a symlink is caught the moment we'd try to re-enter.
    try:
        st = root_path.stat()
        seen_dirs.add((st.st_dev, st.st_ino))
    except OSError:
        return

    def _should_skip_part(part: str) -> bool:
        return part.startswith(_IGNORED_PREFIXES) or part in _IGNORED_NAMES

    # os.walk(followlinks=False) means dir symlinks land in `dirnames` but
    # aren't auto-descended; we choose per-dir whether to recurse, gated by
    # the inode seen-set so a cycle terminates after one visit.
    # EE-2: onerror callback surfaces NAS-disconnect / permission-error
    # scandir() failures. Default (no callback) would silently truncate
    # the walk — a Friday-night router reboot would hide 5,800 files and
    # the user would see scan.status='completed' with no indication.
    for dirpath, dirnames, filenames in os.walk(
        str(root_path), followlinks=False, onerror=_walk_onerror,
    ):
        dp = Path(dirpath)
        # Skip the whole subtree if any segment matches an ignore rule.
        if any(_should_skip_part(p) for p in dp.relative_to(root_path).parts):
            dirnames[:] = []
            continue

        # Manual symlink/junction descent: filter dirnames in-place so
        # os.walk only recurses into the dirs we approve. Loops are
        # caught here via inode dedup.
        # R2-C6: `_is_reparse_or_symlink` catches Windows junctions too,
        # which `Path.is_symlink()` misses — a junction creating a loop
        # would otherwise be followed unbounded.
        kept: list[str] = []
        for d in dirnames:
            if _should_skip_part(d):
                continue
            child = dp / d
            if _is_reparse_or_symlink(child):
                # Symlinked/junction dir — only descend if we haven't
                # visited the real underlying inode yet. Broken targets
                # short-circuit.
                try:
                    st = child.stat()
                except OSError:
                    continue
                key = (st.st_dev, st.st_ino)
                if key in seen_dirs:
                    continue  # symlink/junction loop or repeat visit
                seen_dirs.add(key)
                kept.append(d)
                # Manually expand the subtree by re-walking it; os.walk
                # with followlinks=False won't enter symlinks for us.
                yield from walk(child)
                continue
            kept.append(d)
        dirnames[:] = kept

        for fn in filenames:
            if _should_skip_part(fn):
                continue
            # R2-M6: NUL-byte / control-char filenames sneak in from
            # broken SMB mounts, FUSE filesystems, or torrent clients
            # that didn't sanitize. Python's pathlib accepts them but
            # downstream code (JSON serialization of parsed_data, the
            # rename engine) crashes. Silent-skip would be invisible —
            # log + skip so the user knows a file was passed over.
            if "\x00" in fn or any(ord(c) < 0x20 and c != "\t" for c in fn):
                print(f"[SCAN] Skipping file with control chars in name: {fn!r} (in {dp})")
                continue
            p = dp / fn
            try:
                if not p.is_file():
                    continue
            except OSError as e:
                # Visible log — silent skip on OSError used to hide files
                # whose paths the OS itself refused (NUL bytes, exceeding
                # PATH_MAX). The user wonders where they went.
                print(f"[SCAN] OSError on {p}: {e!r}")
                continue
            if p.suffix.lower() in MEDIA_EXTENSIONS:
                yield p


def media_type_hint(path: Path) -> str:
    """Cheap routing hint based on extension. Real type comes from parser."""
    if path.suffix.lower() in AUDIO_EXTENSIONS:
        return "music"
    return "unknown"
