"""Per-media-type subtitle prefs — `min_score_for` / `sources_for` override the
global value for a media type (mirroring the existing `languages_for`), and the
loader reads `subtitles.min_score.{mt}` / `subtitles.sources.{mt}`. Also pins
that the `enabled_sources` availability-split refactor preserved the global map.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from kira.models import Base, Setting
from kira.subtitles.prefs import SubtitlePrefs, load_subtitle_prefs


def _prefs(**over) -> SubtitlePrefs:
    base = dict(
        languages=["en"], api_key=None, username=None, password=None,
        embedded=True, yifysubtitles=False, hearing_impaired="", forced="",
        auto_fetch=False, backfill_after_scan=False, languages_by_type={},
        min_score=0, upgrade=False, upgrade_below=80,
        min_score_by_type={}, sources_by_type={},
        subdl=False, subdl_api_key=None, podnapisi=False,
        subsource=False, subsource_api_key=None, animetosho=False,
    )
    base.update(over)
    return SubtitlePrefs(**base)


# ── enabled_sources: refactor must preserve the old global behavior ──────────
def test_enabled_sources_unchanged_by_refactor():
    p = _prefs(embedded=True, api_key="k", subdl=True, subdl_api_key="sk",
               podnapisi=True, subsource=True, subsource_api_key=None, animetosho=True)
    es = p.enabled_sources
    assert es["embedded"] is True
    assert es["opensubtitles"] is True            # key present = opt-in
    assert es["subdl"] is True                    # toggle + key
    assert es["podnapisi"] is True
    assert es["subsource"] is False               # toggle on but NO key → off
    assert es["animetosho"] is True


def test_enabled_sources_off_without_key():
    p = _prefs(api_key=None, subdl=True, subdl_api_key=None)
    assert p.enabled_sources["opensubtitles"] is False   # no key
    assert p.enabled_sources["subdl"] is False           # no key


# ── min_score_for ────────────────────────────────────────────────────────────
def test_min_score_for_override_and_fallback():
    p = _prefs(min_score=40, min_score_by_type={"anime": 10})
    assert p.min_score_for("anime") == 10        # per-type override
    assert p.min_score_for("movie") == 40        # falls back to global
    assert p.min_score_for(None) == 40
    # 0 is a valid override (no floor for that type even with a global floor).
    p2 = _prefs(min_score=60, min_score_by_type={"anime": 0})
    assert p2.min_score_for("anime") == 0


# ── sources_for ──────────────────────────────────────────────────────────────
def test_sources_for_override_subset():
    p = _prefs(api_key="k", subdl=True, subdl_api_key="sk",
               sources_by_type={"anime": ["embedded", "subdl"]})
    anime = p.sources_for("anime")
    assert anime["embedded"] is True and anime["subdl"] is True
    assert anime["opensubtitles"] is False       # not in the per-type list
    # A type with no override → the global map.
    assert p.sources_for("movie") == p.enabled_sources
    assert p.sources_for("movie")["opensubtitles"] is True


def test_sources_for_ands_availability():
    # subdl chosen for anime but no key → still off (override can't bypass keys).
    p = _prefs(sources_by_type={"anime": ["subdl", "embedded"]}, subdl_api_key=None)
    s = p.sources_for("anime")
    assert s["subdl"] is False
    assert s["embedded"] is True


# ── loader reads the per-type keys ───────────────────────────────────────────
async def _mem():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def test_loader_reads_per_type_overrides():
    Session = await _mem()
    async with Session() as s:
        s.add(Setting(key="subtitles.min_score", value=50))
        s.add(Setting(key="subtitles.min_score.anime", value=10))
        s.add(Setting(key="subtitles.sources.anime", value="embedded, subdl"))
        await s.commit()

        prefs = await load_subtitle_prefs(s)
        assert prefs.min_score == 50
        assert prefs.min_score_for("anime") == 10      # override
        assert prefs.min_score_for("movie") == 50      # global
        assert prefs.sources_by_type == {"anime": ["embedded", "subdl"]}


async def test_loader_ignores_unknown_source_keys():
    Session = await _mem()
    async with Session() as s:
        s.add(Setting(key="subtitles.sources.movie", value="opensubtitles, bogus, embedded"))
        await s.commit()
        prefs = await load_subtitle_prefs(s)
        assert prefs.sources_by_type == {"movie": ["opensubtitles", "embedded"]}  # bogus dropped
