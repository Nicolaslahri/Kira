"""GET /settings must not leak plaintext secrets (audit S4), and a settings
round-trip must not clobber a stored secret with its own mask."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import pytest

from kira.api import settings as settings_api
from kira.api.settings import (
    _is_secret_key,
    _looks_like_mask,
    _masked,
    get_settings,
    put_settings,
    router as settings_router,
)
from kira.database import get_session
from kira.schemas import SettingsBody


def test_is_secret_key():
    for k in ("providers.tmdb.api_key", "integrations.webhook.token",
              "providers.opensubtitles.password", "integrations.sonarr.api_key",
              "providers.acoustid.client_secret"):
        assert _is_secret_key(k), k
    for k in ("paths.library_root", "naming.profile", "network.force_ipv4"):
        assert not _is_secret_key(k), k


def test_masked_shape():
    assert _masked("abcdef1234") == {"masked": True, "tail": "1234", "set": True}
    assert _masked({"value": "wxyz9876"})["tail"] == "9876"
    assert _masked("")["set"] is False


def test_looks_like_mask():
    assert _looks_like_mask({"masked": True, "tail": "1234"})
    assert _looks_like_mask("•••• •••• •••• 1234")   # the UI's bullet placeholder
    assert not _looks_like_mask("realApiKey123")
    assert not _looks_like_mask("")


class _Row:
    def __init__(self, key, value):
        self.key, self.value = key, value


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def scalars(self, _stmt):
        return _Scalars(self._rows)


def test_get_settings_masks_secrets_keeps_plain():
    rows = [
        _Row("providers.tmdb.api_key", "supersecretkey9999"),
        _Row("integrations.webhook.token", "tok-abcd"),
        _Row("paths.library_root", "/media"),
        _Row("naming.profile", "Plex"),
    ]
    app = FastAPI()
    app.include_router(settings_router)

    async def _fake():
        yield _FakeSession(rows)

    app.dependency_overrides[get_session] = _fake
    body = TestClient(app).get("/settings").json()

    # Secrets masked — plaintext is GONE from the response.
    assert body["providers.tmdb.api_key"] == {"masked": True, "tail": "9999", "set": True}
    assert body["integrations.webhook.token"]["masked"] is True
    assert "supersecretkey9999" not in str(body)
    assert "tok-abcd" not in str(body)
    # Non-secret config still returned in the clear.
    assert body["paths.library_root"] == "/media"
    assert body["naming.profile"] == "Plex"


# ── CR-13: put_settings must not clobber a stored secret with its own mask ────
# `put_settings` consumes the session via `session.get(Setting, key)` (read the
# existing row), `session.add(...)` (insert a new row), and `session.commit()`.
# The mask-guard at the top of its loop is `if _is_secret_key(key) and
# _looks_like_mask(value): continue` — so a masked round-trip value for a secret
# key never reaches get/add at all. This fake mirrors the upsert seam used by
# test_mediainfo_background.py's _FakeSettingsSession, but records every add()
# and the post-call stored value so we can assert "written vs left untouched".
class _UpsertRow:
    def __init__(self, key, value):
        self.key, self.value = key, value


class _UpsertSession:
    """Stands in for the request session in put_settings: seeds existing rows,
    upserts via add(), and records what was added. No parsing.* keys are driven
    in these tests, so the MediaInfo-backfill branch (which would call scalars)
    never fires."""
    def __init__(self, initial):
        self._rows = {k: _UpsertRow(k, v) for k, v in initial.items()}
        self.added: list = []

    async def get(self, model, key):
        from kira.models import Setting
        if model is Setting:
            return self._rows.get(key)
        return None

    def add(self, obj):
        self.added.append(obj)
        self._rows[obj.key] = obj

    async def commit(self):
        pass

    def stored(self, key):
        row = self._rows.get(key)
        return row.value if row is not None else None


async def _put(initial, payload):
    sess = _UpsertSession(initial)
    result = await put_settings(SettingsBody(values=payload), session=sess)
    return sess, result


@pytest.mark.asyncio
async def test_put_skips_masked_dict_for_existing_secret():
    # A GET-masked secret PUT back as its {"masked": true, …} dict must be
    # skipped: the stored plaintext stays, nothing is upserted, updated == 0.
    sess, result = await _put(
        {"providers.tmdb.api_key": "realStoredKey9999"},
        {"providers.tmdb.api_key": {"masked": True, "tail": "9999", "set": True}},
    )
    assert result == {"updated": 0}
    assert sess.stored("providers.tmdb.api_key") == "realStoredKey9999"  # untouched
    assert sess.added == []  # add() never called for the skipped secret


@pytest.mark.asyncio
async def test_put_writes_real_new_secret_for_secret_key():
    # A genuine new key string for a secret key IS written (added + updated==1).
    sess, result = await _put({}, {"providers.tmdb.api_key": "brandNewRealKey"})
    assert result == {"updated": 1}
    assert sess.stored("providers.tmdb.api_key") == "brandNewRealKey"
    assert [o.key for o in sess.added] == ["providers.tmdb.api_key"]


@pytest.mark.asyncio
async def test_put_skips_bullet_placeholder_for_secret_key():
    # The UI's bullet placeholder string (U+2022) for a secret key is a mask too
    # → skipped, stored secret preserved.
    sess, result = await _put(
        {"providers.tmdb.api_key": "realStoredKey9999"},
        {"providers.tmdb.api_key": "•••• •••• •••• 9999"},
    )
    assert result == {"updated": 0}
    assert sess.stored("providers.tmdb.api_key") == "realStoredKey9999"
    assert sess.added == []
