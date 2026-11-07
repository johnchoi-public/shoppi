"""Test doubles: a scripted chat model, an in-memory calendar, a canned doc store."""

from __future__ import annotations

from datetime import datetime
from itertools import count

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from shoppi.calendar.base import CalendarProvider
from shoppi.calendar.models import Event, EventCreate, EventUpdate
from shoppi.knowledge.base import DocumentStore, SearchHit


class ScriptedChatModel(BaseChatModel):
    """Replays a fixed list of AIMessages, one per model call.

    Also records the messages it was shown so tests can inspect the prompt.
    """

    script: list[AIMessage]
    seen: list[list] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self  # tool schemas are irrelevant to a scripted model

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


class InMemoryCalendar(CalendarProvider):
    def __init__(self, events: list[Event] | None = None):
        self.events = {e.id: e for e in events or []}
        self._ids = count(1)

    def list_events(self, start: datetime, end: datetime, query: str | None = None) -> list[Event]:
        hits = [e for e in self.events.values() if e.start < end and e.end > start]
        if query:
            hits = [e for e in hits if query.lower() in e.title.lower()]
        return sorted(hits, key=lambda e: e.start)

    def get_event(self, event_id: str) -> Event:
        return self.events[event_id]

    def create_event(self, event: EventCreate) -> Event:
        ev = Event(id=f"ev{next(self._ids)}", **event.model_dump())
        self.events[ev.id] = ev
        return ev

    def update_event(self, event_id: str, changes: EventUpdate) -> Event:
        ev = self.events[event_id].model_copy(update=changes.model_dump(exclude_none=True))
        self.events[event_id] = ev
        return ev

    def delete_event(self, event_id: str) -> None:
        del self.events[event_id]


class CannedDocs(DocumentStore):
    def search(self, query: str, k: int = 4) -> list[SearchHit]:
        return [SearchHit(text="Team standup is always 9:30am.", source="team.md")]
