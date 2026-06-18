"""Credential scrubbing — masks api_key / token / password before logs hit disk."""
from __future__ import annotations

from kira.log import scrub_secrets


def test_masks_tmdb_api_key_in_url():
    s = "provider tmdb failed: GET https://api.themoviedb.org/3/search/tv?query=Nana&api_key=abc123DEF456 — ConnectTimeout"
    out = scrub_secrets(s)
    assert "abc123DEF456" not in out and "api_key=***" in out


def test_masks_token_and_password():
    assert "secretXYZ" not in scrub_secrets("Authorization token: secretXYZ")
    assert "hunter2" not in scrub_secrets("db password=hunter2 connecting")


def test_leaves_non_secret_text_intact():
    s = "matched S01E05 via _ep_key=(1,5); confidence=0.91; trigram=0.8"
    out = scrub_secrets(s)
    assert out == s                                        # nothing masked
