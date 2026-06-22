"""Pack subtitles land as a guaranteed-sync SubtitleAsset via the existing
subtitle pipeline (save_sidecar + record_results)."""
from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from kira import download_guard, url_guard
from kira.models import Base, MediaFile, SubtitleAsset
from kira.packs.schema import PackSub
from kira.packs.subs import fetch_pack_subs

_SRT = b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"


async def _mem_sessionmaker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def test_pack_sub_saved_and_recorded(monkeypatch, tmp_path):
    Session = await _mem_sessionmaker()
    video = tmp_path / "[One Pace] Romance Dawn 05 [A1B2C3D4].mkv"

    async with Session() as s:
        mf = MediaFile(file_path=str(video), media_type="anime", status="matched")
        s.add(mf)
        await s.flush()
        fid = mf.id
        await s.commit()

    # Hermetic: no real DNS / network.
    monkeypatch.setattr(url_guard, "is_safe_outbound_url", lambda u: (True, ""))

    async def _fetch(client, url, **kw):
        return _SRT, "text/plain"

    monkeypatch.setattr(download_guard, "fetch_capped", _fetch)

    subs = [PackSub(lang="en", url="https://example.com/rd05.en.srt",
                    format="srt", sync="guaranteed")]
    async with Session() as s:
        n = await fetch_pack_subs(s, fid, str(video), subs, "One Pace")
    assert n == 1

    # Sidecar written beside the video.
    sidecar = tmp_path / "[One Pace] Romance Dawn 05 [A1B2C3D4].en.srt"
    assert sidecar.exists()
    assert sidecar.read_bytes() == _SRT

    async with Session() as s:
        rows = list(await s.scalars(select(SubtitleAsset).where(SubtitleAsset.media_file_id == fid)))
    assert len(rows) == 1
    r = rows[0]
    assert r.provider == "pack"
    assert r.language == "en"
    assert r.sync == "guaranteed"
    assert r.active is True
    assert r.path == str(sidecar)


async def test_error_page_body_rejected(monkeypatch, tmp_path):
    Session = await _mem_sessionmaker()
    video = tmp_path / "ep.mkv"
    async with Session() as s:
        mf = MediaFile(file_path=str(video), media_type="anime", status="matched")
        s.add(mf)
        await s.flush()
        fid = mf.id
        await s.commit()

    monkeypatch.setattr(url_guard, "is_safe_outbound_url", lambda u: (True, ""))

    async def _fetch(client, url, **kw):
        return b"<!doctype html><html>rate limited</html>", "text/html"

    monkeypatch.setattr(download_guard, "fetch_capped", _fetch)

    async with Session() as s:
        n = await fetch_pack_subs(
            s, fid, str(video),
            [PackSub(lang="en", url="https://example.com/x.srt")], "X",
        )
    assert n == 0
    assert not (tmp_path / "ep.en.srt").exists()
