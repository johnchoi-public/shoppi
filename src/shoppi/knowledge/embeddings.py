"""Build the embedding model from configuration (provider-agnostic)."""

from __future__ import annotations

from langchain_core.embeddings import Embeddings

from shoppi.config import Settings, get_settings

_embeddings: Embeddings | None = None


def build_embeddings(settings: Settings) -> Embeddings:
    # Imports are local so unused providers don't need to be installed.
    if settings.embedding_provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(model=settings.embedding_model, base_url=settings.embedding_base_url)
    if settings.embedding_provider == "huggingface":
        # Requires `pip install langchain-huggingface sentence-transformers`.
        from langchain_huggingface import HuggingFaceEmbeddings

        return HuggingFaceEmbeddings(model_name=settings.embedding_model)
    if settings.embedding_provider == "openai":
        # Requires `pip install langchain-openai`; also works for OpenAI-compatible servers.
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=settings.embedding_model, base_url=settings.embedding_base_url)
    raise ValueError(f"Unknown embedding provider: {settings.embedding_provider!r}")


def get_embeddings() -> Embeddings:
    global _embeddings
    if _embeddings is None:
        _embeddings = build_embeddings(get_settings())
    return _embeddings


def set_embeddings(embeddings: Embeddings | None) -> None:
    """Replace the embedding model (tests)."""
    global _embeddings
    _embeddings = embeddings
