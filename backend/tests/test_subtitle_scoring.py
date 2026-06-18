"""The subtitle scorer — the 'good subtitle' metric + sync verdict + reasons."""
from __future__ import annotations

from kira.subtitles.model import SubtitleCandidate
from kira.subtitles.scoring import ReleaseInfo, rank, score_candidate


def _video():
    return ReleaseInfo.from_video(
        "Attack on Titan - S01E01 [Moozzi2] BluRay 1080p x265.mkv",
        {"quality": "1080p", "source": "BluRay", "codec": "x265", "release_group": "Moozzi2"},
    )


def test_release_info_parses_filename_fallback():
    ri = ReleaseInfo.from_video("Show.S01E01.WEB-DL.720p.x264-NTb.mkv", {})
    assert ri.resolution == "720p"
    assert ri.source == "webdl"
    assert ri.codec == "avc"
    assert ri.group == "ntb"


def test_hash_match_is_guaranteed_and_high():
    c = SubtitleCandidate(provider="opensubtitles", language="en", hash_match=True,
                          release_name="whatever")
    score_candidate(c, _video())
    assert c.sync == "guaranteed"
    assert c.score >= 70
    assert any("hash" in r for r in c.reasons)


def test_embedded_is_guaranteed():
    c = SubtitleCandidate(provider="embedded", language="en", from_embedded=True)
    score_candidate(c, _video())
    assert c.sync == "guaranteed"
    assert c.score >= 70


def test_release_group_match_is_likely_sync():
    c = SubtitleCandidate(provider="subsource", language="en",
                          release_name="[Moozzi2] Attack on Titan 01 BluRay 1080p")
    score_candidate(c, _video())
    assert c.sync == "likely"
    assert any("moozzi2" in r.lower() for r in c.reasons)
    assert "1080p" in c.reasons


def test_source_plus_resolution_match_is_likely():
    c = SubtitleCandidate(provider="subdl", language="en",
                          release_name="Attack.on.Titan.S01E01.BluRay.1080p.x264-OTHER")
    score_candidate(c, _video())
    # different group, but source+resolution both line up
    assert c.sync == "likely"


def test_title_only_is_unknown_sync():
    c = SubtitleCandidate(provider="podnapisi", language="en", release_name="")
    score_candidate(c, _video())
    assert c.sync == "unknown"


def test_pack_scores_below_exact_episode():
    pack = SubtitleCandidate(provider="subsource", language="en", release_name="Nana S01 complete", is_pack=True)
    exact = SubtitleCandidate(provider="subsource", language="en", release_name="Nana S01E01")
    score_candidate(pack, _video()); score_candidate(exact, _video())
    assert exact.score > pack.score
    assert any("season pack" in r for r in pack.reasons)


def test_downloads_and_rating_add_trust():
    bare = SubtitleCandidate(provider="subsource", language="en", release_name="x")
    loved = SubtitleCandidate(provider="subsource", language="en", release_name="x",
                              downloads=5000, rating=0.95)
    score_candidate(bare, _video()); score_candidate(loved, _video())
    assert loved.score > bare.score


def test_hi_preference_only_penalizes_non_sdh():
    sdh = SubtitleCandidate(provider="opensubtitles", language="en", hearing_impaired=True, release_name="x")
    plain = SubtitleCandidate(provider="opensubtitles", language="en", hearing_impaired=False, release_name="x")
    score_candidate(sdh, _video(), want_hi="only")
    score_candidate(plain, _video(), want_hi="only")
    assert sdh.score > plain.score


def test_rank_orders_best_first_hash_beats_release():
    cands = [
        SubtitleCandidate(provider="podnapisi", language="en", release_name=""),
        SubtitleCandidate(provider="subsource", language="en", release_name="[Moozzi2] BluRay 1080p"),
        SubtitleCandidate(provider="opensubtitles", language="en", hash_match=True, release_name="random"),
    ]
    ranked = rank(cands, _video())
    assert ranked[0].provider == "opensubtitles"   # hash wins
    assert ranked[-1].provider == "podnapisi"        # title-only loses
