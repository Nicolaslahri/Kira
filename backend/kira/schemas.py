from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScanCreate(BaseModel):
    root_path: str


class ScanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    root_path: str
    status: str
    file_count: int
    matched_count: int = 0
    # PB-4: known after Phase 1 (file-walk) completes. Frontend uses it
    # for real-% banner + ETA display. Null while Phase 1 is in progress.
    estimated_total: int | None = None
    current_path: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


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
    created_at: datetime
    updated_at: datetime
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
