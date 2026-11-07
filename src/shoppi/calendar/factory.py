"""Pick the calendar backend from configuration.

This is the only place that knows which concrete providers exist. The agent,
tools, CLI and web app all receive a plain `CalendarProvider`.
"""

from __future__ import annotations

from shoppi.calendar.base import CalendarProvider
from shoppi.config import Settings


def build_calendar(settings: Settings) -> CalendarProvider:
    # Imports are local so the Google client libraries load only when used.
    if settings.calendar_provider == "local":
        from shoppi.calendar.local import LocalCalendarProvider, create_db_engine

        return LocalCalendarProvider(create_db_engine(settings.calendar_db_url), settings.tz)

    if settings.calendar_provider == "google":
        from shoppi.calendar.google import GoogleCalendarProvider
        from shoppi.calendar.google_auth import load_credentials
        from shoppi.storage.token_store import EncryptedFileTokenStore

        if not settings.token_key:
            raise ValueError("SHOPPI_TOKEN_KEY is required when SHOPPI_CALENDAR_PROVIDER=google")
        store = EncryptedFileTokenStore(settings.token_file, settings.token_key)
        return GoogleCalendarProvider(load_credentials(store), settings.tz)

    raise ValueError(f"Unknown calendar provider: {settings.calendar_provider!r}")
