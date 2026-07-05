"""Undo identity check: refuse only a PRESENT-but-mismatched id-stamp.

Regression guard for the review's HIGH finding — an ABSENT stamp must NOT block
undo (it's indistinguishable from a legitimately-unstamped file: renamed before
stamping shipped, renamed with stamping off, or a stamp stripped by copy/restore).
Refusing on it blocked real undos on the very filesystems that CAN stamp
(ext4/Docker). Only a stamp that is present AND carries a different id proves the
file was replaced."""
from __future__ import annotations

import pytest

from kira import xattr_store
from kira.api import history


def _files(tmp_path):
    new = tmp_path / "new.mkv"
    new.write_bytes(b"x")
    old = tmp_path / "old.mkv"  # original location free
    return str(new), str(old)


def test_unstamped_file_is_undoable(tmp_path, monkeypatch):
    new, old = _files(tmp_path)
    monkeypatch.setattr(xattr_store, "read_ids", lambda p: {})  # no stamp on disk
    ok, reason = history._verify_row_undoable_sync(new, old, "anidb", "69")
    assert ok and reason == "", f"unstamped-but-legit must be undoable, got {(ok, reason)}"


def test_mismatched_stamp_refuses(tmp_path, monkeypatch):
    new, old = _files(tmp_path)
    monkeypatch.setattr(xattr_store, "read_ids", lambda p: {"anidb": "999"})  # different id
    ok, reason = history._verify_row_undoable_sync(new, old, "anidb", "69")
    assert not ok and reason == "File changed on disk"


def test_matching_stamp_is_undoable(tmp_path, monkeypatch):
    new, old = _files(tmp_path)
    monkeypatch.setattr(xattr_store, "read_ids", lambda p: {"anidb": "69"})
    ok, reason = history._verify_row_undoable_sync(new, old, "anidb", "69")
    assert ok and reason == ""


def test_missing_target_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(xattr_store, "read_ids", lambda p: {})
    ok, reason = history._verify_row_undoable_sync(
        str(tmp_path / "gone.mkv"), str(tmp_path / "old.mkv"), "anidb", "69")
    assert not ok and reason == "Target missing"


def test_lang_from_sub_name_skips_markers():
    assert history._lang_from_sub_name("Show - S01E01.en.forced.srt") == "en"
    assert history._lang_from_sub_name("Show - S01E01.eng.srt") == "eng"
    assert history._lang_from_sub_name("Show.en.sdh.srt") == "en"
    assert history._lang_from_sub_name("Movie.2010.srt") == "und"
    assert history._lang_from_sub_name("Movie.srt") == "und"


# ── operation-gated "Original location occupied" guard ────────────────────────
# The occupied-source guard only applies to MOVE. For COPY/SYMLINK/HARDLINK,
# undo just deletes the destination and never touches old_path, so old_path
# legitimately still holds the source. Applying the guard to those ops rejected
# every copy-mode undo, and every hardlink/symlink undo on a zero-inode CIFS/SMB
# mount (the default op on the Docker deployment).
def test_copy_undo_not_blocked_by_existing_source(tmp_path, monkeypatch):
    monkeypatch.setattr(xattr_store, "read_ids", lambda p: {})
    new = tmp_path / "new.mkv"; new.write_bytes(b"copy")
    old = tmp_path / "old.mkv"; old.write_bytes(b"the original source, still here")
    # MOVE would refuse (occupied); COPY must allow.
    ok_move, reason_move = history._verify_row_undoable_sync(str(new), str(old), None, None, "move")
    assert not ok_move and reason_move == "Original location occupied"
    ok_copy, reason_copy = history._verify_row_undoable_sync(str(new), str(old), None, None, "copy")
    assert ok_copy and reason_copy == ""


def test_hardlink_and_symlink_undo_not_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(xattr_store, "read_ids", lambda p: {})
    new = tmp_path / "new.mkv"; new.write_bytes(b"link")
    old = tmp_path / "old.mkv"; old.write_bytes(b"source the link points at")
    for op in ("hardlink", "symlink"):
        ok, reason = history._verify_row_undoable_sync(str(new), str(old), None, None, op)
        assert ok and reason == "", f"{op} undo must not be blocked by an existing source"


def test_move_undo_still_guards_occupied_source(tmp_path, monkeypatch):
    monkeypatch.setattr(xattr_store, "read_ids", lambda p: {})
    new = tmp_path / "new.mkv"; new.write_bytes(b"renamed")
    old = tmp_path / "old.mkv"; old.write_bytes(b"different file user dropped here")
    ok, reason = history._verify_row_undoable_sync(str(new), str(old), None, None, "move")
    assert not ok and reason == "Original location occupied"


# ── broken-symlink undo (audit m3): verify gate must not block it ─────────────
def test_broken_symlink_is_undoable_for_symlink_op(monkeypatch, tmp_path):
    """A symlink/hardlink undo just unlinks new_path — a BROKEN symlink
    (exists()=False, is_symlink()=True) must still be undoable, not 'Target
    missing'. MOVE/COPY with a truly-missing target still blocks."""
    from kira.api import history
    from kira import xattr_store
    from pathlib import Path
    monkeypatch.setattr(xattr_store, "read_ids", lambda p: {})

    real_exists = Path.exists
    real_is_symlink = Path.is_symlink

    def fake_exists(self):
        return False if "link.mkv" in str(self) else real_exists(self)

    def fake_is_symlink(self):
        return True if "link.mkv" in str(self) else real_is_symlink(self)

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)

    new = str(tmp_path / "link.mkv")   # a "broken symlink"
    old = str(tmp_path / "orig.mkv")
    ok, reason = history._verify_row_undoable_sync(new, old, None, None, "symlink")
    assert ok is True and reason == ""
    # A MOVE with the same missing target still blocks.
    ok2, r2 = history._verify_row_undoable_sync(new, old, None, None, "move")
    assert ok2 is False and r2 == "Target missing"
