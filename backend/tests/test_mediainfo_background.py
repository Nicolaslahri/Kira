"""Background tech-tag enrichment (the off-the-critical-path MediaInfo pass).

Covers the seam the user asked for — making the authoritative tech tags a
*background* process — plus the half-built bug it finishes: `/files/reparse-all`
used to import `_read_mediainfo_authoritative_setting` (which didn't exist) and
call `_maybe_enrich_mediainfo` with 4 args (it took 3), crashing on invocation.

Everything is faked (SessionLocal, the MediaInfo native lib) so no disk/DB is
touched. `enrich_parsed` itself runs for real — these assert the real merge.
"""
from __future__ import annotations

import asyncio

import pytest

from kira.api import scans
from kira.parser import parse_filename


FAKE_MI = {
    "quality": "1080p", "codec": "x265", "hdr": "HDR10",
    "channels": "5.1", "audio": "EAC3", "duration": 1400,
}


# ── fakes ────────────────────────────────────────────────────────────────────
class _FakeSetting:
    def __init__(self, value):
        self.value = value


class _FakeMF:
    def __init__(self, id, path, parsed):
        self.id = id
        self.file_path = path
        self.parsed_data = parsed


class _FakeDB:
    """Async context manager standing in for a SessionLocal() session.

    `get(Setting, key)` serves the two parsing.* toggles; `get(MediaFile, id)`
    serves the rows under enrichment. Also usable directly as a session arg."""
    def __init__(self, settings, files):
        self._settings = dict(settings)
        self._files = dict(files)
        self.commits = 0
        self.rollbacks = 0
        self.added = []   # captures Notification rows from _post_notification

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, model, key):
        from kira.models import Setting, MediaFile
        if model is Setting:
            return _FakeSetting(self._settings[key]) if key in self._settings else None
        if model is MediaFile:
            return self._files.get(key)
        return None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _wire(monkeypatch, settings, files, *, available=True, mi=FAKE_MI, read_raises=()):
    db = _FakeDB(settings, files)
    monkeypatch.setattr(scans, "SessionLocal", lambda: db)
    monkeypatch.setattr(scans._mediainfo, "available", lambda: available)

    def _read(path):
        if path in read_raises:
            raise OSError("simulated unreadable file")
        return dict(mi)
    monkeypatch.setattr(scans._mediainfo, "read_media_info", _read)
    return db


# ── enrich_mediainfo_background ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_disabled_setting_is_noop(monkeypatch):
    pd = parse_filename("Show.S01E01.mkv").to_dict()
    mf = _FakeMF(1, "/m/Show.S01E01.mkv", dict(pd))
    _wire(monkeypatch, {"parsing.read_mediainfo": False}, {1: mf})
    n = await scans.enrich_mediainfo_background([1])
    assert n == 0
    assert mf.parsed_data == pd  # untouched


@pytest.mark.asyncio
async def test_noop_when_lib_unavailable(monkeypatch):
    pd = parse_filename("Show.S01E01.mkv").to_dict()
    mf = _FakeMF(1, "/m/Show.S01E01.mkv", dict(pd))
    _wire(monkeypatch, {"parsing.read_mediainfo": True}, {1: mf}, available=False)
    n = await scans.enrich_mediainfo_background([1])
    assert n == 0
    assert mf.parsed_data == pd


@pytest.mark.asyncio
async def test_fallback_fills_when_quality_missing(monkeypatch):
    pd = parse_filename("Show.S01E01.mkv").to_dict()
    assert pd.get("quality") is None  # filename carried no quality
    mf = _FakeMF(1, "/m/Show.S01E01.mkv", dict(pd))
    db = _wire(monkeypatch, {"parsing.read_mediainfo": True}, {1: mf})
    n = await scans.enrich_mediainfo_background([1])
    assert n == 1
    assert mf.parsed_data["quality"] == "1080p"
    assert mf.parsed_data["codec"] == "x265"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_fallback_reads_but_keeps_filename_quality(monkeypatch):
    # Quality present in the name → fallback still READS the container (for
    # channels / duration / per-track languages, which have no filename source)
    # but does NOT override the explicit quality tag.
    pd = parse_filename("Show.S01E01.720p.mkv").to_dict()
    assert pd["quality"] == "720p"
    mf = _FakeMF(1, "/m/Show.S01E01.720p.mkv", dict(pd))
    _wire(monkeypatch,
          {"parsing.read_mediainfo": True, "parsing.mediainfo_authoritative": False},
          {1: mf})
    n = await scans.enrich_mediainfo_background([1])
    assert n == 1                                   # read + filled non-quality fields
    assert mf.parsed_data["quality"] == "720p"      # NOT overridden
    assert mf.parsed_data["channels"] == "5.1"      # filled from the container


@pytest.mark.asyncio
async def test_fallback_populates_languages_even_with_quality(monkeypatch):
    # The bug behind "no dual-audio chips": a quality-tagged file used to skip
    # the read, so its per-track languages never populated. Now it reads them.
    pd = parse_filename("Show.S01E01.1080p.mkv").to_dict()
    mf = _FakeMF(1, "/m/Show.S01E01.1080p.mkv", dict(pd))
    _wire(monkeypatch, {"parsing.read_mediainfo": True}, {1: mf},
          mi={"audio_langs": ["jpn", "eng"], "sub_langs": ["eng"]})
    n = await scans.enrich_mediainfo_background([1])
    assert n == 1
    assert mf.parsed_data["audio_langs"] == ["jpn", "eng"]
    assert mf.parsed_data["sub_langs"] == ["eng"]


@pytest.mark.asyncio
async def test_authoritative_overrides_filename_quality(monkeypatch):
    # Authoritative mode reads every file and lets the container win.
    pd = parse_filename("Show.S01E01.720p.mkv").to_dict()
    mf = _FakeMF(1, "/m/Show.S01E01.720p.mkv", dict(pd))
    _wire(monkeypatch,
          {"parsing.read_mediainfo": True, "parsing.mediainfo_authoritative": True},
          {1: mf})
    n = await scans.enrich_mediainfo_background([1])
    assert n == 1
    assert mf.parsed_data["quality"] == "1080p"  # container truth overrode 720p


@pytest.mark.asyncio
async def test_one_bad_file_does_not_block_others(monkeypatch):
    # A row whose parsed_data can't be reconstructed throws inside the loop;
    # the pass must isolate it (rollback) and still enrich the others.
    good = parse_filename("B.S01E02.mkv").to_dict()
    bad = _FakeMF(1, "/m/A.mkv", "not-a-dict")     # ParsedFile(**str) → TypeError
    mf2 = _FakeMF(2, "/m/B.S01E02.mkv", dict(good))
    db = _wire(monkeypatch, {"parsing.read_mediainfo": True}, {1: bad, 2: mf2})
    n = await scans.enrich_mediainfo_background([1, 2])
    assert n == 1                                  # only the good one
    assert mf2.parsed_data["quality"] == "1080p"
    assert db.rollbacks >= 1                        # the bad row was rolled back


# ── _read_mediainfo_authoritative_setting (key-mismatch regression) ──────────
@pytest.mark.asyncio
async def test_authoritative_setting_reads_the_ui_key(monkeypatch):
    # The Settings UI writes `parsing.mediainfo_authoritative`. Guard against the
    # backend drifting back to the orphaned `parsing.read_mediainfo_authoritative`.
    on = _FakeDB({"parsing.mediainfo_authoritative": True}, {})
    assert await scans._read_mediainfo_authoritative_setting(on) is True
    wrong = _FakeDB({"parsing.read_mediainfo_authoritative": True}, {})
    assert await scans._read_mediainfo_authoritative_setting(wrong) is False
    missing = _FakeDB({}, {})
    assert await scans._read_mediainfo_authoritative_setting(missing) is False


# ── _spawn_mediainfo_enrich (detached task) ──────────────────────────────────
def test_spawn_without_running_loop_is_noop():
    # Sync context (no loop): must return cleanly, never raise.
    scans._spawn_mediainfo_enrich([1, 2])
    scans._spawn_mediainfo_enrich([])


@pytest.mark.asyncio
async def test_spawn_with_loop_runs_and_is_strongly_referenced(monkeypatch):
    ran = asyncio.Event()
    seen = {}

    async def _fake(file_ids, **_kw):
        seen["ids"] = file_ids
        ran.set()
        return len(file_ids)
    monkeypatch.setattr(scans, "enrich_mediainfo_background", _fake)

    scans._spawn_mediainfo_enrich([1, 2, 3])
    await asyncio.wait_for(ran.wait(), timeout=2)
    assert seen["ids"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_spawn_empty_list_starts_nothing(monkeypatch):
    started = []
    async def _fake(file_ids, **_kw):
        started.append(file_ids)
    monkeypatch.setattr(scans, "enrich_mediainfo_background", _fake)
    scans._spawn_mediainfo_enrich([])
    await asyncio.sleep(0)
    assert started == []


# ── activity surface (the "how much has processed?" pill) ────────────────────
@pytest.mark.asyncio
async def test_reports_progress_to_activity(monkeypatch):
    # The background pass must publish live N/total to GET /api/v1/activity so
    # the frontend's "Reading file media info · N/total" pill can show it.
    from kira import activity
    activity.reset()
    try:
        files = {i: _FakeMF(i, f"/m/Show.S01E0{i}.mkv",
                            parse_filename(f"Show.S01E0{i}.mkv").to_dict())
                 for i in (1, 2, 3)}
        _wire(monkeypatch, {"parsing.read_mediainfo": True}, files)
        await scans.enrich_mediainfo_background([1, 2, 3])
        job = next(j for j in activity.snapshot()["jobs"]
                   if j["name"] == "mediainfo_enrich")
        assert job["label"] == "Reading file media info"
        assert job["total"] == 3
        assert job["done"] == 3          # walked all three
        assert job["active"] is False    # end() fired in the finally
    finally:
        activity.reset()


@pytest.mark.asyncio
async def test_disabled_creates_no_activity_job(monkeypatch):
    # A disabled / no-op run returns before begin() — it must not flash an empty
    # job in the activity pill.
    from kira import activity
    activity.reset()
    try:
        mf = _FakeMF(1, "/m/x.mkv", parse_filename("Show.S01E01.mkv").to_dict())
        _wire(monkeypatch, {"parsing.read_mediainfo": False}, {1: mf})
        await scans.enrich_mediainfo_background([1])
        assert not any(j["name"] == "mediainfo_enrich"
                       for j in activity.snapshot()["jobs"])
    finally:
        activity.reset()


# ── durable completion notification (the "how do I know it finished?" gap) ───
def _notes(db):
    from kira.models import Notification
    return [o for o in db.added if isinstance(o, Notification)]


@pytest.mark.asyncio
async def test_settings_reason_posts_completion_notification(monkeypatch):
    files = {1: _FakeMF(1, "/m/Show.S01E01.mkv",
                        parse_filename("Show.S01E01.mkv").to_dict())}
    db = _wire(monkeypatch, {"parsing.read_mediainfo": True}, files)
    await scans.enrich_mediainfo_background([1], reason="settings")
    notes = _notes(db)
    assert len(notes) == 1
    assert notes[0].kind == "success"
    assert "media info" in notes[0].title.lower()


@pytest.mark.asyncio
async def test_settings_reason_notifies_even_when_nothing_changed(monkeypatch):
    # Quality already in the filename → fallback updates 0 — but the user still
    # needs the "it ran and checked everything" reassurance.
    files = {1: _FakeMF(1, "/m/Show.S01E01.720p.mkv",
                        parse_filename("Show.S01E01.720p.mkv").to_dict())}
    db = _wire(monkeypatch, {"parsing.read_mediainfo": True}, files)
    await scans.enrich_mediainfo_background([1], reason="settings")
    notes = _notes(db)
    assert len(notes) == 1 and notes[0].kind == "success"


@pytest.mark.asyncio
async def test_no_reason_stays_quiet(monkeypatch):
    # Scan-triggered runs (reason=None) must NOT notify — they'd spam every scan.
    files = {1: _FakeMF(1, "/m/Show.S01E01.mkv",
                        parse_filename("Show.S01E01.mkv").to_dict())}
    db = _wire(monkeypatch, {"parsing.read_mediainfo": True}, files)
    await scans.enrich_mediainfo_background([1])
    assert _notes(db) == []


@pytest.mark.asyncio
async def test_settings_reason_warns_when_lib_unavailable(monkeypatch):
    # The trap the user nearly hit: enable the toggle but the native lib is
    # missing → explain it instead of doing nothing silently.
    files = {1: _FakeMF(1, "/m/x.mkv", parse_filename("Show.S01E01.mkv").to_dict())}
    db = _wire(monkeypatch, {"parsing.read_mediainfo": True}, files, available=False)
    n = await scans.enrich_mediainfo_background([1], reason="settings")
    assert n == 0
    notes = _notes(db)
    assert len(notes) == 1 and notes[0].kind == "warning"
    assert "metadata" in notes[0].title.lower()


# ── enabling the setting backfills the EXISTING library ──────────────────────
# (the gap the user hit: flipping the toggle did nothing to already-scanned media)
class _FakeSettingRow:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class _FakeScalars:
    def __init__(self, ids):
        self._ids = ids

    def all(self):
        return list(self._ids)


class _FakeSettingsSession:
    """Stands in for the request session in put_settings: upserts Setting rows
    into a dict (so a post-commit read sees the new value) and serves the
    library's file ids for select(MediaFile.id)."""
    def __init__(self, initial, file_ids):
        self._settings = {k: _FakeSettingRow(k, v) for k, v in initial.items()}
        self._file_ids = file_ids

    async def get(self, model, key):
        from kira.models import Setting
        if model is Setting:
            return self._settings.get(key)
        return None

    def add(self, obj):
        self._settings[obj.key] = obj

    async def commit(self):
        pass

    async def scalars(self, _stmt):
        return _FakeScalars(self._file_ids)


async def _put(monkeypatch, initial, payload, file_ids):
    """Drive put_settings with a fake session; return the ids handed to the
    background enrich spawn (or None if it never fired)."""
    from kira.api import settings as settings_api
    from kira.schemas import SettingsBody
    spy = {}
    monkeypatch.setattr("kira.api.scans._spawn_mediainfo_enrich",
                        lambda ids, **_kw: spy.setdefault("ids", list(ids)))
    sess = _FakeSettingsSession(initial, file_ids)
    await settings_api.put_settings(SettingsBody(values=payload), session=sess)
    return spy.get("ids")


@pytest.mark.asyncio
async def test_enabling_read_backfills_existing_library(monkeypatch):
    # read OFF (no row) → ON: backfill every existing file.
    ids = await _put(monkeypatch, {}, {"parsing.read_mediainfo": True}, [10, 11, 12])
    assert ids == [10, 11, 12]


@pytest.mark.asyncio
async def test_enabling_authoritative_while_read_on_backfills(monkeypatch):
    # read already ON, authoritative OFF → ON: re-read the library authoritatively.
    ids = await _put(monkeypatch, {"parsing.read_mediainfo": True},
                     {"parsing.mediainfo_authoritative": True}, [5])
    assert ids == [5]


@pytest.mark.asyncio
async def test_resaving_already_on_does_not_backfill(monkeypatch):
    # No OFF→ON transition (whole-object PUT while already on) → no re-read.
    ids = await _put(monkeypatch, {"parsing.read_mediainfo": True},
                     {"parsing.read_mediainfo": True}, [1, 2])
    assert ids is None


@pytest.mark.asyncio
async def test_disabling_read_does_not_backfill(monkeypatch):
    ids = await _put(monkeypatch, {"parsing.read_mediainfo": True},
                     {"parsing.read_mediainfo": False}, [1])
    assert ids is None


@pytest.mark.asyncio
async def test_unrelated_setting_save_does_not_backfill(monkeypatch):
    # A save that doesn't touch the MediaInfo keys never kicks off a re-read.
    ids = await _put(monkeypatch, {"parsing.read_mediainfo": True},
                     {"rename.concurrency": 8}, [1, 2, 3])
    assert ids is None
