"""Strip codec/quality/source tokens from filenames, capturing them as metadata.

Done before title extraction so noise doesn't pollute the title.

Boundary discipline: tokens that contain symbols (`HDR10+`, `H.264`) can't
use `\\b` — the `+` and `.` are non-word chars so `\\b` never asserts. We
use an explicit separator class `_SEP` for those.

Short-token discipline: `MAX`, `TS`, `HMAX` etc. collide with real movie
titles (the 2015 movie "Max", "TS Spivet"). These match only when preceded
by a known separator — so `Max.2015.1080p` (no separator before "Max")
keeps the title, while `Movie.2015.MAX.WEB-DL` correctly strips it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Token tables — keep small + curated. Order matters where prefixes overlap
# (e.g. "WEB-DL" must match before "WEB" alone).
SOURCES = [
    "BluRay", "Blu-Ray", "BDRip", "BRRip", "BDRemux", "REMUX",
    "WEB-DL", "WEBRip", "WEB-Rip", "WEB",
    "HDRip", "HDTV", "PDTV", "DVDRip", "DVDScr", "DVD",
    "CAM", "TC",
    "AMZN", "ATVP", "DSNP", "HULU", "NFLX",
]
# Sources that double as ordinary English when title-cased ("Max", "Ts", "Nf").
# Matched case-SENSITIVELY (must be ALL-CAPS) AND must be preceded by a
# separator so they can't be the first token of a filename.
SOURCES_AMBIGUOUS = ["HMAX", "MAX", "NF", "TS"]

CODECS = [
    "x265", "x264", "H\\.265", "H\\.264", "H265", "H264",
    "HEVC", "AVC", "VP9", "AV1", "XviD", "DivX",
]

# Resolutions kept to unambiguous tokens only — "HD" and "SD" used to be
# here but they're real English / abbreviations that collided with titles
# ("HD: High Definition" — same word everywhere).
RESOLUTIONS = [
    "2160p", "1080p", "720p", "480p", "4K", "UHD", "FHD",
]

# Some release groups (Moozzi2, certain Beatrice-Raws sets) print resolution
# as `1920x1080` / `1280x720` instead of `1080p` / `720p`. Detected separately
# below so we can normalize them back to the standard `p` form when capturing.
_WXH_TO_P: dict[str, str] = {
    "3840x2160": "2160p",
    "1920x1080": "1080p",
    "1280x720":  "720p",
    "854x480":   "480p",
    "720x576":   "576p",
    "720x480":   "480p",
    "640x480":   "480p",
}

AUDIO = [
    "DTS-HD", "DTS-X", "DTS", "TrueHD", "Atmos",
    "DDP5\\.1", "DDP7\\.1", "DDP", "DD5\\.1", "DD7\\.1", "DD",
    "AC3", "EAC3", "E-AC-3",
    "AAC2\\.0", "AAC5\\.1", "AAC", "FLAC", "MP3", "OGG",
]

# Subtitle markers — separate from audio so a UI showing "Audio: AAC, JAP"
# doesn't claim JAP is an audio codec.
SUBTITLES = [
    "Multi-Subs", "Multi-Sub", "MSubs", "Subbed", "Dubbed",
    "JAP", "ENG", "FRE", "GER", "ITA", "SPA",
]

EDITIONS = [
    "Director'?s\\.?Cut", "Extended", "Remastered", "Unrated", "IMAX",
    "Theatrical", "Special\\.?Edition", "Final\\.?Cut", "Uncut",
    "Anniversary", "Criterion", "LIMITED",
]

HDR = ["HDR10\\+", "HDR10", "HDR", "DV", "Dolby\\.?Vision"]

# Bit depth — 10-bit encodes (Hi10P / x265 10-bit) are the gold standard
# for anime because they kill the color banding 8-bit can't avoid in
# gradients (skies, dark scenes). Surfaced for the dedupe ranker so a
# 10-bit version beats an 8-bit version of the same source.
BIT_DEPTH = ["10[\\.\\-]?bit", "Hi10P", "Hi10", "8[\\.\\-]?bit"]

# Known video/audio extensions — anything else after the last "." stays in
# the filename. Without this, a folder named "The.Office.US.S01" would
# have "S01" stripped as the "extension".
_MEDIA_EXTS = {
    ".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".webm", ".ts", ".mts",
    ".flv", ".vob", ".mpg", ".mpeg",
    ".mp3", ".flac", ".m4a", ".ogg", ".oga", ".opus", ".wav", ".wma", ".aac",
}


def _alt(words: list[str]) -> str:
    """Build an alternation regex, longest-first."""
    return "|".join(sorted(words, key=len, reverse=True))


# Token-boundary primitives. Python `re` requires fixed-width lookbehind,
# so we can't put `^` inside the lookbehind itself — `^` is a sibling
# alternative outside the lookbehind. The lookahead is unrestricted and
# can mix `$` with a char class freely.
#
# `_LEFT`  matches: start-of-string OR preceded by a separator char.
# `_RIGHT` matches: end-of-string OR followed by a separator char.
# Including `+` in the set lets symbol-bearing tokens like `HDR10+` match.
_LEFT_SEP  = r"(?:^|(?<=[ \[\]._\-+]))"
_RIGHT_SEP = r"(?=$|[ \[\]._\-+])"

_RESOLUTION_RE = re.compile(rf"{_LEFT_SEP}({_alt(RESOLUTIONS)}){_RIGHT_SEP}", re.IGNORECASE)
# Matches WxH like `1920x1080` only when both sides are in the known set —
# raw `\d+x\d+` would over-match (anime fansubs use the same syntax for
# `12x05` episode notation, which extract_sxe wants to keep intact).
_RESOLUTION_WXH_RE = re.compile(
    rf"{_LEFT_SEP}({_alt(list(_WXH_TO_P.keys()))}){_RIGHT_SEP}",
    re.IGNORECASE,
)
_SOURCE_RE     = re.compile(rf"{_LEFT_SEP}({_alt(SOURCES)}){_RIGHT_SEP}", re.IGNORECASE)
_CODEC_RE      = re.compile(rf"{_LEFT_SEP}({_alt(CODECS)}){_RIGHT_SEP}", re.IGNORECASE)
_AUDIO_RE      = re.compile(rf"{_LEFT_SEP}({_alt(AUDIO)}){_RIGHT_SEP}", re.IGNORECASE)
_SUBTITLES_RE  = re.compile(rf"{_LEFT_SEP}({_alt(SUBTITLES)}){_RIGHT_SEP}", re.IGNORECASE)
_EDITION_RE    = re.compile(rf"{_LEFT_SEP}({_alt(EDITIONS)}){_RIGHT_SEP}", re.IGNORECASE)
_HDR_RE        = re.compile(rf"{_LEFT_SEP}({_alt(HDR)}){_RIGHT_SEP}", re.IGNORECASE)
_BIT_DEPTH_RE  = re.compile(rf"{_LEFT_SEP}({_alt(BIT_DEPTH)}){_RIGHT_SEP}", re.IGNORECASE)

# Ambiguous short sources — CASE-SENSITIVE (require ALL-CAPS) AND MUST be
# preceded by a separator char (NOT start of string). "Max.2015" keeps its
# title; "Movie.2015.MAX.WEB-DL" loses the MAX.
_SOURCE_AMBIG_RE = re.compile(rf"(?<=[._\- ])({_alt(SOURCES_AMBIGUOUS)}){_RIGHT_SEP}")

# Release group: trailing -GROUP_NAME after the last dash, or [GROUP] at start.
# Must start with a letter so plain "-38" (absolute episode) doesn't match.
_TRAILING_GROUP_RE = re.compile(r"-([A-Za-z][A-Za-z0-9]*)$")
_LEADING_GROUP_RE  = re.compile(r"^\[([^\]]+)\]\s*")

# Bracketed noise we always remove (CRC checksums, fansub flags).
_BRACKET_NOISE_RE  = re.compile(
    r"\[(?:[A-F0-9]{8}|Multi-Subs?|hardsub|softsub|MSubs?|JAP|ENG)\]",
    re.IGNORECASE,
)

# M4: Second-pass bracket stripper for noise the whitelist missed.
# Real-world filenames stack 3-5 release-group sub-tags in brackets:
# `[HEVC-Hi10P]`, `[Multi-Subs]`, `[BD]`, `[FLAC]`, `[OPUS]`, etc. The
# whitelist above catches the canonical ones but new variants ship every
# month. After all KNOWN tokens are extracted, this regex removes any
# remaining `[…]` chunk whose contents look like technical metadata
# (uppercase letters / digits / hyphens / dots), preserving brackets
# that carry real title material like `[Unlimited Blade Works]`.
# Conservative heuristic: drop if ≥80% of chars are uppercase/digit/symbol.
_RESIDUAL_BRACKET_RE = re.compile(r"\[([^\]]+)\]")


def _looks_like_metadata(inside: str) -> bool:
    """True if a bracket's contents look like technical tags (not title text)."""
    if not inside or len(inside) > 24:
        return False  # long content is almost certainly title material
    technical = sum(1 for c in inside if c.isupper() or c.isdigit() or c in "-._+")
    return technical / max(1, len(inside)) >= 0.8


@dataclass
class FormatTokens:
    quality: str | None = None        # 2160p, 1080p, etc.
    source: str | None = None         # BluRay, WEB-DL
    codec: str | None = None          # x265, H264
    audio: list[str] = field(default_factory=list)
    subtitles: list[str] = field(default_factory=list)  # JAP, Multi-Subs, etc.
    edition: str | None = None        # Extended, Director's Cut
    hdr: str | None = None
    # Normalized to "10bit" or "8bit" (or None). The raw token in the
    # filename ("10-bit", "Hi10P", "10.bit", "8bit", etc.) is collapsed
    # so downstream code can compare cleanly.
    bit_depth: str | None = None
    release_group: str | None = None  # GROUP from -GROUP or [GROUP]


def strip(name: str) -> tuple[str, FormatTokens]:
    """Return (cleaned_name, captured_tokens).

    Notes on what we DON'T do:
    - We don't blindly strip parenthesized content. `The Office (US)`,
      `Fate/stay night [Unlimited Blade Works]`, and `Spider-Man (Tom
      Holland)` all contain meaningful title material in their brackets.
      We strip ONLY the known noise patterns (CRC checksums, sub flags)
      and known-shape years `(2017)` (handled by `extract_year`).
    - We don't strip "extensions" we don't recognize. A folder named
      `The.Office.US.S01` is NOT a `.S01` extension — it's a directory.
    """
    tokens = FormatTokens()

    # Trim recognized media extensions ONLY. Without this guard a folder
    # path like "The.Office.US.S01" would lose its season marker.
    if "." in name:
        head, sep, tail = name.rpartition(".")
        if sep and head and f".{tail.lower()}" in _MEDIA_EXTS:
            name = head

    # 1. Leading [GROUP] tag (anime style) — capture and remove.
    m = _LEADING_GROUP_RE.match(name)
    if m:
        tokens.release_group = m.group(1)
        name = _LEADING_GROUP_RE.sub("", name, count=1)

    # 2. Capture format tokens FIRST so multi-piece sources like "WEB-DL" are
    #    removed before the trailing-group detector runs.
    name, tokens.quality  = _extract(_RESOLUTION_RE, name)
    # Fallback for `WxH` resolution syntax (Moozzi2's "1920x1080") when the
    # standard "1080p" form wasn't present. Normalize to the same `p` form
    # for display so the FileRow tag reads "1080p" either way.
    if tokens.quality is None:
        name, wxh_match = _extract(_RESOLUTION_WXH_RE, name)
        if wxh_match:
            tokens.quality = _WXH_TO_P.get(wxh_match.lower(), wxh_match)
    name, tokens.source   = _extract(_SOURCE_RE, name)
    # Ambiguous short sources (MAX, TS, HMAX) — case-sensitive ALL-CAPS and
    # must be preceded by a separator, so "Max.2015" isn't mauled. Always
    # strip if matched (a file can carry BOTH a network tag and a delivery
    # tag, e.g. "MAX.WEB-DL"); the main source wins for the stored value.
    name, _ambig_source = _extract(_SOURCE_AMBIG_RE, name)
    if tokens.source is None and _ambig_source:
        tokens.source = _ambig_source
    name, tokens.codec    = _extract(_CODEC_RE, name)
    name, tokens.edition  = _extract(_EDITION_RE, name)
    name, tokens.hdr      = _extract(_HDR_RE, name)
    # Normalize bit-depth to "10bit" / "8bit" so the dedupe ranker can
    # compare cleanly. "10-bit", "Hi10P", "10.bit", "10bit" all collapse.
    name, _bd_raw = _extract(_BIT_DEPTH_RE, name)
    if _bd_raw:
        tokens.bit_depth = "10bit" if _bd_raw.lower().startswith(("10", "hi10")) else "8bit"
    # Audio + subtitles can have multiple hits each — capture all, dedupe.
    audio_matches = _AUDIO_RE.findall(name)
    tokens.audio = list(dict.fromkeys(audio_matches))
    name = _AUDIO_RE.sub(" ", name)
    sub_matches = _SUBTITLES_RE.findall(name)
    tokens.subtitles = list(dict.fromkeys(sub_matches))
    name = _SUBTITLES_RE.sub(" ", name)

    # 3. Trailing -GROUP tag (scene/p2p style) — runs AFTER source/codec
    #    removal, otherwise "-DL" from "WEB-DL" gets miscategorized.
    m = _TRAILING_GROUP_RE.search(name)
    if m and tokens.release_group is None:
        candidate = m.group(1)
        if not _is_known_token(candidate):
            tokens.release_group = candidate
            name = _TRAILING_GROUP_RE.sub("", name, count=1)

    # 4. Strip ONLY known bracket noise (checksums, sub flags). Generic
    #    bracket/paren content stays — see module docstring for why.
    name = _BRACKET_NOISE_RE.sub(" ", name)

    # 4b. M4: Residual-bracket pass. Walks any `[…]` chunks the whitelist
    #     didn't catch and removes them ONLY if their contents look like
    #     technical metadata (HEVC-Hi10P, BD, FLAC, etc.). Title-bearing
    #     brackets like `[Unlimited Blade Works]` survive unchanged.
    name = _RESIDUAL_BRACKET_RE.sub(
        lambda m: " " if _looks_like_metadata(m.group(1)) else m.group(0),
        name,
    )

    # 5. Normalize separators → spaces, collapse whitespace.
    name = re.sub(r"[._]+", " ", name)
    name = re.sub(r"\s{2,}", " ", name).strip(" -_")

    return name, tokens


def _extract(pattern: re.Pattern[str], text: str) -> tuple[str, str | None]:
    """Find the first match, return (text_without_match, matched_token_or_None)."""
    m = pattern.search(text)
    if not m:
        return text, None
    captured = m.group(1)
    text = pattern.sub(" ", text, count=1)
    return text, captured


_ALL_KNOWN: set[str] | None = None


def _is_known_token(s: str) -> bool:
    """Avoid mistaking a stripped codec/source for a release-group name."""
    global _ALL_KNOWN
    if _ALL_KNOWN is None:
        _ALL_KNOWN = set()
        for table in (SOURCES, CODECS, RESOLUTIONS, AUDIO, EDITIONS, HDR):
            for t in table:
                _ALL_KNOWN.add(re.sub(r"\\", "", t).upper())
    return s.upper() in _ALL_KNOWN
