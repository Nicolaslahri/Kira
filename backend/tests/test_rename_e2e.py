"""Real end-to-end `perform_rename`: actual temp files + a real SQLite round-trip.

The rest of the suite only SPIES on perform_rename (test_auto_rename_execute) or
checks route binding + an empty batch (test_rename_route) — nothing drives a real
rename through the pipeline. This is the behavioral safety net under the
rename-hardening pass (and the prerequisite for safely extracting _rename_one_file):
it proves a genuine move/copy relocates the video, records the RenameHistory video
row, drags the `.srt` sidecar along under a parent_id child row, and writes the NFO.

Only `media_type` config + the DB are real; no network (artwork download stays off).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api.history import undo_entry
from kira.api.rename import RenameRequest, perform_rename, reconcile_pending_renames
from kira.models import Match, MediaFile, RenameHistory, RenameIntent, Setting
from kira.parser import parse_filename

_PD = {"original_filename": "x.mkv", "media_type": "movie", "title": "X"}

STEM = "The.Matrix.1999.1080p.BluRay.x264"


async def _fresh_db(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'kira_rename.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)  # post-rename hooks open this
    await db.init_db()
    return sm


async def _setup(tmp_path, monkeypatch, *, write_nfo=True):
    sm = await _fresh_db(tmp_path, monkeypatch)

    # Real files: a movie + a matching subtitle sidecar, inside a `movies`
    # type-folder so the in-place target computation has somewhere to anchor.
    media = tmp_path / "movies"
    media.mkdir()
    src = media / f"{STEM}.mkv"
    srt = media / f"{STEM}.en.srt"
    src.write_bytes(b"video-bytes")
    srt.write_bytes(b"subtitle-bytes")

    pd = parse_filename(f"{STEM}.mkv").to_dict()
    assert pd["media_type"] == "movie"

    async with sm() as s:
        if write_nfo:
            s.add(Setting(key="naming.write_nfo", value=True))
        mf = MediaFile(file_path=str(src), parsed_data=pd, media_type="movie", status="matched")
        s.add(mf)
        await s.flush()
        s.add(Match(
            media_file_id=mf.id, provider="tmdb", provider_id="603", match_type="movie",
            confidence=0.99, title="The Matrix", year=1999, is_selected=True,
        ))
        await s.commit()
        fid = mf.id
    return sm, fid, src, srt


async def _run(sm, req):
    """Drive perform_rename on a session that's properly closed afterward (the
    post-rename hooks open their own SessionLocal, so this one is just the batch).

    The post-rename network tail (subtitle fetch → media-server refresh → Sonarr)
    now runs as a tracked background task, so drain it before returning — that's
    what makes hook effects (e.g. the Sonarr rescan spy) observable synchronously
    in tests, mirroring how shutdown awaits the same tasks."""
    from kira.tasks import drain_background_tasks
    async with sm() as s:
        res = await perform_rename(req, s)
    await drain_background_tasks()
    return res


async def _history(sm):
    async with sm() as s:
        return list(await s.scalars(select(RenameHistory)))


@pytest.mark.asyncio
async def test_move_relocates_video_sidecar_history_and_nfo(tmp_path, monkeypatch):
    sm, fid, src, srt = await _setup(tmp_path, monkeypatch)

    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move"))

    assert res.succeeded == 1 and res.failed == 0, res
    item = res.items[0]
    assert item.ok, item.error
    new = Path(item.new_path)
    assert new.exists(), "renamed video should be at the new path"
    assert not src.exists(), "move should have removed the source video"

    # NFO written beside the renamed movie.
    assert new.with_suffix(".nfo").exists(), "movie .nfo should be written next to the target"

    # Sidecar dragged along: old gone, new present beside the video.
    new_srt = new.with_name(new.stem + ".en.srt")
    assert new_srt.exists(), "subtitle should have moved alongside the video"
    assert not srt.exists(), "old subtitle should be gone after a move"

    rows = await _history(sm)
    parents = [r for r in rows if r.parent_id is None]
    children = [r for r in rows if r.parent_id is not None]
    assert len(parents) == 1 and len(children) == 1
    assert parents[0].old_path == str(src) and parents[0].new_path == str(new)
    assert parents[0].operation == "move"
    assert children[0].parent_id == parents[0].id
    assert children[0].new_path.endswith(".srt")


@pytest.mark.asyncio
async def test_re_submitting_same_rename_is_noop_no_duplicate_history(tmp_path, monkeypatch):
    # Repro of "approved one movie, see it twice in history": re-running the rename
    # after the file already sits at its target must be a no-op — no self-move and
    # no second src==dst history row.
    sm, fid, src, srt = await _setup(tmp_path, monkeypatch, write_nfo=False)

    res1 = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move"))
    assert res1.items[0].ok
    new1 = res1.items[0].new_path

    # Second submit — MediaFile.file_path is now the target.
    res2 = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move"))
    assert res2.items[0].ok
    assert res2.items[0].new_path == new1
    assert "Already at target" in (res2.items[0].error or "")

    parents = [r for r in await _history(sm) if r.parent_id is None]
    assert len(parents) == 1, f"a re-submit must not add a history row, got {len(parents)}"


async def _seed_cours(sm, tmp_path, cours):
    """Seed AniDB cour matches sharing a series_group_id. `cours` = [(aid, title, ep)]."""
    media = tmp_path / "anime" / "Bleach" / "Season 17"
    media.mkdir(parents=True, exist_ok=True)
    ids = []
    async with sm() as s:
        for aid, title, ep in cours:
            pd = {"original_filename": f"{title} - {ep:02d}.mkv", "media_type": "anime",
                  "title": "Bleach", "season": 17, "episode": ep}
            src = media / f"bleach {aid} e{ep:02d}.mkv"
            src.write_bytes(b"v")
            mf = MediaFile(file_path=str(src), parsed_data=pd, media_type="anime", status="matched")
            s.add(mf)
            await s.flush()
            s.add(Match(media_file_id=mf.id, provider="anidb", provider_id=str(aid),
                        match_type="tv_episode", series_group_id="anidb:2369", confidence=0.95,
                        title=title, season_number=17, episode_number=ep,
                        is_selected=True, is_manual=False))
            ids.append(mf.id)
        await s.commit()
    return ids


@pytest.mark.asyncio
async def test_anime_cours_unify_under_one_show_keeping_tvdb_season(tmp_path, monkeypatch):
    # AniDB splits a franchise into per-cour AIDs with distinct titles; they collapse
    # to the EARLIEST member's title (one show folder), and CRUCIALLY keep their real
    # TVDB season — NOT renumbered to fake per-cour seasons (the AoT=7-seasons bug).
    sm = await _fresh_db(tmp_path, monkeypatch)
    # Distinct episodes so there's no collision even without the cour offset (which
    # needs Fribb data this fresh DB doesn't have).
    ids = await _seed_cours(sm, tmp_path, [
        (15449, "Bleach: Thousand-Year Blood War", 1),
        (17765, "Bleach: Thousand-Year Blood War - The Separation", 2),
        (18220, "Bleach: Thousand-Year Blood War - The Conflict", 3),
    ])

    res = await _run(sm, RenameRequest(file_ids=ids, profile="Plex", op="move", dry_run=True))
    paths = [i.new_path for i in res.items]

    for p in paths:
        # One unified folder, earliest cour's title — never the per-cour suffixes.
        assert "Bleach - Thousand-Year Blood War" in p
        assert "The Separation" not in p and "The Conflict" not in p
        # The TVDB season (17) is KEPT, not rank-renumbered to Season 01/02/03.
        assert "Season 17" in p


@pytest.mark.asyncio
async def test_unified_franchise_uses_one_year_for_year_bearing_templates(tmp_path, monkeypatch):
    # Jellyfin's anime template is "{{n}} ({{y}})/..." — each cour carries its own
    # premiere year, so a unified TITLE alone still fragments the franchise into
    # "Show (2022)" / "Show (2023)" folders. The earliest cour supplies BOTH.
    sm = await _fresh_db(tmp_path, monkeypatch)
    media = tmp_path / "anime" / "Bleach" / "Season 17"
    media.mkdir(parents=True)
    ids = []
    async with sm() as s:
        for aid, title, year, ep in [
            (15449, "Bleach: Thousand-Year Blood War", 2022, 1),
            (17765, "Bleach: Thousand-Year Blood War - The Separation", 2023, 2),
        ]:
            pd = {"original_filename": f"x{ep}.mkv", "media_type": "anime",
                  "title": "Bleach", "season": 17, "episode": ep}
            src = media / f"b{aid}e{ep}.mkv"
            src.write_bytes(b"v")
            mf = MediaFile(file_path=str(src), parsed_data=pd, media_type="anime", status="matched")
            s.add(mf)
            await s.flush()
            s.add(Match(media_file_id=mf.id, provider="anidb", provider_id=str(aid),
                        match_type="tv_episode", series_group_id="anidb:2369", confidence=0.95,
                        title=title, year=year, season_number=17, episode_number=ep,
                        is_selected=True, is_manual=False))
            ids.append(mf.id)
        await s.commit()

    res = await _run(sm, RenameRequest(file_ids=ids, profile="Jellyfin", op="move", dry_run=True))
    for i in res.items:
        assert "(2022)" in i.new_path, i.new_path     # earliest cour's year everywhere
        assert "(2023)" not in i.new_path, i.new_path


@pytest.mark.asyncio
async def test_yearless_franchise_never_splits_on_per_file_filename_year(tmp_path, monkeypatch):
    # Regression (Gachiakuta): every file matched the SAME AniDB entry with
    # year=None, but some FILENAMES carried "2025" and others didn't. The folder
    # year fell back to each file's own parsed year, so a year-bearing template
    # split ONE show into "Gachiakuta (2025)" + "Gachiakuta". The group year must
    # unify to a SINGLE value (None here) so the show can never fragment on a
    # per-file filename year.
    sm = await _fresh_db(tmp_path, monkeypatch)
    media = tmp_path / "anime" / "Gachiakuta" / "Season 1"
    media.mkdir(parents=True)
    ids = []
    async with sm() as s:
        for ep, pyear, fname in [
            (1, 2025, "Gachiakuta.S01E01.2025.1080p.WEB-DL.mp4"),   # filename has a year
            (2, None, "[Erai-raws] Gachiakuta-02 [1080p].mkv"),     # filename has none
        ]:
            pd = {"original_filename": fname, "media_type": "anime",
                  "title": "Gachiakuta", "season": 1, "episode": ep, "year": pyear}
            src = media / fname
            src.write_bytes(b"v")
            mf = MediaFile(file_path=str(src), parsed_data=pd, media_type="anime", status="matched")
            s.add(mf)
            await s.flush()
            # Same group, SAME entry, year=None — exactly the live Gachiakuta rows.
            s.add(Match(media_file_id=mf.id, provider="anidb", provider_id="18686",
                        match_type="tv_episode", series_group_id="anidb:18686", confidence=1.0,
                        title="Gachiakuta", year=None, season_number=1, episode_number=ep,
                        is_selected=True, is_manual=False))
            ids.append(mf.id)
        await s.commit()

    res = await _run(sm, RenameRequest(file_ids=ids, profile="Jellyfin", op="move", dry_run=True))
    assert len(res.items) == 2 and all(i.ok for i in res.items), res
    # ONE show folder for both files, and the per-file "2025" must not leak in.
    show_folders = {Path(i.new_path).parent.parent.name for i in res.items}
    assert len(show_folders) == 1, f"show split across folders: {show_folders}"
    for i in res.items:
        assert "(2025)" not in i.new_path, i.new_path


@pytest.mark.asyncio
async def test_anime_show_folder_uses_franchise_root_not_present_cour(tmp_path, monkeypatch):
    # Regression (Haikyu): only a LATER season is in the library — Haikyu S2
    # (AID 10981, AniDB title "Haikyu!! 2nd Season"), whose franchise root is
    # AID 10145 ("Haikyu!!"). The show folder + file prefix must be the ROOT
    # title "Haikyu!!" (+ Season 2), NOT the cour's own qualified title — else
    # you get "Haikyu!! 2nd Season/Season 2/Haikyu!! 2nd Season - S02E24…".
    # This case = the real production failure: the title dump is NOT loaded
    # (restart → re-rename runs no AniDB op), so the root lookup misses and the
    # offline season-qualifier STRIP must still fold it to "Haikyu!!".
    from kira.providers.anidb import AniDBProvider
    monkeypatch.setattr(AniDBProvider, "_titles", {})
    sm = await _fresh_db(tmp_path, monkeypatch)
    media = tmp_path / "anime" / "Haikyu" / "Season 2"
    media.mkdir(parents=True)
    src = media / "Haikyuu!! S02E24.mkv"
    src.write_bytes(b"v")
    pd = {"original_filename": src.name, "media_type": "anime",
          "title": "Haikyuu!!", "season": 2, "episode": 24}
    async with sm() as s:
        mf = MediaFile(file_path=str(src), parsed_data=pd, media_type="anime", status="matched")
        s.add(mf)
        await s.flush()
        s.add(Match(media_file_id=mf.id, provider="anidb", provider_id="10981",
                    match_type="tv_episode", series_group_id="anidb:10145", confidence=1.0,
                    title="Haikyu!! 2nd Season", season_number=2, episode_number=24,
                    is_selected=True, is_manual=False))
        await s.commit()
        fid = mf.id
    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move", dry_run=True))
    p = res.items[0].new_path
    assert "2nd Season" not in p, p                 # the per-cour qualifier must be gone
    assert "Haikyu!!" in p, p                        # root franchise title used
    assert ("Season 2" in p or "Season 02" in p), p  # season still correct


@pytest.mark.asyncio
async def test_anime_show_folder_resolves_root_title_from_loaded_dump(tmp_path, monkeypatch):
    # Same as above but the title dump IS loaded → the franchise ROOT aid's title
    # is used directly (handles subtitle sequels too, which the strip can't).
    from kira.providers.anidb import AniDBProvider
    monkeypatch.setattr(AniDBProvider, "_titles", {9999: [("official", "en", "Mushishi")]})
    sm = await _fresh_db(tmp_path, monkeypatch)
    media = tmp_path / "anime" / "Mushishi" / "S2"
    media.mkdir(parents=True)
    src = media / "Mushishi Zoku Shou E01.mkv"
    src.write_bytes(b"v")
    pd = {"original_filename": src.name, "media_type": "anime", "title": "Mushishi Zoku Shou", "season": 2, "episode": 1}
    async with sm() as s:
        mf = MediaFile(file_path=str(src), parsed_data=pd, media_type="anime", status="matched")
        s.add(mf)
        await s.flush()
        # AniDB names this sequel "Mushishi Zoku Shou" (a SUBTITLE, not an ordinal)
        # — only the root-aid dump lookup can fold it to "Mushishi".
        s.add(Match(media_file_id=mf.id, provider="anidb", provider_id="10000",
                    match_type="tv_episode", series_group_id="anidb:9999", confidence=1.0,
                    title="Mushishi Zoku Shou", season_number=2, episode_number=1,
                    is_selected=True, is_manual=False))
        await s.commit()
        fid = mf.id
    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move", dry_run=True))
    p = res.items[0].new_path
    assert "Zoku Shou" not in p, p   # the subtitle sequel name must be gone
    assert "Mushishi" in p, p        # folded to the root title from the dump


@pytest.mark.asyncio
async def test_anime_cour_episode_offset_applied(tmp_path, monkeypatch):
    # Two cours sharing TVDB season 17, both AniDB-local E01. The static cour-routing
    # table (mocked here) offsets the 2nd cour by the 1st's official length so they
    # run continuously (E01 vs E14) instead of colliding.
    sm = await _fresh_db(tmp_path, monkeypatch)
    ids = await _seed_cours(sm, tmp_path, [
        (15449, "Bleach: Thousand-Year Blood War", 1),               # cour 1, local E01
        (17765, "Bleach: Thousand-Year Blood War - The Separation", 1),  # cour 2, local E01
    ])

    async def _fake_table(provider, top_id, season, registry=None):
        # (start, end, cour_aid, offset) — cour 1 = 13 eps, cour 2 offset +13.
        return [(1, 13, 15449, 0), (14, 26, 17765, 13)]
    monkeypatch.setattr("kira.matcher.cour_routing.build_cour_routing_table", _fake_table)

    res = await _run(sm, RenameRequest(file_ids=ids, profile="Plex", op="move", dry_run=True))
    by_fid = {i.file_id: i.new_path for i in res.items}

    assert "S17E01" in by_fid[ids[0]], by_fid[ids[0]]   # cour 1 unchanged
    assert "S17E14" in by_fid[ids[1]], by_fid[ids[1]]   # cour 2 local E01 -> continuous E14
    for p in by_fid.values():
        assert "Bleach - Thousand-Year Blood War" in p


@pytest.mark.asyncio
async def test_rename_uses_match_episode_not_lying_filename_number(tmp_path, monkeypatch):
    # A rescued/arbitrated file: the FILENAME says E81 (old franchise-continuous
    # numbering) but the Match knows the truth — cour 16177 (AoT Final Season
    # Part 2), cour-LOCAL episode 6 ("Thaw"). The seasonal render must output
    # en + the cour's in-season offset (6+16 → S04E22) — NEVER parsed.episode +
    # offset (81+16 → S04E97, garbage).
    sm = await _fresh_db(tmp_path, monkeypatch)
    media = tmp_path / "anime" / "Attack on Titan" / "Season 06"
    media.mkdir(parents=True)
    src = media / "Attack on Titan - S06E81 - Thaw.mkv"
    src.write_bytes(b"v")
    pd = {"original_filename": src.name, "media_type": "anime",
          "title": "Attack on Titan", "season": 6, "episode": 81}
    async with sm() as s:
        mf = MediaFile(file_path=str(src), parsed_data=pd, media_type="anime", status="matched")
        s.add(mf)
        await s.flush()
        s.add(Match(media_file_id=mf.id, provider="anidb", provider_id="16177",
                    match_type="tv_episode", series_group_id="anidb:9541", confidence=0.95,
                    title="Attack on Titan The Final Season (2022)", season_number=4,
                    episode_number=6, episode_title="Thaw", is_selected=True))
        await s.commit()
        fid = mf.id

    async def _fake_table(provider, top_id, season, registry=None):
        # AoT TVDB S4: Part 1 (16 eps, offset 0) + Part 2 (12 eps, offset 16).
        return [(1, 16, 14977, 0), (17, 28, 16177, 16)]
    monkeypatch.setattr("kira.matcher.cour_routing.build_cour_routing_table", _fake_table)

    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move", dry_run=True))
    p = res.items[0].new_path
    assert "S04E22" in p, p          # en 6 + offset 16 — the truth
    assert "E97" not in p, p          # parsed 81 + 16 — the garbage
    assert "Thaw" in p, p


@pytest.mark.asyncio
async def test_season_zero_match_does_not_dump_to_specials(tmp_path, monkeypatch):
    # One Piece blunder: AniDB AID 69 has no season concept and the match comes
    # back season 0. The file parsed a real positive season (23) + episode 1160.
    # A season-0 match must NOT collapse a numbered episode into Specials/S00 —
    # trust the parsed season instead.
    sm = await _fresh_db(tmp_path, monkeypatch)
    # Isolate the season-0 GUARD: ScudLee mapping OFF so this asserts purely that
    # a season-0 match trusts the file's parsed positive season (23) instead of
    # collapsing into Specials. (The ScudLee seasonal mapping is covered by
    # test_seasonal_anime_maps_to_real_tvdb_season below.)
    async def _no_scud(*a, **k):
        return None
    monkeypatch.setattr("kira.providers.anime_lists.resolve_anidb_to_tvdb", _no_scud)
    media = tmp_path / "anime" / "One Piece" / "Season 23"
    media.mkdir(parents=True)
    src = media / "One Piece - S23E1160 - Episode 1160.mkv"
    src.write_bytes(b"v")
    pd = {"original_filename": src.name, "media_type": "anime",
          "title": "One Piece", "season": 23, "episode": 1160}
    async with sm() as s:
        mf = MediaFile(file_path=str(src), parsed_data=pd, media_type="anime", status="matched")
        s.add(mf)
        await s.flush()
        s.add(Match(media_file_id=mf.id, provider="anidb", provider_id="69",
                    match_type="tv_episode", confidence=1.0, title="One Piece",
                    season_number=0, episode_number=1160,
                    episode_title="An Encounter on a Snowfield", is_selected=True))
        await s.commit()
        fid = mf.id

    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move", dry_run=True))
    p = res.items[0].new_path
    assert "Specials" not in p, p
    assert "S00" not in p, p
    assert "S23E1160" in p, p


@pytest.mark.asyncio
async def test_seasonal_anime_maps_to_real_tvdb_season(tmp_path, monkeypatch):
    # Seasonal mode: an AniDB absolute episode is placed in its REAL TVDB
    # (season, episode) via ScudLee — One Piece anidb 1160 → S23E05 — so the
    # whole flat umbrella unifies in one season instead of scattering across
    # Season 01 / Season 23. The folder + filename use the SAME resolver that
    # stamps Match.season_number, so what's shown is what's written.
    sm = await _fresh_db(tmp_path, monkeypatch)

    async def _scud(aid, ep, *a, **k):
        return (23, 5) if int(aid) == 69 and int(ep) == 1160 else None
    monkeypatch.setattr("kira.providers.anime_lists.resolve_anidb_to_tvdb", _scud)

    media = tmp_path / "anime" / "One Piece" / "Season 01"
    media.mkdir(parents=True)
    src = media / "One Piece - S01E1160 - Episode 1160.mkv"
    src.write_bytes(b"v")
    pd = {"original_filename": src.name, "media_type": "anime",
          "title": "One Piece", "season": 1, "episode": 1160}
    async with sm() as s:
        mf = MediaFile(file_path=str(src), parsed_data=pd, media_type="anime", status="matched")
        s.add(mf)
        await s.flush()
        s.add(Match(media_file_id=mf.id, provider="anidb", provider_id="69",
                    match_type="tv_episode", confidence=1.0, title="One Piece",
                    season_number=23, episode_number=1160,
                    episode_title="An Encounter on a Snowfield", is_selected=True))
        await s.commit()
        fid = mf.id

    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move", dry_run=True))
    p = res.items[0].new_path
    assert "Season 23" in p, p
    assert "S23E05" in p, p          # TVDB season-local episode, NOT absolute 1160
    assert "S01E1160" not in p, p     # the scattered "Season 01" placement is gone


@pytest.mark.asyncio
async def test_episode_nfo_carries_cour_offset_number(tmp_path, monkeypatch):
    # The Jellyfin "two episode 1s" bug: the FILENAME got the cour offset
    # (Part 2 local E01 → S03E13) but the episode NFO was written from the raw
    # Match.episode_number — <episode>1</episode>. Jellyfin trusts the NFO over
    # the filename, so every multi-cour season displayed duplicate numbering.
    # The NFO must mirror the rendered filename exactly.
    sm = await _fresh_db(tmp_path, monkeypatch)
    media = tmp_path / "anime" / "Attack on Titan" / "Season 03"
    media.mkdir(parents=True)
    src = media / "Attack on Titan - S03E01 - The Town Where Everything Began.mkv"
    src.write_bytes(b"v")
    pd = {"original_filename": src.name, "media_type": "anime",
          "title": "Attack on Titan", "season": 3, "episode": 1}
    async with sm() as s:
        s.add(Setting(key="naming.write_nfo", value=True))
        mf = MediaFile(file_path=str(src), parsed_data=pd, media_type="anime", status="matched")
        s.add(mf)
        await s.flush()
        s.add(Match(media_file_id=mf.id, provider="anidb", provider_id="13759",
                    match_type="tv_episode", series_group_id="anidb:9541", confidence=0.95,
                    title="Attack on Titan Season 3 Part 2", season_number=3,
                    episode_number=1, episode_title="The Town Where Everything Began",
                    is_selected=True))
        await s.commit()
        fid = mf.id

    async def _fake_table(provider, top_id, season, registry=None):
        # AoT TVDB S3: Part 1 (12 eps, offset 0) + Part 2 (10 eps, offset 12).
        return [(1, 12, 13700, 0), (13, 22, 13759, 12)]
    monkeypatch.setattr("kira.matcher.cour_routing.build_cour_routing_table", _fake_table)

    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move"))
    assert res.items[0].ok, res.items[0].error
    target = Path(res.items[0].new_path)
    assert "S03E13" in target.name, target.name

    nfo = target.with_suffix(".nfo")
    assert nfo.exists(), "episode NFO should be written"
    content = nfo.read_text(encoding="utf-8")
    assert "<episode>13</episode>" in content, content
    assert "<episode>1</episode>" not in content, content
    assert "<season>3</season>" in content, content


@pytest.mark.asyncio
async def test_copy_keeps_source_and_records_history(tmp_path, monkeypatch):
    sm, fid, src, srt = await _setup(tmp_path, monkeypatch)

    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="copy"))

    assert res.succeeded == 1, res
    new = Path(res.items[0].new_path)
    assert new.exists() and src.exists(), "copy must leave the source in place"
    new_srt = new.with_name(new.stem + ".en.srt")
    assert new_srt.exists() and srt.exists(), "copy must leave the source subtitle in place"

    rows = await _history(sm)
    assert any(r.parent_id is None and r.operation == "copy" for r in rows)


@pytest.mark.asyncio
async def test_dry_run_touches_nothing(tmp_path, monkeypatch):
    sm, fid, src, srt = await _setup(tmp_path, monkeypatch)

    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move", dry_run=True))

    item = res.items[0]
    assert item.ok and item.new_path
    assert src.exists() and srt.exists(), "dry-run must not move anything"
    assert not Path(item.new_path).exists(), "dry-run must not create the target"
    assert await _history(sm) == [], "dry-run must not write history"
    # #6: the preview surfaces the side effects too — the sidecar that would move
    # and the NFO that would be written (write_nfo is on in _setup).
    assert item.sidecars and any(s.endswith(".srt") for s in item.sidecars)
    assert item.nfo and any(n.endswith(".nfo") for n in item.nfo)


@pytest.mark.asyncio
async def test_unselected_matches_pick_highest_confidence_not_list_order(tmp_path, monkeypatch):
    # #5: when nothing is is_selected, the highest-confidence match must win —
    # never relationship list-order [0], which could rename to the wrong title.
    sm = await _fresh_db(tmp_path, monkeypatch)
    media = tmp_path / "movies"
    media.mkdir()
    src = media / f"{STEM}.mkv"
    src.write_bytes(b"v")
    pd = parse_filename(f"{STEM}.mkv").to_dict()
    async with sm() as s:
        mf = MediaFile(file_path=str(src), parsed_data=pd, media_type="movie", status="matched")
        s.add(mf)
        await s.flush()
        # Inserted so [0] is the LOW-confidence (wrong) match.
        s.add_all([
            Match(media_file_id=mf.id, provider="tmdb", provider_id="1", match_type="movie",
                  confidence=0.50, title="Wrong Low", year=1999, is_selected=False),
            Match(media_file_id=mf.id, provider="tmdb", provider_id="2", match_type="movie",
                  confidence=0.70, title="Wrong Mid", year=1999, is_selected=False),
            Match(media_file_id=mf.id, provider="tmdb", provider_id="3", match_type="movie",
                  confidence=0.95, title="Correct High", year=1999, is_selected=False),
        ])
        await s.commit()
        fid = mf.id

    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move", dry_run=True))

    assert res.items[0].ok, res.items[0].error
    assert "Correct High" in res.items[0].new_path
    assert "Wrong" not in res.items[0].new_path


@pytest.mark.asyncio
async def test_created_assets_recorded_and_undo_deletes_them(tmp_path, monkeypatch):
    # #1: the NFO the rename wrote is RECORDED on the history row, and undo
    # deletes exactly that recorded path (authoritative) while restoring the video.
    sm, fid, src, srt = await _setup(tmp_path, monkeypatch)  # write_nfo on
    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move"))
    new = Path(res.items[0].new_path)
    nfo = new.with_suffix(".nfo")
    assert nfo.exists()

    rows = await _history(sm)
    parent = next(r for r in rows if r.parent_id is None)
    assert parent.created_assets and str(nfo) in parent.created_assets

    async with sm() as s:
        await undo_entry(parent.id, s)

    assert not nfo.exists(), "undo must delete the recorded NFO (no orphan)"
    assert src.exists() and not new.exists(), "undo restores the video"


@pytest.mark.asyncio
async def test_re_rename_to_new_target_sweeps_prior_assets(tmp_path, monkeypatch):
    # #1 forward sweep: re-renaming to a DIFFERENT target (no undo between) must
    # remove the artwork/NFO the PRIOR rename wrote under the old target's name.
    sm = await _fresh_db(tmp_path, monkeypatch)
    media = tmp_path / "movies"
    media.mkdir()
    src = media / f"{STEM}.mkv"
    src.write_bytes(b"v")
    pd = parse_filename(f"{STEM}.mkv").to_dict()
    async with sm() as s:
        s.add(Setting(key="naming.write_nfo", value=True))
        mf = MediaFile(file_path=str(src), parsed_data=pd, media_type="movie", status="matched")
        s.add(mf)
        await s.flush()
        s.add(Match(media_file_id=mf.id, provider="tmdb", provider_id="1", match_type="movie",
                    confidence=0.9, title="First Title", year=1999, is_selected=True))
        await s.commit()
        fid = mf.id

    res1 = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move"))
    b_nfo = Path(res1.items[0].new_path).with_suffix(".nfo")
    assert b_nfo.exists()

    # Re-point the match to a DIFFERENT title → different target, with NO undo.
    async with sm() as s:
        m = (await s.scalars(select(Match).where(Match.media_file_id == fid))).first()
        m.title = "Second Title"
        await s.commit()

    res2 = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move"))
    c = Path(res2.items[0].new_path)
    c_nfo = c.with_suffix(".nfo")
    assert "Second Title" in str(c)
    assert c.exists() and c_nfo.exists(), "new target + its NFO present"
    assert not b_nfo.exists(), "prior target's NFO should be swept on re-rename"


@pytest.mark.asyncio
async def test_untrackable_sidecar_is_not_moved(tmp_path, monkeypatch):
    # #2: if we can't get the parent history id (flush fails), the sidecar must
    # NOT be moved — moving it untracked would orphan it on undo. Patching the
    # instance's async flush() hits ONLY the explicit pre-sidecar flush (queries
    # + commit autoflush via the sync session), isolating exactly that branch.
    sm, fid, src, srt = await _setup(tmp_path, monkeypatch, write_nfo=False)

    async def _boom(*a, **k):
        raise RuntimeError("simulated flush failure")

    async with sm() as s:
        monkeypatch.setattr(s, "flush", _boom)
        res = await perform_rename(RenameRequest(file_ids=[fid], profile="Plex", op="move"), s)
    # Let the (harmless, unconfigured) background hook finish so it doesn't leak
    # a pending task into the next test — this path calls perform_rename directly.
    from kira.tasks import drain_background_tasks
    await drain_background_tasks()

    new = Path(res.items[0].new_path)
    assert new.exists() and not src.exists(), "the video itself still moves"
    assert srt.exists(), "untrackable sidecar must stay put (never moved without a history row)"
    assert not new.with_name(new.stem + ".en.srt").exists(), "sidecar must not appear at the target"

    rows = await _history(sm)
    assert rows and all(r.parent_id is None for r in rows), "no child sidecar rows when untrackable"


@pytest.mark.asyncio
async def test_duplicate_target_in_batch_fails_collider_without_clobber(tmp_path, monkeypatch):
    # #3: two files that render to the SAME target (same movie, same quality, in
    # different folders) must not silently overwrite each other. First claimant
    # wins; the second fails with a clear pointer and stays untouched.
    sm = await _fresh_db(tmp_path, monkeypatch)
    da = tmp_path / "movies" / "a"
    db_ = tmp_path / "movies" / "b"
    da.mkdir(parents=True)
    db_.mkdir(parents=True)
    a = da / "The.Matrix.1999.1080p.x264.mkv"
    b = db_ / "The.Matrix.1999.1080p.x264.mkv"
    a.write_bytes(b"aaa")
    b.write_bytes(b"bbb")

    ids = []
    async with sm() as s:
        for p in (a, b):
            pd = parse_filename(p.name).to_dict()
            mf = MediaFile(file_path=str(p), parsed_data=pd, media_type="movie", status="matched")
            s.add(mf)
            await s.flush()
            s.add(Match(media_file_id=mf.id, provider="tmdb", provider_id="603", match_type="movie",
                        confidence=0.9, title="The Matrix", year=1999, is_selected=True))
            ids.append(mf.id)
        await s.commit()

    res = await _run(sm, RenameRequest(file_ids=ids, profile="Plex", op="move"))

    oks = [i for i in res.items if i.ok]
    fails = [i for i in res.items if not i.ok]
    assert len(oks) == 1 and len(fails) == 1, res
    assert "Duplicate target" in (fails[0].error or "")
    # No data loss: the colliding file is still sitting at its source.
    assert Path(fails[0].old_path).exists(), "the colliding file must not be moved or clobbered"


# ── #4: pending-rename intent journal + reconcile ────────────────────────────

@pytest.mark.asyncio
async def test_reconcile_finalizes_a_move_that_landed(tmp_path, monkeypatch):
    # Crash AFTER the move, BEFORE the DB commit: dst on disk, src gone, DB still
    # points at src + an intent row survives. Reconcile finalizes the DB to match.
    sm = await _fresh_db(tmp_path, monkeypatch)
    src = tmp_path / "old.mkv"
    dst = tmp_path / "New Name (2020).mkv"
    dst.write_bytes(b"v")  # the move landed
    async with sm() as s:
        mf = MediaFile(file_path=str(src), parsed_data=_PD, media_type="movie", status="renaming")
        s.add(mf)
        await s.flush()
        s.add(RenameIntent(media_file_id=mf.id, src=str(src), dst=str(dst), operation="move"))
        await s.commit()
        fid = mf.id

    final, disc = await reconcile_pending_renames()

    assert (final, disc) == (1, 0)
    async with sm() as s:
        mf = await s.get(MediaFile, fid)
        assert mf.file_path == str(dst) and mf.status == "renamed", "DB now matches disk"
        hist = list(await s.scalars(select(RenameHistory).where(RenameHistory.new_path == str(dst))))
        assert len(hist) == 1, "a recovery history row is created (undoable)"
        assert list(await s.scalars(select(RenameIntent))) == [], "intent cleared"


@pytest.mark.asyncio
async def test_reconcile_discards_a_move_that_never_ran(tmp_path, monkeypatch):
    # Crash BEFORE the move (or it failed): src still on disk, dst absent. Nothing
    # to finalize — the DB already points at src; just drop the stale intent.
    sm = await _fresh_db(tmp_path, monkeypatch)
    src = tmp_path / "still_here.mkv"
    src.write_bytes(b"v")
    dst = tmp_path / "Target (2020).mkv"  # never created
    async with sm() as s:
        mf = MediaFile(file_path=str(src), parsed_data=_PD, media_type="movie", status="matched")
        s.add(mf)
        await s.flush()
        s.add(RenameIntent(media_file_id=mf.id, src=str(src), dst=str(dst), operation="move"))
        await s.commit()
        fid = mf.id

    final, disc = await reconcile_pending_renames()

    assert (final, disc) == (0, 1)
    async with sm() as s:
        mf = await s.get(MediaFile, fid)
        assert mf.file_path == str(src) and mf.status == "matched", "row untouched"
        assert list(await s.scalars(select(RenameIntent))) == [], "stale intent dropped"
        assert list(await s.scalars(select(RenameHistory))) == [], "no phantom history row"


@pytest.mark.asyncio
async def test_reconcile_does_not_duplicate_existing_history(tmp_path, monkeypatch):
    # If the move's history row already committed (crash AFTER history, in a later
    # step), finalize must not add a SECOND row for the same src→dst.
    sm = await _fresh_db(tmp_path, monkeypatch)
    src = tmp_path / "old.mkv"
    dst = tmp_path / "Dup (2020).mkv"
    dst.write_bytes(b"v")
    async with sm() as s:
        mf = MediaFile(file_path=str(src), parsed_data=_PD, media_type="movie", status="renaming")
        s.add(mf)
        await s.flush()
        s.add(RenameHistory(media_file_id=mf.id, old_path=str(src), new_path=str(dst), operation="move"))
        s.add(RenameIntent(media_file_id=mf.id, src=str(src), dst=str(dst), operation="move"))
        await s.commit()

    final, disc = await reconcile_pending_renames()

    assert final == 1
    async with sm() as s:
        hist = list(await s.scalars(select(RenameHistory).where(RenameHistory.new_path == str(dst))))
        assert len(hist) == 1, "must not duplicate the already-recorded history row"


@pytest.mark.asyncio
async def test_sonarr_rescan_hook_fires_for_renamed_episodes(tmp_path, monkeypatch):
    # After renaming episode files, Kira must tell Sonarr to rescan the series —
    # otherwise Sonarr's next disk scan sees the old paths gone, marks the files
    # deleted, and may re-download monitored episodes.
    sm = await _fresh_db(tmp_path, monkeypatch)
    media = tmp_path / "tv" / "Some Show" / "Season 01"
    media.mkdir(parents=True)
    src = media / "Some.Show.S01E01.720p.mkv"
    src.write_bytes(b"v")
    pd = {"original_filename": src.name, "media_type": "tv",
          "title": "Some Show", "season": 1, "episode": 1}
    async with sm() as s:
        s.add(Setting(key="integrations.sonarr.url", value="http://localhost:8989"))
        s.add(Setting(key="integrations.sonarr.api_key", value="k"))
        mf = MediaFile(file_path=str(src), parsed_data=pd, media_type="tv", status="matched")
        s.add(mf)
        await s.flush()
        s.add(Match(media_file_id=mf.id, provider="tvdb", provider_id="123456",
                    match_type="tv_episode", confidence=0.95, title="Some Show",
                    year=2020, season_number=1, episode_number=1, is_selected=True))
        await s.commit()
        fid = mf.id

    calls: list[int] = []

    async def spy(cfg, tvdb_id):
        calls.append(int(tvdb_id))
        return True
    monkeypatch.setattr("kira.integrations.sonarr.rescan_series_by_tvdb", spy)

    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move"))
    assert res.succeeded == 1, res
    assert calls == [123456], f"sonarr rescan should fire once for the series, got {calls}"


@pytest.mark.asyncio
async def test_sonarr_rescan_hook_silent_when_unconfigured(tmp_path, monkeypatch):
    # No Sonarr settings → the hook must skip without erroring the rename.
    sm, fid, src, srt = await _setup(tmp_path, monkeypatch, write_nfo=False)
    calls: list[int] = []

    async def spy(cfg, tvdb_id):
        calls.append(int(tvdb_id))
        return True
    monkeypatch.setattr("kira.integrations.sonarr.rescan_series_by_tvdb", spy)

    res = await _run(sm, RenameRequest(file_ids=[fid], profile="Plex", op="move"))
    assert res.succeeded == 1
    assert calls == [], "movie + unconfigured Sonarr must not trigger a rescan"


@pytest.mark.asyncio
async def test_post_rename_network_tail_is_backgrounded(tmp_path, monkeypatch):
    # The whole point of the speed fix: /rename returns the moment the files are
    # moved, and the network tail (notify → subtitle fetch → media-server refresh
    # → Sonarr) runs as a tracked BACKGROUND task. We prove it's deferred — not
    # run inline — by gating the hook's FIRST step (notify.fan_out) on an event:
    # while the gate is closed the hook can't reach the Sonarr call, yet
    # perform_rename has already returned a successful result. Releasing the gate
    # and draining then lets the tail finish (proving ordering still holds too).
    import asyncio
    from kira.tasks import drain_background_tasks

    sm = await _fresh_db(tmp_path, monkeypatch)
    media = tmp_path / "tv" / "Some Show" / "Season 01"
    media.mkdir(parents=True)
    src = media / "Some.Show.S01E01.720p.mkv"
    src.write_bytes(b"v")
    pd = {"original_filename": src.name, "media_type": "tv",
          "title": "Some Show", "season": 1, "episode": 1}
    async with sm() as s:
        s.add(Setting(key="integrations.sonarr.url", value="http://localhost:8989"))
        s.add(Setting(key="integrations.sonarr.api_key", value="k"))
        mf = MediaFile(file_path=str(src), parsed_data=pd, media_type="tv", status="matched")
        s.add(mf)
        await s.flush()
        s.add(Match(media_file_id=mf.id, provider="tvdb", provider_id="123456",
                    match_type="tv_episode", confidence=0.95, title="Some Show",
                    year=2020, season_number=1, episode_number=1, is_selected=True))
        await s.commit()
        fid = mf.id

    gate = asyncio.Event()

    async def gated_fanout(*a, **k):
        await gate.wait()

    calls: list[int] = []

    async def spy(cfg, tvdb_id):
        calls.append(int(tvdb_id))
        return True

    monkeypatch.setattr("kira.notify.fan_out", gated_fanout)
    monkeypatch.setattr("kira.integrations.sonarr.rescan_series_by_tvdb", spy)

    async with sm() as s:
        res = await perform_rename(RenameRequest(file_ids=[fid], profile="Plex", op="move"), s)
    assert res.succeeded == 1, res
    # The file is moved already, but the network tail is parked at the gate — the
    # Sonarr rescan (which runs LAST) cannot have fired. This is what makes the
    # rename feel instant: the response doesn't wait on the tail.
    await asyncio.sleep(0)  # let the bg task advance up to the gate
    assert calls == [], "network tail must be deferred, not run inline with /rename"
    assert src.exists() is False and Path(res.items[0].new_path).exists(), "file already moved"

    gate.set()
    await drain_background_tasks()
    assert calls == [123456], "backgrounded Sonarr rescan should run once the tail drains"
