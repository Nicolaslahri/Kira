"""Subtitle coverage — does a file already have the user's wanted languages?

A file is *covered* for a language when that language is present either as an
embedded text track (MediaInfo's `sub_langs`) OR as a sidecar already on disk
(`<stem>.<lang>.<ext>`). *Missing* = the wanted languages minus that union.

Everything here is pure except `scan_sidecar_langs`, which does one directory
listing per parent folder (memoized by the caller) — isolated so it's testable
with a tmp dir and never touches the event loop directly.

Language normalization folds the 3-letter codes MediaInfo emits ("eng"), the
2-letter codes the user's preference uses ("en"), and full names ("English")
all to one 2-letter key, so "eng" embedded satisfies a wanted "en".
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

from kira.subtitles.embedded import normalize_lang

# Sidecar subtitle extensions we recognize as "a subtitle is present". Matches
# what the fetch/extract paths write plus the common external formats a user
# might already have sitting beside their videos.
_SIDECAR_EXTS = ("srt", "ass", "ssa", "vtt", "sub")


def normalize_langs(values: Iterable[Any]) -> set[str]:
    """Normalize a bunch of language strings to the 2-letter coverage key,
    dropping anything that normalizes to nothing."""
    out: set[str] = set()
    for v in values or ():
        n = normalize_lang(v if isinstance(v, str) else str(v) if v is not None else None)
        if n:
            out.add(n)
    return out


def present_languages(parsed: dict[str, Any] | None) -> set[str]:
    """The languages already covered for a file, read from its parsed_data:
    embedded tracks (`sub_langs`) ∪ cached sidecars (`sub_sidecars`). Both are
    normalized to the 2-letter coverage key."""
    if not isinstance(parsed, dict):
        return set()
    present = normalize_langs(parsed.get("sub_langs") or [])
    present |= normalize_langs(parsed.get("sub_sidecars") or [])
    return present


def has_been_inspected(parsed: dict[str, Any] | None) -> bool:
    """True when we actually know a file's subtitle picture — either the
    MediaInfo container read ran (`mi_stamp` is stamped on any successful read,
    tracks or not) or we've recorded its sidecars. Distinguishes a genuinely
    sub-less file from one we simply haven't looked at, so the UI can show
    "unknown" instead of falsely flagging every language missing."""
    if not isinstance(parsed, dict):
        return False
    return parsed.get("mi_stamp") is not None or parsed.get("sub_sidecars") is not None


def missing_languages(parsed: dict[str, Any] | None, wanted: Iterable[str]) -> list[str] | None:
    """Wanted languages a file is missing, preserving the wanted order and the
    user's original codes. Returns:
      - ``None`` when coverage is *unknown* (no wanted langs, or the file was
        never inspected) — the UI shows nothing rather than a false alarm;
      - ``[]`` when fully covered;
      - the missing wanted codes otherwise.
    """
    # Music (audio) has no subtitle concept — never report it as missing subs.
    # Single gate: the CC badge, the coverage tile, and the backfill all consult
    # this, so music drops out of subtitle handling everywhere at once.
    if (parsed or {}).get("media_type") == "music":
        return None
    wanted_list = [w for w in (str(x).strip() for x in (wanted or ())) if w]
    if not wanted_list:
        return None
    if not has_been_inspected(parsed):
        return None
    present = present_languages(parsed)
    return [w for w in wanted_list if normalize_lang(w) not in present]


def sidecar_lang(video_stem: str, sidecar_name: str) -> str | None:
    """Extract the language token from a sidecar filename that belongs to a
    given video stem: ``<stem>.<lang>.<ext>`` → the normalized lang, else None.
    A sidecar with no language segment (``<stem>.srt``) yields None — we can't
    attribute it to a language, so it doesn't count toward coverage. Pure."""
    name = sidecar_name
    if not name.lower().endswith(tuple("." + e for e in _SIDECAR_EXTS)):
        return None
    base, _, _ext = name.rpartition(".")          # strip extension
    stem_lower = video_stem.lower()
    if not base.lower().startswith(stem_lower + "."):
        return None
    lang_token = base[len(video_stem) + 1:]        # the bit between stem. and .ext
    # Only a single trailing segment is a language tag; reject multi-dot junk.
    if not lang_token or "." in lang_token:
        return None
    return normalize_lang(lang_token)


def scan_sidecar_langs(paths: Iterable[str]) -> dict[str, list[str]]:
    """For each video path, the languages of sidecars sitting beside it.

    Groups by parent directory and lists each folder ONCE (not per file), so a
    24-episode season folder costs one `scandir`, not 24 — the same discipline
    the trash-sizing fix used. Returns ``{video_path: [langs...]}`` only for
    paths that have at least one language-tagged sidecar. Best-effort: an
    unreadable directory is simply skipped."""
    by_parent: dict[str, list[str]] = {}
    for p in paths:
        if not p:
            continue
        by_parent.setdefault(str(Path(p).parent), []).append(p)

    out: dict[str, list[str]] = {}
    for parent, vids in by_parent.items():
        try:
            names = [e.name for e in os.scandir(parent) if e.is_file()]
        except OSError:
            continue
        for vid in vids:
            stem = Path(vid).stem
            langs: list[str] = []
            for nm in names:
                lang = sidecar_lang(stem, nm)
                if lang and lang not in langs:
                    langs.append(lang)
            if langs:
                out[vid] = langs
    return out
