"""Anime episode-numbering style toggle (`naming.anime_numbering`).

Seasonal (default) → `S04E05` inside Season folders (unchanged behavior).
Absolute → the series-wide number in a FLAT folder ("One Piece - 1071"), via
the `{{absx}}` token, which falls back to the SxE form when a show has no
absolute number (so a flat layout can't collide two seasons that both have an
episode 5).

`select_template` is the single modular switch: a (media_type, style) pair maps
to a profile field, transparently falling back to the base template when a
profile lacks that variant.
"""

from __future__ import annotations

from kira.parser import parse_filename
from kira.renamer.templates import (
    DEFAULT_PROFILES,
    NamingProfile,
    _build_ctx,
    format_target_path,
    select_template,
)

ROOT = "/lib"


def _render(filename: str, *, profile: str = "Plex", anime_numbering: str = "seasonal") -> str:
    p = parse_filename(filename)
    tgt = format_target_path(
        p, ROOT, DEFAULT_PROFILES[profile],
        library_title=p.title, library_year=p.year,
        anime_numbering=anime_numbering,
    )
    return tgt.as_posix()


# ── select_template: the modular switch ──────────────────────────────────
def test_select_seasonal_returns_base_anime():
    prof = DEFAULT_PROFILES["Plex"]
    assert select_template(prof, "anime", anime_numbering="seasonal") == prof.anime


def test_select_absolute_returns_variant():
    prof = DEFAULT_PROFILES["Plex"]
    assert select_template(prof, "anime", anime_numbering="absolute") == prof.anime_absolute
    assert "{{absx}}" in prof.anime_absolute


def test_select_absolute_falls_back_when_variant_missing():
    # A custom profile that didn't define an absolute variant → base `anime`.
    prof = NamingProfile(movie="m", tv="t", anime="a", music="mu")
    assert prof.anime_absolute is None
    assert select_template(prof, "anime", anime_numbering="absolute") == "a"


def test_select_non_anime_ignores_numbering():
    prof = DEFAULT_PROFILES["Plex"]
    assert select_template(prof, "tv", anime_numbering="absolute") == prof.tv
    assert select_template(prof, "movie", anime_numbering="absolute") == prof.movie


# ── {{absx}} token: absolute-or-SxE, collision-safe ──────────────────────
def test_absx_uses_absolute_when_present():
    p = parse_filename("[SubsPlease] One Piece - 1071 (1080p).mkv")
    ctx = _build_ctx(p, p.title or "", p.year)
    assert ctx["absx"] == "1071"


def test_absx_pads_short_absolute():
    p = parse_filename("[Moozzi2] Shingeki no Kyojin - 60 (BD 1080p).mkv")
    ctx = _build_ctx(p, p.title or "", p.year)
    assert ctx["absx"] == "060"          # :03d padding


def test_absx_falls_back_to_sxe_without_absolute():
    p = parse_filename("Some.Show.S02E05.1080p.mkv")
    ctx = _build_ctx(p, p.title or "", p.year)
    assert p.absolute_episode is None
    assert ctx["absx"] == "S02E05"       # NOT a bare "05" that could collide


# ── format_target_path end-to-end ────────────────────────────────────────
def test_absolute_renders_flat_with_absolute_number():
    out = _render("[SubsPlease] One Piece - 1071 (1080p).mkv", anime_numbering="absolute")
    assert "One Piece - 1071" in out
    assert "Season" not in out           # flat — no Season folder


def test_seasonal_keeps_season_folder_and_sxe():
    out = _render("[SubsPlease] One Piece - 1071 (1080p).mkv", anime_numbering="seasonal")
    assert "Season 01" in out
    assert "S01E1071" in out


def test_default_is_seasonal_unchanged():
    # No explicit arg → seasonal (the documented default; existing behavior).
    assert _render("[SubsPlease] One Piece - 1071 (1080p).mkv") == \
        _render("[SubsPlease] One Piece - 1071 (1080p).mkv", anime_numbering="seasonal")


def test_kodi_absolute_variant_also_flat():
    out = _render("[SubsPlease] One Piece - 1071 (1080p).mkv", profile="Kodi", anime_numbering="absolute")
    assert "One Piece - 1071" in out
    assert "/S01/" not in out and "Season" not in out
