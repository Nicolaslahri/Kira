"""Rename endpoint — executes the actual file operations."""

from __future__ import annotations

import logging

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from kira.config import settings
from kira.database import get_session
from kira.models import Match, MediaFile, Notification, RenameHistory, RenameIntent
from kira.parser import ParsedFile
from kira.renamer import (
    DEFAULT_PROFILES,
    FileOp,
    NamingProfile,
    compute_sidecar_target,
    discover_sidecars,
    execute_op,
    format_target_path,
    RenameSkipped,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rename", tags=["rename"])

# Artwork download bounds (defence against a hostile/oversized image host).
_ARTWORK_MAX_BYTES = 25 * 1024 * 1024   # 25 MiB — generous for an "original" backdrop
_IMG_CACHE_MAX_ENTRIES = 64             # cap the per-batch dedupe cache (FIFO eviction)


# ─────────────────────────────────────────────────────────────────────
# Live template preview (Tier 1.5 step 6 backend)
# ─────────────────────────────────────────────────────────────────────
# Renders user-supplied naming templates against the user's OWN recent
# matched files using the REAL engine (format_target_path → same render +
# _safe + specials-routing pipeline a real rename runs). This makes the
# backend the single source of truth for the Settings → Naming live preview,
# so the displayed path can't drift from what a rename would actually write.
# Read-only: computes paths under a throwaway root and never touches disk.


class TemplatePreviewRequest(BaseModel):
    """The 4 per-type templates being edited. Any omitted type falls back to
    the Plex default so a partial edit still previews."""
    movie: str | None = None
    tv: str | None = None
    anime: str | None = None
    music: str | None = None
    samples_per_type: int = 3


class PreviewSample(BaseModel):
    media_type: str
    filename: str
    rendered: str            # relative path the template produces (posix)
    error: str | None = None


class TemplatePreviewResponse(BaseModel):
    samples: list[PreviewSample]


def _relativize_preview(target: Path, preview_root: str) -> str:
    """Strip the abstract preview root so the UI shows a clean relative path
    (e.g. `TV/Breaking Bad (2008)/Season 01/…`)."""
    try:
        return target.relative_to(Path(preview_root).resolve()).as_posix()
    except ValueError:
        return target.as_posix()


@router.post("/preview-template", response_model=TemplatePreviewResponse)
async def preview_template(
    payload: TemplatePreviewRequest,
    session: AsyncSession = Depends(get_session),
) -> TemplatePreviewResponse:
    """Render the supplied templates against recent matched files (per type)."""
    base = DEFAULT_PROFILES["Plex"]
    profile = NamingProfile(
        movie=payload.movie or base.movie,
        tv=payload.tv or base.tv,
        anime=payload.anime or base.anime,
        music=payload.music or base.music,
    )
    per_type = max(1, min(payload.samples_per_type or 3, 10))
    preview_root = "__kira_preview__"
    samples: list[PreviewSample] = []

    for mtype in ("movie", "tv", "anime", "music"):
        rows = (await session.execute(
            select(MediaFile)
            .options(selectinload(MediaFile.matches))
            .where(MediaFile.media_type == mtype)
            .order_by(MediaFile.id.desc())
            .limit(40)
        )).scalars().all()
        picked = 0
        for f in rows:
            if picked >= per_type:
                break
            if not f.parsed_data:
                continue
            selected = next((m for m in f.matches if m.is_selected), None) \
                or (f.matches[0] if f.matches else None)
            if not selected or not selected.provider or not selected.provider_id:
                continue
            picked += 1
            fname = Path(f.file_path).name  # ORM stores file_path, not filename
            try:
                fields = {
                    k: v for k, v in f.parsed_data.items()
                    if k in ParsedFile.__dataclass_fields__
                }
                parsed = ParsedFile(**fields)
                meta = dict(getattr(selected, "metadata_blob", None) or {})
                prov = (selected.provider or "").lower()
                if selected.provider_id:
                    if prov == "tmdb":
                        meta.setdefault("tmdbid", selected.provider_id)
                    elif prov == "tvdb":
                        meta.setdefault("tvdbid", selected.provider_id)
                    elif prov == "anidb":
                        meta.setdefault("anidbid", selected.provider_id)
                target = format_target_path(
                    parsed, preview_root, profile,
                    library_title=selected.title or parsed.title,
                    library_year=(selected.year if selected.year is not None else parsed.year),
                    episode_title=selected.episode_title,
                    season_override=selected.season_number,
                    metadata=meta,
                    file_size=f.file_size,
                )
                samples.append(PreviewSample(
                    media_type=mtype, filename=fname,
                    rendered=_relativize_preview(target, preview_root),
                ))
            except Exception as e:
                samples.append(PreviewSample(
                    media_type=mtype, filename=fname, rendered="", error=str(e),
                ))
    return TemplatePreviewResponse(samples=samples)


def _filesystem_reachable(src: Path) -> bool:
    """EE-4: True iff the filesystem hosting `src` is alive enough to give
    a truthful exists()/stat() answer about paths beneath it.

    The phantom-rename branch trusts `Path.exists()` returning False to
    mean 'file is gone'. But on Windows, an unmounted NAS makes EVERY
    path on that drive return False — including paths whose underlying
    files are perfectly fine. Without this check, a 45-second NAS blip
    during a 30-file rename batch silently marks every file as 'renamed'
    even though nothing moved. The user only notices weeks later when
    the DB has drifted irrecoverably from the actual filesystem.

    Strategy: probe the DEEPEST EXISTING ancestor of `src` and confirm it's
    actually listable. Iterating a live directory close to the file is a
    truthful "this subtree is responsive" signal — the old drive-root probe
    missed the case where a nested mount drops (e.g. `Z:\media` is a junction
    to a down NAS) while the system drive `Z:\` stays up and happily lists. A
    degraded mount can also answer exists()==True for its reparse point yet
    raise on iterdir; the listability check catches that too.

    Walking up terminates at the volume root (`parent == probe`), which works
    uniformly for Windows drives, UNC shares, and POSIX paths. If nothing in
    the chain is reachable, the mount is down and no exists()==False answer
    about files beneath it can be trusted.
    """
    try:
        probe = src.parent
        while True:
            try:
                here = probe.exists()
            except OSError:
                return False
            if here:
                # exists() said yes — confirm the directory truly responds.
                it = iter(probe.iterdir())  # OSError → outer except → False
                try:
                    next(it)
                except StopIteration:
                    pass  # empty but live — fine
                return True
            parent = probe.parent
            if parent == probe:
                return False  # reached the volume root; nothing existed
            probe = parent
    except OSError:
        return False


class RenameRequest(BaseModel):
    file_ids: list[int]
    profile: str = "Plex"             # Plex | Jellyfin | Kodi (or saved custom)
    op: str = "hardlink"              # move | copy | symlink | hardlink
    # PB-3 (security): replaced raw `library_root: str | None` with a
    # NAMED reference into the server-side `paths.library_roots` dict.
    # The previous design let any unauthenticated LAN caller POST
    # `library_root="/etc"` and have the server write files there —
    # arbitrary-path file write on an auth-less API. Now the client can
    # only pick from an admin-configured set; the server has the full
    # mapping of name → absolute path.
    library_root_name: str | None = None
    dry_run: bool = False
    overwrite: bool = False


class RenameItemResult(BaseModel):
    file_id: int
    ok: bool
    old_path: str | None = None
    new_path: str | None = None
    error: str | None = None
    # #6: dry-run side-effect preview. Populated only for dry_run items so the UI
    # can show the FULL footprint of a rename before it runs. All optional/null
    # on a real run → existing consumers are unaffected.
    sidecars: list[str] | None = None   # sidecar filenames that would move with the video
    nfo: list[str] | None = None        # .nfo filenames that would be written
    artwork: list[str] | None = None    # artwork kinds that would be downloaded


class RenameResult(BaseModel):
    succeeded: int
    failed: int
    items: list[RenameItemResult]


async def _resolve_profile(session: AsyncSession, name: str) -> NamingProfile:
    """Return the named profile — built-in or user-saved override."""
    if name in DEFAULT_PROFILES:
        return DEFAULT_PROFILES[name]
    # Check settings table for a custom profile.
    from kira.models import Setting
    row = await session.get(Setting, f"naming.custom.{name}")
    if row and isinstance(row.value, dict):
        return NamingProfile(
            movie=row.value.get("movie", DEFAULT_PROFILES["Plex"].movie),
            tv=row.value.get("tv", DEFAULT_PROFILES["Plex"].tv),
            anime=row.value.get("anime", DEFAULT_PROFILES["Plex"].anime),
            music=row.value.get("music", DEFAULT_PROFILES["Plex"].music),
            # Optional: a custom profile may define its own absolute-numbering
            # anime variant. Absent → select_template falls back to `anime`.
            anime_absolute=row.value.get("anime_absolute"),
        )
    return DEFAULT_PROFILES["Plex"]


def _compute_inplace_target_root(
    src: Path,
    parsed: ParsedFile,
    profile,
    library_title: str | None,
    library_year: int | None,
    episode_title: str | None,
    season_override: int | None,
) -> str:
    """In-place rename: pick a target_root that keeps the file at its
    current depth on disk.

    Strategy: render the template once to count its path components.
    Walk up from the source file by that many parents — that's the
    folder the file's RENAMED show folder needs to live inside, so
    when we append the rendered template to it, the result sits at
    the same depth the source did.

    Example:
        src = Z:\\media\\tv\\Euphoria (US)\\Season 1\\old-name.mkv
        template = "Euphoria (US) (2019)/Season 01/Euphoria (US) - S01E01 ...mkv"
        template_depth = 3 (3 path components)
        Walk up 3 from src: file → Season 1 → Euphoria (US) → tv
        target_root = Z:\\media\\tv
        new path = Z:\\media\\tv\\Euphoria (US) (2019)\\Season 01\\Euphoria (US) - S01E01 ...mkv
                  └── original parent of show folder is preserved ──┘
    """
    from kira.renamer.templates import apply_template, _build_ctx, SUBFOLDER
    template = getattr(profile, parsed.media_type, profile.movie)
    ctx = _build_ctx(
        parsed,
        library_title or parsed.title or "",
        library_year,
        episode_title=episode_title,
        season_override=season_override,
    )
    rendered = apply_template(template, ctx)
    template_parts = [p for p in rendered.split("/") if p]
    # Walk up from src by len(template_parts) levels. src.parent is one
    # level up (the immediate folder), src.parent.parent is two, etc.
    current = src
    for _ in range(len(template_parts)):
        parent = current.parent
        if parent == current:
            # Hit filesystem root before we walked far enough. The
            # template is deeper than the source's path. Bail —
            # caller will fall back to library_root + subfolder.
            return str(src.parent)
        current = parent

    # Fix #6: sanity check — if we walked PAST the recognized type
    # folder (movies / tv / anime / music), back off one level. This
    # protects flat-file movies and folder-less TV from being relocated
    # OUTSIDE their type folder. Example: `Z:\media\movies\Inception.mkv`
    # with a 2-part template walks 2 up → `Z:\media`. Without the check,
    # the rename would create `Z:\media\Inception (2010)\Inception (2010).mkv`
    # — Inception leaves the `movies` folder. With the check, we detect
    # that the source has a recognized type-folder ancestor (`movies`)
    # closer to the file than the walked-up target, and use that ancestor
    # instead.
    type_folder_names = {v.lower() for v in SUBFOLDER.values()}
    # Also accept lowercase variants users may have (`tv` vs `TV`, etc.)
    type_folder_names |= {v.lower() for v in ("tv", "anime", "movies", "music", "films", "shows")}
    # Walk back UP from `current` toward `src` looking for a type folder.
    # If src has a type-folder ancestor that's BELOW (closer to src than)
    # `current`, prefer that ancestor as the target_root so renamed
    # content stays inside the type folder.
    src_ancestors = list(src.parents)  # [parent, parent.parent, ...]
    try:
        current_resolved = current.resolve()
    except OSError:
        current_resolved = current
    for ancestor in src_ancestors:
        try:
            ancestor_resolved = ancestor.resolve()
        except OSError:
            ancestor_resolved = ancestor
        if ancestor_resolved == current_resolved:
            break  # reached the walked-up root; no closer type folder found
        if ancestor.name.lower() in type_folder_names:
            # Found a recognized type folder between src and current.
            # Use this as the target_root instead — keeps the rename
            # inside the type folder it currently lives in.
            return str(ancestor)
    return str(current)


async def _resolve_rename_mode(session: AsyncSession) -> str:
    """Setting: 'in-place' (default) | 'move-to-library'.

    in-place — file stays inside its current parent-of-show level. Show
    folder and Season folder may be RENAMED to match the template, but
    the file doesn't relocate to a different library root. This is what
    most users actually want (Plex / Jellyfin scanners are happy as
    long as the show / season / episode names match conventions, no
    matter where on disk they live).

    move-to-library — legacy behavior. Files relocate to
    `<library_root>/<Type subfolder>/<template output>`. Useful when
    the user wants Kira to BUILD a fresh library structure from
    scattered source files.

    Default changed to 'in-place' after data-loss incidents: the
    move-to-library default + same-template-target combo could
    silently re-rename files that were already correctly named,
    occasionally hitting edge cases that destroyed data. In-place is
    the conservative default; users who want library-rebuild semantics
    must opt in.
    """
    from kira.models import Setting
    from kira.settings_store import unwrap
    row = await session.get(Setting, "rename.mode")
    if not row:
        return "in-place"
    v = unwrap(row.value)
    # Clamp to the known enum. An unrecognized/garbage value must NOT slip into
    # the mode selector that feeds the (data-loss-historied) rename pipeline —
    # fall back to the conservative default, same as _resolve_cleanup_nonvideo.
    if isinstance(v, str) and v.strip() in ("in-place", "move-to-library"):
        return v.strip()
    return "in-place"


async def _resolve_cleanup_empty_dirs(session: AsyncSession) -> bool:
    """Setting toggle: clean up empty source folders after a Move?

    Default TRUE — and CRUCIALLY this now matches the Settings UI, which has always
    shown "Remove empty folders after Move" as ON by default. The previous backend
    default of FALSE silently disagreed with that toggle, so a user who renamed a
    relocating batch (e.g. anime cours moving from `Bleach/Season 17/` to
    `Bleach - Thousand-Year Blood War/Season 0X/`) saw the cleanup toggle ON yet
    found the emptied source folders left behind — and was never prompted.

    Safe to default on now: the original default-off was a reaction to the walker
    rmdir'ing up to 6 levels of ancestors (wiping folders users wanted to keep) and
    deleting artifacts from folders that ultimately survived. Both were fixed — the
    walk is depth-capped per media type (movie 1 / tv+anime 2) and computes
    removability FIRST, so it only ever touches a folder that is empty or entirely
    media-server artifacts and is about to be removed. Users can still opt OUT.
    """
    from kira.models import Setting
    row = await session.get(Setting, "rename.cleanup_empty_source_dirs")
    if not row:
        return True  # matches the UI's default-ON toggle
    val = row.value
    if isinstance(val, bool):
        return val
    if isinstance(val, dict):
        v = val.get("value")
        if isinstance(v, bool):
            return v
    return True


async def _resolve_cleanup_artifacts(session: AsyncSession) -> bool:
    """Setting sub-toggle: when cleaning up empty source folders, also
    sweep Plex/Jellyfin/Kodi metadata artifacts (poster.jpg, *-thumb.jpg,
    tvshow.nfo, .actors/, etc.) so the folders ACTUALLY become empty
    and can be rmdir'd. Default TRUE — the whole point of folder
    cleanup is to leave the source tidy; an artifact-only folder is
    still "garbage" from the user's perspective.

    Setting key: `rename.cleanup_media_server_artifacts`. Ignored when
    the master `cleanup_empty_source_dirs` toggle is off (no cleanup
    walk happens at all in that case).

    Returns True (default) when the setting is missing, so a fresh
    install gets the friendlier behaviour out of the box.
    """
    from kira.models import Setting
    row = await session.get(Setting, "rename.cleanup_media_server_artifacts")
    if not row:
        return True  # friendly default — user can opt OUT if they want
    val = row.value
    if isinstance(val, bool):
        return val
    if isinstance(val, dict):
        v = val.get("value")
        if isinstance(v, bool):
            return v
    return True


async def _resolve_cleanup_extra_names(session: AsyncSession) -> frozenset[str]:
    """User-added filenames to ALSO treat as deletable during folder cleanup
    (`rename.cleanup_extra_filenames`, a list). Lowercased; empty when unset.
    Merged into the built-in artifact set — "delete these too"."""
    from kira.models import Setting
    row = await session.get(Setting, "rename.cleanup_extra_filenames")
    val = row.value if row else None
    if isinstance(val, dict):
        val = val.get("value")
    if isinstance(val, list):
        return frozenset(s.strip().lower() for s in val if isinstance(s, str) and s.strip())
    return frozenset()


async def _resolve_cleanup_extra_exts(session: AsyncSession) -> frozenset[str]:
    """User-added file EXTENSIONS to ALSO sweep (`rename.cleanup_extra_extensions`,
    a list). Normalized to lowercase, dot-led (`txt`/`*.txt` → `.txt`)."""
    from kira.models import Setting
    row = await session.get(Setting, "rename.cleanup_extra_extensions")
    val = row.value if row else None
    if isinstance(val, dict):
        val = val.get("value")
    if not isinstance(val, list):
        return frozenset()
    out: set[str] = set()
    for s in val:
        if isinstance(s, str) and s.strip():
            e = s.strip().lower().lstrip("*")
            out.add(e if e.startswith(".") else "." + e)
    return frozenset(out)


async def _resolve_cleanup_nonvideo(session: AsyncSession) -> str:
    """Aggressive cleanup mode (`rename.cleanup_nonvideo`): when emptying a
    moved-from source folder, how much NON-video content to delete —
      'off'       → recognized artifacts only (default, safest);
      'keep_subs' → all non-video files except subtitle sidecars;
      'all'       → every non-video file.
    Only ever acts on a folder with no videos left; deletions honor the Trash
    setting (`rename.cleanup_trash`). Invalid/missing → 'off'."""
    from kira.models import Setting
    row = await session.get(Setting, "rename.cleanup_nonvideo")
    val = row.value if row else None
    if isinstance(val, dict):
        val = val.get("value")
    return val if val in ("off", "keep_subs", "all") else "off"


async def _resolve_bool_setting(session: AsyncSession, key: str, default: bool) -> bool:
    """Read a boolean setting that may be stored bare or wrapped in {"value": …}.
    Used by the Pass 7 output toggles (write_nfo, download_artwork)."""
    from kira.models import Setting
    row = await session.get(Setting, key)
    if not row:
        return default
    val = row.value
    if isinstance(val, bool):
        return val
    if isinstance(val, dict):
        v = val.get("value")
        if isinstance(v, bool):
            return v
    return default


async def _resolve_permissions(session: AsyncSession) -> dict | None:
    """Post-rename chmod/chown spec, or None when disabled. The
    `rename.set_permissions` master toggle gates `rename.file_mode` /
    `rename.dir_mode` (octal strings, e.g. "644"/"755") + `rename.owner_uid` /
    `rename.owner_gid` (ints). Applied best-effort by execute_op — chown is
    Unix-only, so on Windows the mode/uid simply no-op."""
    if not await _resolve_bool_setting(session, "rename.set_permissions", False):
        return None
    from kira.models import Setting

    async def _raw(key: str):
        row = await session.get(Setting, key)
        if not row:
            return None
        v = row.value
        return v.get("value") if isinstance(v, dict) else v

    def _mode(v: object) -> str | None:
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, int) and not isinstance(v, bool):
            return str(v)
        return None

    def _id(v: object) -> int | None:
        if isinstance(v, bool):
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.strip().lstrip("-").isdigit():
            return int(v.strip())
        return None

    return {
        "file_mode": _mode(await _raw("rename.file_mode")),
        "dir_mode": _mode(await _raw("rename.dir_mode")),
        "uid": _id(await _raw("rename.owner_uid")),
        "gid": _id(await _raw("rename.owner_gid")),
    }


async def _resolve_str_setting(session: AsyncSession, key: str, default: str) -> str:
    """Read a string setting that may be stored bare or wrapped in {"value": …}.
    Used by the anime episode-numbering style toggle (seasonal | absolute)."""
    from kira.models import Setting
    row = await session.get(Setting, key)
    if not row:
        return default
    val = row.value
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        v = val.get("value")
        if isinstance(v, str):
            return v
    return default


async def _resolve_int_setting(session: AsyncSession, key: str, default: int, *, lo: int, hi: int) -> int:
    """Read an int setting (bare or wrapped, possibly a numeric string), clamped
    to [lo, hi]. Used by the concurrency knob (Settings → Advanced)."""
    from kira.models import Setting
    row = await session.get(Setting, key)
    if not row:
        return default
    val = row.value
    if isinstance(val, dict):
        val = val.get("value")
    try:
        n = int(val)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


async def _resolve_nfo_fields(session: AsyncSession) -> set[str] | None:
    """Which optional NFO fields the user enabled (Settings → Naming).

    Stored as a bare dict under `naming.nfo_fields`, e.g. `{"cast": false}`.
    A field absent from the dict defaults to ON, so:
      - unset / empty   → None (write everything — the default behaviour)
      - `{"cast": false}` → every field except cast
    Returns the SET of enabled keys, or None for "all on" (back-compat)."""
    from kira.models import Setting
    from kira.renamer.nfo import NFO_TOGGLEABLE
    row = await session.get(Setting, "naming.nfo_fields")
    val = row.value if row else None
    # Defensive unwrap in case a writer wrapped it as {"value": {...}}.
    if isinstance(val, dict) and isinstance(val.get("value"), dict):
        val = val["value"]
    if not isinstance(val, dict) or not val:
        return None
    return {k for k in NFO_TOGGLEABLE if val.get(k, True)}


async def _write_nfo_files(target: Path, parsed: ParsedFile, selected: Match, meta: dict | None,
                           fields: set[str] | None = None, season_override: int | None = None,
                           series_name_override: str | None = None,
                           season_posters: dict[int, str] | None = None) -> list[str]:
    """#12: write Kodi/Emby .nfo sidecars beside the renamed video. Best-effort
    output from data already on the Match — no API calls. `fields` selects which
    optional tags to include (None = all).

    Returns the per-file NFO paths written (movie / episode) so the caller can
    record them for authoritative undo. The shared `tvshow.nfo` is deliberately
    EXCLUDED — it's one-per-series and other episodes depend on it, so undoing a
    single episode must never delete it."""
    from kira.renamer import nfo
    written: list[str] = []
    def _atomic_write_text(p: Path, text: str) -> None:
        # Atomic: temp sibling + os.replace, so a crash / disk-full / NAS drop
        # mid-write never leaves a TRUNCATED NFO on disk. Media servers trust the
        # NFO over the filename, so a torn write is permanent wrong/empty metadata
        # until the next rename. Mirrors the artwork path's _atomic_write.
        _tmp = p.with_name(p.name + ".kira-nfo-tmp")
        try:
            _tmp.write_text(text, encoding="utf-8")
            os.replace(_tmp, p)
        except Exception:
            try:
                if _tmp.exists():
                    _tmp.unlink()
            except OSError:
                pass
            raise
    plan = nfo.plan_nfo_writes(target, parsed.media_type)
    if not plan:
        return written
    meta = meta or {}
    # The Match column always carries the chosen poster; older metadata_blobs
    # may predate the poster_url key. Backfill it so the NFO <thumb> is written.
    if not meta.get("poster_url") and selected.poster_url:
        meta = {**meta, "poster_url": selected.poster_url}
    prov, pid = selected.provider, selected.provider_id
    title = selected.title or parsed.title
    # The SHOW name for <showtitle> and the tvshow <title>. Prefer the unified
    # folder title the rename rendered into the PATH (`series_name_override` =
    # the loop's `library_title`). AniDB gives every cour its own title
    # ("Attack on Titan Season 2"), so `selected.series_name` would stamp the
    # cour qualifier into <showtitle> — splitting the show in Plex/Jellyfin from
    # the franchise-unified folder ("Attack on Titan"). The NFO must name the
    # SAME show the folder does, so the override (when present) always wins.
    show_name = series_name_override or selected.series_name or title
    year = selected.year if selected.year is not None else parsed.year
    # The file's own container/tech data → Kodi <fileinfo><streamdetails>. From
    # the filename strip (codec/quality) + MediaInfo when `parsing.read_mediainfo`
    # is on (hdr/channels/audio/duration). Emits only what's known.
    tech = {
        "codec": parsed.codec, "quality": parsed.quality, "hdr": parsed.hdr,
        "channels": parsed.channels, "audio": parsed.audio,
        "duration": getattr(parsed, "duration", None),
        # Per-track languages (MediaInfo) → one <audio>/<subtitle> each in the NFO.
        "audio_langs": getattr(parsed, "audio_langs", None) or [],
        "sub_langs": getattr(parsed, "sub_langs", None) or [],
    }
    if "movie" in plan:
        content = nfo.build_movie_nfo(title, year, meta, prov, pid, fields=fields, tech=tech)
        await asyncio.to_thread(_atomic_write_text, plan["movie"], content)
        written.append(str(plan["movie"]))
    if "episode" in plan:
        # Mirror the FILENAME's season EXACTLY. `season_override` is the value the
        # rename loop rendered into the path — the cour-mapped season for anime
        # (ScudLee), else Match.season_number / parsed. Without it the NFO copied
        # the raw Match.season_number, which can be a stale AniDB-degenerate "1"
        # from a scan done while AniDB was banned: the file landed in S23 but its
        # NFO said <season>1</season>, and Jellyfin trusts the NFO.
        season = season_override if season_override is not None else selected.season_number
        # Season-0 guard: a season-0 tv_episode match over a real parsed positive
        # season is an AniDB-no-season artifact, not a special — mirror parsed.
        if (season in (0, None) and selected.match_type == "tv_episode"
                and isinstance(parsed.season, int) and parsed.season > 0):
            season = parsed.season
        # MUST mirror the rendered FILENAME, not the raw Match row. By the time
        # we run, the caller has already baked the final season-continuous
        # number into the local `parsed` (the cour-offset block rewrites
        # parsed.episode = Match.episode_number + cour offset). Preferring
        # Match.episode_number here re-introduced the cour-LOCAL number: the
        # file said "S03E13 - The Town Where Everything Began" while its NFO
        # said <episode>1</episode> — and Jellyfin trusts the NFO, so every
        # multi-cour season showed two "episode 1"s, two "episode 2"s, etc.
        episode = parsed.episode if parsed.episode is not None else selected.episode_number
        # Resolve the per-episode entry (title / plot / aired) from the provider's
        # episode list. AniDB anime carries no per-episode titles, so without this
        # the NFO had no <title> (and the filename fell back to "Episode NN").
        # Best-effort + cached per series/season; a miss just leaves the lean NFO.
        ep_title, ep_plot, ep_aired = selected.episode_title, None, None
        try:
            from kira.api.series import resolve_episode_meta
            from kira.matcher.engine import registry_from_settings
            from kira import net
            _client = net.shared_client()
            _ep = await resolve_episode_meta(
                selected, season, episode, await registry_from_settings(_client), _client)
            if _ep:
                ep_title = ep_title or _ep.title
                ep_plot, ep_aired = _ep.overview, _ep.air_date
        except Exception:
            pass
        content = nfo.build_episode_nfo(ep_title, season, episode, meta,
                                        series_name=show_name, fields=fields, tech=tech,
                                        plot=ep_plot, aired=ep_aired)
        await asyncio.to_thread(_atomic_write_text, plan["episode"], content)
        written.append(str(plan["episode"]))
    if "tvshow" in plan:
        tv_path = plan["tvshow"]
        # Write-if-absent — one tvshow.nfo per series, not per episode. NOT
        # tracked in `written`: it's shared across the series, so undoing one
        # episode must not delete it.
        if not tv_path.exists():
            content = nfo.build_tvshow_nfo(show_name, year, meta, prov, pid, fields=fields,
                                           season_posters=season_posters)
            await asyncio.to_thread(_atomic_write_text, tv_path, content)
    return written


# Default per-kind enablement for artwork download. Poster + background +
# clearlogo are the everyday set (clearlogo is the headline fanart.tv type most
# Plex/Jellyfin/Kodi skins use); the rest are opt-in via Settings → Naming.
_ARTWORK_DEFAULTS: dict[str, bool] = {
    "poster": True, "fanart": True, "clearlogo": True,
    "clearart": False, "banner": False, "landscape": False,
    "disc": False, "characterart": False,
}


async def _resolve_artwork_kinds(session: AsyncSession) -> set[str]:
    """Which artwork kinds to download (Settings → Naming). Stored as a bare
    dict `{kind: bool}` under `naming.artwork_types`; a kind absent falls back to
    its default. Unset → the default set (poster + fanart + clearlogo)."""
    from kira.models import Setting
    from kira.providers.fanarttv import ALL_KINDS
    row = await session.get(Setting, "naming.artwork_types")
    val = row.value if row else None
    if isinstance(val, dict) and isinstance(val.get("value"), dict):
        val = val["value"]
    if not isinstance(val, dict):
        val = {}
    return {k for k in ALL_KINDS if val.get(k, _ARTWORK_DEFAULTS.get(k, False))}


async def _resolve_artwork_ids(selected: Match, meta: dict, media_type: str):
    """(tmdb_id, tvdb_id, imdb_id) for fanart.tv lookups, from the match + its
    metadata blob. fanart.tv keys movies by TMDB/IMDb and TV by TheTVDB; anime
    (matched to AniDB) resolves its TVDB id via the Fribb cross-ref so it can use
    the /tv endpoint too. Returns string ids (or None)."""
    tmdb_id = tvdb_id = imdb_id = None
    prov, pid = selected.provider, selected.provider_id
    if prov == "tmdb":
        tmdb_id = pid
    elif prov == "tvdb":
        tvdb_id = pid
    elif prov == "anidb":
        try:
            from kira.providers.anime_mappings import AnimeMappings
            tvdb_id = await AnimeMappings.tvdb_id(int(pid))
        except Exception:
            tvdb_id = None
    # Cross-ref ids the provider may have attached to the metadata blob.
    tmdb_id = tmdb_id or meta.get("tmdb_id")
    tvdb_id = tvdb_id or meta.get("tvdb_id")
    imdb_id = meta.get("imdbid") or meta.get("imdb_id")
    return (
        str(tmdb_id) if tmdb_id else None,
        str(tvdb_id) if tvdb_id else None,
        str(imdb_id) if imdb_id else None,
    )


async def _download_artwork_files(
    target: Path, parsed: ParsedFile, selected: Match, meta: dict | None,
    *,
    client: "httpx.AsyncClient | None" = None,
    kinds: set[str] | None = None,
    fanart_key: str = "",
    fanart_client_key: str = "",
    languages: list[str] | None = None,
    fanart_cache: dict | None = None,
    img_cache: dict | None = None,
) -> list[str]:
    """#13: download artwork beside the renamed video (Plex/Kodi local-asset
    convention `<stem>-<kind>.<ext>`). Poster + background come from the matched
    provider (TMDB/TVDB/AniDB); the richer kinds (clearlogo, clearart, banner,
    disc, character art) come from fanart.tv when a key is configured. Best-
    effort, write-if-absent — a slow/down image host never affects the rename.
    `fanart_cache` (a dict shared across the batch) caches the single fanart.tv
    response per series id so a 24-episode season makes ONE API call.

    CR-06: `client` is a SINGLE httpx.AsyncClient owned by the batch scope
    (`perform_rename`) and threaded through here so the fanart.tv lookup AND the
    image-byte fetches reuse one connection pool across the whole season's
    episodes — instead of opening (and tearing down) two fresh clients per file.
    Mirrors the shared `_sc` subtitle client. Per-request timeouts (15s fanart /
    20s image) are applied at the call sites so those budgets are preserved.
    When `client` is None (e.g. a direct unit-test call) the function opens its
    own short-lived client for the duration, so it stays self-contained.
    """
    import httpx
    meta = meta or {}
    if kinds is None:
        kinds = {"poster", "fanart"}   # back-compat default when caller omits
    if not kinds:
        return []

    # Resolve the working client: reuse the batch-shared one, or open a local
    # fallback (closed on exit) when called standalone. The fallback is entered
    # lazily so the no-op/empty-jobs early returns don't open a connection.
    from contextlib import AsyncExitStack
    async with AsyncExitStack() as _stack:
        if client is None:
            client = await _stack.enter_async_context(
                httpx.AsyncClient(timeout=20.0, follow_redirects=True)
            )
        return await _download_artwork_with_client(
            target, parsed, selected, meta, client,
            kinds=kinds, fanart_key=fanart_key,
            fanart_client_key=fanart_client_key, languages=languages,
            fanart_cache=fanart_cache, img_cache=img_cache,
        )


async def _download_artwork_with_client(
    target: Path, parsed: ParsedFile, selected: Match, meta: dict,
    client: "httpx.AsyncClient",
    *,
    kinds: set[str],
    fanart_key: str = "",
    fanart_client_key: str = "",
    languages: list[str] | None = None,
    fanart_cache: dict | None = None,
    img_cache: dict | None = None,
) -> list[str]:
    """Inner artwork worker that operates on an ALREADY-RESOLVED client (the
    batch-shared one, or the local fallback `_download_artwork_files` opened).
    Split out so the client lifetime is managed in exactly one place.

    Returns the artwork paths actually written this call (skipped/already-present
    files are not included) so the caller can record them for authoritative undo."""
    media_type = parsed.media_type or "movie"
    fanart_for = "movie" if media_type == "movie" else "tv"

    # fanart.tv (logos / clear art / banner / disc / character art + higher-res
    # poster+background). One request per series id, cached across the batch.
    fanart_urls: dict[str, str] = {}
    if fanart_key:
        tmdb_id, tvdb_id, imdb_id = await _resolve_artwork_ids(selected, meta, media_type)
        cache_key = (fanart_for, tmdb_id, tvdb_id, imdb_id)
        if fanart_cache is not None and cache_key in fanart_cache:
            fanart_urls = fanart_cache[cache_key]
        elif tmdb_id or tvdb_id or imdb_id:
            from kira.providers import fanarttv
            # Reuse the batch-shared client; apply the fanart lookup's 15s
            # budget per-request so connections pool across episodes.
            fanart_urls = await fanarttv.fetch_artwork(
                media_type=media_type, client=client, api_key=fanart_key,
                tmdb_id=tmdb_id, tvdb_id=tvdb_id, imdb_id=imdb_id,
                client_key=fanart_client_key or None, languages=languages,
                wanted=kinds,
            )
            if fanart_cache is not None:
                fanart_cache[cache_key] = fanart_urls

    from kira.providers.fanarttv import EXT_FOR_KIND
    # Per-kind source: fanart.tv first; the matched provider backstops poster +
    # background (so the toggle still produces art with no fanart.tv key).
    provider_poster = selected.poster_url or meta.get("poster_url")
    provider_fanart = meta.get("fanart_url") or meta.get("backdrop_url")
    # Episode artwork is SHOW-level (poster/fanart/clearlogo describe the series,
    # not a single episode), so write it ONCE into the series root under the
    # canonical media-server name (poster.jpg, fanart.jpg, …) where Plex/Jellyfin/
    # Kodi actually read it — instead of duplicating the show poster as
    # `<episode-stem>-poster.jpg` beside EVERY episode (wasteful, and episode files
    # want a -thumb still, not the show poster). Movies keep the per-file
    # `<stem>-<kind>` local-asset convention.
    from kira.renamer.nfo import series_root_for
    _is_episode = media_type in ("tv", "anime")
    _show_root = series_root_for(target) if _is_episode else None
    # Per-cour SEASON poster. AniDB gives every cour its OWN cover, and the
    # rename unifies cours into one show with seasons — so the single show-root
    # `poster.jpg` (write-if-absent) only ever captures whichever cour renamed
    # first. Write each cour's own poster as `Season NN/poster.jpg` (where
    # Plex/Jellyfin/Kodi read the season poster) so every season shows its real
    # art. ANIME only (regular-TV seasons share the one show poster — a per-
    # season copy would just duplicate it), and only in seasonal layout
    # (absolute/flat numbering has no Season folder → `target.parent` IS the
    # show root).
    _season_dir = (
        target.parent
        if (media_type == "anime" and _show_root is not None and target.parent != _show_root)
        else None
    )
    jobs: list[tuple[str, Path, bool]] = []   # (url, dest, shared_show_art)
    for kind in kinds:
        url = fanart_urls.get(kind)
        if not url and kind == "poster":
            url = provider_poster
        if not url and kind == "fanart":
            url = provider_fanart
        if not url:
            continue
        ext = EXT_FOR_KIND.get(kind, "jpg")
        if _is_episode:
            jobs.append((url, _show_root / f"{kind}.{ext}", True))
        else:
            jobs.append((url, target.with_name(f"{target.stem}-{kind}.{ext}"), False))
    # The cour's OWN poster → the season folder. Uses the provider poster
    # (`Match.poster_url`, the cour-specific cover) — NOT the fanart.tv show
    # poster. Shared across the season's episodes (write-if-absent; the empty-
    # folder sweep reclaims it when the whole season is undone), so it's never
    # in this file's created_assets.
    if _season_dir is not None and provider_poster and "poster" in kinds:
        jobs.append((provider_poster, _season_dir / "poster.jpg", True))
    written: list[str] = []
    if not jobs:
        return written
    from kira.download_guard import fetch_capped, sniff_image

    def _atomic_write(tmp: Path, final: Path, data: bytes) -> None:
        tmp.write_bytes(data)
        os.replace(tmp, final)

    for url, dest, _shared in jobs:
        try:
            if dest.exists():
                continue
            # Per-batch byte cache: a TV season shares one show clearlogo /
            # poster URL across every episode — fetch it ONCE, write it to
            # each episode's sidecar (still write-if-absent on disk).
            data = img_cache.get(url) if img_cache is not None else None
            if data is None:
                # The URL comes from fanart.tv / provider JSON, so route it
                # through the SSRF guard and stream it under a hard size cap
                # (preserve the old 20s per-image budget).
                fetched = await fetch_capped(client, url, max_bytes=_ARTWORK_MAX_BYTES, timeout=20.0)
                if not fetched:
                    continue
                content, _ct = fetched
                # Validate by magic bytes: an image host that 200s with an
                # HTML "not found"/rate-limit page or a JSON error would
                # otherwise be saved as a permanent corrupt file
                # (write-if-absent → no retry).
                if sniff_image(content) is None:
                    logger.warning(f"artwork: {url} returned a non-image payload, skipping")
                    continue
                data = content
                if img_cache is not None:
                    # Bounded per-batch cache: evict the oldest entry once at
                    # capacity so a large, art-diverse batch can't grow process
                    # memory without limit (insertion-ordered dict → FIFO).
                    if len(img_cache) >= _IMG_CACHE_MAX_ENTRIES:
                        img_cache.pop(next(iter(img_cache)), None)
                    img_cache[url] = data
            tmp = dest.with_name(dest.name + ".part")
            await asyncio.to_thread(_atomic_write, tmp, dest, data)
            # Per-series art (poster.jpg/fanart.jpg) is shared by every episode —
            # like tvshow.nfo — so exclude it from THIS file's created_assets:
            # undoing one episode must not delete the show's poster, and the
            # empty-folder sweep removes it when the whole show is undone.
            if not _shared:
                written.append(str(dest))
        except Exception as e:
            logger.warning(f"artwork: failed {url} -> {dest} (non-fatal): {e!r}")
    return written


async def _resolve_type_target_root(
    session: AsyncSession,
    media_type: str,
) -> str | None:
    """Per-media-type destination override.

    Lets the user route each media type to its own folder (Plex /
    Jellyfin convention is "TV Shows", "Movies", "Anime", "Music" as
    SEPARATE roots — not necessarily children of a single Media root).
    Stored in settings as `paths.targets.<type>` where <type> is one of
    movie / tv / anime / music.

    Returns the configured path string when set, else None (caller falls
    back to legacy library_root + SUBFOLDER layout).
    """
    from kira.models import Setting
    key = f"paths.targets.{media_type}"
    row = await session.get(Setting, key)
    if not row:
        return None
    val = row.value
    if isinstance(val, dict):
        if "value" in val and isinstance(val["value"], str) and val["value"].strip():
            return val["value"].strip()
        return None
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


async def _resolve_library_root(session: AsyncSession, name: str | None) -> str:
    """PB-3: resolve a library-root NAME to an absolute path.

    The set of allowed roots is admin-configured in settings under
    `paths.library_roots` (dict of name → absolute path string). The
    request can only pick by name; it can NEVER pass a raw path. This
    closes the `library_root="/etc"` arbitrary-write attack vector.

    Backward compat: the legacy single-root setting `paths.library_root`
    transparently maps to `paths.library_roots["default"]`. Existing
    installs keep working with no settings migration on their end.
    """
    from kira.models import Setting

    # Preferred: multi-root dict.
    roots_row = await session.get(Setting, "paths.library_roots")
    roots: dict[str, str] = {}
    if roots_row and isinstance(roots_row.value, dict):
        roots = {k: str(v) for k, v in roots_row.value.items() if isinstance(v, str) and v}

    # Backward compat: legacy single-root setting becomes the default.
    if not roots:
        legacy = await session.get(Setting, "paths.library_root")
        legacy_val: str | None = None
        if legacy and isinstance(legacy.value, dict) and "value" in legacy.value:
            legacy_val = str(legacy.value["value"])
        elif legacy and isinstance(legacy.value, str):
            legacy_val = legacy.value
        roots = {"default": legacy_val or settings.media_root}

    chosen = name or "default"
    if chosen not in roots:
        raise HTTPException(
            400,
            f"Unknown library root {chosen!r}. Configured roots: {sorted(roots)}",
        )
    return roots[chosen]


# IMPORTANT: this helper MUST stay ABOVE the @router.post route below. If it
# sits BETWEEN the decorator and `async def rename`, the decorator binds to THIS
# function — FastAPI then routes POST /rename to it and treats its
# (anidb, selected, parsed) params as REQUIRED QUERY params, 422-ing every
# single rename ("query.anidb: Field required …"). Keep it here.
async def _resolve_franchise_absolute(anidb, selected, parsed) -> int | None:
    """Franchise-ABSOLUTE episode number for a locally-named anime file matched
    to a per-cour/season AniDB AID — or None.

    Closes the locally-named→absolute rename-output gap: `AoT S4E01` matched to
    the Final-Season cour AID has no absolute in its name, so `{{absx}}` had
    nothing to render. `get_franchise_offsets` (cache-first on disk; returns []
    when AniDB is banned/unresolvable → ban-safe) gives the cour's absolute
    range, and `franchise_absolute` adds the AID-local episode
    (`Match.episode_number` after cour routing). Any miss → None, so the filename
    falls back to its SxE form exactly as it did before."""
    from kira.matcher.cour_routing import franchise_absolute
    try:
        aid = int(selected.provider_id)
    except (TypeError, ValueError):
        return None
    local_ep = selected.episode_number if selected.episode_number is not None else parsed.episode
    try:
        offsets = await anidb.get_franchise_offsets(aid)
    except Exception as e:
        logger.warning(f"_resolve_franchise_absolute: get_franchise_offsets failed (non-fatal): {e!r}")
        return None
    return franchise_absolute(offsets, aid, local_ep)


@router.post("", response_model=RenameResult)
async def rename(
    payload: RenameRequest,
    session: AsyncSession = Depends(get_session),
) -> RenameResult:
    """Thin HTTP wrapper — preserves FastAPI routing (path/method/response
    model) while delegating the full pipeline to the `perform_rename` service
    function. The daemon (watcher.maybe_auto_rename) calls `perform_rename`
    directly with its own session, so the executor is no longer coupled to the
    API layer / `Depends(get_session)`."""
    return await perform_rename(payload, session)


async def _anime_group_members(session: AsyncSession, group_id: str) -> list[tuple[int, str, int | None]]:
    """All AniDB cours of a franchise group that are present (selected) in the
    library, as ``(aid, title, year)`` sorted by AID ascending (≈ air order).

    Read straight from the Match rows — reliable, unlike the in-memory AniDB title
    dump the old collapse relied on (it's often unloaded at rename time, silently
    no-opping and leaving every cour in its own folder). Drives the show-folder
    unification: the earliest-present cour supplies BOTH the title AND the year —
    year-bearing templates (Jellyfin anime = "{{n}} ({{y}})/…") would otherwise
    still fragment one franchise into "Show (2013)" / "Show (2017)" folders even
    with a unified title, because each cour carries its own premiere year."""
    rows = list(await session.scalars(
        select(Match).where(
            Match.series_group_id == group_id,
            Match.provider == "anidb",
            Match.is_selected.is_(True),
        )
    ))
    seen: dict[int, tuple[str, int | None]] = {}
    for m in rows:
        try:
            aid = int(m.provider_id)
        except (TypeError, ValueError):
            continue
        if aid not in seen and (m.title or "").strip():
            seen[aid] = (m.title, m.year)
    return [(aid, t, y) for aid, (t, y) in sorted(seen.items())]


async def _anime_group_season_posters(session: AsyncSession, group_id: str) -> dict[int, str]:
    """`{season_number: poster_url}` across a franchise's present cours.

    Each AniDB cour carries its OWN poster and its ScudLee-stamped `season_number`
    (the same season the rename renders into the path), so a franchise unified
    into one show can ship every season's real cover as a per-season `<thumb>` in
    the `tvshow.nfo` (Kodi's mechanism). First poster seen per season wins; rows
    without a positive season or a poster are skipped. Best-effort — only as
    complete as the per-cour poster warm-up that populated `Match.poster_url`."""
    rows = list(await session.scalars(
        select(Match).where(
            Match.series_group_id == group_id,
            Match.provider == "anidb",
            Match.is_selected.is_(True),
        )
    ))
    out: dict[int, str] = {}
    for m in rows:
        s, p = m.season_number, (m.poster_url or "").strip()
        if isinstance(s, int) and s > 0 and p and s not in out:
            out[s] = p
    return out


async def _discard_intent(session: AsyncSession, intent: RenameIntent | None) -> None:
    """Best-effort removal of a write-ahead intent whose move did NOT complete
    (#4). Committed on its own so it can't be undone by a later per-file rollback."""
    if intent is None:
        return
    try:
        await session.delete(intent)
        await session.commit()
    except Exception as e:
        logger.warning(f"rename: discard intent failed (non-fatal): {e!r}")
        try:
            await session.rollback()
        except Exception:
            pass


async def reconcile_pending_renames() -> tuple[int, int]:
    """Settle rename intents a crash left in the move→commit window (#4). Called
    once on boot, alongside the scan reconcile. Returns ``(finalized, discarded)``.

    For each leftover ``RenameIntent`` it inspects disk:
      • dst present & src gone  → the move landed but the DB commit didn't:
        finalize — point the MediaFile at dst, mark it renamed, and add a
        RenameHistory row if this move isn't already recorded — then drop the intent.
      • src present (move never happened / was reverted) → the file is still at
        src and the DB already points there: just drop the intent.
      • neither present (vanished out-of-band) → log + drop.

    Best-effort: a per-intent failure is logged and skipped; never raises into boot."""
    from kira.database import SessionLocal
    finalized = discarded = 0
    async with SessionLocal() as session:
        intents = list(await session.scalars(select(RenameIntent)))
        for it in intents:
            try:
                src, dst = Path(it.src), Path(it.dst)
                src_exists = await asyncio.to_thread(src.exists)
                dst_exists = await asyncio.to_thread(dst.exists)
                if dst_exists and not src_exists:
                    mf = await session.get(MediaFile, it.media_file_id) if it.media_file_id else None
                    if mf is not None:
                        mf.file_path = it.dst
                        mf.status = "renamed"
                    already = await session.scalar(
                        select(RenameHistory)
                        .where(RenameHistory.old_path == it.src, RenameHistory.new_path == it.dst)
                        .limit(1)
                    )
                    if already is None:
                        session.add(RenameHistory(
                            media_file_id=it.media_file_id,
                            old_path=it.src, new_path=it.dst, operation=it.operation,
                            title=Path(it.dst).stem,
                        ))
                    finalized += 1
                else:
                    discarded += 1
                await session.delete(it)
            except Exception as e:
                logger.warning(f"reconcile_pending_renames: intent {getattr(it, 'id', None)} failed (non-fatal): {e!r}")
        try:
            await session.commit()
        except Exception as e:
            logger.warning(f"reconcile_pending_renames: commit failed (non-fatal): {e!r}")
    return finalized, discarded


async def perform_rename(
    payload: RenameRequest,
    session: AsyncSession,
) -> RenameResult:
    """Execute the rename pipeline for a batch of files.

    Plain service function (NO `Depends`): `session` is a normal parameter so
    both the HTTP endpoint and the watcher daemon can drive it with their own
    AsyncSession. This is the batch scope — shared per-batch resources (fanart
    cache, image cache, the shared artwork httpx client) live here so they're
    reused across every file/episode in the season."""
    op = FileOp(payload.op)
    profile = await _resolve_profile(session, payload.profile)
    library_root = await _resolve_library_root(session, payload.library_root_name)
    # Managed library roots — the allowlist that bounds asset cleanup (#1): the
    # forward-orphan sweep below (and undo) only ever delete satellite files that
    # live under one of these, never anywhere else on disk.
    from kira.api.files import _managed_roots_aliased
    # Alias-aware (mapped-drive `Z:\` ↔ UNC): the forward-orphan sweep below
    # deletes created_assets persisted under the RESOLVED spelling, so a
    # drive-letter-only root set would skip them all — the same alias gap undo hit.
    managed_roots = await _managed_roots_aliased(session)
    cleanup_empty_source = await _resolve_cleanup_empty_dirs(session)
    # Sub-toggle: when cleanup_empty_source is on, ALSO sweep Plex/
    # Jellyfin/Kodi metadata artifacts (poster.jpg, *-thumb.jpg, etc.)
    # so the rmdir actually succeeds. Default-on; user can disable in
    # Settings → Folder cleanup if they want strict "only touch what's
    # already empty" semantics.
    cleanup_artifacts = await _resolve_cleanup_artifacts(session)
    # User-extendable cleanup lists + the aggressive "delete non-video" mode.
    # The aggressive mode is a SUB-option of the artifact sweep: with the sweep
    # off, force 'off' so nothing extra is ever deleted.
    cleanup_extra_names = await _resolve_cleanup_extra_names(session)
    cleanup_extra_exts = await _resolve_cleanup_extra_exts(session)
    cleanup_nonvideo = await _resolve_cleanup_nonvideo(session) if cleanup_artifacts else "off"
    # Symlink op only: write a RELATIVE link target (portable across remounts /
    # changed bind-mount paths) when the user opts in. Absolute by default.
    symlink_relative = await _resolve_bool_setting(session, "rename.symlink_relative", False)
    # Post-rename ownership/mode for Docker / NAS — chmod/chown the renamed file
    # + dirs we create so the media server (often a different uid) can read them.
    permissions = await _resolve_permissions(session)
    # Conflict policy when a DIFFERENT file already occupies the target:
    # "error" (default, surfaces as a failed item) / "skip" (RenameSkipped →
    # no-op) / "overwrite" (replace). Idempotent re-runs are always a safe
    # no-op regardless of this. "overwrite" folds into the overwrite flag so
    # execute_op's existing replace path handles it.
    on_conflict = await _resolve_str_setting(session, "rename.on_conflict", "error")
    effective_overwrite = payload.overwrite or on_conflict == "overwrite"
    # Recoverable cleanup (Settings → Folder cleanup): when on, swept artifacts
    # are MOVED into a managed trash folder instead of deleted, so a mistaken
    # sweep can be recovered from the user's file browser. Defaults to
    # <library_root>/.kira-trash; user may override the path. Off → hard delete
    # (the prior behavior).
    cleanup_trash_dir: Path | None = None
    if await _resolve_bool_setting(session, "rename.cleanup_trash", False):
        _trash_override = await _resolve_str_setting(session, "rename.trash_dir", "")
        if _trash_override:
            cleanup_trash_dir = Path(_trash_override)
        elif library_root:
            cleanup_trash_dir = Path(library_root) / ".kira-trash"
    rename_mode = await _resolve_rename_mode(session)
    # Pass 7 output toggles (both default OFF — opt-in metadata sidecars).
    write_nfo = await _resolve_bool_setting(session, "naming.write_nfo", False)
    # Stamp resolved provider IDs onto renamed files (xattr / ADS / portable
    # index) for instant re-identification. Default ON; switchable for users
    # who don't want Kira attaching ANY metadata to their files.
    stamp_ids = await _resolve_bool_setting(session, "rename.stamp_ids", True)
    nfo_fields = await _resolve_nfo_fields(session) if write_nfo else None
    download_artwork = await _resolve_bool_setting(session, "naming.download_artwork", False)
    # Artwork sources: provider poster/background always; fanart.tv (when a key
    # is set) adds logos / clear art / banner / disc / character art. Resolve the
    # enabled kinds + key ONCE; `_artwork_cache` dedupes the fanart.tv call to one
    # request per series id across a whole season's worth of files.
    artwork_kinds = await _resolve_artwork_kinds(session) if download_artwork else set()
    fanart_key = await _resolve_str_setting(session, "providers.fanarttv.api_key", "") if download_artwork else ""
    fanart_client_key = await _resolve_str_setting(session, "providers.fanarttv.client_key", "") if download_artwork else ""
    # Language preference for artwork (logos/posters): reuse the subtitle
    # languages as the "my language" hint, falling back to English.
    _art_langs_raw = await _resolve_str_setting(session, "subtitles.languages", "en") if download_artwork else "en"
    artwork_langs = [l.strip() for l in _art_langs_raw.split(",") if l.strip()] or ["en"]
    artwork_cache: dict = {}       # fanart.tv response per series id (1 API call/season)
    artwork_img_cache: dict = {}   # downloaded image bytes per URL (1 fetch/image)
    # CR-06: ONE shared httpx client for ALL artwork I/O across the batch — the
    # fanart.tv lookup AND every image-byte fetch reuse this single connection
    # pool instead of opening/closing two fresh clients per file. Created only
    # when artwork download is actually enabled (opt-in), and closed in the
    # `finally` after the file loop. Mirrors the shared `_sc` subtitle client.
    # Per-request timeouts (15s fanart / 20s image) are applied at the call
    # sites, so this default timeout is just a backstop.
    # Use the process-shared client (warm pool across batches) for artwork; no
    # per-batch open/close, so the file loop below doesn't need a try/finally
    # just to tear a client down. Downloads go through fetch_capped (guarded,
    # no redirects); the fanart.tv API returns JSON directly — neither needs the
    # old follow_redirects client.
    artwork_client = None
    if download_artwork:
        from kira import net
        artwork_client = net.shared_client()
    # Anime episode-numbering style: "seasonal" (S04E05, default) | "absolute"
    # (flat "One Piece - 1156"). Only affects anime templates; see select_template.
    anime_numbering = await _resolve_str_setting(session, "naming.anime_numbering", "seasonal")

    # Locally-named anime → franchise-ABSOLUTE output (the {{absx}} token). Built
    # ONCE per batch, and ONLY when the user picked absolute numbering — seasonal
    # renames (the default) never touch AniDB here. None when AniDB isn't
    # configured; `get_franchise_offsets` is cache-first + ban-safe, so even with
    # it built a banned/cold lookup just leaves the filename on its SxE fallback.
    anidb_for_abs = None
    if anime_numbering == "absolute":
        try:
            from kira.matcher.engine import registry_from_settings
            from kira import net
            _abs_reg = await registry_from_settings(net.shared_client())
            if _abs_reg.has("anidb"):
                anidb_for_abs = _abs_reg.build("anidb")
        except Exception as e:
            logger.warning(f"perform_rename: anidb build for absolute numbering failed (non-fatal): {e!r}")

    files = list(await session.scalars(
        select(MediaFile)
        .options(selectinload(MediaFile.matches))
        .where(MediaFile.id.in_(payload.file_ids))
    ))
    by_id = {f.id: f for f in files}

    results: list[RenameItemResult] = []
    # #3: in-batch duplicate-target guard. Maps a normalized target path → the
    # first file id that claimed it, so a second file rendering to the SAME
    # destination fails loudly instead of silently overwriting the first
    # (overwrite on) or erroring obscurely (off). Normalized via webhooks._norm
    # (case-fold + separator-fold) so casing/slash differences still collide.
    from kira.api.webhooks import _norm as _norm_path
    claimed_targets: dict[str, int] = {}
    # Series whose files were RENAMED this batch — feeds the post-rename Sonarr
    # rescan hook. Without it, Sonarr's next disk scan sees the old paths gone,
    # marks the episode files deleted, and may re-download monitored episodes.
    # (provider, provider_id) pairs; resolved to TVDB ids in the hook.
    renamed_episode_series: set[tuple[str, str]] = set()
    # Every path Kira WROTE this batch (renamed videos + their NFO / artwork /
    # co-renamed sidecars), so the post-loop in-place junk sweep can protect our
    # own fresh output when it strips leftovers from a destination folder that
    # keeps its media (the same-folder rename case). Raw abs strings; normalized
    # at compare time in sweep_destination_junk.
    inplace_protected: set[str] = set()
    async def _rename_one_file(f, fid):
        """Run the full pipeline for ONE file: resolve match+target, guard
        against duplicates/phantoms, journal intent, move, then write history /
        NFO / artwork / sidecars and commit. Appends exactly one RenameItemResult
        to the enclosing `results`; an early `return` is this file's terminal
        outcome. Batch settings + shared caches are captured from perform_rename."""
        parsed = ParsedFile(**f.parsed_data)
        selected: Match | None = next((m for m in f.matches if m.is_selected), None)
        if selected is None and f.matches:
            # Deterministic fallback when nothing is explicitly selected: highest
            # confidence wins, ties broken by lowest id (stable insertion order).
            # NEVER `f.matches[0]` — relationship order is arbitrary, so among
            # several real candidates that could silently rename to the WRONG
            # match (and non-reproducibly, run to run).
            selected = max(
                f.matches,
                key=lambda m: (m.confidence if m.confidence is not None else -1.0, -m.id),
            )
        # Refuse to rename files that have no real provider match. Without
        # this, a no_match file (which has a synthesised display object on
        # the frontend but zero real Match rows) would be renamed using just
        # the parsed title — pollution.
        #
        # Fix #8: music gets a clearer error explaining WHY there's no
        # match. Today's "No match to rename to" hides the underlying
        # reason — MusicBrainz isn't implemented yet, so music files
        # never get Match rows in the first place.
        if selected is None or not selected.provider or not selected.provider_id:
            if parsed.media_type == "music":
                err = "Music rename not supported yet — MusicBrainz provider is not implemented."
            else:
                err = "No match to rename to — match the file first."
            results.append(RenameItemResult(file_id=fid, ok=False, error=err))
            return
        library_title = selected.title or parsed.title
        library_year = selected.year if selected.year is not None else parsed.year
        # Per-season posters for the unified tvshow.nfo (anime franchises only —
        # gathered alongside the title unification below).
        season_posters: dict[int, str] | None = None
        season_override_val = selected.season_number
        # Season-0 guard: a tv_episode match that reports season 0 must NOT dump
        # a regular numbered episode into "Specials". AniDB has no season concept
        # and several entries (One Piece AID 69, other long-runners) come back as
        # season 0 — that collapsed One Piece 1156+ into Specials/S00E1156. A
        # GENUINE special parses as season 0/None too, so only override when the
        # FILE itself parsed a real positive season; then trust the filename.
        if (season_override_val in (0, None)
                and selected.match_type == "tv_episode"
                and isinstance(parsed.season, int) and parsed.season > 0):
            season_override_val = parsed.season

        # AniDB franchise grouping. AniDB gives every cour/season its own AID +
        # title, so without unification each lands in its OWN show folder — the user
        # ends up with 3 "Bleach: Thousand-Year Blood War …" folders instead of one
        # show with seasons. All cours of a franchise share Match.series_group_id
        # (`anidb:<canonical_aid>`), so we unify the show folder to the title of the
        # EARLIEST member PRESENT in the library (lowest AID) — read from the Match
        # rows (reliable), NOT the in-memory AniDB title dump the old collapse used
        # (it's often unloaded at rename time → returned None → silently no-op →
        # fragmentation). For TYBW that's "Bleach: Thousand-Year Blood War"; for AoT,
        # "Attack on Titan".
        #
        # SKIP for manual pins — the user's explicit Re-identify choice wins verbatim.
        if (
            selected.provider == "anidb"
            and selected.series_group_id
            and not selected.is_manual
            and parsed.media_type == "anime"
        ):
            members = await _anime_group_members(session, selected.series_group_id)
            if members and members[0][1]:
                # Unify the show folder to the FRANCHISE ROOT title — not each
                # cour's own AniDB title. The group_id encodes the canonical root
                # aid (`anidb:<root>`), whose title is the base franchise name
                # ("Haikyu!!") with NO per-cour season qualifier. The earliest cour
                # PRESENT in the library is only a safe fallback when the root
                # season itself is present; if the user has ONLY "Haikyu!! 2nd
                # Season" (AID 10981) and not S1, members[0]'s title carries the
                # "2nd Season" qualifier → "Haikyu!! 2nd Season/Season 2/Haikyu!!
                # 2nd Season - S02E24…". Resolve the root title from the offline
                # AniDB title dump; fall back to the earliest present member when
                # it's not loaded / the AID is absent (no regression). Year is
                # still unified across the present members below.
                _root_title = None
                try:
                    _canon = selected.series_group_id.split(":", 1)[-1]
                    if _canon.isdigit():
                        from kira.providers.anidb import AniDBProvider
                        _root_title = AniDBProvider._pick_display_title(int(_canon))
                except Exception:
                    _root_title = None
                if _root_title:
                    library_title = _root_title
                else:
                    # The title dump isn't in memory this process — and the common
                    # "restart → re-rename" path runs NO AniDB op, so nothing lazy-
                    # loads it (parsing the 30 MB dump on the rename path is too
                    # heavy to force). Fall back to stripping AniDB's trailing per-
                    # cour season qualifier so a later cour still folds to the base
                    # franchise name ("Haikyu!! 2nd Season" → "Haikyu!!"). Ordinal /
                    # "Part N" forms only; a SUBTITLE sequel ("… To the Top") needs
                    # the dump, which any scan/match loads.
                    import re as _re
                    _stripped = _re.sub(
                        r"\s+(?:\d+(?:st|nd|rd|th)\s+Season|Season\s+\d+|Part\s+\d+)\s*$",
                        "", members[0][1], flags=_re.IGNORECASE,
                    ).strip()
                    library_title = _stripped or members[0][1]
                # Unify the YEAR across the group UNCONDITIONALLY. The old code only
                # overrode the year when the canonical member HAD one — so when the
                # AniDB match carries no year (common), each file fell back to its
                # OWN filename-parsed year (line above), and a release group that
                # stamps "2025" into some filenames but not others split ONE show
                # into "Gachiakuta (2025)" + "Gachiakuta". Take the first member
                # that has a year (AID order), else None — but the SAME value for
                # EVERY file in the group, so a franchise can never again fragment
                # on a per-file year. (None → the show folder simply carries no
                # year, which is correct + consistent for a yearless AniDB entry.)
                library_year = next((y for _aid, _t, y in members if y is not None), None)
                # The template's {{y}} token falls back to parsed.year when
                # library_year is None (templates._build_ctx), so a YEARLESS group
                # would STILL leak each file's own filename year → "Gachiakuta
                # (2025)" beside "Gachiakuta". Pin the file's parsed year to the
                # unified group value too (render-only — mirrors how the cour block
                # below already mutates `parsed`), so every file in the franchise
                # renders the SAME year, or none, no matter what its filename had.
                parsed.year = library_year
            # Each cour's own poster → a per-season <thumb> in the unified
            # tvshow.nfo (Kodi). File-based servers use Season NN/poster.jpg.
            season_posters = await _anime_group_season_posters(session, selected.series_group_id)

        # Seasonal cour-episode offset. AniDB stores each cour's episodes LOCALLY
        # (every cour restarts at 1), but TVDB/Jellyfin want one continuous run per
        # season — so several cours sharing a TVDB season (AoT S3/S4 parts, Bleach
        # TYBW = all S17) would collide at E01 once unified. We KEEP the real TVDB
        # season (selected.season_number) and shift this cour's episode by the
        # cumulative OFFICIAL episode count of the prior cours in that season. The
        # offset comes from the static cour-routing table (Fribb mapping + AniDB's
        # official per-AID episode counts — NEVER the user's on-disk file counts, so
        # a partial/out-of-order download can't corrupt the numbering). Absolute
        # numbering flattens via {{absx}} below instead, so skip there. Render-only:
        # mutate the local `parsed`, never the stored row.
        if (anime_numbering != "absolute"
                and (selected.provider or "").lower() == "anidb"
                and selected.provider_id
                and parsed.media_type == "anime"):
            # ── Seasonal (TVDB) placement — THE single source of truth ────────
            # AniDB is absolute/seasonless; in "seasonal" mode map the matched
            # AniDB (id, episode) to its real TVDB (season, episode) via the
            # ScudLee anime-lists table. ONE resolver handles every shape:
            #   • flat umbrella  — One Piece AID 69 ep 1156 → S23E01 (… 1165→S23E10)
            #   • cour-split     — Bleach TYBW cour 2 (AID 17765) → S17E14
            # `resolve_canonical_season` stamps Match.season_number with the SAME
            # ScudLee season at scan time, so what the popup SHOWS equals what we
            # WRITE here. Render-only: mutate the local `parsed`, never the row.
            anidb_ep = (
                selected.episode_number
                if selected.episode_number is not None else parsed.episode
            )
            resolved = None
            if anidb_ep is not None:
                try:
                    from kira.providers.anime_lists import resolve_anidb_to_tvdb
                    resolved = await resolve_anidb_to_tvdb(int(selected.provider_id), anidb_ep)
                except Exception as e:
                    logger.warning(f"rename: ScudLee season resolve failed for {fid} (non-fatal): {e!r}")
            if resolved is not None:
                scud_season, scud_episode = resolved
                season_override_val = scud_season
                if parsed.episode_end is not None and parsed.episode is not None:
                    parsed.episode_end = scud_episode + (parsed.episode_end - parsed.episode)
                parsed.episode = scud_episode
            elif (selected.season_number is not None and parsed.episode is not None):
                # Fallback (no ScudLee mapping): keep the prior cour-routing offset
                # — Fribb season (selected.season_number) + cumulative cour offset.
                try:
                    from kira.matcher.cour_routing import build_cour_routing_table
                    table = await build_cour_routing_table(
                        "anidb", str(selected.provider_id), selected.season_number,
                    )
                    if table:
                        aid = int(selected.provider_id)
                        offset = next((off for (_s, _e, cid, off) in table if cid == aid), None)
                        if offset is not None:
                            # Match.episode_number is the cour-LOCAL episode after
                            # arbitration; season-continuous output = local + offset.
                            base = (
                                selected.episode_number
                                if selected.episode_number is not None
                                else parsed.episode
                            )
                            new_ep = base + offset
                            if parsed.episode_end is not None:
                                parsed.episode_end = new_ep + (parsed.episode_end - parsed.episode)
                            parsed.episode = new_ep
                except Exception as e:
                    logger.warning(f"rename: cour episode offset failed for {fid} (non-fatal): {e!r}")

        # Locally-named anime → franchise-absolute number for {{absx}}. The file
        # was named per-cour-local (e.g. "S4E01") so parsed.absolute_episode is
        # None; derive it from the matched AID's franchise offset table. Set
        # BEFORE the target-root computation below so both the folder render and
        # the filename render see it. Render-only: we mutate this local `parsed`,
        # never the stored row. No-op unless absolute numbering is on (so
        # `anidb_for_abs` is None otherwise).
        if (
            anidb_for_abs is not None
            and parsed.media_type == "anime"
            and parsed.absolute_episode is None
            and (selected.provider or "").lower() == "anidb"
            and selected.provider_id
        ):
            _abs_ep = await _resolve_franchise_absolute(anidb_for_abs, selected, parsed)
            if _abs_ep is not None:
                parsed.absolute_episode = _abs_ep

        # Determine where this file's renamed copy should land.
        # Priority:
        #   1. rename_mode == 'in-place' (new default): the file STAYS in
        #      its current containing folder structure. We compute a
        #      per-file target_root by walking up from the source file
        #      by (template depth) levels — that puts the rendered
        #      template's path components at exactly the same depth the
        #      source currently sits at. Show + Season + filename can
        #      all RENAME, but no relocation to a different library.
        #   2. Per-type destination override (admin-configured in Settings).
        #   3. Fallback: library_root + SUBFOLDER[type] (legacy).
        # Resolve the real per-episode title up front so BOTH the in-place root
        # and the final filename render "{t}" with it. AniDB anime carry no
        # per-episode title on the Match, so without this the filename fell back
        # to "Episode NN". Cached per series/season; best-effort — on a miss we
        # keep selected.episode_title (the prior behavior).
        _ep_title = selected.episode_title
        if not _ep_title and parsed.episode is not None and selected.provider_id:
            try:
                from kira.api.series import resolve_episode_meta
                from kira.matcher.engine import registry_from_settings
                from kira import net
                _epc = net.shared_client()
                _epr = await resolve_episode_meta(
                    selected, selected.season_number, parsed.episode,
                    await registry_from_settings(_epc), _epc)
                if _epr and _epr.title:
                    _ep_title = _epr.title
            except Exception:
                pass
        type_target_root: str | None = None
        if rename_mode == "in-place":
            try:
                type_target_root = _compute_inplace_target_root(
                    Path(f.file_path), parsed, profile,
                    library_title=library_title,
                    library_year=library_year,
                    episode_title=_ep_title,
                    season_override=season_override_val,
                )
            except Exception as e:
                # The in-place root computation ALSO renders the naming template.
                # A template error here must be a PER-FILE failure — same
                # contract as format_target_path below — not an uncaught raise
                # that 500s the whole batch (and crashes the dry-run preview,
                # so the user can't even see which file/template is broken).
                results.append(RenameItemResult(file_id=fid, ok=False, error=f"Template error: {e}"))
                return
        if type_target_root is None:
            type_target_root = await _resolve_type_target_root(session, parsed.media_type)
        # Tier 1.5 step 2b: assemble provider-metadata for the rich naming
        # tokens ({director}/{genres}/{tmdbid}/{runtime}/…). metadata_blob
        # carries the bulk; provider ids come off the Match row. Defensive
        # getattr + None-coalesce: a missing blob just leaves those template
        # tokens empty — templates that don't reference them are unaffected.
        _meta = dict(getattr(selected, "metadata_blob", None) or {})
        _prov = (selected.provider or "").lower()
        if selected.provider_id:
            if _prov == "tmdb":
                _meta.setdefault("tmdbid", selected.provider_id)
            elif _prov == "tvdb":
                _meta.setdefault("tvdbid", selected.provider_id)
            elif _prov == "anidb":
                _meta.setdefault("anidbid", selected.provider_id)
        try:
            target = format_target_path(
                parsed, library_root, profile,
                library_title=library_title,
                library_year=library_year,
                episode_title=_ep_title,
                season_override=season_override_val,
                type_target_root=type_target_root,
                metadata=_meta,
                file_size=f.file_size,
                anime_numbering=anime_numbering,
            )
        except Exception as e:
            results.append(RenameItemResult(file_id=fid, ok=False, error=f"Template error: {e}"))
            return

        src = Path(f.file_path)

        # #3: refuse to let two files in this batch land on the same path. The
        # first claimant wins; later colliders fail with a pointer to it (rather
        # than one silently clobbering the other). Checked for dry-run too, so the
        # preview surfaces the collision before the user commits.
        tgt_key = _norm_path(str(target))
        if tgt_key in claimed_targets:
            results.append(RenameItemResult(
                file_id=fid, ok=False, old_path=str(src), new_path=str(target),
                error=(
                    f"Duplicate target — file id {claimed_targets[tgt_key]} in this "
                    f"batch already maps to “{target.name}”. Resolve the duplicate "
                    f"(or pick distinct matches) before renaming."
                ),
            ))
            return
        claimed_targets[tgt_key] = fid

        if payload.dry_run:
            # #6: preview the FULL set of side effects, not just the video path,
            # so the user sees everything a real run would touch before committing.
            preview_sidecars: list[str] = []
            try:
                preview_sidecars = [s.name for s in discover_sidecars(src)]
            except Exception:
                pass
            preview_nfo: list[str] = []
            if write_nfo and selected:
                try:
                    from kira.renamer import nfo as _nfo_preview
                    preview_nfo = [p.name for p in _nfo_preview.plan_nfo_writes(target, parsed.media_type).values()]
                except Exception:
                    pass
            preview_art = sorted(artwork_kinds) if (download_artwork and selected) else []
            results.append(RenameItemResult(
                file_id=fid, ok=True, old_path=str(src), new_path=str(target),
                sidecars=preview_sidecars or None,
                nfo=preview_nfo or None,
                artwork=preview_art or None,
            ))
            return

        # Phantom-file guard: if the source doesn't exist on disk AND the
        # target already does, this file was likely renamed previously by
        # a different operation (or the scanner re-discovered it after a
        # case-insensitive Windows scan turned the renamed location into
        # a "new" scan entry). Mark it as already-renamed silently — the
        # user's intent is satisfied; no error toast required.
        #
        # R2-H13: flag the phantom case in the `error` field so the UI
        # can distinguish "file actually moved" from "file reconciled
        # because someone moved it out-of-band". The frontend toasts
        # this differently to set the right expectation.
        #
        # EE-4: BEFORE trusting `Path.exists() == False`, verify the
        # filesystem hosting the source is even reachable. On Windows,
        # an unmounted NAS makes every path on that drive return False —
        # not because files are missing, but because we can't see them.
        # Without this check, a 45-second network blip during a rename
        # batch silently marks every file as 'renamed', diverging the DB
        # from the actual filesystem in a way that's nearly impossible
        # to reconcile later.
        if not src.exists():
            if not _filesystem_reachable(src):
                # Filesystem unmounted/unreachable — we have NO basis to
                # claim phantom. Fail with a transient error so the user
                # can fix the mount and retry without DB corruption.
                results.append(RenameItemResult(
                    file_id=fid, ok=False, old_path=str(src),
                    error=(
                        "Source filesystem unreachable — refusing to claim "
                        "phantom rename. Check NAS / mount and retry."
                    ),
                ))
                return
            if target.exists():
                f.status = "renamed"
                # Update the file_path so future actions point at where
                # the file actually lives.
                f.file_path = str(target)
                results.append(RenameItemResult(
                    file_id=fid, ok=True, old_path=str(src), new_path=str(target),
                    error="[PHANTOM] Already at target — no move performed.",
                ))
                return
            # Source missing AND target missing AND FS is alive — genuine
            # "file is gone". Mark as renamed anyway (nothing to do) but
            # flag in the response so the user knows the row was
            # reconciled, not actually moved.
            f.status = "renamed"
            results.append(RenameItemResult(
                file_id=fid, ok=True, old_path=str(src), new_path=str(src),
                error="[PHANTOM] Source missing — marked as renamed (no file to move).",
            ))
            return

        # Idempotent no-op: the file is ALREADY exactly at its target — e.g. a
        # re-submitted rename (file_path now equals the target). Moving a file onto
        # itself and recording a src==dst history row is what produced the "renamed
        # once but it shows up twice in history" report. Treat it like
        # phantom-already-at-target: mark renamed, no move, no history row.
        #
        # CASE-SENSITIVE (separator-normalized only): a case-only rename
        # (Movie.MKV → movie.mkv) is a REAL, intended operation, so it must NOT be
        # swallowed here — that's why we don't reuse `_norm` (which case-folds).
        if str(src).replace("\\", "/").rstrip("/") == str(target).replace("\\", "/").rstrip("/"):
            f.status = "renamed"
            results.append(RenameItemResult(
                file_id=fid, ok=True, old_path=str(src), new_path=str(target),
                error="[PHANTOM] Already at target — no rename needed.",
            ))
            return

        # #4: write-ahead intent, committed BEFORE the physical move so a crash in
        # the move→commit window is recoverable on next boot (reconcile_pending_renames).
        # Only the video move is journaled (sidecars carry their own history rows).
        # Best-effort: a journal failure degrades to the old narrower window rather
        # than erroring the file.
        intent: RenameIntent | None = RenameIntent(
            media_file_id=fid, src=str(src), dst=str(target), operation=op.value,
        )
        try:
            session.add(intent)
            await session.commit()
        except Exception as e:
            logger.warning(f"rename: intent journal write failed for {fid} (non-fatal): {e!r}")
            try:
                await session.rollback()
            except Exception:
                pass
            intent = None

        try:
            # cleanup_stop_at = the library_root setting. The cleanup
            # walker refuses to rmdir AT or above this path so we never
            # delete the user's media root or anything above it.
            #
            # Fix #7: per-media-type cleanup depth caps. Movies live
            # at <root>/<Movie>/file.mkv (1 level deep) so 1 ancestor
            # cleanup is enough. TV/anime live at <root>/<Show>/<Season X>/
            # so 2 ancestor cleanup catches the Season + Show folder
            # both. Music = <Artist>/<Album>/<file> → 2 levels too.
            cleanup_max_levels = {
                "movie": 1,
                "tv":    2,
                "anime": 2,
                "music": 2,
            }.get(parsed.media_type, 2)
            # ── Autopsy 9: offload blocking disk I/O to a worker thread.
            # `execute_op` calls `shutil.move` / `shutil.copy2` / `os.link`
            # — synchronous, C-level blocking primitives. For a same-disk
            # rename that's microseconds; for a cross-drive move of a 30
            # GB anime cluster that's MINUTES of byte-copy. Running it
            # directly on the asyncio event loop freezes the whole web
            # server — websockets disconnect, /health timeouts, Docker
            # decides the container is dead and SIGKILLs us mid-copy
            # (which is exactly the scenario Autopsy 8 protects against).
            # `asyncio.to_thread` runs the blocking call on a worker
            # thread; the event loop stays responsive for poll/health
            # requests during the copy.
            video_artifacts_cleaned = await asyncio.to_thread(
                execute_op,
                op, src, target,
                overwrite=effective_overwrite,
                on_conflict=on_conflict,
                cleanup_empty_source=cleanup_empty_source,
                cleanup_stop_at=Path(library_root) if library_root else None,
                cleanup_max_levels=cleanup_max_levels,
                cleanup_artifacts=cleanup_artifacts,
                cleanup_trash_dir=cleanup_trash_dir,
                cleanup_nonvideo=cleanup_nonvideo,
                cleanup_extra_names=cleanup_extra_names,
                cleanup_extra_exts=cleanup_extra_exts,
                symlink_relative=symlink_relative,
                permissions=permissions,
            ) or 0
        except RenameSkipped:
            # on_conflict=skip: a DIFFERENT file already occupies the target.
            # Deliberate no-op — leave both untouched, drop the write-ahead
            # intent, and report the file as unchanged (old == new), not failed.
            await _discard_intent(session, intent)
            results.append(RenameItemResult(
                file_id=fid, ok=True, old_path=str(src), new_path=str(src),
            ))
            return
        except Exception as e:
            # The move never happened → discard the write-ahead intent so reconcile
            # doesn't later inspect a rename that didn't occur.
            await _discard_intent(session, intent)
            results.append(RenameItemResult(
                file_id=fid, ok=False, old_path=str(src), new_path=str(target),
                error=str(e),
            ))
            return

        # If we MOVEd, the source path no longer exists — update the row.
        if op == FileOp.MOVE:
            f.file_path = str(target)
        f.status = "renamed"
        video_history = RenameHistory(
            media_file_id=fid,
            match_id=selected.id if selected else None,
            old_path=str(src),
            new_path=str(target),
            operation=op.value,
            template_used=getattr(profile, parsed.media_type, profile.movie),
            media_type=parsed.media_type,
            title=library_title,
            poster_url=selected.poster_url if selected else None,
        )
        session.add(video_history)

        # Stamp the renamed file with its resolved provider ID (xattr / NTFS
        # ADS / the portable index) so a future re-scan re-identifies it
        # instantly via the Phase 14 embedded-ID bypass — even if the filename
        # is later changed. Pure optimisation, gated by `rename.stamp_ids`
        # (Settings → Advanced), never fails the rename.
        if stamp_ids and selected and selected.provider and selected.provider_id:
            try:
                from kira import xattr_store
                xattr_store.write_ids(str(target), {selected.provider: str(selected.provider_id)})
            except Exception as e:
                logger.warning(f"rename: xattr stamp failed for {fid} (non-fatal): {e!r}")

        # Provenance of the satellite files THIS rename creates (#1). Recorded
        # on the video_history row so undo deletes exactly these — no deriving
        # names from the stem, which can drift from the writer. Also drives the
        # forward-orphan sweep below.
        created_assets: list[str] = []

        # ── #12: Kodi/Emby NFO sidecars (opt-in, best-effort) ─────────
        # Write metadata .nfo files beside the renamed video from the data
        # already on the Match. Pure output — never fails the rename.
        if write_nfo and selected:
            try:
                created_assets += await _write_nfo_files(target, parsed, selected, _meta,
                                                          fields=nfo_fields, season_override=season_override_val,
                                                          series_name_override=library_title,
                                                          season_posters=season_posters)
            except Exception as e:
                logger.warning(f"rename: NFO write failed for {fid} (non-fatal): {e!r}")

        # ── #13: artwork download (opt-in, best-effort) ───────────────
        if download_artwork and selected and artwork_client is not None:
            try:
                created_assets += await _download_artwork_files(
                    target, parsed, selected, _meta,
                    client=artwork_client,
                    kinds=artwork_kinds, fanart_key=fanart_key,
                    fanart_client_key=fanart_client_key, languages=artwork_langs,
                    fanart_cache=artwork_cache, img_cache=artwork_img_cache,
                )
            except Exception as e:
                logger.warning(f"rename: artwork download failed for {fid} (non-fatal): {e!r}")

        if created_assets:
            video_history.created_assets = created_assets
        # Protect the renamed video + everything we just wrote beside it from the
        # post-loop in-place junk sweep (it must never delete our own output).
        inplace_protected.add(str(target))
        inplace_protected.update(created_assets)

        # ── #1 forward-orphan sweep ───────────────────────────────────
        # Re-renaming this file to a DIFFERENT target (without an undo in
        # between) would strand the artwork/NFO the PRIOR rename wrote under the
        # old target's name. Each prior non-undone history row recorded exactly
        # what it created, so we can delete that set authoritatively now. Cheap
        # (one indexed query per file), best-effort, never fails the rename.
        try:
            from kira.api.history import sweep_superseded_assets
            await sweep_superseded_assets(session, fid, str(target), managed_roots)
        except Exception as e:
            logger.warning(f"rename: superseded-asset sweep failed for {fid} (non-fatal): {e!r}")

        # ── Tier 1.2: Subtitle / sidecar co-renaming ─────────────────
        # Now that the video has moved, hunt for sidecars (.srt, .ass,
        # .sub, etc.) at the OLD location whose stem matched the video
        # and bring them along to the NEW location under the renamed
        # stem. Each sidecar gets its own RenameHistory row linked back
        # to the video's row via `parent_id` so cascading undo restores
        # everything together. Sidecar failures NEVER fail the video —
        # the user's primary intent (rename the video) already succeeded
        # before this block runs; sidecar warnings are surfaced via the
        # result's error field with a "[SIDECARS]" prefix the frontend
        # can recognise.
        sidecar_msg: str | None = None
        # MOVE removed the video from `src` but the sidecars still sit
        # in `src.parent`. COPY/SYMLINK/HARDLINK leave the video itself
        # in place too. Either way `discover_sidecars(src)` walks
        # `src.parent` looking for stem matches — works for all ops.
        try:
            sidecars = discover_sidecars(src)
        except Exception as e:
            logger.warning(f"rename: sidecar discovery failed for {fid}: {e!r}")
            sidecars = []

        if sidecars:
            # Need the video history row's id to link children — flush
            # without committing so SQLite assigns the autoincrement id
            # while keeping the transaction open. If the flush fails the
            # commit a few lines below will fail too and the per-file
            # error path handles it.
            try:
                await session.flush()
                parent_history_id = video_history.id
            except Exception as e:
                logger.warning(f"rename: flush before sidecars failed for {fid}: {e!r}")
                parent_history_id = None

            if parent_history_id is None:
                # #2: we couldn't get the video row's id to link children. Moving
                # the sidecars anyway would leave them UNTRACKED — undo would
                # revert the video but strand the subtitles at the new location.
                # Refuse: leave them beside the source (re-running the rename once
                # the DB issue clears picks them up cleanly). Emptying the list
                # makes the move loop below a no-op without an extra indent level.
                sidecar_msg = (
                    f"[SIDECARS] Left {len(sidecars)} sidecar"
                    f"{'s' if len(sidecars) != 1 else ''} in place — couldn't record them for undo."
                )
                sidecars = []

            moved_subs = 0
            failed_subs: list[str] = []
            for sidecar in sidecars:
                sub_target = compute_sidecar_target(sidecar, src, target)
                if sub_target is None:
                    continue
                # #7: sidecars are real files too — guard them against the same
                # in-batch duplicate-target collision as the video (#3 above).
                # Without this, two files whose sidecars render to the same path
                # would silently overwrite each other under overwrite=True,
                # entirely outside the video-target guard.
                sub_key = _norm_path(str(sub_target))
                if sub_key in claimed_targets:
                    failed_subs.append(
                        f"{sidecar.name}: duplicate target — file id "
                        f"{claimed_targets[sub_key]} in this batch already maps to "
                        f"“{sub_target.name}”"
                    )
                    continue
                claimed_targets[sub_key] = fid
                try:
                    # Same cleanup_empty_source policy as the video. Last
                    # operation in the parent folder triggers the cleanup
                    # naturally — the rmdir refuses non-empty dirs so
                    # mid-batch sidecar moves never accidentally trip it.
                    sub_artifacts_cleaned = await asyncio.to_thread(
                        execute_op,
                        op, sidecar, sub_target,
                        overwrite=effective_overwrite,
                        on_conflict=on_conflict,
                        cleanup_empty_source=cleanup_empty_source,
                        cleanup_stop_at=Path(library_root) if library_root else None,
                        cleanup_max_levels=cleanup_max_levels,
                        cleanup_artifacts=cleanup_artifacts,
                        cleanup_trash_dir=cleanup_trash_dir,
                        cleanup_nonvideo=cleanup_nonvideo,
                        cleanup_extra_names=cleanup_extra_names,
                        cleanup_extra_exts=cleanup_extra_exts,
                        symlink_relative=symlink_relative,
                        permissions=permissions,
                    ) or 0
                    # Aggregate artifact count across all sidecar moves
                    # for this video. The LAST sidecar move triggers the
                    # actual rmdir-walk (the directory becomes empty
                    # then), so most of the cleanup count usually shows
                    # up there — but counting per-call is robust to any
                    # ordering.
                    video_artifacts_cleaned += sub_artifacts_cleaned
                    # Co-renamed sidecar is our output too — shield it from the
                    # in-place junk sweep (esp. 'all' mode, which deletes subs).
                    inplace_protected.add(str(sub_target))
                except Exception as e:
                    failed_subs.append(f"{sidecar.name}: {e}")
                    continue
                if parent_history_id is not None:
                    session.add(RenameHistory(
                        media_file_id=fid,
                        match_id=selected.id if selected else None,
                        parent_id=parent_history_id,
                        old_path=str(sidecar),
                        new_path=str(sub_target),
                        operation=op.value,
                        template_used=getattr(profile, parsed.media_type, profile.movie),
                        media_type=parsed.media_type,
                        title=library_title,
                        poster_url=selected.poster_url if selected else None,
                    ))
                moved_subs += 1

            if failed_subs:
                joined = "; ".join(failed_subs)
                sidecar_msg = (
                    f"[SIDECARS] Moved {moved_subs}/{len(sidecars)} sidecar"
                    f"{'s' if len(sidecars) != 1 else ''}; "
                    f"{len(failed_subs)} failed: {joined[:200]}"
                )
            elif moved_subs > 0:
                sidecar_msg = (
                    f"[SIDECARS] Moved {moved_subs} sidecar"
                    f"{'s' if moved_subs != 1 else ''} alongside the video."
                )

        # ── Folder cleanup artifact count ─────────────────────────────
        # Append the count of Plex/Jellyfin/Kodi cache files Kira swept
        # from the source folder hierarchy (poster.jpg, tvshow.nfo,
        # .actors/, etc.) so the user knows their library is genuinely
        # tidier than before, not that we silently deleted user data.
        # The [ARTIFACTS] prefix lets the frontend toast surface this
        # as a positive, informational note (not an error).
        if video_artifacts_cleaned > 0:
            artifact_note = (
                f"[ARTIFACTS] Cleaned {video_artifacts_cleaned} stale Plex/Jellyfin "
                f"metadata file{'s' if video_artifacts_cleaned != 1 else ''} "
                f"from the source folder."
            )
            sidecar_msg = (
                f"{sidecar_msg} · {artifact_note}" if sidecar_msg else artifact_note
            )

        # ── Autopsy 8: durable per-file commit. The physical move just
        # succeeded — the disk now reflects the rename. We MUST persist
        # the matching MediaFile.file_path + RenameHistory NOW, before
        # touching the next file. The previous single-commit-at-end
        # design held N file moves hostage in memory: if the worker
        # crashed (OOM, container restart, power loss) anywhere during
        # the batch, SQLite would roll back ALL DB writes but the disk
        # moves were already permanent. Library state and DB state
        # diverge silently and the UI starts showing "File not found"
        # on every previously-renamed row. Per-file commits keep DB
        # and disk in lockstep at every checkpoint.
        if not payload.dry_run:
            # #4: clear the write-ahead intent in the SAME commit that persists the
            # MediaFile/RenameHistory changes — so the disk move, the DB row, and
            # the intent's removal all land atomically. If this commit fails, the
            # intent SURVIVES (rolled back to its committed state) and reconcile
            # finalizes from disk on next boot — exactly the recovery we want.
            if intent is not None:
                await session.delete(intent)
            try:
                await session.commit()
            except Exception as e:
                # The disk move succeeded but the row update couldn't
                # persist. This is the most dangerous failure mode in
                # the entire endpoint — surface it loudly in the
                # result rather than swallowing.
                logger.warning(f"rename: per-file commit failed for {fid}: {e!r}")
                results.append(RenameItemResult(
                    file_id=fid, ok=False, old_path=str(src), new_path=str(target),
                    error=(
                        f"File moved to {target} but database update failed: {e}. "
                        f"Manual recovery may be needed — re-scan the library to resync."
                    ),
                ))
                # Try to roll back the in-memory session state so the
                # next iteration starts clean.
                try:
                    await session.rollback()
                except Exception:
                    pass
                return
        # Successful episode rename → remember the series for the Sonarr
        # rescan hook (movies don't apply; Sonarr keys series by TVDB id).
        if (
            selected.match_type == "tv_episode"
            and selected.provider and selected.provider_id
        ):
            renamed_episode_series.add((selected.provider, str(selected.provider_id)))
        results.append(RenameItemResult(
            file_id=fid, ok=True, old_path=str(src), new_path=str(target),
            error=sidecar_msg,
        ))

    # Surface rename progress on the activity pill (a season's worth of files +
    # artwork/subtitle fetches takes real time — the user needs feedback). Only
    # for a real run; a dry-run preview is instant. Best-effort: the import +
    # begin/progress/end never affect the rename outcome.
    _track = (not payload.dry_run) and bool(payload.file_ids)
    if _track:
        from kira import activity
        n = len(payload.file_ids)
        activity.begin("rename", f"Renaming {n} file{'' if n == 1 else 's'}", total=n)
    try:
        for fid in payload.file_ids:
            try:
                f = by_id.get(fid)
                if f is None:
                    results.append(RenameItemResult(file_id=fid, ok=False, error="File not found"))
                elif not f.parsed_data:
                    results.append(RenameItemResult(file_id=fid, ok=False, error="File not parsed"))
                else:
                    await _rename_one_file(f, fid)
            except Exception as e:
                # One file's UNEXPECTED error must NEVER abort the whole batch.
                # It used to escape `_rename_one_file` → out of `perform_rename`
                # → 500, leaving the rest of the season unprocessed (stuck at
                # "approved") while the activity pill's `finally` still flashed
                # "done". Contain it here: roll back any half-applied per-file
                # transaction so the next file + the summary commit don't inherit
                # a poisoned session, then record a per-file failure so the user
                # sees WHICH file broke and why (instead of a blank 500).
                logger.exception(f"rename: unexpected error on file {fid}")
                try:
                    await session.rollback()
                except Exception:
                    pass
                if not any(r.file_id == fid for r in results):
                    results.append(RenameItemResult(
                        file_id=fid, ok=False, error=f"Unexpected error: {e}"))
            if _track:
                activity.progress("rename", len(results))
    finally:
        if _track:
            activity.end("rename")

    if not payload.dry_run:
        succeeded = sum(1 for r in results if r.ok)
        failed = len(results) - succeeded
        if succeeded:
            session.add(Notification(
                kind="success",
                title=f"Renamed {succeeded} file{'' if succeeded == 1 else 's'}",
                body=f"Operation: {op.value} · Profile: {payload.profile}",
            ))
        if failed:
            session.add(Notification(
                kind="error",
                title=f"{failed} file{'' if failed == 1 else 's'} failed to rename",
                body=", ".join(r.error or "" for r in results if not r.ok)[:300],
            ))
        # Final commit just for the summary notifications — per-file
        # MediaFile / RenameHistory writes have already been persisted
        # inside the loop, so a failure here only loses the notification
        # rows (annoying, not catastrophic).
        await session.commit()

        # ── In-place junk sweep (same-folder rename case) ──────────────────
        # The source-folder walk above only cleans VACATED folders, so a file
        # renamed IN PLACE — or any destination folder that keeps its media —
        # never gets its leftovers swept. Do that here: sweep each folder a file
        # landed in, honoring the SAME cleanup mode + custom lists, while
        # protecting media, everything Kira wrote this batch (inplace_protected),
        # and Kira's own artwork/NFO output names. FS-only + best-effort; gated by
        # the same master + artifact toggles as the source walk. Skipped on dry-run
        # (we're inside `if not payload.dry_run`).
        if cleanup_empty_source and cleanup_artifacts:
            try:
                from kira.renamer.operations import sweep_destination_junk
                _prot = frozenset(inplace_protected)
                _dest_folders = {Path(r.new_path).parent for r in results if r.ok and r.new_path}
                _swept = 0
                for _folder in _dest_folders:
                    _swept += sweep_destination_junk(
                        _folder, mode=cleanup_nonvideo,
                        extra_names=cleanup_extra_names, extra_exts=cleanup_extra_exts,
                        trash_root=cleanup_trash_dir, protected=_prot,
                    )
                if _swept:
                    logger.info(
                        f"rename: in-place junk sweep removed {_swept} file"
                        f"{'' if _swept == 1 else 's'} across {len(_dest_folders)} folder"
                        f"{'' if len(_dest_folders) == 1 else 's'}")
            except Exception as e:
                logger.warning(f"rename: in-place junk sweep failed (non-fatal): {e!r}")

        # ── Pass 6 post-rename hooks (best-effort, run in the BACKGROUND) ──
        # Defined here; SCHEDULED below. Everything in this hook is network-
        # bound — for a full season the per-episode subtitle auto-fetch alone
        # runs for minutes — so it must NOT block the /rename response. We hand
        # it to a tracked background task and return the instant the files are
        # on disk; the activity pill narrates the subtitle phase as it fills the
        # sidecars in. The files are already moved + their history committed, so
        # the rename is DONE and fully undoable before this even starts.
        async def _post_rename_hooks():
            # Run on a FRESH session: the original request session is already
            # closed by the time this task runs (so its loaded MediaFiles are
            # detached), and the rename loop may also have left it in a pending-
            # rollback state after a caught per-file IntegrityError. A clean
            # session keeps every read below bound to a live connection.
            from kira.database import SessionLocal
            from kira import activity
            async with SessionLocal() as hook_session:
                # Re-load the renamed files + their matches on THIS session, so
                # attribute access in the subtitle fetch (matches, parsed_data,
                # metadata_blob) never touches the closed request session.
                _ok_ids = [r.file_id for r in results if r.ok and r.new_path]
                by_id = {
                    f.id: f for f in await hook_session.scalars(
                        select(MediaFile)
                        .options(selectinload(MediaFile.matches))
                        .where(MediaFile.id.in_(_ok_ids))
                    )
                }
                # NOTE: media-server refresh (Plex/Jellyfin) + Sonarr rescan run
                # AFTER the subtitle auto-fetch below — see the ORDERING block at
                # the end. Firing them here would make the media server index the
                # renamed file BEFORE its .srt sidecars exist, so Plex/Jellyfin
                # would miss the subtitles until their next (much later) scan.
                # #10: fan out the summary to external sinks (Discord / webhook).
                try:
                    from kira import notify
                    await notify.fan_out(
                        "success",
                        f"Renamed {succeeded} file{'' if succeeded == 1 else 's'}",
                        f"Operation: {op.value} · Profile: {payload.profile}"
                        + (f" · {failed} failed" if failed else ""),
                    )
                except Exception as e:
                    logger.warning(f"rename: notification fan-out failed (non-fatal): {e!r}")
                # #11: auto-fetch subtitles for the renamed files (opt-in). The
                # aggregator runs the enabled sources cheapest/most-reliable first
                # (embedded → OpenSubtitles → YIFY), each skipping a language already
                # on disk so they compose without duplicating. Best-effort — a
                # subtitle failure never affects the rename result.
                try:
                    if await _resolve_bool_setting(hook_session, "subtitles.auto_fetch", False):
                        from kira import net
                        from kira.subtitles.aggregate import fetch_subtitles
                        from kira.subtitles.model import SearchContext
                        from kira.subtitles.prefs import load_subtitle_prefs
                        # ONE loader for every subtitle setting — the same view
                        # the backfill + per-file fetch use, so all sources
                        # (embedded → OpenSubtitles → SubDL → Podnapisi →
                        # SubSource → AnimeTosho → YIFY) run consistently here too.
                        prefs = await load_subtitle_prefs(hook_session)
                        sub_langs = prefs.languages
                        if sub_langs and prefs.any_source_enabled:
                            # Each file's subtitle work is INDEPENDENT, so run with
                            # bounded concurrency; the shared client keeps the pool
                            # warm across the batch.
                            _sc = net.shared_client()
                            _conc = await _resolve_int_setting(
                                hook_session, "rename.concurrency", 4, lo=1, hi=32)
                            _sem = asyncio.Semaphore(_conc)

                            # Shared stop-flag: OpenSubtitles quota/auth failures hit
                            # every remaining file the same way, so the first one stops
                            # the batch (instead of hammering a dead / 429 API) and
                            # surfaces ONE notification — mirroring the backfill path.
                            from kira.subtitles.errors import AuthRejected, QuotaExceeded
                            _stop = {"reason": None}
                            # video new_path -> [.srt paths auto-fetch wrote]; recorded on
                            # each rename's history row after the batch so undo deletes them.
                            _sub_assets: dict[str, list[str]] = {}
                            # file_id -> (title, results): recorded into the Subtitles
                            # ledger after the batch (same as backfill / manual fetch) so an
                            # auto-fetch is never invisible. `_done` drives live narration.
                            _sub_results: dict[int, tuple] = {}
                            _done = {"n": 0}

                            async def _fetch_one(r):
                                if _stop["reason"]:
                                    return
                                f = by_id.get(r.file_id)
                                sel = next((m for m in (f.matches if f else []) if m.is_selected), None) if f else None
                                tmdb_id = (int(sel.provider_id) if sel and sel.provider == "tmdb"
                                           and (sel.provider_id or "").isdigit() else None)
                                anidb_id = (int(sel.provider_id) if sel and sel.provider == "anidb"
                                            and (sel.provider_id or "").isdigit() else None)
                                imdb_id = None
                                if sel and isinstance(getattr(sel, "metadata_blob", None), dict):
                                    imdb_id = sel.metadata_blob.get("imdbid") or sel.metadata_blob.get("imdb_id")
                                # Parsed (rendered-filename) S/E beats cour-local
                                # match numbers (see backfill for the AoT case).
                                _pd = f.parsed_data if f and isinstance(f.parsed_data, dict) else {}
                                _season = _pd.get("season")
                                _episode = _pd.get("episode")
                                if _season is None and sel is not None:
                                    _season = sel.season_number
                                if _episode is None and sel is not None:
                                    _episode = sel.episode_number
                                _query = sel.title if sel and sel.title else None
                                _ctx = SearchContext(
                                    video_path=r.new_path, languages=sub_langs,
                                    media_type=f.media_type if f else None, query=_query,
                                    tmdb_id=tmdb_id, imdb_id=imdb_id, anidb_id=anidb_id,
                                    season=_season, episode=_episode, parsed=_pd,
                                    os_api_key=prefs.api_key, os_user=prefs.username, os_pw=prefs.password,
                                    subdl_api_key=prefs.subdl_api_key, subsource_api_key=prefs.subsource_api_key,
                                    hearing_impaired=prefs.hearing_impaired or "", forced=prefs.forced or "",
                                    # Per-type (and global) score floor — same as backfill /
                                    # manual; without this the auto path saved ANY-scoring sub.
                                    min_score=prefs.min_score_for(f.media_type if f else None),
                                )
                                async with _sem:
                                    if _stop["reason"]:
                                        return
                                    try:
                                        _res = await fetch_subtitles(_sc, _ctx, enabled=prefs.sources_for(_ctx.media_type))
                                        if _res:
                                            _sub_assets[r.new_path] = [x.path for x in _res if x.path]
                                            _sub_results[r.file_id] = (
                                                (sel.series_name or sel.title) if sel else None, _res)
                                    except QuotaExceeded:
                                        _stop["reason"] = "quota"
                                    except AuthRejected:
                                        _stop["reason"] = "auth"
                                    finally:
                                        # Narrate progress so the pill shows the work — a
                                        # silent background download is what felt "stuck".
                                        _done["n"] += 1
                                        activity.progress("subtitles", _done["n"], _total)

                            targets = [r for r in results if r.ok and r.new_path]
                            _total = len(targets)
                            if _total:
                                activity.progress("subtitles", 0, _total)  # begin the visible phase
                            await asyncio.gather(*(_fetch_one(r) for r in targets), return_exceptions=True)
                            # Surface every fetch in History → Subtitles, exactly like the
                            # backfill / manual paths. Sequential because record_results
                            # commits and the shared hook_session isn't concurrency-safe.
                            if _sub_results:
                                try:
                                    from kira.subtitles import store as _substore
                                    for _fid, (_title, _res) in _sub_results.items():
                                        await _substore.record_results(hook_session, _fid, _title, _res)
                                except Exception as e:
                                    logger.warning(f"rename: recording subtitle history failed (non-fatal): {e!r}")
                            if _total:
                                _n = sum(len(v[1]) for v in _sub_results.values())
                                activity.end("subtitles", ok=not _stop["reason"],
                                             detail=(f"Fetched {_n} subtitle{'' if _n == 1 else 's'} for "
                                                     f"{len(_sub_results)} file{'' if len(_sub_results) == 1 else 's'}"))
                            # Record what auto-fetch wrote on each rename's history row so
                            # UNDO removes the .srt sidecars too — they're Kira-created,
                            # exactly like the NFO/artwork, and were being orphaned before.
                            if _sub_assets:
                                try:
                                    from sqlalchemy.orm.attributes import flag_modified
                                    _hrows = list(await hook_session.scalars(
                                        select(RenameHistory).where(
                                            RenameHistory.new_path.in_(list(_sub_assets)),
                                            RenameHistory.parent_id.is_(None),
                                            RenameHistory.undone_at.is_(None),
                                        )
                                    ))
                                    for _hr in _hrows:
                                        _extra = _sub_assets.get(_hr.new_path) or []
                                        if _extra:
                                            _cur = list(_hr.created_assets or [])
                                            _cur.extend(p for p in _extra if p not in _cur)
                                            _hr.created_assets = _cur
                                            flag_modified(_hr, "created_assets")
                                    await hook_session.commit()
                                except Exception as e:
                                    logger.warning(f"rename: recording auto-fetched subs failed (non-fatal): {e!r}")
                            if _stop["reason"]:
                                # Don't leave a silently sub-less batch — tell the user
                                # WHY and that a backfill will finish the rest once fixed.
                                from kira.database import SessionLocal as _SL
                                _body = ("OpenSubtitles' daily quota is exhausted — the rest of this batch was skipped. "
                                         "They'll fill in on the next subtitle backfill once your quota resets."
                                         if _stop["reason"] == "quota" else
                                         "OpenSubtitles rejected your API key — replace it in Settings → Connections, "
                                         "then run a subtitle backfill to fill the rest.")
                                try:
                                    async with _SL() as _ns:
                                        _ns.add(Notification(kind="warning", title="Subtitle auto-fetch stopped", body=_body))
                                        await _ns.commit()
                                except Exception:
                                    pass
                except Exception as e:
                    logger.warning(f"rename: subtitle auto-fetch failed (non-fatal): {e!r}")

                # ── ORDERING: media-server / Sonarr rescan LAST ──────────────
                # Now that the files are renamed AND their subtitle sidecars are
                # written, tell the media servers to re-index — so Plex/Jellyfin
                # pick up the file and its subtitles in a single scan instead of
                # indexing a sub-less file and missing the .srt until next time.
                # #9: nudge Plex/Jellyfin to re-scan so the renames show up now.
                try:
                    from kira.integrations.media_server import refresh_all
                    await refresh_all(hook_session)
                except Exception as e:
                    logger.warning(f"rename: media-server refresh failed (non-fatal): {e!r}")
                # Sonarr rescan: tell Sonarr to re-scan each renamed series NOW,
                # so it re-links the files under their new names instead of its
                # next disk scan seeing the old paths gone → "episode file
                # deleted" → re-grabbing monitored episodes. Best-effort: skips
                # silently when Sonarr isn't configured or a series isn't in it.
                if renamed_episode_series:
                    try:
                        from kira.api.integrations import _load_sonarr_config
                        from kira.integrations import sonarr as sonarr_mod
                        from kira.providers.anime_mappings import AnimeMappings
                        try:
                            sonarr_cfg = await _load_sonarr_config(hook_session)
                        except Exception:
                            sonarr_cfg = None  # not configured — nothing to do
                        if sonarr_cfg is not None:
                            tvdb_ids: set[int] = set()
                            for prov, pid in renamed_episode_series:
                                try:
                                    if prov == "tvdb":
                                        tvdb_ids.add(int(pid))
                                    elif prov == "anidb":
                                        t = await AnimeMappings.tvdb_id(int(pid))
                                        if t:
                                            tvdb_ids.add(int(t))
                                except (TypeError, ValueError):
                                    continue
                            for tid in tvdb_ids:
                                ok = await sonarr_mod.rescan_series_by_tvdb(sonarr_cfg, tid)
                                if ok:
                                    logger.info(f"rename: sonarr rescan queued for tvdb {tid}")
                    except Exception as e:
                        logger.warning(f"rename: sonarr rescan hook failed (non-fatal): {e!r}")

        # The files are moved and history is committed → the rename is DONE and
        # undoable right now. Schedule the network tail (notify → subtitle auto-
        # fetch → media-server refresh → Sonarr) as a tracked background task and
        # return immediately, so a big season no longer blocks the request for
        # minutes. The ordering invariant (subtitles BEFORE media-server refresh)
        # holds because the task runs its steps sequentially. Tests/shutdown can
        # await it via kira.tasks.drain_background_tasks().
        if succeeded:
            from kira.tasks import spawn_tracked
            spawn_tracked(_post_rename_hooks(), "rename-post-hooks")

    succeeded = sum(1 for r in results if r.ok)
    return RenameResult(
        succeeded=succeeded,
        failed=len(results) - succeeded,
        items=results,
    )


_ = datetime, timezone, Any  # keep imports referenced for future use
