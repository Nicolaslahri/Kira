"""Scan prunes files that vanished from disk — the mark-and-sweep half — safely.

Real SQLite round-trip + real temp files. Proves a tracked file deleted from
disk gets its row removed, while these are ALL kept:
  • a present file the walk saw,
  • a present file the walk DIDN'T see (ignored / extension-filtered) — stat()
    confirms it's there, so it survives,
  • a (missing) file OUTSIDE the scanned roots — never in scope.

The safety crux: a row is pruned only when stat() CONFIRMS the file is gone.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api import scans
from kira.models import MediaFile


def _norm(p) -> set[str]:
    """Stand-in for the worker's `_norm` — lowercased, both slash styles."""
    pl = str(p).lower()
    return {pl, pl.replace("/", "\\"), pl.replace("\\", "/")}


async def _session(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'prune.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    await db.init_db()
    return sm


@pytest.mark.asyncio
async def test_prune_removes_only_confirmed_missing(tmp_path, monkeypatch):
    sm = await _session(tmp_path, monkeypatch)
    root = tmp_path / "media"; root.mkdir()
    other = tmp_path / "other"; other.mkdir()

    keep = root / "keep.S01E01.mkv"; keep.write_text("x")
    filtered = root / "filtered.S01E02.mkv"; filtered.write_text("x")  # exists, not walked
    gone = root / "gone.S01E03.mkv"          # never created → deleted from disk
    outside = other / "outside.S01E04.mkv"   # under a DIFFERENT root, also missing

    async with sm() as s:
        for p in (keep, filtered, gone, outside):
            s.add(MediaFile(file_path=str(p), media_type="tv", status="matched",
                            parsed_data={"title": "X"}))
        await s.commit()

    # The walk this scan "saw" only `keep`. `filtered` exists but wasn't walked.
    walked = _norm(keep)
    async with sm() as s:
        removed = await scans._prune_missing_files(s, [str(root)], walked, _norm)
    assert removed == 1  # only `gone`

    async with sm() as s:
        paths = set((await s.scalars(select(MediaFile.file_path))).all())
    assert str(keep) in paths        # present + walked → kept
    assert str(filtered) in paths    # present but not walked → stat() saved it
    assert str(outside) in paths     # missing but outside scanned roots → kept
    assert str(gone) not in paths    # confirmed gone → pruned


@pytest.mark.asyncio
async def test_prune_is_noop_when_nothing_missing(tmp_path, monkeypatch):
    sm = await _session(tmp_path, monkeypatch)
    root = tmp_path / "media"; root.mkdir()
    a = root / "a.S01E01.mkv"; a.write_text("x")
    b = root / "b.S01E02.mkv"; b.write_text("x")
    async with sm() as s:
        for p in (a, b):
            s.add(MediaFile(file_path=str(p), media_type="tv", status="matched",
                            parsed_data={"title": "X"}))
        await s.commit()
    # Both walked (and both exist) → nothing to prune.
    async with sm() as s:
        removed = await scans._prune_missing_files(s, [str(root)], _norm(a) | _norm(b), _norm)
    assert removed == 0
    async with sm() as s:
        assert len((await s.scalars(select(MediaFile.id))).all()) == 2


@pytest.mark.asyncio
async def test_prune_skips_root_that_walked_zero_files(tmp_path, monkeypatch):
    """The NAS-disconnect guard: when a scan root walks ZERO files (a dropped
    mount presenting as an empty-but-walkable dir), the tracked rows under it are
    KEPT — even though stat() would confirm them gone. A disconnect must never be
    read as 'the user deleted everything' and wipe the whole index."""
    sm = await _session(tmp_path, monkeypatch)
    root = tmp_path / "media"; root.mkdir()
    # Rows for files that are NOT on disk (the share dropped → every stat = gone).
    a = root / "a.S01E01.mkv"
    b = root / "b.S01E02.mkv"
    async with sm() as s:
        for p in (a, b):
            s.add(MediaFile(file_path=str(p), media_type="tv", status="matched",
                            parsed_data={"title": "X"}))
        await s.commit()
    # The walk saw NOTHING this scan (mount dropped) — the catastrophic case.
    async with sm() as s:
        removed = await scans._prune_missing_files(s, [str(root)], set(), _norm)
    assert removed == 0                       # guard tripped — nothing pruned
    async with sm() as s:
        assert len((await s.scalars(select(MediaFile.id))).all()) == 2


def _alias_norm(drive_root: str, real_root: str):
    """Mirror the worker's alias-aware `_norm`: swap between a mapped-drive root
    spelling and its resolved form (what `Path('Z:/').resolve()` returns), and
    emit both slash styles. This is exactly the One Piece S00E04/S23E04 case."""
    dl, rl = drive_root.lower(), real_root.lower()

    def _n(p) -> set[str]:
        pl = str(p).lower()
        bases = {pl}
        if pl.startswith(dl):
            bases.add(rl + pl[len(dl):])
        elif pl.startswith(rl):
            bases.add(dl + pl[len(rl):])
        forms: set[str] = set()
        for b in bases:
            forms.add(b)
            forms.add(b.replace("/", "\\"))
            forms.add(b.replace("\\", "/"))
        return forms

    return _n


@pytest.mark.asyncio
async def test_prune_is_drive_letter_unc_alias_aware(tmp_path, monkeypatch):
    """Phantom-duplicate fix: a row stored under one spelling of a share (the
    UNC/resolved form an earlier rename wrote) must still be pruned when the scan
    runs under the OTHER spelling (the mapped drive). Before the fix the raw-string
    scope check judged it out-of-scope and the stale ghost row lived forever,
    surfacing as a false "duplicate" of the re-scanned file."""
    sm = await _session(tmp_path, monkeypatch)
    real_root = tmp_path / "media"; real_root.mkdir()
    present = real_root / "present.S01E01.mkv"; present.write_text("x")
    gone = real_root / "gone.S01E02.mkv"          # never created → gone on disk

    # Rows stored under the REAL (resolved) spelling, like Kira's renamed rows.
    async with sm() as s:
        for p in (present, gone):
            s.add(MediaFile(file_path=str(p), media_type="tv", status="renamed",
                            parsed_data={"title": "X"}))
        await s.commit()

    # ...but the scan runs under a DIFFERENT spelling: a mapped-drive root that
    # `norm_fn` aliases to the real path (what Path("Z:/").resolve() yields).
    drive_root = "R:\\media"
    norm = _alias_norm(drive_root, str(real_root))

    async with sm() as s:
        removed = await scans._prune_missing_files(s, [drive_root], norm(present), norm)
    assert removed == 1   # `gone` pruned ACROSS the spelling alias

    async with sm() as s:
        paths = set((await s.scalars(select(MediaFile.file_path))).all())
    assert str(present) in paths   # present (aliased) → NOT over-pruned
    assert str(gone) not in paths  # confirmed gone → pruned despite spelling


@pytest.mark.asyncio
async def test_prune_posts_notification(tmp_path, monkeypatch):
    sm = await _session(tmp_path, monkeypatch)
    root = tmp_path / "media"; root.mkdir()
    keep = root / "keep.S01E02.mkv"; keep.write_text("x")  # present + walked → root isn't "empty"
    gone = root / "gone.S01E01.mkv"  # never created
    async with sm() as s:
        for p in (keep, gone):
            s.add(MediaFile(file_path=str(p), media_type="tv", status="matched",
                            parsed_data={"title": "X"}))
        await s.commit()
    async with sm() as s:
        removed = await scans._prune_missing_files(s, [str(root)], _norm(keep), _norm)
    assert removed == 1
    from kira.models import Notification
    async with sm() as s:
        notes = list(await s.scalars(select(Notification)))
    assert any("no longer on disk" in n.title for n in notes)


@pytest.mark.asyncio
async def test_prune_skips_errored_subtree_but_sweeps_clean_one(tmp_path, monkeypatch):
    """Resilient sweep: a folder that errored this scan (passed via `error_paths`)
    is EXCLUDED — a 'missing' row inside it is kept, because 'the walk didn't see
    it' can't be trusted in an unreadable subtree — while a deleted file under a
    cleanly-walked sibling folder is still pruned. One bad folder no longer
    freezes all cleanup (the all-or-nothing-gate bug)."""
    sm = await _session(tmp_path, monkeypatch)
    root = tmp_path / "media"; root.mkdir()
    clean = root / "CleanShow"; clean.mkdir()
    flaky = root / "FlakyShow"; flaky.mkdir()

    clean_keep = clean / "keep.S01E02.mkv"; clean_keep.write_text("x")  # present + walked
    clean_gone = clean / "clean.S01E01.mkv"   # deleted on disk, under a clean folder
    flaky_gone = flaky / "flaky.S01E01.mkv"   # also gone, but its folder errored this scan
    async with sm() as s:
        for p in (clean_keep, clean_gone, flaky_gone):
            s.add(MediaFile(file_path=str(p), media_type="tv", status="matched",
                            parsed_data={"title": "X"}))
        await s.commit()

    # The walk SAW clean_keep (so the root isn't zero-file → the disconnect guard
    # doesn't trip); clean_gone reports gone; FlakyShow raised a scandir error.
    async with sm() as s:
        removed = await scans._prune_missing_files(
            s, [str(root)], _norm(clean_keep), _norm, error_paths=[str(flaky)],
        )
    assert removed == 1   # only the clean-folder deletion

    async with sm() as s:
        paths = set((await s.scalars(select(MediaFile.file_path))).all())
    assert str(clean_keep) in paths       # present + walked → kept
    assert str(clean_gone) not in paths   # clean subtree → swept
    assert str(flaky_gone) in paths       # errored subtree → kept (can't trust)


@pytest.mark.asyncio
async def test_reconcile_endpoint_prunes_gone_review_files(tmp_path, monkeypatch):
    """The walk-FREE /files/reconcile sweep (the on-refresh deletion check): a
    review-stage row whose disk file is gone is dropped with no scan and no walk
    (so no all-or-nothing gate); a present file is kept, and a post-rename
    ("renamed") row is OUT of scope — left to the scan prune."""
    from kira.api.files import reconcile_files

    sm = await _session(tmp_path, monkeypatch)
    root = tmp_path / "media"; root.mkdir()
    present = root / "present.S01E01.mkv"; present.write_text("x")
    gone = root / "gone.S01E02.mkv"               # never created → deleted on disk
    renamed_gone = root / "renamed.S01E03.mkv"    # gone too, but already organized
    async with sm() as s:
        s.add(MediaFile(file_path=str(present), media_type="tv", status="matched",
                        parsed_data={"title": "X"}))
        s.add(MediaFile(file_path=str(gone), media_type="tv", status="matched",
                        parsed_data={"title": "X"}))
        s.add(MediaFile(file_path=str(renamed_gone), media_type="tv", status="renamed",
                        parsed_data={"title": "X"}))
        await s.commit()

    async with sm() as s:
        out = await reconcile_files(s)
    assert out["removed"] == 1   # only the review-stage gone file

    async with sm() as s:
        paths = set((await s.scalars(select(MediaFile.file_path))).all())
    assert str(present) in paths          # present → kept
    assert str(gone) not in paths         # review-stage + gone → pruned on the spot
    assert str(renamed_gone) in paths     # post-rename → out of scope (scan prune owns it)
