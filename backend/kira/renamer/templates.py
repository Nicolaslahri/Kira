"""Naming-template engine — Python port of the frontend formatPath().

Mirrors the token vocabulary the user already sees in Settings → Naming.
Profiles live in the `settings` table under `naming.profiles.<profile>.<type>`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kira.parser import ParsedFile


@dataclass
class NamingProfile:
    movie: str
    tv: str
    anime: str
    music: str


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
        movie="{n} ({y})/{n} ({y}){variant} [{q}].{x}",
        tv="{n} ({y})/Season {s2}/{n} - S{s2}E{e2}{variant} - {t} [{q}].{x}",
        anime="{n}/Season {s2}/{n} - S{s2}E{e2}{variant} - {t} [{rg}].{x}",
        music="{artist}/{album} ({y})/{tn}{variant} - {title}.{x}",
    ),
    "Jellyfin": NamingProfile(
        movie="{n} ({y})/{n} ({y}){variant}.{x}",
        tv="{n} ({y})/Season {s2}/{n} ({y}) - S{s2}E{e2}{variant} - {t}.{x}",
        anime="{n} ({y})/Season {s2}/{n} - S{s2}E{e2}{variant} - {t}.{x}",
        music="{artist}/{album}/{tn}{variant} {title}.{x}",
    ),
    "Kodi": NamingProfile(
        movie="{n} ({y})/{n} ({y}){variant} - {q}.{x}",
        tv="{n}/Season {s2}/{n}.S{s2}E{e2}{variant}.{t}.{x}",
        anime="{n}/S{s2}/{n} - {abs}{variant} - {t}.{x}",
        music="{artist} - {album}/{tn}{variant}. {title}.{x}",
    ),
}


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


def apply_template(template: str, ctx: dict[str, Any]) -> str:
    """Replace {token} placeholders in template using ctx values."""
    out = template
    for k, v in ctx.items():
        out = out.replace("{" + k + "}", "" if v is None else str(v))
    return out


def _build_ctx(
    parsed: ParsedFile,
    library_title: str,
    library_year: int | None,
    episode_title: str | None = None,
    season_override: int | None = None,
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

    return {
        "n":      _safe(library_title or parsed.title or ""),
        "y":      str(library_year if library_year is not None else parsed.year or ""),
        "q":      quality,
        "x":      Path(parsed.original_filename).suffix.lstrip(".").lower() or "mkv",
        "s2":     s2,
        "e2":     e2,
        "abs":    abs_ep,
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
        "artist": _safe(parsed.artist or ""),
        "album":  _safe(parsed.album or ""),
        "tn":     tn,
        "title":  _safe(parsed.track_title or ""),
    }


def format_target_path(
    parsed: ParsedFile,
    library_root: str,
    profile: NamingProfile,
    library_title: str | None = None,
    library_year: int | None = None,
    episode_title: str | None = None,
    season_override: int | None = None,
    type_target_root: str | None = None,
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
    template = getattr(profile, parsed.media_type, profile.movie)
    ctx = _build_ctx(
        parsed,
        library_title or parsed.title or "",
        library_year,
        episode_title=episode_title,
        season_override=season_override,
    )
    filled = apply_template(template, ctx)
    # Each "/" in the template becomes a real path separator. Sanitize each segment.
    parts = [_safe(p) for p in filled.split("/")]

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
