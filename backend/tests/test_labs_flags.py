"""Labs feature flags — opt-in gating for the experimental/cost-bearing metrics."""

from __future__ import annotations

import inspect

from kira.matcher import engine as eng
from kira.matcher.cascade.runner import build_default_cascade


# ── labs_flag reader ─────────────────────────────────────────────────────

async def test_labs_flag_default_off(monkeypatch) -> None:
    async def _empty():
        return {}
    monkeypatch.setattr(eng, "_load_db_settings", _empty)
    assert await eng.labs_flag("episode_title_boost") is False
    assert await eng.labs_flag("runtime_corroboration") is False


async def test_labs_flag_reads_bool(monkeypatch) -> None:
    async def _db():
        return {"labs.episode_title_boost": True, "labs.runtime_corroboration": False}
    monkeypatch.setattr(eng, "_load_db_settings", _db)
    assert await eng.labs_flag("episode_title_boost") is True
    assert await eng.labs_flag("runtime_corroboration") is False


async def test_labs_flag_unwraps_value_dict(monkeypatch) -> None:
    async def _db():
        return {"labs.episode_title_boost": {"value": True}}
    monkeypatch.setattr(eng, "_load_db_settings", _db)
    assert await eng.labs_flag("episode_title_boost") is True


async def test_labs_flag_isolated_on_error(monkeypatch) -> None:
    async def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(eng, "_load_db_settings", _boom)
    assert await eng.labs_flag("episode_title_boost", default=False) is False


# ── runtime metric gating ────────────────────────────────────────────────

def _metric_names(cascade) -> set[str]:
    return {m.name for m in cascade.metrics}


def test_runtime_metric_absent_by_default() -> None:
    c = build_default_cascade(provider_key="tvdb", media_type="tv")
    assert "runtime" not in _metric_names(c)


def test_runtime_metric_present_when_enabled() -> None:
    c = build_default_cascade(provider_key="tvdb", media_type="tv", include_runtime=True)
    assert "runtime" in _metric_names(c)


# ── episode-title boost is bounded + ban-safe (never AniDB) ──────────────

def test_boost_prefetch_excludes_anidb_in_source() -> None:
    """The boost pre-fetch must only fire for TVDB/TMDB, never AniDB (whose
    rate-limited get_episodes froze scans). Guard the gate condition in source."""
    src = inspect.getsource(eng.MatchEngine._match_with)
    assert 'boost_on' in src
    # The pre-fetch is gated on the provider key being tvdb/tmdb.
    assert 'key in ("tvdb", "tmdb")' in src
    # And it's capped at the top-2 candidates.
    assert "scored[:2]" in src
