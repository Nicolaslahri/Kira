"""Watched-folder auto-rename EXECUTION wiring.

The eligibility gate is covered in test_auto_rename_gate.py; this covers the
seam the old docs doubted — that `maybe_auto_rename` actually hands the eligible
files to the real `rename()` executor (and only those: held / below-threshold
files are left for the user). Seams are faked (DB rows, watch config, rename
executor) so no disk/DB is touched.
"""
from __future__ import annotations

import pytest

from kira import watcher
from kira.watcher import merge_watch_config


# ── fakes ──────────────────────────────────────────────────────────────────
class _Match:
    def __init__(self, confidence, *, selected=True, provider="tvdb", provider_id="111"):
        self.is_selected = selected
        self.provider = provider
        self.provider_id = provider_id
        self.confidence = confidence


class _File:
    def __init__(self, id, file_path, matches):
        self.id = id
        self.file_path = file_path
        self.matches = matches


class _FakeDB:
    """Async context manager standing in for a SessionLocal() session."""
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def scalars(self, *a, **k):
        return list(self._rows)   # `list(await db.scalars(stmt))` → the rows


class _RenameResult:
    succeeded = 1
    failed = 0


def _wire(monkeypatch, rows, cfg):
    """Patch the three seams maybe_auto_rename reaches out to, and return the
    list that the rename() spy appends each call's payload to."""
    monkeypatch.setattr(watcher, "SessionLocal", lambda: _FakeDB(rows))

    async def _cfg(_db):
        return cfg
    monkeypatch.setattr(watcher, "get_watch_config", _cfg)

    async def _defaults(_db):
        return ("copy", "Plex")
    monkeypatch.setattr(watcher, "_resolve_rename_defaults", _defaults)

    calls: list = []

    async def _spy_rename(payload, _db):
        calls.append(payload)
        return _RenameResult()
    # maybe_auto_rename does `from kira.api.rename import perform_rename` at call
    # time and invokes the service function (not the HTTP endpoint), so patching
    # the module attr intercepts it.
    monkeypatch.setattr("kira.api.rename.perform_rename", _spy_rename)
    return calls


# ── tests ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_renames_only_the_eligible_file(monkeypatch):
    cfg = merge_watch_config({"auto_scan": True, "folders": {
        "/media/auto": {"mode": "auto_rename", "threshold": 0.85},
    }})
    rows = [
        _File(1, "/media/auto/Good.S01E01.mkv", [_Match(0.95)]),    # clears 0.85 → eligible
        _File(2, "/media/auto/Shaky.S01E02.mkv", [_Match(0.60)]),   # below → held
    ]
    calls = _wire(monkeypatch, rows, cfg)

    await watcher.maybe_auto_rename(scan_id=1, new_file_ids=[1, 2])

    assert len(calls) == 1, "rename() should run once, for the eligible file only"
    payload = calls[0]
    assert payload.file_ids == [1]      # the shaky one is NOT auto-renamed
    assert payload.dry_run is False     # it really renames
    assert payload.op == "copy" and payload.profile == "Plex"   # user defaults honored


@pytest.mark.asyncio
async def test_no_eligible_files_does_not_rename(monkeypatch):
    cfg = merge_watch_config({"auto_scan": True, "folders": {
        "/media/auto": {"mode": "auto_rename", "threshold": 0.9},
    }})
    rows = [_File(1, "/media/auto/Shaky.mkv", [_Match(0.5)])]      # below threshold
    calls = _wire(monkeypatch, rows, cfg)

    await watcher.maybe_auto_rename(scan_id=1, new_file_ids=[1])
    assert calls == []


@pytest.mark.asyncio
async def test_scan_only_folder_never_executes_rename(monkeypatch):
    # No folder opts into auto_rename → the function returns before touching rename.
    cfg = merge_watch_config({"auto_scan": True, "folders": {
        "/media/tv": {"mode": "scan", "threshold": 0.5},
    }})
    rows = [_File(1, "/media/tv/Perfect.S01E01.mkv", [_Match(0.99)])]
    calls = _wire(monkeypatch, rows, cfg)

    await watcher.maybe_auto_rename(scan_id=1, new_file_ids=[1])
    assert calls == []
