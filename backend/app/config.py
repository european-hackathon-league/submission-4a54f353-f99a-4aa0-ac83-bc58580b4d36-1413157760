"""Settings from environment variables and backend/.env (never committed)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
RAW_DATA_DIR = REPO_DIR / "All-context" / "02-data-provided-by-pm" / "raw"
SOURCES_FILE = REPO_DIR / "All-context" / "05-web-sources" / "fetching-sources.md"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # Supabase. SUPABASE_DB_URL is the Postgres connection string (session pooler).
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    # New-style API keys (sb_secret_..., sb_publishable_...); the secret key replaces the
    # service-role key for storage when that one is empty.
    supabase_secret_key: str = ""
    supabase_publishable_key: str = ""
    supabase_jwks_url: str = ""
    supabase_db_url: str = ""
    # API auth: "supabase" (Bearer token), "dev" (X-User-Id header), "auto" = supabase
    # when SUPABASE_URL is set, else dev. See app/api/auth.py.
    auth_mode: str = "auto"
    supabase_jwt_secret: str = ""
    # Comma-separated origins of the UI allowed by CORS.
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    # Fallback for development and tests: embedded local Postgres (pgserver).
    local_db_dir: Path = BACKEND_DIR / ".data" / "pg"

    # OpenAI.
    openai_api_key: str = ""
    openai_chat_model: str = "gpt-5-mini"
    openai_strong_model: str = "gpt-5"
    # Price per 1M tokens (input, output) in USD, used for cost logging only.
    openai_price_in: float = 0.25
    openai_price_out: float = 2.0

    # Embeddings: "openai" or "local" (offline fallback, see changes.md 1.2).
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # Raw record storage: "supabase" or "local" (see changes.md 1.3).
    storage_backend: str = "local"
    raw_local_dir: Path = BACKEND_DIR / ".data" / "raw-records"
    raw_bucket: str = "raw-records"

    # Web fetching.
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    youtube_api_key: str = ""
    fetch_user_agent: str = ("BMW-PM-Research-Prototype/0.1"
                             " (+https://github.com/ghassenzaara/BMW-Product-Management-Challenge)")
    fetch_daily_request_budget: int = 2000

    def model_post_init(self, __context) -> None:
        # Values still holding the .env.example placeholders count as not set.
        for name in ("supabase_url", "supabase_db_url", "supabase_service_role_key", "supabase_secret_key",
                     "supabase_jwks_url", "openai_api_key"):
            if "<" in getattr(self, name):
                setattr(self, name, "")
        if not self.supabase_service_role_key:
            self.supabase_service_role_key = self.supabase_secret_key
        if self.auth_mode in ("auto", ""):
            self.auth_mode = "supabase" if self.supabase_url else "dev"

    @property
    def db_url(self) -> str:
        return self.supabase_db_url

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
