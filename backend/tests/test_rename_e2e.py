"""Real end-to-end `perform_rename`: actual temp files + a real SQLite round-trip.

The rest of the suite only SPIES on perform_rename (test_auto_rename_execute) or
checks route binding + an empty batch (test_rename_route) — nothing drives a real
rename through the pipeline. This is the behavioral safety net under the
rename-hardening pass (and the prerequisite for safely extracting _rename_one_file):
it proves a genuine move/copy relocates the video, records the RenameHistory video
row, drags the `.srt` sidecar along under a parent_id child row, and writes the NFO.

Only `media_type` config + the DB are real; no network (artwork download stays off).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api.history import undo_entry
from kira.api.rename import RenameRequest, perform_rename, reconcile_pending_renames
from kira.models import Match, MediaFile, RenameHistory, RenameIntent, Setting
from kira.parser import parse_filename

_PD = {"original_filename": "x.mkv", "media_type": "movie", "title": "X"}

STEM = "The.Matrix.1999.1080p.BluRay.x264"


async def _fresh_db(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'kira_rename.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)  # post-rename hooks open this
    await db.init_db()
    return sm


async def _setup(tmp_path, monkeypatch, *, write_nfo=True):
    sm = await _fresh_db(tmp_path, monkeypatch)

    # Real files: a movie + a matching subtitle sidecar, inside a `movies`
    # type-folder so the in-place target computation has somewhere to anchor.
    media = tmp_path / "movies"
    media.mkdir()
    src = media / f"{STEM}.mkv"
    srt = media / f"{STEM}.en.srt"
    src.write_bytes(b"video-bytes")
    srt.write_bytes(b"subtitle-bytes")

    pd = parse_filename(f"{STEM}.mkv").to_dict()
    assert pd["media_type"] == "movie"

    async with sm() as s:
        if write_nfo:
            s.add(Setting(key="naming.write_nfo", value=True))
        mf = MediaFile(file_path=str(src), parsed_data=pd, media_type="movie", status="matched")
        s.add(mf)
        await s.flush()
        s.add(Match(
            media_file_id=mf.id, provider="tmdb", provider_id="603", match_type="movie",
            confidence=0.99, title="The Matrix", year=1999, is_selected=True,
        ))
        await s.commit()
        fid = mf.id
    return sm, fid, src, srt


async def _run(sm, req):
    """Drive perform_rename on a session that's properly closed afterward (the
    post-rename hooks open their own SessionLocal, so this one is just the batch)."""
    async with sm() as s:
        return await perform_rename(req, s)


async def _history(sm):
    async with sm() as s:
        return list(await s.scalars(select(RenameHistory)))


@pytest.mark.asyncio
async def test_move_relocates_video_sidecar_history_and_nfo(tmp_path, monkeypatch):
    sm, fid, src, srt = await _setup(tmp_path, monkeypatch)

    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move"))

    assert res.succeeded == 1 and res.failed == 0, res
    item = res.items[0]
    assert item.ok, item.error
    new = Path(item.new_path)
    assert new.exists(), "renamed video should be at the new path"
    assert not src.exists(), "move should have removed the source video"

    # NFO written beside the renamed movie.
    assert new.with_suffix(".nfo").exists(), "movie .nfo should be written next to the target"

    # Sidecar dragged along: old gone, new present beside the video.
    new_srt = new.with_name(new.stem + ".en.srt")
    assert new_srt.exists(), "subtitle should have moved alongside the video"
    assert not srt.exists(), "old subtitle should be gone after a move"

    rows = await _history(sm)
    parents = [r for r in rows if r.parent_id is None]
    children = [r for r in rows if r.parent_id is not None]
    assert len(parents) == 1 and len(children) == 1
    assert parents[0].old_path == str(src) and parents[0].new_path == str(new)
    assert parents[0].operation == "move"
    assert children[0].parent_id == parents[0].id
    assert children[0].new_path.endswith(".srt")


@pytest.mark.asyncio
async def test_re_submitting_same_rename_is_noop_no_duplicate_history(tmp_path, monkeypatch):
    # Repro of "approved one movie, see it twice in history": re-running the rename
    # after the file already sits at its target must be a no-op — no self-move and
    # no second src==dst history row.
    sm, fid, src, srt = await _setup(tmp_path, monkeypatch, write_nfo=False)

    res1 = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move"))
    assert res1.items[0].ok
    new1 = res1.items[0].new_path

    # Second submit — MediaFile.file_path is now the target.
    res2 = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move"))
    assert res2.items[0].ok
    assert res2.items[0].new_path == new1
    assert "Already at target" in (res2.items[0].error or "")

    parents = [r for r in await _history(sm) if r.parent_id is None]
    assert len(parents) == 1, f"a re-submit must not add a history row, got {len(parents)}"


@pytest.mark.asyncio
async def test_copy_keeps_source_and_records_history(tmp_path, monkeypatch):
    sm, fid, src, srt = await _setup(tmp_path, monkeypatch)

    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="copy"))

    assert res.succeeded == 1, res
    new = Path(res.items[0].new_path)
    assert new.exists() and src.exists(), "copy must leave the source in place"
    new_srt = new.with_name(new.stem + ".en.srt")
    assert new_srt.exists() and srt.exists(), "copy must leave the source subtitle in place"

    rows = await _history(sm)
    assert any(r.parent_id is None and r.operation == "copy" for r in rows)


@pytest.mark.asyncio
async def test_dry_run_touches_nothing(tmp_path, monkeypatch):
    sm, fid, src, srt = await _setup(tmp_path, monkeypatch)

    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move", dry_run=True))

    item = res.items[0]
    assert item.ok and item.new_path
    assert src.exists() and srt.exists(), "dry-run must not move anything"
    assert not Path(item.new_path).exists(), "dry-run must not create the target"
    assert await _history(sm) == [], "dry-run must not write history"
    # #6: the preview surfaces the side effects too — the sidecar that would move
    # and the NFO that would be written (write_nfo is on in _setup).
    assert item.sidecars and any(s.endswith(".srt") for s in item.sidecars)
    assert item.nfo and any(n.endswith(".nfo") for n in item.nfo)


@pytest.mark.asyncio
async def test_unselected_matches_pick_highest_confidence_not_list_order(tmp_path, monkeypatch):
    # #5: when nothing is is_selected, the highest-confidence match must win —
    # never relationship list-order [0], which could rename to the wrong title.
    sm = await _fresh_db(tmp_path, monkeypatch)
    media = tmp_path / "movies"
    media.mkdir()
    src = media / f"{STEM}.mkv"
    src.write_bytes(b"v")
    pd = parse_filename(f"{STEM}.mkv").to_dict()
    async with sm() as s:
        mf = MediaFile(file_path=str(src), parsed_data=pd, media_type="movie", status="matched")
        s.add(mf)
        await s.flush()
        # Inserted so [0] is the LOW-confidence (wrong) match.
        s.add_all([
            Match(media_file_id=mf.id, provider="tmdb", provider_id="1", match_type="movie",
                  confidence=0.50, title="Wrong Low", year=1999, is_selected=False),
            Match(media_file_id=mf.id, provider="tmdb", provider_id="2", match_type="movie",
                  confidence=0.70, title="Wrong Mid", year=1999, is_selected=False),
            Match(media_file_id=mf.id, provider="tmdb", provider_id="3", match_type="movie",
                  confidence=0.95, title="Correct High", year=1999, is_selected=False),
        ])
        await s.commit()
        fid = mf.id

    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move", dry_run=True))

    assert res.items[0].ok, res.items[0].error
    assert "Correct High" in res.items[0].new_path
    assert "Wrong" not in res.items[0].new_path


@pytest.mark.asyncio
async def test_created_assets_recorded_and_undo_deletes_them(tmp_path, monkeypatch):
    # #1: the NFO the rename wrote is RECORDED on the history row, and undo
    # deletes exactly that recorded path (authoritative) while restoring the video.
    sm, fid, src, srt = await _setup(tmp_path, monkeypatch)  # write_nfo on
    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move"))
    new = Path(res.items[0].new_path)
    nfo = new.with_suffix(".nfo")
    assert nfo.exists()

    rows = await _history(sm)
    parent = next(r for r in rows if r.parent_id is None)
    assert parent.created_assets and str(nfo) in parent.created_assets

    async with sm() as s:
        await undo_entry(parent.id, s)

    assert not nfo.exists(), "undo must delete the recorded NFO (no orphan)"
    assert src.exists() and not new.exists(), "undo restores the video"


@pytest.mark.asyncio
async def test_re_rename_to_new_target_sweeps_prior_assets(tmp_path, monkeypatch):
    # #1 forward sweep: re-renaming to a DIFFERENT target (no undo between) must
    # remove the artwork/NFO the PRIOR rename wrote under the old target's name.
    sm = await _fresh_db(tmp_path, monkeypatch)
    media = tmp_path / "movies"
    media.mkdir()
    src = media / f"{STEM}.mkv"
    src.write_bytes(b"v")
    pd = parse_filename(f"{STEM}.mkv").to_dict()
    async with sm() as s:
        s.add(Setting(key="naming.write_nfo", value=True))
        mf = MediaFile(file_path=str(src), parsed_data=pd, media_type="movie", status="matched")
        s.add(mf)
        await s.flush()
        s.add(Match(media_file_id=mf.id, provider="tmdb", provider_id="1", match_type="movie",
                    confidence=0.9, title="First Title", year=1999, is_selected=True))
        await s.commit()
        fid = mf.id

    res1 = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move"))
    b_nfo = Path(res1.items[0].new_path).with_suffix(".nfo")
    assert b_nfo.exists()

    # Re-point the match to a DIFFERENT title → different target, with NO undo.
    async with sm() as s:
        m = (await s.scalars(select(Match).where(Match.media_file_id == fid))).first()
        m.title = "Second Title"
        await s.commit()

    res2 = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move"))
    c = Path(res2.items[0].new_path)
    c_nfo = c.with_suffix(".nfo")
    assert "Second Title" in str(c)
    assert c.exists() and c_nfo.exists(), "new target + its NFO present"
    assert not b_nfo.exists(), "prior target's NFO should be swept on re-rename"


@pytest.mark.asyncio
async def test_untrackable_sidecar_is_not_moved(tmp_path, monkeypatch):
    # #2: if we can't get the parent history id (flush fails), the sidecar must
    # NOT be moved — moving it untracked would orphan it on undo. Patching the
    # instance's async flush() hits ONLY the explicit pre-sidecar flush (queries
    # + commit autoflush via the sync session), isolating exactly that branch.
    sm, fid, src, srt = await _setup(tmp_path, monkeypatch, write_nfo=False)

    async def _boom(*a, **k):
        raise RuntimeError("simulated flush failure")

    async with sm() as s:
        monkeypatch.setattr(s, "flush", _boom)
        res = await perform_rename(RenameRequest(file_ids=[fid], profile="Plex", op="move"), s)

    new = Path(res.items[0].new_path)
    assert new.exists() and not src.exists(), "the video itself still moves"
    assert srt.exists(), "untrackable sidecar must stay put (never moved without a history row)"
    assert not new.with_name(new.stem + ".en.srt").exists(), "sidecar must not appear at the target"

    rows = await _history(sm)
    assert rows and all(r.parent_id is None for r in rows), "no child sidecar rows when untrackable"


@pytest.mark.asyncio
async def test_duplicate_target_in_batch_fails_collider_without_clobber(tmp_path, monkeypatch):
    # #3: two files that render to the SAME target (same movie, same quality, in
    # different folders) must not silently overwrite each other. First claimant
    # wins; the second fails with a clear pointer and stays untouched.
    sm = await _fresh_db(tmp_path, monkeypatch)
    da = tmp_path / "movies" / "a"
    db_ = tmp_path / "movies" / "b"
    da.mkdir(parents=True)
    db_.mkdir(parents=True)
    a = da / "The.Matrix.1999.1080p.x264.mkv"
    b = db_ / "The.Matrix.1999.1080p.x264.mkv"
    a.write_bytes(b"aaa")
    b.write_bytes(b"bbb")

    ids = []
    async with sm() as s:
        for p in (a, b):
            pd = parse_filename(p.name).to_dict()
            mf = MediaFile(file_path=str(p), parsed_data=pd, media_type="movie", status="matched")
            s.add(mf)
            await s.flush()
            s.add(Match(media_file_id=mf.id, provider="tmdb", provider_id="603", match_type="movie",
                        confidence=0.9, title="The Matrix", year=1999, is_selected=True))
            ids.append(mf.id)
        await s.commit()

    res = await _run(sm, RenameRequest(file_ids=ids, profile="Plex", op="move"))

    oks = [i for i in res.items if i.ok]
    fails = [i for i in res.items if not i.ok]
    assert len(oks) == 1 and len(fails) == 1, res
    assert "Duplicate target" in (fails[0].error or "")
    # No data loss: the colliding file is still sitting at its source.
    assert Path(fails[0].old_path).exists(), "the colliding file must not be moved or clobbered"


# ── #4: pending-rename intent journal + reconcile ────────────────────────────

@pytest.mark.asyncio
async def test_reconcile_finalizes_a_move_that_landed(tmp_path, monkeypatch):
    # Crash AFTER the move, BEFORE the DB commit: dst on disk, src gone, DB still
    # points at src + an intent row survives. Reconcile finalizes the DB to match.
    sm = await _fresh_db(tmp_path, monkeypatch)
    src = tmp_path / "old.mkv"
    dst = tmp_path / "New Name (2020).mkv"
    dst.write_bytes(b"v")  # the move landed
    async with sm() as s:
        mf = MediaFile(file_path=str(src), parsed_data=_PD, media_type="movie", status="renaming")
        s.add(mf)
        await s.flush()
        s.add(RenameIntent(media_file_id=mf.id, src=str(src), dst=str(dst), operation="move"))
        await s.commit()
        fid = mf.id

    final, disc = await reconcile_pending_renames()

    assert (final, disc) == (1, 0)
    async with sm() as s:
        mf = await s.get(MediaFile, fid)
        assert mf.file_path == str(dst) and mf.status == "renamed", "DB now matches disk"
        hist = list(await s.scalars(select(RenameHistory).where(RenameHistory.new_path == str(dst))))
        assert len(hist) == 1, "a recovery history row is created (undoable)"
        assert list(await s.scalars(select(RenameIntent))) == [], "intent cleared"


@pytest.mark.asyncio
async def test_reconcile_discards_a_move_that_never_ran(tmp_path, monkeypatch):
    # Crash BEFORE the move (or it failed): src still on disk, dst absent. Nothing
    # to finalize — the DB already points at src; just drop the stale intent.
    sm = await _fresh_db(tmp_path, monkeypatch)
    src = tmp_path / "still_here.mkv"
    src.write_bytes(b"v")
    dst = tmp_path / "Target (2020).mkv"  # never created
    async with sm() as s:
        mf = MediaFile(file_path=str(src), parsed_data=_PD, media_type="movie", status="matched")
        s.add(mf)
        await s.flush()
        s.add(RenameIntent(media_file_id=mf.id, src=str(src), dst=str(dst), operation="move"))
        await s.commit()
        fid = mf.id

    final, disc = await reconcile_pending_renames()

    assert (final, disc) == (0, 1)
    async with sm() as s:
        mf = await s.get(MediaFile, fid)
        assert mf.file_path == str(src) and mf.status == "matched", "row untouched"
        assert list(await s.scalars(select(RenameIntent))) == [], "stale intent dropped"
        assert list(await s.scalars(select(RenameHistory))) == [], "no phantom history row"


@pytest.mark.asyncio
async def test_reconcile_does_not_duplicate_existing_history(tmp_path, monkeypatch):
    # If the move's history row already committed (crash AFTER history, in a later
    # step), finalize must not add a SECOND row for the same src→dst.
    sm = await _fresh_db(tmp_path, monkeypatch)
    src = tmp_path / "old.mkv"
    dst = tmp_path / "Dup (2020).mkv"
    dst.write_bytes(b"v")
    async with sm() as s:
        mf = MediaFile(file_path=str(src), parsed_data=_PD, media_type="movie", status="renaming")
        s.add(mf)
        await s.flush()
        s.add(RenameHistory(media_file_id=mf.id, old_path=str(src), new_path=str(dst), operation="move"))
        s.add(RenameIntent(media_file_id=mf.id, src=str(src), dst=str(dst), operation="move"))
        await s.commit()

    final, disc = await reconcile_pending_renames()

    assert final == 1
    async with sm() as s:
        hist = list(await s.scalars(select(RenameHistory).where(RenameHistory.new_path == str(dst))))
        assert len(hist) == 1, "must not duplicate the already-recorded history row"
