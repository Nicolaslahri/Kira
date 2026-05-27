"""Season/episode extraction patterns.

Cascading priority — try each in order, accept the first sane match.
Confidence reflects how trustworthy the pattern is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class SxEMatch:
    season: int | None
    episode: int
    episode_end: int | None = None    # for multi-episode (S01E01-E03)
    absolute: int | None = None       # for anime absolute numbering
    confidence: float = 1.0
    pattern: str = ""
    match_span: tuple[int, int] = (0, 0)
    # When the matched pattern carried a release year alongside the episode
    # (e.g. P3b `YEAR-NN`), surface it here so parse_filename can promote it
    # into ParsedFile.year. None for patterns that don't embed a year.
    year_hint: int | None = None


# 1. Standard SxxExx (highest confidence, handles multi-episode).
#    Range separators we accept: "-", "~", "&", "+", " and " — covers
#    "S01E01-E03", "S01E01~03", "S01E01 & E02" etc. that batch releases use.
#
#    Episode digits: 1-4. Was {1,3}, which silently truncated long-runner
#    files: `One Piece - S23E1160 - Episode 1160 [ToonsHub].mkv` matched
#    as S23E116 (episode "1160" → "116" + leftover "0"), then the matcher
#    chased a non-existent E116 in S23 and the file ended up orphaned.
#    `_sane`'s episode cap rises to 2000 (see below) so explicit 4-digit
#    SxE patterns are accepted while compressed-3-digit patterns (P6)
#    still keep the conservative 500 cap.
_P1_STANDARD = re.compile(
    r"\bS(\d{1,2})E(\d{1,4})(?:(?:\s*[-~&+]\s*|\s+and\s+)E?(\d{1,4}))?\b",
    re.IGNORECASE,
)

# 2. NxMM alternative (12x05).
_P2_X = re.compile(r"\b(\d{1,2})x(\d{1,3})\b", re.IGNORECASE)

# 3. Verbose "Season N Episode M".
_P3_VERBOSE = re.compile(r"\bSeason\s*(\d{1,2}).*?Episode\s*(\d{1,3})\b", re.IGNORECASE)

# 3b. Release-year + episode in `YEAR-NN` form (e.g. `[aL].Sousou.no.Frieren.2023-01`).
#     Some BD reissue groups (notably `[aL]`, certain `[Erai-raws]` re-encodes,
#     and a few music-album rippers' video rips) name episodes as
#     `<show>.<year>-<episode>` instead of `S01E01` or `- 01`. Without this
#     pattern, `2023-01` parses as neither a SxE nor a year (the bare-year
#     extractor requires end-of-string AFTER nothing, and `-01` is in the
#     way), so the file inherits no episode and the cluster reports
#     "File is orphaned · no matching episode" even though the series
#     matched correctly.
#
#     Strict shape rules to avoid colliding with date strings:
#       - R2-H4: Leading hard separator REQUIRED (start of string OR
#         `.` / `_` / `-` / `[`). This blocks `Recorded on 2023-01-15`
#         where the date is embedded in prose with whitespace around it,
#         and other plain-text date forms.
#       - Year MUST be 1980-2049 (real show window).
#       - Episode MUST be 1-3 digits (anime / TV span).
#       - NOT followed by another `-NN` (rules out `2023-01-15` dates).
#       - End-of-string anchor OR a hard separator after the episode.
#     The matcher provides the year via the parsed `year` field too — we
#     promote `m.group(1)` into the caller's year-handling so the year
#     filter in TMDB/AniDB search picks the right release.
_P3B_YEAR_EPISODE = re.compile(
    # Year-dash-episode shape. format_stripper converts dots/underscores
    # to spaces BEFORE this pattern runs, so we can't anchor on "release
    # metadata dot". We rely on two negative shape constraints instead:
    #   1. `(?!\s*-\s*\d)`  — blocks `2023-01-15` (date with third segment)
    #   2. `(?!\s+(?:on|since|from|until|by|at)\b)` BEFORE the year —
    #      blocks "released on 2023-01 last week" and similar prose
    # Plus the trailing-token requirement: must be followed by whitespace
    # / end-of-string / dot / bracket so a bare "2023-01" inside random
    # text doesn't match.
    r"(?<![\w])(?P<y>19[8-9][0-9]|20[0-4][0-9])\s*-\s*(?P<e>\d{1,3})"
    r"(?!\s*-\s*\d)(?=\s|$|\.|\])",
)


# 4. Anime absolute "- 47", "Title-38", or "Title-38v2" (release-version suffix).
#    Accepts: " - 47 ", "-47 ", "Title-38" (end-of-string), "Title-38v2 ".
#    The `(?:v\d+)?` makes the version suffix optional — fansub re-releases
#    name fixes/v2/v3 as `Show-11v2.[…]` and we need to recognize episode 11
#    in either form. Version is intentionally discarded (not surfaced) since
#    it's not a real semantic field anywhere downstream.
_P4_ANIME_DASH = re.compile(r"(?:(?:^|\s)-\s*|(?<=[A-Za-z])-)(\d{1,4})(?:v\d+)?(?=\s|$|\.|\[)")

# 5. Episode-only "E05", "Ep05", "ep 5", "EP1156" (long-running anime).
#    Accepts 1-4 digits — capped at 4 because real long-running anime
#    (One Piece, Detective Conan, Pokémon) hit episode 1000+ but nothing
#    reaches 10000. Three-digit cap left "EP1156" silently unparsed and
#    the matcher then searched the full ugly title and picked the wrong
#    series (e.g. One Piece live-action 2023 instead of the 1999 anime).
_P5_EPISODE_ONLY = re.compile(r"\b[Ee][Pp]?\s*(\d{1,4})\b")

# 6. Compressed 3-4 digit (105 → S1E05). Lowest-confidence pattern;
#    anchored to END-of-cleaned-name so a year-like number mid-title
#    ("The 1492 Project", "Cyberpunk 2077") doesn't get mis-parsed as SxE.
#    Legitimate compressed-numbered files end with the digits ("Show.105").
_P6_COMPRESSED = re.compile(r"(?:^|[\s._\-])(\d{3,4})\s*$")

# 7. "1 of 12" fractional.
_P7_OF = re.compile(r"\b(\d{1,3})\s*of\s*(\d{1,3})\b", re.IGNORECASE)


def extract_sxe(name: str) -> SxEMatch | None:
    """Return the highest-confidence SxE match found, or None."""

    # P1 — standard
    m = _P1_STANDARD.search(name)
    if m:
        s = int(m.group(1))
        e = int(m.group(2))
        end = int(m.group(3)) if m.group(3) else None
        if _sane(s, e):
            return SxEMatch(s, e, end, confidence=0.95, pattern="SxxExx", match_span=m.span())

    # P2 — NxMM
    m = _P2_X.search(name)
    if m:
        s = int(m.group(1))
        e = int(m.group(2))
        if _sane(s, e):
            return SxEMatch(s, e, confidence=0.90, pattern="NxMM", match_span=m.span())

    # P3 — verbose
    m = _P3_VERBOSE.search(name)
    if m:
        s = int(m.group(1))
        e = int(m.group(2))
        if _sane(s, e):
            return SxEMatch(s, e, confidence=0.85, pattern="Season X Episode Y", match_span=m.span())

    # P3b — `<year>-<episode>` ([aL] style: `Sousou.no.Frieren.2023-01`).
    # Runs before P4 (anime dash) so a file with both a year-ep tail and a
    # dash-number elsewhere prefers the more specific signal. Only one
    # interpretation makes sense per file in practice, so this rarely
    # collides with another pattern.
    m = _P3B_YEAR_EPISODE.search(name)
    if m:
        y = int(m.group("y"))
        e = int(m.group("e"))
        if 1 <= e <= 200:
            return SxEMatch(
                season=None, episode=e, absolute=None,
                confidence=0.75, pattern="YEAR-EE",
                match_span=m.span(), year_hint=y,
            )

    # P4 — anime absolute "- 47" — only if no SxE found above.
    # Movie sequels like "John Wick - 4.mkv" or "Toy Story - 3.mkv" use the
    # same dash-number syntax. The reliable discriminator is zero-padding:
    # anime fansubs almost universally pad to at least 2 digits ("- 04"),
    # while movie titles do not. Reject unpadded single digits here so a
    # franchise sequel doesn't get force-classified as anime episode 4.
    m = _P4_ANIME_DASH.search(name)
    if m:
        digit_str = m.group(1)
        abs_ep = int(digit_str)
        if 1 <= abs_ep <= 9999 and not (len(digit_str) == 1 and abs_ep < 10):
            return SxEMatch(None, abs_ep, absolute=abs_ep, confidence=0.70,
                            pattern="anime -NN", match_span=m.span())

    # P5 — episode only (single-season shows / OVAs / long-running anime).
    # Upper bound 9999 covers One Piece (1100+), Detective Conan (1100+),
    # Doraemon (700+), Pokémon (1200+). When the episode number is high
    # enough that no western TV season would reach it (>= 100), we also
    # mark it as `absolute=` so the classifier routes it to anime even
    # without an /anime/ path hint.
    m = _P5_EPISODE_ONLY.search(name)
    if m:
        e = int(m.group(1))
        if 1 <= e <= 9999:
            abs_hint = e if e >= 100 else None
            return SxEMatch(None, e, absolute=abs_hint, confidence=0.60,
                            pattern="EpNN", match_span=m.span())

    # P7 — "N of M"
    m = _P7_OF.search(name)
    if m:
        e = int(m.group(1))
        return SxEMatch(None, e, confidence=0.55, pattern="N of M", match_span=m.span())

    # P6 — compressed 105 → S1E05 (last resort, lowest confidence)
    m = _P6_COMPRESSED.search(name)
    if m:
        digits = m.group(1)
        if len(digits) == 3:
            s, e = int(digits[0]), int(digits[1:])
        else:  # 4 digits
            s, e = int(digits[:2]), int(digits[2:])
        if _sane(s, e, strict=True):
            return SxEMatch(s, e, confidence=0.35, pattern="compressed", match_span=m.span())

    return None


# Common video resolutions that the format-stripper sometimes misses if the
# trailing "p" is dropped ("My.Movie.1080.x264.mkv"). Without explicit
# blacklisting, the compressed-SxE pattern reads 1080 as S10E80, etc.
_RESOLUTION_NUMBERS = {480, 540, 576, 720, 1080, 1440, 2160, 4320}


def extract_absolute_after(cleaned: str, after_pos: int) -> int | None:
    """Greedy second-pass scan for an absolute episode number AFTER the
    primary SxE match. Used when a filename carries both forms — e.g.
    `My Hero Academia S06E15 - 128.mkv` parses primary `(S6, E15)` but
    we still want to capture `128` as the absolute hint for franchise
    routing later. Returns the captured number if one is present and
    passes sanity (1-9999), otherwise None.

    Only scans the slice AFTER the primary match span — searching the
    whole string risks re-matching pieces of the SxE token (the `15`
    in `S06E15` would be captured as absolute=15 otherwise).
    """
    if after_pos < 0 or after_pos >= len(cleaned):
        return None
    tail = cleaned[after_pos:]
    m = _P4_ANIME_DASH.search(tail)
    if not m:
        return None
    digit_str = m.group(1)
    try:
        n = int(digit_str)
    except ValueError:
        return None
    # Same gates as P4's primary path — reject single-digit unpadded
    # (movie sequel trap) and out-of-range values.
    if not (1 <= n <= 9999):
        return None
    if len(digit_str) == 1 and n < 10:
        return None
    return n


def _sane(season: int, episode: int, strict: bool = False) -> bool:
    """Sanity bounds for season/episode numbers.

    Non-strict cap: 2000 episodes. Was 500 — too low for One Piece
    (1160+), Detective Conan (1100+), Pokemon (1200+) and other
    long-running anime that genuinely have explicit episodes >500. The
    cap only mattered for explicit SxE/NxMM patterns; without bumping
    it, `S23E1160` falls through P1 entirely after the regex extension
    to 4 digits, and the file ends up un-numbered.

    Strict cap: still 500. The strict mode protects the compressed
    pattern ``(\\d{3,4})$`` (P6) which can fire on resolutions or
    accidentally-numeric titles. For an unanchored 3-4 digit number
    we want to be conservative.
    """
    if season < 0 or season > 50:
        return False
    if strict and season > 30:
        return False
    cap = 500 if strict else 2000
    if episode < 0 or episode > cap:
        return False
    if strict:
        combined = int(f"{season}{episode:02d}")
        # Reject "1900" → S19E00, "2024" → S20E24 (year as accidental SxE)
        if 1900 <= combined <= 2100:
            return False
        # Reject bare resolution numbers that slipped past the format
        # stripper — 1080 → S10E80, 720 → S7E20, 2160 → S21E60.
        if combined in _RESOLUTION_NUMBERS:
            return False
    return True


# Year extraction. Two tiers:
#   1. Bracketed `(2017)` / `[2017]` — strongest possible release-year
#      signal. Format-stripper preserves these.
#   2. Bare year at the very END of the cleaned name — handles
#      `Inception 2010` once quality tokens (1080p, BluRay, …) have been
#      stripped. Mid-string bare years are deliberately ignored so titles
#      like `The 1492 Project`, `2001 A Space Odyssey` (without a
#      bracketed real year) don't get butchered.
_YEAR_BRACKETED = re.compile(r"[\(\[]\s*(19[0-9]{2}|20[0-4][0-9])\s*[\)\]]")
_YEAR_BARE_END  = re.compile(r"\b(19[0-9]{2}|20[0-4][0-9])\s*$")


def extract_year(name: str) -> tuple[int | None, tuple[int, int] | None]:
    """Return (year, span_in_name).

    The strict end-anchor on bare years prevents `Blade Runner 2049` from
    silently producing year=2049 and truncating the title. The trade-off:
    a movie whose filename has the year mid-string with no brackets won't
    parse the year. That's acceptable — the matcher can still find the
    title and TMDB's year filter is a soft hint anyway.
    """
    bracketed = list(_YEAR_BRACKETED.finditer(name))
    if bracketed:
        m = bracketed[-1]
        return int(m.group(1)), m.span()
    m = _YEAR_BARE_END.search(name)
    if m:
        return int(m.group(1)), m.span()
    return None, None
