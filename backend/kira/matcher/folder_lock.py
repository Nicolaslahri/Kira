"""Phase 11 — folder / batch series locking (pure decision logic).

the reference renamer determines the series ONCE per folder, then forces every file in
that folder to that series. A single weird filename can't escape into another
show. Kira clusters by a per-file ``series_key`` derived from each file's
parsed title — so when the parser mangles one filename ("…Final Season Part
3-01", "Special 05"), that file gets a DIFFERENT key and splinters into its
own cluster (or matches the franchise's base AID). This is exactly why the
Attack on Titan files scattered across the S1 / Final Season / Special cards.

This module holds the PURE majority-vote decision (no DB, fully testable).
``api/scans.py`` builds the per-folder file lists, calls ``compute_relocks``,
and writes the returned ``series_key`` changes back.

Design choices (conservative, to never force-merge a genuinely mixed folder):
  - Only TV/anime files vote and are relocked (movies legitimately share
    folders; music keys have a different shape).
  - The majority is taken over ``(media_type, title, disambig)`` IGNORING
    season, and each relocked file KEEPS its own season. So a series-root
    folder containing S1 + S2 never collapses into one cluster — only the
    title is unified, the season split survives.
  - A strict majority is required (``> 50%`` of the folder's keyed TV/anime
    files, and at least ``MIN_AGREE``). A 2-vs-2 folder does nothing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

# Minimum number of files that must agree on a series before we relock the
# outliers to it. 2 is enough — a lone correctly-named pair pulls in a
# mangled third — but it must STILL clear the >50% majority test below, so a
# 2-vs-2 split never triggers.
MIN_AGREE = 2


@dataclass
class FolderFile:
    fid: int
    media_type: str
    series_key: str | None
    season: int | None  # parsed.season — recovers a season for null-key files


def split_series_key(key: str | None) -> tuple[str, str, str, str] | None:
    """Split a TV/anime ``series_key`` into (type, title, season, disambig).

    Returns ``None`` for a null key or any key that isn't the 4-part TV/anime
    shape (e.g. a music key ``music|artist|album``).
    """
    if not key:
        return None
    parts = key.split("|")
    if len(parts) != 4 or parts[0] not in ("tv", "anime"):
        return None
    return (parts[0], parts[1], parts[2], parts[3])


def compute_relocks(members: list[FolderFile]) -> dict[int, str]:
    """Return ``{fid: new_series_key}`` for files in ONE leaf folder that
    should be relocked to the folder's majority series.

    Empty dict when there's no clear majority (mixed folder, too few files,
    or every file already agrees).
    """
    if len(members) < 2:
        return {}

    # Vote: (media_type, title, disambig) across KEYED TV/anime files.
    triples: list[tuple[str, str, str]] = []
    for m in members:
        if m.media_type not in ("tv", "anime"):
            continue
        parts = split_series_key(m.series_key)
        if parts is None:
            continue
        triples.append((parts[0], parts[1], parts[3]))
    if not triples:
        return {}

    maj_triple, maj_count = Counter(triples).most_common(1)[0]
    n = len(triples)
    # Strict majority: more than half of the keyed votes, and ≥ MIN_AGREE.
    if maj_count < MIN_AGREE or maj_count * 2 <= n:
        return {}

    maj_type, maj_title, maj_disambig = maj_triple
    out: dict[int, str] = {}
    for m in members:
        # Only relock files of the SAME media type as the majority.
        if m.media_type != maj_type:
            continue
        parts = split_series_key(m.series_key)
        cur_triple = (parts[0], parts[1], parts[3]) if parts else None
        if cur_triple == maj_triple:
            continue  # already on the majority series
        # Preserve this file's own season (key season if it has one, else
        # the parsed season for a null-key file) so we never merge across
        # seasons — only the title/disambig is unified.
        if parts is not None:
            season_str = parts[2]
        else:
            season_str = str(m.season) if m.season is not None else ""
        new_key = f"{maj_type}|{maj_title}|{season_str}|{maj_disambig}"
        if new_key != m.series_key:
            out[m.fid] = new_key
    return out
