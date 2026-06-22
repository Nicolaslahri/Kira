"""Pack JSON + binding models, safety caps, and the regex sanitizer.

Everything that touches *untrusted* input from a community-authored pack lives
here so the validation is in one audited place:

  • size + count caps                 (a hostile pack can't exhaust memory)
  • a static ReDoS sanitizer          (a hostile regex can't hang the matcher)
  • the override ⇒ scope_paths rule    (a broad regex can't hijack the library)

The regex story (deliberate): Python's ``re`` runs in C and does NOT release
the GIL during catastrophic backtracking, so a "run it in a worker thread with a
timeout" sandbox is false security — the thread wedges and takes the process
with it. The real defense is therefore (a) reject the patterns that backtrack
catastrophically (nested quantifiers / quantified alternations — "star height"
≥ 2), (b) cap the pattern length, and (c) cap the length of the input we ever
match against. If ``google-re2`` (a DFA engine, mathematically linear-time) is
importable we use it and the whole question is moot; the sanitizer still runs so
behaviour is identical with or without it.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── Safety caps ─────────────────────────────────────────────────────────────
KIRA_PACK_VERSION = 1
MAX_PACK_BYTES = 5 * 1024 * 1024     # 5 MiB — One Piece (1100+ eps) is ~1 MiB
MAX_EPISODES = 100_000               # absurdly generous; guards a runaway pack
MAX_SUBS_PER_EPISODE = 20
MAX_REGEX_LEN = 200                  # community regex source ceiling
MAX_MATCH_INPUT = 512                # never match a regex against more than this

# ── Optional google-re2 (DFA, linear-time, ReDoS-immune) ────────────────────
try:  # pragma: no cover - presence depends on the deployment
    import re2 as _re2  # type: ignore
except Exception:  # pragma: no cover
    _re2 = None

USING_RE2 = _re2 is not None


class PackValidationError(ValueError):
    """Raised when a pack (or one of its regexes) is structurally unsafe/invalid."""


# ── ReDoS sanitizer ─────────────────────────────────────────────────────────
def _has_catastrophic_nesting(pattern: str) -> bool:
    """True if ``pattern`` contains a quantified group whose body itself holds a
    quantifier or an alternation — i.e. "star height" ≥ 2 (``(a+)+``, ``(a*)*``,
    ``(a|aa)+``). These are the shapes that backtrack exponentially. A walk over
    the source, group-depth aware and class/escape aware, suffices."""
    body_risky: list[bool] = []   # per open group: did its body hold a quantifier/alternation?
    i, n = 0, len(pattern)
    in_class = False
    while i < n:
        c = pattern[i]
        if c == "\\":               # escaped metachar — skip the pair
            i += 2
            continue
        if in_class:
            if c == "]":
                in_class = False
            i += 1
            continue
        if c == "[":
            in_class = True
            i += 1
            continue
        if c == "(":
            body_risky.append(False)
            i += 1
            continue
        if c == ")":
            inner = body_risky.pop() if body_risky else False
            j = i + 1
            quantified = j < n and (pattern[j] in "*+" or pattern[j] == "{")
            if quantified and inner:
                return True          # quantified group with a risky body → reject
            if quantified and body_risky:
                body_risky[-1] = True  # a quantified group makes the PARENT risky
            i += 1
            continue
        if c in "*+" or c == "{":   # quantifier at the current depth
            if body_risky:
                body_risky[-1] = True
            i += 1
            continue
        if c == "|":                # alternation at the current depth
            if body_risky:
                body_risky[-1] = True
            i += 1
            continue
        i += 1
    return False


def compile_safe(pattern: str | None):
    """Validate + compile a community regex, or raise ``PackValidationError``.

    Returns a compiled pattern object (``re2`` when available, else ``re``) that
    exposes ``.search(text)``. ``None``/empty in → ``None`` out (no pattern)."""
    if pattern is None:
        return None
    if not isinstance(pattern, str):
        raise PackValidationError("regex must be a string")
    if len(pattern) > MAX_REGEX_LEN:
        raise PackValidationError(f"regex too long (>{MAX_REGEX_LEN} chars)")
    if not pattern:
        return None
    if _has_catastrophic_nesting(pattern):
        raise PackValidationError(
            "regex rejected: nested quantifier / quantified alternation "
            "(catastrophic-backtracking shape)"
        )
    try:
        if _re2 is not None:        # DFA engine — immune to backtracking
            return _re2.compile(pattern)
        return re.compile(pattern)
    except Exception as e:          # re2 may reject a few re-only constructs too
        raise PackValidationError(f"regex does not compile: {e}") from e


def safe_search(compiled, text: str | None) -> bool:
    """Run a compiled pattern against length-capped input. False on no-match or
    when ``compiled``/``text`` is falsy."""
    if compiled is None or not text:
        return False
    try:
        return bool(compiled.search(text[:MAX_MATCH_INPUT]))
    except Exception:
        return False


# ── Pack JSON models ────────────────────────────────────────────────────────
class PackSub(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lang: str                         # 2-letter ISO (normalized on apply)
    url: str
    format: str = "srt"               # srt | ass | ssa | vtt | sub
    sync: Literal["guaranteed", "likely", "unknown"] = "guaranteed"
    hi: bool = False                  # hearing-impaired
    forced: bool = False


class PackEpisodeMatch(BaseModel):
    """How a file is recognised as THIS episode. Precedence ladder, strongest
    first: crc32 → regex → release substring → arc + arc_episode → bare numbers."""
    model_config = ConfigDict(extra="ignore")

    crc32: str | None = None          # the [ABCD1234] hash token, case-insensitive
    regex: str | None = None
    release: str | None = None        # substring of the filename
    arc: str | None = None
    arc_episode: int | None = None

    @field_validator("crc32")
    @classmethod
    def _norm_crc(cls, v: str | None) -> str | None:
        return v.strip().lower() if isinstance(v, str) and v.strip() else None

    @field_validator("regex")
    @classmethod
    def _check_regex(cls, v: str | None) -> str | None:
        compile_safe(v)               # raises if unsafe; we keep the source string
        return v


class PackEpisode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    season: int = 1
    episode: int
    absolute: int | None = None
    title: str | None = None
    overview: str | None = None
    match: PackEpisodeMatch = Field(default_factory=PackEpisodeMatch)
    subs: list[PackSub] = Field(default_factory=list)

    @field_validator("subs")
    @classmethod
    def _cap_subs(cls, v: list[PackSub]) -> list[PackSub]:
        if len(v) > MAX_SUBS_PER_EPISODE:
            raise PackValidationError(f"too many subs on one episode (>{MAX_SUBS_PER_EPISODE})")
        return v


class PackShow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    aliases: list[str] = Field(default_factory=list)
    year: int | None = None
    poster_url: str | None = None
    # Per-season poster art (season number as a string key → URL), the standard
    # Jellyfin/Plex `seasonNN-poster.png` layout. An arc-based pack (One Pace) has
    # distinct art per arc; apply prefers the season's poster over the show one,
    # so each episode carries its own arc cover. Absent → the single show poster.
    season_posters: dict[str, str] = Field(default_factory=dict)
    overview: str | None = None


class PackGate(BaseModel):
    """Show-level signature: which no-match files this pack is even allowed to
    consider. At least one signal is required so a pack can never claim 'all
    files'."""
    model_config = ConfigDict(extra="ignore")

    titles: list[str] = Field(default_factory=list)
    release_groups: list[str] = Field(default_factory=list)
    filename_regex: str | None = None

    @field_validator("filename_regex")
    @classmethod
    def _check_regex(cls, v: str | None) -> str | None:
        compile_safe(v)
        return v


class Pack(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kira_pack: int
    id: str
    name: str
    media_type: Literal["movie", "tv", "anime"] = "anime"
    show: PackShow
    match: PackGate = Field(default_factory=PackGate)
    episodes: list[PackEpisode] = Field(default_factory=list)

    @field_validator("kira_pack")
    @classmethod
    def _supported_version(cls, v: int) -> int:
        if v != KIRA_PACK_VERSION:
            raise PackValidationError(
                f"unsupported kira_pack version {v} (this Kira understands {KIRA_PACK_VERSION})"
            )
        return v

    @field_validator("id")
    @classmethod
    def _clean_id(cls, v: str) -> str:
        v = (v or "").strip()
        if not v or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", v):
            raise PackValidationError("id must be 1-64 chars of [A-Za-z0-9._-]")
        return v

    @field_validator("episodes")
    @classmethod
    def _cap_episodes(cls, v: list[PackEpisode]) -> list[PackEpisode]:
        if len(v) > MAX_EPISODES:
            raise PackValidationError(f"too many episodes (>{MAX_EPISODES})")
        return v

    @model_validator(mode="after")
    def _require_gate(self) -> "Pack":
        g = self.match
        if not (g.titles or g.release_groups or g.filename_regex or self.show.aliases or self.show.title):
            raise PackValidationError(
                "pack needs at least one match signal (match.titles / "
                "release_groups / filename_regex, or show.title/aliases)"
            )
        return self


def parse_pack(data: dict[str, Any]) -> Pack:
    """Validate a raw dict into a ``Pack`` or raise ``PackValidationError``.

    Wraps pydantic's ValidationError so callers only catch one type."""
    try:
        return Pack.model_validate(data)
    except PackValidationError:
        raise
    except Exception as e:
        raise PackValidationError(str(e)) from e


# ── Binding (local, machine-specific — stored under settings key packs.bindings)
class PackBinding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str
    id: str = ""                      # filled from the fetched pack
    name: str = ""                    # display label cache
    enabled: bool = True
    authority: Literal["fallback", "override"] = "fallback"
    subtitles: bool = True
    scope_paths: list[str] = Field(default_factory=list)
    etag: str | None = None
    last_fetched: str | None = None   # ISO timestamp (set by caller, no Date.now in scripts)
    last_error: str | None = None

    @model_validator(mode="after")
    def _override_needs_scope(self) -> "PackBinding":
        if self.authority == "override" and not [p for p in self.scope_paths if p and p.strip()]:
            raise PackValidationError(
                "override authority requires at least one scope folder — a community "
                "pack must never be allowed to override the whole library"
            )
        return self

    @property
    def key(self) -> str:
        """Stable internal key: ``<id>:<md5(url)[:8]>``. The URL hash keeps two
        packs that happen to share an ``id`` (e.g. a fork) from colliding."""
        return f"{self.id}:{url_hash(self.url)}" if self.id else url_hash(self.url)


def url_hash(url: str) -> str:
    """First 8 hex of md5(url) — the disambiguator baked into series_group_id and
    the binding key so same-``id`` packs from different URLs never merge."""
    import hashlib

    return hashlib.md5((url or "").strip().encode("utf-8")).hexdigest()[:8]
