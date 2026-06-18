"""rename.symlink_relative — when on, execute_op writes a RELATIVE symlink
target (portable across remounts / changed bind-mount paths); absolute by
default. Skipped where the OS forbids unprivileged symlink creation (common on
Windows without Developer Mode).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kira.renamer.operations import FileOp, execute_op


def _mk(tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "src" / "movie.mkv"
    src.parent.mkdir(parents=True)
    src.write_text("x")
    dst = tmp_path / "lib" / "Movie (2020).mkv"
    dst.parent.mkdir(parents=True)
    return src, dst


def test_symlink_relative_target(tmp_path: Path) -> None:
    src, dst = _mk(tmp_path)
    try:
        execute_op(FileOp.SYMLINK, src, dst, symlink_relative=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform")
    target = os.readlink(str(dst))
    assert not os.path.isabs(target)                          # relative
    assert (dst.parent / target).resolve() == src.resolve()   # resolves to src


def test_symlink_absolute_by_default(tmp_path: Path) -> None:
    src, dst = _mk(tmp_path)
    try:
        execute_op(FileOp.SYMLINK, src, dst)                  # no flag → absolute
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform")
    target = os.readlink(str(dst))
    assert os.path.isabs(target)
    assert Path(target).resolve() == src.resolve()


def test_symlink_relative_idempotent(tmp_path: Path) -> None:
    """Re-running a relative symlink op is a no-op success — the idempotency
    check resolves the relative target against dst's dir, not the CWD."""
    src, dst = _mk(tmp_path)
    try:
        execute_op(FileOp.SYMLINK, src, dst, symlink_relative=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform")
    execute_op(FileOp.SYMLINK, src, dst, symlink_relative=True)   # must NOT raise
    assert os.path.islink(str(dst))
