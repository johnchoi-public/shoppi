"""The calendar abstraction the rest of the app (and the LLM tools) depend on.

To support another scheduling app (Outlook, CalDAV, ...), implement
`CalendarProvider` and select it in configuration. No other code changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from shoppi.calendar.models import Event, EventCreate, EventUpdate


class CalendarProvider(ABC):
    @abstractmethod
    def list_events(self, start: datetime, end: datetime, query: str | None = None) -> list[Event]:
        """Events overlapping [start, end), ordered by start time.

        `query` is an optional free-text filter (title/description match).
        """

    @abstractmethod
    def get_event(self, event_id: str) -> Event:
        """Fetch a single event by id. Raises KeyError if it does not exist."""

    @abstractmethod
    def create_event(self, event: EventCreate) -> Event:
        """Create an event and return it with its provider-assigned id."""

    @abstractmethod
    def update_event(self, event_id: str, changes: EventUpdate) -> Event:
        """Apply a partial update and return the updated event."""

    @abstractmethod
    def delete_event(self, event_id: str) -> None:
        """Delete an event. Raises KeyError if it does not exist."""
