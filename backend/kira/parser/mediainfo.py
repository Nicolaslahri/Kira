"""Phase 16 — true file metadata via pymediainfo.

The format-stripper reads quality/codec/HDR from the FILENAME only, so a
mislabeled or tag-less file shows wrong/no chips. This module reads the real
values from the file's container headers (resolution, codec, HDR) and is used
as a FALLBACK to fill fields the filename didn't carry.

Graceful degradation is first-class: `pymediainfo` (and the native
`libmediainfo`) may not be installed. Every entry point returns None / no-ops
in that case, so the pipeline behaves exactly as filename-only. Install
`pymediainfo` to light it up — no code change needed.

The pure mapping helpers (`height_to_quality`, `normalize_codec`,
`hdr_label`, `enrich_parsed`) have no dependency on the native lib and are
unit-tested directly.
"""
from __future__ import annotations

from typing import Any

try:  # native lib is optional
    from pymediainfo import MediaInfo as _MediaInfo  # type: ignore
    _AVAILABLE = True
except Exception:  # ModuleNotFoundError OR libmediainfo missing
    _MediaInfo = None  # type: ignore
    _AVAILABLE = False


def available() -> bool:
    """True when pymediainfo + libmediainfo are importable."""
    return _AVAILABLE


def height_to_quality(height: int | None) -> str | None:
    """Map a pixel height to the conventional quality label, tolerating the
    slightly-off heights anamorphic / cropped encodes produce (1072, 1088)."""
    if not height or height <= 0:
        return None
    if height >= 4000:
        return "4320p"
    if height >= 1900:
        return "2160p"
    if height >= 1300:
        return "1440p"
    if height >= 950:
        return "1080p"
    if height >= 620:
        return "720p"
    if height >= 520:
        return "576p"
    if height >= 380:
        return "480p"
    return f"{height}p"


def normalize_codec(fmt: str | None) -> str | None:
    """Map a MediaInfo video format to the filename-style codec token."""
    f = (fmt or "").upper()
    if "HEVC" in f or "H265" in f or "H.265" in f:
        return "x265"
    if "AVC" in f or "H264" in f or "H.264" in f:
        return "x264"
    if "AV1" in f:
        return "AV1"
    if "VP9" in f:
        return "VP9"
    return None  # MPEG-2/4, VC-1, etc. — too generic to surface as a chip


def hdr_label(hdr_format: str | None, transfer: str | None) -> str | None:
    """Classify HDR flavor from MediaInfo's HDR_format + transfer fields."""
    s = f"{hdr_format or ''} {transfer or ''}".lower()
    if "dolby vision" in s or "dovi" in s:
        return "DV"
    if "hdr10+" in s or "hdr10plus" in s:
        return "HDR10+"
    if "hdr10" in s or "smpte st 2084" in s or "pq" in s:
        return "HDR10"
    if "hlg" in s or "hybrid log" in s:
        return "HLG"
    return None


# Conventional speaker-layout labels keyed by raw channel count. Covers the
# layouts that actually appear in releases; anything exotic falls back to
# "N.0" so the token is never blank-but-wrong.
_CHANNEL_LABELS = {1: "1.0", 2: "2.0", 3: "2.1", 4: "4.0", 5: "4.1", 6: "5.1", 7: "6.1", 8: "7.1", 10: "9.1"}


def channels_label(count: int | None) -> str | None:
    """Map a raw audio channel count to the conventional layout label
    (6 → "5.1", 8 → "7.1"). None when unknown."""
    if not count or count <= 0:
        return None
    return _CHANNEL_LABELS.get(count, f"{count}.0")


def normalize_audio(fmt: str | None, commercial: str | None = None) -> str | None:
    """Map a MediaInfo audio format to the filename-style codec token.
    Prefers the commercial name for the lossless/object-based flavors."""
    c = (commercial or "").lower()
    if "truehd" in c or "atmos" in c:
        return "TrueHD"
    if "dts-hd" in c or "dts:x" in c or "dts-x" in c:
        return "DTS-HD"
    f = (fmt or "").upper().replace("-", "").replace(" ", "")
    if "MLPFBA" in f or "TRUEHD" in f:
        return "TrueHD"
    if "EAC3" in f:
        return "EAC3"
    if f == "AC3" or "AC3" in f:
        return "AC3"
    if "DTS" in f:
        return "DTS"
    if "AAC" in f:
        return "AAC"
    if "FLAC" in f:
        return "FLAC"
    if "OPUS" in f:
        return "Opus"
    if "VORBIS" in f:
        return "Vorbis"
    if "PCM" in f or "ADPCM" in f:
        return "PCM"
    return None


def read_media_info(path: str) -> dict[str, Any] | None:
    """Read {quality, codec, hdr, channels, audio} from the file. None when
    the lib is unavailable, the file can't be read, or it has no tracks.

    Video fields come from the first Video track; audio fields from the first
    Audio track (the default/primary track in practice)."""
    if not _AVAILABLE:
        return None
    try:
        info = _MediaInfo.parse(path)  # type: ignore[union-attr]
    except Exception:
        return None
    out: dict[str, Any] = {}
    got_video = got_audio = False
    for track in getattr(info, "tracks", []):
        ttype = getattr(track, "track_type", None)
        if ttype == "Video" and not got_video:
            got_video = True
            q = height_to_quality(_safe_int(getattr(track, "height", None)))
            if q:
                out["quality"] = q
            codec = normalize_codec(getattr(track, "format", None))
            if codec:
                out["codec"] = codec
            hdr = hdr_label(
                getattr(track, "hdr_format", None),
                getattr(track, "transfer_characteristics", None),
            )
            if hdr:
                out["hdr"] = hdr
        elif ttype == "Audio" and not got_audio:
            got_audio = True
            ch = channels_label(_safe_int(getattr(track, "channel_s", None)
                                          or getattr(track, "channels", None)))
            if ch:
                out["channels"] = ch
            ac = normalize_audio(
                getattr(track, "format", None),
                getattr(track, "format_commercial_name", None)
                or getattr(track, "commercial_name", None),
            )
            if ac:
                out["audio"] = ac
        if got_video and got_audio:
            break
    return out or None


def enrich_parsed(parsed, mi: dict[str, Any] | None, authoritative: bool = False) -> bool:
    """Merge a media-info dict onto a ParsedFile.

    Default (authoritative=False): FALLBACK only — fill fields the filename
    didn't supply, never override (the filename's explicit release tag is
    often more specific).

    authoritative=True: the file's real container metadata WINS — override
    quality/codec/hdr/channels/audio whenever MediaInfo has a value. A field
    MediaInfo couldn't read leaves the filename-derived value untouched.

    Returns True when anything changed."""
    if not mi:
        return False
    changed = False

    def _set(attr: str, key: str, current) -> bool:
        val = mi.get(key)
        if not val:
            return False
        if authoritative:
            if current != val:
                setattr(parsed, attr, val)
                return True
            return False
        if not current:
            setattr(parsed, attr, val)
            return True
        return False

    changed |= _set("quality", "quality", parsed.quality)
    changed |= _set("codec", "codec", parsed.codec)
    changed |= _set("hdr", "hdr", parsed.hdr)
    changed |= _set("channels", "channels", parsed.channels)

    # audio is a list on ParsedFile; MediaInfo gives one primary codec.
    mi_audio = mi.get("audio")
    if mi_audio:
        if authoritative:
            if parsed.audio != [mi_audio]:
                parsed.audio = [mi_audio]
                changed = True
        elif not parsed.audio:
            parsed.audio = [mi_audio]
            changed = True
    return changed


def _safe_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
