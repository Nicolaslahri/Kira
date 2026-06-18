"""Undo-path hardening: orphan cleanup, viability probe, identity gate, and
recoverable (trash + subtitle-cache) deletes.

These cover the data-loss-sensitive additions to `kira/api/history.py`:

  • POST /history/cleanup-orphans  — authoritatively sweep recorded assets an
    OLD undo left behind on already-undone rows; returns {"removed": N}.
  • POST /history/verify-undoable  — READ-ONLY per-row probe reporting the exact
    reason a row can't be undone (Target missing / File changed on disk /
    Original location occupied / Already undone).
  • undo_entry identity gate       — a renamed file edited/replaced/relocated on
    disk must 409 (single) / count-failed (bulk) BEFORE any move happens.
  • trash-on-undo                  — with `rename.cleanup_trash` on, the NFO/art
    a rename wrote is MOVED to Kira's trash dir, not hard-deleted.
  • subtitle → reuse-cache         — a removed `.srt` is routed to
    subcache.cache_subtitle (and its subtitle_assets ledger row retired).

Session-based tests mirror the `_fresh_db` fixture in test_undo_folder_cleanup.py
so the route functions can be driven directly off a throwaway SQLite file.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api.history import (
    VerifyUndoableIn,
    _remove_recorded_assets,
    _verify_row_undoable,
    cleanup_orphans,
    undo_entry,
    undo_bulk,
    verify_undoable,
)
from kira.models import Match, MediaFile, RenameHistory, Setting, SubtitleAsset


def _touch(p, data: bytes = b"x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


async def _fresh_db(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'undo_hardening.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    await db.init_db()
    return sm


# ── 1. cleanup-orphans ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cleanup_orphans_removes_recorded_asset_of_undone_row(tmp_path, monkeypatch):
    sm = await _fresh_db(tmp_path, monkeypatch)
    root = tmp_path / "lib"
    nfo = root / "Movie (2020).nfo"
    poster = root / "Movie (2020)-poster.jpg"
    _touch(nfo)
    _touch(poster)
    async with sm() as s:
        s.add(Setting(key="paths.library_root", value=str(root)))
        # An ALREADY-UNDONE primary row whose recorded assets were never cleaned
        # (the old-undo orphan case this endpoint exists to mop up).
        s.add(RenameHistory(
            old_path=str(tmp_path / "orig" / "movie.mkv"),
            new_path=str(root / "Movie (2020).mkv"),
            operation="move",
            undone_at=db_now(),
            created_assets=[str(nfo), str(poster)],
        ))
        await s.commit()

    async with sm() as s:
        out = await cleanup_orphans(session=s)

    assert out == {"removed": 2}
    assert not nfo.exists() and not poster.exists()


@pytest.mark.asyncio
async def test_cleanup_orphans_skips_not_undone_and_childless(tmp_path, monkeypatch):
    # A NOT-yet-undone row's assets must survive (its file is still renamed); a
    # sidecar child row (parent_id set) is skipped too.
    sm = await _fresh_db(tmp_path, monkeypatch)
    root = tmp_path / "lib"
    live_nfo = root / "Live (2021).nfo"
    child_nfo = root / "Child (2021).nfo"
    _touch(live_nfo)
    _touch(child_nfo)
    async with sm() as s:
        s.add(Setting(key="paths.library_root", value=str(root)))
        s.add(RenameHistory(  # not undone → keep
            old_path="o1", new_path=str(root / "Live (2021).mkv"), operation="move",
            undone_at=None, created_assets=[str(live_nfo)],
        ))
        s.add(RenameHistory(  # undone but a sidecar child (parent_id set) → skip
            old_path="o2", new_path=str(root / "Child (2021).mkv"), operation="move",
            undone_at=db_now(), parent_id=999, created_assets=[str(child_nfo)],
        ))
        await s.commit()

    async with sm() as s:
        out = await cleanup_orphans(session=s)

    assert out == {"removed": 0}
    assert live_nfo.exists() and child_nfo.exists()


# ── 2. verify-undoable ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_verify_undoable_reasons(tmp_path, monkeypatch):
    sm = await _fresh_db(tmp_path, monkeypatch)
    # Stamping has no FS support in the test → the absent-stamp branch is skipped
    # so a plain unstamped file is "undoable" (we test mismatch separately).
    import kira.xattr_store as xs
    monkeypatch.setattr(xs, "supported", lambda p: False)

    # (a) target present, original free → undoable
    ok_new = tmp_path / "ok" / "new.mkv"
    _touch(ok_new)
    # (b) target missing → "Target missing"
    gone_new = tmp_path / "gone" / "new.mkv"   # never created
    # (c) original occupied by a DIFFERENT file → "Original location occupied"
    occ_old = tmp_path / "occ" / "old.mkv"
    occ_new = tmp_path / "occ" / "new.mkv"
    _touch(occ_new, b"renamed")
    _touch(occ_old, b"a DIFFERENT precious file")

    async with sm() as s:
        s.add(RenameHistory(id=1, old_path=str(tmp_path / "ok" / "old.mkv"),
                            new_path=str(ok_new), operation="move"))
        s.add(RenameHistory(id=2, old_path=str(tmp_path / "gone" / "old.mkv"),
                            new_path=str(gone_new), operation="move"))
        s.add(RenameHistory(id=3, old_path=str(occ_old),
                            new_path=str(occ_new), operation="move"))
        await s.commit()

    async with sm() as s:
        res = await verify_undoable(VerifyUndoableIn(ids=[1, 2, 3, 404]), session=s)

    assert res["1"] == {"undoable": True, "reason": ""}
    assert res["2"] == {"undoable": False, "reason": "Target missing"}
    assert res["3"] == {"undoable": False, "reason": "Original location occupied"}
    # A missing row reports "Already undone" (mirrors the contract).
    assert res["404"] == {"undoable": False, "reason": "Already undone"}


@pytest.mark.asyncio
async def test_verify_undoable_flags_changed_when_stamp_mismatches(tmp_path, monkeypatch):
    sm = await _fresh_db(tmp_path, monkeypatch)
    import kira.xattr_store as xs
    # A stamp is PRESENT on disk but carries a DIFFERENT id than the row's match
    # → "File changed on disk" (the file was replaced by something Kira stamped
    # for another title, or the stamp drifted).
    monkeypatch.setattr(xs, "read_ids", lambda p: {"tmdb": "999"})

    new = tmp_path / "show" / "new.mkv"
    _touch(new)
    async with sm() as s:
        mf = MediaFile(id=1, file_path=str(new), status="renamed")
        s.add(mf)
        m = Match(id=1, media_file_id=1, provider="tmdb", provider_id="27205",
                  match_type="movie", confidence=1.0)
        s.add(m)
        s.add(RenameHistory(id=1, old_path=str(tmp_path / "show" / "old.mkv"),
                            new_path=str(new), operation="move", match_id=1))
        await s.commit()

    async with sm() as s:
        res = await verify_undoable(VerifyUndoableIn(ids=[1]), session=s)

    assert res["1"] == {"undoable": False, "reason": "File changed on disk"}


@pytest.mark.asyncio
async def test_verify_undoable_matching_stamp_is_undoable(tmp_path, monkeypatch):
    sm = await _fresh_db(tmp_path, monkeypatch)
    import kira.xattr_store as xs
    # Stamp present AND matches the row's provider id → genuine → undoable.
    monkeypatch.setattr(xs, "read_ids", lambda p: {"tmdb": "27205"})

    new = tmp_path / "show" / "new.mkv"
    _touch(new)
    async with sm() as s:
        s.add(MediaFile(id=1, file_path=str(new), status="renamed"))
        s.add(Match(id=1, media_file_id=1, provider="tmdb", provider_id="27205",
                    match_type="movie", confidence=1.0))
        s.add(RenameHistory(id=1, old_path=str(tmp_path / "show" / "old.mkv"),
                            new_path=str(new), operation="move", match_id=1))
        await s.commit()

    async with sm() as s:
        res = await verify_undoable(VerifyUndoableIn(ids=[1]), session=s)
    assert res["1"] == {"undoable": True, "reason": ""}


# ── 3. undo identity gate ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_undo_entry_refuses_when_file_replaced(tmp_path, monkeypatch):
    sm = await _fresh_db(tmp_path, monkeypatch)
    import kira.xattr_store as xs
    from fastapi import HTTPException
    # The on-disk file carries a stamp for a DIFFERENT id than the row → replaced.
    monkeypatch.setattr(xs, "read_ids", lambda p: {"tvdb": "0000"})

    new = tmp_path / "show" / "new.mkv"
    _touch(new, b"a file the user swapped in")
    async with sm() as s:
        s.add(Setting(key="paths.library_root", value=str(tmp_path)))
        s.add(MediaFile(id=1, file_path=str(new), status="renamed"))
        s.add(Match(id=1, media_file_id=1, provider="tvdb", provider_id="81797",
                    match_type="tv_episode", confidence=1.0))
        s.add(RenameHistory(id=1, old_path=str(tmp_path / "show" / "old.mkv"),
                            new_path=str(new), operation="move", match_id=1))
        await s.commit()

    async with sm() as s:
        with pytest.raises(HTTPException) as ei:
            await undo_entry(1, session=s)
    assert ei.value.status_code == 409
    assert ei.value.detail == "File changed on disk"
    # The replaced file was NOT moved — still sitting at new_path untouched.
    assert new.read_bytes() == b"a file the user swapped in"


@pytest.mark.asyncio
async def test_undo_bulk_counts_replaced_as_failed_and_skips(tmp_path, monkeypatch):
    sm = await _fresh_db(tmp_path, monkeypatch)
    import kira.xattr_store as xs
    monkeypatch.setattr(xs, "supported", lambda p: False)

    # Row 1: clean undo. Row 2: original occupied → must be counted failed + skipped.
    good_new = tmp_path / "g" / "new.mkv"
    _touch(good_new, b"good")
    occ_old = tmp_path / "b" / "old.mkv"
    occ_new = tmp_path / "b" / "new.mkv"
    _touch(occ_new, b"renamed")
    _touch(occ_old, b"DIFFERENT file at the original spot")
    async with sm() as s:
        s.add(Setting(key="paths.library_root", value=str(tmp_path)))
        s.add(RenameHistory(id=1, old_path=str(tmp_path / "g" / "old.mkv"),
                            new_path=str(good_new), operation="move"))
        s.add(RenameHistory(id=2, old_path=str(occ_old),
                            new_path=str(occ_new), operation="move"))
        await s.commit()

    async with sm() as s:
        out = await undo_bulk({"ids": [1, 2]}, session=s)

    assert out["succeeded"] == 1
    assert out["failed"] == 1
    # Good row moved back; occupied row left entirely alone (both files intact).
    assert good_new.exists() is False and (tmp_path / "g" / "old.mkv").exists()
    assert occ_new.read_bytes() == b"renamed"
    assert occ_old.read_bytes() == b"DIFFERENT file at the original spot"


# ── 4. trash-on-undo ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_trash_on_undo_moves_nfo_to_trash_dir(tmp_path, monkeypatch):
    sm = await _fresh_db(tmp_path, monkeypatch)
    import kira.xattr_store as xs
    monkeypatch.setattr(xs, "supported", lambda p: False)

    root = tmp_path / "lib"
    old = tmp_path / "orig" / "movie.mkv"   # original location (free → undo OK)
    new = root / "Movie (2020).mkv"
    nfo = root / "Movie (2020).nfo"
    _touch(new, b"video")
    _touch(nfo, b"<nfo/>")
    async with sm() as s:
        s.add(Setting(key="paths.library_root", value=str(root)))
        s.add(Setting(key="rename.cleanup_trash", value=True))  # ← recoverable deletes
        s.add(RenameHistory(id=1, old_path=str(old), new_path=str(new),
                            operation="move", created_assets=[str(nfo)]))
        await s.commit()

    async with sm() as s:
        await undo_entry(1, session=s)

    trash = root / ".kira-trash"
    assert not nfo.exists(), "the NFO must be MOVED out of its place"
    assert trash.is_dir(), "trash dir created"
    trashed = [p for p in trash.iterdir() if p.suffix == ".nfo"]
    assert trashed and trashed[0].read_bytes() == b"<nfo/>", "NFO is recoverable in trash"
    # The video itself was restored to the original location.
    assert old.read_bytes() == b"video" and not new.exists()


@pytest.mark.asyncio
async def test_hard_delete_when_trash_off(tmp_path, monkeypatch):
    # Default (no rename.cleanup_trash) → asset is hard-unlinked, no trash dir.
    sm = await _fresh_db(tmp_path, monkeypatch)
    import kira.xattr_store as xs
    monkeypatch.setattr(xs, "supported", lambda p: False)

    root = tmp_path / "lib"
    old = tmp_path / "orig" / "movie.mkv"
    new = root / "Movie (2020).mkv"
    nfo = root / "Movie (2020).nfo"
    _touch(new, b"video")
    _touch(nfo)
    async with sm() as s:
        s.add(Setting(key="paths.library_root", value=str(root)))
        s.add(RenameHistory(id=1, old_path=str(old), new_path=str(new),
                            operation="move", created_assets=[str(nfo)]))
        await s.commit()

    async with sm() as s:
        await undo_entry(1, session=s)

    assert not nfo.exists()
    assert not (root / ".kira-trash").exists(), "no trash dir when the setting is off"


# ── 5. subtitle → reuse-cache + ledger desync ────────────────────────────────────
@pytest.mark.asyncio
async def test_srt_is_routed_to_cache_subtitle(tmp_path, monkeypatch):
    # Monkeypatch subcache.cache_subtitle to assert it's invoked with the right
    # args and to "consume" the file (as the real one does by moving it).
    sm = await _fresh_db(tmp_path, monkeypatch)
    from kira.subtitles import subcache

    root = tmp_path / "lib"
    video = root / "Movie (2020).mkv"
    srt = root / "Movie (2020).en.srt"
    _touch(video)
    _touch(srt, b"1\n00:00:01,000 --> 00:00:02,000\nhi\n")

    calls: list[dict] = []

    async def _fake_cache(srt_path, *, video_path, language):
        calls.append({"srt": srt_path, "video": video_path, "lang": language})
        import os
        os.remove(srt_path)              # mimic the real move-into-cache
        return str(root / ".kira-subcache" / "h_x.en.srt")

    monkeypatch.setattr(subcache, "cache_subtitle", _fake_cache)

    async with sm() as s:
        # A subtitle_assets ledger row that should be retired once the .srt goes.
        mf = MediaFile(id=1, file_path=str(video), status="renamed")
        s.add(mf)
        s.add(SubtitleAsset(id=1, media_file_id=1, language="en",
                            provider="opensubtitles", path=str(srt), active=True))
        await s.commit()

    removed = await _remove_recorded_assets_for_video(sm, str(video), [str(root)], [str(srt)])

    assert removed == 1
    assert calls and calls[0]["video"] == str(video) and calls[0]["lang"] == "en"
    assert not srt.exists(), "the .srt was consumed by the cache"
    # The ledger row was deactivated so the Subtitles view won't show a gone file.
    async with sm() as s:
        a = await s.get(SubtitleAsset, 1)
        assert a.active is False and a.path is None


# Helper: drive _remove_recorded_assets with a session + a RenameHistory anchor so
# the subtitle_assets desync path is exercised the same way the undo flow does.
async def _remove_recorded_assets_for_video(sm, video_path, roots, paths):
    async with sm() as s:
        entry = RenameHistory(media_file_id=1, old_path="o", new_path=video_path,
                              operation="move", created_assets=paths)
        removed = await _remove_recorded_assets(
            paths, roots, trash_root=None, session=s, entry=entry, video_path=video_path,
        )
        await s.commit()
        return removed


# A naive-UTC "now" for undone_at, matching how the app stores it (SQLite naive).
def db_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)
