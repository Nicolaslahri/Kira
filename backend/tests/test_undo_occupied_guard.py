"""Undo must not clobber an occupied original location.

If a file renamed via Kira leaves its old path free, undo moves it back. But if
the user later drops a DIFFERENT file at the old name, undo must REFUSE — the
cross-device copy path opens old_path with "wb" (truncate) and same-FS os.rename
overwrites on POSIX, so without the guard undo would silently destroy it."""
from __future__ import annotations

import pytest

from kira.renamer.operations import FileOp, undo_op


def test_undo_refuses_when_original_occupied(tmp_path):
    old = tmp_path / "old.mkv"
    new = tmp_path / "new.mkv"
    new.write_bytes(b"the renamed file")
    old.write_bytes(b"a precious DIFFERENT file the user put here later")

    with pytest.raises(FileExistsError):
        undo_op(FileOp.MOVE, old, new)

    # Both files intact — nothing truncated or moved.
    assert old.read_bytes() == b"a precious DIFFERENT file the user put here later"
    assert new.read_bytes() == b"the renamed file"


def test_undo_moves_back_when_original_free(tmp_path):
    old = tmp_path / "old.mkv"
    new = tmp_path / "new.mkv"
    new.write_bytes(b"data")
    undo_op(FileOp.MOVE, old, new)
    assert old.read_bytes() == b"data" and not new.exists()
