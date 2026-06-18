"""Per-episode NFO enrichment: real title / plot / aired resolved from the
provider episode list (via the Fribb TVDB/TMDB cross-ref for AniDB anime).

Before this, an AniDB-matched episode (One Piece et al.) had no per-episode
title, so its NFO carried only <showtitle>/<season>/<episode> and the filename
fell back to "Episode NN". These cover the builder emitting the new fields and
the resolver picking the right episode out of the cross-ref list."""
import types

from kira.providers.base import EpisodeResult
from kira.renamer import nfo


def _ep(season, episode, title, overview=None, air_date=None, absolute=None):
    return EpisodeResult(provider="tvdb", series_id="81797", season=season, episode=episode,
                         title=title, air_date=air_date, overview=overview,
                         runtime=None, absolute_number=absolute)


# ---------------------------------------------------------------- NFO builder

def test_episode_nfo_writes_plot_and_aired():
    out = nfo.build_episode_nfo("Egghead", 23, 1, {"runtime": 24}, series_name="One Piece",
                                plot="Luffy reaches the island of the future.",
                                aired="2024-01-07")
    assert "<title>Egghead</title>" in out
    assert "<plot>Luffy reaches the island of the future.</plot>" in out
    assert "<aired>2024-01-07</aired>" in out


def test_episode_nfo_omits_empty_plot_aired():
    out = nfo.build_episode_nfo("Egghead", 23, 1, {})
    assert "<title>Egghead</title>" in out
    assert "<plot>" not in out
    assert "<aired>" not in out


def test_episode_nfo_plot_honors_field_toggle_aired_does_not():
    # `plot` is in NFO_TOGGLEABLE → suppressed when not in the enabled set.
    # `aired` has no toggle and is always written when present.
    out = nfo.build_episode_nfo("Egghead", 23, 1, {}, fields={"showtitle"},
                                plot="hidden", aired="2024-01-07")
    assert "<plot>" not in out
    assert "<aired>2024-01-07</aired>" in out


# ----------------------------------------------------------- cross-ref resolver

async def test_resolve_episode_meta_anidb_via_crossref(monkeypatch):
    from kira.api import series
    eps = [_ep(23, 1, "Egghead", overview="The future island.", air_date="2024-01-07", absolute=1089),
           _ep(23, 2, "Bonney's Adventure", overview="...", air_date="2024-01-14", absolute=1090)]

    async def _fake(aid, season, registry, client):
        return eps

    monkeypatch.setattr(series, "_anidb_episodes_via_cross_ref", _fake)
    series._episode_meta_cache.clear()
    selected = types.SimpleNamespace(provider="anidb", provider_id="69",
                                     episode_number=1089, season_number=23)
    got = await series.resolve_episode_meta(selected, 23, 1, registry=object(), client=object())
    assert got is not None and got.title == "Egghead"
    assert got.overview == "The future island." and got.air_date == "2024-01-07"


async def test_resolve_episode_meta_absolute_fallback(monkeypatch):
    # parsed.episode still carries the absolute number (file not yet renamed to
    # SxxEyy) — no season-relative hit, so we fall back to absolute_number.
    from kira.api import series
    eps = [_ep(23, 1, "Egghead", absolute=1089), _ep(23, 2, "Bonney's Adventure", absolute=1090)]

    async def _fake(aid, season, registry, client):
        return eps

    monkeypatch.setattr(series, "_anidb_episodes_via_cross_ref", _fake)
    series._episode_meta_cache.clear()
    selected = types.SimpleNamespace(provider="anidb", provider_id="69",
                                     episode_number=1089, season_number=23)
    got = await series.resolve_episode_meta(selected, 23, 1089, registry=object(), client=object())
    assert got is not None and got.title == "Egghead"


async def test_resolve_episode_meta_miss_returns_none(monkeypatch):
    from kira.api import series

    async def _fake(aid, season, registry, client):
        return []

    monkeypatch.setattr(series, "_anidb_episodes_via_cross_ref", _fake)
    series._episode_meta_cache.clear()
    selected = types.SimpleNamespace(provider="anidb", provider_id="69",
                                     episode_number=1089, season_number=23)
    got = await series.resolve_episode_meta(selected, 23, 1, registry=object(), client=object())
    assert got is None


async def test_resolve_episode_meta_no_cour_local_collision(monkeypatch):
    # The "Captain Kuro" bug: AniDB numbers episodes ABSOLUTELY (season always 1),
    # so its list has BOTH episode 11 (a 2000 episode) and episode 1166. The
    # cour-local number (11) must NOT win — we match the authoritative absolute
    # episode_number (1166).
    from kira.api import series

    eps = [EpisodeResult(provider="anidb", series_id="69", season=1, episode=11,
                         title="Expose the Plot! Captain Kuro", air_date="2000-01-26"),
           EpisodeResult(provider="anidb", series_id="69", season=1, episode=1166,
                         title="Encountering Loki", air_date="2026-06-14")]

    class _AniDB:
        async def get_episodes(self, pid, season, **kw):
            return eps

    class _Reg:
        def has(self, k):
            return k == "anidb"

        def build(self, k):
            return _AniDB()

    series._episode_meta_cache.clear()
    selected = types.SimpleNamespace(provider="anidb", provider_id="69",
                                     episode_number=1166, season_number=1)
    # season=1, cour-local episode=11 passed — must still resolve absolute 1166.
    got = await series.resolve_episode_meta(selected, 1, 11, registry=_Reg(), client=object())
    assert got is not None and got.title == "Encountering Loki", \
        f"expected absolute 1166, got {got and got.title!r}"


async def test_resolve_episode_meta_caches_per_series_season(monkeypatch):
    from kira.api import series
    calls = {"n": 0}

    async def _fake(aid, season, registry, client):
        calls["n"] += 1
        return [_ep(23, 1, "Egghead", absolute=1089), _ep(23, 2, "Bonney's Adventure", absolute=1090)]

    monkeypatch.setattr(series, "_anidb_episodes_via_cross_ref", _fake)
    series._episode_meta_cache.clear()
    # Distinct absolute episode_numbers — the picker keys on episode_number, the
    # cache keys on (provider, id, season), so one fetch serves both lookups.
    sel_a = types.SimpleNamespace(provider="anidb", provider_id="69",
                                  episode_number=1089, season_number=23)
    sel_b = types.SimpleNamespace(provider="anidb", provider_id="69",
                                  episode_number=1090, season_number=23)
    a = await series.resolve_episode_meta(sel_a, 23, 1, registry=object(), client=object())
    b = await series.resolve_episode_meta(sel_b, 23, 2, registry=object(), client=object())
    assert a.title == "Egghead" and b.title == "Bonney's Adventure"
    assert calls["n"] == 1   # one fetch served both episodes of the same series/season


# ----------------------------------------------- AniDB episode-title selection
# AniDB auto-fills an episode's English title with the literal "Episode <num>"
# placeholder until a real one is added; a freshly-aired episode therefore shows
# "Episode 1166" even though its real romaji/Japanese title already exists. The
# picker must prefer the real title over that placeholder.
import defusedxml.ElementTree as _ET  # noqa: E402

from kira.providers.anidb import _select_episode_title  # noqa: E402


def _anidb_ep(num, titles):
    parts = "".join(f'<title xml:lang="{lang}">{text}</title>' for lang, text in titles)
    return _ET.fromstring(f'<episode><epno type="1">{num}</epno>{parts}</episode>')


def test_anidb_title_prefers_romaji_over_episode_n_placeholder():
    ep = _anidb_ep(1166, [("en", "Episode 1166"), ("x-jat", "Shin Sekai e"), ("ja", "新世界へ")])
    assert _select_episode_title(ep, 1166) == "Shin Sekai e"


def test_anidb_title_prefers_real_english_when_present():
    ep = _anidb_ep(1000, [("en", "Overwhelming Strength"), ("x-jat", "Attsu")])
    assert _select_episode_title(ep, 1000) == "Overwhelming Strength"


def test_anidb_title_keeps_placeholder_when_only_option():
    # No romaji/native alternative → keep the placeholder so the episode is not
    # left untitled (which would drop it from the popup).
    ep = _anidb_ep(1166, [("en", "Episode 1166")])
    assert _select_episode_title(ep, 1166) == "Episode 1166"


def test_anidb_title_unrelated_episode_number_is_not_a_placeholder():
    # "Episode 50" on episode 1166 is a real (if unusual) title — only the
    # episode's OWN "Episode <num>" is treated as the placeholder.
    ep = _anidb_ep(1166, [("en", "Episode 50"), ("x-jat", "Romaji")])
    assert _select_episode_title(ep, 1166) == "Episode 50"


def test_anidb_title_falls_back_to_native_without_english():
    ep = _anidb_ep(1166, [("ja", "新世界へ")])
    assert _select_episode_title(ep, 1166) == "新世界へ"
