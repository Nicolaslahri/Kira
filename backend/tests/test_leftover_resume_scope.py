"""Leftover-resume must be scoped to the roots being scanned (audit).

A post-crash boot reset can leave thousands of files in 'discovered' across the
whole library. The scan worker's resume step now filters those by
`path_under_roots(file_path, current_roots)` — the exact contract tested here —
so a targeted folder scan resumes only ITS subtree, not the entire backlog,
while a full-library scan still resumes everything.
"""
from __future__ import annotations

from kira.api.webhooks import path_under_roots


def test_targeted_scan_only_resumes_its_own_subtree():
    roots = ["/media/tv"]                                  # a targeted re-scan
    assert path_under_roots("/media/tv/ShowA/ep.mkv", roots) is True    # resumed
    assert path_under_roots("/media/tv/ShowB/s01e02.mkv", roots) is True
    assert path_under_roots("/media/movies/Film (2020)/m.mkv", roots) is False  # NOT vacuumed
    assert path_under_roots("/media/anime/One Piece/1156.mkv", roots) is False


def test_full_library_scan_resumes_everything():
    roots = ["/media"]                                     # whole-library scan
    assert path_under_roots("/media/tv/ShowA/ep.mkv", roots) is True
    assert path_under_roots("/media/movies/Film/m.mkv", roots) is True
    assert path_under_roots("/media/anime/X/1.mkv", roots) is True
