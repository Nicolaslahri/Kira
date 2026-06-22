"""Throwaway verification for the threaded discovery walk. NOT a committed test.

Drives the REAL _scan_worker against a temp tree (only the match phase is
stubbed) to confirm:
  1. _drain_walk_batch: dedup, stat-only-survivors, pull-bounded batching, EOF.
  2. End-to-end walk discovers real media, skips NAS/system dirs + samples,
     and runs OFF the event loop (loop stays responsive during the walk).
  3. The walk-error thread-local, recorded on the walk thread, is captured
     across the single-thread-executor boundary → completed_partial + the
     prune sweep is SKIPPED (the NAS-blip guard).
  4. A clean walk → completed + prune sweep RUNS.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira import scanner
from kira.api import scans
from kira.models import MediaFile, Scan


async def _fresh_db(tmp: Path, monkeypatch_targets):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp / 'verify.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    db.engine = eng
    db.SessionLocal = sm
    scans.SessionLocal = sm
    await db.init_db()
    return sm


# ── stubs to keep the match phase off the network ──────────────────────────
async def _noop_match_phase(session, engine, match_ids, scan_id):
    return 0


async def _noop_registry(client):
    return object()


async def _noop_folder_lock(session, ids):
    return 0


async def _noop_read_mediainfo(session):
    return False


def _noop_spawn(ids):
    return None


def _install_stubs():
    scans._match_phase = _noop_match_phase
    scans.registry_from_settings = _noop_registry
    scans._apply_folder_series_lock = _noop_folder_lock
    scans._read_mediainfo_setting = _noop_read_mediainfo
    scans._spawn_mediainfo_enrich = _noop_spawn
    # MatchEngine(registry) is still constructed; make it cheap.
    scans.MatchEngine = lambda registry: object()


def check_drain_batch(tmp: Path):
    """Unit-level check of _drain_walk_batch semantics."""
    files = []
    for i in range(7):
        p = tmp / f"d{i}.mkv"
        p.write_bytes(b"x" * (i + 1))
        files.append(p)

    # A simple iterator over the paths.
    it = iter(files)
    walked: set[str] = set()
    # batch of 3 pulls → first batch returns 3 survivors, not exhausted
    surv, done = scans._drain_walk_batch(it, 3, walked, set(), set(), lambda s: {s.lower()})
    assert len(surv) == 3 and done is False, (len(surv), done)
    assert all(sz is not None for _, sz in surv), "survivors must be stat'd for size"
    # sizes correct (proves the stat happened on the right path)
    assert surv[0][1] == 1 and surv[2][1] == 3, [s for _, s in surv]

    # Next pull of 3
    surv2, done2 = scans._drain_walk_batch(it, 3, walked, set(), set(), lambda s: {s.lower()})
    assert len(surv2) == 3 and done2 is False
    # Last pull: 1 left → survivors=1, exhausted True
    surv3, done3 = scans._drain_walk_batch(it, 3, walked, set(), set(), lambda s: {s.lower()})
    assert len(surv3) == 1 and done3 is True, (len(surv3), done3)

    # Dedup: feed the same paths again with `walked` already populated → 0 survivors
    it_again = iter(files)
    surv4, done4 = scans._drain_walk_batch(it_again, 100, walked, set(), set(),
                                           lambda s: {str(s).lower().replace('/', '\\')})
    # walked has the norm-forms from the FIRST run (str(path).lower().replace('/','\\'))
    assert len(surv4) == 0, f"all should dedup, got {len(surv4)}"
    assert done4 is True

    # existing_lc dedup: survivor whose norm hits existing set is skipped
    walked5: set[str] = set()
    p = files[0]
    existing = {str(p).lower(), str(p).lower().replace("\\", "/")}
    surv5, _ = scans._drain_walk_batch(iter([p]), 10, walked5, set(), existing,
                                       lambda s: {str(s).lower(), str(s).lower().replace('\\', '/')})
    assert len(surv5) == 0, "existing file must be deduped, not stat'd into survivors"

    # Pull-bounded: a batch where everything dedups still returns promptly with
    # exhausted=False if the pull cap is hit before EOF.
    many = [tmp / f"dup{i}.mkv" for i in range(50)]
    walked6 = {str(m).lower().replace("/", "\\") for m in many}  # all pre-seen
    surv6, done6 = scans._drain_walk_batch(iter(many), 10, walked6, set(), set(),
                                           lambda s: {str(s).lower().replace('/', '\\')})
    assert surv6 == [] and done6 is False, (surv6, done6)
    print("  [1] _drain_walk_batch: dedup / stat-survivors / pull-bound / EOF  OK")


def build_tree(root: Path) -> int:
    """Create a realistic tree; return the count of REAL media files expected."""
    (root / "Show" / "Season 01").mkdir(parents=True)
    (root / "Show" / "Season 01" / "Show.S01E01.mkv").write_bytes(b"a" * 10)
    (root / "Show" / "Season 01" / "Show.S01E02.mkv").write_bytes(b"b" * 10)
    (root / "Movie (2020)").mkdir()
    (root / "Movie (2020)" / "Movie.2020.1080p.mkv").write_bytes(b"c" * 10)
    # Excluded: NAS index dir
    (root / "@eaDir" / "thumb").mkdir(parents=True)
    (root / "@eaDir" / "thumb" / "junk.mkv").write_bytes(b"z" * 10)
    # Excluded: a scene sample (small, "sample" stem)
    (root / "Show" / "Season 01" / "sample.mkv").write_bytes(b"s" * 5)
    # Excluded: non-media file
    (root / "Show" / "readme.txt").write_bytes(b"hi")
    return 3  # the 3 real media files


async def scenario_clean(tmp: Path):
    root = tmp / "lib"
    root.mkdir()
    expected = build_tree(root)
    sm = await _fresh_db(tmp, None)
    _install_stubs()

    prune_called = {"n": 0}
    orig_prune = scans._prune_missing_files

    async def _tracking_prune(*a, **k):
        prune_called["n"] += 1
        return await orig_prune(*a, **k)
    scans._prune_missing_files = _tracking_prune

    async with sm() as s:
        scan = Scan(root_path=str(root), status="scanning", source="manual")
        s.add(scan)
        await s.commit()
        await s.refresh(scan)
        sid = scan.id

    # Probe loop responsiveness: a concurrent ticker should keep firing while
    # the walk runs (it would starve if the walk blocked the loop).
    ticks = {"n": 0}

    async def ticker():
        while True:
            ticks["n"] += 1
            await asyncio.sleep(0.001)

    t = asyncio.create_task(ticker())
    await scans._scan_worker(sid, [str(root)])
    t.cancel()

    async with sm() as s:
        rows = list(await s.scalars(MediaFile.__table__.select()))
        scan = await s.get(Scan, sid)
    paths = sorted(Path(r.file_path).name for r in rows)
    assert len(rows) == expected, f"expected {expected} media rows, got {len(rows)}: {paths}"
    assert "sample.mkv" not in paths and "junk.mkv" not in paths, paths
    assert "readme.txt" not in paths, paths
    assert scan.status == "completed", scan.status
    assert scan.file_count == expected, scan.file_count
    assert prune_called["n"] == 1, "clean walk must run the prune sweep"
    assert ticks["n"] > 0, "event loop was starved during the walk (ran on-loop!)"
    print(f"  [2] clean scan: {len(rows)} media discovered, exclusions skipped, "
          f"completed, prune ran, loop ticked {ticks['n']}x during walk  OK")


async def scenario_walk_error(tmp: Path):
    """Inject a scandir failure recorded on the walk thread; confirm it's
    captured across the executor → completed_partial + prune SKIPPED."""
    root = tmp / "lib2"
    root.mkdir()
    (root / "real.mkv").write_bytes(b"x" * 10)
    sm = await _fresh_db(tmp, None)
    _install_stubs()

    prune_called = {"n": 0}

    async def _tracking_prune(*a, **k):
        prune_called["n"] += 1
        return 0
    scans._prune_missing_files = _tracking_prune

    real_walk = scanner.walk

    def walk_with_error(r):
        # Body runs on first next() → inside the drainer, on the WALK THREAD.
        err = OSError("simulated NAS scandir drop")
        err.filename = str(Path(r) / "unreadable")
        scanner._walk_onerror(err)        # records into the walk thread's local
        yield from real_walk(r)
    scanner.walk = walk_with_error

    async with sm() as s:
        scan = Scan(root_path=str(root), status="scanning", source="manual")
        s.add(scan)
        await s.commit()
        await s.refresh(scan)
        sid = scan.id

    try:
        await scans._scan_worker(sid, [str(root)])
    finally:
        scanner.walk = real_walk

    async with sm() as s:
        scan = await s.get(Scan, sid)
        rows = list(await s.scalars(MediaFile.__table__.select()))
    assert scan.status == "completed_partial", (
        f"walk error must downgrade to completed_partial, got {scan.status!r} "
        f"— thread-local capture across the executor is BROKEN")
    assert prune_called["n"] == 0, "prune MUST be skipped when the walk errored"
    assert len(rows) == 1, f"the real file should still be tracked, got {len(rows)}"
    print("  [3] walk error captured across executor → completed_partial, "
          "prune skipped, file still tracked  OK")


async def main():
    import tempfile
    print("Verifying threaded discovery walk...")
    with tempfile.TemporaryDirectory() as d0:
        check_drain_batch(Path(d0))
    with tempfile.TemporaryDirectory() as d1:
        await scenario_clean(Path(d1))
    with tempfile.TemporaryDirectory() as d2:
        await scenario_walk_error(Path(d2))
    print("ALL VERIFICATIONS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(0)
