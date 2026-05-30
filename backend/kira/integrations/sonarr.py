"""Sonarr REST client — `/api/v3/` surface.

User-owned, runs in the same network as Kira (typical Docker stack:
`http://sonarr:8989`). Auth via the `X-Api-Key` header pulled from
Sonarr's Settings → General → Security page.

This module is a thin REST wrapper, NOT a metadata provider. It
PUSHES actions (add series, search episodes) rather than pulling
metadata. The matcher never calls this; only the `/integrations/sonarr`
API surface and (eventually) the auto-rescan webhook do.

Calls are scoped to a per-request `httpx.AsyncClient` so a misconfigured
Sonarr (wrong URL, unreachable host) can't poison a long-lived client
shared with anything else.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Literal

import httpx

# Sonarr's API timeout is conservatively short. Their endpoints typically
# respond in <500ms; if they're slower than 10s the user has bigger
# problems than us throttling them.
_DEFAULT_TIMEOUT = 10.0


@dataclass
class SonarrConfig:
    """Resolved Sonarr connection config. Caller builds this from
    settings (`integrations.sonarr.*`).

    `series_type` controls Sonarr's folder structure + episode-naming
    conventions for series we add: "standard" (S01E01) / "anime"
    (absolute numbering) / "daily" (yyyy-mm-dd). Picked per series-
    flavor — TV matches use the user's TV setting, AniDB matches use
    the user's Anime setting.

    `season_folders` toggles whether Sonarr creates per-season
    subfolders under the series root (`Season 01/`, `Season 02/`).

    `monitor_new_seasons` is Sonarr's behavior when a series we
    monitor airs a brand-new season: "all" (auto-monitor and search),
    "future" (monitor but don't auto-search), "none" (manual review).
    """
    base_url: str        # e.g. "http://sonarr:8989" or with URL base appended
    api_key: str
    quality_profile_id: int | None = None
    root_folder_path: str | None = None
    series_type: str = "standard"   # standard | anime | daily
    season_folders: bool = True
    monitor_new_seasons: str = "all"  # all | future | none
    # Audio preference for grabs (Settings → Integrations). On Sonarr v4 the
    # quality profile decides sub-vs-dub via Custom Formats, which Kira can't
    # override in a normal auto-search. So when this is "sub"/"dub" we do an
    # INTERACTIVE search instead — fetch the candidate releases per episode and
    # grab the one matching the preference, skipping the opposite. "any" keeps
    # Sonarr's default auto-search behavior.
    audio_preference: str = "any"   # any | sub | dub


@dataclass
class SonarrSeriesType:
    """Type tag Sonarr stores on each series — drives folder structure
    and episode-naming defaults. Standard = SxxExx-named live-action /
    western TV; Anime = absolute-number-named, separate root folder."""
    value: Literal["standard", "anime", "daily"]


class SonarrError(Exception):
    """Raised when Sonarr returns a non-2xx or its response is unusable.

    Carries the upstream status code + body snippet so the UI can show
    something more helpful than "request failed."
    """
    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


def _client(cfg: SonarrConfig) -> httpx.AsyncClient:
    """Construct a per-call httpx client with auth + sane timeout.

    Per-call (not module-level) because misconfigured base_url shouldn't
    persistently break the rest of Kira's HTTP pool. The client is
    short-lived; `async with` cleanup happens at the call site.
    """
    return httpx.AsyncClient(
        base_url=cfg.base_url.rstrip("/"),
        headers={
            "X-Api-Key": cfg.api_key,
            "Accept": "application/json",
        },
        timeout=_DEFAULT_TIMEOUT,
    )


async def test_connection(cfg: SonarrConfig) -> dict[str, Any]:
    """Verify the URL + API key combo works. Returns Sonarr's
    `/system/status` payload (version, branch, runtime info).

    Raises SonarrError on any failure — the caller (the test endpoint
    in api/integrations.py) translates that into a 4xx for the UI.
    """
    async with _client(cfg) as c:
        try:
            r = await c.get("/api/v3/system/status")
        except httpx.RequestError as e:
            raise SonarrError(f"Cannot reach Sonarr at {cfg.base_url}: {e}") from e
        if r.status_code == 401:
            raise SonarrError("Sonarr rejected the API key (401).", status=401, body=r.text[:200])
        if r.status_code != 200:
            raise SonarrError(
                f"Sonarr returned HTTP {r.status_code} on /system/status",
                status=r.status_code,
                body=r.text[:200],
            )
        try:
            return r.json()
        except ValueError as e:
            raise SonarrError(f"Sonarr returned non-JSON on /system/status: {e}") from e


async def list_quality_profiles(cfg: SonarrConfig) -> list[dict[str, Any]]:
    """Fetch the user's Sonarr quality profiles so the UI can offer a
    real dropdown (instead of having them paste a numeric id blind).
    """
    async with _client(cfg) as c:
        r = await c.get("/api/v3/qualityprofile")
        if r.status_code != 200:
            raise SonarrError(
                f"Sonarr /qualityprofile returned HTTP {r.status_code}",
                status=r.status_code,
                body=r.text[:200],
            )
        data = r.json()
        if not isinstance(data, list):
            raise SonarrError("Sonarr /qualityprofile returned non-list")
        return data


async def list_root_folders(cfg: SonarrConfig) -> list[dict[str, Any]]:
    """Fetch Sonarr's configured root folders (where new series are
    saved). Same UX rationale as quality profiles — surface real
    options instead of free-typed paths."""
    async with _client(cfg) as c:
        r = await c.get("/api/v3/rootfolder")
        if r.status_code != 200:
            raise SonarrError(
                f"Sonarr /rootfolder returned HTTP {r.status_code}",
                status=r.status_code,
                body=r.text[:200],
            )
        data = r.json()
        if not isinstance(data, list):
            raise SonarrError("Sonarr /rootfolder returned non-list")
        return data


async def _find_series_by_tvdb(c: httpx.AsyncClient, tvdb_id: int) -> dict[str, Any] | None:
    """Look up an EXISTING series in the user's Sonarr by TVDB id.

    Sonarr's `/series` returns the full library; we filter in memory
    because there's no native filter-by-tvdb-id query param. Libraries
    of 500+ series resolve in a few KB of JSON, so this is fine.
    """
    r = await c.get("/api/v3/series")
    if r.status_code != 200:
        raise SonarrError(
            f"Sonarr /series returned HTTP {r.status_code}",
            status=r.status_code,
            body=r.text[:200],
        )
    items = r.json()
    if not isinstance(items, list):
        return None
    for s in items:
        if isinstance(s, dict) and s.get("tvdbId") == tvdb_id:
            return s
    return None


async def _add_series(
    c: httpx.AsyncClient,
    cfg: SonarrConfig,
    tvdb_id: int,
) -> dict[str, Any]:
    """Add a new series to Sonarr using its TVDB id.

    Flow per Sonarr's documented API:
      1. POST `/series/lookup?term=tvdb:{id}` → returns the series'
         shape as if it WERE added (title, images, year, status, etc.)
      2. POST `/series` with that shape augmented with the user's
         quality profile + root folder + monitoring options.

    All series-type / season-folder / monitor behaviour comes from
    `cfg`, which the caller built from the per-flavor settings (TV or
    Anime). Earlier versions hardcoded `seriesType` from a bare
    `is_anime` flag and `monitor: all` for new-season behaviour;
    those are now config-driven so the user controls them in Settings.
    """
    # Step 1: lookup
    r = await c.get("/api/v3/series/lookup", params={"term": f"tvdb:{tvdb_id}"})
    if r.status_code != 200:
        raise SonarrError(
            f"Sonarr /series/lookup returned HTTP {r.status_code}",
            status=r.status_code,
            body=r.text[:200],
        )
    matches = r.json()
    if not isinstance(matches, list) or not matches:
        raise SonarrError(f"Sonarr couldn't find TVDB id {tvdb_id} in its catalog.")
    series = matches[0]
    if not isinstance(series, dict):
        raise SonarrError(f"Sonarr lookup for TVDB id {tvdb_id} returned malformed data.")

    # Step 2: augment + POST
    payload = dict(series)
    payload["qualityProfileId"] = cfg.quality_profile_id
    payload["rootFolderPath"] = cfg.root_folder_path
    payload["monitored"] = True
    payload["seriesType"] = cfg.series_type
    payload["seasonFolder"] = cfg.season_folders
    payload["addOptions"] = {
        # Don't auto-search the entire backlog on add — Kira will
        # explicitly trigger per-episode searches for the missing
        # episodes only. Otherwise Sonarr would queue every episode
        # of every season, which is exactly the "blunt instrument"
        # behaviour the user is trying to avoid.
        "searchForMissingEpisodes": False,
        "searchForCutoffUnmetEpisodes": False,
        "monitor": cfg.monitor_new_seasons,
    }
    r2 = await c.post("/api/v3/series", json=payload)
    if r2.status_code not in (200, 201):
        raise SonarrError(
            f"Sonarr /series (add) returned HTTP {r2.status_code}",
            status=r2.status_code,
            body=r2.text[:200],
        )
    added = r2.json()
    if not isinstance(added, dict):
        raise SonarrError("Sonarr /series (add) returned malformed data.")
    return added


async def _list_episodes(c: httpx.AsyncClient, series_id: int) -> list[dict[str, Any]]:
    """Pull every episode Sonarr knows about for a series. We need
    Sonarr's internal episode IDs to drive the EpisodeSearch command —
    EpisodeSearch takes episode IDs, not (season, number) pairs."""
    r = await c.get("/api/v3/episode", params={"seriesId": series_id})
    if r.status_code != 200:
        raise SonarrError(
            f"Sonarr /episode returned HTTP {r.status_code}",
            status=r.status_code,
            body=r.text[:200],
        )
    eps = r.json()
    if not isinstance(eps, list):
        raise SonarrError("Sonarr /episode returned non-list")
    return eps


# ─────────────────────────────────────────────────────────────────────
# Audio-preference release selection (sub vs dub)
# ─────────────────────────────────────────────────────────────────────
# Dual / multi audio carries BOTH a sub and a dub track → fine either way.
_AUDIO_DUAL_RE = re.compile(r"\b(?:dual|multi)[\s._-]?audio\b", re.IGNORECASE)
# English-dub-only markers.
_AUDIO_DUB_RE = re.compile(r"(?:\bdub(?:bed)?\b|\beng(?:lish)?[\s._-]?dub\b)", re.IGNORECASE)
# Explicit sub markers. Most anime fansubs DON'T say "sub" in the title
# (e.g. "[SubsPlease] … - 01"), so absence of a dub marker is treated as
# sub-friendly ("neutral"), not unknown-bad.
_AUDIO_SUB_RE = re.compile(
    r"\b(?:sub(?:bed|s)?|vostfr|softsubs?|hardsubs?|multi[\s._-]?subs?)\b", re.IGNORECASE
)

# Positive "this is the original Japanese audio" signals. Sonarr's pre-download
# `languages` field is NOT trustworthy for UNMARKED scene releases: it defaults
# them to the series' original language, so an English-dub re-encode like
# `Rent-A-Girlfriend.S03E10…WEBRip.x265-iVy` shows up as "Japanese" in the
# interactive search yet imports as English (verified via MediaInfo:
# audio='eng'). To pick a REAL sub we prefer releases that carry an explicit
# fansub signal over ones that merely claim Japanese.
#
# Explicit subtitle / dual-audio markers in the title.
_JP_SIGNAL_RE = re.compile(
    r"\b(?:multiple[\s._-]?subtitles?|multi[\s._-]?subs?|dual[\s._-]?audio|"
    r"vostfr|soft[\s._-]?subs?)\b",
    re.IGNORECASE,
)
# Fansub-style LEADING "[Group]" tag (e.g. "[Erai-raws] …", "[Moozzi2] …").
# Scene dubs use a TRAILING "-GROUP" suffix (e.g. "…-iVy"), so a leading
# bracket group is a decent structural proxy for a genuine JP-audio fansub.
_LEADING_BRACKET_RE = re.compile(r"^\s*\[[^\]]+\]")
# Known JP-audio fansub / raw groups (booster on top of the structural signal).
_JP_SUB_GROUPS = frozenset({
    "subsplease", "erai-raws", "horriblesubs", "moozzi2", "ohys-raws",
    "cleo", "ember", "dkb", "asw", "amzero", "new-raws", "judas",
    "beatrice-raws", "commie", "coalgirls", "subsplus",
})

# A live interactive search hits every indexer and can take far longer than the
# default 10s client timeout — especially the first one (cold Prowlarr cache).
# We do ONE season-level search (not one per episode), so a generous per-call
# timeout here is safe and prevents the "stuck on Sending…" hang.
_INTERACTIVE_SEARCH_TIMEOUT = 120.0


def _release_audio_kind(title: str) -> str:
    """Classify a release title's audio as 'dub', 'sub', or 'neutral'.

    'neutral' = dual/multi-audio (both tracks present) OR no audio marker at all
    (the common fansub case — sub-only despite not saying "sub"). Neutral is
    acceptable under either preference; only the OPPOSITE explicit kind is
    excluded.
    """
    t = title or ""
    if _AUDIO_DUAL_RE.search(t):
        return "neutral"
    if _AUDIO_DUB_RE.search(t):
        return "dub"
    if _AUDIO_SUB_RE.search(t):
        return "sub"
    return "neutral"


def _release_matches_preference(title: str, preference: str) -> bool:
    """sub → not dub-only · dub → not sub-only · any → everything."""
    if preference not in ("sub", "dub"):
        return True
    kind = _release_audio_kind(title)
    return kind != ("dub" if preference == "sub" else "sub")


def _release_languages(rel: dict[str, Any]) -> set[str]:
    """Lower-cased set of audio languages Sonarr parsed for a release.

    Sonarr's `/release` response carries a `languages` array (e.g.
    `[{"name": "Japanese"}]`) parsed from the release name + indexer metadata.
    Good for EXCLUDING explicit dubs (Sonarr correctly tags `[TRC]…English.Dub`
    as English), but NOT trustworthy as positive proof of a sub: an unmarked
    scene release defaults to the series' original language, so a dub re-encode
    like `…x265-iVy` reports "Japanese" pre-download yet is English on disk.
    `_is_trusted_jp_sub` adds the positive signal the language field lacks.
    """
    out: set[str] = set()
    for lang in (rel.get("languages") or []):
        if isinstance(lang, dict) and isinstance(lang.get("name"), str):
            out.add(lang["name"].strip().lower())
    return out


def _release_audio_kind_rel(rel: dict[str, Any]) -> str:
    """Audio kind ('dub' / 'sub' / 'neutral') for a Sonarr release DICT.

    Prefers Sonarr's parsed `languages`; falls back to the title heuristic
    when Sonarr reported no usable language (rare, but some indexers omit it):
      * Japanese AND English present  → 'neutral' (multi-audio: both tracks)
      * English only                  → 'dub'
      * Japanese only                 → 'sub'
      * neither / unknown             → title heuristic
    """
    langs = _release_languages(rel)
    has_ja = "japanese" in langs
    has_en = "english" in langs
    if has_ja and has_en:
        return "neutral"
    if has_en and not has_ja:
        return "dub"
    if has_ja and not has_en:
        return "sub"
    return _release_audio_kind(rel.get("title") or "")


def _release_matches_preference_rel(rel: dict[str, Any], preference: str) -> bool:
    """sub → not dub-only · dub → not sub-only · any → everything. Uses the
    language-aware classifier."""
    if preference not in ("sub", "dub"):
        return True
    kind = _release_audio_kind_rel(rel)
    return kind != ("dub" if preference == "sub" else "sub")


def _release_group_norm(rel: dict[str, Any]) -> str:
    g = rel.get("releaseGroup")
    return g.strip().lower() if isinstance(g, str) else ""


def _is_trusted_jp_sub(rel: dict[str, Any]) -> bool:
    """True when a release carries a POSITIVE signal that it's the original
    Japanese audio — a fansub-style leading `[Group]` tag, an explicit
    subtitle / dual-audio marker, or a known JP fansub group. This is the
    signal Sonarr's pre-download `languages` field can't give us: it
    distinguishes a genuine sub (`[Erai-raws] … [Multiple Subtitle]`) from a
    scene release that merely *claims* Japanese (`…-iVy`, actually a dub)."""
    title = rel.get("title") or ""
    if _LEADING_BRACKET_RE.match(title):
        return True
    if _JP_SIGNAL_RE.search(title):
        return True
    return _release_group_norm(rel) in _JP_SUB_GROUPS


def _audio_pref_rank(rel: dict[str, Any], preference: str) -> int:
    """Sort rank within the keepers (opposite audio is excluded first):
    0 = positively matches the preference, 1 = acceptable but unconfirmed.

    For 'sub' this demotes scene releases that only claim Japanese below
    genuine fansubs, so Kira grabs a real sub instead of a mislabeled dub."""
    if preference == "sub":
        return 0 if _is_trusted_jp_sub(rel) else 1
    if preference == "dub":
        confirmed = (
            bool(_AUDIO_DUB_RE.search(rel.get("title") or ""))
            or _release_audio_kind_rel(rel) == "dub"
        )
        return 0 if confirmed else 1
    return 0


def _release_is_pack(rel: dict[str, Any]) -> bool:
    """True when a release covers more than one episode (full-season pack or a
    multi-episode batch). Grabbed at most once so we don't fire the same pack
    once per requested episode (which is how a single BD pack got grabbed 12×
    and jammed Sonarr's import queue)."""
    if rel.get("fullSeason"):
        return True
    meps = rel.get("mappedEpisodeNumbers")
    return isinstance(meps, list) and len(meps) > 1


def _release_covers_episode(rel: dict[str, Any], episode_number: int) -> bool:
    """True when a release (from a SEASON-level search) maps to a given
    season-relative episode number — either a full-season pack or a single/
    batch release whose `mappedEpisodeNumbers` includes it. Lets us do ONE
    season search and slice per-episode candidates out of the single result
    set, instead of one slow live search per episode."""
    if rel.get("fullSeason"):
        return True
    meps = rel.get("mappedEpisodeNumbers")
    return isinstance(meps, list) and episode_number in meps


def _release_is_grabbable(rel: dict[str, Any]) -> bool:
    """Sonarr's OWN verdict on whether this release should be downloaded.

    `approved == True` means it passed every quality / cutoff / upgrade check.
    A rejected release (already-have-the-file, profile-disallows-upgrade, full
    season pack, etc.) must NOT be force-grabbed — doing so is exactly what
    produced the 'No files found are eligible for import' stuck queue. When all
    candidates for an episode are rejected, the caller skips that episode.
    """
    if "approved" in rel:
        return bool(rel.get("approved"))
    return not rel.get("rejected", False)


def _pick_release_for_episode(
    releases: list[dict[str, Any]], preference: str,
) -> dict[str, Any] | None:
    """Best grabbable release for ONE episode under the audio preference, or
    None to skip the episode.

    Returns None when there's no acceptable release — either only the opposite
    audio exists, or Sonarr rejected every candidate (already satisfied / no
    upgrade allowed). Skipping is the correct, safe outcome in both cases.

    Among acceptable releases, ranks: single-episode before packs (avoid
    over-downloading a whole season for one missing episode), then higher
    custom-format score, then higher quality weight, then more seeders.
    """
    cand = [
        r for r in releases
        if isinstance(r, dict)
        and isinstance(r.get("guid"), str)
        and isinstance(r.get("indexerId"), int)
        and _release_is_grabbable(r)
        and _release_matches_preference_rel(r, preference)
    ]
    if not cand:
        return None
    cand.sort(key=lambda r: (
        _audio_pref_rank(r, preference),   # genuine sub/dub before "claimed"
        1 if _release_is_pack(r) else 0,   # single episode before season pack
        -(r.get("customFormatScore") or 0),
        -(r.get("qualityWeight") or 0),
        -(r.get("seeders") or 0),
    ))
    return cand[0]


@dataclass
class SendMissingResult:
    """Structured outcome the API endpoint serialises to JSON for the
    frontend toast. `queued` is the number of distinct episode searches
    Sonarr accepted; `series_was_added` tells the UI whether a fresh
    series-add happened (which warrants a bigger toast)."""
    ok: bool
    queued: int
    series_was_added: bool
    sonarr_series_title: str | None = None
    skipped_episodes: list[int] | None = None  # asked for but not present in Sonarr's episode list
    message: str | None = None


async def send_missing_episodes(
    cfg: SonarrConfig,
    *,
    tvdb_id: int,
    season: int,
    episode_numbers: list[int],
) -> SendMissingResult:
    """One-shot: ensure series is in Sonarr, then trigger searches
    for the specified missing episodes.

    Returns SendMissingResult capturing what happened. Raises SonarrError
    only for hard failures (auth, network, Sonarr-side 5xx).

    Caller (api/integrations.py) is responsible for:
      * Resolving the TVDB id from Kira's Match row
      * De-duping / sorting episode_numbers
      * Translating SonarrError into a 4xx with the user-readable
        message embedded
    """
    if not episode_numbers:
        return SendMissingResult(
            ok=True, queued=0, series_was_added=False,
            message="No missing episodes to send.",
        )
    if cfg.quality_profile_id is None or not cfg.root_folder_path:
        raise SonarrError(
            "Sonarr default quality profile or root folder isn't set. "
            "Configure them in Settings → Integrations."
        )

    async with _client(cfg) as c:
        # Find or add the series.
        existing = await _find_series_by_tvdb(c, tvdb_id)
        if existing is not None:
            series = existing
            series_was_added = False
        else:
            series = await _add_series(c, cfg, tvdb_id)
            series_was_added = True

        series_id = series.get("id")
        if not isinstance(series_id, int):
            raise SonarrError("Sonarr series response lacked an 'id' field.")

        # Map Kira's (season, episode_number) tuples to Sonarr's
        # internal episode IDs. Sonarr just-added series may have a
        # short delay before /episode returns the list — but in
        # practice it's synchronous once /series POST returns 201.
        episodes = await _list_episodes(c, series_id)
        wanted = set(int(n) for n in episode_numbers)
        # Build a (season, number) → id index. Sonarr's anime series
        # use the same field shape (seasonNumber + episodeNumber); the
        # absolute-numbering convention lives in the file-naming stage,
        # not the episode-list shape.
        idx: dict[tuple[int, int], int] = {}
        for e in episodes:
            if not isinstance(e, dict):
                continue
            s = e.get("seasonNumber")
            n = e.get("episodeNumber")
            ep_id = e.get("id")
            if isinstance(s, int) and isinstance(n, int) and isinstance(ep_id, int):
                idx[(s, n)] = ep_id

        target_ids: list[int] = []
        skipped: list[int] = []
        for n in sorted(wanted):
            ep_id = idx.get((season, n))
            if ep_id is None:
                skipped.append(n)
            else:
                target_ids.append(ep_id)

        if not target_ids:
            return SendMissingResult(
                ok=False, queued=0,
                series_was_added=series_was_added,
                sonarr_series_title=series.get("title"),
                skipped_episodes=skipped,
                message=(
                    f"Sonarr knows the series but not the requested "
                    f"episode numbers {skipped} for season {season}. "
                    f"(Series may need a metadata refresh in Sonarr.)"
                ),
            )

        # ── Audio-preference interactive grab (sub / dub) ──────────────
        # On Sonarr v4 the quality profile decides sub-vs-dub via Custom
        # Formats, so a normal auto-search (EpisodeSearch) grabs whatever the
        # profile scores highest — which is how an English dub slipped in. When
        # the user set a sub/dub preference (Settings → Integrations), we run an
        # interactive search and grab the release matching the preference,
        # SKIPPING the opposite audio (and anything Sonarr rejected) rather
        # than auto-grabbing the wrong thing.
        #
        # ONE season-level search, not one per episode: an interactive search
        # hits every indexer and is slow, so 12 of them serialised behind a
        # 10s client timeout is what made the button hang on "Sending…". The
        # season search returns every release for the season in a single call;
        # we slice per-episode candidates out of that one result set.
        if cfg.audio_preference in ("sub", "dub"):
            num_to_eid = {n: idx[(season, n)] for n in sorted(wanted) if (season, n) in idx}
            eid_to_num = {v: k for k, v in num_to_eid.items()}

            all_releases: list[dict[str, Any]] = []
            try:
                rr = await c.get(
                    "/api/v3/release",
                    params={"seriesId": series_id, "seasonNumber": season},
                    timeout=_INTERACTIVE_SEARCH_TIMEOUT,
                )
            except httpx.RequestError as e:
                raise SonarrError(
                    f"Sonarr's interactive search timed out or failed: {e}. "
                    f"Try again in a moment."
                ) from e
            if rr.status_code != 200:
                raise SonarrError(
                    f"Sonarr interactive search returned HTTP {rr.status_code}",
                    status=rr.status_code,
                    body=rr.text[:200],
                )
            body = rr.json()
            if isinstance(body, list):
                all_releases = body

            grabbed_guids: set[str] = set()
            covered_eids: set[int] = set()
            skipped_have: list[int] = []      # right audio existed but rejected
            skipped_no_match: list[int] = []  # only opposite audio / nothing
            for eid in target_ids:
                if eid in covered_eids:
                    continue  # already handled by a season pack we grabbed
                n = eid_to_num.get(eid)
                relevant = [r for r in all_releases
                            if isinstance(r, dict) and n is not None
                            and _release_covers_episode(r, n)]
                pick = _pick_release_for_episode(relevant, cfg.audio_preference)
                if pick is None:
                    # Distinguish WHY we're skipping so the toast is honest:
                    # a matching-audio release that Sonarr rejected means the
                    # user already has the file (or the profile won't upgrade);
                    # otherwise there was simply no sub/dub release.
                    has_audio_match = any(
                        isinstance(r.get("guid"), str) and isinstance(r.get("indexerId"), int)
                        and _release_matches_preference_rel(r, cfg.audio_preference)
                        for r in relevant
                    )
                    (skipped_have if has_audio_match else skipped_no_match).append(
                        eid_to_num.get(eid, eid)
                    )
                    continue

                # Which requested episodes does this pick satisfy? Always the
                # current one; for a pack, every other requested episode it
                # maps to (so we don't grab the same pack again).
                covered_now = {eid}
                if _release_is_pack(pick):
                    for m in (pick.get("mappedEpisodeNumbers") or []):
                        other = num_to_eid.get(m)
                        if other is not None:
                            covered_now.add(other)

                guid = pick["guid"]
                if guid in grabbed_guids:
                    covered_eids |= covered_now
                    continue
                try:
                    gr = await c.post(
                        "/api/v3/release",
                        json={"guid": guid, "indexerId": pick["indexerId"]},
                    )
                    if gr.status_code in (200, 201):
                        grabbed_guids.add(guid)
                        covered_eids |= covered_now
                    else:
                        skipped_no_match.append(eid_to_num.get(eid, eid))
                except Exception as e:
                    print(f"sonarr grab failed for ep {eid}: {e!r}")
                    skipped_no_match.append(eid_to_num.get(eid, eid))

            # Build an honest message. queued==0 is NOT an error — it usually
            # means "you already have these / the profile won't replace them".
            parts: list[str] = []
            if skipped_have:
                parts.append(
                    f"{len(skipped_have)} already in Sonarr (or its profile "
                    f"won't replace the existing file)"
                )
            if skipped_no_match:
                parts.append(f"{len(skipped_no_match)} had no {cfg.audio_preference} release")
            grabbed_n = len(covered_eids)
            if grabbed_n and parts:
                msg = "Skipped " + "; ".join(parts) + "."
            elif parts:
                msg = "Nothing to grab — " + "; ".join(parts) + "."
            else:
                msg = None
            return SendMissingResult(
                ok=True,  # the search succeeded; queued==0 is a valid outcome
                queued=grabbed_n,
                series_was_added=series_was_added,
                sonarr_series_title=series.get("title"),
                skipped_episodes=skipped or None,
                message=msg,
            )

        # Default path: preference == "any" → Sonarr's auto-search. Its quality
        # profile / Custom Formats decide which release to grab.
        cmd = await c.post(
            "/api/v3/command",
            json={"name": "EpisodeSearch", "episodeIds": target_ids},
        )
        if cmd.status_code not in (200, 201):
            raise SonarrError(
                f"Sonarr /command (EpisodeSearch) returned HTTP {cmd.status_code}",
                status=cmd.status_code,
                body=cmd.text[:200],
            )
        return SendMissingResult(
            ok=True,
            queued=len(target_ids),
            series_was_added=series_was_added,
            sonarr_series_title=series.get("title"),
            skipped_episodes=skipped or None,
            message=None,
        )


# ─────────────────────────────────────────────────────────────────────
# Queue introspection — Phase 2: live download progress
# ─────────────────────────────────────────────────────────────────────
#
# Sonarr's `/api/v3/queue` is the source of truth for what's currently
# downloading. The endpoint returns paginated records with embedded
# series + episode metadata (when `includeSeries=true` + `includeEpisode
# =true`), so we get everything in one round-trip.
#
# Status normalization: Sonarr splits "what's happening" across THREE
# fields — `status` (queue-level), `trackedDownloadStatus` (success/
# warning/error verdict), and `trackedDownloadState` (download-client-
# level state). The popup just wants one user-readable word — we collapse
# the three into Kira's seven canonical states (queued, searching,
# downloading, importing, completed, failed, warning).


@dataclass
class SonarrQueueItem:
    """One in-flight Sonarr download, normalized for Kira's popup.

    `tvdb_id` + (season, episode_number) is the join key — Kira matches
    each item back to its episode in the popup's `providerEpisodes` list.
    The progress + ETA + release name drive the visible UI: a green
    progress-bar fill on the "missing" row, "47% · 12 min remaining ·
    [SubsPlease] Frieren - 06 [1080p].mkv" as the visible text.

    `queue_id` and `download_id` are Sonarr's identifiers we pass back
    to the retry-import endpoint. `needs_manual_import` flags the
    common "Downloaded - Unable to Import Automatically" trap so the
    popup can render a one-click fix button.
    """
    tvdb_id: int
    season: int
    episode_number: int
    episode_title: str | None
    # Normalized state — see _normalize_status for the mapping. UI maps
    # these to badge text + colors; backend doesn't decide presentation.
    status: str
    progress_pct: float       # 0..100; 0 when not yet downloading
    eta_seconds: int | None   # None when unknown (queued/searching) or done
    size_bytes: int | None
    size_left_bytes: int | None
    release_title: str | None  # The release name Sonarr's actively grabbing
    protocol: str | None       # "usenet" | "torrent"
    error_message: str | None  # Surfaces statusMessages[].messages when warn/fail
    download_client: str | None
    queue_id: int | None       # Sonarr's queue.id, needed to delete / retry
    download_id: str | None    # Sonarr's downloadId (hash from torrent client)
    needs_manual_import: bool  # Stuck: downloaded but Sonarr refuses to import


def _parse_timeleft(timeleft: Any) -> int | None:
    """Sonarr returns `timeleft` as `"HH:MM:SS"` (or omits it when
    unknown). Convert to plain seconds; bail on any malformed input
    rather than guessing. Used for the popup's "12 min remaining" hint.
    """
    if not isinstance(timeleft, str):
        return None
    parts = timeleft.split(":")
    if len(parts) != 3:
        return None
    try:
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    except (ValueError, TypeError):
        return None
    if h < 0 or m < 0 or s < 0:
        return None
    return h * 3600 + m * 60 + s


def _normalize_status(rec: dict[str, Any]) -> str:
    """Collapse Sonarr's three status fields into one Kira state.

    Priority order matters: a record that's both `status=downloading`
    AND `trackedDownloadStatus=warning` should surface as "warning"
    (the warning is the important signal — the download is happening
    but something is wrong, e.g. quality cutoff unmet, indexer reported
    a checksum mismatch). Failed states take priority over both.

    The seven Kira states the popup understands:
      * queued       — Sonarr accepted the search; waiting on download client
      * searching    — Sonarr is searching indexers (rarely seen in /queue;
                       most "searching" time happens BEFORE queue entry)
      * downloading  — Bytes are flowing; size_left > 0
      * importing    — Download finished; Sonarr is moving to library
      * completed    — Imported successfully; row will disappear next poll
      * failed       — Download or import broke; user intervention needed
      * warning      — Download in flight but Sonarr flagged a concern
    """
    status = (rec.get("status") or "").lower()
    track_status = (rec.get("trackedDownloadStatus") or "").lower()
    track_state = (rec.get("trackedDownloadState") or "").lower()

    # Hard failures first.
    if status == "failed" or track_status == "error" or track_state in ("downloadfailed", "failedpending"):
        return "failed"

    # Import lifecycle.
    if track_state in ("importpending", "importing"):
        return "importing"
    if track_state in ("imported",) or status == "completed":
        return "completed"

    # Warning (download IS happening but Sonarr flagged it).
    if track_status == "warning":
        return "warning"
    if status == "warning":
        return "warning"

    # Active download.
    if status == "downloading" or track_state == "downloading":
        return "downloading"

    # Held / paused states.
    if status in ("paused", "delay", "downloadclientunavailable", "fallback"):
        return "warning"

    # Default — Sonarr knows about it, waiting on something.
    return "queued"


async def get_queue(cfg: SonarrConfig) -> list[SonarrQueueItem]:
    """Fetch Sonarr's active download queue, normalized for Kira.

    Drops records that:
      * Are for series Sonarr doesn't have a tvdbId for (orphan queue
        items left over from a removed series — Sonarr does flush these
        but they linger briefly).
      * Don't have the embedded series/episode blocks (shouldn't happen
        with `includeSeries=true&includeEpisode=true` but defensive).

    No filtering by tvdb_id here — the API endpoint filters, since the
    same raw queue is useful for both the popup (one series) and the
    library-wide cover-card pills (every series). Caching is done at
    the endpoint layer.
    """
    async with _client(cfg) as c:
        try:
            r = await c.get("/api/v3/queue", params={
                "pageSize": 200,
                "includeUnknownSeriesItems": "false",
                "includeSeries": "true",
                "includeEpisode": "true",
            })
        except httpx.RequestError as e:
            raise SonarrError(f"Cannot reach Sonarr at {cfg.base_url}: {e}") from e
        if r.status_code != 200:
            raise SonarrError(
                f"Sonarr /queue returned HTTP {r.status_code}",
                status=r.status_code,
                body=r.text[:200],
            )
        try:
            data = r.json()
        except ValueError as e:
            raise SonarrError(f"Sonarr /queue returned non-JSON: {e}") from e
        if not isinstance(data, dict):
            raise SonarrError("Sonarr /queue returned non-dict envelope.")
        records = data.get("records")
        if not isinstance(records, list):
            return []

        items: list[SonarrQueueItem] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            series = rec.get("series")
            episode = rec.get("episode")
            if not isinstance(series, dict) or not isinstance(episode, dict):
                continue
            tvdb_id = series.get("tvdbId")
            if not isinstance(tvdb_id, int) or tvdb_id <= 0:
                continue
            season = episode.get("seasonNumber")
            ep_num = episode.get("episodeNumber")
            if not isinstance(season, int) or not isinstance(ep_num, int):
                continue

            # Progress calc: Sonarr gives `size` (total bytes once known)
            # and `sizeleft` (remaining). Until the download starts they
            # can both be 0; in that case progress is 0.
            size = rec.get("size")
            size_left = rec.get("sizeleft")
            progress = 0.0
            if isinstance(size, (int, float)) and size > 0 and isinstance(size_left, (int, float)):
                # Clamp defensively — sizeleft > size is possible briefly
                # mid-grab as Sonarr re-estimates.
                progress = max(0.0, min(100.0, (float(size) - float(size_left)) / float(size) * 100.0))

            # Status messages — Sonarr surfaces things like "No files found
            # are eligible for import in ..." here. Joined into one line
            # so the popup can show a short tooltip.
            error_msg: str | None = None
            needs_manual_import = False
            sm = rec.get("statusMessages")
            if isinstance(sm, list) and sm:
                pieces: list[str] = []
                for entry in sm:
                    if not isinstance(entry, dict):
                        continue
                    msgs = entry.get("messages")
                    if isinstance(msgs, list):
                        for m in msgs:
                            if isinstance(m, str):
                                pieces.append(m)
                    elif isinstance(entry.get("title"), str):
                        pieces.append(entry["title"])
                if pieces:
                    error_msg = " · ".join(pieces[:3])
            # Detect the "stuck import" trap. Sonarr's specific behaviour:
            # the release was grabbed by series-ID (release title doesn't
            # contain the parseable series name), so on import Sonarr's
            # safety check refuses to auto-import even though grab history
            # confirms what it is. The file IS sitting in the download
            # client's completed folder. The fix is to call Sonarr's
            # manual-import API which already knows the mapping.
            #
            # CRITICAL: this is NOT the same as "No files found are
            # eligible for import." That message means Sonarr looked
            # and the files weren't there — they got moved/deleted
            # already, or Sonarr is pointing at the wrong path. Calling
            # /manualimport in that state returns nothing and the user
            # sees "Sonarr couldn't find the downloaded files anymore."
            # We deliberately exclude that case so Kira doesn't promise
            # a fix it can't deliver. The user gets the regular
            # Warning treatment for those entries instead.
            tracked_state_field = (rec.get("trackedDownloadState") or "").lower()
            haystack = (error_msg or "").lower()
            # Only the FIXABLE patterns. "Unable to Import Automatically"
            # is the canonical phrase Sonarr emits for the ID-grab
            # safety check. "Manual import required" appears in newer
            # Sonarr versions for the same situation.
            fixable_phrases = (
                "unable to import automatically",
                "manual import required",
            )
            if any(p in haystack for p in fixable_phrases):
                needs_manual_import = True
            # Belt-and-braces: Sonarr's `trackedDownloadState=importBlocked`
            # is the canonical machine-readable signal for the same
            # state on newer Sonarr versions. This bypasses the brittle
            # message-substring check entirely.
            if tracked_state_field == "importblocked":
                needs_manual_import = True

            # Status normalization with a post-pass for stuck imports.
            # Sonarr reports trackedDownloadState=importPending for items
            # that finished downloading but can't be auto-imported — the
            # same state it uses for items actively being imported. The
            # difference is in the status message: a true importing item
            # has no message OR a transient "moving file" note, while a
            # stuck item shouts "No files found are eligible for import"
            # or "Unable to import automatically". When the message
            # carries those signals, override "importing" → "warning"
            # so the UI doesn't shimmer green forever on entries that
            # will never advance without intervention.
            status = _normalize_status(rec)
            if status == "importing" and error_msg:
                err_lower = error_msg.lower()
                stuck_signals = (
                    "no files found",
                    "unable to import",
                    "no eligible",
                    "not eligible for import",
                    "manual import required",
                )
                if any(s in err_lower for s in stuck_signals):
                    status = "warning"
            items.append(SonarrQueueItem(
                tvdb_id=tvdb_id,
                season=season,
                episode_number=ep_num,
                episode_title=(
                    episode.get("title")
                    if isinstance(episode.get("title"), str) else None
                ),
                status=status,
                progress_pct=round(progress, 1),
                eta_seconds=_parse_timeleft(rec.get("timeleft")),
                size_bytes=int(size) if isinstance(size, (int, float)) and size >= 0 else None,
                size_left_bytes=int(size_left) if isinstance(size_left, (int, float)) and size_left >= 0 else None,
                release_title=(
                    rec.get("title") if isinstance(rec.get("title"), str) else None
                ),
                protocol=(
                    rec.get("protocol") if isinstance(rec.get("protocol"), str) else None
                ),
                error_message=error_msg,
                download_client=(
                    rec.get("downloadClient") if isinstance(rec.get("downloadClient"), str) else None
                ),
                queue_id=(
                    int(rec["id"]) if isinstance(rec.get("id"), (int, float)) else None
                ),
                download_id=(
                    rec.get("downloadId") if isinstance(rec.get("downloadId"), str) else None
                ),
                needs_manual_import=needs_manual_import,
            ))
        return items


# ─────────────────────────────────────────────────────────────────────
# Manual-import retry for stuck downloads
# ─────────────────────────────────────────────────────────────────────


@dataclass
class ManualImportCandidate:
    """One file Sonarr would import, surfaced via the preview API so
    the UI's confirmation modal can show source + destination before
    the user commits.

    `source_path` is where the file currently sits (download client's
    completed folder, usually). `destination_root` is the series'
    configured root — Sonarr's import will write under this path.
    The combination tells the user what's about to happen physically
    on disk; data-loss bugs in Sonarr's import are most often a
    surprise on the destination path.
    """
    source_path: str
    destination_root: str
    series_title: str
    series_id: int
    episode_labels: list[str]   # e.g. ["S01E05", "S01E06"]
    episode_ids: list[int]
    quality_name: str | None
    release_group: str | None
    rejection_reasons: list[str]   # non-empty → Sonarr says don't import


@dataclass
class ManualImportRetryResult:
    """Outcome of attempting to nudge Sonarr past a stuck import.

    `imported_count` is how many files Sonarr actually accepted. Often
    1 (one episode) but for season packs it can be higher. `command_id`
    is Sonarr's async-command handle — useful for the UI to poll if we
    want progress, though for v1 we just return after dispatch.

    `destinations` is what Sonarr's recent history reports happened
    AFTER the import command processed — the path the file landed at,
    one per episode. Populated by a follow-up history query so the
    user gets a concrete "where did my file go" answer in the toast
    rather than the silent void that caused the AoT S01 incident.
    """
    ok: bool
    imported_count: int = 0
    command_id: int | None = None
    detail: str | None = None
    destinations: list[str] | None = None
    history_warning: str | None = None


async def preview_manual_import(
    cfg: SonarrConfig,
    *,
    download_id: str,
) -> list[ManualImportCandidate]:
    """Fetch Sonarr's manualimport candidates WITHOUT triggering the
    import. The UI's confirmation modal shows the user exactly what
    Sonarr will do — source path, destination root, episode mapping,
    rejections — before they click Confirm.

    This is the v2 of "Force Import": the original v1 fired the
    import command immediately, which caused at least one data-loss
    incident (AoT S01E05+E06 vanished when Sonarr's move partially
    failed mid-flight on a cross-device move). Now the import is
    a two-step interaction: preview, then commit.
    """
    async with _client(cfg) as c:
        try:
            r = await c.get("/api/v3/manualimport", params={"downloadId": download_id})
        except httpx.RequestError as e:
            raise SonarrError(f"Cannot reach Sonarr: {e}") from e
        if r.status_code != 200:
            raise SonarrError(
                f"Sonarr /manualimport returned HTTP {r.status_code}",
                status=r.status_code,
                body=r.text[:200],
            )
        candidates_raw = r.json()
        if not isinstance(candidates_raw, list):
            return []

        out: list[ManualImportCandidate] = []
        for cand in candidates_raw:
            if not isinstance(cand, dict):
                continue
            series = cand.get("series")
            episodes = cand.get("episodes") or []
            if not isinstance(series, dict):
                continue
            series_id = series.get("id")
            series_title = series.get("title") or "Unknown series"
            series_path = series.get("path") or ""
            if not isinstance(series_id, int):
                continue

            ep_ids: list[int] = []
            ep_labels: list[str] = []
            for e in episodes:
                if not isinstance(e, dict):
                    continue
                if isinstance(e.get("id"), int):
                    ep_ids.append(e["id"])
                s_no = e.get("seasonNumber")
                e_no = e.get("episodeNumber")
                if isinstance(s_no, int) and isinstance(e_no, int):
                    ep_labels.append(f"S{s_no:02d}E{e_no:02d}")

            quality_name: str | None = None
            quality = cand.get("quality")
            if isinstance(quality, dict):
                q_inner = quality.get("quality")
                if isinstance(q_inner, dict) and isinstance(q_inner.get("name"), str):
                    quality_name = q_inner["name"]

            rej_reasons: list[str] = []
            rejections = cand.get("rejections")
            if isinstance(rejections, list):
                for rej in rejections:
                    if isinstance(rej, dict) and isinstance(rej.get("reason"), str):
                        rej_reasons.append(rej["reason"])

            out.append(ManualImportCandidate(
                source_path=str(cand.get("path") or ""),
                destination_root=series_path,
                series_title=series_title,
                series_id=series_id,
                episode_labels=ep_labels,
                episode_ids=ep_ids,
                quality_name=quality_name,
                release_group=(cand.get("releaseGroup") or "") or None,
                rejection_reasons=rej_reasons,
            ))
        return out


async def _fetch_recent_import_history(
    cfg: SonarrConfig,
    *,
    episode_ids: list[int],
) -> tuple[list[str], str | None]:
    """After the import command, query Sonarr's history to see what
    ACTUALLY happened. Returns (destination_paths, warning_message).

    Sonarr's /history endpoint can be queried per-episode. For each
    episode we look at the most recent event AFTER our command fired
    and pull out the destination path from its data blob. Events with
    eventType=downloadFolderImported carry `data.importedPath` (the
    new library path) and `data.droppedPath` (the source). Events with
    eventType=downloadFailed indicate trouble.

    Best-effort: errors here don't fail the calling import — they
    just leave destination_paths empty so the toast says "Sonarr
    accepted; verify location in Sonarr's history".
    """
    destinations: list[str] = []
    warnings: list[str] = []
    try:
        async with _client(cfg) as c:
            for ep_id in episode_ids[:10]:   # cap to avoid hammering Sonarr
                try:
                    r = await c.get(
                        "/api/v3/history",
                        params={
                            "episodeId": ep_id,
                            "pageSize": 5,
                            "sortKey": "date",
                            "sortDirection": "descending",
                        },
                    )
                except httpx.RequestError:
                    continue
                if r.status_code != 200:
                    continue
                try:
                    body = r.json()
                except ValueError:
                    continue
                records = body.get("records") if isinstance(body, dict) else None
                if not isinstance(records, list) or not records:
                    continue
                # Most recent event
                evt = records[0]
                if not isinstance(evt, dict):
                    continue
                etype = (evt.get("eventType") or "").lower()
                data = evt.get("data") if isinstance(evt.get("data"), dict) else {}
                if etype == "downloadfolderimported":
                    p = data.get("importedPath") or evt.get("sourceTitle")
                    if isinstance(p, str) and p:
                        destinations.append(p)
                elif etype == "downloadfailed":
                    msg = data.get("message") or "import failed"
                    warnings.append(f"Episode {ep_id}: {msg}")
    except Exception as e:
        warnings.append(f"history check failed: {e}")
    return destinations, ("; ".join(warnings) or None) if warnings else None


async def retry_manual_import(
    cfg: SonarrConfig,
    *,
    download_id: str,
    import_mode: str = "Copy",
) -> ManualImportRetryResult:
    """Force Sonarr to import a stuck "Downloaded - Unable to Import
    Automatically" entry.

    Algorithm:
      1. GET /api/v3/manualimport?downloadId=<id> — Sonarr returns
         the candidate files for that download with its best-guess
         (series, episodes, quality, languages, releaseGroup) already
         filled in. The reason auto-import refused was the safety
         check around release-vs-filename mismatch — Sonarr's parser
         already knows the right answer; it just won't act on it.
      2. Filter to files Sonarr marked importable (`rejections` is
         empty). If everything has rejections, bail and surface them.
      3. POST /api/v3/command with {name: "ManualImport", files: [...],
         importMode: "Move"} — Sonarr accepts the mapping verbatim.

    Returns ManualImportRetryResult. ok=False with a detail message
    when Sonarr couldn't resolve the files (different download id,
    files already moved out, etc.).
    """
    async with _client(cfg) as c:
        # Step 1: ask Sonarr what it sees in the download folder
        try:
            r = await c.get("/api/v3/manualimport", params={"downloadId": download_id})
        except httpx.RequestError as e:
            raise SonarrError(f"Cannot reach Sonarr: {e}") from e
        if r.status_code != 200:
            raise SonarrError(
                f"Sonarr /manualimport returned HTTP {r.status_code}",
                status=r.status_code,
                body=r.text[:200],
            )
        try:
            candidates = r.json()
        except ValueError as e:
            raise SonarrError(f"Sonarr /manualimport returned non-JSON: {e}") from e
        if not isinstance(candidates, list) or not candidates:
            return ManualImportRetryResult(
                ok=False,
                detail=(
                    "Sonarr couldn't find the downloaded files anymore "
                    "(may have been moved, deleted, or the download "
                    "client purged the queue)."
                ),
            )

        # Step 2: collect importable items. Anything with `rejections`
        # is filtered out — Sonarr is refusing for a reason we can't
        # safely override (file too small, wrong format, etc.). The
        # specific "Unable to Import Automatically" trap typically
        # leaves rejections empty AND has series/episodes resolved.
        importable: list[dict[str, Any]] = []
        skipped: list[str] = []
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            rejections = cand.get("rejections")
            if isinstance(rejections, list) and rejections:
                reasons = [
                    r.get("reason") for r in rejections
                    if isinstance(r, dict) and r.get("reason")
                ]
                skipped.append("; ".join(r for r in reasons if r) or "unspecified")
                continue
            # Sonarr expects the manual-import command body to include
            # series.id, episodes[].id, quality, languages, etc. Pass
            # them through verbatim from the candidate response.
            series = cand.get("series")
            episodes = cand.get("episodes")
            if not isinstance(series, dict) or not isinstance(series.get("id"), int):
                skipped.append("series id missing")
                continue
            if not isinstance(episodes, list) or not episodes:
                skipped.append("no episodes mapped")
                continue
            file_payload: dict[str, Any] = {
                "path": cand.get("path"),
                "folderName": cand.get("folderName"),
                "seriesId": series["id"],
                "episodeIds": [
                    e["id"] for e in episodes
                    if isinstance(e, dict) and isinstance(e.get("id"), int)
                ],
                "quality": cand.get("quality"),
                "languages": cand.get("languages") or [],
                "releaseGroup": cand.get("releaseGroup") or "",
                "downloadId": download_id,
            }
            importable.append(file_payload)

        if not importable:
            return ManualImportRetryResult(
                ok=False,
                detail=(
                    "Sonarr rejected every candidate file: "
                    + " · ".join(skipped[:3])
                ),
            )

        # Step 3: trigger the import via Sonarr's command endpoint.
        # import_mode controls source-side behaviour:
        #   "Copy" — leaves the source file intact (DEFAULT, safer)
        #   "Move" — deletes the source after successful copy
        #   "Hardlink" — same volume only
        # Default changed to "Copy" after the AoT S01E05+E06 incident
        # where Move's copy-then-delete-source approach lost files
        # mid-flight on a cross-device move. Users who want Move-style
        # cleanup can pass it explicitly through the API.
        if import_mode not in ("Copy", "Move", "Hardlink", "Auto"):
            import_mode = "Copy"
        cmd = await c.post(
            "/api/v3/command",
            json={
                "name": "ManualImport",
                "files": importable,
                "importMode": import_mode,
            },
        )
        if cmd.status_code not in (200, 201):
            raise SonarrError(
                f"Sonarr /command (ManualImport) returned HTTP {cmd.status_code}",
                status=cmd.status_code,
                body=cmd.text[:300],
            )
        try:
            cmd_body = cmd.json()
        except ValueError:
            cmd_body = {}
        command_id = cmd_body.get("id") if isinstance(cmd_body, dict) else None

        # Wait briefly for Sonarr to process the import, then check
        # its history to see where the file actually landed. Sonarr's
        # commands are async — a 200 from /command means "I accepted
        # the work order", NOT "the file is in the library now". The
        # 2-second wait is empirical: Sonarr usually finishes the
        # move + writes the history row within 1-1.5s on local
        # filesystem moves; cross-device can take longer but we don't
        # block forever (the user can navigate away).
        await asyncio.sleep(2.0)
        all_ep_ids: list[int] = []
        for f in importable:
            ep_ids = f.get("episodeIds")
            if isinstance(ep_ids, list):
                all_ep_ids.extend(int(x) for x in ep_ids if isinstance(x, int))
        destinations, history_warning = await _fetch_recent_import_history(
            cfg, episode_ids=all_ep_ids,
        )

        return ManualImportRetryResult(
            ok=True,
            imported_count=len(importable),
            command_id=int(command_id) if isinstance(command_id, int) else None,
            detail=(
                f"Sonarr accepted {len(importable)} file"
                f"{'' if len(importable) == 1 else 's'} for manual import "
                f"(mode: {import_mode})."
            ),
            destinations=destinations or None,
            history_warning=history_warning,
        )
