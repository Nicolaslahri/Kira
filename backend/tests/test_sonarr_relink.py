"""Sonarr path relink — when Kira renames a series FOLDER, push the new path to
Sonarr (so its files don't orphan), then rescan. Undo passes the roots reversed.

`_translate_path` is pure (re-roots Kira's new folder into Sonarr's namespace via
the shared path suffix); `relink_series` is exercised end-to-end against an httpx
MockTransport (same technique as test_sonarr_url_base)."""
from __future__ import annotations

import json

import httpx
import pytest

from kira.integrations import sonarr
from kira.integrations.sonarr import SonarrConfig, _translate_path


# ── _translate_path: pure shared-suffix re-rooting ───────────────────────────

def test_translate_different_mounts_folder_rename():
    # Kira sees /media, Sonarr sees /data/media; folder renamed in place.
    assert _translate_path(
        "/data/media/tv/Euphoria (US)",
        "/media/tv/Euphoria (US)",
        "/media/tv/Euphoria (US) (2019)",
    ) == "/data/media/tv/Euphoria (US) (2019)"


def test_translate_same_mount():
    assert _translate_path(
        "/data/media/tv/Show",
        "/data/media/tv/Show",
        "/data/media/tv/Show (2020)",
    ) == "/data/media/tv/Show (2020)"


def test_translate_no_change_when_folder_same():
    # arr_new == arr_old → the caller skips the PUT and only rescans.
    assert _translate_path(
        "/data/media/tv/Show", "/media/tv/Show", "/media/tv/Show",
    ) == "/data/media/tv/Show"


def test_translate_no_common_suffix_returns_none():
    assert _translate_path("/data/tv/A", "/library/B", "/library/B2") is None


def test_translate_cross_mount_move_returns_none():
    # New folder isn't under the Kira mount prefix the suffix implied.
    assert _translate_path(
        "/data/media/tv/Show", "/media/tv/Show", "/elsewhere/Show (2020)",
    ) is None


def test_translate_windows_kira_path():
    # Kira on Windows (Z:\), Sonarr on POSIX, same underlying share.
    assert _translate_path(
        "/data/media/tv/Show", r"Z:\media\tv\Show", r"Z:\media\tv\Show (2021)",
    ) == "/data/media/tv/Show (2021)"


# ── relink_series: GET series → PUT new path (moveFiles=false) → rescan ───────

def _route(monkeypatch, *, series, captured, put_status=200):
    """MockTransport serving GET /series, PUT /series/{id}, POST /command."""
    def handler(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if method == "GET" and path.endswith("/api/v3/series"):
            return httpx.Response(200, json=[series])
        if method == "PUT" and "/api/v3/series/" in path:
            captured["put_body"] = json.loads(request.content.decode())
            captured["put_params"] = dict(request.url.params)
            return httpx.Response(put_status, json=captured["put_body"])
        if method == "POST" and path.endswith("/api/v3/command"):
            captured["rescan"] = True
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404)

    real = httpx.AsyncClient

    class Capturing(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **k)

    monkeypatch.setattr(sonarr.httpx, "AsyncClient", Capturing)


@pytest.mark.asyncio
async def test_relink_updates_path_then_rescans(monkeypatch):
    captured: dict = {}
    _route(monkeypatch, captured=captured,
           series={"id": 7, "tvdbId": 1234, "path": "/data/media/tv/Euphoria (US)"})
    cfg = SonarrConfig(base_url="http://son:8989", api_key="k")
    ok, changed, detail = await sonarr.relink_series(
        cfg, 1234,
        old_root="/media/tv/Euphoria (US)",
        new_root="/media/tv/Euphoria (US) (2019)",
    )
    assert ok is True and changed is True
    assert captured["put_body"]["path"] == "/data/media/tv/Euphoria (US) (2019)"
    assert captured["put_params"]["moveFiles"] == "false"   # Kira already moved them
    assert captured.get("rescan") is True
    assert "path" in detail


@pytest.mark.asyncio
async def test_relink_no_path_change_just_rescans(monkeypatch):
    # Folder unchanged → no PUT, only a rescan (the pre-existing behavior).
    captured: dict = {}
    _route(monkeypatch, captured=captured,
           series={"id": 7, "tvdbId": 1234, "path": "/data/media/tv/Show"})
    cfg = SonarrConfig(base_url="http://son:8989", api_key="k")
    ok, changed, detail = await sonarr.relink_series(
        cfg, 1234, old_root="/media/tv/Show", new_root="/media/tv/Show",
    )
    assert ok is True and changed is False
    assert "put_body" not in captured
    assert captured.get("rescan") is True
    assert detail == "rescanned"


@pytest.mark.asyncio
async def test_relink_unmappable_path_still_rescans(monkeypatch):
    # Cross-mount move we can't translate → leave path alone, still rescan.
    captured: dict = {}
    _route(monkeypatch, captured=captured,
           series={"id": 7, "tvdbId": 1234, "path": "/data/media/tv/Show"})
    cfg = SonarrConfig(base_url="http://son:8989", api_key="k")
    ok, changed, detail = await sonarr.relink_series(
        cfg, 1234, old_root="/media/tv/Show", new_root="/elsewhere/Show (2020)",
    )
    assert ok is True and changed is False
    assert "put_body" not in captured
    assert "couldn't map" in detail


@pytest.mark.asyncio
async def test_relink_series_not_in_sonarr(monkeypatch):
    captured: dict = {}
    _route(monkeypatch, captured=captured,
           series={"id": 7, "tvdbId": 9999, "path": "/data/media/tv/Other"})
    cfg = SonarrConfig(base_url="http://son:8989", api_key="k")
    ok, changed, detail = await sonarr.relink_series(
        cfg, 1234, old_root="/media/tv/X", new_root="/media/tv/Y",
    )
    assert ok is False and changed is False
    assert detail == "not in Sonarr"
