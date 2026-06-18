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


# ── Tier 3: portable index fallback (NAS shares with no xattr / ADS) ──────


@pytest.fixture
def _no_native(monkeypatch, tmp_path):
    """Simulate a volume that supports neither xattr nor ADS (the Samba-NAS
    case that silently no-op'ed every stamp), and point the index at a temp
    dir so tests never touch the real one."""
    monkeypatch.setattr(xattr_store, "_HAS_XATTR", False)
    monkeypatch.setattr(xattr_store, "_IS_WINDOWS", False)
    monkeypatch.setattr(xattr_store, "_index_path", lambda: str(tmp_path / "kira-id-index.json"))
    monkeypatch.setattr(xattr_store, "_index_cache", None)
    return tmp_path


def test_index_fallback_round_trip(_no_native, tmp_path) -> None:
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"data")
    assert xattr_store.write_ids(str(f), {"anidb": "13759"}) is True
    assert xattr_store.read_ids(str(f)) == {"anidb": "13759"}
    # The stamp went to the portable index, not the file.
    assert (tmp_path / "kira-id-index.json").exists()


def test_index_fallback_survives_cache_invalidation(_no_native, tmp_path) -> None:
    a = tmp_path / "a.mkv"; a.write_bytes(b"a")
    b = tmp_path / "b.mkv"; b.write_bytes(b"b")
    assert xattr_store.write_ids(str(a), {"tmdb": "1"}) is True
    assert xattr_store.write_ids(str(b), {"tvdb": "2"}) is True
    # Second write must not clobber the first (read-modify-replace).
    assert xattr_store.read_ids(str(a)) == {"tmdb": "1"}
    assert xattr_store.read_ids(str(b)) == {"tvdb": "2"}


def test_index_fallback_missing_file_still_false(_no_native, tmp_path) -> None:
    # The index refuses to stamp a file that doesn't exist — same contract
    # the native tiers enforce for free.
    assert xattr_store.write_ids(str(tmp_path / "ghost.mkv"), {"tmdb": "1"}) is False


def test_index_key_is_case_and_separator_insensitive(_no_native, tmp_path) -> None:
    f = tmp_path / "Show.mkv"
    f.write_bytes(b"x")
    assert xattr_store.write_ids(str(f), {"tvdb": "81797"}) is True
    # Windows: same path with different case/separators must hit the same key.
    alt = str(f).replace("\\", "/")
    assert xattr_store.read_ids(alt) == {"tvdb": "81797"}


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
