"""Filesystem-persisted match identity — the reference renamer's xattr trick, reimplemented.

the reference renamer stamps every file it processes with extended-attribute metadata
(`net.filebot.*`) so a later re-scan re-identifies the file instantly from its
stored IDs, even if the filename was later mangled. Kira does the same: after a
successful rename we stamp the destination with the provider IDs we resolved;
on the next scan we read them back into `ParsedFile.provider_ids`, where the
existing Phase 14 embedded-ID bypass resolves them with zero ambiguity and zero
network search.

Cross-platform + graceful, in priority order:
  1. POSIX xattrs (`os.setxattr`/`os.getxattr`, key ``user.kira.ids``) — Linux,
     the Docker deployment target, and most NAS mounts. This is the primary path.
  2. NTFS Alternate Data Streams (``<path>:kira.ids``) — Windows dev machines.
  3. No-op — FAT/exFAT/some SMB shares support neither. Writes silently do
     nothing; reads return None. Persistence is a pure optimisation, never a
     correctness dependency, so "unsupported filesystem" degrades to today's
     behaviour (resolve by filename every time).

The payload is a tiny JSON object: ``{"tmdb": "27205", "tvdb": "81797", ...}``
— the SAME shape as `ParsedFile.provider_ids`, so the read path drops straight
in. Never raises to the caller.
"""
from __future__ import annotations

import json
import os
import sys

# POSIX xattr key. The `user.` namespace is the only one writable without
# CAP_SYS_ADMIN on Linux; macOS ignores the namespace prefix but accepts it.
_XATTR_KEY = "user.kira.ids"
# NTFS Alternate Data Stream name appended to the path as `<path>:kira.ids`.
_ADS_SUFFIX = ":kira.ids"

_HAS_XATTR = hasattr(os, "setxattr") and hasattr(os, "getxattr")
_IS_WINDOWS = sys.platform == "win32"

# Only persist IDs the matcher can actually resolve by (Phase 14's
# `_match_by_embedded_id` handles tmdb/tvdb/anidb directly; imdb is recorded
# for completeness but needs a /find call, so it's carried but not relied on).
_PERSISTABLE = ("tmdb", "tvdb", "anidb", "imdb")


def _normalize_ids(ids: dict[str, str] | None) -> dict[str, str]:
    """Keep only the known provider keys with truthy string values."""
    if not ids:
        return {}
    out: dict[str, str] = {}
    for k in _PERSISTABLE:
        v = ids.get(k)
        if v:
            out[k] = str(v)
    return out


def write_ids(path: str, ids: dict[str, str] | None) -> bool:
    """Stamp provider IDs onto a file. Returns True if anything was written.

    Never raises — an unsupported filesystem, a read-only mount, or a missing
    file all degrade to a silent no-op (returns False)."""
    payload = _normalize_ids(ids)
    if not payload:
        return False
    blob = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    if _HAS_XATTR:
        try:
            os.setxattr(path, _XATTR_KEY, blob)  # type: ignore[attr-defined]
            return True
        except (OSError, ValueError):
            # ENOTSUP (filesystem can't), EACCES (read-only), ENOENT (gone).
            # Fall through to the ADS attempt only on Windows; otherwise give up.
            if not _IS_WINDOWS:
                return False

    if _IS_WINDOWS:
        try:
            with open(path + _ADS_SUFFIX, "wb") as fh:
                fh.write(blob)
            return True
        except OSError:
            return False
    return False


def read_ids(path: str) -> dict[str, str] | None:
    """Read provider IDs previously stamped on a file. None when absent /
    unsupported / unreadable. Never raises."""
    blob: bytes | None = None

    if _HAS_XATTR:
        try:
            blob = os.getxattr(path, _XATTR_KEY)  # type: ignore[attr-defined]
        except (OSError, ValueError):
            blob = None

    if blob is None and _IS_WINDOWS:
        try:
            with open(path + _ADS_SUFFIX, "rb") as fh:
                blob = fh.read()
        except OSError:
            blob = None

    if not blob:
        return None
    try:
        data = json.loads(blob.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return _normalize_ids({str(k): v for k, v in data.items()}) or None


def supported(path: str) -> bool:
    """Best-effort probe: can this path hold our metadata? Used by diagnostics
    / settings to tell the user whether persistence is active. Writes then
    removes a throwaway value, so it has no lasting effect."""
    if _HAS_XATTR:
        try:
            os.setxattr(path, "user.kira.probe", b"1")  # type: ignore[attr-defined]
            os.removexattr(path, "user.kira.probe")     # type: ignore[attr-defined]
            return True
        except (OSError, ValueError):
            pass
    if _IS_WINDOWS:
        try:
            probe = path + ":kira.probe"
            with open(probe, "wb") as fh:
                fh.write(b"1")
            os.remove(probe)
            return True
        except OSError:
            pass
    return False
