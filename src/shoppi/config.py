"""Application settings, loaded from environment variables / a local .env file.

Every variable is prefixed with SHOPPI_ (see .env.example).
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHOPPI_", env_file=".env", extra="ignore")

    # --- Database (Postgres + pgvector) ---
    # The web app connects as a non-owner role so row-level security applies to it.
    database_url: str = "postgresql+psycopg://shoppi_app:shoppi_app_dev@localhost:5432/shoppi"
    # Migrations run as the schema owner (creates tables, policies, triggers).
    database_owner_url: str = "postgresql+psycopg://shoppi_owner:shoppi_owner_dev@localhost:5432/shoppi"

    # --- Web ---
    # Public origin of the app; used to build OAuth redirect URLs and intake links.
    base_url: str = "http://localhost:8000"
    session_days: int = 14
    # Set true when served over HTTPS so the session cookie is marked Secure.
    cookie_secure: bool = False

    # Fernet key used to encrypt integration credentials (OAuth tokens, API keys).
    # Generate with: python -m shoppi.integrations.credentials
    token_key: str | None = None

    # --- Google OAuth "Web application" client (optional) ---
    # Enables "Sign in with Google" and the Google Calendar integration.
    google_client_id: str | None = None
    google_client_secret: str | None = None

    # --- Chat model (any LangChain-supported provider) ---
    # `llm_provider` is a name understood by langchain's init_chat_model
    # ("ollama", "openai", "anthropic", ...). The model name is provider-specific.
    # For a vLLM / OpenAI-compatible server: provider "openai" + llm_base_url.
    llm_provider: str = "ollama"
    llm_model: str = "nemotron-3.5-lightning"
    llm_base_url: str | None = None  # None = provider default (Ollama: localhost:11434)
    llm_temperature: float = 0.0  # tool calling is more reliable when deterministic
    # Context window cap in tokens (Ollama only). Leave unset for other providers.
    llm_num_ctx: int | None = None

    # --- Embeddings for the knowledge store (also provider-agnostic) ---
    embedding_provider: str = "ollama"  # "ollama" | "huggingface"
    embedding_model: str = "nomic-embed-text"
    embedding_base_url: str | None = None
    # Width of the pgvector column. It is fixed per deployment: changing the
    # embedding model to one with a different width needs a re-embed migration.
    embedding_dim: int = 768

    # Where uploaded documents are kept on disk (per-shop subfolders).
    upload_dir: Path = Path("data/uploads")

    # --- Outbound integrations ---
    vin_decoder_url: str = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"
    http_timeout_s: float = 8.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
