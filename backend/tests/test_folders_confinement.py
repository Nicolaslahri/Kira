"""Folder-picker confinement (audit S1).

The picker browses the filesystem by design (you must be able to see a folder to
pick it), so it isn't locked to configured roots. But an admin can set
KIRA_BROWSE_ROOT to hard-confine it to one subtree — resolve()-based, so `..`
can't climb out. Unset = full browse (unchanged).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import kira.api.system as system


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(system.router)
    return TestClient(app, raise_server_exceptions=False)


def test_confined_empty_path_lists_root(tmp_path, monkeypatch):
    (tmp_path / "Movies").mkdir()
    (tmp_path / "TV").mkdir()
    monkeypatch.setattr(system, "_BROWSE_ROOT", str(tmp_path))
    body = _client().get("/folders").json()
    assert body["parent"] is None                       # no drive enumeration
    assert {e["name"] for e in body["entries"]} == {"Movies", "TV"}


def test_confined_allows_inside(tmp_path, monkeypatch):
    sub = tmp_path / "Movies"
    sub.mkdir()
    monkeypatch.setattr(system, "_BROWSE_ROOT", str(tmp_path))
    assert _client().get("/folders", params={"path": str(sub)}).status_code == 200


def test_confined_blocks_outside(tmp_path, monkeypatch):
    root = tmp_path / "media"
    root.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()
    monkeypatch.setattr(system, "_BROWSE_ROOT", str(root))
    assert _client().get("/folders", params={"path": str(outside)}).status_code == 403


def test_confined_blocks_dotdot_traversal(tmp_path, monkeypatch):
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setattr(system, "_BROWSE_ROOT", str(root))
    escape = str(root / ".." / "etc")
    assert _client().get("/folders", params={"path": escape}).status_code == 403


def test_unconfined_browse_unchanged(monkeypatch):
    # No KIRA_BROWSE_ROOT → original behaviour (drive roots / "/"); setup works.
    monkeypatch.setattr(system, "_BROWSE_ROOT", "")
    body = _client().get("/folders").json()
    assert body["parent"] is None
    assert len(body["entries"]) >= 1
