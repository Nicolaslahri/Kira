"""Rename endpoint — executes the actual file operations."""

from __future__ import annotations

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
from kira.models import Match, MediaFile, Notification, RenameHistory
from kira.parser import ParsedFile
from kira.renamer import (
    DEFAULT_PROFILES,
    FileOp,
    NamingProfile,
    compute_sidecar_target,
    discover_sidecars,
    execute_op,
    format_target_path,
)

router = APIRouter(prefix="/rename", tags=["rename"])


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

    Strategy: probe the volume root. iterating it (one entry, or a clean
    StopIteration on an empty drive) means the mount is responsive.
    OSError = the mount itself is unreachable; no claim about files
    beneath it should be trusted.
    """
    try:
        if os.name == "nt":
            drive = src.drive  # e.g. "Z:"
            if not drive:
                # UNC path (\\server\share\…). Probe the share root.
                anchor = src.anchor  # "\\\\server\\share\\"
                if not anchor:
                    return True  # nothing to probe — assume reachable
                return Path(anchor).exists()
            root = Path(drive + "\\")
            it = iter(root.iterdir())  # raises OSError if drive unmounted
            try:
                next(it)
            except StopIteration:
                pass  # empty drive but mounted — that's fine
            return True
        # POSIX: walk up to the mount anchor and confirm it's a directory.
        anchor = src.anchor or "/"
        return Path(anchor).is_dir()
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
    row = await session.get(Setting, "rename.mode")
    if not row:
        return "in-place"
    val = row.value
    if isinstance(val, str) and val.strip():
        return val.strip()
    if isinstance(val, dict):
        v = val.get("value")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "in-place"


async def _resolve_cleanup_empty_dirs(session: AsyncSession) -> bool:
    """Setting toggle: clean up empty source folders after a Move?

    SAFETY: default is now FALSE. The previous default-on caused real
    user pain — the walker rmdir'd up to 6 levels of empty ancestors,
    which on a heavily-renamed library could wipe Show/Season/Type
    folders the user expected to keep. Users must explicitly opt in
    via Settings → Folder cleanup → "Remove empty folders after Move".
    """
    from kira.models import Setting
    row = await session.get(Setting, "rename.cleanup_empty_source_dirs")
    if not row:
        return False  # safety default
    val = row.value
    if isinstance(val, bool):
        return val
    if isinstance(val, dict):
        v = val.get("value")
        if isinstance(v, bool):
            return v
    return False


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


@router.post("", response_model=RenameResult)
async def rename(
    payload: RenameRequest,
    session: AsyncSession = Depends(get_session),
) -> RenameResult:
    op = FileOp(payload.op)
    profile = await _resolve_profile(session, payload.profile)
    library_root = await _resolve_library_root(session, payload.library_root_name)
    cleanup_empty_source = await _resolve_cleanup_empty_dirs(session)
    # Sub-toggle: when cleanup_empty_source is on, ALSO sweep Plex/
    # Jellyfin/Kodi metadata artifacts (poster.jpg, *-thumb.jpg, etc.)
    # so the rmdir actually succeeds. Default-on; user can disable in
    # Settings → Folder cleanup if they want strict "only touch what's
    # already empty" semantics.
    cleanup_artifacts = await _resolve_cleanup_artifacts(session)
    rename_mode = await _resolve_rename_mode(session)

    files = list(await session.scalars(
        select(MediaFile)
        .options(selectinload(MediaFile.matches))
        .where(MediaFile.id.in_(payload.file_ids))
    ))
    by_id = {f.id: f for f in files}

    results: list[RenameItemResult] = []
    for fid in payload.file_ids:
        f = by_id.get(fid)
        if f is None:
            results.append(RenameItemResult(file_id=fid, ok=False, error="File not found"))
            continue
        if not f.parsed_data:
            results.append(RenameItemResult(file_id=fid, ok=False, error="File not parsed"))
            continue
        parsed = ParsedFile(**f.parsed_data)
        selected: Match | None = next((m for m in f.matches if m.is_selected), None)
        if selected is None and f.matches:
            selected = f.matches[0]
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
            continue
        library_title = selected.title or parsed.title
        library_year = selected.year if selected.year is not None else parsed.year

        # Fix #3: AniDB franchise collapse. AniDB treats every sequel
        # season as a separate AID with its own display title — Frieren
        # S1 = AID 17617 ("Frieren: Beyond Journey's End"), S2 = AID 18886
        # ("Sousou no Frieren (2026)"), S3 = AID 19977 ("Sousou no Frieren
        # (2027)"). Without this collapse, each season produces a
        # DIFFERENT show-folder name on disk and the user ends up with 3+
        # folders for the same franchise. We use Match.series_group_id
        # (the canonical-AID identity that's already computed at scan
        # time, `anidb:<lowest_aid>` for the franchise) to look up the
        # franchise root's title — the S1 title that all sequels collapse
        # under. Pure in-memory; zero HTTP; safe during AniDB bans.
        #
        # CRITICAL: SKIP THIS WHEN selected.is_manual IS TRUE. The user
        # explicitly chose this show via Re-identify; their pick MUST
        # win. Pre-fix bug: user picked "Bleach: Thousand Year Blood War"
        # (AID 15449), but the canonical AID for the Bleach franchise is
        # the original 2004 show (AID 269 — way lower number). The collapse
        # lookup returned "Bleach", overwriting the user's pick. Resulting
        # folder: "Z:/anime/Bleach/" instead of "Z:/anime/Bleach: Thousand
        # Year Blood War/" as the popup promised. Now: manual pins bypass
        # the collapse and the chosen title flows through verbatim.
        if (
            selected.provider == "anidb"
            and selected.series_group_id
            and not selected.is_manual
        ):
            try:
                # series_group_id format: "anidb:<canonical_aid>"
                root_aid_str = selected.series_group_id.split(":", 1)[-1]
                root_aid = int(root_aid_str)
                from kira.providers.anidb import AniDBProvider
                root_title = AniDBProvider._pick_display_title(root_aid)
                if root_title:
                    library_title = root_title
            except (ValueError, AttributeError):
                pass  # series_group_id wasn't well-formed; keep original title

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
        type_target_root: str | None = None
        if rename_mode == "in-place":
            type_target_root = _compute_inplace_target_root(
                Path(f.file_path), parsed, profile,
                library_title=library_title,
                library_year=library_year,
                episode_title=selected.episode_title,
                season_override=selected.season_number,
            )
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
                episode_title=selected.episode_title,
                season_override=selected.season_number,
                type_target_root=type_target_root,
                metadata=_meta,
                file_size=f.file_size,
            )
        except Exception as e:
            results.append(RenameItemResult(file_id=fid, ok=False, error=f"Template error: {e}"))
            continue

        src = Path(f.file_path)
        if payload.dry_run:
            results.append(RenameItemResult(
                file_id=fid, ok=True, old_path=str(src), new_path=str(target),
            ))
            continue

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
                continue
            if target.exists():
                f.status = "renamed"
                # Update the file_path so future actions point at where
                # the file actually lives.
                f.file_path = str(target)
                results.append(RenameItemResult(
                    file_id=fid, ok=True, old_path=str(src), new_path=str(target),
                    error="[PHANTOM] Already at target — no move performed.",
                ))
                continue
            # Source missing AND target missing AND FS is alive — genuine
            # "file is gone". Mark as renamed anyway (nothing to do) but
            # flag in the response so the user knows the row was
            # reconciled, not actually moved.
            f.status = "renamed"
            results.append(RenameItemResult(
                file_id=fid, ok=True, old_path=str(src), new_path=str(src),
                error="[PHANTOM] Source missing — marked as renamed (no file to move).",
            ))
            continue

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
                overwrite=payload.overwrite,
                cleanup_empty_source=cleanup_empty_source,
                cleanup_stop_at=Path(library_root) if library_root else None,
                cleanup_max_levels=cleanup_max_levels,
                cleanup_artifacts=cleanup_artifacts,
            ) or 0
        except Exception as e:
            results.append(RenameItemResult(
                file_id=fid, ok=False, old_path=str(src), new_path=str(target),
                error=str(e),
            ))
            continue

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
            print(f"rename: sidecar discovery failed for {fid}: {e!r}")
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
                print(f"rename: flush before sidecars failed for {fid}: {e!r}")
                parent_history_id = None

            moved_subs = 0
            failed_subs: list[str] = []
            for sidecar in sidecars:
                sub_target = compute_sidecar_target(sidecar, src, target)
                if sub_target is None:
                    continue
                try:
                    # Same cleanup_empty_source policy as the video. Last
                    # operation in the parent folder triggers the cleanup
                    # naturally — the rmdir refuses non-empty dirs so
                    # mid-batch sidecar moves never accidentally trip it.
                    sub_artifacts_cleaned = await asyncio.to_thread(
                        execute_op,
                        op, sidecar, sub_target,
                        overwrite=payload.overwrite,
                        cleanup_empty_source=cleanup_empty_source,
                        cleanup_stop_at=Path(library_root) if library_root else None,
                        cleanup_max_levels=cleanup_max_levels,
                        cleanup_artifacts=cleanup_artifacts,
                    ) or 0
                    # Aggregate artifact count across all sidecar moves
                    # for this video. The LAST sidecar move triggers the
                    # actual rmdir-walk (the directory becomes empty
                    # then), so most of the cleanup count usually shows
                    # up there — but counting per-call is robust to any
                    # ordering.
                    video_artifacts_cleaned += sub_artifacts_cleaned
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
            try:
                await session.commit()
            except Exception as e:
                # The disk move succeeded but the row update couldn't
                # persist. This is the most dangerous failure mode in
                # the entire endpoint — surface it loudly in the
                # result rather than swallowing.
                print(f"rename: per-file commit failed for {fid}: {e!r}")
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
                continue
        results.append(RenameItemResult(
            file_id=fid, ok=True, old_path=str(src), new_path=str(target),
            error=sidecar_msg,
        ))

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

    succeeded = sum(1 for r in results if r.ok)
    return RenameResult(
        succeeded=succeeded,
        failed=len(results) - succeeded,
        items=results,
    )


_ = datetime, timezone, Any  # keep imports referenced for future use
