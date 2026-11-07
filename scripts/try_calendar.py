"""Build step 1 smoke test: exercise all four CRUD operations against real Google Calendar.

Creates a temporary event tomorrow at 10:00, moves it, then deletes it.
"""

from datetime import datetime, timedelta

from shoppi.calendar.google import GoogleCalendarProvider
from shoppi.calendar.google_auth import load_credentials
from shoppi.calendar.models import EventCreate, EventUpdate
from shoppi.config import get_settings
from shoppi.storage.token_store import EncryptedFileTokenStore

s = get_settings()
cal = GoogleCalendarProvider(load_credentials(EncryptedFileTokenStore(s.token_file, s.token_key)), s.tz)

# Tomorrow 10:00-11:00 in the user's timezone.
tomorrow = (datetime.now(s.tz) + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)

created = cal.create_event(EventCreate(title="shoppi smoke test", start=tomorrow, end=tomorrow + timedelta(hours=1)))
print("created:", created)

day_start = tomorrow.replace(hour=0)
print("listed :", [e.title for e in cal.list_events(day_start, day_start + timedelta(days=1))])

moved = cal.update_event(created.id, EventUpdate(start=tomorrow + timedelta(hours=2), end=tomorrow + timedelta(hours=3)))
print("moved  :", moved.start, "->", moved.end)

cal.delete_event(created.id)
print("deleted.")
