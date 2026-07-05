"""AniDB provider — read-only access to anime metadata.

Search strategy: AniDB has no /search endpoint. We download their
`anime-titles.xml.gz` dump (~5 MB, refreshed daily) and build an in-memory
title→AID index. All search hits the local index — no rate limit.

Details fetch (used when a candidate is selected for full info) goes through
AniDB's HTTP API at api.anidb.net:9001/httpapi, throttled to 1 req / 4s via
an asyncio.Semaphore + delay (per AniDB's strict client guidelines).

Auth: read-only HTTP API needs only a registered `client` name + `clientver`.
No user/password required. Personal-list features (mylist) use the UDP API
and would need user creds — deferred.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, ClassVar

import httpx
from cachetools import TTLCache

# PB-1: defusedxml is a drop-in replacement for xml.etree.ElementTree that
# rejects XXE / entity-expansion / external-entity attacks. We parse XML
# fetched from AniDB's CDN — even though AniDB itself is benign, MITM /
# compromised cache layers could inject malicious entities to exfiltrate
# local files (`file:///etc/passwd`) or pin our CPU (billion-laughs).
# The stdlib parser is explicitly documented as vulnerable to this class
# of attack; defusedxml shares the same API surface so the swap is local.
# We import as `ET` to keep the call sites (`ET.fromstring`, `ET.parse`,
# `ET.ParseError`, type hints) identical.
import defusedxml.ElementTree as ET
from xml.etree.ElementTree import Element  # noqa: E402  # type alias only — no parsing

from kira.matcher.acronyms import KNOWN_ACRONYMS, acronym_forms, is_acronym_shaped
from kira.matcher.similarity import normalize, trigram_similarity
from kira.providers.base import (
    KIRA_USER_AGENT,
    EpisodeResult,
    MetadataProvider,
    MovieResult,
    ProviderAuth,
    ProviderKey,
    TVResult,
)

logger = logging.getLogger(__name__)

# Local alias preserves existing `_USER_AGENT` call sites unchanged.
# See KI-13 in the plan's Known Issues for the hoist rationale: AniDB
# rejected the default `python-httpx` UA with a 403, and so does the
# GitHub raw CDN that serves anime_mappings.py's Fribb dump. Same
# defensive header now used in both places via the shared constant.
_USER_AGENT = KIRA_USER_AGENT

# PB-1: dedicated logger for AniDB telemetry. Default handler routes to
# stderr at INFO level via uvicorn; ops can route this to a sink (file,
# fluent-bit, structured forwarder) via standard logging config without
# touching code. Format is JSON per event — see `_http_api` for shape.
_anidb_log = logging.getLogger("kira.anidb")

# Cache directory. Defaults to a repo-local .cache/ (git-ignored) for dev, but
# honors KIRA_CACHE_DIR so a container can point it at the PERSISTED /config
# volume. This matters: the AniDB title dump + the `anidb-relations.json`
# franchise sequel-chains live here, and the relations cache is what folds all
# seasons of a show into ONE library card. If the cache sits on the container's
# ephemeral layer, every image rebuild wipes it → Kira must re-walk every
# franchise via live AniDB calls → a library-wide re-match trips AniDB's
# rate-limit ban → the walk falls back to per-season ids → season grouping
# silently breaks until it heals. Persisting it under /config prevents that.
from kira.config import cache_dir as _kira_cache_dir
_CACHE_DIR = _kira_cache_dir()
_TITLES_PATH = _CACHE_DIR / "anidb-titles.xml.gz"
_TITLES_URL = "https://anidb.net/api/anime-titles.xml.gz"
_TITLE_MAX_AGE_SEC = 24 * 3600  # AniDB regenerates daily; refresh same cadence.

# AniDB docs say 1 req / 2s, but real-world experience says network jitter
# makes a 2-second client-side gap insufficient: two requests sent 2s apart
# can arrive at the server <2s apart and trigger an instant 12h IP ban.
# Their own moderators recommend a 4s minimum; we use 5s as additional
# safety margin since we'd rather scan slowly than burn 12h on every ban.
# Every HTTP API call serializes through `_api_lock` + this delay AND
# the cross-process disk timestamp (see _http_api), so this floor is
# enforced both within a process and across uvicorn workers.
_API_DELAY_SEC = 5.0

# Circuit-breaker thresholds. If we see >= N AniDB-side errors (5xx, 4xx
# 'banned', client-version rejections) within ERROR_WINDOW seconds, the
# circuit OPENS and refuses outgoing requests for CIRCUIT_OPEN_SEC. This
# prevents the "death spiral" where a transient AniDB hiccup makes us
# fire dozens of doomed retries that each individually contribute to a
# real ban.
_ERROR_WINDOW_SEC = 60.0
_ERROR_THRESHOLD = 3
_CIRCUIT_OPEN_SEC = 300.0  # 5 min cool-down after circuit trips

# AniDB auto-fills an episode's ENGLISH title with the literal "Episode <num>"
# (the absolute number) until someone adds a real localized one — a zero-info
# placeholder. For a freshly-aired episode that means the English title is just
# "Episode 1166" while the real romaji/Japanese title already exists. So when the
# English title is exactly this episode's placeholder, prefer romaji/native.
_XML_LANG_NS = "{http://www.w3.org/XML/1998/namespace}lang"
_EP_TITLE_PLACEHOLDER = re.compile(r"(?i)\s*episode\s+0*(\d+)\s*$")


def _select_episode_title(ep, num: int) -> str | None:
    """Best title from an AniDB ``<episode>`` for episode number ``num``.

    Prefer a REAL English title; fall back to romaji (x-jat) → native (ja) when
    English is missing OR is just AniDB's auto-filled "Episode <num>" placeholder
    — so a brand-new episode AniDB only carries in Japanese still shows its actual
    title instead of "Episode 1166". The placeholder (or the first title present)
    is used only as a last resort, so the episode is never left untitled (which
    would drop it from the popup). Older episodes WITH a real English title are
    unaffected — they take the English branch as before."""
    en = jat = ja = first = None
    for cand in ep.findall("title"):
        t = (cand.text or "").strip() or None
        if not t:
            continue
        if first is None:
            first = t
        lang = (cand.get(_XML_LANG_NS) or "").lower()
        if lang == "en" and en is None:
            en = t
        elif lang == "x-jat" and jat is None:
            jat = t
        elif lang == "ja" and ja is None:
            ja = t
    m = _EP_TITLE_PLACEHOLDER.fullmatch(en) if en else None
    en_is_placeholder = m is not None and int(m.group(1)) == num
    if en and not en_is_placeholder:
        return en
    return jat or ja or en or first


class AniDBProvider(MetadataProvider):
    key: ClassVar[ProviderKey] = "anidb"

    # Class-level index so multiple provider instances share one parse.
    # aid → [(type, lang, title), …]. Refactored from a 2-tuple shape when
    # we added language-aware display-title picking; helper consumers
    # (get_titles_for_aid, search_tv) unpack the 3-tuple.
    _titles: ClassVar[dict[int, list[tuple[str, str, str]]] | None] = None
    _title_index: ClassVar[list[tuple[int, str, str]] | None] = None    # [(aid, normalized, original), ...]
    # M2 offline prefilter indices, built alongside `_title_index` in
    # `_parse_titles`. Both map a key → set of AIDs for instant lookup with no
    # network and no full-scan; they make acronym-only / exact-name filenames
    # resolvable even while the AniDB HTTP API is banned.
    _name_index: ClassVar[dict[str, set[int]] | None] = None       # normalized title → {aid}
    _acronym_index: ClassVar[dict[str, set[int]] | None] = None    # initialism (3-6 chars) → {aid}
    _index_lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _api_lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    # Wall-clock timestamp of the last HTTP-API call BY ANY WORKER.
    # Persisted to .cache/anidb-last-call.txt and re-read inside the critical
    # section so multi-worker deployments share the 4s budget. Without this,
    # each uvicorn worker had its own `_last_api_call` and could fire calls
    # 1-2s apart cross-worker — silent path to a 12h IP ban.
    _LAST_CALL_PATH: ClassVar[Path] = _CACHE_DIR / "anidb-last-call.txt"

    @classmethod
    def _read_last_call_wallclock(cls) -> float:
        """Best-effort cross-process last-call timestamp (Unix seconds).

        Returns 0.0 on missing/corrupt file. R2-H1: callers now WRITE
        a future `fire_at` (claimed slot) so reads can legitimately
        return a value > now — that's the next-fire reservation, not
        garbage. We only filter out values absurdly far in the future
        (>60s ahead, suggesting clock skew or snapshot rollback).
        """
        try:
            ts = float(cls._LAST_CALL_PATH.read_text().strip())
        except (OSError, ValueError):
            return 0.0
        # Bogus far-future timestamp (clock skew / snapshot rollback).
        # 60s tolerance lets the legitimate "I claimed the next slot"
        # case (max ~4s in the future) through.
        if ts > time.time() + 60:
            return 0.0
        return ts

    @classmethod
    def _write_last_call_wallclock(cls, ts: float | None = None) -> None:
        """Atomic-replace write of a timestamp. Safe under concurrent writers.

        `ts` defaults to `time.time()` (call-actually-fired semantics).
        Pass an explicit timestamp to write a CLAIMED future fire-time
        (R2-H1 cross-process slot reservation). Either way `.tmp +
        os.replace` makes the swap atomic so torn writes can't corrupt
        the live file.
        """
        import os
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = cls._LAST_CALL_PATH.with_suffix(".txt.tmp")
        when = time.time() if ts is None else ts
        try:
            tmp.write_text(f"{when:.3f}")
            os.replace(str(tmp), str(cls._LAST_CALL_PATH))
        except OSError as e:
            # Disk full / permission denied / read-only mount — don't crash
            # the AniDB call; we just lose the inter-worker rate guarantee
            # for this one request. Better than blowing up the whole scan.
            # Log it though: a RECURRING failure here means the 4s rate guard
            # is silently off, which is exactly what precedes a 12h AniDB ban.
            logger.warning(f"anidb: rate-guard timestamp write failed (rate guarantee lost this call): {e!r}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def __init__(self, base_url: str, auth: ProviderAuth, client: httpx.AsyncClient):
        super().__init__(base_url=base_url, auth=auth, client=client)
        # Pull the optional client name / version out of the auth credentials
        # bag — falls back to sensible defaults so v1 works out of the box.
        creds = self.auth.credentials or {}
        self._client_name = creds.get("client") or "kira"
        self._client_ver = creds.get("clientver") or "1"
        # Cross-reference providers (TVDB, TMDB) used by get_picture_url for
        # poster fallback. Built lazily ONCE per AniDB instance and reused —
        # otherwise every poster lookup would build a fresh TVDBProvider
        # with an empty token cache, triggering a TVDB /login per call.
        self._xref_providers: dict[str, MetadataProvider | None] = {}

    # ── Title index management ────────────────────────────────────────────
    async def _ensure_index(self) -> None:
        """Download + parse the title dump if we don't have it or it's stale."""
        if AniDBProvider._title_index is not None and self._fresh():
            return
        async with AniDBProvider._index_lock:
            if AniDBProvider._title_index is not None and self._fresh():
                return
            if not _TITLES_PATH.exists() or not self._fresh():
                await self._download_titles()
            # 30 MB+ XML parse — push it off the event loop so WebSocket
            # connections and other coroutines stay responsive during scan.
            await asyncio.to_thread(self._parse_titles)

    def _fresh(self) -> bool:
        if not _TITLES_PATH.exists():
            return False
        return (time.time() - _TITLES_PATH.stat().st_mtime) < _TITLE_MAX_AGE_SEC

    async def _download_titles(self) -> None:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # AniDB blocks default Python user-agents with 403 — must identify as
        # a real client. The dump host also throttles aggressively, so one
        # attempt with a generous timeout.
        r = await self.client.get(
            _TITLES_URL,
            headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip"},
            timeout=60.0,
            follow_redirects=True,
        )
        r.raise_for_status()
        _TITLES_PATH.write_bytes(r.content)

    def _parse_titles(self) -> None:
        """Decompress + walk the XML, building two structures:
        - `_titles[aid]` → list of (type, lang, title). `lang` is the
          `xml:lang` code from the dump (en, ja, x-jat for romaji, ms,
          it, …). Stored so display picking can prefer English.
        - `_title_index` → flat list of (aid, normalized_title, original_title)
          for fuzzy search.
        """
        with gzip.open(_TITLES_PATH, "rb") as f:
            tree = ET.parse(f)

        XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
        # Cap AIDs per acronym so a common 3-letter initialism doesn't accrue a
        # giant list. The injection in search_tv only matters when the right AID
        # is in here; a curated acronym resolves precisely via the expansion
        # trigram instead, so this generated index is the long-tail fallback.
        _ACRO_CAP = 60
        titles: dict[int, list[tuple[str, str, str]]] = {}
        index: list[tuple[int, str, str]] = []
        name_index: dict[str, set[int]] = {}
        acro_index: dict[str, set[int]] = {}
        for anime in tree.getroot().findall("anime"):
            aid_attr = anime.get("aid")
            if not aid_attr:
                continue
            try:
                aid = int(aid_attr)
            except ValueError:
                continue
            entries: list[tuple[str, str, str]] = []
            for title_el in anime.findall("title"):
                ttype = title_el.get("type") or "syn"
                if ttype not in ("main", "official", "syn", "short"):
                    continue
                text = (title_el.text or "").strip()
                if not text:
                    continue
                lang = (title_el.get(XML_LANG) or "").lower()
                entries.append((ttype, lang, text))
                normalized = normalize(text)
                if normalized:
                    index.append((aid, normalized, text))
                    name_index.setdefault(normalized, set()).add(aid)
                    # Generated initialisms (3-6 chars) → AID, for acronym-only
                    # filenames the trigram scan can't catch ("JJK" vs the
                    # romaji "Jujutsu Kaisen" shares no trigrams).
                    for form in acronym_forms(normalized):
                        if 3 <= len(form) <= 6:
                            bucket = acro_index.setdefault(form, set())
                            if len(bucket) < _ACRO_CAP:
                                bucket.add(aid)
            if entries:
                titles[aid] = entries

        AniDBProvider._titles = titles
        AniDBProvider._title_index = index
        AniDBProvider._name_index = name_index
        AniDBProvider._acronym_index = acro_index

    # ── Public search API ─────────────────────────────────────────────────
    async def search_movie(self, title: str, year: int | None = None) -> list[MovieResult]:
        # AniDB has anime movies too (e.g. Studio Ghibli), but our pipeline
        # routes movies to TMDB/TVDB. Provide a stub so the engine doesn't
        # crash if it accidentally calls this.
        return []

    async def search_tv(self, title: str, year: int | None = None) -> list[TVResult]:
        """Fuzzy title search against the local AniDB title index.

        M2 offline prefilter: when the query looks like an acronym ("JJK",
        "AoT") the plain trigram scan finds nothing — an initialism shares no
        trigrams with the romaji title. So we also consult the offline indices:
          - exact normalized-name → AID (guarantees inclusion),
          - a curated acronym → its full expansion, trigram-scanned + exact-name,
          - a non-curated acronym → the generated-initialism index.
        All in-memory, no HTTP — works even while the AniDB API is banned. The
        cascade (AcronymMetric / Substring / Fribb) decides the real
        confidence, so an injected candidate can never auto-commit on its own.
        """
        await self._ensure_index()
        if not AniDBProvider._title_index or not title.strip():
            return []

        q_norm = normalize(title)
        if not q_norm:
            return []

        # Acronym handling. A CURATED acronym expands to its full title so we can
        # trigram against it directly (precise); a non-curated acronym falls back
        # to the generated-initialism index (best-effort).
        acro = is_acronym_shaped(q_norm)
        is_known = acro and q_norm in KNOWN_ACRONYMS
        exp_norm = normalize(KNOWN_ACRONYMS[q_norm]) if is_known else None

        # Score every indexed title against the query. Group by aid so the
        # same anime doesn't appear multiple times.
        per_aid: dict[int, tuple[float, str]] = {}
        for aid, t_norm, t_orig in AniDBProvider._title_index:
            score = trigram_similarity(q_norm, t_norm)
            if exp_norm:
                score = max(score, trigram_similarity(exp_norm, t_norm))
            if score <= 0.15:  # cheap floor — skip obvious non-matches
                continue
            prev = per_aid.get(aid)
            if prev is None or score > prev[0]:
                per_aid[aid] = (score, t_orig)

        # ── Offline index injections ────────────────────────────────────────
        def _inject(aid: int, floor: float) -> None:
            prev = per_aid.get(aid)
            if prev is None or floor > prev[0]:
                per_aid[aid] = (floor, self._pick_display_title(aid) or "")

        name_idx = AniDBProvider._name_index or {}
        # Exact normalized-name hit → perfect; guarantee it's in the candidate
        # set even past the top-10 trigram cut.
        for aid in name_idx.get(q_norm, ()):
            _inject(aid, 1.0)
        if exp_norm:
            # Curated acronym whose expansion maps to an exact title → strong.
            for aid in name_idx.get(exp_norm, ()):
                _inject(aid, 0.95)
        elif acro:
            # Non-curated acronym → generated-initialism index, modest floor.
            for aid in (AniDBProvider._acronym_index or {}).get(q_norm, ()):
                _inject(aid, 0.5)

        # Sort and take top 10 — matcher.score_match adds its own confidence.
        ranked = sorted(per_aid.items(), key=lambda x: x[1][0], reverse=True)[:10]

        results: list[TVResult] = []
        for aid, (_score, best_title) in ranked:
            display = self._pick_display_title(aid) or best_title
            aliases = [t for _, _lang, t in (AniDBProvider._titles or {}).get(aid, [])]
            results.append(TVResult(
                provider="anidb",
                provider_id=str(aid),
                title=display,
                year=None,                          # not in the title dump; details fetch supplies it
                overview=None,
                poster_url=None,
                popularity=None,
                aliases=aliases,
                original_country="jpn",            # everything in AniDB is JP-origin
                original_language="jpn",
            ))
        return results

    @classmethod
    def _pick_display_title(cls, aid: int) -> str | None:
        """Pick the most user-friendly title from the dump.

        AniDB stores titles in 20+ languages. Priority:
          1. official + en      (English release title — what most users know)
          2. main + x-jat       (canonical romaji like "Sousou no Frieren")
          3. official + x-jat
          4. official + ja      (Japanese, only if no romaji)
          5. syn + en
          6. anything (first one wins)

        Without this, AniDB's title-dump order leaks through and we end up
        showing Malay/Italian/Polish titles to English-speaking users.

        Promoted to classmethod so callers outside the provider (e.g. the
        matcher's Phase-4 AID reroute, the rename pipeline's franchise
        collapse) can pull a title without holding a provider instance.
        """
        entries = (cls._titles or {}).get(aid, [])
        if not entries:
            return None
        priorities = [
            ("official", "en"),
            ("main",     "x-jat"),
            ("official", "x-jat"),
            ("official", "ja"),
            ("syn",      "en"),
        ]
        for want_type, want_lang in priorities:
            for ttype, lang, title in entries:
                if ttype == want_type and lang == want_lang:
                    return title
        return entries[0][2]

    def get_display_extras(self, aid: str) -> dict[str, Any]:
        """Return romaji / native / alt titles for an AID, all from memory.

        No HTTP call — reads the already-loaded title dump. Used by the
        matcher to populate hero metadata on AniDB matches without
        spending an AniDB rate-limited request.

        Romaji = the x-jat (Japanese in Latin alphabet) title — what
        most international fans know the show by ("Sousou no Frieren").
        Native = the ja title in kana / kanji ("葬送のフリーニ").
        Alt titles = English variants beyond the primary display, plus
        a handful of other Latin-script languages, deduplicated.
        """
        try:
            aid_i = int(aid)
        except (ValueError, TypeError):
            return {}
        entries = (AniDBProvider._titles or {}).get(aid_i, [])
        if not entries:
            return {}

        # Title type priority — AniDB ranks them: main (primary romaji) >
        # official (per-language official title) > syn (synonym) > short
        # (abbreviation). Picking the first matching language without
        # this priority gave us "Bleach Season 2" (a synonym) instead of
        # "Bleach Sennen Kessen-hen" (the official x-jat). Sort by rank
        # before scanning so the canonical picks win deterministically.
        _T_RANK = {"main": 0, "official": 1, "syn": 2, "short": 3}
        ordered = sorted(entries, key=lambda e: _T_RANK.get(e[0], 9))

        romaji: str | None = None
        native: str | None = None
        alts: list[str] = []
        for ttype, lang, title in ordered:
            if not title:
                continue
            if romaji is None and lang == "x-jat":
                romaji = title
                continue
            if native is None and lang == "ja":
                native = title
                continue
            # Surface a small number of distinct alternates — most useful are
            # other official English/regional titles different from the display.
            if ttype in ("official", "main") and lang in ("en", "ko", "zh-hans", "zh-hant", "fr", "de", "it", "es"):
                if title not in alts:
                    alts.append(title)
        return {
            "title_romaji": romaji,
            "title_native": native,
            "alt_titles": alts[:6],  # cap for the hero "a.k.a." row
            # AniDB anime are always JP origin.
            "original_country": "jpn",
            "original_language": "jpn",
        }


# Legacy class-level entries shape was (type, title); some external callers
# may still expect that. New consumers use the (type, lang, title) shape.

    # Cache parsed episode lists per (AID, include_specials). AniDB's HTTP API
    # is rate-limited (5s gate) and One Piece-scale series return 1100+ episodes
    # — re-fetching on every popup open / cluster match was the "popup takes
    # forever" cause. 6h TTL lets newly-aired episodes appear without a restart.
    # Only successful (non-empty) parses are cached.
    _episodes_cache: ClassVar[TTLCache] = TTLCache(maxsize=512, ttl=6 * 3600)

    async def get_episodes(
        self, series_id: str, season: int, include_specials: bool = False,
        order: str = "default",
    ) -> list[EpisodeResult]:
        """Fetch full anime details from AniDB HTTP API. Rate-limited.

        `order` is a no-op (AniDB has a single episode ordering) — present
        only to satisfy the shared provider signature.

        AniDB has no season concept; regular `<epno type="1">` episodes come
        back tagged season=1. type=3 (credit/OP/ED), type=4 (trailer),
        type=5 (parody), type=6 (other) are always skipped — they'd only
        confuse episode-title assignment.

        Phase 2: when `include_specials=True`, type=2 (specials) are ALSO
        returned, tagged season=0 (Plex/Jellyfin "Specials" convention).
        AniDB numbers specials as `S1`, `S2`, … in `<epno>` — we strip the
        leading letter and keep the integer. Defaults False so the scan /
        cluster path is unchanged; the `/series` endpoint opts in for
        season-0 cards.
        """
        cache_key = (str(series_id), bool(include_specials))
        hit = AniDBProvider._episodes_cache.get(cache_key)
        if hit is not None:
            return hit
        data = await self._http_api(series_id)
        if not data:
            return []
        out: list[EpisodeResult] = []
        regular_count = 0
        for ep in data.findall(".//episode"):
            num_el = ep.find("epno")
            if num_el is None:
                continue
            eptype = num_el.get("type") or "1"
            raw = (num_el.text or "").strip()
            if not raw:
                continue
            # Regular episode (type 1) → season 1. Special (type 2) → season 0
            # only when the caller opted in. Everything else (OP/ED, trailers,
            # parodies) is dropped.
            if eptype == "1":
                try:
                    num = int(raw)
                except ValueError:
                    continue
                regular_count += 1
                ep_season = 1
            elif include_specials and eptype == "2":
                # Specials carry "S1"/"S2" epno — keep the trailing digits.
                digits = re.sub(r"\D", "", raw)
                if not digits:
                    continue
                num = int(digits)
                ep_season = 0
            else:
                continue

            # Best title — prefers a real one over AniDB's "Episode <num>"
            # English placeholder (see _select_episode_title).
            title = _select_episode_title(ep, num)

            air_el = ep.find("airdate")
            # AniDB stores per-episode runtime as <length>NN</length> in minutes.
            length_el = ep.find("length")
            runtime: int | None = None
            if length_el is not None and length_el.text:
                try:
                    runtime = int(length_el.text.strip())
                except ValueError:
                    runtime = None
            out.append(EpisodeResult(
                provider="anidb",
                series_id=series_id,
                season=ep_season,
                episode=num,
                title=title,
                air_date=(air_el.text.strip() if air_el is not None and air_el.text else None),
                runtime=runtime,
            ))
        # Sort regulars (season 1) before specials (season 0)? No — keep
        # numeric (season, episode) so specials (season 0) sort first; the
        # frontend groups by season anyway. Within a season, ascending.
        out.sort(key=lambda e: (e.season, e.episode))

        # Write-through to the per-AID episode-count cache. This is the
        # data Phase 3's franchise-offset builder needs; piggybacking
        # on the existing get_episodes call means most franchise members
        # already have counts cached by the time `get_franchise_offsets`
        # runs — no extra AniDB HTTP for them. Count REGULAR episodes only —
        # specials must never inflate the absolute-range arithmetic.
        try:
            await AniDBProvider._record_episode_count(int(series_id), regular_count)
        except (ValueError, TypeError):
            pass

        AniDBProvider._episodes_cache[cache_key] = out
        return out

    # ── Episode-count cache — populated by get_episodes write-through
    # AND by explicit get_episode_count calls. Keyed by AID (int), stored
    # as count (int). Used by get_franchise_offsets to compute absolute-
    # episode ranges per franchise member without re-fetching the full
    # episode list each time.
    _EP_COUNT_CACHE_PATH: ClassVar[Path] = _CACHE_DIR / "anidb-episode-counts.json"
    _ep_count_cache: ClassVar[dict[int, int] | None] = None
    _ep_count_cache_lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    @classmethod
    def _load_ep_count_cache(cls) -> dict[int, int]:
        if cls._ep_count_cache is not None:
            return cls._ep_count_cache
        import json
        if cls._EP_COUNT_CACHE_PATH.exists():
            try:
                raw = json.loads(cls._EP_COUNT_CACHE_PATH.read_text())
                # JSON keys come back as strings — coerce to int.
                cls._ep_count_cache = {int(k): int(v) for k, v in raw.items()}
            except Exception:
                cls._ep_count_cache = {}
        else:
            cls._ep_count_cache = {}
        return cls._ep_count_cache

    @classmethod
    def _save_ep_count_cache(cls) -> None:
        import json
        if cls._ep_count_cache is None:
            return
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # JSON object keys must be strings.
        serializable = {str(k): v for k, v in cls._ep_count_cache.items()}
        cls._EP_COUNT_CACHE_PATH.write_text(json.dumps(serializable))

    @classmethod
    async def _record_episode_count(cls, aid: int, count: int) -> None:
        """Update the cache for one AID and flush to disk."""
        # Hold the lock across the read-check-mutate-AND-save. Concurrent cour
        # fetches call this in parallel; mutating the shared dict outside the
        # lock (as before) raced the `_save_ep_count_cache` snapshot iteration
        # ("dict changed size during iteration") and could drop a just-fetched
        # count when two writers serialized partial views.
        async with cls._ep_count_cache_lock:
            cache = cls._load_ep_count_cache()
            if cache.get(aid) == count:
                return  # no change, skip the write
            cache[aid] = count
            await asyncio.to_thread(cls._save_ep_count_cache)

    async def get_episode_count(self, aid: int | str) -> int | None:
        """Return the number of regular episodes for an AID.

        Cache hit → instant dict read.
        Cache miss → one rate-limited AniDB HTTP call (same chokepoint as
        get_episodes; 4s gap enforced via _api_lock). Result cached.
        Banned / error → returns None, does NOT cache (lets caller retry).
        """
        try:
            aid_i = int(aid)
        except (ValueError, TypeError):
            return None
        cache = self._load_ep_count_cache()
        if aid_i in cache:
            return cache[aid_i]
        # Cache miss — fetch via the rate-limited path.
        data = await self._http_api(str(aid_i))
        if data is None:
            return None
        count = sum(
            1 for ep in data.findall(".//episode")
            if (ep.find("epno") is not None
                and (ep.find("epno").get("type") or "1") == "1"
                and (ep.find("epno").text or "").strip().isdigit())
        )
        await AniDBProvider._record_episode_count(aid_i, count)
        return count

    # ── Sequel/prequel chain — walks AniDB's <relatedanime> block so a
    # franchise like Rent-a-Girlfriend (5 separate AIDs for its 5 seasons)
    # can be presented as a single grouped franchise on the Review page.
    _RELATIONS_CACHE_PATH: ClassVar[Path] = _CACHE_DIR / "anidb-relations.json"
    # Maps AID → sorted list of all AIDs in the franchise (including self).
    _relations_cache: ClassVar[dict[str, list[int]] | None] = None
    _relations_cache_lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    # Relation types we treat as "same franchise". AniDB also exposes
    # `side_story`, `alternative_setting`, `same_setting`, `parent_story`,
    # `full_story` — we include parent_story since some seasons are listed
    # that way, but exclude side stories / alternates which the user usually
    # wants as their own card.
    _FRANCHISE_RELATIONS: ClassVar[set[str]] = {"sequel", "prequel", "parent_story", "full_story"}

    @classmethod
    def _load_relations_cache(cls) -> dict[str, list[int]]:
        if cls._relations_cache is not None:
            return cls._relations_cache
        import json
        if cls._RELATIONS_CACHE_PATH.exists():
            try:
                cls._relations_cache = json.loads(cls._RELATIONS_CACHE_PATH.read_text())
            except Exception:
                cls._relations_cache = {}
        else:
            cls._relations_cache = {}
        return cls._relations_cache

    @classmethod
    def _save_relations_cache(cls) -> None:
        import json
        if cls._relations_cache is None:
            return
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cls._RELATIONS_CACHE_PATH.write_text(json.dumps(cls._relations_cache))

    async def get_related_aids(self, aid: str) -> list[int]:
        """Return every AID in this anime's sequel/prequel franchise (incl. self).

        Walks `<relatedanime>` transitively — fetching each related AID's own
        relations and following them until the closure is reached. Visited
        set prevents infinite loops (some franchises are cyclic).

        Each `_http_api` call is rate-limited to 1 req / 4s. A 5-season
        franchise costs ~5 calls the first time (~20s) and is then cached
        on disk forever; subsequent lookups are O(1).

        Returns the sorted list — by convention the lowest AID is the
        canonical "season 1" of the franchise, which is what we key off.
        """
        cache = self._load_relations_cache()
        if aid in cache:
            return cache[aid]

        # AniDB banned → don't walk. Every _http_api hop below already no-ops to
        # None under the ban, so the walk can only ever collapse to the seed AID
        # anyway. Bail to cache-only: any franchise we've previously resolved is
        # returned from the cache check above and STILL groups correctly while
        # banned; a never-before-seen franchise just can't be resolved until the
        # ban lifts (impossible without the API). This skips the pointless,
        # 5s-gated no-op loop so a re-match under a ban stays fast and quiet.
        if AniDBProvider.is_banned():
            return []

        visited: set[int] = set()
        queue: list[str] = [aid]
        # ── KI-8: skip-and-continue on a missing related AID ───────────
        # Pre-fix: a single dead/banned/timed-out related AID broke out
        # of the traversal entirely, leaving the franchise resolved to
        # just the seed AID. Old/sprawling franchises with 50-80 AIDs
        # (Gundam, Pretty Cure, the early-2000s long-runners) routinely
        # have one bad relation that nuked the whole group.
        # Post-fix: log + continue, so a single bad relation no longer
        # poisons the whole chain. We DO still hard-fail if the SEED
        # AID itself can't be fetched — without the seed we can't trust
        # anything we're inferring.
        # Pattern D — Cache-Completeness Invariant: when ANY related
        # AID was skipped, return the partial walk to the caller but
        # DON'T persist to the relations cache. Subsequent calls retry
        # the missing siblings instead of hitting a stale-partial entry.
        try:
            seed_i = int(aid)
        except (TypeError, ValueError):
            seed_i = None
        seed_failed = False
        had_failures = False
        while queue:
            current = queue.pop()
            try:
                current_i = int(current)
            except ValueError:
                continue
            if current_i in visited:
                continue
            visited.add(current_i)
            data = await self._http_api(current)
            if data is None:
                _anidb_log.warning(json.dumps({
                    "evt": "anidb_related_skip",
                    "seed": aid,
                    "missing_aid": current,
                }))
                if seed_i is not None and current_i == seed_i:
                    # Seed itself failed — entire result is unreliable.
                    seed_failed = True
                    break
                had_failures = True
                continue
            related_block = data.find("relatedanime")
            if related_block is None:
                continue
            for rel in related_block.findall("anime"):
                rel_type = (rel.get("type") or "").lower()
                if rel_type not in AniDBProvider._FRANCHISE_RELATIONS:
                    continue
                rel_aid = rel.get("id")
                if rel_aid and int(rel_aid) not in visited:
                    queue.append(rel_aid)

        if seed_failed:
            # Seed lookup couldn't even resolve. Don't return a partial
            # walk built from un-seeded relations; caller falls back to
            # whatever the singleton match path can produce.
            return [int(aid)] if aid.isdigit() else []

        group = sorted(visited)
        if not group:
            group = [int(aid)] if aid.isdigit() else []

        if had_failures:
            # KI-8 Pattern D: skip-and-continue gave us a USEFUL partial
            # group (more than just the seed) but at least one related
            # AID is missing. Return the partial so the matcher gets
            # SOMETHING immediately, but DON'T cache — the cache
            # invariant is "ground truth or nothing." A future call
            # will retry from scratch and may catch the missing AID.
            return group

        # Full success — every walked AID resolved. Persist the closure
        # under EVERY member so any starting AID returns the same answer
        # instantly next time. Disk write offloaded to a worker thread
        # so the event loop isn't blocked on json.dumps + filesystem
        # fsync for large caches.
        async with AniDBProvider._relations_cache_lock:
            for member in group:
                cache[str(member)] = group
            await asyncio.to_thread(self._save_relations_cache)
        return group

    # ── Franchise offset table — converts an absolute episode number to
    # the AID + local episode it belongs to. Built by walking the relations
    # chain (cached) then fetching each member's episode count (cached).
    _FRANCHISE_OFFSETS_PATH: ClassVar[Path] = _CACHE_DIR / "anidb-franchise-offsets.json"
    # Maps canonical_aid (str) → {"ts": epoch, "v": [[aid, abs_start, abs_end], …]}.
    # (Legacy entries are a bare list with no timestamp — treated as expired so
    # they refetch once and upgrade to the timestamped form.)
    _franchise_offsets_cache: ClassVar[dict | None] = None
    _franchise_offsets_lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    # A franchise's offsets can go STALE while it's still airing (a cour cached
    # mid-run understates its episode count, shifting every later cour). Expire
    # entries so an airing show self-corrects instead of misrouting until the
    # cache file is hand-deleted. 14 days: well under a cour, cheap to refresh.
    _FRANCHISE_OFFSETS_TTL: ClassVar[float] = 14 * 24 * 3600

    @classmethod
    def _franchise_entry_fresh(cls, entry) -> bool:
        """True when a cache entry is the timestamped form AND within the TTL.
        Legacy bare-list entries (no timestamp) return False → refetch once."""
        if not isinstance(entry, dict):
            return False
        ts = entry.get("ts")
        if not isinstance(ts, (int, float)):
            return False
        import time
        return (time.time() - ts) < cls._FRANCHISE_OFFSETS_TTL

    @classmethod
    def _load_franchise_offsets(cls) -> dict[str, list[list[int]]]:
        if cls._franchise_offsets_cache is not None:
            return cls._franchise_offsets_cache
        import json
        if cls._FRANCHISE_OFFSETS_PATH.exists():
            try:
                cls._franchise_offsets_cache = json.loads(cls._FRANCHISE_OFFSETS_PATH.read_text())
            except Exception:
                cls._franchise_offsets_cache = {}
        else:
            cls._franchise_offsets_cache = {}
        return cls._franchise_offsets_cache

    @classmethod
    def _save_franchise_offsets(cls) -> None:
        import json
        if cls._franchise_offsets_cache is None:
            return
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cls._FRANCHISE_OFFSETS_PATH.write_text(json.dumps(cls._franchise_offsets_cache))

    async def get_franchise_offsets(self, seed_aid: int | str) -> list[tuple[int, int, int]]:
        """Return per-AID absolute-episode ranges for a franchise.

        Result format: `[(aid, abs_start, abs_end), …]` sorted by start.
        Example for Rent-a-Girlfriend (12 eps × 5 seasons):
            [(15299,  1, 12), (15743, 13, 24), (17629, 25, 36),
             (18753, 37, 48), (19587, 49, 60)]

        Used by the matcher to route an absolute-numbered file
        (e.g. `My Hero - 014.mkv`, absolute=14) to the correct AID
        (S2 in that example) without trusting trigram-only matching.

        Cost: cache hit = dict read. Cold = walks relations chain (cached
        on disk) + fetches episode counts for any uncached members
        (rate-limited 4s each via _api_lock). A 5-season franchise cold-
        fetches in ~20s; sub-millisecond thereafter.

        Returns [] if the franchise can't be resolved (ban / unknown AID).
        """
        try:
            seed_i = int(seed_aid)
        except (ValueError, TypeError):
            return []
        # Canonical AID = lowest in the chain. Use as cache key so all
        # members of a franchise share one entry.
        chain = await self.get_related_aids(str(seed_i))
        if not chain:
            return []
        canonical = min(chain)
        cache = self._load_franchise_offsets()
        cached = cache.get(str(canonical))
        if cached and self._franchise_entry_fresh(cached):
            offsets = cached["v"] if isinstance(cached, dict) else cached
            return [(int(a), int(s), int(e)) for a, s, e in offsets]

        # Fetch counts for every member. Members already in the episode-
        # count cache are free; cold members each cost ~4s.
        counts: dict[int, int] = {}
        for member in chain:
            n = await self.get_episode_count(member)
            if n is None:
                # Couldn't get count for this member (banned, error). Bail
                # rather than computing partial / wrong offsets — they'd
                # cache forever.
                return []
            counts[member] = n

        # ── KI-9 Pattern A: Observer Mode for franchise sort order ─────
        # The bare-AID sort below is a heuristic — AniDB assigns AIDs
        # in registration order, which is "usually" chronological but
        # breaks down for reboots (the reboot is registered AFTER the
        # parent series so gets a higher AID, but episode 1 of the
        # reboot belongs at the START of the franchise timeline) and
        # for side-stories that get registered out-of-band.
        #
        # The Fribb cross-reference carries a `season` integer per AID
        # (TVDB season number); when present, that's a far more
        # reliable chronological signal than AID order.
        #
        # We compute BOTH orders, KEEP USING the bare-AID sort (current
        # behaviour, no regression), and emit a structured log every
        # time the two diverge. After 24-72 hours of normal scans the
        # log will surface every franchise where the heuristic disagrees
        # with the canonical Fribb order; we can then triage and flip
        # the live sort.
        old_order = sorted(counts.keys())
        # Pull Fribb seasons for each member. Module imported lazily to
        # avoid circular-import noise (AnimeMappings imports utilities
        # that might re-import this file).
        from kira.providers.anime_mappings import AnimeMappings
        fribb_seasons: dict[int, int | None] = {}
        for m in counts.keys():
            try:
                fribb_seasons[m] = await AnimeMappings.tvdb_season(m)
            except Exception:
                fribb_seasons[m] = None

        def _new_sort_key(aid: int) -> tuple[int, int]:
            # Fribb season as primary; bare AID as tiebreaker. Unknown
            # Fribb season goes to the end of the list (sentinel 9999)
            # so AIDs with proper season metadata sort first.
            s = fribb_seasons.get(aid)
            return (s if isinstance(s, int) else 9999, aid)
        new_order = sorted(counts.keys(), key=_new_sort_key)

        # Phase 8 (KI-9 flip): adopt the Fribb-season order — but ONLY when
        # EVERY member has a known season. Fribb's TVDB-season integer is a
        # far more reliable chronological signal than AID registration order
        # (reboots / side-stories get high AIDs but belong earlier in the
        # timeline). When the franchise is fully season-mapped we trust it;
        # if ANY member lacks a season we fall back to bare-AID order rather
        # than shoving the unmapped members to the end via the 9999 sentinel
        # (which could be worse than the registration-order heuristic). So
        # this strictly improves fully-mapped franchises and never regresses
        # partially-mapped ones.
        all_have_season = all(isinstance(fribb_seasons.get(m), int) for m in counts.keys())
        order = new_order if all_have_season else old_order
        if old_order != new_order:
            _anidb_log.info(json.dumps({
                "evt": "franchise_sort",
                "canonical_aid": canonical,
                "applied": "fribb_season" if all_have_season else "bare_aid",
                "old_order": old_order,
                "new_order": new_order,
                "fribb_seasons": {str(k): v for k, v in fribb_seasons.items()},
            }))

        # Build the cumulative range table from the chosen order.
        offsets: list[tuple[int, int, int]] = []
        cursor = 1
        for member in order:
            n = counts[member]
            if n <= 0:
                continue
            # Skip Fribb season-0 members (recap movies / specials / compilation
            # films that get linked into the relation chain). They are NOT part
            # of the franchise's ABSOLUTE episode numbering, and — sorting first
            # via the season-0 key — would otherwise consume absolute range
            # [1..n] and shift every real TV episode's absolute span, mis-routing
            # pure absolute-numbered files to the wrong AID.
            if fribb_seasons.get(member) == 0:
                continue
            offsets.append((member, cursor, cursor + n - 1))
            cursor += n

        import time
        async with AniDBProvider._franchise_offsets_lock:
            cache[str(canonical)] = {"ts": time.time(), "v": [[a, s, e] for a, s, e in offsets]}
            await asyncio.to_thread(self._save_franchise_offsets)
        return offsets

    def offset_for_aid(self, offsets: list[tuple[int, int, int]], aid: int) -> int:
        """Cumulative episodes BEFORE this AID in the franchise. Used to
        translate absolute → local (`local_ep = absolute - offset_for_aid`).
        Returns 0 if the AID isn't in the table (i.e. is the first season)."""
        for member, start, _end in offsets:
            if member == aid:
                return start - 1
        return 0

    # ── Picture URL — separate path because most callers only need the
    # poster, not full episode metadata. Persisted to disk so we don't keep
    # hammering AniDB for things we've already looked up.
    _PICTURE_CACHE_PATH: ClassVar[Path] = _CACHE_DIR / "anidb-pictures.json"
    _picture_cache: ClassVar[dict[str, str | None] | None] = None
    _picture_cache_lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    @classmethod
    def _load_picture_cache(cls) -> dict[str, str | None]:
        if cls._picture_cache is not None:
            return cls._picture_cache
        import json
        if cls._PICTURE_CACHE_PATH.exists():
            try:
                cls._picture_cache = json.loads(cls._PICTURE_CACHE_PATH.read_text())
            except Exception:
                cls._picture_cache = {}
        else:
            cls._picture_cache = {}
        return cls._picture_cache

    @classmethod
    def _save_picture_cache(cls) -> None:
        import json
        if cls._picture_cache is None:
            return
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cls._PICTURE_CACHE_PATH.write_text(json.dumps(cls._picture_cache))

    # Bumped whenever the picture-resolution algorithm changes in a way
    # that should invalidate previously-cached URLs. v2 = season-aware
    # TVDB/TMDB fetch landed; previous v1 cached series-level posters
    # that need re-fetching for non-S1 franchise members. v3 = cour-aware
    # (prefer AniDB's own per-AID art over the shared season poster for
    # multi-cour seasons) — the existing season≥2 eviction below re-resolves
    # every cour, since all cours are season ≥ 2.
    _PICTURE_CACHE_VERSION: ClassVar[int] = 3

    @classmethod
    async def migrate_picture_cache(cls) -> None:
        """Evict cached URLs that predate the season-aware poster fetch.

        Walks every cached AID, looks it up in the Fribb cross-ref. If the
        AID's mapping has a season number >= 2 (i.e. it's NOT the franchise
        opener), the cached URL is almost certainly the shared series-level
        poster and needs re-fetching. We delete those entries so the next
        get_picture_url call re-runs the season-aware path.

        Idempotent — guarded by a version marker stored in the cache itself.
        """
        cache = cls._load_picture_cache()
        if cache.get("__version__") == cls._PICTURE_CACHE_VERSION:
            return
        from kira.providers.anime_mappings import AnimeMappings
        evicted = 0
        for aid in list(cache.keys()):
            if aid.startswith("__"):
                continue
            try:
                aid_i = int(aid)
            except ValueError:
                continue
            season_num = await AnimeMappings.tvdb_season(aid_i)
            if season_num and season_num >= 2:
                del cache[aid]
                evicted += 1
        cache["__version__"] = cls._PICTURE_CACHE_VERSION
        async with cls._picture_cache_lock:
            await asyncio.to_thread(cls._save_picture_cache)
        if evicted:
            logger.warning(f"anidb pictures: evicted {evicted} stale franchise-member URLs (now season-aware).")

    async def _anidb_cdn_picture(self, aid: str) -> tuple[str | None, bool]:
        """Fetch the AID's OWN picture from the AniDB HTTP API.

        Returns ``(url_or_None, responded)``. ``responded=False`` means the
        API errored / we're banned — the caller must NOT cache a null
        (so the next call retries). ``responded=True`` with ``None`` means
        AniDB confirmed this AID has no picture.
        """
        data = await self._http_api(aid)
        if data is None:
            return None, False
        pic = data.find("picture")
        if pic is not None and pic.text and pic.text.strip():
            return f"https://cdn.anidb.net/images/main/{pic.text.strip()}", True
        return None, True

    async def get_picture_url(self, aid: str) -> str | None:
        """Return a poster URL for an anime, or None if no source has one.

        Lookup chain — first hit wins:
          1. Disk cache (resolved URLs from any prior call)
          2. **AID → TVDB cross-reference** (Fribb anime-lists). Fetch the
             poster from TVDB's API — way more generous rate limit, no IP
             bans. This is the path 95% of lookups now take.
          3. **AID → TMDB cross-reference** for anime missing on TVDB.
          4. AniDB CDN as last resort (rate-limited / ban risk; this is
             why we don't lead with it).

        Cache discipline: ONLY persist null when ALL three sources came
        back empty AND the AniDB call was successful. A null from an API
        error (rejection, ban, timeout) is NOT cached — otherwise transient
        failures would poison the cache forever.
        """
        from kira.providers.anime_mappings import AnimeMappings

        cache = self._load_picture_cache()
        if aid in cache:
            return cache[aid]

        try:
            aid_i = int(aid)
        except ValueError:
            return None

        # ── Cour detection (distinct cour covers) ─────────────────────────
        # Fetch the Fribb cross-ref up front. When ≥2 AIDs map to the SAME
        # (tvdb_id, season) — Bleach TYBW cours, Attack on Titan S3 / Final-
        # Season parts — the TVDB/TMDB SEASON poster is IDENTICAL across them,
        # so the franchise grid looks like clones. For those, prefer AniDB's
        # OWN per-AID art (distinct per cour). Single-AID seasons keep the
        # nicer English TVDB/TMDB season poster (the path below).
        tvdb_id = await AnimeMappings.tvdb_id(aid_i)
        season_num = await AnimeMappings.tvdb_season(aid_i)
        is_cour = False
        if tvdb_id and season_num:
            try:
                sibs = await AnimeMappings.aids_by_tvdb_season(tvdb_id, season_num)
                is_cour = len([s for s in (sibs or []) if s]) >= 2
            except Exception:
                is_cour = False
        if is_cour:
            cour_url, _responded = await self._anidb_cdn_picture(aid)
            if cour_url:
                async with AniDBProvider._picture_cache_lock:
                    cache[aid] = cour_url
                    await asyncio.to_thread(self._save_picture_cache)
                return cour_url
            # No AniDB art for this cour (or banned) → fall through to the
            # shared season poster: a collide-y cover beats no cover.

        # Path 1: TVDB via cross-reference (season-specific poster).
        if tvdb_id:
            tvdb = await self._get_xref("tvdb")
            if tvdb is not None:
                try:
                    season_num = await AnimeMappings.tvdb_season(aid_i)
                    if season_num and hasattr(tvdb, "get_season_poster"):
                        url = await tvdb.get_season_poster(str(tvdb_id), season_num)  # type: ignore[attr-defined]
                    else:
                        url = await tvdb.get_series_poster(str(tvdb_id))  # type: ignore[attr-defined]
                    if url:
                        async with AniDBProvider._picture_cache_lock:
                            cache[aid] = url
                            await asyncio.to_thread(self._save_picture_cache)
                        return url
                except Exception:
                    pass  # fall through to TMDB / AniDB

        # Path 2: TMDB via cross-reference. Same season-aware logic — TMDB's
        # `/tv/{id}/season/{N}` returns per-season posters; we use that when
        # the mapping carries a season hint.
        tmdb_id = await AnimeMappings.tmdb_tv_id(aid_i)
        if tmdb_id:
            tmdb = await self._get_xref("tmdb")
            if tmdb is not None:
                try:
                    # Reuse the TVDB season hint when present (Fribb maps
                    # store the same season number under both providers).
                    season_num = await AnimeMappings.tvdb_season(aid_i)
                    url: str | None = None
                    if season_num and hasattr(tmdb, "get_season_poster"):
                        url = await tmdb.get_season_poster(str(tmdb_id), season_num)  # type: ignore[attr-defined]
                    if not url:
                        # Series-level fallback.
                        r = await self.client.get(
                            f"{tmdb.base_url}/tv/{tmdb_id}",
                            params=tmdb._auth_params(),
                            headers=tmdb._auth_headers(),
                            timeout=15.0,
                        )
                        if r.status_code == 200:
                            p = r.json().get("poster_path")
                            if p:
                                url = f"https://image.tmdb.org/t/p/w500{p}"
                    if url:
                        async with AniDBProvider._picture_cache_lock:
                            cache[aid] = url
                            await asyncio.to_thread(self._save_picture_cache)
                        return url
                except Exception:
                    pass

        # Path 3: AniDB CDN — last resort, hits the rate-limited HTTP API.
        # Skipped entirely when we're banned (the _http_api short-circuit
        # returns None which bypasses the cache write below).
        #
        # ── R2-H9: Cache discipline ─────────────────────────────────────
        # We ONLY persist a value (URL or None) when AniDB explicitly
        # returned a response. Transient failures (ban, timeout, network
        # blip) cause `_http_api` to return None — we bail without
        # caching so the next call retries. Without this guard, a single
        # ban event during scan would freeze 100+ AIDs to "no poster
        # forever" until the user restarted the backend.
        #
        # The TVDB / TMDB branches above are similarly guarded — they
        # only cache when `url` is a non-empty string (the `if url:`
        # truth check), so an empty-string response (vs a valid URL)
        # falls through to the next provider rather than poisoning
        # the cache.
        # Cours already tried their own AniDB picture above — don't fire a
        # second rate-limited call.
        if is_cour:
            return None
        url, responded = await self._anidb_cdn_picture(aid)
        if not responded:
            # API error / banned — do NOT cache. Future calls retry.
            return None
        async with AniDBProvider._picture_cache_lock:
            cache[aid] = url  # may be None — only when AniDB confirmed no picture
            await asyncio.to_thread(self._save_picture_cache)
        return url

    async def _get_xref(self, provider_key: str) -> MetadataProvider | None:
        """Lazily build & cache a cross-reference provider (TVDB/TMDB) per
        AniDB instance. Reusing the SAME provider instance is what keeps the
        TVDB JWT alive between poster lookups — building a fresh TVDBProvider
        per call would trigger a /login on every poster fetch and rate-limit
        TVDB instantly on a large scan.
        """
        if provider_key in self._xref_providers:
            return self._xref_providers[provider_key]
        try:
            from kira.matcher.engine import registry_from_settings
            registry = await registry_from_settings(self.client)
            if not registry.has(provider_key):  # type: ignore[arg-type]
                self._xref_providers[provider_key] = None
                return None
            built = registry.build(provider_key)  # type: ignore[arg-type]
        except Exception:
            self._xref_providers[provider_key] = None
            return None
        self._xref_providers[provider_key] = built
        return built

    # Tracks the last AniDB HTTP API error so callers (picture endpoint) can
    # surface it to the user. AniDB rejects unregistered clients with
    # <error code="302">client version missing or invalid</error> — once we
    # hit that, every subsequent call will fail the same way, so we short-
    # circuit to avoid wasting the 4s-per-call rate budget.
    _last_error: ClassVar[str | None] = None
    _client_rejected: ClassVar[bool] = False
    # R2-H10: Per-process counter of _http_api invocations. The bulk
    # rematch worker uses this to yield AFTER N AniDB calls (not after
    # N files), so a franchise-heavy file that fires 5+ AniDB calls
    # doesn't monopolise the rate budget for foreground scans.
    _http_call_count: ClassVar[int] = 0

    # ── Circuit breaker ────────────────────────────────────────────────
    # Per-process sliding window of recent error timestamps. When the
    # window holds >= _ERROR_THRESHOLD errors, the circuit "opens" — we
    # refuse outgoing AniDB calls for _CIRCUIT_OPEN_SEC. Survives a
    # transient AniDB outage without each in-flight call individually
    # contributing to a real 12h ban. Cross-worker safety is provided
    # by the existing disk-backed ban file: when ANY worker trips the
    # circuit AND AniDB responds with a real ban, all workers see the
    # ban via _load_ban_state.
    _recent_errors: ClassVar[list[float]] = []
    _circuit_open_until: ClassVar[float] = 0.0

    @classmethod
    def _circuit_open(cls) -> bool:
        """True if the breaker is currently refusing outgoing calls."""
        return time.time() < cls._circuit_open_until

    @classmethod
    def _record_error(cls) -> None:
        """Add an error timestamp; trip the breaker if the threshold
        is crossed within the sliding window."""
        now = time.time()
        cls._recent_errors.append(now)
        # Drop entries outside the window (keeps list bounded).
        cutoff = now - _ERROR_WINDOW_SEC
        cls._recent_errors = [t for t in cls._recent_errors if t >= cutoff]
        if len(cls._recent_errors) >= _ERROR_THRESHOLD:
            cls._circuit_open_until = now + _CIRCUIT_OPEN_SEC
            cls._last_error = (
                f"AniDB circuit breaker tripped after "
                f"{len(cls._recent_errors)} errors in "
                f"{_ERROR_WINDOW_SEC:.0f}s; pausing for "
                f"{_CIRCUIT_OPEN_SEC / 60:.0f} min."
            )
            logger.warning(f"anidb: {cls._last_error}")
            # Reset window so we don't immediately re-trip after cool-down.
            cls._recent_errors = []
    # Set when AniDB returns a "banned" error (5xx or text with "banned").
    # Persisted to disk + timestamped so we hold off for hours even across
    # backend restarts. AniDB doesn't publish the exact cool-down, but 12h
    # is the community-accepted convention for self-rate-limited clients.
    _BAN_FILE: ClassVar[Path] = _CACHE_DIR / "anidb-banned-until.txt"
    _BAN_COOLDOWN_SEC: ClassVar[float] = 12 * 3600  # 12 hours
    _banned_until: ClassVar[float] = 0.0  # Unix timestamp; 0 = not banned.

    @classmethod
    def _load_ban_state(cls) -> None:
        """Read the persisted ban-until timestamp from disk EVERY call.

        Old behavior short-circuited if `_banned_until != 0.0`, which meant
        worker B never saw worker A's freshly-written ban file. After
        worker A got the ban, worker B kept firing requests, deepening the
        cool-down. We now re-read the file on every call — cheap (a single
        stat + small read) and worth it to keep the ban shared across
        every uvicorn worker without IPC.
        """
        if not cls._BAN_FILE.exists():
            cls._banned_until = 0.0
            return
        try:
            cls._banned_until = float(cls._BAN_FILE.read_text().strip())
        except (OSError, ValueError):
            cls._banned_until = 0.0

    @classmethod
    def is_banned(cls) -> bool:
        """True if AniDB has banned us and the cool-down hasn't elapsed yet.

        Re-reads disk on each call so a ban triggered by sibling worker
        N is visible to worker N+1 instantly — without this guard, two
        workers could see "not banned" simultaneously, both fire a call,
        both hit the ban, both *extend* the cool-down by 12h each.
        """
        cls._load_ban_state()
        return time.time() < cls._banned_until

    @classmethod
    def _set_banned(cls, msg: str) -> None:
        """Record a ban — refuse new calls for the cool-down window.

        ── R2-C2: Idempotent ban (don't extend deadline) ───────────────
        Under multi-worker load, several workers can race a single ban:
        worker A's request returns 503 first, calls _set_banned, writes
        `banned_until = T_A + 12h`. Worker B's in-flight request returns
        next, also 503, also calls _set_banned. Previously that overwrote
        with `banned_until = T_B + 12h` (slightly later), drifting the
        deadline forward every wave. Run a dozen times and the ban becomes
        effectively permanent.

        Fix: re-read disk state first; if an active ban is already
        recorded, append to the log file for diagnostic but DON'T move
        the deadline. The first worker's deadline is canonical.

        ── R2-C2 part 2: Atomic ban-file write ────────────────────────
        Use `.tmp + os.replace` instead of raw `write_text`. Under
        concurrent writers a torn write can corrupt the timestamp (we
        verified ban-file format is just a float, but a partial write
        would parse as 0.0 and re-enable AniDB calls immediately).
        """
        import os
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Re-read whatever's on disk RIGHT NOW (not the stale in-memory
        # copy). The sibling worker may have written the ban while we
        # were waiting for the lock; this catches that race.
        cls._load_ban_state()
        now = time.time()
        if cls._banned_until > now:
            # Already banned. Log the additional ban event for diagnostics
            # but DON'T touch the deadline — keep the first worker's value.
            try:
                log = _CACHE_DIR / "anidb-ban-events.log"
                with log.open("a", encoding="utf-8") as f:
                    f.write(
                        f"{now:.1f}: ban already active (until "
                        f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(cls._banned_until))}); "
                        f"event: {msg}\n"
                    )
            except OSError:
                pass
            cls._last_error = (
                f"AniDB ban active until "
                f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(cls._banned_until))}: {msg}"
            )
            return

        # Fresh ban — set the deadline and write atomically via .tmp + replace.
        cls._banned_until = now + cls._BAN_COOLDOWN_SEC
        tmp = cls._BAN_FILE.with_suffix(".txt.tmp")
        try:
            tmp.write_text(str(cls._banned_until))
            os.replace(str(tmp), str(cls._BAN_FILE))
        except OSError:
            # Disk write failed — fall back to direct write. We'd rather
            # have a possibly-torn ban file than no ban file at all.
            try:
                cls._BAN_FILE.write_text(str(cls._banned_until))
            except OSError:
                pass
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        cls._last_error = f"AniDB banned: {msg} (cooling down ~{cls._BAN_COOLDOWN_SEC // 3600:.0f}h)"

    @classmethod
    def clear_ban(cls) -> None:
        """Manual override — user clicked 'try again' after waiting."""
        cls._banned_until = 0.0
        try:
            cls._BAN_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    async def _http_api(self, aid: str) -> Element | None:
        """Throttled call to AniDB's HTTP API. 1 request per 4 seconds,
        enforced **across uvicorn workers** via a disk-backed wall-clock
        timestamp.

        Ordering inside the critical section is crucial for the cross-
        worker guarantee:
          1. Acquire intra-process `_api_lock` (serialises this process).
          2. Re-read ban state from disk (sibling worker may have just
             been banned — we must not fire a request behind their back).
          3. Read sibling-worker last-call timestamp from disk.
          4. Sleep until 4s after that timestamp.
          5. Fire the request, write the new timestamp BEFORE releasing
             the lock so the next worker's read sees a fresh value.
        """
        if AniDBProvider._client_rejected:
            return None
        if AniDBProvider.is_banned():
            AniDBProvider._last_error = (
                f"AniDB banned — refusing calls until "
                f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(AniDBProvider._banned_until))}"
            )
            return None
        # Circuit breaker: pause outgoing calls when we've had too many
        # recent errors. Cheaper than letting each failing call eat a
        # 5-second throttle slot just to error out again.
        if AniDBProvider._circuit_open():
            return None
        async with AniDBProvider._api_lock:
            # Re-check ban inside the critical section. A sibling worker
            # may have hit a ban during the (potentially long) sleep
            # for our turn — we must NOT fire a request after the ban
            # landed; that would deepen it.
            if AniDBProvider.is_banned():
                return None
            if AniDBProvider._circuit_open():
                return None

            # R2-H1: Claim our fire-time BEFORE sleeping (cross-process)
            # The asyncio lock is per-process; a sibling worker in a
            # different process has its own lock and won't be blocked by
            # ours. If we sleep first and write later, that sibling can
            # read the SAME stale `last_disk`, sleep the same duration,
            # and fire simultaneously with us — both get 503.
            #
            # Solution: compute `fire_at` and write it to disk IMMEDIATELY
            # while we still hold our lock. The sibling worker, on its
            # next read, sees our claimed future timestamp and computes
            # its own `fire_at = our_fire_at + 4s`. Each worker effectively
            # reserves the next 4-second slot atomically via the disk
            # write. The actual sleep happens after the reservation,
            # so multiple workers serialize via the disk-timestamp ladder.
            last_disk = await asyncio.to_thread(AniDBProvider._read_last_call_wallclock)
            now = time.time()
            fire_at = max(now, last_disk + _API_DELAY_SEC)
            # Reserve the slot — write fire_at to disk before sleeping.
            # Use to_thread for the same reason as the read: avoid
            # blocking the event loop on slow filesystems.
            await asyncio.to_thread(
                lambda ts=fire_at: AniDBProvider._write_last_call_wallclock(ts)
            )
            wait = fire_at - now
            if wait > 0:
                await asyncio.sleep(wait)
            params = {
                "request": "anime",
                "client": self._client_name,
                "clientver": self._client_ver,
                "protover": "1",
                "aid": aid,
            }
            # PB-1: structured outcome capture for one-line JSON log emission.
            # `outcome` is set at every return / exception point; a `finally`
            # block at the end emits the structured event. Without this, we
            # had print() statements scattered through error paths and zero
            # visibility into the success path (latency, request count).
            started_at = time.monotonic()
            outcome = "error"
            http_status: int | None = None
            err_msg: str | None = None
            try:
                # R2-H10: count this HTTP call so the bulk-rematch worker
                # can yield per-call rather than per-file (a single file
                # can fire 5+ HTTP calls for a franchise-heavy show).
                AniDBProvider._http_call_count += 1
                try:
                    r = await self.client.get(
                        self.base_url,
                        params=params,
                        headers={"User-Agent": _USER_AGENT},
                        timeout=30.0,
                    )
                    http_status = r.status_code
                except Exception as e:
                    # Failed mid-request — write timestamp anyway so we
                    # don't immediately retry within the rate-limit window.
                    await asyncio.to_thread(AniDBProvider._write_last_call_wallclock)
                    AniDBProvider._last_error = str(e)
                    AniDBProvider._record_error()
                    err_msg = repr(e)
                    return None

                # Write timestamp BEFORE releasing the lock so the next
                # waiter (here or in another worker) sees the fresh value.
                await asyncio.to_thread(AniDBProvider._write_last_call_wallclock)

                body = r.text or ""
                body_lower = body.lower()

                # ── KI-7 Pattern C: capture ground-truth response shapes ─
                # For every 4xx/5xx we log a structured event with status,
                # a body snippet, and a few discriminating headers. This is
                # free intelligence — costs nothing in normal operation,
                # gives us ammunition for tightening the ban detector when
                # AniDB or a CDN-side error page evolves its shape. The
                # detector below makes informed decisions today; the log
                # is for the next change.
                if r.status_code >= 400:
                    _anidb_log.warning(json.dumps({
                        "evt": "anidb_http_error",
                        "status": r.status_code,
                        "body_snippet": body[:240],
                        "content_type": r.headers.get("content-type", "")[:80],
                        "server": r.headers.get("server", "")[:80],
                    }))

                # ── KI-7: narrow the ban trigger ─────────────────────────
                # PRE-FIX: any 5xx — even a transient AniDB-overloaded 503
                # or a CDN-side 502 — tripped the 12-hour cooldown. A single
                # blip during a routine scan locked out every anime call for
                # half a day; the user saw "AniDB banned" in the UI with no
                # recourse beyond a hidden manual reset.
                #
                # POST-FIX: only treat these as real bans —
                #   * `banned` token anywhere in the body (AniDB's primary
                #     signal; works for both <html> error pages and the
                #     plain-text ban responses we've seen historically)
                #   * status 429 (Too Many Requests — explicit rate-limit
                #     ban signal per HTTP semantics)
                # Anything else 5xx falls through to the elif branch:
                # record into the circuit-breaker window (5 errors in 60s
                # → 5-min pause) but DON'T trigger the 12-hour cooldown.
                # The circuit breaker handles the "AniDB is genuinely
                # broken right now" case at a reasonable cost; the ban
                # only fires when AniDB explicitly tells us we're banned.
                is_ban = (
                    r.status_code == 429
                    or "banned" in body_lower
                )
                if is_ban:
                    AniDBProvider._set_banned(f"HTTP {r.status_code}: {body[:80]}")
                    AniDBProvider._record_error()
                    outcome = "banned"
                    err_msg = f"HTTP {r.status_code}"
                    return None
                if r.status_code >= 500:
                    # Transient server-side problem (overload, CDN 502,
                    # gateway timeout, etc.). Record into the circuit
                    # breaker so repeated failures still escalate to a
                    # short pause, but never trip the 12h cooldown.
                    AniDBProvider._last_error = f"HTTP {r.status_code}"
                    AniDBProvider._record_error()
                    outcome = "server_error"
                    err_msg = f"HTTP {r.status_code} (transient)"
                    return None

                if r.status_code != 200 or not body.strip().startswith("<"):
                    AniDBProvider._last_error = f"HTTP {r.status_code}"
                    AniDBProvider._record_error()
                    err_msg = f"non-200/non-xml ({r.status_code})"
                    return None
                try:
                    root = ET.fromstring(body)
                except ET.ParseError as e:
                    AniDBProvider._last_error = f"malformed XML: {e}"
                    AniDBProvider._record_error()
                    err_msg = f"parse_error: {e}"
                    return None
                # AniDB wraps API errors in <error code="N">message</error>.
                # Most common: 302 "client version missing or invalid" when
                # the (client, clientver) pair isn't a registered AniDB client.
                if root.tag == "error":
                    code = root.get("code") or "?"
                    msg = (root.text or "").strip() or "unknown error"
                    AniDBProvider._last_error = f"AniDB error {code}: {msg}"
                    if "banned" in msg.lower():
                        AniDBProvider._set_banned(msg)
                        outcome = "banned"
                    elif code == "302" or "client" in msg.lower():
                        AniDBProvider._client_rejected = True
                        outcome = "client_rejected"
                    AniDBProvider._record_error()
                    err_msg = f"api_error_{code}"
                    return None
                outcome = "ok"
                return root
            finally:
                # PB-1: emit one structured event per HTTP call. Single
                # log line, JSON body — easy to parse with `jq` or pipe
                # into a real obs stack. Captures latency, outcome, ban
                # state, circuit-breaker state in one place.
                _anidb_log.info(json.dumps({
                    "evt": "anidb_http",
                    "endpoint": "anime",
                    "aid": aid,
                    "latency_ms": int((time.monotonic() - started_at) * 1000),
                    "outcome": outcome,
                    "http_status": http_status,
                    "banned": AniDBProvider.is_banned(),
                    "circuit_open": AniDBProvider._circuit_open(),
                    "call_count": AniDBProvider._http_call_count,
                    "error": err_msg,
                }))

    @classmethod
    def reset_rejection(cls) -> None:
        """Re-enable HTTP API calls after the user updates client/clientver.

        Also evicts every cached null from the picture cache. Those nulls
        were almost certainly recorded during the rejected-client window
        (before we fixed get_picture_url to skip caching on error), so
        retrying them now will produce real URLs.
        """
        cls._client_rejected = False
        cls._last_error = None
        cache = cls._load_picture_cache()
        before = len(cache)
        for aid in [k for k, v in cache.items() if v is None]:
            del cache[aid]
        if len(cache) != before:
            cls._save_picture_cache()


# Helper used by the matcher to look up an anime by AID and pull aliases.
async def get_titles_for_aid(aid: int) -> list[str]:
    """Return all known titles for an AID from the (possibly already-loaded)
    in-memory index. Returns [] if the index hasn't been built.

    `_titles` entries are 3-tuples (type, lang, title) — see _parse_titles.
    The legacy 2-tuple shape was retired during the lang-priority refactor.
    """
    titles = AniDBProvider._titles
    if titles is None:
        return []
    return [t for _, _lang, t in titles.get(aid, [])]


_ = Any  # placeholder to keep typing import referenced if unused later
