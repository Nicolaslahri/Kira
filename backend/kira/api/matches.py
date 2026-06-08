"""Match endpoints — run the matcher for a single file or a bulk batch."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from kira.api.match_cleanup import detach_and_delete_matches
from kira.database import SessionLocal, get_session
from kira.matcher import MatchEngine
from kira.matcher.cour_routing import remap_umbrella_local_to_absolute
from kira.matcher.engine import compute_series_group_id, fetch_match_metadata, registry_from_settings, resolve_canonical_season
from kira.models import Match, MediaFile, Setting
from kira.parser import ParsedFile
from kira.providers.opensubtitles import identify_file_by_hash
from kira.schemas import ManualMatch, MediaFileOut
from kira.settings_store import unwrap_str as _unwrap_setting  # canonical settings-value unwrap

router = APIRouter(tags=["matches"])


async def _rematch_one(
    media_file: MediaFile,
    engine: MatchEngine,
    session: AsyncSession,
    *,
    force: bool = False,
) -> int:
    """Run the matcher for one file, replace its Match rows. Returns count stored.

    Now does the same episode-title enrichment + series-group-id stamping
    as the scan-time cluster matcher — previously this path silently
    stripped both, so any user-triggered rematch produced rows with
    "Episode N" generic labels and no franchise grouping.

    ── C3: Atomicity + Manual Pin Protection ─────────────────────────
    Previously: a `DELETE FROM matches WHERE media_file_id=X` followed by
    N inserts. Not atomic — concurrent readers could see ZERO matches
    mid-flight, and a manually-pinned row would be obliterated silently
    if the user happened to be reviewing while /rematch-all ran.

    Now: wrapped in a SAVEPOINT (`begin_nested`) so reads always see
    either the old set or the new set, never an empty middle. Manual
    rows are preserved unless `force=True` — the auto-flow defaults to
    preservation; the UI must explicitly request destruction. New auto
    candidates with the same `(provider, provider_id)` as a preserved
    manual row are filtered out so the UNIQUE constraint can't fire.
    """
    if not media_file.parsed_data:
        return 0

    from sqlalchemy.orm import selectinload as _selectinload
    refreshed = await session.scalar(
        select(MediaFile)
        .options(_selectinload(MediaFile.matches))
        .where(MediaFile.id == media_file.id)
    )

    # ── Separation of Discovery from Enrichment ───────────────────────
    # `_rematch_one` historically conflated two concerns: re-running
    # `engine.match()` to (re)discover the series identity, and
    # populating the enrichment fields (episode_title, metadata_blob,
    # poster_url). The old sticky-match guard's early `return` correctly
    # protected the user's pinned identity from being re-guessed — but
    # it ALSO blocked the enrichment fields from ever being filled. So
    # bulk-manual-match rows (which write `is_manual=True` but leave
    # episode_title/metadata_blob null intentionally, expecting a follow-
    # up fill) would sit forever with no titles or art, and the auto-
    # heal loop would queue them every boot and slam into the early
    # return every time.
    #
    # The structural fix: when the row is manually pinned but missing
    # enrichment fields, take the ENRICHMENT-ONLY fast path. Fetch the
    # episode list + metadata blob + season poster for the EXACT pinned
    # `(provider, provider_id)`. Never call `engine.match()` for these
    # — Discovery is locked, only Enrichment is missing. Cost: one
    # episode-list call per series (cached), one details call, one
    # poster call. No trigram, no similarity, no provider search burn.
    manual_sel = None
    if refreshed and not force:
        manual_sel = next(
            (m for m in refreshed.matches if m.is_selected and m.is_manual),
            None,
        )

    if manual_sel is not None:
        needs_enrichment = (
            (manual_sel.match_type == "tv_episode" and manual_sel.episode_title is None)
            or manual_sel.metadata_blob is None
            or (manual_sel.match_type == "tv_episode" and not manual_sel.poster_url)
        )
        if not needs_enrichment:
            # Pinned and fully populated. Honor the user's choice; exit.
            return len(refreshed.matches)

        parsed_for_enrich = ParsedFile(**media_file.parsed_data)
        ep_num_for_enrich = (
            parsed_for_enrich.absolute_episode
            if parsed_for_enrich.absolute_episode is not None
            else parsed_for_enrich.episode
        )

        # 1) Episode title — only when missing on a tv_episode pin.
        if (
            manual_sel.match_type == "tv_episode"
            and manual_sel.episode_title is None
        ):
            try:
                from kira.api.scans import _fetch_episodes_for_match, _lookup_episode_title
                ep_results = await _fetch_episodes_for_match(
                    manual_sel.provider,
                    manual_sel.provider_id,
                    parsed_for_enrich.season,
                    engine.registry,
                )
                ep_dict: dict[tuple[int, int], str | None] = {
                    (ep.season, ep.episode): ep.title for ep in ep_results
                }
                # absolute_number→episode map so an absolute-named file ("- 88")
                # can reach the season-local cour table in the routing below.
                enrich_abs_to_local: dict[int, int] = {
                    ep.absolute_number: ep.episode
                    for ep in ep_results
                    if getattr(ep, "absolute_number", None) is not None and ep.episode is not None
                }
                # ── Local-episode memory for multi-cour anime ────────
                # When this pin's provider_id is a sibling cour AID
                # (e.g. Bleach S17E31 routed to AID 18671), the
                # episode list returned by `get_episodes` is the
                # COUR's own 14-entry list keyed (1, 1) through (1, 14).
                # But parsed.episode is 31 (the season-absolute number
                # from the filename). The default lookup tries (17, 31)
                # and (1, 31), both miss → orphan.
                # The cour-routing helper translates 31 → local 5 for
                # AID 18671. Passing that as `local_episode` lets the
                # helper's tier-3 lookup hit (1, 5) = "Against the
                # Judgement" or whatever Cour 3 ep 5 is. Without this,
                # every routed file silently orphans even though the
                # data is correct in the DB.
                local_ep_for_enrich: int | None = None
                if manual_sel.provider == "anidb":
                    try:
                        from kira.matcher.cour_routing import (
                            build_cour_routing_table,
                            route_file_to_cour,
                        )
                        cour_table_enrich = await build_cour_routing_table(
                            manual_sel.provider,
                            manual_sel.provider_id,
                            parsed_for_enrich.season,
                            registry=engine.registry,
                        )
                        if cour_table_enrich is not None and ep_num_for_enrich is not None:
                            routed = route_file_to_cour(cour_table_enrich, ep_num_for_enrich, enrich_abs_to_local)
                            # Only trust the local-episode translation when
                            # the cour-routed AID MATCHES the row's
                            # provider_id. If they disagree, the row is
                            # mis-routed (e.g. pre-fix manual pin still on
                            # Cour 1's AID 15449 when the cour table says
                            # E31 belongs to Cour 3 AID 18671) and using
                            # the local_episode lookup against Cour 1's
                            # episode list would write Cour 1's ep 5 title
                            # onto a Cour 3 file. Worse than leaving the
                            # row orphaned. Let the heal write null; the
                            # user can re-pin to invoke the corrected
                            # routing in bulk_select_manual_match.
                            if (
                                routed is not None
                                and str(routed[0]) == str(manual_sel.provider_id)
                            ):
                                _, local_ep_for_enrich = routed
                    except Exception as e:
                        print(f"_rematch_one enrichment: cour route build failed for {media_file.id}: {e!r}")

                manual_sel.episode_title = _lookup_episode_title(
                    ep_dict,
                    manual_sel.provider,
                    parsed_for_enrich,
                    ep_num_for_enrich,
                    local_episode=local_ep_for_enrich,
                )
                # When routing produced a local episode AND the lookup
                # hit a title, also write the local_episode to
                # episode_number so the row's user-facing episode index
                # matches the AID's canonical numbering (Cour 3 ep 1
                # not Cour 3 ep 27). This makes the popup row display
                # the right episode badge and the rename template emit
                # the right SxxExx.
                if (
                    manual_sel.episode_title is not None
                    and local_ep_for_enrich is not None
                ):
                    manual_sel.episode_number = local_ep_for_enrich
            except Exception as e:
                print(f"_rematch_one enrichment: episode fetch failed for {media_file.id}: {e!r}")

        # 2) Rich metadata blob + overview.
        if manual_sel.metadata_blob is None:
            try:
                top_meta = await fetch_match_metadata(
                    manual_sel.provider,
                    manual_sel.provider_id,
                    manual_sel.match_type,
                    engine.registry,
                )
                if top_meta is not None:
                    manual_sel.metadata_blob = top_meta
                    if not manual_sel.overview:
                        manual_sel.overview = top_meta.get("overview")
            except Exception as e:
                print(f"_rematch_one enrichment: metadata fetch failed for {media_file.id}: {e!r}")

        # 3) Poster fill — only when the row currently has none. Never
        # clobber an existing URL with a transient provider miss. The
        # source path depends on the provider:
        #   - TVDB / TMDB shows: per-season poster via `get_season_poster`
        #     (each season of a franchise gets its own cover instead of
        #     all sharing the series-level image).
        #   - AniDB anime: `get_picture_url` which walks AniDB → TVDB
        #     cross-ref → TMDB cross-ref → AniDB CDN. Covers the
        #     "user clicked a result before the modal's lazy-load
        #     filled in r.poster_url" race AND multi-cour clusters
        #     where files routed to a sibling cour AID need that
        #     cour's own poster (rather than the user-picked AID's
        #     image).
        if not manual_sel.poster_url:
            try:
                pp = engine.registry.build(manual_sel.provider)
            except Exception:
                pp = None
            if pp is not None:
                try:
                    fetched_url: str | None = None
                    if (
                        manual_sel.match_type == "tv_episode"
                        and manual_sel.provider in ("tvdb", "tmdb")
                        and parsed_for_enrich.season is not None
                        and hasattr(pp, "get_season_poster")
                    ):
                        fetched_url = await pp.get_season_poster(  # type: ignore[attr-defined]
                            manual_sel.provider_id, parsed_for_enrich.season,
                        )
                    elif (
                        manual_sel.provider == "anidb"
                        and hasattr(pp, "get_picture_url")
                    ):
                        fetched_url = await pp.get_picture_url(  # type: ignore[attr-defined]
                            manual_sel.provider_id,
                        )
                    if fetched_url:
                        manual_sel.poster_url = fetched_url
                except Exception as e:
                    print(f"_rematch_one enrichment: poster fetch failed for {media_file.id}: {e!r}")

        # Reflect that the file is now matched, in case bulk-select left
        # the MediaFile status as `no_match` / `discovered` / `matching`.
        if media_file.status in ("no_match", "matching", "discovered", "parsed"):
            media_file.status = "matched"

        # Enrichment-only path: bypass all discovery logic + savepoint
        # rewrite below. Caller commits.
        return len(refreshed.matches)

    parsed = ParsedFile(**media_file.parsed_data)

    # Re-parse from the filename. The parser is deterministic and cheap
    # (microseconds); re-running it picks up later pattern improvements
    # without forcing the user to re-scan. Example: rows scanned before
    # P5 grew to 4-digit support stored episode=None for "EP1156"; a
    # fresh parse now extracts ep 1156 so the rematch can actually find
    # the episode in the provider's episode list.
    from pathlib import Path as _Path
    from kira.parser import parse_filename
    from kira.api.scans import _compute_series_key, _compute_variant_key
    parent = str(_Path(media_file.file_path).parent) if media_file.file_path else ""
    fresh = parse_filename(_Path(media_file.file_path).name, parent_path=parent)
    if fresh.to_dict() != media_file.parsed_data:
        media_file.parsed_data = fresh.to_dict()
        if media_file.media_type != fresh.media_type:
            media_file.media_type = fresh.media_type
        # series_key was computed against the OLD parsed data — refresh it
        # so subsequent rescans cluster this file correctly. Most relevant
        # for anime files where a new season hint (S3 / parent "Season 3")
        # now changes the cluster identity.
        new_key = _compute_series_key(fresh)
        if new_key != media_file.series_key:
            media_file.series_key = new_key
        # variant_key also recomputed — new format-stripper additions (e.g.
        # newly recognized edition keyword) flow through without rescan.
        new_variant = _compute_variant_key(fresh)
        if new_variant != (media_file.variant_key or ""):
            media_file.variant_key = new_variant
        parsed = fresh

    # Differentiate "API failed" from "API returned 0 results" — the first
    # must preserve existing matches (network blip shouldn't wipe state),
    # the second is a legitimate "no match" that does clear them.
    try:
        scored = await engine.match(parsed, limit=5)
    except Exception as e:
        print(f"_rematch_one: matcher raised for file {media_file.id}: {e!r}")
        return 0  # leave existing rows untouched

    # Fetch the episode list for the top match (TV/anime only) FIRST — cour
    # routing below needs it to bridge absolute-numbered files into the cour
    # table. Keyed by (season, episode) for the episode_title write (the season
    # in the key stops cross-season collisions); `abs_to_local` is the
    # absolute_number→episode map that lets a file named by series-absolute
    # ("- 88") reach the season-local cour table — the SAME bridge the scan
    # path uses, so Re-identify now matches a full rescan.
    #
    # Routes through `_fetch_episodes_for_match` (in scans.py) which prefers
    # TVDB cross-ref for AniDB matches — AniDB-ban hardening; one cached call,
    # not a per-file AniDB hit.
    episodes_by_key: dict[tuple[int, int], str | None] = {}
    abs_to_local: dict[int, int] = {}
    if scored and scored[0].match_type == "tv_episode":
        try:
            from kira.api.scans import _fetch_episodes_for_match
            ep_results = await _fetch_episodes_for_match(
                scored[0].provider, scored[0].provider_id, parsed.season, engine.registry,
            )
            for ep in ep_results:
                episodes_by_key[(ep.season, ep.episode)] = ep.title
                _abs = getattr(ep, "absolute_number", None)
                if _abs is not None and ep.episode is not None:
                    abs_to_local[_abs] = ep.episode
        except Exception as e:
            print(f"_rematch_one: get_episodes failed: {e!r}")

    # Flat-umbrella detection (mirrors _match_cluster): a single AniDB AID that
    # numbers the whole long-runner absolutely (One Piece 69 → tvdb_season None).
    # `local_to_abs` (reverse of abs_to_local) drives the local→absolute remap so
    # a TVDB-season-LOCAL file ("S23E04") RE-IDENTIFIES to its absolute (1159) —
    # so Re-identify produces the same number a full rescan does. Per-season AIDs
    # (Frieren S2, AoT cours) carry a tvdb_season → not umbrellas → left local.
    is_flat_umbrella = False
    if scored and scored[0].match_type == "tv_episode" and scored[0].provider == "anidb":
        try:
            from kira.providers.anime_mappings import AnimeMappings
            is_flat_umbrella = (await AnimeMappings.tvdb_season(int(scored[0].provider_id))) is None
        except (ValueError, TypeError):
            is_flat_umbrella = False
        except Exception as e:
            print(f"_rematch_one: flat-umbrella check failed: {e!r}")
            is_flat_umbrella = False
    local_to_abs: dict[int, int] = {loc: ab for ab, loc in abs_to_local.items()}

    # ── Per-file cour routing (shared helper) ──────────────────────────
    # Single source of truth: the same builder _match_cluster uses. Auto-
    # heal + manual single-file rematch + bulk rematch all route through
    # this; without it, the engine's tie-break would re-pick Cour 1's
    # AID for every Bleach S17 file and orphan E14-E40 silently. Passing
    # `abs_to_local` lets absolute-numbered files (AoT "- 88") route to their
    # cour here too — previously only the scan path could (Re-identify missed).
    routed_aid: int | None = None
    routed_local_ep: int | None = None
    routed_eps: list = []  # cour AID's own episode list for title fallback
    if scored and scored[0].match_type == "tv_episode":
        try:
            from kira.matcher.cour_routing import (
                build_cour_routing_table,
                route_file_to_cour_precise,
            )
            cour_table = await build_cour_routing_table(
                scored[0].provider, scored[0].provider_id, parsed.season,
                registry=engine.registry,
            )
            if cour_table is not None:
                file_ep_for_routing = (
                    parsed.episode if parsed.episode is not None
                    else parsed.absolute_episode
                )
                routed = await route_file_to_cour_precise(
                    cour_table, file_ep_for_routing,
                    provider=scored[0].provider, top_provider_id=scored[0].provider_id,
                    parsed_season=parsed.season,
                    abs_to_local=abs_to_local,
                )
                if routed is not None:
                    routed_aid, routed_local_ep = routed
                    # Pre-fetch the routed cour's own episode list — used
                    # below as fallback when the top match's episode list
                    # doesn't carry the lumped TVDB data (split-cour case).
                    try:
                        from kira.api.scans import _fetch_episodes_for_match
                        routed_eps = list(await _fetch_episodes_for_match(
                            "anidb", str(routed_aid),
                            parsed.season, engine.registry,
                        ))
                    except Exception as e:
                        print(f"_rematch_one: routed cour {routed_aid} episode fetch failed: {e!r}")
                        routed_eps = []
        except Exception as e:
            print(f"_rematch_one: cour routing build failed: {e!r}")

    # Snapshot manual rows BEFORE the delete so we can preserve them
    # across the rematch. Without this, /rematch-all + auto-heal silently
    # destroy every manual choice the user has ever made.
    preserved_manual: list[Match] = []
    preserved_pins: set[tuple[str, str]] = set()
    if not force and refreshed:
        for m in refreshed.matches:
            if m.is_manual:
                preserved_manual.append(m)
                preserved_pins.add((m.provider or "", m.provider_id or ""))

    # F-02: snapshot the OLD selected match's poster_url before delete.
    # If the new top-match's poster_url is null (transient TVDB hiccup,
    # search response missing image_url, season-poster fetch returned
    # null), we carry the old value forward so the cover doesn't
    # silently disappear from the grid. Empty cover = grid placeholder
    # gradient = "looks broken" — single-most-visible failure mode for
    # the user. This fallback is `None or old` so a fresh non-null
    # value always wins.
    old_selected_poster: str | None = None
    if refreshed:
        for m in refreshed.matches:
            if m.is_selected and m.poster_url:
                old_selected_poster = m.poster_url
                break

    ep_num = parsed.absolute_episode if parsed.absolute_episode is not None else parsed.episode
    from kira.api.scans import _lookup_episode_title
    top_provider = scored[0].provider if scored else ""
    # Phase 4 reroute stashes local_episode on raw; tier-3 lookup uses it.
    top_local_ep = (scored[0].raw or {}).get("local_episode") if scored and scored[0].raw else None
    ep_title: str | None = _lookup_episode_title(
        episodes_by_key, top_provider, parsed, ep_num, local_episode=top_local_ep,
    )
    # Cour-routing fallback: if the top match's episode list doesn't
    # carry this episode (TVDB-split case — TVDB's S17 holds only Cour 1's
    # 13 eps, so files E14+ miss), try the routed cour AID's own list
    # keyed by the cour-local episode number.
    if ep_title is None and routed_aid is not None and routed_local_ep is not None and routed_eps:
        routed_eb_key: dict[tuple[int, int], str | None] = {
            (ep.season, ep.episode): ep.title for ep in routed_eps
        }
        ep_title = _lookup_episode_title(
            routed_eb_key, "anidb", parsed, routed_local_ep,
        )

    # Keep episode_number consistent with the routed cour AID's own numbering
    # (mirrors the scan path) so the popup can pair the file against that AID's
    # episode list. Rename output is unaffected (renders from parsed / {{absx}}).
    if routed_aid is not None and routed_local_ep is not None:
        ep_num = routed_local_ep

    # Flat-umbrella local→absolute remap (mirrors _match_cluster; the One Piece
    # "S23E04" → 1159 fix on the Re-identify path). No-ops for absolute-named
    # files, per-season AIDs, normal TV, and early-cour self-maps.
    ep_num = remap_umbrella_local_to_absolute(
        ep_num, is_flat_umbrella=is_flat_umbrella, routed_aid=routed_aid, local_to_abs=local_to_abs,
    )

    # When the file routes to a specific cour AID, pull its display title
    # from AniDB's in-memory cache so the Match row carries the COUR's
    # canonical name (e.g. "Bleach: TYBW The Conflict" for Cour 3) rather
    # than the matcher's top-pick (typically Cour 1's name).
    routed_title_override: str | None = None
    if routed_aid is not None:
        try:
            from kira.providers.anidb import AniDBProvider as _AniDB
            routed_title_override = _AniDB._pick_display_title(routed_aid)
        except Exception:
            routed_title_override = None

    # Fetch rich popup metadata for the top match. One extra call per file
    # rematch; cached on the Match row's metadata_blob.
    top_metadata = None
    if scored:
        top = scored[0]
        top_metadata = await fetch_match_metadata(top.provider, top.provider_id, top.match_type, engine.registry)

        # Per-season poster — see scans.py _match_cluster for the rationale.
        # TVDB/TMDB shows that share one provider_id across seasons need
        # season-specific art so each per-season card in the franchise
        # group renders distinct cover art (mirroring AniDB sequel-AIDs).
        if (
            top.match_type == "tv_episode"
            and top.provider in ("tvdb", "tmdb")
            and parsed.season is not None
        ):
            try:
                poster_provider = engine.registry.build(top.provider)
                if hasattr(poster_provider, "get_season_poster"):
                    season_url = await poster_provider.get_season_poster(
                        top.provider_id, parsed.season,
                    )
                    if season_url:
                        top.poster_url = season_url
            except Exception as e:
                print(f"_rematch_one: per-season poster fetch failed: {e!r}")

    # AniDB search never returns an overview; the cross-ref via Fribb
    # populates `overview` on the metadata blob. Promote it onto the
    # Match.overview column for the top match so the popup hero (which
    # reads repMatch.overview) shows it.
    top_overview_fallback = (top_metadata or {}).get("overview") if top_metadata else None

    # Determine if any preserved manual row should remain the selected one.
    # If yes, NONE of the new auto candidates can be selected (the user's
    # pick wins). If no manual rows exist, the top auto-match is selected.
    keep_manual_selected = any(m.is_selected for m in preserved_manual)

    # ── Atomic replacement via SAVEPOINT ──────────────────────────────
    # Inside this block, the delete + inserts succeed or fail as one unit.
    # Concurrent readers see either the old matches or the new ones —
    # never an empty middle. If any insert fails, the savepoint rolls
    # back and the user keeps their existing matches.
    async with session.begin_nested():
        # Delete only non-manual rows when preservation is on. With force=True
        # (or no manual rows present), delete everything.
        if force or not preserved_manual:
            # Detach rename_history back-refs first — on a DB whose
            # rename_history.match_id FK predates ON DELETE SET NULL, a raw
            # delete of a previously-renamed file's Match rows trips
            # "FOREIGN KEY constraint failed" (the auto-heal crash).
            await detach_and_delete_matches(session, media_file_id=media_file.id)
        else:
            await detach_and_delete_matches(
                session, media_file_id=media_file.id, manual_false_only=True,
            )

        # Update file status to reflect outcome — no_match when the matcher
        # rejected everything AND no preserved manual pin would carry status.
        if not scored and not preserved_manual:
            media_file.status = "no_match"
        elif media_file.status in ("no_match", "matching", "discovered", "parsed"):
            media_file.status = "matched"

        # Insert new auto candidates, skipping any whose (provider, provider_id)
        # collides with a preserved manual row (UNIQUE constraint guard).
        # Also: never insert with is_selected=True if a manual row already
        # claims the selected slot.
        rank_emitted = 0
        for m in scored:
            # Intercept the top candidate's provider_id + title when cour
            # routing fires (rank_emitted == 0 only — alternate candidates
            # keep their original provider_id so the user can still pick
            # a different cour from the alternates list).
            row_provider_id = m.provider_id
            row_title = m.title
            if rank_emitted == 0 and routed_aid is not None:
                row_provider_id = str(routed_aid)
                if routed_title_override:
                    row_title = routed_title_override

            # Pin collision check uses the (possibly-routed) provider_id:
            # a preserved manual row pinned to the same routed cour AID
            # should suppress this auto row to avoid UNIQUE violation.
            pin = (m.provider or "", row_provider_id or "")
            if pin in preserved_pins:
                continue  # already represented by the preserved manual row
            gid = await compute_series_group_id(m.provider, row_provider_id, engine.registry)
            canonical_season = await resolve_canonical_season(m.provider, row_provider_id, parsed.season)
            row_overview = m.overview or (top_overview_fallback if rank_emitted == 0 else None)
            # F-02: poster preservation — for the top auto-match only,
            # if the new poster_url came back null but the old selected
            # match had one, carry it forward. A transient image-less
            # search response shouldn't blank the user's cover.
            poster_for_row = m.poster_url
            if rank_emitted == 0 and not poster_for_row and old_selected_poster:
                poster_for_row = old_selected_poster
            session.add(Match(
                media_file_id=media_file.id,
                provider=m.provider,
                provider_id=row_provider_id,
                match_type=m.match_type,
                confidence=m.confidence,
                title=row_title,
                year=m.year,
                series_name=row_title if m.match_type == "tv_episode" else None,
                season_number=canonical_season,
                episode_number=ep_num,
                episode_title=ep_title if rank_emitted == 0 else None,
                poster_url=poster_for_row,
                overview=row_overview,
                # Only the auto-top wins selection when there's no manual pin.
                is_selected=(rank_emitted == 0 and not keep_manual_selected),
                series_group_id=gid,
                metadata_blob=top_metadata if rank_emitted == 0 else None,
            ))
            rank_emitted += 1
    return rank_emitted + len(preserved_manual)


@router.post("/files/{file_id}/rematch", response_model=MediaFileOut)
async def rematch_file(
    file_id: int,
    force: bool = False,
    session: AsyncSession = Depends(get_session),
) -> MediaFile:
    """Re-run the matcher for one file.

    `force=true` opts into destroying manually-pinned matches. Default is
    False — the matcher runs but a user's `is_manual` selection is
    preserved (the row stays, no auto candidate overrides it). UI surfaces
    this as a "Re-match (including pinned)" toggle in the rematch dialog.
    """
    media_file = await session.get(MediaFile, file_id)
    if media_file is None:
        raise HTTPException(404, "File not found")

    async with httpx.AsyncClient() as client:
        engine = MatchEngine(await registry_from_settings(client))
        await _rematch_one(media_file, engine, session, force=force)

    await session.commit()
    # Reload with the matches relationship eager-loaded so the response
    # serializer can read MediaFile.matches without triggering an async
    # lazy load (which would 500 from sync Pydantic context).
    from sqlalchemy.orm import selectinload
    refreshed = await session.scalar(
        select(MediaFile)
        .options(selectinload(MediaFile.matches))
        .where(MediaFile.id == file_id)
    )
    if refreshed:
        refreshed.matches.sort(key=lambda m: m.confidence, reverse=True)
    return refreshed  # type: ignore[return-value]


@router.post("/files/{file_id}/select/{match_id}", response_model=MediaFileOut)
async def select_match(
    file_id: int,
    match_id: int,
    session: AsyncSession = Depends(get_session),
) -> MediaFile:
    """Mark an existing candidate as the selected match for this file."""
    from sqlalchemy.orm import selectinload  # local import — used only here
    media_file = await session.scalar(
        select(MediaFile)
        .options(selectinload(MediaFile.matches))
        .where(MediaFile.id == file_id)
    )
    if media_file is None:
        raise HTTPException(404, "File not found")
    target = next((m for m in media_file.matches if m.id == match_id), None)
    if target is None:
        raise HTTPException(404, "Match not found for this file")
    for m in media_file.matches:
        m.is_selected = (m.id == match_id)
    # Picking a candidate from the list is a deliberate user action → sticky.
    # Survives /rematch-all + auto-heal so it doesn't silently get clobbered.
    target.is_manual = True
    await session.commit()
    # Refresh `updated_at` (server-side `onupdate`) so the serializer doesn't
    # trigger a lazy-load and raise MissingGreenlet.
    await session.refresh(media_file, ["updated_at"])
    media_file.matches.sort(key=lambda m: m.confidence, reverse=True)
    return media_file


def _apply_media_type_for_manual_pick(
    media_file: MediaFile, provider: str, payload_media_type: str | None
) -> None:
    """Reconcile a file's media_type with a MANUAL match pick, then recompute
    its series/variant keys so it re-clusters into the right group.

    Why: manual select used to leave media_type untouched, so pinning a
    `tv`-typed file (e.g. one scanned from a `/tv/` usenet folder) to an AniDB
    anime kept it in the "TV Series" group. AniDB is anime-only, so an AniDB
    pick is authoritative → anime; otherwise we honor the type of the result
    the user explicitly chose (tv / movie / music). No-op when unchanged."""
    if provider == "anidb":
        new_mt = "anime"
    elif payload_media_type in ("movie", "tv", "anime", "music"):
        new_mt = payload_media_type
    else:
        return
    if new_mt == media_file.media_type:
        return
    try:
        from kira.matcher.media_type import apply_media_type_and_recompute_keys
        apply_media_type_and_recompute_keys(media_file, new_mt)
    except Exception:
        # Key recompute is best-effort; the helper sets media_type first
        # internally, so even if the recompute raises the grouping fix
        # (media_type flip) has already landed.
        media_file.media_type = new_mt


@router.post("/files/{file_id}/select-manual", response_model=MediaFileOut)
async def select_manual_match(
    file_id: int,
    payload: ManualMatch,
    session: AsyncSession = Depends(get_session),
) -> MediaFile:
    """Create a new Match from a Manual Search pick and mark it selected.

    Inserts a high-confidence (1.0) match row representing the user's explicit
    choice. Existing matches are kept as alternates but un-selected.
    """
    from sqlalchemy.orm import selectinload
    media_file = await session.scalar(
        select(MediaFile)
        .options(selectinload(MediaFile.matches))
        .where(MediaFile.id == file_id)
    )
    if media_file is None:
        raise HTTPException(404, "File not found")
    match_type = "tv_episode" if payload.media_type in ("tv", "anime") else "movie"

    # First pass: deselect every existing row AND find any row whose
    # (provider, provider_id) matches the user's target. Same hazard +
    # fix as the bulk endpoint (Autopsy 2):
    # `(media_file_id, provider, provider_id)` has a UNIQUE constraint,
    # so blindly INSERTing a new row when the auto-matcher had already
    # registered the same show as an unselected alternate crashes the
    # whole commit with IntegrityError. Manual match on "Wuthering
    # Heights" (or any movie whose auto-match already landed on the
    # same TMDB id) would silently fail and the user would see no
    # state change in the UI.
    target_match: Match | None = None
    for m in media_file.matches:
        m.is_selected = False
        if m.provider == payload.provider and m.provider_id == payload.provider_id:
            target_match = m

    if target_match is not None:
        # Row exists — commandeer it. Preserves any existing
        # episode_title / metadata_blob / season_number enrichment
        # from a prior auto-match pass; only overwrite display fields
        # when the user's payload actually carries richer data (the
        # manual search response sometimes carries a thinner payload
        # than the existing row).
        target_match.is_selected = True
        target_match.is_manual = True
        target_match.confidence = 1.0
        target_match.match_type = match_type
        if payload.title:
            target_match.title = payload.title
            if match_type == "tv_episode":
                target_match.series_name = payload.title
        if payload.year is not None:
            target_match.year = payload.year
        if payload.poster_url:
            target_match.poster_url = payload.poster_url
        if payload.overview:
            target_match.overview = payload.overview
    else:
        # Truly new manual candidate — safe to append.
        new_match = Match(
            media_file_id=file_id,
            provider=payload.provider,
            provider_id=payload.provider_id,
            match_type=match_type,
            confidence=1.0,
            title=payload.title,
            year=payload.year,
            series_name=payload.title if match_type == "tv_episode" else None,
            poster_url=payload.poster_url,
            overview=payload.overview,
            is_selected=True,
            is_manual=True,  # user's pick — sticky across heal/rematch
        )
        session.add(new_match)

    # Manual match resolves no_match state.
    if media_file.status == "no_match":
        media_file.status = "matched"
    # Move the file into the right group (e.g. TV → Anime when pinned to an
    # AniDB show) so a manual fix doesn't leave it stranded under TV Series.
    _apply_media_type_for_manual_pick(media_file, payload.provider, payload.media_type)
    await session.commit()
    # Refresh with the new match in the relationship.
    refreshed = await session.scalar(
        select(MediaFile)
        .options(selectinload(MediaFile.matches))
        .where(MediaFile.id == file_id)
    )
    refreshed.matches.sort(key=lambda m: m.confidence, reverse=True)  # type: ignore[union-attr]
    return refreshed  # type: ignore[return-value]


@router.post("/files/{file_id}/identify-by-hash", response_model=MediaFileOut)
async def identify_by_content_hash(
    file_id: int,
    session: AsyncSession = Depends(get_session),
) -> MediaFile:
    """M5 — content-hash identification (filename-independent).

    Hash the file's BYTES (OSDb 64-bit), ask OpenSubtitles which release that
    hash is, and pin the resulting TMDB match. Works even when the filename is
    total garbage — the only matching path that does. Requires an OpenSubtitles
    API key (Settings → Connections). Reuses the hardened manual-select writer,
    so the pinned Match is sticky across heal / rematch like any user pick.
    """
    media_file = await session.get(MediaFile, file_id)
    if media_file is None:
        raise HTTPException(404, "File not found")
    if not media_file.file_path:
        raise HTTPException(422, "File has no on-disk path to hash")

    key_row = await session.get(Setting, "providers.opensubtitles.api_key")
    api_key = key_row.value if key_row else None
    if isinstance(api_key, dict):  # masked placeholder, not a usable key
        api_key = None
    if not api_key:
        raise HTTPException(
            400, "OpenSubtitles API key not configured (Settings → Connections)"
        )

    async with httpx.AsyncClient() as client:
        ident = await identify_file_by_hash(media_file.file_path, str(api_key), client)
    if not ident:
        raise HTTPException(404, "No content-hash match found on OpenSubtitles")

    tmdb_id = ident.get("tmdb_id")
    if not tmdb_id:
        raise HTTPException(422, "Hash matched but OpenSubtitles returned no TMDB id")

    feature = (ident.get("feature_type") or "").lower()
    media_type = "movie" if feature == "movie" else "tv"
    payload = ManualMatch(
        provider="tmdb",
        provider_id=str(tmdb_id),
        media_type=media_type,
        title=ident.get("title"),
        year=ident.get("year"),
    )
    # Reuse the hardened commandeer-or-append writer (sticky manual pin).
    return await select_manual_match(file_id, payload, session)


async def load_opensubtitles_settings(session: AsyncSession):
    """(api_key, username, password, languages) from settings. api_key is None
    when unset or stored as a masked placeholder. languages defaults to ['en']."""
    async def _val(key: str):
        row = await session.get(Setting, key)
        return row.value if row is not None else None

    api_key_raw = await _val("providers.opensubtitles.api_key")
    api_key = None if isinstance(api_key_raw, dict) else _unwrap_setting(api_key_raw)
    user = _unwrap_setting(await _val("providers.opensubtitles.username"))
    pw = _unwrap_setting(await _val("providers.opensubtitles.password"))

    languages = ["en"]
    lang_v = await _val("subtitles.languages")
    if isinstance(lang_v, str) and lang_v.strip():
        languages = [s.strip().lower() for s in lang_v.split(",") if s.strip()]
    elif isinstance(lang_v, list) and lang_v:
        languages = [str(s).strip().lower() for s in lang_v if str(s).strip()]
    return api_key, user, pw, (languages or ["en"])


@router.post("/files/{file_id}/fetch-subtitles")
async def fetch_subtitles(
    file_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """#11 — download subtitles for one file from OpenSubtitles and save them as
    `<stem>.<lang>.srt` sidecars (which the rename co-move then carries). Hash-
    first, falling back to the selected match's TMDB/IMDb id + season/episode.
    Requires an OpenSubtitles API key; downloads also need username/password
    (the API charges downloads against the user's quota)."""
    from sqlalchemy.orm import selectinload
    from kira.providers.opensubtitles import fetch_and_save_subtitles

    media_file = await session.scalar(
        select(MediaFile).options(selectinload(MediaFile.matches)).where(MediaFile.id == file_id)
    )
    if media_file is None:
        raise HTTPException(404, "File not found")
    if not media_file.file_path:
        raise HTTPException(422, "File has no on-disk path")

    api_key, user, pw, languages = await load_opensubtitles_settings(session)
    if not api_key:
        raise HTTPException(400, "OpenSubtitles API key not configured (Settings → Connections)")

    selected = next((m for m in media_file.matches if m.is_selected), None)
    tmdb_id = None
    if selected and selected.provider == "tmdb" and selected.provider_id:
        try:
            tmdb_id = int(selected.provider_id)
        except (TypeError, ValueError):
            tmdb_id = None
    season = selected.season_number if selected else None
    episode = selected.episode_number if selected else None

    async with httpx.AsyncClient() as client:
        saved = await fetch_and_save_subtitles(
            media_file.file_path, api_key=api_key, client=client, languages=languages,
            username=user, password=pw, tmdb_id=tmdb_id, season=season, episode=episode,
        )
    return {"saved": saved, "count": len(saved), "languages": languages}


class BulkSelectManualPayload(ManualMatch):
    """Same ManualMatch fields PLUS the list of file IDs to apply to."""
    file_ids: list[int] = Field(..., max_length=10_000)


@router.post("/files/bulk-select-manual", response_model=dict[str, int])
async def bulk_select_manual_match(
    payload: BulkSelectManualPayload,
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Apply the same manual match to every file in `file_ids`.

    For the "Needs matching" workflow: user selects 14 One Pace clusters,
    picks "One Piece" once, all 76 underlying files get pinned to that
    AID with is_manual=True. Future heal/rematch leaves them alone.
    """
    from sqlalchemy.orm import selectinload
    if not payload.file_ids:
        return {"updated": 0, "skipped": 0}
    files = list(await session.scalars(
        select(MediaFile)
        .options(selectinload(MediaFile.matches))
        .where(MediaFile.id.in_(payload.file_ids))
    ))
    match_type = "tv_episode" if payload.media_type in ("tv", "anime") else "movie"

    # ── Per-file cour routing for multi-cour anime ────────────────────
    # When the user picks a single AniDB show (e.g. "Bleach: Thousand
    # Year Blood War") for a 40-file cluster spanning multiple cours,
    # blindly stamping the same AID on every file leaves files E14-E40
    # orphaned: AID 15449 (Cour 1) only has 13 episodes. Per-file cour
    # routing computes the correct sibling cour AID + local episode
    # number for each file using the shared helper from Autopsy 7.
    #
    # For non-anime / non-multi-cour shows, build_cour_routing_table
    # returns None and routing is a no-op — behavior matches today.
    from kira.matcher.cour_routing import (
        build_cour_routing_table,
        route_file_to_cour_precise,
    )
    # Pre-compute the routing table ONCE for the user's pick. The table
    # depends only on (provider, provider_id, parsed.season) which is
    # constant across all files in the cluster, so we don't need to
    # rebuild per file. For mixed-season clusters (rare), we re-derive
    # per file below.
    rep_season: int | None = None
    for f in files:
        if f.parsed_data and f.parsed_data.get("season") is not None:
            rep_season = f.parsed_data.get("season")
            break
    cour_table = None
    abs_to_local: dict[int, int] = {}
    is_flat_umbrella = False
    # The helper's lazy-fetch needs a proper ProviderRegistry to construct
    # an AniDBProvider — this endpoint doesn't have one natively (no
    # matcher engine in scope), so we build a short-lived registry via
    # `registry_from_settings` for the duration of the table build.
    # Without this, the first-ever bulk-pin of a multi-cour franchise
    # (Bleach, MHA, AoT) would see Cour 2/3 episode counts missing from
    # disk cache, the table build aborts, and every file gets stamped
    # with Cour 1's AID — silently orphaning E14-E40.
    try:
        async with httpx.AsyncClient() as _routing_client:
            _routing_registry = await registry_from_settings(_routing_client)
            cour_table = await build_cour_routing_table(
                payload.provider, payload.provider_id, rep_season,
                registry=_routing_registry,
            )
            # Flat-umbrella detection for the local→absolute remap: a single
            # AniDB AID that numbers the whole long-runner absolutely (One Piece
            # 69 → tvdb_season None) has NO cour table, so the abs→local fetch
            # below must ALSO fire for it — not only for multi-cour shows.
            if match_type == "tv_episode" and payload.provider == "anidb":
                try:
                    from kira.providers.anime_mappings import AnimeMappings
                    is_flat_umbrella = (
                        await AnimeMappings.tvdb_season(int(payload.provider_id))
                    ) is None
                except (ValueError, TypeError):
                    is_flat_umbrella = False
            # Build the absolute_number→local-episode map when EITHER a cour table
            # exists (AoT "- 88" bridge) OR the pick is a flat umbrella (One Piece
            # local→absolute remap). One cached episode-list fetch for the pick —
            # parity with the scan + Re-identify paths.
            if cour_table or is_flat_umbrella:
                from kira.api.scans import _fetch_episodes_for_match
                for _ep in await _fetch_episodes_for_match(
                    payload.provider, payload.provider_id, rep_season, _routing_registry,
                ):
                    _abs = getattr(_ep, "absolute_number", None)
                    if _abs is not None and _ep.episode is not None:
                        abs_to_local[_abs] = _ep.episode
    except Exception as e:
        print(f"bulk_select_manual_match: cour routing build failed: {e!r}")
        cour_table = None
    local_to_abs: dict[int, int] = {loc: ab for ab, loc in abs_to_local.items()}
    if cour_table:
        print(
            f"bulk_select_manual_match: routing across {len(cour_table)} cours "
            f"for {payload.provider}/{payload.provider_id} s={rep_season}"
        )

    updated = 0
    for f in files:
        # Per-file routing: derive (final_provider_id, final_episode_number)
        # using the cour table. For files outside any cour range, or
        # non-anime matches, falls back to the user's picked values.
        final_provider_id = payload.provider_id
        final_episode_override: int | None = None
        if cour_table and f.parsed_data:
            file_ep = (
                f.parsed_data.get("episode")
                or f.parsed_data.get("absolute_episode")
            )
            routed = await route_file_to_cour_precise(
                cour_table, file_ep,
                provider=payload.provider, top_provider_id=payload.provider_id,
                parsed_season=rep_season,
                abs_to_local=abs_to_local,
            )
            if routed is not None:
                final_provider_id = str(routed[0])
                final_episode_override = routed[1]

        # First pass: deselect everything AND look for an existing row
        # whose (provider, provider_id) matches the file's FINAL routed
        # target (post-cour-routing). The `(media_file_id, provider,
        # provider_id)` UNIQUE constraint on matches means we CANNOT
        # blindly append — re-running bulk-select for the same (file,
        # show) pair would crash the entire batch with `IntegrityError:
        # UNIQUE constraint failed` and undo every row in the
        # transaction. Same hazard exists when an unselected auto-match
        # candidate already exists for the same show: the user's manual
        # pin would race the auto row.
        #
        # Commandeering the existing row preserves any episode_number /
        # season_number / episode_title / metadata_blob enrichment from
        # the previous match pass too — re-creating it from scratch
        # would null those out.
        target_match: Match | None = None
        for m in f.matches:
            m.is_selected = False
            if m.provider == payload.provider and m.provider_id == final_provider_id:
                target_match = m

        # Preserve any per-file context that's already on existing matches:
        # episode_number / season_number / absolute_episode etc. live on each
        # individual file's Match rows from a previous match run. For the
        # bulk pick, we DON'T know the per-file episode here — that comes
        # from each file's parsed_data, which the matcher reads on its own
        # passes. Just stamp the show identity. The episode lookup happens
        # via a follow-up rematch_one call (which respects is_manual via the
        # provider/provider_id pin) — for now keep it simple: just write the
        # show pin.
        existing_ep = next((m.episode_number for m in f.matches if m.episode_number), None)
        existing_season = next((m.season_number for m in f.matches if m.season_number), None)
        # Pull season/episode from parsed_data when no prior match has them.
        if f.parsed_data:
            existing_ep = existing_ep or f.parsed_data.get("episode")
            existing_season = existing_season or f.parsed_data.get("season")
        # When routing supplied a cour-local episode number, that wins
        # over the season-local one (it's the canonical AniDB episode
        # index for the routed AID). Skip for non-routed files.
        if final_episode_override is not None:
            existing_ep = final_episode_override

        # Re-identify drift repair (One Piece S23E1156→ep1): when cour routing
        # did NOT place this file (single-cour series like One Piece) and the
        # pick is an absolute-numbered AniDB show, re-derive the episode from
        # the file's OWN parsed number. The user picks the SERIES in the modal,
        # never an episode index, so a stored episode_number matching NEITHER
        # parsed.episode NOR parsed.absolute_episode is stale auto-derived drift
        # — safe to overwrite (only episode_number; identity/title/poster kept).
        # Mirrors the absolute-preferred expression used elsewhere (L97-102).
        redrive_episode = False
        if final_episode_override is None and payload.provider == "anidb" and f.parsed_data:
            _p_ep = f.parsed_data.get("episode")
            _p_abs = f.parsed_data.get("absolute_episode")
            _canonical = _p_abs if _p_abs is not None else _p_ep
            _parsed_candidates = {v for v in (_p_ep, _p_abs) if v is not None}
            if _canonical is not None and (existing_ep is None or existing_ep not in _parsed_candidates):
                existing_ep = _canonical
                redrive_episode = True

        # Flat-umbrella local→absolute remap (One Piece "S23E04" → 1159): on a
        # manual bulk-pick of the umbrella, a TVDB-season-LOCAL file's number is
        # rewritten to its absolute so duplicates line up — parity with scan +
        # Re-identify. Only non-routed files (a real umbrella has no cour table;
        # routed files already carry final_episode_override). `redrive_episode`
        # is set so the commandeer branch is authorised to overwrite a stale
        # local index sitting on an existing row.
        if final_episode_override is None:
            _remapped = remap_umbrella_local_to_absolute(
                existing_ep, is_flat_umbrella=is_flat_umbrella,
                routed_aid=None, local_to_abs=local_to_abs,
            )
            if _remapped != existing_ep:
                existing_ep = _remapped
                redrive_episode = True

        # The user's payload.poster_url + payload.title are for the AID
        # THEY PICKED in the modal. For non-routed files (final_provider_id
        # == payload.provider_id) those are correct. For ROUTED files
        # (e.g. Bleach S17E20 routed to Cour 2 AID 17849 when the user
        # picked Cour 1 AID 15449), they're wrong — both the poster and
        # the title belong to Cour 1, not Cour 2.
        # - poster_url: drop (None) so auto-heal fetches the cour's own
        #   art via the AniDB picture cache on the next sweep.
        # - title: try to swap to the routed AID's canonical AniDB
        #   display title (e.g. "Bleach: TYBW - The Conflict" for Cour 3
        #   AID 18671) so each cour-card shows its own franchise name
        #   instead of all three cards reading "Sennen Kessen Hen".
        #   Falls back to payload.title when AniDB's title cache is
        #   cold or the lookup fails.
        is_routed_away = final_provider_id != payload.provider_id
        poster_for_row = None if is_routed_away else payload.poster_url
        title_for_row = payload.title
        if is_routed_away:
            try:
                from kira.providers.anidb import AniDBProvider as _AniDB
                cour_title = _AniDB._pick_display_title(int(final_provider_id))
                if cour_title:
                    title_for_row = cour_title
            except Exception:
                pass

        if target_match is not None:
            # Row exists — commandeer it. Flip the selection + manual
            # flags, refresh confidence, and only overwrite display
            # fields when the user's payload actually carries richer
            # data (manual-search results sometimes ship a thinner
            # payload than the existing auto-match row).
            target_match.is_selected = True
            target_match.is_manual = True
            target_match.confidence = 1.0
            target_match.match_type = match_type
            if title_for_row:
                target_match.title = title_for_row
                if match_type == "tv_episode":
                    target_match.series_name = title_for_row
            if payload.year is not None:
                target_match.year = payload.year
            if poster_for_row:
                target_match.poster_url = poster_for_row
            if payload.overview:
                target_match.overview = payload.overview
            # Fill season/episode only when missing — never clobber a
            # value the matcher previously assigned correctly. Routing
            # overrides take precedence (final_episode_override forces
            # episode_number to the cour-local number).
            if target_match.season_number is None and existing_season is not None:
                target_match.season_number = existing_season
            if final_episode_override is not None:
                target_match.episode_number = final_episode_override
            elif redrive_episode and existing_ep is not None:
                # Authoritative drift repair — overwrite a stale auto-derived
                # episode index (the One Piece 1156→1 collapse) with the file's
                # real number. Narrower than #66's anti-clobber: identity,
                # title, poster, selection are all left untouched.
                target_match.episode_number = existing_ep
            elif target_match.episode_number is None and existing_ep is not None:
                target_match.episode_number = existing_ep
            # Null out enrichment fields that depend on the AID — the
            # auto-heal enrichment-only fast path will refill them
            # against the new (routed) AID on next sweep. Without this,
            # commandeered rows keep their old Cour 1 episode_title
            # even when routed to Cour 3. Same logic for poster_url
            # when routing flipped the AID — but only when no good
            # existing poster is on the commandeered row.
            if target_match.provider_id != final_provider_id:
                target_match.provider_id = final_provider_id
                target_match.episode_title = None
                target_match.metadata_blob = None
                # If commandeering an existing routed-AID row that
                # already has a poster, keep it (it's correct for that
                # AID). Only null when there's nothing useful there.
                if not target_match.poster_url:
                    target_match.poster_url = None
        else:
            # Truly new manual candidate — safe to append. `poster_for_row`
            # is the user's picked URL when the file lands on the picked
            # AID, or None when routing flipped it to a sibling cour AID
            # (in which case auto-heal will fetch the routed AID's own
            # poster on the next sweep via the enrichment-only fast path).
            # `title_for_row` is the routed AID's canonical AniDB title
            # for routed-away files, or payload.title for files that
            # landed on the user-picked AID.
            f.matches.append(Match(
                media_file_id=f.id,
                provider=payload.provider,
                provider_id=final_provider_id,
                match_type=match_type,
                confidence=1.0,
                title=title_for_row,
                year=payload.year,
                series_name=title_for_row if match_type == "tv_episode" else None,
                season_number=existing_season,
                episode_number=existing_ep,
                poster_url=poster_for_row,
                overview=payload.overview,
                is_selected=True,
                is_manual=True,
            ))
        if f.status == "no_match":
            f.status = "matched"
        # Move each file into the right group (TV → Anime when pinned to an
        # AniDB show) so a bulk manual fix doesn't strand files under TV Series.
        _apply_media_type_for_manual_pick(f, payload.provider, payload.media_type)
        updated += 1
    await session.commit()
    return {"updated": updated, "skipped": len(payload.file_ids) - updated}


@router.post("/rematch-all", response_model=dict[str, int])
async def rematch_all(
    background: BackgroundTasks,
    media_type: str | None = None,
    limit: int = Query(50, ge=1, le=100_000),
    force: bool = False,
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Kick off a bulk rematch in the background and return immediately.

    `force=true` rematches even files whose user manually pinned a match.
    Default is False (preserve manual pins).

    Previously this blocked on N rate-limited provider calls inside one HTTP
    request — for AniDB that's up to 4s per fresh AID, so a 50-file batch
    can run 3-4 minutes. Holding the request session that long fights with
    other concurrent traffic and ends up 500-ing on SQLite write contention.

    The background task gets its own SessionLocal, processes one file at a
    time, commits per file, and frontends see updated matches via their
    normal /files polling.
    """
    stmt = select(MediaFile.id).where(MediaFile.parsed_data.is_not(None))
    if media_type is not None:
        stmt = stmt.where(MediaFile.media_type == media_type)
    stmt = stmt.limit(limit)
    fids = list(await session.scalars(stmt))
    background.add_task(_bulk_rematch_worker, fids, force)
    return {"files_queued": len(fids), "files_processed": 0, "files_with_matches": 0}


# Bumped whenever auto-heal needs to revisit rows it already processed
# (e.g. because the matcher logic changed in a way that retroactively
# invalidates old decisions). Stored in the `settings` table so we know
# whether THIS boot needs to do extra work.
_HEAL_VERSION = 25  # v25 = media_type-from-provider heal: _heal_media_type_from_provider sets media_type=anime for files whose selected match is AniDB (anime-only) but were typed tv/movie by the parser (the "Rent-a-Girlfriend in TV Series" bug — a copy scanned from a release-named folder outside /anime/), recomputing series/variant keys so they re-cluster under anime. v24 = episode-drift self-heal: _heal_episode_number_drift re-matches selected non-manual tv_episode rows whose stored episode_number matches neither the parsed season-local nor absolute episode (the One Piece stale-match class — files parsed as 1156-1160 but Match rows stuck on 1-5). Detection is comparison-only and over-approximate; the real fix is delegated to the ban-aware BATCH loop so cour-routed/absolute remaps re-confirm idempotently while genuine drift heals. v23 = ruthless cascade vetoes (Autopsy 10+11): FribbAuthority explicit-season contradiction now vetoes (-1.0) instead of abstaining, and EpisodeCountSanity drops the ≤3-ep floor and vetoes any candidate that fails both the own-count margin AND the Fribb-sibling aggregate. Forces every existing AniDB anime match to be re-evaluated so wrong-season picks (My Hero S06 stuck on S01 AID, 12-ep spin-offs stealing 60-file clusters) get killed on the next heal pass.


async def _heal_anime_fribb_misroutes(session: AsyncSession) -> int:
    """One-shot: find anime files where the existing match's AID contradicts
    Fribb's authoritative mapping AND fix them in-place via direct DB update.

    Why direct UPDATE instead of full rematch:
      - The full rematch path calls `provider.get_episodes(...)` and
        `compute_series_group_id(...)`, both of which can hit AniDB's
        rate-limited HTTP API. While AniDB is IP-banned, those calls fail
        and the auto-heal loop aborts before reaching this work.
      - The AID correction itself doesn't need ANY provider HTTP — Fribb
        gives us `(TVDB series, season) → AID` straight from the
        in-memory mapping. We can flip the AID, season_number, and
        series_group_id with a pure SQL update.
      - Episode titles and metadata_blob get cleared by this same pass,
        so the regular auto-heal trigger picks them up later (once the
        ban clears) and fills the gaps via the normal rematch flow.

    Catches cases like:
      - File parsed as 'Bleach S17E27' (parsed.season=17)
      - Existing match: AniDB AID 2369 (umbrella for original Bleach;
        Fribb season=None) but Fribb has AID 15449 mapped to
        (tvdb=74796, season=17) → flip the AID to 15449.

    Returns the count of fixed rows.
    """
    import json as _json
    from kira.providers.anime_mappings import AnimeMappings

    stmt = (
        select(MediaFile.id, MediaFile.parsed_data, Match.id.label("match_id"), Match.provider_id)
        .join(Match, Match.media_file_id == MediaFile.id)
        .where(
            Match.is_selected.is_(True),
            Match.provider == "anidb",
            Match.match_type == "tv_episode",
        )
    )
    rows = (await session.execute(stmt)).all()
    fixed = 0
    for fid, parsed_raw, match_id, current_aid_str in rows:
        parsed = _json.loads(parsed_raw) if isinstance(parsed_raw, str) else (parsed_raw or {})
        parsed_season = parsed.get("season")
        if parsed_season is None:
            continue
        try:
            current_aid = int(current_aid_str)
        except (TypeError, ValueError):
            continue
        # Look up where the current AID actually belongs in TVDB space.
        current_mapped_season = await AnimeMappings.tvdb_season(current_aid)
        # Only act when the current AID's mapping disagrees with parsed
        # (or has no season — umbrella entry mismatch for a sequel file).
        is_misroute = False
        if current_mapped_season is not None and current_mapped_season != parsed_season:
            is_misroute = True
        elif current_mapped_season is None and parsed_season > 1:
            is_misroute = True
        if not is_misroute:
            continue

        # Find which AID Fribb says is the right one for this (TVDB series, season).
        current_entry = await AnimeMappings.get(current_aid)
        tvdb_id = current_entry.get("tvdb_id") if current_entry else None
        if not tvdb_id:
            continue
        correct_aid = await AnimeMappings.aid_by_tvdb_season(tvdb_id, parsed_season)
        if correct_aid is None or correct_aid == current_aid:
            continue

        # Compute the CANONICAL series_group_id for the new AID. Reading
        # from the AniDB relations cache so this stays HTTP-free even
        # while AniDB is banned. The canonical group is the lowest AID
        # in the franchise chain — matches what `compute_series_group_id`
        # writes for fresh matches, so the row blends in cleanly with
        # everything else once it's flipped.
        canonical_gid = f"anidb:{correct_aid}"  # fallback if chain unknown
        try:
            from kira.providers.anidb import AniDBProvider
            chain = AniDBProvider._load_relations_cache().get(str(correct_aid))
            if chain:
                canonical_gid = f"anidb:{min(chain)}"
        except Exception:
            pass

        # Pure SQL update — no provider HTTP. Reset title-related fields
        # so the regular auto-heal loop refills them with the correct
        # display data (from the new AID) once AniDB unblocks.
        from sqlalchemy import update as sql_update
        await session.execute(
            sql_update(Match)
            .where(Match.id == match_id)
            .values(
                provider_id=str(correct_aid),
                season_number=parsed_season,
                series_group_id=canonical_gid,
                title=None,
                series_name=None,
                episode_title=None,
                metadata_blob=None,
                # Confidence stays — the Fribb mapping is more authoritative
                # than any trigram score, so the existing high number is fine.
            )
        )
        fixed += 1
    return fixed


async def _refill_anidb_titles_from_cache(session: AsyncSession) -> int:
    """For every selected AniDB match with NULL title, look up the AID's
    display title in AniDBProvider's in-memory title cache and set it.

    Pure in-memory — no HTTP. Safe to run during AniDB ban. Used to
    prevent the "title falls back to romaji from filename" UX glitch
    when the cleanup migration nulls titles for refill, but auto-heal
    can't run a full rematch because AniDB is banned.

    Returns the number of rows updated.
    """
    from sqlalchemy import update as sql_update
    from kira.providers.anidb import AniDBProvider
    # Make sure the title cache is loaded (it normally already is, but
    # belt-and-braces — _ensure_index is idempotent and disk-only when
    # the cached dump is fresh).
    if AniDBProvider._titles is None:
        try:
            import httpx
            from kira.providers.base import ProviderAuth
            async with httpx.AsyncClient() as c:
                p = AniDBProvider(
                    base_url="http://api.anidb.net:9001/httpapi",
                    auth=ProviderAuth(credentials={"client": "kira", "clientver": "1"}),
                    client=c,
                )
                await p._ensure_index()
        except Exception:
            return 0
    if AniDBProvider._titles is None:
        return 0

    stmt = (select(Match.id, Match.provider_id)
            .where(Match.is_selected.is_(True),
                   Match.provider == "anidb",
                   Match.title.is_(None)))
    rows = (await session.execute(stmt)).all()
    # Build a temporary AniDBProvider instance just to access _pick_display_title.
    # The method is a closure over the class-level _titles dict.
    import httpx
    from kira.providers.base import ProviderAuth
    async with httpx.AsyncClient() as c:
        p = AniDBProvider(
            base_url="http://api.anidb.net:9001/httpapi",
            auth=ProviderAuth(credentials={"client": "kira", "clientver": "1"}),
            client=c,
        )
        updated = 0
        for match_id, provider_id in rows:
            try:
                aid_i = int(provider_id)
            except (TypeError, ValueError):
                continue
            title = p._pick_display_title(aid_i)
            if not title:
                continue
            await session.execute(
                sql_update(Match)
                .where(Match.id == match_id)
                .values(title=title, series_name=title)
            )
            updated += 1
    return updated


async def _cleanup_post_migration_cruft(session: AsyncSession) -> dict[str, int]:
    """One-shot cleanup of two specific kinds of leftover damage from the
    v2 → v3 migration sequence:

    1. **Wrong titles on flipped rows.** The v2 migration UPDATE'd
       provider_id (e.g. 2369 → 15449) but didn't clear the `title`
       field — so rows now read `title='Bleach'` while pointing at
       AID 15449 (which is 'Bleach: Thousand-Year Blood War'). v3
       added title=None to the UPDATE, but v3 only re-runs on rows
       that are STILL misrouted; rows v2 already fixed are skipped.
       Force-NULL title/series_name/metadata_blob on every selected
       AniDB match so auto-heal picks them up and refills correctly.

    2. **Duplicate match rows.** Some files have two match rows that
       both point to the same (provider, provider_id) — one from the
       original scan, one from the v2 migration's flip. They cluster
       fine on the frontend (same provider_id), but the unselected
       row is dead weight and can confuse downstream tools. Delete
       the unselected duplicates.
    """
    from sqlalchemy import delete as sql_delete, update as sql_update

    # 1. NULL stale titles on selected AniDB anime matches whose title
    #    looks like it might be wrong. Conservative heuristic: when the
    #    title doesn't include either of the canonical English/Japanese
    #    forms for the AID. To avoid an N×M comparison against the
    #    AniDB title cache, we use a simpler signal: ANY selected AniDB
    #    match whose row was touched by the migration sequence — i.e.
    #    has metadata_blob NOT NULL only when title was set in the same
    #    write. Simpler still: just null ALL selected AniDB anime titles
    #    so they all heal uniformly. Conservative — auto-heal will
    #    repopulate everything next time it runs.
    res = await session.execute(
        sql_update(Match)
        .where(
            Match.is_selected.is_(True),
            Match.provider == "anidb",
            Match.match_type == "tv_episode",
        )
        .values(
            title=None,
            series_name=None,
            metadata_blob=None,
            episode_title=None,
        )
    )
    nulled = res.rowcount or 0

    # 2. Find duplicate (mf_id, provider, provider_id) groups and delete
    #    the non-selected dupes. Keep is_selected=True always.
    dup_rows = (await session.execute(select(
        Match.media_file_id, Match.provider, Match.provider_id,
    ))).all()
    from collections import defaultdict
    by_key: dict[tuple, list[int]] = defaultdict(list)
    # Walk in two passes: first gather all match.id per (mf, prov, pid).
    full_rows = (await session.execute(select(
        Match.id, Match.media_file_id, Match.provider, Match.provider_id, Match.is_selected,
    ))).all()
    for mid, mfid, prov, pid, sel in full_rows:
        by_key[(mfid, prov, pid)].append((mid, bool(sel)))
    del dup_rows
    dup_ids_to_delete: list[int] = []
    for _key, mids in by_key.items():
        if len(mids) <= 1:
            continue
        # Keep the selected one; drop the rest. If multiple selected
        # (shouldn't happen but defensive), keep the first.
        seen_selected = False
        for mid, sel in mids:
            if sel and not seen_selected:
                seen_selected = True
                continue
            dup_ids_to_delete.append(mid)
    if dup_ids_to_delete:
        # Detach rename_history back-refs before delete (FK-safe on old DBs).
        await detach_and_delete_matches(session, match_ids=dup_ids_to_delete)

    return {"titles_nulled": nulled, "dupes_deleted": len(dup_ids_to_delete)}


async def _trigger_tvdb_tmdb_season_rematch(session: AsyncSession) -> int:
    """v11 cleanup: NULL `metadata_blob` for every selected TVDB/TMDB TV
    match so the regular heal loop queues a rematch — which will now
    fetch and store the per-season poster_url via the new matcher hook.

    Without this, existing rows scanned before the per-season poster
    code landed keep their series-level poster forever. The frontend
    splits them into per-season cards via the new grouping key, but
    all the cards visually show the same series poster — making the
    franchise group look like clones of one card.

    Pure UPDATE, no provider HTTP. Per-row rematch costs one extra
    HTTP call (the get_season_poster) on top of the existing rematch
    cost; TVDB/TMDB rate limits handle it comfortably.
    """
    from sqlalchemy import update as sql_update
    res = await session.execute(
        sql_update(Match)
        .where(
            Match.is_selected.is_(True),
            Match.match_type == "tv_episode",
            Match.provider.in_(("tvdb", "tmdb")),
        )
        .values(metadata_blob=None)
    )
    return res.rowcount or 0


async def _reparse_missing_episodes(session: AsyncSession) -> int:
    """v9: Re-run the parser on TV/anime files whose existing parsed_data
    has NO episode number, and update parsed_data + null Match.episode_title
    so the regular heal loop picks them up next.

    Original trigger case: files like `[aL].Sousou.no.Frieren.2023-01.WEB...`
    which the original parser couldn't crack (the year + episode-in-dash
    convention wasn't recognised). They live in their series cluster but
    with `episode=None`, rendering as "orphaned" in the popup.

    v13 extension: also catches files where the OLD parser truncated a
    long episode number to its first 3 digits. Concrete: P1 used to be
    `S(\\d{1,2})E(\\d{1,3})` and silently chopped `S23E1160` to `S23E116`,
    with the trailing `0` dangling. The file then matched to a non-
    existent E116 in S23 and ended up orphaned. The new P1 accepts
    `\\d{1,4}` so re-parsing produces the right number.

    Re-parse condition (either-or):
      - existing.episode is None AND existing.absolute_episode is None
        (the original case — parser found nothing)
      - existing.episode != new.episode (truncation case — parser found
        something different now)
    Both are cheap; re-parse runs in pure regex with no provider HTTP.

    The actual provider re-match happens via the regular heal loop after
    parsed_data + match's episode_title are updated.
    """
    from pathlib import Path as _Path
    from sqlalchemy import update as sql_update
    from kira.parser import parse_filename
    from kira.api.scans import _compute_series_key, _compute_variant_key

    stmt = (
        select(MediaFile)
        .where(MediaFile.media_type.in_(("tv", "anime")))
        .where(MediaFile.parsed_data.is_not(None))
    )
    files = list(await session.scalars(stmt))
    reparsed = 0
    for mf in files:
        existing = mf.parsed_data or {}
        old_ep = existing.get("episode")
        old_abs = existing.get("absolute_episode")
        missing_both = old_ep is None and old_abs is None
        # v13: also re-parse when the parser MIGHT have truncated. Skip the
        # expensive parser call when old data already has a small episode
        # number that can't have been a truncation victim (E1-E99 fit fine
        # in the old 3-digit regex).
        suspect_truncation = (old_ep is not None) and (old_ep >= 100)
        if not (missing_both or suspect_truncation):
            continue
        if not mf.file_path:
            continue
        parent = str(_Path(mf.file_path).parent)
        fresh = parse_filename(_Path(mf.file_path).name, parent_path=parent)
        new_data = fresh.to_dict()
        new_ep = new_data.get("episode")
        new_abs = new_data.get("absolute_episode")
        # Decide whether the re-parse meaningfully differs.
        if missing_both:
            # Only act when the new parser actually extracted an episode that
            # the old parse missed (avoid pointless rewrites).
            if new_ep is None and new_abs is None:
                continue
        else:
            # Truncation-suspect path — only update if the episode number
            # actually changed (e.g. 116 → 1160). Same number = nothing to
            # do; the row stays as-is. This guards against thrashing rows
            # where the new parser agrees with the old.
            if new_ep == old_ep:
                continue
        mf.parsed_data = new_data
        mf.media_type = fresh.media_type
        mf.series_key = _compute_series_key(fresh)
        mf.variant_key = _compute_variant_key(fresh)
        # Null the selected match's episode_title + metadata_blob so the
        # main heal loop queues this file for a full rematch (which will
        # pick up the new episode number and write the right Match row).
        from sqlalchemy import update as sql_update2
        await session.execute(
            sql_update2(Match)
            .where(Match.media_file_id == mf.id, Match.is_selected.is_(True))
            .values(episode_title=None, episode_number=None, metadata_blob=None)
        )
        reparsed += 1
    return reparsed


async def _heal_title_mismatch_matches(session: AsyncSession) -> int:
    """v14: Direct-SQL nuke of Match rows where the matched title doesn't
    actually look like the file's parsed title.

    Background — the One Pace bug:
    The Fribb-season rerank can promote a candidate to confidence=1.0
    when it's the only AID whose Fribb cross-ref maps to the user's
    season number. That worked fine for "Bleach S17 → AID 15449" where
    the title genuinely matches. But it falsely promoted "ONE: Kagayaku
    Kisetsu e" to 1.0 for "One Pace s01e01" files — because the real
    answer (One Piece) got filtered out of the candidate list by M7's
    short-title penalty, leaving ONE: Kagayaku as the only Fribb-S1
    candidate with no contradictor.

    The matcher gate `PROMOTION_MIN_CONF` (in engine.py) now blocks this
    at write time. But existing Match rows from before that gate landed
    are still in the DB at 1.0 confidence pointing at the wrong show.
    The regular heal loop's rematch path won't fix them if AniDB is
    banned (it skips anidb-matched files) — leaving the user staring
    at a confidently-wrong match indefinitely.

    Detection — pure in-memory, no HTTP:
      - Compute trigram_similarity(parsed.title, match.title)
      - If similarity < 0.50 AND match.confidence >= 0.95, the match was
        promoted by Fribb (or some other rerank) past what its actual
        title-similarity warrants. That's the failure pattern.

    Action: delete the Match row, flip file status to no_match. The
    user then sees the cluster in "Needs matching" with a proper
    Search-manually CTA instead of a fake-confident 100% match.
    """
    from sqlalchemy import delete as sql_delete, update as sql_update
    from kira.matcher.similarity import trigram_similarity
    from kira.parser import ParsedFile

    # Pull every selected anime match. We could narrow to "confidence >= 0.95"
    # in SQL to skip obviously-correct matches, but the title-sim check
    # below is the real gate and runs in microseconds.
    stmt = (
        select(MediaFile, Match)
        .join(Match, Match.media_file_id == MediaFile.id)
        .where(
            MediaFile.media_type == "anime",
            Match.is_selected.is_(True),
            Match.is_manual.is_(False),  # never touch user-pinned rows
            Match.match_type == "tv_episode",
            Match.confidence >= 0.95,
        )
    )
    nuked = 0
    for mf, m in (await session.execute(stmt)).all():
        if not mf.parsed_data or not m.title:
            continue
        try:
            parsed_title = (ParsedFile(**mf.parsed_data).title or "").strip()
        except Exception:
            continue
        if not parsed_title:
            continue
        sim = trigram_similarity(parsed_title, m.title)
        if sim >= 0.50:
            continue  # title genuinely looks like a match — leave it
        # Title-similarity is poor but the match was promoted to 100%.
        # That's the One Pace failure pattern. Delete every match row
        # for this file (not just the selected one — all candidates
        # from the same broken scoring round are equally suspect) and
        # mark the file no_match.
        await detach_and_delete_matches(session, media_file_id=mf.id)
        await session.execute(
            sql_update(MediaFile)
            .where(MediaFile.id == mf.id)
            .values(status="no_match")
        )
        nuked += 1
    return nuked


async def _heal_episode_number_drift(session: AsyncSession) -> int:
    """v24: Re-match TV/anime files whose stored ``Match.episode_number`` no
    longer agrees with the file's parsed episode — the "stale match" class
    the user hit on One Piece (the files parse as episodes 1156–1160, but
    the selected Match rows still said episodes 1–5 from an earlier scan,
    so the popup showed the wrong episodes).

    Why the existing heals miss it:
      - The regular BATCH loop triggers on a MISSING ``episode_title`` /
        ``metadata_blob``. These rows had both — they just pointed at the
        wrong episode.
      - ``_reparse_missing_episodes`` only fires when the parser now
        extracts a DIFFERENT number than before. Here the parser was
        already right; it's the stored Match that drifted.
      - ``_trigger_anime_rematch`` only re-touches rows once per version
        bump, and only for ``media_type == 'anime'``.

    Detection — pure in-memory, no HTTP — a row is suspect when its stored
    ``episode_number`` matches NEITHER the parsed season-local episode NOR
    the parsed absolute episode::

        episode_number not in {parsed.episode, parsed.absolute_episode}

    Comparing against BOTH is what keeps correctly-numbered rows out of the
    suspect set: AniDB-pipeline anime is usually absolute-numbered on disk
    AND in the Match (``1156 == 1156`` → not flagged), and standard TV is
    season-local in both (``S01E05`` → ``5 == 5`` → not flagged).

    Crucially we do NOT decide the correct episode here. Number comparison
    alone cannot distinguish genuine drift (``1156`` wrongly stored as ``1``)
    from a legitimate remap (a cour-routed file whose season-local episode
    differs from its absolute on-disk number) — both look like "mismatch".
    So we only flag "worth re-checking" and NULL the enrichment, letting the
    regular, ban-aware heal loop re-run each row through the REAL matcher
    (cour routing + absolute→local mapping and all). That makes a false
    positive harmless: a correctly cour-routed file is simply re-matched to
    the same answer (idempotent, no data change), while a genuinely drifted
    row heals to the right episode.

    Two deliberate safety choices:
      - ``episode_number`` is LEFT INTACT (only ``episode_title`` +
        ``metadata_blob`` are nulled to arm the BATCH-loop trigger). If the
        re-match is deferred during an AniDB ban, a correctly cour-routed
        row keeps displaying its right episode instead of going blank.
      - This runs INSIDE the version-gated CAS block, so it's a one-shot.
        The small set of legitimately-remapped rows that get re-matched
        once won't be re-flagged (and re-matched) on every subsequent boot.

    Manual pins are never touched.
    """
    from sqlalchemy import update as sql_update

    stmt = (
        select(MediaFile.id, MediaFile.parsed_data, Match.episode_number)
        .join(Match, Match.media_file_id == MediaFile.id)
        .where(
            MediaFile.media_type.in_(("tv", "anime")),
            MediaFile.parsed_data.is_not(None),
            Match.is_selected.is_(True),
            Match.is_manual.is_(False),  # never touch user-pinned rows
            Match.match_type == "tv_episode",
        )
    )
    drifted_ids: list[int] = []
    for fid, parsed, ep_no in (await session.execute(stmt)).all():
        parsed = parsed or {}
        p_ep = parsed.get("episode")
        p_abs = parsed.get("absolute_episode")
        # Need a parsed episode to compare against. The no-episode case is
        # _reparse_missing_episodes' job, not ours.
        candidates = {v for v in (p_ep, p_abs) if v is not None}
        if not candidates:
            continue
        # A tv_episode Match with no episode number at all is itself broken;
        # so is one whose number matches neither parsed value.
        if ep_no is not None and ep_no in candidates:
            continue  # stored episode agrees with the file — leave it alone
        drifted_ids.append(fid)

    if not drifted_ids:
        return 0
    # Arm the BATCH-loop trigger (episode_title IS NULL) so each row is
    # re-matched through the real engine on this same boot — ban-aware,
    # throttled. Chunk the IN(...) to stay under SQLite's variable cap.
    for i in range(0, len(drifted_ids), 400):
        chunk = drifted_ids[i:i + 400]
        await session.execute(
            sql_update(Match)
            .where(Match.media_file_id.in_(chunk), Match.is_selected.is_(True))
            .values(episode_title=None, metadata_blob=None)
        )
    return len(drifted_ids)


async def _heal_media_type_from_provider(session: AsyncSession) -> int:
    """v25: fix MediaFile.media_type for files whose SELECTED match is from
    AniDB (an anime-only source) but were typed 'tv'/'movie' by the parser.

    Background — the "Rent-a-Girlfriend in TV Series" bug: media_type is decided
    once at scan time from the path/filename (only `/anime/` paths or fansub
    groups → "anime"), and a successful AniDB match never corrected it. A copy
    scanned from a release-named download folder (outside `/anime/`) came out
    "tv", so it grouped under "TV Series" and split from its anime siblings.

    Since AniDB only catalogues anime, an AniDB match is authoritative: set
    media_type to "anime" and recompute the series/variant keys off the
    corrected type so the row re-clusters under its anime identity. Pure
    in-memory — no provider HTTP."""
    from kira.matcher.media_type import apply_media_type_and_recompute_keys

    stmt = (
        select(MediaFile)
        .join(Match, Match.media_file_id == MediaFile.id)
        .where(
            Match.is_selected.is_(True),
            Match.provider == "anidb",
            MediaFile.media_type != "anime",
            MediaFile.parsed_data.is_not(None),
        )
    )
    # CR-05: stream the matched set instead of materializing every row (each
    # carries the parsed_data JSON blob) at once. `stream_scalars` + `yield_per`
    # bounds how many ORM instances live in memory per fetch while keeping them
    # ATTACHED to the session — dirty-tracking still flushes the in-place
    # mutations on the caller's commit, exactly as the prior `.all()` loop did.
    fixed = 0
    result = await session.stream_scalars(stmt.execution_options(yield_per=200))
    async for mf in result:
        try:
            # Helper sets media_type='anime' first, then rebuilds the
            # ParsedFile (field-filtered) and recomputes series/variant keys.
            apply_media_type_and_recompute_keys(mf, "anime")
        except Exception:
            # Key recompute is best-effort; ensure the grouping fix (the
            # media_type flip) still lands even if the recompute raised.
            mf.media_type = "anime"
        fixed += 1
    return fixed


async def _heal_movie_year_mismatch(session: AsyncSession) -> int:
    """v25: re-match selected movie rows whose stored year disagrees with the
    file's PARSED year — the "Nobody 2 (2025) stuck on Nobody (2021)" class.

    Such rows are usually STALE: matched before the parser extracted the year
    (so the matcher had no temporal anchor and fell back to the more prominent
    original). NULLing ``metadata_blob`` arms the BATCH-loop movie rematch,
    which re-runs the real matcher WITH the parsed year so the year metric can
    pick the right film.

    Safe by the same logic as the episode-drift heal: we don't choose the film
    here, the matcher does. For a row the matcher confidently picked at a
    different year (it overrode a wrong filename year), re-matching is
    idempotent — same parsed year + same provider data → same pick. Only genuinely
    stale rows actually change. Manual pins are never touched."""
    from sqlalchemy import update as sql_update

    stmt = (
        select(MediaFile.id, MediaFile.parsed_data, Match.year)
        .join(Match, Match.media_file_id == MediaFile.id)
        .where(
            MediaFile.media_type == "movie",
            MediaFile.parsed_data.is_not(None),
            Match.is_selected.is_(True),
            Match.is_manual.is_(False),
            Match.match_type == "movie",
        )
    )
    drifted: list[int] = []
    for fid, parsed, m_year in (await session.execute(stmt)).all():
        parsed = parsed or {}
        p_year = parsed.get("year")
        if p_year is None or m_year is None:
            continue  # no year on one side → nothing to compare
        try:
            if int(p_year) != int(m_year):
                drifted.append(fid)
        except (TypeError, ValueError):
            continue
    if not drifted:
        return 0
    for i in range(0, len(drifted), 400):
        chunk = drifted[i:i + 400]
        await session.execute(
            sql_update(Match)
            .where(Match.media_file_id.in_(chunk), Match.is_selected.is_(True))
            .values(metadata_blob=None)
        )
    return len(drifted)


async def _trigger_anime_rematch(session: AsyncSession) -> int:
    """v7 cleanup: nullify episode_title + metadata_blob for every anime
    file's selected match so the heal loop picks them up and re-runs
    through the new matcher (Fribb franchise guard, no-match floor,
    cross-provider anime filter).

    Also resets file status to `matched` if it was `no_match` — the new
    matcher might find a hit for files the old matcher rejected.

    Why this is needed: the matcher's reranking changes don't propagate
    to already-written Match rows. Without forcing a rematch, the wrong
    AIDs ("Queen Millennia" for BLEACH TYBW, "One Page Love" for One
    Pace) stay in the DB forever. Heal-driven rematch is more polite
    than asking the user to delete+rescan.

    Cheap: pure UPDATE; the heal loop does the actual rematch work.
    """
    from sqlalchemy import update as sql_update
    # NULL episode_title + metadata_blob for all selected anime matches.
    # The heal loop's trigger condition catches "episode_title IS NULL OR
    # metadata_blob IS NULL" for tv_episode rows. Scope to anime via a
    # subquery on MediaFile so we don't disturb working TV/movie matches.
    anime_file_ids = select(MediaFile.id).where(MediaFile.media_type == "anime")
    res = await session.execute(
        sql_update(Match)
        .where(
            Match.is_selected.is_(True),
            Match.match_type == "tv_episode",
            Match.media_file_id.in_(anime_file_ids),
        )
        .values(episode_title=None, metadata_blob=None)
    )
    # Also reset no_match files so the new matcher gets a fresh shot.
    from sqlalchemy import update as sql_update2
    await session.execute(
        sql_update2(MediaFile)
        .where(
            MediaFile.media_type == "anime",
            MediaFile.status == "no_match",
        )
        .values(status="matched")
    )
    return res.rowcount or 0


async def _recompute_series_keys(session: AsyncSession) -> int:
    """EE-5 / v12: recompute series_key for every existing MediaFile row
    using the new (parsed, file_path) signature with year/parent-folder
    disambiguation.

    Without this migration, libraries that already had files indexed under
    the old key format (`{type}|{title}|{season}`) keep their stale keys
    forever — meaning The Office UK and The Office US continue to cluster
    as one card even after the fix lands. A re-scan would fix it for new
    files but not existing rows.

    Cheap: pure Python recompute + UPDATE. No provider HTTP. No risk to
    Match rows or rename history. Idempotent: re-running it produces the
    same keys (only re-writes rows whose key actually changed).

    Returns the count of MediaFile rows whose series_key was updated.
    """
    from kira.api.scans import _compute_series_key
    from kira.models import MediaFile
    from sqlalchemy import update as sql_update
    from kira.parser import ParsedFile

    stmt = select(MediaFile.id, MediaFile.file_path, MediaFile.parsed_data, MediaFile.series_key).where(
        MediaFile.media_type.in_(("tv", "anime", "music"))
    )
    rows = (await session.execute(stmt)).all()
    fixed = 0
    for mf_id, file_path, parsed_raw, old_key in rows:
        if not parsed_raw:
            continue
        try:
            parsed = ParsedFile(**parsed_raw)
        except Exception:
            continue  # corrupted parsed_data — leave the row alone
        try:
            new_key = _compute_series_key(parsed, file_path=file_path)
        except Exception:
            continue
        if new_key != old_key:
            await session.execute(
                sql_update(MediaFile)
                .where(MediaFile.id == mf_id)
                .values(series_key=new_key)
            )
            fixed += 1
    return fixed


async def _null_stale_metadata_blobs(session: AsyncSession) -> int:
    """v6 cleanup: NULL metadata_blob for any selected match where the row's
    `overview` column is also NULL.

    Rationale: pre-v6 provider details (`get_series_extended`, `get_tv_details`,
    `get_movie_details`) didn't return `overview`. Rows that already had
    metadata_blob NON-null were never refilled because the heal trigger is
    "metadata_blob IS NULL". By nulling the stale blobs here, the heal loop's
    next pass will re-fetch via the now-fixed provider methods (which include
    overview) and also promote it onto Match.overview via the new
    `top_overview_fallback` write.

    Cheap: one UPDATE; no provider HTTP. The heal pass that follows is rate-
    limited by AniDB but TVDB/TMDB rows refill quickly.
    """
    from sqlalchemy import update as sql_update
    res = await session.execute(
        sql_update(Match)
        .where(
            Match.is_selected.is_(True),
            Match.metadata_blob.is_not(None),
            Match.overview.is_(None),
        )
        .values(metadata_blob=None)
    )
    return res.rowcount or 0


async def _auto_heal_stale_matches() -> None:
    """Background-rematch any TV/anime file whose top match is missing
    `episode_title` — the canonical "matched before the title-fetch fix
    landed" signal. Runs once on startup; cheap when nothing's stale.

    Throttled to a small batch per pass so we don't flood AniDB (rate-
    limited 1 req / 4s on first-touch AIDs). The pass repeats until the
    pool is empty.

    Also catches the dual case where the file matched the WRONG series
    because the parser failed to extract an episode number — _rematch_one
    now re-parses the filename, so a parser-pattern fix (e.g. four-digit
    EP support for One Piece / Detective Conan / Pokémon) heals every
    stale row on the next boot without a manual re-scan.

    Without this, every iteration of matcher improvements would force
    users to manually click Re-match on every card to benefit. With it,
    the library quietly heals itself after the next backend restart.
    """
    BATCH = 10  # Smaller batches — every AniDB-touching rematch is ≥4s.
    # Brief sleep so the app finishes booting before we start hammering
    # the DB + providers; lets the user reach a usable Review page first.
    import asyncio
    from kira.providers.anidb import AniDBProvider
    from kira import activity
    await asyncio.sleep(5)
    activity.begin("heal", "Healing library matches")

    # Cursor pagination — see the loop body below for full rationale.
    # `last_processed_id` advances monotonically through MediaFile.id so
    # we never re-query the same row twice in a single boot, AND we
    # never blow up SQLite's variable cap with a 30k-entry `NOT IN(...)`
    # clause (the prior implementation collapsed at ~32k healed files
    # because the `seen_fids` set was inlined into the WHERE clause).
    last_processed_id: int = 0
    async with SessionLocal() as session, httpx.AsyncClient() as client:
        engine = MatchEngine(await registry_from_settings(client))

        # One-shot Fribb-misroute pass. Runs once per _HEAL_VERSION bump.
        # Flips wrong AIDs in-place via direct SQL — no provider HTTP needed,
        # so it works even while AniDB is banned. Cleared episode_title /
        # metadata_blob get refilled by the regular rematch loop below when
        # the ban clears (or on the next boot when ban has expired).
        #
        # R2-H2: Atomic compare-and-swap on the version row. The previous
        # SELECT-then-UPDATE allowed two workers booting simultaneously
        # (uvicorn --reload, container orchestrator) to BOTH see
        # applied_version < _HEAL_VERSION and BOTH run the cleanup pass,
        # double-spending AniDB rate budget and double-nulling Match
        # metadata_blobs. We now use a single UPDATE that only modifies
        # the row if the value differs — and only the worker whose
        # UPDATE returns rowcount > 0 proceeds with the heal pass.
        from kira.models import Setting
        from sqlalchemy import update as sql_update
        # Make sure the row exists so the UPDATE can target it. INSERT
        # OR IGNORE — first writer wins, ties go to NULL value which
        # CAS catches below as "needs upgrade".
        version_row = await session.get(Setting, "system.heal_version")
        if version_row is None:
            session.add(Setting(key="system.heal_version", value=0))
            await session.commit()
        # CAS: only one worker's UPDATE will return rowcount=1.
        res = await session.execute(
            sql_update(Setting)
            .where(Setting.key == "system.heal_version")
            .where(Setting.value != _HEAL_VERSION)
            .values(value=_HEAL_VERSION)
        )
        await session.commit()
        won_lock = (res.rowcount or 0) > 0
        applied_version = _HEAL_VERSION if won_lock else _HEAL_VERSION  # for downstream printing
        if won_lock:
            try:
                # v14: nuke title-mismatch anime matches FIRST so the
                # downstream passes don't waste cycles refilling
                # metadata on doomed rows. Pure in-memory check (no
                # provider HTTP) so it runs even during AniDB bans.
                # Catches the One Pace → ONE: Kagayaku Kisetsu e
                # failure pattern: confidence 1.0 stored, but
                # parsed.title trigrams <50% against the matched
                # title.
                mismatched = await _heal_title_mismatch_matches(session)
                if mismatched:
                    print(f"auto_heal: nuked {mismatched} title-mismatched anime matches (now no_match).")
                fixed = await _heal_anime_fribb_misroutes(session)
                if fixed:
                    print(f"auto_heal: Fribb-corrected {fixed} misrouted anime AIDs in-place.")
                # v4 cleanup: NULL stale titles + drop duplicate match rows
                # left behind by earlier in-place flips.
                cleanup = await _cleanup_post_migration_cruft(session)
                if cleanup["titles_nulled"] or cleanup["dupes_deleted"]:
                    print(
                        f"auto_heal: cleaned {cleanup['titles_nulled']} stale anime titles "
                        f"and removed {cleanup['dupes_deleted']} duplicate match rows."
                    )
                # Immediately refill AniDB titles from the in-memory title
                # cache — no HTTP. Without this, freshly-nulled titles
                # would fall back to the parser's romaji-from-filename
                # ("Kanojo, Okarishimasu") in the UI until the AniDB ban
                # clears and a full rematch can run. This restores the
                # canonical English-preferred display title instantly.
                refilled = await _refill_anidb_titles_from_cache(session)
                if refilled:
                    print(f"auto_heal: refilled {refilled} AniDB titles from in-memory cache.")
                # v6: NULL metadata blobs that pre-date the overview-in-details
                # change. Heal loop refills them with the now-fixed payload.
                nulled_blobs = await _null_stale_metadata_blobs(session)
                if nulled_blobs:
                    print(f"auto_heal: nulled {nulled_blobs} stale metadata blobs for overview refill.")
                # v7: trigger rematch for all anime so the Fribb franchise
                # guard + no-match floor + cross-provider anime filter
                # apply to existing rows. Without this, files matched
                # before v7 stay wrong forever.
                rematch_n = await _trigger_anime_rematch(session)
                if rematch_n:
                    print(f"auto_heal: queued {rematch_n} anime matches for rematch with new logic.")
                # v9: re-parse TV/anime files whose old parsed_data has
                # no episode number — picks up new parser patterns (e.g.
                # YEAR-EE `[aL].Show.2023-01.WEB`) so orphan-on-popup
                # files get their episode assigned next heal pass.
                reparsed_n = await _reparse_missing_episodes(session)
                if reparsed_n:
                    print(f"auto_heal: re-parsed {reparsed_n} files that now have an episode number.")
                # v11: queue TVDB/TMDB TV matches for rematch so they
                # fetch per-season poster art via the new matcher hook.
                # Existing rows are otherwise stuck on series-level
                # posters — every season card in a franchise group ends
                # up looking identical until this fires.
                season_poster_n = await _trigger_tvdb_tmdb_season_rematch(session)
                if season_poster_n:
                    print(f"auto_heal: queued {season_poster_n} TVDB/TMDB matches for per-season poster refresh.")
                # v12 (EE-5): recompute series_key for every TV/anime/music
                # row using the new year/parent-folder disambiguator.
                # Without this, libraries that pre-date the fix keep
                # clustering same-titled shows ("The Office" UK + US)
                # together forever. New format adds a trailing |disambig
                # component, so EVERY row gets rewritten this pass even
                # when the disambig component is empty.
                series_key_n = await _recompute_series_keys(session)
                if series_key_n:
                    print(f"auto_heal: recomputed series_key for {series_key_n} files with year/parent disambiguator.")
                # v24: re-match files whose stored episode_number drifted away
                # from the file's parsed episode (the One Piece stale-match
                # class). Pure in-memory detection; arms the BATCH loop to do
                # the real, ban-aware rematch below.
                drift_n = await _heal_episode_number_drift(session)
                if drift_n:
                    print(f"auto_heal: flagged {drift_n} episode-drifted matches for rematch.")
                # v25: AniDB-matched files that the parser typed non-anime get
                # corrected to media_type=anime so they leave the TV Series group.
                mt_n = await _heal_media_type_from_provider(session)
                if mt_n:
                    print(f"auto_heal: corrected media_type=anime for {mt_n} AniDB-matched file(s).")
                # v25: re-match movies whose stored year != parsed year (stale
                # fallback to the wrong-year film, e.g. Nobody 2 → Nobody 2021).
                myr_n = await _heal_movie_year_mismatch(session)
                if myr_n:
                    print(f"auto_heal: flagged {myr_n} year-mismatched movie(s) for rematch.")
                await session.commit()
            except Exception as e:
                # R2-H2 caveat: the CAS already bumped the version row, so
                # on retry next boot the cleanup won't fire even if we
                # rolled back the data work. Rollback the version too.
                print(f"auto_heal: heal pass failed: {e!r}")
                await session.rollback()
                try:
                    await session.execute(
                        sql_update(Setting)
                        .where(Setting.key == "system.heal_version")
                        .values(value=max(0, _HEAL_VERSION - 1))
                    )
                    await session.commit()
                except Exception:
                    pass
        total_healed = 0
        while True:
            # Find files where the selected match exists, is TV/anime, and
            # has no episode_title OR no metadata_blob. Pull the file's
            # CURRENT match provider too — used to gate AniDB-banned files
            # without blocking TVDB/TMDB heal work.
            from sqlalchemy import and_, or_
            # Heal trigger: tv_episode rows need either an episode title or
            # metadata blob; movie rows only need metadata blob (movies
            # don't have an episode_title concept — including it in the OR
            # would make EVERY movie row trigger heal forever).
            #
            # ── Cursor pagination instead of seen-fids NOT IN(...) ──────
            # Previously the loop tracked already-attempted IDs in a
            # Python set and inlined them into the WHERE clause via
            # `MediaFile.id.notin_(seen_fids)`. SQLite has a compile-time
            # variable cap (SQLITE_MAX_VARIABLE_NUMBER, usually 32 766
            # but historically 999) — at ~30 k healed files the query
            # crashed with `OperationalError: too many SQL variables`
            # and the loop died permanently for the boot. We now order
            # by MediaFile.id ASC and carry a `last_processed_id`
            # cursor — every row in a returned batch has id strictly
            # greater than the cursor, so we naturally never revisit a
            # row within one sweep. AniDB-banned files are skipped
            # in-batch and revisited on the NEXT boot (cursor resets
            # at function entry), which matches the prior semantics
            # without the variable-cap bomb.
            stmt = (
                select(MediaFile.id, Match.provider)
                .join(Match, Match.media_file_id == MediaFile.id)
                .where(
                    MediaFile.id > last_processed_id,
                    Match.is_selected.is_(True),
                    # NOTE: manual pins are INCLUDED in the heal query.
                    # Previously `Match.is_manual.is_(False)` excluded
                    # them entirely, which was safe-but-wasteful when
                    # `_rematch_one` ran a full discovery pass (would
                    # have overwritten the pin). Since Autopsy 4 added
                    # the enrichment-only fast path, manual pins are
                    # detected at the top of `_rematch_one` and ONLY
                    # missing enrichment fields (episode_title,
                    # metadata_blob, poster_url) get filled — the
                    # pinned identity is never re-discovered. Removing
                    # the exclusion lets the user's freshly-pinned
                    # rows pick up genres/cast/director/etc. on the
                    # next heal sweep without requiring a separate
                    # rematch click (the popup's old "Re-match" button
                    # was the only path before; now there is none).
                    or_(
                        and_(
                            Match.match_type == "tv_episode",
                            or_(Match.episode_title.is_(None),
                                Match.metadata_blob.is_(None)),
                        ),
                        and_(
                            Match.match_type == "movie",
                            Match.metadata_blob.is_(None),
                        ),
                    ),
                )
                .order_by(MediaFile.id.asc())
                .limit(BATCH)
            )
            rows = list(await session.execute(stmt))
            if not rows:
                break

            # Advance the cursor BEFORE processing — that way a
            # mid-batch crash on one file doesn't infinitely re-query
            # rows we already pulled. Cursor advancement is constant-
            # time and unconditional; it does not depend on whether
            # processable is empty (an all-banned batch still needs to
            # move past those IDs to reach the next AniDB-free row).
            last_processed_id = max(fid for fid, _ in rows)

            # Per-file ban check: skip AniDB-matched files when banned (their
            # _rematch_one would burn through the rate limit pointlessly),
            # but ALWAYS proceed for TVDB / TMDB matches — those have no
            # AniDB dependency at all. Without this split, the previous
            # blanket abort starved metadata refills for the entire library
            # whenever AniDB was banned.
            banned = AniDBProvider.is_banned()
            processable = [(fid, prov) for fid, prov in rows
                           if not (banned and prov == "anidb")]

            if not processable:
                # Nothing to do this batch (everything was AniDB during ban).
                # Continue the loop to look past the skipped IDs.
                continue

            print(f"auto_heal: rematching {len(processable)} stale matches…")
            for fid, prov in processable:
                # Per-iteration ban check covers the case where AniDB bans
                # us mid-sweep on a TVDB-rematch that nevertheless triggered
                # a follow-up AniDB call (e.g. relations walk for a cross
                # provider). Cheap belt.
                if prov == "anidb" and AniDBProvider.is_banned():
                    continue
                try:
                    # Load the row fresh — was the M9 race-check site,
                    # which used to skip manual pins entirely. We now
                    # rely on `_rematch_one`'s enrichment-only fast
                    # path (Autopsy 4) to handle manual pins safely:
                    # it detects them at the top and only fills missing
                    # enrichment fields without re-running discovery,
                    # so the user's pinned identity is preserved
                    # whether they pinned BEFORE or AFTER the batch
                    # fetch. The fresh load is still useful for picking
                    # up any other concurrent state changes.
                    from sqlalchemy.orm import selectinload as _sl
                    fresh = await session.scalar(
                        select(MediaFile)
                        .options(_sl(MediaFile.matches))
                        .where(MediaFile.id == fid)
                    )
                    if fresh is None:
                        continue
                    await _rematch_one(fresh, engine, session)
                    await session.commit()
                    total_healed += 1
                    activity.progress("heal", total_healed)
                except Exception as e:
                    print(f"auto_heal: file {fid} failed: {e!r}")
                    await session.rollback()
            # Yield between batches so request handlers stay responsive.
            await asyncio.sleep(1.0)
        print("auto_heal: done.")
        activity.end("heal")
        if total_healed > 0:
            try:
                from kira.models import Notification
                async with SessionLocal() as n_sess:
                    n_sess.add(Notification(
                        kind="info",
                        title=f"Auto-heal: {total_healed} file{'s' if total_healed != 1 else ''} refreshed",
                        body="Stale matches were automatically re-matched with the latest engine.",
                    ))
                    await n_sess.commit()
            except Exception:
                pass


async def _bulk_rematch_worker(fids: list[int], force: bool = False) -> None:
    """Background bulk-rematch — one fresh session, one httpx client, one
    file at a time. Commits after each so a partial failure doesn't undo
    earlier progress.

    ── R2-H10: Yield per AniDB call, not per file ─────────────────────
    A single franchise-heavy file (e.g. AID in a 5-season chain) fires
    5+ AniDB HTTP calls: relations walk + episode list + per-AID episode
    counts. Yielding every 5 FILES means 25+ AniDB calls bunched
    together with no break — foreground scans starve for the full burst.
    Instead, track the cumulative HTTP call count via
    `AniDBProvider._http_call_count` and yield after every CALL_YIELD
    AniDB calls. Each yield lets foreground coroutines acquire the
    AniDB lock between our bursts.
    """
    import asyncio
    from kira.providers.anidb import AniDBProvider
    CALL_YIELD = 10  # yield to foreground after this many AniDB calls
    SETTLE_SLEEP = 0.5  # how long to step aside
    done = 0
    failed = 0
    async with SessionLocal() as session, httpx.AsyncClient() as client:
        engine = MatchEngine(await registry_from_settings(client))
        last_call_mark = AniDBProvider._http_call_count
        for fid in fids:
            try:
                mf = await session.get(MediaFile, fid)
                if mf:
                    await _rematch_one(mf, engine, session, force=force)
                    await session.commit()
                    done += 1
            except Exception as e:
                print(f"bulk_rematch: file {fid} failed: {e!r}")
                await session.rollback()
                failed += 1
            if AniDBProvider._http_call_count - last_call_mark >= CALL_YIELD:
                await asyncio.sleep(SETTLE_SLEEP)
                last_call_mark = AniDBProvider._http_call_count
    try:
        from kira.models import Notification
        async with SessionLocal() as n_sess:
            parts = [f"{done} file{'s' if done != 1 else ''} re-matched"]
            if failed:
                parts.append(f"{failed} failed")
            n_sess.add(Notification(
                kind="success" if not failed else "warning",
                title=f"Rematch complete: {', '.join(parts)}",
                body="Open Review to see the updated matches.",
            ))
            await n_sess.commit()
    except Exception:
        pass
