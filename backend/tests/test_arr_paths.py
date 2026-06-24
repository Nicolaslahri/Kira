"""Shared Kira↔*arr path translation — the mount-bridge that re-roots Kira's new
folder into Sonarr/Radarr's path namespace via the common path suffix.

This is the seam BOTH relink hooks depend on, tested once here for both. The
back-compat re-export (`sonarr._translate_path`) is asserted to be the same
object so the Sonarr callers/tests can't silently drift from this implementation.
Covers the cross-mount cases from the user's real setup (POSIX *arr in Docker vs
Kira addressing the share via a Windows drive letter / UNC path)."""
from __future__ import annotations

from kira.integrations import sonarr
from kira.integrations.arr_paths import translate_path


def test_reexport_is_the_same_object():
    # Sonarr (and its tests) import `_translate_path` from sonarr for back-compat;
    # it MUST be the shared implementation, not a stale fork.
    assert sonarr._translate_path is translate_path


def test_different_mounts_folder_rename():
    # Kira sees /media, the *arr sees /data/media; folder renamed in place.
    assert translate_path(
        "/data/media/movies/Inception (2010)",
        "/media/movies/Inception (2010)",
        "/media/movies/Inception (2010) [Bluray-1080p]",
    ) == "/data/media/movies/Inception (2010) [Bluray-1080p]"


def test_same_mount():
    assert translate_path(
        "/data/media/movies/Film",
        "/data/media/movies/Film",
        "/data/media/movies/Film (2020)",
    ) == "/data/media/movies/Film (2020)"


def test_no_change_when_folder_same():
    # arr_new == arr_old → the caller skips the PUT and only rescans/refreshes.
    assert translate_path(
        "/data/media/movies/Film", "/media/movies/Film", "/media/movies/Film",
    ) == "/data/media/movies/Film"


def test_no_common_suffix_returns_none():
    assert translate_path("/data/movies/A", "/library/B", "/library/B2") is None


def test_cross_mount_move_returns_none():
    # New folder isn't under the Kira mount prefix the shared suffix implied.
    assert translate_path(
        "/data/media/movies/Film", "/media/movies/Film", "/elsewhere/Film (2020)",
    ) is None


def test_windows_drive_kira_path():
    # Kira on Windows (Z:\), the *arr on POSIX, same underlying share.
    assert translate_path(
        "/data/media/movies/Film", r"Z:\media\movies\Film", r"Z:\media\movies\Film (2021)",
    ) == "/data/media/movies/Film (2021)"


def test_windows_unc_kira_path():
    # Kira addressing the share via UNC (\\nas\Media\...), the *arr on POSIX
    # (/mnt/tank/...). The shared suffix is movies/Film; the UNC host+share form
    # the Kira mount prefix, stripped then re-rooted under the *arr prefix.
    assert translate_path(
        "/mnt/tank/movies/Film",
        r"\\nas\Media\movies\Film",
        r"\\nas\Media\movies\Film (2021)",
    ) == "/mnt/tank/movies/Film (2021)"
