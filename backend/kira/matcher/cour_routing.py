"""Shared cour-routing helper — single source of truth for the
"this file's episode belongs to a specific cour AID" decision.

Without this module, the Platinum cour-routing logic lived only inside
`_match_cluster` (the scan-time path). `_rematch_one` (single-file
rematch + auto-heal) bypassed it entirely and would re-pick the first
sibling cour AID via the cascade tie-break, orphaning every file
outside Cour 1's range. Auto-heal would then silently destroy the
correct routing the next time it touched any of these files.

The fix: extract the routing logic into a pure function and call it
from EVERY code path that writes a Match row. Anyone who commits an
AniDB Match for a Fribb-pinned multi-cour TVDB season passes through
this helper.

Pure-disk: reads only the in-memory Fribb dict + the AniDB episode-
count cache on disk. No provider HTTP, safe during AniDB IP-ban.
"""

from __future__ import annotations

from typing import Any


async def build_cour_routing_table(
    provider: str,
    top_provider_id: str,
    parsed_season: int | None,
    registry: Any = None,
) -> list[tuple[int, int, int, int]] | None:
    """Return the routing table for a multi-cour TVDB season, or None.

    Each entry is `(start_episode, end_episode, cour_aid, offset)`
    where `offset = start_episode - 1`. A file with `parsed.episode`
    in `[start, end]` belongs to `cour_aid`, with local episode
    number `parsed.episode - offset`.

    Returns `None` when:
      - provider isn't AniDB
      - parsed_season is missing
      - the top AID's `provider_id` isn't a valid int
      - Fribb has no `(tvdb_id, tvdb_season)` mapping for the top AID
      - the top AID's `tvdb_season` doesn't agree with `parsed_season`
        (a stale Fribb entry or umbrella mismatch — refuse to route)
      - `aids_by_tvdb_season(tvdb_id, season)` returns fewer than 2
        siblings (a single-cour series — no routing needed)
      - any sibling lacks a cached episode count (incomplete data,
        the offset table can't be trusted without it)

    Bleach S17 (Thousand Year Blood War) example:
      top_provider_id="15449" (Cour 1, 13 eps),
      parsed_season=17
      → Fribb says (tvdb_id=74796, season=17) has siblings
        [15449, 17849, 18671] (Cours 1/2/3, 13/13/14 episodes).
      → Returns:
          [(1, 13, 15449, 0),
           (14, 26, 17849, 13),
           (27, 40, 18671, 26)]
    """
    if provider != "anidb" or parsed_season is None:
        return None

    # Imports kept local so the module is import-light when not needed
    # (e.g. movie matching, non-AniDB providers).
    from kira.providers.anidb import AniDBProvider
    from kira.providers.anime_mappings import AnimeMappings

    try:
        top_aid = int(top_provider_id)
    except (ValueError, TypeError):
        return None

    try:
        top_tvdb = await AnimeMappings.tvdb_id(top_aid)
        top_season = await AnimeMappings.tvdb_season(top_aid)
    except Exception:
        return None

    if top_tvdb is None or top_season is None or top_season != parsed_season:
        return None

    try:
        sibling_aids = await AnimeMappings.aids_by_tvdb_season(top_tvdb, parsed_season)
    except Exception:
        return None

    if len(sibling_aids) <= 1:
        return None  # single cour — no routing needed

    count_cache = AniDBProvider._load_ep_count_cache()

    # Lazy-fetch missing sibling counts (Autopsy 15). Common scenario:
    # the initial scan only matched files to Cour 1 (umbrella mis-route
    # in pre-fix builds, OR first ever scan on a brand-new library), so
    # the episode-count cache has AID 15449 = 13 but NO entry for
    # 17849 / 18671. Without dynamic fetching, the table build aborts,
    # bulk-select-manual writes everything as Cour 1, and E14-E40
    # silently orphan when auto-heal looks up against Cour 1's 13-ep
    # list.
    #
    # The lazy-fetch needs a properly-constructed AniDBProvider, which
    # in turn needs (base_url, auth, httpx.AsyncClient). Those live in
    # the matcher's ProviderRegistry. The caller threads `registry`
    # through — every caller of this helper (scans `_match_cluster`,
    # matches `_rematch_one` discovery + enrichment fast path,
    # bulk_select_manual_match, select_manual_match) has access to a
    # registry. Without registry passed in, we fall back to the strict
    # behavior (abandon routing) — safer than failing midway.
    missing = [a for a in sibling_aids if count_cache.get(a) is None or count_cache.get(a) <= 0]
    if missing:
        if registry is None or not getattr(registry, "has", None) or not registry.has("anidb"):
            return None
        if AniDBProvider.is_banned():
            return None
        try:
            anidb = registry.build("anidb")
        except Exception:
            return None
        for sib_aid in missing:
            try:
                eps = await anidb.get_episodes(str(sib_aid), 1)
                if not eps:
                    return None
                # get_episodes write-through populates _ep_count_cache
                # as a side effect (see AniDBProvider.get_episodes).
                # Defensive: stamp count_cache directly in case the
                # provider's write was suppressed (e.g. test stubs).
                count_cache[sib_aid] = len(eps)
            except Exception as e:
                print(f"cour_routing: dynamic fetch for AID {sib_aid} failed: {e!r}")
                return None
        # Re-read the on-disk cache in case the provider's write-through
        # produced canonical values (e.g. specials-stripped count).
        count_cache = AniDBProvider._load_ep_count_cache()

    table: list[tuple[int, int, int, int]] = []
    current_start = 1
    for sib_aid in sibling_aids:
        count = count_cache.get(sib_aid)
        if count is None or count <= 0:
            # After the lazy-fetch above this should be impossible.
            # Defensive: abandon routing if we still can't build it.
            return None
        current_end = current_start + count - 1
        offset = current_start - 1
        table.append((current_start, current_end, sib_aid, offset))
        current_start = current_end + 1

    return table


def route_file_to_cour(
    table: list[tuple[int, int, int, int]] | None,
    file_episode: int | None,
) -> tuple[int, int] | None:
    """Look up which cour `(aid, local_episode)` owns `file_episode`.

    `table` should be the result of `build_cour_routing_table`.
    `file_episode` is typically `parsed.episode` (season-local
    numbering); fall back to `parsed.absolute_episode` only when
    season-local is unavailable.

    Returns `(routed_aid, routed_local_episode)` when the file falls
    in some cour's range, or `None` when:
      - `table` is None (no routing applicable for this match)
      - `file_episode` is None
      - the episode falls outside every cour's range (anomalous file,
        e.g. an episode 41 in a 40-episode 3-cour season)

    Pure-Python lookup; no I/O.
    """
    if table is None or file_episode is None:
        return None
    for c_start, c_end, c_aid, c_offset in table:
        if c_start <= file_episode <= c_end:
            return c_aid, file_episode - c_offset
    return None


async def route_file_to_cour_precise(
    table: list[tuple[int, int, int, int]] | None,
    file_episode: int | None,
    *,
    provider: str = "",
    top_provider_id: str = "",
    parsed_season: int | None = None,
) -> tuple[int, int] | None:
    """Cour routing: summed-count table FIRST, ScudLee only to fill gaps.

    The summed-episode-count offset table (`route_file_to_cour`) is authoritative
    for CONTIGUOUS cours — which is the overwhelmingly common case (Bleach TYBW,
    AoT Final Season, etc.: Cour 1 = eps 1-13, Cour 2 = 14-26, …). We trust it
    first.

    ScudLee's `anime-lists` XML is consulted ONLY when the table can't place the
    episode (it falls outside every contiguous cour range — a mid-season special
    insert or an offset cour the summed-count math doesn't model). Even then we
    accept ScudLee's answer only when it lands on one of THIS franchise's sibling
    cours already in `table`.

    Why not ScudLee-first: ScudLee maps many cours via a FLAT
    `defaulttvdbseason + episodeoffset` form with no end bound, so when several
    cours share one TVDB season its resolver returns the FIRST cour's AID for
    EVERY episode — which silently collapsed all of Bleach/AoT onto Cour 1 and
    orphaned everything past Cour 1's episode count. Table-first makes this a
    strict, safe refinement: it can only place episodes the table left None,
    never override a correct contiguous routing.

    In-memory after first use (ScudLee XML cached 24 h on disk); sourced from
    GitHub, not AniDB → safe during an AniDB ban. `resolve_tvdb_to_anidb` never
    raises.
    """
    # 1. Authoritative summed-count routing for contiguous cours.
    base = route_file_to_cour(table, file_episode)
    if base is not None:
        return base

    # 2. Only reached when the table couldn't place the episode. ScudLee may
    #    have an explicit mapping for it (special insert / offset cour).
    if (
        table
        and provider == "anidb"
        and parsed_season is not None
        and file_episode is not None
    ):
        try:
            top_aid = int(top_provider_id)
        except (ValueError, TypeError):
            top_aid = None
        if top_aid is not None:
            try:
                from kira.providers.anime_mappings import AnimeMappings
                tvdb_id = await AnimeMappings.tvdb_id(top_aid)
            except Exception:
                tvdb_id = None
            if tvdb_id is not None:
                try:
                    from kira.providers.anime_lists import resolve_tvdb_to_anidb
                    scud = await resolve_tvdb_to_anidb(tvdb_id, parsed_season, file_episode)
                except Exception:
                    scud = None
                if scud is not None:
                    s_aid, s_ep = scud
                    table_aids = {c_aid for _, _, c_aid, _ in table}
                    if s_aid in table_aids and s_ep >= 1:
                        return s_aid, s_ep
    return None
