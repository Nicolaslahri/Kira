"""Subtitle scoring — rank candidates 0-100 with a plain-English reason and a
sync-confidence verdict, so the picker chooses the BEST (not the first) and the
UI can explain why. Pure + deterministic; the whole point is transparency.

The model. The base is deliberately generous: a right-language candidate from a
real provider is a legitimate pick, so CORRECTNESS sets a healthy floor and
release-affinity only REFINES it (matching the exact release is a SYNC hint, not
a correctness signal — a different-release sub is still the right subtitle). The
aggregator layers episode/film confirmation on top and buries CONFIRMED-wrong
content; here we just rank a plausible candidate by how perfectly it'll line up.
  base 45
  + embedded (the file's own track) .............. +55   → 100, sync GUARANTEED
  + hash match (OpenSubtitles moviehash) ......... +50   → ~95, sync GUARANTEED
  + release-group match ([Moozzi2] == [Moozzi2]) . +9          → sync LIKELY
  + source match (BluRay / WEB-DL / …) ........... +5
  + resolution match (1080p / 2160p / …) ......... +4
  + codec match (x265 / x264 / …) ................ +2
  − season pack (needs extraction; prefer a single) −6
  + community downloads (log-scaled) ............. up to +7
  + community rating ............................. up to +5
  + matches your hearing-impaired preference ..... +4 / -3 mismatch
  + matches your forced preference ............... +4 / -3 mismatch
  capped 0–100. The episode-match (+16 / −50) and movie-identity (+12 / −60)
  passes live in aggregate.gather_candidates — they need the wanted ids/episode.

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
    score = 45
    reasons: list[str] = []
    sync = "unknown"

    if cand.from_embedded:
        score += 55   # the file's own track is definitionally perfect → 100
        sync = "guaranteed"
        reasons.append("embedded track (perfect sync)")
    elif cand.hash_match:
        score += 50   # made for THIS exact file → guaranteed sync, ~95
        sync = "guaranteed"
        reasons.append("hash-matched (perfect sync)")

    rel = cand.release_name or ""
    if rel and not cand.from_embedded:
        if video.group and _group(rel) == video.group:
            score += 9
            reasons.append(f"release group [{video.group}]")
            if sync == "unknown":
                sync = "likely"
        _src_m = _SRC_RE.search(rel)
        src_match = bool(video.source and _src_m and _norm_src(_src_m.group(1)) == video.source)
        _res_m = _RES_RE.search(rel)
        res_match = bool(video.resolution and _res_m and _res_m.group(1).lower() == video.resolution)
        if src_match:
            score += 5
            reasons.append(f"source {video.source}")
        if res_match:
            score += 4
            reasons.append(video.resolution)
        if src_match and res_match and sync == "unknown":
            sync = "likely"
        _cod_m = _CODEC_RE.search(rel)
        if video.codec and _cod_m and _norm_codec(_cod_m.group(1)) == video.codec:
            score += 2
            reasons.append(video.codec)

    # A pack is a valid candidate (it CONTAINS the episode — the pack ranker
    # extracts it), just less direct than a single-episode sub, so a mild nudge
    # rather than the old flat "+10 = this episode" (which fired for ANY non-pack,
    # right episode or not — the episode-match pass now does that honestly).
    if cand.is_pack:
        score -= 6
        reasons.append("from season pack")

    if cand.downloads > 0:
        # log-scaled trust tiebreaker: ~1k downloads ≈ +5, 10k ≈ +7 (capped)
        bonus = min(7, int(math.log10(cand.downloads + 1) * 1.75))
        if bonus:
            score += bonus
            reasons.append(f"{cand.downloads:,} downloads")
    if cand.rating is not None and cand.rating > 0:
        bonus = int(round(cand.rating * 5))
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
    elif want_forced == "only" and not cand.forced:
        score -= 6; reasons.append("not forced (forced-only wanted)")
    elif want_forced == "exclude" and cand.forced:
        score -= 3; reasons.append("forced (not wanted)")

    cand.score = max(0, min(100, score))
    cand.reasons = reasons
    cand.sync = sync
    return cand


def _digits(v) -> str | None:
    """The bare numeric core of an id ('tt1375666' / 1375666 / 'tt0133093' →
    '1375666' / '133093') for robust cross-provider comparison. Pure."""
    if v is None:
        return None
    s = "".join(ch for ch in str(v) if ch.isdigit()).lstrip("0")
    return s or None


def identity_match(cand: SubtitleCandidate, *, imdb_id=None, tmdb_id=None,
                   year: int | None = None) -> str:
    """Compare a candidate's provider-reported FILM identity to the wanted movie.
    Returns "mismatch" (a confirmed WRONG film — id or year clearly differs),
    "match" (confirmed same film), or "unknown" (can't tell). Conservative: it
    only judges on a signal BOTH sides carry, strongest-first (imdb > tmdb >
    year), so a candidate with no identity is never penalized. Pure."""
    want_i, cand_i = _digits(imdb_id), _digits(getattr(cand, "imdb_id", None))
    if want_i and cand_i:
        return "match" if want_i == cand_i else "mismatch"
    if tmdb_id and getattr(cand, "tmdb_id", None):
        return "match" if int(tmdb_id) == int(cand.tmdb_id) else "mismatch"
    if year and getattr(cand, "year", None):
        return "match" if abs(int(cand.year) - int(year)) <= 1 else "mismatch"
    return "unknown"


def rank(candidates: list[SubtitleCandidate], video: ReleaseInfo, **prefs) -> list[SubtitleCandidate]:
    """Score every candidate and return them sorted best-first (stable)."""
    for c in candidates:
        score_candidate(c, video, **prefs)
    return sorted(candidates, key=lambda c: c.score, reverse=True)
