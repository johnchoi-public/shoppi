"""Build the chat model from configuration.

This is the only place that picks a model provider. Everything else receives a
generic LangChain `BaseChatModel`, so switching from Ollama to vLLM (via the
"openai" provider with a base_url) or a hosted API is a config change.
"""

from __future__ import annotations

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from shoppi.config import Settings


def build_chat_model(settings: Settings) -> BaseChatModel:
    kwargs: dict = {"temperature": settings.llm_temperature}
    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url
    if settings.llm_num_ctx:
        kwargs["num_ctx"] = settings.llm_num_ctx
    return init_chat_model(settings.llm_model, model_provider=settings.llm_provider, **kwargs)
