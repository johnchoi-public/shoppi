"""Internal calendar: a `CalendarProvider` backed by our own SQL database.

Uses SQLAlchemy, so the default SQLite file can be swapped for Postgres by
changing `SHOPPI_CALENDAR_DB_URL`; nothing else changes.

Storage convention: start/end are stored as *naive UTC* datetimes. SQLite has no
timezone-aware column type, and UTC instants are unaffected by DST. They are
converted to the user's timezone on the way out, so callers always get aware
datetimes, exactly like the Google provider.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Boolean, DateTime, Engine, String, Text, create_engine, event, or_, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from shoppi.calendar.base import CalendarProvider
from shoppi.calendar.models import Event, EventCreate, EventUpdate


class _Base(DeclarativeBase):
    pass


class EventRow(_Base):
    """One row per event. Recurring events are out of MVP scope."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    start_utc: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_utc: Mapped[datetime] = mapped_column(DateTime, index=True)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(DateTime)


def _to_utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def create_db_engine(url: str) -> Engine:
    """Create the engine and the schema if missing.

    For the MVP the schema is created directly; introduce Alembic migrations
    once the table shape needs to change with real data in it.
    """
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        # Make sure the directory for the SQLite file exists.
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

    # The web server calls the provider from worker threads.
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)

    if url.startswith("sqlite"):
        # WAL lets the UI read while the agent writes without "database is locked".
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(conn, _record):
            conn.execute("PRAGMA journal_mode=WAL")

    _Base.metadata.create_all(engine)
    return engine


class LocalCalendarProvider(CalendarProvider):
    def __init__(self, engine: Engine, tz: ZoneInfo):
        # expire_on_commit=False: rows stay readable after commit, so they can be
        # converted to `Event`s once the transaction has closed.
        self._session = sessionmaker(engine, expire_on_commit=False)
        self._tz = tz  # timezone used for returned datetimes

    # ---- CalendarProvider API -------------------------------------------------

    def list_events(self, start: datetime, end: datetime, query: str | None = None) -> list[Event]:
        # Overlap test: an event is in range if it starts before `end` and ends after `start`.
        stmt = (
            select(EventRow)
            .where(EventRow.start_utc < _to_utc_naive(end), EventRow.end_utc > _to_utc_naive(start))
            .order_by(EventRow.start_utc)
        )
        if query:
            like = f"%{query}%"
            stmt = stmt.where(or_(EventRow.title.ilike(like), EventRow.description.ilike(like)))
        with self._session() as s:
            return [self._to_event(r) for r in s.scalars(stmt)]

    def get_event(self, event_id: str) -> Event:
        with self._session() as s:
            return self._to_event(self._row(s, event_id))

    def create_event(self, event: EventCreate) -> Event:
        row = EventRow(
            id=uuid4().hex,
            title=event.title,
            start_utc=_to_utc_naive(event.start),
            end_utc=_to_utc_naive(event.end),
            description=event.description,
            location=event.location,
            updated_at=_to_utc_naive(datetime.now(timezone.utc)),
        )
        with self._session() as s, s.begin():
            s.add(row)
        return self._to_event(row)

    def update_event(self, event_id: str, changes: EventUpdate) -> Event:
        with self._session() as s, s.begin():
            row = self._row(s, event_id)
            fields = changes.model_dump(exclude_none=True)
            # Map neutral field names onto columns; times are normalized to UTC.
            for key, value in fields.items():
                if key in ("start", "end"):
                    setattr(row, f"{key}_utc", _to_utc_naive(value))
                else:
                    setattr(row, key, value)
            # A partial time change must still leave a valid range.
            if row.end_utc <= row.start_utc:
                raise ValueError("end must be after start")
            row.updated_at = _to_utc_naive(datetime.now(timezone.utc))
            s.flush()
            return self._to_event(row)

    def delete_event(self, event_id: str) -> None:
        with self._session() as s, s.begin():
            s.delete(self._row(s, event_id))

    # ---- Helpers --------------------------------------------------------------

    @staticmethod
    def _row(s: Session, event_id: str) -> EventRow:
        row = s.get(EventRow, event_id)
        if row is None:
            raise KeyError("event not found")  # same contract as the Google provider
        return row

    def _to_event(self, r: EventRow) -> Event:
        def local(dt: datetime) -> datetime:
            return dt.replace(tzinfo=timezone.utc).astimezone(self._tz)

        return Event(
            id=r.id,
            title=r.title,
            start=local(r.start_utc),
            end=local(r.end_utc),
            all_day=r.all_day,
            description=r.description,
            location=r.location,
        )
