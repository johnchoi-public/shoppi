"""Provider-neutral calendar data models.

Nothing in here knows about Google. Each provider translates to/from these
types, and the same models will later generate the JSON schemas handed to the
LLM as tool definitions.

All datetimes must be timezone-aware (ISO 8601 with offset), per the MVP rule
that the model never emits naive times.
"""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, Field, model_validator


class Event(BaseModel):
    """An event as returned by a provider."""

    id: str
    title: str
    start: AwareDatetime
    end: AwareDatetime
    all_day: bool = False
    description: str | None = None
    location: str | None = None


class EventCreate(BaseModel):
    """Fields required to create an event."""

    title: str = Field(min_length=1)
    start: AwareDatetime
    end: AwareDatetime
    description: str | None = None
    location: str | None = None

    @model_validator(mode="after")
    def _end_after_start(self) -> "EventCreate":
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class EventUpdate(BaseModel):
    """Partial update: only fields that are set (not None) are changed."""

    title: str | None = Field(default=None, min_length=1)
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    description: str | None = None
    location: str | None = None

    @model_validator(mode="after")
    def _end_after_start(self) -> "EventUpdate":
        if self.start and self.end and self.end <= self.start:
            raise ValueError("end must be after start")
        return self

