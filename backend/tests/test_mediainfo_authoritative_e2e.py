"""End-to-end proof that "Authoritative tech tags" actually overrides — through
the REAL background pass and a REAL SQLite round-trip (not mocked sessions).

This guards the exact wiring that was doubted: the settings key
(`parsing.mediainfo_authoritative`) → `enrich_mediainfo_background` →
`enrich_parsed(authoritative=True)` → the container's values persisted into the
row's `parsed_data`, winning over what the filename claimed. Only the native
MediaInfo library is faked (to a known reading); everything else is real.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api import scans
from kira.models import MediaFile, Setting
from kira.parser import parse_filename


async def _setup(tmp_path, monkeypatch, *, read=True, authoritative=False):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'kira_e2e.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    # The background pass opens `SessionLocal()` from the scans namespace.
    monkeypatch.setattr(scans, "SessionLocal", sm)
    await db.init_db()

    # Fake the native lib to a KNOWN container reading that DISAGREES with the
    # filename (file is really 1080p/x265, dual-audio JPN+ENG, ENG subs).
    monkeypatch.setattr(scans._mediainfo, "available", lambda: True)
    monkeypatch.setattr(scans._mediainfo, "read_media_info", lambda _p: {
        "quality": "1080p", "codec": "x265", "channels": "5.1",
        "audio_langs": ["jpn", "eng"], "sub_langs": ["eng"],
    })

    async with sm() as s:
        s.add(Setting(key="parsing.read_mediainfo", value=read))
        s.add(Setting(key="parsing.mediainfo_authoritative", value=authoritative))
        pd = parse_filename("Show.S01E01.720p.x264.mkv").to_dict()  # filename: 720p/x264
        assert pd["quality"] == "720p" and pd["codec"] == "x264"
        mf = MediaFile(file_path="/m/Show.S01E01.720p.x264.mkv", parsed_data=pd,
                       media_type="tv", status="matched")
        s.add(mf)
        await s.commit()
        fid = mf.id
    return sm, fid


@pytest.mark.asyncio
async def test_authoritative_override_persists_through_real_db(tmp_path, monkeypatch):
    sm, fid = await _setup(tmp_path, monkeypatch, read=True, authoritative=True)
    n = await scans.enrich_mediainfo_background([fid])
    assert n == 1
    async with sm() as s:
        mf = await s.get(MediaFile, fid)
        # The container WON over the filename — this is the whole point.
        assert mf.parsed_data["quality"] == "1080p"
        assert mf.parsed_data["codec"] == "x265"
        # And per-track languages populated (no filename source for these).
        assert mf.parsed_data["audio_langs"] == ["jpn", "eng"]
        assert mf.parsed_data["sub_langs"] == ["eng"]


@pytest.mark.asyncio
async def test_fallback_keeps_filename_quality_but_still_reads_langs(tmp_path, monkeypatch):
    # Same file, authoritative OFF: the filename's quality is KEPT (not
    # overridden), but languages are still read (they have no filename source).
    sm, fid = await _setup(tmp_path, monkeypatch, read=True, authoritative=False)
    n = await scans.enrich_mediainfo_background([fid])
    assert n == 1
    async with sm() as s:
        mf = await s.get(MediaFile, fid)
        assert mf.parsed_data["quality"] == "720p"          # filename kept
        assert mf.parsed_data["codec"] == "x264"            # filename kept
        assert mf.parsed_data["audio_langs"] == ["jpn", "eng"]


@pytest.mark.asyncio
async def test_read_disabled_changes_nothing(tmp_path, monkeypatch):
    # read_mediainfo OFF → the pass is a no-op even with authoritative ON.
    sm, fid = await _setup(tmp_path, monkeypatch, read=False, authoritative=True)
    n = await scans.enrich_mediainfo_background([fid])
    assert n == 0
    async with sm() as s:
        mf = await s.get(MediaFile, fid)
        assert mf.parsed_data["quality"] == "720p"          # untouched
        assert "audio_langs" not in mf.parsed_data or not mf.parsed_data["audio_langs"]
