"""Configurable folder cleanup: user-added filenames/extensions + the 3-way
aggressive "delete non-video" mode (off / keep_subs / all).

A video file is NEVER removable in any mode; user content DIRECTORIES (Subs/,
Extras/) always block cleanup; and a folder that still holds a video is never
touched. Deletions honor the Trash setting at the call layer (not exercised
here — these assert the classification + the rmdir outcome).
"""
from __future__ import annotations

from kira.renamer.operations import (
    _is_artifact_file,
    _is_removable_file,
    _folder_cleanable,
    _cleanup_empty_source_parents,
    sweep_destination_junk,
)

NAMES = frozenset({"custom.dat", "backdrop.jpg"})
EXTS = frozenset({".foo"})


# ── extra names / extensions feed _is_artifact_file ──────────────────────────
def test_extra_names_and_exts_recognized() -> None:
    assert _is_artifact_file("custom.dat", NAMES, EXTS)
    assert _is_artifact_file("CUSTOM.DAT", NAMES, EXTS)        # case-insensitive
    assert _is_artifact_file("whatever.foo", NAMES, EXTS)      # by extension
    assert not _is_artifact_file("keepme.txt", NAMES, EXTS)    # neither
    assert not _is_artifact_file("custom.dat")                 # no extras → not recognized


# ── _is_removable_file per mode ──────────────────────────────────────────────
def test_removable_off_mode_artifacts_only() -> None:
    f = dict(mode="off", extra_names=frozenset(), extra_exts=frozenset())
    assert _is_removable_file("poster.jpg", **f)               # recognized artifact
    assert not _is_removable_file("notes.txt", **f)            # unknown → kept
    assert not _is_removable_file("movie.srt", **f)            # sub → kept
    assert not _is_removable_file("Movie.mkv", **f)            # video → kept


def test_removable_keep_subs_mode() -> None:
    f = dict(mode="keep_subs", extra_names=frozenset(), extra_exts=frozenset())
    assert _is_removable_file("notes.txt", **f)                # non-video, non-sub → go
    assert _is_removable_file("poster.jpg", **f)
    assert not _is_removable_file("movie.srt", **f)            # subtitle → kept
    assert not _is_removable_file("movie.eng.ass", **f)        # subtitle → kept
    assert not _is_removable_file("Movie.mkv", **f)            # video → never


def test_removable_all_mode_keeps_only_video() -> None:
    f = dict(mode="all", extra_names=frozenset(), extra_exts=frozenset())
    assert _is_removable_file("notes.txt", **f)
    assert _is_removable_file("movie.srt", **f)                # subs go too
    assert _is_removable_file("poster.jpg", **f)
    assert not _is_removable_file("Movie.mkv", **f)            # video → still never


# ── _folder_cleanable gate ───────────────────────────────────────────────────
def _mk(tmp_path, names):
    d = tmp_path / "lib" / "Show"
    d.mkdir(parents=True)
    for n in names:
        if n.endswith("/"):
            (d / n.rstrip("/")).mkdir()
        else:
            (d / n).write_bytes(b"x")
    return tmp_path / "lib", d


def test_folder_cleanable_off_requires_all_artifacts(tmp_path) -> None:
    _, d = _mk(tmp_path, ["poster.jpg", "notes.txt"])
    assert not _folder_cleanable(d, mode="off")                          # stray user file blocks
    assert _folder_cleanable(d, mode="off", extra_names=frozenset({"notes.txt"}))  # …unless added


def test_folder_cleanable_nuke_blocks_on_video_and_user_dir(tmp_path) -> None:
    _, d1 = _mk(tmp_path, ["notes.txt", "movie.srt"])
    assert _folder_cleanable(d1, mode="all")                   # no video → fair game
    assert _folder_cleanable(d1, mode="keep_subs")
    _, d2 = _mk(tmp_path / "a", ["Movie.mkv", "notes.txt"])
    assert not _folder_cleanable(d2, mode="all")               # a video remains → never
    _, d3 = _mk(tmp_path / "b", ["notes.txt", "Extras/"])
    assert not _folder_cleanable(d3, mode="all")               # user dir blocks


# ── _cleanup_empty_source_parents end-to-end (real fs) ───────────────────────
def test_cleanup_off_keeps_folder_with_user_file(tmp_path) -> None:
    lib, d = _mk(tmp_path, ["poster.jpg", "notes.txt"])
    _cleanup_empty_source_parents(d, lib, 2, mode="off")
    assert d.exists() and (d / "notes.txt").exists() and (d / "poster.jpg").exists()


def test_cleanup_custom_name_lets_folder_go(tmp_path) -> None:
    lib, d = _mk(tmp_path, ["poster.jpg", "notes.txt"])
    _cleanup_empty_source_parents(d, lib, 2, mode="off", extra_names=frozenset({"notes.txt"}))
    assert not d.exists()                                      # both swept → folder removed


def test_cleanup_keep_subs_removes_junk_keeps_sub(tmp_path) -> None:
    lib, d = _mk(tmp_path, ["junk.txt", "movie.srt"])
    _cleanup_empty_source_parents(d, lib, 2, mode="keep_subs")
    assert d.exists()                                          # survives (sub remains)
    assert not (d / "junk.txt").exists()                       # junk gone
    assert (d / "movie.srt").exists()                          # subtitle kept


def test_cleanup_all_nukes_everything_nonvideo(tmp_path) -> None:
    lib, d = _mk(tmp_path, ["junk.txt", "movie.srt", "backdrop.jpg"])
    _cleanup_empty_source_parents(d, lib, 2, mode="all")
    assert not d.exists()                                      # all non-video gone → folder removed


def test_cleanup_nuke_never_touches_folder_with_video(tmp_path) -> None:
    lib, d = _mk(tmp_path, ["Movie.mkv", "junk.txt"])
    _cleanup_empty_source_parents(d, lib, 2, mode="all")
    assert d.exists() and (d / "Movie.mkv").exists() and (d / "junk.txt").exists()


# ── data-loss guards (adversarial-review findings) ───────────────────────────
def test_user_extras_can_never_delete_media() -> None:
    """A user who (mistakenly) lists a video/audio extension or a media filename
    in the custom 'delete' lists must NOT cause the content to be deleted — the
    media guard runs before the artifact/extras check."""
    names = frozenset({"movie.mkv", "song.flac"})
    exts = frozenset({".mkv", ".flac"})
    for mode in ("off", "keep_subs", "all"):
        f = dict(mode=mode, extra_names=names, extra_exts=exts)
        assert not _is_removable_file("Movie.mkv", **f), mode
        assert not _is_removable_file("Song.FLAC", **f), mode
        assert not _is_removable_file("clip.mp4", **f), mode


def test_audio_protected_like_video_in_nuke_modes(tmp_path) -> None:
    f = dict(mode="all", extra_names=frozenset(), extra_exts=frozenset())
    assert not _is_removable_file("track.flac", **f)       # audio → never removable
    assert not _is_removable_file("track.mp3", **f)
    # a folder still holding an audio file is never stripped (music libraries).
    lib, d = _mk(tmp_path / "m", ["track.flac", "junk.txt"])
    assert not _folder_cleanable(d, mode="all")
    _cleanup_empty_source_parents(d, lib, 2, mode="all")
    assert d.exists() and (d / "track.flac").exists() and (d / "junk.txt").exists()


# ── sweep_destination_junk: the in-place / same-folder case ──────────────────
# This folder KEEPS its media (a rename landed here), so the source-walk can't
# touch it. The destination sweep strips junk while protecting media, everything
# Kira wrote this batch (`protected`), and Kira's own artwork/NFO output names.
def _mkflat(tmp_path, names):
    d = tmp_path / "lib" / "Show" / "Season 01"
    d.mkdir(parents=True)
    for n in names:
        if n.endswith("/"):
            (d / n.rstrip("/")).mkdir()
        else:
            (d / n).write_bytes(b"x")
    return d


def test_inplace_all_mode_strips_junk_keeps_media_and_kira_output(tmp_path) -> None:
    d = _mkflat(tmp_path, [
        "Show - S01E01.mkv",       # renamed video (protected)
        "Show - S01E01.nfo",       # Kira episode NFO (protected)
        "Show - S01E01.en.srt",    # co-renamed sub (protected)
        "poster.jpg",              # season poster (artifact NAME → protect, even if a prior run wrote it)
        "tvshow.nfo",              # artifact NAME → protect
        "Show.S01E01-thumb.jpg",   # per-file artifact NAME → protect
        "sample.mkv",              # media extension → NEVER deletable
        "readme.nfo",              # ANY .nfo matches the artifact catch-all → protected in-place
        "RARBG.txt", "screen.png",  # leftover junk
    ])
    protected = frozenset({
        str(d / "Show - S01E01.mkv"), str(d / "Show - S01E01.nfo"), str(d / "Show - S01E01.en.srt"),
    })
    n = sweep_destination_junk(d, mode="all", protected=protected)
    # Kept (media + this-batch output + artifact NAMES incl. any .nfo):
    for keep in ("Show - S01E01.mkv", "Show - S01E01.nfo", "Show - S01E01.en.srt",
                 "poster.jpg", "tvshow.nfo", "Show.S01E01-thumb.jpg", "sample.mkv", "readme.nfo"):
        assert (d / keep).exists(), keep
    # Gone (non-media, non-artifact-name leftovers):
    for gone in ("RARBG.txt", "screen.png"):
        assert not (d / gone).exists(), gone
    assert d.exists(), "folder is never removed (it holds media)"
    assert n == 2


def test_inplace_off_mode_only_deletes_user_custom_list(tmp_path) -> None:
    d = _mkflat(tmp_path, ["Movie.mkv", "Movie.nfo", "poster.jpg", "RARBG.txt", "x.foo", "keep.txt"])
    protected = frozenset({str(d / "Movie.mkv"), str(d / "Movie.nfo")})
    sweep_destination_junk(
        d, mode="off",
        extra_names=frozenset({"rarbg.txt"}), extra_exts=frozenset({".foo"}),
        protected=protected,
    )
    assert (d / "Movie.mkv").exists() and (d / "Movie.nfo").exists()
    assert (d / "poster.jpg").exists(), "artifact name, not user-listed → protected in-place"
    assert (d / "keep.txt").exists(), "off mode never touches non-listed junk"
    assert not (d / "RARBG.txt").exists() and not (d / "x.foo").exists()


def test_inplace_keep_subs_spares_subtitles(tmp_path) -> None:
    d = _mkflat(tmp_path, ["Ep.mkv", "Ep.nfo", "old.en.srt", "notes.txt", "poster.jpg"])
    protected = frozenset({str(d / "Ep.mkv"), str(d / "Ep.nfo")})
    sweep_destination_junk(d, mode="keep_subs", protected=protected)
    assert (d / "old.en.srt").exists(), "keep_subs spares subtitles"
    assert (d / "poster.jpg").exists(), "artifact name protected"
    assert not (d / "notes.txt").exists()


def test_inplace_user_can_target_an_artifact_but_never_this_batch_output(tmp_path) -> None:
    # The user may explicitly list an artifact name to delete a STALE one…
    d1 = _mkflat(tmp_path / "a", ["Movie.mkv", "poster.jpg"])
    sweep_destination_junk(d1, mode="off", extra_names=frozenset({"poster.jpg"}),
                           protected=frozenset({str(d1 / "Movie.mkv")}))
    assert not (d1 / "poster.jpg").exists(), "user explicitly listed it → deleted"
    # …but the protected set (this batch's output) ALWAYS wins, even when listed + 'all'.
    d2 = _mkflat(tmp_path / "b", ["Movie.mkv", "poster.jpg"])
    sweep_destination_junk(d2, mode="all", extra_names=frozenset({"poster.jpg"}),
                           protected=frozenset({str(d2 / "Movie.mkv"), str(d2 / "poster.jpg")}))
    assert (d2 / "poster.jpg").exists(), "this-batch output is never deleted"


def test_inplace_never_recurses_or_removes_folder(tmp_path) -> None:
    d = _mkflat(tmp_path, ["Ep.mkv", "junk.txt", "Subs/", "Extras/"])
    (d / "Subs" / "x.srt").write_bytes(b"x")
    sweep_destination_junk(d, mode="all", protected=frozenset({str(d / "Ep.mkv")}))
    assert d.exists() and (d / "Subs").exists() and (d / "Extras").exists()
    assert (d / "Subs" / "x.srt").exists(), "must not recurse into user dirs"
    assert not (d / "junk.txt").exists()


def test_inplace_media_never_deleted_even_if_user_lists_it(tmp_path) -> None:
    d = _mkflat(tmp_path, ["Movie.mkv", "extra.mkv"])
    # A user who (mistakenly) lists .mkv must never lose a video.
    sweep_destination_junk(d, mode="all", extra_exts=frozenset({".mkv"}),
                           protected=frozenset({str(d / "Movie.mkv")}))
    assert (d / "Movie.mkv").exists() and (d / "extra.mkv").exists()


def test_inplace_spares_unrenamed_neighbour_episode_sidecars(tmp_path) -> None:
    # Adversarial-review catch: a Season folder where ep01 was renamed THIS batch
    # (in `protected`) but ep02 is a pre-existing episode the user did NOT touch.
    # 'all' mode must strip LOOSE junk yet never collateral-delete ep02's own
    # subtitle (a sidecar of media still present) — only files matching no media
    # stem go.
    d = _mkflat(tmp_path, [
        "Show - S01E01.mkv",       # renamed this batch
        "Show - S01E01.en.srt",    # co-renamed sub (in protected)
        "Show - S01E02.mkv",       # NEIGHBOUR — not renamed, not in protected
        "Show - S01E02.en.srt",    # neighbour's sub — must SURVIVE (sidecar of ep02)
        "Show - S01E02.fr.srt",    # neighbour's 2nd sub — must SURVIVE
        "RARBG.txt", "info.url",   # loose junk — no media stem → go
    ])
    protected = frozenset({str(d / "Show - S01E01.mkv"), str(d / "Show - S01E01.en.srt")})
    sweep_destination_junk(d, mode="all", protected=protected)
    assert (d / "Show - S01E01.mkv").exists()
    assert (d / "Show - S01E02.mkv").exists()                 # media, never touched
    assert (d / "Show - S01E02.en.srt").exists(), "neighbour sub must survive"
    assert (d / "Show - S01E02.fr.srt").exists(), "neighbour sub must survive"
    assert not (d / "RARBG.txt").exists()                     # loose junk gone
    assert not (d / "info.url").exists()
