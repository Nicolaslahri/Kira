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
  3. Portable JSON index (``kira-id-index.json`` beside the database) — the
     real-world fallback for SMB shares that support neither (a Samba NAS
     without `streams_xattr` rejects ADS, which silently no-op'ed every stamp
     and made persistence dead on exactly the setups that need it most).
     Keyed by normalised absolute path; survives a database reset because the
     reset wipes tables, not files. Unlike a true xattr it does NOT travel
     with a file the user moves by hand — but it fully covers the Kira-rename
     → DB-reset → rescan round-trip, which is the case that actually bites.

Persistence is a pure optimisation, never a correctness dependency — every
tier failing degrades to resolving by filename.

The payload is a tiny JSON object: ``{"tmdb": "27205", "tvdb": "81797", ...}``
— the SAME shape as `ParsedFile.provider_ids`, so the read path drops straight
in. Never raises to the caller.
"""
from __future__ import annotations

import json
import os
import sys
import threading

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


# ── Tier 3: portable JSON index ──────────────────────────────────────
# One flat file beside Kira's database (the backend CWD — same resolution as
# the `sqlite:///./kira.db` default), NOT on the media volume. Guarded by a
# lock because index writes do read-modify-replace; the mtime-keyed cache
# makes per-file reads during a scan free after the first load.
_INDEX_FILENAME = "kira-id-index.json"
_index_lock = threading.Lock()
_index_cache: tuple[float, dict] | None = None  # (file mtime, parsed dict)


def _index_path() -> str:
    return os.path.join(os.getcwd(), _INDEX_FILENAME)


def _index_key(path: str) -> str:
    # normcase folds case + separators on Windows so `Z:\x` and `z:/x` agree;
    # a no-op on POSIX.
    return os.path.normcase(os.path.abspath(path))


def _index_load() -> dict:
    global _index_cache
    p = _index_path()
    try:
        mtime = os.stat(p).st_mtime
    except OSError:
        return {}
    if _index_cache is not None and _index_cache[0] == mtime:
        return _index_cache[1]
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    _index_cache = (mtime, data)
    return data


def _index_write(path: str, payload: dict) -> bool:
    global _index_cache
    with _index_lock:
        data = dict(_index_load())
        data[_index_key(path)] = payload
        tmp = _index_path() + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, separators=(",", ":"))
            os.replace(tmp, _index_path())
        except OSError:
            return False
        _index_cache = None  # next read re-stats; cheap
        return True


def _index_read(path: str) -> dict | None:
    v = _index_load().get(_index_key(path))
    return v if isinstance(v, dict) else None


def write_ids(path: str, ids: dict[str, str] | None) -> bool:
    """Stamp provider IDs onto a file. Returns True if anything was written.

    Never raises — an unsupported filesystem or read-only mount falls through
    to the portable index; only a missing payload or a failed index write
    returns False."""
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
            # Fall through: ADS on Windows, then the portable index.
            pass

    if _IS_WINDOWS:
        try:
            with open(path + _ADS_SUFFIX, "wb") as fh:
                fh.write(blob)
            return True
        except OSError:
            pass

    # Tier 3 — the volume itself can't hold metadata; remember it Kira-side.
    # Existence check preserves the contract the native tiers enforce for
    # free (you can't stamp a file that isn't there) and keeps caller bugs
    # from seeding ghost entries.
    if not os.path.exists(path):
        return False
    return _index_write(path, payload)


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

    data: dict | None = None
    if blob:
        try:
            parsed = json.loads(blob.decode("utf-8"))
            if isinstance(parsed, dict):
                data = parsed
        except (ValueError, UnicodeDecodeError):
            data = None
    if data is None:
        data = _index_read(path)
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
