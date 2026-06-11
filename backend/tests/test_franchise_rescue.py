"""Franchise rescue — pair number/title-mangled anime files across cours.

Report: AoT files renamed by an old build as "S05E61 - Midnight Train" (synthetic
season, franchise-continuous number) matched the show but every episode showed
"orphaned · no matching episode" — the bipartite passes only search the matched
AID's own list, and E61/"Midnight Train" live in the Final Season cour. The rescue
places such files via STATIC franchise metadata: the offsets table for numbers
beyond the matched AID's own range, and episode-title trigram across sibling cours.

Index semantics under test: the episode fetch goes through the TVDB cross-ref,
which returns ONE LUMPED list per TVDB season — shared by every cour in it. The
rescue must store the LUMPED index (cour-local + the cour's in-season offset), and
the title pass must only claim entries inside the owning cour's stretch. Getting
this wrong stored "S06E81 - Thaw" as Part 1's E6 "The War Hammer Titan".
All provider I/O is faked.
"""
from __future__ import annotations

from types import SimpleNamespace

from kira.api import scans

# AoT franchise shape: cour AID → official absolute range (TVDB-continuous).
_OFFSETS = [
    (9541, 1, 25), (10944, 26, 37), (13241, 38, 49),
    (14444, 50, 59), (14977, 60, 75), (16177, 76, 87),
]
_SEASONS = {9541: 1, 10944: 2, 13241: 3, 14444: 3, 14977: 4, 16177: 4}
# The LUMPED TVDB S4 list — identical for BOTH S4 cours (that's the crux).
_S4_LUMPED = {
    1: "The Other Side of the Sea", 2: "Midnight Train", 7: "Assault",
    16: "Above and Below", 17: "Judgment", 22: "Thaw",
}
_S1_LIST = {1: "To You, in 2,000 Years", 5: "First Battle: Battle of Trost (1)"}


def _wire(monkeypatch, offsets=_OFFSETS):
    class _FakeAniDB:
        async def get_franchise_offsets(self, seed):
            return list(offsets)

    registry = SimpleNamespace(has=lambda k: k == "anidb", build=lambda k: _FakeAniDB())

    async def fake_fetch(provider_key, provider_id, season, reg):
        aid = int(provider_id)
        titles = _S4_LUMPED if _SEASONS.get(aid) == 4 else (_S1_LIST if aid == 9541 else {})
        return [SimpleNamespace(season=1, episode=n, title=t) for n, t in titles.items()]

    async def fake_season(aid):
        return _SEASONS.get(int(aid))

    monkeypatch.setattr(scans, "_fetch_episodes_for_match", fake_fetch)
    monkeypatch.setattr(
        "kira.providers.anime_mappings.AnimeMappings.tvdb_season", fake_season,
    )
    return registry


def _file(fid, *, ep=None, abs_ep=None, guess=None):
    return (fid, SimpleNamespace(episode=ep, absolute_episode=abs_ep, episode_title_guess=guess))


async def test_numeric_rescue_first_cour_lumped_equals_local(monkeypatch):
    # Part 1 opens its TVDB season (in-season offset 0): E61 → lumped 2.
    registry = _wire(monkeypatch)
    out = await scans._franchise_rescue_unpaired([_file(1, ep=61)], "9541", registry)
    assert out == {1: (14977, 2, "Midnight Train")}


async def test_numeric_rescue_later_cour_uses_lumped_index(monkeypatch):
    # THE regression: "S06E81 - Thaw" = Part 2's local E6 — whose TITLE lives at
    # LUMPED S4 E22 in the cross-ref list. The stored number must be the LOCAL 6
    # (the popup pairs against the AID's native 1..12 list) while the title is
    # resolved at the lumped index ("Thaw", not Part 1's E6 "The War Hammer Titan").
    registry = _wire(monkeypatch)
    out = await scans._franchise_rescue_unpaired([_file(1, ep=81)], "9541", registry)
    assert out == {1: (16177, 6, "Thaw")}


async def test_numbers_within_local_range_are_left_alone(monkeypatch):
    # E3 ≤ the matched AID's 25 episodes — could be a normal cour-LOCAL number
    # (the Frieren S2E03 shape). Numeric must not reinterpret; no guess → no rescue.
    registry = _wire(monkeypatch)
    out = await scans._franchise_rescue_unpaired([_file(1, ep=3)], "9541", registry)
    assert out == {}


async def test_title_rescue_claims_only_the_owning_cours_stretch(monkeypatch):
    # "Thaw" (lumped 22) lives in Part 2's stretch (17..28). Part 1 shares the
    # SAME lumped list but must not claim it — the owning cour is 16177.
    registry = _wire(monkeypatch)
    out = await scans._franchise_rescue_unpaired(
        [_file(1, ep=3, guess="Thaw")], "9541", registry,
    )
    assert out == {1: (16177, 6, "Thaw")}


async def test_two_files_cannot_claim_one_episode(monkeypatch):
    registry = _wire(monkeypatch)
    out = await scans._franchise_rescue_unpaired(
        [_file(1, ep=61), _file(2, ep=61)], "9541", registry,
    )
    assert out.get(1) == (14977, 2, "Midnight Train")
    assert 2 not in out, "the same (aid, episode) slot must not be claimed twice"


async def test_no_anidb_provider_returns_empty(monkeypatch):
    registry = SimpleNamespace(has=lambda k: False, build=lambda k: None)
    out = await scans._franchise_rescue_unpaired([_file(1, ep=61)], "9541", registry)
    assert out == {}


async def test_title_only_mode_skips_the_numeric_pass(monkeypatch):
    # Arbitration mode: the NUMBER is what's on trial, so the numeric pass must
    # not run. A numeric-placeable E61 with no guess yields nothing; with a
    # contradicting real title ("Thaw") the TITLE decides the placement.
    registry = _wire(monkeypatch)
    out = await scans._franchise_rescue_unpaired(
        [_file(1, ep=61)], "9541", registry, title_only=True,
    )
    assert out == {}, "no guess + title_only must place nothing"
    out = await scans._franchise_rescue_unpaired(
        [_file(1, ep=61, guess="Thaw")], "9541", registry, title_only=True,
    )
    assert out == {1: (16177, 6, "Thaw")}, "the title, not E61, decides"
