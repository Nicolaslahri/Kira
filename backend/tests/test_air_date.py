"""Phase 9 — date-based episode matching."""

from __future__ import annotations

from kira.matcher.bipartite import assign_files_to_episodes
from kira.parser import ParsedFile, parse_filename


def test_parse_date_named_file() -> None:
    pf = parse_filename("The Daily Show 2020.01.15.mkv")
    assert pf.air_date == "2020-01-15"
    assert pf.title == "The Daily Show"      # date cut from the title
    assert pf.year == 2020                   # year seeded from the date
    assert pf.season is None and pf.episode is None
    assert pf.media_type == "tv"


def test_dash_separated_date() -> None:
    pf = parse_filename("Conan 2019-11-20 1080p WEB.mkv")
    assert pf.air_date == "2019-11-20"


def test_sxe_present_means_date_is_just_a_tag() -> None:
    pf = parse_filename("Show S01E05 2020.01.15.mkv")
    assert pf.season == 1 and pf.episode == 5
    assert pf.air_date is None  # SxE is the primary numbering, date ignored


def test_bipartite_air_date_pass() -> None:
    files = [
        (1, ParsedFile(original_filename="a.mkv", media_type="tv",
                       title="The Daily Show", air_date="2020-01-15")),
    ]
    eps = [
        {"season": 2020, "episode": 14, "title": "Mon", "air_date": "2020-01-14"},
        {"season": 2020, "episode": 15, "title": "Tue", "air_date": "2020-01-15"},
        {"season": 2020, "episode": 16, "title": "Wed", "air_date": "2020-01-16"},
    ]
    out = assign_files_to_episodes(files, eps)
    assert out[1].matched_via == "air_date"
    assert out[1].episode_title == "Tue"
