"""Naming-template engine — Python port of the frontend formatPath().

Mirrors the token vocabulary the user already sees in Settings → Naming.
Profiles live in the `settings` table under `naming.profiles.<profile>.<type>`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2.sandbox import SandboxedEnvironment

from kira.parser import ParsedFile

# Jinja2 naming-template engine (Tier 1.5). SandboxedEnvironment so a
# user-authored template can't reach Python internals (`{{ ''.__class__ }}`
# and friends are blocked). autoescape OFF — we render filesystem paths, not
# HTML, so `&` / quotes must pass through untouched (the per-segment _safe()
# pass is what sanitizes for the filesystem). `finalize` coerces None → ""
# so a missing optional value renders blank instead of the literal "None".
# A token the template references but that we don't populate renders blank
# (Jinja's default Undefined → ""), matching the old str.replace behavior
# where an absent token simply wasn't substituted.
_JINJA_ENV = SandboxedEnvironment(
    autoescape=False,
    finalize=lambda v: "" if v is None else v,
    keep_trailing_newline=False,
)


# ── Custom Jinja filters (Tier 1.5 step 4) ───────────────────────────────
# the reference renamer-style helpers that Jinja doesn't ship. Built-ins (upper, lower,
# title, replace, default, trim, truncate, join, int) cover the rest, so we
# only add the gaps. Registered on the env's filter table — purely additive,
# they only do anything when a template actually pipes through them, so
# existing profiles are unaffected (locked by the equivalence test).

def _filter_pad(value: Any, width: int = 2, char: str = "0") -> str:
    """Left-pad to `width` with `char` (default zero). `{{ episode | pad(3) }}`."""
    return str(value).rjust(int(width), (str(char)[:1] or "0"))


def _filter_ascii(value: Any) -> str:
    """Fold accents to plain ASCII: `Frieren: Sōsō` → `Frieren: Soso`.
    NFKD-decompose, drop combining marks, then drop any remaining non-ASCII."""
    decomposed = unicodedata.normalize("NFKD", str(value))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.encode("ascii", "ignore").decode("ascii")


_ROMAN_TABLE = [
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
    (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
]


def _filter_roman(value: Any) -> str:
    """Integer → Roman numeral (`{{ season | roman }}` → `II`). Out-of-range
    or non-numeric values pass through unchanged."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return str(value)
    if n <= 0 or n >= 4000:
        return str(value)
    out: list[str] = []
    for v, sym in _ROMAN_TABLE:
        while n >= v:
            out.append(sym)
            n -= v
    return "".join(out)


def _filter_clean(value: Any) -> str:
    """Collapse runs of whitespace to a single space and trim the ends."""
    return re.sub(r"\s+", " ", str(value)).strip()


_LEADING_ARTICLE_RE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)


def _filter_sort_name(value: Any) -> str:
    """Move a leading article to the end, library-sort style:
    `The Matrix` → `Matrix, The`. No-op when there's no leading article."""
    s = str(value).strip()
    m = _LEADING_ARTICLE_RE.match(s)
    if not m:
        return s
    article, rest = s[:m.end()].strip(), s[m.end():].strip()
    return f"{rest}, {article}" if rest else s


def _filter_upper_initial(value: Any) -> str:
    """Capitalize the first letter of each word, leaving the rest untouched
    (unlike `title`, which lowercases the remainder and mangles `iPhone`)."""
    return re.sub(r"(^|\s)([a-z])", lambda m: m.group(1) + m.group(2).upper(), str(value))


def _filter_acronym(value: Any) -> str:
    """First letter of each alphanumeric word, uppercased:
    `The Lord of the Rings` → `TLOTR`. `{{ n | acronym }}`."""
    return "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", str(value))).upper()


_JINJA_ENV.filters.update({
    "pad": _filter_pad,
    "ascii": _filter_ascii,
    "roman": _filter_roman,
    "clean": _filter_clean,
    "sortName": _filter_sort_name,
    "upperInitial": _filter_upper_initial,
    "acronym": _filter_acronym,
})


@dataclass
class NamingProfile:
    movie: str
    tv: str
    anime: str
    music: str
    # Optional alternate anime template, selected when the user sets
    # `naming.anime_numbering = "absolute"`. Flat, absolute-numbered layout
    # (e.g. "One Piece/One Piece - 1156 - Title.mkv"). None → fall back to the
    # standard seasonal `anime` template, so a custom profile that omits it
    # simply keeps seasonal naming. New numbering/ordering "styles" plug in the
    # same way: add a field here + an entry in _TEMPLATE_STYLE_FIELD.
    anime_absolute: str | None = None


DEFAULT_PROFILES: dict[str, NamingProfile] = {
    # `{variant}` interpolates to a dot-prefixed suffix like `.JAP` /
    # `.DirectorsCut` / `.10bit` when a file carries an identity-variant
    # signal (audio language, edition, or 10-bit encode). Empty string
    # for default-flavor files so paths stay clean.
    #
    # Without `{variant}` in the template, `Frieren.01.JAP.mkv` and
    # `Frieren.01.ENG.mkv` (same episode, different audio) generate the
    # SAME output path and the second file silently overwrites the
    # first during rename. Including `{variant}` produces
    # `Frieren - S01E01.JAP.mkv` + `Frieren - S01E01.ENG.mkv` — both
    # files survive on disk with distinguishable names.
    "Plex": NamingProfile(
        movie="{{n}} ({{y}})/{{n}} ({{y}}){{disc}}{{variant}} [{{q}}].{{x}}",
        tv="{{n}} ({{y}})/Season {{s2}}/{{n}} - S{{s2}}E{{e2}}{{variant}} - {{t}} [{{q}}].{{x}}",
        anime="{{n}}/Season {{s2}}/{{n}} - S{{s2}}E{{e2}}{{variant}} - {{t}} [{{rg}}].{{x}}",
        anime_absolute="{{n}}/{{n}} - {{absx}}{{variant}} - {{t}} [{{rg}}].{{x}}",
        music="{{artist}}/{{album}} ({{y}})/{{tn}}{{variant}} - {{title}}.{{x}}",
    ),
    "Jellyfin": NamingProfile(
        movie="{{n}} ({{y}})/{{n}} ({{y}}){{disc}}{{variant}}.{{x}}",
        tv="{{n}} ({{y}})/Season {{s2}}/{{n}} ({{y}}) - S{{s2}}E{{e2}}{{variant}} - {{t}}.{{x}}",
        anime="{{n}} ({{y}})/Season {{s2}}/{{n}} - S{{s2}}E{{e2}}{{variant}} - {{t}}.{{x}}",
        anime_absolute="{{n}} ({{y}})/{{n}} - {{absx}}{{variant}} - {{t}}.{{x}}",
        music="{{artist}}/{{album}}/{{tn}}{{variant}} {{title}}.{{x}}",
    ),
    "Kodi": NamingProfile(
        movie="{{n}} ({{y}})/{{n}} ({{y}}){{disc}}{{variant}} - {{q}}.{{x}}",
        tv="{{n}}/Season {{s2}}/{{n}}.S{{s2}}E{{e2}}{{variant}}.{{t}}.{{x}}",
        anime="{{n}}/S{{s2}}/{{n}} - {{abs}}{{variant}} - {{t}}.{{x}}",
        anime_absolute="{{n}}/{{n}} - {{absx}}{{variant}} - {{t}}.{{x}}",
        music="{{artist}} - {{album}}/{{tn}}{{variant}}. {{title}}.{{x}}",
    ),
}


# Style → the NamingProfile field that overrides the base `media_type` template
# when that style is active. A style with no matching variant on the profile
# (e.g. a Custom profile that didn't define `anime_absolute`) falls back to the
# base template. THIS is the single place to extend: a new numbering/ordering
# style is one entry here + one field on NamingProfile + one setting.
_TEMPLATE_STYLE_FIELD: dict[tuple[str, str], str] = {
    ("anime", "absolute"): "anime_absolute",
}


def select_template(profile: NamingProfile, media_type: str, *, anime_numbering: str = "seasonal") -> str:
    """Resolve the template string for a media_type, honoring style variants.

    `anime_numbering` is the only style today ("seasonal" default | "absolute").
    Modular: the (media_type, style) → field mapping lives in
    _TEMPLATE_STYLE_FIELD, and an absent variant transparently falls back to the
    base `media_type` template, so nothing downstream needs to branch.
    """
    field = _TEMPLATE_STYLE_FIELD.get((media_type, anime_numbering))
    if field:
        variant = getattr(profile, field, None)
        if variant:
            return variant
    return getattr(profile, media_type, profile.movie)


SUBFOLDER = {"movie": "Movies", "tv": "TV", "anime": "Anime", "music": "Music"}

_INVALID_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')

# Fix #1: Punctuation normalization map applied BEFORE `_INVALID_FS` strips.
# Without this, "Mission: Impossible" → "Mission Impossible" (colon silently
# disappears), "Re:Zero" → "ReZero", "Yu-Gi-Oh!" → "Yu-Gi-Oh", etc. We
# substitute readable separators so the renamed folder/file is still
# scrapeable by Plex/Jellyfin and visually intact.
#   :   → " - "    (colons become space-dash-space, Plex-friendly)
#   ?   → ""       (Windows can't have ?, drop it)
#   /   → "-"      (slashes can't survive in a single path component)
#   |   → "-"      (pipes can't survive)
#
# Fix #11: Smart-quote / backtick normalization. AniDB's title dump uses
# U+0060 BACKTICK as the typographic apostrophe character (e.g. literal
# `Frieren: Beyond Journey` `s End`). The renamed folder ended up with a
# backtick that looked broken to users and wouldn't match anyone's intent
# for matching against the canonical title. Same for left/right curly
# quotes that occasionally appear in provider data. Normalize them all
# to ASCII equivalents.
_PUNCT_NORMALIZE = str.maketrans({
    ":": " - ",
    "?": "",
    "/": "-",
    "|": "-",
    "‘": "'",   # left single quote
    "’": "'",   # right single quote / curly apostrophe
    "“": '"',   # left double quote — then stripped by _INVALID_FS
    "”": '"',   # right double quote — same
    "`": "'",   # backtick (AniDB title dump artifact)
})

# Windows device names — files with these basenames are inaccessible regardless
# of extension or directory. `CON.mkv`, `prn.txt`, even `aux` inside a folder
# all raise "Invalid name" in Explorer/Win32. We sanitize on all platforms so
# a library written from Linux/macOS doesn't break the second a Windows user
# scans it.
# Reference: https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file
_RESERVED_BASENAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM0", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT0", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
})

# Per-component max bytes — Windows + ext4 + APFS all enforce 255 bytes per
# segment (NOT 255 chars; multi-byte UTF-8 spends more). We trim by bytes
# from the right but preserve the extension if there is one.
_MAX_COMPONENT_BYTES = 255


def _safe(part: str) -> str:
    """Sanitize a single path component for cross-platform filesystem safety.

    Steps:
      1. Strip forbidden chars (`<>:"/\\|?*` + control chars).
      2. Strip leading/trailing whitespace and trailing dot/space (Windows
         silently truncates them, which renames `foo.` → `foo`).
      3. Prefix `_` to Windows device names so `CON.mkv` becomes `_CON.mkv`
         and remains accessible. Match is case-insensitive and considers
         only the stem before the first dot, matching Win32 semantics.
      4. Clamp to 255 bytes per component (true filesystem limit). When an
         extension is present, preserve it and trim the stem.
    """
    # Fix #1 + #11: normalize punctuation BEFORE the FS-invalid strip so
    # readable separators replace colons/slashes and smart quotes / backticks
    # become ASCII. See _PUNCT_NORMALIZE comment above for the full map.
    part = part.translate(_PUNCT_NORMALIZE)
    # Collapse consecutive whitespace produced by colon → " - " when the
    # source already had a space after the colon ("Mission: Impossible"
    # becomes "Mission -  Impossible" → "Mission - Impossible").
    part = re.sub(r"\s{2,}", " ", part)
    # Drop empty optional-token residue. A template like "{{n}} ({{y}})" with no
    # year leaves "Title ()"; "[{{rg}}]" with no release group leaves "Title []"
    # or "Title [_]" (the blank-token placeholder). Strip any bracket/paren/brace
    # group containing only whitespace and/or "_" placeholders, plus the space
    # before it, so missing metadata never litters names with "()" / "[_]".
    part = re.sub(r"\s*(\([\s_]*\)|\[[\s_]*\]|\{[\s_]*\})", "", part)
    part = re.sub(r"\s{2,}", " ", part)
    out = _INVALID_FS.sub("", part).strip()
    out = out.rstrip(". ")
    if not out:
        return "_"

    # Reserved-name guard. Win32 looks at the basename BEFORE the first dot
    # for the device check — `COM1.foo.bar.mkv` is still rejected.
    stem = out.split(".", 1)[0].upper()
    if stem in _RESERVED_BASENAMES:
        out = "_" + out

    # Length clamp. Walk down from the end keeping the last `.ext` intact
    # (multi-suffix files like `.tar.gz` only protect the very last suffix —
    # acceptable, media files always have a single extension).
    if len(out.encode("utf-8")) > _MAX_COMPONENT_BYTES:
        dot = out.rfind(".")
        if 0 < dot < len(out) - 1 and (len(out) - dot) <= 16:
            stem, ext = out[:dot], out[dot:]
        else:
            stem, ext = out, ""
        # Trim the stem byte-by-byte until the whole thing fits.
        while len((stem + ext).encode("utf-8")) > _MAX_COMPONENT_BYTES and len(stem) > 1:
            stem = stem[:-1]
        out = (stem + ext).rstrip(". ") or "_"

    return out


def _safe_opt(value: str) -> str:
    """`_safe()` for OPTIONAL tokens: an empty/blank input stays empty rather
    than becoming the `_` placeholder `_safe()` emits for empty path segments.

    The legacy tokens (rg, title, artist, album) keep `_safe()`'s `_`-on-empty
    behavior so their rename output is byte-identical to before (locked by the
    equivalence test). New tech / metadata tokens use this instead, so an
    absent value (no HDR, no director, …) renders blank in the path, not `_`.
    """
    return _safe(value) if value else ""


def apply_template(template: str, ctx: dict[str, Any]) -> str:
    """Render a Jinja2 naming template against `ctx`.

    Replaces the old `str.replace` loop. Tokens are now `{{ token }}` and can
    be piped through filters (`{{ n | upper }}`), guarded by conditionals
    (`{% if hdr %}…{% endif %}`), and defaulted (`{{ t | default('Episode ' ~ e2) }}`).
    Sandboxed: a user template can't reach Python internals. Tokens not in
    `ctx` render blank, matching the old "absent token = no substitution"
    behavior. The per-segment `_safe()` pass + path-traversal guard in
    `format_target_path` still run afterward, unchanged — they remain the
    filesystem safety net regardless of what the template produces.

    A malformed template (syntax error, undefined filter) raises ValueError
    so the rename endpoint reports it clearly instead of writing a path
    containing literal `{{ … }}`.
    """
    try:
        return _JINJA_ENV.from_string(template).render(ctx)
    except Exception as e:
        raise ValueError(f"Naming template failed to render: {e}") from e


def _build_ctx(
    parsed: ParsedFile,
    library_title: str,
    library_year: int | None,
    episode_title: str | None = None,
    season_override: int | None = None,
    metadata: dict[str, Any] | None = None,
    file_size: int | None = None,
) -> dict[str, Any]:
    # Fix #2: strip a trailing `(YYYY)` from library_title before it lands
    # in {n}. AniDB embeds the year directly in its sequel-season titles
    # (e.g. AID 18886 = `"Sousou no Frieren (2026)"`). Combined with the
    # template's separate `{y}` token, the rendered folder would read
    # `Sousou no Frieren (2026) (2026)`. Some TVDB / TMDB entries have
    # year-suffixed names too. Strip the trailing year here; if `{y}` is
    # also populated, the template adds it back cleanly in the right slot.
    if library_title:
        library_title = re.sub(r"\s*\(\d{4}\)\s*$", "", library_title).strip() or library_title

    # Default season=1 for anime/TV with no explicit season — common for anime
    # where filenames only carry an absolute episode (`Title - 06`). Otherwise
    # the template produces "Season /" with an empty number.
    season = season_override if season_override is not None else parsed.season
    if season is None and parsed.media_type in ("anime", "tv") and parsed.episode is not None:
        season = 1
    s2 = f"{season:02d}" if season is not None else ""
    e2 = f"{parsed.episode:02d}" if parsed.episode is not None else ""
    tn = f"{parsed.track:02d}" if parsed.track is not None else ""
    abs_ep = f"{parsed.absolute_episode:03d}" if parsed.absolute_episode is not None else e2
    # `absx`: absolute-number-or-SxE, used by the absolute anime templates. With
    # a real absolute number it IS the name ("One Piece - 1156"); without one it
    # falls back to the SxE form (NOT a bare episode number) so a flat absolute
    # layout can't collide two different seasons that both have an episode 5.
    absx = (
        f"{parsed.absolute_episode:03d}" if parsed.absolute_episode is not None
        else (f"S{s2}E{e2}" if s2 and e2 else (e2 or s2 or ""))
    )

    # Quality tag — prefer parser-extracted, otherwise fall back to common defaults.
    quality_bits = [parsed.quality, parsed.source]
    quality = " ".join(b for b in quality_bits if b) or "1080p"

    # Identity-variant suffix — produces `.JAP` / `.DirectorsCut` / `.10bit`
    # (with leading dot) when the file carries a variant signal, OR empty
    # string for default-flavor files. Lets templates write
    # `{n} - S{s2}E{e2}{variant}.{x}` and have it produce both
    # `Show - S01E01.mkv` (default) and `Show - S01E01.JAP.mkv` (audio
    # variant) without colliding on disk. Critical fix for dual-audio
    # libraries (`Frieren.01.JAP.mkv` + `Frieren.01.ENG.mkv`) which used
    # to silently overwrite each other during rename.
    variant_parts: list[str] = []
    # R2-C3: include ALL matching audio-language tags (deduped + sorted)
    # so a multi-audio file gets a distinct suffix from its single-audio
    # sibling. Was: first match only — caused two encodes of the same
    # episode with different audio packages to rename to the same path.
    _lang_seen: set[str] = set()
    for tok in (parsed.subtitles or []):
        if isinstance(tok, str) and tok.lower() in ("jap", "eng", "fre", "ger", "ita", "spa"):
            up = tok.upper()
            if up not in _lang_seen:
                _lang_seen.add(up)
                variant_parts.append(up)
    if parsed.edition:
        # Strip punctuation/spaces and CamelCase the edition for filesystem-friendly output.
        # "Director's Cut" → "DirectorsCut", "IMAX" → "IMAX".
        ed = "".join(w.capitalize() for w in
                     re.findall(r"[A-Za-z0-9]+", str(parsed.edition)))
        if ed:
            variant_parts.append(ed)
    if (parsed.bit_depth or "").lower() == "10bit":
        variant_parts.append("10bit")
    # R2-H12: cour falls in ONLY when nothing else distinguishes — split-
    # cour anime (Bleach TYBW arcs, Attack on Titan S4 parts) need it to
    # keep cour-1 and cour-2 files from generating identical paths when
    # they share TVDB season + episode numbers.
    _cour = getattr(parsed, "cour", None)
    if _cour and not variant_parts:
        variant_parts.append(f"Cour{_cour}")
    variant_suffix = "." + ".".join(variant_parts) if variant_parts else ""

    # ── Tier 1.5 step 2: additional tokens ───────────────────────────────
    # PURELY ADDITIVE. Every token below is new; none changes an existing
    # token's value, so any template that doesn't reference these renders
    # byte-for-byte identically (locked by test_templates_jinja). All derive
    # from `parsed` or locals already computed above — no new caller plumbing.
    _name = _safe(library_title or parsed.title or "")
    _year = str(library_year if library_year is not None else parsed.year or "")
    _ny = f"{_name} ({_year})" if _year else _name
    _s00e00 = f"S{s2}E{e2}" if (s2 and e2) else ""
    _sxe = f"{season}x{e2}" if (season is not None and e2) else ""
    _e2end = f"{parsed.episode_end:02d}" if parsed.episode_end is not None else ""
    _audio = " ".join(a for a in (parsed.audio or []) if isinstance(a, str))
    _original = Path(parsed.original_filename).stem

    # Derived: decade from the resolved year, and human file-size tokens.
    _yr_for_decade = library_year if library_year is not None else parsed.year
    _decade = f"{(_yr_for_decade // 10) * 10}s" if _yr_for_decade else ""
    _bytes = str(file_size) if file_size else ""
    _megabytes = f"{file_size / 1048576:.0f}" if file_size else ""
    _gigabytes = f"{file_size / 1073741824:.1f}" if file_size else ""

    # ── Provider-metadata tokens (Tier 1.5 step 2b) ──────────────────────
    # Sourced from the Match row's metadata_blob (director/genres/cast/etc.)
    # + provider ids, assembled by the caller. `metadata=None` (the default,
    # and the path the equivalence test exercises) means every token below is
    # "" — present but empty, so templates that don't use them are unchanged.
    md = metadata or {}

    def _mget(key: str) -> str:
        v = md.get(key)
        if isinstance(v, list):
            return ", ".join(str(x) for x in v if x)
        return "" if v is None else str(v)

    _genres_list = md.get("genres") if isinstance(md.get("genres"), list) else []

    ctx: dict[str, Any] = {
        "n":      _safe(library_title or parsed.title or ""),
        "y":      str(library_year if library_year is not None else parsed.year or ""),
        "q":      quality,
        "x":      Path(parsed.original_filename).suffix.lstrip(".").lower() or "mkv",
        "s2":     s2,
        "e2":     e2,
        "abs":    abs_ep,
        "absx":   absx,
        # `episode_title` lives on the Match row, NOT on ParsedFile — callers
        # pass it in. Earlier code did `parsed.episode_title` which raised
        # AttributeError on every rename, killing the entire flow silently.
        #
        # Fix #9: when episode_title is empty (TVDB cross-ref returned [],
        # provider didn't have a title yet, AniDB ban during scan, etc.),
        # fall back to "Episode NN" so the rendered path doesn't have an
        # ugly double-space-dash-double-space sequence (`...E01 -  [1080p]`).
        # Better to read "Show - S01E01 - Episode 01" than "... - <blank>".
        "t":      _safe(episode_title) if episode_title else (
                      f"Episode {e2}" if e2 else (
                          f"Track {tn}" if tn else ""
                      )
                  ),
        "rg":     _safe(parsed.release_group or ""),
        # `variant` is dot-prefixed when present, empty when not — templates
        # can interpolate freely without producing stray dots on default files.
        "variant": variant_suffix,
        # #15: multi-disc movie marker, pre-formatted Plex-style (" - cd1") so
        # the two halves of a split film land on distinct, stack-detectable
        # paths. Empty for single-file movies and every non-movie type
        # (parsed.disc is None there), so it's a no-op in normal templates.
        "disc":   f" - cd{parsed.disc}" if getattr(parsed, "disc", None) else "",
        "artist": _safe(parsed.artist or ""),
        "album":  _safe(parsed.album or ""),
        "tn":     tn,
        "title":  _safe(parsed.track_title or ""),

        # ── Tier 1.5 step 2 additions (all optional in templates) ────────
        # Convenience composites (the reference renamer-canonical names):
        "ny":     _ny,          # "Frieren (2023)" — name + year
        "s00e00": _s00e00,      # "S01E05"
        "sxe":    _sxe,         # "1x05"
        "e2end":  _e2end,       # zero-padded end episode for ranges (S01E01-E03)
        "ext":    Path(parsed.original_filename).suffix.lstrip(".").lower() or "mkv",  # alias of {x}
        "group":  _safe_opt(parsed.release_group or ""),  # alias of {rg} (blank, not "_", when absent)
        "original": _safe_opt(_original),              # original filename stem
        "mtype":  parsed.media_type or "",             # movie | tv | anime | music
        # Tech tags straight off the parser (filename-derived; pymediainfo
        # backfill in a later step will make these authoritative):
        "resolution": _safe_opt(parsed.quality or ""),  # "1080p"
        "vf":     _safe_opt(parsed.quality or ""),     # the reference renamer {vf} alias
        "source": _safe_opt(parsed.source or ""),      # "BluRay" / "WEB-DL"
        "vc":     _safe_opt(parsed.codec or ""),       # "x265" / "HEVC"
        "ac":     _safe_opt(_audio),                   # audio codec(s)
        "channels": _safe_opt(parsed.channels or ""),  # "5.1" / "7.1" (MediaInfo)
        "hdr":    _safe_opt(parsed.hdr or ""),         # "HDR10" / "DV" / ""
        "bitdepth": _safe_opt(parsed.bit_depth or ""), # "10bit" / "8bit"
        "edition": _safe_opt(parsed.edition or ""),    # "Director's Cut" (normalized)
        "cour":   str(parsed.cour) if parsed.cour is not None else "",
        "airdate": _safe_opt(parsed.air_date or ""),   # ISO "YYYY-MM-DD"
        "decade": _decade,                             # "2010s" (from year)
        "bytes":  _bytes,                              # raw byte count
        "megabytes": _megabytes,                       # "1413"
        "gigabytes": _gigabytes,                       # "3.4"

        # ── Provider-metadata tokens (empty unless the caller passed metadata) ──
        "director": _safe_opt(_mget("director")),
        "genres":  _safe_opt(_mget("genres")),         # "Drama, Sci-Fi"
        "genre":   _safe_opt(str(_genres_list[0]) if _genres_list else ""),  # first genre
        "cast":    _safe_opt(_mget("cast")),           # "Cillian Murphy, Emily Blunt"
        "actors":  _safe_opt(_mget("cast")),           # the reference renamer {actors} alias
        "network": _safe_opt(_mget("network")),
        "studio":  _safe_opt(_mget("studio")),
        "language": _safe_opt(_mget("language")),
        "country": _safe_opt(_mget("country")),
        "runtime": _mget("runtime"),                   # minutes, e.g. "180"
        "label":   _safe_opt(_mget("label")),          # music label
        "yearrange": _safe_opt(_mget("yearRange")),    # "2022 – 2024"
        "tmdbid":  _mget("tmdbid"),
        "tvdbid":  _mget("tvdbid"),
        "anidbid": _mget("anidbid"),
        "imdbid":  _mget("imdbid"),
    }

    # ── Preset macros (Tier 1.5 step 5) ──────────────────────────────────
    # `{{ plex }}` / `{{ jellyfin }}` / `{{ kodi }}` / `{{ emby }}` expand to
    # the FULL canonical path for this file's media type, so a user template
    # can be just `{{ plex }}` (or build on it). Rendered against the ctx we
    # just built; the built-in presets never reference these macro tokens, so
    # there's no recursion. Additive — doesn't touch any existing token.
    for _preset in ("Plex", "Jellyfin", "Kodi"):
        _tmpl = getattr(DEFAULT_PROFILES[_preset], parsed.media_type, DEFAULT_PROFILES[_preset].movie)
        try:
            ctx[_preset.lower()] = apply_template(_tmpl, ctx)
        except Exception:
            ctx[_preset.lower()] = ""
    ctx["emby"] = ctx.get("jellyfin", "")   # Emby shares Jellyfin's layout
    return ctx


def format_target_path(
    parsed: ParsedFile,
    library_root: str,
    profile: NamingProfile,
    library_title: str | None = None,
    library_year: int | None = None,
    episode_title: str | None = None,
    season_override: int | None = None,
    type_target_root: str | None = None,
    metadata: dict[str, Any] | None = None,
    file_size: int | None = None,
    anime_numbering: str = "seasonal",
) -> Path:
    """Build the destination Path for a renamed file.

    `library_title`/`library_year` are the *match-resolved* values (e.g. canonical
    TVDB title), preferred over the parser's guess where available.
    `episode_title` comes from the Match row (the matcher fetched it from the
    provider's episode list). Optional — passes through to the {t} token.
    `season_override` lets the caller pin a canonical season (e.g. AniDB's
    Fribb-mapped TVDB season) without mutating the ParsedFile.

    `type_target_root` (new): per-media-type destination override. When set,
    bypasses the `library_root / SUBFOLDER[type]` convention and uses the
    given path AS the root. Lets users send each media type to its own
    folder (e.g. `Z:\\Plex\\TV Shows` for tv, `Z:\\Plex\\Anime` for anime).
    When None, the legacy `library_root + SUBFOLDER` layout is used so
    existing installs don't change behavior.
    """
    template = select_template(profile, parsed.media_type, anime_numbering=anime_numbering)
    ctx = _build_ctx(
        parsed,
        library_title or parsed.title or "",
        library_year,
        episode_title=episode_title,
        season_override=season_override,
        metadata=metadata,
        file_size=file_size,
    )
    filled = apply_template(template, ctx)
    # Each "/" in the template becomes a real path separator. Sanitize each segment.
    parts = [_safe(p) for p in filled.split("/")]

    # Phase 2: route season-0 files to a "Specials" folder (the reference renamer / Plex /
    # Jellyfin convention) instead of "Season 00" / "S00". We swap the FOLDER
    # segment only (exact match, case-insensitive) — the filename keeps its
    # SxxExx token (e.g. "Show.S00E05...") because only a whole-segment match
    # triggers, never a substring. The episode number still renders as S00E05,
    # which both Plex and Jellyfin read as a special.
    effective_season = season_override if season_override is not None else parsed.season
    if effective_season == 0 and len(parts) > 1:
        parts = [
            "Specials" if p.strip().lower() in ("season 00", "season 0", "s00", "s0") else p
            for p in parts[:-1]
        ] + parts[-1:]

    # Resolve the effective root: caller-supplied per-type override wins,
    # else fall back to `library_root / SUBFOLDER[type]` (legacy default).
    if type_target_root:
        target_root_path = Path(type_target_root).resolve()
    else:
        subfolder = SUBFOLDER.get(parsed.media_type, "Movies")
        target_root_path = (Path(library_root).resolve() / subfolder).resolve()

    target = (target_root_path / Path(*parts)).resolve()

    # PB-3 defense-in-depth: even though `_safe()` strips dangerous chars
    # per-segment, defense-in-depth — confirm the resolved target really
    # is under the configured target root. Belt-and-braces against a
    # future template-injection bug or any path-traversal payload that
    # smuggles `..` through `_safe()`'s sanitizer.
    try:
        target.relative_to(target_root_path)
    except ValueError as e:
        raise ValueError(
            f"Refusing to write outside target root: {target} not under {target_root_path}"
        ) from e
    return target
