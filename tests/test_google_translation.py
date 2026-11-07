"""Offline tests for Google <-> neutral model translation (no network)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from shoppi.calendar.google import GoogleCalendarProvider
from shoppi.calendar.models import EventCreate

NY = ZoneInfo("America/New_York")


def test_to_google_uses_iana_timezone():
    start = datetime(2026, 11, 2, 10, 0, tzinfo=NY)
    body = GoogleCalendarProvider._to_google({"title": "Lunch", "start": start})
    assert body["summary"] == "Lunch"
    assert body["start"] == {"dateTime": start.isoformat(), "timeZone": "America/New_York"}


def test_from_google_handles_all_day():
    # Bypass __init__ (it builds a network client); only _tz is needed here.
    provider = GoogleCalendarProvider.__new__(GoogleCalendarProvider)
    provider._tz = NY
    ev = provider._from_google({"id": "a", "start": {"date": "2026-11-02"}, "end": {"date": "2026-11-03"}})
    assert ev.all_day and ev.start.tzinfo is not None and ev.title == "(no title)"


def test_naive_datetime_rejected():
    with pytest.raises(ValidationError):
        EventCreate(title="x", start=datetime(2026, 1, 1, 9), end=datetime(2026, 1, 1, 10))


def test_end_before_start_rejected():
    with pytest.raises(ValidationError):
        EventCreate(title="x", start=datetime(2026, 1, 1, 10, tzinfo=NY), end=datetime(2026, 1, 1, 9, tzinfo=NY))
