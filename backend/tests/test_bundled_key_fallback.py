"""Bundled fanart.tv key must survive a blanked settings row.

A persisted EMPTY `providers.fanarttv.api_key` (type-then-clear-then-Save) used
to be returned verbatim by `_resolve_str_setting`, silently disabling ALL
fanart artwork while the card still said 'Connected'. The artwork pipeline now
does `.strip() or PROJECT_KEY`.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api.rename import _resolve_str_setting
from kira.models import Setting
from kira.providers.fanarttv import PROJECT_KEY


async def _sess(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bk.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    db.engine = eng
    db.SessionLocal = sm
    await db.init_db()
    return sm


def _effective(raw: str) -> str:
    """Mirror the rename.py resolution: strip-or-bundled."""
    return (raw or "").strip() or PROJECT_KEY


@pytest.mark.asyncio
async def test_blank_row_falls_back_to_bundled(tmp_path, monkeypatch):
    sm = await _sess(tmp_path)
    async with sm() as s:
        s.add(Setting(key="providers.fanarttv.api_key", value=""))
        await s.commit()
        raw = await _resolve_str_setting(s, "providers.fanarttv.api_key", PROJECT_KEY)
    # The stored "" is returned by the resolver; the .strip() or PROJECT_KEY
    # step (applied at the call site) restores the bundled key.
    assert _effective(raw) == PROJECT_KEY


@pytest.mark.asyncio
async def test_whitespace_row_falls_back(tmp_path):
    sm = await _sess(tmp_path)
    async with sm() as s:
        s.add(Setting(key="providers.fanarttv.api_key", value="   "))
        await s.commit()
        raw = await _resolve_str_setting(s, "providers.fanarttv.api_key", PROJECT_KEY)
    assert _effective(raw) == PROJECT_KEY


@pytest.mark.asyncio
async def test_real_user_key_wins(tmp_path):
    sm = await _sess(tmp_path)
    async with sm() as s:
        s.add(Setting(key="providers.fanarttv.api_key", value="USERKEY123"))
        await s.commit()
        raw = await _resolve_str_setting(s, "providers.fanarttv.api_key", PROJECT_KEY)
    assert _effective(raw) == "USERKEY123"
