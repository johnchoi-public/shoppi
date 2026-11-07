"""Build step 2: chat with the calendar agent in the terminal.

Wires config -> (chat model, calendar provider, Chroma docs) -> LangGraph agent.
The calendar backend (internal SQLite or Google) comes from SHOPPI_CALENDAR_PROVIDER.
Type 'exit' to quit.
"""

from uuid import uuid4

from shoppi.agent.build import build_agent
from shoppi.calendar.factory import build_calendar
from shoppi.config import get_settings
from shoppi.knowledge.chroma_store import ChromaDocumentStore
from shoppi.knowledge.embeddings import build_embeddings
from shoppi.llm.factory import build_chat_model

s = get_settings()
calendar = build_calendar(s)
documents = ChromaDocumentStore(s.vector_dir, build_embeddings(s))
agent = build_agent(build_chat_model(s), calendar, documents, s.tz)

config = {"configurable": {"thread_id": str(uuid4())}}  # one conversation per run
while True:
    user = input("\nyou> ").strip()
    if user.lower() in {"exit", "quit"}:
        break
    if not user:
        continue
    # stream_mode="updates" yields each graph step, so tool calls are visible as they happen.
    for step in agent.stream({"messages": [("user", user)]}, config, stream_mode="updates"):
        for node, update in step.items():
            for msg in update.get("messages", []):
                for call in getattr(msg, "tool_calls", None) or []:
                    print(f"  [tool call] {call['name']}({call['args']})")
                if node == "tools":
                    print(f"  [tool result] {str(msg.content)[:200]}")
                elif msg.content:
                    print(f"assistant> {msg.content}")
