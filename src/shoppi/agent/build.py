"""Assemble the LangGraph agent.

`create_agent` compiles a graph implementing the loop
model -> tool call -> tool result -> model until the model answers in text.
The graph is built once per process; who is asking is passed per request as the
runtime `context` (an Actor), which is how tools know the shop and user.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, dynamic_prompt
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import InMemorySaver

from shoppi.agent.prompt import build_system_prompt
from shoppi.agent.tools import build_tools
from shoppi.core.actor import Actor
from shoppi.db.models import Shop
from shoppi.db.session import tenant_tx


def build_agent(model: BaseChatModel, checkpointer=None, tools=None):
    # Re-evaluated before every model call, so "now" is never stale mid-conversation.
    @dynamic_prompt
    def system_prompt(request: ModelRequest) -> str:
        actor: Actor = request.runtime.context
        with tenant_tx(actor) as tx:
            shop = tx.get(Shop, actor.shop_id)
            name, tz = shop.name, ZoneInfo(shop.timezone)
        return build_system_prompt(datetime.now(tz), tz, shop_name=name, user_name=actor.name, role=actor.role)

    return create_agent(
        model,
        tools=tools if tools is not None else build_tools(),
        middleware=[system_prompt],
        context_schema=Actor,
        # Conversation state, keyed by a server-derived thread id (shop:user:thread).
        checkpointer=checkpointer or InMemorySaver(),
    )


def thread_key(actor: Actor, thread_id) -> str:
    """Checkpointer key. Derived on the server from the session, so one user can
    never read another user's (or another shop's) conversation by guessing an id."""
    return f"{actor.shop_id}:{actor.user_id}:{thread_id}"
