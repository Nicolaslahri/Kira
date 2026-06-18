"""rename.on_conflict — policy when a DIFFERENT file already occupies the
target. "error" (default) raises FileExistsError; "skip" raises RenameSkipped
(the rename endpoint treats it as a deliberate no-op); "overwrite" is folded
into execute_op's overwrite flag by the caller, so it replaces. Idempotent
re-runs (the same file already in place) stay a safe no-op regardless.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kira.renamer.operations import FileOp, RenameSkipped, execute_op


def _two(tmp_path: Path) -> tuple[Path, Path]:
    """A source file + a DIFFERENT pre-existing file already at the target."""
    src = tmp_path / "src" / "new.mkv"
    src.parent.mkdir(parents=True)
    src.write_text("NEW")
    dst = tmp_path / "lib" / "existing.mkv"
    dst.parent.mkdir(parents=True)
    dst.write_text("OLD")
    return src, dst


def test_default_policy_raises_fileexists(tmp_path: Path) -> None:
    src, dst = _two(tmp_path)
    with pytest.raises(FileExistsError):
        execute_op(FileOp.MOVE, src, dst)          # on_conflict defaults to "error"
    assert dst.read_text() == "OLD"                # left untouched
    assert src.exists()                            # not moved


def test_skip_policy_raises_renameskipped(tmp_path: Path) -> None:
    src, dst = _two(tmp_path)
    with pytest.raises(RenameSkipped):
        execute_op(FileOp.MOVE, src, dst, on_conflict="skip")
    assert dst.read_text() == "OLD"                # BOTH files untouched
    assert src.read_text() == "NEW"


def test_overwrite_replaces(tmp_path: Path) -> None:
    src, dst = _two(tmp_path)
    # The endpoint folds on_conflict="overwrite" into overwrite=True, so the
    # existing replace path runs — no RenameSkipped / FileExistsError.
    execute_op(FileOp.MOVE, src, dst, overwrite=True)
    assert dst.read_text() == "NEW"                # replaced
    assert not src.exists()                        # moved


def test_skip_does_not_fire_on_idempotent_noop(tmp_path: Path) -> None:
    # Same path in and out → a genuine no-op; skip must NOT raise RenameSkipped.
    f = tmp_path / "lib" / "m.mkv"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    execute_op(FileOp.MOVE, f, f, on_conflict="skip")   # returns cleanly
    assert f.read_text() == "x"
