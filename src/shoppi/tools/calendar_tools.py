"""LangChain tools that expose a `CalendarProvider` to the model.

The tools are built from whichever provider is passed in, so they work
unchanged for Google or any other calendar backend. Args schemas are strict
pydantic models, which LangChain converts to the JSON schemas the model sees.

Time handling: the model only ever sees and writes LOCAL wall-clock times in the
user's timezone, with no UTC offset (e.g. 2026-11-01T15:00:00). Code attaches the
timezone, so the correct offset for that specific date (DST!) is computed by
zoneinfo, never guessed by the model. The provider-facing models stay fully
timezone-aware; the conversion happens only in this file.

Tool results are JSON strings. Event titles/descriptions inside them are
untrusted user data (prompt-injection surface); the system prompt says so.

NOTE: update/delete currently execute immediately. Confirmation + undo is
build step 4 and will wrap these two tools; do not expose this CLI to
untrusted input before then.
"""

from __future__ import annotations

import json
from zoneinfo import ZoneInfo

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, NaiveDatetime, model_validator

from shoppi.calendar.base import CalendarProvider
from shoppi.calendar.localtime import event_to_local_dict, localize
from shoppi.calendar.models import EventCreate, EventUpdate

LOCAL_TIME_HELP = "Local time in the user's timezone, ISO 8601 WITHOUT a UTC offset, e.g. 2026-11-06T15:00:00."


class ListEventsArgs(BaseModel):
    start: NaiveDatetime = Field(description=f"Range start. {LOCAL_TIME_HELP}")
    end: NaiveDatetime = Field(description=f"Range end. {LOCAL_TIME_HELP}")
    query: str | None = Field(default=None, description="Optional text to filter titles/descriptions.")


class CreateEventArgs(BaseModel):
    title: str = Field(min_length=1)
    start: NaiveDatetime = Field(description=f"Event start. {LOCAL_TIME_HELP}")
    end: NaiveDatetime = Field(description=f"Event end, after start. {LOCAL_TIME_HELP}")
    description: str | None = None
    location: str | None = None

    @model_validator(mode="after")
    def _end_after_start(self) -> "CreateEventArgs":
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class UpdateEventArgs(BaseModel):
    event_id: str = Field(description="An id previously returned by list_events. Never invent ids.")
    title: str | None = Field(default=None, min_length=1)
    start: NaiveDatetime | None = Field(default=None, description=f"New start. {LOCAL_TIME_HELP}")
    end: NaiveDatetime | None = Field(default=None, description=f"New end. {LOCAL_TIME_HELP}")
    description: str | None = None
    location: str | None = None


class DeleteEventArgs(BaseModel):
    event_id: str = Field(description="An id previously returned by list_events. Never invent ids.")


NOT_FOUND = "Error: no event with that id. Call list_events first and use an id it returned."


def build_calendar_tools(cal: CalendarProvider, tz: ZoneInfo) -> list[BaseTool]:
    def list_events(start, end, query: str | None = None) -> str:
        events = cal.list_events(localize(start, tz), localize(end, tz), query)
        return json.dumps([event_to_local_dict(e, tz) for e in events])

    def create_event(title, start, end, description=None, location=None) -> str:
        created = cal.create_event(
            EventCreate(
                title=title, start=localize(start, tz), end=localize(end, tz), description=description, location=location
            )
        )
        return json.dumps(event_to_local_dict(created, tz))

    def update_event(event_id, title=None, start=None, end=None, description=None, location=None) -> str:
        changes = EventUpdate(
            title=title, start=localize(start, tz), end=localize(end, tz), description=description, location=location
        )
        try:
            return json.dumps(event_to_local_dict(cal.update_event(event_id, changes), tz))
        except KeyError:
            return NOT_FOUND

    def delete_event(event_id) -> str:
        try:
            cal.delete_event(event_id)
        except KeyError:
            return NOT_FOUND
        return "Deleted."

    return [
        StructuredTool.from_function(
            func=list_events,
            name="list_events",
            description="List calendar events overlapping a time range. Always call this before changing or deleting an event.",
            args_schema=ListEventsArgs,
        ),
        StructuredTool.from_function(
            func=create_event,
            name="create_event",
            description="Create a calendar event.",
            args_schema=CreateEventArgs,
        ),
        StructuredTool.from_function(
            func=update_event,
            name="update_event",
            description="Change fields of an existing event (only the provided fields change).",
            args_schema=UpdateEventArgs,
        ),
        StructuredTool.from_function(
            func=delete_event,
            name="delete_event",
            description="Delete an existing event.",
            args_schema=DeleteEventArgs,
        ),
    ]
