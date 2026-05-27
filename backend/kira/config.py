from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KIRA_",
        env_file=".env",
        extra="ignore",
    )

    database_url: str = "sqlite+aiosqlite:///./kira.db"
    media_root: str = "/media"

    # Bootstrap keys for dev. In production these come from the per-provider
    # config row in the settings table (so the user can change them from the UI).
    tmdb_api_key: str | None = Field(default=None)
    tvdb_api_key: str | None = Field(default=None)

    # CORS origins for the React dev server.
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


settings = Settings()
