"""One place that reads every subtitle-related setting.

Before this, the wanted languages, OpenSubtitles credentials, per-source
toggles and HI/forced variants were re-read ad-hoc in the rename hook, the
per-file endpoint, and the matches helper — three slightly different parses of
the same keys. Coverage, backfill, and the files endpoint need the exact same
view, so it lives here as a single typed loader.

Settings keys (all optional; defaults chosen so the feature is safe out of the
box):
  subtitles.languages              "en, es"  → wanted languages (2-letter)
  subtitles.embedded               bool, default ON  (free, offline ffmpeg)
  subtitles.yifysubtitles          bool, default OFF (movie HTML scraper)
  subtitles.hearing_impaired       '' | include | exclude | only
  subtitles.forced                 '' | include | exclude | only
  subtitles.auto_fetch             bool, default OFF — fetch after each RENAME
  subtitles.backfill_after_scan    bool, default OFF — fetch after each SCAN's
                                   tech-tag pass, for files still missing subs
  providers.opensubtitles.api_key / .username / .password
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from kira.models import Setting
from kira.settings_store import unwrap_str as _unwrap


# Every subtitle source key, cheapest-first. Keeps the global + per-type source
# maps covering the exact same set.
_ALL_SOURCES = ("embedded", "opensubtitles", "subdl", "podnapisi", "subsource", "animetosho", "yifysubtitles")


@dataclass
class SubtitlePrefs:
    languages: list[str]
    api_key: str | None
    username: str | None
    password: str | None
    embedded: bool
    yifysubtitles: bool
    hearing_impaired: str
    forced: str
    auto_fetch: bool
    backfill_after_scan: bool
    # Phase 4 — per-type wanted languages (override the global list for a media
    # type; empty → use global), a minimum-score floor (never save a sub worse
    # than this), and upgrade-over-time (re-check low-scoring subs for a better
    # one later).
    languages_by_type: dict[str, list[str]]
    min_score: int
    upgrade: bool
    upgrade_below: int
    # Per-type overrides (absent → use the global value). Mirror
    # languages_by_type / languages_for(): a media type can carry its own quality
    # floor and/or its own enabled-source list. Lets anime lean on embedded +
    # fansub sources at a looser floor while movies stay OpenSubtitles-only.
    min_score_by_type: dict[str, int]
    sources_by_type: dict[str, list[str]]

    def languages_for(self, media_type: str | None) -> list[str]:
        """Wanted languages for a media type — the per-type override if set,
        else the global list."""
        if media_type and self.languages_by_type.get(media_type):
            return self.languages_by_type[media_type]
        return self.languages

    def min_score_for(self, media_type: str | None) -> int:
        """Quality floor for a media type — per-type override if set, else global."""
        if media_type and media_type in self.min_score_by_type:
            return self.min_score_by_type[media_type]
        return self.min_score

    def sources_for(self, media_type: str | None) -> dict[str, bool]:
        """Enabled-source map for a media type: the per-type override (its listed
        sources, each still ANDed with availability so a list can't enable e.g.
        SubDL without its key) if set, else the global enabled_sources."""
        if media_type and media_type in self.sources_by_type:
            chosen = set(self.sources_by_type[media_type])
            avail = self._source_available
            return {s: (s in chosen) and avail[s] for s in _ALL_SOURCES}
        return self.enabled_sources
    # Additional providers (all opt-in). SubDL + SubSource need their own keys
    # (in Connections); Podnapisi + AnimeTosho are keyless.
    subdl: bool
    subdl_api_key: str | None
    podnapisi: bool
    subsource: bool
    subsource_api_key: str | None
    animetosho: bool

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    @property
    def has_download_creds(self) -> bool:
        """OpenSubtitles charges downloads against the account, so a download
        (not just search) needs username + password."""
        return bool(self.username and self.password)

    @property
    def _source_available(self) -> dict[str, bool]:
        """Whether each source CAN run (key present / no key needed), ignoring
        the on/off toggle. The per-type override ANDs its picks against this."""
        return {
            "embedded": True,
            "opensubtitles": self.has_key,
            "subdl": bool(self.subdl_api_key),
            "podnapisi": True,
            "subsource": bool(self.subsource_api_key),
            "animetosho": True,
            "yifysubtitles": True,
        }

    @property
    def enabled_sources(self) -> dict[str, bool]:
        """Global source map for the aggregator: the user's toggle AND
        availability. OpenSubtitles is on whenever a key exists (the key IS the
        opt-in); the rest follow their toggles. SubDL / SubSource additionally
        need their own key to actually run."""
        avail = self._source_available
        toggled = {
            "embedded": self.embedded,
            "opensubtitles": self.has_key,
            "subdl": self.subdl,
            "podnapisi": self.podnapisi,
            "subsource": self.subsource,
            "animetosho": self.animetosho,
            "yifysubtitles": self.yifysubtitles,
        }
        return {s: toggled[s] and avail[s] for s in _ALL_SOURCES}

    @property
    def any_source_enabled(self) -> bool:
        return any(self.enabled_sources.values())


def _variant(raw) -> str:
    v = (_unwrap(raw) or "").strip().lower()
    return v if v in ("include", "exclude", "only") else ""


async def load_subtitle_prefs(session: AsyncSession) -> SubtitlePrefs:
    async def _val(key: str):
        row = await session.get(Setting, key)
        return row.value if row is not None else None

    # Languages — comma string or list, normalized to lowercased 2-letter,
    # defaulting to English.
    def _parse_langs(v) -> list[str]:
        if isinstance(v, str) and v.strip():
            return [s.strip().lower() for s in v.split(",") if s.strip()]
        if isinstance(v, list) and v:
            return [str(s).strip().lower() for s in v if str(s).strip()]
        return []

    languages = _parse_langs(await _val("subtitles.languages")) or ["en"]
    languages_by_type: dict[str, list[str]] = {}
    for mt in ("movie", "tv", "anime"):
        per = _parse_langs(await _val(f"subtitles.languages.{mt}"))
        if per:
            languages_by_type[mt] = per

    def _int(v, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(_unwrap(v) if not isinstance(v, (int, float)) else v)))
        except (TypeError, ValueError):
            return default

    async def _api_key(setting: str) -> str | None:
        raw = await _val(setting)
        return None if isinstance(raw, dict) else (_unwrap(raw) or None)

    api_key = await _api_key("providers.opensubtitles.api_key")

    def _bool(v, default: bool) -> bool:
        return bool(v) if isinstance(v, bool) else default

    # Per-type overrides (absent / blank → the global value applies). A per-type
    # min_score of "0" IS a valid override (no floor for that type), so we store
    # it whenever the key is present and non-blank — not just when truthy.
    min_score_by_type: dict[str, int] = {}
    sources_by_type: dict[str, list[str]] = {}
    for _mt in ("movie", "tv", "anime"):
        _ms = await _val(f"subtitles.min_score.{_mt}")
        _msu = _unwrap(_ms) if isinstance(_ms, (str, dict)) else _ms
        if _msu is not None and str(_msu).strip() != "":
            min_score_by_type[_mt] = _int(_ms, 0, 0, 100)
        # _parse_langs is a generic comma/list → lowercased-token parser; reuse
        # it for source keys, then keep only recognized sources.
        _srcs = [s for s in _parse_langs(await _val(f"subtitles.sources.{_mt}")) if s in _ALL_SOURCES]
        if _srcs:
            sources_by_type[_mt] = _srcs

    return SubtitlePrefs(
        languages=languages,
        api_key=api_key,
        username=_unwrap(await _val("providers.opensubtitles.username")) or None,
        password=_unwrap(await _val("providers.opensubtitles.password")) or None,
        embedded=_bool(await _val("subtitles.embedded"), True),
        yifysubtitles=_bool(await _val("subtitles.yifysubtitles"), False),
        hearing_impaired=_variant(await _val("subtitles.hearing_impaired")),
        forced=_variant(await _val("subtitles.forced")),
        auto_fetch=_bool(await _val("subtitles.auto_fetch"), False),
        backfill_after_scan=_bool(await _val("subtitles.backfill_after_scan"), False),
        languages_by_type=languages_by_type,
        min_score_by_type=min_score_by_type,
        sources_by_type=sources_by_type,
        min_score=_int(await _val("subtitles.min_score"), 0, 0, 100),
        upgrade=_bool(await _val("subtitles.upgrade"), False),
        upgrade_below=_int(await _val("subtitles.upgrade_below"), 80, 1, 100),
        subdl=_bool(await _val("subtitles.subdl"), False),
        subdl_api_key=await _api_key("providers.subdl.api_key"),
        podnapisi=_bool(await _val("subtitles.podnapisi"), False),
        subsource=_bool(await _val("subtitles.subsource"), False),
        subsource_api_key=await _api_key("providers.subsource.api_key"),
        animetosho=_bool(await _val("subtitles.animetosho"), False),
    )
