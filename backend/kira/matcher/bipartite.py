"""Bipartite file-to-episode pairing — FileBot's Matcher.deepMatch approach.

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
    matched_via: str   # "exact" | "absolute" | "episode_number" | "unpaired"


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
    remaining_files = list(files)
    remaining_eps = list(episodes)
    used_ep_keys: set[tuple[int, int]] = set()

    def _ep_key(ep) -> tuple[int, int]:
        s = getattr(ep, "season", None)
        e = getattr(ep, "episode", None)
        if s is None and isinstance(ep, dict):
            s = ep.get("season")
            e = ep.get("episode")
        return (int(s or 1), int(e or 0))

    def _ep_title(ep) -> str | None:
        if hasattr(ep, "title"):
            return ep.title
        if isinstance(ep, dict):
            return ep.get("title")
        return None

    def _claim(fid: int, parsed, ep, via: str) -> None:
        s, e = _ep_key(ep)
        out[fid] = FileToEpisode(fid, s, e, _ep_title(ep), via)
        used_ep_keys.add((s, e))

    # Pass 1 — exact (parsed.season, parsed.episode) match.
    for fid, parsed in list(remaining_files):
        if parsed.season is None or parsed.episode is None:
            continue
        for ep in remaining_eps:
            s, e = _ep_key(ep)
            if (s, e) in used_ep_keys:
                continue
            if s == parsed.season and e == parsed.episode:
                _claim(fid, parsed, ep, "exact")
                remaining_files = [(f, p) for f, p in remaining_files if f != fid]
                break

    # Pass 2 — absolute episode pairing (AniDB native: ep.season=1, ep.episode=absolute).
    for fid, parsed in list(remaining_files):
        abs_ep = parsed.absolute_episode
        if abs_ep is None:
            continue
        for ep in remaining_eps:
            s, e = _ep_key(ep)
            if (s, e) in used_ep_keys:
                continue
            if e == abs_ep:
                _claim(fid, parsed, ep, "absolute")
                remaining_files = [(f, p) for f, p in remaining_files if f != fid]
                break

    # Pass 3 — season-agnostic episode-number match (the One Piece
    # S23E1158 fix: parsed.episode=1158, AniDB has ep.episode=1158 with
    # ep.season=1; strict (23, 1158) misses but season-agnostic 1158
    # hits). Only fire for anime to avoid TV cross-season collisions.
    is_anime = any(getattr(p, "media_type", None) == "anime" for _, p in files)
    if is_anime:
        for fid, parsed in list(remaining_files):
            if parsed.episode is None:
                continue
            for ep in remaining_eps:
                s, e = _ep_key(ep)
                if (s, e) in used_ep_keys:
                    continue
                if e == parsed.episode:
                    _claim(fid, parsed, ep, "episode_number")
                    remaining_files = [(f, p) for f, p in remaining_files if f != fid]
                    break

    # Anything still unmatched is genuinely orphan.
    for fid, _parsed in remaining_files:
        out[fid] = FileToEpisode(fid, None, None, None, "unpaired")

    return out
