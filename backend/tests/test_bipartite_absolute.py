"""Bipartite absolute-episode pairing — the One Piece S23E1156→ep1 collapse fix.

Root cause (verified end-to-end against live TVDB + the live DB): the provider
per-season list numbers episodes LOCALLY (One Piece S23 → episode 1..13) but
carries absolute_number 1156..1168. `_to_dicts` used to STRIP absolute_number,
so the only bipartite pass that could fire was the title pass, and `_claim`
stored the LOCAL index (1156→1). The fix preserves absolute_number and stores
the file's REAL number when paired by absolute identity.

These are pure-function regression locks (no DB / no network); episode lists
hardcode a deterministic slice (local 1..13, absolute 1156..1168).
"""
from __future__ import annotations

from types import SimpleNamespace

from kira.matcher.bipartite import assign_files_to_episodes


def _pf(season, episode, abs_ep=None, media_type="anime", guess=None, air_date=None):
    return SimpleNamespace(
        season=season, episode=episode, absolute_episode=abs_ep,
        media_type=media_type, episode_title_guess=guess, air_date=air_date,
    )


def _ep(season, episode, title, absolute_number=None, air_date=None):
    return {"season": season, "episode": episode, "title": title,
            "absolute_number": absolute_number, "air_date": air_date}


# One Piece S23 list AFTER the season=1 rewrite: season=1, local episode 1..13,
# absolute_number 1156..1168, real Elbaf-arc titles for the first five.
_TITLES = [
    "The Long-sought Elbaph! The Big Reunion Banquet",
    "Nami in a Fix! An Adventure in Block Kingdom",
    "A Quest in the Land of Mystery! The Secret of the Sun God",
    "Destroy the Miniature Garden - Escape Block Kingdom!",
    "An Encounter on a Snowfield - Loki, the Accursed Prince",
]
def _op_list():
    return [_ep(1, n, _TITLES[n - 1] if n <= 5 else f"Elbaf episode {n}",
                absolute_number=1155 + n) for n in range(1, 14)]


def test_longrunner_sxe_keeps_absolute_episode():
    """THE bug: S23E1156..1160 (episode=1156.., absolute_episode=None) must
    store 1156..1160, NOT the local index 1..5."""
    eps = _op_list()
    files = [(100 + i, _pf(23, 1156 + i, abs_ep=None, guess=_TITLES[i])) for i in range(5)]
    out = assign_files_to_episodes(files, eps)
    got = [out[100 + i].episode_number for i in range(5)]
    assert got == [1156, 1157, 1158, 1159, 1160], got
    for i in range(5):
        assert out[100 + i].matched_via == "absolute_sxe"
        assert out[100 + i].episode_title == eps[i]["title"]   # title still correct


def test_pass3_skips_multiseason_to_avoid_cross_season_hijack():
    """Pass 3 (season-agnostic) must NOT fire on a MULTI-season provider list. A
    file labelled for a season the provider doesn't have ("S05E03" when the show
    has S01-S02) would otherwise grab an unrelated season's identically-numbered
    episode (→ S01E03). On a multi-season list it stays UNPAIRED (the user fixes
    it); real multi-season absolute numbering is owned by Pass 3.5."""
    multi = [_ep(s, e, f"S{s}E{e}", absolute_number=(s - 1) * 20 + e)
             for s in (1, 2) for e in range(1, 21)]
    files = [(i, _pf(5, e, abs_ep=None)) for i, e in ((1, 3), (2, 7), (3, 11))]
    out = assign_files_to_episodes(files, multi)
    assert all(out[i].matched_via == "unpaired" for i in (1, 2, 3))


def test_pass3_still_rescues_flat_list():
    """The flat-AniDB rescue is PRESERVED: on a single-season list (local episode
    == absolute), a season-agnostic SxE number ("S23E13") still pairs by episode."""
    flat = [_ep(1, n, f"ep{n}", absolute_number=n) for n in range(1, 16)]
    files = [(i, _pf(23, e, abs_ep=None)) for i, e in ((1, 13), (2, 14), (3, 15))]
    out = assign_files_to_episodes(files, flat)
    assert all(out[i].matched_via == "episode_number" and out[i].episode_season == 1 for i in (1, 2, 3))
    assert sorted(out[i].episode_number for i in (1, 2, 3)) == [13, 14, 15]


def test_bracket_absolute_stores_absolute():
    """Bracket/dash form sets parsed.absolute_episode → Pass 2 must store the
    absolute (1156..1158), not the local index 1..3."""
    eps = _op_list()
    files = [(200 + i, _pf(23, 1156 + i, abs_ep=1156 + i, guess=None)) for i in range(3)]
    out = assign_files_to_episodes(files, eps)
    assert [out[200 + i].episode_number for i in range(3)] == [1156, 1157, 1158]
    for i in range(3):
        assert out[200 + i].matched_via == "absolute"


def test_percour_local_numbering_unaffected():
    """Frieren-S2 shape: a real 12-ep cour numbered LOCAL 1..12 / absolute
    17..28. Local-numbered files (S2E03/05/12) must keep their LOCAL number —
    Pass 3 wins and the absolute pass is skipped (number <= max local)."""
    eps = [_ep(1, n, f"Cour ep {n}", absolute_number=16 + n) for n in range(1, 13)]
    files = [(300 + n, _pf(2, n, abs_ep=None)) for n in (3, 5, 12)]
    out = assign_files_to_episodes(files, eps)
    assert out[303].episode_number == 3
    assert out[305].episode_number == 5
    assert out[312].episode_number == 12


def test_single_season_anime_noop():
    """Single-season list where absolute_number == local episode → the new
    pass is a strict no-op (guard: absolute_number != episode)."""
    eps = [_ep(1, n, f"S1 ep {n}", absolute_number=n) for n in range(1, 13)]
    files = [(400 + n, _pf(1, n, abs_ep=None)) for n in (1, 2, 3)]
    out = assign_files_to_episodes(files, eps)
    assert [out[400 + n].episode_number for n in (1, 2, 3)] == [1, 2, 3]


def test_western_tv_exact_unchanged():
    """Non-anime cluster: the anime-only passes never fire; exact (s,e) wins."""
    eps = [_ep(1, n, f"GoT ep {n}", absolute_number=n) for n in range(1, 11)]
    files = [(500, _pf(1, 9, media_type="tv")),
             (501, _pf(1, 10, media_type="tv")),
             (502, _pf(1, 8, media_type="tv"))]
    out = assign_files_to_episodes(files, eps)
    assert out[500].episode_number == 9 and out[500].matched_via == "exact"
    assert out[501].episode_number == 10
    assert out[502].episode_number == 8


def test_absolute_pass_skipped_when_number_within_local_range():
    """The max_local guard: a low number that merely COLLIDES with some
    episode's absolute_number (but is within the local range) must NOT be
    hijacked by the absolute pass — it stays local or unpaired, never wrong."""
    # local 1..12, absolute 5..16 → ep with absolute_number==5 is local episode 1.
    eps = [_ep(1, n, f"ep {n}", absolute_number=4 + n) for n in range(1, 13)]
    # A genuine local S2E05 → Pass 3 matches local ep 5, stores 5 (NOT hijacked
    # to the ep whose absolute_number==5, which is local 1).
    out = assign_files_to_episodes([(600, _pf(2, 5, abs_ep=None))], eps)
    assert out[600].episode_number == 5


# ── Season-0 isolation (Specials/OVAs must not hijack main episodes) ──────────
def test_season0_special_not_paired_to_main_episode():
    # 3-file anime cluster; one is a Special (S00E05). The provider list has
    # ONLY main-run episodes (season 1, eps 1..13) — no Season-0 entry. The
    # special must NOT be season-agnostically paired to main episode 5.
    files = [(1, _pf(1, 4)), (2, _pf(1, 6)), (3, _pf(0, 5))]
    eps = [_ep(1, e, f"Ep {e}") for e in range(1, 14)]
    out = assign_files_to_episodes(files, eps)
    assert out[1].matched_via == "exact" and out[1].episode_number == 4
    assert out[2].matched_via == "exact" and out[2].episode_number == 6
    assert out[3].matched_via == "unpaired" and out[3].episode_number is None


def test_season0_special_pairs_to_real_season0_entry():
    # When the provider DOES carry the special as a Season-0 entry, the exact
    # (0, 5) pass pairs it (requires _ep_key preserving season 0).
    files = [(1, _pf(1, 4)), (2, _pf(1, 6)), (3, _pf(0, 5))]
    eps = [_ep(1, e, f"Ep {e}") for e in range(1, 14)] + [_ep(0, 5, "OVA 5")]
    out = assign_files_to_episodes(files, eps)
    assert out[3].matched_via == "exact"
    assert out[3].episode_season == 0 and out[3].episode_number == 5
