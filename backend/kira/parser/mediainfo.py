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


def duration_to_seconds(raw) -> int | None:
    """Map a MediaInfo Duration value to whole seconds.

    pymediainfo reports General/Video `duration` in MILLISECONDS (as an int,
    float, or numeric string — occasionally with a trailing decimal). Returns
    None for missing / zero / unparseable values so callers can abstain
    cleanly. A 22-minute episode → 1320; a 2h movie → 7200."""
    try:
        ms = float(raw)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    secs = int(round(ms / 1000.0))
    return secs or None


# ISO-639-1 / -639-2 codes and English names → canonical 639-2/B 3-letter code.
# Covers the languages a real media library realistically carries. Unknown but
# plausibly-valid short codes pass through (lowercased) so nothing's silently
# dropped; long unrecognized names are ignored as noise.
_LANG_NORMALIZE = {
    "en": "eng", "eng": "eng", "english": "eng",
    "ja": "jpn", "jp": "jpn", "jpn": "jpn", "japanese": "jpn",
    "fr": "fre", "fre": "fre", "fra": "fre", "french": "fre",
    "de": "ger", "ger": "ger", "deu": "ger", "german": "ger",
    "es": "spa", "spa": "spa", "spanish": "spa", "castilian": "spa",
    "it": "ita", "ita": "ita", "italian": "ita",
    "pt": "por", "por": "por", "portuguese": "por",
    "ru": "rus", "rus": "rus", "russian": "rus",
    "zh": "chi", "chi": "chi", "zho": "chi", "chinese": "chi",
    "mandarin": "chi", "cantonese": "chi",
    "ko": "kor", "kor": "kor", "korean": "kor",
    "nl": "dut", "dut": "dut", "nld": "dut", "dutch": "dut",
    "pl": "pol", "pol": "pol", "polish": "pol",
    "sv": "swe", "swe": "swe", "swedish": "swe",
    "no": "nor", "nor": "nor", "norwegian": "nor",
    "da": "dan", "dan": "dan", "danish": "dan",
    "fi": "fin", "fin": "fin", "finnish": "fin",
    "ar": "ara", "ara": "ara", "arabic": "ara",
    "hi": "hin", "hin": "hin", "hindi": "hin",
    "th": "tha", "tha": "tha", "thai": "tha",
    "tr": "tur", "tur": "tur", "turkish": "tur",
    "cs": "cze", "cze": "cze", "ces": "cze", "czech": "cze",
    "hu": "hun", "hun": "hun", "hungarian": "hun",
    "el": "gre", "gre": "gre", "ell": "gre", "greek": "gre",
    "he": "heb", "heb": "heb", "hebrew": "heb",
    "id": "ind", "ind": "ind", "indonesian": "ind",
    "vi": "vie", "vie": "vie", "vietnamese": "vie",
    "uk": "ukr", "ukr": "ukr", "ukrainian": "ukr",
}

# MediaInfo placeholders that mean "no real language" — never surfaced.
_LANG_IGNORE = {"und", "undetermined", "unknown", "mul", "mis", "zxx", ""}


def normalize_language(raw) -> str | None:
    """Map a MediaInfo track `language` to a canonical lowercase ISO-639-2/B
    3-letter code (eng, jpn, fre, …) for stable comparison + display.

    Handles 2-letter codes ("en"), 3-letter codes ("eng"), English names
    ("English"), and locale forms ("en-US", "pt-BR" → primary subtag). Returns
    None for missing / undetermined ("und") / unrecognized long strings. Unknown
    short alpha codes pass through (lowercased) so a rarer language still shows."""
    if not raw:
        return None
    t = str(raw).strip().lower()
    if t in _LANG_IGNORE:
        return None
    # locale → primary subtag: "en-us" → "en", "pt/br" → "pt"
    t = t.replace("_", "-").split("-")[0].split("/")[0].strip()
    if t in _LANG_NORMALIZE:
        return _LANG_NORMALIZE[t]
    # Unknown but plausibly a 2-3 letter code → keep; long names → drop as noise.
    if t.isalpha() and 2 <= len(t) <= 3:
        return t
    return None


def _parse_fast_then_full(path: str):
    """Parse with MediaInfo's minimum ParseSpeed when the installed pymediainfo
    supports it. Everything Kira reads (track formats, height, HDR transfer,
    channel counts, languages, container duration) lives in the container
    HEADERS — ParseSpeed 0 skips the deep bitstream scan (interlacement /
    bitrate sampling) that the default 0.5 pays for, which over a network share
    is most of the wall-clock per file. Falls back to a default-speed parse for
    older pymediainfo without the kwarg."""
    try:
        return _MediaInfo.parse(path, parse_speed=0)  # type: ignore[union-attr]
    except TypeError:
        return _MediaInfo.parse(path)  # type: ignore[union-attr]


def read_media_info(path: str) -> dict[str, Any] | None:
    """Read {quality, codec, hdr, channels, audio, duration} from the file.
    None when the lib is unavailable, the file can't be read, or it has no
    tracks. `duration` is whole seconds (M4 runtime corroboration).

    Video fields come from the first Video track; audio fields from the first
    Audio track (the default/primary track in practice). Duration prefers the
    General track (whole-container length) and falls back to the Video track.

    Uses a header-only fast parse first; if that yields nothing usable (or no
    duration — possible on odd containers), retries once at default speed so
    the fast path can never REDUCE coverage, only cost."""
    if not _AVAILABLE:
        return None
    try:
        info = _parse_fast_then_full(path)
    except Exception:
        return None
    out = _extract_tracks(info)
    if out is None or "duration" not in out:
        try:
            full = _MediaInfo.parse(path)  # type: ignore[union-attr]
        except Exception:
            return out
        out2 = _extract_tracks(full)
        if out2 is not None:
            return out2
    return out


def _extract_tracks(info) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    audio_langs: list[str] = []
    sub_langs: list[str] = []
    got_video = got_audio = False
    # Walk ALL tracks (no early break): video/primary-audio fields come from the
    # first of each, but languages are collected across every audio + text track
    # so dual-audio / multi-sub files surface every language.
    for track in getattr(info, "tracks", []):
        ttype = getattr(track, "track_type", None)
        if ttype == "General":
            if "duration" not in out:
                d = duration_to_seconds(getattr(track, "duration", None))
                if d:
                    out["duration"] = d
        elif ttype == "Video" and not got_video:
            got_video = True
            if "duration" not in out:
                d = duration_to_seconds(getattr(track, "duration", None))
                if d:
                    out["duration"] = d
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
        elif ttype == "Audio":
            if not got_audio:
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
            lang = normalize_language(getattr(track, "language", None))
            if lang and lang not in audio_langs:
                audio_langs.append(lang)
        elif ttype == "Text":
            lang = normalize_language(getattr(track, "language", None))
            if lang and lang not in sub_langs:
                sub_langs.append(lang)
    if audio_langs:
        out["audio_langs"] = audio_langs
    if sub_langs:
        out["sub_langs"] = sub_langs
    return out or None


def read_embedded_title(path: str) -> str | None:
    """Read the container's embedded title (General track ``title``, falling
    back to ``movie_name``). None when the lib is unavailable, the file can't
    be read, or no title tag is present.

    Caveat for callers: this tag is INCONSISTENT in the wild — scene/p2p
    releases routinely leave it blank or stuff the release-name junk in it, so
    it is a best-effort HINT, not a reliable identifier. Worth a shot only for
    a file whose FILENAME yielded nothing (it'd otherwise never match)."""
    if not _AVAILABLE:
        return None
    try:
        # Header-only fast parse: the title lives in the General track header, so
        # the deep bitstream scan the default speed pays for is wasted here —
        # and this runs serially per untitled file on the match path (NAS reads).
        info = _parse_fast_then_full(path)
    except Exception:
        return None
    for track in getattr(info, "tracks", []):
        if getattr(track, "track_type", None) == "General":
            for attr in ("title", "movie_name"):
                v = getattr(track, attr, None)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            return None
    return None


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

    # Duration (seconds) is NEVER carried by a filename, so it's always a
    # pure fill — there's nothing for it to conflict with. Set it whenever
    # MediaInfo read one and ParsedFile doesn't already have it (M4 runtime
    # corroboration). `duration` may be absent on ParsedFile for older rows.
    mi_dur = mi.get("duration")
    if mi_dur and not getattr(parsed, "duration", None):
        parsed.duration = mi_dur
        changed = True

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

    # Per-track languages (lists). Authoritative overwrites; fallback fills only
    # when empty. The container is the sole source for these — the filename
    # parser never sets them — so even fallback mode effectively always fills.
    for attr in ("audio_langs", "sub_langs"):
        mi_val = mi.get(attr)
        if not mi_val:
            continue
        cur = list(getattr(parsed, attr, None) or [])
        if authoritative:
            if cur != list(mi_val):
                setattr(parsed, attr, list(mi_val))
                changed = True
        elif not cur:
            setattr(parsed, attr, list(mi_val))
            changed = True
    return changed


def _safe_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
