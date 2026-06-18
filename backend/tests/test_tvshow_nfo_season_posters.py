"""Per-season posters in tvshow.nfo — Kodi's season-art mechanism.

Each AniDB cour carries its own poster; unified into one show, `build_tvshow_nfo`
emits a `<thumb aspect="poster" type="season" season="N">` per cour so NFO-driven
servers (Kodi) show distinct season covers. File-based servers (Plex/Jellyfin/
Emby) read `Season NN/poster.jpg` from the artwork pass instead.
"""
from __future__ import annotations

from kira.renamer import nfo


def test_emits_season_thumbs_sorted_by_season() -> None:
    out = nfo.build_tvshow_nfo(
        "Attack on Titan", 2013, {}, "anidb", "1",
        season_posters={3: "https://x/s3.jpg", 2: "https://x/s2.jpg"},
    )
    assert '<thumb aspect="poster" type="season" season="2">https://x/s2.jpg</thumb>' in out
    assert '<thumb aspect="poster" type="season" season="3">https://x/s3.jpg</thumb>' in out
    assert out.index('season="2"') < out.index('season="3"')   # sorted ascending


def test_none_or_empty_emits_no_season_thumbs() -> None:
    assert 'type="season"' not in nfo.build_tvshow_nfo("Show", None, {}, season_posters=None)
    assert 'type="season"' not in nfo.build_tvshow_nfo("Show", None, {}, season_posters={})


def test_season_thumbs_use_their_own_field_independent_of_artwork() -> None:
    sp = {2: "https://x/s2.jpg"}
    # Field not enabled → no thumbs.
    assert 'type="season"' not in nfo.build_tvshow_nfo("Show", None, {}, fields={"title"}, season_posters=sp)
    # 'seasonposters' on WITHOUT 'artwork' → thumbs still emitted (own toggle).
    on = nfo.build_tvshow_nfo("Show", None, {}, fields={"seasonposters"}, season_posters=sp)
    assert '<thumb aspect="poster" type="season" season="2">https://x/s2.jpg</thumb>' in on
    # 'artwork' on but 'seasonposters' off → NO season thumbs (decoupled from show art).
    assert 'type="season"' not in nfo.build_tvshow_nfo("Show", None, {}, fields={"artwork"}, season_posters=sp)
