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
    # When the matched pattern carried an anime "Part N" / "Cour N" token
    # (e.g. PB `Part 3 - 01`), surface the cour so parse_filename can route
    # split-cour anime to the right sibling AID. None for non-cour patterns.
    cour: int | None = None


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
#    Multi-episode end (group 3) covers the SEPARATED forms (`-E03`, `-03`,
#    `~03`, `& E02`, ` and 02`). Group 4 covers the GLUED form `S01E01E02`
#    (no separator at all) that batch rippers emit — without it the second
#    episode silently dropped and a 2-parter imported as a single episode.
#    The `(?:E\d{1,4})*` before group 4 absorbs ANY extra glued episodes so
#    a 3+-parter (`S01E01E02E03`) still matches — group 4 captures the LAST
#    one as the upper bound. Without it the trailing `\b` can't anchor between
#    two glued `E\d` tokens and the WHOLE match fails (file → unmatchable).
_P1_STANDARD = re.compile(
    r"\bS(\d{1,2})E(\d{1,4})(?:(?:\s*[-~&+]\s*|\s+and\s+)E?(\d{1,4})|(?:E\d{1,4})*E(\d{1,4}))?\b",
    re.IGNORECASE,
)

# 2. NxMM alternative (12x05).
_P2_X = re.compile(r"\b(\d{1,2})x(\d{1,3})\b", re.IGNORECASE)

# 3. Verbose "Season N Episode M".
_P3_VERBOSE = re.compile(r"\bSeason\s*(\d{1,2}).*?Episode\s*(\d{1,3})\b", re.IGNORECASE)

# 3c. Named-season dash-episode "Season N-MM" / "Season N - MM".
#     The bare "2-06" in "Attack on Titan Season 2-06" matches NO existing
#     SxE pattern: P4 (anime dash) needs a space or letter before the dash
#     ("- 06" or "Title-06"), but "2-06" has a digit before the dash; P6
#     (compressed) needs 3-4 contiguous digits. So the file parsed with
#     episode=None and the matcher fell back to the most-similar AoT entry,
#     scattering Season-2 files into the Season-1 card. The literal "Season"
#     keyword makes this an unambiguous (season, episode) signal. We deliver
#     a real season number too, which the AnimeSeasonOrdinalMetric then uses
#     to route the file to the correct per-season AID.
_PA_SEASON_DASH = re.compile(
    r"\bSeason\s+(\d{1,2})\s*-\s*(\d{1,3})\b",
    re.IGNORECASE,
)

# 3d. Anime cour dash-episode "Part N-MM" / "Cour N - MM".
#     "Shingeki no Kyojin - The Final Season Part 3 - 01" / "...Part 3-01".
#     "Part N" / "Cour N" is anime's sub-season chunk — AniDB splits it into
#     its own AID while TVDB lumps the parts under one season. We capture the
#     episode AND the cour (so cour_routing can pick the right sibling AID),
#     and anchor match_span at "Part"/"Cour" so _extract_title cuts the noise
#     but keeps the real title qualifier (e.g. "The Final Season" survives —
#     it's AniDB's actual title for that AID and trigram-matches it directly).
#     season is left None: we don't know the franchise's season count here;
#     Fribb / the episode-list gate resolves the AID.
_PB_PART_DASH = re.compile(
    r"\b(?:Part|Cour)\s+(\d{1,2})\s*-\s*(\d{1,3})\b",
    re.IGNORECASE,
)

# Specials / OVA / OAV / ONA / SP → season 0 (Plex / Jellyfin "Specials").
#   "Bleach Special 05", "Attack on Titan OVA", "Naruto SP01", "Show OAV-3".
#   NOTE: "S00E01" is already handled by P1 (season 0); these cover the
#   word/abbreviation forms the reference renamer recognizes that no other pattern catches.
#
# Two regexes so we can require a NUMBER after the high-collision word
# "Special" (blocks "Special Edition" / "Special Forces" / the movie
# "Special 26") while still accepting a bare "OVA"/"OAV"/"ONA" (those are
# almost never real title words). A title-before guard in extract_sxe rejects
# a leading marker so "Special 26" the movie isn't read as a special episode.
_PSPECIAL_NUM = re.compile(
    r"\b(?:Specials?|OVA|OAV|ONA|SP)\s*[-:]?\s*(\d{1,3})\b",
    re.IGNORECASE,
)
_PSPECIAL_BARE = re.compile(r"\b(?:OVA|OAV|ONA)\b", re.IGNORECASE)

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

# 5b. Bracketed absolute episode: `[1158]`, `[03]`, `[247]`.
#     Anime fansubs use this shape for absolute episode numbers on
#     long-runners and standalone OVAs (`[SubsPlease] Frieren - [1158].mkv`).
#     The format-stripper's `_looks_like_metadata` preserves pure 2-4
#     digit brackets so they survive into the cleaned name; without
#     this pattern they'd still be discarded by `_extract_title` as
#     bracket noise. Confidence parity with P4 (anime-dash) since both
#     are explicit absolute markers.
_P5B_BRACKET_ABS = re.compile(r"\[(\d{2,4})\]")

# 6. Compressed 3-4 digit (105 → S1E05). Lowest-confidence pattern;
#    anchored to END-of-cleaned-name so a year-like number mid-title
#    ("The 1492 Project", "Cyberpunk 2077") doesn't get mis-parsed as SxE.
#    Legitimate compressed-numbered files end with the digits ("Show.105").
_P6_COMPRESSED = re.compile(r"(?:^|[\s._\-])(\d{3,4})\s*$")

# 7. "1 of 12" fractional.
_P7_OF = re.compile(r"\b(\d{1,3})\s*of\s*(\d{1,3})\b", re.IGNORECASE)


def _has_title_before(name: str, start: int) -> bool:
    """True when there's real title text before position `start`.

    Used to reject a leading special-marker that's actually a title word —
    "Special 26" (the movie) / "OVA" as a standalone name should NOT be read
    as a season-0 episode. A genuine special is "<Series> Special 05", where
    "Special" follows the series name.
    """
    before = name[:start].strip(" .-_[]()")
    return len(before) >= 2 and any(c.isalpha() for c in before)


def _bracket_absolute(name: str) -> int | None:
    """A SEPARATE bracketed absolute number `[36]` in the name, if any —
    excluding release years and stray resolution values.

    Lets a file carrying BOTH a season-local dash-episode AND a bracket-
    absolute (`[Moozzi2] Kanojo S3 - 12 [36]`) keep 12 as the (season-local)
    episode while taking 36 as the absolute, instead of P4 overloading the
    dash-number as both episode and absolute and discarding the real `[36]`.

    Prefer the LAST qualifying bracket: the absolute is conventionally the
    trailing `[NN]`, whereas a leading numeric bracket is more often a group
    tag / index (`[12] Show - 05 [36]` → 36, not 12)."""
    result: int | None = None
    for bm in _P5B_BRACKET_ABS.finditer(name):
        v = int(bm.group(1))
        if 1 <= v <= 9999 and not (1900 <= v <= 2049) and v not in _RESOLUTION_NUMBERS:
            result = v
    return result


def extract_sxe(name: str) -> SxEMatch | None:
    """Return the highest-confidence SxE match found, or None."""

    # P1 — standard
    m = _P1_STANDARD.search(name)
    if m:
        s = int(m.group(1))
        e = int(m.group(2))
        # group(3) = separated end (`-E03` / `-03` / `& E02`); group(4) = the
        # glued end (`S01E01E02`). Either supplies the multi-episode upper bound.
        end_str = m.group(3) or m.group(4)
        end = int(end_str) if end_str else None
        # A span must run FORWARD: "E05-E02" is a parse artifact (checksum,
        # date fragment), not a 4-episode range — drop the bogus end rather
        # than let an inverted span flow into rename templates.
        if end is not None and end <= e:
            end = None
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

    # PA — "Season N-MM" dash-episode (named-season form). Runs before P4 so
    # the match_span covers the whole "Season N-MM" token and _extract_title
    # cuts the title cleanly. High confidence — the literal "Season" keyword
    # plus a dash-episode is unambiguous.
    m = _PA_SEASON_DASH.search(name)
    if m:
        s = int(m.group(1))
        e = int(m.group(2))
        if _sane(s, e):
            return SxEMatch(s, e, confidence=0.88, pattern="Season N-MM", match_span=m.span())

    # PB — "Part N-MM" / "Cour N-MM" anime cour dash-episode. Runs before P4
    # so "Part 3 - 01" is consumed as (episode=1, cour=3) instead of P4
    # grabbing just "- 01" and leaving "Part 3" glued into the title.
    # Reject an unpadded single-digit episode ("Part 2 - 5") the same way P4
    # does — anime fansubs pad ("- 05"), and a bare "- 5" after "Part 2" is
    # more likely a movie-title fragment than a cour episode.
    m = _PB_PART_DASH.search(name)
    if m:
        cour = int(m.group(1))
        e_str = m.group(2)
        e = int(e_str)
        if 1 <= e <= 2000 and 1 <= cour <= 10 and not (len(e_str) == 1 and e < 10):
            return SxEMatch(None, e, cour=cour, confidence=0.80,
                            pattern="Part N-MM", match_span=m.span())

    # PSPECIAL — Specials / OVA / OAV / ONA / SP → season 0. Runs after the
    # standard + named-season patterns (a real SxE wins) but before P4/P5/P6
    # so "Special 105" isn't read as compressed S1E05 and "OVA 3" isn't
    # grabbed as absolute 3. The title-before guard rejects a leading marker
    # (a bare "Special 26" / "OVA" at position 0 is a title, not an episode).
    m = _PSPECIAL_NUM.search(name)
    if m and _has_title_before(name, m.start()):
        e = int(m.group(1))
        if 1 <= e <= 500:
            return SxEMatch(season=0, episode=e, confidence=0.68,
                            pattern="special", match_span=m.span())
    m = _PSPECIAL_BARE.search(name)
    if m and _has_title_before(name, m.start()):
        return SxEMatch(season=0, episode=1, confidence=0.60,
                        pattern="special bare", match_span=m.span())

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
    #
    # Year-range guard: "Toy Story - 1995", "Tron - 1982", "Twilight -
    # New Moon - 2009" use the dash-year suffix as a Radarr-style
    # release-year decorator, NOT as an episode marker. 4-digit values
    # in the year window are rejected so the extract_year pass can
    # pick them up as the release year and the matcher routes the file
    # as a movie. Real long-running anime never hits the 1900-2099
    # window (One Piece ~1100, Pokémon ~1200 — multiple decades of
    # headroom). Same logic mirrors the P5B bracket-abs and P6
    # compressed-SxE year guards we already apply elsewhere.
    m = _P4_ANIME_DASH.search(name)
    if m:
        digit_str = m.group(1)
        abs_ep = int(digit_str)
        # Title-with-number guard: a dash-number IMMEDIATELY followed by a
        # release year is part of the title, not an episode — `Catch-22.2019`,
        # `Fahrenheit-451 (1966)`. Real anime ("Blood War-38 [1080p]") is
        # followed by tags/brackets, never a bare year, so this only suppresses
        # the false positives. (A single `.`/space separator, then a 4-digit
        # year with no bracket/paren in between.)
        _after = name[m.end():]
        _year_follows = bool(re.match(r"[. ](?:19|20)\d\d\b", _after))
        if (
            1 <= abs_ep <= 9999
            and not (len(digit_str) == 1 and abs_ep < 10)
            and not (1900 <= abs_ep <= 2099)
            and not _year_follows
        ):
            # If a SEPARATE bracket-absolute exists, IT is the real absolute and
            # the dash-number is the season-local episode (`S3 - 12 [36]` → ep
            # 12, abs 36). With no bracket, the dash-number IS the absolute for
            # a pure long-runner (`One Piece - 1156`) — keep the prior behaviour.
            bracket_abs = _bracket_absolute(name)
            return SxEMatch(None, abs_ep, absolute=bracket_abs or abs_ep,
                            confidence=0.70, pattern="anime -NN", match_span=m.span())

    # P5 — episode only (single-season shows / OVAs / long-running anime).
    # Upper bound 9999 covers One Piece (1100+), Detective Conan (1100+),
    # Doraemon (700+), Pokémon (1200+). When the episode number is high
    # enough that no western TV season would reach it (>= 100), we also
    # mark it as `absolute=` so the classifier routes it to anime even
    # without an /anime/ path hint.
    m = _P5_EPISODE_ONLY.search(name)
    if m:
        e = int(m.group(1))
        # Year guard (same as P4 / P5B / P6): a 4-digit value in the 1900-2099
        # window is a release year, not an episode — `WALL-E.2008` matched the
        # trailing `E` + `2008` and became "anime episode 2008". Real
        # absolute-numbered anime never reaches this window (One Piece ~1100).
        if 1 <= e <= 9999 and not (1900 <= e <= 2099):
            abs_hint = e if e >= 100 else None
            return SxEMatch(None, e, absolute=abs_hint, confidence=0.60,
                            pattern="EpNN", match_span=m.span())

    # P5B — bracketed absolute episode `[1158]` (anime fansub convention).
    # Year-range guard: 1900-2049 four-digit values are almost certainly
    # release years (`[YTS] Dune [2021].mkv`, `[aL] Akira [1988].mkv`) NOT
    # episode 2021 or 1988. Skipping that range loses no legitimate
    # episodes — One Piece is the longest at ~1100, Pokémon ~1200, real
    # absolute-numbered anime never reaches the 1900-2049 window. Lets
    # `extract_year` later consume `[2021]` as the release year cleanly.
    m = _P5B_BRACKET_ABS.search(name)
    if m:
        e = int(m.group(1))
        if 1 <= e <= 9999 and not (1900 <= e <= 2049):
            return SxEMatch(None, e, absolute=e, confidence=0.70,
                            pattern="bracket abs", match_span=m.span())

    # P7 — "N of M"
    m = _P7_OF.search(name)
    if m:
        e = int(m.group(1))
        return SxEMatch(None, e, confidence=0.55, pattern="N of M", match_span=m.span())

    # P6 — compressed 105 → S1E05 (last resort, lowest confidence).
    #
    # Year-as-title guard for 4-digit values: numbers ≥ 1900 at the
    # end of a cleaned name are almost always release years embedded
    # mid-title OR sci-fi title-years, NOT compressed SxE codes:
    #   - `3022.mkv` → would parse as S30E22 (a movie titled "3022")
    #   - `Dracula 3000.mkv` → would parse as S30E0
    #   - `The.Year.3000.mkv` → would parse as S30E0
    #   - `Cyborg 2087.mkv` → would parse as S20E87
    #   - `Show.1984.mkv` → would parse as S19E84 (historical title)
    # The strict `_sane` cap of `season ≤ 30` was too permissive: S30
    # was accepted even though no TV show actually uses compressed
    # `SS|EE` notation at 30 seasons (they'd use explicit SxxExx).
    # Real compressed 4-digit SxE files sit below 1900 (rare anyway —
    # the natural ceiling is `S18E99` = 1899 for a hypothetical 18-
    # season run; most users write `S08E12` not `0812`). When the
    # guard fires, the file falls through to no-SxE-match → the year
    # extractor downstream gets first crack at the 4-digit number,
    # which is what we want.
    m = _P6_COMPRESSED.search(name)
    if m:
        digits = m.group(1)
        parsed: tuple[int, int] | None = None
        if len(digits) == 3:
            parsed = (int(digits[0]), int(digits[1:]))
        elif len(digits) == 4 and int(digits) < 1900:
            parsed = (int(digits[:2]), int(digits[2:]))
        if parsed is not None and _sane(*parsed, strict=True):
            return SxEMatch(*parsed, confidence=0.35, pattern="compressed", match_span=m.span())

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
        # Episode 0 in the compressed form is spurious — `300` → S03E00 is the
        # movie "300", not a real episode. (An explicit `S01E00` pilot/special
        # uses the NON-strict path, so it's unaffected.)
        if episode == 0:
            return False
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
