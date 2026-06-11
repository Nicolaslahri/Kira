"""M1 — ReleaseInfo dataset expansion + token-table externalization.

Covers the expanded curated tables (newer sources/codecs/resolutions/audio),
the new case-sensitive release-flag stripper, and the scene-rules.json
externalization path via `reload_rules()`.
"""

from __future__ import annotations

import json

from kira.parser import format_stripper as fs


# ── Release flags (case-sensitive clutter) ─────────────────────────────────


def test_release_flag_proper_stripped() -> None:
    cleaned, tok = fs.strip("Movie.PROPER.2020.1080p.BluRay.x264-GRP.mkv")
    assert "PROPER" not in cleaned
    assert "PROPER" in tok.release_flags


def test_release_flag_repack_stripped() -> None:
    cleaned, tok = fs.strip("Show.S01E01.REPACK.1080p.WEB-DL.mkv")
    assert "REPACK" not in cleaned
    assert "REPACK" in tok.release_flags


def test_multiple_release_flags_captured() -> None:
    _, tok = fs.strip("Show.S01E01.PROPER.REPACK.1080p.WEB-DL-GRP.mkv")
    assert set(tok.release_flags) == {"PROPER", "REPACK"}


def test_lowercase_proper_in_title_preserved() -> None:
    """A real title word 'Proper' (not ALL-CAPS) must survive untouched."""
    cleaned, tok = fs.strip("The.Proper.Way.2019.1080p.mkv")
    assert "Proper" in cleaned
    assert tok.release_flags == []


def test_titlecase_internal_word_preserved() -> None:
    """'Internal Affairs' — the title word must not be eaten by the INTERNAL flag."""
    cleaned, _ = fs.strip("Internal.Affairs.1990.1080p.BluRay.mkv")
    assert "Internal" in cleaned


def test_leading_allcaps_flag_word_preserved() -> None:
    """A film literally titled 'PROPER' leads the name → no-start guard keeps it."""
    cleaned, tok = fs.strip("PROPER.2018.1080p.WEB-DL.mkv")
    assert "PROPER" in cleaned
    assert tok.release_flags == []


# ── Expanded curated tables ────────────────────────────────────────────────


def test_expanded_resolution_1440p() -> None:
    _, tok = fs.strip("Doc.2021.1440p.WEB-DL.mkv")
    assert tok.quality == "1440p"


def test_expanded_source_uhd_bluray() -> None:
    _, tok = fs.strip("Film.2019.2160p.UHD-BluRay.x265.mkv")
    assert tok.source == "UHD-BluRay"


def test_expanded_codec_mpeg2() -> None:
    _, tok = fs.strip("Old.Show.S01E01.MPEG-2.mkv")
    assert tok.codec == "MPEG-2"


def test_expanded_audio_dts_hd_ma() -> None:
    _, tok = fs.strip("Movie.2018.1080p.BluRay.DTS-HD.MA.x264.mkv")
    assert any("DTS-HD" in a for a in tok.audio)


def test_expanded_edition_ultimate() -> None:
    cleaned, tok = fs.strip("Film.2009.Ultimate.Edition.1080p.BluRay.mkv")
    assert tok.edition is not None
    assert "ultimate" not in cleaned.lower()


def test_expanded_hdr_dolby_vision_dovi() -> None:
    _, tok = fs.strip("Movie.2022.2160p.DoVi.HDR10.x265.mkv")
    assert tok.hdr is not None


def test_streaming_tag_pcok_stripped() -> None:
    cleaned, tok = fs.strip("Show.S01E01.PCOK.WEB-DL.1080p.mkv")
    assert "PCOK" not in cleaned


# ── Regression: known-good behaviour preserved ─────────────────────────────


def test_web_dl_still_not_a_release_group() -> None:
    _, tok = fs.strip("Breaking.Bad.S01E05.720p.WEB-DL.mkv")
    assert tok.release_group is None
    assert tok.source == "WEB-DL"


def test_ambiguous_max_at_start_kept() -> None:
    """'Max' leading a filename is the movie, not the MAX streaming tag."""
    cleaned, _ = fs.strip("Max.2015.1080p.BluRay.mkv")
    assert "Max" in cleaned


# ── Externalization via scene-rules.json ───────────────────────────────────


def test_user_extra_source_via_reload(monkeypatch, tmp_path) -> None:
    p = tmp_path / "scene-rules.json"
    p.write_text(json.dumps({"sources": ["MYNET"]}))
    monkeypatch.setenv("KIRA_SCENE_RULES", str(p))
    try:
        fs.reload_rules()
        cleaned, tok = fs.strip("Movie.2020.1080p.MYNET.x264-GRP.mkv")
        assert tok.source == "MYNET"
        assert "MYNET" not in cleaned
    finally:
        monkeypatch.delenv("KIRA_SCENE_RULES", raising=False)
        fs.reload_rules()  # restore curated-only state for other tests


def test_user_extra_release_flag_case_preserved(monkeypatch, tmp_path) -> None:
    p = tmp_path / "scene-rules.json"
    p.write_text(json.dumps({"release_flags": ["SCENEFLAG"]}))
    monkeypatch.setenv("KIRA_SCENE_RULES", str(p))
    try:
        fs.reload_rules()
        cleaned, tok = fs.strip("Movie.2020.SCENEFLAG.1080p.WEB-DL.mkv")
        assert "SCENEFLAG" not in cleaned
        assert "SCENEFLAG" in tok.release_flags
    finally:
        monkeypatch.delenv("KIRA_SCENE_RULES", raising=False)
        fs.reload_rules()


def test_reload_with_no_rules_is_curated_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("KIRA_SCENE_RULES", str(tmp_path / "absent.json"))
    try:
        fs.reload_rules()
        # A curated token still strips; an unknown one does not.
        _, tok = fs.strip("Movie.2020.1080p.BluRay.x264-GRP.mkv")
        assert tok.source == "BluRay"
    finally:
        monkeypatch.delenv("KIRA_SCENE_RULES", raising=False)
        fs.reload_rules()


# ── Streaming platform tags vs. source TYPE (dupe-ranker correctness) ────────
def test_platform_does_not_shadow_real_source() -> None:
    # AMZN is the platform, WEB-DL the delivery type. Even though AMZN appears
    # first, the stored source must be the real type — else the dedupe ranker
    # mis-ranks the WEB-DL (treated as unknown "AMZN") below a WEBRip.
    _, tok = fs.strip("Euphoria.US.S03E01.REPACK.1080p.AMZN.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv")
    assert tok.source == "WEB-DL"


def test_platform_alone_implies_webdl() -> None:
    _, tok = fs.strip("Show.S01E02.1080p.AMZN.x264-GRP.mkv")
    assert tok.source == "WEB-DL"


def test_platform_stripped_from_title() -> None:
    cleaned, _ = fs.strip("Show.S01E02.1080p.NFLX.WEB-DL.x265-GRP.mkv")
    assert "NFLX" not in cleaned


def test_real_disc_source_unaffected() -> None:
    _, tok = fs.strip("Movie.2021.1080p.BluRay.x264-GRP.mkv")
    assert tok.source == "BluRay"


def test_streaming_ambiguous_implies_webdl() -> None:
    _, tok = fs.strip("The.Boys.S01E01.1080p.HMAX.x264-GRP.mkv")
    assert tok.source == "WEB-DL"


def test_max_title_not_eaten_as_platform() -> None:
    cleaned, tok = fs.strip("Max.2015.1080p.WEB-DL.mkv")
    assert tok.source == "WEB-DL"
    assert "Max" in cleaned


def test_hdr10plus_spelled_out_does_not_poison_title_or_year():
    # "HDR10Plus" (PSA et al.) wasn't in the HDR list, and boundary discipline
    # means HDR10 can't partial-match inside it — the unknown token blocked the
    # end-anchored bare-year detection and dragged "2025 HDR10Plus" into the
    # title, so "Nobody 2" fuzzy-matched part 1 ("Nobody" 2021) instead of the
    # sequel. The spelled-out token must strip like its symbol form.
    from kira.parser import parse_filename
    p = parse_filename(
        "Nobody.2.2025.2160p.HDR10Plus.DV.WEBRip.DDP5.1.Atmos.X265.HEVC-PSA.mkv"
    )
    assert p.title == "Nobody 2"
    assert p.year == 2025
    assert p.hdr == "HDR10Plus"
    assert p.quality == "2160p"


def test_dotted_channel_suffix_and_multi_count_do_not_poison_year():
    # "DDP.5.1" (dotted channels) left a bare "5 1" in the title, and "Multi3"
    # (language-count flag, mixed case) was unknown — both blocked the
    # end-anchored year and the 2025 remake nearly lost to the 2010 original.
    from kira.parser import parse_filename
    p = parse_filename("How.to.Train.Your.Dragon.2025.1080p.BluRay.AV1.DDP.5.1.Multi3-dAV1nci.mkv")
    assert p.title == "How to Train Your Dragon"
    assert p.year == 2025
    # Mixed-case "Multi" strips ONLY with a digit — a real title word survives.
    p2 = parse_filename("Multiplicity.1996.1080p.BluRay.mkv")
    assert p2.title == "Multiplicity" and p2.year == 1996
