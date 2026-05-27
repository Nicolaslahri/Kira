"""Filename parser — turns a path into a ParsedFile with type, title, SxE, year, etc."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from kira.parser.format_stripper import FormatTokens, strip
from kira.parser.patterns import SxEMatch, extract_absolute_after, extract_sxe, extract_year
from kira.scanner import AUDIO_EXTENSIONS

MediaType = Literal["movie", "tv", "anime", "music", "unknown"]


@dataclass
class ParsedFile:
    original_filename: str
    media_type: MediaType
    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None
    absolute_episode: int | None = None
    # Music
    artist: str | None = None
    album: str | None = None
    track: int | None = None
    track_title: str | None = None
    # Format/quality
    quality: str | None = None
    source: str | None = None
    codec: str | None = None
    audio: list[str] = field(default_factory=list)
    subtitles: list[str] = field(default_factory=list)
    edition: str | None = None
    hdr: str | None = None
    bit_depth: str | None = None  # "10bit" | "8bit" | None — drives the dedupe ranker
    release_group: str | None = None
    # R2-H12: cour/part/arc sub-season hint detected from parent path
    # (e.g. `/Bleach/Season 17/Cour 1/`). Anime split-cour shows assign
    # different AIDs per cour but TVDB lumps them under one season — we
    # store the cour to disambiguate which sub-AID a file belongs to
    # and to surface in the variant_key when no other variant signal
    # exists. None for non-cour-split shows.
    cour: int | None = None
    # Parser's own confidence in the extraction (0-1)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse(path: str | Path) -> ParsedFile:
    """Parse a filesystem path into a ParsedFile."""
    p = Path(path)
    return parse_filename(p.name, parent_path=str(p.parent))


def parse_filename(filename: str, parent_path: str = "") -> ParsedFile:
    """Parse a bare filename. parent_path is optional; used as a fallback hint
    (e.g. an `/anime/` ancestor folder pushes the type toward anime)."""

    # ── Music routing — by extension ──────────────────────────────────────
    ext = Path(filename).suffix.lower()
    if ext in AUDIO_EXTENSIONS:
        return _parse_music(filename, parent_path)

    # ── Video pipeline: strip → classify → split ──────────────────────────
    cleaned, tokens = strip(filename)
    sxe = extract_sxe(cleaned)
    year, year_span = extract_year(cleaned)

    # When the SxE pattern itself carried a release year (P3b YEAR-EE used
    # by `[aL]` and similar BD reissue groups), promote it. Otherwise files
    # like `[aL].Sousou.no.Frieren.2023-01.WEB.…` would lose both the year
    # (bare-year regex needs end-anchor; the `-01` is in the way) AND the
    # episode (no SxE pattern matched before P3b landed). The SxE span
    # already covers the `YEAR-NN` text so _extract_title cuts it cleanly.
    if sxe is not None and sxe.year_hint is not None and year is None:
        year = sxe.year_hint
        year_span = sxe.match_span

    # Greedy second pass for absolute episode: when the primary SxE match
    # captured a (season, episode) but didn't set `absolute`, look for a
    # trailing absolute number AFTER the SxE span (e.g. "S06E15 - 128").
    # Lets the matcher route the file via franchise offset tables later.
    if sxe is not None and sxe.absolute is None and sxe.season is not None:
        # Edge case: P1's multi-episode group greedily absorbed the
        # trailing absolute as `episode_end` (e.g. "S06E15 - 128" parses
        # as the implausible batch S06E15-E128). Re-classify when the
        # span is wider than any realistic multi-ep release — anime
        # multi-eps are typically 2-4, never 100+. Treat such big
        # spans as absolute hints instead.
        if (
            sxe.episode_end is not None
            and sxe.episode_end > sxe.episode + 20
        ):
            sxe.absolute = sxe.episode_end
            sxe.episode_end = None
        else:
            after_pos = sxe.match_span[1] if sxe.match_span else 0
            abs_hint = extract_absolute_after(cleaned, after_pos)
            if abs_hint is not None:
                sxe.absolute = abs_hint

    media_type = _classify(tokens, sxe, parent_path, year)
    title = _extract_title(cleaned, sxe, year_span, media_type)

    # Season fallback — anime files often look like `Foo S3 - 03` (filename
    # carries a bare S3, not a full SxE) and Plex-style libraries put each
    # season in its own `Season N` folder. Without picking up either, the
    # matcher has no way to tell S3 apart from S1 of the same franchise
    # and merrily matches both to AID 15299 (S1) for Rent-a-Girlfriend.
    season_from_path = _season_from_parent(parent_path) if media_type in ("tv", "anime") else None
    season_inline = _season_from_title_suffix(title) if media_type in ("tv", "anime") else None
    final_season: int | None
    if sxe and sxe.season is not None:
        final_season = sxe.season
    elif season_inline is not None:
        final_season = season_inline
        # Strip the trailing `S3` so the provider search isn't polluted by
        # a token AniDB doesn't index ("Kanojo, Okarishimasu S3" returns
        # the S1 AID with the highest trigram score; bare "Kanojo,
        # Okarishimasu" lets the season-rerank do its job).
        title = _strip_season_suffix(title)
    elif season_from_path is not None:
        final_season = season_from_path
    else:
        final_season = None

    # R2-H12: cour detection from parent path. The helper exists since
    # an earlier iteration but was never actually called. Files in a
    # `Cour 1` / `Cour 2` subfolder need this hint to disambiguate split-
    # cour anime (Bleach TYBW, Attack on Titan S4, Demon Slayer S2). The
    # cour number flows into _compute_variant_key so two files in
    # different cours of the same TVDB season don't collide on rename.
    cour = _cour_from_parent(parent_path) if media_type in ("tv", "anime") else None

    # ── Confidence ───────────────────────────────────────────────────────
    confidence = _score(title, sxe, year, media_type)

    return ParsedFile(
        original_filename=filename,
        media_type=media_type,
        title=title,
        year=year if media_type in ("movie", "tv", "anime") else None,
        season=final_season,
        episode=sxe.episode if sxe else None,
        episode_end=sxe.episode_end if sxe else None,
        absolute_episode=sxe.absolute if sxe else None,
        quality=tokens.quality,
        source=tokens.source,
        codec=tokens.codec,
        audio=tokens.audio,
        subtitles=tokens.subtitles,
        edition=tokens.edition,
        hdr=tokens.hdr,
        bit_depth=tokens.bit_depth,
        release_group=tokens.release_group,
        cour=cour,
        confidence=confidence,
    )


# ──────────────────────────────────────────────────────────────────────────
# Music — separate pipeline (artist/album/track structure)
# ──────────────────────────────────────────────────────────────────────────

# Anchor on the unambiguous track-number token "- NN - …", then split the
# prefix from the right so artist names containing hyphens ("Jay - Z",
# "Florence + The Machine") stay intact instead of being chopped by the
# old greedy "Artist - Album - N - Title" regex.
_MUSIC_TRACK_ANCHOR = re.compile(r"^(?P<pre>.+?)\s*[-]\s*(?P<n>\d{1,3})\s*[-]\s*(?P<title>.+)$")
# Bare "03 - Title" with no prefix.
_MUSIC_TRACK_TITLE = re.compile(r"^(?P<n>\d{1,3})\s*[-_.]\s*(?P<title>.+)$")
# "Track 03" — no title in filename.
_MUSIC_TRACK_ONLY = re.compile(r"^Track\s*(?P<n>\d{1,3})$", re.IGNORECASE)

# Folder names that look like "Music"/"Downloads" — don't promote them to
# the artist field when walking grandparent dirs.
_NON_ARTIST_FOLDERS = {
    "music", "downloads", "audio", "songs", "tracks",
    "media", "library", "albums", "artists", "rip", "rips",
}


def _parse_music(filename: str, parent_path: str) -> ParsedFile:
    stem = Path(filename).stem
    # underscore folder names like "fleetwood_mac_-_rumours_-_05_-_..." normalize
    norm = re.sub(r"[_]+", " ", stem)
    norm = re.sub(r"\s{2,}", " ", norm).strip()

    artist: str | None = None
    album: str | None = None
    track: int | None = None
    track_title: str | None = None

    if m := _MUSIC_TRACK_ANCHOR.match(norm):
        # Found "<prefix> - NN - <title>". Split prefix from the RIGHT so
        # hyphenated artist names stay together.
        prefix = m.group("pre").strip()
        track = int(m.group("n"))
        track_title = m.group("title").strip()
        if " - " in prefix:
            a, b = prefix.rsplit(" - ", 1)
            artist = a.strip().title()
            album = b.strip().title()
        else:
            artist = prefix.title()
    elif m := _MUSIC_TRACK_TITLE.match(norm):
        track = int(m.group("n"))
        track_title = m.group("title").strip()
    elif m := _MUSIC_TRACK_ONLY.match(norm):
        track = int(m.group("n"))

    # Fall back to parent folder structure for artist/album when filename
    # didn't supply them. The canonical layout is `Artist / Album / NN.mp3`,
    # so we walk up one level too.
    if (artist is None or album is None) and parent_path:
        parent_dir = Path(parent_path)
        parent_name = parent_dir.name
        if " - " in parent_name:
            # "Artist - Album" all-in-one folder.
            a, b = parent_name.split(" - ", 1)
            if artist is None:
                artist = a.strip().replace("_", " ")
            if album is None:
                album = b.strip().replace("_", " ")
        else:
            # Album folder. Grandparent is usually the artist.
            if album is None and parent_name:
                album = parent_name.replace("_", " ").strip()
            if artist is None:
                gp_name = parent_dir.parent.name if str(parent_dir.parent) != str(parent_dir) else ""
                if gp_name and gp_name.lower() not in _NON_ARTIST_FOLDERS:
                    artist = gp_name.replace("_", " ").strip()

    # Fix #12: extract year from the album folder name when present.
    # Album folders commonly include the release year as `Abbey Road (1969)`
    # or `Rumours [1977]`. Without this, the music template renders an
    # empty `()` in the album path (`Beatles/Abbey Road ()/01 - ...`).
    # We strip the year suffix from the album name too so the rendered
    # path doesn't double up.
    year_extracted: int | None = None
    if album:
        m_year = re.search(r"[\(\[](\d{4})[\)\]]", album)
        if m_year:
            y = int(m_year.group(1))
            if 1900 <= y <= 2100:
                year_extracted = y
                # Trim the (YYYY) suffix from album so {y} owns it.
                album = re.sub(r"\s*[\(\[]\d{4}[\)\]]\s*$", "", album).strip() or album

    confidence = 0.0
    if artist and album and track_title:
        confidence = 0.85
    elif artist and track_title:
        confidence = 0.65
    elif track_title:
        confidence = 0.45
    elif track:
        confidence = 0.25

    return ParsedFile(
        original_filename=filename,
        media_type="music",
        title=track_title or stem,
        year=year_extracted,
        artist=artist,
        album=album,
        track=track,
        track_title=track_title,
        confidence=confidence,
    )


# ──────────────────────────────────────────────────────────────────────────
# Classification helpers
# ──────────────────────────────────────────────────────────────────────────


def _classify(tokens: FormatTokens, sxe: SxEMatch | None,
              parent_path: str, year: int | None) -> MediaType:
    """Decide movie | tv | anime | unknown based on signals.

    Path hints come first — they're almost always right when present.
    The SxE signal is checked after path hints so an unstructured TV folder
    like `/TV Shows/The Sopranos/Pilot.mkv` still classifies as TV even
    though there's no SxE in the filename.
    """
    parent = parent_path.lower().replace("\\", "/")

    # Anime path hint
    if "/anime/" in parent or parent.endswith("/anime"):
        return "anime"

    # [GROUP] tag at start strongly indicates anime fansub.
    if tokens.release_group and _looks_like_fansub(tokens.release_group):
        return "anime"

    # TV path hints — covers common Plex/Jellyfin/Sonarr layouts. A `Season N`
    # folder is a near-certain TV signal even when the episode title is the
    # only thing in the filename.
    if any(p in parent for p in ("/tv/", "/tv shows/", "/series/", "/shows/")) \
       or "/season " in parent or parent.endswith("/season"):
        return "tv"

    # Movie path hints — /Movies/, /Films/, /Cinema/
    if any(p in parent for p in ("/movies/", "/films/", "/cinema/")):
        return "movie"

    # SxE present without a path hint — anime if absolute numbering, else TV.
    if sxe is not None:
        if sxe.absolute is not None:
            return "anime"
        return "tv"

    # No path hint, no SxE. Fall through to "movie" only when we have a year
    # (a near-canonical release-named movie signal). Garbage filenames like
    # `video_final_v2.mkv` with no year stay "unknown" so they can't
    # artificially inflate confidence via the +0.1 scoring boost.
    if year is not None:
        return "movie"
    return "unknown"


# Common fansub release groups — extend as we see more. Used only as one signal.
_FANSUB_GROUPS = {
    "subsplease", "erai-raws", "horriblesubs", "asw", "judas", "smc",
    "commie", "fff", "underwater", "doki", "lazier", "toonshub", "anime time",
}


def _looks_like_fansub(group: str) -> bool:
    return group.lower() in _FANSUB_GROUPS


# ──────────────────────────────────────────────────────────────────────────
# Title extraction
# ──────────────────────────────────────────────────────────────────────────


# Bare `S2` / `S03` at the END of the extracted title, after the franchise
# name. Matches "Kanojo, Okarishimasu S3" but NOT "Series S01E05" (that
# would have been captured by P1 SxxExx already and stripped from the
# title by _extract_title's match-span cut).
_INLINE_SEASON_SUFFIX = re.compile(r"\s+[sS](\d{1,2})\s*$")

# `Season N` or `Series N` ancestor folder. Used as a season hint when
# the filename itself doesn't carry one. The path comes from pathlib so we
# normalize backslashes before matching.
_PARENT_SEASON = re.compile(r"[/\\](?:Season|Series)\s+(\d{1,2})(?:[/\\]|$)", re.IGNORECASE)

# M3: `Cour N` / `Part N` / `Arc N` sub-folder hints. AniDB splits some
# long anime into multiple AIDs per cour (Bleach TYBW arcs, Attack on
# Titan S4 parts, Demon Slayer S2 cour 2 vs Mugen Train arc). When a file
# lives under a cour/part subfolder, that's a strong identity signal
# distinct from the parent Season folder. Captured as a separate field
# so downstream code (Fribb lookup, variant_key) can use it without it
# being confused with the season number.
_PARENT_COUR = re.compile(
    r"[/\\](?:Cour|Part|Arc)\s*(\d{1,2})(?:[/\\]|$)",
    re.IGNORECASE,
)


def _season_from_title_suffix(title: str) -> int | None:
    """Detect a trailing `S2` / `S03` in the extracted title."""
    m = _INLINE_SEASON_SUFFIX.search(title or "")
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 50 else None


def _strip_season_suffix(title: str) -> str:
    """Drop the trailing `S2` / `S03` token from a title."""
    return _INLINE_SEASON_SUFFIX.sub("", title or "").strip()


def _season_from_parent(parent_path: str) -> int | None:
    """Detect `…/Season 3/…` from the parent path."""
    if not parent_path:
        return None
    m = _PARENT_SEASON.search(parent_path)
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 50 else None


def _cour_from_parent(parent_path: str) -> int | None:
    """M3: detect `…/Cour 2/…`, `…/Part 1/…`, `…/Arc 3/…` in the parent path.

    Cour/part/arc is anime's "sub-season" — a chronological chunk of a
    show that AniDB usually splits into its own AID. Without picking this
    up, two files in `Show/Season 17/cour 1/` and `Show/Season 17/cour 2/`
    look identical to the matcher and end up with the same AID, mis-
    rendered as one season in the UI.
    """
    if not parent_path:
        return None
    m = _PARENT_COUR.search(parent_path)
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 10 else None


def _extract_title(cleaned: str, sxe: SxEMatch | None,
                   year_span: tuple[int, int] | None,
                   media_type: MediaType) -> str:
    """Pull the title from the cleaned name, cutting at the earliest of SxE
    or year — the year always lives in ParsedFile.year, never in the title."""
    cut: int | None = None
    if sxe is not None:
        cut = sxe.match_span[0]
    if year_span is not None and (cut is None or year_span[0] < cut):
        cut = year_span[0]

    title = cleaned[:cut] if cut is not None else cleaned

    # Title-case-friendly cleanup: drop trailing/leading separators, collapse spaces.
    title = re.sub(r"^[-._\s]+|[-._\s]+$", "", title)
    title = re.sub(r"\s{2,}", " ", title)
    return title.strip()


# ──────────────────────────────────────────────────────────────────────────
# Confidence scoring (parser-internal, separate from matcher confidence)
# ──────────────────────────────────────────────────────────────────────────


def _score(title: str, sxe: SxEMatch | None, year: int | None, media_type: MediaType) -> float:
    """Parser confidence in its own extraction (not the eventual TMDB match)."""
    if not title:
        return 0.0
    score = 0.4  # baseline for having any title at all
    if len(title) >= 3:
        score += 0.1
    if year is not None:
        score += 0.15
    if sxe is not None:
        score += 0.15 * sxe.confidence
    if media_type != "unknown":
        score += 0.1
    return min(score, 1.0)
