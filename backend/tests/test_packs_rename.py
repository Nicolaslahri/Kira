"""A pack match renames into the pack's OWN season/episode layout, verbatim.

The /rename endpoint's pack branch pins ``parsed.episode`` + ``season_override``
to the stored pack values and (because the AniDB blocks are gated on
``provider == "anidb"``) never routes a pack row through ScudLee. This test
exercises the downstream render: given the values the pack branch produces, the
path lands in ``One Pace/Season 01/... S01E05 ...`` — the pack author's layout,
not a re-derived TVDB season.
"""
from __future__ import annotations

from kira.parser.parser import ParsedFile
from kira.renamer.templates import DEFAULT_PROFILES, format_target_path

ROOT = "/lib"


def test_pack_numbers_render_verbatim_seasonal():
    # What the pack branch sets: episode = pack episode, season via override.
    p = ParsedFile(
        original_filename="[One Pace] Romance Dawn 05 [A1B2C3D4].mkv",
        media_type="anime", title="One Pace", year=1999,
        episode=5, release_group="One Pace",
    )
    tgt = format_target_path(
        p, ROOT, DEFAULT_PROFILES["Plex"],
        library_title="One Pace", library_year=1999,
        episode_title="Romance Dawn 05", season_override=1,
        anime_numbering="seasonal",
    ).as_posix()
    assert "One Pace" in tgt
    assert "Season 01" in tgt
    assert "S01E05" in tgt


def test_pack_arc_as_season_two():
    # A pack that lays arcs out as seasons → Season 02 renders straight through.
    p = ParsedFile(
        original_filename="[One Pace] Orange Town 01.mkv",
        media_type="anime", title="One Pace", year=1999,
        episode=1, release_group="One Pace",
    )
    tgt = format_target_path(
        p, ROOT, DEFAULT_PROFILES["Plex"],
        library_title="One Pace", season_override=2,
        anime_numbering="seasonal",
    ).as_posix()
    assert "Season 02" in tgt
    assert "S02E01" in tgt
