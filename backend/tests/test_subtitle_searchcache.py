"""Search-result cache + the thorough-mode dual-query in gather_candidates."""

from __future__ import annotations

import pytest

from kira.subtitles import aggregate, searchcache
from kira.subtitles.model import SearchContext, SubtitleCandidate


def _ctx(**kw) -> SearchContext:
    base = dict(video_path="x.mkv", languages=["en"], media_type="anime")
    base.update(kw)
    return SearchContext(**base)


# ── signature ────────────────────────────────────────────────────────
def test_signature_stable_and_sensitive():
    a = _ctx(anidb_id=69, absolute=1080, episode=8, season=21)
    b = _ctx(anidb_id=69, absolute=1080, episode=8, season=21)
    assert searchcache.signature("opensubtitles", a) == searchcache.signature("opensubtitles", b)
    # provider is part of the key
    assert searchcache.signature("subdl", a) != searchcache.signature("opensubtitles", a)
    # a different episode coordinate changes the key (so the two dual-query twins
    # cache independently)
    assert searchcache.signature("opensubtitles", a) != \
        searchcache.signature("opensubtitles", _ctx(anidb_id=69, absolute=None, episode=8, season=21))
    # language set is order-independent
    assert searchcache.signature("opensubtitles", _ctx(languages=["en", "es"])) == \
        searchcache.signature("opensubtitles", _ctx(languages=["es", "en"]))


# ── store: copy-on-read, TTL, LRU ────────────────────────────────────
def test_put_get_roundtrip_and_copy_on_read():
    searchcache.clear()
    cands = [SubtitleCandidate(provider="opensubtitles", language="en",
                               release_name="X.S01E05", score=50)]
    searchcache.put("k", cands)
    got = searchcache.get("k")
    assert got and got[0].release_name == "X.S01E05"
    # the per-file scorer mutates candidates — that must NOT poison the cache
    got[0].score = 999
    assert searchcache.get("k")[0].score == 50
    # nor must mutating the original list after the put
    cands[0].score = -7
    assert searchcache.get("k")[0].score == 50


def test_ttl_expiry(monkeypatch):
    searchcache.clear()
    t = {"now": 1000.0}
    monkeypatch.setattr(searchcache, "_now", lambda: t["now"])
    searchcache.put("k", [SubtitleCandidate(provider="p", language="en")])
    assert searchcache.get("k") is not None
    t["now"] += searchcache._TTL_SECONDS + 1
    assert searchcache.get("k") is None           # expired
    assert "k" not in searchcache._store           # and dropped on the read


def test_lru_eviction(monkeypatch):
    searchcache.clear()
    monkeypatch.setattr(searchcache, "_MAX_ENTRIES", 3)
    clock = {"t": 0.0}
    monkeypatch.setattr(searchcache, "_now", lambda: clock.__setitem__("t", clock["t"] + 1) or clock["t"])
    for i in range(3):
        searchcache.put(f"k{i}", [SubtitleCandidate(provider="p", language="en")])
    searchcache.get("k0")                          # touch k0 → most-recently-used
    searchcache.put("k3", [SubtitleCandidate(provider="p", language="en")])  # full → evict LRU
    assert searchcache.get("k1") is None           # k1 was least-recently-used
    assert searchcache.get("k0") is not None
    assert searchcache.get("k3") is not None


# ── gather_candidates: dual-query + cache reuse ──────────────────────
class _FakeMod:
    def __init__(self):
        self.calls = []

    async def search(self, client, ctx):
        self.calls.append((ctx.season, ctx.episode, ctx.absolute))
        # name the release after whichever number this query carried
        n = ctx.absolute if ctx.absolute is not None else ctx.episode
        return [SubtitleCandidate(provider="fake", language="en", release_name=f"Show - {n}")]


@pytest.mark.asyncio
async def test_gather_dual_query_then_cache(monkeypatch):
    searchcache.clear()
    fake = _FakeMod()
    monkeypatch.setattr(aggregate, "_EXTERNAL", [("fake", fake)])
    ctx = _ctx(anidb_id=69, absolute=1080, episode=8, season=21, thorough=True)
    enabled = {"fake": True}

    res = await aggregate.gather_candidates(None, ctx, enabled)
    # Thorough + ambiguous anime → TWO queries: by absolute (1080) and by the
    # cour S/E twin (absolute dropped → episode 8).
    assert (21, 8, 1080) in fake.calls and (21, 8, None) in fake.calls
    assert len(fake.calls) == 2
    assert res  # candidates came back and were scored/ranked

    # A second identical gather is fully served from cache — no new live calls.
    fake.calls.clear()
    await aggregate.gather_candidates(None, ctx, enabled)
    assert fake.calls == []


@pytest.mark.asyncio
async def test_gather_single_query_when_not_thorough(monkeypatch):
    searchcache.clear()
    fake = _FakeMod()
    monkeypatch.setattr(aggregate, "_EXTERNAL", [("fake", fake)])
    ctx = _ctx(anidb_id=69, absolute=1080, episode=8, season=21, thorough=False)
    await aggregate.gather_candidates(None, ctx, {"fake": True})
    assert len(fake.calls) == 1                    # no dual-query when thorough is off


class _MovieMod:
    async def search(self, client, ctx):
        return [
            SubtitleCandidate(provider="m", language="en",
                              release_name="Ballerina.2025.1080p.WEB", year=2025, downloads=12),
            # The WRONG Ballerina (2023) — far more downloads, so without the
            # identity gate its community trust would float it to the top.
            SubtitleCandidate(provider="m", language="en",
                              release_name="Ballerina.2023.1080p.BluRay", year=2023, downloads=9999),
        ]


@pytest.mark.asyncio
async def test_gather_movie_identity_gate_buries_wrong_year(monkeypatch):
    searchcache.clear()
    monkeypatch.setattr(aggregate, "_EXTERNAL", [("m", _MovieMod())])
    ctx = SearchContext(video_path="Ballerina (2025).mkv", languages=["en"],
                        media_type="movie", year=2025, thorough=False)
    res = await aggregate.gather_candidates(None, ctx, {"m": True})
    assert res[0].year == 2025                     # right film wins despite fewer downloads
    wrong = next(c for c in res if c.year == 2023)
    assert "different film" in wrong.reasons and wrong.score < res[0].score
