"""Build the chat model from configuration.

This is the only place that picks a model provider. Everything else receives a
generic LangChain `BaseChatModel`, so switching between Ollama, a vLLM /
OpenAI-compatible server ("openai" + base_url), Anthropic or any other provider
LangChain supports is a change to SHOPPI_LLM_PROVIDER / SHOPPI_LLM_MODEL only.
"""

from __future__ import annotations

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from shoppi.config import Settings, get_settings

_model: BaseChatModel | None = None


def build_chat_model(settings: Settings) -> BaseChatModel:
    kwargs: dict = {"temperature": settings.llm_temperature}
    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url
    if settings.llm_num_ctx:
        kwargs["num_ctx"] = settings.llm_num_ctx
    return init_chat_model(settings.llm_model, model_provider=settings.llm_provider, **kwargs)


def get_chat_model() -> BaseChatModel:
    """The process-wide chat model (built lazily, so the app starts without an LLM running)."""
    global _model
    if _model is None:
        _model = build_chat_model(get_settings())
    return _model


def set_chat_model(model: BaseChatModel | None) -> None:
    """Replace the chat model (tests, or to swap providers at runtime)."""
    global _model
    _model = model
