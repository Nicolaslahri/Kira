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
    abs_to_local: dict[int, int] | None = None,
) -> tuple[int, int] | None:
    """Look up which cour `(aid, local_episode)` owns `file_episode`.

    `table` should be the result of `build_cour_routing_table` — its ranges
    are in SEASON-LOCAL episode space (1..N across the TVDB season).
    `file_episode` is typically `parsed.episode` (season-local numbering).

    `abs_to_local` (optional): a `{absolute_number: season_local_episode}` map
    built from the matched provider's episode list. It bridges the one case the
    plain table can't: a file numbered in SERIES-ABSOLUTE space. AoT's Final
    Season files are `- 60`..`- 89` (absolute), but the cours are keyed 1..30
    (season-local), so a direct lookup of 60 falls outside every range. When the
    direct lookup misses AND `file_episode` is a known absolute number, we
    convert it to season-local and retry — which lets pure-absolute-numbered
    long-runners (AoT Final Season, One-Piece-style "- 1071") reach the router.
    Only consulted ON A DIRECT MISS, so cour-local-numbered shows (Bleach TYBW,
    whose files are 1..40 and hit the table directly) are completely unaffected.

    Returns `(routed_aid, routed_local_episode)` when the file falls in some
    cour's range, or `None` when:
      - `table` is None (no routing applicable for this match)
      - `file_episode` is None
      - the episode is outside every range AND not bridgeable via `abs_to_local`

    Pure-Python lookup; no I/O.
    """
    if table is None or file_episode is None:
        return None

    def _lookup(ep: int) -> tuple[int, int] | None:
        for c_start, c_end, c_aid, c_offset in table:
            if c_start <= ep <= c_end:
                return c_aid, ep - c_offset
        return None

    direct = _lookup(file_episode)
    if direct is not None:
        return direct
    # Absolute-number bridge: series-absolute → season-local, then retry.
    if abs_to_local:
        local = abs_to_local.get(file_episode)
        if local is not None:
            return _lookup(local)
    return None


async def route_file_to_cour_precise(
    table: list[tuple[int, int, int, int]] | None,
    file_episode: int | None,
    *,
    provider: str = "",
    top_provider_id: str = "",
    parsed_season: int | None = None,
    abs_to_local: dict[int, int] | None = None,
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
    # 1. Authoritative summed-count routing for contiguous cours (with the
    #    absolute→local bridge for pure-absolute-numbered files).
    base = route_file_to_cour(table, file_episode, abs_to_local)
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


def remap_umbrella_local_to_absolute(
    ep_num: int | None,
    *,
    is_flat_umbrella: bool,
    routed_aid: int | None,
    local_to_abs: dict[int, int],
) -> int | None:
    """Flat-umbrella local→absolute remap (the One Piece "S23E04" → 1159 fix).

    The INVERSE of `route_file_to_cour`'s abs→local bridge. A FLAT umbrella is a
    single AniDB AID that numbers the WHOLE long-runner absolutely (One Piece 69,
    Naruto, Detective Conan — Fribb carries no `season.tvdb`, so the caller's
    `tvdb_season(top_aid) is None`). A file that arrived in TVDB-season-LOCAL form
    ("One Piece 1999 S23E04" → bipartite pairs it to the Elbaf cour's LOCAL
    episode 4) must store the ABSOLUTE (1159) so it lines up with its
    absolute-numbered siblings ("S23E1159" → already 1159) and is recognised as
    the duplicate it is.

    `local_to_abs` is the cluster's reverse of `abs_to_local`
    (`{season_local_episode: provider_absolute_number}`). Returns `ep_num`
    unchanged unless ALL hold:
      • `is_flat_umbrella` — caller proved tvdb_season(top_aid) is None;
      • `routed_aid is None` — cour routing didn't already place the file. By
        construction a flat umbrella has NO Fribb cours (aids_by_tvdb_season is
        empty → no routing table), so this is always true for a real umbrella;
        the guard just makes the two systems provably non-overlapping;
      • `ep_num` is a known LOCAL index present in `local_to_abs`.

    Therefore left UNTOUCHED:
      • absolute-named files (1159 ∉ local_to_abs → siblings keep their number);
      • per-season AIDs — Frieren S2 (tvdb_season=2), AoT cours (=4) — whose
        episode lists ARE local: caller passes is_flat_umbrella=False;
      • normal TV, whose provider episodes carry no absolute_number, so
        `local_to_abs` is empty;
      • an early-cour file where absolute == local (One Piece ep 4 in the 1999
        season): local_to_abs[4] == 4 → a self-mapping no-op.

    Pure; no I/O. The rename FILENAME is independent of this (it renders from
    parsed.episode / {{absx}}); this only corrects the stored episode_number so
    the popup pairs the file against the umbrella's absolute episode list.
    """
    if ep_num is None or not is_flat_umbrella or routed_aid is not None:
        return ep_num
    return local_to_abs.get(ep_num, ep_num)


def franchise_absolute(
    offsets: list[tuple[int, int, int]] | None,
    aid: int | None,
    local_ep: int | None,
) -> int | None:
    """Map an AID-LOCAL episode to its FRANCHISE-ABSOLUTE number — the
    rename-output inverse of `AniDBProvider.get_franchise_offsets`.

    This closes the locally-named→absolute gap: a file named per-cour-local
    (`AoT S4E01`) and matched to a per-cour AniDB AID has no absolute number in
    its name, so `{{absx}}` had nothing to render. The franchise offset table
    supplies it.

    `offsets` is `get_franchise_offsets()`'s result — `[(aid, abs_start, abs_end)]`
    sorted by start, where each season/cour AID owns the absolute range
    `[abs_start, abs_end]`. A file matched to `aid` at AID-local episode
    `local_ep` (1-based within that AID — i.e. `Match.episode_number` after cour
    routing) is the `abs_start + local_ep - 1`-th episode of the whole franchise.

    Example: AoT Final Season cour at `(…, 60, 87)`, local ep 1 → **60**.

    Returns None — leaving the filename to fall back to its SxE form, exactly as
    today — when it can't be computed SAFELY:
      • no offsets / aid / local_ep (or local_ep < 1),
      • `aid` isn't in the franchise table (unknown / not an offset member),
      • the result falls OUTSIDE that AID's `[abs_start, abs_end]` span (local_ep
        too large — a numbering mismatch we refuse to guess through, since a
        wrong absolute on a rename path is worse than none).

    Pure; no I/O — the offsets come from the (cache-first) provider call."""
    if not offsets or aid is None or local_ep is None or local_ep < 1:
        return None
    for a, abs_start, abs_end in offsets:
        if a == aid:
            absolute = abs_start + local_ep - 1
            return absolute if abs_start <= absolute <= abs_end else None
    return None
