"""Standalone library artifact sweep — strips media-server junk (the
`<episode>-thumb.jpg` / `poster.jpg` / `.tbn` / `.actors/` Jellyfin & Plex leave
behind) out of folders that STILL contain media, leaving the videos in place.

The safety crux: allow-list only. Videos, subtitles, and unrecognized user files
are never touched; only files/dirs Kira positively classifies as server artifacts
are removed.
"""
from __future__ import annotations

from kira.renamer.operations import sweep_artifacts


def _touch(p) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def test_removes_artifacts_keeps_media_and_user_content(tmp_path) -> None:
    root = tmp_path / "anime" / "Bleach" / "Season 17"
    show = tmp_path / "anime" / "Bleach"

    # KEEP — real media + a subtitle sidecar + an unrecognized user file.
    video = root / "Bleach - S17E01 - THE BLOOD WARFARE [].mkv"
    srt = root / "Bleach - S17E01 - THE BLOOD WARFARE [].en.srt"
    userfile = root / "notes.txt"
    # REMOVE — the leftover media-server artifacts.
    thumb = root / "Bleach - S17E01 - THE BLOOD WARFARE []-thumb.jpg"  # per-episode thumb
    tbn = root / "Bleach - S17E02.tbn"                                  # Kodi binary thumb
    poster = show / "poster.jpg"                                        # generic poster
    tvshownfo = show / "tvshow.nfo"                                     # show NFO
    seasonart = show / "season17-poster.jpg"                            # season-numbered art
    actor = root / ".actors" / "Ichigo.jpg"                            # Kodi actor cache dir
    for p in (video, srt, userfile, thumb, tbn, poster, tvshownfo, seasonart, actor):
        _touch(p)

    removed, sample = sweep_artifacts([str(tmp_path)])

    # Media + user content survive.
    assert video.exists(), "video must never be touched"
    assert srt.exists(), "subtitle sidecar must never be touched"
    assert userfile.exists(), "unrecognized user file must be left alone"
    # Artifacts gone.
    assert not thumb.exists() and not tbn.exists()
    assert not poster.exists() and not tvshownfo.exists() and not seasonart.exists()
    assert not (root / ".actors").exists(), "artifact dir removed whole"
    # 6 removals: thumb, tbn, poster, tvshow.nfo, season art, .actors (dir = 1)
    assert removed == 6, f"got {removed}: {sample}"


def test_dry_run_reports_without_deleting(tmp_path) -> None:
    poster = tmp_path / "Movie (2020)" / "poster.jpg"
    video = tmp_path / "Movie (2020)" / "Movie (2020) [1080p].mkv"
    _touch(poster)
    _touch(video)

    removed, sample = sweep_artifacts([str(tmp_path)], dry_run=True)

    assert removed == 1
    assert poster.exists() and video.exists(), "dry-run must delete nothing"
    assert any("poster.jpg" in s for s in sample)


def test_trash_mode_moves_instead_of_deleting(tmp_path) -> None:
    lib = tmp_path / "lib"
    poster = lib / "Movie" / "poster.jpg"
    _touch(poster)
    trash = tmp_path / ".kira-trash"

    removed, _ = sweep_artifacts([str(lib)], trash_root=trash)

    assert removed == 1
    assert not poster.exists(), "swept from its original location"
    assert trash.is_dir() and any(trash.iterdir()), "moved into the recoverable trash"


def test_only_walks_the_given_roots(tmp_path) -> None:
    inside = tmp_path / "lib" / "poster.jpg"
    outside = tmp_path / "elsewhere" / "poster.jpg"
    _touch(inside)
    _touch(outside)

    removed, _ = sweep_artifacts([str(tmp_path / "lib")])

    assert removed == 1
    assert not inside.exists()
    assert outside.exists(), "files outside the passed roots are never touched"


# ── endpoint wiring (destructive route — verify roots + dry_run + real delete) ──

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.database import get_session
from kira.main import app
from kira.models import Setting


@pytest.mark.asyncio
async def test_artifacts_endpoint_preview_then_delete(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sweep.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    await db.init_db()

    root = tmp_path / "lib"
    poster = root / "Movie (2020)" / "poster.jpg"
    video = root / "Movie (2020)" / "Movie (2020) [1080p].mkv"
    _touch(poster)
    _touch(video)
    async with sm() as s:
        s.add(Setting(key="paths.library_root", value=str(root)))
        await s.commit()

    async def _override():
        async with sm() as s:
            yield s
    app.dependency_overrides[get_session] = _override
    try:
        client = TestClient(app)

        # Preview: reports the artifact, deletes nothing.
        r = client.post("/api/v1/cleanup/artifacts", json={"dry_run": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["removed"] == 1 and body["dry_run"] is True
        assert poster.exists() and video.exists()

        # Real: deletes the artifact, leaves the video.
        r = client.post("/api/v1/cleanup/artifacts", json={"dry_run": False})
        assert r.status_code == 200, r.text
        assert r.json()["removed"] == 1
        assert not poster.exists() and video.exists()
    finally:
        app.dependency_overrides.pop(get_session, None)
