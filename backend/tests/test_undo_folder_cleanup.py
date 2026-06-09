"""Undo cleans up the destination Show/Season folders Kira created.

Reported: undoing renames returned the videos to their originals but left behind
the `Bleach - Thousand-Year Blood War/…` show folders (with `tvshow.nfo`/`poster.jpg`
inside) — just without the media. `_cleanup_undo_vacated_folders` walks up from the
vacated destination and removes folders that are empty or entirely media-server
artifacts (allow-list only), bounded by the managed library root.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api.history import _cleanup_undo_vacated_folders
from kira.models import RenameHistory, Setting


def _touch(p) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


async def _fresh_db(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'undo_folders.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    await db.init_db()
    return sm


@pytest.mark.asyncio
async def test_removes_empty_show_and_season_folders(tmp_path, monkeypatch):
    sm = await _fresh_db(tmp_path, monkeypatch)
    root = tmp_path / "anime"
    show = root / "Bleach - Thousand-Year Blood War"
    season = show / "Season 1"
    season.mkdir(parents=True)            # season is empty — the video already went back
    _touch(show / "tvshow.nfo")            # show-level artifacts left behind
    _touch(show / "poster.jpg")
    async with sm() as s:
        s.add(Setting(key="paths.library_root", value=str(root)))
        await s.commit()

    entry = RenameHistory(
        old_path=str(tmp_path / "orig" / "bleach ep1.mkv"),
        new_path=str(season / "Bleach - S01E01 [].mkv"),  # the vacated destination
        operation="move",
    )
    async with sm() as s:
        await _cleanup_undo_vacated_folders(s, entry)

    assert not season.exists(), "emptied season folder should be removed"
    assert not show.exists(), "show folder (only artifacts left) should be removed"
    assert root.exists(), "the library root itself is never removed"


@pytest.mark.asyncio
async def test_keeps_show_folder_that_still_has_other_media(tmp_path, monkeypatch):
    # If a sibling season still holds episodes, the show folder must survive.
    sm = await _fresh_db(tmp_path, monkeypatch)
    root = tmp_path / "anime"
    show = root / "Bleach - Thousand-Year Blood War"
    s1 = show / "Season 1"
    s2 = show / "Season 2"
    s1.mkdir(parents=True)                         # vacated season (empty)
    _touch(s2 / "Bleach - S02E01 [].mkv")          # sibling season still has media
    _touch(show / "tvshow.nfo")
    async with sm() as s:
        s.add(Setting(key="paths.library_root", value=str(root)))
        await s.commit()

    entry = RenameHistory(
        old_path=str(tmp_path / "orig" / "ep.mkv"),
        new_path=str(s1 / "Bleach - S01E01 [].mkv"),
        operation="move",
    )
    async with sm() as s:
        await _cleanup_undo_vacated_folders(s, entry)

    assert not s1.exists(), "the emptied season folder is removed"
    assert show.exists(), "show folder kept — Season 2 still holds media"
    assert (show / "tvshow.nfo").exists(), "show-level artifact kept while media remains"
    assert (s2 / "Bleach - S02E01 [].mkv").exists()


@pytest.mark.asyncio
async def test_outside_managed_roots_is_untouched(tmp_path, monkeypatch):
    sm = await _fresh_db(tmp_path, monkeypatch)
    async with sm() as s:
        s.add(Setting(key="paths.library_root", value=str(tmp_path / "library")))
        await s.commit()
    elsewhere = tmp_path / "elsewhere" / "Show" / "Season 1"
    elsewhere.mkdir(parents=True)
    entry = RenameHistory(old_path="o", new_path=str(elsewhere / "x.mkv"), operation="move")
    async with sm() as s:
        n = await _cleanup_undo_vacated_folders(s, entry)
    assert n == 0 and elsewhere.exists(), "never touch folders outside a managed root"
