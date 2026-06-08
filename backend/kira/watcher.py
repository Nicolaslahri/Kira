"""Watched-folders auto-scan daemon.

Watches the configured library_root + watch_folders and auto-triggers an
incremental scan when files appear — so "drop a release into Downloads and
have it organized" works without a manual Scan click.

Design (deliberately conservative):
- SCAN + MATCH ONLY by default. Reuses the existing incremental scan
  pipeline via `kira.api.scans._start_scan(source="auto")`, which discovers
  + parses + matches only NEW files and surfaces them in Review. The user
  still approves every rename.
- Per-folder automation mode: each watched folder is "scan" (default) or
  "auto_rename". In "auto_rename", `maybe_auto_rename()` (called from the scan-
  completion hook) renames the NEW files whose selected match clears that
  folder's STRICT confidence threshold, reusing the real `rename()` executor
  (so RenameHistory, the media-server refresh, and notifications all fire). The
  default op is hardlink — non-destructive, the original stays put. Sub-threshold
  / unmatched files are left in Review for the user.
- Two triggers, belt-and-braces:
    1. Filesystem events via `watchfiles.awatch` (near-real-time, debounced
       so a batched download settles before we scan).
    2. A periodic poll fallback (cheap dir-signature diff) for NAS / SMB /
       NFS mounts where inotify-style events are unreliable.
- Opt-in: does nothing unless `watch.config.auto_scan` is true.
- Restart-resilient + reconfigurable: re-armed from settings on boot and
  whenever the relevant settings change (`reconfigure()`).

Import discipline: only stdlib + `kira.database` at module top. Everything
under `kira.api.*` is imported lazily inside methods to avoid import cycles
(scans.py / settings.py import this singleton lazily in turn).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from kira.database import SessionLocal
from kira.models import Setting
from kira.settings_store import unwrap_str as _coerce_str  # canonical settings-value unwrap

_log = logging.getLogger("kira.watcher")

# Settings keys (shared with the frontend / settings API).
KEY_WATCH = "watch.config"
KEY_LIBRARY_ROOT = "paths.library_root"
KEY_WATCH_FOLDERS = "paths.watch_folders"

# Per-folder + global defaults.
DEFAULT_FOLDER_MODE = "scan"          # "scan" | "auto_rename"
DEFAULT_FOLDER_THRESHOLD = 0.9
VALID_FOLDER_MODES = ("scan", "auto_rename")

DEFAULT_WATCH_CONFIG: dict[str, Any] = {
    "auto_scan": False,
    "debounce_seconds": 30,
    "poll_interval_seconds": 900,
    "folders": {},  # path -> {"mode": ..., "threshold": ...}
}

# File suffixes that signal an in-progress download / temp artifact — a
# change to one of these should NOT wake the scanner; we wait for the final
# rename to the real extension.
_IGNORE_SUFFIXES = (
    ".part", ".crdownload", ".!qb", ".!ut",
    ".downloading", ".partial", ".tmp", ".temp", ".filepart",
)

# Path fragments for trash / recycle areas we never scan.
_IGNORE_FRAGMENTS = (
    "/.trash", "\\.trash",
    "/$recycle.bin", "\\$recycle.bin",
    "/.recycle", "\\.recycle",
)

# Cap on files counted while computing the poll signature, so a pathological
# tree can't make the poll loop expensive.
_POLL_SIGNATURE_FILE_CAP = 50_000


def _is_ignored_path(path: str) -> bool:
    """True if a filesystem change at `path` should NOT trigger a scan.

    Pure (no I/O) so it's unit-testable. Ignores partial-download temp files
    and trash/recycle locations; everything else (real media + sidecars) is
    scan-worthy.
    """
    if not path:
        return True
    p = path.lower()
    if p.endswith(_IGNORE_SUFFIXES):
        return True
    return any(frag in p for frag in _IGNORE_FRAGMENTS)


def merge_watch_config(stored: Any) -> dict[str, Any]:
    """Merge a stored watch.config value over the defaults (defensive)."""
    cfg = dict(DEFAULT_WATCH_CONFIG)
    cfg["folders"] = {}
    if isinstance(stored, dict):
        if isinstance(stored.get("auto_scan"), bool):
            cfg["auto_scan"] = stored["auto_scan"]
        try:
            cfg["debounce_seconds"] = max(5, int(stored.get("debounce_seconds", 30)))
        except (TypeError, ValueError):
            pass
        try:
            cfg["poll_interval_seconds"] = max(60, int(stored.get("poll_interval_seconds", 900)))
        except (TypeError, ValueError):
            pass
        folders = stored.get("folders")
        if isinstance(folders, dict):
            for path, fc in folders.items():
                cfg["folders"][path] = _normalize_folder_cfg(fc)
    return cfg


def _normalize_folder_cfg(fc: Any) -> dict[str, Any]:
    mode = DEFAULT_FOLDER_MODE
    threshold = DEFAULT_FOLDER_THRESHOLD
    if isinstance(fc, dict):
        if fc.get("mode") in VALID_FOLDER_MODES:
            mode = fc["mode"]
        try:
            t = float(fc.get("threshold", DEFAULT_FOLDER_THRESHOLD))
            threshold = min(1.0, max(0.0, t))
        except (TypeError, ValueError):
            pass
    return {"mode": mode, "threshold": threshold}


def folder_mode(cfg: dict[str, Any], path: str) -> tuple[str, float]:
    """Resolve (mode, threshold) for a watched folder path, with defaults."""
    folders = cfg.get("folders", {}) if isinstance(cfg, dict) else {}
    fc = folders.get(path)
    if not isinstance(fc, dict):
        # Also try a path-prefix match so a file deep inside a watched root
        # inherits that root's mode.
        norm = path.replace("\\", "/").lower()
        for fp, candidate in folders.items():
            if norm.startswith(fp.replace("\\", "/").lower().rstrip("/") + "/"):
                fc = candidate
                break
    if not isinstance(fc, dict):
        return (DEFAULT_FOLDER_MODE, DEFAULT_FOLDER_THRESHOLD)
    return (fc.get("mode", DEFAULT_FOLDER_MODE), float(fc.get("threshold", DEFAULT_FOLDER_THRESHOLD)))


async def _read_setting(db, key: str) -> Any:
    row = await db.get(Setting, key)
    return row.value if row is not None else None


async def get_watch_config(db) -> dict[str, Any]:
    """Return the watch config merged over defaults."""
    return merge_watch_config(await _read_setting(db, KEY_WATCH))


class WatcherService:
    """Singleton daemon managing the awatch + debounce + poll tasks."""

    def __init__(self) -> None:
        self._lifecycle_lock = asyncio.Lock()  # serialize start/stop/reconfigure
        self._stop_event: asyncio.Event | None = None
        self._tasks: list[asyncio.Task] = []

        # Resolved config snapshot (set on start()).
        self._enabled: bool = False
        self._cfg: dict[str, Any] = dict(DEFAULT_WATCH_CONFIG)
        self._dirs: list[str] = []
        self._debounce_seconds: int = 30
        self._poll_interval_seconds: int = 900

        # Event-trigger bookkeeping.
        self._dirty: bool = False
        self._last_event: float = 0.0

        # Poll-trigger bookkeeping.
        self._last_signature: tuple[int, int] | None = None

        # Observability.
        self._last_fire_at: str | None = None
        self._last_reason: str | None = None

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        async with self._lifecycle_lock:
            await self._start_locked()

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            await self._stop_locked()

    async def reconfigure(self) -> None:
        async with self._lifecycle_lock:
            await self._stop_locked()
            await self._start_locked()

    async def _start_locked(self) -> None:
        try:
            async with SessionLocal() as db:
                cfg = await get_watch_config(db)
                lib = _coerce_str(await _read_setting(db, KEY_LIBRARY_ROOT))
                folders_raw = await _read_setting(db, KEY_WATCH_FOLDERS)
        except Exception as e:  # noqa: BLE001 — never let config errors crash boot
            _log.warning("watcher: failed to read settings, staying idle: %r", e)
            self._enabled = False
            self._dirs = []
            return

        self._cfg = cfg
        self._enabled = bool(cfg.get("auto_scan", False))
        self._debounce_seconds = int(cfg.get("debounce_seconds", 30))
        self._poll_interval_seconds = int(cfg.get("poll_interval_seconds", 900))

        roots: list[str] = []
        if lib:
            roots.append(lib)
        if isinstance(folders_raw, list):
            roots.extend(str(p) for p in folders_raw if isinstance(p, str))
        # dedupe (order-preserving), keep only existing directories
        self._dirs = [r for r in dict.fromkeys(roots) if r and os.path.isdir(r)]

        if not self._enabled or not self._dirs:
            _log.info("watcher: idle (enabled=%s, dirs=%d)", self._enabled, len(self._dirs))
            return

        self._stop_event = asyncio.Event()
        self._dirty = False
        self._last_event = 0.0
        self._last_signature = None  # first poll computes the baseline, won't fire
        self._tasks = [
            asyncio.create_task(self._awatch_loop(), name="kira-watcher-awatch"),
            asyncio.create_task(self._debounce_loop(), name="kira-watcher-debounce"),
            asyncio.create_task(self._poll_loop(), name="kira-watcher-poll"),
        ]
        _log.info(
            "watcher: armed over %d dir(s); debounce=%ds poll=%ds",
            len(self._dirs), self._debounce_seconds, self._poll_interval_seconds,
        )

    async def _stop_locked(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        tasks, self._tasks = self._tasks, []
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001
                _log.debug("watcher: task ended with %r", e)
        self._stop_event = None

    # ── trigger loops ────────────────────────────────────────────────────

    async def _awatch_loop(self) -> None:
        try:
            from watchfiles import awatch
        except Exception as e:  # noqa: BLE001
            _log.warning("watcher: watchfiles unavailable, FS events off: %r", e)
            return
        assert self._stop_event is not None
        try:
            async for changes in awatch(*self._dirs, stop_event=self._stop_event, recursive=True):
                if any(not _is_ignored_path(path) for _change, path in changes):
                    self._dirty = True
                    self._last_event = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — a vanished dir etc. mustn't crash boot
            _log.warning("watcher: awatch loop ended: %r", e)

    async def _debounce_loop(self) -> None:
        try:
            while self._stop_event is not None and not self._stop_event.is_set():
                await asyncio.sleep(2)
                if not self._dirty:
                    continue
                if (time.monotonic() - self._last_event) >= self._debounce_seconds:
                    self._dirty = False
                    await self._fire("event")
        except asyncio.CancelledError:
            raise

    async def _poll_loop(self) -> None:
        try:
            while self._stop_event is not None and not self._stop_event.is_set():
                waited = 0
                while (
                    waited < self._poll_interval_seconds
                    and self._stop_event is not None
                    and not self._stop_event.is_set()
                ):
                    await asyncio.sleep(min(5, self._poll_interval_seconds - waited))
                    waited += 5
                if self._stop_event is None or self._stop_event.is_set():
                    break
                sig = await asyncio.to_thread(self._compute_signature)
                if self._last_signature is not None and sig != self._last_signature:
                    self._last_signature = sig
                    await self._fire("poll")
                else:
                    self._last_signature = sig
        except asyncio.CancelledError:
            raise

    def _compute_signature(self) -> tuple[int, int]:
        """(file_count, max_mtime_ns) across watched dirs — cheap change probe.

        Runs in a worker thread (os.walk is blocking).
        """
        count = 0
        max_mtime = 0
        for root in self._dirs:
            for dirpath, _dirs, files in os.walk(root):
                for name in files:
                    if _is_ignored_path(name):
                        continue
                    count += 1
                    try:
                        m = os.stat(os.path.join(dirpath, name)).st_mtime_ns
                        if m > max_mtime:
                            max_mtime = m
                    except OSError:
                        continue
                    if count >= _POLL_SIGNATURE_FILE_CAP:
                        return (count, max_mtime)
        return (count, max_mtime)

    # ── fire ─────────────────────────────────────────────────────────────

    async def _fire(self, reason: str) -> None:
        try:
            from kira.api.scans import _start_scan  # lazy: avoid import cycle

            scan_id = await _start_scan(list(self._dirs), source="auto")
            if scan_id is None:
                _log.debug("watcher: skip auto-scan (%s) — scan already running", reason)
                return
            self._last_fire_at = datetime.now(timezone.utc).isoformat()
            self._last_reason = reason
            _log.info("watcher: auto-scan triggered (%s), scan_id=%s", reason, scan_id)
            # Per-folder auto_rename is handled post-scan by the completion
            # hook (scans.py → maybe_auto_rename, which actually renames the
            # files that clear threshold). The scan itself is fire-and-forget;
            # we don't await it here.
        except Exception as e:  # noqa: BLE001 — a trigger failure must not kill the loop
            _log.warning("watcher: failed to fire auto-scan (%s): %r", reason, e)

    # ── status ───────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "watching": bool(self._tasks),
            "folders": list(self._dirs),
            "debounce_seconds": self._debounce_seconds,
            "poll_interval_seconds": self._poll_interval_seconds,
            "last_fire_at": self._last_fire_at,
            "last_reason": self._last_reason,
        }


# Module-level singleton, imported by main.py (lifespan), scans.py, settings.py.
watcher = WatcherService()


def compute_auto_rename_eligibility(
    cfg: dict[str, Any],
    files: list[tuple[int, str, float | None, bool]],
) -> tuple[list[int], int]:
    """Pure gate (Pass 6 #6 + #7) — no I/O, unit-testable.

    `files` is [(file_id, file_path, confidence, has_valid_match)]. A file is
    ELIGIBLE for auto-rename when its watched-folder mode (by path) is
    "auto_rename" AND it has a valid match that clears
    `meets_threshold(confidence, STRICT, folder_threshold)`. Returns
    (eligible_file_ids, held_count) where `held` counts auto_rename-folder
    files that had a match but fell below threshold (or lacked one) — i.e.
    files we deliberately left for manual review. Files in scan-only folders
    are neither eligible nor held (they were never auto-rename candidates).
    """
    from kira.matcher.strict_mode import MatchMode, meets_threshold

    eligible: list[int] = []
    held = 0
    for fid, path, confidence, has_match in files:
        if not path:
            continue
        mode, threshold = folder_mode(cfg, path)
        if mode != "auto_rename":
            continue
        if not has_match:
            held += 1
            continue
        if meets_threshold(confidence, MatchMode.STRICT, threshold):
            eligible.append(fid)
        else:
            held += 1
    return eligible, held


async def _resolve_rename_defaults(db) -> tuple[str, str]:
    """The op + naming profile an auto-rename should use — the SAME defaults
    the user set for manual renames (`rename.default_op`, `naming.profile`).
    Falls back to ('hardlink', 'Plex'): hardlink is non-destructive (the
    original stays put), the safe choice for an unattended move."""
    op = _coerce_str(await _read_setting(db, "rename.default_op")) or "hardlink"
    profile = _coerce_str(await _read_setting(db, "naming.profile")) or "Plex"
    return op, profile


async def maybe_auto_rename(scan_id: int, new_file_ids: list[int]) -> None:
    """Auto-organize newly-matched files that sit in an `auto_rename` watched
    folder AND clear that folder's confidence threshold (Pass 6 #6 + #7).

    Gate, per file:
      1. its watched-folder mode (by path) is "auto_rename" — else it stays
         pending for manual review (the conservative default);
      2. its selected match clears `meets_threshold(confidence, STRICT,
         folder_threshold)` — STRICT mode, so a shaky match is never auto-acted.

    Eligible files are renamed by reusing the normal `rename()` executor with
    the user's default op + profile — which also fires the media-server refresh
    + notification fan-out. Files below threshold are left for the user. The
    whole pass is exception-isolated: an auto-rename failure must never crash
    the scan worker that called it.
    """
    if not new_file_ids:
        return
    try:
        from kira.models import MediaFile
        from sqlalchemy.orm import selectinload

        async with SessionLocal() as db:
            cfg = await get_watch_config(db)
            folders = cfg.get("folders", {}) if isinstance(cfg, dict) else {}
            if not any(
                isinstance(fc, dict) and fc.get("mode") == "auto_rename"
                for fc in folders.values()
            ):
                return  # no folder opts into auto-rename — nothing to do

            rows = list(await db.scalars(
                select(MediaFile)
                .options(selectinload(MediaFile.matches))
                .where(MediaFile.id.in_(new_file_ids))
            ))

            # Build (id, path, confidence, has_valid_match) tuples for the pure gate.
            file_infos: list[tuple[int, str, float | None, bool]] = []
            for f in rows:
                selected = next((m for m in f.matches if m.is_selected), None)
                has_match = bool(selected and selected.provider and selected.provider_id)
                conf = selected.confidence if selected else None
                file_infos.append((f.id, f.file_path or "", conf, has_match))

            eligible, held = compute_auto_rename_eligibility(cfg, file_infos)

            if not eligible:
                _log.info(
                    "watcher: auto_rename — scan %s: 0 of %d new file(s) cleared "
                    "threshold (%d held for review)", scan_id, len(rows), held,
                )
                return

            op, profile = await _resolve_rename_defaults(db)

            # CR-08: reuse the real rename executor via the plain SERVICE
            # function `perform_rename` (NOT the FastAPI endpoint handler), so
            # the daemon isn't coupled to the API layer / `Depends(get_session)`.
            # It still persists RenameHistory, creates the summary Notification,
            # refreshes Plex/Jellyfin, and fans out to external sinks — we just
            # drive it with our own session.
            from kira.api.rename import RenameRequest, perform_rename
            payload = RenameRequest(
                file_ids=eligible, profile=profile, op=op,
                library_root_name=None, dry_run=False,
            )
            result = await perform_rename(payload, db)
            _log.info(
                "watcher: auto_rename — scan %s: renamed %d, failed %d, held %d (op=%s, profile=%s)",
                scan_id, result.succeeded, result.failed, held, op, profile,
            )
    except Exception as e:  # noqa: BLE001 — never crash the scan worker
        _log.warning("watcher: maybe_auto_rename failed: %r", e)
