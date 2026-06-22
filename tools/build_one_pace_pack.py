#!/usr/bin/env python3
"""Build a Kira pack JSON from the One Pace for Plex repo's .nfo metadata.

Source: https://github.com/SpykerNZ/one-pace-for-plex (a Plex .nfo tree — one
season folder per One Pace arc, one `One Pace - SssEee - Title.nfo` per episode,
plus a root `tvshow.nfo` carrying the show plot + arc names via <namedseason>).

We map each Plex season → its One Pace arc, and each episode → an arc-relative
number, then emit a Kira pack whose per-episode `match` recognises the ORIGINAL
One Pace *release* filenames (e.g. `[One Pace][1-7] Romance Dawn 01 [1080p][CRC].mkv`)
via arc + arc_episode + a precise regex. No CRC32s are available in this repo, so
the arc-name signal is the strongest one we can derive.

Usage:
    python tools/build_one_pace_pack.py <path-to-one-pace-for-plex> <out.json>
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _strip_season_prefix(name: str) -> str:
    """'1. Romance Dawn' -> 'Romance Dawn'."""
    return re.sub(r"^\s*\d+\.\s*", "", (name or "").strip()).strip()


def build(src_root: Path) -> dict:
    base = src_root / "One Pace" if (src_root / "One Pace").is_dir() else src_root
    tvshow = ET.parse(base / "tvshow.nfo").getroot()
    show_plot = (tvshow.findtext("plot") or "").strip()

    arc_by_season: dict[int, str] = {}
    for ns in tvshow.findall("namedseason"):
        try:
            arc_by_season[int(ns.get("number"))] = _strip_season_prefix(ns.text or "")
        except (TypeError, ValueError):
            continue

    def _episode_entry(nfo: Path, arc: str | None) -> dict | None:
        root = ET.parse(nfo).getroot()
        try:
            s = int(root.findtext("season"))
            e = int(root.findtext("episode"))
        except (TypeError, ValueError):
            return None
        ep: dict = {"season": s, "episode": e}
        title = (root.findtext("title") or "").strip()
        if title:
            ep["title"] = title
        plot = (root.findtext("plot") or "").strip()
        if plot:
            ep["overview"] = plot
        if arc:
            # Numbered arc: arc + arc_episode is the forgiving fallback; the
            # regex is the precise primary (arc name then the number).
            ep["match"] = {
                "arc": arc,
                "arc_episode": e,
                "regex": rf"(?i){re.escape(arc)}\s+0*{e}(?!\d)",
            }
        elif title:
            # Special (season 0): match by its standalone title. Trailing
            # parentheticals ("(April Fools 2017)") aren't in release filenames,
            # so strip them for the matcher. Specials are appended LAST so a
            # numbered arc always wins a regex tie (e.g. "Gaimon 01" → the arc).
            clean = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
            if clean:
                ep["match"] = {"regex": rf"(?i){re.escape(clean)}", "release": clean}
        return ep

    episodes: list[dict] = []
    season_dirs = sorted(
        (d for d in base.glob("Season *") if d.is_dir()),
        key=lambda p: int(p.name.split()[-1]),
    )
    for sdir in season_dirs:
        snum = int(sdir.name.split()[-1])
        arc = arc_by_season.get(snum)
        if not arc:  # fall back to season.nfo <title>
            sn = sdir / "season.nfo"
            if sn.exists():
                arc = _strip_season_prefix(ET.parse(sn).getroot().findtext("title") or "")
        for nfo in sorted(sdir.glob("One Pace - S*E*.nfo")):
            ep = _episode_entry(nfo, arc)
            if ep is not None:
                episodes.append(ep)

    # Specials (season 0) LAST — title-matched, never out-prioritise an arc.
    specials_dir = base / "Specials"
    if specials_dir.is_dir():
        for nfo in sorted(specials_dir.glob("One Pace - S*E*.nfo")):
            ep = _episode_entry(nfo, None)
            if ep is not None:
                episodes.append(ep)

    return {
        "kira_pack": 1,
        "id": "one-pace",
        "name": "One Pace",
        "media_type": "anime",
        "show": {
            "title": "One Pace",
            "aliases": ["One Pace (One Piece fan edit)"],
            "year": 1999,
            "poster_url": None,
            "overview": show_plot,
        },
        "match": {
            "titles": ["One Pace"],
            "release_groups": ["One Pace"],
            "filename_regex": r"(?i)\bone[ ._-]?pace\b",
        },
        "episodes": episodes,
    }


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    out = Path(sys.argv[2])
    pack = build(src)
    out.write_text(json.dumps(pack, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out} — {len(pack['episodes'])} episodes, "
          f"{len({e['season'] for e in pack['episodes']})} seasons, "
          f"{out.stat().st_size // 1024} KiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
