"""Backfill service: narration, sidecar recording, quota stop, summary."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import activity
from kira import database as db
from kira.models import MediaFile, Match, Notification, Setting
from kira.parser.parser import ParsedFile
from kira.subtitles import backfill as bf
from kira.subtitles.errors import QuotaExceeded


async def _seed(tmp_path, monkeypatch, *, parsed_extra=None):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bf.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    monkeypatch.setattr(bf, "SessionLocal", sm)        # backfill binds it directly
    await db.init_db()
    activity.reset()
    async with sm() as s:
        # wanted English; OpenSubtitles key present so a source is "enabled".
        s.add(Setting(key="subtitles.languages", value="en"))
        s.add(Setting(key="providers.opensubtitles.api_key", value="k"))
        pd = {"original_filename": "Show.S01E01.mkv", "title": "Show",
              "media_type": "anime", "mi_stamp": [1, 2], "sub_langs": ["jpn"]}
        if parsed_extra:
            pd.update(parsed_extra)
        mf = MediaFile(file_path=str(tmp_path / "Show.S01E01.mkv"),
                       parsed_data=pd, media_type="anime", status="renamed")
        s.add(mf)
        await s.flush()
        s.add(Match(media_file_id=mf.id, provider="anidb", provider_id="1",
                    match_type="tv_episode", confidence=0.9, title="Show",
                    season_number=1, episode_number=1, is_selected=True))
        await s.commit()
        fid = mf.id
    return sm, fid


@pytest.mark.asyncio
async def test_backfill_saves_and_records_sidecars(tmp_path, monkeypatch):
    sm, fid = await _seed(tmp_path, monkeypatch)
    saw_status: list[str] = []

    async def fake_fetch(client, ctx, *, enabled, on_status=None):
        # narrate like the real aggregator, then "save" an English sidecar
        if on_status:
            on_status("subsource · 92% — downloading EN")
        from pathlib import Path
        from kira.subtitles.model import SubtitleFetchResult
        dest = str(Path(ctx.video_path).with_name(Path(ctx.video_path).stem + ".en.srt"))
        return [SubtitleFetchResult(language="en", path=dest, provider="subsource",
                                    score=92, sync="likely", release_name="[X] BluRay 1080p")]

    monkeypatch.setattr(bf, "fetch_subtitles", fake_fetch)

    summary = await bf.run_subtitle_backfill([fid])
    assert summary["saved"] == 1
    assert summary["files"] == 1
    assert summary["quota"] is False

    # Sidecar language recorded on parsed_data → coverage flips.
    async with sm() as s:
        mf = await s.get(MediaFile, fid)
        assert "en" in (mf.parsed_data.get("sub_sidecars") or [])
        notes = list(await s.scalars(select(Notification)))
        assert any("Subtitle fetch complete" == n.title for n in notes)
    # Activity job ended (not pinned active).
    assert activity.snapshot()["active"] is False


@pytest.mark.asyncio
async def test_backfill_skips_already_covered(tmp_path, monkeypatch):
    # File already has an English sidecar cached → nothing to fetch.
    sm, fid = await _seed(tmp_path, monkeypatch, parsed_extra={"sub_sidecars": ["en"]})
    called = {"n": 0}

    async def fake_fetch(*a, **k):
        called["n"] += 1
        return []

    monkeypatch.setattr(bf, "fetch_subtitles", fake_fetch)
    summary = await bf.run_subtitle_backfill([fid])
    assert called["n"] == 0
    assert summary["covered"] == 1
    assert summary["files"] == 0


@pytest.mark.asyncio
async def test_backfill_on_disk_sub_counts_covered_not_missing(tmp_path, monkeypatch):
    """A source skipping because the sidecar is ALREADY on disk must count as
    'covered', not a misleading 'not found' (the second-click / sweep bug)."""
    sm, fid = await _seed(tmp_path, monkeypatch)
    # parsed_data says EN missing (sub_langs jpn, no sub_sidecars), but the
    # English sidecar physically exists next to the video.
    (tmp_path / "Show.S01E01.en.srt").write_text("subs")

    async def fake_fetch(*a, **k):
        return []  # source skipped it (already on disk)

    monkeypatch.setattr(bf, "fetch_subtitles", fake_fetch)
    summary = await bf.run_subtitle_backfill([fid])
    assert summary["not_found"] == 0
    assert summary["covered"] == 1
    # and parsed_data is updated so the chip clears
    async with sm() as s:
        mf = await s.get(MediaFile, fid)
        assert "en" in (mf.parsed_data.get("sub_sidecars") or [])


@pytest.mark.asyncio
async def test_backfill_stops_on_quota(tmp_path, monkeypatch):
    sm, fid = await _seed(tmp_path, monkeypatch)

    async def fake_fetch(*a, **k):
        raise QuotaExceeded(remaining=0, reset_hint="in 4h")

    monkeypatch.setattr(bf, "fetch_subtitles", fake_fetch)
    summary = await bf.run_subtitle_backfill([fid])
    assert summary["quota"] is True
    assert summary["saved"] == 0
    async with sm() as s:
        notes = list(await s.scalars(select(Notification)))
        assert any("quota" in (n.body or "").lower() for n in notes)


def test_title_query_strips_season_and_year():
    assert bf._title_query("Attack on Titan Season 3 (2019)") == "Attack on Titan"
    assert bf._title_query("Attack on Titan: The Final Season") == "Attack on Titan"
    assert bf._title_query("Frieren: Beyond Journey`s End") == "Frieren: Beyond Journey's End"
    assert bf._title_query("Ballerina") == "Ballerina"
    # degenerate: title that IS just a season label keeps the original
    assert bf._title_query("Season 2") == "Season 2"


@pytest.mark.asyncio
async def test_backfill_search_uses_parsed_numbers_and_query(tmp_path, monkeypatch):
    """AniDB matches store cour-LOCAL episode numbers — the OpenSubtitles
    search must use the parsed (rendered-filename) S/E or it fetches subs for
    the WRONG episode. And with no TMDB/IMDb id it must send a title query."""
    sm, fid = await _seed(tmp_path, monkeypatch, parsed_extra={"season": 3, "episode": 14})
    # make the selected match look cour-local (Final Season ep 2)
    async with sm() as s:
        m = (await s.scalars(select(Match))).first()
        m.title = "Attack on Titan Season 3 (2019)"
        m.season_number = 3
        m.episode_number = 2
        await s.commit()
    seen = {}

    async def fake_fetch(client, ctx, *, enabled, on_status=None):
        seen["ctx"] = ctx
        return []

    monkeypatch.setattr(bf, "fetch_subtitles", fake_fetch)
    await bf.run_subtitle_backfill([fid])
    ctx = seen["ctx"]
    assert ctx.season == 3
    assert ctx.episode == 14          # parsed wins over cour-local 2
    assert ctx.query == "Attack on Titan"
    assert ctx.tmdb_id is None


@pytest.mark.asyncio
async def test_backfill_not_found_notification_carries_hints(tmp_path, monkeypatch):
    """'27 not found' with no why is indistinguishable from a failure — when
    nothing saved, the summary must explain the silent gaps (no ffmpeg, no
    OpenSubtitles login)."""
    sm, fid = await _seed(tmp_path, monkeypatch)  # api_key set, NO username/password
    monkeypatch.setattr("kira.subtitles.embedded.available", lambda: False)

    async def fake_fetch(*a, **k):
        return []

    monkeypatch.setattr(bf, "fetch_subtitles", fake_fetch)
    await bf.run_subtitle_backfill([fid])
    async with sm() as s:
        note = (await s.scalars(
            select(Notification).where(Notification.title == "Subtitle fetch complete")
        )).first()
    assert note is not None and note.kind == "warning"
    assert "To fix:" in note.body          # bulleted, bell-readable format
    assert "ffmpeg" in note.body
    assert "username + password" in note.body


@pytest.mark.asyncio
async def test_coverage_endpoint_counts(tmp_path, monkeypatch):
    from kira.api.subtitles import coverage
    sm, fid = await _seed(tmp_path, monkeypatch)  # one file: en wanted, sub_langs jpn → missing en
    async with sm() as s:
        # add a second file already covered (English embedded)
        mf2 = MediaFile(
            file_path=str(tmp_path / "Show.S01E02.mkv"),
            parsed_data={"original_filename": "Show.S01E02.mkv", "mi_stamp": [3, 4],
                         "sub_langs": ["eng"]},
            media_type="anime", status="renamed")
        s.add(mf2)
        await s.commit()
    async with sm() as s:
        cov = await coverage(session=s)
    assert cov.inspected == 2
    assert cov.covered == 1
    assert cov.missing_files == 1
    assert cov.by_language == {"en": 1}
    assert cov.wanted == ["en"]


@pytest.mark.asyncio
async def test_backfill_endpoint_library_scope(tmp_path, monkeypatch):
    import kira.api.subtitles as ep
    from kira.api.subtitles import BackfillBody, backfill
    sm, fid = await _seed(tmp_path, monkeypatch)  # one file missing en
    captured = {}

    def fake_spawn(ids, *, language_override=None):
        captured["ids"] = list(ids)
        return True

    monkeypatch.setattr(ep, "spawn_subtitle_backfill", fake_spawn)
    async with sm() as s:
        res = await backfill(BackfillBody(scope="library"), session=s)
    assert res.started is True
    assert res.queued == 1
    assert captured["ids"] == [fid]


@pytest.mark.asyncio
async def test_backfill_stops_on_rejected_key(tmp_path, monkeypatch):
    """A 401/403 from OpenSubtitles fails every file identically — the batch
    must stop after the FIRST and the summary must say what to fix."""
    from kira.subtitles.errors import AuthRejected
    sm, fid = await _seed(tmp_path, monkeypatch)
    calls = {"n": 0}

    async def fake_fetch(*a, **k):
        calls["n"] += 1
        raise AuthRejected("OpenSubtitles rejected the API key (HTTP 403)")

    monkeypatch.setattr(bf, "fetch_subtitles", fake_fetch)
    summary = await bf.run_subtitle_backfill([fid])
    assert calls["n"] == 1
    assert summary["saved"] == 0
    async with sm() as s:
        note = (await s.scalars(
            select(Notification).where(Notification.title == "Subtitle fetch complete")
        )).first()
    assert note is not None and note.kind == "warning"
    assert "API key was rejected" in note.body


@pytest.mark.asyncio
async def test_backfill_no_source_enabled(tmp_path, monkeypatch):
    sm, fid = await _seed(tmp_path, monkeypatch)
    # Remove the key and disable embedded → no source.
    async with sm() as s:
        row = await s.get(Setting, "providers.opensubtitles.api_key")
        await s.delete(row)
        s.add(Setting(key="subtitles.embedded", value=False))
        await s.commit()
    summary = await bf.run_subtitle_backfill([fid])
    assert summary["files"] == 0
    async with sm() as s:
        notes = list(await s.scalars(select(Notification)))
        assert any("No subtitle source" in n.title for n in notes)
