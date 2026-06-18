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

    # CORS origins for the React dev server (5173) and the secondary
    # dev/preview server (5181), so a second Vite instance can talk to the
    # same backend during development.
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5181",
        "http://127.0.0.1:5181",
    ]

    # Optional HTTP Basic auth on the API. OFF by default (both unset) so
    # localhost / existing setups are untouched. Set BOTH (env KIRA_AUTH_USER +
    # KIRA_AUTH_PASS) to require credentials on every API request — recommended
    # whenever Kira is reachable beyond localhost. Health + token-gated webhooks
    # stay exempt so container probes and *arr callbacks keep working.
    auth_user: str | None = Field(default=None)
    auth_pass: str | None = Field(default=None)


settings = Settings()
