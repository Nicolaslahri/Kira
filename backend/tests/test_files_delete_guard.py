"""DELETE /files containment (audit S2): never delete a file on disk that sits
outside every directory Kira manages. We test the roots-gathering + the
containment decision with a fake async session (no DB needed)."""
from __future__ import annotations

import pytest

from kira.api.files import _managed_roots
from kira.api.webhooks import path_under_roots


class _FakeSetting:
    def __init__(self, value):
        self.value = value


class _FakeSession:
    """Minimal async `.get(Model, key)` stand-in for the Setting table."""
    def __init__(self, data: dict):
        self._data = data

    async def get(self, _model, key):
        v = self._data.get(key)
        return _FakeSetting(v) if v is not None else None


@pytest.mark.asyncio
async def test_managed_roots_gathers_every_source():
    sess = _FakeSession({
        "paths.library_root": "/media/tv",
        "paths.watch_folders": ["/incoming", "  "],          # blank entry ignored
        "paths.library_roots": {"default": "/media/movies"},
        "paths.targets.anime": {"value": "/media/anime"},    # wrapped form
    })
    roots = await _managed_roots(sess)
    assert set(roots) == {"/media/tv", "/incoming", "/media/movies", "/media/anime"}

    # A file in any managed root is allowed; one outside all of them is refused.
    assert path_under_roots("/media/anime/Show/ep.mkv", roots) is True
    assert path_under_roots("/media/movies/Inception/m.mkv", roots) is True
    assert path_under_roots("/etc/passwd", roots) is False
    assert path_under_roots("/media/tv/../../etc/shadow", roots) is False  # traversal


@pytest.mark.asyncio
async def test_managed_roots_empty_when_unconfigured():
    # Nothing configured → empty list → caller treats the check as "can't
    # validate" and skips it (permissive, no false-positive block).
    assert await _managed_roots(_FakeSession({})) == []
