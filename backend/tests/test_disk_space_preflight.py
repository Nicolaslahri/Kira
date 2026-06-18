"""Disk-space preflight — refuse a copy that can't fit BEFORE writing a byte."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from kira.renamer import operations
from kira.renamer.operations import FileOp, _ensure_space, execute_op


def _fake_usage(free):
    return lambda _p: SimpleNamespace(total=0, used=0, free=free)


def test_ensure_space_raises_when_insufficient(tmp_path, monkeypatch):
    src = tmp_path / "big.mkv"
    src.write_bytes(b"x" * 5000)
    (tmp_path / "out").mkdir()
    monkeypatch.setattr(operations.shutil, "disk_usage", _fake_usage(100))
    with pytest.raises(OSError):
        _ensure_space(src, tmp_path / "out" / "big.mkv")


def test_ensure_space_ok_when_enough(tmp_path, monkeypatch):
    src = tmp_path / "small.mkv"
    src.write_bytes(b"x" * 5000)
    (tmp_path / "out").mkdir()
    monkeypatch.setattr(operations.shutil, "disk_usage", _fake_usage(10**9))
    _ensure_space(src, tmp_path / "out" / "small.mkv")     # no raise


def test_execute_copy_refuses_and_writes_nothing(tmp_path, monkeypatch):
    src = tmp_path / "s.mkv"
    src.write_bytes(b"x" * 5000)
    dst = tmp_path / "lib" / "s.mkv"
    monkeypatch.setattr(operations.shutil, "disk_usage", _fake_usage(100))
    with pytest.raises(OSError):
        execute_op(FileOp.COPY, src, dst)
    assert not dst.exists()                                # no partial left behind
