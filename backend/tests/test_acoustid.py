"""AcoustID client — fingerprint (fpcalc subprocess) + lookup (AcoustID API),
both mocked. Pure-function regression locks; no real binary, no network."""
from __future__ import annotations

import asyncio
import json

import pytest

import kira.music.acoustid as ac


# ── fingerprint() — fpcalc subprocess mocked ──────────────────────────────
class _FakeProc:
    def __init__(self, out: bytes, rc: int = 0):
        self._out, self.returncode = out, rc

    async def communicate(self):
        return self._out, b""


def _patch_fpcalc(monkeypatch, out: bytes | None, rc: int = 0, *, exe: str | None = "fpcalc"):
    monkeypatch.setattr(ac.fpcalc_setup, "resolve_fpcalc", lambda: exe)
    async def fake_exec(*a, **k):
        return _FakeProc(out or b"", rc)
    monkeypatch.setattr(ac.asyncio, "create_subprocess_exec", fake_exec)


@pytest.mark.asyncio
async def test_fingerprint_parses_fpcalc_json(monkeypatch):
    _patch_fpcalc(monkeypatch, json.dumps({"duration": 215.4, "fingerprint": "AQAAxyz"}).encode())
    fp = await ac.fingerprint("/music/x.flac")
    assert fp == {"duration": 215.4, "fingerprint": "AQAAxyz"}


@pytest.mark.asyncio
async def test_fingerprint_none_when_fpcalc_absent(monkeypatch):
    _patch_fpcalc(monkeypatch, b"{}", exe=None)   # resolve_fpcalc → None
    assert await ac.fingerprint("/music/x.flac") is None


@pytest.mark.asyncio
async def test_fingerprint_none_on_nonzero_exit_or_garbage(monkeypatch):
    _patch_fpcalc(monkeypatch, b'{"duration":1,"fingerprint":"z"}', rc=1)   # fpcalc failed
    assert await ac.fingerprint("/music/x.flac") is None
    _patch_fpcalc(monkeypatch, b"not json")
    assert await ac.fingerprint("/music/x.flac") is None
    _patch_fpcalc(monkeypatch, b'{"duration": 0}')                          # missing fingerprint
    assert await ac.fingerprint("/music/x.flac") is None


# ── lookup() — AcoustID API mocked ────────────────────────────────────────
class _FakeResp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class _FakeClient:
    def __init__(self, data, *, capture=None):
        self._d, self._cap = data, capture

    async def post(self, url, **kwargs):
        if self._cap is not None:
            self._cap.update(url=url, **kwargs)
        return _FakeResp(self._d)


_OK = {
    "status": "ok",
    "results": [
        {"score": 0.97, "recordings": [
            {"id": "rec-yummy", "title": "Yummy", "artists": [{"name": "Justin Bieber"}]}]},
        {"score": 0.40, "recordings": [{"id": "rec-low", "title": "Below floor"}]},
    ],
}


@pytest.mark.asyncio
async def test_lookup_returns_best_above_floor():
    cap: dict = {}
    m = await ac.lookup(_FakeClient(_OK, capture=cap), "AQAAfp", 215.4, "my-key")
    assert m is not None
    assert m.recording_mbid == "rec-yummy" and m.title == "Yummy"
    assert m.artist == "Justin Bieber" and m.score == 0.97
    # duration is sent as a rounded int; the key rides as the `client` param.
    assert cap["data"]["duration"] == "215" and cap["data"]["client"] == "my-key"


@pytest.mark.asyncio
async def test_lookup_none_when_all_below_floor():
    only_low = {"status": "ok", "results": [{"score": 0.3, "recordings": [{"id": "r"}]}]}
    assert await ac.lookup(_FakeClient(only_low), "fp", 200.0, "key") is None


@pytest.mark.asyncio
async def test_lookup_none_on_bad_inputs_or_error_status():
    assert await ac.lookup(_FakeClient(_OK), "fp", 200.0, "") is None        # no key
    assert await ac.lookup(_FakeClient(_OK), "", 200.0, "key") is None        # no fingerprint
    assert await ac.lookup(_FakeClient(_OK), "fp", 0, "key") is None          # no duration
    assert await ac.lookup(_FakeClient({"status": "error"}), "fp", 200.0, "key") is None


@pytest.mark.asyncio
async def test_identify_chains_fingerprint_and_lookup(monkeypatch):
    async def fake_fp(path, **k):
        return {"duration": 200.0, "fingerprint": "AQAAfp"}
    monkeypatch.setattr(ac, "fingerprint", fake_fp)
    m = await ac.identify(_FakeClient(_OK), "/music/track_07.flac", "my-key")
    assert m is not None and m.recording_mbid == "rec-yummy"
    # no fingerprint → no lookup
    async def no_fp(path, **k):
        return None
    monkeypatch.setattr(ac, "fingerprint", no_fp)
    assert await ac.identify(_FakeClient(_OK), "/music/x.flac", "my-key") is None
