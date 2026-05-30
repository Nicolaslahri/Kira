"""M2 — AniDB offline acronym / name prefilter (search_tv injection).

Exercises the index-driven candidate injection without any network: we install
a tiny fake title index on the class vars and stub `_ensure_index` so search_tv
runs purely against memory.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from kira.providers import build_provider
from kira.providers.anidb import AniDBProvider
from kira.providers.base import ProviderConfig, ProviderMode


def _install_fixture_index() -> None:
    AniDBProvider._titles = {
        100: [("main", "x-jat", "Shingeki no Kyojin"),
              ("official", "en", "Attack on Titan")],
        200: [("main", "x-jat", "Naruto")],
        300: [("main", "x-jat", "Bocchi the Rock")],
    }
    AniDBProvider._title_index = [
        (100, "shingeki no kyojin", "Shingeki no Kyojin"),
        (100, "attack on titan", "Attack on Titan"),
        (200, "naruto", "Naruto"),
        (300, "bocchi the rock", "Bocchi the Rock"),
    ]
    AniDBProvider._name_index = {
        "shingeki no kyojin": {100},
        "attack on titan": {100},
        "naruto": {200},
        "bocchi the rock": {300},
    }
    AniDBProvider._acronym_index = {
        "snk": {100}, "aot": {100},
        "btr": {300}, "br": {300},
    }


def _reset_index() -> None:
    AniDBProvider._titles = None
    AniDBProvider._title_index = None
    AniDBProvider._name_index = None
    AniDBProvider._acronym_index = None


async def _make_provider(monkeypatch):
    client = httpx.AsyncClient()
    prov = build_provider("anidb", ProviderConfig(mode=ProviderMode.DIRECT), client)

    async def _noop(self):  # never hit the network in tests
        return None

    monkeypatch.setattr(AniDBProvider, "_ensure_index", _noop)
    return prov, client


async def test_curated_acronym_resolves_via_expansion(monkeypatch) -> None:
    _install_fixture_index()
    prov, client = await _make_provider(monkeypatch)
    try:
        res = await prov.search_tv("AoT")
        assert res, "acronym query returned no candidates"
        assert res[0].provider_id == "100"  # Attack on Titan
    finally:
        await client.aclose()
        _reset_index()


async def test_generated_acronym_injects_candidate(monkeypatch) -> None:
    _install_fixture_index()
    prov, client = await _make_provider(monkeypatch)
    try:
        # "btr" isn't in the curated map → resolved via the generated index.
        res = await prov.search_tv("btr")
        assert "300" in {r.provider_id for r in res}  # Bocchi the Rock
    finally:
        await client.aclose()
        _reset_index()


async def test_exact_name_still_resolves(monkeypatch) -> None:
    _install_fixture_index()
    prov, client = await _make_provider(monkeypatch)
    try:
        res = await prov.search_tv("Naruto")
        assert res[0].provider_id == "200"
    finally:
        await client.aclose()
        _reset_index()


async def test_acronym_identity_clears_anime_floor() -> None:
    """End-to-end scoring: a curated-acronym candidate reaches tier-1, so the
    cascade's final score clears the 0.80 anime floor (a tier-2 acronym hit
    would top out ~0.73 and orphan)."""
    from kira.matcher.cascade.metrics.acronym import AcronymMetric
    from kira.matcher.cascade.runner import Cascade
    from kira.matcher.cascade.types import CascadeContext
    from kira.parser import ParsedFile

    @dataclass
    class _Cand:
        provider: str = "anidb"
        provider_id: str = "100"
        title: str = "Attack on Titan"
        aliases: list | None = None
        match_type: str = "tv_episode"
        confidence: float = 0.0

    casc = Cascade(metrics=[AcronymMetric()])
    ctx = CascadeContext(
        parsed=ParsedFile(original_filename="AoT - 01.mkv", media_type="anime", title="AoT"),
        candidates=[],
    )
    trace = await casc.score_one(_Cand(), ctx)
    assert trace.final_score >= 0.85
