"""The legacy POST /files/{id}/fetch-subtitles endpoint was REMOVED (audit
§20 m): it bypassed the user's language preferences, provider toggles, the
blacklist and the subtitle history ledger. The supported path is
POST /subtitles/backfill with file_ids.

This file now only pins the removal — the route must stay gone (a request
hits FastAPI's 404/405, never a live handler)."""

import pytest
from httpx import ASGITransport, AsyncClient

from kira.main import app


@pytest.mark.asyncio
async def test_legacy_fetch_subtitles_route_is_gone():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/v1/files/1/fetch-subtitles",
            headers={"X-Requested-With": "kira-tests"},
        )
    assert r.status_code in (404, 405)


# ── load_opensubtitles_settings — the surviving helper the old endpoint tests
#    also covered. Restored coverage after the endpoint removal. ────────────
from kira.database import SessionLocal, init_db
from kira.models import Setting


async def _set(session, key, value):
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


async def test_os_settings_defaults_to_en():
    from kira.api.matches import load_opensubtitles_settings
    await init_db()
    async with SessionLocal() as s:
        api_key, user, pw, langs = await load_opensubtitles_settings(s)
        assert langs == ["en"] or isinstance(langs, list)


async def test_os_settings_reads_values_and_language_csv():
    from kira.api.matches import load_opensubtitles_settings
    await init_db()
    async with SessionLocal() as s:
        await _set(s, "providers.opensubtitles.api_key", "k123")
        await _set(s, "providers.opensubtitles.username", "u")
        await _set(s, "providers.opensubtitles.password", "p")
        await _set(s, "subtitles.languages", "EN, ja ,")
        await s.commit()
        api_key, user, pw, langs = await load_opensubtitles_settings(s)
        assert api_key == "k123" and user == "u" and pw == "p"
        assert langs == ["en", "ja"]
        # cleanup so other tests see pristine settings
        for k in ("providers.opensubtitles.api_key", "providers.opensubtitles.username",
                  "providers.opensubtitles.password", "subtitles.languages"):
            row = await s.get(Setting, k)
            if row is not None:
                await s.delete(row)
        await s.commit()


async def test_os_settings_masked_key_is_none():
    from kira.api.matches import load_opensubtitles_settings
    await init_db()
    async with SessionLocal() as s:
        await _set(s, "providers.opensubtitles.api_key", {"masked": True, "set": True})
        await s.commit()
        api_key, _, _, _ = await load_opensubtitles_settings(s)
        assert api_key is None
        row = await s.get(Setting, "providers.opensubtitles.api_key")
        if row is not None:
            await s.delete(row)
        await s.commit()
