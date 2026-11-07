"""Assemble the LangGraph agent from a model, a calendar, and a document store.

`create_agent` compiles a LangGraph graph implementing the loop
model -> tool call -> tool result -> model until the model answers in text.
Every dependency is passed in, so tests can substitute fakes and the rest of
the app never imports a concrete provider.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, dynamic_prompt
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import InMemorySaver

from shoppi.agent.prompt import build_system_prompt
from shoppi.calendar.base import CalendarProvider
from shoppi.knowledge.base import DocumentStore
from shoppi.tools.calendar_tools import build_calendar_tools
from shoppi.tools.doc_tools import build_document_tools


def build_agent(
    model: BaseChatModel,
    calendar: CalendarProvider,
    documents: DocumentStore | None,
    tz: ZoneInfo,
):
    tools = build_calendar_tools(calendar, tz)
    if documents is not None:
        tools += build_document_tools(documents)

    # Re-evaluated before every model call, so "now" is never stale mid-conversation.
    @dynamic_prompt
    def system_prompt(request: ModelRequest) -> str:
        return build_system_prompt(datetime.now(tz), tz)

    return create_agent(
        model,
        tools=tools,
        middleware=[system_prompt],
        # In-memory conversation state, keyed by thread_id at invoke time.
        # Swap for a persistent checkpointer when the web UI needs sessions.
        checkpointer=InMemorySaver(),
    )
