from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer


def _utc_iso(dt: datetime) -> str:
    """Serialize a datetime as an unambiguous UTC ISO-8601 string ending in 'Z'.

    Our timestamps are stored NAIVE but always represent UTC (`func.now()` /
    `datetime.now(timezone.utc)`). Emitted without a timezone, the browser's
    `new Date("...")` parses them as LOCAL time — so every "x ago" was off by the
    viewer's UTC offset (the "5 hours ago" bug). Stamping UTC here makes the wire
    format self-describing and the frontend needs no timezone handling."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


# Drop-in for `datetime` response fields: same validation, but JSON output is
# always UTC with a trailing 'Z'. Use wherever a timestamp crosses the wire.
UtcDateTime = Annotated[datetime, PlainSerializer(_utc_iso, return_type=str, when_used="json")]


class ScanCreate(BaseModel):
    root_path: str
    # Bug A fix: optional list of additional roots to walk in the same
    # scan. When non-empty, the worker walks `root_paths`; when empty/
    # null, it walks only `root_path` (preserves the pre-Bug-A API
    # contract). The primary `root_path` is the one stored on the
    # Scan history row for display — the worker still walks the full
    # set so files under any configured watch folder land in the same
    # scan. Callers should include `root_path` as the first element of
    # `root_paths` when both are sent.
    root_paths: list[str] | None = None


class ScanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    root_path: str
    status: str
    # 'manual' | 'reparse' | 'watch' | ... — lets the UI label a resumed job
    # ("Resuming re-parse…") instead of always saying "scan".
    source: str = "manual"
    file_count: int
    matched_count: int = 0
    # PB-4: known after Phase 1 (file-walk) completes. Frontend uses it
    # for real-% banner + ETA display. Null while Phase 1 is in progress.
    estimated_total: int | None = None
    current_path: str | None = None
    created_at: UtcDateTime
    completed_at: UtcDateTime | None = None


class MatchOut(BaseModel):
    # populate_by_name lets us also accept `metadata` directly (not just the
    # validation_alias) in case some code path constructs the schema by name.
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    provider: str
    provider_id: str
    match_type: str
    confidence: float
    title: str | None
    year: int | None
    season_number: int | None = None
    episode_number: int | None = None
    episode_title: str | None = None
    poster_url: str | None = None
    overview: str | None = None
    is_selected: bool = False
    is_manual: bool = False
    series_group_id: str | None = None
    # #14 movie-collection identity — drives the Review page's collection band +
    # "missing in collection" completion (read by the frontend's collection merge).
    collection_id: str | None = None
    collection_name: str | None = None
    # Rich popup metadata — genres, cast, director, network, studio,
    # language, country, runtime, last_air_date, title_romaji, title_native,
    # alt_titles. Single JSON blob so the wire format doesn't fragment as
    # providers grow. Frontend reads keys defensively via topMatch.metadata.
    #
    # The SQLAlchemy attribute is `metadata_blob` (column-aliased to
    # `metadata` in SQL because `metadata` is a reserved name on
    # DeclarativeBase). The validation_alias here lets Pydantic read from
    # the Python attribute while still serializing as plain `metadata`.
    metadata: dict[str, Any] | None = Field(default=None, validation_alias="metadata_blob")


class MediaFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_path: str
    file_size: int | None
    media_type: str | None
    status: str
    parsed_data: dict[str, Any] | None = None
    series_key: str | None = None
    # Identity-variant suffix — empty string means default flavor; non-empty
    # like "jap" / "eng" / "directors-cut-10bit" lets the frontend show a
    # chip on the file row + prevents rename collisions between same-episode
    # variants. See MediaFile.variant_key in models.py for the schema notes.
    variant_key: str | None = None
    # Wanted subtitle languages this file is missing (2-letter codes), computed
    # against the user's `subtitles.languages` preference. ``None`` = unknown
    # (no preference set, or the file's container was never inspected); ``[]`` =
    # fully covered; non-empty drives the "No EN subs" chip + fetch action.
    missing_subs: list[str] | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    matches: list[MatchOut] = []  # ranked by confidence desc


class FileStatusUpdate(BaseModel):
    status: str  # 'pending' | 'approved' | 'rejected' | 'no_match'


class ManualMatch(BaseModel):
    provider: str
    provider_id: str
    title: str | None = None
    year: int | None = None
    poster_url: str | None = None
    overview: str | None = None
    media_type: str = "movie"  # movie | tv | anime | music


class SettingsBody(BaseModel):
    """Bulk upsert of settings keys. Values can be any JSON-serializable shape."""
    values: dict[str, Any]


class ProviderTestResponse(BaseModel):
    ok: bool
    detail: str | None = None
    latency_ms: int | None = None


class ProviderTestBody(BaseModel):
    """Candidate credentials for the 'Test' button. The settings page buffers
    edits until Save, so Test must validate the JUST-TYPED draft key, not the
    stale value on the server. All optional — an empty body falls back to the
    stored config (the 'test the saved key' behavior)."""
    api_key: str | None = None
    username: str | None = None
    password: str | None = None
