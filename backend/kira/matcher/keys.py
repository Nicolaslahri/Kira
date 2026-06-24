"""Pure clustering-key computation for media files.

These functions derive the *grouping* identity of a parsed file:

- ``compute_series_key`` — the key files cluster under on the Review page
  (one card per series/season). Movies get ``None`` (each its own row).
- ``compute_variant_key`` — the identity-variant suffix that keeps distinct
  audio/edition/bit-depth/cour releases of the same episode from colliding
  on a single rename path.

Extracted out of ``kira.api.scans`` (CR-07) so the API layer no longer owns
pure domain logic and so callers can import the keys without pulling in the
FastAPI scan machinery. ``kira.api.scans`` re-exports these under their old
private names (``_compute_series_key`` / ``_compute_variant_key``) for
back-compat.

IMPORTANT: imports ``ParsedFile`` from ``kira.parser`` (NOT from
``kira.api.scans``) to avoid an import cycle.
"""

from pathlib import Path

from kira.matcher.similarity import normalize
from kira.parser import ParsedFile


# Album names that are NOT a real album — a "Singles" / loose-tracks folder collects
# unrelated songs, each its own single/collab. Compared against `normalize(album)`
# (lowercased, hyphens→spaces). Shared with `kira.music.matcher`.
SINGLES_ALBUM_MARKERS = {
    "singles", "single", "non album tracks", "loose",
    "misc", "miscellaneous", "unknown", "unknown album",
}


def compute_series_key(parsed: ParsedFile, file_path: str | None = None) -> str | None:
    """Build the clustering key for series consolidation.

    Files with the same key cluster into one card on the Review page.
    - Movies → null (each is its own row).
    - TV/anime → "{type}|{normalized_title}|{season or ''}|{disambig}".
      Season is kept distinct for BOTH tv and anime: AniDB assigns a
      separate AID to every sequel-season, so merging My Hero Academia
      S01 + S02 under one cluster would mis-stamp every S02 file with
      S01's AID. Visual franchise grouping on the Review page happens
      AFTER matching via Match.series_group_id (which walks AniDB's
      sequel chain).

      EE-5 disambig: without a year or parent-folder fingerprint, two
      shows with the same normalized title (The Office UK 2001 / The
      Office US 2005) collapse into ONE cluster. The matcher then picks
      whichever the provider returns first and stamps every file across
      both shows with the wrong ID. We add a third key component:
        - `parsed.year` when known (strongest signal)
        - else the parent series-folder name (e.g. "The.Office.UK"),
          walked up past any "Season N" subfolder
        - else empty (current behavior — files still cluster together)
    - Music → "music|{normalized_artist}|{normalized_album}" when both
      artist and album exist; otherwise null.
    """
    if parsed.media_type == "movie":
        return None
    if parsed.media_type in ("tv", "anime"):
        if not parsed.title:
            return None
        title_n = normalize(parsed.title)
        if not title_n:
            return None
        season = str(parsed.season) if parsed.season is not None else ""

        # EE-5 disambiguator: year > parent folder > empty.
        disambig = ""
        if parsed.year is not None:
            disambig = str(parsed.year)
        elif file_path:
            try:
                p = Path(file_path)
                parent = p.parent
                # Walk up past "Season N" / "S01" folders to the series root.
                pname_lower = parent.name.lower()
                if pname_lower.startswith("season") or (
                    len(parent.name) <= 4 and pname_lower.startswith("s")
                    and pname_lower[1:].isdigit()
                ):
                    parent = parent.parent
                disambig = normalize(parent.name) or ""
            except Exception:
                disambig = ""

        return f"{parsed.media_type}|{title_n}|{season}|{disambig}"
    if parsed.media_type == "music":
        album_n = normalize(parsed.album) if parsed.album else ""
        # A loose-singles folder collects unrelated songs (each a different
        # single/collab artist). Cluster by the FOLDER so they form ONE "Singles"
        # group instead of scattering into N one-file clusters that each get
        # force-matched to a wrong release. (match_album skips album resolution for
        # the same marker → per-recording matching keeps each song distinct.)
        if album_n in SINGLES_ALBUM_MARKERS and file_path:
            return f"music|singles|{Path(file_path).parent.as_posix().lower()}"
        if not (parsed.artist and parsed.album):
            return None
        return f"music|{normalize(parsed.artist)}|{album_n}"
    return None


# Audio-language tags the format-stripper extracts into `subtitles` (the
# field name is historical — these are AUDIO language indicators in
# real-world media filenames). Used for variant disambiguation.
_LANG_TAGS = ("jap", "eng", "fre", "ger", "ita", "spa")


def compute_variant_key(parsed: ParsedFile) -> str:
    """Build the identity-variant suffix for a file.

    Empty string when no variant signal is detected — most files. Non-empty
    when the file carries any of these differentiators real libraries
    multiplex on:
      - **audio language** (`JAP`, `ENG`, …) — caught from the parser's
        `subtitles` list (misnamed but actually language tags).
        R2-C3 hardening: ALL matching language tags are surfaced (not just
        the first), so a `[JAP, ENG]` multi-audio file gets a distinct
        `jap-eng` key from a `[JAP]`-only sibling. Prevents UNIQUE collisions
        when the same episode has multiple sub/audio variants.
      - **edition** (`Director's Cut`, `IMAX`, `Extended`).
      - **bit depth** (`10bit` only — 8bit is default and excluded so
        legacy files don't suddenly look like "variants of themselves").
      - **cour** (R2-H12) — when present and no other signal is set,
        cour 1/2/3 of a split-cour anime gets surfaced to keep the
        cluster identity distinct. Folded into the key only when no
        other variant is present (audio/edition trump cour for naming).

    Format: "lang-edition-bitdepth[-courN]", components separated by `-`,
    empties skipped. Example: `jap-eng-directors-cut-10bit`, `eng`,
    `10bit`, `cour2`, `""`. Lowercased so equality is reliable across
    "JAP" / "jap" / "Jap" spellings the same release group sometimes mixes.
    """
    lang_tokens = [s for s in (parsed.subtitles or []) if isinstance(s, str)]
    # R2-C3: collect ALL matching languages (deduped, sorted for stable keys)
    # rather than just the first one. A `[JAP, ENG]` file is a distinct
    # variant from a `[JAP]`-only file because it carries an extra audio
    # track — they shouldn't collide on the same variant_key.
    langs = sorted({t.lower() for t in lang_tokens if t.lower() in _LANG_TAGS})
    lang = "-".join(langs)
    edition_raw = parsed.edition or ""
    edition = "".join(c.lower() if c.isalnum() else "-" for c in str(edition_raw)).strip("-")
    bit = (parsed.bit_depth or "").lower()
    if bit == "8bit":
        bit = ""  # 8bit is default, not a variant indicator
    parts = [p for p in (lang, edition, bit) if p]
    # R2-H12: surface cour when nothing else distinguishes — avoids two
    # cour-1 / cour-2 files of the same TVDB season generating the same
    # rename path. Skipped when audio/edition already disambiguate.
    cour = getattr(parsed, "cour", None)
    if cour and not parts:
        parts.append(f"cour{cour}")
    return "-".join(parts)
