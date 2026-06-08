"""xattr / NTFS-ADS persistence — round-trip + graceful degradation."""

from __future__ import annotations

import os

import pytest

from kira import xattr_store


def _can_persist(tmp_path) -> bool:
    """The test filesystem may not support xattr/ADS (tmpfs, some CI mounts)."""
    f = tmp_path / "probe.bin"
    f.write_bytes(b"x")
    return xattr_store.supported(str(f))


def test_normalize_keeps_known_keys_only() -> None:
    out = xattr_store._normalize_ids(
        {"tmdb": "27205", "tvdb": "81797", "junk": "x", "anidb": "", "imdb": "tt1"}
    )
    assert out == {"tmdb": "27205", "tvdb": "81797", "imdb": "tt1"}


def test_normalize_none_and_empty() -> None:
    assert xattr_store._normalize_ids(None) == {}
    assert xattr_store._normalize_ids({}) == {}


def test_write_empty_ids_is_noop(tmp_path) -> None:
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"data")
    assert xattr_store.write_ids(str(f), None) is False
    assert xattr_store.write_ids(str(f), {}) is False
    assert xattr_store.read_ids(str(f)) is None


def test_round_trip(tmp_path) -> None:
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"data")
    if not _can_persist(tmp_path):
        pytest.skip("filesystem does not support xattr / ADS")
    assert xattr_store.write_ids(str(f), {"tmdb": "27205"}) is True
    assert xattr_store.read_ids(str(f)) == {"tmdb": "27205"}


def test_round_trip_multiple_ids(tmp_path) -> None:
    f = tmp_path / "show.s01e01.mkv"
    f.write_bytes(b"data")
    if not _can_persist(tmp_path):
        pytest.skip("filesystem does not support xattr / ADS")
    ids = {"tvdb": "81797", "anidb": "9541"}
    assert xattr_store.write_ids(str(f), ids) is True
    assert xattr_store.read_ids(str(f)) == ids


def test_read_missing_file_is_none() -> None:
    assert xattr_store.read_ids("/nonexistent/path/to/file.mkv") is None


def test_write_missing_file_is_false() -> None:
    # Never raises, even on a path that can't be written.
    assert xattr_store.write_ids("/nonexistent/dir/file.mkv", {"tmdb": "1"}) is False


def test_unstamped_file_reads_none(tmp_path) -> None:
    f = tmp_path / "fresh.mkv"
    f.write_bytes(b"data")
    # A file we never stamped has no IDs to read.
    assert xattr_store.read_ids(str(f)) is None


# ── _apply_xattr_ids (match-time read; NOT in the discovery walk) ─────────

async def test_apply_xattr_ids_fills_when_stamped(monkeypatch) -> None:
    from kira.api import scans
    from kira.parser import ParsedFile

    monkeypatch.setattr(scans._xattr_store, "read_ids", lambda p: {"tmdb": "27205"})
    parsed = ParsedFile(original_filename="x.mkv", media_type="movie", title="X")
    await scans._apply_xattr_ids(parsed, "/lib/x.mkv")
    assert parsed.provider_ids == {"tmdb": "27205"}


async def test_apply_xattr_ids_skips_when_already_set(monkeypatch) -> None:
    from kira.api import scans
    from kira.parser import ParsedFile

    called = {"n": 0}
    def _read(p):
        called["n"] += 1
        return {"tmdb": "999"}
    monkeypatch.setattr(scans._xattr_store, "read_ids", _read)
    parsed = ParsedFile(original_filename="x.mkv", media_type="movie", title="X",
                        provider_ids={"tvdb": "111"})
    await scans._apply_xattr_ids(parsed, "/lib/x.mkv")
    assert parsed.provider_ids == {"tvdb": "111"}  # untouched
    assert called["n"] == 0                         # never even read


async def test_apply_xattr_ids_no_path_is_noop(monkeypatch) -> None:
    from kira.api import scans
    from kira.parser import ParsedFile
    parsed = ParsedFile(original_filename="x.mkv", media_type="movie", title="X")
    await scans._apply_xattr_ids(parsed, None)
    assert parsed.provider_ids is None


async def test_apply_xattr_ids_read_error_is_isolated(monkeypatch) -> None:
    from kira.api import scans
    from kira.parser import ParsedFile

    def _boom(p):
        raise OSError("nas down")
    monkeypatch.setattr(scans._xattr_store, "read_ids", _boom)
    parsed = ParsedFile(original_filename="x.mkv", media_type="movie", title="X")
    await scans._apply_xattr_ids(parsed, "/lib/x.mkv")  # must not raise
    assert parsed.provider_ids is None
