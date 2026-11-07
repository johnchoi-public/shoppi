"""Build the embedding model from configuration (provider-agnostic)."""

from __future__ import annotations

from langchain_core.embeddings import Embeddings

from shoppi.config import Settings


def build_embeddings(settings: Settings) -> Embeddings:
    # Imports are local so unused providers don't need to be installed.
    if settings.embedding_provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(model=settings.embedding_model, base_url=settings.embedding_base_url)
    if settings.embedding_provider == "huggingface":
        # Requires `pip install langchain-huggingface sentence-transformers`.
        from langchain_huggingface import HuggingFaceEmbeddings

        return HuggingFaceEmbeddings(model_name=settings.embedding_model)
    raise ValueError(f"Unknown embedding provider: {settings.embedding_provider!r}")
