"""`_filesystem_reachable` walk-up probe (audit: EE-4 / dead-mount subtree).

The phantom-rename branch trusts exists()==False to mean "file gone". That's
only safe when the filesystem under the file is actually responsive. The probe
now walks up to the deepest existing ancestor and confirms it's listable, so a
dropped nested mount can't masquerade as "file gone".
"""
from __future__ import annotations

from pathlib import Path

from kira.api.rename import _filesystem_reachable


def test_reachable_healthy_dir(tmp_path: Path) -> None:
    f = tmp_path / "a" / "b.mkv"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    assert _filesystem_reachable(f) is True


def test_reachable_when_file_folder_gone_but_tree_alive(tmp_path: Path) -> None:
    # The file's own folder was never created, but the tree above is alive and
    # listable → exists()==False is TRUTHFUL → treat as reachable.
    f = tmp_path / "gone" / "b.mkv"
    assert _filesystem_reachable(f) is True


def test_unreachable_when_nothing_exists(tmp_path: Path, monkeypatch) -> None:
    # Simulate a dead mount: every ancestor reports not-exists. The walk must
    # climb to the volume root, terminate, and report unreachable (never trust
    # the exists()==False answer).
    monkeypatch.setattr(Path, "exists", lambda self: False)
    f = tmp_path / "a" / "b.mkv"
    assert _filesystem_reachable(f) is False


def test_unreachable_when_ancestor_raises(tmp_path: Path, monkeypatch) -> None:
    # A half-dropped mount that raises OSError on access → unreachable.
    def boom(self):
        raise OSError("mount down")
    monkeypatch.setattr(Path, "exists", boom)
    f = tmp_path / "a" / "b.mkv"
    assert _filesystem_reachable(f) is False
