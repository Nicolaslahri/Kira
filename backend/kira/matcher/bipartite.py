"""Bipartite file-to-episode pairing — the reference renamer's Matcher.deepMatch approach.

For an N-file cluster scored against a series with M episodes, build a
metric matrix and greedily extract unambiguous pairs. Iterate: each pass
removes pairs where one file maps uniquely to one episode AND that
episode maps uniquely to that file.

This fixes the One Piece S23E1158 failure: the file's `parsed.episode=1158`
doesn't exist in TVDB's S23 (which has ~15 eps). Strict (season, episode)
lookup orphans every file. Bipartite refinement runs multiple metrics:

  1. Exact (parsed.season, parsed.episode) → ep.season, ep.episode
  2. Absolute (parsed.absolute) → ep.episode (AniDB-native pairing)
  3. Just parsed.episode → ep.episode (season-agnostic, the absolute-
     numbered-as-episode case for One Piece S23E1158)

If all three metrics fail to extract any unambiguous pair, the file
stays unpaired (orphan in the popup, "no episode" pill).

User-locked: fires only when len(cluster) >= 3. N=1 has no signal,
N=2 is a coin flip; the existing per-file lookup handles those.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MIN_CLUSTER_FOR_BIPARTITE = 3


@dataclass
class FileToEpisode:
    file_id: int
    episode_season: int | None
    episode_number: int | None
    episode_title: str | None
    matched_via: str   # "exact"|"absolute"|"absolute_sxe"|"episode_number"|"air_date"|"title"|"unpaired"


def assign_files_to_episodes(
    files: list[tuple[int, Any]],          # list[(file_id, ParsedFile)]
    episodes: list[Any],                    # list[EpisodeResult or {season,episode,title}]
) -> dict[int, FileToEpisode]:
    """Greedy bipartite assignment.

    Returns a dict {file_id: FileToEpisode}. Files that couldn't be
    paired have matched_via="unpaired" and None episode fields.

    For clusters smaller than MIN_CLUSTER_FOR_BIPARTITE, returns the
    naive per-file lookup result (no bipartite refinement).
    """
    if not files:
        return {}
    if not episodes:
        # No episode list at all — every file unpaired.
        return {
            fid: FileToEpisode(fid, None, None, None, "unpaired")
            for fid, _ in files
        }

    out: dict[int, FileToEpisode] = {}
    remaining_eps = list(episodes)
    used_ep_keys: set[tuple[int, int]] = set()
    # Track claimed files in a set and skip them with an O(1) membership test,
    # instead of rebuilding the whole `remaining_files` list on every claim
    # (which made each pass O(n²) in cluster size). Each pass now iterates the
    # original `files` once and skips already-claimed ids.
    claimed_fids: set[int] = set()

    def _ep_key(ep) -> tuple[int, int]:
        s = getattr(ep, "season", None)
        e = getattr(ep, "episode", None)
        if s is None and isinstance(ep, dict):
            s = ep.get("season")
            e = ep.get("episode")
        # Preserve season 0 (Specials). `int(s or 1)` would coerce 0 → 1
        # (0 is falsy), mis-keying every Season-0 entry into the main season's
        # key space — so the exact (0, e) pass could never pair a real special.
        return (int(s) if s is not None else 1, int(e) if e is not None else 0)

    def _ep_title(ep) -> str | None:
        if hasattr(ep, "title"):
            return ep.title
        if isinstance(ep, dict):
            return ep.get("title")
        return None

    def _ep_air_date(ep) -> str | None:
        v = getattr(ep, "air_date", None)
        if v is None and isinstance(ep, dict):
            v = ep.get("air_date")
        return (v[:10] if isinstance(v, str) and len(v) >= 10 else None)

    def _claim(fid: int, parsed, ep, via: str, episode_override: int | None = None) -> None:
        s, e = _ep_key(ep)
        # episode_override stores the file's REAL number when it was paired by
        # ABSOLUTE identity against a per-season list that numbers episodes
        # locally (One Piece S23E1156 paired via absolute_number → store 1156,
        # not the local index 1). The claim KEY stays (s, e) so the episode
        # slot still can't be double-assigned. Defaults to the local number, so
        # every existing caller is byte-identical.
        stored_ep = episode_override if episode_override is not None else e
        out[fid] = FileToEpisode(fid, s, stored_ep, _ep_title(ep), via)
        used_ep_keys.add((s, e))
        claimed_fids.add(fid)

    # Pass 1 — exact (parsed.season, parsed.episode) match.
    for fid, parsed in files:
        if fid in claimed_fids:
            continue
        if parsed.season is None or parsed.episode is None:
            continue
        for ep in remaining_eps:
            s, e = _ep_key(ep)
            if (s, e) in used_ep_keys:
                continue
            if s == parsed.season and e == parsed.episode:
                _claim(fid, parsed, ep, "exact")
                break

    # Pass 2 — absolute episode pairing. Two provider conventions:
    #   - AniDB: ep.season=1 for everything, ep.episode IS the absolute
    #     number (E1158 → ep.episode=1158).
    #   - TVDB: ep.season=23, ep.episode=5 (LOCAL), ep.absolute_number=1158.
    # The straight `e == abs_ep` check only worked for AniDB. For TVDB-
    # matched anime (the AniDB-ban fallback path on long-runners) the
    # 1158-vs-5 comparison always failed, orphaning every absolute-
    # numbered file. Resolution order: provider's absolute_number first
    # when set, fall back to ep.episode (the AniDB-native case).
    for fid, parsed in files:
        if fid in claimed_fids:
            continue
        abs_ep = parsed.absolute_episode
        if abs_ep is None:
            continue
        for ep in remaining_eps:
            s, e = _ep_key(ep)
            if (s, e) in used_ep_keys:
                continue
            provider_absolute = getattr(ep, "absolute_number", None)
            if isinstance(ep, dict):
                provider_absolute = ep.get("absolute_number")
            target_ep = provider_absolute if provider_absolute is not None else e
            if target_ep == abs_ep:
                # Store the ABSOLUTE number, not the provider-LOCAL index — a
                # TVDB per-season list pairs a [1158] file via absolute_number
                # but its local episode is 3; storing 3 was the collapse.
                _claim(fid, parsed, ep, "absolute", episode_override=abs_ep)
                break

    # Pass 3 — season-agnostic episode-number match (the One Piece
    # S23E1158 fix: parsed.episode=1158, AniDB has ep.episode=1158 with
    # ep.season=1; strict (23, 1158) misses but season-agnostic 1158
    # hits). Only fire for anime to avoid TV cross-season collisions.
    is_anime = any(getattr(p, "media_type", None) == "anime" for _, p in files)
    if is_anime:
        for fid, parsed in files:
            if fid in claimed_fids:
                continue
            # Season-0 isolation: a Special/OVA ("S00E05") must NOT be matched
            # season-agnostically against a MAIN-run episode (its episode 5).
            # Specials only legitimately pair via Pass 1's exact (0, e) lookup
            # against a real Season-0 entry; if that missed, leave it unpaired
            # rather than overwriting canonical episode 5 in the grid.
            if parsed.episode is None or parsed.season == 0:
                continue
            for ep in remaining_eps:
                s, e = _ep_key(ep)
                if (s, e) in used_ep_keys:
                    continue
                if e == parsed.episode:
                    _claim(fid, parsed, ep, "episode_number")
                    break

    # Pass 3.5 — anime long-runner ABSOLUTE-by-episode (the One Piece S23E1156
    # fix). The SxE form "S23E1156" parses as episode=1156 with
    # absolute_episode=None, so Pass 2 (which gates on parsed.absolute_episode)
    # can't fire, and Pass 3's local `e == 1156` misses a per-season list
    # numbered 1..13. Pair by the provider's absolute_number == parsed.episode
    # and STORE the file's real number (1156). Without this the only remaining
    # pass is the title pass — which stores the LOCAL index (1156 → 1), the
    # exact collapse. Hard-gated so it can't change any working shape:
    #   • anime only;
    #   • file has NO parsed.absolute_episode (Pass 2 owns those);
    #   • runs AFTER Pass 3 so per-cour LOCAL numbering (Frieren S2E03) wins;
    #   • requires ep.absolute_number set AND != ep.episode, so single-season
    #     lists (absolute == local) are a strict no-op;
    #   • the file's number must EXCEED every local episode in the list
    #     (parsed.episode > max local index) — that's what proves it's an
    #     absolute, not a local. This stops a low per-cour number from ever
    #     colliding with a sibling episode's absolute_number (e.g. a 12-ep
    #     cour where some ep's absolute_number happens to equal a small local
    #     number elsewhere); such a file isn't > max_local, so it's skipped.
    if is_anime:
        # max_local must be the maximum LOCAL index across the WHOLE episode
        # list (not a subset): the "file number exceeds every local index"
        # test is what proves a number is an absolute rather than a local, so
        # it has to see all episodes. (`remaining_eps` is the full list today,
        # but compute from `episodes` directly so this stays correct even if a
        # future pass starts pruning the remaining set.)
        max_local = max((_ep_key(ep)[1] for ep in episodes), default=0)
        for fid, parsed in files:
            if fid in claimed_fids:
                continue
            if parsed.episode is None or parsed.absolute_episode is not None:
                continue
            if parsed.season == 0:        # Season-0 isolation (see Pass 3)
                continue
            if parsed.episode <= max_local:
                continue
            for ep in remaining_eps:
                s, e = _ep_key(ep)
                if (s, e) in used_ep_keys:
                    continue
                ep_abs = getattr(ep, "absolute_number", None)
                if ep_abs is None and isinstance(ep, dict):
                    ep_abs = ep.get("absolute_number")
                if ep_abs is not None and ep_abs != e and ep_abs == parsed.episode:
                    _claim(fid, parsed, ep, "absolute_sxe", episode_override=parsed.episode)
                    break

    # Pass 4 — air-date match (Phase 9). Daily / talk / news files numbered
    # by date pair against the provider's air_date field. Exact date match,
    # so it runs ahead of the fuzzier title pass.
    for fid, parsed in files:
        if fid in claimed_fids:
            continue
        ad = getattr(parsed, "air_date", None)
        if not ad:
            continue
        target = ad[:10]
        for ep in remaining_eps:
            if _ep_key(ep) in used_ep_keys:
                continue
            if _ep_air_date(ep) == target:
                _claim(fid, parsed, ep, "air_date")
                break

    # Pass 5 — episode-title similarity (Phase 6). For files STILL unpaired
    # after number-based passes, match the filename's episode-title guess
    # (text after the SxE marker) against the remaining episodes' titles.
    # Resolves SxE-less or wrong-numbered files by NAME — the reference renamer's
    # episode-title matching. Additive: only touches files passes 1-3 left
    # orphaned, and only claims an episode on a strong (≥0.6) title match.
    from kira.matcher.similarity import trigram_similarity
    for fid, parsed in files:
        if fid in claimed_fids:
            continue
        guess = getattr(parsed, "episode_title_guess", None)
        if not guess:
            continue
        best_ep = None
        best_sim = 0.0
        for ep in remaining_eps:
            if _ep_key(ep) in used_ep_keys:
                continue
            t = _ep_title(ep)
            if not t:
                continue
            sim = trigram_similarity(guess, t)
            if sim > best_sim:
                best_sim = sim
                best_ep = ep
        if best_ep is not None and best_sim >= 0.6:
            _claim(fid, parsed, best_ep, "title")

    # Anything still unmatched is genuinely orphan.
    for fid, _parsed in files:
        if fid not in claimed_fids:
            out[fid] = FileToEpisode(fid, None, None, None, "unpaired")

    return out
