"""Season-pack intelligence — turn a "whole-season archive" into the RIGHT
single episode by leaning on everything Kira already knows about the file.

A provider often only has a complete-season ZIP ("Show 1-47 complete"). To pull
one episode out we score every subtitle entry inside the archive against the
matched metadata — NOT just a filename regex:

  • SxxEyy            — the strongest signal (matches our season+episode)
  • Eyy / "- 06 -"    — an episode token without a season
  • absolute number   — anime packs number by absolute (One Piece "- 1075")
  • episode title      — the matched provider title appearing in the entry name
  • runtime            — the entry's LAST subtitle cue ≈ the file's real duration
                         (read from MediaInfo); a 24-min sub doesn't belong to a
                         48-min episode
  • release group      — same fansub group as the video

When one entry wins clearly we extract it automatically (one click). When the
signals are weak or tied we DON'T guess — the caller hands the ranked list to
the user to pick from, so a bad situation (no per-episode sub exists) still ends
in the right file instead of a silent wrong one.

The ranker is pure + unit-tested; the small byte cache lets the manual flow
"inspect then extract a chosen entry" without downloading the archive twice.
"""

from __future__ import annotations

import io
import logging
import os
import re
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from kira.subtitles._common import MAX_SUB_BYTES

_log = logging.getLogger("kira.subtitles.pack")

_SUB_EXTS = ("srt", "ass", "ssa", "vtt", "sub")
_EXT_RANK = {"srt": 0, "ass": 1, "ssa": 2, "vtt": 3, "sub": 4}
_MAX_ENTRIES = 2000   # entry-count guard (zip-bomb of tiny files)

# Optional archive backends. 7z is pure-Python (always works once installed);
# RAR needs an external extractor (proprietary algorithm — no pure-Python
# decompressor exists), which rarfile delegates to. Both are soft deps: if a
# lib is missing we degrade to "can't open this format" rather than crash.
try:
    import py7zr  # 7z, pure-python
except Exception:   # pragma: no cover
    py7zr = None
try:
    import rarfile  # RAR, via a backend tool
except Exception:   # pragma: no cover
    rarfile = None


def _configure_rar_backend() -> None:
    """Point rarfile at a RAR-capable extractor without a manual install. On
    Windows the built-in System32\\tar.exe IS bsdtar (libarchive ≥3.7 reads
    RAR/RAR5), so it works out of the box; we also honor a tool dropped in
    Kira's ./tools dir (the same managed-binary spot ffmpeg uses) and anything
    already on PATH. rarfile probes these lazily and uses the first that works."""
    if rarfile is None:
        return
    import shutil
    # Kira's managed tools dir first (explicit, offline), then a Windows bsdtar.
    tools = Path.cwd() / "tools"
    for attr, names in (
        ("UNRAR_TOOL", ("unrar", "unrar.exe")),
        ("UNAR_TOOL", ("unar", "unar.exe")),
        ("SEVENZIP_TOOL", ("7z", "7z.exe", "7zz", "7zz.exe")),
    ):
        for n in names:
            cand = tools / n
            if cand.is_file():
                setattr(rarfile, attr, str(cand))
                break
    # Windows bsdtar (libarchive) — present on Win10 1803+ as System32\tar.exe.
    if os.name == "nt":
        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        bsdtar = Path(sysroot) / "System32" / "tar.exe"
        if bsdtar.is_file():
            rarfile.BSDTAR_TOOL = str(bsdtar)
    elif shutil.which("bsdtar"):
        rarfile.BSDTAR_TOOL = "bsdtar"


_configure_rar_backend()


def rar_backend_available() -> bool:
    """True when rarfile can find a working RAR extractor on this machine."""
    if rarfile is None:
        return False
    try:
        rarfile.tool_setup()   # raises RarCannotExec if no backend tool found
        return True
    except Exception:
        return False

# Confidence gates for the auto-extract decision. A clear, strong winner is
# taken without bothering the user; anything weak or close → ask. The floor is
# set so that ONE strong identifier (explicit E06 = 40, absolute = 40, episode
# title = 35) — or a corroborating combination (bare number 16 + runtime 22 =
# 38) — auto-picks, while a lone weak signal (bare number, or runtime alone)
# does NOT: those only RANK the list the user is asked to confirm.
_CONFIDENT_FLOOR = 35      # the winner must score at least this
_CONFIDENT_MARGIN = 15     # …and beat the runner-up by at least this

# Runtime tolerance: a sub whose last cue lands within this many seconds of the
# file's real duration is very likely the same episode.
_RUNTIME_TIGHT = 60
_RUNTIME_LOOSE = 150


@dataclass
class PackEntry:
    """One subtitle file inside a pack, scored as a candidate for the episode."""
    name: str
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    guessed_episode: int | None = None
    other_episode: int | None = None   # an explicit DIFFERENT episode in the name
    matched: bool = False              # EXPLICITLY matched the wanted episode (SxE / token / absolute)

    def public(self) -> dict:
        return {
            "name": self.name,
            "score": self.score,
            "reasons": self.reasons,
            "guessed_episode": self.guessed_episode,
        }


@dataclass
class PackChoice:
    """Outcome of inspecting a pack: the ranked entries, the best pick, and
    whether we're confident enough to extract it without asking."""
    entries: list[PackEntry]
    best: PackEntry | None
    confident: bool
    is_pack: bool          # >1 subtitle entry inside


# ── timestamp / runtime ──────────────────────────────────────────────────────
_TS_RE = re.compile(rb"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def srt_last_cue_seconds(data: bytes) -> int | None:
    """The largest timestamp in an SRT/VTT body → whole seconds. Approximates
    the episode runtime (the last line of dialogue lands near the end). None for
    formats we can't read cheaply (e.g. ASS uses a different time grammar)."""
    best = -1
    for m in _TS_RE.finditer(data[:2_000_000]):   # cap the scan
        h, mn, s, _ = m.groups()
        secs = int(h) * 3600 + int(mn) * 60 + int(s)
        if secs > best:
            best = secs
    return best if best >= 0 else None


# ── per-entry episode parsing ─────────────────────────────────────────────────
def _basename(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def _norm(text: str) -> str:
    """Lowercase, non-alphanumerics → single spaces — for loose title contains."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


# Release-noise tokens whose DIGITS would otherwise masquerade as an episode or
# absolute number — the classic trap is a CRC32 hash like [D383C47E] (the "47"
# in "C47E" reading as absolute episode 47). Strip these BEFORE any number
# matching, exactly like the main release scorer does.
_NOISE_RE = re.compile(
    r"\[[0-9a-f]{8}\]|\([0-9a-f]{8}\)"      # CRC32 hash in [..] or (..)
    r"|\d{3,4}\s*x\s*\d{3,4}"               # resolution 1920x1080
    r"|\b\d{3,4}[pi]\b"                     # 720p / 1080p / 1080i
    r"|x\s*26[45]|h\.?\s*26[45]|hevc|avc"  # video codecs (x264/x265/h264…)
    r"|10\s*bit|8\s*bit"                    # bit depth
    r"|aac|ac3|flac|dts|opus|mp3|ddp?5\.?1",  # audio codecs
    re.IGNORECASE,
)


def _denoise(low: str) -> str:
    """Blank out release-noise tokens so their digits can't be read as an
    episode/absolute number. Operates on an already-lowercased string."""
    return _NOISE_RE.sub(" ", low)


def _explicit_sxe(low: str) -> tuple[int, int] | None:
    m = re.search(r"s(\d{1,2})[\s._-]*e(\d{1,3})", low)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _explicit_episode_token(low: str) -> int | None:
    """An episode number stated with a marker (E06 / ep 6 / " - 06 - " / #06 /
    the "[Group] Title - 06 [tags]" fansub form), season-less. Returns the number
    or None. Distinct from a bare number so a title coincidence ('Show 6 Feet
    Under') doesn't read as episode 6."""
    m = (re.search(r"\be(\d{1,3})\b", low)
         or re.search(r"\bep\.?\s*(\d{1,3})\b", low)
         or re.search(r"\bepisode\s*(\d{1,3})\b", low)
         or re.search(r"[\s._-]-[\s._]*(\d{1,3})[\s._]*-", low)   # " - 06 - "
         # Anime fansub form "[Erai-raws] Title - 06 [1080p]" / "Title - 06" /
         # "Title - 06v2": the number after " - " before a bracket / version /
         # end-of-name. The "- 06 -" form above required a TRAILING dash and so
         # missed the (far more common) bracketed-tag releases entirely.
         or re.search(r"[\s._]-[\s._]+(\d{1,3})(?=[\s._]*[\[(]|[\s._]*v\d|[\s._]*\.[a-z]{2,4}|[\s._]*$)", low)
         or re.search(r"#(\d{1,3})\b", low))
    return int(m.group(1)) if m else None


def episode_match(name: str, *, season: int | None, episode: int | None,
                  absolute: int | None) -> str:
    """Classify ONE subtitle's RELEASE NAME against the wanted episode, matching
    BOTH a cour-local SxxEyy and an anime ABSOLUTE number (we may know either or
    both). Returns:
      "match"    — the name advertises the requested episode,
      "mismatch" — it explicitly advertises a DIFFERENT episode (reliably-
                   numbered TV only),
      "unknown"  — no usable single-episode signal, a season/batch pack, or an
                   absolute-numbered show whose S/E token we can't trust.

    Conservative by design: it only buries a candidate ("mismatch") when an
    explicit S/E or episode token clearly names a different episode AND no
    absolute number is in play (cour numbering on absolute shows is unreliable —
    AniDB's single-AID quirk — so there we only ever boost a positive match,
    never bury). Reuses the pack ranker's denoise + parsers. Pure."""
    if not name or (episode is None and absolute is None):
        return "unknown"
    raw_low = _denoise(_basename(name).lower())
    # A range / batch / whole-season archive names no single episode — leave it
    # to the pack ranker; never judge it as a wrong single episode.
    if (re.search(r"\b\d{1,4}\s*[-~]\s*\d{1,4}\b", raw_low)
            or re.search(r"\b(batch|complete|season)\b", raw_low)
            or re.search(r"\bs\d{1,2}\s*-\s*s\d{1,2}\b", raw_low)):
        return "unknown"

    sxe = _explicit_sxe(raw_low)
    tok = _explicit_episode_token(raw_low)

    # Positive matches — any one wins (absolute is the strongest anime signal;
    # zero-padding tolerated so 09 / 007 still hit absolute 9 / 7).
    if absolute is not None and re.search(rf"(?<!\d)0*{absolute}(?!\d)", raw_low):
        return "match"
    if episode is not None and sxe is not None \
            and sxe[1] == episode and (season is None or sxe[0] == season):
        return "match"
    if episode is not None and tok is not None and tok == episode:
        return "match"

    # Mismatch — only for reliably-numbered TV (no absolute in play) where the
    # name explicitly advertises a different episode/season.
    if absolute is None and episode is not None:
        if sxe is not None and (sxe[1] != episode or (season is not None and sxe[0] != season)):
            return "mismatch"
        if sxe is None and tok is not None and tok != episode:
            return "mismatch"
    return "unknown"


def is_likely_pack(name: str, file_count: int = 1) -> bool:
    """Whether a provider result is a SEASON PACK (multiple episodes) rather than
    a single episode — judged from the release NAME first (reliable), and from
    the provider's file-count only for an ambiguous name. A single fansub release
    often bundles srt+ass+fonts, so `file_count > 1` does NOT mean a pack — the
    old `files > 1` rule mislabeled every such release ("[Erai-raws] Show - 04")
    as a season pack. Pure."""
    low = _denoise(_basename(name).lower())
    if _explicit_sxe(low) is not None or _explicit_episode_token(low) is not None:
        return False                                       # names ONE episode → not a pack
    if re.search(r"\b\d{1,4}\s*[-~]\s*\d{1,4}\b", low):     # a range: 01-12 / 01~24
        return True
    if re.search(r"\b(batch|complete|season)\b", low):
        return True
    return file_count > 1                                   # ambiguous name → trust the count


def score_entry(
    name: str, *, season: int | None, episode: int | None,
    absolute: int | None, episode_title: str | None,
    release_group: str | None, entry_seconds: int | None, target_seconds: int | None,
) -> PackEntry:
    """Score ONE entry name (+ optional runtime) against the wanted episode.
    Pure. Higher = more likely the episode the user actually wants."""
    e = PackEntry(name=name)
    # Strip release noise (CRC hashes, resolution, codec) FIRST so its digits
    # can't be read as an episode/absolute number.
    raw_low = _denoise(_basename(name).lower())
    low = _norm(raw_low)
    score = 0

    sxe = _explicit_sxe(raw_low)
    tok = _explicit_episode_token(raw_low)
    # Record what episode THIS entry advertises (for display + wrong-ep guard).
    if sxe is not None:
        e.guessed_episode = sxe[1]
    elif tok is not None:
        e.guessed_episode = tok

    matched_explicit = False
    if episode is not None:
        if sxe is not None and (season is None or sxe[0] == season) and sxe[1] == episode:
            score += 55
            e.reasons.append(f"matches S{(season or sxe[0]):02d}E{episode:02d}")
            matched_explicit = True
        elif tok is not None and tok == episode:
            score += 40
            e.reasons.append(f"episode {episode} in name")
            matched_explicit = True

    # Absolute numbering (anime). Only count when it's not already an explicit
    # S/E hit, and the number appears as a standalone token.
    if absolute is not None and not matched_explicit:
        # `0*` so a zero-padded fansub number (`09`, `007`) still matches
        # absolute 9 / 7 — Erai-raws/SubsPlease/Moozzi2 pad to 2-3 digits, so
        # without this the primary +40 anime identifier silently never fires.
        # Mirrors the bare-number matcher below.
        if re.search(rf"(?<!\d)0*{absolute}(?!\d)", raw_low):
            score += 40
            e.reasons.append(f"absolute #{absolute}")
            matched_explicit = True

    # Episode TITLE from the provider match (e.g. "The Rains of Castamere").
    if episode_title:
        nt = _norm(episode_title)
        if len(nt) >= 4 and nt in low:
            score += 35
            e.reasons.append(f'title "{episode_title.strip()}"')

    # Bare matching number — weak, only when nothing explicit fired (so a year
    # or resolution digit doesn't masquerade as the episode).
    if episode is not None and not matched_explicit:
        if re.search(rf"(?<!\d)0*{episode}(?!\d)", raw_low):
            score += 16
            e.reasons.append(f"number {episode} in name")

    # Runtime: the entry's last cue vs the file's real duration.
    if entry_seconds is not None and target_seconds:
        delta = abs(entry_seconds - target_seconds)
        if delta <= _RUNTIME_TIGHT:
            score += 22
            e.reasons.append(f"runtime ~{round(entry_seconds / 60)}m matches")
        elif delta <= _RUNTIME_LOOSE:
            score += 10
            e.reasons.append("runtime close")

    # Same fansub / release group as the video.
    if release_group:
        g = _norm(release_group)
        if len(g) >= 2 and g in low:
            score += 8
            e.reasons.append(f"group {release_group}")

    # Wrong-episode guard: an entry that EXPLICITLY names a different episode —
    # OR a different SEASON with the same episode number, the classic
    # complete-series / multi-season-pack trap (a pack's S02E02 matching the
    # bare "2" we wanted for S01E02) — is almost certainly not ours, even if a
    # title/group/runtime token coincidentally hit.
    wanted = episode
    advertised = (sxe[1] if sxe is not None else tok)
    wrong_episode = wanted is not None and advertised is not None and advertised != wanted
    # Only meaningful when the entry carries a season (SxxEyy) AND we know ours.
    wrong_season = sxe is not None and season is not None and sxe[0] != season
    if (wrong_episode or wrong_season) and not matched_explicit:
        e.other_episode = advertised
        score = 0
        if wrong_season and not wrong_episode and wanted is not None:
            e.reasons = [f"is S{sxe[0]:02d}E{advertised:02d}, not S{season:02d}E{wanted:02d}"]
        elif wrong_season and not wrong_episode:
            e.reasons = [f"is season {sxe[0]}, not season {season}"]
        else:
            e.reasons = [f"is episode {advertised}, not {wanted}"]

    e.score = max(0, min(100, score))
    e.matched = matched_explicit
    return e


# ── unified archive reading (zip / 7z / rar) ─────────────────────────────────
def archive_kind(raw: bytes) -> str | None:
    """Identify the archive container by magic bytes. zip/7z/rar are openable;
    gzip (lone .gz) we don't handle. None = not an archive (a plain subtitle).
    Lets us (a) pick the right reader and (b) tell the user what was served."""
    if not raw:
        return None
    if raw[:2] == b"PK":
        return "zip"
    if raw[:4] == b"Rar!":
        return "rar"
    if raw[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    if raw[:2] == b"\x1f\x8b":
        return "gzip"
    return None


def _keep(name: str) -> bool:
    return not name.endswith("/") and name.lower().rsplit(".", 1)[-1] in _SUB_EXTS


def _read_zip(raw: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for n in zf.namelist():
            if not _keep(n) or zf.getinfo(n).file_size > MAX_SUB_BYTES:
                continue
            out[n] = zf.read(n)
            if len(out) >= _MAX_ENTRIES:
                break
    return out


def _read_7z(raw: bytes) -> dict[str, bytes] | None:
    if py7zr is None:
        return None
    out: dict[str, bytes] = {}
    # py7zr 1.x extracts to a path (no in-memory read), so decompress JUST the
    # subtitle members into a throwaway temp dir and slurp them back.
    with tempfile.TemporaryDirectory(prefix="kira-7z-") as td:
        with py7zr.SevenZipFile(io.BytesIO(raw)) as z:
            names = [n for n in z.getnames() if _keep(n)][:_MAX_ENTRIES]
            if not names:
                return {}
            z.extract(path=td, targets=names)
        base = Path(td)
        for n in names:
            fp = base / n
            try:
                if fp.is_file() and 0 < fp.stat().st_size <= MAX_SUB_BYTES:
                    out[n] = fp.read_bytes()
            except OSError:
                continue
    return out


def _read_rar(raw: bytes) -> dict[str, bytes] | None:
    if rarfile is None:
        return None
    out: dict[str, bytes] = {}
    with rarfile.RarFile(io.BytesIO(raw)) as rf:
        for info in rf.infolist():
            n = info.filename
            if info.isdir() or not _keep(n) or (info.file_size or 0) > MAX_SUB_BYTES:
                continue
            out[n] = rf.read(info)
            if len(out) >= _MAX_ENTRIES:
                break
    return out


def read_subtitle_entries(raw: bytes) -> dict[str, bytes] | None:
    """Decompress JUST the subtitle files out of a zip / 7z / rar archive →
    {entry name: bytes}. The single place that touches each archive backend.
    None when the format is unreadable (unknown container, missing lib, or —
    for RAR — no backend extractor); {} when readable but holding no subtitles.
    Per-entry size-capped and entry-count capped."""
    kind = archive_kind(raw)
    try:
        if kind == "zip":
            return _read_zip(raw)
        if kind == "7z":
            return _read_7z(raw)
        if kind == "rar":
            return _read_rar(raw)
    except Exception as ex:
        _log.warning("archive read failed (%s): %r", kind, ex)
        return None
    return None


def entry_durations(subs: dict[str, bytes]) -> dict[str, int]:
    """Per-entry runtime (last SRT/VTT cue → seconds) for the whole pack, read
    ONCE. Independent of any target, so the harvest can reuse it across every
    sibling episode instead of re-parsing per file."""
    out: dict[str, int] = {}
    if len(subs) > 120:
        return out
    for n, b in subs.items():
        if n.lower().rsplit(".", 1)[-1] in ("srt", "vtt"):
            secs = srt_last_cue_seconds(b)
            if secs is not None:
                out[n] = secs
    return out


def rank_entries(
    subs: dict[str, bytes], durations: dict[str, int], *,
    season: int | None = None, episode: int | None = None, absolute: int | None = None,
    episode_title: str | None = None, release_group: str | None = None,
    target_seconds: int | None = None,
) -> PackChoice | None:
    """Rank PRE-READ pack entries for ONE wanted episode. Pure — lets the
    harvest rank the same decompressed pack against many sibling episodes
    without re-opening the archive each time."""
    if not subs:
        return None
    names = list(subs.keys())
    entries = [
        score_entry(
            n, season=season, episode=episode, absolute=absolute,
            episode_title=episode_title, release_group=release_group,
            entry_seconds=durations.get(n), target_seconds=target_seconds,
        )
        for n in names
    ]
    # Sort by score, then prefer the better subtitle format, then name (stable).
    entries.sort(key=lambda e: (
        -e.score, _EXT_RANK.get(e.name.lower().rsplit(".", 1)[-1], 9), e.name.lower()))
    best = entries[0]
    is_pack = len(entries) > 1
    if not is_pack:
        confident = True
    elif best.matched and best.score >= _CONFIDENT_FLOOR:
        # The best entry EXPLICITLY matches the wanted episode → it IS the right
        # episode. A close runner-up here is just another COPY/variant of the SAME
        # episode (a "[Erai-raws] Gachiakuta - 04" archive that bundles full +
        # signs + an alt encode — all episode 4), NOT a competing different
        # episode (those are zeroed by the wrong-episode guard). Don't force the
        # user to choose between equally-correct files; take the best.
        confident = True
    else:
        runner_up = entries[1].score
        confident = best.score >= _CONFIDENT_FLOOR and (best.score - runner_up) >= _CONFIDENT_MARGIN
        # If we don't even know which episode we want, never auto-pick from a pack.
        if episode is None and absolute is None and not episode_title:
            confident = False
    return PackChoice(entries=entries, best=best, confident=confident, is_pack=is_pack)


def choose_from_pack(
    content: bytes, *, season: int | None = None, episode: int | None = None,
    absolute: int | None = None, episode_title: str | None = None,
    release_group: str | None = None, target_seconds: int | None = None,
    read_runtime: bool = True,
) -> PackChoice | None:
    """Open the archive (zip/7z/rar) and rank its entries for ONE wanted
    episode, deciding whether we can pick confidently. None if unreadable / no
    subtitles. A single-entry archive is always confident; a multi-entry pack
    must clear the score floor AND beat the runner-up by a margin."""
    subs = read_subtitle_entries(content)
    if not subs:
        return None
    durations = entry_durations(subs) if (read_runtime and target_seconds) else {}
    return rank_entries(
        subs, durations, season=season, episode=episode, absolute=absolute,
        episode_title=episode_title, release_group=release_group, target_seconds=target_seconds)


def extract_entry(content: bytes, name: str) -> tuple[bytes, str] | None:
    """Read ONE named entry out of a zip/7z/rar archive → (bytes, ext),
    size-capped. None if the entry is missing/oversized/unreadable."""
    subs = read_subtitle_entries(content)
    if not subs or name not in subs:
        return None
    data = subs[name]
    if not data or len(data) > MAX_SUB_BYTES:
        return None
    return data, name.rsplit(".", 1)[-1].lower()


# ── bounded byte cache (inspect → extract without re-downloading) ──────────────
_CACHE: dict[str, tuple[float, bytes]] = {}
_CACHE_TTL = 30 * 60          # 30 minutes — span a longer "pick → fill the season"
_CACHE_MAX_ITEMS = 12         # several shows in flight before one evicts (was 4)
_CACHE_MAX_BYTES = 200 * 1024 * 1024


def _cache_key(provider: str, ref: str) -> str:
    return f"{provider}:{ref}"


def cache_pack(provider: str, ref: str, data: bytes) -> None:
    """Stash a downloaded pack so a follow-up 'extract this entry' reuses it.
    Bounded by item count + total bytes; expired/oldest evicted first."""
    now = time.monotonic()
    # Drop expired.
    for k in [k for k, (ts, _) in _CACHE.items() if now - ts > _CACHE_TTL]:
        _CACHE.pop(k, None)
    _CACHE[_cache_key(provider, ref)] = (now, data)
    # Enforce caps (evict oldest first).
    while len(_CACHE) > _CACHE_MAX_ITEMS or sum(len(v) for _, v in _CACHE.values()) > _CACHE_MAX_BYTES:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)
        if not _CACHE:
            break


def get_cached_pack(provider: str, ref: str) -> bytes | None:
    item = _CACHE.get(_cache_key(provider, ref))
    if item is None:
        return None
    ts, data = item
    if time.monotonic() - ts > _CACHE_TTL:
        _CACHE.pop(_cache_key(provider, ref), None)
        return None
    return data
