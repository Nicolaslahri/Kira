"""File operations — move / copy / symlink / hardlink, plus their inverse for undo.

All operations write to the target's parent dir, creating it if needed.
Failures raise — callers (the /rename endpoint) wrap them into per-file results.
"""

from __future__ import annotations

import os
import re
import shutil
from enum import Enum
from pathlib import Path

from kira.scanner import MEDIA_EXTENSIONS


class FileOp(str, Enum):
    MOVE = "move"
    COPY = "copy"
    SYMLINK = "symlink"
    HARDLINK = "hardlink"


# ── Tier 1.2: Subtitle / sidecar co-renaming ───────────────────────────
# Extensions that travel with a video file. When the video moves, these
# move alongside it under the renamed basename. the reference renamer does this for
# every video — Plex / Jellyfin / Kodi rely on sidecar files sitting
# next to the video with a matching stem to attach subtitles, chapters,
# and aux audio. Renaming the video without the sidecars silently
# breaks subtitle pairing (user clicks play → no subs → confused).
#
# Lowercase, with leading dot. Matched case-insensitively against the
# real filesystem extension. We deliberately exclude `.txt` (too risky
# — common for readme/scene notes) and `.nfo` (those are MANAGED by
# Kira itself in a later iteration; we don't want to move user-written
# .nfo as if it were ours).
_SIDECAR_EXTS = frozenset({
    ".srt",      # SubRip — overwhelming majority
    ".ass",      # Advanced SubStation Alpha
    ".ssa",      # SubStation Alpha (legacy)
    ".sub",      # MicroDVD / VobSub index pair
    ".idx",      # VobSub index (paired with .sub)
    ".vobsub",   # MKV-extracted VobSub blob
    ".sup",      # PGS (Blu-ray)
    ".smi",      # SAMI
    ".usf",      # Universal Subtitle Format
})


# ── Plex / Jellyfin / Kodi media-server artifacts ──────────────────────
# Files and directories that media servers auto-generate inside library
# folders. These are NOT user content — they're cached metadata. When
# the videos move out, the old folder is left dangling because rmdir
# refuses to remove a directory containing these artifact files.
#
# Without explicit cleanup, the user sees:
#   Z:\media\tv\Old Show\poster.jpg          ← Plex generated
#   Z:\media\tv\Old Show\tvshow.nfo          ← Jellyfin generated
#   Z:\media\tv\Old Show\.actors\actor.jpg   ← Kodi generated
#   Z:\media\tv\Old Show\Season 01\Season01-poster.jpg
# … and every directory above the lowest non-empty one stays around
# forever as garbage.
#
# The lists below are intentionally conservative. We only delete things
# we're SURE are server-generated. Trailers/, Featurettes/, Behind The
# Scenes/, Subs/, Bonus/, etc. could legitimately contain user content
# (a tracker pack might include the trailer) — explicitly NOT on the
# list. If a user has a `poster.jpg` they care about they shouldn't
# put it in a folder named after a Kira-managed show; the convention
# is universal enough that this trade-off is correct.

# Exact filenames (case-insensitive match against `entry.name.lower()`).
# Covers the standard artwork + NFO files Plex/Jellyfin/Kodi write.
_ARTIFACT_FILENAMES = frozenset({
    # Series / movie / album poster
    "poster.jpg", "poster.png", "poster.jpeg",
    # Banner art
    "banner.jpg", "banner.png", "banner.jpeg",
    # Background fanart (Kodi `fanart`, Emby `background`, Jellyfin/TMDB `backdrop`)
    "fanart.jpg", "fanart.png", "fanart.jpeg",
    "background.jpg", "background.png",
    "backdrop.jpg", "backdrop.png", "backdrop.jpeg",
    # Clear art / logo (transparent PNG overlays)
    "clearart.jpg", "clearart.png",
    "clearlogo.jpg", "clearlogo.png",
    "logo.jpg", "logo.png",
    # Landscape / thumb (widescreen)
    "landscape.jpg", "landscape.png",
    "thumb.jpg", "thumb.png",
    # Movie disc art / key art / character art
    "disc.jpg", "disc.png",
    "keyart.jpg", "keyart.png",
    "characterart.jpg", "characterart.png",
    # Folder cover (legacy Windows / Kodi)
    "folder.jpg", "folder.png",
    "cover.jpg", "cover.png",
    # NFO metadata (series / season / movie / music level — NOT per-
    # episode NFOs which would be `<stem>.nfo` and arguably travel with
    # the video as sidecars; that's a separate iteration)
    "tvshow.nfo", "season.nfo", "show.nfo",
    "movie.nfo",
    "album.nfo", "artist.nfo",
})

# Regex-matched artifact filenames (case-insensitive). Covers Kodi's
# `seasonNN-<type>.<ext>` artwork files that live in the show root
# rather than in the per-season subfolder.
#
#   season01-poster.jpg / season01-banner.jpg / season01-fanart.jpg
#   season-specials-poster.jpg
#   season-all-poster.jpg
#
# Plus a few rarer variants. Pattern is permissive on the prefix
# ("season" + anything) to catch upper/lower-case + numbered + "all"
# / "specials" / "0".
_ARTIFACT_FILENAME_RE = re.compile(
    r"^season[-\w]*-(?:poster|banner|fanart|thumb|landscape|clearart|clearlogo|logo)"
    r"\.(?:jpg|jpeg|png)$",
    re.IGNORECASE,
)

# Per-file artwork pattern: any filename ending with `-<artwork-type>.<ext>`
# where <artwork-type> is one of the conventional media-server suffixes.
# This is the most common form of leftover garbage in real libraries —
# Sonarr/Jellyfin/Plex/Kodi all generate per-episode thumbnails using
# this convention.
#
# Examples from the wild:
#   Frieren.Beyond.Journeys.End.S02E01.2026.2160p.WEB-DL-thumb.jpg ← Sonarr thumb
#   Show.S01E05-thumb.jpg                                          ← Jellyfin thumb
#   Movie (2023)-poster.jpg                                        ← per-file poster
#   Album-fanart.png                                               ← music fanart
#   Show.S01E01-fanart-2.jpg                                       ← numbered alt
#
# Trade-off accepted: a user file named `holiday-poster.jpg` sitting
# inside a Kira-managed media folder would match and be deleted. The
# media-server convention is so universal (dash + lowercase suffix + jpg/png)
# that the false-positive risk is acceptably small. If the user has
# personal images they want preserved, they shouldn't keep them in
# Kira's managed folders.
#
# The `(?:-\d+)?` group catches numbered variants for alternate artwork
# (e.g. `-fanart-2.jpg`, `-poster-3.png`).
_PER_FILE_ARTIFACT_RE = re.compile(
    r".+-(?:thumb|poster|banner|fanart|landscape|clearart|clearlogo|logo|"
    r"disc|keyart|characterart)"
    r"(?:-\d+)?"
    r"\.(?:jpg|jpeg|png)$",
    re.IGNORECASE,
)

# Catchall extensions: any file with one of these extensions is treated
# as a media-server artifact regardless of basename. `.tbn` is Kodi's
# binary thumbnail format — there's no other legitimate use of `.tbn`
# in media libraries.
#
# `.nfo` is safe as a catchall HERE (and only here) because artifact
# classification is exclusively consulted behind the `_is_artifacts_only`
# gate — i.e. for folders whose every video has already moved out. A
# per-episode/movie NFO without its video is orphaned metadata (and Kira
# rewrites NFOs at the destination when that output is enabled). Without
# this, Kira's OWN previous NFO output blocked the cleanup walk: a
# season folder left with `<episode>.nfo` + `-poster.jpg` + `-thumb.jpg`
# failed the artifacts-only check on the NFO alone and survived forever.
# (Exact names like tvshow.nfo in _ARTIFACT_FILENAMES are now redundant
# but kept for documentation value.)
_ARTIFACT_EXTENSIONS = frozenset({
    ".tbn",   # Kodi legacy binary thumbnail
    ".nfo",   # orphaned episode/movie/scene metadata — see note above
})

# Exact directory names that are pure media-server caches. These are
# wiped with `shutil.rmtree` — deeper than rmdir, because Kodi's
# .actors/ has actor portrait files nested inside. None of these
# directories ever contain user content by convention.
_ARTIFACT_DIRNAMES = frozenset({
    ".actors",       # Kodi: actor headshots
    ".metadata",     # Plex: local metadata cache
    "extrafanart",   # Kodi: extra fanart
    "extrathumbs",   # Kodi: extra thumbs
    "backdrops",     # Jellyfin: backdrop images
    "metadata",      # Generic metadata cache (some scrapers)
})


def _is_artifact_file(name: str, extra_names: frozenset[str] = frozenset(),
                      extra_exts: frozenset[str] = frozenset()) -> bool:
    """True if `name` is a filename we recognize as a media-server
    artifact safe to delete during empty-folder cleanup.

    Case-insensitive on every check (Plex on Windows happily creates
    `Poster.jpg`, `POSTER.JPG`, etc.). Families:
      1. Exact-name match (`poster.jpg`, `tvshow.nfo`, …) + user `extra_names`
      2. Season-numbered artwork (`season01-poster.jpg`, …)
      3. Per-file artwork suffix (`<stem>-thumb.jpg`, `<stem>-fanart-2.png`, …)
      4. Extension catchall (`.tbn` — Kodi binary thumbs) + user `extra_exts`

    `extra_names` (lowercased filenames) and `extra_exts` (lowercased, dot-led
    extensions) come from the user's `rename.cleanup_extra_filenames` /
    `rename.cleanup_extra_extensions` settings — "delete these too".
    """
    lower = name.lower()
    if lower in _ARTIFACT_FILENAMES or lower in extra_names:
        return True
    if _ARTIFACT_FILENAME_RE.match(lower):
        return True
    if _PER_FILE_ARTIFACT_RE.match(lower):
        return True
    # Extension-only catchall — e.g. `Show.S01E01.tbn` (Kodi binary thumb).
    # endswith() instead of suffix-set membership so we don't pay the
    # `Path()` allocation cost when this is hot during a large cleanup.
    for ext in _ARTIFACT_EXTENSIONS:
        if lower.endswith(ext):
            return True
    for ext in extra_exts:
        if ext and lower.endswith(ext):
            return True
    return False


# Subtitle sidecars — kept by the `keep_subs` aggressive-cleanup mode (a user
# might still want them even when nuking other non-video leftovers).
_SUBTITLE_EXTENSIONS = frozenset({
    ".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".smi", ".sami", ".pgs", ".sup",
})


def _is_media_file(name: str) -> bool:
    """True for a real media file — video OR audio. Cleanup must NEVER delete
    one of these: it's the library's actual content. (Audio matters because the
    cleanup walk runs for music libraries too — an un-renamed album track must
    survive aggressive cleanup, not just video.)"""
    from kira.scanner import MEDIA_EXTENSIONS  # lazy — avoid import cycle; video|audio
    lower = name.lower()
    return any(lower.endswith(ext) for ext in MEDIA_EXTENSIONS)


def _is_subtitle_file(name: str) -> bool:
    lower = name.lower()
    return any(lower.endswith(ext) for ext in _SUBTITLE_EXTENSIONS)


def _is_removable_file(name: str, *, mode: str, extra_names: frozenset[str],
                       extra_exts: frozenset[str]) -> bool:
    """Whether a FILE should be deleted during source-folder cleanup, per the
    aggressive-cleanup `mode`:
      'off'       → recognized artifacts only (built-in list + user extras).
      'keep_subs' → any non-media, non-subtitle file (artifacts included).
      'all'       → any non-media file (artifacts + subtitles + everything else).

    A real MEDIA file (video OR audio) is NEVER removable, in ANY mode — and
    this guard runs FIRST, so even a user who puts a media extension/name in the
    custom 'delete' lists can't cause the content itself to be deleted."""
    if _is_media_file(name):
        return False
    if _is_artifact_file(name, extra_names, extra_exts):
        return True
    if mode == "all":
        return True
    if mode == "keep_subs":
        return not _is_subtitle_file(name)
    return False


def _norm_path_key(p: str) -> str:
    """Case/separator-normalized absolute-path key for set membership. Matches
    two spellings of the SAME file within one batch (the rename writes targets
    and we read them back via iterdir, so they share a spelling)."""
    return os.path.normcase(os.path.normpath(str(p)))


def _user_listed(name: str, extra_names: frozenset[str], extra_exts: frozenset[str]) -> bool:
    """True iff the user EXPLICITLY put this filename or extension on their
    custom delete lists — their stated intent overrides the 'protect Kira's own
    artwork/NFO output names' rule in the in-place sweep below."""
    lower = name.lower()
    if lower in extra_names:
        return True
    return any(ext and lower.endswith(ext) for ext in extra_exts)


def sweep_destination_junk(
    folder: Path,
    *,
    mode: str = "off",
    extra_names: frozenset[str] = frozenset(),
    extra_exts: frozenset[str] = frozenset(),
    trash_root: Path | None = None,
    protected: frozenset[str] = frozenset(),
) -> int:
    """Sweep leftover junk from a folder a rename just wrote INTO — the in-place
    / same-folder case the source-walk (`_cleanup_empty_source_parents`) can't
    reach, because it only ever REMOVES vacated folders and bails the instant a
    folder still holds media. Here the folder keeps its media; we only strip the
    junk beside it. Deletes per the aggressive `mode` + the user's custom lists,
    but NEVER:

      * a media file (video/audio) — `_is_removable_file` guards this first;
      * anything in `protected` (case/separator-normalized abs paths) — the
        renamed videos + every sidecar/NFO/artwork Kira wrote THIS batch, so we
        can't delete our own fresh output;
      * a file whose NAME matches a built-in media-server artifact (poster.jpg,
        tvshow.nfo, `<stem>-thumb.jpg`, `*.nfo`, season art, …) — those are
        EXACTLY what Kira emits, so a PRIOR run's poster/NFO that isn't in this
        batch's `protected` set is still spared — UNLESS the user explicitly put
        that name/extension on their custom delete list.

    Never removes `folder` itself (it holds the media) and never recurses into
    subdirectories (user content: Subs/, Extras/, Season NN/). Honors Trash.
    Best-effort: every OSError on an entry is skipped. Returns the count deleted.
    """
    if not folder.exists() or not folder.is_dir():
        return 0
    try:
        entries = list(folder.iterdir())
    except OSError:
        return 0
    prot = {_norm_path_key(p) for p in protected}
    # Media STILL in this folder — the renamed file(s) AND any pre-existing,
    # un-renamed episode sharing the folder. A non-media file is treated as a
    # SIDECAR of that media when the media's stem prefixes it (`ep02.en.srt` for
    # `ep02.mkv`) and is then spared, so an aggressive 'all'/'keep_subs' sweep
    # strips only LOOSE junk (matching no media stem) and never collateral-
    # deletes a neighbouring episode's subtitle / NFO. (The custom delete lists +
    # aggressive modes are for release junk, not other content's sidecars.)
    media_stems = [
        e.stem.lower() for e in entries
        if (e.is_file() or e.is_symlink()) and _is_media_file(e.name) and e.stem
    ]
    removed = 0
    for entry in entries:
        try:
            # Files (and broken/symlinked files) only — directories are user
            # content here and are never touched by the in-place sweep.
            if entry.is_dir() and not entry.is_symlink():
                continue
            if not (entry.is_file() or entry.is_symlink()):
                continue
            name = entry.name
            try:
                if _norm_path_key(str(entry)) in prot:
                    continue  # our own fresh output / the renamed media
            except OSError:
                continue       # can't resolve a key → never risk deleting
            if not _is_removable_file(name, mode=mode, extra_names=extra_names, extra_exts=extra_exts):
                continue       # media, or not removable in this mode
            # A built-in artifact NAME is Kira's own output shape → protect it,
            # unless the user explicitly targeted that name/extension.
            if _is_artifact_file(name, frozenset(), frozenset()) and not _user_listed(name, extra_names, extra_exts):
                continue
            # A sidecar of media still present (its stem prefixes this name)
            # belongs to that content — never collateral-delete a neighbouring
            # un-renamed episode's .srt/.nfo. Absolute (not user-list-overridable).
            _cand = name.lower()
            if any(_cand.startswith(stem + ".") for stem in media_stems):
                continue
            if trash_root is not None:
                if _move_to_trash(entry, trash_root):
                    removed += 1
                continue
            try:
                entry.unlink()
                removed += 1
            except OSError:
                pass           # locked / permission denied — skip silently
        except OSError:
            continue
    return removed


def _is_artifact_dir(name: str) -> bool:
    """True if `name` is a directory we recognize as a media-server
    cache safe to recursively delete during empty-folder cleanup."""
    return name.lower() in _ARTIFACT_DIRNAMES


def discover_sidecars(video_src: Path) -> list[Path]:
    """Return paths to sidecar files in the video's directory that share
    the video's basename stem.

    Matches both flat naming — `Movie (2010).srt` — and Plex multi-locale
    naming — `Movie (2010).eng.srt`, `Movie (2010).fr.forced.srt`.

    Discovery rules:
      * Lives in the SAME directory as the video. No subfolders (avoids
        accidentally hoovering up an unrelated `Subs/` directory).
      * Extension is in `_SIDECAR_EXTS` (case-insensitive).
      * Filename starts with the video's stem followed by either nothing
        else (just `.srt`) or a `.` (language / forced / sdh marker).
      * The sidecar's full path is NOT the video itself — a video with
        a sidecar-typed extension can't be its own sidecar.

    Returns an empty list when the parent directory can't be listed
    (permission denied, network volume hiccup) — sidecar co-rename is
    best-effort and never blocks the primary video move.
    """
    parent = video_src.parent
    if not parent.exists():
        return []
    stem = video_src.stem
    if not stem:
        return []
    try:
        entries = list(parent.iterdir())
    except OSError:
        return []

    # Other media files (video/audio) whose stem could ALSO prefix a sidecar.
    # Used to resolve prefix collisions: a sidecar belongs to the LONGEST
    # matching media stem, so `Show.S01E01.mkv` (stem "Show.S01E01") doesn't
    # steal `Show.S01E01.Extended.eng.srt` — that sidecar belongs to the
    # longer-stemmed `Show.S01E01.Extended.mkv` sitting in the same folder.
    other_media_stems = [
        e.stem for e in entries
        if e.suffix.lower() in MEDIA_EXTENSIONS and e.stem != stem and e.is_file()
    ]

    matches: list[Path] = []
    video_resolved: Path | None
    try:
        video_resolved = video_src.resolve()
    except OSError:
        video_resolved = None

    for entry in entries:
        if not entry.is_file():
            continue
        # Self-skip — defensive (videos shouldn't have sidecar extensions,
        # but a .sub or .srt file might match its own discovery rule).
        try:
            if video_resolved is not None and entry.resolve() == video_resolved:
                continue
        except OSError:
            if entry.name == video_src.name:
                continue

        ext = entry.suffix.lower()
        if ext not in _SIDECAR_EXTS:
            continue

        name = entry.name
        # Two acceptable shapes:
        #   1. `<stem><ext>`                      — single sidecar (no lang)
        #   2. `<stem>.<lang>...<ext>`            — Plex-style multi-lang
        # Both require the stem followed immediately by `.` or by the ext.
        if name == f"{stem}{entry.suffix}":
            matches.append(entry)
            continue
        prefix = f"{stem}."
        if name.startswith(prefix):
            # Make sure the part between the stem and the extension is
            # non-empty (`Movie..srt` is malformed but harmless to skip).
            tail = name[len(stem):]  # ".eng.srt" or ".srt"
            if tail and tail != entry.suffix:
                # Defer to a longer-stemmed sibling media file that also claims
                # this sidecar — it's theirs (more specific), not ours.
                if any(
                    len(s2) > len(stem)
                    and (name == s2 + entry.suffix or name.startswith(f"{s2}."))
                    for s2 in other_media_stems
                ):
                    continue
                matches.append(entry)
    return matches


def compute_sidecar_target(
    sidecar: Path, video_src: Path, video_dst: Path,
) -> Path | None:
    """Compute where a sidecar should land given the video's source + target.

    Strips the video's stem from the sidecar's name, leaving the trailing
    suffix (e.g. `.eng.srt`, `.srt`, `.forced.fr.ass`), then appends that
    suffix to the renamed video's stem in the target directory.

    Returns None if the sidecar's name doesn't actually start with the
    video's stem — defensive guard, shouldn't fire if `discover_sidecars`
    produced the input.
    """
    src_stem = video_src.stem
    dst_stem = video_dst.stem
    if not src_stem or not dst_stem:
        return None

    name = sidecar.name
    if name == f"{src_stem}{sidecar.suffix}":
        # Plain `<stem>.srt` — drop the stem, keep the extension only.
        return video_dst.parent / f"{dst_stem}{sidecar.suffix}"
    prefix = f"{src_stem}."
    if not name.startswith(prefix):
        return None
    tail = name[len(src_stem):]  # ".eng.srt" / ".forced.fr.srt"
    return video_dst.parent / f"{dst_stem}{tail}"


# Free-space margin over the file size — covers FS metadata/rounding so a copy
# that lands the volume at exactly 0 free doesn't wedge the destination drive.
_SPACE_MARGIN = 16 * 1024 * 1024   # 16 MiB


def _ensure_space(src: Path, dst: Path) -> None:
    """Refuse a full-bytes copy that can't fit on the destination volume BEFORE
    writing a byte — so a too-small drive fails clean (clear error, nothing
    written) instead of crashing mid-write with ENOSPC and leaving a multi-GB
    partial for the user to hand-delete. Best-effort: if src/dst can't be
    stat'd we skip the check and let the write itself surface any error."""
    try:
        need = src.stat().st_size
        free = shutil.disk_usage(dst.parent).free
    except OSError:
        return
    if need + _SPACE_MARGIN > free:
        raise OSError(
            f"Not enough space to copy {src.name}: needs {need // (1<<20)} MiB "
            f"(+margin), only {free // (1<<20)} MiB free at {dst.parent}")


class RenameSkipped(Exception):
    """Raised by execute_op when `on_conflict='skip'` and a DIFFERENT file
    already occupies the destination. The caller treats it as a deliberate
    no-op (leave both files untouched), not an error."""


def _apply_permissions(path: Path, perms: dict, *, is_dir: bool) -> None:
    """Best-effort post-rename chmod/chown for Docker / NAS deployments —
    applies the octal `dir_mode`/`file_mode` + `uid`/`gid` from the resolved
    `rename.set_permissions` spec. NEVER raises: a perms failure (no privilege,
    FAT/exFAT, Windows, a symlink) must not fail the rename itself."""
    mode = perms.get("dir_mode") if is_dir else perms.get("file_mode")
    if mode:
        try:
            os.chmod(str(path), int(str(mode), 8))
        except (OSError, ValueError):
            pass
    uid, gid = perms.get("uid"), perms.get("gid")
    if (uid is not None or gid is not None) and hasattr(os, "chown"):
        try:
            os.chown(str(path), uid if uid is not None else -1,  # type: ignore[attr-defined]
                     gid if gid is not None else -1)
        except OSError:
            pass


def execute_op(
    op: FileOp,
    src: Path,
    dst: Path,
    *,
    overwrite: bool = False,
    cleanup_empty_source: bool = False,
    cleanup_stop_at: Path | None = None,
    cleanup_max_levels: int = 2,
    cleanup_artifacts: bool = True,
    cleanup_trash_dir: Path | None = None,
    cleanup_nonvideo: str = "off",
    cleanup_extra_names: frozenset[str] = frozenset(),
    cleanup_extra_exts: frozenset[str] = frozenset(),
    symlink_relative: bool = False,
    permissions: dict | None = None,
    on_conflict: str = "error",
) -> int:
    """Run the requested operation. Idempotent for already-correct hardlinks.

    Returns the count of Plex/Jellyfin/Kodi metadata artifacts that were
    deleted as part of `cleanup_empty_source` (zero when the flag is
    off or no artifacts were present). Callers surface this number in
    the rename result so the user sees "Moved 1 file · cleaned 4
    metadata artifacts" rather than wondering whether their library
    silently grew or shrank.

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
    artifacts_cleaned = 0

    try:
        if dst.exists():
            if not overwrite:
                # If hardlink and they point at the same inode, treat as success.
                if op == FileOp.HARDLINK and _same_inode(src, dst):
                    return artifacts_cleaned
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
                        # Case-FOLDED resolved compare. On a case-insensitive
                        # volume (NTFS, APFS), a case-only rename ("show" →
                        # "Show") leaves src and dst as the SAME directory
                        # entry — `resolve()` may preserve the input case, so
                        # a plain string compare sees them as different and
                        # falls through to unlink(), DESTROYING the only entry
                        # (data loss). normcase folds case on those volumes; on
                        # case-sensitive POSIX it's a no-op, so genuinely
                        # distinct hardlinks still compare unequal there.
                        src_r = os.path.normcase(str(src.resolve()))
                        dst_r = os.path.normcase(str(dst.resolve()))
                    except OSError:
                        # Path resolution failed — be conservative, treat as no-op.
                        return artifacts_cleaned
                    if src_r == dst_r:
                        # Same directory entry. If the on-disk spelling already
                        # equals dst, it's a literal no-op. Otherwise it's a
                        # case-only rename — do it via a temp hop so the entry
                        # is NEVER unlinked (which would delete the file).
                        if str(src) != str(dst):
                            try:
                                tmp = dst.with_name(dst.name + ".kira-casefix-tmp")
                                os.rename(str(src), str(tmp))
                                os.rename(str(tmp), str(dst))
                            except OSError:
                                pass  # best-effort; leave as-is rather than risk loss
                        return artifacts_cleaned
                    # Two distinct paths sharing one inode — genuine hardlink
                    # case. Safe to unlink the source; bytes remain at dst.
                    src.unlink()
                    if cleanup_empty_source:
                        artifacts_cleaned += _cleanup_empty_source_parents(
                            src.parent, cleanup_stop_at, cleanup_max_levels,
                            sweep_artifacts=cleanup_artifacts,
                            trash_root=cleanup_trash_dir,
                            mode=cleanup_nonvideo,
                            extra_names=cleanup_extra_names,
                            extra_exts=cleanup_extra_exts,
                        )
                    return artifacts_cleaned
                # SYMLINK + dst is already a symlink to src: idempotent success.
                if op == FileOp.SYMLINK:
                    try:
                        if dst.is_symlink() and (dst.parent / os.readlink(str(dst))).resolve() == src.resolve():
                            return artifacts_cleaned
                    except OSError:
                        pass
                # Genuine conflict — a DIFFERENT file occupies dst (all
                # idempotent no-op cases already returned above). Honor the
                # user's on_conflict policy. "overwrite" never reaches here
                # (the caller passes overwrite=True for it); "skip" raises a
                # distinct signal the caller treats as a no-op; default errors.
                if on_conflict == "skip":
                    raise RenameSkipped(f"a different file already exists at {dst}")
                raise FileExistsError(f"Destination already exists: {dst}")
            # COPY does its own atomic temp-file + os.replace below, so it must
            # NOT pre-delete the (good) destination here — otherwise a mid-write
            # copy failure would leave neither the original nor a complete copy.
            # The other ops (rename/symlink/hardlink) need the slot freed first.
            if op != FileOp.COPY:
                dst.unlink()

        if op == FileOp.MOVE:
            src_parent_before = src.parent
            _atomic_move(src, dst)
            # User-requested: walk up the source's parent chain and rmdir
            # each level that's now empty after the move. Safety: rmdir
            # refuses non-empty directories so we can't clobber siblings.
            # We stop at cleanup_stop_at (typically the library root) so
            # we never rmdir the user's media root itself.
            #
            # Bonus: the cleanup walker also sweeps known Plex/Jellyfin/
            # Kodi metadata artifacts (`poster.jpg`, `tvshow.nfo`,
            # `.actors/`, etc.) from each level so the rmdir actually
            # succeeds instead of giving up on the first media-server
            # cache file it encounters.
            if cleanup_empty_source:
                artifacts_cleaned += _cleanup_empty_source_parents(
                    src_parent_before, cleanup_stop_at, cleanup_max_levels,
                    sweep_artifacts=cleanup_artifacts,
                    trash_root=cleanup_trash_dir,
                    mode=cleanup_nonvideo,
                    extra_names=cleanup_extra_names,
                    extra_exts=cleanup_extra_exts,
                )
        elif op == FileOp.COPY:
            _ensure_space(src, dst)          # refuse BEFORE writing if it won't fit
            # Atomic copy: write to a temp sibling, then os.replace into place.
            # A partial/failed copy never overwrites a good destination — on any
            # error we delete the temp and the original dst is left untouched.
            # (`os.replace` is an atomic same-dir rename, so there's no window
            # where dst is missing or half-written.)
            tmp = dst.with_name(dst.name + ".kira-copy-tmp")
            try:
                shutil.copy2(str(src), str(tmp))
                os.replace(str(tmp), str(dst))
            except Exception:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass
                raise
        elif op == FileOp.SYMLINK:
            # Relative target (portable across remounts / changed bind-mount
            # paths) when enabled; absolute otherwise. Computed from dst's
            # directory so the link resolves correctly in place.
            link_target = os.path.relpath(str(src), str(dst.parent)) if symlink_relative else str(src)
            os.symlink(link_target, str(dst))
        elif op == FileOp.HARDLINK:
            os.link(str(src), str(dst))
        else:
            raise ValueError(f"Unknown FileOp: {op}")

        # Best-effort post-op ownership/mode (Docker/NAS): apply the chmod/chown
        # spec to the renamed file + any dirs we created. SYMLINK skips the dst
        # (chmod-ing a link affects its target, not the link). Swallows errors
        # inside the helper so a perms failure never fails the rename.
        if permissions:
            if op != FileOp.SYMLINK:
                _apply_permissions(dst, permissions, is_dir=False)
            for _perm_dir in created_dirs:
                _apply_permissions(_perm_dir, permissions, is_dir=True)
    except Exception:
        # Operation failed AFTER we created destination dirs — roll them
        # back so the target volume doesn't accumulate orphan skeletons.
        # Best-effort: rmdir refuses non-empty dirs so this can't clobber
        # siblings that happened to live in the same path.
        _rmdir_tracked(created_dirs)
        raise

    return artifacts_cleaned


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


# Provenance log inside the trash root: one JSON line per trashed item
# ({name, original, at}), consumed by the trash API's list/restore endpoints.
TRASH_MANIFEST = ".kira-trash-manifest.jsonl"


def _move_to_trash(entry: Path, trash_root: Path) -> bool:
    """Move `entry` into Kira's managed trash folder instead of deleting it, so
    a cleanup sweep is recoverable from the user's file browser. Returns True on
    success; any OSError returns False so the caller behaves exactly as a failed
    delete would (leave the item, stop the walk).

    OS recycle bins are useless in a Docker container (the XDG trash is
    ephemeral and has no restore UI), so Kira keeps its own trash dir under the
    library. The trashed name is prefixed with the source folder so two
    `poster.jpg` files from different shows don't collide; a numeric suffix
    breaks any remaining tie."""
    try:
        trash_root.mkdir(parents=True, exist_ok=True)
        base = f"{entry.parent.name}__{entry.name}" if entry.parent.name else entry.name
        target = trash_root / base
        n = 1
        while target.exists():
            target = trash_root / f"{base}.{n}"
            n += 1
        original = str(entry)
        shutil.move(str(entry), str(target))
        # Provenance manifest — one JSON line per trashed item. The trashed
        # NAME is flattened (`<parent>__<name>`), so without this the original
        # location is lost and the trash UI can't offer "Restore". Best-effort:
        # a manifest write failure never fails the trash move itself (the item
        # is still recoverable by hand).
        try:
            import json as _json
            from datetime import datetime, timezone
            line = _json.dumps({
                "name": target.name,
                "original": original,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }, separators=(",", ":"))
            with open(trash_root / TRASH_MANIFEST, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass
        return True
    except OSError:
        return False


def _cleanup_media_server_artifacts(parent: Path, *, mode: str = "off",
                                    extra_names: frozenset[str] = frozenset(),
                                    extra_exts: frozenset[str] = frozenset(),
                                    trash_root: Path | None = None) -> int:
    """Delete known-safe Plex/Jellyfin/Kodi media-server artifacts from
    `parent` so the now-source-empty directory can actually be rmdir'd.

    Walks ONLY one level (no recursion into subdirs other than the
    artifact-dir whitelist). Returns the count of items deleted (files
    + top-level artifact directories combined; nested files inside
    artifact dirs are counted as 1 toward the parent dir).

    Never raises — best-effort cleanup. Permission errors, missing
    paths, locked files: all silently skipped. The next rmdir attempt
    will fail if anything's left, and the parent-walk stops naturally.

    What's KEPT:
      * Any file Kira didn't put on its artifact lists (user data,
        sidecars Kira already handled, unknown extensions, .nfo files
        per-episode that aren't on the show-level list, etc.)
      * Any directory not on `_ARTIFACT_DIRNAMES` — user content like
        Subs/, Extras/, Featurettes/, etc. stay intact.

    What's REMOVED:
      * `_ARTIFACT_FILENAMES` exact-name files (poster.jpg, tvshow.nfo,
        etc.) — case-insensitive
      * `_ARTIFACT_FILENAME_RE` regex matches (seasonNN-poster.jpg etc.)
      * `_ARTIFACT_DIRNAMES` directories (.actors, .metadata,
        extrafanart, extrathumbs, etc.) — recursively via rmtree
    """
    if not parent.exists() or not parent.is_dir():
        return 0
    try:
        entries = list(parent.iterdir())
    except OSError:
        return 0

    removed = 0
    for entry in entries:
        try:
            if entry.is_dir() and not entry.is_symlink():
                # Symlinked artifact dirs get unlinked, not recursed.
                # We never follow a symlink into who-knows-where.
                if _is_artifact_dir(entry.name):
                    if trash_root is not None:
                        if _move_to_trash(entry, trash_root):
                            removed += 1
                        continue
                    shutil.rmtree(str(entry), ignore_errors=True)
                    # rmtree with ignore_errors=True doesn't tell us if
                    # the dir is actually gone. Verify; only count on
                    # success so the user's "cleaned N artifacts" toast
                    # is honest.
                    if not entry.exists():
                        removed += 1
                continue
            if entry.is_file() or entry.is_symlink():
                if _is_removable_file(entry.name, mode=mode, extra_names=extra_names, extra_exts=extra_exts):
                    if trash_root is not None:
                        if _move_to_trash(entry, trash_root):
                            removed += 1
                        continue
                    try:
                        entry.unlink()
                        removed += 1
                    except OSError:
                        pass  # locked / permission denied — skip silently
        except OSError:
            # is_dir / is_file probe failed (broken symlink, race with
            # another process). Skip this entry, keep going.
            continue
    return removed


def _folder_cleanable(parent: Path, *, mode: str = "off",
                      extra_names: frozenset[str] = frozenset(),
                      extra_exts: frozenset[str] = frozenset()) -> bool:
    """Data-loss guard: may the cleanup TOUCH this folder at all?

    A subdirectory that isn't a known cache dir (Subs/, Extras/, Featurettes/…)
    ALWAYS blocks cleanup — we never recurse into or delete user content dirs.

    For files the bar depends on the aggressive-cleanup `mode`:
      'off'       → every file must be a recognized artifact (+ user extras),
                    i.e. the folder is purely media-server garbage (the strict,
                    safe default — a stray user file keeps the whole folder).
      'keep_subs'/'all' → only a VIDEO file blocks. The folder is fair game as
                    long as no video remains; the per-file delete decision
                    (`_is_removable_file`) then honors the mode (keep_subs leaves
                    subtitle sidecars, so the folder may survive the rmdir with
                    just subs in it — but its junk is still swept).

    Returns False on any inspection error → keep the folder (never delete on doubt)."""
    try:
        entries = list(parent.iterdir())
    except OSError:
        return False  # can't inspect → assume content, don't delete
    for entry in entries:
        try:
            if entry.is_dir() and not entry.is_symlink():
                if not _is_artifact_dir(entry.name):
                    return False
            elif entry.is_file() or entry.is_symlink():
                if mode == "off":
                    if not _is_artifact_file(entry.name, extra_names, extra_exts):
                        return False
                elif _is_media_file(entry.name):
                    return False  # never strip a folder that still holds content (video OR audio)
            else:
                return False  # unknown entry type → treat as content
        except OSError:
            return False  # classification failed → keep the folder
    return True


def _is_artifacts_only(parent: Path) -> bool:
    """Back-compat shim: the strict 'off'-mode form of `_folder_cleanable`
    (folder is nothing but recognized artifacts). Retained for external callers
    / tests that predate the mode-aware version."""
    return _folder_cleanable(parent, mode="off")


def _cleanup_empty_source_parents(
    start: Path,
    stop_at: Path | None,
    max_levels: int = 2,
    *,
    sweep_artifacts: bool = True,
    trash_root: Path | None = None,
    mode: str = "off",
    extra_names: frozenset[str] = frozenset(),
    extra_exts: frozenset[str] = frozenset(),
) -> int:
    """User-requested: after a Move, walk UP the source's parent chain
    and rmdir each ancestor that's now empty. Saves the user from manually
    cleaning leftover `Show/Season 01/` shells after every file has been
    moved out.

    Before each rmdir attempt at a given level, we sweep known Plex/
    Jellyfin/Kodi artifacts (`poster.jpg`, `tvshow.nfo`, `.actors/`,
    etc.) so the directory actually CAN become empty. Without this,
    the rmdir hits a folder with one `poster.jpg` in it, silently
    fails, and the walk-up stops — leaving dangling parent folders
    for every show + season the user touched.

    `sweep_artifacts=False` disables the artifact pre-sweep — the
    walk only rmdir's folders that are GENUINELY empty (no Plex/
    Jellyfin/Kodi files left). Useful when the user wants strict
    "don't touch anything that isn't already empty" semantics. The
    Settings UI exposes this as a sub-toggle of the master cleanup
    setting; default is True so the user gets clean folders out of
    the box.

    Safety:
      - Artifact sweep (when enabled) is allow-list only: only items
        on the curated `_ARTIFACT_FILENAMES` / `_ARTIFACT_DIRNAMES`
        lists are deleted. User content (random files, Subs/,
        Featurettes/) is untouched.
      - rmdir refuses non-empty directories — if anything not on the
        artifact list remains, the rmdir fails and the walk stops.
      - Stops at `stop_at` (typically the library root) so we never
        rmdir the user's media root itself.
      - Best-effort: every OSError silently breaks the loop.

    Returns the total count of artifact files / directories deleted
    across all levels walked, so callers can surface the number in
    the rename result toast ("Moved 1 file · cleaned 4 metadata
    artifacts"). Returns 0 when sweep_artifacts is False (no sweep
    means no count to report).

    `start` is the FIRST directory to attempt to remove (the file's
    immediate parent before the move). We walk parents upward.
    """
    if not start:
        return 0
    stop_abs = stop_at.resolve() if stop_at else None
    current = start
    total_artifacts_deleted = 0
    # Reasonable upper bound on walk depth so a misconfiguration doesn't
    # walk all the way to the drive root. The stop_at check is the real
    # guard; this is belt-and-braces.
    # Fix #7: per-call cap (default 2) — TV/anime layout is at most
    # `<library>/<Show>/<Season X>/<file>` = 2 levels of show+season
    # folders to clean up. Music is `<Artist>/<Album>/<track>` = 2.
    # Movies are flat or `<Movie>/<file>` = 1.
    for _ in range(max_levels):
        try:
            current_abs = current.resolve()
        except OSError:
            return total_artifacts_deleted
        # Never rmdir at or above the configured stop boundary.
        if stop_abs is not None:
            try:
                # If current is the same as or NOT strictly inside stop_at,
                # bail. .relative_to() raises if current isn't under stop_at.
                rel = current_abs.relative_to(stop_abs)
                # rel == Path('.') means current IS stop_at — also stop.
                if str(rel) in ('.', ''):
                    return total_artifacts_deleted
            except ValueError:
                # current is outside stop_at — definitely stop.
                return total_artifacts_deleted
        # DATA-LOSS GUARD: only sweep + remove a folder that is ENTIRELY
        # media-server artifacts (or already empty). If ANY real user content
        # remains, leave this folder — and everything in it (poster.jpg,
        # hand-authored tvshow.nfo, album cover.jpg, that stray personal
        # image) — completely untouched, and stop walking up. Previously the
        # sweep ran before this check, so a folder that ultimately SURVIVED
        # (rmdir failed) had already lost its artifacts. Computing
        # removability first means a deletion only ever happens for a folder
        # that is about to disappear.
        if not _folder_cleanable(current, mode=mode, extra_names=extra_names, extra_exts=extra_exts):
            return total_artifacts_deleted
        # Folder is cleanable (artifacts-only in 'off' mode, or no-video-left in
        # the aggressive modes). Now it's safe to clear the removable files so
        # the rmdir can succeed. With sweep disabled, we skip the delete; the
        # folder then still contains files, rmdir fails, and we stop — preserving
        # the strict "only genuinely-empty" semantics.
        if sweep_artifacts:
            total_artifacts_deleted += _cleanup_media_server_artifacts(
                current, mode=mode, extra_names=extra_names, extra_exts=extra_exts, trash_root=trash_root)
        try:
            current.rmdir()
        except OSError:
            # Non-empty (user content remains), missing, or permission
            # denied. Stop here; parents above are almost certainly
            # non-empty too. Return what we've cleaned so far so the
            # toast still credits the artifact sweep.
            return total_artifacts_deleted
        parent = current.parent
        if parent == current:
            return total_artifacts_deleted  # reached filesystem root
        current = parent
    return total_artifacts_deleted


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

    # Cross-device path. We stream-copy OURSELVES (not shutil.copy2) for two
    # reasons the old code got wrong and which risk deleting the only copy:
    #   1. fsync the WRITE handle before closing it. The previous code opened
    #      `dst` READ-only and fsync'd that — which flushes nothing the writer
    #      dirtied (copy2 already closed its own write fd), so on a network
    #      volume the "durability" guarantee was a no-op.
    #   2. Verify by CONTENT (hash), not size. The next step unlinks the
    #      source; a size-only check read through a network attribute cache
    #      can pass on a truncated/corrupt destination, destroying the original.
    # On any failure we remove the partial dst so the operation looks unstarted
    # and the source remains the canonical copy.
    import hashlib
    _CHUNK = 8 * 1024 * 1024
    _ensure_space(src, dst)          # cross-device move duplicates bytes — refuse if it won't fit
    try:
        h_src = hashlib.blake2b()
        with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
            while True:
                chunk = fsrc.read(_CHUNK)
                if not chunk:
                    break
                fdst.write(chunk)
                h_src.update(chunk)
            fdst.flush()
            os.fsync(fdst.fileno())   # commit the WRITE fd before it closes
        try:
            shutil.copystat(str(src), str(dst))  # mtime/mode like copy2 — best effort
        except OSError:
            pass
        # Re-read the destination from disk and compare content hashes.
        h_dst = hashlib.blake2b()
        with open(dst, "rb") as f:
            while True:
                chunk = f.read(_CHUNK)
                if not chunk:
                    break
                h_dst.update(chunk)
        if h_dst.digest() != h_src.digest():
            raise OSError(f"Cross-device copy verification failed (content mismatch): {src} -> {dst}")
        # Content confirmed durable at dst — only now is it safe to delete src.
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
        # Refuse if the original location is now OCCUPIED by a DIFFERENT file. The
        # cross-device copy path below opens `src` (old_path) with "wb" (truncate),
        # and same-FS `os.rename` overwrites on POSIX — so without this guard, undo
        # would SILENTLY destroy whatever the user placed at the old name after the
        # rename. Same-inode ⇒ it's literally the same file (hard link / already
        # restored) → safe to proceed.
        if src.exists() and not _same_inode(src, dst):
            raise FileExistsError(
                f"Cannot undo move — original location {src} is now occupied by a "
                f"different file; move or remove it first."
            )
        src.parent.mkdir(parents=True, exist_ok=True)
        # Use the SAME hardened move as the forward path: same-FS os.rename, else
        # cross-device copy → fsync → hash-verify → unlink, with rollback. Undo
        # runs on the ONLY remaining copy, so a plain shutil.move (unverified
        # copy+delete across the SMB boundary) could destroy it on a short read.
        _atomic_move(dst, src)
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
