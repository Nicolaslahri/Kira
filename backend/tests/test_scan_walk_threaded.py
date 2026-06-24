"""The discovery walk runs in a worker thread, off the event loop (audit: NAS
responsiveness).

`_scan_worker`'s Phase-1 walk used to iterate `scanner.walk()` (os.walk +
per-file `stat()`) directly on the asyncio loop, so a slow NAS/SMB share stalled
every other HTTP request between yields. It now drains the walk in bounded
batches on a DEDICATED single-thread executor (`_drain_walk_batch`).

These guard the three things that refactor must not regress:
  • `_drain_walk_batch` dedups, stats ONLY survivors (the NAS-perf optimization),
    is bounded by entries *pulled* (not survivors), and signals EOF explicitly.
  • A real end-to-end scan discovers media, honors exclusions, completes, runs
    the prune sweep — and leaves the event loop responsive (a concurrent ticker
    keeps firing, proving the walk is off-loop).
  • A scandir failure recorded on the walk thread is still captured across the
    executor boundary → `completed_partial` and the prune sweep is SKIPPED. This
    is the subtle one: `scanner` tracks walk errors in a thread-local, so the
    reset, walk, and final `get_walk_errors()` read are pinned to one thread.
    If that pinning broke, a partial NAS scan would wrongly read `completed` and
    the prune sweep would delete present files.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira import scanner
from kira.api import scans
from kira.models import MediaFile, Scan


async def _fresh_db(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'walk.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    monkeypatch.setattr(scans, "SessionLocal", sm)
    await db.init_db()
    return sm


def _stub_match(monkeypatch):
    """Keep Phase 2 (matching) off the network so we exercise only the walk."""
    async def _noop_match_phase(session, engine, match_ids, scan_id):
        return 0

    async def _noop_registry(client):
        return object()

    async def _noop_folder_lock(session, ids):
        return 0

    async def _noop_read_mediainfo(session):
        return False

    monkeypatch.setattr(scans, "_match_phase", _noop_match_phase)
    monkeypatch.setattr(scans, "registry_from_settings", _noop_registry)
    monkeypatch.setattr(scans, "_apply_folder_series_lock", _noop_folder_lock)
    monkeypatch.setattr(scans, "_read_mediainfo_setting", _noop_read_mediainfo)
    monkeypatch.setattr(scans, "_spawn_mediainfo_enrich", lambda ids: None)
    monkeypatch.setattr(scans, "MatchEngine", lambda registry: object())


def test_drain_walk_batch_semantics(tmp_path):
    files = []
    for i in range(7):
        p = tmp_path / f"d{i}.mkv"
        p.write_bytes(b"x" * (i + 1))
        files.append(p)

    ident = lambda s: {str(s).lower().replace("/", "\\")}
    it = iter(files)
    walked: set[str] = set()

    surv, done = scans._drain_walk_batch(it, 3, walked, set(), set(), ident)
    assert len(surv) == 3 and done is False
    assert all(sz is not None for _, sz in surv), "survivors must be stat'd"
    assert surv[0][1] == 1 and surv[2][1] == 3, "stat read the right file sizes"

    surv2, done2 = scans._drain_walk_batch(it, 3, walked, set(), set(), ident)
    assert len(surv2) == 3 and done2 is False
    surv3, done3 = scans._drain_walk_batch(it, 3, walked, set(), set(), ident)
    assert len(surv3) == 1 and done3 is True  # remainder + EOF

    # Already-walked dedup (walked now populated from the first pass).
    surv4, done4 = scans._drain_walk_batch(iter(files), 100, walked, set(), set(), ident)
    assert surv4 == [] and done4 is True

    # existing_lc dedup → file is skipped, never stat'd into survivors.
    p0 = files[0]
    existing = {str(p0).lower(), str(p0).lower().replace("\\", "/")}
    surv5, _ = scans._drain_walk_batch(
        iter([p0]), 10, set(), set(), existing,
        lambda s: {str(s).lower(), str(s).lower().replace("\\", "/")},
    )
    assert surv5 == []

    # Pull-bounded: an all-deduped batch returns promptly (NOT exhausted) once
    # the pull cap is hit — it never walks the whole tree in one hop.
    many = [tmp_path / f"dup{i}.mkv" for i in range(50)]
    walked6 = {str(m).lower().replace("/", "\\") for m in many}
    surv6, done6 = scans._drain_walk_batch(iter(many), 10, walked6, set(), set(), ident)
    assert surv6 == [] and done6 is False


def _build_tree(root: Path) -> int:
    """A realistic tree; returns the count of REAL media files expected."""
    (root / "Show" / "Season 01").mkdir(parents=True)
    (root / "Show" / "Season 01" / "Show.S01E01.mkv").write_bytes(b"a" * 10)
    (root / "Show" / "Season 01" / "Show.S01E02.mkv").write_bytes(b"b" * 10)
    (root / "Movie (2020)").mkdir()
    (root / "Movie (2020)" / "Movie.2020.1080p.mkv").write_bytes(b"c" * 10)
    (root / "@eaDir" / "thumb").mkdir(parents=True)            # NAS index → skip
    (root / "@eaDir" / "thumb" / "junk.mkv").write_bytes(b"z" * 10)
    (root / "Show" / "Season 01" / "sample.mkv").write_bytes(b"s" * 5)  # sample → skip
    (root / "Show" / "readme.txt").write_bytes(b"hi")          # non-media → skip
    return 3


async def test_clean_scan_runs_offloop_and_prunes(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    root.mkdir()
    expected = _build_tree(root)
    sm = await _fresh_db(tmp_path, monkeypatch)
    _stub_match(monkeypatch)

    prune_called = {"n": 0}
    orig_prune = scans._prune_missing_files

    async def _tracking_prune(*a, **k):
        prune_called["n"] += 1
        return await orig_prune(*a, **k)

    monkeypatch.setattr(scans, "_prune_missing_files", _tracking_prune)

    async with sm() as s:
        scan = Scan(root_path=str(root), status="scanning", source="manual")
        s.add(scan)
        await s.commit()
        await s.refresh(scan)
        sid = scan.id

    # A concurrent ticker should keep firing while the walk runs; it would
    # starve if the blocking walk/stat ran on the event loop.
    ticks = {"n": 0}

    async def ticker():
        while True:
            ticks["n"] += 1
            await asyncio.sleep(0.001)

    t = asyncio.create_task(ticker())
    await scans._scan_worker(sid, [str(root)])
    t.cancel()

    async with sm() as s:
        rows = list(await s.scalars(select(MediaFile)))
        scan = await s.get(Scan, sid)
    names = sorted(Path(r.file_path).name for r in rows)
    assert len(rows) == expected, names
    assert "sample.mkv" not in names and "junk.mkv" not in names and "readme.txt" not in names
    assert scan.status == "completed"
    assert scan.file_count == expected
    assert prune_called["n"] == 1, "a clean walk must run the prune sweep"
    assert ticks["n"] > 0, "event loop starved during the walk → it ran on-loop!"


async def test_walk_error_downgrades_status_but_still_sweeps_clean_subtrees(tmp_path, monkeypatch):
    root = tmp_path / "lib2"
    root.mkdir()
    (root / "real.mkv").write_bytes(b"x" * 10)
    sm = await _fresh_db(tmp_path, monkeypatch)
    _stub_match(monkeypatch)

    prune_calls = {"n": 0, "error_paths": []}

    async def _tracking_prune(session, root_paths, walked, norm_fn, *, error_paths=()):
        prune_calls["n"] += 1
        prune_calls["error_paths"] = list(error_paths)
        return 0

    monkeypatch.setattr(scans, "_prune_missing_files", _tracking_prune)

    real_walk = scanner.walk

    def walk_with_error(r):
        # Body runs on the first next() → inside the drainer, on the WALK THREAD,
        # exactly where a real scandir failure would be recorded.
        err = OSError("simulated NAS scandir drop")
        err.filename = str(Path(r) / "unreadable")
        scanner._walk_onerror(err)        # records into the walk thread's local
        yield from real_walk(r)

    monkeypatch.setattr(scanner, "walk", walk_with_error)

    async with sm() as s:
        scan = Scan(root_path=str(root), status="scanning", source="manual")
        s.add(scan)
        await s.commit()
        await s.refresh(scan)
        sid = scan.id

    await scans._scan_worker(sid, [str(root)])

    async with sm() as s:
        scan = await s.get(Scan, sid)
        rows = list(await s.scalars(select(MediaFile)))
    assert scan.status == "completed_partial", (
        f"a walk error must downgrade to completed_partial, got {scan.status!r} — "
        "thread-local walk-error capture across the executor is BROKEN"
    )
    # RESILIENT sweep: the prune now RUNS (it used to be skipped wholesale on ANY
    # walk error, which froze all deleted-file cleanup) — but the errored folder
    # is handed in as `error_paths` so that subtree is excluded while the rest of
    # the library is still swept.
    assert prune_calls["n"] == 1, "prune must still run so clean subtrees get swept"
    assert any("unreadable" in p for p in prune_calls["error_paths"]), (
        "the errored folder must be passed to the sweep so it's excluded from pruning"
    )
    assert len(rows) == 1, "the real file should still be tracked"
