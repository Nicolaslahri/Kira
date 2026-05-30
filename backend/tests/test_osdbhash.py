"""M5 — OSDb content hash (pure + file)."""

from __future__ import annotations

import struct

import pytest

from kira.providers._osdbhash import _CHUNK, compute_osdb_hash, osdb_hash_from_parts


def test_zero_chunks_hash_equals_filesize() -> None:
    # All-zero head/tail contribute 0 → hash is just the filesize, hex-padded.
    size = 200000
    head = bytes(_CHUNK)
    tail = bytes(_CHUNK)
    assert osdb_hash_from_parts(size, head, tail) == f"{size:016x}"


def test_head_word_is_summed() -> None:
    size = 200000
    head = struct.pack("<Q", 0x42) + bytes(_CHUNK - 8)
    tail = bytes(_CHUNK)
    # filesize + the single 0x42 word at the start of the head chunk.
    assert osdb_hash_from_parts(size, head, tail) == f"{size + 0x42:016x}"


def test_tail_word_is_summed() -> None:
    size = 200000
    head = bytes(_CHUNK)
    tail = bytes(_CHUNK - 8) + struct.pack("<Q", 0x7)
    assert osdb_hash_from_parts(size, head, tail) == f"{size + 0x7:016x}"


def test_wraps_mod_2_64() -> None:
    # filesize near 2**64 + a word should wrap, not overflow.
    big = (1 << 64) - 1
    head = struct.pack("<Q", 2) + bytes(_CHUNK - 8)
    tail = bytes(_CHUNK)
    # (2**64 - 1) + 2 = 2**64 + 1 ≡ 1 (mod 2**64)
    assert osdb_hash_from_parts(big, head, tail) == f"{1:016x}"


def test_short_chunk_raises() -> None:
    with pytest.raises(ValueError):
        osdb_hash_from_parts(100, bytes(10), bytes(_CHUNK))


def test_output_is_16_hex_chars() -> None:
    h = osdb_hash_from_parts(123456789, bytes(_CHUNK), bytes(_CHUNK))
    assert len(h) == 16
    int(h, 16)  # parses as hex


def test_compute_on_real_file(tmp_path) -> None:
    p = tmp_path / "movie.mkv"
    size = 200000
    p.write_bytes(bytes(size))  # all zeros
    assert compute_osdb_hash(p) == f"{size:016x}"


def test_compute_too_small_returns_none(tmp_path) -> None:
    p = tmp_path / "tiny.mkv"
    p.write_bytes(bytes(_CHUNK))  # only 64 KiB < 2×64 KiB
    assert compute_osdb_hash(p) is None


def test_compute_missing_file_returns_none(tmp_path) -> None:
    assert compute_osdb_hash(tmp_path / "nope.mkv") is None


def test_compute_distinguishes_content(tmp_path) -> None:
    a = tmp_path / "a.mkv"
    b = tmp_path / "b.mkv"
    a.write_bytes(bytes(200000))
    b.write_bytes(struct.pack("<Q", 0x99) + bytes(200000 - 8))
    assert compute_osdb_hash(a) != compute_osdb_hash(b)
