"""Walk reliability — dot-prefixed real titles + Windows long-path descent.

Two audit findings (blueprint §9.54 → §9.71):
  • `.hack//SIGN` and friends lead with a dot, so the "hidden dir" skip silently
    dropped the whole franchise. The walk now carves them out.
  • On the Windows .exe, deep anime/light-novel trees blow past MAX_PATH (260) and
    os.walk's internal scandir failed → files silently lost. The walk now feeds
    os.walk an extended-length root and strips the prefix back off.
"""

from __future__ import annotations

import os
import shutil

import pytest

from kira import scanner


def test_walk_keeps_dot_hack_but_skips_hidden_dirs(tmp_path) -> None:
    # A real `.hack//` title — its on-disk folder leads with a dot.
    hack = tmp_path / ".hack__SIGN"
    hack.mkdir()
    (hack / "ep01.mkv").write_bytes(b"\x00" * 16)
    # A genuinely-hidden dir must STILL be skipped.
    hidden = tmp_path / ".AppleDouble"
    hidden.mkdir()
    (hidden / "junk.mkv").write_bytes(b"\x00" * 16)

    found = {p.name for p in scanner.walk(tmp_path)}
    assert "ep01.mkv" in found        # .hack content kept
    assert "junk.mkv" not in found    # .AppleDouble still skipped


def test_walk_onerror_and_record_feed_one_list() -> None:
    # The manual recorder (symlink descent) and os.walk's own onerror callback
    # share the single thread-local that get_walk_errors() reads.
    scanner.reset_walk_errors()
    assert scanner.get_walk_errors() == []
    scanner._record_walk_error("/mnt/x/deep")
    err = OSError("boom")
    err.filename = "/mnt/y/dead"
    scanner._walk_onerror(err)
    errs = scanner.get_walk_errors()
    assert "/mnt/x/deep" in errs and "/mnt/y/dead" in errs
    scanner.reset_walk_errors()
    assert scanner.get_walk_errors() == []


def test_walk_records_unreadable_symlink_target(tmp_path, monkeypatch) -> None:
    """A symlink/junction scandir classified as a dir but whose stat() then fails
    (target went unreachable) must be RECORDED, not silently dropped — otherwise the
    subtree vanishes and the scan still reads 'completed'."""
    scanner.reset_walk_errors()
    sub = tmp_path / "linkdir"
    sub.mkdir()
    (sub / "behind_link.mkv").write_bytes(b"\x00" * 16)
    (tmp_path / "real.mkv").write_bytes(b"\x00" * 16)

    # Force the symlink-descent branch for `linkdir`, then make its stat() raise.
    monkeypatch.setattr(scanner, "_is_reparse_or_symlink", lambda p: p.name == "linkdir")
    real_stat = scanner.Path.stat

    def flaky_stat(self, *a, **k):
        if self.name == "linkdir":
            raise OSError("target unreachable")
        return real_stat(self, *a, **k)

    monkeypatch.setattr(scanner.Path, "stat", flaky_stat)

    found = {p.name for p in scanner.walk(tmp_path)}
    assert "real.mkv" in found                # the reachable file still ingests
    assert "behind_link.mkv" not in found     # the unreachable subtree is skipped
    assert any("linkdir" in e for e in scanner.get_walk_errors())  # …but RECORDED


def test_strip_ext_prefix_inverts_force() -> None:
    # Pure-helper round-trip (runs everywhere; the prefix is only *applied* on nt).
    assert scanner._strip_ext_prefix("\\\\?\\Z:\\Anime\\Show") == "Z:\\Anime\\Show"
    assert scanner._strip_ext_prefix("\\\\?\\UNC\\srv\\share\\x") == "\\\\srv\\share\\x"
    assert scanner._strip_ext_prefix("Z:\\already\\plain") == "Z:\\already\\plain"


def test_walk_feeds_oswalk_an_extended_length_root(tmp_path, monkeypatch) -> None:
    r"""The real proof the fix is applied REGARDLESS of the machine's long-path
    setting: os.walk must receive a `\\?\`-prefixed root. (The end-to-end test
    below can't discriminate on a dev box that already has LongPathsEnabled — bare
    os.walk works there too — but the prefix is what saves the Windows *default*.)"""
    if os.name != "nt":
        pytest.skip("extended-length prefix is Windows-only")
    seen: dict[str, str] = {}
    real_walk = os.walk

    def spy(top, *a, **k):
        seen.setdefault("root", str(top))
        return real_walk(top, *a, **k)

    monkeypatch.setattr(scanner.os, "walk", spy)
    list(scanner.walk(tmp_path))
    assert seen["root"].startswith("\\\\?\\")   # os.walk got the extended-length root


def test_walk_finds_files_under_long_paths(tmp_path) -> None:
    # End-to-end: a genuinely >260-char path is discovered and the prefix doesn't
    # leak into yielded paths. NOTE: on a box with LongPathsEnabled this passes even
    # without the fix — the discriminating check is the monkeypatch test above.
    if os.name != "nt":
        pytest.skip("MAX_PATH 260-char limit is Windows-only")
    # 6 fat components → the tree alone is past 260 even before tmp_path.
    leaf = "longcomponent" + "x" * 30
    deep = tmp_path
    for i in range(6):
        deep = deep / f"{leaf}{i}"
    target = deep / "Show S01E01 1080p.mkv"
    assert len(str(target)) > 260  # sanity: we're genuinely past MAX_PATH

    # Build the deep tree + file via the extended-length prefix so the SETUP
    # itself isn't blocked by MAX_PATH.
    os.makedirs(scanner._ext_prefix(str(deep)), exist_ok=True)
    try:
        with open(scanner._ext_prefix(str(target)), "wb") as fh:
            fh.write(b"\x00" * 16)

        found = list(scanner.walk(tmp_path))
        names = {p.name for p in found}
        assert "Show S01E01 1080p.mkv" in names         # deep file discovered
        # …and the \\?\ prefix never leaked into the yielded paths
        assert all("\\\\?\\" not in str(p) for p in found)
    finally:
        # pytest's own rmtree can't delete a >260 tree without the prefix.
        shutil.rmtree(scanner._ext_prefix(str(tmp_path / f"{leaf}0")), ignore_errors=True)
