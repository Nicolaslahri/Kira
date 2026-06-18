"""Trash bin — manifest provenance, list / restore / delete / empty, purge."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api.trash import (
    TrashItemBody,
    _contained_item,
    delete_item,
    empty_trash,
    list_trash,
    purge_old_trash,
    restore_item,
)
from kira.models import Setting
from kira.renamer.operations import TRASH_MANIFEST, _move_to_trash


async def _session_with_root(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'trash.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    await db.init_db()
    async with sm() as s:
        s.add(Setting(key="rename.trash_dir", value=str(tmp_path / "trash")))
        await s.commit()
    return sm


def _trash_one(tmp_path) -> tuple[Path, Path]:
    """Create a real file and move it to trash via the production helper."""
    src_dir = tmp_path / "Show" / "Season 01"
    src_dir.mkdir(parents=True, exist_ok=True)
    f = src_dir / "poster.jpg"
    f.write_bytes(b"art")
    trash = tmp_path / "trash"
    assert _move_to_trash(f, trash)
    return f, trash


def test_move_to_trash_writes_manifest(tmp_path) -> None:
    original, trash = _trash_one(tmp_path)
    assert not original.exists()
    recs = [json.loads(l) for l in (trash / TRASH_MANIFEST).read_text().splitlines()]
    assert len(recs) == 1
    assert recs[0]["original"] == str(original)
    assert (trash / recs[0]["name"]).exists()


@pytest.mark.asyncio
async def test_list_and_restore_roundtrip(tmp_path, monkeypatch) -> None:
    sm = await _session_with_root(tmp_path, monkeypatch)
    original, trash = _trash_one(tmp_path)

    async with sm() as s:
        listing = await list_trash(s)
    assert len(listing["items"]) == 1
    item = listing["items"][0]
    assert item["original"] == str(original)
    assert item["size_bytes"] == 3

    async with sm() as s:
        r = await restore_item(TrashItemBody(name=item["name"]), s)
    assert r["to"] == str(original)
    assert original.exists() and original.read_bytes() == b"art"
    # Restored item left the trash AND the manifest.
    async with sm() as s:
        assert (await list_trash(s))["items"] == []
    assert item["name"] not in (trash / TRASH_MANIFEST).read_text()


@pytest.mark.asyncio
async def test_restore_without_manifest_is_409(tmp_path, monkeypatch) -> None:
    sm = await _session_with_root(tmp_path, monkeypatch)
    trash = tmp_path / "trash"
    trash.mkdir()
    (trash / "orphan.jpg").write_bytes(b"x")  # pre-manifest item
    with pytest.raises(HTTPException) as ei:
        async with sm() as s:
            await restore_item(TrashItemBody(name="orphan.jpg"), s)
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_restore_refuses_to_clobber(tmp_path, monkeypatch) -> None:
    sm = await _session_with_root(tmp_path, monkeypatch)
    original, _ = _trash_one(tmp_path)
    original.write_bytes(b"NEW FILE AT ORIGINAL SPOT")
    async with sm() as s:
        name = (await list_trash(s))["items"][0]["name"]
    with pytest.raises(HTTPException) as ei:
        async with sm() as s:
            await restore_item(TrashItemBody(name=name), s)
    assert ei.value.status_code == 409
    assert original.read_bytes() == b"NEW FILE AT ORIGINAL SPOT"  # untouched


@pytest.mark.asyncio
async def test_delete_and_empty(tmp_path, monkeypatch) -> None:
    sm = await _session_with_root(tmp_path, monkeypatch)
    _trash_one(tmp_path)
    (tmp_path / "Show" / "Season 01" / "banner.jpg").write_bytes(b"b")
    assert _move_to_trash(tmp_path / "Show" / "Season 01" / "banner.jpg", tmp_path / "trash")

    async with sm() as s:
        items = (await list_trash(s))["items"]
    assert len(items) == 2
    async with sm() as s:
        await delete_item(TrashItemBody(name=items[0]["name"]), s)
    async with sm() as s:
        assert len((await list_trash(s))["items"]) == 1
        r = await empty_trash(s)
    assert r["deleted"] >= 1
    async with sm() as s:
        assert (await list_trash(s))["items"] == []


def test_containment_rejects_traversal(tmp_path) -> None:
    trash = tmp_path / "trash"
    trash.mkdir()
    for bad in ("..", "a/b", "a\\b", "C:evil", "", TRASH_MANIFEST):
        with pytest.raises(HTTPException) as ei:
            _contained_item(trash, bad)
        assert ei.value.status_code == 400


def test_purge_old_trash_by_age(tmp_path) -> None:
    import os, time
    _trash_one(tmp_path)
    trash = tmp_path / "trash"
    item = next(p for p in trash.iterdir() if p.name != TRASH_MANIFEST)
    old = time.time() - 40 * 86400
    os.utime(item, (old, old))
    assert purge_old_trash(trash, 30) == 1
    assert not item.exists()
    assert item.name not in (trash / TRASH_MANIFEST).read_text()
    # keep-forever (0) never deletes
    _trash_one(tmp_path)
    assert purge_old_trash(trash, 0) == 0
