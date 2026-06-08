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

# Token tables — curated defaults. Order matters where prefixes overlap
# (e.g. "WEB-DL" must match before "WEB" alone), though `_alt()` also sorts
# longest-first as a backstop. Every table below is user-extensible at runtime
# via scene-rules.json — see `scene_rules`, `_load_extras`, and `_build`.
SOURCES = [
    "UHD-BluRay", "UHDBD", "UHDRip", "BluRay", "Blu-Ray",
    "BDRemux", "BDRip", "BRRip", "BDMV", "REMUX",
    "WEB-DL", "WEBRip", "WEB-Rip", "WEBDL", "WEB",
    "HD-DVD", "HDDVD", "HDRip", "HDTV", "PDTV", "SDTV",
    "DVDRip", "DVDScr", "DVDR", "DVD",
    "DVBRip", "SATRip", "TVRip", "VODRip", "DSR",
    "HDCAM", "HDTS", "TELESYNC", "TELECINE", "WORKPRINT", "SCREENER",
    "CAM", "TC", "PPV",
]
# Streaming PLATFORM tags — the service a web release came from. These are NOT a
# source TYPE: a file is `AMZN WEB-DL`, where WEB-DL is the delivery method and
# AMZN is the platform. They're stripped from titles like any tech token, but a
# platform tag DEFINES the source only when no real delivery type is present, in
# which case it implies WEB-DL (a platform tag alone is virtually always a web
# download). Keeping them in SOURCES made "...AMZN.WEB-DL..." store source=AMZN
# (whichever matched first), which the dedupe ranker doesn't recognize — so a
# real WEB-DL mis-ranked below a WEBRip.
PLATFORMS = ["AMZN", "ATVP", "DSNP", "HULU", "NFLX", "PCOK", "PMTP", "CRAV"]
# Sources that double as ordinary English when title-cased ("Max", "Ts", "Nf",
# "Bd", "Stan"). Matched case-SENSITIVELY (must be ALL-CAPS) AND must be
# preceded by a separator so they can't be the first token of a filename.
SOURCES_AMBIGUOUS = ["HMAX", "MAX", "NF", "TS", "BD", "STAN"]
# The ALL-CAPS-ambiguous tokens that are actually streaming platforms (HBO Max,
# Netflix, Stan) → like PLATFORMS, they imply WEB-DL rather than being a source.
_STREAMING_AMBIG = {"HMAX", "NF", "STAN"}

CODECS = [
    "x265", "x264", "x266", "H\\.265", "H\\.264", "H\\.266",
    "H265", "H264", "H266", "HEVC", "AVC", "VVC",
    "VC-1", "VC1", "VP9", "VP8", "AV1", "XviD", "DivX",
    "MPEG-2", "MPEG2", "MPEG-4", "MPEG4",
]

# Resolutions kept to unambiguous tokens only — "HD" and "SD" used to be
# here but they're real English / abbreviations that collided with titles
# ("HD: High Definition" — same word everywhere).
RESOLUTIONS = [
    "2160p", "1440p", "1080p", "720p", "576p", "540p", "480p", "360p",
    "4K", "8K", "2K", "UHD", "QHD", "FHD",
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
    "DTS-HD\\.MA", "DTS-HD\\.HRA", "DTS-HD", "DTSHD", "DTS-X", "DTS-ES", "DTS",
    "TrueHD\\.?Atmos", "TrueHD", "Atmos",
    "DDPA", "DDP5\\.1", "DDP7\\.1", "DDP2\\.0", "DDP", "DD\\+",
    "DD5\\.1", "DD7\\.1", "DD2\\.0", "DD",
    "Dolby\\.?Digital\\.?Plus", "Dolby\\.?Digital",
    "AC3", "EAC3", "E-AC-3", "AC4",
    "AAC2\\.0", "AAC5\\.1", "AAC-LC", "AAC", "FLAC", "ALAC",
    "LPCM", "PCM", "MP3", "MP2", "WMA", "OGG",
]

# Subtitle markers — separate from audio so a UI showing "Audio: AAC, JAP"
# doesn't claim JAP is an audio codec.
SUBTITLES = [
    "Multi-Subs", "Multi-Sub", "MSubs", "Subbed", "Dubbed",
    "VOSTFR", "VOST", "SoftSubs", "HardSubs",
    "JAP", "ENG", "FRE", "GER", "ITA", "SPA",
]

EDITIONS = [
    "Director'?s\\.?Cut", "Extended\\.?Edition", "Extended\\.?Cut", "Extended",
    "Theatrical\\.?Cut", "Theatrical", "Unrated\\.?Cut", "Unrated",
    "Ultimate\\.?Edition", "Collector'?s?\\.?Edition", "Collector'?s?\\.?Cut",
    "Deluxe\\.?Edition", "Limited\\.?Edition", "Definitive\\.?Edition",
    "Special\\.?Edition", "Final\\.?Cut", "Uncut", "Recut", "Redux",
    "Open\\.?Matte", "Fan\\.?Edit", "Despecialized", "Restored",
    "Remastered", "IMAX", "Anniversary", "Criterion", "LIMITED",
]

HDR = ["HDR10\\+", "HDR10", "HDR", "DoVi", "DV", "Dolby\\.?Vision", "HLG"]

# Bit depth — 10-bit encodes (Hi10P / x265 10-bit) are the gold standard
# for anime because they kill the color banding 8-bit can't avoid in
# gradients (skies, dark scenes). Surfaced for the dedupe ranker so a
# 10-bit version beats an 8-bit version of the same source.
BIT_DEPTH = ["10[\\.\\-]?bit", "Hi10P", "Hi10", "8[\\.\\-]?bit"]

# Scene "clutter" / release flags — REPACK, PROPER, INTERNAL, etc. Left in,
# they pollute the title ("Movie PROPER 1080p" → title "Movie PROPER"). Matched
# CASE-SENSITIVELY (no IGNORECASE) and only after a separator, so a real title
# word ("proper", "festival", "Internal Affairs") is never touched — only the
# uppercase scene form strips. Captured into tokens.release_flags for the dedupe
# ranker (a PROPER/REPACK beats the original release).
RELEASE_FLAGS = [
    "PROPER", "REPACK", "RERIP", "INTERNAL", "iNTERNAL",
    "READNFO", "DIRFIX", "NFOFIX", "SUBFIX", "RETAIL", "FESTIVAL", "MULTI",
]

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


# Token-boundary primitives. Python `re` requires fixed-width lookbehind, so
# `^` can't live inside the lookbehind — it's a sibling alternative. The
# lookahead is unrestricted and can mix `$` with a char class freely.
#
# `_LEFT_SEP`         matches: start-of-string OR preceded by a separator char.
# `_LEFT_SEP_NOSTART` matches: preceded by a separator char ONLY (never start).
# `_RIGHT_SEP`        matches: end-of-string OR followed by a separator char.
# Including `+` lets symbol-bearing tokens like `HDR10+` match. Parens are
# included so paren-wrapped tokens (`Movie (1080p)`, `Show (BluRay).mkv`) match.
# `_LEFT_SEP_NOSTART` guards tokens that must never strip when they LEAD a
# filename (ambiguous short sources, all-caps release flags) — so a movie
# literally titled `MAX` or `PROPER` keeps its name.
_LEFT_SEP         = r"(?:^|(?<=[ \[\]\(\)._\-+]))"
_LEFT_SEP_NOSTART = r"(?<=[ \[\]\(\)._\-+])"
_RIGHT_SEP        = r"(?=$|[ \[\]\(\)._\-+])"


# In-code defaults — the guaranteed fallback. Phase 17 ships the SAME tables as
# `release_tokens.json` (loaded as the editable BASE); these literals are what we
# fall back to if that file is missing or malformed, so the parser never breaks
# on a bad data file. Extras from scene-rules.json fold ON TOP of the base in
# `_build()`. Snapshotting here means `reload_rules()` rebuilds from a clean base
# rather than re-merging an already-merged table.
_CURATED: dict[str, list[str]] = {
    "sources":       list(SOURCES),
    "codecs":        list(CODECS),
    "resolutions":   list(RESOLUTIONS),
    "audio":         list(AUDIO),
    "subtitles":     list(SUBTITLES),
    "editions":      list(EDITIONS),
    "hdr":           list(HDR),
    "release_flags": list(RELEASE_FLAGS),
}
# These tables don't take user extras (they're structural), but Phase 17 still
# lets the shipped JSON retune them — pristine in-code fallbacks live here.
_DEFAULT_BIT_DEPTH = list(BIT_DEPTH)
_DEFAULT_SOURCES_AMBIGUOUS = list(SOURCES_AMBIGUOUS)
_DEFAULT_WXH_TO_P = dict(_WXH_TO_P)

# Shipped base-table data file (co-located with this module). Edit it to retune
# the curated tables globally without touching code.
import json as _json
from pathlib import Path as _Path
_BASE_TABLE_FILE = _Path(__file__).resolve().parent / "release_tokens.json"


def _load_base_tables() -> dict:
    """Load the shipped base token tables (Phase 17). Returns {} (→ the in-code
    literals act as defaults) when the file is missing / unreadable / malformed,
    so a bad data file can never break parsing."""
    try:
        if _BASE_TABLE_FILE.exists():
            data = _json.loads(_BASE_TABLE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:  # pragma: no cover - defensive
        print(f"format_stripper: base token file unreadable ({e!r}); using in-code defaults")
    return {}


def _base_list(loaded: dict, key: str, fallback: list[str]) -> list[str]:
    """A base table from the JSON file, or the in-code fallback when absent/empty."""
    v = loaded.get(key)
    return [str(x) for x in v] if isinstance(v, list) and v else list(fallback)


def _merge_extra(curated: list[str], extra: set[str]) -> list[str]:
    """Curated regex fragments + user literals (regex-escaped), deduped.

    Curated entries are hand-authored regex (`H\\.265`, `Director'?s\\.?Cut`);
    user entries are plain strings and MUST be `re.escape`-d. Dedup compares the
    de-escaped lowercase form so a user re-adding a known token is a no-op.
    """
    out = list(curated)
    seen = {re.sub(r"\\", "", c).lower() for c in curated}
    for e in sorted(extra):
        key = e.lower()
        if e and key not in seen:
            out.append(re.escape(e))
            seen.add(key)
    return out


def _load_extras() -> dict[str, set[str]]:
    """Read user token extras from scene-rules.json. Never raises — a missing or
    malformed file degrades to curated-only tables."""
    try:
        from kira.parser import scene_rules
        return {
            "sources":       scene_rules.extra_sources(),
            "codecs":        scene_rules.extra_codecs(),
            "resolutions":   scene_rules.extra_resolutions(),
            "audio":         scene_rules.extra_audio(),
            "subtitles":     scene_rules.extra_subtitles(),
            "editions":      scene_rules.extra_editions(),
            "hdr":           scene_rules.extra_hdr(),
            "release_flags": scene_rules.extra_release_flags(),
        }
    except Exception as e:  # pragma: no cover - defensive
        print(f"format_stripper: scene_rules unavailable ({e!r}); curated tables only")
        return {}


def _build() -> None:
    """(Re)merge token tables with user extras and compile their regexes.

    Called once at import; re-callable via `reload_rules()` after the user edits
    scene-rules.json (settings UI / tests). Reassigns the module-level table +
    regex globals in place.
    """
    global SOURCES, CODECS, RESOLUTIONS, AUDIO, SUBTITLES, EDITIONS, HDR, RELEASE_FLAGS
    global SOURCES_AMBIGUOUS, BIT_DEPTH, _WXH_TO_P
    global _RESOLUTION_RE, _RESOLUTION_WXH_RE, _SOURCE_RE, _PLATFORM_RE, _CODEC_RE, _AUDIO_RE
    global _SUBTITLES_RE, _EDITION_RE, _HDR_RE, _BIT_DEPTH_RE
    global _SOURCE_AMBIG_RE, _RELEASE_FLAGS_RE, _ALL_KNOWN

    # Phase 17: shipped JSON is the editable BASE (falls back to the in-code
    # _CURATED / _DEFAULT_* literals); user scene-rules extras fold ON TOP.
    base = _load_base_tables()
    extras = _load_extras()

    def _eg(key: str) -> set[str]:
        return extras.get(key, set())

    SOURCES       = _merge_extra(_base_list(base, "sources",       _CURATED["sources"]),       _eg("sources"))
    CODECS        = _merge_extra(_base_list(base, "codecs",        _CURATED["codecs"]),         _eg("codecs"))
    RESOLUTIONS   = _merge_extra(_base_list(base, "resolutions",   _CURATED["resolutions"]),    _eg("resolutions"))
    AUDIO         = _merge_extra(_base_list(base, "audio",         _CURATED["audio"]),          _eg("audio"))
    SUBTITLES     = _merge_extra(_base_list(base, "subtitles",     _CURATED["subtitles"]),      _eg("subtitles"))
    EDITIONS      = _merge_extra(_base_list(base, "editions",      _CURATED["editions"]),       _eg("editions"))
    HDR           = _merge_extra(_base_list(base, "hdr",           _CURATED["hdr"]),            _eg("hdr"))
    RELEASE_FLAGS = _merge_extra(_base_list(base, "release_flags", _CURATED["release_flags"]),  _eg("release_flags"))
    # Structural tables — JSON can retune them, no user-extra layer.
    SOURCES_AMBIGUOUS = _base_list(base, "sources_ambiguous", _DEFAULT_SOURCES_AMBIGUOUS)
    BIT_DEPTH         = _base_list(base, "bit_depth", _DEFAULT_BIT_DEPTH)
    wxh = base.get("wxh_to_p")
    _WXH_TO_P = {str(k): str(v) for k, v in wxh.items()} if isinstance(wxh, dict) and wxh else dict(_DEFAULT_WXH_TO_P)

    _RESOLUTION_RE = re.compile(rf"{_LEFT_SEP}({_alt(RESOLUTIONS)}){_RIGHT_SEP}", re.IGNORECASE)
    # WxH (Moozzi2's `1920x1080`) — only when both sides are a known pair, so
    # anime `12x05` episode notation isn't eaten as a resolution.
    _RESOLUTION_WXH_RE = re.compile(
        rf"{_LEFT_SEP}({_alt(list(_WXH_TO_P.keys()))}){_RIGHT_SEP}", re.IGNORECASE,
    )
    _SOURCE_RE    = re.compile(rf"{_LEFT_SEP}({_alt(SOURCES)}){_RIGHT_SEP}", re.IGNORECASE)
    _PLATFORM_RE  = re.compile(rf"{_LEFT_SEP}({_alt(PLATFORMS)}){_RIGHT_SEP}", re.IGNORECASE)
    _CODEC_RE     = re.compile(rf"{_LEFT_SEP}({_alt(CODECS)}){_RIGHT_SEP}", re.IGNORECASE)
    _AUDIO_RE     = re.compile(rf"{_LEFT_SEP}({_alt(AUDIO)}){_RIGHT_SEP}", re.IGNORECASE)
    _SUBTITLES_RE = re.compile(rf"{_LEFT_SEP}({_alt(SUBTITLES)}){_RIGHT_SEP}", re.IGNORECASE)
    _EDITION_RE   = re.compile(rf"{_LEFT_SEP}({_alt(EDITIONS)}){_RIGHT_SEP}", re.IGNORECASE)
    _HDR_RE       = re.compile(rf"{_LEFT_SEP}({_alt(HDR)}){_RIGHT_SEP}", re.IGNORECASE)
    _BIT_DEPTH_RE = re.compile(rf"{_LEFT_SEP}({_alt(BIT_DEPTH)}){_RIGHT_SEP}", re.IGNORECASE)
    # Ambiguous short sources — CASE-SENSITIVE (ALL-CAPS) and never at start, so
    # "Max.2015" keeps its title; "Movie.2015.MAX.WEB-DL" loses the MAX.
    _SOURCE_AMBIG_RE = re.compile(rf"{_LEFT_SEP_NOSTART}({_alt(SOURCES_AMBIGUOUS)}){_RIGHT_SEP}")
    # Release flags — CASE-SENSITIVE and never at start (a leading title word
    # equal to a flag is preserved). User extras strip as authored (case kept).
    _RELEASE_FLAGS_RE = re.compile(rf"{_LEFT_SEP_NOSTART}({_alt(RELEASE_FLAGS)}){_RIGHT_SEP}")

    _ALL_KNOWN = None  # force lazy rebuild against the merged tables


def reload_rules() -> None:
    """Re-read scene-rules.json and rebuild the token regexes. Call after the
    user edits their rules (settings UI), or from tests."""
    _build()


_build()

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
    """True if a bracket's contents look like technical tags (not title text).

    Carve-outs from the "≥80% upper/digit/symbol" rule:

    1. Long content (> 24 chars) is almost certainly title material —
       `[Unlimited Blade Works]`, `[Director's Commentary]`, etc.

    2. Pure 2-4 digit content (`[1158]`, `[02]`, `[2021]`) is preserved.
       Anime fansubs use `[NNNN]` brackets for absolute episode numbers
       (`[SubsPlease] Frieren - [1158].mkv`), and movies use `[YYYY]` for
       release years (`[YTS] Dune [2021].mkv`). Stripping them as
       "metadata" deletes information `extract_sxe` and `extract_year`
       are about to consume. The 2-4 char width is the discriminator
       that keeps 8-char CRC checksums like `[ABCD1234]` (all-digit but
       too long) classified as metadata.

    3. Internal spaces signal title material. `[ALICE IN WONDERLAND]`,
       `[BLAZING SADDLES]`, `[THE GOOD THE BAD AND THE UGLY]` etc. all
       trip the "≥80% upper/digit" heuristic because all-caps letters
       count as technical chars, but the spaces give them away as
       human-readable phrases. Technical tags (`[HEVC-Hi10P]`, `[BD-Rip]`,
       `[Multi-Subs]`) are always single tokens — no spaces inside.
       This carve-out preserves all-caps movie titles that scene groups
       sometimes wrap in brackets (kidzcorner Disney rips, etc.).
    """
    if not inside or len(inside) > 24:
        return False  # long content is almost certainly title material
    if 2 <= len(inside) <= 4 and inside.isdigit():
        return False  # episode-number / year shape; preserve for downstream parsing
    if " " in inside:
        return False  # contains spaces → human-readable phrase, not a tech tag
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
    # Scene release flags (PROPER, REPACK, INTERNAL, …) — captured so a future
    # dedupe ranker can prefer a PROPER/REPACK over the original release.
    release_flags: list[str] = field(default_factory=list)
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
    #    removed before the trailing-group detector runs. Source is extracted
    #    BEFORE resolution: some source tokens (`UHD-BluRay`) begin with a
    #    resolution token (`UHD`), so grabbing the longest source first keeps
    #    the compound intact, then the leftover `2160p` becomes the quality.
    name, tokens.source   = _extract_first_strip_rest(_SOURCE_RE, name)
    # Streaming PLATFORM tags (AMZN/NFLX/DSNP/…) are stripped from the title like
    # any tech token, but only DEFINE the source when no real delivery type was
    # found — then they imply WEB-DL (a platform tag alone is virtually always a
    # web download). This keeps "...AMZN.WEB-DL..." → source=WEB-DL (not AMZN).
    name, _platform = _extract_first_strip_rest(_PLATFORM_RE, name)
    # Ambiguous short sources (MAX, TS, HMAX) — case-sensitive ALL-CAPS and
    # must be preceded by a separator, so "Max.2015" isn't mauled. Always
    # strip if matched (a file can carry BOTH a network tag and a delivery
    # tag, e.g. "MAX.WEB-DL"); the main source wins for the stored value.
    name, _ambig_source = _extract(_SOURCE_AMBIG_RE, name)
    if tokens.source is None:
        if _ambig_source and _ambig_source.upper() not in _STREAMING_AMBIG:
            tokens.source = _ambig_source                       # MAX / TS / BD → as-is
        elif _platform or _ambig_source:                        # AMZN/… or HMAX/NF/STAN
            tokens.source = "WEB-DL"                             # platform ⇒ web download
    name, tokens.quality  = _extract_first_strip_rest(_RESOLUTION_RE, name)
    # Fallback for `WxH` resolution syntax (Moozzi2's "1920x1080") when the
    # standard "1080p" form wasn't present. Normalize to the same `p` form
    # for display so the FileRow tag reads "1080p" either way.
    if tokens.quality is None:
        name, wxh_match = _extract(_RESOLUTION_WXH_RE, name)
        if wxh_match:
            tokens.quality = _WXH_TO_P.get(wxh_match.lower(), wxh_match)
    name, tokens.codec    = _extract_first_strip_rest(_CODEC_RE, name)
    name, tokens.edition  = _extract_first_strip_rest(_EDITION_RE, name)
    name, tokens.hdr      = _extract_first_strip_rest(_HDR_RE, name)
    # Normalize bit-depth to "10bit" / "8bit" so the dedupe ranker can
    # compare cleanly. "10-bit", "Hi10P", "10.bit", "10bit" all collapse.
    name, _bd_raw = _extract_first_strip_rest(_BIT_DEPTH_RE, name)
    if _bd_raw:
        tokens.bit_depth = "10bit" if _bd_raw.lower().startswith(("10", "hi10")) else "8bit"
    # Audio + subtitles can have multiple hits each — capture all, dedupe.
    audio_matches = _AUDIO_RE.findall(name)
    tokens.audio = list(dict.fromkeys(audio_matches))
    name = _AUDIO_RE.sub(" ", name)
    sub_matches = _SUBTITLES_RE.findall(name)
    tokens.subtitles = list(dict.fromkeys(sub_matches))
    name = _SUBTITLES_RE.sub(" ", name)
    # Release flags (PROPER/REPACK/INTERNAL/…) — case-sensitive clutter that
    # would otherwise leak into the title. Capture all, dedupe, strip.
    flag_matches = _RELEASE_FLAGS_RE.findall(name)
    tokens.release_flags = list(dict.fromkeys(flag_matches))
    name = _RELEASE_FLAGS_RE.sub(" ", name)

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

    # 4c. Collapse empty / residue-only brackets + parens left behind
    #     after token extraction. Two scenarios:
    #       - `Movie (BluRay).mkv` → source stripped → `Movie ( )` →
    #         collapsed to `Movie`.
    #       - `Show.[HEVC-Hi10P].S01E05.mkv` → codec + bit_depth tokens
    #         stripped from inside → `Show [ - ] S01E05` → collapsed.
    #     The inner allowance `[\s\-_]*` catches whitespace, hyphens,
    #     and underscores — the connective tissue left behind when an
    #     interior token is extracted. Legitimate single-letter title
    #     brackets like `[!]`, `[A]`, `[1]` still survive because they
    #     contain characters outside this set.
    name = re.sub(r"\(\s*[\-_\s]*\s*\)|\[\s*[\-_\s]*\s*\]", " ", name)

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


def _extract_first_strip_rest(pattern: re.Pattern[str], text: str) -> tuple[str, str | None]:
    """Capture the FIRST match as the value but strip ALL matches from the text.

    Releases routinely stack redundant tags of the same class — `2160p UHD`,
    `BluRay REMUX`, `HDR DV`, `Hi10P 10bit`. With single-match removal the first
    is captured and the rest leak into the title (`oppenheimer 2023 uhd`). Keep
    the first as the stored value, remove every occurrence. This only differs
    from `_extract` when 2+ tokens of the class are present, so it adds no new
    single-token false-positive risk.
    """
    m = pattern.search(text)
    if not m:
        return text, None
    captured = m.group(1)
    text = pattern.sub(" ", text)
    return text, captured


_ALL_KNOWN: set[str] | None = None


def _is_known_token(s: str) -> bool:
    """Avoid mistaking a stripped codec/source for a release-group name."""
    global _ALL_KNOWN
    if _ALL_KNOWN is None:
        _ALL_KNOWN = set()
        for table in (SOURCES, PLATFORMS, CODECS, RESOLUTIONS, AUDIO, EDITIONS, HDR, RELEASE_FLAGS):
            for t in table:
                _ALL_KNOWN.add(re.sub(r"\\", "", t).upper())
    return s.upper() in _ALL_KNOWN
