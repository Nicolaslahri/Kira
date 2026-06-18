"""Cross-reference table: AniDB AID → TVDB series ID, TMDB series ID, etc.

AniDB has the best canonical anime IDs, but their API is rate-limited and
will IP-ban us within an hour if we fetch a few hundred posters. TVDB and
TMDB don't have that problem, and they carry the same artwork. So the
modern approach is:

  1. Use AniDB for matching (title disambiguation, season identity)
  2. Use the AID to look up the corresponding TVDB / TMDB series ID
  3. Fetch posters + episode metadata from TVDB / TMDB

The Fribb/anime-lists project maintains a community-curated JSON mapping
that combines anime-lists, kitsu, anilist and other sources. We download
it once a week and serve lookups out of memory.

https://github.com/Fribb/anime-lists
"""

from __future__ import annotations

import logging

import asyncio
import json
import time
from pathlib import Path
from typing import Any, ClassVar

import httpx

from kira.providers.base import KIRA_USER_AGENT

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"
_MAPPING_FILE = _CACHE_DIR / "anime-mappings.json"
_MAPPING_URL = "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-full.json"
_REFRESH_AGE_SEC = 7 * 24 * 3600  # weekly

# Fribb's dump is always ~20 MB. Anything dramatically smaller (sub-1 MB)
# is a corruption symptom — a truncated download, an upstream 503 HTML
# error page, a NAS that returned a stub file. We refuse to clobber a
# good cached copy with a payload that small.
_MIN_DUMP_BYTES = 1_000_000  # 1 MB sanity floor


def _load_and_prune() -> dict[int, dict[str, Any]]:
    """Read + parse the Fribb dump, keeping only the fields we actually use.

    Module-level so asyncio.to_thread can call it without serializing a
    classmethod reference. Each Fribb entry has 20+ external-provider IDs
    (anilist, kitsu, mal, livechart, etc.); we keep three. Dropping the
    rest collapses the in-memory footprint from ~80 MB to ~3 MB.
    """
    raw = json.loads(_MAPPING_FILE.read_text(encoding="utf-8"))
    pruned: dict[int, dict[str, Any]] = {}
    for entry in raw:
        aid = entry.get("anidb_id")
        if not isinstance(aid, int):
            continue
        pruned[aid] = {
            "tvdb_id": entry.get("tvdb_id"),
            "themoviedb_id": entry.get("themoviedb_id"),
            "season": entry.get("season"),
        }
    return pruned


class AnimeMappings:
    """In-memory AID → other-provider-ID lookup. Lazy-loaded on first call."""

    _by_aid: ClassVar[dict[int, dict[str, Any]] | None] = None
    _load_lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    # Reverse indexes, rebuilt whenever `_by_aid` is (re)assigned via _set_map.
    # Without these, every reverse lookup (aid_by_tvdb / aids_by_tvdb /
    # aid_by_tmdb_tv / aid_by_tvdb_season / aids_by_tvdb_season) linearly
    # scanned the whole ~40k-entry table — and those run per-candidate in the
    # match hot loop and across the whole library on the boot heal sweep, i.e.
    # millions of probes. Lists preserve insertion (Fribb-JSON) order so the
    # "first match wins" single-AID lookups return the same AID as before.
    _by_tvdb: ClassVar[dict[int, list[int]]] = {}
    _by_tmdb_tv: ClassVar[dict[int, list[int]]] = {}
    _by_tvdb_season: ClassVar[dict[tuple[int, int], list[int]]] = {}

    @classmethod
    def _set_map(cls, parsed: dict[int, dict[str, Any]]) -> None:
        """Assign `_by_aid` and (re)build the reverse indexes in one place."""
        cls._by_aid = parsed
        by_tvdb: dict[int, list[int]] = {}
        by_tmdb_tv: dict[int, list[int]] = {}
        by_tvdb_season: dict[tuple[int, int], list[int]] = {}
        for aid, entry in parsed.items():
            tvdb = entry.get("tvdb_id")
            if isinstance(tvdb, int):
                by_tvdb.setdefault(tvdb, []).append(aid)
                s = entry.get("season")
                if isinstance(s, dict):
                    sv = s.get("tvdb")
                    if isinstance(sv, int):
                        by_tvdb_season.setdefault((tvdb, sv), []).append(aid)
            tm = entry.get("themoviedb_id")
            tmdb_tv = tm.get("tv") if isinstance(tm, dict) else tm
            if isinstance(tmdb_tv, int):
                by_tmdb_tv.setdefault(tmdb_tv, []).append(aid)
        cls._by_tvdb = by_tvdb
        cls._by_tmdb_tv = by_tmdb_tv
        cls._by_tvdb_season = by_tvdb_season

    @classmethod
    def _fresh(cls) -> bool:
        if not _MAPPING_FILE.exists():
            return False
        return (time.time() - _MAPPING_FILE.stat().st_mtime) < _REFRESH_AGE_SEC

    @classmethod
    async def _ensure_loaded(cls, client: httpx.AsyncClient | None = None) -> None:
        if cls._by_aid is not None and cls._fresh():
            return
        async with cls._load_lock:
            if cls._by_aid is not None and cls._fresh():
                return

            # Try to refresh from upstream. A GitHub 5xx must NOT kill the app —
            # if we have any cached copy on disk, fall through and use it. Only
            # the "no upstream AND no cache" path leaves us with an empty map.
            if not _MAPPING_FILE.exists() or not cls._fresh():
                try:
                    await cls._download(client)
                except Exception as e:
                    logger.warning(f"anime_mappings: download failed ({e!r}); falling back to cache.")
                    if not _MAPPING_FILE.exists():
                        cls._set_map({})
                        return

            # Parse + prune in a worker thread. The Fribb dump is ~20 MB JSON;
            # parsing it on the event loop blocks every request for ~500ms.
            # We also drop every key we don't consume — only tvdb_id /
            # themoviedb_id / season survive — which collapses ~80 MB of dicts
            # down to a few MB.
            try:
                parsed = await asyncio.to_thread(_load_and_prune)
            except Exception as e:
                # H6: Preserve the previous in-memory copy rather than
                # blowing it away. A corrupt download shouldn't take out
                # every anime poster + season-aware match in the entire
                # library until the next 7-day refresh cycle. We rename
                # the bad file aside so a manual user inspection is
                # possible without re-downloading.
                logger.warning(f"anime_mappings: parse failed ({e!r}); preserving in-memory cache.")
                try:
                    bad = _MAPPING_FILE.with_suffix(".bad")
                    if _MAPPING_FILE.exists():
                        _MAPPING_FILE.rename(bad)
                except OSError:
                    pass
                if cls._by_aid is None:
                    # Cold start: we have nothing in memory and nothing valid
                    # on disk. Mark as empty so we don't keep retrying inside
                    # the same request; the next call will retry the download.
                    cls._set_map({})
                return

            # H6: Sanity-check the parsed map. A "successful" parse that
            # yields <100 entries means the upstream gave us something
            # structurally valid but semantically empty (maintenance page
            # disguised as JSON, etc.). Keep the previous in-memory copy.
            if len(parsed) < 100:
                logger.info(f"anime_mappings: parsed only {len(parsed)} entries (suspect); keeping previous cache.")
                if cls._by_aid is None:
                    cls._set_map(parsed)  # take what we got; better than nothing
                return

            cls._set_map(parsed)

    @classmethod
    async def _download(cls, client: httpx.AsyncClient | None) -> None:
        """Atomic download with content-length sanity guard.

        H6: Validate the response body BEFORE atomically replacing the
        live file. Catches truncated downloads (network drop mid-stream),
        upstream error pages (GitHub serves HTML 5xx), and stub responses
        from misconfigured proxies. On any validation failure we keep
        whatever copy was already on disk — better stale than corrupt.
        """
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient()
        try:
            # KI-13: GitHub's raw CDN (raw.githubusercontent.com) throttles
            # or 403s the default `python-httpx/x.y` UA when aggregate
            # traffic from a single IP spikes. Anidb.py learned this the
            # hard way; the Fribb path was overlooked when the defensive
            # header was added there. A failure here on a fresh install
            # (no disk cache yet) means zero anime cross-reference data,
            # which breaks AniDB → TVDB/TMDB poster cross-ref AND the
            # Fribb-season override for anime AND the anime-rerank
            # pipeline. Same User-Agent constant as AniDB so the two
            # external surfaces stay in lockstep.
            r = await client.get(
                _MAPPING_URL,
                headers={
                    "User-Agent": KIRA_USER_AGENT,
                    "Accept-Encoding": "gzip",
                },
                timeout=60.0,
                follow_redirects=True,
            )
            r.raise_for_status()
            body = r.content

            # Sanity guards before we touch the live file.
            if len(body) < _MIN_DUMP_BYTES:
                raise RuntimeError(
                    f"Fribb dump too small ({len(body):,} bytes < {_MIN_DUMP_BYTES:,}); "
                    f"refusing to overwrite cached copy."
                )
            if not body.lstrip().startswith(b"["):
                raise RuntimeError(
                    f"Fribb dump not JSON array (starts with {body[:32]!r}); "
                    f"refusing to overwrite cached copy."
                )

            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            # Atomic write — os.replace is atomic on both POSIX and Windows
            # for same-filesystem moves.
            tmp_file = _MAPPING_FILE.with_suffix(".tmp")
            tmp_file.write_bytes(body)
            tmp_file.replace(_MAPPING_FILE)
        finally:
            if owns_client:
                await client.aclose()

    @classmethod
    async def get(cls, aid: int) -> dict[str, Any] | None:
        """Return the mapping entry for an AID, or None if unknown."""
        await cls._ensure_loaded()
        return (cls._by_aid or {}).get(aid)

    @classmethod
    async def tvdb_id(cls, aid: int) -> int | None:
        e = await cls.get(aid)
        if not e:
            return None
        v = e.get("tvdb_id")
        if isinstance(v, int) and v > 0:
            return v
        return None

    @classmethod
    async def tmdb_tv_id(cls, aid: int) -> int | None:
        e = await cls.get(aid)
        if not e:
            return None
        tm = e.get("themoviedb_id")
        if isinstance(tm, dict):
            tv = tm.get("tv")
            if isinstance(tv, int) and tv > 0:
                return tv
        elif isinstance(tm, int) and tm > 0:
            return tm
        return None

    @classmethod
    async def tvdb_season(cls, aid: int) -> int | None:
        """Some AIDs map to a specific season inside a TVDB series. Returns
        that season number when the mapping carries it (e.g. R-A-G S2 → TVDB
        series 380654 season 2)."""
        e = await cls.get(aid)
        if not e:
            return None
        s = e.get("season", {})
        if isinstance(s, dict):
            v = s.get("tvdb")
            if isinstance(v, int):
                return v
        return None

    @classmethod
    async def aid_by_tvdb(cls, tvdb_id: int) -> int | None:
        """Reverse lookup: ANY AID whose Fribb mapping has this tvdb_id.
        Returns the first match (caller doesn't care which AID specifically;
        it just wants to confirm "this TVDB series is a known anime").

        Used to filter cross-provider fallback for anime: a TVDB hit with
        NO matching AID is almost certainly live-action and shouldn't be
        served as an "anime" match. A TVDB hit WITH an AID is in the Fribb
        anime cross-ref and is legitimate.
        """
        await cls._ensure_loaded()
        lst = cls._by_tvdb.get(tvdb_id)
        return lst[0] if lst else None

    @classmethod
    async def aids_by_tvdb(cls, tvdb_id: int) -> list[int]:
        """Every AID Fribb maps to this TVDB series id, across ALL seasons.

        Unlike `aids_by_tvdb_season` (just one season's cours), this spans the
        whole franchise — S1 + S2 + … + the Final Season's parts. Used by
        EpisodeCountSanityMetric to recognize that an ABSOLUTE-numbered cluster
        (whose max episode is a series-wide absolute index, e.g. AoT's 89) is
        covered by the franchise's full absolute span, so a tail cour isn't
        wrongly vetoed for being "too small to hold episode 89."
        """
        await cls._ensure_loaded()
        return list(cls._by_tvdb.get(tvdb_id, ()))

    @classmethod
    async def aid_by_tmdb_tv(cls, tmdb_tv_id: int) -> int | None:
        """Same idea as aid_by_tvdb but for TMDB TV IDs."""
        await cls._ensure_loaded()
        lst = cls._by_tmdb_tv.get(tmdb_tv_id)
        return lst[0] if lst else None

    @classmethod
    async def aid_by_tvdb_season(cls, tvdb_id: int, season: int) -> int | None:
        """Reverse lookup: given a TVDB series ID and a season number, return
        the AniDB AID that Fribb says maps to that exact (series, season)
        pair. Returns None if no Fribb entry pins this combination.

        Critical for the offline misroute heal: when a Bleach S17 file got
        misrouted to AID 2369 (the umbrella entry, Fribb season=None) but
        AID 15449 exists with Fribb season=17, this lookup finds AID 15449
        without making any provider HTTP call. Lets us correct AID
        assignments even while AniDB is IP-banned.
        """
        await cls._ensure_loaded()
        lst = cls._by_tvdb_season.get((tvdb_id, season))
        return lst[0] if lst else None

    @classmethod
    async def aids_by_tvdb_season(cls, tvdb_id: int, season: int) -> list[int]:
        """Return EVERY AID whose Fribb mapping pins it to (tvdb_id, season).

        The single-AID `aid_by_tvdb_season` returns just the first match,
        which collapses multi-cour TVDB seasons (Bleach TYBW Cour 1/2/3
        all map to tvdb=74796, season=17) into a single answer. For the
        EpisodeCountSanityMetric's summed-sibling check we need them ALL
        so we can aggregate episode counts across the franchise season.

        Returns the sorted list (ascending AID — usually equates to
        chronological cour order, which the bipartite refinement / per-
        file routing pass relies on for episode-range assignment).
        Empty list if nothing matches.
        """
        await cls._ensure_loaded()
        return sorted(cls._by_tvdb_season.get((tvdb_id, season), ()))
