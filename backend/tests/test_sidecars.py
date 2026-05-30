"""Tier 1.2: subtitle / sidecar co-renaming.

`discover_sidecars` and `compute_sidecar_target` are filesystem-only
pure functions — `tmp_path` is sufficient to exercise every shape we
care about: plain `<stem>.srt`, Plex-style `<stem>.<lang>.srt`,
multiple sidecars, unrelated files in the same folder, ALL CAPS
extensions, and the self-skip edge case.
"""
from __future__ import annotations

from pathlib import Path

from kira.renamer import compute_sidecar_target, discover_sidecars


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("")
    return p


def test_discover_sidecars_plain(tmp_path: Path) -> None:
    """Single-locale subtitle next to the video. The most common case."""
    video = _touch(tmp_path / "Movie (2010).mkv")
    sub = _touch(tmp_path / "Movie (2010).srt")
    _touch(tmp_path / "unrelated.txt")  # noise

    found = discover_sidecars(video)
    assert found == [sub]


def test_discover_sidecars_plex_multilang(tmp_path: Path) -> None:
    """Plex convention: language tag inside the filename."""
    video = _touch(tmp_path / "Show.S01E01.mkv")
    eng = _touch(tmp_path / "Show.S01E01.eng.srt")
    fre = _touch(tmp_path / "Show.S01E01.fre.forced.srt")
    sup = _touch(tmp_path / "Show.S01E01.eng.sup")

    found = set(discover_sidecars(video))
    assert found == {eng, fre, sup}


def test_discover_sidecars_unrelated_prefix(tmp_path: Path) -> None:
    """`Movie 2.srt` should NOT match `Movie.mkv` — different stem
    (the `.` boundary check matters)."""
    video = _touch(tmp_path / "Movie.mkv")
    _touch(tmp_path / "Movie 2.srt")   # different stem entirely
    _touch(tmp_path / "MovieX.srt")     # also no boundary
    actual_sub = _touch(tmp_path / "Movie.srt")

    found = discover_sidecars(video)
    assert found == [actual_sub]


def test_discover_sidecars_caps_extension(tmp_path: Path) -> None:
    """Case-insensitive extension match (Windows users may have .SRT)."""
    video = _touch(tmp_path / "Film.mkv")
    sub = _touch(tmp_path / "Film.SRT")

    found = discover_sidecars(video)
    assert found == [sub]


def test_discover_sidecars_missing_parent(tmp_path: Path) -> None:
    """Nonexistent parent returns empty (best-effort, no exception)."""
    fake = tmp_path / "nowhere" / "Movie.mkv"
    assert discover_sidecars(fake) == []


def test_discover_sidecars_no_match(tmp_path: Path) -> None:
    """Empty result when nothing in the folder pairs with the video."""
    video = _touch(tmp_path / "Anime.S01E01.mkv")
    _touch(tmp_path / "Different.Show.srt")
    _touch(tmp_path / "Cover.jpg")  # not a sidecar extension

    assert discover_sidecars(video) == []


def test_discover_sidecars_self_excluded(tmp_path: Path) -> None:
    """A file whose own extension is a sidecar type shouldn't pair with itself."""
    fake_video = _touch(tmp_path / "Foo.srt")   # hypothetical: someone passes a sub as the "video"
    found = discover_sidecars(fake_video)
    # The file should never appear as its own sidecar.
    assert fake_video not in found


def test_compute_sidecar_target_plain(tmp_path: Path) -> None:
    src = tmp_path / "Movie (2010).mkv"
    sub = tmp_path / "Movie (2010).srt"
    dst = tmp_path / "out" / "Inception (2010)" / "Inception (2010).mkv"

    out = compute_sidecar_target(sub, src, dst)
    assert out == dst.parent / "Inception (2010).srt"


def test_compute_sidecar_target_multilang(tmp_path: Path) -> None:
    src = tmp_path / "Show.S01E01.mkv"
    sub = tmp_path / "Show.S01E01.eng.forced.srt"
    dst = tmp_path / "out" / "Show (2020)" / "Season 01" / "Show - S01E01 - Pilot.mkv"

    out = compute_sidecar_target(sub, src, dst)
    expected = dst.parent / "Show - S01E01 - Pilot.eng.forced.srt"
    assert out == expected


def test_compute_sidecar_target_unrelated(tmp_path: Path) -> None:
    """A sidecar whose name doesn't actually start with the video's stem
    returns None (defensive guard — discovery shouldn't produce this)."""
    src = tmp_path / "Movie.mkv"
    not_a_sidecar = tmp_path / "Unrelated.srt"
    dst = tmp_path / "out" / "Renamed.mkv"

    assert compute_sidecar_target(not_a_sidecar, src, dst) is None
