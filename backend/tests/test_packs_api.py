"""Packs API: validate dry-run, install/list/update/delete round-trip, and the
override-without-scope rejection. Endpoints are called directly against an
in-memory session; the network fetch is monkeypatched."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from kira.api import packs as api
from kira.api.packs import (
    PackAddBody,
    PackUpdateBody,
    PackValidateBody,
    add_pack,
    delete_pack,
    list_packs,
    update_pack,
    validate_pack,
)
from kira.models import Base, MediaFile
from kira.packs import loader as _loader
from kira.packs.schema import parse_pack

PACK_DICT = {
    "kira_pack": 1, "id": "one-pace", "name": "One Pace", "media_type": "anime",
    "show": {"title": "One Pace", "year": 1999, "poster_url": "https://x/p.jpg"},
    "match": {"titles": ["One Pace"], "release_groups": ["One Pace"]},
    "episodes": [{"season": 1, "episode": 5, "match": {"crc32": "a1b2c3d4"}}],
}
PACK = parse_pack(PACK_DICT)
URL = "https://example.com/one-pace.json"


async def _mem_sessionmaker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _patch_network(monkeypatch):
    async def _fetch(url, **kw):
        return (PACK, None) if url == URL else (None, "not found")

    async def _get(binding, **kw):
        return PACK

    monkeypatch.setattr(_loader, "fetch_pack", _fetch)
    monkeypatch.setattr(_loader, "get_pack", _get)


async def test_validate_dry_run(monkeypatch):
    Session = await _mem_sessionmaker()
    _patch_network(monkeypatch)
    async with Session() as s:
        # Seed a no_match One Pace file so the dry-run reports a rescue.
        s.add(MediaFile(
            file_path="/anime/One Pace/rd05.mkv", media_type="anime", status="no_match",
            parsed_data={"original_filename": "[One Pace] Romance Dawn 05 [A1B2C3D4].mkv",
                         "media_type": "anime", "title": "One Pace",
                         "release_group": "One Pace", "episode": 5, "season": 1},
        ))
        await s.commit()
        out = await validate_pack(PackValidateBody(url=URL), s)
    assert out["ok"] is True
    assert out["episode_count"] == 1
    assert out["would_rescue"] == 1
    assert out["sample_files"]


async def test_install_list_update_delete(monkeypatch):
    Session = await _mem_sessionmaker()
    _patch_network(monkeypatch)
    async with Session() as s:
        added = await add_pack(PackAddBody(url=URL), s)
    assert added["id"] == "one-pace"
    key = added["key"]

    async with Session() as s:
        listed = await list_packs(s)
    assert len(listed["packs"]) == 1
    assert listed["packs"][0]["enabled"] is True

    async with Session() as s:
        upd = await update_pack(key, PackUpdateBody(enabled=False), s)
    assert upd["enabled"] is False

    async with Session() as s:
        gone = await delete_pack(key, s)
    assert gone["ok"] is True
    async with Session() as s:
        listed = await list_packs(s)
    assert listed["packs"] == []


async def test_install_override_without_scope_is_422(monkeypatch):
    Session = await _mem_sessionmaker()
    _patch_network(monkeypatch)
    async with Session() as s:
        with pytest.raises(HTTPException) as ei:
            await add_pack(PackAddBody(url=URL, authority="override", scope_paths=[]), s)
    assert ei.value.status_code == 422


async def test_install_bad_url_is_422(monkeypatch):
    Session = await _mem_sessionmaker()
    _patch_network(monkeypatch)
    async with Session() as s:
        with pytest.raises(HTTPException) as ei:
            await add_pack(PackAddBody(url="https://nope.example/x.json"), s)
    assert ei.value.status_code == 422


async def test_update_to_override_without_scope_is_422(monkeypatch):
    Session = await _mem_sessionmaker()
    _patch_network(monkeypatch)
    async with Session() as s:
        added = await add_pack(PackAddBody(url=URL), s)
    key = added["key"]
    async with Session() as s:
        with pytest.raises(HTTPException) as ei:
            await update_pack(key, PackUpdateBody(authority="override"), s)
    assert ei.value.status_code == 422
