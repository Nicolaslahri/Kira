"""Phase 5 — anime-lists per-episode mappings (the deep-anime keystone).

Fribb's `anime-list-full.json` (already loaded elsewhere) carries only a FLAT
`season` integer per AID — enough for contiguous franchises, but it can't
express start-episode offsets, mid-season special inserts, or non-contiguous
ranges. ScudLee's `anime-lists` XML does, via `<mapping>` blocks. This is the
data that lets the reference renamer nail the hard anime cases.

This module ingests that XML (openly licensed; reimplemented parser, no
the reference renamer code) and exposes a resolver:

    (tvdb_id, tvdb_season, tvdb_episode) → (anidb_id, anidb_episode)

The parser + resolver are PURE and unit-tested against a fixture. The
download/cache layer mirrors the AniDB title-dump pattern (lazy, 24h refresh,
corruption-safe, never throws to callers).

ScudLee `<anime>` shapes handled:
  • flat:    defaulttvdbseason + episodeoffset (tvdb_ep = anidb_ep + offset)
  • range:   <mapping anidbseason tvdbseason start end offset/>
  • explicit:<mapping …>;<anidb_ep>-<tvdb_ep>;…</mapping>
"""
from __future__ import annotations

import logging

import asyncio
import re
import time
# defusedxml (not stdlib xml): this XML comes from a remote CDN
# (raw.githubusercontent.com), so a MITM / compromised cache could inject XXE
# or billion-laughs entities. Same hardening as providers/anidb.py; drop-in API.
import defusedxml.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from kira.providers.base import KIRA_USER_AGENT

logger = logging.getLogger(__name__)

_MAPPING_URL = (
    "https://raw.githubusercontent.com/Anime-Lists/anime-lists/master/anime-list-master.xml"
)

_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"
_XML_PATH = _CACHE_DIR / "anime-list-master.xml"
_MAX_AGE_SEC = 24 * 3600

# ── ;a-b;a-b; explicit per-episode map inside a <mapping> body ──────────────
_EXPLICIT_RE = re.compile(r";(-?\d+)-(-?\d+)")


@dataclass
class Mapping:
    anidb_season: int
    tvdb_season: int
    offset: int = 0
    start: int | None = None          # anidb-episode range start (inclusive)
    end: int | None = None            # anidb-episode range end (inclusive)
    # explicit anidb_ep → tvdb_ep pairs (override the offset arithmetic)
    explicit: dict[int, int] = field(default_factory=dict)


@dataclass
class AnimeListEntry:
    anidb_id: int
    tvdb_id: int | None
    default_tvdb_season: int | None   # None when "a" (absolute) or absent
    episode_offset: int = 0
    mappings: list[Mapping] = field(default_factory=list)


def _to_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_anime_list_xml(data: bytes | str) -> dict[int, list[AnimeListEntry]]:
    """Parse the ScudLee XML into ``{tvdb_id: [AnimeListEntry, …]}``.

    Indexed by TVDB id (multiple AIDs map to one TVDB series across seasons).
    Entries with no usable TVDB id are dropped — the resolver is TVDB-keyed.
    """
    index: dict[int, list[AnimeListEntry]] = {}
    try:
        root = ET.fromstring(data)
    except Exception:
        return index  # malformed / truncated XML → empty index, never raise
    if root is None:
        return index
    for anime in root.findall("anime"):
        aid = _to_int(anime.get("anidbid"))
        tvdb_id = _to_int(anime.get("tvdbid"))
        if aid is None or tvdb_id is None:
            continue  # resolver needs both
        default_season = _to_int(anime.get("defaulttvdbseason"))  # None for "a"
        ep_offset = _to_int(anime.get("episodeoffset")) or 0
        entry = AnimeListEntry(
            anidb_id=aid, tvdb_id=tvdb_id,
            default_tvdb_season=default_season, episode_offset=ep_offset,
        )
        mlist = anime.find("mapping-list")
        if mlist is not None:
            for m in mlist.findall("mapping"):
                a_season = _to_int(m.get("anidbseason"))
                t_season = _to_int(m.get("tvdbseason"))
                if a_season is None or t_season is None:
                    continue
                explicit: dict[int, int] = {}
                for a_ep, t_ep in _EXPLICIT_RE.findall(m.text or ""):
                    ai, ti = _to_int(a_ep), _to_int(t_ep)
                    if ai is not None and ti is not None:
                        explicit[ai] = ti
                entry.mappings.append(Mapping(
                    anidb_season=a_season, tvdb_season=t_season,
                    offset=_to_int(m.get("offset")) or 0,
                    start=_to_int(m.get("start")), end=_to_int(m.get("end")),
                    explicit=explicit,
                ))
        index.setdefault(tvdb_id, []).append(entry)
    return index


def resolve_tvdb_episode(
    index: dict[int, list[AnimeListEntry]],
    tvdb_id: int,
    tvdb_season: int,
    tvdb_episode: int,
) -> tuple[int, int] | None:
    """Resolve a TVDB (season, episode) to ``(anidb_id, anidb_episode)``.

    Tries, per candidate AID for this TVDB id:
      1. explicit ``;anidb-tvdb;`` pairs (inverted),
      2. ``<mapping>`` ranges (anidb_ep = tvdb_ep − offset, within range),
      3. the flat default-season + episode_offset.
    Returns the first hit, or None when nothing maps.
    """
    entries = index.get(tvdb_id)
    if not entries:
        return None

    # 1 + 2: per-mapping resolution (most precise).
    for entry in entries:
        for m in entry.mappings:
            if m.tvdb_season != tvdb_season:
                continue
            # Explicit pairs win — invert anidb→tvdb to tvdb→anidb.
            for a_ep, t_ep in m.explicit.items():
                if t_ep == tvdb_episode:
                    return (entry.anidb_id, a_ep)
            # Range form: tvdb_ep = anidb_ep + offset → anidb_ep = tvdb_ep − offset.
            anidb_ep = tvdb_episode - m.offset
            if anidb_ep >= 1:
                if (m.start is None or anidb_ep >= m.start) and \
                   (m.end is None or anidb_ep <= m.end):
                    return (entry.anidb_id, anidb_ep)

    # 3: flat default season + episode offset — ONLY for entries WITHOUT an
    # explicit mapping-list (those are authoritative for their own episodes;
    # an out-of-range episode must not silently fall through to flat math).
    for entry in entries:
        if entry.mappings:
            continue
        if entry.default_tvdb_season is not None and entry.default_tvdb_season == tvdb_season:
            anidb_ep = tvdb_episode - entry.episode_offset
            if anidb_ep >= 1:
                return (entry.anidb_id, anidb_ep)
    return None


def resolve_anidb_episode(
    index: dict[int, list[AnimeListEntry]],
    anidb_id: int,
    anidb_episode: int,
    anidb_season: int = 1,
) -> tuple[int, int] | None:
    """Reverse of `resolve_tvdb_episode`: an AniDB ``(id, episode)`` → its TVDB
    ``(season, episode)``.

    This is how a seasonless AniDB umbrella gets a real per-arc TVDB season:
    ScudLee splits the flat absolute run into ``<mapping>`` season ranges, e.g.
    One Piece (AID 69) ``…tvdbseason=22 start=1086 end=1155; tvdbseason=23
    start=1156 …`` → anidb ep 1156 → TVDB **S23E01**. A cour AID with no
    mapping-list uses ``defaulttvdbseason + episodeoffset`` (Bleach TYBW cour 2 =
    AID 17765, season 17 + offset 13 → S17E14). One mechanism fits both shapes.

    Precedence mirrors `resolve_tvdb_episode`: explicit pairs → ranges → flat
    default. Returns None when nothing covers the episode (caller keeps its own
    season). Pure / filesystem-free.
    """
    if anidb_episode is None or anidb_episode < 1:
        return None
    entry: AnimeListEntry | None = None
    for entries in index.values():
        for e in entries:
            if e.anidb_id == anidb_id:
                entry = e
                break
        if entry is not None:
            break
    if entry is None:
        return None

    # 1: explicit anidb→tvdb pairs (specials interleaving) win.
    for m in entry.mappings:
        if m.anidb_season == anidb_season:
            t_ep = m.explicit.get(anidb_episode)
            if t_ep is not None:
                return (m.tvdb_season, t_ep)
    # 2: range mappings — tvdb_ep = anidb_ep + offset, within [start, end].
    for m in entry.mappings:
        if m.anidb_season != anidb_season:
            continue
        if (m.start is None or anidb_episode >= m.start) and \
           (m.end is None or anidb_episode <= m.end):
            t_ep = anidb_episode + m.offset
            if t_ep >= 1:
                return (m.tvdb_season, t_ep)
    # 3: flat default season + offset — ONLY for entries WITHOUT a mapping-list
    # (an explicit list is authoritative; an out-of-range ep must not fall
    # through to flat math). This is the cour-AID case (Bleach TYBW cours).
    if not entry.mappings and entry.default_tvdb_season is not None:
        t_ep = anidb_episode + entry.episode_offset
        if t_ep >= 1:
            return (entry.default_tvdb_season, t_ep)
    return None


# ── Lazy download + cache + parse (mirrors the AniDB title-dump pattern) ────
_index: dict[int, list[AnimeListEntry]] | None = None
_load_lock = asyncio.Lock()
# AID → entry reverse index, built lazily from `_index` for resolve_anidb_to_tvdb
# (the forward index is TVDB-keyed; scanning all entries per file would be O(n)).
_aid_index: dict[int, AnimeListEntry] | None = None
_aid_index_src: dict | None = None


def _fresh() -> bool:
    return _XML_PATH.exists() and (time.time() - _XML_PATH.stat().st_mtime) < _MAX_AGE_SEC


async def _ensure_index(client: httpx.AsyncClient | None = None) -> dict[int, list[AnimeListEntry]]:
    """Return the parsed index, downloading/refreshing the XML if stale.
    Never raises — returns {} when the source is unreachable AND no cache."""
    global _index
    if _index is not None and _fresh():
        return _index
    async with _load_lock:
        if _index is not None and _fresh():
            return _index
        if not _fresh():
            own = client is None
            c = client or httpx.AsyncClient()
            try:
                r = await c.get(
                    _MAPPING_URL,
                    headers={"User-Agent": KIRA_USER_AGENT, "Accept-Encoding": "gzip"},
                    timeout=60.0, follow_redirects=True,
                )
                r.raise_for_status()
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                _XML_PATH.write_bytes(r.content)
            except Exception as e:
                # Stale cache is better than nothing — fall through to parse it.
                logger.warning(f"anime_lists: download failed ({e!r}); using cache if present")
            finally:
                if own:
                    await c.aclose()
        if _XML_PATH.exists():
            try:
                data = await asyncio.to_thread(_XML_PATH.read_bytes)
                parsed = await asyncio.to_thread(parse_anime_list_xml, data)
                if parsed:  # corruption-safety: only adopt a non-empty parse
                    _index = parsed
            except Exception as e:
                logger.warning(f"anime_lists: parse failed: {e!r}")
        if _index is None:
            _index = {}
        return _index


async def resolve_tvdb_to_anidb(
    tvdb_id: int | str, season: int | str, episode: int | str,
    client: httpx.AsyncClient | None = None,
) -> tuple[int, int] | None:
    """Async front door: ensure the index is loaded, then resolve a TVDB
    (season, episode) to ``(anidb_id, anidb_episode)``. None on any failure."""
    try:
        idx = await _ensure_index(client)
        return resolve_tvdb_episode(idx, int(tvdb_id), int(season), int(episode))
    except (TypeError, ValueError):
        return None
    except Exception as e:
        logger.warning(f"anime_lists.resolve_tvdb_to_anidb failed: {e!r}")
        return None


async def resolve_anidb_to_tvdb(
    anidb_id: int | str, anidb_episode: int | str,
    anidb_season: int | str = 1,
    client: httpx.AsyncClient | None = None,
) -> tuple[int, int] | None:
    """Async front door: ensure the index is loaded, then resolve an AniDB
    ``(id, episode)`` to its TVDB ``(season, episode)``. None on any failure.

    Uses a cached AID→entry reverse index so a large batch rename doesn't rescan
    the whole (TVDB-keyed) table per file."""
    global _aid_index, _aid_index_src
    try:
        idx = await _ensure_index(client)
        if not idx:
            return None
        if _aid_index is None or _aid_index_src is not idx:
            built: dict[int, AnimeListEntry] = {}
            for entries in idx.values():
                for e in entries:
                    built.setdefault(e.anidb_id, e)
            _aid_index = built
            _aid_index_src = idx
        entry = _aid_index.get(int(anidb_id))
        if entry is None:
            return None
        # Reuse the pure resolver (single code path / precedence) against a
        # one-entry index keyed by this entry's tvdb id.
        return resolve_anidb_episode(
            {entry.tvdb_id or 0: [entry]},
            int(anidb_id), int(anidb_episode), int(anidb_season),
        )
    except (TypeError, ValueError):
        return None
    except Exception as e:
        logger.warning(f"anime_lists.resolve_anidb_to_tvdb failed: {e!r}")
        return None
