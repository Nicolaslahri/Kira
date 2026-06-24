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


def test_enrich_parsed_fills_music_tech_specs() -> None:
    """Music audio specs (MediaInfo) → ParsedFile, including a legitimate
    `lossless=False` (which the generic `_set` would have skipped) and a clean
    parsed_data round-trip. `audio_bit_depth`, NOT `bit_depth` (the video field)."""
    pf = ParsedFile(original_filename="track.flac", media_type="music", title="Track")
    changed = mediainfo.enrich_parsed(
        pf, {"audio_bitrate": 1024, "sample_rate": 96000, "audio_bit_depth": 24, "lossless": True},
        authoritative=True,
    )
    assert changed
    assert pf.audio_bitrate == 1024 and pf.sample_rate == 96000
    assert pf.audio_bit_depth == 24 and pf.lossless is True
    assert pf.bit_depth is None    # the VIDEO field must stay untouched
    # round-trips through parsed_data unchanged
    assert ParsedFile(**pf.to_dict()).audio_bit_depth == 24
    # a lossy file: bitrate set, no bit depth, lossless=False is recorded
    lossy = ParsedFile(original_filename="y.mp3", media_type="music", title="Y")
    mediainfo.enrich_parsed(lossy, {"audio_bitrate": 320, "lossless": False}, authoritative=True)
    assert lossy.audio_bitrate == 320 and lossy.audio_bit_depth is None and lossy.lossless is False


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


# ── per-track languages (roadmap item c) ─────────────────────────────────────
def test_normalize_language() -> None:
    assert mediainfo.normalize_language("en") == "eng"
    assert mediainfo.normalize_language("eng") == "eng"
    assert mediainfo.normalize_language("English") == "eng"
    assert mediainfo.normalize_language("ja") == "jpn"
    assert mediainfo.normalize_language("Japanese") == "jpn"
    assert mediainfo.normalize_language("en-US") == "eng"   # locale → primary subtag
    assert mediainfo.normalize_language("pt-BR") == "por"
    assert mediainfo.normalize_language("und") is None      # undetermined
    assert mediainfo.normalize_language("") is None
    assert mediainfo.normalize_language(None) is None
    assert mediainfo.normalize_language("xyz") == "xyz"     # unknown short code passes
    assert mediainfo.normalize_language("klingon") is None  # unknown long name dropped


def test_enrich_parsed_fills_languages() -> None:
    pf = ParsedFile(original_filename="x.mkv", media_type="tv", title="X")
    changed = mediainfo.enrich_parsed(pf, {"audio_langs": ["jpn", "eng"], "sub_langs": ["eng"]})
    assert changed
    assert pf.audio_langs == ["jpn", "eng"]
    assert pf.sub_langs == ["eng"]


def test_enrich_parsed_authoritative_overrides_languages() -> None:
    pf = ParsedFile(original_filename="y.mkv", media_type="tv", title="Y",
                    audio_langs=["eng"], sub_langs=[])
    changed = mediainfo.enrich_parsed(
        pf, {"audio_langs": ["jpn", "eng"], "sub_langs": ["eng", "spa"]}, authoritative=True,
    )
    assert changed
    assert pf.audio_langs == ["jpn", "eng"]
    assert pf.sub_langs == ["eng", "spa"]


def test_read_media_info_collects_track_languages(monkeypatch) -> None:
    class _T:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _Info:
        tracks = [
            _T(track_type="General", duration="1320000"),
            _T(track_type="Video", height=1080, format="HEVC"),
            _T(track_type="Audio", language="ja", channel_s=6, format="DTS"),
            _T(track_type="Audio", language="en", channel_s=2, format="AAC"),
            _T(track_type="Text", language="eng"),
            _T(track_type="Text", language="spa"),
            _T(track_type="Text", language="en"),   # dup of eng → deduped
        ]

    class _MI:
        @staticmethod
        def parse(_path):
            return _Info()

    monkeypatch.setattr(mediainfo, "_AVAILABLE", True)
    monkeypatch.setattr(mediainfo, "_MediaInfo", _MI)
    out = mediainfo.read_media_info("/fake.mkv")
    assert out is not None
    # languages collected across ALL tracks, in order, deduped + normalized
    assert out["audio_langs"] == ["jpn", "eng"]
    assert out["sub_langs"] == ["eng", "spa"]
    # primary (first) video + audio fields still come from the first of each
    assert out["quality"] == "1080p" and out["codec"] == "x265"
    assert out["channels"] == "5.1" and out["audio"] == "DTS"
    assert out["duration"] == 1320


def test_read_media_info_no_languages_omits_keys(monkeypatch) -> None:
    class _T:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _Info:
        tracks = [
            _T(track_type="Video", height=720, format="AVC"),
            _T(track_type="Audio", channel_s=2, format="AAC"),  # no language attr
        ]

    class _MI:
        @staticmethod
        def parse(_path):
            return _Info()

    monkeypatch.setattr(mediainfo, "_AVAILABLE", True)
    monkeypatch.setattr(mediainfo, "_MediaInfo", _MI)
    out = mediainfo.read_media_info("/fake.mkv")
    assert out is not None
    assert "audio_langs" not in out and "sub_langs" not in out  # omitted, not []
