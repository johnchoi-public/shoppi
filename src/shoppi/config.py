"""Application settings, loaded from environment variables / a local .env file.

Every variable is prefixed with SHOPPI_ (see .env.example).
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHOPPI_", env_file=".env", extra="ignore")

    # --- Calendar backend ---
    # "local" = the internal SQLite calendar; "google" = the user's Google Calendar.
    calendar_provider: Literal["local", "google"] = "local"
    # SQLAlchemy URL for the internal calendar. Swap for postgresql+psycopg://... later.
    calendar_db_url: str = "sqlite:///data/calendar.db"

    # Google OAuth "Desktop app" client file downloaded from Google Cloud Console.
    google_client_secrets_file: Path = Path("secrets/client_secret.json")

    # Fernet key used to encrypt the stored Google refresh token.
    # Only required when calendar_provider == "google".
    token_key: str | None = None
    token_file: Path = Path("secrets/google_token.enc")

    # IANA timezone name of the user. Used for date math and prompt injection.
    timezone: str = "UTC"

    # --- Chat model (any LangChain-supported provider) ---
    # `llm_provider` is a name understood by langchain's init_chat_model
    # ("ollama", "openai", "anthropic", ...). The model name is provider-specific.
    # To use vLLM later: provider "openai" + llm_base_url pointing at its /v1 endpoint.
    llm_provider: str = "ollama"
    llm_model: str = "nemotron-3.5-lightning"
    llm_base_url: str | None = None  # None = provider default (Ollama: localhost:11434)
    llm_temperature: float = 0.0  # tool calling is more reliable when deterministic
    # Context window cap in tokens. Ollama-specific: lowering it shrinks the KV cache
    # and so memory use. Leave unset for other providers.
    llm_num_ctx: int | None = None

    # --- Embeddings for the document store (also provider-agnostic) ---
    embedding_provider: str = "ollama"  # "ollama" | "huggingface"
    embedding_model: str = "nomic-embed-text"
    embedding_base_url: str | None = None

    # --- Document store (read-only knowledge base) ---
    docs_dir: Path = Path("docs_store/source")  # where the user's documents live
    vector_dir: Path = Path("docs_store/chroma")  # persisted Chroma index

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        # Fail at startup with a useful hint rather than deep inside a calendar call.
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(
                f"'{v}' is not a valid IANA timezone. Use e.g. America/Los_Angeles "
                "(there is no America/San_Francisco)."
            ) from e
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
