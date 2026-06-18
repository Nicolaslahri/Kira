"""Issue #4 from the friction audit: Plex/Jellyfin/Kodi media-server
artifacts left in source folders after a rename used to block the
empty-parent cleanup walk and litter the library with dangling folders.

These tests exercise the artifact-cleanup helpers against the layouts
Plex / Jellyfin / Kodi actually create. Pure filesystem operations
against `tmp_path` — no DB, no async.
"""
from __future__ import annotations

import os
from pathlib import Path

from kira.renamer.operations import (
    FileOp,
    _cleanup_empty_source_parents,
    _cleanup_media_server_artifacts,
    _is_artifact_dir,
    _is_artifact_file,
    execute_op,
)


def _touch(p: Path, content: str = "") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


# ─────────────────────────────────────────────────────────────────────
# Predicate helpers — the cheapest, fastest signal that the allow-lists
# match real-world naming.
# ─────────────────────────────────────────────────────────────────────


def test_is_artifact_file_known_names() -> None:
    """Every name on the curated list of Plex/Jellyfin/Kodi artwork +
    NFO files should be recognized — case-insensitively, since Plex on
    Windows happily writes 'Poster.jpg' with title-case."""
    yes = [
        "poster.jpg", "Poster.jpg", "POSTER.JPG",
        "banner.png", "fanart.jpeg", "background.jpg",
        "backdrop.jpg", "Backdrop.JPG", "backdrop.png", "backdrop.jpeg",
        "clearart.png", "clearlogo.png",
        "landscape.jpg", "thumb.png", "logo.jpg",
        "disc.jpg", "keyart.png", "characterart.jpg",
        "folder.jpg", "cover.png",
        "tvshow.nfo", "Tvshow.NFO",
        "season.nfo", "movie.nfo", "show.nfo",
        "album.nfo", "artist.nfo",
        # Season-numbered artwork patterns
        "season01-poster.jpg", "Season02-banner.png",
        "season-specials-poster.jpg", "season-all-fanart.jpg",
    ]
    for name in yes:
        assert _is_artifact_file(name), f"expected artifact: {name}"


def test_is_artifact_file_keeps_user_content() -> None:
    """User content should never be on the artifact list — these are
    the false positives we'd be most worried about."""
    no = [
        # Generic media
        "Show.S01E01.mkv", "Movie.mkv", "audio.flac",
        # Subtitle sidecars (Kira already handles these separately)
        "Show.S01E01.srt", "Show.S01E01.eng.ass",
        # Random user files
        "readme.txt", "notes.md", "playlist.m3u",
        # Anything that LOOKS like a poster but isn't on the list
        "fan_art_compilation.jpg",
        "movieposter.jpg",  # no underscore-separation; not standard naming
        "season1poster.jpg",  # no dash between "season1" and "poster"
        "season01poster.jpg",  # same
    ]
    for name in no:
        assert not _is_artifact_file(name), f"should not be artifact: {name}"


def test_is_artifact_file_nfo_catchall() -> None:
    """ALL .nfo files classify as artifacts — including per-episode ones.
    Safe because classification is only consulted behind the
    `_is_artifacts_only` gate (every video already moved out), where any
    remaining NFO is orphaned metadata. This flipped from the earlier
    "episode NFOs are user content" stance after Kira's OWN NFO output
    blocked the cleanup walk: renaming a season out of a folder left
    `<episode>.nfo` + `-poster.jpg` + `-thumb.jpg` behind, and the lone
    NFO kept the dead folder alive forever."""
    yes = [
        "Show.S01E01.nfo",
        "Attack on Titan - S05E60 - The Other Side of the Sea.nfo",
        "Movie (2023).NFO",       # case-insensitive
        "release-group.nfo",      # scene NFO junk
    ]
    for name in yes:
        assert _is_artifact_file(name), f"expected .nfo artifact: {name}"
    assert not _is_artifact_file("Show.S01E01.nfo.bak")


def test_is_artifact_dir_known_caches() -> None:
    """Kodi/Plex/Jellyfin cache directories that are safe to nuke."""
    yes = [
        ".actors", ".Actors", ".ACTORS",
        ".metadata", "extrafanart", "extrathumbs",
        "backdrops", "Backdrops", "metadata",
    ]
    for name in yes:
        assert _is_artifact_dir(name), f"expected cache dir: {name}"


def test_is_artifact_dir_keeps_user_dirs() -> None:
    """Directories that could plausibly hold user content stay
    untouched — Subs/ (manually downloaded subtitles), Extras/ /
    Featurettes/ / Trailers/ (could be user-curated), etc."""
    no = [
        "Season 01", "Season 1", "Specials",
        "Subs", "Subtitles",
        "Extras", "Featurettes", "Trailers", "Behind The Scenes",
        "Bonus", "Movies", "TV",
    ]
    for name in no:
        assert not _is_artifact_dir(name), f"should not be cache dir: {name}"


def test_is_artifact_file_per_episode_thumbs() -> None:
    """The most common form of leftover garbage in real libraries:
    per-episode/per-file artwork following the `<stem>-<type>.<ext>`
    convention that Sonarr/Jellyfin/Plex/Kodi all use.

    The first entry is the EXACT filename from the user's screenshot —
    the regression test for the audit's Issue #4 expansion."""
    yes = [
        # User's actual screenshot — Sonarr per-episode thumb
        "Frieren.Beyond.Journeys.End.S02E01.2026.2160p.IQ.WEB-DL.DDP2.0.H.265-HDSWEB-thumb.jpg",
        # Shorter forms
        "Show.S01E05-thumb.jpg",
        "Show.S01E05-thumb.png",
        "Movie (2023)-poster.jpg",
        "Album-fanart.png",
        "Episode-landscape.jpg",
        "ShowName-clearart.png",
        "Anything-clearlogo.png",
        "Anything-keyart.jpg",
        "Anything-characterart.jpg",
        # Numbered alternate artwork (e.g. `-fanart-2.jpg`)
        "Show.S01E01-fanart-2.jpg",
        "Show.S01E01-poster-3.png",
        # Case variants
        "MOVIE-POSTER.JPG",
        "Show-Thumb.PNG",
    ]
    for name in yes:
        assert _is_artifact_file(name), f"expected per-file artifact: {name}"


def test_is_artifact_file_per_file_pattern_keeps_user_content() -> None:
    """The per-file regex is the broadest in the system — risk of
    false positives is real. These names look related but MUST be
    preserved. The user's stray photos / personal images live here."""
    no = [
        # No dash before the artwork suffix → not the convention
        "movieposter.jpg",
        "season01poster.jpg",
        "fanartshot.jpg",
        # Different extension → user's edited preview, not media-server output
        "Movie-poster.jpg.bak",
        "Show-thumb.jpg.tmp",
        # Suffix is BEFORE the dash, not after → user named it weirdly
        # but the convention is `-suffix-as-ending`
        "thumb-extra.jpg",
        "poster-edit.jpg",
        # Empty stem before the dash (regex requires .+ = 1+ chars)
        "-thumb.jpg",
        # Title contains an artwork word but isn't the suffix
        "fan-art.jpg",          # "art" alone isn't a recognized suffix
        "thumbnail-list.jpg",   # "list" isn't a suffix
        # Generic user files that happen to be jpg
        "screenshot.jpg",
        "vacation.jpg",
        "DCIM_0001.jpg",
    ]
    for name in no:
        assert not _is_artifact_file(name), f"should not match: {name}"


def test_is_artifact_file_tbn_catchall() -> None:
    """Kodi's legacy `.tbn` (binary thumbnail) format. Always an
    artifact regardless of basename — there's no legitimate non-Kodi
    use of `.tbn` in media libraries."""
    yes = [
        "Show.S01E01.tbn",
        "Movie.tbn",
        "Anything.TBN",   # case-insensitive
        "x.tbn",          # bare minimum
    ]
    for name in yes:
        assert _is_artifact_file(name), f"expected .tbn artifact: {name}"
    # Wrong extension should NOT match
    assert not _is_artifact_file("Show.S01E01.tbn.bak")
    assert not _is_artifact_file("Show.tbnsomething.jpg")


# ─────────────────────────────────────────────────────────────────────
# _cleanup_media_server_artifacts — single-directory sweep, no
# parent-walk yet. Tests the surgical "what gets deleted in one dir"
# behaviour.
# ─────────────────────────────────────────────────────────────────────


def test_cleanup_single_dir_removes_artifacts(tmp_path: Path) -> None:
    """Standard Plex-touched show folder: poster + banner + tvshow.nfo
    + an .actors/ subdir. All four should be deleted; user content
    (the .mkv) stays."""
    folder = tmp_path / "Old Show"
    _touch(folder / "poster.jpg")
    _touch(folder / "banner.jpg")
    _touch(folder / "tvshow.nfo", "<show>...</show>")
    _touch(folder / ".actors" / "actor1.jpg")
    _touch(folder / ".actors" / "actor2.jpg")
    user_file = _touch(folder / "Show.S01E01.mkv")

    count = _cleanup_media_server_artifacts(folder)

    assert count == 4   # 3 files + 1 dir-as-unit
    assert user_file.exists()
    assert not (folder / "poster.jpg").exists()
    assert not (folder / "banner.jpg").exists()
    assert not (folder / "tvshow.nfo").exists()
    assert not (folder / ".actors").exists()


def test_cleanup_single_dir_keeps_user_subdirs(tmp_path: Path) -> None:
    """User-curated subfolders MUST stay even when the parent contains
    artifacts. Subs/ is the classic case — a user's manually-acquired
    subtitle library; deleting it would be catastrophic."""
    folder = tmp_path / "Show"
    _touch(folder / "poster.jpg")
    user_subs = _touch(folder / "Subs" / "manual.srt")
    user_extras = _touch(folder / "Featurettes" / "behind.mkv")

    count = _cleanup_media_server_artifacts(folder)

    assert count == 1   # only poster.jpg
    assert user_subs.exists()
    assert user_extras.exists()


def test_cleanup_season_numbered_artwork(tmp_path: Path) -> None:
    """Kodi's season-NN artwork lives in the SHOW root, not the season
    subfolder. The regex pattern needs to catch them all."""
    folder = tmp_path / "Show"
    season_arts = [
        _touch(folder / "season01-poster.jpg"),
        _touch(folder / "season02-banner.jpg"),
        _touch(folder / "season-specials-poster.jpg"),
        _touch(folder / "Season03-Fanart.png"),  # mixed case
    ]

    count = _cleanup_media_server_artifacts(folder)

    assert count == len(season_arts)
    for art in season_arts:
        assert not art.exists(), f"should have been deleted: {art.name}"


def test_cleanup_missing_dir_is_safe(tmp_path: Path) -> None:
    """Pointing the helper at a nonexistent dir returns 0, no exception.
    Belt-and-braces: the rename engine might call us on a path that's
    already been rmdir'd by a sibling worker."""
    fake = tmp_path / "does-not-exist"
    assert _cleanup_media_server_artifacts(fake) == 0


def test_cleanup_empty_dir_is_noop(tmp_path: Path) -> None:
    """An empty directory has no artifacts to clean → returns 0."""
    folder = tmp_path / "empty"
    folder.mkdir()
    assert _cleanup_media_server_artifacts(folder) == 0
    assert folder.exists()  # we don't touch the dir itself


# ─────────────────────────────────────────────────────────────────────
# _cleanup_empty_source_parents — the parent-walk, integrating artifact
# sweep + rmdir. This is the function that was broken before this
# iteration: rmdir refused dirs containing artifacts, the walk stopped,
# the user saw dangling folders.
# ─────────────────────────────────────────────────────────────────────


def test_cleanup_per_episode_thumbs_real_scenario(tmp_path: Path) -> None:
    """The user's actual problem: 7 per-episode `*-thumb.jpg` files left
    behind in a season folder after the videos moved out. Pre-fix, the
    cleanup helper didn't recognize the `<stem>-thumb.jpg` pattern, so
    these stayed forever and prevented `rmdir` from removing the
    parent folders.

    This test uses the exact filenames from the user's screenshot."""
    folder = tmp_path / "Frieren - Beyond Journey's End" / "Season 2"
    folder.mkdir(parents=True)
    real_filenames = [
        "Frieren.Beyond.Journeys.End.S02E01.2026.2160p.IQ.WEB-DL.DDP2.0.H.265-HDSWEB-thumb.jpg",
        "Frieren.Beyond.Journeys.End.S02E02.2026.2160p.IQ.WEB-DL.DDP2.0.H.265-HDSWEB-thumb.jpg",
        "Frieren.Beyond.Journeys.End.S02E04.2026.2160p.IQ.WEB-DL.DDP2.0.H.265-HDSWEB-thumb.jpg",
        "Frieren.Beyond.Journeys.End.S02E05.2026.2160p.IQ.WEB-DL.DDP2.0.H.265-HDSWEB-thumb.jpg",
        "Frieren.Beyond.Journeys.End.S02E06.2026.2160p.IQ.WEB-DL.DDP2.0.H.265-HDSWEB-thumb.jpg",
        "Frieren.Beyond.Journeys.End.S02E07.2026.2160p.IQ.WEB-DL.DDP2.0.H.265-HDSWEB-thumb.jpg",
        "Frieren.Beyond.Journeys.End.S02E08.2026.2160p.IQ.WEB-DL.DDP2.0.H.265-HDSWEB-thumb.jpg",
    ]
    for fn in real_filenames:
        _touch(folder / fn)

    count = _cleanup_media_server_artifacts(folder)

    assert count == 7
    for fn in real_filenames:
        assert not (folder / fn).exists(), f"should have been deleted: {fn}"


def test_cleanup_kira_own_sidecar_output_real_scenario(tmp_path: Path) -> None:
    """The user's actual problem (round 2): renaming a synthetic-season
    library (Season 05/06/07 → proper S01–S04) left the OLD season
    folders full of Kira's OWN previous sidecar output — per-episode
    `<title>.nfo` + `-poster.jpg` + `-thumb.jpg`. The images matched the
    per-file artwork pattern, but the episode NFO matched nothing, so
    `_is_artifacts_only` failed and the dead folders survived with ALL
    their debris. Exact filenames from the user's library."""
    library = tmp_path / "media"
    show = library / "anime" / "Attack on Titan"
    season = show / "Season 05"
    for fn in [
        "Attack on Titan - S05E60 - The Other Side of the Sea-poster.jpg",
        "Attack on Titan - S05E60 - The Other Side of the Sea-thumb.jpg",
        "Attack on Titan - S05E60 - The Other Side of the Sea.nfo",
        "Attack on Titan - S05E61 - Midnight Train-poster.jpg",
        "Attack on Titan - S05E61 - Midnight Train.nfo",
    ]:
        _touch(season / fn)
    # The show root keeps OTHER season folders that still hold videos —
    # so the walk must remove Season 05 but leave the show root alone.
    survivor = _touch(show / "Season 04" / "Attack on Titan - S04E01.mkv")

    count = _cleanup_empty_source_parents(
        season, stop_at=library, max_levels=2,
    )

    assert count == 5
    assert not season.exists()
    assert show.exists()          # still holds Season 04
    assert survivor.exists()


def test_parent_walk_unwinds_through_artifacts(tmp_path: Path) -> None:
    """The bug: `Z:\\media\\TV\\Old Show\\Season 01\\` had only
    artifacts left after the videos moved out. Pre-fix, rmdir failed on
    `poster.jpg`, walk stopped, the show folder stranded forever.
    Post-fix: artifacts get swept, rmdir succeeds, walk continues up."""
    library = tmp_path / "media"
    show = library / "TV" / "Old Show"
    season = show / "Season 01"
    # Both show and season hold Plex artifacts
    _touch(show / "poster.jpg")
    _touch(show / "tvshow.nfo")
    _touch(show / ".actors" / "actor.jpg")
    _touch(season / "season01-poster.jpg")
    # The video that started the rename is gone (simulating post-move);
    # season folder has only its season artwork now.

    count = _cleanup_empty_source_parents(
        season, stop_at=library, max_levels=2,
    )

    # 1 (season-poster) + 2 (show files: poster.jpg + tvshow.nfo)
    # + 1 (.actors dir) = 4
    assert count == 4
    assert not season.exists()
    assert not show.exists()
    # But the library root and TV genre folder stay (stop_at + non-empty)
    assert library.exists()


def test_parent_walk_preserves_artifacts_in_surviving_folder(tmp_path: Path) -> None:
    """DATA-LOSS FIX: a folder with a poster + a user file is NOT
    artifacts-only, so cleanup deletes NOTHING and leaves the poster intact.

    Pre-fix the sweep ran before the rmdir check, so it stripped poster.jpg
    even though the folder survived (rmdir failed on the user file) — silent
    loss of a file the user might care about. Now: no removal at all when any
    real content remains."""
    library = tmp_path / "media"
    show = library / "Show"
    poster = _touch(show / "poster.jpg")
    user_file = _touch(show / "user-notes.txt")

    count = _cleanup_empty_source_parents(
        show, stop_at=library, max_levels=2,
    )

    assert count == 0            # nothing deleted — folder isn't artifacts-only
    assert show.exists()         # folder kept (has user content)
    assert user_file.exists()    # never touched
    assert poster.exists()       # FIX: artifact preserved because the folder survives


def test_freeform_user_image_preserved_when_folder_has_content(tmp_path: Path) -> None:
    """A user's free-form 'tour-poster.jpg' (which DOES match the broad
    per-file artifact regex) must not be deleted while its folder still holds
    real content — the data-loss class the audit flagged."""
    library = tmp_path / "media"
    show = library / "Concert Films"
    art = _touch(show / "tour-poster.jpg")      # matches _PER_FILE_ARTIFACT_RE
    keep = _touch(show / "Concert.2024.1080p.mkv")

    count = _cleanup_empty_source_parents(
        show, stop_at=library, max_levels=2,
    )

    assert count == 0
    assert art.exists()          # preserved — folder is not artifacts-only
    assert keep.exists()


def test_parent_walk_stops_at_library_root(tmp_path: Path) -> None:
    """The walk MUST stop at the stop_at boundary — we never want to
    rmdir the user's library root even if it happens to be empty."""
    library = tmp_path / "media"
    show = library / "Show"
    _touch(show / "poster.jpg")
    # No user files anywhere — library would technically be rmdir-able
    # all the way up if the boundary check failed.

    count = _cleanup_empty_source_parents(
        show, stop_at=library, max_levels=5,
    )

    assert count == 1
    assert not show.exists()
    assert library.exists()  # CRITICAL: we never touched the library root


def test_parent_walk_max_levels_cap(tmp_path: Path) -> None:
    """`max_levels=1` means clean exactly one parent and stop, even if
    the next one up would also be empty."""
    library = tmp_path / "media"
    show = library / "Show"
    season = show / "Season 01"
    _touch(season / "season01-poster.jpg")
    _touch(show / "poster.jpg")

    count = _cleanup_empty_source_parents(
        season, stop_at=library, max_levels=1,
    )

    # Only the season level got walked + cleaned
    assert count == 1
    assert not season.exists()
    # Show level stayed despite being now-empty after season's removal
    # — the level cap prevented the walk from continuing.
    assert show.exists()
    assert (show / "poster.jpg").exists()


# ─────────────────────────────────────────────────────────────────────
# execute_op end-to-end — proves the artifact count threads back from
# the rename engine to the caller (used by rename.py to surface in the
# result toast).
# ─────────────────────────────────────────────────────────────────────


def test_execute_op_move_returns_artifact_count(tmp_path: Path) -> None:
    """Move with cleanup_empty_source=True returns the count of
    artifacts cleaned during the parent-walk. This is what rename.py
    forwards into the [ARTIFACTS] note in the result message."""
    library = tmp_path / "media"
    show = library / "Show"
    season = show / "Season 01"
    src = _touch(season / "Show.S01E01.mkv", "video bytes")
    # Plex artifacts in both season and show folders
    _touch(season / "season01-poster.jpg")
    _touch(show / "poster.jpg")
    _touch(show / "tvshow.nfo")

    dst = library / "library" / "Show (2020)" / "Season 01" / "Show - S01E01.mkv"

    cleaned = execute_op(
        FileOp.MOVE, src, dst,
        cleanup_empty_source=True,
        cleanup_stop_at=library,
        cleanup_max_levels=2,
    )

    assert cleaned == 3  # season-poster + show-poster + tvshow.nfo
    assert dst.exists()
    assert not src.exists()
    assert not season.exists()
    assert not show.exists()
    assert library.exists()


def test_execute_op_returns_zero_when_cleanup_disabled(tmp_path: Path) -> None:
    """With cleanup_empty_source=False the cleanup walker never runs,
    no artifacts get swept, return value is 0. The artifacts stay
    intact on disk (we don't surprise-delete metadata)."""
    src = _touch(tmp_path / "src" / "Movie.mkv")
    _touch(tmp_path / "src" / "poster.jpg")
    dst = tmp_path / "dst" / "Movie.mkv"

    cleaned = execute_op(
        FileOp.MOVE, src, dst,
        cleanup_empty_source=False,
    )

    assert cleaned == 0
    assert (tmp_path / "src" / "poster.jpg").exists()  # untouched


def test_execute_op_cleanup_without_artifact_sweep(tmp_path: Path) -> None:
    """Settings sub-toggle off: master `cleanup_empty_source=True` but
    `cleanup_artifacts=False`. The walker still runs but doesn't sweep
    media-server artifacts — only truly-empty folders get rmdir'd.
    User keeps Plex/Jellyfin cache files in place (their choice)."""
    library = tmp_path / "media"
    show = library / "Show"
    season = show / "Season 01"
    src = _touch(season / "Show.S01E01.mkv")
    poster = _touch(show / "poster.jpg")
    dst = library / "library" / "Show (2020)" / "Season 01" / "Show - S01E01.mkv"

    cleaned = execute_op(
        FileOp.MOVE, src, dst,
        cleanup_empty_source=True,
        cleanup_artifacts=False,  # sub-toggle OFF
        cleanup_stop_at=library,
        cleanup_max_levels=2,
    )

    # Season folder was genuinely empty after the move → rmdir succeeds
    # → walk continues to show folder → poster.jpg still there → rmdir
    # fails → walk stops. No artifacts deleted because sweep is off.
    assert cleaned == 0
    assert not season.exists()       # was empty, rmdir'd
    assert show.exists()             # still has poster.jpg
    assert poster.exists()           # user-protected by the sub-toggle


def test_execute_op_copy_returns_zero(tmp_path: Path) -> None:
    """COPY doesn't trigger the source-cleanup walk (the source still
    exists), so artifact count stays 0 regardless of cleanup flag."""
    src = _touch(tmp_path / "src" / "Movie.mkv")
    _touch(tmp_path / "src" / "poster.jpg")
    dst = tmp_path / "dst" / "Movie.mkv"

    cleaned = execute_op(
        FileOp.COPY, src, dst,
        cleanup_empty_source=True,  # ignored for COPY
    )

    assert cleaned == 0
    assert src.exists()
    assert dst.exists()
    assert (tmp_path / "src" / "poster.jpg").exists()


def test_execute_op_hardlink_with_same_inode_unlinks_and_cleans(tmp_path: Path) -> None:
    """The MOVE + already-hardlinked branch: source and dest are
    different paths sharing one inode. execute_op unlinks the source
    and runs the same parent-cleanup walk."""
    # Skip on Windows/non-NTFS where hardlinks across paths can fail
    if not hasattr(os, "link"):
        return  # pragma: no cover

    library = tmp_path / "media"
    show = library / "Show"
    src = _touch(show / "Show.S01E01.mkv")
    _touch(show / "poster.jpg")
    dst_parent = library / "library" / "Show (2020)"
    dst_parent.mkdir(parents=True)
    dst = dst_parent / "Show - S01E01.mkv"
    try:
        os.link(str(src), str(dst))
    except OSError:
        return  # filesystem doesn't support hardlinks (Windows ReFS,
                # network share, etc.) — skip gracefully

    cleaned = execute_op(
        FileOp.MOVE, src, dst,
        cleanup_empty_source=True,
        cleanup_stop_at=library,
        cleanup_max_levels=2,
    )

    # The original source is gone, hardlink at dst keeps the bytes,
    # show/poster.jpg got swept during the unwind.
    assert not src.exists()
    assert dst.exists()
    assert cleaned == 1
    assert not show.exists()


# ─────────────────────────────────────────────────────────────────────
# Recycle / trash instead of hard delete — swept artifacts are MOVED to a
# managed trash folder so a mistaken sweep stays recoverable.
# ─────────────────────────────────────────────────────────────────────


def test_cleanup_artifacts_trash_moves_not_deletes(tmp_path: Path) -> None:
    """With a trash_root, swept artifacts (files + artifact dirs) leave the
    source folder but land in the trash, recoverable — not destroyed."""
    src = tmp_path / "Show" / "Season 01"
    _touch(src / "poster.jpg", "img")
    _touch(src / "tvshow.nfo", "<nfo/>")
    (src / ".actors").mkdir(parents=True, exist_ok=True)
    _touch(src / ".actors" / "a.jpg", "x")
    trash = tmp_path / ".kira-trash"

    removed = _cleanup_media_server_artifacts(src, trash_root=trash)

    assert removed == 3  # poster + nfo + .actors dir
    assert not (src / "poster.jpg").exists()
    assert not (src / "tvshow.nfo").exists()
    assert not (src / ".actors").exists()
    # Recoverable: each item is in the trash, name prefixed by its source folder.
    trashed = {p.name for p in trash.iterdir()}
    assert any("poster.jpg" in n for n in trashed)
    assert any("tvshow.nfo" in n for n in trashed)
    assert any("actors" in n for n in trashed)


def test_cleanup_trash_collision_gets_suffix(tmp_path: Path) -> None:
    """Two same-named artifacts whose source folders share a name don't clobber
    each other in the trash — the second gets a numeric suffix, both survive."""
    trash = tmp_path / ".kira-trash"
    a = tmp_path / "x" / "Show"   # parent.name == "Show"
    b = tmp_path / "y" / "Show"   # parent.name == "Show" → same trash base
    _touch(a / "poster.jpg", "one")
    _touch(b / "poster.jpg", "two")

    _cleanup_media_server_artifacts(a, trash_root=trash)
    _cleanup_media_server_artifacts(b, trash_root=trash)

    # Exclude the provenance manifest the trash now writes per item.
    from kira.renamer.operations import TRASH_MANIFEST
    files = sorted(p.name for p in trash.iterdir() if p.name != TRASH_MANIFEST)
    assert len(files) == 2  # both preserved
    contents = sorted((trash / f).read_text() for f in files)
    assert contents == ["one", "two"]
    # And the manifest recorded BOTH originals for per-item restore.
    # (parse the JSON lines — raw substring checks fail on Windows where
    # json escapes the path backslashes)
    import json
    originals = {
        json.loads(line)["original"]
        for line in (trash / TRASH_MANIFEST).read_text().splitlines()
    }
    assert originals == {str(a / "poster.jpg"), str(b / "poster.jpg")}
