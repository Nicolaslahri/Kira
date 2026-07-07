"""anime-offline-database integration — offline episode counts for AniDB ids.

AniDB's only official dump is `anime-titles.xml.gz` (titles ONLY — it already
powers Kira's instant local anime search). Episode data is NOT in any official
dump, which is why per-AID HTTP calls exist at all. The community-maintained
manami-project/anime-offline-database (ODbL) fills most of that gap: a weekly
JSON with ~14.5k AniDB-linked entries, each carrying an episode count.

Index v2 — Kira mines the dump for THREE things (anime-speed plan, Phase 2):
  1. FINISHED episode counts → pre-fill the episode-count cache consumed by
     cour routing, franchise offsets, and the episode-count sanity metric.
  2. Per-AID entry facts (episodes, status, type, year) → lets callers use
     ONGOING counts as guarded stale hints and reason about entry types
     without a live call.
  3. The RELATIONS graph (AID → related AIDs) → serves the franchise walk
     offline. This was the single biggest cold-scan cost: the live walk pays
     one throttled 5-second AniDB call per franchise member.

Safety rules:
  - Only FINISHED counts land in the base episode-count layer. An airing
    show's count changes weekly; callers may read ONGOING counts explicitly
    via `offline_count()` but must treat them as stale hints.
  - Offline data is a BASE layer only — anything the live API ever reported
    (the write-through caches) overrides it. Offline franchise closures are
    verified by the real typed walk in the background (see
    AniDBProvider.drain_relations_verify) and never written to the
    authoritative relations cache.
  - manami's relatedAnime edges are UNTYPED (no sequel-vs-side-story
    distinction) — consumers gate the closure through the Fribb same-series
    cross-ref to approximate the live walk's type filter.
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

# Index format version. v2 added `entries` (episodes/status/type/year per AID)
# and `relations` (the franchise graph); a v1 file on disk forces a rebuild.
_INDEX_VERSION = 2

# Module-level lazy singletons of the reduced index maps.
_index: dict[int, int] | None = None                       # FINISHED counts
_entries: dict[int, tuple[int, str, str, int | None]] | None = None
_relations: dict[int, list[int]] | None = None
_refresh_lock = asyncio.Lock()

_STATUS_CHAR = {"FINISHED": "F", "ONGOING": "O", "UPCOMING": "U"}
_TYPE_CHAR = {"TV": "T", "ONA": "O", "OVA": "V", "MOVIE": "M", "SPECIAL": "S"}


def _build_index_from_raw(raw_path: Path) -> dict:
    """Stream-parse the dump's `data` array with raw_decode — one entry object
    in memory at a time (the full tree would be ~10x the 62 MB text). Sync;
    call via to_thread.

    Returns {"counts": {aid: eps} FINISHED-only,
             "entries": {aid: [eps, status_char, type_char, year|None]},
             "relations": {aid: [related aids]}}."""
    # BOUNDED memory: read in 4 MB chunks into a rolling buffer, decode one
    # entry at a time, trim consumed text. A whole-file read + full-tree
    # json.load measured ~550 MB peak — this must coexist with the live app
    # on a 1 GB NAS. Chunked, the peak is ~one chunk + one entry.
    decoder = json.JSONDecoder()
    out: dict[int, int] = {}
    entries: dict[int, list] = {}
    relations: dict[int, list[int]] = {}
    _CHUNK = 4 * 1024 * 1024
    buf = ""
    started = False

    def _consume(entry: dict) -> None:
        try:
            aid: int | None = None
            for src in entry.get("sources", ()):
                m = _ANIDB_SRC_RE.match(src)
                if m:
                    aid = int(m.group(1))
                    break
            if aid is None:
                return  # not AniDB-linked — useless to us
            eps = entry.get("episodes")
            eps_i = eps if isinstance(eps, int) and eps > 0 else 0
            status = _STATUS_CHAR.get(entry.get("status") or "", "X")
            typ = _TYPE_CHAR.get(entry.get("type") or "", "X")
            season = entry.get("animeSeason") or {}
            year = season.get("year") if isinstance(season.get("year"), int) else None
            entries[aid] = [eps_i, status, typ, year]
            if status == "F" and eps_i > 0:
                out[aid] = eps_i
            rel: list[int] = []
            for r in entry.get("relatedAnime", ()):
                m = _ANIDB_SRC_RE.match(r)
                if m:
                    rel.append(int(m.group(1)))
            if rel:
                relations[aid] = rel
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
    return {"counts": out, "entries": entries, "relations": relations}


def _load_raw_index() -> dict:
    try:
        return json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_index() -> dict[int, int]:
    """The reduced {aid: episodes} index from disk (cached in-process).
    Empty dict when never built — callers fall back to live fetches."""
    global _index
    if _index is not None:
        return _index
    try:
        raw = _load_raw_index()
        _index = {int(k): int(v) for k, v in raw.get("counts", {}).items()}
    except Exception:
        _index = {}
    return _index


def load_entries() -> dict[int, tuple[int, str, str, int | None]]:
    """{aid: (episodes, status F/O/U/X, type T/O/V/M/S/X, year|None)} — all
    AniDB-linked entries regardless of status. Empty when never built or on a
    pre-v2 index (a refresh rebuilds it)."""
    global _entries
    if _entries is not None:
        return _entries
    try:
        raw = _load_raw_index()
        _entries = {
            int(k): (int(v[0]), str(v[1]), str(v[2]),
                     int(v[3]) if v[3] is not None else None)
            for k, v in raw.get("entries", {}).items()
        }
    except Exception:
        _entries = {}
    return _entries


def load_relations() -> dict[int, list[int]]:
    """{aid: [related aids]} — the UNTYPED franchise graph from the dump,
    SYMMETRIZED at load (manami edges are per-entry and occasionally one-way;
    a closure must reach A from B whenever it reaches B from A). Empty when
    never built or on a pre-v2 index."""
    global _relations
    if _relations is not None:
        return _relations
    try:
        raw = _load_raw_index()
        fwd = {
            int(k): [int(a) for a in v]
            for k, v in raw.get("relations", {}).items()
        }
        sym: dict[int, set[int]] = {}
        for src, targets in fwd.items():
            for dst in targets:
                sym.setdefault(src, set()).add(dst)
                sym.setdefault(dst, set()).add(src)
        _relations = {k: sorted(v) for k, v in sym.items()}
    except Exception:
        _relations = {}
    return _relations


def offline_count(aid: int) -> tuple[int, str] | None:
    """(episodes, status_char) for an AID — INCLUDING ongoing shows. Callers
    must treat non-FINISHED counts as stale hints (the dump is ~weekly).
    None when the AID isn't in the dump or has no usable count."""
    e = load_entries().get(aid)
    if not e or e[0] <= 0:
        return None
    return e[0], e[1]


def related_closure(aid: int) -> list[int] | None:
    """Transitive closure of the offline relations graph from `aid` (self
    included), sorted. None when the AID isn't in the dump at all — callers
    fall back to the live walk. A known AID with no edges returns [aid]
    (a genuine singleton, saving the live walk's one confirmation call)."""
    entries = load_entries()
    if aid not in entries:
        return None
    graph = load_relations()
    visited: set[int] = set()
    queue = [aid]
    while queue:
        cur = queue.pop()
        if cur in visited:
            continue
        visited.add(cur)
        for nxt in graph.get(cur, ()):
            if nxt not in visited and nxt in entries:
                queue.append(nxt)
    return sorted(visited)


async def refresh_if_stale() -> dict:
    """Download + reduce the dump when the local index is missing or older
    than a week. Best-effort; never raises. Returns a small summary."""
    global _index, _entries, _relations
    summary = {"refreshed": False, "entries": 0}
    async with _refresh_lock:
        try:
            if _INDEX_PATH.exists():
                age = time.time() - _INDEX_PATH.stat().st_mtime
                # A pre-v2 index lacks entries/relations — rebuild regardless
                # of age (requires a re-download; the raw file was deleted).
                current_version = _load_raw_index().get("v", 1)
                if age < _REFRESH_SEC and current_version >= _INDEX_VERSION:
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
            built = await asyncio.to_thread(_build_index_from_raw, _RAW_PATH)
            counts = built["counts"]
            tmp = _INDEX_PATH.with_suffix(".json.tmp")
            await asyncio.to_thread(
                tmp.write_text,
                json.dumps({
                    "v": _INDEX_VERSION,
                    "built_at": int(time.time()),
                    "counts": {str(k): v for k, v in counts.items()},
                    "entries": {str(k): v for k, v in built["entries"].items()},
                    "relations": {str(k): v for k, v in built["relations"].items()},
                }),
                "utf-8",
            )
            os.replace(tmp, _INDEX_PATH)
            # The 62 MB raw file has served its purpose — don't hoard it.
            try:
                _RAW_PATH.unlink()
            except OSError:
                pass
            # Reset ALL in-process singletons so the new maps load lazily.
            _index = counts
            _entries = None
            _relations = None
            summary["refreshed"] = True
            summary["entries"] = len(counts)
            logger.info(
                "anime-offline-database index built: %d finished counts, %d entries, %d relation nodes",
                len(counts), len(built["entries"]), len(built["relations"]))
            activity.end(AODB_JOB, ok=True,
                         detail=f"Anime database refreshed — {len(built['entries'])} shows indexed")
        except Exception as e:  # noqa: BLE001 — a broken refresh must never break matching
            logger.warning("anime-offline-database refresh failed (non-fatal): %r", e)
            try:
                from kira import activity
                activity.end(AODB_JOB, ok=False, detail="Anime database refresh failed — keeping the previous index")
            except Exception:  # noqa: BLE001
                pass
            summary["entries"] = len(load_index())
    return summary
