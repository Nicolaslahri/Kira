"""Subtitle backfill — fetch the user's wanted languages for files that are
missing them, narrating progress to the activity surface so the UI shows a
Sonarr-style live story ("Attack on Titan S03E07 · searching OpenSubtitles ·
found 80 · downloading EN · 3 of 12 done").

Runs SEQUENTIALLY on purpose: it gives a coherent per-file story and is gentle
on the OpenSubtitles daily download quota (which a parallel blast would burn
through in seconds). When the quota IS hit, it stops cleanly and reports what
got done plus when it resumes — never failing the remaining files one by one.

Reuses the exact aggregator the rename hook uses (embedded → OpenSubtitles →
YIFY), so source order, HI/forced variants, and per-source toggles all match.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from kira import activity, net
from kira.database import SessionLocal
from kira.models import MediaFile, Notification
from kira.parser.parser import ParsedFile
from kira.subtitles.aggregate import fetch_subtitles
from kira.subtitles.coverage import normalize_lang, present_languages
from kira.subtitles.errors import AuthRejected, QuotaExceeded
from kira.subtitles.model import SearchContext
from kira.subtitles.prefs import load_subtitle_prefs
from kira.subtitles import store as _store

logger = logging.getLogger("kira.subtitles.backfill")

SUBTITLE_BACKFILL_JOB = "subtitle_backfill"


SUBTITLE_UPGRADE_JOB = "subtitle_upgrade"


async def run_subtitle_upgrade() -> dict:
    """Upgrade-over-time: re-check subtitles that scored BELOW the user's
    `upgrade_below` bar for a better candidate, and replace when one is found
    (strictly higher score, a different release). Embedded tracks (perfect
    sync) and blacklisted refs are left alone. Best-effort; narrates to the
    activity pill. No-op unless `subtitles.upgrade` is on."""
    from kira.models import SubtitleAsset
    from kira.subtitles.aggregate import download_and_save, gather_candidates
    summary = {"checked": 0, "upgraded": 0}
    started = False
    try:
        async with SessionLocal() as session:
            prefs = await load_subtitle_prefs(session)
            if not prefs.upgrade or not prefs.any_source_enabled:
                return summary
            assets = list(await session.scalars(
                select(SubtitleAsset).where(
                    SubtitleAsset.active.is_(True),
                    SubtitleAsset.blacklisted.is_(False),
                    SubtitleAsset.provider != "embedded",
                    SubtitleAsset.score < prefs.upgrade_below,
                    SubtitleAsset.media_file_id.isnot(None),
                )
            ))
            if not assets:
                return summary
            client = net.shared_client()
            activity.begin(SUBTITLE_UPGRADE_JOB, "Upgrading subtitles", total=len(assets))
            started = True
            done = 0
            for asset in assets:
                done += 1
                activity.progress(SUBTITLE_UPGRADE_JOB, done, len(assets))
                # A deliberate MANUAL pick is never auto-replaced, even below
                # the upgrade threshold — the user chose it (audit §20 m).
                if isinstance(asset.reasons, list) and "manual pick" in asset.reasons:
                    continue
                mf = await session.get(MediaFile, asset.media_file_id)
                if mf is None or not mf.file_path:
                    continue
                summary["checked"] += 1
                ctx = await build_context(session, mf, prefs, [asset.language])
                try:
                    cands = await gather_candidates(client, ctx, prefs.sources_for(ctx.media_type))
                except Exception as e:
                    logger.warning(f"upgrade: gather failed for {mf.id}: {e!r}")
                    continue
                best = next((c for c in cands if c.language == asset.language), None)
                # Only replace on a STRICTLY better, DIFFERENT release.
                if best is None or best.score <= asset.score or str(best.download_ref) == (asset.ref or ""):
                    continue
                activity.set_label(SUBTITLE_UPGRADE_JOB,
                                   f"{_file_label(mf)} · {asset.score}% → {best.score}%")
                try:
                    res = await download_and_save(client, ctx, best, overwrite=True)
                except Exception as e:
                    logger.warning(f"upgrade: download failed for {mf.id}: {e!r}")
                    res = None
                if res is not None:
                    # Cross-ext strand (audit §20 m): replacing an `.srt` with a
                    # better `.ass` (or vice versa) left the OLD file on disk —
                    # and the srt-first sidecar probe kept preferring it. Remove
                    # the superseded file when the extension changed.
                    try:
                        if (asset.path and res.path
                                and Path(asset.path).suffix.lower() != Path(res.path).suffix.lower()
                                and Path(asset.path).exists()):
                            Path(asset.path).unlink()
                    except OSError:
                        pass
                    asset.active = False  # supersede the old record
                    await _store.record_results(session, mf.id, asset.title, [res])
                    summary["upgraded"] += 1
    except Exception as e:
        logger.warning(f"run_subtitle_upgrade aborted (non-fatal): {e!r}")
    finally:
        if started:
            activity.end(SUBTITLE_UPGRADE_JOB, ok=True,
                         detail=f"upgraded {summary['upgraded']} of {summary['checked']} checked")
    if summary["upgraded"]:
        await _notify("success", "Subtitles upgraded",
                      f"Replaced {summary['upgraded']} subtitle(s) with a better-scoring match.")
    return summary


def spawn_subtitle_upgrade() -> bool:
    if not _has_loop():
        return False
    from kira.tasks import spawn_tracked
    spawn_tracked(run_subtitle_upgrade(), label=SUBTITLE_UPGRADE_JOB)
    return True


def _has_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def spawn_subtitle_backfill(file_ids: list[int], *, language_override: list[str] | None = None) -> bool:
    """Fire-and-forget the backfill (detached, strong-ref'd, exception-logged).
    No-op without a running loop (sync/test contexts). Returns True if spawned."""
    if not file_ids:
        return False
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    # In-flight guard: a second click (or a scan-triggered backfill racing a
    # manual one) would otherwise run two concurrent sweeps over the same
    # files — burning provider quota twice and fighting over the activity pill.
    # Mark active SYNCHRONOUSLY here (not just in the async task's own begin,
    # which runs later) so a second call in the same tick is rejected.
    from kira import activity
    if activity.is_active(SUBTITLE_BACKFILL_JOB):
        return False
    activity.begin(SUBTITLE_BACKFILL_JOB, "Finding subtitles")
    from kira.tasks import spawn_tracked
    spawn_tracked(
        run_subtitle_backfill(list(file_ids), language_override=language_override),
        label=SUBTITLE_BACKFILL_JOB,
    )
    return True


def _file_label(mf: MediaFile) -> str:
    """Short human label for the activity narration — the matched title with
    SxxEyy when we have it, else the filename."""
    sel = next((m for m in mf.matches if m.is_selected), None)
    name = Path(mf.file_path).stem if mf.file_path else f"file {mf.id}"
    if sel and sel.title:
        if sel.season_number is not None and sel.episode_number is not None:
            return f"{sel.title} S{sel.season_number:02d}E{sel.episode_number:02d}"
        return sel.title
    return name


def _title_query(title: str) -> str:
    """Reduce a match title to an OpenSubtitles-friendly series query: drop a
    trailing '(year)' and a trailing 'Season N' / 'The Final Season' style
    suffix (the season number goes in its own search param — AniDB titles like
    'Attack on Titan Season 3 (2019)' would otherwise miss), and normalize the
    backtick AniDB uses for apostrophes."""
    q = title.replace("`", "'").strip()
    q = re.sub(r"\s*\(\d{4}\)\s*$", "", q)
    q = re.sub(r"\s*(?::\s*)?(?:the\s+)?(?:final\s+season|season\s+\d+)\s*$", "", q, flags=re.IGNORECASE)
    return q.strip() or title


async def build_context(session, mf, prefs, languages: list[str]) -> SearchContext:
    """Build the per-file SearchContext shared by the backfill, the manual-pick
    endpoint, and the candidate-browse endpoint. Resolves provider ids off the
    selected match and prefers PARSED (rendered-filename) S/E over cour-local
    match numbers (AniDB stores cour-local episodes → searching by those would
    fetch the WRONG episode's subs)."""
    sel = next((m for m in mf.matches if m.is_selected), None)
    tmdb_id = (int(sel.provider_id) if sel and sel.provider == "tmdb"
               and (sel.provider_id or "").isdigit() else None)
    imdb_id = None
    if sel and isinstance(getattr(sel, "metadata_blob", None), dict):
        imdb_id = sel.metadata_blob.get("imdbid") or sel.metadata_blob.get("imdb_id")
    anidb_id = (int(sel.provider_id) if sel and sel.provider == "anidb"
                and (sel.provider_id or "").isdigit() else None)
    parsed = mf.parsed_data if isinstance(mf.parsed_data, dict) else {}
    season = parsed.get("season")
    episode = parsed.get("episode")
    if season is None and sel is not None:
        season = sel.season_number
    if episode is None and sel is not None:
        episode = sel.episode_number
    query = _title_query(sel.title) if sel and sel.title else None
    episode_title = sel.episode_title if sel else None
    year = sel.year if sel else None
    absolute = parsed.get("absolute_episode")
    # An AniDB match is ANIME even if the file was scanned into a `tv` library —
    # this drives absolute-episode search + the anime fallback chain (a tv-typed
    # AniDB file otherwise misses absolute-numbered subs entirely).
    media_type = "anime" if (sel and sel.provider == "anidb") else mf.media_type
    return SearchContext(
        video_path=mf.file_path, languages=languages, media_type=media_type,
        query=query, tmdb_id=tmdb_id, imdb_id=imdb_id, anidb_id=anidb_id, year=year,
        season=season, episode=episode, absolute=absolute, episode_title=episode_title, parsed=parsed,
        os_api_key=prefs.api_key, os_user=prefs.username, os_pw=prefs.password,
        subdl_api_key=prefs.subdl_api_key, subsource_api_key=prefs.subsource_api_key,
        hearing_impaired=prefs.hearing_impaired or "", forced=prefs.forced or "",
        blacklist=await _store.load_blacklist(session, mf.id),
        min_score=prefs.min_score_for(media_type),
        thorough=prefs.thorough_search,
    )


async def _find_pack_siblings(session, source_mf) -> list:
    """Episodes in the SAME series as source_mf — by shared
    `Match.series_group_id`, else by same parent folder. Used to spread a
    downloaded season pack across the season (harvest) and to count how many
    episodes a pack could still fill (the opt-in offer)."""
    from pathlib import Path
    from kira.models import Match
    sel = next((m for m in source_mf.matches if getattr(m, "is_selected", False)), None)
    group = getattr(sel, "series_group_id", None) if sel else None
    if group:
        rows = list(await session.scalars(
            select(MediaFile).options(selectinload(MediaFile.matches))
            .join(Match, Match.media_file_id == MediaFile.id)
            .where(Match.is_selected.is_(True), Match.series_group_id == group)
        ))
        return list({m.id: m for m in rows}.values())
    if not source_mf.file_path:
        return []
    parent = str(Path(source_mf.file_path).parent)
    prefix = (parent + ("\\" if "\\" in parent else "/")).replace("%", "\\%").replace("_", "\\_")
    rows = list(await session.scalars(
        select(MediaFile).options(selectinload(MediaFile.matches))
        .where(MediaFile.file_path.like(prefix + "%", escape="\\"))
    ))
    return [m for m in rows if m.file_path and str(Path(m.file_path).parent) == parent]


async def count_missing_siblings(session, source_mf, language: str) -> int:
    """How many OTHER episodes in this series are still missing `language`
    (per coverage, no disk I/O). Drives the opt-in "fill N more from this pack"
    offer after a single-episode pick — so we never mass-patch without consent."""
    from kira.subtitles.coverage import present_languages
    from kira.subtitles.embedded import normalize_lang
    lang = normalize_lang(language) or (language or "").lower()
    n = 0
    for mf in await _find_pack_siblings(session, source_mf):
        if mf.id == source_mf.id or not mf.file_path or mf.media_type not in ("tv", "anime"):
            continue
        if lang not in present_languages(mf.parsed_data):
            n += 1
    return n


async def _redownload_pack(session, source_mf, provider: str, ref, language: str, client) -> bytes | None:
    """Re-fetch a season pack we no longer have in the (short-lived, in-memory)
    byte cache, so a user-requested harvest still runs instead of silently
    no-op'ing. Rebuilds the minimal candidate + context from the source file and
    re-caches on success. Best-effort → None on any failure."""
    from kira.subtitles.aggregate import download_raw
    from kira.subtitles.model import SubtitleCandidate
    from kira.subtitles import pack as _pack
    try:
        prefs = await load_subtitle_prefs(session)
        ctx = await build_context(session, source_mf, prefs, [language])
        cand = SubtitleCandidate(provider=provider, language=language, download_ref=ref)
        raw = await download_raw(client, cand, ctx)
        if raw and _pack.archive_kind(raw):
            _pack.cache_pack(provider, str(ref), raw)
            return raw
    except Exception as e:
        logger.warning("pack harvest re-download failed (%s:%s): %r", provider, ref, e)
    return None


async def harvest_from_cached_pack(session, source_mf, provider: str, ref: str, language: str,
                                   *, client=None) -> int:
    """A season pack we downloaded for ONE episode holds subs for the WHOLE
    season — so don't throw the other 23 away. Decompress the cached pack once
    and, for every sibling episode in the same series that's still MISSING this
    language, find its matching entry (same ranker: S/E, absolute, title,
    runtime, group) and save it. One download → the whole season covered, and
    the per-episode coverage chips clear so we never re-query/re-download for
    them. If the pack has aged out of the in-memory cache (and a `client` is
    given), it is re-fetched ONCE — one download still covers the whole season,
    far cheaper than re-querying per episode. Best-effort; returns how many extra
    sidecars it saved. Never raises."""
    from kira.subtitles import _common, pack as _pack
    from kira.subtitles.model import SubtitleFetchResult

    lang = normalize_lang(language) or (language or "").lower()
    try:
        raw = _pack.get_cached_pack(provider, str(ref))
        if not raw and client is not None:
            raw = await _redownload_pack(session, source_mf, provider, ref, lang, client)
        if not raw:
            return 0
        # Decompressing the archive (zip/7z/rar) and scanning every SRT's last
        # cue is heavy CPU/IO — run it OFF the event loop so a season pack
        # doesn't stall every other request/scan. (Every other archive path
        # already offloads via asyncio.to_thread; this one was missed.)
        subs = await asyncio.to_thread(_pack.read_subtitle_entries, raw)
        if not subs or len(subs) < 2:          # not a multi-episode pack
            return 0
        durations = await asyncio.to_thread(_pack.entry_durations, subs)
        siblings = await _find_pack_siblings(session, source_mf)

        # The pack was downloaded for the SOURCE file's season. Siblings from
        # OTHER seasons must not harvest from it: an episode-less entry name
        # ("Show - 05.srt") explicit-matches the wrong season's E05 with full
        # confidence — S1 subs written onto S2 episodes.
        _src_parsed = source_mf.parsed_data if isinstance(source_mf.parsed_data, dict) else {}
        _src_sel = next((m for m in source_mf.matches if getattr(m, "is_selected", False)), None)
        _src_season = (_src_parsed.get("season") if _src_parsed.get("season") is not None
                       else (_src_sel.season_number if _src_sel else None))

        saved = 0
        for mf in siblings:
            if mf.id == source_mf.id or not mf.file_path:
                continue
            if mf.media_type not in ("tv", "anime"):
                continue
            _sib_parsed = mf.parsed_data if isinstance(mf.parsed_data, dict) else {}
            _sib_sel = next((m for m in mf.matches if getattr(m, "is_selected", False)), None)
            _sib_season = (_sib_parsed.get("season") if _sib_parsed.get("season") is not None
                           else (_sib_sel.season_number if _sib_sel else None))
            if (_src_season is not None and _sib_season is not None
                    and _sib_season != _src_season):
                continue
            if _common.has_sidecar(mf.file_path, lang):
                continue
            parsed = mf.parsed_data if isinstance(mf.parsed_data, dict) else {}
            msel = next((m for m in mf.matches if getattr(m, "is_selected", False)), None)
            season = parsed.get("season") if parsed.get("season") is not None else (msel.season_number if msel else None)
            episode = parsed.get("episode") if parsed.get("episode") is not None else (msel.episode_number if msel else None)
            choice = await asyncio.to_thread(
                _pack.rank_entries,
                subs, durations, season=season, episode=episode,
                absolute=parsed.get("absolute_episode"),
                episode_title=(msel.episode_title if msel else None),
                release_group=parsed.get("release_group"),
                target_seconds=parsed.get("duration"),
            )
            if not (choice and choice.confident and choice.best):
                continue
            data = subs.get(choice.best.name)
            if not data:
                continue
            ext = choice.best.name.rsplit(".", 1)[-1].lower()
            path = _common.save_sidecar(mf.file_path, lang, data, ext=ext, overwrite=False)
            if not path:
                continue
            res = SubtitleFetchResult(
                language=lang, path=path, provider=provider, ref=str(ref),
                score=choice.best.score, sync="unknown",
                reasons=["from the same season pack"] + choice.best.reasons[:1])
            title = next((m.title for m in mf.matches if getattr(m, "is_selected", False) and m.title), None)
            await _store.record_results(session, mf.id, title, [res])
            await _record_langs(session, mf.id, [lang])
            saved += 1
            if saved >= 500:                   # safety cap
                break
        if saved:
            logger.info("harvested %d extra %s sidecars from %s pack", saved, lang.upper(), provider)
        return saved
    except Exception as e:
        logger.warning(f"pack harvest failed (non-fatal): {e!r}")
        try:
            await session.rollback()
        except Exception:
            pass
        return 0


def needed_languages(parsed: dict | None, wanted: list[str]) -> list[str]:
    """Wanted languages not already present (embedded or sidecar). Unlike the
    coverage chip this ignores the 'inspected' gate — the user explicitly asked
    to fetch, and the aggregator self-skips anything truly on disk anyway."""
    if (parsed or {}).get("media_type") == "music":
        return []   # music has no subtitles — never target it for backfill
    present = present_languages(parsed)
    return [w for w in wanted if normalize_lang(w) not in present]


def _langs_from_saved(saved: list[str]) -> list[str]:
    """The 2-letter languages represented by a batch of saved sidecar paths.
    A sidecar is `<video stem>.<lang>.<ext>`, so the language is the
    second-to-last dotted segment of the filename."""
    out: list[str] = []
    for p in saved:
        parts = Path(p).name.rsplit(".", 2)  # [stem, lang, ext]
        if len(parts) != 3:
            continue
        lang = normalize_lang(parts[1])
        if lang and lang not in out:
            out.append(lang)
    return out


async def run_subtitle_backfill(file_ids: list[int], *, language_override: list[str] | None = None) -> dict:
    """Fetch missing subtitles for `file_ids`, narrating to the activity pill.
    Best-effort and fully exception-isolated. Returns a summary dict."""
    summary = {"files": 0, "saved": 0, "covered": 0, "not_found": 0, "quota": False}
    if not file_ids:
        return summary

    started = False
    quota_err: QuotaExceeded | None = None
    try:
        async with SessionLocal() as session:
            prefs = await load_subtitle_prefs(session)
            wanted = [w.lower() for w in (language_override or prefs.languages) if w]
            if not wanted:
                return summary
            if not prefs.any_source_enabled:
                await _notify(
                    "warning", "No subtitle source enabled",
                    "Turn on embedded extraction or add an OpenSubtitles API key "
                    "(Settings → Subtitles) before fetching subtitles.",
                )
                return summary

            rows = list(await session.scalars(
                select(MediaFile)
                .options(selectinload(MediaFile.matches))
                .where(MediaFile.id.in_(file_ids))
            ))
            # Only files that have a path AND still need at least one language.
            # Wanted languages are PER MEDIA TYPE (anime may want different langs
            # than movies); an explicit override wins over everything.
            work: list[tuple[MediaFile, list[str]]] = []
            for mf in rows:
                if not mf.file_path:
                    continue
                file_wanted = ([w.lower() for w in language_override if w]
                               if language_override else prefs.languages_for(mf.media_type))
                need = needed_languages(mf.parsed_data, file_wanted)
                if need:
                    work.append((mf, need))
                else:
                    summary["covered"] += 1

            total = len(work)
            if total == 0:
                return summary

            client = net.shared_client()
            # Diagnose silent gaps UP FRONT so a "not found" summary can say
            # WHY: embedded toggled on but unusable (no ffmpeg/pymediainfo),
            # or OpenSubtitles searchable but not downloadable (no login).
            from kira.subtitles import embedded as _embedded
            hints: list[str] = []
            if prefs.embedded and not _embedded.available():
                hints.append("Install ffmpeg to extract the subs already inside your files (best source for anime).")
            # OpenSubtitles near-misses: a login saved but no key (it's off), or
            # neither — it's one of the strongest sources, so flag the gap.
            if not prefs.has_key:
                if prefs.username or prefs.password:
                    hints.append("OpenSubtitles is OFF — you saved a login but no API key. Add the key in Connections.")
                else:
                    hints.append("Connect OpenSubtitles (free API key in Connections) — a top source, currently off.")
            elif not prefs.has_download_creds:
                hints.append("Add your OpenSubtitles username + password (Connections) — downloads need a login.")
            # SubDL / SubSource toggled on but missing their key → silently off.
            if prefs.subdl and not prefs.subdl_api_key:
                hints.append("SubDL is on but has no API key — add it in Connections (or it sits out).")
            if prefs.subsource and not prefs.subsource_api_key:
                hints.append("SubSource is on but has no API key — add it in Connections.")
            summary["hints"] = hints
            activity.begin(SUBTITLE_BACKFILL_JOB, "Finding subtitles", total=total)
            started = True

            done = 0
            for mf, need in work:
                label = _file_label(mf)
                activity.progress(SUBTITLE_BACKFILL_JOB, done, total)

                def _say(msg: str, _label=label) -> None:
                    activity.set_label(SUBTITLE_BACKFILL_JOB, f"{_label} · {msg}")

                ctx = await build_context(session, mf, prefs, need)
                try:
                    results = await fetch_subtitles(
                        client, ctx, enabled=prefs.sources_for(ctx.media_type), on_status=_say)
                    saved = [r.path for r in results]
                except QuotaExceeded as qe:
                    quota_err = qe
                    summary["quota"] = True
                    break
                except AuthRejected:
                    # Dead key fails every file the same way — stop, and make
                    # the summary say exactly what to fix.
                    summary["aborted"] = True
                    summary.setdefault("hints", []).append(
                        "Your OpenSubtitles API key was rejected — replace it in "
                        "Settings → Subtitles (real keys are 32 characters, from "
                        "opensubtitles.com → API consumers)."
                    )
                    break
                except Exception as e:
                    logger.warning(f"backfill: {mf.file_path} failed (non-fatal): {e!r}")
                    saved = []
                    # Rate-limit backoff (non-OS providers raise generic HTTP
                    # errors): consecutive 429s used to hammer a throttling
                    # provider once per remaining file. Slow down, then stop.
                    if "429" in str(e):
                        _throttle_hits = summary.get("throttle_hits", 0) + 1
                        summary["throttle_hits"] = _throttle_hits
                        if _throttle_hits >= 5:
                            summary["aborted"] = True
                            summary.setdefault("hints", []).append(
                                "A subtitle provider is rate-limiting — the sweep "
                                "stopped early; re-run it later.")
                            break
                        await asyncio.sleep(min(60.0, 10.0 * _throttle_hits))

                if saved:
                    summary["saved"] += len(saved)
                    summary.setdefault("scores", []).extend(r.score for r in results)
                    await _record_sidecars(session, mf.id, saved)
                    await _store.record_results(session, mf.id, label, results)
                    # If any result came from a season pack, harvest the rest of
                    # the season from that one download — later episodes in this
                    # sweep then find their sidecar already on disk and skip.
                    for r in results:
                        if r.ref:
                            extra = await harvest_from_cached_pack(
                                session, mf, r.provider, r.ref, r.language,
                                client=net.shared_client())
                            if extra:
                                summary["saved"] += extra
                else:
                    # Nothing saved — but a source may have SKIPPED because the
                    # sidecar is already on disk (parsed_data just hadn't caught
                    # up). Re-check the disk: all needed langs present → it's
                    # "already covered", not a genuine miss. This is why a second
                    # click / a series-wide sweep over already-fetched episodes
                    # used to report a misleading "not found".
                    from kira.subtitles._common import has_sidecar
                    present = [l for l in need if has_sidecar(mf.file_path, l)]
                    if len(present) == len(need):
                        summary["covered"] += 1
                    else:
                        summary["not_found"] += 1
                    if present:
                        # Record so the chip clears + future runs skip cleanly.
                        await _record_langs(session, mf.id, present)
                summary["files"] += 1
                done += 1
                activity.progress(SUBTITLE_BACKFILL_JOB, done, total)
    except Exception as e:
        logger.warning(f"backfill: aborted (non-fatal): {e!r}")
    finally:
        if started:
            # Final pill state — THE completion feedback. ok=True flashes a
            # green summary; ok=False pins a red card with the explanation
            # until dismissed. A failure on file 1 (rejected key) ends in
            # under a poll interval, so this lingering state is the only
            # way the user ever sees it live.
            ok = not summary["quota"] and (
                summary["saved"] > 0 or not summary.get("hints")
            )
            activity.end(SUBTITLE_BACKFILL_JOB, ok=ok, detail=_pill_detail(summary, quota_err))

    await _notify_summary(summary, quota_err)
    return summary


def _pill_detail(summary: dict, quota_err: QuotaExceeded | None) -> str:
    """One short line for the pill's final state. The bell keeps the full
    bulleted version; this is the at-a-glance outcome."""
    parts: list[str] = []
    if summary["saved"]:
        scores = summary.get("scores") or []
        avg = f" (avg {sum(scores) // len(scores)}% match)" if scores else ""
        parts.append(f"saved {summary['saved']} subtitle file(s){avg}")
    if summary["not_found"]:
        parts.append(f"{summary['not_found']} not found")
    if summary["covered"]:
        parts.append(f"{summary['covered']} already covered")
    line = " · ".join(parts) or "nothing fetched"
    if summary["quota"]:
        line += " — OpenSubtitles quota reached"
        if quota_err is not None and quota_err.reset_hint:
            line += f", resets {quota_err.reset_hint}"
    elif (summary["not_found"] or summary.get("aborted")) and summary.get("hints"):
        # Hints only matter when something was genuinely MISSED or the run was
        # cut short — not when the files turned out already-covered. First hint
        # inline; the bell has the rest.
        line += f" — {summary['hints'][0]}"
        if len(summary["hints"]) > 1:
            line += f" (+{len(summary['hints']) - 1} more in notifications)"
    return line


async def _record_sidecars(session, file_id: int, saved: list[str]) -> None:
    """Merge the just-saved sidecar languages into the file's parsed_data so the
    coverage chip flips without waiting for a rescan."""
    await _record_langs(session, file_id, _langs_from_saved(saved))


async def _record_langs(session, file_id: int, langs: list[str]) -> None:
    """Merge `langs` (2-letter) into the file's parsed_data.sub_sidecars."""
    if not langs:
        return
    try:
        mf = await session.get(MediaFile, file_id)
        if mf is None or not mf.parsed_data:
            return
        parsed = ParsedFile(**mf.parsed_data)
        existing = list(parsed.sub_sidecars or [])
        for lang in langs:
            if lang not in existing:
                existing.append(lang)
        parsed.sub_sidecars = existing
        mf.parsed_data = parsed.to_dict()
        await session.commit()
    except Exception as e:
        logger.warning(f"backfill: sidecar record failed for {file_id} (non-fatal): {e!r}")
        try:
            await session.rollback()
        except Exception:
            pass


async def _notify(kind: str, title: str, body: str) -> None:
    try:
        async with SessionLocal() as session:
            session.add(Notification(kind=kind, title=title, body=body))
            await session.commit()
    except Exception as e:
        logger.warning(f"backfill: notify failed (non-fatal): {e!r}")


async def _notify_summary(summary: dict, quota_err: QuotaExceeded | None) -> None:
    """Durable completion record. Stays quiet when there was nothing to do —
    but an early stop (quota, rejected key) MUST report even at 0 files."""
    if summary["files"] == 0 and not summary["quota"] and not summary.get("hints"):
        return
    parts: list[str] = []
    if summary["saved"]:
        parts.append(f"saved {summary['saved']} subtitle file(s)")
    if summary["not_found"]:
        parts.append(f"{summary['not_found']} not found")
    if summary["covered"]:
        parts.append(f"{summary['covered']} already covered")
    body = "; ".join(parts) or ("stopped before fetching" if summary.get("hints") else "nothing to fetch")
    kind = "success" if summary["saved"] else "info"
    if summary["quota"]:
        kind = "warning"
        hint = ""
        if quota_err is not None:
            if quota_err.remaining is not None:
                hint += f" {quota_err.remaining} downloads remaining."
            if quota_err.reset_hint:
                hint += f" Resets {quota_err.reset_hint}."
        body += f". OpenSubtitles quota reached — stopped early.{hint}"
    # Explain the silent gaps (no ffmpeg, no OS login) only when something was
    # genuinely MISSED — "27 not found" with no why reads as a failure. When
    # the files were merely already-covered, there's nothing to fix, so stay
    # quiet. One bullet per issue (the bell renders newlines).
    if (summary["not_found"] or summary.get("aborted")) and summary.get("hints"):
        kind = "warning"
        body += "\n\nTo fix:\n" + "\n".join(f"• {h}" for h in summary["hints"])
    await _notify(kind, "Subtitle fetch complete", body)
