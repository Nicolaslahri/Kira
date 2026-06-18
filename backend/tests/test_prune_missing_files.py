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
    gone = root / "gone.S01E01.mkv"  # never created
    async with sm() as s:
        s.add(MediaFile(file_path=str(gone), media_type="tv", status="matched",
                        parsed_data={"title": "X"}))
        await s.commit()
    async with sm() as s:
        removed = await scans._prune_missing_files(s, [str(root)], set(), _norm)
    assert removed == 1
    from kira.models import Notification
    async with sm() as s:
        notes = list(await s.scalars(select(Notification)))
    assert any("no longer on disk" in n.title for n in notes)
