from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kira.database import Base


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    root_path: Mapped[str] = mapped_column(String)
    # Phases: scanning → matching → completed | completed_partial | failed: <reason>
    status: Mapped[str] = mapped_column(String, default="pending")
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    matched_count: Mapped[int] = mapped_column(Integer, default=0)
    # PB-4: estimated_total is set ONCE Phase 1 (file walk) completes —
    # at that point the scanner knows the universe of files Phase 2 will
    # match. Frontend uses it to compute a real % + ETA in the scan
    # banner instead of the "no idea, watch the count climb" experience.
    # Null during Phase 1 (still discovering files) and on failed scans.
    estimated_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_path: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    files: Mapped[list["MediaFile"]] = relationship(back_populates="scan")


class MediaFile(Base):
    __tablename__ = "media_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int | None] = mapped_column(ForeignKey("scans.id"), nullable=True)
    file_path: Mapped[str] = mapped_column(String, unique=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    media_type: Mapped[str | None] = mapped_column(String, nullable=True)  # movie|tv|anime|music
    # Statuses: discovered | parsed | matching | matched | approved | rejected | renamed
    status: Mapped[str] = mapped_column(String, default="discovered")
    parsed_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Clustering key — files sharing this value form one series card on the
    # Review page. Format: "{media_type}|{normalized_title}|{season|''}".
    # Null for movies (never cluster) and for files we couldn't parse a title from.
    series_key: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    # Identity-variant key — disambiguates files of the SAME logical episode
    # that exist in multiple flavors (audio language, edition, bit depth).
    # Example: `Frieren.01.JAP.mkv` and `Frieren.01.ENG.mkv` share the same
    # series_key + episode_number but get distinct variant_keys ("jap" vs
    # "eng") so the renamer can produce non-colliding output filenames
    # (`...Episode 01.JAP.mkv` / `...Episode 01.ENG.mkv`) and the UI can
    # show a "🇯🇵 JAP" / "🇬🇧 ENG" chip next to each file row.
    # Empty string when no variant signal was detected (the default).
    # Format: "{audio_lang}|{edition}|{bit_depth}", normalized & lowercased.
    variant_key: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    scan: Mapped[Scan | None] = relationship(back_populates="files")
    matches: Mapped[list["Match"]] = relationship(back_populates="media_file", cascade="all, delete-orphan")


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    media_file_id: Mapped[int] = mapped_column(ForeignKey("media_files.id"))
    provider: Mapped[str] = mapped_column(String)  # tmdb|tvdb|anidb|musicbrainz
    provider_id: Mapped[str] = mapped_column(String)
    # Canonical franchise identity used for visual grouping on the Review
    # page. Format: "{provider}:{canonical_id}". For AniDB sequel chains
    # we use the lowest AID in the franchise (e.g. all 5 Rent-a-Girlfriend
    # seasons share series_group_id="anidb:15299"). For TMDB/TVDB which
    # already have one ID per franchise, it's just "{provider}:{provider_id}".
    series_group_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    match_type: Mapped[str] = mapped_column(String)  # movie|tv_episode|track
    confidence: Mapped[float] = mapped_column()
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    series_name: Mapped[str | None] = mapped_column(String, nullable=True)
    season_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode_title: Mapped[str | None] = mapped_column(String, nullable=True)
    poster_url: Mapped[str | None] = mapped_column(String, nullable=True)
    overview: Mapped[str | None] = mapped_column(String, nullable=True)
    # `none_as_null=True` — without this, SQLAlchemy stores Python None as the
    # JSON text "null" (the 4-char string) instead of SQL NULL. The auto-heal
    # trigger uses `metadata_blob IS NULL` which only matches SQL NULL, so
    # silently-text-stored "null" rows became invisible to the heal sweep.
    metadata_blob: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON(none_as_null=True), nullable=True,
    )
    is_selected: Mapped[bool] = mapped_column(default=False)
    # User explicitly picked this match (manual search OR bulk match). The
    # auto-heal loop, /rematch-all, and scan-time rematch all skip files
    # whose selected match has this flag — user's choice is law and must
    # survive every subsequent matcher pass.
    is_manual: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    media_file: Mapped[MediaFile] = relationship(back_populates="matches")


class Setting(Base):
    """Key-value store for runtime config (provider configs, naming profile, etc.)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)


class RenameHistory(Base):
    """One row per file operation. Drives the History page + undo."""

    __tablename__ = "rename_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ON DELETE SET NULL: if the underlying MediaFile is deleted, this
    # history row stays (preserves "we renamed X → Y in the past" even
    # after X is gone) but its FK is automatically nulled by the DB.
    # H9 fix: previously a Python-side `UPDATE → DELETE` sequence could
    # leave orphans if the commit partially failed mid-flush; the FK
    # constraint now enforces it at the DB layer regardless.
    media_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_files.id", ondelete="SET NULL"), nullable=True,
    )
    match_id: Mapped[int | None] = mapped_column(
        ForeignKey("matches.id", ondelete="SET NULL"), nullable=True,
    )
    old_path: Mapped[str] = mapped_column(String)
    new_path: Mapped[str] = mapped_column(String)
    operation: Mapped[str] = mapped_column(String)  # move|copy|symlink|hardlink
    template_used: Mapped[str | None] = mapped_column(String, nullable=True)
    media_type: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    poster_url: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    undone_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Notification(Base):
    """Persistent notifications surfaced by the bell icon."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String)        # info|success|warning|error
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str | None] = mapped_column(String, nullable=True)
    read: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
