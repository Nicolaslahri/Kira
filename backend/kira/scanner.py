"""Directory walker — finds candidate media files. No parsing yet."""

from __future__ import annotations

import logging

import os
import re
import threading
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".ts", ".m2ts", ".mpg", ".mpeg", ".vob",
}
AUDIO_EXTENSIONS = {
    ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma", ".alac",
}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS


# NOTE: NOT a bare "@" — that over-excluded legit user folders like "@Animes"
# / "@Movies" / "@4K" (a common sort-to-top prefix). We list only the actual
# Synology/QNAP system dirs (`@eaDir`, `@__thumb`, `@Recycle`, snapshot dirs).
_IGNORED_PREFIXES = (
    ".", "$", "__MACOSX", "#recycle",
    "@eaDir", "@__", "@Recycle", "@Recently-Snapshot", "@SynoFinder", "@sharesnap",
)
# Exact-match dirs we always skip — common NAS thumbnail/trash folders
# whose names don't share a single prefix character.
_IGNORED_NAMES = frozenset({"System Volume Information", "Thumbs.db", "lost+found"})

# A leading dot normally means "hidden" (.git, .AppleDouble) and the segment is
# skipped — but a few REAL titles legitimately start with one, most famously the
# `.hack//` franchise (`.hack//SIGN`, `.hack//Roots`, `.hack//G.U.`). Carve those
# out so they aren't silently dropped. Matched case-insensitively as a prefix.
_DOT_KEEP_PREFIXES = (".hack",)

# Phase 19: sample / extras / trailer exclusion. Scene releases ship a
# tiny `sample.mkv` (5-50 MB preview), `trailer.mp4`, `proof.mkv`, and
# Plex/Jellyfin extras folders (Featurettes/, Behind The Scenes/, …). Without
# excluding them a 30 MB sample gets renamed AS the real episode/movie. We
# skip them at walk time (the reference renamer's default). NOTE: "Specials" is NOT an
# extras folder — Phase 2 routes specials to season 0 as real content.
_EXTRAS_DIRNAMES = frozenset({
    "extras", "featurettes", "behind the scenes", "deleted scenes", "bloopers",
    "interviews", "trailers", "trailer", "sample", "samples", "shorts", "other",
})
# A `sample` / `trailer` / `proof` token bounded by separators or string ends.
_SAMPLE_TOKEN_RE = re.compile(r"(?:^|[\s._-])(?:sample|trailer|proof)(?:[\s._-]|$)", re.IGNORECASE)
# Below this, a `sample`-named video is almost certainly a real sample, not a
# legit title that happens to contain the word.
_SAMPLE_MAX_BYTES = 300 * 1024 * 1024


def _is_sample_or_extra(p: Path) -> bool:
    """True when `p` is a scene sample, trailer, proof, or lives in an
    extras folder — files we must NOT rename as the main media."""
    if p.parent.name.lower() in _EXTRAS_DIRNAMES:
        return True
    stem = p.stem.lower()
    # Unambiguous junk stems regardless of size.
    if stem in ("sample", "trailer", "proof") or stem.endswith(("-sample", ".sample", "-trailer")):
        return True
    # A `sample`/`trailer`/`proof` token elsewhere in the name → confirm with
    # size so a legit title ("Free Sample", "Trailer Park Boys") isn't culled.
    if _SAMPLE_TOKEN_RE.search(stem):
        try:
            return p.stat().st_size < _SAMPLE_MAX_BYTES
        except OSError:
            return False
    return False

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


def _record_walk_error(path: str | os.PathLike[str]) -> None:
    """Append a path the walk had to skip to the thread-local error list — the same
    one `_walk_onerror` feeds and `get_walk_errors()` reads. Used for skips that
    `os.walk`'s own onerror callback never sees (the manual symlink/junction
    descent), so a broken/unreachable target is SURFACED + spared by the prune
    sweep instead of vanishing silently with the scan still 'completed'."""
    if not hasattr(_walk_errors, "paths"):
        _walk_errors.paths = []
    _walk_errors.paths.append(str(path))


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
    logger.warning(f"[SCAN] walk OSError on {filename!r}: {err!r}")
    _record_walk_error(filename)


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


def _ext_prefix(path_str: str) -> str:
    r"""Force a Windows extended-length (`\\?\`) prefix so `os.walk`'s internal
    `scandir` can descend into >260-char CHILD paths.

    Unlike `longpath.long_path` this does NOT gate on length: the scan ROOT is
    usually short ("Z:\\Anime"), and it's the deep children (long anime / light-
    novel titles nested several folders down) that blow past MAX_PATH — but the
    prefix has to be present from the root down for `scandir` to inherit it. The
    caller strips it back off every yielded path (`_strip_ext_prefix`) so the
    prefix never leaks into the DB / parser / renamer. No-op off Windows or when
    already prefixed (so the primary Docker-on-Linux deployment is untouched)."""
    if os.name != "nt":
        return path_str
    if path_str.startswith("\\\\?\\"):
        return path_str
    ap = os.path.abspath(path_str)
    if ap.startswith("\\\\"):
        return "\\\\?\\UNC\\" + ap[2:]   # \\server\share\… → \\?\UNC\server\share\…
    return "\\\\?\\" + ap


def _strip_ext_prefix(path_str: str) -> str:
    r"""Inverse of `_ext_prefix` — strip a `\\?\` / `\\?\UNC\` prefix so yielded
    paths match the rest of the pipeline (DB, parser, renamer expect plain paths).
    No-op when there's no prefix."""
    if path_str.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path_str[len("\\\\?\\UNC\\"):]
    if path_str.startswith("\\\\?\\"):
        return path_str[len("\\\\?\\"):]
    return path_str


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
        # `.hack//SIGN` and friends legitimately lead with a dot — keep them
        # (still honoring exact-name skips); everything else leading with a dot
        # (.git, .AppleDouble) stays hidden/skipped.
        if part.lower().startswith(_DOT_KEEP_PREFIXES):
            return part in _IGNORED_NAMES
        return part.startswith(_IGNORED_PREFIXES) or part in _IGNORED_NAMES

    # os.walk(followlinks=False) means dir symlinks land in `dirnames` but
    # aren't auto-descended; we choose per-dir whether to recurse, gated by
    # the inode seen-set so a cycle terminates after one visit.
    # EE-2: onerror callback surfaces NAS-disconnect / permission-error
    # scandir() failures. Default (no callback) would silently truncate
    # the walk — a Friday-night router reboot would hide 5,800 files and
    # the user would see scan.status='completed' with no indication.
    # Long-path safety (Windows): feed os.walk an extended-length (`\\?\`) root so
    # its internal scandir can descend into >260-char children — deep anime / light-
    # novel trees routinely blow past MAX_PATH and were silently lost on the .exe
    # build. We strip the prefix back off every yielded path (below) so it never
    # leaks downstream. `rel_root` is the root run through the SAME abspath
    # normalization as the dirpaths, so `relative_to` lines up. All no-ops off Windows.
    rel_root = Path(_strip_ext_prefix(_ext_prefix(str(root_path))))
    for dirpath, dirnames, filenames in os.walk(
        _ext_prefix(str(root_path)), followlinks=False, onerror=_walk_onerror,
    ):
        dp = Path(_strip_ext_prefix(dirpath))
        # Skip the whole subtree if any segment matches an ignore rule.
        if any(_should_skip_part(p) for p in dp.relative_to(rel_root).parts):
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
                except OSError as e:
                    # Broken / unreachable symlink or junction target. os.walk
                    # (followlinks=False) never touches it, so without recording it
                    # the whole subtree vanishes silently and the scan still reads
                    # 'completed'. Surface it like any other walk error.
                    logger.warning(f"[SCAN] symlink/junction target unreadable, skipping {child}: {e!r}")
                    _record_walk_error(child)
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
                logger.warning(f"[SCAN] Skipping file with control chars in name: {fn!r} (in {dp})")
                continue
            p = dp / fn
            # PERF: filter by EXTENSION first — a pure string check, no
            # filesystem hit. The previous `p.is_file()` was a redundant stat
            # PER FILE (os.walk already classified these as non-directory
            # entries via scandir); on a NAS that's a wasted round-trip for
            # every file. Non-regular entries (broken symlinks, FIFOs) that
            # slip through are harmless — the scan worker's single `stat()`
            # for file size handles them (size stays None, file still tracked).
            if p.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            # Phase 19: drop scene samples / trailers / extras so a 30 MB
            # sample never gets renamed as the real episode/movie.
            if _is_sample_or_extra(p):
                logger.warning(f"[SCAN] Skipping sample/extra: {p}")
                continue
            # User-defined ignore globs (Settings → Paths). Checked after the
            # built-in rules so they only ever EXCLUDE more, never less.
            if _matches_user_ignore(p):
                logger.info(f"[SCAN] Skipping user-ignored: {p}")
                continue
            yield p


# ── User-defined ignore globs ────────────────────────────────────────
# Set per-scan by the scan worker from the `scanning.ignore_patterns`
# setting (module state, like `_walk_errors` — the recursive walk makes
# parameter-threading awkward). Each pattern is an fnmatch glob tested
# case-insensitively against the FILENAME and every folder name on the
# file's path, so both `*.partial.mkv` and `Anime Music Videos` work.
_USER_IGNORES: list[str] = []


def set_user_ignores(patterns: list[str] | None) -> None:
    global _USER_IGNORES
    _USER_IGNORES = [p.strip().lower() for p in (patterns or []) if p and p.strip()]


def _matches_user_ignore(p: Path) -> bool:
    if not _USER_IGNORES:
        return False
    import fnmatch
    parts = [p.name.lower(), *(seg.lower() for seg in p.parent.parts)]
    return any(
        fnmatch.fnmatch(part, pat)
        for pat in _USER_IGNORES
        for part in parts
    )


def media_type_hint(path: Path) -> str:
    """Cheap routing hint based on extension. Real type comes from parser."""
    if path.suffix.lower() in AUDIO_EXTENSIONS:
        return "music"
    return "unknown"
