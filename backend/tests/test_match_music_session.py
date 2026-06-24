"""Integration coverage for the scan→match seams that let runtime bugs slip past
~1290 unit tests this session.

`_match_music` reads the AcoustID config (providers.acoustid.*) mid-match; an
earlier revision referenced `unwrap`/`get_raw` without importing them, NameError-ing
the whole scan — and no test ever drove the function on a real session, so the suite
stayed green. These tests run it (and the scoped reparse worker) end-to-end on a real
(temp) DB with the network + file I/O stubbed.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api import scans
from kira.models import MediaFile, Match, Scan, Setting
from kira.music.matcher import MusicMatch


async def _session(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mm.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    # scans.py does `from kira.database import SessionLocal`, a module-local binding
    # the db patch above doesn't reach — the reparse worker opens its OWN session, so
    # without this it would run against the real kira.db. Patch the binding it uses.
    monkeypatch.setattr(scans, "SessionLocal", sm)
    await db.init_db()
    return sm


def _mm_result(fid: int, track_no: int, via: str = "mbid") -> MusicMatch:
    return MusicMatch(
        file_id=fid, release_id="rel-1", recording_id=f"rec-{track_no}",
        title=f"Song {track_no}", album="Album", artist="Artist", year=2020,
        track_no=track_no, disc_no=1, cover_art_url="http://x/c.jpg",
        confidence=0.9, matched_via=via,
    )


@pytest.mark.asyncio
async def test_match_music_real_session_writes_matches(tmp_path, monkeypatch):
    sm = await _session(tmp_path, monkeypatch)
    async with sm() as s:
        for i in (1, 2):
            s.add(MediaFile(
                file_path=rf"Z:\music\Artist\Album\{i:02d}.flac",
                media_type="music", status="discovered",
                parsed_data={"artist": "Artist", "album": "Album",
                             "track_title": f"Song {i}", "track": i},
            ))
        await s.commit()
        fids = list((await s.scalars(select(MediaFile.id).order_by(MediaFile.id))).all())

    # No file reads, no network: stub the tag reader + the matcher.
    monkeypatch.setattr("kira.music.tags.read_tags", lambda p: None)

    async def fake_match_album(client, inputs, acoustid_key=None):
        return [_mm_result(fids[0], 1), _mm_result(fids[1], 2)]
    monkeypatch.setattr("kira.music.matcher.match_album", fake_match_album)

    # The real call — exercises the AcoustID config read (the unwrap path) on a real
    # session. Before the fix this raised NameError and killed the scan.
    async with sm() as s:
        await scans._match_music(s, fids)

    async with sm() as s:
        matches = list((await s.scalars(select(Match))).all())
        assert len(matches) == 2
        assert {m.provider for m in matches} == {"musicbrainz"}
        assert all(m.metadata_blob and m.metadata_blob.get("music") for m in matches)
        statuses = [mf.status for mf in (await s.scalars(select(MediaFile))).all()]
        assert statuses == ["matched", "matched"]


@pytest.mark.asyncio
async def test_match_music_acoustid_config_flows_through(tmp_path, monkeypatch):
    """With auto_fingerprint ON + fpcalc resolvable, the configured AcoustID key is
    read via the REAL get_raw/unwrap (the import that was missing) and handed to the
    matcher. If that import regressed, the try/except would swallow the NameError and
    the key would arrive as None — so asserting it flowed through is the regression."""
    sm = await _session(tmp_path, monkeypatch)
    async with sm() as s:
        s.add(MediaFile(file_path=r"Z:\music\A\Al\01.flac", media_type="music",
                        status="discovered", parsed_data={"artist": "A", "album": "Al"}))
        s.add(Setting(key="providers.acoustid.auto_fingerprint", value=True))
        s.add(Setting(key="providers.acoustid.api_key", value="TESTKEY123"))
        await s.commit()
        fids = list((await s.scalars(select(MediaFile.id))).all())

    monkeypatch.setattr("kira.music.tags.read_tags", lambda p: None)
    monkeypatch.setattr("kira.fpcalc_setup.resolve_fpcalc", lambda: "/fake/fpcalc")
    captured: dict = {}

    async def fake_match_album(client, inputs, acoustid_key=None):
        captured["key"] = acoustid_key
        return []
    monkeypatch.setattr("kira.music.matcher.match_album", fake_match_album)

    async with sm() as s:
        await scans._match_music(s, fids)

    assert captured["key"] == "TESTKEY123"   # config read succeeded + reached the matcher


@pytest.mark.asyncio
async def test_reparse_worker_scopes_by_media_type(tmp_path, monkeypatch):
    """A `media_type` scope must re-match ONLY that type — the scoped-reparse feature
    added this session. Stub the match phase + mediainfo so we observe the scope."""
    sm = await _session(tmp_path, monkeypatch)
    async with sm() as s:
        s.add(MediaFile(file_path=r"Z:\music\A\Al\01.flac", media_type="music",
                        status="matched", parsed_data={"title": "m"}))
        s.add(MediaFile(file_path=r"Z:\tv\Show\S01E01.mkv", media_type="tv",
                        status="matched", parsed_data={"title": "t"}))
        scan = Scan(root_path="Z:\\", status="pending")
        s.add(scan)
        await s.commit()
        scan_id = scan.id
        music_fid = (await s.scalars(
            select(MediaFile.id).where(MediaFile.media_type == "music")
        )).one()

    seen: dict = {}

    async def fake_match_phase(session, engine, fids, scan_id):
        seen["fids"] = list(fids)
        return len(fids)

    async def fake_registry(client):
        return None

    monkeypatch.setattr(scans, "_match_phase", fake_match_phase)
    monkeypatch.setattr(scans, "registry_from_settings", fake_registry)
    monkeypatch.setattr(scans, "MatchEngine", lambda reg: object())
    monkeypatch.setattr(scans, "_spawn_mediainfo_enrich", lambda *a, **k: None)

    await scans._reparse_worker(scan_id, media_type="music")

    assert seen["fids"] == [music_fid]   # only the music file was in scope (not the tv one)
    async with sm() as s:
        scan = await s.get(Scan, scan_id)
        assert scan.estimated_total == 1
        assert scan.status == "completed"
