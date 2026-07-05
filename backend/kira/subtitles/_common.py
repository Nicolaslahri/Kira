"""Shared helpers for subtitle SOURCES — language filtering, safe sidecar
writes, and zip extraction, all with the same security posture the YIFY
scraper proved out: atomic temp+replace, never follow a symlink planted at the
sidecar path, never clobber an existing sub, hard size caps (zip-bomb /
unbounded-download guard).

Every source module composes these so the dangerous filesystem + decompression
logic lives in ONE audited place, not copy-pasted per provider.
"""

from __future__ import annotations

import io
import logging
import os
import re
import zipfile
from collections.abc import Iterable
from pathlib import Path

from kira.longpath import long_path
from kira.subtitles.naming import subtitle_sidecar_name
from kira.subtitles.embedded import normalize_lang

_log = logging.getLogger("kira.subtitles.common")

# Cap the compressed download and the single decompressed entry so a hostile/
# oversized payload can't exhaust memory or fill the disk. The ZIP cap is the
# generous one: a SINGLE sub is tiny, but providers routinely ship a WHOLE
# SEASON in one archive (SubSource's "Show 1-47 complete" — 47 .srt/.ass, and
# anime fansub packs sometimes bundle styling fonts), so 4 MiB silently failed
# every pack download. The real safety bound is the per-entry cap below — only
# ONE subtitle is ever read out and written to disk.
MAX_ZIP_BYTES = 64 * 1024 * 1024    # 64 MiB compressed archive (season packs)
MAX_SUB_BYTES = 8 * 1024 * 1024     # 8 MiB decompressed / direct file (one sub)

_SIDECAR_EXTS = ("srt", "ass", "ssa", "vtt", "sub")


def languages_needing_fetch(
    video_path: str, languages: list[str],
    embedded: Iterable[str] | None = None,
    forced: bool = False,
) -> list[str]:
    """Languages still worth fetching — i.e. NOT already satisfied. Drops a
    language that already has ANY sidecar on disk (from a prior run or another
    source) so sources compose without duplicating a download.

    When `embedded` is a non-empty iterable of language codes (the container's
    own text tracks, from MediaInfo's `sub_langs`), also drop any wanted
    language whose normalized form is already embedded — fetching an EXTERNAL
    sub for a language the file already carries inside it is pure noise, even
    when embedded extraction isn't enabled. `embedded=None`/empty behaves
    EXACTLY as before (MediaInfo may not have run — never assume it did).

    Normalization is the shared subtitle helper, so embedded `eng`/`English`
    correctly satisfies a wanted `en`."""
    embedded_norm = {
        n for n in (normalize_lang(e) for e in (embedded or ())) if n
    }
    out: list[str] = []
    for lang in languages:
        if embedded_norm and normalize_lang(lang) in embedded_norm:
            continue
        # Alias-aware disk probe: an existing `.eng.srt` (or `.english.srt`)
        # satisfies a wanted `en` — the literal-code-only check used to fetch
        # a duplicate sub beside every alias-spelled sidecar.
        if not any(
            Path(video_path).with_name(subtitle_sidecar_name(video_path, spelling, ext=e, forced=forced)).exists()
            for spelling in _lang_spellings(lang)
            for e in _SIDECAR_EXTS
        ):
            out.append(lang)
    return out


def _lang_spellings(lang: str) -> list[str]:
    """The wanted code plus every alias that normalizes to the same language
    (en → en/eng/english), for disk-probe purposes. The wanted code always
    comes first so naming for NEW sidecars is unchanged."""
    from kira.subtitles.embedded import _LANG_TO_2
    want = normalize_lang(lang) or lang
    out = [lang]
    for alias, two in _LANG_TO_2.items():
        if two == want and alias not in out:
            out.append(alias)
    return out


def has_sidecar(video_path: str, lang: str, *, forced: bool = False) -> bool:
    return find_sidecar(video_path, lang, forced=forced) is not None


def find_sidecar(video_path: str, lang: str, *, forced: bool = False) -> str | None:
    """Path of an existing `<stem>.<lang>.<ext>` sidecar beside the video, or
    None. Used to short-circuit a re-fetch of a language already on disk."""
    for e in _SIDECAR_EXTS:
        p = Path(video_path).with_name(subtitle_sidecar_name(video_path, lang, ext=e, forced=forced))
        if os.path.exists(long_path(p)):
            return str(p)
    return None


def save_sidecar(video_path: str, lang: str, data: bytes, ext: str = "srt",
                 *, overwrite: bool = False, forced: bool = False) -> str | None:
    """Write `<stem>.<lang>.<ext>` beside the video, atomically. Returns the
    path on success, None if the write fails. By default refuses to clobber an
    existing sidecar; `overwrite=True` (used by upgrade-over-time) replaces it.
    A symlink at the path is NEVER followed. The data is assumed already
    size-checked + validated by the caller (not an HTML error page)."""
    if not data:
        return None
    dest = Path(video_path).with_name(subtitle_sidecar_name(video_path, lang, ext=ext, forced=forced))
    if dest.is_symlink():
        return None
    if os.path.exists(long_path(dest)) and not overwrite:
        return None
    tmp = dest.with_name(dest.name + ".part")
    try:
        if tmp.is_symlink():
            tmp.unlink()
        Path(long_path(tmp)).write_bytes(data)
        os.replace(long_path(tmp), long_path(dest))
        return str(dest)
    except Exception as e:
        _log.warning("save %s sidecar failed: %r", lang, e)
        try:
            if os.path.exists(long_path(tmp)):
                os.unlink(long_path(tmp))
        except OSError:
            pass
        return None


def _episode_score(name: str, season: int | None, episode: int | None) -> int:
    """How well a zip-entry filename matches a wanted season/episode. Higher =
    better. 0 = no episode signal. Used to pick the RIGHT file out of a
    whole-season pack (SubSource/others ship season ZIPs with E01.srt…E12.srt —
    grabbing the first would save the wrong episode)."""
    if episode is None:
        return 0
    low = name.lower()
    score = 0
    if season is not None and re.search(rf"s0*{season}\s*e0*{episode}\b", low):
        score = 3                                      # SxxEyy — strongest
    elif re.search(rf"\be0*{episode}\b", low) or re.search(rf"\bep\.?\s*0*{episode}\b", low):
        score = 2                                      # Eyy / ep yy
    elif re.search(rf"(?<!\d)0*{episode}(?!\d)", low):
        score = 1                                      # a bare matching number
    return score


def subtitle_from_zip(content: bytes, *, season: int | None = None,
                      episode: int | None = None) -> tuple[bytes, str] | None:
    """Best subtitle entry in a ZIP → (bytes, ext). Prefers .srt, then .ass /
    .ssa / .vtt / .sub. When `episode` is given AND the archive holds several
    subtitle files (a season pack), picks the entry whose filename matches the
    episode rather than blindly taking the first. Size-capped against zip-bombs
    (trusts the central-dir size, then re-checks the actual read). None on any
    failure / no entry."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            subs = [n for n in zf.namelist()
                    if not n.endswith("/")
                    and n.lower().rsplit(".", 1)[-1] in ("srt", "ass", "ssa", "vtt", "sub")]
            if not subs:
                return None
            # Episode-aware pick for multi-file packs; otherwise prefer .srt,
            # then the codec order, then first.
            _ext_rank = {"srt": 0, "ass": 1, "ssa": 2, "vtt": 3, "sub": 4}
            chosen = max(
                subs,
                key=lambda n: (
                    _episode_score(n, season, episode),
                    -_ext_rank.get(n.lower().rsplit(".", 1)[-1], 9),
                ),
            )
            # Wrong-episode guard: a MULTI-file pack where we asked for a
            # specific episode but NOTHING inside matches it (best score 0) is
            # ambiguous — saving the alphabetically-first entry would silently
            # pass off (say) episode 1 as episode 6. Refuse rather than guess;
            # the caller surfaces "couldn't find that episode in the pack". A
            # single-entry archive is unambiguous, so it's always taken.
            if (len(subs) > 1 and episode is not None
                    and _episode_score(chosen, season, episode) == 0):
                _log.info("pack has no entry matching S%sE%s (%d files) — not guessing",
                          season, episode, len(subs))
                return None
            if zf.getinfo(chosen).file_size > MAX_SUB_BYTES:
                _log.warning("zip entry %s exceeds %d bytes, skipping", chosen, MAX_SUB_BYTES)
                return None
            data = zf.read(chosen)
            if len(data) > MAX_SUB_BYTES:
                return None
            ext = chosen.rsplit(".", 1)[-1].lower()
            return data, ext
    except Exception as e:
        _log.warning("unzip failed: %r", e)
        return None
