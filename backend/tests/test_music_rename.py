"""Music rename (Phase 3) — the values the rename's music overlay produces render
into the music layout: ``Music/{artist}/{album}/{tn} - {title}``.

The overlay copies the MusicBrainz-corrected artist/album/title/track from the
Match onto the parsed copy; the TV/movie params (``library_title`` = the track
title, ``season_override`` = the disc) flow through but must NOT pollute the music
tokens (the music template uses {{artist}}/{{album}}/{{tn}}/{{title}}, never
{n}/{s2}). These tests pin that downstream render.
"""
from __future__ import annotations

from kira.parser.parser import ParsedFile
from kira.renamer.templates import DEFAULT_PROFILES, format_target_path

ROOT = "/lib"


def test_music_renders_corrected_metadata():
    # What the overlay sets on `parsed` from the Match: artist/album/track/title.
    p = ParsedFile(
        original_filename="01. One Time.flac",
        media_type="music", title="",
        artist="Daft Punk", album="Discovery", track=1, track_title="One More Time", year=2001,
    )
    tgt = format_target_path(
        p, ROOT, DEFAULT_PROFILES["Plex"],
        library_title="One More Time",  # selected.title (the TRACK title) as the {n} override
        library_year=2001,
        season_override=1,              # disc_no flows in as a "season" — music ignores it
    ).as_posix()
    assert "Daft Punk" in tgt
    assert "Discovery" in tgt
    assert "01 - One More Time.flac" in tgt   # padded track number + corrected title
    assert "/Music/" in tgt                   # under the music subfolder
    assert "Season" not in tgt                # the disc override must NOT make a Season dir


def test_show_title_override_does_not_clobber_album():
    # Guard the coincidence: library_title == the track title, but the album folder
    # must stay the ALBUM, never the title override.
    p = ParsedFile(
        original_filename="03. Down To Earth.flac",
        media_type="music", title="",
        artist="Justin Bieber", album="My World", track=3, track_title="Down to Earth", year=2009,
    )
    tgt = format_target_path(
        p, ROOT, DEFAULT_PROFILES["Plex"],
        library_title="Down to Earth", library_year=2009, season_override=1,
    ).as_posix()
    assert "Justin Bieber/My World" in tgt          # album folder intact
    assert "03 - Down to Earth.flac" in tgt
