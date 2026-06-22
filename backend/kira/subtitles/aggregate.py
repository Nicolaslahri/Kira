"""Subtitle aggregator — gather candidates from every enabled source, SCORE
them against the video, and download the single best per language (instead of
each source blindly taking its own first hit).

Flow per file:
  1. Embedded extraction first — the file's OWN text tracks are perfect sync
     and free; any wanted language found there wins outright.
  2. For the remaining languages, every enabled external provider SEARCHES
     concurrently. All candidates are scored (kira.subtitles.scoring) against
     the video's release. Per language, we try the best-scored candidate; if
     its download/extract fails we fall through to the next.
  3. Each saved subtitle is returned as a SubtitleFetchResult carrying the
     provider, score, sync confidence, and human reasons — so the caller can
     narrate it and store it in history.

A QuotaExceeded / AuthRejected from OpenSubtitles propagates so a batch can
stop cleanly. Everything else is best-effort and never raises out.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import replace
from pathlib import Path

import httpx

from kira.providers import opensubtitles as _opensubtitles
from kira.subtitles import _common
from kira.subtitles import pack as _pack
from kira.subtitles import searchcache as _searchcache
from kira.subtitles import subcache as _subcache
from kira.subtitles import animetosho as _animetosho
from kira.subtitles import embedded as _embedded
from kira.subtitles import podnapisi as _podnapisi
from kira.subtitles import subdl as _subdl
from kira.subtitles import subsource as _subsource
from kira.subtitles import yifysubtitles as _yify
from kira.subtitles.errors import AuthRejected, PackEpisodeMissing, QuotaExceeded
from kira.subtitles.model import SearchContext, SubtitleCandidate, SubtitleFetchResult
from kira.subtitles.scoring import ReleaseInfo, identity_match, score_candidate

_log = logging.getLogger("kira.subtitles.aggregate")

# External providers in default-priority order (used only as a stable tiebreak;
# the SCORE decides). Each exposes async search(client, ctx) + download(client,
# cand, ctx).
_EXTERNAL = [
    ("opensubtitles", _opensubtitles),
    ("subsource", _subsource),
    ("subdl", _subdl),
    ("podnapisi", _podnapisi),
    ("animetosho", _animetosho),
    ("yifysubtitles", _yify),
]
_MODULES = {name: mod for name, mod in _EXTERNAL}


def _lang_from_path(path: str) -> str | None:
    parts = os.path.basename(path).rsplit(".", 2)  # stem, lang, ext
    return parts[1].lower() if len(parts) == 3 else None


def _embedded_langs(parsed) -> list[str]:
    """The container's embedded subtitle-track languages from a SearchContext's
    `parsed` — MediaInfo's `sub_langs` (canonical 3-letter codes). Tolerates the
    dict shape every caller actually passes, a ParsedFile-like object, or None,
    so a missing/odd `parsed` just yields no embedded langs (no skip)."""
    if isinstance(parsed, dict):
        val = parsed.get("sub_langs")
    else:
        val = getattr(parsed, "sub_langs", None)
    return [str(x) for x in val] if isinstance(val, (list, tuple)) else []


async def _cached_search(name: str, mod, client: httpx.AsyncClient,
                         ctx: SearchContext) -> list:
    """One provider search, served from the short-lived result cache when the
    same query ran recently (a backfill loops a whole season; the browse modal
    reopens). A miss runs the live search and stores it. Errors PROPAGATE to the
    caller — so a Quota/Auth stop still halts the batch — and are never cached."""
    key = _searchcache.signature(name, ctx)
    cached = _searchcache.get(key)
    if cached is not None:
        return cached
    result = await mod.search(client, ctx)
    _searchcache.put(key, result)
    return result


async def gather_candidates(
    client: httpx.AsyncClient, ctx: SearchContext, enabled: dict[str, bool],
) -> list[SubtitleCandidate]:
    """Search every enabled external provider concurrently and return all
    candidates, SCORED against the video. Pure-ish (network only); used by both
    the auto-pick flow and the manual browse-and-pick endpoint. Provider calls go
    through a short-lived result cache, so repeated queries (backfill loop,
    reopened browse modal) don't re-hit the network."""
    # Query variants. THOROUGH mode adds a second search by the cour-local S/E
    # for ambiguous anime (we know an absolute number AND a different cour
    # episode), so we catch subs a provider filed under EITHER numbering — the
    # absolute=None twin forces the providers' season/episode path. The merge +
    # dedupe + episode_match passes below (run on the ORIGINAL ctx) reconcile the
    # two result sets.
    query_ctxs = [ctx]
    if (ctx.thorough and ctx.media_type == "anime" and ctx.absolute is not None
            and ctx.episode is not None and ctx.episode != ctx.absolute):
        query_ctxs.append(replace(ctx, absolute=None))
    tasks = []
    for name, mod in _EXTERNAL:
        if not enabled.get(name):
            continue
        for qctx in query_ctxs:
            tasks.append(_cached_search(name, mod, client, qctx))
    if not tasks:
        return []
    results = await asyncio.gather(*tasks, return_exceptions=True)
    cands: list[SubtitleCandidate] = []
    for r in results:
        if isinstance(r, (QuotaExceeded, AuthRejected)):
            raise r
        if isinstance(r, Exception):
            _log.warning("provider search errored: %r", r)
            continue
        cands.extend(r)
    # Drop candidates the user blacklisted for this file (a bad sub they binned).
    if ctx.blacklist:
        cands = [c for c in cands if (c.provider, str(c.download_ref)) not in ctx.blacklist]
    video = ReleaseInfo.from_video(os.path.basename(ctx.video_path), ctx.parsed)
    for c in cands:
        score_candidate(c, video, want_hi=ctx.hearing_impaired, want_forced=ctx.forced)
    # Episode-match pass — the scorer ranks on release pedigree but never checks
    # the EPISODE NUMBER, and some providers (SubSource) return the whole season
    # unfiltered. Read EVERY candidate's release name and match it against BOTH
    # the cour-local S/E and the anime absolute number — boost the right episode,
    # bury a clearly-wrong one. Run it on packs too: a genuine multi-episode pack
    # name (range / complete / season) yields "unknown" via episode_match's own
    # guard so it stays neutral, while a single-episode result a provider
    # MISLABELED as a pack ("[Erai-raws] Show - 04") still ranks correctly.
    if ctx.episode is not None or ctx.absolute is not None:
        for c in cands:
            verdict = _pack.episode_match(
                c.release_name, season=ctx.season, episode=ctx.episode, absolute=ctx.absolute)
            if verdict == "match":
                c.score = min(100, c.score + 16)
                c.reasons.append("matches the requested episode")
                if c.sync == "unknown":
                    c.sync = "likely"
            elif verdict == "mismatch":
                c.score = max(0, c.score - 50)
                c.reasons.append("names a different episode")
    # Movie identity gate — a candidate whose provider-reported FILM identity
    # (imdb/tmdb id, or year) clearly differs from the title we matched is the
    # WRONG MOVIE (Ballerina 2023 vs 2025), however good its release looks. Only
    # OpenSubtitles reports this today; fires only on a KNOWN mismatch. Movies
    # only — for episodes the feature id is the episode's (not the series'), and
    # the episode-match pass already covers TV/anime.
    if ctx.media_type == "movie":
        for c in cands:
            verdict = identity_match(c, imdb_id=ctx.imdb_id, tmdb_id=ctx.tmdb_id, year=ctx.year)
            if verdict == "mismatch":
                c.score = max(0, c.score - 60)
                c.reasons.append("different film")
            elif verdict == "match":
                c.score = min(100, c.score + 12)
                c.reasons.append("confirmed title")
    cands.sort(key=lambda c: c.score, reverse=True)
    # Dedupe near-identical candidates surfaced by multiple providers (same
    # language + same release string), keeping the highest-scored — a shorter,
    # cleaner pick menu. Candidates with NO release string can't be compared, so
    # they're all kept (never collapse distinct subs that merely lack a name).
    seen: set = set()
    deduped: list[SubtitleCandidate] = []
    for c in cands:
        rel = " ".join((c.release_name or "").lower().split())
        if rel:
            key = (c.language, rel)
            if key in seen:
                continue
            seen.add(key)
        deduped.append(c)
    return deduped


async def fetch_subtitles(
    client: httpx.AsyncClient, ctx: SearchContext, *,
    enabled: dict[str, bool], on_status=None,
) -> list[SubtitleFetchResult]:
    """Fetch the best subtitle per wanted language → sidecars. Returns the
    saved results (with provider/score/sync/reasons). Best-effort per source;
    a quota/auth stop from OpenSubtitles propagates."""
    def _say(msg: str) -> None:
        if on_status is not None:
            try:
                on_status(msg)
            except Exception:
                pass

    results: list[SubtitleFetchResult] = []
    # Skip any language already EMBEDDED in the container (MediaInfo's
    # `sub_langs`) as well as any already on disk — don't pull an external sub
    # for a language the file already carries inside it, even when embedded
    # EXTRACTION isn't enabled. Pulled safely from ctx.parsed (dict | object |
    # None); when MediaInfo never ran this is empty → no behavior change.
    embedded_langs = _embedded_langs(ctx.parsed)
    remaining = set(_common.languages_needing_fetch(
        ctx.video_path, ctx.languages, embedded=embedded_langs))
    if not remaining:
        return results

    # 1) Embedded — the file's own tracks (perfect sync, free).
    if enabled.get("embedded", True) and _embedded.available():
        try:
            _say("checking embedded tracks")
            for p in await _embedded.extract(ctx.video_path, list(remaining), forced=ctx.forced):
                lang = _lang_from_path(p)
                if lang:
                    results.append(SubtitleFetchResult(
                        language=lang, path=p, provider="embedded",
                        release_name="embedded track", score=100, sync="guaranteed",
                        reasons=["embedded track (perfect sync)"]))
                    remaining.discard(lang)
        except Exception as e:
            _log.warning("embedded failed for %s: %r", ctx.video_path, e)
    if not remaining:
        return results

    # 1b) Reuse-cache — a sub we previously downloaded for THIS video (keyed by
    # content hash, so it survives the rename) was kept on undo instead of being
    # deleted. Reusing it skips a network fetch entirely (no OpenSubtitles quota
    # burned). Mirrors the on-disk-sidecar skip above: on a hit we copy it to the
    # sidecar and drop the language from the to-download set. Best-effort.
    for lang in list(remaining):
        if _common.has_sidecar(ctx.video_path, lang):
            remaining.discard(lang)
            continue
        try:
            cached = await _subcache.find_cached_subtitle(ctx.video_path, lang)
        except Exception as e:
            _log.debug("subcache lookup failed for %s/%s: %r", ctx.video_path, lang, e)
            cached = None
        if not cached:
            continue
        try:
            data = await asyncio.to_thread(Path(cached).read_bytes)
        except OSError:
            continue
        if not data or len(data) > _common.MAX_SUB_BYTES:
            continue
        path = await asyncio.to_thread(
            _common.save_sidecar, ctx.video_path, lang, data, "srt")
        if not path:
            continue
        _say(f"reused cached {lang.upper()} subtitle — no download needed")
        results.append(SubtitleFetchResult(
            language=lang, path=path, provider="cache",
            release_name="reuse-cache", score=100, sync="likely",
            reasons=["reused from cache (previously downloaded for this file)"]))
        remaining.discard(lang)
    if not remaining:
        return results

    # 2) External providers — search all, score, pick best per language.
    sub_ctx = SearchContext(**{**ctx.__dict__, "languages": list(remaining)})
    _say("searching subtitle providers")
    candidates = await gather_candidates(client, sub_ctx, enabled)  # may raise quota/auth
    by_lang: dict[str, list[SubtitleCandidate]] = {}
    for c in candidates:
        if c.language in remaining:
            by_lang.setdefault(c.language, []).append(c)

    # 3) Per language: try best-scored first, fall through on failure.
    for lang in list(remaining):
        for cand in by_lang.get(lang, []):
            if _common.has_sidecar(ctx.video_path, lang):
                break
            # Minimum-score floor: better no sub than a likely-mistimed one.
            if ctx.min_score and cand.score < ctx.min_score:
                _say(f"best {lang.upper()} candidate {cand.score}% < {ctx.min_score}% floor — skipped")
                break
            _say(f"{cand.provider} · {cand.score}% — downloading {lang.upper()}")
            try:
                res = await download_and_save(client, sub_ctx, cand)  # propagates quota/auth
            except PackEpisodeMissing:
                # This pack didn't contain the episode — try the next candidate
                # instead of failing the language outright.
                _say(f"{cand.provider} pack missing {lang.upper()} episode — trying next")
                continue
            if res is not None:
                results.append(res)
                break
    return results


def _pack_signals(ctx: SearchContext) -> dict:
    """The episode-identifying signals we feed the pack ranker — everything the
    matcher already resolved (S/E, absolute number, episode title, release
    group, real duration), so a season pack is split by the SAME knowledge the
    rest of the app uses, not a lone regex."""
    parsed = ctx.parsed if isinstance(ctx.parsed, dict) else {}
    return dict(
        season=ctx.season, episode=ctx.episode,
        absolute=parsed.get("absolute_episode"),
        episode_title=ctx.episode_title,
        release_group=parsed.get("release_group"),
        target_seconds=parsed.get("duration"),
    )


def _build_result(ctx: SearchContext, cand: SubtitleCandidate, path: str,
                  pack_reasons: list[str] | None = None) -> SubtitleFetchResult:
    reasons = list(cand.reasons)
    if pack_reasons:
        reasons = reasons + [f"pack — {r}" for r in pack_reasons]
    return SubtitleFetchResult(
        language=cand.language, path=path, provider=cand.provider,
        release_name=cand.release_name, ref=str(cand.download_ref),
        score=cand.score, sync=cand.sync, reasons=reasons)


async def download_raw(
    client: httpx.AsyncClient, cand: SubtitleCandidate, ctx: SearchContext,
) -> bytes | None:
    """Fetch one candidate's raw bytes (a sub or a ZIP). Best-effort; Quota/Auth
    propagate so a batch can stop. Shared by every download path."""
    try:
        return await _MODULES[cand.provider].download(client, cand, ctx)
    except (QuotaExceeded, AuthRejected):
        raise
    except Exception as e:
        _log.warning("%s download failed: %r", cand.provider, e)
        return None


async def download_and_save(
    client: httpx.AsyncClient, ctx: SearchContext, cand: SubtitleCandidate,
    *, overwrite: bool = False,
) -> SubtitleFetchResult | None:
    """AUTOMATED download+save (backfill, after-rename, upgrade sweep). For a
    season pack it extracts the episode ONLY when the ranker is confident;
    otherwise it raises PackEpisodeMissing so the caller falls through to the
    next candidate (a non-interactive flow has no user to ask). `overwrite=True`
    replaces an existing sidecar. None on plain failure; Quota/Auth propagate."""
    # Quality floor: automated saves must clear the user's min_score, same as
    # fetch_subtitles. The INTERACTIVE manual_pick path deliberately skips this
    # (the user chose it). Closes the upgrade-sweep gap where a low manual pick
    # could be auto-replaced by a still-below-floor release.
    if ctx.min_score and cand.score is not None and cand.score < ctx.min_score:
        return None
    raw = await download_raw(client, cand, ctx)
    if not raw:
        return None
    kind = _pack.archive_kind(raw)
    if kind in ("zip", "7z", "rar"):
        choice = await asyncio.to_thread(_pack.choose_from_pack, raw, **_pack_signals(ctx))
        if choice is None or choice.best is None:
            return None
        if not choice.confident:
            # Ambiguous pack — don't silently save the wrong episode.
            raise PackEpisodeMissing(
                f"no confident episode match inside the pack ({len(choice.entries)} entries)")
        extracted = await asyncio.to_thread(_pack.extract_entry, raw, choice.best.name)
        if extracted is None:
            return None
        data, ext = extracted
        pack_reasons = choice.best.reasons if choice.is_pack else None
        # Keep the pack bytes warm so the caller can HARVEST the rest of the
        # season from this one download instead of re-fetching per episode.
        if choice.is_pack:
            _pack.cache_pack(cand.provider, str(cand.download_ref), raw)
    elif kind is not None:
        # gzip / unknown container — can't open it; don't save the blob as .srt.
        _log.warning("%s served a %s archive — unsupported, skipping %s",
                     cand.provider, kind, ctx.video_path)
        return None
    else:
        if len(raw) > _common.MAX_SUB_BYTES:
            return None
        data, ext, pack_reasons = raw, "srt", None
    path = _common.save_sidecar(ctx.video_path, cand.language, data, ext=ext, overwrite=overwrite)
    if not path:
        return None
    return _build_result(ctx, cand, path, pack_reasons)


async def manual_pick(
    client: httpx.AsyncClient, ctx: SearchContext, cand: SubtitleCandidate,
    *, overwrite: bool = False,
) -> dict:
    """INTERACTIVE download for the browse modal. Saves immediately when it can
    pick the episode confidently; when a pack is ambiguous it caches the
    downloaded bytes and returns the RANKED entries so the user chooses (no
    re-download on the follow-up). Returns a discriminated dict:
      {ok: True, result: SubtitleFetchResult}
      {ok: False, needs_choice: True, entries: [...]}   (pack — user must pick)
      {ok: False, error: "..."}                          (download/save failed)
    Quota/Auth propagate."""
    # Self-heal: if this language is ALREADY on disk (coverage was stale, or the
    # user clicked the chip twice), don't re-download or fail — re-assert it so
    # the chip clears and report it's already there. Kills the "pick → saved →
    # chip still shows → pick again" loop.
    existing = _common.find_sidecar(ctx.video_path, cand.language)
    if existing and not overwrite:
        return {"ok": True, "already_present": True,
                "result": _build_result(ctx, cand, existing)}
    raw = await download_raw(client, cand, ctx)
    if not raw:
        return {"ok": False, "error": "download_failed"}
    kind = _pack.archive_kind(raw)
    if kind in ("zip", "7z", "rar"):
        # RAR needs an external extractor; if none is available say so precisely
        # (rather than a vague failure) so the user knows what to install.
        if kind == "rar" and not _pack.rar_backend_available():
            return {"ok": False, "error": "no_rar_tool"}
        choice = await asyncio.to_thread(_pack.choose_from_pack, raw, **_pack_signals(ctx))
        if choice is None or choice.best is None:
            # A RAR we have a backend for but still couldn't read usually means
            # the detected extractor (e.g. Windows bsdtar) lacks RAR support.
            return {"ok": False, "error": "rar_extract_failed" if kind == "rar" else "empty_archive"}
        if choice.confident:
            extracted = await asyncio.to_thread(_pack.extract_entry, raw, choice.best.name)
            if extracted is None:
                return {"ok": False, "error": "extract_failed"}
            data, ext = extracted
            # Warm the cache so the endpoint can harvest the rest of the season.
            if choice.is_pack:
                _pack.cache_pack(cand.provider, str(cand.download_ref), raw)
            path = _common.save_sidecar(ctx.video_path, cand.language, data, ext=ext, overwrite=overwrite)
            return ({"ok": True, "result": _build_result(
                        ctx, cand, path, choice.best.reasons if choice.is_pack else None)}
                    if path else {"ok": False, "error": "save_failed"})
        # Ambiguous pack — hand the ranked list to the user; keep the bytes warm.
        _pack.cache_pack(cand.provider, str(cand.download_ref), raw)
        return {"ok": False, "needs_choice": True,
                "entries": [e.public() for e in choice.entries[:60]]}
    if kind is not None:
        # gzip / unknown container — can't open it; say so plainly rather than
        # writing the blob to disk as a broken .srt.
        return {"ok": False, "error": "unsupported_archive", "kind": kind}
    # Plain subtitle file.
    if len(raw) > _common.MAX_SUB_BYTES:
        return {"ok": False, "error": "too_large"}
    path = _common.save_sidecar(ctx.video_path, cand.language, raw, ext="srt", overwrite=overwrite)
    return ({"ok": True, "result": _build_result(ctx, cand, path)} if path
            else {"ok": False, "error": "save_failed"})


async def save_pack_entry(
    client: httpx.AsyncClient, ctx: SearchContext, cand: SubtitleCandidate,
    entry_name: str, *, overwrite: bool = False,
) -> dict:
    """Extract a SPECIFIC entry the user chose from an ambiguous pack and save
    it. Reuses the cached pack bytes when still warm, else re-downloads (and
    re-caches). If the exact entry can't be found — e.g. the cache aged out / a
    server reload wiped it and the re-download's listing drifted — it falls back
    to the best-ranked entry for THIS file's episode rather than just failing.
    Returns a discriminated dict ({ok, result} | {ok: False, error}); Quota/Auth
    propagate."""
    raw = _pack.get_cached_pack(cand.provider, str(cand.download_ref))
    if raw is None:
        raw = await download_raw(client, cand, ctx)
        if raw and _pack.archive_kind(raw):
            _pack.cache_pack(cand.provider, str(cand.download_ref), raw)   # keep warm for harvest
    if not raw:
        return {"ok": False, "error": "download_failed"}
    if _pack.archive_kind(raw) == "rar" and not _pack.rar_backend_available():
        return {"ok": False, "error": "no_rar_tool"}

    extracted = await asyncio.to_thread(_pack.extract_entry, raw, entry_name)
    note = entry_name
    if extracted is None:
        # Exact entry gone (cache wiped → re-download drifted). Recover ONLY if
        # the ranker is CONFIDENT about this file's episode — never silently save
        # a wrong episode just because the user clicked something.
        choice = await asyncio.to_thread(_pack.choose_from_pack, raw, **_pack_signals(ctx))
        if choice and choice.confident and choice.best:
            extracted = await asyncio.to_thread(_pack.extract_entry, raw, choice.best.name)
            note = choice.best.name
            _log.info("pack extract: entry %r not found, fell back to confident %r", entry_name, choice.best.name)
    if extracted is None:
        return {"ok": False, "error": "entry_not_found"}
    data, ext = extracted
    path = _common.save_sidecar(ctx.video_path, cand.language, data, ext=ext, overwrite=overwrite)
    if not path:
        return {"ok": False, "error": "save_failed"}
    short = note.replace("\\", "/").rsplit("/", 1)[-1]
    return {"ok": True, "result": _build_result(ctx, cand, path, [f"you chose “{short}”"])}
