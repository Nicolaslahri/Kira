"""Phase 11 — folder/batch series lock (pure decision tests, no DB)."""

from __future__ import annotations

from kira.matcher.folder_lock import FolderFile, compute_relocks, split_series_key


def _ff(fid, key, media_type="anime", season=None):
    return FolderFile(fid=fid, media_type=media_type, series_key=key, season=season)


def test_split_series_key() -> None:
    assert split_series_key("anime|attack on titan|4|aot") == (
        "anime", "attack on titan", "4", "aot")
    assert split_series_key("tv|breaking bad||") == ("tv", "breaking bad", "", "")
    assert split_series_key(None) is None
    assert split_series_key("music|pink floyd|the wall") is None  # 3-part music key
    assert split_series_key("movie|x|y|z") is None  # not tv/anime


def test_aot_outlier_relocked_to_majority() -> None:
    members = [
        _ff(i, "anime|attack on titan|4|aot", season=4) for i in range(1, 12)
    ]
    # The mangled file parsed to a different title but same folder/season.
    members.append(_ff(99, "anime|shingeki no kyojin final season|4|aot", season=4))
    relocks = compute_relocks(members)
    assert relocks == {99: "anime|attack on titan|4|aot"}


def test_null_key_outlier_pulled_in_with_recovered_season() -> None:
    members = [_ff(i, "tv|breaking bad|1|x", media_type="tv", season=1) for i in (1, 2, 3)]
    members.append(_ff(50, None, media_type="tv", season=1))  # parser produced no title
    relocks = compute_relocks(members)
    assert relocks == {50: "tv|breaking bad|1|x"}


def test_mixed_seasons_same_show_unchanged() -> None:
    """S1 + S2 of one show share title → already the majority triple → no
    relock, and their season split is preserved (no collapse)."""
    members = [
        _ff(1, "tv|the wire|1|x", media_type="tv", season=1),
        _ff(2, "tv|the wire|1|x", media_type="tv", season=1),
        _ff(3, "tv|the wire|2|x", media_type="tv", season=2),
        _ff(4, "tv|the wire|2|x", media_type="tv", season=2),
    ]
    assert compute_relocks(members) == {}


def test_two_different_shows_not_force_merged() -> None:
    members = [
        _ff(1, "tv|show a|1|x", media_type="tv"),
        _ff(2, "tv|show a|1|x", media_type="tv"),
        _ff(3, "tv|show b|1|y", media_type="tv"),
        _ff(4, "tv|show b|1|y", media_type="tv"),
    ]
    assert compute_relocks(members) == {}  # 2-vs-2, no strict majority


def test_movies_never_touched() -> None:
    members = [
        _ff(1, "anime|gintama|1|g") for _ in range(1)
    ] + [
        _ff(2, None, media_type="movie"),
        _ff(3, None, media_type="movie"),
    ]
    # Only one anime file → triples has 1 entry, majority count 1 < MIN_AGREE.
    assert compute_relocks(members) == {}


def test_null_season_outlier_keeps_null() -> None:
    members = [_ff(i, "anime|frieren|1|f", season=1) for i in (1, 2)]
    members.append(_ff(9, "anime|sousou no frieren|1|f", season=None))
    relocks = compute_relocks(members)
    # Title unified, season taken from the outlier's OWN key ("1" here).
    assert relocks == {9: "anime|frieren|1|f"}


def test_single_file_folder_noop() -> None:
    assert compute_relocks([_ff(1, "anime|x|1|y")]) == {}
