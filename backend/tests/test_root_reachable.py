"""A configured scan root that's gone/unmounted must read as unreachable, so the
scan worker marks it completed_partial instead of a silent 'completed' (audit:
dead-NAS-root status)."""
from __future__ import annotations

from kira.api.scans import _root_reachable


def test_reachable_for_real_dir(tmp_path):
    assert _root_reachable(tmp_path) is True
    assert _root_reachable(str(tmp_path)) is True


def test_unreachable_for_missing_root(tmp_path):
    missing = tmp_path / "unmounted_nas"
    assert _root_reachable(missing) is False          # gone / never mounted
    assert _root_reachable(str(missing)) is False


def test_unreachable_for_file_not_dir(tmp_path):
    f = tmp_path / "x.mkv"
    f.write_bytes(b"x")
    assert _root_reachable(f) is False                # a file isn't a browsable root
