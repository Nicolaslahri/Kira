"""Embedded-ID bypass must only return a confidence-1.0 match when the ID
actually RESOLVES (audit: embedded-ID 1.0). A stale/typo'd ID or one of the
wrong media type resolves to None and must NOT fabricate a confident match —
the file should fall through to normal title search instead.
"""
from __future__ import annotations

from kira.matcher import engine as eng
from kira.parser import parse_filename


class _FakeRegistry:
    def has(self, _key):
        return True

    def build(self, _key):  # pragma: no cover - must not be reached
        raise AssertionError("registry.build should not be called; meta is patched")


async def test_unresolved_embedded_id_does_not_fabricate_match(monkeypatch):
    async def fake_meta(_key, _pid, _mt, _registry):
        return None  # ID points at nothing / wrong media type

    monkeypatch.setattr(eng, "_basic_meta_by_id", fake_meta)
    engine = eng.MatchEngine(_FakeRegistry())
    parsed = parse_filename("Some.Movie.2020.mkv")

    out = await engine._match_by_embedded_id(parsed, ["tmdb"], {"tmdb": "99999999"})
    assert out == []  # falls through — no confident wrong match


async def test_resolved_embedded_id_returns_confident_match(monkeypatch):
    async def fake_meta(_key, _pid, _mt, _registry):
        return {"title": "Inception", "year": 2010, "poster_url": None,
                "overview": None, "aliases": None}

    monkeypatch.setattr(eng, "_basic_meta_by_id", fake_meta)
    engine = eng.MatchEngine(_FakeRegistry())
    parsed = parse_filename("Inception.2010.mkv")

    out = await engine._match_by_embedded_id(parsed, ["tmdb"], {"tmdb": "27205"})
    assert len(out) == 1
    assert out[0].confidence == 1.0
    assert out[0].provider == "tmdb" and out[0].provider_id == "27205"
    assert out[0].title == "Inception"


async def test_unresolved_falls_through_to_next_provider(monkeypatch):
    """First provider's ID is dead; a later provider's resolves → use that one."""
    async def fake_meta(key, _pid, _mt, _registry):
        return None if key == "tmdb" else {"title": "Frieren", "year": 2023}

    monkeypatch.setattr(eng, "_basic_meta_by_id", fake_meta)
    engine = eng.MatchEngine(_FakeRegistry())
    parsed = parse_filename("Frieren.S01E01.mkv")

    out = await engine._match_by_embedded_id(
        parsed, ["tmdb", "tvdb"], {"tmdb": "0", "tvdb": "424536"})
    assert len(out) == 1
    assert out[0].provider == "tvdb" and out[0].title == "Frieren"
