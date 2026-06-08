"""CR-14 — coverage for OpenSubtitles fetch wiring in kira/api/matches.py:

  • `load_opensubtitles_settings` — pulls (api_key, user, pw, languages) from the
    settings store, masking placeholder api_keys to None and defaulting languages
    to ["en"].
  • `fetch_subtitles` (POST /files/{file_id}/fetch-subtitles) — the handler that
    resolves credentials + the selected match's tmdb/season/episode and calls
    `fetch_and_save_subtitles`.

Everything is faked (session, the OpenSubtitles download fn) so no disk/DB/net
is touched. `fetch_and_save_subtitles` is monkeypatched to a spy.

NOTE: `fetch_subtitles` previously raised `NameError: name 'selectinload' is
not defined` because it referenced `selectinload(MediaFile.matches)` without
importing it (every other handler in matches.py imports it locally). That bug
is now FIXED — `fetch_subtitles` imports `selectinload` inside its body — so the
handler reaches its real credential/match logic without any test-side shim, and
the regression test below asserts it no longer raises NameError.
"""
from __future__ import annotations

import pytest

from kira.api import matches
from kira.models import MediaFile, Match, Setting


# ── fakes ────────────────────────────────────────────────────────────────────
class _SettingRow:
    def __init__(self, value):
        self.value = value


class _SettingsSession:
    """Serves `session.get(Setting, key)` from a dict for load_opensubtitles_settings."""
    def __init__(self, settings: dict):
        self._settings = settings

    async def get(self, model, key):
        if model is Setting and key in self._settings:
            return _SettingRow(self._settings[key])
        return None


class _FetchSession:
    """Serves the `session.scalar(select(MediaFile)...)` lookup (returning a
    prepared MediaFile or None) AND `session.get(Setting, key)` for the
    credential read inside load_opensubtitles_settings."""
    def __init__(self, media_file, settings: dict):
        self._media_file = media_file
        self._settings = settings

    async def scalar(self, _stmt):
        return self._media_file

    async def get(self, model, key):
        if model is Setting and key in self._settings:
            return _SettingRow(self._settings[key])
        return None


def _mf(file_path="/media/Show.S01E01.mkv", matches_list=None):
    m = MediaFile()
    m.id = 1
    m.file_path = file_path
    # set the relationship collection directly (no DB) so `media_file.matches` works
    m.matches = matches_list or []
    return m


def _match(provider, provider_id, *, selected=True, season=None, episode=None):
    mt = Match()
    mt.provider = provider
    mt.provider_id = provider_id
    mt.is_selected = selected
    mt.season_number = season
    mt.episode_number = episode
    return mt


# ── load_opensubtitles_settings ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_load_settings_bare_string_api_key():
    api_key, user, pw, langs = await matches.load_opensubtitles_settings(
        _SettingsSession({"providers.opensubtitles.api_key": "myRealApiKey"})
    )
    assert api_key == "myRealApiKey"
    assert user is None and pw is None
    assert langs == ["en"]


@pytest.mark.asyncio
async def test_load_settings_masked_dict_api_key_is_none():
    # A masked-placeholder dict (what GET /settings returns for a secret) must
    # NOT be treated as a usable key → None.
    api_key, *_ = await matches.load_opensubtitles_settings(
        _SettingsSession({
            "providers.opensubtitles.api_key": {"masked": True, "tail": "1234", "set": True}
        })
    )
    assert api_key is None


@pytest.mark.asyncio
async def test_load_settings_wrapped_value_dict_api_key_is_none():
    # CURRENT BEHAVIOR: api_key uses `None if isinstance(raw, dict) else …`, so
    # ANY dict — including a {"value": "..."} wrapper — collapses to None (it
    # never reaches _unwrap_setting). username/password DO go through
    # _unwrap_setting and so honor the {"value": …} wrapper; assert that
    # asymmetry here.
    api_key, user, pw, _ = await matches.load_opensubtitles_settings(
        _SettingsSession({
            "providers.opensubtitles.api_key": {"value": "wrappedKey"},
            "providers.opensubtitles.username": {"value": "alice"},
            "providers.opensubtitles.password": {"value": "secretpw"},
        })
    )
    assert api_key is None          # dict api_key → None (wrapper not unwrapped)
    assert user == "alice"          # username wrapper IS unwrapped
    assert pw == "secretpw"


@pytest.mark.asyncio
async def test_load_settings_languages_csv_string():
    _, _, _, langs = await matches.load_opensubtitles_settings(
        _SettingsSession({"subtitles.languages": "EN, fr ,De"})
    )
    assert langs == ["en", "fr", "de"]  # split, trimmed, lowercased


@pytest.mark.asyncio
async def test_load_settings_languages_list():
    _, _, _, langs = await matches.load_opensubtitles_settings(
        _SettingsSession({"subtitles.languages": ["EN", " Fr "]})
    )
    assert langs == ["en", "fr"]


@pytest.mark.asyncio
async def test_load_settings_languages_default_when_unset():
    _, _, _, langs = await matches.load_opensubtitles_settings(_SettingsSession({}))
    assert langs == ["en"]


# ── fetch_subtitles handler ──────────────────────────────────────────────────
@pytest.fixture
def _spy_fetch(monkeypatch):
    """Spy replacing kira.providers.opensubtitles.fetch_and_save_subtitles (the
    name fetch_subtitles imports locally). Records kwargs, returns one saved path."""
    calls = []

    async def _fake(file_path, **kw):
        calls.append({"file_path": file_path, **kw})
        return ["/media/Show.S01E01.en.srt"]

    monkeypatch.setattr(
        "kira.providers.opensubtitles.fetch_and_save_subtitles", _fake
    )
    return calls


@pytest.mark.asyncio
async def test_fetch_subtitles_no_longer_raises_nameerror(_spy_fetch):
    """Regression: the un-imported `selectinload` bug is fixed — `fetch_subtitles`
    now imports it inside its body, so a real call no longer raises NameError and
    instead reaches its credential/match logic. With a present file but no api_key
    configured, that means it falls through to the 400 HTTPException (NOT NameError)."""
    sess = _FetchSession(_mf(), {})
    with pytest.raises(matches.HTTPException) as exc:
        await matches.fetch_subtitles(1, session=sess)
    assert exc.value.status_code == 400
    assert _spy_fetch == []


@pytest.mark.asyncio
async def test_fetch_subtitles_404_when_file_missing(_spy_fetch):
    sess = _FetchSession(None, {"providers.opensubtitles.api_key": "k"})
    with pytest.raises(matches.HTTPException) as exc:
        await matches.fetch_subtitles(1, session=sess)
    assert exc.value.status_code == 404
    assert _spy_fetch == []


@pytest.mark.asyncio
async def test_fetch_subtitles_422_when_no_on_disk_path(_spy_fetch):
    sess = _FetchSession(_mf(file_path=None), {"providers.opensubtitles.api_key": "k"})
    with pytest.raises(matches.HTTPException) as exc:
        await matches.fetch_subtitles(1, session=sess)
    assert exc.value.status_code == 422
    assert _spy_fetch == []


@pytest.mark.asyncio
async def test_fetch_subtitles_400_when_no_api_key(_spy_fetch):
    # File exists + on-disk path present, but no OpenSubtitles api_key configured.
    sess = _FetchSession(_mf(), {})
    with pytest.raises(matches.HTTPException) as exc:
        await matches.fetch_subtitles(1, session=sess)
    assert exc.value.status_code == 400
    assert _spy_fetch == []


@pytest.mark.asyncio
async def test_fetch_subtitles_passes_tmdb_season_episode(_spy_fetch):
    # A selected TMDB match → its tmdb_id/season/episode flow through to
    # fetch_and_save_subtitles, and the response echoes saved/count/languages.
    sel = _match("tmdb", "12345", selected=True, season=2, episode=7)
    mf = _mf(file_path="/media/Show.S02E07.mkv", matches_list=[sel])
    sess = _FetchSession(mf, {
        "providers.opensubtitles.api_key": "realkey",
        "providers.opensubtitles.username": "alice",
        "providers.opensubtitles.password": "pw",
        "subtitles.languages": "en,fr",
    })
    resp = await matches.fetch_subtitles(1, session=sess)

    assert len(_spy_fetch) == 1
    call = _spy_fetch[0]
    assert call["file_path"] == "/media/Show.S02E07.mkv"
    assert call["api_key"] == "realkey"
    assert call["username"] == "alice"
    assert call["password"] == "pw"
    assert call["tmdb_id"] == 12345          # int, parsed from provider_id
    assert call["season"] == 2
    assert call["episode"] == 7
    assert call["languages"] == ["en", "fr"]
    assert resp == {
        "saved": ["/media/Show.S01E01.en.srt"], "count": 1, "languages": ["en", "fr"],
    }


@pytest.mark.asyncio
async def test_fetch_subtitles_non_tmdb_match_passes_no_tmdb_id(_spy_fetch):
    # A selected non-TMDB match (e.g. anidb) → tmdb_id stays None but the
    # selected match's season/episode still pass through (hash-first download).
    sel = _match("anidb", "999", selected=True, season=1, episode=3)
    mf = _mf(matches_list=[sel])
    sess = _FetchSession(mf, {"providers.opensubtitles.api_key": "realkey"})
    await matches.fetch_subtitles(1, session=sess)
    assert _spy_fetch[0]["tmdb_id"] is None
    assert _spy_fetch[0]["season"] == 1
    assert _spy_fetch[0]["episode"] == 3
