"""Write a pack-sourced ``Match`` row (and fire its subtitles).

Two entry points, both used from ``api/scans.py``:

  • ``try_pack_match`` — the FALLBACK rescue. Called at the single point a file
    is about to be stamped ``no_match``. Tries every enabled binding; the first
    pack that gates + claims the file wins. This is the isolation guarantee — a
    matched file never reaches here.

  • ``try_pack_override`` — the OVERRIDE pre-pass. Called before provider
    matching for files an ``override`` pack (which is folder-scoped by rule)
    claims, so the pack can win over a *wrong* provider match. Off by default.

Both produce identical rows; only WHEN they run differs.
"""
from __future__ import annotations

import logging

from kira.api.match_cleanup import detach_and_delete_matches
from kira.models import Match, MediaFile
from kira.packs import PACK_PROVIDER
from kira.packs import loader as _loader
from kira.packs import resolver as _resolver
from kira.packs.schema import Pack, PackBinding, PackEpisode, url_hash
from kira.parser.parser import ParsedFile

logger = logging.getLogger("kira.packs.apply")


def _series_group_id(pack: Pack, binding: PackBinding) -> str:
    # md5(url) disambiguator → two packs sharing an id (a fork) stay separate cards.
    return f"pack:{pack.id}:{url_hash(binding.url)}"


def _parsed_of(mf: MediaFile) -> ParsedFile | None:
    if not mf.parsed_data:
        return None
    try:
        return ParsedFile(**mf.parsed_data)
    except Exception:
        return None


async def _find_claim(
    session, parsed: ParsedFile, file_path: str, bindings: list[PackBinding],
) -> tuple[Pack, PackBinding, PackEpisode] | None:
    """First (pack, binding, episode) that gates + claims this file, else None."""
    for binding in bindings:
        if not binding.enabled:
            continue
        pack = await _loader.get_pack(binding)
        if pack is None:
            continue
        if not _resolver.gate(parsed, file_path, pack, binding):
            continue
        ep = _resolver.claim(parsed, file_path, pack)
        if ep is not None:
            return pack, binding, ep
    return None


async def _write_pack_match(
    session, mf: MediaFile, pack: Pack, binding: PackBinding, ep: PackEpisode,
) -> None:
    """Replace mf's matches with a single authoritative pack match, and queue its
    subtitles (background, own session) when the binding opts in."""
    await detach_and_delete_matches(session, media_file_id=mf.id)
    mtype = "movie" if pack.media_type == "movie" else "tv_episode"
    # Per-arc cover: a season-specific poster (Jellyfin seasonNN-poster.png) wins
    # over the single show poster, so each One Pace arc carries its own art.
    poster_url = (pack.show.season_posters.get(str(ep.season)) if mtype == "tv_episode" else None) or pack.show.poster_url
    session.add(Match(
        media_file_id=mf.id,
        provider=PACK_PROVIDER,
        provider_id=f"{pack.id}:{ep.season}:{ep.episode}",
        series_group_id=_series_group_id(pack, binding),
        match_type=mtype,
        confidence=1.0,
        title=pack.show.title,
        year=pack.show.year,
        series_name=pack.show.title if mtype == "tv_episode" else None,
        season_number=ep.season if mtype == "tv_episode" else None,
        episode_number=ep.episode if mtype == "tv_episode" else None,
        episode_title=ep.title,
        poster_url=poster_url,
        overview=ep.overview or pack.show.overview,
        is_selected=True,
        is_manual=False,
    ))

    if binding.subtitles and ep.subs:
        try:
            from kira.tasks import spawn_tracked
            from kira.packs.subs import fetch_pack_subs_bg

            spawn_tracked(
                fetch_pack_subs_bg(
                    mf.id, mf.file_path, [s.model_dump() for s in ep.subs], pack.show.title,
                ),
                label=f"pack-subs:{pack.id}",
            )
        except Exception as e:
            logger.warning("packs: could not queue subtitle fetch: %r", e)


async def try_pack_match(session, fid: int, mf: MediaFile | None = None) -> bool:
    """FALLBACK: rescue a file the providers left unmatched. Returns True iff a
    pack claimed it and a Match row was written."""
    if mf is None:
        mf = await session.get(MediaFile, fid)
    if mf is None:
        return False
    parsed = _parsed_of(mf)
    if parsed is None:
        return False
    bindings = await _loader.load_bindings(session)
    if not bindings:
        return False
    found = await _find_claim(session, parsed, mf.file_path, bindings)
    if found is None:
        return False
    pack, binding, ep = found
    await _write_pack_match(session, mf, pack, binding, ep)
    logger.info("packs: rescued file %s as %s S%sE%s via pack %s",
                fid, pack.show.title, ep.season, ep.episode, pack.id)
    return True


async def try_pack_override(session, fid: int, mf: MediaFile | None = None) -> bool:
    """OVERRIDE pre-pass: let a folder-scoped override pack win over the
    providers. Only ``authority="override"`` bindings are considered (and those
    are guaranteed non-empty scope by the schema). Returns True iff it claimed."""
    if mf is None:
        mf = await session.get(MediaFile, fid)
    if mf is None:
        return False
    parsed = _parsed_of(mf)
    if parsed is None:
        return False
    bindings = [b for b in await _loader.load_bindings(session)
                if b.enabled and b.authority == "override"]
    if not bindings:
        return False
    found = await _find_claim(session, parsed, mf.file_path, bindings)
    if found is None:
        return False
    pack, binding, ep = found
    await _write_pack_match(session, mf, pack, binding, ep)
    logger.info("packs: OVERRODE file %s as %s S%sE%s via pack %s",
                fid, pack.show.title, ep.season, ep.episode, pack.id)
    return True


async def any_override_bindings(session) -> bool:
    """Cheap guard so the scan's override pre-pass is skipped entirely when no
    override pack is installed (the common case)."""
    return any(
        b.enabled and b.authority == "override"
        for b in await _loader.load_bindings(session)
    )
