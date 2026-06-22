"""A FIRST-TIME bulk pin (the APPEND branch of bulk_select_manual_match, where
no existing Match row can be commandeered) now stamps the PROVIDER's canonical
season — the same `resolve_canonical_season` call _rematch_one (matches.py L563)
and the scan path (scans.py L1175) use — instead of the raw parsed/sibling
season. Without it a multi-season show pinned via the bulk path could carry a
season-LOCAL folder index that then drove a wrong rename SxxExx.

The cour-routing helper is monkeypatched to None so these stay pure in-memory;
the AniDB case additionally pins its mapping data so it never touches the network.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from kira.api.matches import BulkSelectManualPayload, bulk_select_manual_match
from kira.models import Base, Match, MediaFile


async def _mem():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _mf(path, parsed, media_type="tv"):
    return MediaFile(file_path=path, media_type=media_type, parsed_data=parsed, status="no_match")


def _patch_no_cour(monkeypatch):
    # No multi-cour routing table → final_provider_id == payload.provider_id.
    # Patch the source module since bulk_select_manual_match imports it locally.
    import kira.matcher.cour_routing as cr
    async def _none(*a, **k):
        return None
    monkeypatch.setattr(cr, "build_cour_routing_table", _none)


async def test_bulk_append_first_time_pin_canonicalizes_season(monkeypatch):
    """Multi-season TVDB show, first-time bulk pin, NO existing row → APPEND.

    The file parsed as season-LOCAL 1 (a 'Season 01' box-set subfolder) but the
    provider's canonical season for this pin is 4. The APPEND row must carry the
    canonical 4, proving it now routes through resolve_canonical_season — before
    the fix it stamped the raw parsed 1. The resolver is stubbed (for TVDB the
    real one is a passthrough, so a stub is the only way to assert the wiring)
    and also records its args to confirm the ROUTED provider_id is consulted.
    """
    _patch_no_cour(monkeypatch)

    import kira.api.matches as matches_mod
    seen: dict = {}

    async def _canon(provider, provider_id, parsed_season, episode=None):
        seen["args"] = (provider, provider_id, parsed_season)
        return 4  # the provider's canonical season, != the parsed-local 1

    monkeypatch.setattr(matches_mod, "resolve_canonical_season", _canon)

    Session = await _mem()
    async with Session() as s:
        mf = _mf(r"Z:\media\tv\Show\Season 01\Show - S01E05.mkv",
                 {"episode": 5, "season": 1})
        s.add(mf)
        await s.flush()
        # No Match rows on the file → the truly-new-candidate APPEND branch.
        await s.commit()

        await bulk_select_manual_match(
            BulkSelectManualPayload(provider="tvdb", provider_id="555",
                                    media_type="tv", title="Show", file_ids=[mf.id]),
            s,
        )
        m = await s.scalar(select(Match).where(
            Match.media_file_id == mf.id, Match.is_selected.is_(True)))
        assert m is not None
        assert m.provider_id == "555"          # APPEND wrote the show pin
        assert m.season_number == 4            # canonical — NOT the parsed-local 1
        assert m.episode_number == 5           # episode routing untouched
        assert m.is_manual is True
        # Resolver consulted with (provider, FINAL routed provider_id, parsed season).
        assert seen["args"] == ("tvdb", "555", 1)


async def test_bulk_append_anidb_flat_umbrella_unifies_season_1(monkeypatch):
    """Delicate-path guard: a first-time bulk pin of a flat-umbrella anime (One
    Piece, AID 69) must canonicalize to Season 1 — the cour case that is
    season-1-by-design — via the REAL resolver, NOT the parsed 'S23'. Proves the
    canonicalization doesn't disturb the AniDB cour/umbrella behavior.
    """
    _patch_no_cour(monkeypatch)

    # Pin the mapping data so resolve_canonical_season (and the bulk path's own
    # flat-umbrella probe) stay offline. 69 = One Piece (seasonless umbrella);
    # its only mapped sibling 411 is a movie/special (season 0) → pins Season 1.
    from kira.providers.anime_mappings import AnimeMappings
    seasons = {69: None, 411: 0}

    async def _season(aid):
        return seasons.get(int(aid))

    async def _tvdb(aid):
        return 81797  # One Piece

    async def _aids(tvdb_id):
        return [69, 411]

    monkeypatch.setattr(AnimeMappings, "tvdb_season", _season)
    monkeypatch.setattr(AnimeMappings, "tvdb_id", _tvdb)
    monkeypatch.setattr(AnimeMappings, "aids_by_tvdb", _aids)

    # The umbrella branch fetches the pick's episode list to build abs↔local;
    # stub it so the test never reaches AniDB. Empty map → no episode remap.
    import kira.api.scans as scans_mod

    async def _no_eps(*a, **k):
        return []

    monkeypatch.setattr(scans_mod, "_fetch_episodes_for_match", _no_eps)

    Session = await _mem()
    async with Session() as s:
        mf = _mf(r"Z:\media\anime\One Piece\Season 23\One Piece - S23E1165.mkv",
                 {"episode": 1165, "season": 23}, media_type="anime")
        s.add(mf)
        await s.flush()
        await s.commit()  # no existing Match → APPEND

        await bulk_select_manual_match(
            BulkSelectManualPayload(provider="anidb", provider_id="69",
                                    media_type="anime", title="One Piece",
                                    file_ids=[mf.id]),
            s,
        )
        m = await s.scalar(select(Match).where(
            Match.media_file_id == mf.id, Match.is_selected.is_(True)))
        assert m is not None
        assert m.provider_id == "69"           # identity preserved (not routed away)
        assert m.season_number == 1            # flat-umbrella canonical — NOT parsed 23
        assert m.episode_number == 1165        # absolute episode preserved
        assert m.is_manual is True
