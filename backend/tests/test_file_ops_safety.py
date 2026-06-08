"""Data-loss safety of the file-operation path (audit fixes).

Two CRITICAL bugs the adversarial audit found, both able to delete the only
copy of a file:
  1. Cross-device Move: the integrity step fsync'd a READ handle (no-op) and
     verified by SIZE only, so a truncated/corrupt destination could pass and
     the source got deleted. Now: own the copy, fsync the WRITE fd, verify by
     content hash before unlinking the source.
  2. Case-only rename on a case-insensitive volume: src and dst are the same
     directory entry, but a resolve()-string compare saw them as different and
     unlinked the source — destroying the file. Now: case-folded identity test
     + a temp-hop rename that never unlinks the only entry.
"""

from __future__ import annotations

import os

import pytest

from kira.renamer import operations as ops
from kira.renamer.operations import FileOp, _atomic_move, execute_op


def _force_exdev(monkeypatch):
    """Make os.rename raise EXDEV so _atomic_move takes the cross-device path."""
    def fake_rename(a, b):
        raise OSError(18, "Invalid cross-device link")
    monkeypatch.setattr(ops.os, "rename", fake_rename)


def test_cross_device_move_copies_verifies_and_removes_source(tmp_path, monkeypatch):
    src = tmp_path / "a.mkv"
    src.write_bytes(b"the-only-copy" * 5000)  # ~65 KB, spans the chunk loop fine
    dst = tmp_path / "sub" / "b.mkv"
    dst.parent.mkdir()
    _force_exdev(monkeypatch)

    _atomic_move(src, dst)

    assert dst.read_bytes() == b"the-only-copy" * 5000  # content intact at dst
    assert not src.exists()                              # source removed only after verify


def test_cross_device_move_aborts_and_preserves_source_on_corruption(tmp_path, monkeypatch):
    src = tmp_path / "a.mkv"
    src.write_bytes(b"precious-bytes" * 5000)
    dst = tmp_path / "b.mkv"
    _force_exdev(monkeypatch)

    # Simulate a corrupt/truncated destination AFTER the copy but BEFORE the
    # content verify (copystat runs in that window). The hash check must then
    # fail, the source must be preserved, and the partial dst rolled back.
    def truncating_copystat(s, d):
        from pathlib import Path
        Path(d).write_bytes(b"")  # destination silently goes empty

    monkeypatch.setattr(ops.shutil, "copystat", truncating_copystat)

    with pytest.raises(OSError):
        _atomic_move(src, dst)

    assert src.exists()                                   # SOURCE PRESERVED (no data loss)
    assert src.read_bytes() == b"precious-bytes" * 5000   # untouched
    assert not dst.exists()                               # partial dst rolled back


def test_case_only_rename_never_destroys_the_file(tmp_path):
    """`show.mkv` → `Show.mkv` (case-only). On a case-insensitive volume these
    are one entry; the file must survive under the new case, never be unlinked.
    (On a case-sensitive volume they're distinct and it's a plain move — the
    file still ends up at the new name. Either way: no loss.)"""
    src = tmp_path / "show.mkv"
    src.write_bytes(b"keepme")
    dst = tmp_path / "Show.mkv"

    execute_op(FileOp.MOVE, src, dst)

    survivors = [p for p in tmp_path.iterdir() if p.is_file()]
    assert len(survivors) == 1
    assert survivors[0].read_bytes() == b"keepme"        # bytes survived
    assert survivors[0].name == "Show.mkv"               # under the new case


def test_literal_same_path_move_is_noop(tmp_path):
    """src == dst exactly: no-op, file untouched (the prior CATASTROPHIC-BUG
    guard must still hold)."""
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"data")
    execute_op(FileOp.MOVE, f, f)
    assert f.read_bytes() == b"data"
