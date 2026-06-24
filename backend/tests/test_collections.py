"""Collection-completion endpoint — for each TMDB collection you partially own,
return the parts you're MISSING (released vs upcoming flagged). TMDB is stubbed
via a fake provider registry so the diff logic is tested offline."""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import kira.api.collections as collections_mod
from kira.models import Base, Match, MediaFile


async def _mem_sessionmaker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _add_movie(Session, *, path, tmdb_id, coll_id, coll_name, selected=True):
    async with Session() as s:
        mf = MediaFile(file_path=path, parsed_data={}, media_type="movie", status="matched")
        s.add(mf)
        await s.flush()
        s.add(Match(
            media_file_id=mf.id, provider="tmdb", provider_id=str(tmdb_id),
            match_type="movie", confidence=0.95, is_selected=selected,
            collection_id=coll_id, collection_name=coll_name,
        ))
        await s.commit()


# A fake TMDB provider whose get_collection returns the Matrix trilogy + an
# unreleased 4th film, regardless of which collection id it's asked for.
class _FakeTmdb:
    async def get_collection(self, cid):
        return {
            "id": cid, "name": "The Matrix Collection", "poster_url": "cp",
            "parts": [
                {"tmdb_id": "603", "title": "The Matrix", "year": 1999, "poster_url": "p1", "release_date": "1999-03-31"},
                {"tmdb_id": "604", "title": "The Matrix Reloaded", "year": 2003, "poster_url": "p2", "release_date": "2003-05-15"},
                {"tmdb_id": "605", "title": "The Matrix Revolutions", "year": 2003, "poster_url": "p3", "release_date": "2003-11-05"},
                {"tmdb_id": "9999", "title": "The Matrix 5", "year": None, "poster_url": "p4", "release_date": "2099-01-01"},
            ],
        }


class _FakeRegistry:
    def has(self, k): return k == "tmdb"
    def build(self, k): return _FakeTmdb()


@pytest.fixture
def _stub_registry(monkeypatch):
    async def _fake(client):
        return _FakeRegistry()
    monkeypatch.setattr(collections_mod, "registry_from_settings", _fake)


@pytest.mark.asyncio
async def test_missing_parts_with_released_flag(monkeypatch, _stub_registry):
    Session = await _mem_sessionmaker()
    # Own 2 of the 4 Matrix parts.
    await _add_movie(Session, path="/m/Matrix (1999)/x.mkv", tmdb_id=603, coll_id="100", coll_name="The Matrix Collection")
    await _add_movie(Session, path="/m/Matrix Reloaded (2003)/x.mkv", tmdb_id=604, coll_id="100", coll_name="The Matrix Collection")

    async with Session() as s:
        out = await collections_mod.list_collections(s)

    colls = out["collections"]
    assert len(colls) == 1
    c = colls[0]
    assert c["collection_id"] == "100"
    assert c["name"] == "The Matrix Collection"
    assert c["owned"] == 2 and c["total"] == 4
    missing = {m["tmdb_id"]: m for m in c["missing"]}
    assert set(missing) == {"605", "9999"}
    assert missing["605"]["released"] is True     # 2003 ≤ today
    assert missing["9999"]["released"] is False    # 2099 → upcoming


@pytest.mark.asyncio
async def test_full_collection_omitted(monkeypatch, _stub_registry):
    # Owning every part Kira knows about → nothing to surface for that collection.
    Session = await _mem_sessionmaker()
    for tid in (603, 604, 605, 9999):
        await _add_movie(Session, path=f"/m/{tid}/x.mkv", tmdb_id=tid, coll_id="100", coll_name="The Matrix Collection")
    async with Session() as s:
        out = await collections_mod.list_collections(s)
    assert out["collections"] == []


@pytest.mark.asyncio
async def test_no_collection_movies_returns_empty(monkeypatch, _stub_registry):
    Session = await _mem_sessionmaker()
    # A standalone movie with no collection_id is never queried.
    await _add_movie(Session, path="/m/Solo (2018)/x.mkv", tmdb_id=348350, coll_id=None, coll_name=None)
    async with Session() as s:
        out = await collections_mod.list_collections(s)
    assert out["collections"] == []
