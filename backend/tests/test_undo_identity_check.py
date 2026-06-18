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
