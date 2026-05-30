"""Phase 16 — MediaInfo pure helpers + graceful degradation."""

from __future__ import annotations

from kira.parser import mediainfo
from kira.parser import ParsedFile


def test_height_to_quality() -> None:
    assert mediainfo.height_to_quality(2160) == "2160p"
    assert mediainfo.height_to_quality(1080) == "1080p"
    assert mediainfo.height_to_quality(1072) == "1080p"   # anamorphic tolerance
    assert mediainfo.height_to_quality(720) == "720p"
    assert mediainfo.height_to_quality(480) == "480p"
    assert mediainfo.height_to_quality(None) is None
    assert mediainfo.height_to_quality(0) is None


def test_normalize_codec() -> None:
    assert mediainfo.normalize_codec("HEVC") == "x265"
    assert mediainfo.normalize_codec("AVC") == "x264"
    assert mediainfo.normalize_codec("AV1") == "AV1"
    assert mediainfo.normalize_codec("VP9") == "VP9"
    assert mediainfo.normalize_codec("MPEG-2 Video") is None
    assert mediainfo.normalize_codec(None) is None


def test_hdr_label() -> None:
    assert mediainfo.hdr_label("Dolby Vision", None) == "DV"
    assert mediainfo.hdr_label("HDR10+", None) == "HDR10+"
    assert mediainfo.hdr_label("HDR10", None) == "HDR10"
    assert mediainfo.hdr_label(None, "SMPTE ST 2084") == "HDR10"
    assert mediainfo.hdr_label(None, "HLG") == "HLG"
    assert mediainfo.hdr_label(None, "BT.709") is None


def test_available_returns_bool() -> None:
    assert isinstance(mediainfo.available(), bool)


def test_read_media_info_degrades_when_unavailable() -> None:
    if not mediainfo.available():
        assert mediainfo.read_media_info("/nonexistent/file.mkv") is None


def test_enrich_parsed_fills_only_missing() -> None:
    pf = ParsedFile(original_filename="x.mkv", media_type="tv", title="X")
    assert pf.quality is None and pf.codec is None
    changed = mediainfo.enrich_parsed(pf, {"quality": "1080p", "codec": "x265", "hdr": "HDR10"})
    assert changed
    assert pf.quality == "1080p" and pf.codec == "x265" and pf.hdr == "HDR10"

    # Existing filename-derived values are NOT overridden.
    pf2 = ParsedFile(original_filename="y.mkv", media_type="tv", title="Y",
                     quality="720p", codec="x264")
    mediainfo.enrich_parsed(pf2, {"quality": "1080p", "codec": "x265"})
    assert pf2.quality == "720p" and pf2.codec == "x264"


def test_enrich_parsed_none_is_noop() -> None:
    pf = ParsedFile(original_filename="x.mkv", media_type="tv", title="X")
    assert mediainfo.enrich_parsed(pf, None) is False


def test_channels_label() -> None:
    assert mediainfo.channels_label(2) == "2.0"
    assert mediainfo.channels_label(6) == "5.1"
    assert mediainfo.channels_label(8) == "7.1"
    assert mediainfo.channels_label(1) == "1.0"
    assert mediainfo.channels_label(12) == "12.0"   # exotic — never blank-wrong
    assert mediainfo.channels_label(0) is None
    assert mediainfo.channels_label(None) is None


def test_normalize_audio() -> None:
    assert mediainfo.normalize_audio("AC-3") == "AC3"
    assert mediainfo.normalize_audio("E-AC-3") == "EAC3"
    assert mediainfo.normalize_audio("DTS") == "DTS"
    assert mediainfo.normalize_audio("AAC") == "AAC"
    assert mediainfo.normalize_audio("FLAC") == "FLAC"
    assert mediainfo.normalize_audio("MLP FBA") == "TrueHD"
    # Commercial name promotes the lossless flavor.
    assert mediainfo.normalize_audio("MLP FBA", "Dolby TrueHD with Atmos") == "TrueHD"
    assert mediainfo.normalize_audio("DTS", "DTS-HD Master Audio") == "DTS-HD"
    assert mediainfo.normalize_audio("MPEG Audio") is None
    assert mediainfo.normalize_audio(None) is None


def test_enrich_parsed_fills_channels_and_audio() -> None:
    pf = ParsedFile(original_filename="x.mkv", media_type="tv", title="X")
    changed = mediainfo.enrich_parsed(pf, {"channels": "5.1", "audio": "DTS"})
    assert changed
    assert pf.channels == "5.1"
    assert pf.audio == ["DTS"]


def test_enrich_parsed_authoritative_overrides() -> None:
    # Filename claimed 720p/x264/stereo; the real file is 1080p/x265/5.1.
    pf = ParsedFile(
        original_filename="y.mkv", media_type="tv", title="Y",
        quality="720p", codec="x264", audio=["AAC"],
    )
    changed = mediainfo.enrich_parsed(
        pf,
        {"quality": "1080p", "codec": "x265", "hdr": "HDR10", "channels": "5.1", "audio": "DTS"},
        authoritative=True,
    )
    assert changed
    assert pf.quality == "1080p" and pf.codec == "x265" and pf.hdr == "HDR10"
    assert pf.channels == "5.1" and pf.audio == ["DTS"]


def test_enrich_parsed_authoritative_keeps_value_when_mediainfo_blank() -> None:
    # MediaInfo couldn't read codec → the filename's codec survives even in
    # authoritative mode (we only override fields MediaInfo actually has).
    pf = ParsedFile(original_filename="z.mkv", media_type="tv", title="Z",
                    quality="720p", codec="x264")
    mediainfo.enrich_parsed(pf, {"quality": "1080p"}, authoritative=True)
    assert pf.quality == "1080p"   # MediaInfo had it → overridden
    assert pf.codec == "x264"      # MediaInfo lacked it → kept
