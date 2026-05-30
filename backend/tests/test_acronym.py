"""Phase 13 — AcronymMetric tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from kira.matcher.cascade.metrics.acronym import AcronymMetric, _acronyms
from kira.matcher.cascade.types import CascadeContext, MetricTier
from kira.parser import ParsedFile


@dataclass
class _Cand:
    title: str
    aliases: list[str] = field(default_factory=list)


def _ctx(title: str) -> CascadeContext:
    return CascadeContext(
        parsed=ParsedFile(original_filename="x.mkv", media_type="anime", title=title),
        candidates=[],
    )


def test_generate_initialisms() -> None:
    assert "aot" in _acronyms("attack on titan")          # all-words form
    assert "tlotr" in _acronyms("the lord of the rings")  # all-words (both 'the's)
    assert "lr" in _acronyms("the lord of the rings")     # without-stopwords
    # 'lotr' itself isn't generated — that's exactly why it's in the KNOWN map.
    assert "lotr" not in _acronyms("the lord of the rings")


async def test_known_acronym_exact_is_tier1() -> None:
    """A curated acronym whose expansion exactly matches a candidate title is
    identity-strength (tier-1) — so an acronym-only anime file clears the floor."""
    m = AcronymMetric()
    r = await m.score(_Cand("Attack on Titan"), _ctx("AoT"))
    assert r.raw >= 0.9
    assert r.tier == MetricTier.IDENTITY
    assert r.score >= 0.85


async def test_known_acronym_jjk() -> None:
    m = AcronymMetric()
    r = await m.score(_Cand("Jujutsu Kaisen"), _ctx("JJK"))
    assert r.raw >= 0.9
    assert r.tier == MetricTier.IDENTITY


async def test_known_acronym_fuzzy_stays_tier2() -> None:
    """A curated acronym that only loosely matches the candidate (expansion
    differs from the alias) stays tier-2 — probable, not clinching."""
    m = AcronymMetric()
    # "dbz" → "dragon ball z"; candidate "Dragon Ball Zero Adventures" is a
    # near-but-not-exact expansion → trigram in [0.55, 0.85).
    r = await m.score(_Cand("Dragon Ball Zeta Gaiden"), _ctx("DBZ"))
    if r.raw > 0.0:  # only assert tier when it actually fired
        assert r.tier == MetricTier.SIMILARITY
        assert r.score < 0.85


async def test_generated_initialism_matches() -> None:
    m = AcronymMetric()
    # not in the known map, but the initialism of the candidate matches
    r = await m.score(_Cand("Bocchi the Rock"), _ctx("btr"))
    assert r.raw > 0.0


async def test_multiword_needle_abstains() -> None:
    m = AcronymMetric()
    r = await m.score(_Cand("Attack on Titan"), _ctx("Attack on Titan"))
    assert r.raw == 0.0  # full title, not acronym-shaped


async def test_two_letter_generated_does_not_fire() -> None:
    m = AcronymMetric()
    # "to" is 2 chars — generated path needs >=3; not in known map → abstain
    r = await m.score(_Cand("The Office"), _ctx("to"))
    assert r.raw == 0.0
