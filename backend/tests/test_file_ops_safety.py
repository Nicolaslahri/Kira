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
from kira.renamer.operations import FileOp, _atomic_move, execute_op, undo_op


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


# ── COPY-with-overwrite atomicity (audit fix) ─────────────────────────────────
# Bug: the overwrite path unlink()ed the existing destination, THEN ran
# shutil.copy2. A copy that died mid-write left neither the original nor a
# complete copy. Now COPY writes to a temp sibling and os.replace()s into place,
# so a failed copy never touches the good destination.
def test_copy_overwrite_preserves_destination_on_failure(tmp_path, monkeypatch):
    src = tmp_path / "src.mkv"
    src.write_bytes(b"new-content" * 3000)
    dst = tmp_path / "dst.mkv"
    dst.write_bytes(b"GOOD-EXISTING-FILE" * 3000)   # a file we must not lose

    def boom_copy2(a, b):
        # Simulate copy2 dying part-way (ENOSPC / network drop).
        from pathlib import Path
        Path(b).write_bytes(b"partial")            # leaves a truncated temp
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ops.shutil, "copy2", boom_copy2)

    with pytest.raises(OSError):
        execute_op(FileOp.COPY, src, dst, overwrite=True)

    # The good destination is INTACT (never pre-deleted) ...
    assert dst.read_bytes() == b"GOOD-EXISTING-FILE" * 3000
    # ... and the half-written temp was cleaned up.
    assert not (tmp_path / "dst.mkv.kira-copy-tmp").exists()
    leftovers = sorted(p.name for p in tmp_path.iterdir())
    assert leftovers == ["dst.mkv", "src.mkv"]


def test_copy_overwrite_succeeds_atomically(tmp_path):
    src = tmp_path / "src.mkv"
    src.write_bytes(b"the-new-bytes" * 3000)
    dst = tmp_path / "dst.mkv"
    dst.write_bytes(b"old")

    execute_op(FileOp.COPY, src, dst, overwrite=True)

    assert dst.read_bytes() == b"the-new-bytes" * 3000   # replaced
    assert src.read_bytes() == b"the-new-bytes" * 3000   # COPY keeps the source
    assert not (tmp_path / "dst.mkv.kira-copy-tmp").exists()


# ── Cross-device UNDO of a move (audit fix) ───────────────────────────────────
# Bug: undo_op used a plain shutil.move (unverified copy+delete across devices).
# Undo runs on the ONLY remaining copy, so a short/corrupt read could destroy it.
# Now undo routes through _atomic_move (same hash-verify + rollback as forward).
def test_undo_move_uses_verified_cross_device_path(tmp_path, monkeypatch):
    src = tmp_path / "orig" / "a.mkv"
    src.parent.mkdir()
    src.write_bytes(b"only-copy" * 4000)
    dst = tmp_path / "dest" / "b.mkv"
    dst.parent.mkdir()
    execute_op(FileOp.MOVE, src, dst)            # same-FS forward move
    assert dst.exists() and not src.exists()

    _force_exdev(monkeypatch)                    # undo takes the cross-device branch
    undo_op(FileOp.MOVE, src, dst)

    assert src.read_bytes() == b"only-copy" * 4000   # restored, content verified
    assert not dst.exists()                          # moved back, not duplicated


def test_undo_move_cross_device_preserves_only_copy_on_corruption(tmp_path, monkeypatch):
    src = tmp_path / "orig" / "a.mkv"
    src.parent.mkdir()
    src.write_bytes(b"precious" * 4000)
    dst = tmp_path / "dest" / "b.mkv"
    dst.parent.mkdir()
    execute_op(FileOp.MOVE, src, dst)            # dst now holds the only copy

    _force_exdev(monkeypatch)

    def truncating_copystat(s, d):
        from pathlib import Path
        Path(d).write_bytes(b"")                  # restore target silently empties

    monkeypatch.setattr(ops.shutil, "copystat", truncating_copystat)

    with pytest.raises(OSError):
        undo_op(FileOp.MOVE, src, dst)

    assert dst.read_bytes() == b"precious" * 4000    # the only copy survived
    assert not src.exists()                          # partial restore rolled back


# ── Zero-inode filesystem guard (audit fix) ───────────────────────────────────
# Bug: _same_inode compared st_ino/st_dev with no zero guard. Many CIFS/NFS
# mounts (common under Docker) report st_ino == st_dev == 0 for EVERY file, so
# two genuinely different files looked identical → an occupied-target MOVE would
# unlink the SOURCE believing dst was its hardlink. Now an unknown (zero) inode
# is never judged "same".
def test_same_inode_zero_inode_fs_is_not_same(tmp_path, monkeypatch):
    from types import SimpleNamespace
    a = tmp_path / "a.mkv"; a.write_bytes(b"a")
    b = tmp_path / "b.mkv"; b.write_bytes(b"b")
    # Simulate the CIFS/NFS "no real inode identity" reporting.
    monkeypatch.setattr(type(a), "stat", lambda self, *args, **kw: SimpleNamespace(st_ino=0, st_dev=0))
    assert ops._same_inode(a, b) is False


def test_same_inode_true_for_real_hardlink(tmp_path):
    a = tmp_path / "a.mkv"; a.write_bytes(b"x" * 100)
    b = tmp_path / "b.mkv"
    try:
        os.link(a, b)
    except OSError:
        pytest.skip("hardlinks unsupported on this filesystem")
    assert ops._same_inode(a, b) is True


def test_same_inode_false_for_distinct_files(tmp_path):
    a = tmp_path / "a.mkv"; a.write_bytes(b"x")
    b = tmp_path / "b.mkv"; b.write_bytes(b"y")
    assert ops._same_inode(a, b) is False


# ── Cross-batch destination claim (audit fix) ────────────────────────────────
# Bug: two concurrent rename batches (user /rename + watcher auto-rename) each
# hold their own per-batch duplicate-target guard, both pass the non-atomic
# dst.exists() check for the same rendered target, and the second os.rename()
# silently replaces the first file on POSIX — data loss with both history rows
# reporting success. execute_op now claims the destination in a process-global
# registry for the duration of the operation.
def test_concurrent_target_claim_conflicts_instead_of_replacing(tmp_path):
    from kira.renamer.operations import RenameSkipped, _claim_target, _release_target

    src = tmp_path / "b.mkv"
    src.write_bytes(b"BBB")
    dst = tmp_path / "out" / "Show.S01E01.mkv"

    # Simulate batch A holding the claim mid-operation.
    assert _claim_target(dst)
    try:
        with pytest.raises(FileExistsError):
            execute_op(FileOp.MOVE, src, dst)
        with pytest.raises(RenameSkipped):
            execute_op(FileOp.MOVE, src, dst, on_conflict="skip")
        assert src.read_bytes() == b"BBB"  # loser's file untouched
    finally:
        _release_target(dst)

    # Claim released → operation proceeds normally.
    execute_op(FileOp.MOVE, src, dst)
    assert dst.read_bytes() == b"BBB"


def test_target_claim_released_after_success_and_failure(tmp_path):
    from kira.renamer.operations import _claim_target, _release_target

    src = tmp_path / "a.mkv"
    src.write_bytes(b"AAA")
    dst = tmp_path / "done.mkv"
    execute_op(FileOp.MOVE, src, dst)
    # Registry must not leak the claim after a completed op.
    assert _claim_target(dst)
    _release_target(dst)

    # Failure path releases too (missing source raises FileNotFoundError).
    with pytest.raises(FileNotFoundError):
        execute_op(FileOp.MOVE, tmp_path / "missing.mkv", tmp_path / "x.mkv")
    assert _claim_target(tmp_path / "x.mkv")
    _release_target(tmp_path / "x.mkv")


def test_undo_claims_original_location(tmp_path):
    from kira.renamer.operations import _claim_target, _release_target

    src = tmp_path / "orig.mkv"
    src.write_bytes(b"V")
    dst = tmp_path / "renamed.mkv"
    execute_op(FileOp.MOVE, src, dst)

    # A concurrent batch is writing to the original location → undo must refuse.
    assert _claim_target(src)
    try:
        with pytest.raises(FileExistsError):
            undo_op(FileOp.MOVE, src, dst)
    finally:
        _release_target(src)

    undo_op(FileOp.MOVE, src, dst)
    assert src.read_bytes() == b"V"
