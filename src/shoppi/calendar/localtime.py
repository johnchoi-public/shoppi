"""Conversion between the user's local wall-clock time and aware datetimes.

Both the LLM tools and the web API speak *local naive* times
(e.g. 2026-11-01T15:00:00 = 3pm in the user's timezone). This keeps clients from
having to know UTC offsets: zoneinfo computes the right offset for each date,
including across DST changes. Providers only ever see aware datetimes.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from shoppi.calendar.models import Event


def localize(dt: datetime | None, tz: ZoneInfo) -> datetime | None:
    """Naive local wall-clock time -> aware datetime in `tz` (None passes through)."""
    return dt.replace(tzinfo=tz) if dt is not None else None


def event_to_local_dict(e: Event, tz: ZoneInfo) -> dict:
    """JSON-ready event with start/end as local naive ISO strings."""
    d = e.model_dump(mode="json")
    d["start"] = e.start.astimezone(tz).replace(tzinfo=None).isoformat()
    d["end"] = e.end.astimezone(tz).replace(tzinfo=None).isoformat()
    return d
