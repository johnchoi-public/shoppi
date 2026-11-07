"""Google Calendar implementation of `CalendarProvider`.

Google Calendar is the source of truth: every call goes to the API and nothing
is cached locally (a local DB + syncToken is a post-MVP item).
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from shoppi.calendar.base import CalendarProvider
from shoppi.calendar.models import Event, EventCreate, EventUpdate

# The user's primary calendar. Multi-calendar support is out of MVP scope.
CALENDAR_ID = "primary"


class GoogleCalendarProvider(CalendarProvider):
    def __init__(self, creds: Credentials, tz: ZoneInfo):
        # `tz` is the user's timezone; used to interpret all-day events.
        self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        self._tz = tz

    # ---- CalendarProvider API -------------------------------------------------

    def list_events(self, start: datetime, end: datetime, query: str | None = None) -> list[Event]:
        events: list[Event] = []
        page_token = None
        while True:
            resp = (
                self._service.events()
                .list(
                    calendarId=CALENDAR_ID,
                    timeMin=start.isoformat(),
                    timeMax=end.isoformat(),
                    q=query,
                    singleEvents=True,  # expand recurring events into instances
                    orderBy="startTime",
                    pageToken=page_token,
                )
                .execute()
            )
            events.extend(self._from_google(item) for item in resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return events

    def get_event(self, event_id: str) -> Event:
        return self._from_google(self._call_event(self._service.events().get(calendarId=CALENDAR_ID, eventId=event_id)))

    def create_event(self, event: EventCreate) -> Event:
        body = self._to_google(event.model_dump(exclude_none=True))
        return self._from_google(self._call_event(self._service.events().insert(calendarId=CALENDAR_ID, body=body)))

    def update_event(self, event_id: str, changes: EventUpdate) -> Event:
        # PATCH semantics: only the supplied fields are modified.
        body = self._to_google(changes.model_dump(exclude_none=True))
        return self._from_google(
            self._call_event(self._service.events().patch(calendarId=CALENDAR_ID, eventId=event_id, body=body))
        )

    def delete_event(self, event_id: str) -> None:
        self._call_event(self._service.events().delete(calendarId=CALENDAR_ID, eventId=event_id))

    # ---- Translation helpers --------------------------------------------------

    @staticmethod
    def _call_event(request):
        """Execute a request, mapping Google's 404/410 to KeyError for the abstract API."""
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status in (404, 410):
                raise KeyError("event not found") from e
            raise

    @staticmethod
    def _to_google(fields: dict) -> dict:
        """Neutral model fields -> Google event resource (partial)."""
        body: dict = {}
        if "title" in fields:
            body["summary"] = fields["title"]
        if "description" in fields:
            body["description"] = fields["description"]
        if "location" in fields:
            body["location"] = fields["location"]
        # Google wants dateTime plus an IANA timeZone name for correct DST handling.
        for key in ("start", "end"):
            if key in fields:
                dt: datetime = fields[key]
                body[key] = {"dateTime": dt.isoformat(), "timeZone": getattr(dt.tzinfo, "key", "UTC")}
        return body

    def _from_google(self, item: dict) -> Event:
        """Google event resource -> neutral Event."""
        start, all_day = self._parse_when(item["start"])
        end, _ = self._parse_when(item["end"])
        return Event(
            id=item["id"],
            title=item.get("summary", "(no title)"),
            start=start,
            end=end,
            all_day=all_day,
            description=item.get("description"),
            location=item.get("location"),
        )

    def _parse_when(self, when: dict) -> tuple[datetime, bool]:
        # Timed events carry `dateTime`; all-day events carry only `date`.
        if "dateTime" in when:
            # Google returns a bare UTC offset; convert to the user's named zone so
            # later writes carry the right IANA timeZone and DST behaves correctly.
            return datetime.fromisoformat(when["dateTime"]).astimezone(self._tz), False
        return datetime.combine(date.fromisoformat(when["date"]), time.min, tzinfo=self._tz), True
