"""User-configurable provider preference (Settings → Matching).

resolve_provider_order() turns the hardcoded PROVIDER_PREFERENCE defaults into
a per-media_type setting (`matching.provider_order.<type>`). It's a SOFT
preference: the user's picks go first, omitted defaults stay as trailing
fallbacks, so a preference can never strand a title as no-match just because the
preferred source lacks it.
"""

from __future__ import annotations

from kira.matcher.engine import PROVIDER_PREFERENCE, resolve_provider_order


def test_unset_returns_default():
    assert resolve_provider_order("anime", None) == ["anidb", "tvdb", "tmdb"]
    assert resolve_provider_order("anime", {}) == ["anidb", "tvdb", "tmdb"]
    assert resolve_provider_order("movie", None) == PROVIDER_PREFERENCE["movie"]


def test_override_reorders_and_keeps_fallbacks():
    # User prefers TVDB for anime → TVDB first, AniDB/TMDB stay as fallbacks.
    s = {"matching.provider_order.anime": ["tvdb", "anidb"]}
    assert resolve_provider_order("anime", s) == ["tvdb", "anidb", "tmdb"]


def test_single_pick_appends_all_omitted_defaults():
    s = {"matching.provider_order.anime": ["tvdb"]}
    # soft: the omitted defaults (anidb, tmdb) follow in their default order.
    assert resolve_provider_order("anime", s) == ["tvdb", "anidb", "tmdb"]


def test_value_wrapper_tolerated():
    s = {"matching.provider_order.anime": {"value": ["tvdb", "anidb"]}}
    assert resolve_provider_order("anime", s) == ["tvdb", "anidb", "tmdb"]


def test_unknown_keys_dropped():
    s = {"matching.provider_order.anime": ["tvdb", "junk", "anidb", 123]}
    assert resolve_provider_order("anime", s) == ["tvdb", "anidb", "tmdb"]


def test_empty_or_all_junk_falls_back_to_default():
    assert resolve_provider_order("anime", {"matching.provider_order.anime": []}) \
        == ["anidb", "tvdb", "tmdb"]
    assert resolve_provider_order("anime", {"matching.provider_order.anime": ["nope"]}) \
        == ["anidb", "tvdb", "tmdb"]


def test_non_list_value_falls_back_to_default():
    assert resolve_provider_order("anime", {"matching.provider_order.anime": "tvdb"}) \
        == ["anidb", "tvdb", "tmdb"]


def test_movie_and_tv_independent():
    s = {
        "matching.provider_order.movie": ["tvdb"],
        "matching.provider_order.tv": ["tmdb"],
    }
    assert resolve_provider_order("movie", s) == ["tvdb", "tmdb"]
    assert resolve_provider_order("tv", s) == ["tmdb", "tvdb"]
    # anime untouched (no override for it) → default.
    assert resolve_provider_order("anime", s) == ["anidb", "tvdb", "tmdb"]


def test_music_default_empty():
    assert resolve_provider_order("music", None) == []
    assert resolve_provider_order("music", {"matching.provider_order.music": ["tmdb"]}) \
        == ["tmdb"]  # honored if the user somehow sets it; no defaults to append
