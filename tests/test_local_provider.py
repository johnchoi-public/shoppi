"""LocalCalendarProvider against a real (temporary) SQLite file."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from shoppi.calendar.local import LocalCalendarProvider, create_db_engine
from shoppi.calendar.models import EventCreate, EventUpdate

LA = ZoneInfo("America/Los_Angeles")


@pytest.fixture
def cal(tmp_path):
    return LocalCalendarProvider(create_db_engine(f"sqlite:///{tmp_path}/cal.db"), LA)


def at(day: int, hour: int) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=LA)


def test_crud_round_trip(cal):
    ev = cal.create_event(EventCreate(title="Standup", start=at(6, 9), end=at(6, 10), location="Room 1"))
    assert cal.get_event(ev.id).title == "Standup"

    moved = cal.update_event(ev.id, EventUpdate(start=at(6, 11), end=at(6, 12)))
    assert moved.start == at(6, 11) and moved.location == "Room 1"  # untouched fields kept

    cal.delete_event(ev.id)
    with pytest.raises(KeyError):
        cal.get_event(ev.id)


def test_list_uses_overlap_and_query(cal):
    cal.create_event(EventCreate(title="Dentist", start=at(6, 9), end=at(6, 10)))
    cal.create_event(EventCreate(title="Gym", start=at(6, 17), end=at(6, 18)))
    cal.create_event(EventCreate(title="Next day", start=at(7, 9), end=at(7, 10)))

    day = cal.list_events(at(6, 0), at(7, 0))
    assert [e.title for e in day] == ["Dentist", "Gym"]  # ordered, excludes next day
    assert [e.title for e in cal.list_events(at(6, 9) + timedelta(minutes=30), at(6, 11))] == ["Dentist"]
    assert [e.title for e in cal.list_events(at(1, 0), at(30, 0), "gym")] == ["Gym"]


def test_stored_as_utc_returned_in_user_zone_across_dst(cal):
    # Nov 1 2026 3pm is after the DST change: PST, UTC-8 -> 23:00 UTC.
    ev = cal.create_event(EventCreate(title="x", start=at(1, 15), end=at(1, 16)))
    assert ev.start.astimezone(timezone.utc).hour == 23
    assert ev.start.tzinfo == LA and ev.start.hour == 15


def test_update_cannot_invert_range(cal):
    ev = cal.create_event(EventCreate(title="x", start=at(6, 9), end=at(6, 10)))
    with pytest.raises(ValueError):
        cal.update_event(ev.id, EventUpdate(start=at(6, 11)))  # new start after existing end
    assert cal.get_event(ev.id).start == at(6, 9)  # rolled back


def test_missing_ids_raise_keyerror(cal):
    with pytest.raises(KeyError):
        cal.update_event("nope", EventUpdate(title="y"))
    with pytest.raises(KeyError):
        cal.delete_event("nope")
