"""TVDB v4 provider.

Auth flow: POST {base}/login with {"apikey": "..."} → response.data.token.
Token is a JWT valid for ~30 days. We cache it in-process and refresh on 401.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

import httpx

from kira.providers.base import (
    EpisodeResult,
    MetadataProvider,
    MovieResult,
    ProviderAuth,
    ProviderKey,
    TVResult,
)


class TVDBProvider(MetadataProvider):
    key: ClassVar[ProviderKey] = "tvdb"

    # ── H4: Concurrency limit ─────────────────────────────────────────
    # The anime re-rank in matcher/engine.py fires `gather(*5)` of
    # `get_series_extended` calls — and with multiple anime clusters
    # processed in parallel scans, that compounds to 25+ concurrent
    # TVDB HTTP calls per second. TVDB has no published rate-limit
    # number but community testing shows 3 concurrent is the safe ceiling
    # before 429s start cascading. Semaphore is class-level so all
    # provider instances (constructed per match cluster) share it.
    _CONCURRENCY: ClassVar[int] = 3
    _request_sem: ClassVar[asyncio.Semaphore] = asyncio.Semaphore(_CONCURRENCY)

    # ── R2-C3 helper: cache get_series_extended results ────────────────
    # The matcher fires get_series_extended for every TVDB candidate
    # during anime re-rank AND during the Fribb-empty fallback. For a
    # 200-file scan where 50 files match the same TVDB series, we'd
    # otherwise issue 50 extended calls. Keep one cached payload per
    # series_id so subsequent files (and subsequent reranks) reuse it.
    # Per-process cache; clears on backend restart. Bounded by series
    # count, not file count, so memory is trivial.
    _extended_cache: ClassVar[dict[str, dict[str, Any]]] = {}

    def __init__(self, base_url: str, auth: ProviderAuth, client: httpx.AsyncClient):
        super().__init__(base_url=base_url, auth=auth, client=client)
        self._token: str | None = None

    # ── Auth ──────────────────────────────────────────────────────────────
    def _is_cloud(self) -> bool:
        """True when speaking to Kira Cloud proxy instead of TVDB directly.

        In cloud mode the proxy handles TVDB's /login bookkeeping for us —
        we just attach the cloud-issued bearer/header from `auth` to every
        request. Detecting cloud by either an explicit auth header (the
        factory sets one for CLOUD mode) or by a `kira.app` host as a belt
        for self-hosted proxies that forget the header.
        """
        if self.auth.header_name and self.auth.header_value:
            return True
        return "kira.app" in self.base_url

    async def _ensure_token(self) -> str:
        """Login on first use, return cached token afterwards.

        Skipped entirely in cloud mode — the proxy owns the TVDB token.
        """
        if self._token is not None:
            return self._token
        seed = (self.auth.credentials or {}).get("apikey")
        if not seed:
            raise RuntimeError("TVDB provider has no apikey in auth.credentials")
        r = await self.client.post(
            f"{self.base_url}/login",
            json={"apikey": seed},
            timeout=15.0,
        )
        r.raise_for_status()
        token = r.json().get("data", {}).get("token")
        if not token:
            raise RuntimeError("TVDB /login did not return a token")
        self._token = token
        return token

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        # All HTTP exits this single chokepoint — the concurrency
        # semaphore lives here so it gates every API call regardless of
        # which higher-level method initiated it.
        async with TVDBProvider._request_sem:
            if self._is_cloud():
                # Cloud proxy: auth preset on `self.auth`; base helper attaches it.
                headers = self._auth_headers()
                query = {**(params or {}), **self._auth_params()}
                r = await self.client.get(
                    f"{self.base_url}{path}",
                    params=query,
                    headers=headers,
                    timeout=15.0,
                )
                r.raise_for_status()
                return r.json()

            token = await self._ensure_token()
            headers = {"Authorization": f"Bearer {token}"}
            r = await self.client.get(
                f"{self.base_url}{path}",
                params=params or {},
                headers=headers,
                timeout=15.0,
            )
            # Token expired? Refresh once and retry.
            if r.status_code == 401:
                self._token = None
                token = await self._ensure_token()
                headers["Authorization"] = f"Bearer {token}"
                r = await self.client.get(
                    f"{self.base_url}{path}",
                    params=params or {},
                    headers=headers,
                    timeout=15.0,
                )
            r.raise_for_status()
            return r.json()

    # ── Searches ──────────────────────────────────────────────────────────
    async def search_movie(self, title: str, year: int | None = None) -> list[MovieResult]:
        params: dict[str, Any] = {"query": title, "type": "movie", "language": "eng"}
        if year is not None:
            params["year"] = year
        data = await self._get("/search", params=params)
        out: list[MovieResult] = []
        for d in data.get("data", []):
            out.append(MovieResult(
                provider="tvdb",
                provider_id=str(d.get("tvdb_id") or d.get("id") or ""),
                title=_pick_eng(d, "translations", "name", "name"),
                year=_year_from(d.get("year") or d.get("first_air_time")),
                overview=_pick_eng(d, "overviews", "overview", "overview"),
                poster_url=d.get("image_url") or d.get("poster"),
                popularity=None,
                aliases=_clean_aliases(d.get("aliases")),
            ))
        return out

    async def search_tv(self, title: str, year: int | None = None) -> list[TVResult]:
        params: dict[str, Any] = {"query": title, "type": "series", "language": "eng"}
        if year is not None:
            params["year"] = year
        data = await self._get("/search", params=params)
        out: list[TVResult] = []
        for d in data.get("data", []):
            out.append(TVResult(
                provider="tvdb",
                provider_id=str(d.get("tvdb_id") or d.get("id") or ""),
                title=_pick_eng(d, "translations", "name", "name"),
                year=_year_from(d.get("year") or d.get("first_air_time")),
                overview=_pick_eng(d, "overviews", "overview", "overview"),
                poster_url=d.get("image_url"),
                popularity=None,
                aliases=_clean_aliases(d.get("aliases")),
            ))
        return out

    async def get_series_extended(self, series_id: str) -> dict[str, Any]:
        """Fetch extended series metadata for the popup hero + anime re-rank.

        Returns: aliases, original_language, original_country, genres
        (used by the matcher's anime disambiguator), PLUS cast / network /
        studio / runtime / last_air_date for the popup hero details.

        Passes `?meta=translations` so the response includes ALL language
        translations of the overview/name. Critical for anime: the master
        record on a Japan-origin show is in Japanese, but we always want
        to display the English text. Without translations meta, we'd
        either ship the raw Japanese (ugly) or do a second HTTP call.

        ── R2-C3: cached per series_id ────────────────────────────────
        The matcher's Fribb-empty fallback + anime re-rank can both
        request extended for the same series_id during a single scan.
        Cache hit returns instantly without any HTTP call.
        """
        # Cache hit: bypass the HTTP fetch + semaphore entirely.
        cached = TVDBProvider._extended_cache.get(series_id)
        if cached is not None:
            return cached
        try:
            data = await self._get(f"/series/{series_id}/extended", params={"meta": "translations"})
        except Exception:
            return {}
        payload = data.get("data", {}) or {}
        # TVDB v4 returns aliases as [{language, name}] — flatten to a list of strings.
        aliases_raw = payload.get("aliases") or []
        aliases: list[str] = []
        for a in aliases_raw:
            if isinstance(a, dict):
                name = a.get("name")
                if name:
                    aliases.append(name)
            elif isinstance(a, str):
                aliases.append(a)

        # Cast: TVDB returns `characters` with role + people-name nested.
        # Sort by `sort` (lower = more prominent), cap at 5.
        characters = payload.get("characters") or []
        try:
            characters_sorted = sorted(
                [c for c in characters if isinstance(c, dict)],
                key=lambda c: c.get("sort") or 9999,
            )
        except Exception:
            characters_sorted = [c for c in characters if isinstance(c, dict)]
        cast: list[str] = []
        for c in characters_sorted:
            # Only actor-type entries; TVDB's `type` is 3 for actors, 1 for guest stars.
            if c.get("type") not in (None, 3):
                continue
            name = c.get("personName") or c.get("name")
            if name and name not in cast:
                cast.append(name)
            if len(cast) >= 5:
                break

        # Network / studio. TVDB returns `latestNetwork`, `originalNetwork`,
        # `companies[{companyType: {name: 'Studio'}}]`.
        network = None
        latest_net = payload.get("latestNetwork") or {}
        if isinstance(latest_net, dict):
            network = latest_net.get("name")
        if not network:
            orig_net = payload.get("originalNetwork") or {}
            if isinstance(orig_net, dict):
                network = orig_net.get("name")

        studios: list[str] = []
        for comp in (payload.get("companies") or []):
            if not isinstance(comp, dict):
                continue
            ctype = (comp.get("companyType") or {}).get("companyTypeName", "").lower()
            if "studio" in ctype or "production" in ctype:
                nm = comp.get("name")
                if nm and nm not in studios:
                    studios.append(nm)
            if len(studios) >= 2:
                break

        # Overview: PREFER the explicit English translation over the
        # master-record `overview` (which is in the show's original
        # language — Japanese for anime, etc.). Only fall back to the
        # bare field if there's no English translation on file.
        overview = None
        translations = (payload.get("translations") or {}).get("overviewTranslations") or []
        for t in translations:
            if isinstance(t, dict) and (t.get("language") in ("eng", "en")):
                cand = t.get("overview")
                if cand:
                    overview = cand
                    break
        if not overview:
            overview = payload.get("overview")

        result: dict[str, Any] = {
            "aliases": aliases,
            "original_language": payload.get("originalLanguage"),
            "original_country": payload.get("originalCountry"),
            "genres": [g.get("name") for g in (payload.get("genres") or []) if isinstance(g, dict)],
            "cast": cast,
            "network": network,
            "studio": ", ".join(studios) if studios else None,
            "runtime": payload.get("averageRuntime"),
            "last_air_date": payload.get("lastAired"),
            "in_production": (payload.get("status") or {}).get("name", "") == "Continuing",
            "director": None,
            "label": None,
            "overview": overview,
        }
        # R2-C3: stash for the rest of this process — multiple matcher
        # phases (Fribb-empty filter, anime rerank, popup hero) call
        # this for the same series_id.
        TVDBProvider._extended_cache[series_id] = result
        return result

    async def get_movie_details(self, movie_id: str) -> dict[str, Any]:
        """Same shape as get_series_extended but for movies."""
        try:
            data = await self._get(f"/movies/{movie_id}/extended", params={"meta": "translations"})
        except Exception:
            return {}
        payload = data.get("data", {}) or {}
        characters = payload.get("characters") or []
        directors = [c.get("personName") for c in characters
                     if isinstance(c, dict) and (c.get("peopleType") == "Director")]
        cast = [c.get("personName") for c in characters[:5]
                if isinstance(c, dict) and c.get("personName")]
        # English overview wins — see get_series_extended for the why.
        overview = None
        translations = (payload.get("translations") or {}).get("overviewTranslations") or []
        for t in translations:
            if isinstance(t, dict) and (t.get("language") in ("eng", "en")):
                cand = t.get("overview")
                if cand:
                    overview = cand
                    break
        if not overview:
            overview = payload.get("overview")
        return {
            "genres": [g.get("name") for g in (payload.get("genres") or []) if isinstance(g, dict)],
            "cast": cast,
            "director": directors[0] if directors else None,
            "runtime": payload.get("runtime"),
            "original_language": payload.get("originalLanguage"),
            "original_country": payload.get("originalCountry"),
            "studio": ", ".join(
                c.get("name") for c in (payload.get("studios") or [])[:2]
                if isinstance(c, dict) and c.get("name")
            ) or None,
            "network": None,
            "label": None,
            "overview": overview,
        }

    async def get_series_poster(self, series_id: str) -> str | None:
        """Return the canonical poster URL for one TVDB series.

        Used as the AniDB cover-art fallback path — we cross-reference an
        AID to a TVDB series ID via the Fribb mappings, then call this
        instead of hammering AniDB's rate-limited image API.
        """
        try:
            data = await self._get(f"/series/{series_id}")
        except Exception:
            return None
        payload = (data or {}).get("data") or {}
        # TVDB v4 puts the poster on `image` (a CDN URL); some entries also
        # carry `image_url`. Either works.
        return payload.get("image") or payload.get("image_url")

    async def get_season_poster(self, series_id: str, season_number: int) -> str | None:
        """Return the poster URL for a SPECIFIC season of a TVDB series.

        Critical for multi-season franchises that share one TVDB series ID
        across their AniDB seasons (e.g. all 5 Rent-a-Girlfriend AIDs map
        to TVDB series 380654, but each season has its own cover art).
        Without this, every season card in the franchise group shows the
        same poster.

        Strategy:
          1. Fetch `/series/{id}/extended` once (small payload, returns
             `seasons[]` with per-season `id` + `image`).
          2. Find the season with matching `number` (filtering for the
             default `type=Aired Order` to avoid alternate orderings).
          3. If that season carries an inline `image`, return it.
          4. Else, fall back to fetching that season's `/seasons/{id}/extended`
             to get the highest-resolution poster artwork.
          5. Else, fall back to the series-level poster so the card isn't
             blank.
        """
        try:
            data = await self._get(f"/series/{series_id}/extended")
        except Exception:
            return await self.get_series_poster(series_id)
        payload = (data or {}).get("data") or {}
        seasons = payload.get("seasons") or []

        # TVDB has multiple "season orders" (Aired, DVD, etc.). Default is
        # `type.id == 1` (Aired Order); fall back to anything else if missing.
        candidates: list[dict] = []
        for s in seasons:
            if not isinstance(s, dict):
                continue
            if s.get("number") != season_number:
                continue
            stype = s.get("type") or {}
            type_id = stype.get("id") if isinstance(stype, dict) else None
            type_name = (stype.get("name") if isinstance(stype, dict) else "") or ""
            # Aired Order is canonical; everything else is a secondary preference.
            if type_id == 1 or "aired" in type_name.lower():
                candidates.insert(0, s)  # priority
            else:
                candidates.append(s)

        for season in candidates:
            inline = season.get("image") or season.get("image_url")
            if inline:
                return inline
            # No inline image — try the season's extended endpoint for artwork.
            sid = season.get("id")
            if not sid:
                continue
            try:
                sdata = await self._get(f"/seasons/{sid}/extended")
            except Exception:
                continue
            spayload = (sdata or {}).get("data") or {}
            inline = spayload.get("image") or spayload.get("image_url")
            if inline:
                return inline
            # Some seasons only carry artwork in an `artwork[]` array; prefer
            # type 7 (Season Poster), fall back to any image.
            artwork = spayload.get("artwork") or []
            posters = [a for a in artwork if isinstance(a, dict) and a.get("type") == 7]
            other = [a for a in artwork if isinstance(a, dict)]
            for a in (posters + other):
                url = a.get("image") or a.get("thumbnail")
                if url:
                    return url

        # Nothing season-specific found — fall back to the series poster so
        # the card isn't blank.
        return payload.get("image") or payload.get("image_url") or await self.get_series_poster(series_id)

    async def get_episodes(self, series_id: str, season: int) -> list[EpisodeResult]:
        # TVDB paginates at 100 ep/page. Long-running shows (One Piece, Pokémon,
        # Simpsons) lose episodes past page 0 without this walk. We follow the
        # `links.next` token until exhausted, with a hard cap to avoid infinite
        # loops if the server misreports pagination state.
        #
        # `/eng` suffix forces English-translated names + overviews. Without
        # this, anime (and any non-English-originated show) returns titles
        # in the show's MASTER record language — Japanese kanji for anime,
        # Korean for K-dramas, etc. TVDB returns null name when no English
        # translation exists for a specific episode; we map that to None
        # and the frontend's `Episode N` fallback kicks in.
        out: list[EpisodeResult] = []
        page = 0
        while page < 50:
            try:
                data = await self._get(
                    f"/series/{series_id}/episodes/default/eng",
                    params={"page": page},
                )
            except Exception:
                # Some series don't have an English translation track at all;
                # TVDB returns 404 in that case. Fall back to the master
                # record (Japanese for most anime) once and accept it —
                # better than empty episode rows in the popup.
                data = await self._get(
                    f"/series/{series_id}/episodes/default",
                    params={"page": page},
                )
            payload = data.get("data", {}) or {}
            episodes = payload.get("episodes", []) or []
            for ep in episodes:
                if ep.get("seasonNumber") != season:
                    continue
                out.append(EpisodeResult(
                    provider="tvdb",
                    series_id=series_id,
                    season=ep.get("seasonNumber"),
                    episode=ep.get("number") or ep.get("episodeNumber"),
                    title=ep.get("name"),
                    air_date=ep.get("aired"),
                    overview=ep.get("overview"),
                    runtime=ep.get("runtime"),
                ))
            # Stop when this page returned nothing or when the API signals no next page.
            if not episodes:
                break
            links = (data.get("links") or {})
            if not links.get("next"):
                break
            page += 1
        return out


def _year_from(value: Any) -> int | None:
    if value is None:
        return None
    s = str(value)
    if len(s) >= 4 and s[:4].isdigit():
        return int(s[:4])
    return None


def _clean_aliases(raw: Any) -> list[str] | None:
    """Normalize TVDB search-result aliases — strings or {language, name} dicts."""
    if not raw or not isinstance(raw, list):
        return None
    out: list[str] = []
    for a in raw:
        if isinstance(a, str) and a.strip():
            out.append(a.strip())
        elif isinstance(a, dict):
            name = a.get("name")
            if isinstance(name, str) and name.strip():
                out.append(name.strip())
    # Dedupe while preserving order — same alias often appears multiple times.
    seen: set[str] = set()
    deduped = [a for a in out if not (a in seen or seen.add(a))]
    return deduped or None


def _pick_eng(d: dict[str, Any], translations_key: str, primary_key: str, fallback_key: str) -> str | None:
    """Prefer the English translation, fall back to the primary-language field.

    TVDB v4 returns `translations` / `overviews` as either a dict {lang: text}
    or a list [{language, name|overview}]. Both shapes appear in the wild.

    The list-branch lookup keys on `primary_key` exactly — NOT `name OR overview`.
    Without that discipline, an overview lookup would read `item.get("name")`
    first and pull the title into the description (and vice versa).
    """
    tr = d.get(translations_key)
    if isinstance(tr, dict):
        en = tr.get("eng")
        if en:
            return en
    elif isinstance(tr, list):
        for item in tr:
            if isinstance(item, dict) and item.get("language") == "eng":
                v = item.get(primary_key)
                if v:
                    return v
    return d.get(primary_key) or d.get(fallback_key) or None
