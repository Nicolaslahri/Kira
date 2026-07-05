"""Embedded subtitle extraction — pull TEXT subtitle tracks already inside a
container (MKV/MP4) out to language-tagged sidecars. Offline, no API, no key.

This is the highest-yield "source" for anime: fansub MKVs almost always carry
the subs embedded, so extracting them needs no network at all. Detection uses
`pymediainfo` (already an optional Kira dep) to enumerate Text tracks + their
language; extraction shells out to `ffmpeg` (`-map 0:s:<n> -c copy`).

Both are graceful: no pymediainfo OR no ffmpeg on PATH → `available()` is False
and `extract()` returns `[]` (a clean no-op, exactly like the filename-only
path). Only text formats (SubRip / ASS / SSA) are extracted — image subs
(PGS / VobSub) can't become a text sidecar, so they're skipped.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

# Reuse the one canonical sidecar-naming helper so embedded subs land at the
# SAME `<stem>.<lang>.<ext>` path the OpenSubtitles path uses (no divergence,
# and the cross-source exists-check below actually lines up). It lives in the
# neutral subtitles.naming module (not tied to any single provider).
from kira.subtitles.naming import subtitle_sidecar_name

_log = logging.getLogger("kira.subtitles.embedded")

try:  # native lib is optional — mirrors parser/mediainfo.py
    from pymediainfo import MediaInfo as _MediaInfo  # type: ignore
    _MI_AVAILABLE = True
except Exception:
    _MediaInfo = None  # type: ignore
    _MI_AVAILABLE = False


# ── Language normalization ────────────────────────────────────────────────
# MediaInfo reports a track language as a 2-letter code ("en"), a 3-letter
# code ("eng"), or sometimes a full name ("English"). The user's wanted-language
# setting is typically 2-letter. Normalize BOTH sides to a 2-letter code for
# matching; fall back to the lowercased raw value when it's not in the table so
# an exotic language still matches itself.
_LANG_TO_2: dict[str, str] = {}
# Each row: 2-letter code, then every alias (ISO 639-2/B + 639-2/T 3-letter
# codes and the English name) that should fold to it. Both 3-letter variants
# matter — e.g. French is "fre" (bibliographic) OR "fra" (terminological);
# MediaInfo / providers use either. A short list here silently split codes so
# a `nor` embedded track never satisfied a wanted `no` (permanent "missing" +
# duplicate downloads).
for _two, *_aliases in [
    ("en", "eng", "english"), ("ja", "jpn", "jp", "japanese"),
    ("es", "spa", "spanish", "castilian"), ("fr", "fre", "fra", "french"),
    ("de", "ger", "deu", "german"), ("it", "ita", "italian"),
    ("pt", "por", "portuguese"), ("ru", "rus", "russian"),
    ("zh", "chi", "zho", "chinese", "mandarin"), ("ko", "kor", "korean"),
    ("ar", "ara", "arabic"), ("nl", "dut", "nld", "dutch", "flemish"),
    ("pl", "pol", "polish"), ("tr", "tur", "turkish"),
    ("sv", "swe", "swedish"), ("hu", "hun", "hungarian"),
    ("hi", "hin", "hindi"),
    # ── expanded coverage ──
    ("no", "nor", "norwegian"), ("nb", "nob", "bokmal"), ("nn", "nno", "nynorsk"),
    ("fi", "fin", "finnish"), ("da", "dan", "danish"),
    ("cs", "cze", "ces", "czech"), ("sk", "slo", "slk", "slovak"),
    ("el", "gre", "ell", "greek"), ("he", "heb", "hebrew", "iw"),
    ("uk", "ukr", "ukrainian"), ("vi", "vie", "vietnamese"),
    ("th", "tha", "thai"), ("id", "ind", "indonesian", "in"),
    ("ro", "rum", "ron", "romanian"), ("bg", "bul", "bulgarian"),
    ("hr", "hrv", "croatian"), ("sr", "srp", "serbian"),
    ("sl", "slv", "slovenian"), ("et", "est", "estonian"),
    ("lv", "lav", "latvian"), ("lt", "lit", "lithuanian"),
    ("ca", "cat", "catalan"), ("fa", "per", "fas", "persian", "farsi"),
    ("ta", "tam", "tamil"), ("te", "tel", "telugu"),
    ("ml", "mal", "malayalam"), ("bn", "ben", "bengali"),
    ("ms", "may", "msa", "malay"), ("uz", "uzb", "uzbek"),
    ("is", "ice", "isl", "icelandic"), ("ga", "gle", "irish"),
]:
    _LANG_TO_2[_two] = _two
    for _a in _aliases:
        _LANG_TO_2[_a] = _two


def normalize_lang(value: str | None) -> str | None:
    """Map a language string (2/3-letter or name) to a 2-letter code, or the
    lowercased raw value when unknown. None/empty → None."""
    if not value:
        return None
    key = str(value).strip().lower()
    if not key:
        return None
    return _LANG_TO_2.get(key, key)


# ── Codec → sidecar extension ───────────────────────────────────────────────
def codec_to_ext(fmt: str | None) -> str | None:
    """Map a MediaInfo subtitle Format to a TEXT sidecar extension, or None for
    formats that can't be a text sidecar (image subs — PGS/VobSub)."""
    f = (fmt or "").upper().replace("-", "").replace(" ", "")
    if "ASS" in f or "SSA" in f:
        return "ass"
    if "SUBRIP" in f or "UTF8" in f or "SRT" in f:
        return "srt"
    if "WEBVTT" in f or "VTT" in f:
        return "vtt"
    # PGS / HDMVPGS / VOBSUB / DVD / S_HDMV → image-based, no text sidecar.
    return None


def available() -> bool:
    """True only when BOTH pieces exist: pymediainfo (to find tracks) AND an
    ffmpeg binary (system PATH or Kira's own managed copy). Either missing →
    no-op."""
    from kira.ffmpeg_setup import resolve_ffmpeg
    return _MI_AVAILABLE and resolve_ffmpeg() is not None


def list_text_tracks(path: str) -> list[dict[str, Any]]:
    """Enumerate the container's TEXT subtitle tracks. Each entry:
    `{sindex, lang, ext, title, forced}` where `sindex` is the 0-based ordinal
    AMONG subtitle streams (what ffmpeg's `-map 0:s:<n>` expects), `lang` is the
    normalized 2-letter code, and `ext` is the text sidecar extension. Image
    subs are dropped. `[]` when the lib is unavailable / file unreadable."""
    if not _MI_AVAILABLE:
        return []
    try:
        info = _MediaInfo.parse(path)  # type: ignore[union-attr]
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    sub_ordinal = -1  # 0-based index among ALL subtitle/text streams (for ffmpeg -map)
    for track in getattr(info, "tracks", []):
        if getattr(track, "track_type", None) != "Text":
            continue
        sub_ordinal += 1  # counts EVERY text track, incl. image ones, so the
        #                   ffmpeg subtitle-stream index stays correct
        ext = codec_to_ext(getattr(track, "format", None))
        if ext is None:
            continue  # image sub — keep the ordinal advanced, skip extraction
        out.append({
            "sindex": sub_ordinal,
            "lang": normalize_lang(getattr(track, "language", None)),
            "ext": ext,
            "title": getattr(track, "title", None),
            "forced": str(getattr(track, "forced", "") or "").lower() in ("yes", "1", "true"),
        })
    return out


async def extract(video_path: str, languages: list[str], forced: str = "") -> list[str]:
    """Extract embedded text subs for the wanted `languages` → sidecars.

    Track choice honors the user's forced preference (`forced` — same
    vocabulary as the external-search path: '' | include | exclude | only):
      • only    → prefer the forced (signs/songs) track, fall back to the full one
      • exclude → only the full non-forced track, never a forced one
      • '' / include → prefer the full track, fall back to forced
    Skips a language whose sidecar already exists, then `ffmpeg -map 0:s:<n>
    -c copy` extracts it. Returns saved sidecar paths. Best-effort: any failure
    on a single track is logged and skipped; never raises."""
    if not languages or not available():
        return []
    # `list_text_tracks` does a blocking MediaInfo container parse (a full
    # header read, a NAS round-trip on networked storage). Run it in a worker
    # thread so a batch of renamed files can't freeze the event loop — every
    # other request/scan would otherwise stall for the whole batch.
    tracks = await asyncio.to_thread(list_text_tracks, video_path)
    if not tracks:
        return []

    wanted = [w for w in (normalize_lang(x) for x in languages) if w]
    saved: list[str] = []
    _exts = ("srt", "ass", "vtt")
    # Untagged-track rescue: many fansub MKVs carry a SINGLE text track with no
    # language metadata (normalize_lang → None), which the per-language match
    # below would never select — so the file's own embedded sub is ignored and
    # Kira needlessly fetches over the network (defeating the "embedded is best
    # for anime" path). When there's exactly one text track and it's untagged,
    # treat it as the user's TOP wanted language.
    lone_untagged = tracks[0] if (len(tracks) == 1 and tracks[0]["lang"] is None) else None
    for i, want in enumerate(wanted):
        # Already have a sidecar for this language (a prior run OR another
        # source — OpenSubtitles writes `.srt`)? Skip; don't clobber/duplicate.
        if any(
            Path(video_path).with_name(subtitle_sidecar_name(video_path, want, ext=e)).exists()
            for e in _exts
        ):
            continue
        # Pick a track honoring the user's forced preference. Without this the
        # embedded path always preferred non-forced, so a `forced=only` user got
        # the full-dialogue sub instead of the signs/songs track they asked for.
        forced_t = next((t for t in tracks if t["lang"] == want and t["forced"]), None)
        plain_t = next((t for t in tracks if t["lang"] == want and not t["forced"]), None)
        if forced == "only":
            match = forced_t or plain_t      # want forced; soft fallback to full
        elif forced == "exclude":
            match = plain_t                  # never emit a forced (signs-only) track
        else:                                 # '' | include → main dialogue preferred
            match = plain_t or forced_t
        # No language-tagged track matched — fall back to the lone untagged track
        # for the user's TOP language only (default/include preference; a forced
        # requirement can't be assumed of an untagged track). Skip it if the track
        # is itself flagged forced: that's a signs/songs-only sub (dialogue is
        # hardsubbed), and writing it as the main dialogue sidecar would both give
        # the user the wrong sub and suppress the OpenSubtitles fetch that would
        # have grabbed a real one — mirror the `exclude` guard at L174.
        if (
            match is None
            and i == 0
            and lone_untagged is not None
            and not lone_untagged["forced"]
            and forced in ("", "include")
        ):
            match = lone_untagged
        if match is None:
            continue
        # Keep the track's native extension (ASS styling would be lost to SRT).
        dest = str(Path(video_path).with_name(
            subtitle_sidecar_name(video_path, want, ext=match["ext"])
        ))
        if await _ffmpeg_extract(video_path, match["sindex"], dest):
            saved.append(dest)
    return saved


# ffmpeg output-muxer name per sidecar extension. Passed explicitly with `-f`
# because we write to a `.part` temp file, and ffmpeg would otherwise pick the
# muxer from the output filename's extension — `.part` matches NO muxer, so
# every extraction failed with "Unable to find a suitable output format".
_EXT_TO_FFMPEG_FORMAT = {"srt": "srt", "ass": "ass", "ssa": "ass", "vtt": "webvtt"}


async def _ffmpeg_extract(video_path: str, sub_index: int, dest: str) -> bool:
    """Run `ffmpeg -map 0:s:<sub_index> -c copy -f <fmt> <dest>` off the event
    loop. Writes to a `.part` temp then renames (atomic — a crash never leaves
    a half-written sidecar). Returns True on success."""
    from kira.ffmpeg_setup import resolve_ffmpeg
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        return False
    import os as _os
    ext = _os.path.splitext(dest)[1].lstrip(".").lower()
    fmt = _EXT_TO_FFMPEG_FORMAT.get(ext)
    if not fmt:
        _log.warning("embedded extract: no ffmpeg format for extension %r (%s)", ext, dest)
        return False
    tmp = dest + ".part"
    cmd = [
        ffmpeg, "-y", "-v", "error",
        "-i", video_path,
        "-map", f"0:s:{sub_index}",
        "-c", "copy",
        "-f", fmt,          # explicit — the .part temp gives no usable extension
        tmp,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        import os as _os
        if proc.returncode == 0 and _os.path.exists(tmp) and _os.path.getsize(tmp) > 0:
            _os.replace(tmp, dest)
            return True
        if _os.path.exists(tmp):
            _os.unlink(tmp)
        if proc.returncode != 0:
            _log.warning("ffmpeg map 0:s:%s failed: %s", sub_index,
                         (stderr or b'').decode('utf-8', 'replace')[:200])
        return False
    except Exception as e:
        _log.warning("ffmpeg extract failed for %s: %r", video_path, e)
        try:
            import os as _os
            if _os.path.exists(tmp):
                _os.unlink(tmp)
        except OSError:
            pass
        return False
