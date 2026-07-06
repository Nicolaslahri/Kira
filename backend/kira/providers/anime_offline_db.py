"""anime-offline-database integration — offline episode counts for AniDB ids.

AniDB's only official dump is `anime-titles.xml.gz` (titles ONLY — it already
powers Kira's instant local anime search). Episode data is NOT in any official
dump, which is why per-AID HTTP calls exist at all. The community-maintained
manami-project/anime-offline-database (ODbL) fills most of that gap: a weekly
JSON with ~14.5k AniDB-linked entries, each carrying an episode count.

Kira uses it for ONE thing: pre-filling the episode-count cache that cour
routing, franchise offsets, and the episode-count sanity metric consume — the
lazy per-sibling `get_episodes` fetches that used to cost one throttled
5-second AniDB call each on first encounter. With the prefill, a first scan of
a large anime library resolves counts from disk.

Safety rules:
  - Only FINISHED shows are indexed. An airing show's count changes weekly and
    a stale count could misroute a cour; those still resolve live.
  - The offline count is a BASE layer only — anything the live API ever
    reported (the write-through cache) overrides it.
  - Everything is best-effort: no dump → behaviour is exactly as before.

The 62 MB raw JSON is parsed STREAMING (one entry decoded at a time) so peak
memory stays at one entry + the raw text, not the full 40k-entry object tree —
this runs on a 1 GB NAS. The raw file is deleted after the ~200 KB reduced
index is written.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

from kira.config import cache_dir as _kira_cache_dir

logger = logging.getLogger("kira.providers.anime_offline_db")

_AODB_URL = (
    "https://github.com/manami-project/anime-offline-database"
    "/releases/latest/download/anime-offline-database-minified.json"
)
_CACHE_DIR = _kira_cache_dir()
_RAW_PATH = _CACHE_DIR / "anime-offline-database-minified.json"
_INDEX_PATH = _CACHE_DIR / "anidb-offline-ep-counts.json"
_REFRESH_SEC = 7 * 24 * 3600          # the dump itself updates ~weekly

# Activity-job name for the refresh — read by GET /system/datasets so the UI
# can show live progress while the 62 MB dump downloads/parses.
AODB_JOB = "anime-offline-db-refresh"

_ANIDB_SRC_RE = re.compile(r"https?://anidb\.net/anime/(\d+)")


def index_status() -> dict:
    """On-disk state of the reduced episode-count index, for GET /system/datasets."""
    try:
        st = _INDEX_PATH.stat()
        return {"exists": True, "size_bytes": st.st_size, "updated_at": st.st_mtime}
    except OSError:
        return {"exists": False, "size_bytes": None, "updated_at": None}

# Module-level lazy singleton of the reduced index ({aid: episode_count}).
_index: dict[int, int] | None = None
_refresh_lock = asyncio.Lock()


def _build_index_from_raw(raw_path: Path) -> dict[int, int]:
    """Stream-parse the dump's `data` array with raw_decode — one entry object
    in memory at a time (the full tree would be ~10x the 62 MB text). Sync;
    call via to_thread. Returns {anidb_id: episodes} for FINISHED entries."""
    # BOUNDED memory: read in 4 MB chunks into a rolling buffer, decode one
    # entry at a time, trim consumed text. A whole-file read + full-tree
    # json.load measured ~550 MB peak — this must coexist with the live app
    # on a 1 GB NAS. Chunked, the peak is ~one chunk + one entry.
    decoder = json.JSONDecoder()
    out: dict[int, int] = {}
    _CHUNK = 4 * 1024 * 1024
    buf = ""
    started = False

    def _consume(entry: dict) -> None:
        try:
            if entry.get("status") != "FINISHED":
                return
            eps = entry.get("episodes")
            if not isinstance(eps, int) or eps <= 0:
                return
            for src in entry.get("sources", ()):
                m = _ANIDB_SRC_RE.match(src)
                if m:
                    out[int(m.group(1))] = eps
                    return
        except Exception:  # noqa: BLE001 — one malformed entry must not kill the build
            return

    with open(raw_path, encoding="utf-8") as fh:
        while True:
            chunk = fh.read(_CHUNK)
            buf += chunk
            if not started:
                d = buf.find('"data"')
                if d == -1:
                    if not chunk:
                        break
                    buf = buf[-16:]        # '"data"' may span the boundary
                    continue
                b = buf.find("[", d)
                if b == -1:
                    if not chunk:
                        break
                    continue
                buf = buf[b + 1:]
                started = True
            i, n = 0, len(buf)
            done = False
            while True:
                while i < n and buf[i] in " \t\r\n,":
                    i += 1
                if i >= n:
                    buf = ""
                    break
                if buf[i] == "]":
                    done = True
                    break
                try:
                    entry, j = decoder.raw_decode(buf, i)
                except json.JSONDecodeError:
                    buf = buf[i:]           # entry truncated mid-chunk — read more
                    break
                i = j
                _consume(entry)
            if done or not chunk:
                break
    return out


def load_index() -> dict[int, int]:
    """The reduced {aid: episodes} index from disk (cached in-process).
    Empty dict when never built — callers fall back to live fetches."""
    global _index
    if _index is not None:
        return _index
    try:
        raw = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
        _index = {int(k): int(v) for k, v in raw.get("counts", {}).items()}
    except Exception:
        _index = {}
    return _index


async def refresh_if_stale() -> dict:
    """Download + reduce the dump when the local index is missing or older
    than a week. Best-effort; never raises. Returns a small summary."""
    global _index
    summary = {"refreshed": False, "entries": 0}
    async with _refresh_lock:
        try:
            if _INDEX_PATH.exists():
                age = time.time() - _INDEX_PATH.stat().st_mtime
                if age < _REFRESH_SEC:
                    summary["entries"] = len(load_index())
                    return summary
            from kira import activity, net
            from kira.download_guard import fetch_capped
            activity.begin(AODB_JOB, "Updating anime database · downloading")

            def _narrate(received: int, total: int | None) -> None:
                if total:
                    activity.set_label(
                        AODB_JOB,
                        f"Updating anime database · downloading {received >> 20} / {total >> 20} MB",
                    )
                else:
                    activity.set_label(
                        AODB_JOB,
                        f"Updating anime database · downloading {received >> 20} MB",
                    )

            fetched = await fetch_capped(
                net.shared_client(), _AODB_URL,
                max_bytes=200 * 1024 * 1024, timeout=120.0,
                follow_redirects=True,   # github releases 302 to a CDN host
                on_progress=_narrate,
            )
            if not fetched:
                logger.info("anime-offline-database download failed — keeping previous index")
                activity.end(AODB_JOB, ok=False, detail="Anime database download failed — keeping the previous index")
                summary["entries"] = len(load_index())
                return summary
            content, _ct = fetched
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(_RAW_PATH.write_bytes, content)
            del content
            activity.set_label(AODB_JOB, "Updating anime database · building index")
            counts = await asyncio.to_thread(_build_index_from_raw, _RAW_PATH)
            tmp = _INDEX_PATH.with_suffix(".json.tmp")
            await asyncio.to_thread(
                tmp.write_text,
                json.dumps({"built_at": int(time.time()),
                            "counts": {str(k): v for k, v in counts.items()}}),
                "utf-8",
            )
            os.replace(tmp, _INDEX_PATH)
            # The 62 MB raw file has served its purpose — don't hoard it.
            try:
                _RAW_PATH.unlink()
            except OSError:
                pass
            _index = counts
            summary["refreshed"] = True
            summary["entries"] = len(counts)
            logger.info("anime-offline-database index built: %d finished shows", len(counts))
            activity.end(AODB_JOB, ok=True,
                         detail=f"Anime database refreshed — {len(counts)} finished shows indexed")
        except Exception as e:  # noqa: BLE001 — a broken refresh must never break matching
            logger.warning("anime-offline-database refresh failed (non-fatal): %r", e)
            try:
                from kira import activity
                activity.end(AODB_JOB, ok=False, detail="Anime database refresh failed — keeping the previous index")
            except Exception:  # noqa: BLE001
                pass
            summary["entries"] = len(load_index())
    return summary
