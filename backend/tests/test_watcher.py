"""Unit tests for the watched-folders daemon's pure logic.

We deliberately do NOT spin up real filesystem watching here — those paths
are exercised by the live smoke test. These lock the pure, deterministic
bits: the ignore filter, config merging, per-folder mode resolution, the
idle status shape, and the trigger-loop retry semantics (with `_fire`
stubbed out).
"""

import asyncio
import time

_REAL_SLEEP = asyncio.sleep  # the fakes below shadow the module-global

import pytest

from kira.watcher import (
    DEFAULT_FOLDER_MODE,
    DEFAULT_FOLDER_THRESHOLD,
    WatcherService,
    _is_ignored_path,
    folder_mode,
    merge_watch_config,
)


def test_ignored_paths_partial_downloads():
    for p in [
        "Z:/dl/Show.S01E01.mkv.part",
        "Z:/dl/movie.crdownload",
        "Z:/dl/anime.!qb",
        "Z:/dl/file.downloading",
        "Z:/dl/file.partial",
        "Z:/dl/scratch.tmp",
        "Z:/dl/scratch.temp",
        "Z:/dl/x.filepart",
    ]:
        assert _is_ignored_path(p) is True, p


def test_ignored_paths_trash():
    assert _is_ignored_path("Z:/media/.trash/old.mkv") is True
    assert _is_ignored_path("C:/$Recycle.Bin/x.mkv") is True


def test_real_media_not_ignored():
    for p in [
        "Z:/media/Show.S01E01.mkv",
        "Z:/media/Movie (2020).mp4",
        "Z:/media/Show.S01E01.eng.srt",
        "Z:/media/clip.avi",
    ]:
        assert _is_ignored_path(p) is False, p


def test_empty_path_ignored():
    assert _is_ignored_path("") is True


def test_merge_watch_config_defaults():
    cfg = merge_watch_config(None)
    assert cfg["auto_scan"] is False
    assert cfg["debounce_seconds"] == 30
    assert cfg["poll_interval_seconds"] == 900
    assert cfg["folders"] == {}


def test_merge_watch_config_clamps_minimums():
    cfg = merge_watch_config({
        "auto_scan": True,
        "debounce_seconds": 1,        # below the 5s floor
        "poll_interval_seconds": 10,  # below the 60s floor
    })
    assert cfg["auto_scan"] is True
    assert cfg["debounce_seconds"] == 5
    assert cfg["poll_interval_seconds"] == 60


def test_merge_watch_config_normalizes_folders():
    cfg = merge_watch_config({
        "folders": {
            "Z:/a": {"mode": "auto_rename", "threshold": 0.8},
            "Z:/b": {"mode": "bogus", "threshold": 5},   # invalid mode + out-of-range
            "Z:/c": "not-a-dict",
        }
    })
    assert cfg["folders"]["Z:/a"] == {"mode": "auto_rename", "threshold": 0.8}
    # invalid mode falls back to default; threshold clamps to 1.0
    assert cfg["folders"]["Z:/b"]["mode"] == DEFAULT_FOLDER_MODE
    assert cfg["folders"]["Z:/b"]["threshold"] == 1.0
    # non-dict folder cfg gets full defaults
    assert cfg["folders"]["Z:/c"] == {"mode": DEFAULT_FOLDER_MODE, "threshold": DEFAULT_FOLDER_THRESHOLD}


def test_folder_mode_default_for_unknown():
    cfg = merge_watch_config({"folders": {}})
    assert folder_mode(cfg, "Z:/unknown") == (DEFAULT_FOLDER_MODE, DEFAULT_FOLDER_THRESHOLD)


def test_folder_mode_exact_match():
    cfg = merge_watch_config({"folders": {"Z:/tv": {"mode": "auto_rename", "threshold": 0.95}}})
    assert folder_mode(cfg, "Z:/tv") == ("auto_rename", 0.95)


def test_folder_mode_prefix_match():
    cfg = merge_watch_config({"folders": {"Z:/tv": {"mode": "auto_rename", "threshold": 0.9}}})
    # a file deep inside the watched root inherits the root's mode
    mode, thr = folder_mode(cfg, "Z:/tv/Show/S01/ep.mkv")
    assert mode == "auto_rename"


@pytest.mark.asyncio
async def test_startup_catchup_fires_once(monkeypatch):
    """Files that arrive while Kira is down are invisible to awatch AND get
    absorbed into the poll baseline — the catch-up scan on arm is the only
    thing that finds them. Lock that it fires."""
    svc = WatcherService()
    svc._stop_event = asyncio.Event()
    fired: list[str] = []

    async def fake_fire(reason):
        fired.append(reason)
        return True

    monkeypatch.setattr(svc, "_fire", fake_fire)
    monkeypatch.setattr("kira.watcher.asyncio.sleep", lambda _s: _REAL_SLEEP(0))
    await svc._startup_catchup()
    assert fired == ["startup"]


@pytest.mark.asyncio
async def test_startup_catchup_skips_when_stopped(monkeypatch):
    svc = WatcherService()
    svc._stop_event = asyncio.Event()
    svc._stop_event.set()  # stop() raced the catch-up delay
    fired: list[str] = []

    async def fake_fire(reason):
        fired.append(reason)
        return True

    monkeypatch.setattr(svc, "_fire", fake_fire)
    monkeypatch.setattr("kira.watcher.asyncio.sleep", lambda _s: _REAL_SLEEP(0))
    await svc._startup_catchup()
    assert fired == []


@pytest.mark.asyncio
async def test_debounce_retries_when_fire_skipped(monkeypatch):
    """A skipped fire (another scan already running) must NOT consume the
    dirty flag — the change retries until a scan actually starts."""
    svc = WatcherService()
    svc._stop_event = asyncio.Event()
    svc._debounce_seconds = 0
    svc._dirty = True
    svc._last_event = time.monotonic() - 60
    results = iter([False, False, True])  # busy, busy, started
    fired: list[str] = []

    async def fake_fire(reason):
        ok = next(results)
        fired.append(f"{reason}:{ok}")
        if ok:
            svc._stop_event.set()
        return ok

    monkeypatch.setattr(svc, "_fire", fake_fire)
    monkeypatch.setattr("kira.watcher.asyncio.sleep", lambda _s: _REAL_SLEEP(0))
    await asyncio.wait_for(svc._debounce_loop(), timeout=5)
    assert fired == ["event:False", "event:False", "event:True"]
    assert svc._dirty is False  # consumed only by the successful fire


@pytest.mark.asyncio
async def test_poll_keeps_signature_when_fire_skipped(monkeypatch):
    """A signature change whose fire was skipped must re-trigger on the next
    tick — advancing the baseline on a skipped fire would lose it forever."""
    svc = WatcherService()
    svc._stop_event = asyncio.Event()
    svc._poll_interval_seconds = 0
    svc._last_signature = (1, 1)
    monkeypatch.setattr(svc, "_compute_signature", lambda: (2, 2))
    results = iter([False, True])  # busy once, then started
    fired: list[str] = []
    sigs_at_fire: list[tuple] = []

    async def fake_fire(reason):
        ok = next(results)
        fired.append(f"{reason}:{ok}")
        sigs_at_fire.append(svc._last_signature)
        if ok:
            svc._stop_event.set()
        return ok

    monkeypatch.setattr(svc, "_fire", fake_fire)
    monkeypatch.setattr("kira.watcher.asyncio.sleep", lambda _s: _REAL_SLEEP(0))
    await asyncio.wait_for(svc._poll_loop(), timeout=5)
    assert fired == ["poll:False", "poll:True"]
    assert sigs_at_fire[0] == (1, 1)      # skipped fire left the baseline alone
    assert svc._last_signature == (2, 2)  # successful fire advanced it


def test_status_shape_when_idle():
    s = WatcherService().status()
    assert set(s) == {
        "enabled", "watching", "folders",
        "debounce_seconds", "poll_interval_seconds",
        "last_fire_at", "last_reason",
    }
    assert s["enabled"] is False
    assert s["watching"] is False
    assert s["folders"] == []
