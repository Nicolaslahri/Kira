"""Pass 7 #15 — multi-disc movie handling."""

from __future__ import annotations

from kira.parser import parse_filename
from kira.parser.parser import _extract_disc
from kira.renamer import DEFAULT_PROFILES, format_target_path
from kira.parser import ParsedFile


# ── _extract_disc (pure) ─────────────────────────────────────────────────

def test_extract_disc_cd() -> None:
    assert _extract_disc("Inception (2010) CD1") == ("Inception (2010)", 1)


def test_extract_disc_disc_word() -> None:
    assert _extract_disc("Some Film Disc 2") == ("Some Film", 2)


def test_extract_disc_disk_spelling() -> None:
    assert _extract_disc("Old War Movie Disk 1") == ("Old War Movie", 1)


def test_extract_disc_none_when_absent() -> None:
    assert _extract_disc("Inception (2010)") == ("Inception (2010)", None)


def test_part_is_not_a_disc() -> None:
    # "Part 1" is a real title component — must NOT be eaten as a disc.
    title, disc = _extract_disc("Harry Potter and the Deathly Hallows Part 1")
    assert disc is None
    assert "Part 1" in title


# ── parse_filename integration ───────────────────────────────────────────

def test_parse_multidisc_movie() -> None:
    p = parse_filename("Inception (2010) CD1.mkv")
    assert p.media_type == "movie"
    assert p.disc == 1
    assert p.year == 2010
    assert "cd1" not in p.title.lower()


def test_parse_second_disc() -> None:
    p = parse_filename("The Lord of the Rings (2003) Disc 2.mkv")
    assert p.disc == 2
    assert "disc" not in p.title.lower()


def test_parse_non_disc_movie_unaffected() -> None:
    p = parse_filename("The Matrix 1999 1080p BluRay.mkv")
    assert p.disc is None


def test_tv_episode_never_gets_disc() -> None:
    # disc is movie-only — a TV file is never assigned one.
    p = parse_filename("Breaking Bad S01E05 720p.mkv")
    assert p.disc is None


# ── template rendering ───────────────────────────────────────────────────

def test_multidisc_renders_distinct_paths() -> None:
    profile = DEFAULT_PROFILES["Plex"]
    p1 = ParsedFile(original_filename="m.mkv", media_type="movie", title="Inception", year=2010, disc=1, quality="1080p")
    p2 = ParsedFile(original_filename="m.mkv", media_type="movie", title="Inception", year=2010, disc=2, quality="1080p")
    t1 = format_target_path(p1, "/lib", profile, library_title="Inception", library_year=2010)
    t2 = format_target_path(p2, "/lib", profile, library_title="Inception", library_year=2010)
    assert str(t1) != str(t2)
    assert "cd1" in str(t1).lower()
    assert "cd2" in str(t2).lower()


def test_single_disc_movie_has_no_disc_marker() -> None:
    profile = DEFAULT_PROFILES["Plex"]
    p = ParsedFile(original_filename="m.mkv", media_type="movie", title="Inception", year=2010, quality="1080p")
    t = format_target_path(p, "/lib", profile, library_title="Inception", library_year=2010)
    assert "cd" not in str(t).lower().replace("inception", "")  # no stray cd marker
