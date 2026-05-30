"""Sonarr grab audio-preference (sub vs dub) release selection."""

from __future__ import annotations

from kira.integrations.sonarr import (
    _audio_pref_rank,
    _is_trusted_jp_sub,
    _pick_release_for_episode,
    _release_audio_kind,
    _release_audio_kind_rel,
    _release_is_grabbable,
    _release_is_pack,
    _release_matches_preference,
)


# ── Title-only heuristic (fallback when Sonarr reports no languages) ──────


def test_audio_kind_dub() -> None:
    assert _release_audio_kind("Rent-a-Girlfriend S01E01 English Dubbed 1080p") == "dub"
    assert _release_audio_kind("Kanojo - 01 [Dub] 1080p") == "dub"
    assert _release_audio_kind("Show S01E01 ENG DUB") == "dub"


def test_audio_kind_neutral() -> None:
    # Fansub release with no audio marker → sub-friendly "neutral".
    assert _release_audio_kind("[SubsPlease] Kanojo, Okarishimasu - 01 (1080p)") == "neutral"
    # Dual audio carries BOTH tracks → neutral (acceptable either way).
    assert _release_audio_kind("Kanojo Okarishimasu S01E01 [Dual Audio] 1080p") == "neutral"
    assert _release_audio_kind("Show S01E01 1080p WEB-DL x265") == "neutral"


def test_audio_kind_sub() -> None:
    assert _release_audio_kind("Kanojo S01E01 VOSTFR 1080p") == "sub"
    assert _release_audio_kind("Show - 01 [Multi-Subs] 1080p") == "sub"


def test_matches_preference() -> None:
    dub = "Rent-a-Girlfriend S01E01 English Dubbed"
    sub = "Kanojo S01E01 VOSTFR"
    neutral = "[SubsPlease] Kanojo - 01"
    dual = "Kanojo S01E01 Dual Audio"
    # sub preference excludes dub-only, allows sub + neutral + dual
    assert not _release_matches_preference(dub, "sub")
    assert _release_matches_preference(sub, "sub")
    assert _release_matches_preference(neutral, "sub")
    assert _release_matches_preference(dual, "sub")
    # dub preference excludes sub-only, allows dub + neutral + dual
    assert not _release_matches_preference(sub, "dub")
    assert _release_matches_preference(dub, "dub")
    assert _release_matches_preference(dual, "dub")
    # any allows everything
    assert _release_matches_preference(dub, "any")
    assert _release_matches_preference(sub, "any")


# ── Language-aware classification (the real path, uses Sonarr's parsed langs) ─


def _rel(title, *, langs=None, approved=True, full_season=False, mapped=None,
         cfs=0, qw=0, seeders=0, guid=None, indexer=1, group=None):
    """Build a release dict shaped like Sonarr's /release records."""
    r = {
        "title": title,
        "guid": guid or f"g:{title}",
        "indexerId": indexer,
        "approved": approved,
        "rejected": not approved,
        "fullSeason": full_season,
        "customFormatScore": cfs,
        "qualityWeight": qw,
        "seeders": seeders,
    }
    if langs is not None:
        r["languages"] = [{"name": n} for n in langs]
    if mapped is not None:
        r["mappedEpisodeNumbers"] = mapped
    if group is not None:
        r["releaseGroup"] = group
    return r


def test_audio_kind_rel_from_languages() -> None:
    # Japanese-only audio → sub (even if the title says nothing).
    assert _release_audio_kind_rel(_rel("Rent-A-Girlfriend.S03E01.x265-iVy", langs=["Japanese"])) == "sub"
    # English-only audio → dub.
    assert _release_audio_kind_rel(_rel("[TRC] RaG S03E01 English Dub", langs=["English"])) == "dub"
    # Both tracks → neutral (multi-audio, fine for either preference).
    assert _release_audio_kind_rel(_rel("RaG S03E01 MULTi", langs=["English", "Japanese"])) == "neutral"
    # French + Japanese (no English-only) → sub.
    assert _release_audio_kind_rel(_rel("Kanojo S03E01 MULTi", langs=["French", "Japanese"])) == "sub"


def test_audio_kind_rel_falls_back_to_title() -> None:
    # No languages reported → use the title heuristic.
    assert _release_audio_kind_rel(_rel("Show S01E01 English Dubbed")) == "dub"
    assert _release_audio_kind_rel(_rel("[SubsPlease] Show - 01")) == "neutral"


def test_languages_override_misleading_title() -> None:
    # iVy WEBRips have English episode TITLES but Japanese AUDIO — the regex
    # would call this neutral, but Sonarr's languages make it correctly sub,
    # and crucially a dub-titled release with Japanese audio is NOT excluded
    # under sub preference.
    r = _rel("Rent-A-Girlfriend.S03E01.Home.Cooking.and.Girlfriend.x265-iVy", langs=["Japanese"])
    assert _release_audio_kind_rel(r) == "sub"


# ── Grabbable / pack helpers ─────────────────────────────────────────────


def test_is_grabbable() -> None:
    assert _release_is_grabbable(_rel("x", approved=True)) is True
    assert _release_is_grabbable(_rel("x", approved=False)) is False
    # No `approved` key → fall back to `rejected`.
    assert _release_is_grabbable({"rejected": True}) is False
    assert _release_is_grabbable({"rejected": False}) is True
    assert _release_is_grabbable({}) is True


def test_is_pack() -> None:
    assert _release_is_pack(_rel("season pack", full_season=True)) is True
    assert _release_is_pack(_rel("batch", mapped=[1, 2, 3])) is True
    assert _release_is_pack(_rel("single", mapped=[5])) is False
    assert _release_is_pack(_rel("single, no mapping")) is False


# ── Per-episode selection (the function the grab loop calls) ──────────────


def test_pick_prefers_sub_skips_dub() -> None:
    releases = [
        _rel("[TRC] RaG S03E01 English Dub", langs=["English"], mapped=[1]),
        _rel("[SubsPlease] Kanojo - 01", langs=["Japanese"], mapped=[1]),
    ]
    pick = _pick_release_for_episode(releases, "sub")
    assert pick is not None
    assert "SubsPlease" in pick["title"]


def test_pick_returns_none_when_only_dub() -> None:
    releases = [
        _rel("Kanojo - 01 English Dubbed", langs=["English"], mapped=[1]),
        _rel("Kanojo - 02 [Dub]", langs=["English"], mapped=[1]),
    ]
    assert _pick_release_for_episode(releases, "sub") is None


def test_pick_returns_none_when_all_rejected() -> None:
    # Re-requesting episodes the user already has: Sonarr rejects every
    # candidate. We must SKIP (return None), never force-grab — that is the
    # exact bug that jammed the import queue.
    releases = [
        _rel("[SubsPlease] Kanojo - 01", langs=["Japanese"], approved=False, mapped=[1]),
        _rel("[Moozzi2] Kanojo S3 pack", langs=["Japanese"], approved=False, full_season=True),
    ]
    assert _pick_release_for_episode(releases, "sub") is None


def test_pick_prefers_single_episode_over_pack() -> None:
    releases = [
        _rel("[Moozzi2] Kanojo S3 pack", langs=["Japanese"], full_season=True,
             mapped=[1, 2, 3], cfs=100),
        _rel("[SubsPlease] Kanojo - 01", langs=["Japanese"], mapped=[1], cfs=0),
    ]
    pick = _pick_release_for_episode(releases, "sub")
    # Single episode wins even though the pack has a higher custom-format score.
    assert pick is not None and "SubsPlease" in pick["title"]


def test_pick_ranks_score_then_quality_among_singles() -> None:
    releases = [
        _rel("low", langs=["Japanese"], mapped=[1], cfs=0, qw=10),
        _rel("hi-score", langs=["Japanese"], mapped=[1], cfs=50, qw=1),
        _rel("hi-quality", langs=["Japanese"], mapped=[1], cfs=0, qw=99),
    ]
    pick = _pick_release_for_episode(releases, "sub")
    assert pick["title"] == "hi-score"  # custom-format score beats quality weight


def test_pick_any_preference_takes_best_grabbable() -> None:
    releases = [
        _rel("dub", langs=["English"], mapped=[1], cfs=5),
        _rel("sub", langs=["Japanese"], mapped=[1], cfs=50),
    ]
    pick = _pick_release_for_episode(releases, "any")
    assert pick["title"] == "sub"  # higher score, audio unconstrained


def test_pick_ignores_releases_without_guid_or_indexer() -> None:
    releases = [
        {"title": "no ids", "languages": [{"name": "Japanese"}], "approved": True},
        _rel("[Erai-raws] Kanojo - 01", langs=["Japanese"], mapped=[1]),
    ]
    pick = _pick_release_for_episode(releases, "sub")
    assert pick is not None and "Erai-raws" in pick["title"]


# ── Trusted-JP signal (the iVy mislabel case) ────────────────────────────


def test_trusted_jp_sub_signals() -> None:
    # Fansub-style leading [Group] tag → trusted.
    assert _is_trusted_jp_sub(_rel("[Erai-raws] Kanojo Okarishimasu - 10 [Multiple Subtitle]"))
    assert _is_trusted_jp_sub(_rel("[Moozzi2] Kanojo, Okarishimasu S3-10 [BD]"))
    # Explicit dual-audio / subtitle markers → trusted even without a bracket.
    assert _is_trusted_jp_sub(_rel("Kanojo Okarishimasu - 10 Dual Audio 1080p"))
    # Known group via releaseGroup field → trusted.
    assert _is_trusted_jp_sub(_rel("whatever", group="SubsPlease"))
    # Scene release that merely CLAIMS Japanese (the iVy dub) → NOT trusted.
    assert not _is_trusted_jp_sub(
        _rel("Rent-A-Girlfriend.S03E10.Spontaneous.Trip.EAC3.2.0.1080p.WEBRip.x265-iVy",
             langs=["Japanese"], group="iVy")
    )


def test_audio_pref_rank_demotes_claimed_sub() -> None:
    fansub = _rel("[Erai-raws] Kanojo - 10 [Multiple Subtitle]", langs=["Japanese"])
    ivy = _rel("Rent-A-Girlfriend.S03E10.WEBRip.x265-iVy", langs=["Japanese"], group="iVy")
    assert _audio_pref_rank(fansub, "sub") == 0   # genuine fansub
    assert _audio_pref_rank(ivy, "sub") == 1      # claims JP, demoted


def test_pick_prefers_real_fansub_over_ivy_dub() -> None:
    # The exact Rent-a-Girlfriend S3E10 situation: an iVy WEBRip that Sonarr
    # tags Japanese (but is actually an English dub) vs a genuine Erai-raws
    # fansub. Even though iVy is a higher quality tier (WEBRip > HDTV), the
    # genuine sub must win.
    releases = [
        _rel("Rent-A-Girlfriend.S03E10.EAC3.2.0.1080p.WEBRip.x265-iVy",
             langs=["Japanese"], group="iVy", mapped=[10], qw=400),
        _rel("[Erai-raws] Kanojo Okarishimasu 3rd Season - 10 [1080p][Multiple Subtitle]",
             langs=["Japanese"], group="Erai-raws", mapped=[10], qw=200),
    ]
    pick = _pick_release_for_episode(releases, "sub")
    assert pick is not None and "Erai-raws" in pick["title"]
