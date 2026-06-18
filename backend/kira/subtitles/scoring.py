"""Subtitle scoring — rank candidates 0-100 with a plain-English reason and a
sync-confidence verdict, so the picker chooses the BEST (not the first) and the
UI can explain why. Pure + deterministic; the whole point is transparency.

The model, roughly (Bazarr-informed):
  base 30
  + hash match (OpenSubtitles moviehash) ......... +40   → sync GUARANTEED
  + embedded (the file's own track) .............. +40   → sync GUARANTEED
  + exact episode (not guessed from a pack) ...... +10
  + release-group match ([Moozzi2] == [Moozzi2]) . +12   → sync LIKELY
  + source match (BluRay / WEB-DL / …) ........... +8
  + resolution match (1080p / 2160p / …) ......... +6
  + codec match (x265 / x264 / …) ................ +2
  + community downloads (log-scaled) ............. up to +8
  + community rating ............................. up to +6
  + matches your hearing-impaired preference ..... +4 / -3 mismatch
  + matches your forced preference ............... +4 / -3 mismatch
  capped at 100.

`sync`:
  guaranteed — hash match or embedded track
  likely     — release-group match, or source+resolution both match
  unknown    — title/id match only (timing not assured)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from kira.subtitles.model import SubtitleCandidate

# ── Release tokenizers ───────────────────────────────────────────────
_RES_RE = re.compile(r"\b(2160p|1080p|720p|480p|576p)\b", re.I)
_SRC_RE = re.compile(
    r"\b(blu-?ray|bdrip|bdremux|remux|web-?dl|web-?rip|webrip|web|hdtv|dvdrip|hdrip)\b", re.I)
_CODEC_RE = re.compile(r"\b(x265|h\.?265|hevc|x264|h\.?264|avc|av1|xvid|divx)\b", re.I)
# Release group: a [Bracket] tag, or the trailing -GROUP after the last dash.
_GROUP_BRACKET_RE = re.compile(r"[\[(]([A-Za-z0-9_.\- ]{2,30})[\])]")
_GROUP_DASH_RE = re.compile(r"-([A-Za-z0-9]{2,20})\s*$")


def _norm_src(s: str | None) -> str | None:
    if not s:
        return None
    t = re.sub(r"[\s\-]", "", s.lower())
    if t in ("bluray", "bdremux", "remux", "bdrip"):
        return "bluray"
    if t in ("webdl",):
        return "webdl"
    if t in ("webrip", "web"):
        return "webrip"
    if t == "hdtv":
        return "hdtv"
    if t in ("dvdrip", "hdrip"):
        return t
    return t


def _norm_codec(s: str | None) -> str | None:
    if not s:
        return None
    t = re.sub(r"[\s.\-]", "", s.lower())
    if t in ("x265", "h265", "hevc"):
        return "hevc"
    if t in ("x264", "h264", "avc"):
        return "avc"
    if t in ("av1",):
        return "av1"
    if t in ("xvid", "divx"):
        return "xvid"
    return t


def _group(s: str) -> str | None:
    m = _GROUP_BRACKET_RE.search(s)
    if m:
        return m.group(1).strip().lower().replace(" ", "")
    m = _GROUP_DASH_RE.search(s.strip())
    if m:
        return m.group(1).strip().lower()
    return None


@dataclass
class ReleaseInfo:
    """What we know about the VIDEO, to compare candidates against. Built from
    the parsed_data fields plus the raw filename as a fallback token source."""
    group: str | None = None
    source: str | None = None
    resolution: str | None = None
    codec: str | None = None

    @classmethod
    def from_video(cls, filename: str, parsed: dict | None) -> "ReleaseInfo":
        parsed = parsed or {}
        # Strip a trailing video extension so the group tokenizer's `$` anchor
        # sees "…x264-NTb", not "…x264-NTb.mkv".
        name = re.sub(r"\.(mkv|mp4|avi|m4v|mov|wmv|ts|webm)$", "", filename or "", flags=re.I)
        res = (parsed.get("quality") or "")
        res_m = _RES_RE.search(res) or _RES_RE.search(name)
        src = parsed.get("source")
        if not src:
            sm = _SRC_RE.search(name)
            src = sm.group(1) if sm else None
        cod = parsed.get("codec")
        if not cod:
            cm = _CODEC_RE.search(name)
            cod = cm.group(1) if cm else None
        grp = parsed.get("release_group") or _group(name)
        return cls(
            group=(grp.lower().replace(" ", "") if grp else None),
            source=_norm_src(src),
            resolution=(res_m.group(1).lower() if res_m else None),
            codec=_norm_codec(cod),
        )


def score_candidate(
    cand: SubtitleCandidate,
    video: ReleaseInfo,
    *,
    want_hi: str = "",     # "" | "include" | "exclude" | "only"
    want_forced: str = "",
) -> SubtitleCandidate:
    """Score `cand` against `video` + prefs. Mutates and returns it (sets
    score/reasons/sync)."""
    score = 30
    reasons: list[str] = []
    sync = "unknown"

    if cand.from_embedded:
        score += 40
        sync = "guaranteed"
        reasons.append("embedded track (perfect sync)")
    elif cand.hash_match:
        score += 40
        sync = "guaranteed"
        reasons.append("hash-matched (perfect sync)")

    rel = cand.release_name or ""
    if rel and not cand.from_embedded:
        if video.group and _group(rel) == video.group:
            score += 12
            reasons.append(f"release group [{video.group}]")
            if sync == "unknown":
                sync = "likely"
        _src_m = _SRC_RE.search(rel)
        src_match = bool(video.source and _src_m and _norm_src(_src_m.group(1)) == video.source)
        _res_m = _RES_RE.search(rel)
        res_match = bool(video.resolution and _res_m and _res_m.group(1).lower() == video.resolution)
        if src_match:
            score += 8
            reasons.append(f"source {video.source}")
        if res_match:
            score += 6
            reasons.append(video.resolution)
        if src_match and res_match and sync == "unknown":
            sync = "likely"
        _cod_m = _CODEC_RE.search(rel)
        if video.codec and _cod_m and _norm_codec(_cod_m.group(1)) == video.codec:
            score += 2
            reasons.append(video.codec)

    if cand.is_pack:
        reasons.append("from season pack")
    elif not cand.from_embedded:
        score += 10  # an entry that's explicitly this episode

    if cand.downloads > 0:
        # log-scaled: ~1k downloads ≈ +6, 10k ≈ +8 (capped)
        bonus = min(8, int(math.log10(cand.downloads + 1) * 2))
        if bonus:
            score += bonus
            reasons.append(f"{cand.downloads:,} downloads")
    if cand.rating is not None and cand.rating > 0:
        bonus = int(round(cand.rating * 6))
        if bonus:
            score += bonus
            reasons.append(f"rated {int(round(cand.rating * 100))}%")

    # Hearing-impaired preference.
    if want_hi == "only" and cand.hearing_impaired:
        score += 4; reasons.append("SDH")
    elif want_hi == "exclude" and cand.hearing_impaired:
        score -= 3; reasons.append("SDH (not wanted)")
    elif want_hi == "only" and not cand.hearing_impaired:
        score -= 6; reasons.append("not SDH")
    # Forced preference.
    if want_forced == "only" and cand.forced:
        score += 4; reasons.append("forced")
    elif want_forced == "exclude" and cand.forced:
        score -= 3; reasons.append("forced (not wanted)")

    cand.score = max(0, min(100, score))
    cand.reasons = reasons
    cand.sync = sync
    return cand


def rank(candidates: list[SubtitleCandidate], video: ReleaseInfo, **prefs) -> list[SubtitleCandidate]:
    """Score every candidate and return them sorted best-first (stable)."""
    for c in candidates:
        score_candidate(c, video, **prefs)
    return sorted(candidates, key=lambda c: c.score, reverse=True)
