"""M5 — OpenSubtitles client (pure response parsing + key gating)."""

from __future__ import annotations

import httpx

from kira.providers.opensubtitles import OpenSubtitlesClient, parse_identity


def _entry(*, hash_match: bool, **fd) -> dict:
    return {"attributes": {"moviehash_match": hash_match, "feature_details": fd}}


def test_parse_picks_hash_match_over_first() -> None:
    payload = {
        "data": [
            _entry(hash_match=False, feature_type="Movie", title="Wrong", tmdb_id=111, year=1999),
            _entry(hash_match=True, feature_type="Movie", title="Right", tmdb_id=222, year=2001),
        ]
    }
    ident = parse_identity(payload)
    assert ident is not None
    assert ident["tmdb_id"] == 222
    assert ident["title"] == "Right"
    assert ident["year"] == 2001
    assert ident["feature_type"] == "movie"


def test_parse_falls_back_to_first_when_no_hash_match() -> None:
    payload = {
        "data": [
            _entry(hash_match=False, feature_type="Movie", title="Only", tmdb_id=500),
        ]
    }
    ident = parse_identity(payload)
    assert ident is not None
    assert ident["tmdb_id"] == 500


def test_parse_episode_fields() -> None:
    payload = {
        "data": [
            _entry(hash_match=True, feature_type="Episode", title="Pilot",
                   tmdb_id=999, season_number=2, episode_number=5),
        ]
    }
    ident = parse_identity(payload)
    assert ident["feature_type"] == "episode"
    assert ident["season_number"] == 2
    assert ident["episode_number"] == 5


def test_parse_empty_data_is_none() -> None:
    assert parse_identity({"data": []}) is None
    assert parse_identity({}) is None
    assert parse_identity({"data": "nonsense"}) is None


def test_parse_useless_entry_is_none() -> None:
    # feature_details present but no id and no title → nothing to resolve.
    payload = {"data": [_entry(hash_match=True, feature_type="Movie")]}
    assert parse_identity(payload) is None


async def test_client_without_key_is_noop() -> None:
    client = httpx.AsyncClient()
    try:
        # No api_key → returns None before any network call.
        assert await OpenSubtitlesClient("", client).identify_by_hash("deadbeefdeadbeef") is None
        assert await OpenSubtitlesClient(None, client).identify_by_hash("deadbeefdeadbeef") is None
    finally:
        await client.aclose()
