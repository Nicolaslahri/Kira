"""Phase 4 — episode-list validation helpers.

the reference renamer's model is "parse loosely → resolve against the episode database":
once a series is matched, the file is checked against that series' actual
episode list, and the series match is reconsidered when the episode can't
exist. Kira already does this for AniDB via the count-based
``EpisodeCountSanityMetric`` (+ cour routing + the absolute→AID reroute).
The documented gap is *western TV*: a TVDB/TMDB candidate whose ``(season,
episode)`` simply doesn't exist still wins, silently.

These are the PURE pieces of that gate — no HTTP, fully unit-testable. The
async probing that fetches alternate candidates' episode lists lives in
``api/scans.py`` (it needs the provider registry), but the coverage math
and the promotion decision live here so they can be tested in isolation.
"""

from __future__ import annotations

# Coverage thresholds (see _match_cluster's gate for how they're used):
#   FLOOR    — below this, the top candidate is "suspicious" and we probe
#              alternates. 0.34 ≈ "fewer than a third of the cluster's
#              episodes exist in this candidate" — a strong wrong-series hint
#              for western TV (which has no absolute-numbering excuse).
#   PROMOTE  — an alternate must clear this absolute coverage to be trusted.
#   MARGIN   — and it must beat the incumbent by at least this much, so we
#              never flip on a coin-toss difference.
COVERAGE_FLOOR = 0.34
COVERAGE_PROMOTE = 0.67
COVERAGE_MARGIN = 0.34


def episode_exists(
    by_key: dict[tuple[int, int], str | None],
    season: int | None,
    episode: int | None,
) -> bool:
    """True when (season, episode) — or the season-agnostic (1, episode)
    fallback — is present in the provider's episode list.

    The (1, episode) fallback mirrors ``_lookup_episode_title``: AniDB returns
    everything as season 1, and some providers don't model the user's folder
    season. ``None`` season is treated as season 1.
    """
    if episode is None:
        return False
    s = season if season is not None else 1
    return (s, episode) in by_key or (1, episode) in by_key


def coverage(
    file_episodes: list[tuple[int | None, int | None]],
    by_key: dict[tuple[int, int], str | None],
) -> float:
    """Fraction of the cluster's files whose episode exists in ``by_key``.

    ``file_episodes`` is ``[(season, episode), …]`` per file. Files with no
    episode number are ignored (they can't be validated). Returns 1.0 for an
    empty/episode-less cluster (nothing to disprove) so the gate never fires
    on missing data.
    """
    total = 0
    have = 0
    for season, episode in file_episodes:
        if episode is None:
            continue
        total += 1
        if episode_exists(by_key, season, episode):
            have += 1
    return (have / total) if total else 1.0


def should_promote(top_cov: float, alt_cov: float) -> bool:
    """Decide whether an alternate (alt_cov) should replace the incumbent
    (top_cov). Requires the incumbent to be below FLOOR, the alternate to
    clear PROMOTE, and the alternate to beat the incumbent by MARGIN."""
    return (
        top_cov < COVERAGE_FLOOR
        and alt_cov >= COVERAGE_PROMOTE
        and alt_cov >= top_cov + COVERAGE_MARGIN
    )
