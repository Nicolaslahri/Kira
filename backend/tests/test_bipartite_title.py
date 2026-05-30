"""Phase 6 — bipartite episode-title pairing (Pass 4)."""

from __future__ import annotations

from kira.matcher.bipartite import assign_files_to_episodes
from kira.parser import ParsedFile


def _f(fid: int, **kw) -> tuple[int, ParsedFile]:
    return fid, ParsedFile(original_filename=f"{fid}.mkv", media_type="tv",
                           title="Game of Thrones", **kw)


def test_title_pass_resolves_sxe_less_file() -> None:
    files = [
        _f(1, season=1, episode=1),
        _f(2, season=1, episode=2),
        # No number — only an episode-title guess.
        _f(3, episode_title_guess="The Rains of Castamere"),
    ]
    eps = [
        {"season": 1, "episode": 1, "title": "Winter Is Coming"},
        {"season": 1, "episode": 2, "title": "The Kingsroad"},
        {"season": 1, "episode": 9, "title": "The Rains of Castamere"},
    ]
    out = assign_files_to_episodes(files, eps)
    assert out[1].matched_via == "exact"
    assert out[3].matched_via == "title"
    assert out[3].episode_number == 9
    assert out[3].episode_title == "The Rains of Castamere"


def test_title_pass_no_false_pair_on_weak_match() -> None:
    files = [
        _f(1, season=1, episode=1),
        _f(2, season=1, episode=2),
        _f(3, episode_title_guess="Completely Unrelated Words Here"),
    ]
    eps = [
        {"season": 1, "episode": 1, "title": "Winter Is Coming"},
        {"season": 1, "episode": 2, "title": "The Kingsroad"},
        {"season": 1, "episode": 9, "title": "The Rains of Castamere"},
    ]
    out = assign_files_to_episodes(files, eps)
    assert out[3].matched_via == "unpaired"  # below the 0.6 threshold
