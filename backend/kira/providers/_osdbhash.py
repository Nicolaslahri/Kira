"""OpenSubtitles (OSDb) 64-bit file hash — the filename-independent fingerprint.

This is the keystone of content-hash identification (Matching-completeness M5):
identify a file by its BYTES, not its name — the only thing that works on a
totally-garbage filename. the reference renamer uses the same hash for its OpenSubtitles
lookups.

Algorithm (spec: trac.opensubtitles.org/projects/opensubtitles/wiki/HashSourceCodes):

    hash = (filesize
            + sum(first  64 KiB as little-endian uint64s)
            + sum(last   64 KiB as little-endian uint64s)) mod 2**64

rendered as a 16-char lowercase hex string. Files smaller than 2×64 KiB can't be
hashed (there aren't two distinct chunks) — we return None for those.

Pure + dependency-free. `osdb_hash_from_parts` is fully pure (no I/O) so it can
be unit-tested with deterministic byte vectors; `compute_osdb_hash` is the thin
file-reading wrapper.
"""
from __future__ import annotations

import os
import struct

_CHUNK = 65536  # 64 KiB
_U64 = 0xFFFFFFFFFFFFFFFF


def osdb_hash_from_parts(filesize: int, head: bytes, tail: bytes) -> str:
    """Compute the OSDb hash from the size + the first/last 64 KiB.

    `head` and `tail` must each be at least `_CHUNK` (65536) bytes; only the
    first 64 KiB of `head` and the last 64 KiB of `tail` are used (so passing a
    larger buffer is fine). Raises ValueError if a chunk is too short.
    """
    if len(head) < _CHUNK or len(tail) < _CHUNK:
        raise ValueError("head and tail must each be at least 65536 bytes")
    h = filesize & _U64
    for chunk in (head[:_CHUNK], tail[-_CHUNK:]):
        # 65536 / 8 = 8192 unsigned little-endian 64-bit words per chunk.
        for off in range(0, _CHUNK, 8):
            (word,) = struct.unpack_from("<Q", chunk, off)
            h = (h + word) & _U64
    return f"{h:016x}"


def compute_osdb_hash(path: str | os.PathLike) -> str | None:
    """OSDb hash of a real file, or None if it's too small / unreadable.

    No network, no native libraries — just two 64 KiB reads. Safe to call on any
    path; returns None rather than raising so callers can treat "can't hash" as
    "fall back to name matching".
    """
    try:
        size = os.path.getsize(path)
        if size < 2 * _CHUNK:
            return None  # too small for a meaningful two-chunk hash
        with open(path, "rb") as f:
            head = f.read(_CHUNK)
            f.seek(size - _CHUNK)
            tail = f.read(_CHUNK)
        if len(head) < _CHUNK or len(tail) < _CHUNK:
            return None  # truncated read (e.g. file shrank under us)
        return osdb_hash_from_parts(size, head, tail)
    except OSError:
        return None
