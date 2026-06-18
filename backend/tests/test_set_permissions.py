"""rename.set_permissions — post-rename chmod/chown for Docker / NAS so the
media server (often a different uid) can read renamed files. execute_op applies
a perms spec best-effort: octal file_mode/dir_mode + uid/gid. chown is Unix-only
and every step is swallowed on failure so it never fails the rename itself.

chmod/chown are monkeypatched so these run identically on Windows and Linux.
"""

from __future__ import annotations

import os
from pathlib import Path

from kira.renamer.operations import FileOp, _apply_permissions, execute_op


def test_apply_permissions_file_mode_and_chown(tmp_path: Path, monkeypatch) -> None:
    chmods, chowns = [], []
    monkeypatch.setattr(os, "chmod", lambda p, m: chmods.append((str(p), m)))
    monkeypatch.setattr(os, "chown", lambda p, u, g: chowns.append((str(p), u, g)), raising=False)
    f = tmp_path / "f.mkv"
    f.write_text("x")
    _apply_permissions(f, {"file_mode": "644", "dir_mode": "755", "uid": 1000, "gid": 1000}, is_dir=False)
    assert chmods == [(str(f), 0o644)]      # FILE → file_mode, parsed as octal
    assert chowns == [(str(f), 1000, 1000)]


def test_apply_permissions_dir_uses_dir_mode(tmp_path: Path, monkeypatch) -> None:
    chmods: list[int] = []
    monkeypatch.setattr(os, "chmod", lambda p, m: chmods.append(m))
    d = tmp_path / "d"
    d.mkdir()
    _apply_permissions(d, {"file_mode": "644", "dir_mode": "755"}, is_dir=True)
    assert chmods == [0o755]                 # DIR → dir_mode


def test_apply_permissions_partial_uid_only(tmp_path: Path, monkeypatch) -> None:
    chmods, chowns = [], []
    monkeypatch.setattr(os, "chmod", lambda p, m: chmods.append(m))
    monkeypatch.setattr(os, "chown", lambda p, u, g: chowns.append((u, g)), raising=False)
    f = tmp_path / "f.mkv"
    f.write_text("x")
    _apply_permissions(f, {"file_mode": None, "uid": 1000, "gid": None}, is_dir=False)
    assert chmods == []                      # no mode → no chmod
    assert chowns == [(1000, -1)]            # gid -1 = "leave unchanged"


def test_apply_permissions_swallows_errors(tmp_path: Path, monkeypatch) -> None:
    def _boom(*a, **k):
        raise OSError("permission denied")
    monkeypatch.setattr(os, "chmod", _boom)
    monkeypatch.setattr(os, "chown", _boom, raising=False)
    _apply_permissions(tmp_path, {"file_mode": "644", "uid": 0}, is_dir=False)  # must NOT raise


def test_apply_permissions_bad_octal_is_skipped(tmp_path: Path, monkeypatch) -> None:
    chmods: list[int] = []
    monkeypatch.setattr(os, "chmod", lambda p, m: chmods.append(m))
    f = tmp_path / "f.mkv"
    f.write_text("x")
    _apply_permissions(f, {"file_mode": "not-octal"}, is_dir=False)
    assert chmods == []                      # int("not-octal", 8) → ValueError → skipped


def test_execute_op_threads_permissions(tmp_path: Path, monkeypatch) -> None:
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr("kira.renamer.operations._apply_permissions",
                        lambda p, perms, *, is_dir: seen.append((str(p), is_dir)))
    src = tmp_path / "src" / "m.mkv"
    src.parent.mkdir(parents=True)
    src.write_text("x")
    dst = tmp_path / "lib" / "Season 01" / "M.mkv"   # forces dir creation
    execute_op(FileOp.MOVE, src, dst, permissions={"file_mode": "644"})
    assert (str(dst), False) in seen                 # the file got perms
    assert any(is_dir for _, is_dir in seen)         # created dir(s) got perms


def test_execute_op_no_permissions_is_noop(tmp_path: Path, monkeypatch) -> None:
    seen: list[int] = []
    monkeypatch.setattr("kira.renamer.operations._apply_permissions",
                        lambda *a, **k: seen.append(1))
    src = tmp_path / "src" / "m.mkv"
    src.parent.mkdir(parents=True)
    src.write_text("x")
    dst = tmp_path / "lib" / "M.mkv"
    dst.parent.mkdir(parents=True)
    execute_op(FileOp.MOVE, src, dst)                # permissions=None
    assert seen == []                                # never touched perms
