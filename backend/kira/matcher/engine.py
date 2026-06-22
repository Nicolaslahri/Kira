"""Matcher engine — given a ParsedFile, query the appropriate provider(s),
score each result, and return ranked candidates.

Confidence is a weighted blend (see plan §3c):
  - title similarity (trigram)  0.55
  - year match                  0.25
  - result rank                 0.20
"""

from __future__ import annotations

import logging

import asyncio
import contextvars
import re as _engine_re
from dataclasses import dataclass
from pathlib import Path as _EnginePath

import httpx

from kira.matcher.acronyms import KNOWN_ACRONYMS, is_acronym_shaped
from kira.matcher.similarity import normalize, trigram_similarity
from kira.parser import ParsedFile
from kira.providers import build_provider
from kira.providers.base import (
    DEFAULT_CLOUD_BASE_URL,
    MetadataProvider,
    MovieResult,
    ProviderConfig,
    ProviderKey,
    ProviderMode,
    ProviderPermanentError,
    ProviderTransientError,
    TVResult,
)
from kira.providers.factory import KEYLESS_PROVIDERS

logger = logging.getLogger(__name__)


# Retry policy. TWO schedules, picked by error class — this matters a LOT:
#   • RATE-LIMIT / server-busy (HTTP 429/5xx → ProviderTransientError): the
#     upstream is asking us to slow down, so back off for real (1s→2s→4s).
#   • CONNECTION BLIP (dropped TCP/TLS, brief network stall → httpx.TransportError
#     / asyncio.TimeoutError): the endpoint is UP, a single connect just failed.
#     Retrying immediately almost always succeeds, so a long backoff is pure
#     dead time. With a flaky-but-up provider (e.g. ~20% of TMDB connects drop)
#     and several provider calls per file, the old "always 1-2-4s" turned every
#     blip into a 7s stall and tanked matching ~20x. Fast retry fixes that:
#     worst case ~1.7s, typically ~0.2s.
_RETRY_BACKOFFS = (1.0, 2.0, 4.0)                    # rate-limit / 5xx — back off
_CONNECT_BACKOFFS = (0.2, 0.4, 0.6, 0.8, 1.0)        # connection/TLS blip — retry fast


async def _provider_call_with_retry(coro_factory, *, what: str):
    """Run a provider coroutine with retry. The schedule depends on the error
    class: connection / TLS-handshake blips retry FAST and more times
    (`_CONNECT_BACKOFFS`); rate-limits back off harder but fewer times
    (`_RETRY_BACKOFFS`). With warm keep-alive connections (see kira.net), a
    scan re-handshakes ~once, so these connection retries mostly cover that one
    cold handshake rather than firing per file.

    Raises:
      ProviderTransientError after all retries exhausted.
      ProviderPermanentError immediately, no retry.
    """
    import random

    last_err: Exception | None = None
    attempt = 0
    while True:
        try:
            return await coro_factory()
        except ProviderPermanentError:
            # 4xx auth/invalid-ID — retry won't help.
            raise
        except (httpx.TransportError, asyncio.TimeoutError) as e:
            # Dropped/refused connection or reset TLS handshake — reconnect fast.
            last_err, backoffs, kind, jitter = e, _CONNECT_BACKOFFS, "connection", 0.1
        except (ProviderTransientError, httpx.HTTPError) as e:
            # 429 / 5xx / other HTTP-level transient — respect a real backoff.
            last_err, backoffs, kind, jitter = e, _RETRY_BACKOFFS, "transient", 0.25
        if attempt >= len(backoffs):
            break
        delay = backoffs[attempt] + random.uniform(0, jitter)
        logger.warning(f"matcher: {what} {kind} error ({last_err!r}); retry {attempt + 1}/{len(backoffs)} in {delay:.2f}s")
        await asyncio.sleep(delay)
        attempt += 1
    # Out of retries — raise as a typed exception so callers can distinguish
    # "tried hard, gave up" from "didn't try".
    raise ProviderTransientError(f"{what} failed after {attempt} retries: {last_err!r}") from last_err

# Per media_type, the DEFAULT providers to try in order. First with a real key
# wins; fallbacks kick in if the primary returns no usable result. This is only
# the default — `resolve_provider_order()` lets the user override it per
# media_type via the `matching.provider_order.<type>` setting.
PROVIDER_PREFERENCE: dict[str, list[ProviderKey]] = {
    "movie": ["tmdb", "tvdb"],
    "tv":    ["tvdb", "tmdb"],
    # AniDB first (canonical anime source, keyless), TVDB fallback (re-ranked).
    "anime": ["anidb", "tvdb", "tmdb"],
    "music": [],                  # MusicBrainz lives in a separate engine (audio path)
}

# Provider keys we accept when validating a user-supplied order. Anything not
# in here (typo, dropped provider, junk) is silently filtered out.
_KNOWN_PROVIDER_KEYS: frozenset[str] = frozenset({"tmdb", "tvdb", "anidb", "musicbrainz"})


def resolve_provider_order(media_type: str, settings: dict | None) -> list[ProviderKey]:
    """The provider cascade for `media_type`, honoring a user override.

    Default = the PROVIDER_PREFERENCE table above. The user may override per
    media_type via the setting `matching.provider_order.<type>`, whose value is
    an ordered list of provider keys, e.g. ["tvdb", "anidb", "tmdb"].

    SOFT preference, on purpose: the user's chosen providers come FIRST in their
    stated order, then any default providers they DIDN'T list are appended as
    trailing fallbacks. So a preference can never strand a title as no-match
    just because the preferred source happens not to carry it — the others still
    get a turn (coverage gaps, not just provider outages). Unknown / junk keys
    are dropped; an empty or missing setting yields the default order unchanged.
    """
    default = PROVIDER_PREFERENCE.get(media_type, [])
    if not settings:
        return list(default)
    raw = settings.get(f"matching.provider_order.{media_type}")
    if isinstance(raw, dict):            # tolerate a {"value": [...]} wrapper
        raw = raw.get("value")
    if not isinstance(raw, list):
        return list(default)
    chosen = [p for p in raw if isinstance(p, str) and p in _KNOWN_PROVIDER_KEYS]
    if not chosen:
        return list(default)
    tail = [p for p in default if p not in chosen]   # soft: keep omitted defaults as fallback
    return chosen + tail  # type: ignore[return-value]


# Anime cross-reference order: when a title is matched on AniDB (sparse episode
# titles, no cast/studio data), Kira enriches via the Fribb cross-ref to a
# fuller provider — episode names in the popup AND cast/studio in the NFO.
# Default TVDB-first (richer English titles), TMDB fallback. The user flips it
# via `matching.anime_crossref_order`. Only TVDB/TMDB carry the Fribb cross-ref,
# so the set is fixed; SOFT like the provider order — the omitted source stays a
# trailing fallback so a flip never strands enrichment.
_ANIME_CROSSREF_DEFAULT: list[str] = ["tvdb", "tmdb"]
_CROSSREF_KEYS: frozenset[str] = frozenset({"tvdb", "tmdb"})


def resolve_anime_crossref_order(settings: dict | None) -> list[str]:
    """Ordered cross-ref providers for AniDB enrichment (episode titles + NFO
    metadata), honoring `matching.anime_crossref_order`. Default ["tvdb","tmdb"].
    SOFT: the user's pick comes first, the omitted default is appended as a
    fallback. Unknown / junk keys (incl. "anidb" — it's the source, not a
    cross-ref target) are dropped; empty/missing yields the default."""
    if not settings:
        return list(_ANIME_CROSSREF_DEFAULT)
    raw = settings.get("matching.anime_crossref_order")
    if isinstance(raw, dict):            # tolerate a {"value": [...]} wrapper
        raw = raw.get("value")
    if not isinstance(raw, list):
        return list(_ANIME_CROSSREF_DEFAULT)
    chosen = [p for p in raw if isinstance(p, str) and p in _CROSSREF_KEYS]
    if not chosen:
        return list(_ANIME_CROSSREF_DEFAULT)
    tail = [p for p in _ANIME_CROSSREF_DEFAULT if p not in chosen]
    return chosen + tail


@dataclass
class ScoredMatch:
    provider: ProviderKey
    provider_id: str
    match_type: str          # "movie" | "tv_episode"
    confidence: float        # 0.0 - 1.0
    title: str
    year: int | None
    poster_url: str | None
    overview: str | None
    raw: dict | None = None
    # All known titles for the candidate — preserved so the season-aware
    # rerank can scan alternate-language entries (AniDB stores `5th Season`
    # only on the romaji title; the English display title is bare).
    aliases: list[str] | None = None


# ContextVar gives cascade metrics access to the current matcher's
# ProviderRegistry without threading it through every async call. Set
# inside MatchEngine.match() before running the cascade; reset on exit.
# Lets AnimeTVDBJPMetric build the TVDB client without inheriting it
# explicitly. None when there's no active match in flight.
_global_registry_ref: "contextvars.ContextVar[ProviderRegistry | None]" = (
    contextvars.ContextVar("kira_active_registry", default=None)
)


def _series_folder_from_parsed(parsed: ParsedFile) -> str | None:
    """Walk up the parsed file's parent path past any 'Season N' / 'Cour N'
    subfolder, return the next-up folder name as the series anchor.

    `/Anime/Rent-a-Girlfriend/Season 2/file.mkv` → `Rent-a-Girlfriend`
    `/Anime/One Piece/Season 23/file.mkv` → `One Piece`
    `/Anime/Movie.mkv` → `Anime` (which the FolderIdentityMetric will
    treat as generic and ignore).
    """
    src = getattr(parsed, "original_filename", None) or ""
    parent = ""
    if isinstance(getattr(parsed, "_parent_path", None), str):
        parent = parsed._parent_path  # type: ignore[attr-defined]
    if not parent:
        # Fall back to whatever the caller stored on parsed_data later
        # (scans.py reads parent_path; matcher path doesn't have it).
        return None
    p = _EnginePath(parent)
    # Walk up past Season/Cour/Part/Arc folders.
    parts = list(p.parts)
    skip_re = _engine_re.compile(r"^(season|series|cour|part|arc)\b", _engine_re.IGNORECASE)
    while parts:
        last = parts[-1]
        if skip_re.match(last):
            parts.pop()
            continue
        break
    if not parts:
        return None
    return parts[-1]


@dataclass
class ProviderRegistry:
    """Per-key ProviderConfig + a shared httpx.AsyncClient.

    The engine doesn't care if a config is missing — it just skips that provider.
    """
    configs: dict[ProviderKey, ProviderConfig]
    client: httpx.AsyncClient

    def has(self, key: ProviderKey) -> bool:
        cfg = self.configs.get(key)
        if cfg is None:
            return False
        if cfg.mode == ProviderMode.DIRECT and not cfg.api_key and key not in KEYLESS_PROVIDERS:
            return False
        if cfg.mode == ProviderMode.CLOUD and not cfg.cloud_token:
            return False
        return True

    def build(self, key: ProviderKey) -> MetadataProvider:
        # AniDB's registered client/version live on ProviderConfig — the
        # factory consumes them. No more post-construct monkey-patching.
        return build_provider(key, self.configs[key], self.client)


# ──────────────────────────────────────────────────────────────────────────
# Engine
# ──────────────────────────────────────────────────────────────────────────


class MatchEngine:
    """Routes a parsed file to the right provider(s) and returns ranked matches."""

    def __init__(self, registry: ProviderRegistry):
        self.registry = registry
        # Scan.4: provider failures seen during this engine's lifetime (one
        # entry per provider — FIRST error wins so we don't spam). The scan
        # worker reads this after matching and raises a Notification, so a
        # bad/missing API key or a down provider shows up as a clear warning
        # instead of silently marking every file no_match.
        self.provider_errors: dict[str, str] = {}

    # Confidence above which we trust the preferred provider's top hit and
    # stop trying fallbacks. Below this, we keep going and merge results
    # across all providers — the preferred provider's mediocre match
    # shouldn't beat a much better match from a fallback provider.
    EARLY_EXIT_CONFIDENCE = 0.85

    # Anime-specific: AniDB is the authoritative anime source. If it
    # returns ANY result at or above this floor, take it — DO NOT fall
    # through to TVDB. Pitched HIGH (0.80) on purpose: real anime
    # matches hit 0.85+ or 1.0 because either the canonical title
    # matches exactly or an AniDB alias does. The 0.55–0.79 band is
    # fuzzy "could-be-right" territory — "One Pace" scores 0.73 against
    # "One Piece" because the strings differ by 1 character, but that
    # similarity isn't trustworthy: One Pace is a fan re-edit, not One
    # Piece. Better to mark as no_match and let the user decide.
    ANIME_ANIDB_TRUST_FLOOR = 0.80

    # Minimum confidence to return any match at all. Below this the
    # matcher returns [] so the file gets marked `no_match`. Anime gets
    # a stricter floor — fuzzy 1-char-off names ("One Pace" vs "One
    # Piece" = 0.67 trigram) shouldn't auto-match; the user organized
    # them under a different folder for a reason.
    MIN_CONFIDENCE = 0.55
    MIN_CONFIDENCE_ANIME = 0.80

    async def match(self, parsed: ParsedFile, limit: int = 5) -> list[ScoredMatch]:
        if parsed.media_type == "music":
            return []  # handled by separate AudioProvider path (future)

        # User-overridable per media_type (Settings → Matching). Falls back to
        # the PROVIDER_PREFERENCE default. _load_db_settings is cached (30s) and
        # invalidated on PUT /settings, so this is cheap per-file and picks up
        # a preference change without a restart.
        provider_order = resolve_provider_order(
            parsed.media_type, await _load_db_settings()
        )

        # Phase 14: explicit embedded provider ID → resolve directly, skip
        # title search entirely. Runs BEFORE the title guard because an
        # ID-tagged file may have a junk/empty title (that's WHY it carries
        # an ID). Only directly-resolvable providers (tmdb/tvdb/anidb); an
        # imdb-only tag is recorded but needs a /find call we don't do yet.
        ids = getattr(parsed, "provider_ids", None) or {}
        if ids:
            bypass = await self._match_by_embedded_id(parsed, provider_order, ids)
            if bypass:
                return bypass[:limit]

        if not parsed.title:
            return []

        all_scored: list[ScoredMatch] = []
        for key in provider_order:
            if not self.registry.has(key):
                continue
            try:
                scored = await self._match_with(key, parsed)
            except ProviderPermanentError as e:
                # Bad/missing key or invalid request — config problem the user
                # must fix. Record it (first wins) so the scan worker surfaces
                # a notification instead of leaving silent no-matches.
                self.provider_errors.setdefault(key, f"authentication / configuration error ({e})")
                logger.warning(f"matcher: provider {key} permanent error: {e!r}")
                continue
            except ProviderTransientError as e:
                # Retries exhausted — provider unreachable / timing out / down.
                self.provider_errors.setdefault(key, f"unreachable or timing out ({e})")
                logger.warning(f"matcher: provider {key} transient error: {e!r}")
                continue
            except Exception as e:
                # Provider raised something else — keep trying the others. We do
                # NOT discard previously-gathered scored matches here.
                self.provider_errors.setdefault(key, f"unexpected error ({type(e).__name__})")
                logger.info(f"matcher: provider {key} raised: {e!r}")
                continue
            if not scored:
                continue

            # Anime guardrail: when using a non-AniDB provider in an anime
            # context, the candidate MUST have a Fribb cross-ref to a known
            # AniDB AID. Without this, TVDB-only live-action dramas with
            # similar-looking titles ("One Page Love" ↔ "One Pace") land
            # as confident anime matches. Filtering drops them entirely.
            # R2-C3: pass the provider so the Fribb-empty fallback can
            # do a language check via get_series_extended.
            if parsed.media_type == "anime" and key != "anidb":
                try:
                    _filter_provider = self.registry.build(key)
                except Exception:
                    _filter_provider = None
                scored = await _filter_anime_to_known_aids(scored, key, _filter_provider)
                if not scored:
                    continue

            all_scored.extend(scored)

            # Anime early-exit: AniDB hit ≥ trust floor → take it. AniDB
            # IS the anime source of truth; don't second-guess it with
            # TVDB live-action drift.
            if (
                parsed.media_type == "anime" and key == "anidb"
                and scored[0].confidence >= self.ANIME_ANIDB_TRUST_FLOOR
            ):
                return scored[:limit]

            # General early-exit: high-confidence hit on preferred provider.
            if scored[0].confidence >= self.EARLY_EXIT_CONFIDENCE:
                return scored[:limit]

        # Walked all providers without a clear winner — return the global best
        # ranked across everyone we asked. Deterministic tiebreak on (provider,
        # provider_id): equal-confidence candidates (two 1.0s at the tier-1
        # ceiling is common) must resolve the SAME way every scan, not by
        # non-deterministic provider search-result order (e.g. AniDB title-dump
        # iteration). is_ambiguous in the cascade trace still flags these for a
        # future "needs resolution" surface.
        all_scored.sort(key=lambda s: (s.confidence, str(s.provider), str(s.provider_id)), reverse=True)

        # No-match floor: refuse to return junk. Caller treats empty as
        # `no_match` and renders the manual-search affordance.
        floor = self.MIN_CONFIDENCE_ANIME if parsed.media_type == "anime" else self.MIN_CONFIDENCE
        if not all_scored or all_scored[0].confidence < floor:
            return []

        return all_scored[:limit]

    async def _match_by_embedded_id(
        self, parsed: ParsedFile, provider_order: list[ProviderKey],
        ids: dict[str, str],
    ) -> list[ScoredMatch]:
        """Phase 14: resolve a file by an embedded provider ID, bypassing
        title search. Returns a single confidence-1.0 ScoredMatch (the ID is
        authoritative) or [] when no directly-resolvable ID is configured."""
        is_episode = parsed.media_type in ("tv", "anime")
        mt = "tv_episode" if is_episode else "movie"
        for key in provider_order:
            pid = ids.get(key)
            if not pid or not self.registry.has(key):
                continue
            meta = await _basic_meta_by_id(key, str(pid), mt, self.registry)
            # An embedded ID is only authoritative if it actually RESOLVES.
            # `_basic_meta_by_id` returns None when the get-by-id details call
            # finds nothing — a stale/typo'd ID, or one of the WRONG media type
            # (a movie TMDB id on a file we classified tv). Don't fabricate a
            # confidence-1.0 match for an ID that points at nothing: skip to the
            # next provider, else fall through to normal title search. (AniDB is
            # exempt — it returns a dict even with no display title, because the
            # AID itself is a valid identity.)
            if meta is None:
                logger.warning(f"matcher: embedded-id {key}:{pid} did not resolve ({mt}) — skipping")
                continue
            title = meta.get("title") or parsed.title or f"{key}:{pid}"
            logger.info(f"matcher: embedded-id match {key}:{pid} -> {title!r}")
            return [ScoredMatch(
                provider=key, provider_id=str(pid), match_type=mt,
                confidence=1.0, title=title,
                year=(meta or {}).get("year"),
                poster_url=(meta or {}).get("poster_url"),
                overview=(meta or {}).get("overview"),
                aliases=(meta or {}).get("aliases"),
                raw={"embedded_id": key},
            )]
        return []

    async def _match_with(self, key: ProviderKey, parsed: ParsedFile) -> list[ScoredMatch]:
        provider = self.registry.build(key)
        is_episode = parsed.media_type in ("tv", "anime")
        match_type = "tv_episode" if is_episode else "movie"

        # Build the query ladder: try the most specific first, then simplify.
        # Returns the first non-empty result set.
        #
        # Each rung's provider call is wrapped in retry-with-backoff so a
        # transient 502 / connection reset / timeout doesn't permanently
        # mark the file as no_match. A permanent 4xx (invalid key) raises
        # ProviderPermanentError and bubbles up immediately — fall through
        # to the next provider in PROVIDER_PREFERENCE.
        results: list[MovieResult | TVResult] = []
        for query, with_year in _query_ladder(parsed):
            year = parsed.year if with_year else None
            label = f"{key}.{'search_tv' if is_episode else 'search_movie'}({query!r})"
            try:
                if is_episode:
                    results = list(await _provider_call_with_retry(
                        lambda q=query, y=year: provider.search_tv(q, y), what=label))
                else:
                    results = list(await _provider_call_with_retry(
                        lambda q=query, y=year: provider.search_movie(q, y), what=label))
            except ProviderPermanentError as e:
                # Bad key / invalid request — no point trying other rungs
                # against the same provider. Re-raise for the outer caller
                # to skip this provider entirely.
                raise
            except ProviderTransientError as e:
                # All retries exhausted on this rung — fall through to the
                # next, simpler query; if it also fails the file ends up
                # no_match for this run. Next scan will retry.
                logger.info(f"matcher: {label} gave up: {e!r}")
                continue
            if results:
                break
        if not results:
            return []

        # Build ScoredMatch objects with placeholder confidence; the
        # cascade fills in the real number below. We keep the rank info
        # implicit via list order (cascade's RankMetric reads it).
        scored: list[ScoredMatch] = []
        for r in results:
            scored.append(ScoredMatch(
                provider=key,
                provider_id=r.provider_id,
                match_type=match_type,
                confidence=0.0,        # cascade overwrites this
                title=r.title,
                year=r.year,
                poster_url=r.poster_url,
                overview=r.overview,
                aliases=getattr(r, "aliases", None),
            ))

        # ── Cascade scoring ─────────────────────────────────────────
        # Every candidate goes through the same ordered metric pipeline.
        # The cascade aggregates per-tier (identity beats similarity
        # beats corroboration) and returns a CascadeTrace with the full
        # audit trail. We stash the trace on .raw so downstream code
        # (rematch path, popup hover) can render "why this confidence?"
        # without re-running the matcher.
        from kira.matcher.cascade import build_default_cascade, CascadeContext
        # Labs flags (opt-in, default off — Settings → Labs).
        runtime_on = await labs_flag("runtime_corroboration")
        boost_on = await labs_flag("episode_title_boost")
        cascade = build_default_cascade(
            provider_key=key, media_type=parsed.media_type, include_runtime=runtime_on,
        )
        _global_registry_ref.set(self.registry)
        ctx = CascadeContext(
            parsed=parsed,
            candidates=scored,
            series_folder_name=_series_folder_from_parsed(parsed),
            cluster_signal=getattr(parsed, "_cluster_signal", None),
            provider_key=key,
        )
        # Labs: episode-title series-boost (opt-in, BOUNDED + ban-safe). Pre-
        # populate the episode-title cache for the top-2 candidates so
        # EpisodeTitleMetric can boost the right same-titled show. ONLY for
        # TVDB/TMDB (fast, reliable get_episodes); NEVER AniDB — its rate-limited
        # get_episodes is exactly what froze scans, so the boost stays dormant
        # for anime (where AniDB's title dump already disambiguates). Capped at
        # 2 fetches per cluster, each through the connection-blip-tolerant retry.
        if boost_on and key in ("tvdb", "tmdb") and getattr(parsed, "episode_title_guess", None):
            _season = parsed.season if parsed.season is not None else 1
            for _cand in scored[:2]:
                _ck = ("ep_titles", key, _cand.provider_id, _season)
                if _ck not in ctx.enrich_cache:
                    try:
                        _eps = await _provider_call_with_retry(
                            lambda c=_cand: provider.get_episodes(c.provider_id, _season),
                            what=f"{key}.get_episodes(boost)",
                        )
                        ctx.enrich_cache[_ck] = _eps or []
                    except Exception:
                        ctx.enrich_cache[_ck] = []
        traces = await cascade.score_all(scored, ctx)
        for sm, trace in zip(scored, traces):
            sm.confidence = trace.final_score
            sm.raw = dict(sm.raw or {})
            sm.raw["cascade_trace"] = trace.to_dict()

        # Sort by cascade-derived confidence, with a deterministic tiebreak on
        # (provider, provider_id) so equal-confidence ties don't flip between
        # scans on non-deterministic candidate order.
        scored.sort(key=lambda s: (s.confidence, str(s.provider), str(s.provider_id)), reverse=True)

        # AniDB absolute → AID routing stays as a separate routing pass
        # (bipartite refinement at cluster-write time handles the per-
        # episode resolution; this just rewrites which AID the top
        # candidate points at when the file is pure-absolute).
        if (
            key == "anidb"
            and parsed.media_type == "anime"
            and parsed.absolute_episode is not None
            and parsed.season is None
        ):
            scored = await _route_anime_absolute_to_aid(provider, parsed, scored)

        return scored

    async def _rerank_anime_tvdb(
        self,
        provider: MetadataProvider,
        parsed: ParsedFile,
        scored: list[ScoredMatch],
    ) -> list[ScoredMatch]:
        """Boost anime-flavoured TVDB results, penalize live-action remakes.

        Fetches /series/{id}/extended for the top 5 candidates and applies:
          - +0.15 if originalCountry == 'jpn' OR originalLanguage == 'jpn'
          - +0.10 confidence-only bump when an alias matches the parsed
            title better than the canonical display title — we never
            REPLACE the canonical title with the matched alias (that
            would pollute the Library with mixed romaji / English /
            Japanese names based on how each file happened to be named).
          - -0.20 if genres include obviously-not-anime markers
        """
        if not hasattr(provider, "get_series_extended"):
            return scored

        # Only inspect the top candidates — extra TVDB calls are expensive.
        head = scored[:5]
        tail = scored[5:]

        async def enrich(sm: ScoredMatch) -> ScoredMatch:
            try:
                ext = await provider.get_series_extended(sm.provider_id)  # type: ignore[attr-defined]
            except Exception:
                return sm
            if not ext:
                return sm
            new_conf = sm.confidence

            origin = (ext.get("original_country") or "").lower()
            lang = (ext.get("original_language") or "").lower()
            if origin == "jpn" or lang == "jpn" or origin == "jp" or lang == "ja":
                new_conf = min(1.0, new_conf + 0.15)

            # Score aliases against the parsed title — used only as a
            # confidence signal. The canonical title from the provider
            # stays as-is so the Library displays consistent names
            # regardless of how the user spelled the filename.
            best_alias_sim = 0.0
            for alias in (ext.get("aliases") or []):
                if not isinstance(alias, str):
                    continue
                sim = trigram_similarity(parsed.title, alias)
                if sim > best_alias_sim:
                    best_alias_sim = sim
            primary_sim = trigram_similarity(parsed.title, sm.title)
            if best_alias_sim > primary_sim + 0.05:
                new_conf = min(1.0, new_conf + 0.10)

            # Penalize obvious live-action / non-anime genres.
            genres = {(g or "").lower() for g in (ext.get("genres") or [])}
            if genres & {"live action", "live-action", "reality", "talk show", "news"}:
                new_conf = max(0.0, new_conf - 0.20)

            return ScoredMatch(
                provider=sm.provider, provider_id=sm.provider_id,
                match_type=sm.match_type, confidence=new_conf,
                title=sm.title, year=sm.year,
                poster_url=sm.poster_url, overview=sm.overview,
            )

        # Run the 5 enrich calls in parallel.
        enriched = await asyncio.gather(*(enrich(sm) for sm in head))
        return list(enriched) + tail


# Roman numerals 2-12 — covers basically every anime franchise; we only
# look up roman when season >= 2, so "I" isn't in the map.
_ROMAN = {2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII",
          9: "IX", 10: "X", 11: "XI", 12: "XII"}

# Ordinal words AniDB uses on its main titles: `2nd Season`, `3rd Season`, etc.
_ORDINAL = {1: "1st", 2: "2nd", 3: "3rd"}
def _ordinal(n: int) -> str:
    return _ORDINAL.get(n, f"{n}th")


import re as _re_mod  # local alias so test-only imports don't shadow the engine's `re`


def _rerank_anime_by_season(season: int, scored: list[ScoredMatch]) -> list[ScoredMatch]:
    """Boost candidates whose title (or any alias) carries the season ordinal.

    AniDB stores every season of a franchise as a separate AID. The English
    display title for AID 17629 (Rent-a-Girlfriend S3) reads
    `"Kanojo, Okarishimasu 3rd Season"`. The S1 AID (15299) reads bare
    `"Kanojo, Okarishimasu"`. With a season=3 hint we boost the former and
    knock down the latter so the cluster lands on the right AID.
    """
    ord_word = _ordinal(season)
    roman = _ROMAN.get(season)
    season_terms = [
        rf"\b{ord_word}\s+season\b",
        rf"\bseason\s+0?{season}\b",
        rf"\bpart\s+0?{season}\b",
        rf"\bs{season}\b",
    ]
    if roman:
        # Roman pads to a hard word boundary so "II" doesn't match "Twins II".
        season_terms.append(rf"(?:\s|^|:){roman}(?:\s|$|:)")
    season_re = _re_mod.compile("|".join(season_terms), _re_mod.IGNORECASE)

    # Skip the rerank entirely when NO candidate carries an ordinal hint —
    # this is the long-running-single-AID case (One Piece has one AID
    # titled "One Piece" covering 1100+ episodes; users shelf it into
    # "Season 23" folders for Plex layout, not because AniDB knows about
    # such a season). Penalizing the lone "One Piece" candidate here would
    # demote a perfectly correct match. The rerank only applies to
    # franchises where AniDB actually splits seasons into separate AIDs.
    has_ordinal_candidate = any(
        any(season_re.search(t or "") for t in [sm.title] + list(sm.aliases or []))
        for sm in scored
    )
    if not has_ordinal_candidate:
        return scored

    rescored: list[ScoredMatch] = []
    for sm in scored:
        haystack = [sm.title] + list(sm.aliases or [])
        hit = any(season_re.search(t or "") for t in haystack)
        if hit:
            new_conf = min(1.0, sm.confidence + 0.20)
        else:
            # Plain title without any season indicator is most likely S1
            # — penalize when the parser knows we wanted a later season.
            new_conf = max(0.0, sm.confidence - 0.15)
        rescored.append(ScoredMatch(
            provider=sm.provider, provider_id=sm.provider_id,
            match_type=sm.match_type, confidence=new_conf,
            title=sm.title, year=sm.year,
            poster_url=sm.poster_url, overview=sm.overview,
            aliases=sm.aliases,
        ))
    return rescored


async def _filter_anime_to_known_aids(
    scored: list[ScoredMatch],
    provider_key: ProviderKey,
    provider: MetadataProvider | None = None,
) -> list[ScoredMatch]:
    """For non-AniDB providers in an anime context, keep only candidates
    that have a Fribb cross-ref to an AniDB AID.

    Rationale: an anime file should match to an anime entity. TVDB and
    TMDB both contain Japanese live-action dramas, talk shows, and
    variety programs — none of which are anime. Without this filter,
    "One Pace - s10e01.mp4" falls through to TVDB and matches "One Page
    Love" (a 2019 Japanese romance drama) at 80% because the trigram
    accidentally lines up.

    Fribb's anime-list dataset is the source of truth for "is this TVDB
    series a known anime?" — if the series ID is in there, it's anime
    or anime-adjacent. If it's not, drop it.

    Cheap: in-memory dict lookups, no HTTP.

    ── R2-C3: Fribb-empty fallback now uses language metadata ─────────
    The Round-1 fallback (just pass everything through) silently wiped
    TVDB-only anime libraries: results passed the filter but then failed
    the 0.80 anime confidence floor because none had been boosted by
    `_rerank_anime_tvdb` yet (rerank runs AFTER this filter). The fix
    is to perform a minimal language check INSIDE this function when
    Fribb is empty: fetch get_series_extended for high-trigram candidates
    only (>= 0.60 to bound the cost) and keep those flagged as JP-origin
    or Japanese-language. Drops non-anime live-action Japanese drama in
    the same flow that catches genuine anime.
    """
    from kira.providers.anime_mappings import AnimeMappings
    # Force-load (cheap if already loaded) so we can inspect the cache size.
    try:
        await AnimeMappings._ensure_loaded()
    except Exception:
        pass
    # R2-C3 short-circuit: empty Fribb → fetch extended metadata for
    # high-trigram candidates only, keep the JP-origin ones.
    if not AnimeMappings._by_aid:
        logger.info("matcher: Fribb cache empty; falling back to language-based anime detection.")
        if provider is None or not hasattr(provider, "get_series_extended"):
            # No way to language-check — fall back to unfiltered pass.
            # Anime floor (0.80) will filter most junk; some junk may
            # leak through but user can manually reject.
            return scored

        out_lang: list[ScoredMatch] = []
        # Only language-check the top candidates worth investigating.
        # Pre-gate on confidence so a 0.20-trigram garbage candidate
        # doesn't trigger an HTTP call. 0.60 is the matcher's MIN_CONFIDENCE
        # floor for non-anime; below this no amount of language boost will
        # clear the 0.80 anime floor.
        for sm in scored:
            if sm.confidence < 0.60:
                continue
            try:
                ext = await provider.get_series_extended(sm.provider_id)  # type: ignore[attr-defined]
            except Exception:
                # On fetch failure, be conservative — drop the candidate
                # rather than risk a false positive. User can manually
                # search if needed.
                continue
            if not ext:
                continue
            origin = (ext.get("original_country") or "").lower()
            lang = (ext.get("original_language") or "").lower()
            if origin in ("jpn", "jp") or lang in ("jpn", "ja"):
                out_lang.append(sm)
        return out_lang

    out: list[ScoredMatch] = []
    for sm in scored:
        try:
            pid = int(sm.provider_id)
        except (ValueError, TypeError):
            continue
        if provider_key == "tvdb":
            aid = await AnimeMappings.aid_by_tvdb(pid)
        elif provider_key == "tmdb":
            aid = await AnimeMappings.aid_by_tmdb_tv(pid)
        else:
            aid = None
        if aid:
            out.append(sm)
    return out


async def _rerank_anime_by_fribb_season(parsed_season: int, scored: list[ScoredMatch]) -> list[ScoredMatch]:
    """Use Fribb cross-ref as authoritative truth on which AID matches a
    given (TVDB series, season) pair.

    Concrete example: a file named "Bleach.S17E27" returns from AniDB
    search:
      - AID 2369  "Bleach"                         (trigram 1.0)
      - AID 15449 "Bleach: Thousand-Year Blood War" (trigram ~0.55)

    Trigram alone picks AID 2369. But Fribb says:
      - AID 2369  → tvdb_id=74796, season=None    (not season-specific)
      - AID 15449 → tvdb_id=74796, season=17      (definitive S17)

    parsed.season=17 means the user has TVDB Season 17 of Bleach. Fribb's
    `(74796, 17)` reverse lookup is unambiguous: AID 15449. Override the
    matcher's choice.

    Override conditions:
      - Exactly ONE candidate has Fribb season == parsed_season AND that
        AID shares a tvdb_id with at least one OTHER candidate in the list
        (i.e. it's part of a franchise that AniDB returned). Force to top
        with confidence 1.0.
      - Multiple matches → soft boost.
      - Zero matches OR matching AID isn't a franchise sibling → leave
        scored as-is.

    The franchise-sibling guard is critical: without it, "BLEACH Thousand-
    Year Blood War" with parsed.season=1 would pick AID 366 (Queen
    Millennia, an unrelated 1981 anime that happens to have Fribb
    season=1) over AID 15449 (Bleach: TYBW, which actually IS the show
    but has Fribb season=17 not 1). Restricting to franchise siblings
    means "pick among Bleach AIDs", not "pick from any AID anywhere
    that happens to share a season number with this folder".

    Pure in-memory — Fribb dict already loaded on startup. No HTTP.
    """
    from kira.providers.anime_mappings import AnimeMappings

    # Single pass — collect each candidate's Fribb tvdb_id + season.
    # mapped_seasons[i] = None means Fribb has no opinion on this AID
    # (typically the umbrella entry for a long-running show like the
    # original Bleach AID 2369); = N means Fribb pins it to TVDB S{N}.
    mapped_seasons: list[int | None] = []
    mapped_tvdb_ids: list[int | None] = []
    for sm in scored:
        try:
            aid_i = int(sm.provider_id)
            mapped_seasons.append(await AnimeMappings.tvdb_season(aid_i))
            mapped_tvdb_ids.append(await AnimeMappings.tvdb_id(aid_i))
        except Exception:
            mapped_seasons.append(None)
            mapped_tvdb_ids.append(None)

    matching = [sm for sm, m in zip(scored, mapped_seasons) if m == parsed_season]

    if not matching:
        # No candidate matches the requested season. Defensive penalty:
        # if SOME candidates have explicit Fribb mappings that CONTRADICT
        # the requested season, demote them. Candidates with no Fribb
        # opinion (mapped=None) stay neutral. This catches the case where
        # parsed.season comes from a partial mapping or where the user's
        # season number doesn't appear in Fribb at all — better to surface
        # uncertainty than silently pick a known-wrong AID.
        any_explicit = any(m is not None for m in mapped_seasons)
        if not any_explicit:
            return scored
        rescored: list[ScoredMatch] = []
        for sm, m in zip(scored, mapped_seasons):
            penalty = 0.0
            if m is not None and m != parsed_season:
                penalty = 0.20
            new_conf = max(0.0, sm.confidence - penalty) if penalty else sm.confidence
            rescored.append(ScoredMatch(
                provider=sm.provider, provider_id=sm.provider_id,
                match_type=sm.match_type, confidence=new_conf,
                title=sm.title, year=sm.year,
                poster_url=sm.poster_url, overview=sm.overview,
                aliases=sm.aliases,
            ))
        rescored.sort(key=lambda s: s.confidence, reverse=True)
        return rescored

    if len(matching) == 1:
        winner = matching[0]
        # H8: Franchise-sibling guard relaxed. Previously: only promote
        # the matched AID if at least one OTHER candidate in the scored
        # list shares its tvdb_id. That was too strict — if AniDB search
        # didn't surface the franchise opener (S1 AID) in the top 10
        # results, the sibling check failed and the correct S17 AID
        # never got promoted. Real example: "Bleach S17E27" search
        # returns the umbrella AID 2369 + 5 other Bleach-ish AIDs but
        # NOT AID 15449 (the actual S17 Thousand-Year Blood War). Old
        # code refused to inject 15449 because it wasn't "in the list".
        #
        # New rule: trust the Fribb mapping unconditionally when:
        #   - the winner has a tvdb_id (Fribb knows it)
        #   - AND no other candidate carries a CONTRADICTORY tvdb_id
        #     (so we're not picking a totally unrelated show)
        # The "Queen Millennia happens to be season 1" failure mode is
        # caught by the existing trigram floor — Queen Millennia won't
        # score high enough on a "Bleach" search to even become a
        # candidate.
        #
        # **TITLE-SIMILARITY GATE** (One Pace fix). The contradictor
        # check above presumes the *correct* show is in the candidate
        # list. When the AniDB search filters out the real target
        # because the parsed title scores below the 0.15 trigram floor
        # (M7 short-title penalty does this on purpose for "One Pace"
        # vs "One Piece"), the real show never enters `scored` — so it
        # can't contradict the winner. Result: ANOTHER show with the
        # same Fribb season number (e.g. "ONE: Kagayaku Kisetsu e",
        # which happens to be TVDB S1 of a totally unrelated 2001 OVA)
        # gets the 1.0 promotion by default.
        #
        # The fix: require the AID's OWN pre-promotion confidence to be
        # at least PROMOTION_MIN_CONF. Below that, the title genuinely
        # doesn't look like the filename — the Fribb (tvdb_id, season)
        # match is coincidence, not identity. Bail out and let the
        # MIN_CONFIDENCE_ANIME floor drop the cluster to no_match,
        # which is the correct outcome for a fan-edit nobody's database
        # actually catalogues.
        PROMOTION_MIN_CONF = 0.55
        if winner.confidence < PROMOTION_MIN_CONF:
            return scored
        try:
            winner_idx = scored.index(winner)
            winner_tvdb = mapped_tvdb_ids[winner_idx]
        except (ValueError, IndexError):
            winner_tvdb = None
        # Block promotion only if a HIGHER-confidence candidate maps to
        # a DIFFERENT tvdb_id — that would mean the search clearly
        # wanted a different show, not just a different season of ours.
        contradicted = winner_tvdb is not None and any(
            tvid is not None and tvid != winner_tvdb
            and idx < winner_idx  # higher-confidence rival
            for idx, tvid in enumerate(mapped_tvdb_ids)
        )
        if contradicted:
            return scored
        if winner_tvdb is None:
            # Fribb has no opinion on the winner — fall back to old
            # sibling check so we don't blindly promote a no-info row.
            has_sibling = any(
                i != winner_idx and tvid is not None
                for i, tvid in enumerate(mapped_tvdb_ids)
            )
            if not has_sibling:
                return scored
        # Unambiguous Fribb hit AND franchise-confirmed — force to top.
        rescored: list[ScoredMatch] = []
        for sm in scored:
            if sm is winner:
                rescored.append(ScoredMatch(
                    provider=sm.provider, provider_id=sm.provider_id,
                    match_type=sm.match_type, confidence=1.0,
                    title=sm.title, year=sm.year,
                    poster_url=sm.poster_url, overview=sm.overview,
                    aliases=sm.aliases,
                ))
            else:
                # Cap others at 0.95 so the winner sorts first deterministically.
                rescored.append(ScoredMatch(
                    provider=sm.provider, provider_id=sm.provider_id,
                    match_type=sm.match_type, confidence=min(sm.confidence, 0.95),
                    title=sm.title, year=sm.year,
                    poster_url=sm.poster_url, overview=sm.overview,
                    aliases=sm.aliases,
                ))
        rescored.sort(key=lambda s: s.confidence, reverse=True)
        return rescored

    # Multiple matches — this happens when Fribb maps several AIDs to the
    # same TVDB season (e.g. Bleach TYBW arc-cours all map to TVDB S17).
    # CRITICAL: only promote when matching AIDs share a tvdb_id (= they're
    # cours of the same series). Without this guard, common season=1 hint
    # would promote a random low-AID show ("One: Kagayaku Kisetsu e" for
    # "One Pace s01") over the correct trigram winner ("One Piece").
    matching_indexes = [i for i, m in enumerate(mapped_seasons) if m == parsed_season]
    matching_tvdb = {mapped_tvdb_ids[i] for i in matching_indexes if mapped_tvdb_ids[i] is not None}
    if len(matching_tvdb) != 1:
        # Matching candidates come from multiple unrelated shows — Fribb
        # has no useful signal here. Bail out and trust trigram scoring.
        return scored

    # Collect matching AIDs (same tvdb_id confirmed above).
    matching_aids: list[int] = []
    for sm in matching:
        try:
            matching_aids.append(int(sm.provider_id))
        except (ValueError, TypeError):
            pass
    winner_aid = min(matching_aids) if matching_aids else None

    rescored: list[ScoredMatch] = []
    for sm, m in zip(scored, mapped_seasons):
        try:
            aid_i = int(sm.provider_id)
        except (ValueError, TypeError):
            aid_i = None
        if aid_i == winner_aid:
            new_conf = 1.0
        elif m == parsed_season:
            # Another franchise sibling that's also a valid TVDB-season
            # candidate — kept just below the winner.
            new_conf = 0.95
        elif m is not None and m != parsed_season:
            # Explicit contradiction — drop hard so it can't win.
            new_conf = max(0.0, sm.confidence - 0.30)
        else:
            # No Fribb opinion (m is None). Cap at 0.90 so the explicitly-
            # confirmed winner wins regardless of base trigram.
            new_conf = min(sm.confidence, 0.90)
        rescored.append(ScoredMatch(
            provider=sm.provider, provider_id=sm.provider_id,
            match_type=sm.match_type, confidence=new_conf,
            title=sm.title, year=sm.year,
            poster_url=sm.poster_url, overview=sm.overview,
            aliases=sm.aliases,
        ))
    rescored.sort(key=lambda s: s.confidence, reverse=True)
    return rescored


async def _route_anime_absolute_to_aid(
    provider: MetadataProvider,
    parsed: ParsedFile,
    scored: list[ScoredMatch],
) -> list[ScoredMatch]:
    """Reroute a pure-absolute file (e.g. `My Hero - 014`) to the right
    AniDB AID via the franchise offset table.

    Without this, the matcher picks the highest-trigram AID (almost
    always the franchise S1) and tries to find episode 14 — which
    doesn't exist in a 13-episode S1. The file ends up matched to S1
    with no episode_title, mis-clustered in the UI.

    With this, we look up which AID's absolute range covers
    `parsed.absolute_episode` and rewrite the top match in place. The
    derived local episode (absolute - cumulative_offset) gets stashed
    on the ScoredMatch.raw dict so the episode-title lookup later can
    use it directly.

    ── M8: Walk from any franchise member, not just the root ──────────
    Previously assumed `top.provider_id` was the franchise root (S1
    AID). For shows where the search returned S2/S3 as top candidate
    instead of S1 (because trigram favored a longer "Season 2" title),
    that AID's offsets table starts at episode 1 of S2, not absolute 1
    of the franchise. The reroute then picked the wrong target.
    Fix: canonicalize via the relations chain (lowest AID = root) and
    fetch offsets keyed on the root so the abs→aid math is always
    against the franchise's absolute numbering, not a member's local
    numbering.

    Bails out (returns scored unchanged) when:
      - No top candidate (matcher returned nothing)
      - Top candidate isn't part of a multi-AID franchise
      - Franchise offset table can't be built (banned AniDB, partial
        episode-count cache, etc.)
      - The absolute episode already falls within the current AID's range
    """
    if not scored:
        return scored
    top = scored[0]
    if not hasattr(provider, "get_franchise_offsets"):
        return scored
    try:
        current_aid = int(top.provider_id)
    except (ValueError, TypeError):
        return scored
    # M8 + R2-H5: canonicalize ONLY when we have a confirmed multi-AID
    # chain. A partial cache (some related AIDs cached, others not) or
    # a transient error returns an incomplete chain — in those cases
    # `min(chain)` could yield the wrong root and offset math goes off
    # the rails. Better to bail and leave the file matched to its
    # current AID than reroute to a worse one.
    try:
        if not hasattr(provider, "get_related_aids"):
            return scored
        chain = await provider.get_related_aids(str(current_aid))  # type: ignore[attr-defined]
    except Exception:
        return scored
    # Need a complete chain of at least 2 AIDs to make routing sensible.
    # A single-entry chain means we don't know the franchise structure
    # (could be a single-AID show OR a partial cache); either way, don't
    # try to reroute. Single-AID franchises naturally fall through here.
    if not chain or len(chain) < 2:
        return scored
    canonical = min(chain)
    try:
        offsets = await provider.get_franchise_offsets(canonical)  # type: ignore[attr-defined]
    except Exception:
        return scored
    if not offsets or len(offsets) < 2:
        # Franchise table truncated — bail rather than route on partial data.
        return scored

    target_aid: int | None = None
    local_ep: int | None = None
    for aid_, start, end in offsets:
        if start <= parsed.absolute_episode <= end:  # type: ignore[operator]
            target_aid = aid_
            local_ep = parsed.absolute_episode - (start - 1)  # type: ignore[operator]
            break
    if target_aid is None or target_aid == current_aid:
        return scored

    # Rewrite the top match. Confidence stays the same (we're not
    # changing match strength, just correcting which AID it points at).
    # raw["local_episode"] hands the derived per-AID episode number to
    # downstream code (episode-title lookup) so it doesn't have to redo
    # the offset math.
    #
    # Fix #4: refresh the title from the NEW AID's in-memory title cache.
    # The previous code carried over `top.title` (the OLD AID's title)
    # which leaked into Match.title and ultimately into the renamed
    # folder name — files for `My Hero Academia` S2 absolute-episode
    # got rerouted to the S2 AID but kept the S1 AID's title, producing
    # the wrong show folder in the user's library. Pure in-memory
    # lookup, zero HTTP, safe even during AniDB bans.
    rerouted_title = top.title
    try:
        from kira.providers.anidb import AniDBProvider
        cached_title = AniDBProvider._pick_display_title(int(target_aid))
        if cached_title:
            rerouted_title = cached_title
    except Exception:
        pass

    rewritten = ScoredMatch(
        provider=top.provider,
        provider_id=str(target_aid),
        match_type=top.match_type,
        confidence=top.confidence,
        title=rerouted_title,
        year=top.year,
        poster_url=top.poster_url,
        overview=top.overview,
        aliases=top.aliases,
        raw={"local_episode": local_ep, "rerouted_from": current_aid},
    )
    return [rewritten] + scored[1:]


async def _basic_meta_by_id(
    provider_key: ProviderKey,
    provider_id: str,
    match_type: str,
    registry: "ProviderRegistry",
) -> dict | None:
    """Best-effort `{title, year, poster_url, overview, aliases}` for an
    explicit provider ID — feeds the Phase 14 embedded-ID bypass.

    AniDB: pure in-memory (display title + alt-titles from the loaded dump;
    None when the dump isn't loaded — caller falls back to the parsed title).
    TMDB / TVDB: one get-by-id details call (the detail methods now surface
    title/year/poster_url). Returns None on any failure.
    """
    try:
        provider = registry.build(provider_key)
    except Exception:
        return None
    try:
        if provider_key == "anidb":
            from kira.providers.anidb import AniDBProvider
            try:
                aid = int(provider_id)
            except (TypeError, ValueError):
                return None
            title = AniDBProvider._pick_display_title(aid)
            ents = (AniDBProvider._titles or {}).get(aid) or []
            aliases = [t for _ty, _lang, t in ents] or None
            return {"title": title, "year": None, "poster_url": None,
                    "overview": None, "aliases": aliases}
        d: dict | None = None
        if match_type == "movie" and hasattr(provider, "get_movie_details"):
            d = await provider.get_movie_details(provider_id)
        elif hasattr(provider, "get_tv_details"):
            d = await provider.get_tv_details(provider_id)
        elif hasattr(provider, "get_series_extended"):
            d = await provider.get_series_extended(provider_id)
        if not d:
            return None
        return {
            "title": d.get("title"), "year": d.get("year"),
            "poster_url": d.get("poster_url"), "overview": d.get("overview"),
            "aliases": d.get("aliases"),
        }
    except Exception:
        return None


async def fetch_match_metadata(
    provider_key: ProviderKey,
    provider_id: str,
    match_type: str,
    registry: "ProviderRegistry",
) -> dict | None:
    """Fetch the rich popup metadata for a top match.

    Dispatches to the provider's `get_movie_details` / `get_tv_details` /
    `get_series_extended` / `get_display_extras` depending on what's
    available. Returns None on error so callers can skip the write
    instead of clobbering a previously good blob.

    Calls happen ONCE per series cluster, not once per file — the matcher
    runs this on the top match only.

    ── RATE-LIMIT SAFETY AUDIT ─────────────────────────────────────────
    Per-provider HTTP cost:
      - **AniDB**: ZERO HTTP. `get_display_extras` is a pure dict read
        from the already-loaded title cache. Safe to call even while
        AniDB is IP-banned — and we'd WANT it to, so titles still show.
      - **TMDB**: 1 call. TMDB's documented limit is 50 req/sec; we use
        ≤2 req per cluster (search + this) and clusters loop sequentially.
        No bans observed in the wild for this rate.
      - **TVDB**: 1 call (`/series/{id}/extended` or `/movies/{id}/extended`).
        TVDB has no published hard rate limit beyond fair use; we make
        ≤3 req per cluster. No risk.

    Defensive bouncer: if a future change adds an HTTP path inside
    `get_display_extras`, this function STILL won't hit AniDB during a
    ban because we check `is_banned()` before dispatching to the anidb
    branch — except that the in-memory branch is fine. The result: even
    during a ban we deliver title metadata; we only skip when there's
    no useful work to do.
    """
    try:
        provider = registry.build(provider_key)
    except Exception:
        return None
    try:
        if provider_key == "anidb":
            # AniDB-matched files get a LAYERED metadata blob:
            #   1. AniDB in-memory display extras (romaji, native,
            #      alt_titles, original_language=jpn, original_country=jpn)
            #      — authoritative for anime-specific title fields,
            #      pure in-memory, zero HTTP, safe during ban.
            #   2. TVDB/TMDB cross-ref details (cast, director, network,
            #      studio, runtime, genres) layered ON TOP — AniDB never
            #      exposes these cheaply, but they exist for the SAME
            #      show under its TVDB/TMDB entry via Fribb mapping.
            #      These calls don't share AniDB's rate limit and work
            #      during the AniDB ban.
            extras: dict = {}
            if hasattr(provider, "get_display_extras"):
                extras = provider.get_display_extras(provider_id) or {}  # type: ignore[attr-defined]
            cross = await _anime_metadata_via_cross_ref(provider_id, registry)
            # Merge: TVDB/TMDB fields fill gaps, but AniDB's titles win
            # (we don't want the romaji-from-Fribb to overwrite the AniDB
            # x-jat that the title-dump picker chose).
            for k, v in cross.items():
                if not v:
                    continue
                if k in ("title_romaji", "title_native", "alt_titles",
                         "original_language", "original_country"):
                    # AniDB owns these — only fill if extras is missing.
                    extras.setdefault(k, v)
                else:
                    # Cross-ref owns these (cast, genres, etc.).
                    extras[k] = v
            return extras or None
        if match_type == "movie":
            if hasattr(provider, "get_movie_details"):
                return await provider.get_movie_details(provider_id)  # type: ignore[attr-defined]
            return None
        # tv_episode
        if hasattr(provider, "get_tv_details"):
            return await provider.get_tv_details(provider_id)  # type: ignore[attr-defined]
        if hasattr(provider, "get_series_extended"):
            return await provider.get_series_extended(provider_id)  # type: ignore[attr-defined]
    except Exception as e:
        logger.warning(f"fetch_match_metadata: {provider_key}/{provider_id} failed: {e!r}")
        return None
    return None


async def _anime_metadata_via_cross_ref(
    aid: str,
    registry: "ProviderRegistry",
) -> dict:
    """Fetch rich metadata for an AniDB AID via the Fribb cross-ref to
    TVDB or TMDB. Returns whatever the cross-ref provider exposes that
    AniDB doesn't cheaply: cast, director, network, studio, runtime,
    genres. Empty dict on any failure / missing mapping.

    Cost: 1 TVDB or 1 TMDB HTTP call per AniDB cluster (caller invokes
    this once per cluster, not per file). TVDB and TMDB have generous
    quotas and aren't affected by the AniDB rate limit.
    """
    try:
        from kira.providers.anime_mappings import AnimeMappings
        aid_i = int(aid)
    except (ValueError, TypeError):
        return {}

    # Cross-ref order honors `matching.anime_crossref_order` (default TVDB-first
    # — richer character/cast data; TMDB fallback). First non-empty result wins.
    order = resolve_anime_crossref_order(await _load_db_settings())

    async def _from_tvdb() -> dict:
        tvdb_id = await AnimeMappings.tvdb_id(aid_i)
        if tvdb_id and registry.has("tvdb"):
            tvdb = registry.build("tvdb")
            if hasattr(tvdb, "get_series_extended"):
                return await tvdb.get_series_extended(str(tvdb_id)) or {}  # type: ignore[attr-defined]
        return {}

    async def _from_tmdb() -> dict:
        tmdb_id = await AnimeMappings.tmdb_tv_id(aid_i)
        if tmdb_id and registry.has("tmdb"):
            tmdb = registry.build("tmdb")
            if hasattr(tmdb, "get_tv_details"):
                return await tmdb.get_tv_details(str(tmdb_id)) or {}  # type: ignore[attr-defined]
        return {}

    fetchers = {"tvdb": _from_tvdb, "tmdb": _from_tmdb}
    for key in order:
        fn = fetchers.get(key)
        if fn is None:
            continue
        try:
            data = await fn()
            if data:
                return data
        except Exception:
            pass

    return {}


async def resolve_canonical_season(
    provider_key: ProviderKey,
    provider_id: str,
    parsed_season: int | None,
    episode: int | None = None,
) -> int | None:
    """Return the authoritative season number for this match.

    When `episode` is known, the ScudLee anime-lists per-EPISODE TVDB season is
    consulted FIRST — it's the only signal that splits a flat absolute umbrella
    (One Piece AID 69) into its real per-arc TVDB seasons (ep 1156 → S23). The
    rename uses the SAME resolver, so the stored season equals what we write.

    For AniDB matches, the Fribb cross-reference IS the ground truth: each
    AID maps to exactly one TVDB/TMDB season number, regardless of how the
    user named their folders. Without this override, two files in
    different folders ("Season 23" vs "Season 1") would store divergent
    season_numbers even though they belong to the same anime, and the
    frontend's "Season N" badge would be derived from a year-asc heuristic
    instead of provider truth.

    For TMDB/TVDB matches, `parsed.season` already came from the filename
    or path and matches the provider's season layout — no override needed.

    Returns None when no signal is available (caller keeps whatever they had).
    """
    if provider_key == "anidb":
        try:
            from kira.providers.anime_mappings import AnimeMappings
            aid = int(provider_id)
            # ScudLee per-episode TVDB season — most precise; the only thing that
            # gives a flat absolute umbrella (One Piece) a real per-arc season.
            scud_missed = False
            if episode is not None:
                from kira.providers.anime_lists import resolve_anidb_to_tvdb
                scud = await resolve_anidb_to_tvdb(aid, episode)
                if scud is not None:
                    return scud[0]
                # A CONCRETE episode that ScudLee can't place yet — a brand-new
                # absolute (One Piece ep 1166) not in the mapping table. This is
                # categorically different from "no episode signal at all": the
                # episode genuinely belongs to the show, ScudLee is just stale.
                # Remembered so the flat-umbrella fallback below keeps it beside
                # its already-catalogued siblings instead of collapsing to S1.
                scud_missed = True
            mapped = await AnimeMappings.tvdb_season(aid)
            if mapped is not None:
                return mapped
            # Fribb has NO season for this AID — classic mapping-database rot for a
            # brand-new cour (e.g. Bleach: TYBW "The Calamity", AID 19079, which the
            # mapping hasn't linked to TVDB S17 yet). Falling back to parsed_season
            # then defaults it to the franchise base — Season 1 — fracturing the
            # show. Instead inherit from the nearest PREQUEL cour of the SAME TVDB
            # series: the immediately-preceding mapped AID (The Conflict = S17), so
            # the new cour lands beside its siblings, not in Season 1.
            tvdb = await AnimeMappings.tvdb_id(aid)
            if tvdb is not None:
                seasoned: list[tuple[int, int]] = []   # POSITIVE-season siblings only
                saw_zero_sibling = False
                for sib in await AnimeMappings.aids_by_tvdb(tvdb):
                    if sib == aid:
                        continue
                    s = await AnimeMappings.tvdb_season(sib)
                    # A movie/special sibling maps to season 0; its season must
                    # NOT define the main run's — inheriting 0 collapses regular
                    # episodes toward "Specials". Keep only real (positive) cours.
                    if s is not None and s > 0:
                        seasoned.append((sib, s))
                    elif s == 0:
                        saw_zero_sibling = True
                if seasoned:
                    below = [(a, s) for (a, s) in seasoned if a < aid]
                    # nearest prequel (highest AID below this one); else nearest sibling
                    pick = max(below or seasoned, key=lambda t: t[0])
                    return pick[1]
                if saw_zero_sibling:
                    # Flat umbrella: a seasonless, absolute-numbered series (One
                    # Piece AID 69, Naruto, Detective Conan …) whose ONLY mapped
                    # siblings are its movies/specials (all season 0). There is no
                    # real per-cour season, so pin ONE stable basis — Season 1 — for
                    # the whole series. Returning 0 here used to send regular
                    # episodes toward Specials; the rename guard then papered over it
                    # by echoing each FILE'S parsed Sxx, scattering the library
                    # (ep 1165 → "Season 23", 1156-1164 → "Season 1"). The absolute
                    # episode number still rides in the filename; only the folder
                    # unifies. (A genuine special's OWN aid maps to season 0 via the
                    # `tvdb_season(aid)` check above and is unaffected.)
                    #
                    # EXCEPTION — a brand-new episode ScudLee hasn't catalogued yet
                    # (scud_missed): its arc-mates (ep 1156-1165) already resolved to
                    # their real per-arc season via ScudLee, so collapsing JUST the
                    # new one to S1 is what splits the card (ep 1166 → S1 beside ep
                    # 1165 → S23). Inherit the folder season so the newcomer lands
                    # with its siblings. Only fires when we HAD a concrete episode
                    # AND a folder season — the no-signal call still unifies to S1
                    # (locked by test_flat_umbrella_pins_season_1).
                    if scud_missed and parsed_season is not None:
                        return parsed_season
                    return 1
        except Exception:
            pass
    return parsed_season


def _query_ladder(parsed: ParsedFile) -> list[tuple[str, bool]]:
    """Build progressively-simpler queries to try in order.

    Returns list of (query, include_year). Stops at the simplest meaningful
    form so we don't spam the API with degenerate queries.
    """
    title = (parsed.title or "").strip()
    if not title:
        return []

    ladder: list[tuple[str, bool]] = []
    seen: set[str] = set()

    def add(q: str, with_year: bool) -> None:
        q = q.strip()
        key = f"{q}|{with_year}"
        if q and key not in seen:
            ladder.append((q, with_year))
            seen.add(key)

    # 0. Phase 12 — cluster-wide common token sequence FIRST. This is the
    # longest run shared across EVERY filename in the batch (the reference renamer's
    # getSeriesName behavior); it's far more robust than any single file's
    # parsed title when some filenames are mangled ("…Final Season Part
    # 3-01 [ ]"). Stashed on the rep parsed object by `_match_cluster`;
    # absent for singletons (they fall straight through to the title rungs).
    # Falls through to the per-file title rungs below if it returns nothing.
    cluster_signal = getattr(parsed, "_cluster_signal", None)
    if isinstance(cluster_signal, str):
        cs = cluster_signal.strip()
        if len(cs) >= 3:
            if parsed.year:
                add(cs, with_year=True)
            add(cs, with_year=False)

    # 0b. Acronym expansion (M2). A file titled just "LotR" / "GoT" / "AoT"
    # can't be resolved by TMDB/TVDB network search — they don't expand
    # initialisms. Add the curated full name as an early, high-priority rung.
    # (AniDB expands acronyms itself inside search_tv via its offline index;
    # this rung is what carries the non-anime acronyms to the other providers.)
    for _raw in (cluster_signal, title):
        if not isinstance(_raw, str):
            continue
        _rn = normalize(_raw)
        if is_acronym_shaped(_rn) and _rn in KNOWN_ACRONYMS:
            _exp = KNOWN_ACRONYMS[_rn]
            if parsed.year:
                add(_exp, with_year=True)
            add(_exp, with_year=False)

    # 1. Full title + year (most specific).
    add(title, with_year=True)
    # 2. Title with the extracted year folded BACK into the search text,
    # no API year filter. Saves the "Blade Runner 2049" / "Cyberpunk 2077"
    # case: the parser had to peel "2049" off the title because it looked
    # like a release year, but TMDB stores "Blade Runner 2049" as the
    # canonical title — so feeding it the recombined text matches the real
    # 2017 film. For unambiguous cases ("Inception 2010") step 1 already
    # won; this rung is only consulted if step 1 returned nothing.
    if parsed.year:
        add(f"{title} {parsed.year}", with_year=False)
    # 3. Full title without year (year filters cut anime/older shows wrongly).
    add(title, with_year=False)
    # 3. Drop arc/subtitle after ':' or ' - '.
    for sep in (":", " - "):
        if sep in title:
            add(title.split(sep, 1)[0], with_year=False)
    # 4. Drop trailing dash-words like 'War-38' → 'War'.
    if "-" in title:
        add(title.rsplit("-", 1)[0], with_year=False)
    # 5. Just the first 1-3 leading words — but NEVER fall back to a
    # single article or short word ("The", "A", "Le", "El"). Querying
    # TMDB for "The" returns tens of thousands of results and adds zero
    # signal; worse, a coincidentally-named movie can score 100% trigram
    # and beat the real target.
    _STOP_FIRST_WORDS = {"the", "a", "an", "le", "la", "el", "los", "las", "and", "of"}
    words = title.split()
    if len(words) > 3:
        add(" ".join(words[:3]), with_year=False)
    if len(words) > 1:
        first = words[0]
        if len(first) >= 3 and first.lower() not in _STOP_FIRST_WORDS:
            add(first, with_year=False)

    return ladder


async def compute_series_group_id(provider_key: str, provider_id: str, registry: ProviderRegistry) -> str:
    """Identity used to visually group cards from the same franchise.

    For TMDB / MusicBrainz one ID already covers all seasons — just echo it.
    For AniDB, walk the sequel/prequel chain to find every related AID and
    use the lowest one as canonical (e.g. all 5 seasons of Rent-a-Girlfriend
    resolve to `anidb:15299`). First call per AID is rate-limited (~4s);
    cached on disk afterwards.

    TVDB gets one extra step: a long-runner whose files are pure-absolute-
    numbered (Attack on Titan's Final Season — "Shingeki no Kyojin - 60")
    can't be placed in any single AniDB cour, so the matcher routes it to
    TVDB. That used to leave it as its OWN card (`tvdb:267440`) while S1-S3,
    which matched AniDB cours, folded into `anidb:9541`. If the TVDB id is a
    known anime (has a Fribb AID), we resolve it THROUGH the AniDB franchise
    so it lands in the same card as its siblings. Live-action TVDB shows have
    no Fribb AID and fall through to `tvdb:<id>` unchanged.
    """
    # ── Cross-provider anime fold (TVDB → AniDB franchise) ────────────────
    # Scoped to TVDB on purpose: anime *movies* carry TMDB ids, and folding
    # TMDB here would wrongly collapse a film into a TV franchise group.
    # Movies never match TVDB, so a TVDB id is always a series — and only
    # anime series have a Fribb reverse mapping, so live-action is untouched.
    if provider_key == "tvdb":
        try:
            from kira.providers.anime_mappings import AnimeMappings
            aid = await AnimeMappings.aid_by_tvdb(int(provider_id))
            if aid is not None:
                # Delegate to the AniDB branch so the franchise root is
                # resolved by the SAME sequel-walk the AniDB-matched siblings
                # used → identical group id (anidb:9541). Under an AniDB ban
                # the walk falls back to anidb:<aid> — the same best-effort
                # behavior AniDB-matched rows already get; self-heals later.
                return await compute_series_group_id("anidb", str(aid), registry)
        except (ValueError, TypeError):
            pass  # non-numeric provider_id → not a Fribb-mappable TVDB id
        except Exception:
            pass  # Fribb/registry hiccup → fall through to tvdb:<id>

    if provider_key != "anidb":
        return f"{provider_key}:{provider_id}"
    try:
        provider = registry.build("anidb")
        related = await provider.get_related_aids(provider_id)  # type: ignore[attr-defined]
        if related:
            return f"anidb:{min(related)}"
    except Exception:
        pass
    return f"anidb:{provider_id}"


# In-process cache for the tiny settings table. We refresh it ourselves
# whenever PUT /settings runs (see api/settings.py) AND opportunistically
# every TTL seconds if a stale cache happens to be hit.
_SETTINGS_CACHE: dict[str, str] | None = None
_SETTINGS_CACHE_AT: float = 0.0
_SETTINGS_TTL_SEC: float = 30.0


def invalidate_settings_cache() -> None:
    """Force the next _load_db_settings() to re-read from disk. Called from
    the PUT /settings handler after any user-side write."""
    global _SETTINGS_CACHE, _SETTINGS_CACHE_AT
    _SETTINGS_CACHE = None
    _SETTINGS_CACHE_AT = 0.0


def _read_settings_from_disk() -> dict[str, Any]:
    """Plain sync sqlite read. Pulled out so callers in async contexts
    can wrap it in `asyncio.to_thread` to avoid event-loop blocking when
    another writer is holding the SQLite lock."""
    import sqlite3
    from pathlib import Path
    from typing import Any
    from kira.config import settings as app_settings

    url = app_settings.database_url
    if "sqlite" not in url:
        return {}
    raw = url.split("///", 1)[-1] if "///" in url else url
    db_path = Path(raw).resolve() if not Path(raw).is_absolute() else Path(raw)
    if not db_path.exists():
        alt = Path(__file__).resolve().parents[2] / Path(raw).name
        if alt.exists():
            db_path = alt
        else:
            return {}
    try:
        # `timeout=0.1` keeps a transient writer-lock contention from
        # blocking the request thread for the SQLite default 5s. We'd
        # rather serve a stale-but-cached settings dict than hang the
        # event loop waiting for a lock.
        conn = sqlite3.connect(str(db_path), timeout=0.1)
        try:
            cur = conn.execute("SELECT key, value FROM settings")
            # Settings.value is a JSON column — SQLAlchemy auto-encodes
            # strings as JSON on write (so `"abc123"` becomes the literal
            # bytes `"abc123"` with quotes in the column). Raw sqlite3
            # reads return the bytes verbatim, so we have to JSON-decode
            # to recover the original value. Without this, the TMDB
            # api_key flows through with literal quotes wrapping it
            # (`api_key="abc123"`) and every request returns 401.
            import json
            out: dict[str, Any] = {}
            for k, raw_v in cur.fetchall():
                if isinstance(raw_v, str):
                    try:
                        out[k] = json.loads(raw_v)
                    except (json.JSONDecodeError, ValueError):
                        # Legacy rows written before the JSON encoding
                        # contract was honored may already be plain
                        # strings. Fall back to using the raw value.
                        out[k] = raw_v
                else:
                    out[k] = raw_v
            return out
        finally:
            conn.close()
    except Exception:
        return {}


async def _load_db_settings() -> dict[str, str]:
    """Return the cached settings dict, refreshing from disk if stale.

    Earlier this opened a fresh sqlite connection on EVERY call from the
    matcher. Each match could touch this 5+ times (engine init + per-
    provider build + cluster), and under SQLite write-lock contention
    each open blocked the event loop for up to 5s. Caching collapses
    that to one read per TTL window (or until PUT /settings invalidates).

    The disk read itself runs in a worker thread so a cache miss can't
    freeze the FastAPI event loop while sqlite waits for a writer lock.
    """
    global _SETTINGS_CACHE, _SETTINGS_CACHE_AT
    import asyncio
    import time
    now = time.monotonic()
    if _SETTINGS_CACHE is not None and (now - _SETTINGS_CACHE_AT) < _SETTINGS_TTL_SEC:
        return _SETTINGS_CACHE
    _SETTINGS_CACHE = await asyncio.to_thread(_read_settings_from_disk)
    _SETTINGS_CACHE_AT = now
    return _SETTINGS_CACHE


async def labs_flag(name: str, default: bool = False) -> bool:
    """Read a Labs feature toggle (`labs.<name>`) from the cached settings.

    These gate the experimental / cost-bearing features the user opts into in
    Settings → Labs. Default OFF. Robust to bare-bool or `{"value": bool}`
    storage, and never raises (a settings read failure just yields the default).
    """
    try:
        db = await _load_db_settings()
    except Exception:
        return default
    v = db.get(f"labs.{name}")
    if isinstance(v, dict):
        v = v.get("value")
    return v if isinstance(v, bool) else default


# Maps the Settings → Connections TMDB language dropdown (which stores its
# human label) to a TMDB ISO language tag. Already-valid tags (e.g. "fr-FR")
# pass through, so a future free-text/code value keeps working. Unknown →
# None, which the provider treats as its en-US default.
_TMDB_LANGUAGE_LABELS = {
    "english (us)": "en-US",
    "english (uk)": "en-GB",
    "français": "fr-FR",
    "deutsch": "de-DE",
    "日本語": "ja-JP",
}


def _tmdb_language_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    mapped = _TMDB_LANGUAGE_LABELS.get(v.lower())
    if mapped:
        return mapped
    # Looks like an ISO tag already (xx or xx-YY) — pass through.
    if 2 <= len(v) <= 5 and all(c.isalpha() or c == "-" for c in v):
        return v
    return None


_TVDB_LANGUAGE_LABELS: dict[str, str] = {
    "english": "eng", "français": "fra", "french": "fra",
    "deutsch": "deu", "german": "deu", "español": "spa", "spanish": "spa",
    "italiano": "ita", "italian": "ita", "português": "por", "portuguese": "por",
    "日本語": "jpn", "japanese": "jpn",
}


def _tvdb_language_code(value: object) -> str | None:
    """Map a UI language label (or bare ISO 639-2 code) to a TVDB search
    language code, e.g. 'eng'. Unknown / empty → None (provider keeps 'eng')."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    mapped = _TVDB_LANGUAGE_LABELS.get(v.lower())
    if mapped:
        return mapped
    if len(v) == 3 and v.isalpha():
        return v.lower()
    return None


# Convenience constructor reading from app settings (one client per app).
async def registry_from_settings(client: httpx.AsyncClient) -> ProviderRegistry:
    from kira.config import settings
    db = await _load_db_settings()

    configs: dict[ProviderKey, ProviderConfig] = {}

    # TMDB / TVDB keys: DB wins over env so UI edits take effect without restart.
    tmdb_key = db.get("providers.tmdb.api_key") or settings.tmdb_api_key
    if tmdb_key:
        configs["tmdb"] = ProviderConfig(
            mode=ProviderMode.DIRECT, api_key=tmdb_key,
            tmdb_language=_tmdb_language_code(db.get("providers.tmdb.language")),
        )
    tvdb_key = db.get("providers.tvdb.api_key") or settings.tvdb_api_key
    if tvdb_key:
        configs["tvdb"] = ProviderConfig(
            mode=ProviderMode.DIRECT, api_key=tvdb_key,
            tvdb_language=_tvdb_language_code(db.get("providers.tvdb.language")),
        )

    # AniDB's read-only HTTP API needs no user key — always register it.
    # Client name + version flow from settings so the user can paste their
    # AniDB-approved registration once it's accepted. Passed via the config
    # so the factory builds the right auth on every construction.
    configs["anidb"] = ProviderConfig(
        mode=ProviderMode.DIRECT,
        api_key=None,
        anidb_client=db.get("providers.anidb.client") or None,
        anidb_clientver=db.get("providers.anidb.clientver") or None,
    )

    return ProviderRegistry(configs=configs, client=client)


# Keep DEFAULT_CLOUD_BASE_URL referenced so it doesn't fall out of imports.
_ = DEFAULT_CLOUD_BASE_URL
