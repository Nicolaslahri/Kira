"""Table-driven parser tests. Covers the plan examples + real-world filenames
seen during the Z:\\media scan.
"""

from __future__ import annotations

import pytest

from kira.parser import parse_filename

# (filename, expected partial dict). Only keys we list are checked, so adding
# new fields to ParsedFile doesn't break old tests.
CASES: list[tuple[str, dict]] = [
    # ── Movies ────────────────────────────────────────────────────────────
    ("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv",
     {"media_type": "movie", "title": "The Matrix", "year": 1999,
      "quality": "1080p", "source": "BluRay", "codec": "x264", "release_group": "GROUP"}),

    ("Inception (2010) [1080p].mkv",
     {"media_type": "movie", "title": "Inception", "year": 2010, "quality": "1080p"}),

    ("1917 2019 1080p WEB-DL.mkv",
     {"media_type": "movie", "title": "1917", "year": 2019, "source": "WEB-DL"}),

    ("oppenheimer.2023.imax.2160p.uhd.bluray.x265-pmp.mkv",
     {"media_type": "movie", "title": "oppenheimer", "year": 2023,
      "quality": "2160p", "release_group": "pmp"}),

    # ── Standard TV ───────────────────────────────────────────────────────
    ("Breaking.Bad.S01E05.720p.WEB-DL.mkv",
     {"media_type": "tv", "title": "Breaking Bad", "season": 1, "episode": 5,
      "quality": "720p", "source": "WEB-DL", "release_group": None}),

    ("the.office.us.s02e15.720p.mkv",
     {"media_type": "tv", "title": "the office us", "season": 2, "episode": 15}),

    ("Game of Thrones - 3x09 - The Rains of Castamere.mkv",
     {"media_type": "tv", "title": "Game of Thrones", "season": 3, "episode": 9}),

    # Multi-episode
    ("Severance.S01E01-E03.1080p.mkv",
     {"media_type": "tv", "season": 1, "episode": 1, "episode_end": 3}),

    # ── Anime ─────────────────────────────────────────────────────────────
    ("[SubsPlease] Frieren - Beyond Journey's End - 28 (1080p) [F2A7B3D9].mkv",
     {"media_type": "anime", "release_group": "SubsPlease",
      "absolute_episode": 28, "episode": 28}),

    ("[ToonsHub] BLEACH Thousand-Year Blood War - S17E25 (JAP 2160p x264 AAC) [Multi-Subs].mkv",
     {"media_type": "anime", "release_group": "ToonsHub",
      "season": 17, "episode": 25, "title": "BLEACH Thousand-Year Blood War"}),

    ("[Lazier] Bleach Thousand-Year Blood War-38 [WEB 1080p AAC] [CD3833B0].mkv",
     {"media_type": "anime", "release_group": "Lazier",
      "absolute_episode": 38, "episode": 38,
      "title": "Bleach Thousand-Year Blood War"}),

    # ── Music ─────────────────────────────────────────────────────────────
    ("03 - Black Star.flac",
     {"media_type": "music", "track": 3, "track_title": "Black Star"}),

    ("fleetwood_mac_-_rumours_-_05_-_go_your_own_way.mp3",
     {"media_type": "music", "artist": "Fleetwood Mac", "album": "Rumours",
      "track": 5, "track_title": "go your own way"}),

    # ── Garbage / unmatchable ────────────────────────────────────────────
    ("IMG_9482.mov",
     {"media_type": "movie", "season": None, "episode": None, "year": None}),

    # Lone single-word file with no markers — parser shouldn't crash.
    ("Movie.Name.mkv",
     {"media_type": "movie", "title": "Movie Name", "year": None}),
]


@pytest.mark.parametrize("filename,expected", CASES, ids=[c[0] for c in CASES])
def test_parse(filename: str, expected: dict) -> None:
    actual = parse_filename(filename)
    for key, want in expected.items():
        got = getattr(actual, key)
        assert got == want, (
            f"{filename}: field {key!r} expected {want!r}, got {got!r} "
            f"(full parse: {actual.to_dict()})"
        )


def test_year_is_never_in_title() -> None:
    """Regression: the year always lives in `year`, never inside `title`."""
    pf = parse_filename("Anora.2024.HDR.HEVC-RELEASE.mkv")
    assert pf.year == 2024
    assert "2024" not in pf.title


def test_web_dl_does_not_become_release_group() -> None:
    """Regression: trailing -DL from WEB-DL must not be captured as group."""
    pf = parse_filename("Breaking.Bad.S01E05.720p.WEB-DL.mkv")
    assert pf.release_group is None
    assert pf.source == "WEB-DL"


def test_anime_trailing_digits_not_release_group() -> None:
    """Regression: 'War-38' is absolute episode 38, not group '38'."""
    pf = parse_filename("[Lazier] Bleach Thousand-Year Blood War-38 [1080p].mkv")
    assert pf.release_group == "Lazier"
    assert pf.absolute_episode == 38


def test_compressed_3digit_rejects_years() -> None:
    """1900-2100 in compressed form (e.g. '2024') would imply S20E24 — must reject."""
    pf = parse_filename("Some.Movie.2024.mkv")
    # year should win, not be coerced to S20E24
    assert pf.year == 2024
    assert pf.season is None
    assert pf.episode is None


def test_audio_extension_routes_to_music() -> None:
    pf = parse_filename("track 07.m4a")
    assert pf.media_type == "music"


def test_parent_folder_path_hints_anime() -> None:
    """An /anime/ ancestor classifies as anime even without [Group] tag."""
    pf = parse_filename("Some Show - 12.mkv", parent_path="/media/downloads/anime")
    assert pf.media_type == "anime"
