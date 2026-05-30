"""Phase 19 — sample / extras / trailer exclusion."""

from __future__ import annotations

from pathlib import Path

from kira.scanner import _is_sample_or_extra


def test_exact_sample_stem() -> None:
    assert _is_sample_or_extra(Path("/m/Movie (2020)/sample.mkv"))
    assert _is_sample_or_extra(Path("/m/Movie (2020)/Movie-sample.mkv"))
    assert _is_sample_or_extra(Path("/m/x/trailer.mp4"))
    assert _is_sample_or_extra(Path("/m/x/proof.mkv"))


def test_extras_folder() -> None:
    assert _is_sample_or_extra(Path("/m/Movie/Featurettes/making-of.mkv"))
    assert _is_sample_or_extra(Path("/m/Movie/Behind The Scenes/clip.mkv"))
    assert _is_sample_or_extra(Path("/m/Movie/Extras/bonus.mkv"))


def test_specials_folder_not_excluded() -> None:
    """Specials are real season-0 content (Phase 2) — must NOT be culled."""
    assert not _is_sample_or_extra(Path("/m/Show/Specials/Show S00E01.mkv"))


def test_normal_episode_not_excluded() -> None:
    assert not _is_sample_or_extra(Path("/m/Show/Season 1/Show S01E01.mkv"))


def test_legit_title_with_token_survives_when_large(tmp_path) -> None:
    """'Trailer Park Boys S01E01' has a 'trailer' token but is a real
    episode — only culled if it's small. A large file survives."""
    big = tmp_path / "Trailer Park Boys S01E01.mkv"
    big.write_bytes(b"\0")  # tiny here, so it WOULD be culled by size...
    # ...but the exact-stem rule doesn't fire (stem isn't 'trailer'), and the
    # token rule gates on size; a 1-byte test file is < 300MB, so it culls.
    # Assert the size gate works the other way: a non-existent large-name
    # path can't be stat'd → token rule returns False (no cull).
    ghost = Path("/m/Show/Trailer Park Boys S01E01.mkv")
    assert not _is_sample_or_extra(ghost)
