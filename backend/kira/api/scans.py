"""Scan endpoints — scan + match runs as a background task so the frontend
polls /scans/{id} for live progress while rows appear in real time.
"""

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path  # noqa: F401  (used by _compute_series_key for EE-5 disambig)

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from kira import scanner
from kira.api.match_cleanup import detach_and_delete_matches
from kira.database import SessionLocal, get_session
from kira.matcher import MatchEngine
from kira.matcher.engine import compute_series_group_id, fetch_match_metadata, registry_from_settings, resolve_canonical_season
from kira.matcher.similarity import normalize
from kira.models import Match, MediaFile, Scan
from kira.parser import ParsedFile, parse as parse_path
from kira.parser import mediainfo as _mediainfo
from kira.schemas import ScanCreate, ScanOut

router = APIRouter(prefix="/scans", tags=["scans"])

# How often to commit during the walk — every N files, push a checkpoint
# so the polling client sees rows appear in real time.
SCAN_COMMIT_EVERY = 5
MATCH_COMMIT_EVERY = 3

# EE-3: Process-level lock around scan worker. Without this, two
# concurrent POST /scans calls (user double-clicks "Scan", or two browser
# tabs hit it within a second) spawn two background workers walking the
# SAME root. SQLite's UNIQUE(file_path) becomes a footgun rather than a
# safety net: worker B's INSERT raises mid-batch AFTER it has already
# committed earlier partial rows, leaving orphans that auto-heal later
# tries to match (firing duplicate AniDB calls + reawakening the ban).
#
# Autopsy 6: `asyncio.Lock` is per-process. In a multi-worker uvicorn
# deployment two workers can simultaneously see `_SCAN_LOCK.locked() ==
# False` and BOTH spawn scan workers walking the same disk. The fix is
# a DB-level CAS on the `system.scan_running` setting row — see
# `create_scan` for the claim and `_scan_worker_locked` for the release.
# We keep the in-process lock as a fast-fail belt for same-worker
# double-clicks (no DB roundtrip needed when the conflict is local).
_SCAN_LOCK = asyncio.Lock()

# Auto-expire DB lock entries older than 6 hours. A worker that crashed
# mid-scan would otherwise leave the lock pinned forever; this lets a
# subsequent boot reclaim it cleanly without manual DB surgery. Real
# scans complete in minutes (even on 100k-file libraries); 6 h is wide
# enough that a slow scan never gets pre-empted but narrow enough that
# a crashed scan unblocks the next-day boot.
_SCAN_LOCK_MAX_AGE_SEC = 6 * 3600


async def _read_mediainfo_setting(session) -> bool:
    """Whether to backfill missing quality/codec/HDR from real file metadata
    (Phase 16). Default True; a no-op anyway unless pymediainfo is installed."""
    try:
        from kira.models import Setting
        row = await session.get(Setting, "parsing.read_mediainfo")
        if row is None:
            return True
        return bool(row.value)
    except Exception:
        return True


async def _maybe_enrich_mediainfo(parsed: ParsedFile, path: str, enabled: bool) -> None:
    """Phase 16: fill missing quality/codec/HDR from the file's real metadata.
    Fallback only — fires solely when the FILENAME yielded no quality (bounds
    the per-file I/O) and pymediainfo is available. Read runs off the event
    loop. Fully no-op when the lib is absent."""
    if not enabled or parsed.quality is not None or not _mediainfo.available():
        return
    try:
        mi = await asyncio.to_thread(_mediainfo.read_media_info, path)
        _mediainfo.enrich_parsed(parsed, mi)
    except Exception as e:
        print(f"_maybe_enrich_mediainfo failed for {path}: {e!r}")


async def _match_singleton(session, engine, fid: int) -> None:
    """Match one file independently — used for movies and unclustered files.

    For TV/anime singletons we also fetch the episode list so the row gets
    a real `episode_title` from the very first scan — without this, a
    one-off episode shows "Episode N" generic text until the auto-heal
    sweep runs on the next restart, which looks broken in the UI.
    """
    mf = await session.get(MediaFile, fid)
    if mf is None or not mf.parsed_data:
        return
    parsed = ParsedFile(**mf.parsed_data)
    # Same exception-vs-empty discipline as _match_cluster: a TMDB outage
    # must NOT wipe the user's existing matches. Skip the file entirely
    # if the matcher throws; only the empty-result case clears Match rows.
    try:
        scored = await engine.match(parsed, limit=5)
    except Exception as e:
        print(f"_match_singleton: matcher raised for file {fid}: {e!r}")
        return

    # Resolve an episode title for the top match before we touch the DB.
    # Episode-list fetch routes through `_fetch_episodes_for_match` which
    # prefers TVDB cross-ref over AniDB direct calls — AniDB-ban hardening.
    ep_title: str | None = None
    if scored and scored[0].match_type == "tv_episode":
        # Phase 4 validation gate: fetch the top's episode list and, for a
        # western-TV singleton whose TVDB/TMDB match doesn't contain the
        # file's episode, re-rank to a better-fitting alternate. No-op for
        # anime/AniDB. May reorder `scored`, so the metadata/poster fetch
        # below (which reads scored[0]) picks up the corrected top.
        try:
            scored, episodes_by_key, _ep_dicts = await _validate_and_rerank_by_episodes(
                scored, [(fid, parsed)], parsed.season, parsed.media_type, engine.registry,
            )
        except Exception as e:
            print(f"_match_singleton: episode validation failed for file {fid}: {e!r}")
            episodes_by_key = {}
        ep_num = parsed.absolute_episode if parsed.absolute_episode is not None else parsed.episode
        if ep_num is not None and episodes_by_key:
            # When the absolute→AID reroute fired, the matcher stashed the
            # per-AID local episode on scored[0].raw. Pass it to tier 3.
            local_ep = (scored[0].raw or {}).get("local_episode") if scored[0].raw else None
            ep_title = _lookup_episode_title(
                episodes_by_key, scored[0].provider, parsed, ep_num,
                local_episode=local_ep,
            )

    # Rich metadata for the top match only (one extra call per file).
    top_metadata = None
    if scored:
        top = scored[0]
        top_metadata = await fetch_match_metadata(top.provider, top.provider_id, top.match_type, engine.registry)

    # Promote metadata's overview onto Match.overview when the search result
    # didn't include one (AniDB's case — its title-dump search returns no
    # description, but the TVDB/TMDB cross-ref does).
    top_overview_fallback = (top_metadata or {}).get("overview") if top_metadata else None

    await detach_and_delete_matches(session, media_file_id=fid)
    for rank, m in enumerate(scored):
        gid = await compute_series_group_id(m.provider, m.provider_id, engine.registry)
        canonical_season = await resolve_canonical_season(m.provider, m.provider_id, parsed.season)
        row_overview = m.overview or (top_overview_fallback if rank == 0 else None)
        session.add(Match(
            media_file_id=fid,
            provider=m.provider, provider_id=m.provider_id,
            match_type=m.match_type, confidence=m.confidence,
            title=m.title, year=m.year,
            series_name=m.title if m.match_type == "tv_episode" else None,
            season_number=canonical_season, episode_number=parsed.episode,
            episode_title=ep_title if rank == 0 else None,
            poster_url=m.poster_url, overview=row_overview,
            is_selected=(rank == 0),
            series_group_id=gid,
            metadata_blob=top_metadata if rank == 0 else None,
        ))


async def _match_cluster(session, engine, fids: list[int]) -> None:
    """Match a cluster of files sharing a series_key.

    Strategy: run engine.match() on the FIRST file to identify the series, then
    reuse the same scored candidates for every file in the cluster. Each file
    keeps its own episode_number/season_number from its own parsed data, but
    they all share the same provider/provider_id/title/year/poster.

    If the matched provider supports get_episodes(), fetch the season's
    episode list ONCE and use it to fill episode_title per file.
    """
    # Load all parsed data up front.
    files: list[tuple[int, ParsedFile]] = []
    for fid in fids:
        mf = await session.get(MediaFile, fid)
        if mf is None or not mf.parsed_data:
            continue
        files.append((fid, ParsedFile(**mf.parsed_data)))
    if not files:
        return

    # Pick the most "standard" episode as the cluster representative.
    # Otherwise os.walk's traversal order can hand us a Special, an OVA,
    # or a movie tie-in as files[0] and the search query for THAT skews
    # the entire cluster's match.
    def _rep_score(p: ParsedFile) -> tuple[int, int]:
        # Treat missing season/episode as "very high" so they sort LAST.
        s = p.season if p.season is not None else 999
        e = p.episode if p.episode is not None else (p.absolute_episode if p.absolute_episode is not None else 999)
        return (s, e)
    files_sorted = sorted(files, key=lambda f: _rep_score(f[1]))
    rep_parsed = files_sorted[0][1]

    # Cluster-level common-sequence title signal. Replaces the per-file
    # title in the cascade's scoring so a cluster of 26 "One Pace - S01EXX"
    # files scores against "one pace" (the cluster signal) not against
    # any single noisy filename. This is what kills the One Pace → ONE:
    # Kagayaku Kisetsu e false positive at the matcher level.
    from kira.matcher.cluster_signal import compute_cluster_signal
    titles_for_signal = [p.title for _, p in files if p.title]
    cluster_signal = compute_cluster_signal(titles_for_signal)
    # Stash the signal + parent path on the rep parsed object so the
    # cascade context can pick them up. Attribute-stash to avoid
    # threading new params through engine.match's existing signature.
    if cluster_signal:
        rep_parsed._cluster_signal = cluster_signal  # type: ignore[attr-defined]
    # Cluster max episode + size — used by EpisodeCountSanityMetric to
    # veto candidates that physically can't hold this many episodes
    # (1-episode movies/OVAs trying to match 40-file TV clusters).
    cluster_max_ep = max(
        (p.episode or p.absolute_episode or 0 for _, p in files),
        default=0,
    )
    rep_parsed._cluster_max_episode = cluster_max_ep  # type: ignore[attr-defined]
    rep_parsed._cluster_size = len(files)  # type: ignore[attr-defined]
    # Use the rep file's parent for folder identity. All cluster files
    # share a series_key so their parents are equivalent for our purposes.
    rep_mf = await session.get(MediaFile, files_sorted[0][0])
    if rep_mf and rep_mf.file_path:
        from pathlib import Path as _P
        rep_parsed._parent_path = str(_P(rep_mf.file_path).parent)  # type: ignore[attr-defined]

    # Differentiate "API said no results" from "API call failed". On a
    # network failure we MUST NOT delete existing matches — a TMDB outage
    # would wipe the user's library otherwise.
    matched_ok = False
    scored: list = []
    try:
        scored = await engine.match(rep_parsed, limit=5)
        matched_ok = True
    except Exception as e:
        print(f"_match_cluster: matcher raised for cluster of {len(files)} files: {e!r}")
        return  # leave existing Match rows untouched

    if not scored:
        # Successful call, zero results — that IS legitimate "no match", so
        # clear the cluster (it'll render as no_match in the UI).
        for fid, _ in files:
            await detach_and_delete_matches(session, media_file_id=fid)
        return

    top = scored[0]

    # Fetch the full episode list once for the matched series. Keyed by
    # (season, episode) so multi-season providers can't collide their
    # episode 1 from S01 with episode 1 from S02. AniDB returns season=1
    # for all entries (no season concept) which is fine — its AIDs are
    # per-season, so each cluster's get_episodes is already season-scoped.
    #
    # ── AniDB-ban-reduction: prefer TVDB cross-ref for episode list ───
    # AniDB's get_episodes hits the rate-limited HTTP API (1 call per 5s
    # min + 12h ban risk). For AniDB matches that have a Fribb mapping
    # to TVDB, fetch episodes from TVDB instead — same titles in better
    # English, no AniDB load. Falls back to AniDB direct only when no
    # Fribb cross-ref exists. This is the SAME pattern series.py uses
    # for the popup; doing it here means the scan-time matcher doesn't
    # burn AniDB calls just to populate episode_title fields.
    # Phase 4 validation gate: fetch the top's episode list AND, for western
    # TV with a poor-coverage TVDB/TMDB incumbent, re-rank to a better-fitting
    # alternate before we commit to `top`. No-op for anime/AniDB/cour paths
    # (handled by EpisodeCountSanityMetric + cour routing). The gate may
    # reorder `scored`, so re-read `top` afterwards — everything downstream
    # (cour routing, metadata, poster, group id) keys off the corrected top.
    episodes_by_key: dict[tuple[int, int], str | None] = {}
    ep_dicts: list[dict] = []
    if top.match_type == "tv_episode":
        try:
            scored, episodes_by_key, ep_dicts = await _validate_and_rerank_by_episodes(
                scored, files, rep_parsed.season, rep_parsed.media_type, engine.registry,
            )
            top = scored[0]
        except Exception as e:
            print(f"_match_cluster: episode validation/fetch failed: {e!r}")  # non-fatal

    # ── Per-file cour routing (Platinum solution) ──────────────────
    # When the cluster's top match is a Fribb-confirmed cour of a
    # multi-cour TVDB season, build a routing table mapping
    # absolute-episode-range → cour AID, so each file's Match row can
    # point at the AID that ACTUALLY owns its episode. Bleach S17
    # example:
    #   AID 15449 (Cour 1, 13 eps)  → files with parsed.episode in 1-13
    #   AID 17849 (Cour 2, 13 eps)  → files with parsed.episode in 14-26
    #   AID 18671 (Cour 3, 14 eps)  → files with parsed.episode in 27-40
    #
    # The user-facing card stays as ONE card because every file shares
    # the same series_group_id (= the franchise root, lowest AID). But
    # the per-file Match.provider_id reflects reality, so episode_title
    # comes from the right AniDB entry and renaming uses the right
    # canonical series name.
    #
    # Scope: ONLY fires for AniDB top match with Fribb-pinned cour AND
    # ≥2 sibling cours. Sets `cour_routing` to a list of
    # (start_ep, end_ep, aid, local_ep_offset) or None. Single-cour
    # anime / TV / movies → cour_routing stays None, original behavior.
    # Build the per-file cour routing table via the shared helper.
    # Single source of truth — `_rematch_one` calls the same builder so
    # scan-time and rematch-time routing decisions cannot diverge.
    cour_routing: list[tuple[int, int, int, int]] | None = None
    cour_episodes_by_aid: dict[int, list] = {}
    if top.match_type == "tv_episode":
        try:
            from kira.matcher.cour_routing import build_cour_routing_table
            cour_routing = await build_cour_routing_table(
                top.provider, top.provider_id, rep_parsed.season,
                registry=engine.registry,
            )
        except Exception as e:
            # Routing is best-effort. On ANY failure, fall back to
            # single-AID matching (no cour split).
            print(f"_match_cluster: cour routing build failed: {e!r}")
            cour_routing = None
        if cour_routing:
            print(
                f"_match_cluster: cour routing for top AID {top.provider_id} "
                f"s={rep_parsed.season}: {cour_routing}"
            )
            # Pre-fetch each cour's episode list ONCE for the cluster.
            # Used as the per-file title-fallback when the top match's
            # episode list doesn't carry the lumped data (TVDB-split
            # case) — see the per-file fallback in the write loop below.
            for _start, _end, sib_aid, _off in cour_routing:
                try:
                    eps = await _fetch_episodes_for_match(
                        "anidb", str(sib_aid),
                        rep_parsed.season, engine.registry,
                    )
                    cour_episodes_by_aid[sib_aid] = list(eps)
                except Exception as e:
                    print(
                        f"_match_cluster: cour {sib_aid} episode "
                        f"fetch failed: {e!r}"
                    )
                    cour_episodes_by_aid[sib_aid] = []

    # Compute the franchise identity once for the cluster. For AniDB this
    # walks the sequel chain (rate-limited first time, cached after).
    top_group_id = await compute_series_group_id(top.provider, top.provider_id, engine.registry)

    # Rich popup metadata — genres, cast, director, network, language,
    # country, runtime, native/romaji titles, etc. One extra call per
    # cluster (not per file). Saved on the TOP match's metadata_blob.
    top_metadata = await fetch_match_metadata(top.provider, top.provider_id, top.match_type, engine.registry)
    top_overview_fallback = (top_metadata or {}).get("overview") if top_metadata else None

    # Per-season poster art — multi-season TVDB/TMDB shows share ONE
    # provider_id across seasons but expose season-specific cover art
    # via `seasons[].image` (TVDB) or `/tv/{id}/season/{N}.poster_path`
    # (TMDB). The frontend splits these into per-season cards via the
    # `match|provider|provider_id|s<N>` grouping key; without this hook
    # every per-season card would carry the same series-level poster.
    # AniDB doesn't need this — each AID already has its own picture.
    if (
        top.match_type == "tv_episode"
        and top.provider in ("tvdb", "tmdb")
        and rep_parsed.season is not None
    ):
        try:
            poster_provider = engine.registry.build(top.provider)
            if hasattr(poster_provider, "get_season_poster"):
                season_url = await poster_provider.get_season_poster(
                    top.provider_id, rep_parsed.season,
                )
                if season_url:
                    top.poster_url = season_url
        except Exception as e:
            print(f"_match_cluster: per-season poster fetch failed: {e!r}")
    # Other-rank matches each get their own group id (they're alternate
    # candidates, not part of this series).
    other_group_ids: dict[int, str] = {}
    for i, m in enumerate(scored[1:], start=1):
        other_group_ids[i] = await compute_series_group_id(m.provider, m.provider_id, engine.registry)

    # Bipartite refinement: for clusters of ≥3 files, run the file-to-
    # episode assignment over the fetched episode list using multiple
    # metrics in order (exact → absolute → episode-number). This catches
    # the One Piece S23E1158 case where strict (season, episode) misses
    # but episode-number-alone hits AniDB's flat S1 list.
    from kira.matcher.bipartite import (
        assign_files_to_episodes,
        MIN_CLUSTER_FOR_BIPARTITE,
    )
    bipartite_assignments: dict[int, object] = {}
    if (
        top.match_type == "tv_episode"
        and len(files) >= MIN_CLUSTER_FOR_BIPARTITE
        and episodes_by_key
    ):
        # Use the rich episode dicts from the validation gate — they carry
        # air_date (Phase 9's bipartite air-date pass needs it), which the
        # title-only episodes_by_key map drops. Fall back to reconstructing
        # from episodes_by_key if the gate returned none.
        ep_list = ep_dicts or [
            {"season": s, "episode": e, "title": t}
            for (s, e), t in episodes_by_key.items()
        ]
        bipartite_assignments = assign_files_to_episodes(files, ep_list)

        # Phase 18: DVD-order retry. Anime fansubs sometimes follow DVD order,
        # which the aired-order episode list pairs wrong. For a TVDB-matched
        # anime cluster with files the aired pass left orphaned, fetch the DVD
        # ordering ONCE and re-pair only those files. Bounded + best-effort;
        # AniDB-matched clusters (the common case) never reach here.
        if (
            rep_parsed.media_type == "anime"
            and top.provider == "tvdb"
            and bipartite_assignments
        ):
            unpaired = [
                (fid, p) for fid, p in files
                if (fid not in bipartite_assignments
                    or bipartite_assignments[fid].matched_via == "unpaired")
            ]
            if unpaired and engine.registry.has("tvdb"):
                try:
                    tvdb = engine.registry.build("tvdb")
                    dvd_eps = await tvdb.get_episodes(
                        top.provider_id, rep_parsed.season or 1, order="dvd",
                    )
                    dvd_dicts = [
                        {"season": e.season, "episode": e.episode,
                         "title": e.title, "air_date": getattr(e, "air_date", None)}
                        for e in dvd_eps
                    ]
                    if dvd_dicts:
                        for fid, a in assign_files_to_episodes(unpaired, dvd_dicts).items():
                            if a.matched_via != "unpaired":
                                bipartite_assignments[fid] = a
                except Exception as e:
                    print(f"_match_cluster: DVD-order retry failed: {e!r}")

    # Write one Match per file. Same series identity for all; per-file
    # episode info from each file's own parsed_data OR the bipartite
    # assignment (which may have resolved a season/episode disagreement).
    for fid, parsed in files:
        await detach_and_delete_matches(session, media_file_id=fid)

        # ── Per-file cour routing (Platinum solution, step 2) ────────
        # When the cluster top match is a Fribb-pinned cour AND we built
        # a routing table above, find which cour's AID actually owns
        # this file's episode number. routed_aid stays None when:
        #   - no cour routing in scope (cour_routing is None)
        #   - file has no parsed.episode (and no absolute_episode)
        #   - episode falls outside every cour range (anomalous file)
        # In all those cases we preserve the original per-cluster
        # behavior (single AID for all files).
        routed_aid: int | None = None
        routed_local_ep: int | None = None
        if cour_routing is not None:
            # Prefer parsed.episode (season-local). cour_routing keys
            # are season-local episode numbers (1..N across the season).
            # Fall back to absolute_episode only if no season episode
            # was parsed (rare in multi-cour S-tagged clusters).
            file_ep_for_routing = (
                parsed.episode if parsed.episode is not None
                else parsed.absolute_episode
            )
            from kira.matcher.cour_routing import route_file_to_cour
            routed = route_file_to_cour(cour_routing, file_ep_for_routing)
            if routed is not None:
                routed_aid, routed_local_ep = routed

        # Prefer bipartite assignment when it found a real pair —
        # otherwise fall back to per-file parsed data.
        assignment = bipartite_assignments.get(fid)
        if assignment is not None and assignment.matched_via != "unpaired":
            ep_num = assignment.episode_number
            ep_title = assignment.episode_title
        else:
            # episode_number is the user's filename-typed episode number
            # (parsed.episode = 27 for "Bleach.S17E27.mkv"). We do NOT
            # overwrite this with the cour-local number, even when cour
            # routing fires — preserving the user's filename intent means
            # the rename output keeps "S17E27" rather than collapsing to
            # "S17E01" of Cour 3. Cour routing only affects provider_id
            # (canonical AID per file).
            ep_num = parsed.absolute_episode if parsed.absolute_episode is not None else parsed.episode
            local_ep = (top.raw or {}).get("local_episode") if top.raw else None
            # First attempt: look up via the TOP match's episode list
            # using the season-absolute parsed.episode. Works when TVDB
            # lumps all cours under one season (most common case for
            # multi-cour anime — TVDB tends to model an anime "season"
            # as the full broadcast year, not the per-cour split AniDB
            # uses).
            ep_title = _lookup_episode_title(
                episodes_by_key, top.provider, parsed, ep_num,
                local_episode=local_ep,
            )
            # Second attempt (cour-routing fallback): when ep_title is
            # still None but cour routing assigned a local episode, the
            # TVDB-lumped lookup missed (TVDB-split case, or short TVDB
            # data). Try the routed AID's own episode list with the
            # cour-local number — for AID 18671's Cour 3 file with
            # routed_local_ep=1, this looks up "(1, 1)" in Cour 3's own
            # 14-episode list and finds the right title without needing
            # parsed.episode to land in the top's list.
            if (
                ep_title is None
                and routed_aid is not None
                and routed_local_ep is not None
            ):
                routed_eps = cour_episodes_by_aid.get(routed_aid, [])
                if routed_eps:
                    routed_eb_key: dict[tuple[int, int], str | None] = {
                        (ep.season, ep.episode): ep.title for ep in routed_eps
                    }
                    ep_title = _lookup_episode_title(
                        routed_eb_key, "anidb", parsed, routed_local_ep,
                    )
        # When cour routing fires, look up the cour AID's display
        # title from AniDB's in-memory cache so each cour-card carries
        # its OWN canonical title ("Bleach Cour 1" / "Bleach Cour 2" /
        # "Bleach Cour 3"). Falls back to the top match's title when
        # AniDB hasn't loaded the cache yet (cold start) or when the
        # AID isn't found in the dump.
        row_title_override: str | None = None
        if routed_aid is not None:
            try:
                from kira.providers.anidb import AniDBProvider
                row_title_override = AniDBProvider._pick_display_title(routed_aid)
            except Exception:
                row_title_override = None
        for rank, m in enumerate(scored):
            # Per-file cour routing intercepts the TOP candidate's
            # provider_id (rank == 0). Other candidates keep their
            # original provider_id so alternate-candidate UX still
            # works. This is the heart of the per-file routing:
            # Match.provider_id reflects which AID ACTUALLY owns this
            # file's episode, not the cluster-level top winner.
            row_provider_id = m.provider_id
            row_title = m.title
            if rank == 0 and routed_aid is not None:
                row_provider_id = str(routed_aid)
                if row_title_override:
                    row_title = row_title_override

            # Canonical season — for AniDB matches, Fribb mapping is the
            # ground truth (each AID = one TVDB season). Parser's guess
            # from the folder name is the fallback for other providers.
            # Use the (possibly-routed) row_provider_id so a sibling cour
            # AID's canonical season is consulted (still = parsed.season
            # since all cours of a TVDB season share one season number).
            canonical_season = await resolve_canonical_season(m.provider, row_provider_id, parsed.season)
            # For non-top candidates, preserve them so the user can override.
            # episode_title only meaningful on the selected (top) candidate.
            row_overview = m.overview or (top_overview_fallback if rank == 0 else None)
            # Merge the cascade trace (if present) into metadata_blob so
            # the frontend can render "why this confidence?" on hover.
            # Only the top candidate gets the rich top_metadata; alternates
            # get just their cascade_trace.
            row_metadata: dict | None = None
            cascade_trace = (m.raw or {}).get("cascade_trace") if m.raw else None
            if rank == 0:
                row_metadata = dict(top_metadata or {})
                if cascade_trace:
                    row_metadata["cascade_trace"] = cascade_trace
            elif cascade_trace:
                row_metadata = {"cascade_trace": cascade_trace}
            session.add(Match(
                media_file_id=fid,
                provider=m.provider, provider_id=row_provider_id,
                match_type=m.match_type, confidence=m.confidence,
                title=row_title, year=m.year,
                series_name=row_title if m.match_type == "tv_episode" else None,
                season_number=canonical_season, episode_number=ep_num,
                episode_title=ep_title if rank == 0 else None,
                poster_url=m.poster_url, overview=row_overview,
                is_selected=(rank == 0),
                # series_group_id stays as the franchise root across ALL
                # routed cours so the visual franchise grouping in the
                # frontend stays intact (one heading, one card per cour).
                series_group_id=top_group_id if rank == 0 else other_group_ids.get(rank),
                metadata_blob=row_metadata,
            ))
    _ = matched_ok  # marker — keeps the success path readable


async def _try_cross_ref(provider, provider_id: str, season: int) -> tuple[bool, list]:
    """EE-1: Returns (ok, result).

    `ok=True` → the HTTP call completed cleanly, even if the response is
    an empty list. "0 episodes for this season" is a VALID answer (e.g.
    a future season that hasn't aired yet, or a stale Fribb mapping
    pointing at a deleted TVDB row — both terminal, no point falling
    through to another provider).

    `ok=False` → transient / auth / connectivity failure. Caller should
    fall through to the next cross-ref provider or AniDB direct.

    Without this distinction, `if result:` (falsiness check) treats an
    empty success the same as an exception, which silently fires a
    rate-limited AniDB call for every stale Fribb mapping in the library
    — exactly the ban cascade `_fetch_episodes_for_match` exists to avoid.
    """
    try:
        return True, await provider.get_episodes(provider_id, season)
    except Exception as e:
        print(f"_fetch_episodes cross-ref failed for {provider_id}/s{season}: {e!r}")
        return False, []


async def _fetch_episodes_for_match(
    provider_key: str,
    provider_id: str,
    season: int | None,
    registry,
):
    """Fetch a series' episode list, preferring TVDB cross-ref for AniDB.

    AniDB-ban hardening: AniDB's HTTP API is rate-limited (1 call per 5s
    minimum, with 12h IP-bans for violations). Every avoidable AniDB call
    is a small dent in our ban exposure. For AniDB-matched series that
    have a Fribb cross-reference to TVDB or TMDB (most of them do), we
    fetch the episode list from TVDB instead — which is:
      - not rate-limited at the same fragility
      - English by default (better UX than AniDB's romaji-by-default)
      - already loaded by other parts of the matcher

    Falls back to AniDB direct only when no cross-ref exists. Falls back
    to `[]` on any failure so the caller degrades gracefully to generic
    "Episode N" titles rather than crashing the cluster.

    For non-AniDB providers, this is a thin wrapper around
    `provider.get_episodes` that adds the same defensive try/except.

    EE-1 hardening: empty-success and exception-failure are now
    distinguished (see `_try_cross_ref`), and the AniDB circuit breaker
    is consulted BEFORE queuing a direct call so bulk workers don't
    enqueue 800 calls behind a 5s lock just to short-circuit them all.
    """
    # ── AniDB → TVDB / TMDB cross-ref preferred (saves rate-limited call)
    if provider_key == "anidb":
        try:
            from kira.providers.anime_mappings import AnimeMappings
            aid_i = int(provider_id)
        except (ValueError, TypeError):
            aid_i = None

        if aid_i is not None:
            tvdb_id = await AnimeMappings.tvdb_id(aid_i)
            if tvdb_id and registry.has("tvdb"):
                try:
                    tvdb = registry.build("tvdb")
                    # Fribb usually carries the canonical season number;
                    # fall back to the caller's season hint, then to 1.
                    cross_season = await AnimeMappings.tvdb_season(aid_i) or season or 1
                    ok, result = await _try_cross_ref(tvdb, str(tvdb_id), cross_season)
                    # EE-1: trust ANY successful response, even empty.
                    # An empty list is a valid answer; only transient
                    # failures (ok=False) justify falling through.
                    if ok:
                        # Bug-fix: preserve the AniDB contract that callers
                        # downstream expect — `_lookup_episode_title` has a
                        # documented tier "(1, ep_num) works for AniDB
                        # because AniDB always returns season=1". TVDB
                        # returns the REAL season (e.g. Frieren S2 →
                        # season=2), which made the lookup miss EVERY
                        # episode for AniDB-matched files. Rewrite the
                        # cross-ref response to season=1 before returning
                        # so downstream stays substrate-agnostic.
                        return [ep.model_copy(update={"season": 1}) for ep in result]
                except Exception as e:
                    print(f"_fetch_episodes cross-ref TVDB setup failed for AID {provider_id}: {e!r}")

            tmdb_id = await AnimeMappings.tmdb_tv_id(aid_i)
            if tmdb_id and registry.has("tmdb"):
                try:
                    tmdb = registry.build("tmdb")
                    cross_season = await AnimeMappings.tvdb_season(aid_i) or season or 1
                    ok, result = await _try_cross_ref(tmdb, str(tmdb_id), cross_season)
                    if ok:
                        # Same AniDB-contract normalization as TVDB above.
                        return [ep.model_copy(update={"season": 1}) for ep in result]
                except Exception as e:
                    print(f"_fetch_episodes cross-ref TMDB setup failed for AID {provider_id}: {e!r}")
        # Cross-ref unavailable / both providers transiently failed —
        # we're about to consider AniDB direct. EE-1: check the circuit
        # breaker BEFORE queuing, so a bulk worker fanning out 800 stale
        # mappings doesn't fight a 5s lock just to short-circuit them all.
        try:
            from kira.providers.anidb import AniDBProvider
            if AniDBProvider._circuit_open():
                return []
        except Exception:
            pass

    # ── Direct provider call (non-AniDB OR AniDB cross-ref unavailable)
    if not registry.has(provider_key):
        return []
    try:
        p = registry.build(provider_key)
    except (ValueError, NotImplementedError):
        return []
    if not hasattr(p, "get_episodes"):
        return []
    season_for_lookup = season if season is not None else 1
    try:
        return await p.get_episodes(provider_id, season_for_lookup)
    except Exception as e:
        print(f"_fetch_episodes direct {provider_key} failed: {e!r}")
        return []


async def _validate_and_rerank_by_episodes(
    scored: list,
    files: list[tuple[int, ParsedFile]],
    season: int | None,
    media_type: str,
    registry,
) -> tuple[list, dict[tuple[int, int], str | None], list[dict]]:
    """Phase 4 episode-list validation gate.

    Returns ``(possibly_reordered_scored, top_episodes_by_key)``. The top
    candidate's episode list is always fetched (the caller needs it for
    title lookup anyway), so this is a drop-in replacement for the old
    "fetch the top's episode list" block — with one addition:

    For a **western-TV** cluster (``media_type == "tv"``) whose TOP candidate
    is **TVDB/TMDB**, verify the cluster's episodes actually EXIST in that
    candidate's episode list. When coverage is very low AND an alternate
    TVDB/TMDB candidate covers materially better, promote the alternate to
    rank 0 and return ITS episode list.

    Deliberately scoped OUT of the anime / AniDB / cour paths: there,
    per-cour coverage is *legitimately* partial (a 13-ep cour against a
    40-file franchise cluster), and ``EpisodeCountSanityMetric`` + cour
    routing + the absolute→AID reroute already do the resolution. Running a
    naive coverage gate there would wrongly promote the umbrella AID. The
    gate also no-ops unless there are ≥2 candidates and the top actually
    returned a non-empty episode list.

    Ban-safe: ``_fetch_episodes_for_match`` prefers the TVDB/TMDB cross-ref
    and consults the AniDB circuit breaker, and alternates are only probed
    when the incumbent's coverage is already below the floor (rare).
    """
    from kira.matcher.episode_validation import coverage, should_promote

    def _to_dicts(eps) -> list[dict]:
        # Rich episode dicts (incl. air_date) for the bipartite pairing —
        # Phase 9's air-date pass needs air_date, which the title-only
        # `by_key` map drops.
        return [
            {"season": e.season, "episode": e.episode,
             "title": e.title, "air_date": getattr(e, "air_date", None)}
            for e in eps
        ]

    if not scored:
        return scored, {}, []
    top = scored[0]

    top_eps = await _fetch_episodes_for_match(top.provider, top.provider_id, season, registry)
    top_by_key: dict[tuple[int, int], str | None] = {
        (ep.season, ep.episode): ep.title for ep in top_eps
    }
    top_dicts = _to_dicts(top_eps)

    # Gate scope — only western TV with a TVDB/TMDB incumbent.
    if (
        top.match_type != "tv_episode"
        or media_type != "tv"
        or top.provider not in ("tvdb", "tmdb")
        or len(scored) < 2
        or not top_by_key
    ):
        return scored, top_by_key, top_dicts

    file_eps = [
        (p.season, (p.episode if p.episode is not None else p.absolute_episode))
        for _fid, p in files
    ]
    top_cov = coverage(file_eps, top_by_key)
    from kira.matcher.episode_validation import COVERAGE_FLOOR
    if top_cov >= COVERAGE_FLOOR:
        return scored, top_by_key, top_dicts  # incumbent fits — no probing

    # Incumbent is suspicious. Probe alternate TVDB/TMDB candidates.
    best_idx = 0
    best_cov = top_cov
    best_by_key = top_by_key
    best_eps = top_eps
    for i in range(1, len(scored)):
        alt = scored[i]
        if alt.match_type != "tv_episode" or alt.provider not in ("tvdb", "tmdb"):
            continue
        alt_eps = await _fetch_episodes_for_match(alt.provider, alt.provider_id, season, registry)
        alt_by_key = {(ep.season, ep.episode): ep.title for ep in alt_eps}
        if not alt_by_key:
            continue
        alt_cov = coverage(file_eps, alt_by_key)
        if alt_cov > best_cov:
            best_cov, best_idx, best_by_key, best_eps = alt_cov, i, alt_by_key, alt_eps

    if best_idx != 0 and should_promote(top_cov, best_cov):
        promoted = scored[best_idx]
        print(
            f"_validate: episode-coverage re-rank — promoted "
            f"{promoted.provider}:{promoted.provider_id} (cov {best_cov:.2f}) over "
            f"{top.provider}:{top.provider_id} (cov {top_cov:.2f})"
        )
        reordered = [scored[best_idx]] + [s for i, s in enumerate(scored) if i != best_idx]
        return reordered, best_by_key, _to_dicts(best_eps)

    return scored, top_by_key, top_dicts


def _lookup_episode_title(
    episodes_by_key: dict[tuple[int, int], str | None],
    provider: str,
    parsed: ParsedFile,
    ep_num: int | None,
    local_episode: int | None = None,
) -> str | None:
    """Pick the right episode title from a fetched (season, episode) → title map.

    Three lookups, in order:
      1. `(parsed.season, ep_num)` — works for TMDB/TVDB multi-season shows.
      2. `(1, ep_num)` — works for AniDB (no season concept; everything
         comes back as season=1) and for files whose folder-season the
         provider doesn't model.
      3. `(1, local_episode)` — kicks in when the matcher's franchise
         reroute supplied a derived local episode (e.g. a `My Hero - 014`
         file rerouted from S1 AID to S2 AID, with `local_episode=1`).
         AniDB returns S2's episodes numbered 1..12 (not 14..25 absolute),
         so the `(1, 1)` lookup is the right one for the rerouted file.

    `provider` is accepted for caller convenience but unused — the
    fallback hits the right key regardless of who the provider is.
    """
    del provider
    if ep_num is None and local_episode is None:
        return None
    if ep_num is not None:
        season_for_key = parsed.season if parsed.season is not None else 1
        hit = episodes_by_key.get((season_for_key, ep_num))
        if hit is not None:
            return hit
        hit = episodes_by_key.get((1, ep_num))
        if hit is not None:
            return hit
    if local_episode is not None:
        hit = episodes_by_key.get((1, local_episode))
        if hit is not None:
            return hit
    return None


async def _apply_folder_series_lock(session, all_new: list[int]) -> int:
    """Phase 11: pull outlier files into their leaf folder's majority series.

    One mangled filename parses to a different title than its folder-mates and
    splinters into its own cluster (or matches the franchise's base AID) — the
    Attack on Titan "Final Season Part 3-01" / "Special 05" scattering. We
    follow FileBot's "one folder = one series" rule, conservatively: within a
    leaf folder, if a strict majority of TV/anime files agree on a series, the
    outliers are relocked to it (title + disambig unified, each file's own
    season preserved). The pure decision lives in ``matcher/folder_lock.py``.

    Returns the number of files relocked. Movies / music are never touched.
    """
    from collections import defaultdict as _dd

    from kira.matcher.folder_lock import FolderFile, compute_relocks

    by_folder: dict[str, list[tuple[int, MediaFile, FolderFile]]] = _dd(list)
    for fid in all_new:
        mf = await session.get(MediaFile, fid)
        if mf is None or not mf.file_path or mf.media_type not in ("tv", "anime"):
            continue
        season: int | None = None
        if mf.parsed_data:
            try:
                season = ParsedFile(**mf.parsed_data).season
            except Exception:
                season = None
        folder = str(Path(mf.file_path).parent).lower()
        by_folder[folder].append(
            (fid, mf, FolderFile(fid=fid, media_type=mf.media_type,
                                 series_key=mf.series_key, season=season))
        )

    relocked = 0
    for _folder, members in by_folder.items():
        if len(members) < 2:
            continue
        relocks = compute_relocks([ff for _fid, _mf, ff in members])
        if not relocks:
            continue
        mf_by_fid = {fid: mf for fid, mf, _ff in members}
        for fid, new_key in relocks.items():
            mf = mf_by_fid.get(fid)
            if mf is not None and mf.series_key != new_key:
                print(
                    f"_folder_lock: relock file {fid} "
                    f"{mf.series_key!r} → {new_key!r}"
                )
                mf.series_key = new_key
                relocked += 1
    if relocked:
        await session.commit()
    return relocked


def _compute_series_key(parsed: ParsedFile, file_path: str | None = None) -> str | None:
    """Build the clustering key for series consolidation.

    Files with the same key cluster into one card on the Review page.
    - Movies → null (each is its own row).
    - TV/anime → "{type}|{normalized_title}|{season or ''}|{disambig}".
      Season is kept distinct for BOTH tv and anime: AniDB assigns a
      separate AID to every sequel-season, so merging My Hero Academia
      S01 + S02 under one cluster would mis-stamp every S02 file with
      S01's AID. Visual franchise grouping on the Review page happens
      AFTER matching via Match.series_group_id (which walks AniDB's
      sequel chain).

      EE-5 disambig: without a year or parent-folder fingerprint, two
      shows with the same normalized title (The Office UK 2001 / The
      Office US 2005) collapse into ONE cluster. The matcher then picks
      whichever the provider returns first and stamps every file across
      both shows with the wrong ID. We add a third key component:
        - `parsed.year` when known (strongest signal)
        - else the parent series-folder name (e.g. "The.Office.UK"),
          walked up past any "Season N" subfolder
        - else empty (current behavior — files still cluster together)
    - Music → "music|{normalized_artist}|{normalized_album}" when both
      artist and album exist; otherwise null.
    """
    if parsed.media_type == "movie":
        return None
    if parsed.media_type in ("tv", "anime"):
        if not parsed.title:
            return None
        title_n = normalize(parsed.title)
        if not title_n:
            return None
        season = str(parsed.season) if parsed.season is not None else ""

        # EE-5 disambiguator: year > parent folder > empty.
        disambig = ""
        if parsed.year is not None:
            disambig = str(parsed.year)
        elif file_path:
            try:
                p = Path(file_path)
                parent = p.parent
                # Walk up past "Season N" / "S01" folders to the series root.
                pname_lower = parent.name.lower()
                if pname_lower.startswith("season") or (
                    len(parent.name) <= 4 and pname_lower.startswith("s")
                    and pname_lower[1:].isdigit()
                ):
                    parent = parent.parent
                disambig = normalize(parent.name) or ""
            except Exception:
                disambig = ""

        return f"{parsed.media_type}|{title_n}|{season}|{disambig}"
    if parsed.media_type == "music":
        if not (parsed.artist and parsed.album):
            return None
        return f"music|{normalize(parsed.artist)}|{normalize(parsed.album)}"
    return None


# Audio-language tags the format-stripper extracts into `subtitles` (the
# field name is historical — these are AUDIO language indicators in
# real-world media filenames). Used for variant disambiguation.
_LANG_TAGS = ("jap", "eng", "fre", "ger", "ita", "spa")


def _compute_variant_key(parsed: ParsedFile) -> str:
    """Build the identity-variant suffix for a file.

    Empty string when no variant signal is detected — most files. Non-empty
    when the file carries any of these differentiators real libraries
    multiplex on:
      - **audio language** (`JAP`, `ENG`, …) — caught from the parser's
        `subtitles` list (misnamed but actually language tags).
        R2-C3 hardening: ALL matching language tags are surfaced (not just
        the first), so a `[JAP, ENG]` multi-audio file gets a distinct
        `jap-eng` key from a `[JAP]`-only sibling. Prevents UNIQUE collisions
        when the same episode has multiple sub/audio variants.
      - **edition** (`Director's Cut`, `IMAX`, `Extended`).
      - **bit depth** (`10bit` only — 8bit is default and excluded so
        legacy files don't suddenly look like "variants of themselves").
      - **cour** (R2-H12) — when present and no other signal is set,
        cour 1/2/3 of a split-cour anime gets surfaced to keep the
        cluster identity distinct. Folded into the key only when no
        other variant is present (audio/edition trump cour for naming).

    Format: "lang-edition-bitdepth[-courN]", components separated by `-`,
    empties skipped. Example: `jap-eng-directors-cut-10bit`, `eng`,
    `10bit`, `cour2`, `""`. Lowercased so equality is reliable across
    "JAP" / "jap" / "Jap" spellings the same release group sometimes mixes.
    """
    lang_tokens = [s for s in (parsed.subtitles or []) if isinstance(s, str)]
    # R2-C3: collect ALL matching languages (deduped, sorted for stable keys)
    # rather than just the first one. A `[JAP, ENG]` file is a distinct
    # variant from a `[JAP]`-only file because it carries an extra audio
    # track — they shouldn't collide on the same variant_key.
    langs = sorted({t.lower() for t in lang_tokens if t.lower() in _LANG_TAGS})
    lang = "-".join(langs)
    edition_raw = parsed.edition or ""
    edition = "".join(c.lower() if c.isalnum() else "-" for c in str(edition_raw)).strip("-")
    bit = (parsed.bit_depth or "").lower()
    if bit == "8bit":
        bit = ""  # 8bit is default, not a variant indicator
    parts = [p for p in (lang, edition, bit) if p]
    # R2-H12: surface cour when nothing else distinguishes — avoids two
    # cour-1 / cour-2 files of the same TVDB season generating the same
    # rename path. Skipped when audio/edition already disambiguate.
    cour = getattr(parsed, "cour", None)
    if cour and not parts:
        parts.append(f"cour{cour}")
    return "-".join(parts)


async def _match_phase(session, engine, fids: list[int], scan_id: int) -> int:
    """Cluster `fids` by series_key, then match each cluster (≥2 files) or
    singleton, updating the Scan row's live progress. Returns the number of
    files that ended with at least one Match row.

    Shared by the scan worker (new files) and the re-parse worker (existing
    files) so both paths cluster + match identically.
    """
    clusters: dict[str | int, list[int]] = defaultdict(list)
    for fid in fids:
        mf = await session.get(MediaFile, fid)
        if mf is None or not mf.parsed_data:
            continue
        bucket = mf.series_key if mf.series_key else fid
        clusters[bucket].append(fid)

    matched = 0
    for bucket_key, cfids in clusters.items():
        # Shimmer the cluster's rows while it resolves.
        for fid in cfids:
            mf = await session.get(MediaFile, fid)
            if mf:
                mf.status = "matching"
        await session.commit()
        await asyncio.sleep(0)

        if isinstance(bucket_key, str) and len(cfids) >= 2:
            await _match_cluster(session, engine, cfids)
        else:
            await _match_singleton(session, engine, cfids[0])

        for fid in cfids:
            has_match = await session.scalar(
                select(Match.id).where(Match.media_file_id == fid).limit(1)
            )
            mf = await session.get(MediaFile, fid)
            if has_match:
                matched += 1
                if mf and mf.status == "matching":
                    mf.status = "matched"
            elif mf and mf.status == "matching":
                mf.status = "no_match"

        scan = await session.get(Scan, scan_id)
        if scan:
            scan.matched_count = matched
            last_mf = await session.get(MediaFile, cfids[-1])
            scan.current_path = last_mf.file_path if last_mf else None
        await session.commit()
        await asyncio.sleep(0)
    return matched


async def _scan_worker(scan_id: int, root_paths: list[str] | str) -> None:
    """Walk the tree, parse each file, then match each new file in turn.

    Two distinct phases, both reported via Scan.status:
      'scanning' → 'matching' → 'completed' (or 'failed: ...').

    Bug A: accepts a list of roots and walks each one sequentially in
    Phase 1, accumulating all discovered files into a single Phase 2
    matching pass. A bare string is promoted to `[string]` for
    back-compat with internal callers that haven't been updated.
    """
    # Defensive normalization — `_scan_worker_locked` already promotes
    # single strings to lists, but a hand-spawned task or test could
    # still pass a string directly.
    if isinstance(root_paths, str):
        root_paths = [root_paths]
    async with SessionLocal() as session:
        # Track the MediaFile ORM objects directly instead of trying to
        # capture .id mid-flight (placeholders + re-query was a workaround
        # for the fact that .id is None until flush/commit). After the
        # final phase-1 commit below, every object has its real .id.
        new_files: list[MediaFile] = []
        count = 0
        # Pre-load every path that's already a rename TARGET (new_path
        # of a previous rename). Skip those during this scan — otherwise,
        # when the user's library_root contains both source and destination
        # (e.g. Z:\media holds both Z:\media\tv\... and Z:\media\TV\...),
        # the scanner re-discovers renamed files as "new" MediaFile rows.
        # Those phantoms then fail to rename ("source does not exist")
        # because the user already renamed them once.
        from kira.models import RenameHistory
        renamed_paths_raw = set(
            (await session.scalars(
                select(RenameHistory.new_path).where(RenameHistory.undone_at.is_(None))
            )).all()
        )
        # Path-normalization bug-fix: the rename engine writes RESOLVED
        # paths to MediaFile.file_path and RenameHistory.new_path. On
        # Windows with a mapped drive (Z:\ → \\nas\share), the stored
        # path is the UNC form (`\\192.168.0.63\Data\...`) while the
        # scanner walks the drive-letter path (`Z:\...`). String
        # comparison misses, so renamed files re-appear as "new" on the
        # next scan — leading to duplicate Match rows AND the "Resolve N
        # duplicates" footer button lighting up on rows the user JUST
        # finished renaming. We pre-compute lowercased + .resolve()'d
        # variants of every stored path so the per-file lookup below
        # can match against either form cheaply.
        def _norm(p: str) -> set[str]:
            """Return the set of equivalent lower-cased strings for `p`.
            We never trust a single form because OS path normalization
            differs by mount: drive-letter, UNC, forward-slash, etc."""
            forms = {p.lower(), p.replace("/", "\\").lower(), p.replace("\\", "/").lower()}
            try:
                resolved = str(Path(p).resolve()).lower()
                forms.add(resolved)
                forms.add(resolved.replace("/", "\\"))
                forms.add(resolved.replace("\\", "/"))
            except OSError:
                pass
            return forms
        renamed_paths_lc: set[str] = set()
        for p in renamed_paths_raw:
            renamed_paths_lc |= _norm(p)
        # Same trick for existing MediaFile.file_path lookup — preload the
        # whole set so we don't pay a DB roundtrip per file.
        existing_lc: set[str] = set()
        for p in (await session.scalars(select(MediaFile.file_path))).all():
            existing_lc |= _norm(p)
        try:
            # ── Phase 1: walk + parse ─────────────────────────────────────
            # EE-2: reset the thread-local walk-error list BEFORE iteration
            # so this scan sees only its own scandir() failures. We do it
            # here (not inside scanner.walk) because walk() recurses on
            # symlinked dirs and a per-call reset would erase parent state.
            scanner.reset_walk_errors()
            # Bug A: walk every configured root in sequence. All
            # discovered files land in the same `new_files` list →
            # one Phase 2 pass matches them all together so clusters
            # spanning multiple watch folders still resolve correctly
            # (e.g. half a season on the main library + the rest on
            # an external drive that's mounted as a watch folder).
            # Reset walk-errors stays outside this outer loop so a
            # single error list spans the whole scan.
            #
            # Bug A safety net: track every file path we've already
            # added in THIS scan. Without this, overlapping roots
            # (e.g. library_root=`Z:\media` AND watch_folder=`Z:\media`
            # with subtle string differences that defeat the upstream
            # dedup — trailing slash, case, forward vs backslash)
            # would cause the same file to enter `new_files` twice,
            # hit the UNIQUE constraint on `media_files.file_path` at
            # commit time, and trigger the outer scan-worker
            # exception handler which DELETES every row from this
            # scan. The user then sees "scan completed" and an empty
            # Review page. Path-set dedup keeps the inner loop
            # idempotent regardless of how many roots overlap.
            walked_paths_this_scan: set[str] = set()
            # Phase 16: read the mediainfo-backfill toggle once for the scan.
            read_mi = await _read_mediainfo_setting(session)
            for root_path in root_paths:
              for path in scanner.walk(root_path):
                spath_str = str(path)
                # Skip-if-already-walked-this-scan. Lowercase + both
                # slash forms so trailing-slash / case / separator
                # noise can't sneak duplicates past us.
                spath_norm = spath_str.lower().replace("/", "\\")
                if spath_norm in walked_paths_this_scan:
                    continue
                walked_paths_this_scan.add(spath_norm)
                # Skip files that ARE the result of a previous rename
                # (or are already tracked as MediaFiles).
                #
                # Compare against the pre-normalized sets built above —
                # they include the lowercased original string AND the
                # lowercased resolved form (so a UNC-stored DB path
                # matches the drive-letter-walked filesystem path).
                spath_forms = _norm(str(path))
                if spath_forms & renamed_paths_lc:
                    continue
                if spath_forms & existing_lc:
                    continue
                try:
                    file_size = path.stat().st_size
                except OSError:
                    file_size = None
                parsed = parse_path(path)
                # Phase 16: backfill missing quality/codec/HDR from the file
                # itself (no-op without pymediainfo / when the filename had tags).
                await _maybe_enrich_mediainfo(parsed, str(path), read_mi)
                mf = MediaFile(
                    scan_id=scan_id,
                    file_path=str(path),
                    file_size=file_size,
                    media_type=parsed.media_type,
                    status="discovered",
                    parsed_data=parsed.to_dict(),
                    # EE-5: pass file_path so same-titled shows in different
                    # folders ("The Office UK" vs "The Office US") get
                    # distinct series_keys via the parent-folder fingerprint
                    # when the parser couldn't extract a year.
                    series_key=_compute_series_key(parsed, file_path=str(path)),
                    variant_key=_compute_variant_key(parsed),
                )
                session.add(mf)
                new_files.append(mf)
                count += 1
                if count % SCAN_COMMIT_EVERY == 0:
                    scan = await session.get(Scan, scan_id)
                    if scan:
                        scan.file_count = count
                        scan.current_path = str(path)
                    await session.commit()
                    await asyncio.sleep(0)

            scan = await session.get(Scan, scan_id)
            if scan:
                scan.file_count = count
                # PB-4: snapshot the Phase 2 universe size. Frontend uses
                # this as the denominator for real % + ETA computation.
                scan.estimated_total = count
                scan.current_path = None
                scan.status = "matching"
            await session.commit()

            # After the final commit every mf.id is populated; no need to
            # re-query. This is also faster than running a SELECT over the
            # whole scan_id (the old "all_new" query scaled with library size).
            all_new = [mf.id for mf in new_files if mf.id is not None]

            # Phase 11: folder-level series lock. Pull outlier files (a
            # mangled "Final Season Part 3-01" / "Special 05") into their
            # leaf folder's majority series BEFORE clustering, so one bad
            # filename can't escape its season's card. No-op for folders
            # without a clear majority (mixed folders stay split).
            try:
                relocked = await _apply_folder_series_lock(session, all_new)
                if relocked:
                    print(f"_scan_worker: folder-lock relocked {relocked} outlier file(s)")
            except Exception as e:
                print(f"_scan_worker: folder-lock pass failed (non-fatal): {e!r}")

            # ── Phase 2: match. Cluster by series_key first so a 26-episode
            # anime fires 2 API calls (one search + one episode list) instead
            # of 26. Singletons (movies, null series_key) take the per-file path.
            async with httpx.AsyncClient() as client:
                registry = await registry_from_settings(client)
                engine = MatchEngine(registry)

                # Cluster by series_key + match each cluster/singleton,
                # pushing live progress. Shared with the re-parse worker.
                matched = await _match_phase(session, engine, all_new, scan_id)

            # EE-2: did the directory walk hit any unreachable paths?
            # If so, the scan technically completed but is INCOMPLETE.
            # Mark it `completed_partial` (frontend can render a warning
            # badge instead of a green check) and notify so the user
            # knows to retry once the NAS/permissions are stable.
            walk_failures = scanner.get_walk_errors()

            scan = await session.get(Scan, scan_id)
            if scan:
                scan.status = "completed_partial" if walk_failures else "completed"
                scan.file_count = count
                scan.matched_count = matched
                scan.current_path = None
                # Naive UTC to match SQLite's storage of `server_default=func.now()`
                # — comparing aware vs naive 500s every downstream filter.
                scan.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            if walk_failures:
                from kira.models import Notification
                # Cap to 20 paths so the notification body doesn't bloat
                # the notifications popover. Backend logs have the full list.
                shown = walk_failures[:20]
                more = f"\n…and {len(walk_failures) - len(shown)} more" if len(walk_failures) > len(shown) else ""
                session.add(Notification(
                    kind="warning",
                    title=f"Scan finished with {len(walk_failures)} unreachable folder(s)",
                    body=(
                        "Some directories couldn't be read. Common causes: "
                        "NAS disconnect, permission change, drive ejected mid-scan. "
                        "Re-scan once the filesystem is stable.\n\n"
                        + "\n".join(shown) + more
                    )[:1000],
                ))
            await session.commit()
        except Exception as e:
            scan = await session.get(Scan, scan_id)
            if scan:
                scan.status = f"failed: {e}"[:200]
                # Naive UTC to match SQLite's storage of `server_default=func.now()`
                # — comparing aware vs naive 500s every downstream filter.
                scan.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await session.commit()


async def _release_db_scan_lock() -> None:
    """Drop the DB-level scan lock unconditionally.

    Resets `settings.system.scan_running` to 0 regardless of current
    value. Called in the worker's finally block so a successful scan,
    a crashed scan, and a synchronous cancel all release the lock.
    Multi-worker uvicorn deployments need this — without it, the
    worker holding the lock can never tell the OTHER workers to allow
    a new scan once it finishes.
    """
    from sqlalchemy import update as sql_update
    from kira.models import Setting
    async with SessionLocal() as sess:
        await sess.execute(
            sql_update(Setting)
            .where(Setting.key == "system.scan_running")
            .values(value=0)
        )
        await sess.commit()


async def _scan_worker_locked(scan_id: int, root_paths: list[str] | str) -> None:
    """EE-3: Process-locked wrapper around `_scan_worker` with orphan cleanup.

    Holds `_SCAN_LOCK` for the duration so a second background task can't
    walk the same root concurrently. If `_scan_worker` raises ANYTHING
    that escapes its inner try/except (rare — but e.g. SQLAlchemy session
    corruption can do it), we scrub the MediaFile rows this scan inserted
    so the next scan isn't fighting orphans. Without this, partial commits
    from a failed worker leave discovered-but-unmatched rows that auto-heal
    later tries to rematch, firing duplicate AniDB calls.

    Autopsy 6: the finally block always releases the DB-level lock so
    sibling uvicorn workers can spawn a new scan once this one ends.

    Bug A: signature widened to accept a list of roots. A bare string is
    promoted to `[string]` for back-compat with any internal caller that
    hasn't been updated. The endpoint always passes a list now; this
    fallback is defensive for tests / hand-spawned tasks.
    """
    if isinstance(root_paths, str):
        root_paths = [root_paths]
    async with _SCAN_LOCK:
        try:
            try:
                await _scan_worker(scan_id, root_paths)
            except Exception:
                async with SessionLocal() as cleanup:
                    await cleanup.execute(
                        delete(MediaFile).where(MediaFile.scan_id == scan_id)
                    )
                    await cleanup.commit()
                raise
        finally:
            # Always drop the DB lock — success, crash, cancellation.
            # Wrapped in try/except so a release failure can't mask the
            # original exception bubbling up from _scan_worker.
            try:
                await _release_db_scan_lock()
            except Exception as e:
                print(f"_scan_worker_locked: failed to release scan lock: {e!r}")


async def _reparse_worker(scan_id: int) -> None:
    """In-place re-parse of the EXISTING library.

    A normal re-scan SKIPS files already in the DB, so parser improvements
    (named-season parsing, specials, title cleanup) and folder-level series
    locking never reach an already-indexed library. This re-runs the parser
    on every stored MediaFile from its current path, re-applies the folder
    lock, then re-matches NON-manual files. Manual pins + rename history are
    preserved (manual files are excluded from the match phase). Reuses the
    Scan row for progress so the frontend's scan banner shows it like a scan.
    """
    async with SessionLocal() as session:
        try:
            all_ids = list((await session.scalars(select(MediaFile.id))).all())
            total = len(all_ids)
            scan = await session.get(Scan, scan_id)
            if scan:
                scan.status = "scanning"
                scan.file_count = 0
                scan.estimated_total = total
            await session.commit()

            # ── Re-parse every file in place. parse_path is pure string work
            # (name + parent), no disk I/O, so this is fast even for large
            # libraries. file_size already lives on the row — don't re-stat.
            for i, fid in enumerate(all_ids):
                mf = await session.get(MediaFile, fid)
                if mf is None or not mf.file_path:
                    continue
                try:
                    parsed = parse_path(mf.file_path)
                except Exception as e:
                    print(f"_reparse_worker: parse failed for {fid}: {e!r}")
                    continue
                mf.parsed_data = parsed.to_dict()
                mf.media_type = parsed.media_type
                mf.series_key = _compute_series_key(parsed, file_path=mf.file_path)
                mf.variant_key = _compute_variant_key(parsed)
                if (i + 1) % SCAN_COMMIT_EVERY == 0:
                    scan = await session.get(Scan, scan_id)
                    if scan:
                        scan.file_count = i + 1
                        scan.current_path = mf.file_path
                    await session.commit()
                    await asyncio.sleep(0)
            await session.commit()

            # ── Folder-level series lock (Phase 11) on the fresh parses ──
            try:
                relocked = await _apply_folder_series_lock(session, all_ids)
                if relocked:
                    print(f"_reparse_worker: folder-lock relocked {relocked} file(s)")
            except Exception as e:
                print(f"_reparse_worker: folder-lock failed (non-fatal): {e!r}")

            # ── Re-match, preserving manual pins ──
            scan = await session.get(Scan, scan_id)
            if scan:
                scan.status = "matching"
                scan.current_path = None
            await session.commit()

            manual_fids = set((await session.scalars(
                select(Match.media_file_id).where(
                    Match.is_manual.is_(True), Match.is_selected.is_(True),
                )
            )).all())
            to_match = [fid for fid in all_ids if fid not in manual_fids]

            async with httpx.AsyncClient() as client:
                registry = await registry_from_settings(client)
                engine = MatchEngine(registry)
                await _match_phase(session, engine, to_match, scan_id)

            scan = await session.get(Scan, scan_id)
            if scan:
                scan.status = "completed"
                scan.current_path = None
                scan.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await session.commit()
        except Exception as e:
            print(f"_reparse_worker failed: {e!r}")
            scan = await session.get(Scan, scan_id)
            if scan:
                scan.status = f"failed: {e}"[:200]
                scan.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await session.commit()


async def _reparse_worker_locked(scan_id: int) -> None:
    """Process-locked wrapper around `_reparse_worker`.

    Unlike `_scan_worker_locked` it NEVER deletes MediaFile rows on failure —
    re-parse runs against the EXISTING library, so a mid-run crash must leave
    the user's files + matches intact. Always releases the DB scan lock.
    """
    async with _SCAN_LOCK:
        try:
            await _reparse_worker(scan_id)
        finally:
            try:
                await _release_db_scan_lock()
            except Exception as e:
                print(f"_reparse_worker_locked: failed to release scan lock: {e!r}")


@router.post("/reparse", response_model=ScanOut, status_code=201)
async def reparse_library(
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> Scan:
    """Re-parse the EXISTING library in place and re-match it.

    A scan skips already-indexed files; this re-runs the parser on every
    stored file so parser + folder-lock improvements apply WITHOUT a
    destructive DB reset. Manual pins + rename history are preserved. Uses
    the same single-scan lock as create_scan (409 if a scan is running).
    """
    if _SCAN_LOCK.locked():
        raise HTTPException(409, "A scan is already running. Please wait for it to complete.")

    import time as _time
    from sqlalchemy import or_, update as sql_update
    from kira.models import Setting
    now_ts = int(_time.time())
    stale_cutoff = now_ts - _SCAN_LOCK_MAX_AGE_SEC
    res = await session.execute(
        sql_update(Setting)
        .where(Setting.key == "system.scan_running")
        .where(or_(Setting.value == 0, Setting.value < stale_cutoff))
        .values(value=now_ts)
    )
    await session.commit()
    if (res.rowcount or 0) == 0:
        raise HTTPException(409, "A scan is already running. Please wait for it to complete.")

    try:
        scan = Scan(root_path="(re-parse existing library)", status="scanning")
        session.add(scan)
        await session.commit()
        await session.refresh(scan)
        background.add_task(_reparse_worker_locked, scan.id)
        return scan
    except Exception:
        try:
            await _release_db_scan_lock()
        except Exception as e:
            print(f"reparse_library: lock release after failure also failed: {e!r}")
        raise


async def _start_scan(paths: list[str], source: str = "manual") -> int | None:
    """Shared scan trigger: claim the locks, create a Scan row, launch the worker.

    Single source of truth for kicking off a scan — used by the manual POST
    /scans endpoint (source="manual") and the watched-folders daemon
    (source="auto"). Respects BOTH the process-level `_SCAN_LOCK` fast-fail and
    the multi-worker DB CAS on `system.scan_running` (same discipline as the
    original create_scan).

    `paths` must already be cleaned by the caller (the manual endpoint also
    validates existence and returns 400s; the watcher only passes existing
    dirs). Blank entries are defensively dropped + deduped here.

    Returns the new scan id, or ``None`` if a scan is already running (caller
    decides 409 vs silent skip). The launched worker owns the locks and
    releases them in its finally block.
    """
    # Clean + dedup, preserving order.
    seen: set[str] = set()
    effective_roots: list[str] = []
    for p in paths:
        if not isinstance(p, str):
            continue
        stripped = p.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        effective_roots.append(stripped)
    if not effective_roots:
        return None

    # Fast reject: this process is already running a scan.
    if _SCAN_LOCK.locked():
        return None

    # ── Autopsy 6: atomic DB-level scan lock (multi-worker safe) ──────
    # A single conditional UPDATE on the `system.scan_running` setting row.
    # The CAS allows the claim when the value is `0` (idle) OR a stale
    # timestamp from a crashed prior scan. Only the caller whose UPDATE
    # returns rowcount=1 proceeds.
    import time as _time
    from sqlalchemy import or_, update as sql_update
    from kira.models import Setting
    now_ts = int(_time.time())
    stale_cutoff = now_ts - _SCAN_LOCK_MAX_AGE_SEC
    async with SessionLocal() as session:
        res = await session.execute(
            sql_update(Setting)
            .where(Setting.key == "system.scan_running")
            .where(or_(Setting.value == 0, Setting.value < stale_cutoff))
            .values(value=now_ts)
        )
        await session.commit()
        if (res.rowcount or 0) == 0:
            return None

        # The lock is now claimed. Create the Scan row + hand off to the
        # worker (which releases the lock in its finally block). If anything
        # fails before hand-off, release the lock so the user can retry.
        try:
            scan = Scan(root_path=effective_roots[0], status="scanning", source=source)
            session.add(scan)
            await session.commit()
            await session.refresh(scan)
            scan_id = scan.id
        except Exception:
            try:
                await _release_db_scan_lock()
            except Exception as e:
                print(f"_start_scan: lock release after failure also failed: {e!r}")
            raise

    # Launch outside the session context. asyncio.create_task keeps the worker
    # alive for the lifetime of the event loop (same as the BackgroundTasks
    # path), and the worker's finally block releases both locks.
    asyncio.create_task(_scan_worker_locked(scan_id, effective_roots, source))
    return scan_id


@router.post("", response_model=ScanOut, status_code=201)
async def create_scan(
    payload: ScanCreate,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> Scan:
    """Kick off a scan + match and return immediately. Frontend polls /scans/{id}.

    EE-3: Refuses to start a new scan while one is already running.
    Process-level lock catches the same-process double-click; DB-level
    check catches a different worker process (e.g. multi-worker gunicorn)
    that's already running a scan. 409 lets the frontend show a friendly
    "scan in progress" toast rather than silently spawning duplicates.
    """
    # Fast reject: this process is already running a scan. Saves the
    # DB roundtrip for the same-worker double-click case.
    if _SCAN_LOCK.locked():
        raise HTTPException(409, "A scan is already running. Please wait for it to complete.")

    # Bug A fix: resolve the effective root list. If the client passed
    # `root_paths`, walk all of them; otherwise fall back to the single
    # `root_path` (preserves the original API contract). Dedup + filter
    # empty paths so we don't walk the same dir twice.
    raw_roots: list[str] = (
        list(payload.root_paths) if payload.root_paths else [payload.root_path]
    )
    seen: set[str] = set()
    effective_roots: list[str] = []
    for p in raw_roots:
        if not isinstance(p, str):
            continue
        stripped = p.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        effective_roots.append(stripped)
    if not effective_roots:
        raise HTTPException(400, "No roots to scan. Set a library root in Settings → Paths.")

    # Pre-flight path check for EACH root. Most common silent failure after a
    # DB reset: the Setting table got wiped (clearing paths.library_root), the
    # frontend falls back to the '/media' default, and the scanner walks a
    # non-existent path returning zero files. Surface this as a 400 so the
    # frontend toast is honest rather than pretending the scan succeeded.
    from pathlib import Path as _Path
    for p in effective_roots:
        try:
            root = _Path(p).resolve()
        except (OSError, ValueError) as e:
            raise HTTPException(400, f"Invalid library path '{p}': {e}")
        if not root.exists():
            raise HTTPException(
                400,
                f"Library folder doesn't exist: {p}. Set it in Settings → Paths.",
            )
        if not root.is_dir():
            raise HTTPException(400, f"Library path is not a folder: {p}.")

    # Delegate to the shared trigger (claims locks + launches the worker).
    # None means another worker grabbed the lock between our fast-fail check
    # and the DB CAS → 409, identical to the original behaviour.
    scan_id = await _start_scan(effective_roots, source="manual")
    if scan_id is None:
        raise HTTPException(409, "A scan is already running. Please wait for it to complete.")
    scan = await session.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(500, "Scan row vanished after creation.")
    return scan


@router.get("", response_model=list[ScanOut])
async def list_scans(session: AsyncSession = Depends(get_session)) -> list[Scan]:
    result = await session.scalars(select(Scan).order_by(Scan.created_at.desc()))
    return list(result)


@router.get("/{scan_id}", response_model=ScanOut)
async def get_scan(scan_id: int, session: AsyncSession = Depends(get_session)) -> Scan:
    scan = await session.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(404, "Scan not found")
    return scan
