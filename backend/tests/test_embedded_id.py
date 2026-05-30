"""Phase 14 — embedded provider-ID extraction + matcher bypass."""

from __future__ import annotations

from kira.matcher.engine import MatchEngine
from kira.parser import ParsedFile, parse_filename
from kira.parser.parser import _extract_provider_ids


def test_extract_tmdb_brace() -> None:
    assert _extract_provider_ids("Movie {tmdb-27205}.mkv") == {"tmdb": "27205"}


def test_extract_tvdb_and_anidb_brackets() -> None:
    assert _extract_provider_ids("Show [tvdb-81797] S01E01.mkv")["tvdb"] == "81797"
    assert _extract_provider_ids("[anidb-9541] file.mkv")["anidb"] == "9541"


def test_extract_imdb_bare() -> None:
    assert _extract_provider_ids("The Matrix (1999) tt0133093.mkv")["imdb"] == "tt0133093"


def test_no_ids_returns_none() -> None:
    assert _extract_provider_ids("Breaking Bad S01E01.mkv") is None


def test_parse_surfaces_provider_ids() -> None:
    pf = parse_filename("Inception (2010) {tmdb-27205}.mkv")
    assert pf.provider_ids == {"tmdb": "27205"}


# ── Matcher bypass ─────────────────────────────────────────────────────────


class _FakeProvider:
    async def get_movie_details(self, mid):
        return {"title": "Inception", "year": 2010, "poster_url": "http://p",
                "overview": "o", "aliases": None}

    async def get_tv_details(self, sid):
        return {"title": "The Show", "year": 2019, "poster_url": None,
                "overview": None, "aliases": None}


class _FakeRegistry:
    def has(self, key):  # all providers "configured"
        return True

    def build(self, key):
        return _FakeProvider()


async def test_embedded_id_bypass_movie() -> None:
    eng = MatchEngine(_FakeRegistry())
    pf = ParsedFile(original_filename="x.mkv", media_type="movie",
                    title="garbled filename title", provider_ids={"tmdb": "27205"})
    res = await eng.match(pf)
    assert len(res) == 1
    assert res[0].provider == "tmdb"
    assert res[0].provider_id == "27205"
    assert res[0].confidence == 1.0
    # Canonical title from the get-by-id fetch, NOT the garbled filename.
    assert res[0].title == "Inception"


async def test_no_embedded_id_does_not_bypass() -> None:
    eng = MatchEngine(_FakeRegistry())
    pf = ParsedFile(original_filename="x.mkv", media_type="movie", title="")
    # No provider_ids and empty title → falls through to the title guard → [].
    res = await eng.match(pf)
    assert res == []
