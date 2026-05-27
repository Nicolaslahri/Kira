from __future__ import annotations

from typing import Any, ClassVar

from kira.providers.base import (
    EpisodeResult,
    MetadataProvider,
    MovieResult,
    ProviderKey,
    TVResult,
)


class TMDBProvider(MetadataProvider):
    key: ClassVar[ProviderKey] = "tmdb"

    async def search_movie(self, title: str, year: int | None = None) -> list[MovieResult]:
        params = {"query": title, **self._auth_params()}
        if year is not None:
            params["year"] = str(year)
        r = await self.client.get(
            f"{self.base_url}/search/movie",
            params=params,
            headers=self._auth_headers(),
            timeout=15.0,
        )
        r.raise_for_status()
        return [
            MovieResult(
                provider="tmdb",
                provider_id=str(d["id"]),
                title=d.get("title") or d.get("original_title") or "",
                year=_year_from(d.get("release_date")),
                overview=d.get("overview"),
                poster_url=_poster_url(d.get("poster_path")),
                popularity=d.get("popularity"),
                aliases=_aliases_from(d.get("title"), d.get("original_title")),
            )
            for d in r.json().get("results", [])
        ]

    async def search_tv(self, title: str, year: int | None = None) -> list[TVResult]:
        params = {"query": title, **self._auth_params()}
        if year is not None:
            params["first_air_date_year"] = str(year)
        r = await self.client.get(
            f"{self.base_url}/search/tv",
            params=params,
            headers=self._auth_headers(),
            timeout=15.0,
        )
        r.raise_for_status()
        return [
            TVResult(
                provider="tmdb",
                provider_id=str(d["id"]),
                title=d.get("name") or d.get("original_name") or "",
                year=_year_from(d.get("first_air_date")),
                overview=d.get("overview"),
                poster_url=_poster_url(d.get("poster_path")),
                popularity=d.get("popularity"),
                aliases=_aliases_from(d.get("name"), d.get("original_name")),
            )
            for d in r.json().get("results", [])
        ]

    async def get_episodes(self, series_id: str, season: int) -> list[EpisodeResult]:
        # `language=en-US` forces English titles + overviews. TMDB's default
        # falls back to the show's master record language for non-localized
        # shows (e.g. anime returns Japanese names). Explicit `language` lets
        # the popup always render readable English text.
        r = await self.client.get(
            f"{self.base_url}/tv/{series_id}/season/{season}",
            params={**self._auth_params(), "language": "en-US"},
            headers=self._auth_headers(),
            timeout=15.0,
        )
        r.raise_for_status()
        return [
            EpisodeResult(
                provider="tmdb",
                series_id=series_id,
                season=season,
                episode=ep.get("episode_number"),
                title=ep.get("name"),
                air_date=ep.get("air_date"),
                overview=ep.get("overview"),
                runtime=ep.get("runtime"),
            )
            for ep in r.json().get("episodes", [])
        ]

    # ── Rich details for the popup hero ──────────────────────────────────
    # One extra call per cluster after the matcher picks a top match.
    # Returned dict shape is normalized across providers so the matcher
    # can stash it on Match.metadata_blob without per-provider branching.
    async def get_movie_details(self, movie_id: str) -> dict[str, Any]:
        """Fetch genres / cast / director / runtime / language for a movie."""
        try:
            r = await self.client.get(
                f"{self.base_url}/movie/{movie_id}",
                params={**self._auth_params(), "append_to_response": "credits"},
                headers=self._auth_headers(),
                timeout=15.0,
            )
            r.raise_for_status()
        except Exception:
            return {}
        d = r.json()
        credits = d.get("credits") or {}
        crew = credits.get("crew") or []
        directors = [c.get("name") for c in crew if c.get("job") == "Director" and c.get("name")]
        cast = [c.get("name") for c in (credits.get("cast") or [])[:5] if c.get("name")]
        return {
            "genres": [g.get("name") for g in (d.get("genres") or []) if g.get("name")],
            "cast": cast,
            "director": directors[0] if directors else None,
            "runtime": d.get("runtime") or None,
            "original_language": d.get("original_language"),
            "original_country": (d.get("production_countries") or [{}])[0].get("iso_3166_1"),
            "studio": ", ".join(c.get("name") for c in (d.get("production_companies") or [])[:2] if c.get("name")) or None,
            "network": None,
            "label": None,
            "overview": d.get("overview"),
        }

    async def get_season_poster(self, series_id: str, season_number: int) -> str | None:
        """Return the poster URL for a SPECIFIC season of a TMDB TV series.

        Same purpose as TVDB's get_season_poster — multi-season anime
        franchises that the AniDB ↔ Fribb cross-ref maps to the same TMDB
        series ID across different season numbers need per-season art.

        Uses `/tv/{id}/season/{N}` which returns the season's poster_path
        directly. Falls back to the series-level poster if unavailable.
        """
        try:
            r = await self.client.get(
                f"{self.base_url}/tv/{series_id}/season/{season_number}",
                params=self._auth_params(),
                headers=self._auth_headers(),
                timeout=15.0,
            )
            if r.status_code == 200:
                p = r.json().get("poster_path")
                if p:
                    return f"https://image.tmdb.org/t/p/w500{p}"
        except Exception:
            pass
        # Fallback: series-level poster
        try:
            r = await self.client.get(
                f"{self.base_url}/tv/{series_id}",
                params=self._auth_params(),
                headers=self._auth_headers(),
                timeout=15.0,
            )
            if r.status_code == 200:
                p = r.json().get("poster_path")
                if p:
                    return f"https://image.tmdb.org/t/p/w500{p}"
        except Exception:
            pass
        return None

    async def get_tv_details(self, series_id: str) -> dict[str, Any]:
        """Fetch genres / cast / network / runtime / language for a TV series."""
        try:
            r = await self.client.get(
                f"{self.base_url}/tv/{series_id}",
                params={**self._auth_params(), "append_to_response": "credits"},
                headers=self._auth_headers(),
                timeout=15.0,
            )
            r.raise_for_status()
        except Exception:
            return {}
        d = r.json()
        credits = d.get("credits") or {}
        cast = [c.get("name") for c in (credits.get("cast") or [])[:5] if c.get("name")]
        run_times = d.get("episode_run_time") or []
        creators = d.get("created_by") or []
        return {
            "genres": [g.get("name") for g in (d.get("genres") or []) if g.get("name")],
            "cast": cast,
            "director": creators[0].get("name") if creators else None,
            "runtime": run_times[0] if run_times else None,
            "original_language": d.get("original_language"),
            "original_country": (d.get("origin_country") or [None])[0],
            "studio": ", ".join(c.get("name") for c in (d.get("production_companies") or [])[:2] if c.get("name")) or None,
            "network": ", ".join(n.get("name") for n in (d.get("networks") or [])[:2] if n.get("name")) or None,
            "last_air_date": d.get("last_air_date"),
            "in_production": d.get("in_production"),
            "label": None,
            "overview": d.get("overview"),
        }


def _year_from(date_str: str | None) -> int | None:
    if not date_str or len(date_str) < 4:
        return None
    try:
        return int(date_str[:4])
    except ValueError:
        return None


def _poster_url(path: str | None) -> str | None:
    if not path:
        return None
    return f"https://image.tmdb.org/t/p/w500{path}"


def _aliases_from(primary: str | None, original: str | None) -> list[str] | None:
    """Surface TMDB's original_name/original_title as an alias when it differs.
    Cheap signal that disambiguates e.g. romaji anime title vs English release."""
    if original and primary and original.strip() and original.strip() != primary.strip():
        return [original.strip()]
    return None
