"""Regression tests for the settings/config hardening pass (angry-boss audit):
secret-mask fingerprint control, rename-mode enum clamp, MediaInfo wrapped-value
unwrap, and Sonarr URL validation.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api.settings import _masked
from kira.api.rename import _resolve_rename_mode
from kira.api.scans import _read_mediainfo_setting, _read_mediainfo_authoritative_setting
from kira.api.integrations import _load_sonarr_config
from kira.models import Setting


# ── _masked: API keys keep a 4-char fingerprint; hashes expose nothing ───────
def test_masked_default_exposes_tail_fingerprint() -> None:
    m = _masked("supersecretkey")
    assert m == {"masked": True, "tail": "tkey", "set": True}


def test_masked_no_fingerprint_hides_tail() -> None:
    # password_hash path — even a 4-char tail of the hash is a needless leak.
    m = _masked("pbkdf2_sha256$harshtail", fingerprint=False)
    assert m["set"] is True and m["tail"] == "" and m["masked"] is True


async def _fresh(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'settings_hardening.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    await db.init_db()
    return sm


# ── rename.mode clamps to the enum ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_rename_mode_clamps_garbage_to_inplace(tmp_path, monkeypatch) -> None:
    sm = await _fresh(tmp_path, monkeypatch)
    async with sm() as s:
        s.add(Setting(key="rename.mode", value="garbage-nonsense"))
        await s.commit()
    async with sm() as s:
        assert await _resolve_rename_mode(s) == "in-place"


@pytest.mark.asyncio
async def test_rename_mode_accepts_valid_wrapped(tmp_path, monkeypatch) -> None:
    sm = await _fresh(tmp_path, monkeypatch)
    async with sm() as s:
        s.add(Setting(key="rename.mode", value={"value": "move-to-library"}))
        await s.commit()
    async with sm() as s:
        assert await _resolve_rename_mode(s) == "move-to-library"


# ── MediaInfo toggles unwrap the {"value": …} shape (False must read False) ──
@pytest.mark.asyncio
async def test_mediainfo_readers_unwrap_wrapped_false(tmp_path, monkeypatch) -> None:
    sm = await _fresh(tmp_path, monkeypatch)
    async with sm() as s:
        s.add(Setting(key="parsing.read_mediainfo", value={"value": False}))
        s.add(Setting(key="parsing.mediainfo_authoritative", value={"value": False}))
        await s.commit()
    async with sm() as s:
        # Pre-fix `bool({"value": False})` was True — OFF read as ON.
        assert await _read_mediainfo_setting(s) is False
        assert await _read_mediainfo_authoritative_setting(s) is False


@pytest.mark.asyncio
async def test_mediainfo_reader_wrapped_true_reads_true(tmp_path, monkeypatch) -> None:
    sm = await _fresh(tmp_path, monkeypatch)
    async with sm() as s:
        s.add(Setting(key="parsing.read_mediainfo", value={"value": True}))
        await s.commit()
    async with sm() as s:
        assert await _read_mediainfo_setting(s) is True


# ── Sonarr URL validation ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_sonarr_url_rejects_bad_scheme(tmp_path, monkeypatch) -> None:
    sm = await _fresh(tmp_path, monkeypatch)
    async with sm() as s:
        s.add(Setting(key="integrations.sonarr.url", value="file:///etc/passwd"))
        s.add(Setting(key="integrations.sonarr.api_key", value="k"))
        await s.commit()
    async with sm() as s:
        with pytest.raises(HTTPException):
            await _load_sonarr_config(s)


@pytest.mark.asyncio
async def test_sonarr_url_accepts_valid_http(tmp_path, monkeypatch) -> None:
    sm = await _fresh(tmp_path, monkeypatch)
    async with sm() as s:
        s.add(Setting(key="integrations.sonarr.url", value="http://localhost:8989"))
        s.add(Setting(key="integrations.sonarr.api_key", value="k"))
        await s.commit()
    async with sm() as s:
        cfg = await _load_sonarr_config(s)
        assert cfg.base_url == "http://localhost:8989"
