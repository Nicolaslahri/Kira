"""Undo removes the artwork + NFO it wrote, so rename→undo→rename stops piling up.

Each rename writes `<stem>-<kind>.<ext>` artwork + `<stem>.nfo` named after the
TARGET. Undo reverted the video but used to leave those behind, so doing it a few
times left a folder full of orphaned poster/fanart/logo/nfo sets. `_remove_orphaned_assets`
deletes exactly what Kira wrote — matched on the video's own stem — and nothing else.

Real temp files. Proves:
  • every `<stem>-*` image + `<stem>.nfo` is removed,
  • the video, the generic Kodi assets (folder.jpg/backdrop.jpg — no stem prefix),
    a subtitle sidecar (not an image), and a DIFFERENT title's artwork all survive,
  • bracket-laden stems (`… [1080p WEBRip]`) are matched literally, NOT as glob
    character classes (the pitfall that made me avoid Path.glob),
  • the managed-roots guard refuses to delete anything outside the library.
"""
from __future__ import annotations

import pytest

from kira.api.history import _cleanup_entry_assets, _remove_orphaned_assets
from kira.models import RenameHistory


def _touch(p) -> None:
    p.write_bytes(b"x")


@pytest.mark.asyncio
async def test_dispatcher_uses_recorded_paths_authoritatively(tmp_path) -> None:
    # #1: with created_assets recorded, undo deletes EXACTLY those paths — and a
    # stem-prefixed decoy that was NOT recorded survives (proves it's not deriving).
    rec_art = tmp_path / "X (2020)-poster.jpg"
    rec_nfo = tmp_path / "X (2020).nfo"
    decoy = tmp_path / "X (2020)-fanart.jpg"  # matches stem, NOT recorded → kept
    for f in (rec_art, rec_nfo, decoy):
        _touch(f)
    entry = RenameHistory(
        old_path="old", new_path=str(tmp_path / "X (2020).mkv"), operation="move",
        created_assets=[str(rec_art), str(rec_nfo)],
    )
    removed = await _cleanup_entry_assets(entry, [str(tmp_path)])
    assert removed == 2
    assert not rec_art.exists() and not rec_nfo.exists()
    assert decoy.exists(), "only RECORDED paths are deleted in authoritative mode"


@pytest.mark.asyncio
async def test_dispatcher_falls_back_to_derivation_for_legacy_rows(tmp_path) -> None:
    # A legacy row (created_assets=None) → undo derives from the stem (the band-aid).
    stem = "Legacy Movie (2001)"
    nfo = tmp_path / f"{stem}.nfo"
    art = tmp_path / f"{stem}-poster.jpg"
    for f in (nfo, art):
        _touch(f)
    entry = RenameHistory(
        old_path="old", new_path=str(tmp_path / f"{stem}.mkv"), operation="move",
        created_assets=None,
    )
    removed = await _cleanup_entry_assets(entry, [str(tmp_path)])
    assert removed == 2 and not nfo.exists() and not art.exists()


@pytest.mark.asyncio
async def test_removes_kira_assets_keeps_everything_else(tmp_path) -> None:
    folder = tmp_path / "Evil Dead Rise (2023)"
    folder.mkdir()
    stem = "Evil Dead Rise (2023)"
    video = folder / f"{stem}.mkv"
    _touch(video)

    # Kira's artwork (note -logo.png / -landscape.webp: kinds that are NOT in the
    # current ALL_KINDS list — stem-prefix matching catches them regardless) + NFO.
    kira_assets = [
        folder / f"{stem}-poster.jpg",
        folder / f"{stem}-fanart.jpg",
        folder / f"{stem}-logo.png",
        folder / f"{stem}-landscape.webp",
        folder / f"{stem}.nfo",
    ]
    for a in kira_assets:
        _touch(a)

    # Must all survive: the video, generic Kodi assets (no stem prefix), a subtitle
    # sidecar (matches stem prefix but isn't an image), a different title's artwork.
    survivors = [
        video,
        folder / "folder.jpg",
        folder / "backdrop.jpg",
        folder / f"{stem}.en.srt",
        folder / "Some Other Movie (2020)-poster.jpg",
    ]
    for s in survivors:
        _touch(s)

    removed = await _remove_orphaned_assets(str(video), [str(tmp_path)])

    assert removed == len(kira_assets)
    for a in kira_assets:
        assert not a.exists(), f"should have removed {a.name}"
    for s in survivors:
        assert s.exists(), f"should have kept {s.name}"


@pytest.mark.asyncio
async def test_bracket_stem_matched_literally_not_as_glob(tmp_path) -> None:
    # `[1080p WEBRip]` would be a glob character class under Path.glob — this
    # locks in the iterdir/startswith approach that treats it as a literal.
    folder = tmp_path / "show"
    folder.mkdir()
    stem = "The Show (2021).10bit [1080p WEBRip]"
    video = folder / f"{stem}.mkv"
    _touch(video)
    art = folder / f"{stem}-poster.jpg"
    nfo = folder / f"{stem}.nfo"
    _touch(art)
    _touch(nfo)

    removed = await _remove_orphaned_assets(str(video), [str(tmp_path)])

    assert removed == 2
    assert not art.exists()
    assert not nfo.exists()
    assert video.exists()


@pytest.mark.asyncio
async def test_roots_guard_refuses_outside_library(tmp_path) -> None:
    # The video sits OUTSIDE the configured roots → nothing is touched, even
    # though the artwork is right there next to it.
    library = tmp_path / "library"
    outside = tmp_path / "elsewhere"
    library.mkdir()
    outside.mkdir()
    stem = "Stray (2019)"
    video = outside / f"{stem}.mkv"
    art = outside / f"{stem}-poster.jpg"
    nfo = outside / f"{stem}.nfo"
    for f in (video, art, nfo):
        _touch(f)

    removed = await _remove_orphaned_assets(str(video), [str(library)])

    assert removed == 0
    assert art.exists() and nfo.exists()
