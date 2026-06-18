"""Subtitle history/management store — record, blacklist, delete."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.models import MediaFile, SubtitleAsset
from kira.subtitles import store
from kira.subtitles.model import SubtitleFetchResult


async def _seed(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'subs.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    await db.init_db()                       # must create subtitle_assets
    async with sm() as s:
        mf = MediaFile(file_path=str(tmp_path / "Show.S01E01.mkv"),
                       parsed_data={"original_filename": "Show.S01E01.mkv",
                                    "mi_stamp": [1, 2], "sub_sidecars": ["en"]},
                       media_type="anime", status="renamed")
        s.add(mf); await s.flush(); fid = mf.id
        await s.commit()
    return sm, fid


def _result(lang="en", provider="subsource", ref="123", score=80):
    return SubtitleFetchResult(language=lang, path="/x/Show.S01E01.en.srt",
                               provider=provider, release_name="[X] BluRay 1080p",
                               ref=ref, score=score, sync="likely", reasons=["1080p"])


@pytest.mark.asyncio
async def test_record_supersedes_prior_active(tmp_path, monkeypatch):
    sm, fid = await _seed(tmp_path, monkeypatch)
    async with sm() as s:
        await store.record_results(s, fid, "Show S01E01", [_result(ref="a", score=50)])
        await store.record_results(s, fid, "Show S01E01", [_result(ref="b", score=90)])
    async with sm() as s:
        rows = list(await s.scalars(select(SubtitleAsset).order_by(SubtitleAsset.id)))
        assert len(rows) == 2
        active = [r for r in rows if r.active]
        assert len(active) == 1 and active[0].ref == "b" and active[0].score == 90


@pytest.mark.asyncio
async def test_blacklist_loads_for_aggregator(tmp_path, monkeypatch):
    sm, fid = await _seed(tmp_path, monkeypatch)
    async with sm() as s:
        await store.record_results(s, fid, "t", [_result(provider="subsource", ref="bad")])
        rows = list(await s.scalars(select(SubtitleAsset)))
        await store.remove_asset(s, rows[0].id, blacklist=True)
    async with sm() as s:
        bl = await store.load_blacklist(s, fid)
        assert ("subsource", "bad") in bl


@pytest.mark.asyncio
async def test_remove_asset_deletes_file_and_clears_coverage(tmp_path, monkeypatch):
    sm, fid = await _seed(tmp_path, monkeypatch)
    srt = tmp_path / "Show.S01E01.en.srt"
    srt.write_text("subs")
    async with sm() as s:
        await store.record_results(s, fid, "t", [
            SubtitleFetchResult(language="en", path=str(srt), provider="subsource", ref="1", score=80)])
        rows = list(await s.scalars(select(SubtitleAsset)))
        res = await store.remove_asset(s, rows[0].id, blacklist=False)
    assert res["ok"] and res["deleted_file"] is True
    assert not srt.exists()
    async with sm() as s:
        mf = await s.get(MediaFile, fid)
        assert "en" not in (mf.parsed_data.get("sub_sidecars") or [])   # coverage reopened
        a = (await s.scalars(select(SubtitleAsset))).first()
        assert a.active is False and a.path is None and a.blacklisted is False


@pytest.mark.asyncio
async def test_remove_missing_asset_is_graceful(tmp_path, monkeypatch):
    sm, fid = await _seed(tmp_path, monkeypatch)
    async with sm() as s:
        res = await store.remove_asset(s, 9999, blacklist=False)
    assert res["ok"] is False


@pytest.mark.asyncio
async def test_history_endpoint_serializes(tmp_path, monkeypatch):
    from kira.api.subtitles import subtitle_history, delete_subtitle_asset
    sm, fid = await _seed(tmp_path, monkeypatch)
    async with sm() as s:
        await store.record_results(s, fid, "Show S01E01", [_result(provider="opensubtitles", score=88)])
    async with sm() as s:
        rows = await subtitle_history(session=s)
    assert len(rows) == 1
    assert rows[0].provider == "opensubtitles" and rows[0].score == 88
    assert rows[0].title == "Show S01E01" and rows[0].active is True
    # delete via the endpoint
    async with sm() as s:
        out = await delete_subtitle_asset(rows[0].id, blacklist=False, session=s)
    assert out["ok"] is True
