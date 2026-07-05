"""Subtitle reuse-cache — keep a removed `.srt` around for a while so a later
re-rename can REUSE it instead of re-downloading (burning OpenSubtitles quota).

The problem this solves: undoing a rename used to HARD-DELETE the downloaded
sidecar, and the next re-rename re-fetched the exact same subtitle from the
network. Now the undo path (wired elsewhere — this module only exposes the
function) MOVES the sidecar into a managed cache dir keyed by the video's
content; the fetch path checks the cache BEFORE hitting any provider.

Cache layout — a sibling `.kira-subcache/` dir under the (first) library root,
exactly like the rename trash dir (`api/cleanup._resolve_trash_root`), so it
lives INSIDE a root Kira already manages (never strays onto an arbitrary disk):

    <library root>/.kira-subcache/
        <key>.srt          the cached subtitle bytes
        <key>.json         tiny sidecar: original path, language, cached_at (ISO)

The KEY is rename-stable: it's the OpenSubtitles OSDb content hash of the video
bytes (`providers._osdbhash.compute_osdb_hash`) + the language — so the SAME
file finds its cached sub again even after its name/folder changed. When the
file can't be hashed (too small / unreadable / already gone) we fall back to a
normalized basename-stem + language, which still survives a pure move.

RETENTION — `subtitles.cache_retention_days` (default 30; 0 = keep forever),
read the same way the other int settings are (peel the `{"value": …}` wrapper).
`sweep_expired()` drops entries older than that and is meant to ride along with
the per-scan history prune.

Everything is best-effort: a failure to cache returns None (the caller falls
back to deleting), and a failure to read the cache returns None (the caller
just fetches as before). All filesystem work runs off the event loop via
`asyncio.to_thread`, like the rest of the codebase.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from kira.providers._osdbhash import compute_osdb_hash

_log = logging.getLogger("kira.subtitles.subcache")

_CACHE_DIRNAME = ".kira-subcache"
_DEFAULT_RETENTION_DAYS = 30
_SETTING_RETENTION = "subtitles.cache_retention_days"

# A cached subtitle is always normalized to `.srt` on the way in (that's what
# the sidecar save path writes); the JSON sidecar carries the provenance.
_SUB_EXT = "srt"
_META_EXT = "json"


# ── key ──────────────────────────────────────────────────────────────────────
def _norm_stem(video_path: str) -> str:
    """Filesystem-safe, lowercased basename stem — the rename-surviving fallback
    key when the file can't be content-hashed. Pure."""
    stem = Path(video_path).stem.lower()
    # Collapse to a safe token set so the key is a valid filename on any OS.
    return re.sub(r"[^a-z0-9._-]+", "_", stem).strip("_") or "unknown"


def cache_key(video_path: str, language: str) -> str:
    """A stable cache key for (video, language). PREFERS the OpenSubtitles OSDb
    content hash of the video bytes so the key survives a rename/move; falls
    back to a normalized basename-stem when the file can't be hashed (too small,
    unreadable, or already moved away). The language is always part of the key
    so two languages of the same video never collide.

    Synchronous + cheap (two 64 KiB reads at most); callers wrap it in
    `to_thread` along with the surrounding file I/O."""
    lang = (language or "").strip().lower() or "und"
    digest = compute_osdb_hash(video_path)
    if digest:
        return f"h_{digest}.{lang}"
    return f"n_{_norm_stem(video_path)}.{lang}"


# ── root + retention resolution (session-free) ────────────────────────────────
async def _cache_root() -> Path | None:
    """The managed `.kira-subcache` dir under the FIRST library root, or None
    when no root is configured. Mirrors `api/cleanup._resolve_trash_root`'s
    "sibling dir under the managed root" approach, but resolves the root through
    a short-lived session so the public functions stay session-free (the fetch
    path that calls `find_cached_subtitle` has no DB handle of its own).

    Priority matches `api/files._managed_roots`: single library_root, then the
    first watch folder, then the first named library root."""
    from kira.database import SessionLocal
    from kira.models import Setting
    from kira.settings_store import unwrap_str

    def _first_named(val) -> str | None:
        if isinstance(val, dict):
            for v in val.values():
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return None

    def _first_watch(val) -> str | None:
        if isinstance(val, list):
            for v in val:
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return None

    try:
        async with SessionLocal() as session:
            single = await session.get(Setting, "paths.library_root")
            root = unwrap_str(single.value) if single is not None else None
            if not root:
                watch = await session.get(Setting, "paths.watch_folders")
                root = _first_watch(watch.value) if watch is not None else None
            if not root:
                named = await session.get(Setting, "paths.library_roots")
                root = _first_named(named.value) if named is not None else None
    except Exception as e:  # DB unavailable / migration race — just skip caching.
        _log.debug("subcache: could not resolve library root: %r", e)
        return None
    if not root:
        return None
    return Path(root) / _CACHE_DIRNAME


async def _retention_days() -> int:
    """`subtitles.cache_retention_days` (default 30; 0 = keep forever), read the
    same wrapped-or-bare way the other int settings are. Negative is clamped to
    0 (keep forever) rather than "expire everything"."""
    from kira.database import SessionLocal
    from kira.models import Setting
    from kira.settings_store import unwrap

    try:
        async with SessionLocal() as session:
            row = await session.get(Setting, _SETTING_RETENTION)
            raw = unwrap(row.value) if row is not None else None
    except Exception:
        return _DEFAULT_RETENTION_DAYS
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return _DEFAULT_RETENTION_DAYS
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_RETENTION_DAYS
    return max(0, days)


def _is_expired(meta_path: Path, retention_days: int, now: float) -> bool:
    """Whether a cache entry is past its retention window. 0 days = never
    expires. Uses the entry's mtime (cheap + monotonic enough for a TTL);
    `cached_at` in the JSON is for humans/debugging, not the gate."""
    if retention_days <= 0:
        return False
    try:
        mtime = meta_path.stat().st_mtime
    except OSError:
        return False
    return mtime < (now - retention_days * 86400)


# ── public API ────────────────────────────────────────────────────────────────
async def cache_subtitle(srt_path: str, *, video_path: str, language: str) -> str | None:
    """MOVE the subtitle at `srt_path` into the cache (keyed by the video's
    content hash + language) and write a tiny JSON sidecar recording the
    original path, language, and `cached_at`. Returns the cached path, or None
    on ANY failure so the caller can fall back to a plain delete.

    Called by the undo/teardown path INSTEAD of deleting the sidecar, so a later
    re-rename can reuse it. Off-loop via `to_thread`."""
    root = await _cache_root()
    if root is None:
        return None
    # The cache stores everything under a `.srt` key and reuse replays the
    # bytes as an .srt sidecar with a flat score-100 — an `.ass`/`.vtt` payload
    # cached here would masquerade as srt forever (and be exempt from
    # upgrades). Only genuine .srt files enter the reuse cache.
    if not str(srt_path).lower().endswith(".srt"):
        return None
    key = await asyncio.to_thread(cache_key, video_path, language)
    lang = (language or "").strip().lower() or "und"

    def _store() -> str | None:
        try:
            if not os.path.isfile(srt_path):
                return None
            root.mkdir(parents=True, exist_ok=True)
            dest = root / f"{key}.{_SUB_EXT}"
            meta = root / f"{key}.{_META_EXT}"
            # Never follow a symlink planted at the cache path.
            for p in (dest, meta):
                if p.is_symlink():
                    p.unlink()
            # os.replace is atomic on the same filesystem and overwrites an older
            # cached copy; fall back to copy+unlink across devices.
            try:
                os.replace(srt_path, dest)
            except OSError:
                import shutil
                shutil.copy2(srt_path, dest)
                try:
                    os.remove(srt_path)
                except OSError:
                    pass
            meta.write_text(
                json.dumps({
                    "original_path": str(video_path),
                    "language": lang,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                }),
                encoding="utf-8",
            )
            return str(dest)
        except Exception as e:
            _log.warning("subcache: failed to cache %s (%s): %r", srt_path, lang, e)
            return None

    return await asyncio.to_thread(_store)


async def find_cached_subtitle(video_path: str, language: str) -> str | None:
    """A cached `.srt` for this (video, language) if present and not expired,
    else None. The lookup uses the SAME key scheme as `cache_subtitle`, so it
    finds the entry even after the file was renamed/moved (content hash). Pure
    read; best-effort. Off-loop via `to_thread`."""
    root = await _cache_root()
    if root is None:
        return None
    retention = await _retention_days()
    key = await asyncio.to_thread(cache_key, video_path, language)

    def _lookup() -> str | None:
        dest = root / f"{key}.{_SUB_EXT}"
        meta = root / f"{key}.{_META_EXT}"
        try:
            if not dest.is_file():
                return None
            # Honor retention: a hit older than the window is treated as a miss
            # (and will be reaped by the next sweep).
            if _is_expired(meta if meta.exists() else dest, retention, time.time()):
                return None
            return str(dest)
        except OSError:
            return None

    return await asyncio.to_thread(_lookup)


async def sweep_expired() -> int:
    """Delete cache entries older than `subtitles.cache_retention_days`; return
    the number of subtitle entries removed. 0-retention (keep forever) is a
    no-op. Best-effort — meant to ride along with the per-scan history prune.
    Off-loop via `to_thread`."""
    root = await _cache_root()
    if root is None:
        return 0
    retention = await _retention_days()
    if retention <= 0:
        return 0

    def _sweep() -> int:
        if not root.exists():
            return 0
        now = time.time()
        removed = 0
        try:
            entries = list(root.glob(f"*.{_SUB_EXT}"))
        except OSError:
            return 0
        for sub in entries:
            meta = sub.with_suffix(f".{_META_EXT}")
            # Gate on the subtitle's own mtime (the sidecar may be absent for a
            # legacy/partial entry); drop both files together.
            if not _is_expired(sub, retention, now):
                continue
            try:
                sub.unlink()
                removed += 1
            except OSError:
                continue
            try:
                if meta.exists():
                    meta.unlink()
            except OSError:
                pass
        return removed

    return await asyncio.to_thread(_sweep)
