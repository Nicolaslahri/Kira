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

    # ── Phase 1: named-season / part / dash-episode ───────────────────────
    # "Season N-MM" — the bare "2-06" matched no SxE pattern before PA.
    ("Attack on Titan Season 2-06.mkv",
     {"media_type": "tv", "title": "Attack on Titan", "season": 2, "episode": 6}),

    ("Attack.on.Titan.Season.2-06.1080p.mkv",
     {"media_type": "tv", "title": "Attack on Titan", "season": 2, "episode": 6}),

    # "Part N - MM" anime cour — episode + cour captured, "Final Season"
    # qualifier kept in the title (it's AniDB's real title for the AID).
    ("[SubsPlease] Shingeki no Kyojin - The Final Season Part 3 - 01 (1080p).mkv",
     {"media_type": "anime", "release_group": "SubsPlease",
      "episode": 1, "cour": 3, "named_season": "final", "absolute_episode": None}),

    # ── Phase 2: specials / OVA → season 0 ────────────────────────────────
    ("Bleach Special 05.mkv",
     {"media_type": "tv", "title": "Bleach", "season": 0, "episode": 5}),

    ("[SubsPlease] Attack on Titan OVA (1080p).mkv",
     {"media_type": "anime", "release_group": "SubsPlease",
      "title": "Attack on Titan", "season": 0, "episode": 1}),

    # ── Music ─────────────────────────────────────────────────────────────
    ("03 - Black Star.flac",
     {"media_type": "music", "track": 3, "track_title": "Black Star"}),

    ("fleetwood_mac_-_rumours_-_05_-_go_your_own_way.mp3",
     {"media_type": "music", "artist": "Fleetwood Mac", "album": "Rumours",
      "track": 5, "track_title": "go your own way"}),

    # ── Garbage / unmatchable ────────────────────────────────────────────
    # A year-less, marker-less, path-less filename stays "unknown" ON PURPOSE
    # (see parser._classify): with no year/SxE/path signal we must NOT guess
    # "movie", or scene junk (IMG_9482.mov, video_final_v2.mkv) would be
    # confidently mislabeled and inflate match confidence. The matcher can
    # still try both movie + TV for an "unknown". Title/year are still parsed.
    ("IMG_9482.mov",
     {"media_type": "unknown", "season": None, "episode": None, "year": None}),

    # Lone single-word file with no markers — parser shouldn't crash; title is
    # still extracted, type stays "unknown" (no year/SxE/path to justify movie).
    ("Movie.Name.mkv",
     {"media_type": "unknown", "title": "Movie Name", "year": None}),
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


def test_expanded_fansub_group_classifies_anime() -> None:
    """Phase 10: a newly-added group tag is recognized as anime."""
    for grp in ("Ember", "Moozzi2", "GJM", "Yameii"):
        pf = parse_filename(f"[{grp}] Some Show - 05 [1080p].mkv")
        assert pf.media_type == "anime", f"{grp} should classify as anime"


# ── Phase 1 regressions ───────────────────────────────────────────────────


def test_season_dash_episode_no_longer_orphans() -> None:
    """'Season 2-06' must parse the episode; before PA it was episode=None."""
    pf = parse_filename("Attack on Titan Season 2-06.mkv")
    assert pf.season == 2
    assert pf.episode == 6
    # The named-season token must be cut out of the title, not left as junk.
    assert "season" not in pf.title.lower()


def test_part_dash_episode_captures_cour_no_spaces() -> None:
    """'Part 3-01' (no spaces around the dash) → episode=1, cour=3."""
    pf = parse_filename("Shingeki no Kyojin - The Final Season Part 3-01.mkv")
    assert pf.episode == 1
    assert pf.cour == 3
    # "Part 3-01" noise is cut, but "The Final Season" qualifier is kept.
    assert "part" not in pf.title.lower()
    assert "final season" in pf.title.lower()


def test_part_dash_episode_with_spaces() -> None:
    """'Part 3 - 01' (spaces) → episode=1, cour=3, no spurious absolute."""
    pf = parse_filename("[SubsPlease] Bleach - Part 2 - 05 (1080p).mkv")
    assert pf.episode == 5
    assert pf.cour == 2
    assert pf.absolute_episode is None


def test_part_dash_rejects_unpadded_single_digit() -> None:
    """'Part 2 - 5' (unpadded single digit) is movie-fragment-shaped; PB
    must not claim it as a cour episode (mirrors P4's padding guard)."""
    pf = parse_filename("Some Movie Part 2 - 5.mkv")
    assert pf.cour is None


def test_final_season_bare_e_keeps_qualifier() -> None:
    """'Final Season E07' → episode 7 via P5; 'Final Season' stays in title
    so it trigram-matches the provider's Final-Season AID directly."""
    pf = parse_filename("Attack on Titan - The Final Season E07.mkv")
    assert pf.episode == 7
    assert pf.named_season == "final"
    assert "final season" in pf.title.lower()


def test_trailing_part_suffix_stripped_when_no_episode() -> None:
    """A batch file with a trailing 'Part 3' but no episode number still has
    the part token stripped from the title and adopted as the cour."""
    pf = parse_filename("Attack on Titan The Final Season Part 3.mkv",
                        parent_path="/media/anime/Attack on Titan")
    assert pf.cour == 3
    assert "part" not in pf.title.lower()


def test_episode_title_guess_extracted() -> None:
    pf = parse_filename("Game of Thrones - 3x09 - The Rains of Castamere.mkv")
    assert pf.episode_title_guess == "The Rains of Castamere"


def test_episode_title_guess_none_when_no_title() -> None:
    pf = parse_filename("Breaking.Bad.S01E05.720p.WEB-DL.mkv")
    assert pf.episode_title_guess is None


def test_episode_title_guess_none_for_movie() -> None:
    pf = parse_filename("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv")
    assert pf.episode_title_guess is None


def test_named_season_dash_does_not_break_standard_sxe() -> None:
    """Regression: PA/PB must not steal a standard SxxExx match."""
    pf = parse_filename("Breaking.Bad.S02E06.1080p.mkv")
    assert pf.season == 2
    assert pf.episode == 6
    assert pf.cour is None


# ── Phase 2: specials / OVA / S00 ──────────────────────────────────────────


def test_special_word_routes_to_season_zero() -> None:
    pf = parse_filename("[Erai-raws] Mob Psycho 100 - Special 03 [1080p].mkv")
    assert pf.season == 0
    assert pf.episode == 3


def test_sp_abbreviation_routes_to_season_zero() -> None:
    pf = parse_filename("Naruto Shippuuden SP01.mkv")
    assert pf.season == 0
    assert pf.episode == 1


def test_oav_dash_number() -> None:
    pf = parse_filename("Some Show OAV-2.mkv")
    assert pf.season == 0
    assert pf.episode == 2


def test_s00e_standard_still_season_zero() -> None:
    """S00E05 is handled by P1 — confirm specials work via the standard path."""
    pf = parse_filename("The.Office.S00E05.1080p.mkv")
    assert pf.season == 0
    assert pf.episode == 5


def test_leading_special_is_not_an_episode() -> None:
    """'Special 26' (the movie) leads with the marker — must NOT be a special."""
    pf = parse_filename("Special 26 (2013) 1080p BluRay.mkv")
    assert pf.season != 0
    assert pf.season is None


def test_special_edition_is_not_a_special_episode() -> None:
    """'Special Edition' has no number after 'Special' — never a season-0 ep."""
    pf = parse_filename("Blade.Runner.Special.Edition.1992.1080p.mkv")
    assert pf.season != 0


def test_specialist_word_not_matched() -> None:
    """'Specialist' must not trip the \\bSpecials?\\b word boundary."""
    pf = parse_filename("The.Specialist.1994.720p.mkv")
    assert pf.season != 0


# ── Phase 3: title bracket cleanup ─────────────────────────────────────────


def test_dual_audio_bracket_dropped_from_title() -> None:
    pf = parse_filename("[Group] Demon Slayer [Dual Audio] S01E05.mkv")
    assert "dual audio" not in pf.title.lower()
    assert "demon slayer" in pf.title.lower()


def test_same_language_echo_bracket_dropped() -> None:
    """'Bleach [BLEACH]' — the all-caps echo is redundant, drop it."""
    pf = parse_filename("Bleach [BLEACH] S01E05.mkv")
    assert pf.title == "Bleach"


def test_real_subtitle_bracket_kept() -> None:
    """A genuine distinguishing subtitle must survive the cleanup."""
    pf = parse_filename("Fate stay night [Unlimited Blade Works] S01E05.mkv")
    assert "unlimited blade works" in pf.title.lower()


def test_empty_bracket_residue_removed() -> None:
    pf = parse_filename("Some Show [ ] S01E05.mkv")
    assert "[" not in pf.title and "]" not in pf.title
    assert "some show" in pf.title.lower()
