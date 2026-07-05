"""Short-lived in-memory cache for provider SEARCH results.

Why it exists: a season backfill re-queries the same show for every episode, and
reopening the browse modal re-queries the same file — without a cache that's the
same provider call over and over. This caches each provider search keyed by its
query signature so those redundant calls collapse to one.

Why it's SHORT-lived and in-process (not persistent): subtitles are uploaded
continuously, so a long-lived/persistent cache would happily serve "nothing
found" for a show whose subs landed an hour ago. A ~15-minute TTL kills the
redundant calls that actually happen in a burst (backfill loop, repeated browse)
while still re-checking fresh enough to catch new uploads. Wiped on restart,
exactly like the pack-bytes cache.

It stores the RAW candidate list and hands back a deep COPY on every read, so the
aggregator's per-file scoring (which mutates score/reasons/sync) can never poison
a cached entry shared by another file of the same episode.
"""

from __future__ import annotations

import copy
import time
from typing import Any

_TTL_SECONDS = 900          # 15 min — comfortably spans a backfill loop
_MAX_ENTRIES = 256          # bounded; least-recently-used evicted when full

# key -> [stored_at, last_access, value]. stored_at drives TTL (never bumped, so
# a hot-but-stale entry still expires); last_access drives LRU eviction.
_store: dict[str, list] = {}


def _now() -> float:
    # A function (not inline) so tests can monkeypatch the clock for TTL.
    return time.monotonic()


def signature(provider: str, ctx: Any) -> str:
    """Stable key for ONE provider search: everything that changes its result —
    the media identity (ids / title), the episode coordinates, the wanted
    languages, and the variant prefs a provider may pass through to its API.
    Two files that resolve to the same show + episode share this key."""
    langs = ",".join(sorted((l or "").lower() for l in (getattr(ctx, "languages", None) or [])))
    parts = (
        provider,
        # Per-FILE, not per-episode: hash-match results (+50 "guaranteed sync")
        # are only guaranteed for the EXACT file they were computed against —
        # two releases of the same episode sharing a cache slot let one
        # release's hash-matched flag leak onto the other's subs.
        getattr(ctx, "video_path", None) or "",
        getattr(ctx, "media_type", None) or "",
        getattr(ctx, "tmdb_id", None),
        getattr(ctx, "imdb_id", None),
        getattr(ctx, "anidb_id", None),
        (getattr(ctx, "query", None) or "").strip().lower(),
        getattr(ctx, "season", None),
        getattr(ctx, "episode", None),
        getattr(ctx, "absolute", None),
        langs,
        getattr(ctx, "hearing_impaired", None) or "",
        getattr(ctx, "forced", None) or "",
    )
    return "|".join("" if p is None else str(p) for p in parts)


def get(key: str) -> list | None:
    """Return a COPY of the cached candidates, or None on miss/expiry."""
    hit = _store.get(key)
    if hit is None:
        return None
    stored_at, _last, value = hit
    if _now() - stored_at > _TTL_SECONDS:
        _store.pop(key, None)
        return None
    hit[1] = _now()                 # bump recency only — NOT the TTL clock
    return copy.deepcopy(value)


def put(key: str, value: list) -> None:
    """Store a COPY of the candidates under `key`, evicting the least-recently-
    used entry if the cache is full."""
    if key not in _store and len(_store) >= _MAX_ENTRIES:
        oldest = min(_store, key=lambda k: _store[k][1])
        _store.pop(oldest, None)
    now = _now()
    _store[key] = [now, now, copy.deepcopy(value)]


def clear() -> None:
    """Drop everything — used by tests and available for a manual flush."""
    _store.clear()
