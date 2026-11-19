"""Google: sign-in and an optional calendar mirror.

The shop's built-in calendar is the source of truth. When connected, this pushes
it one way to a Google calendar so people can see the schedule on their phones.
Nothing in the app depends on it.

Requires a Google OAuth "Web application" client (SHOPPI_GOOGLE_CLIENT_ID /
SHOPPI_GOOGLE_CLIENT_SECRET) with these redirect URIs:
    <base_url>/api/integrations/google_calendar/callback
    <base_url>/api/auth/google/callback
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from shoppi.config import get_settings
from shoppi.db.models import SyncMap
from shoppi.integrations.base import CalendarMirror, Integration, IntegrationError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
SIGNIN_SCOPE = "openid email profile"

# Which kinds of calendar item are mirrored by default.
DEFAULT_KINDS = ["job", "event", "bay_closure"]


def google_configured() -> bool:
    s = get_settings()
    return bool(s.google_client_id and s.google_client_secret)


def authorize_url(scope: str, state: str, redirect_uri: str, *, offline: bool) -> str:
    params = {"client_id": get_settings().google_client_id, "redirect_uri": redirect_uri, "response_type": "code",
              "scope": scope, "state": state, "include_granted_scopes": "true"}
    if offline:
        params.update(access_type="offline", prompt="consent")
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str, redirect_uri: str) -> dict:
    s = get_settings()
    try:
        resp = httpx.post(TOKEN_URL, timeout=s.http_timeout_s, data={
            "code": code, "client_id": s.google_client_id, "client_secret": s.google_client_secret,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        raise IntegrationError("Google sign-in failed. Please try again.") from e


def verify_id_token(token: str) -> dict:
    """Validate a Google ID token (signature, audience, expiry) and return its claims."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    try:
        return id_token.verify_oauth2_token(token, google_requests.Request(), get_settings().google_client_id)
    except ValueError as e:
        raise IntegrationError("Google sign-in could not be verified.") from e


def to_google_event(item: dict, tz: ZoneInfo) -> dict:
    """A calendar feed item -> Google event resource (the direction-reversed twin of
    the original app's translation helpers)."""
    start = datetime.fromisoformat(item["start"]).replace(tzinfo=tz)
    end = datetime.fromisoformat(item["end"]).replace(tzinfo=tz)
    lines = []
    for label, key in (("Mechanic", "employee_name"), ("Bay", "bay_name"), ("Customer", "customer_name"), ("Notes", "notes")):
        if item.get(key):
            lines.append(f"{label}: {item[key]}")
    body = {"summary": item["title"], "description": "\n".join(lines)}
    if item.get("location") or item.get("bay_name"):
        body["location"] = item.get("location") or item.get("bay_name")
    if item.get("all_day"):
        body["start"], body["end"] = {"date": start.date().isoformat()}, {"date": end.date().isoformat()}
    else:
        # Google wants dateTime plus an IANA timeZone name for correct DST handling.
        body["start"] = {"dateTime": start.isoformat(), "timeZone": tz.key}
        body["end"] = {"dateTime": end.isoformat(), "timeZone": tz.key}
    return body


class GoogleCalendarMirror(Integration, CalendarMirror):
    key = "google_calendar"
    name = "Google Calendar"
    description = ("Mirror the shop schedule to a Google calendar (one way). The built-in calendar stays the "
                   "source of truth; changes made in Google are not read back.")
    category = "calendar"
    connect = "oauth"

    def is_available(self) -> tuple[bool, str]:
        if google_configured():
            return True, ""
        return False, "The server has no Google OAuth client configured (SHOPPI_GOOGLE_CLIENT_ID / _SECRET)."

    def oauth_url(self, state: str, redirect_uri: str) -> str:
        return authorize_url(CALENDAR_SCOPE, state, redirect_uri, offline=True)

    def oauth_exchange(self, params: dict[str, str], redirect_uri: str) -> tuple[dict, dict]:
        if params.get("error") or not params.get("code"):
            raise IntegrationError("Google access was not granted.")
        tokens = exchange_code(params["code"], redirect_uri)
        if not tokens.get("refresh_token"):
            raise IntegrationError("Google didn't return a refresh token. Remove the app's access in your Google account and connect again.")
        return {"refresh_token": tokens["refresh_token"]}, {"calendar_id": "primary", "kinds": DEFAULT_KINDS, "days_ahead": 30}

    def _service(self, creds: dict):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        s = get_settings()
        credentials = Credentials(None, refresh_token=creds["refresh_token"], token_uri=TOKEN_URL,
                                  client_id=s.google_client_id, client_secret=s.google_client_secret, scopes=[CALENDAR_SCOPE])
        return build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def sync(self, ctx, creds: dict, options: dict, events: list[dict], service=None) -> dict:
        """Make the Google calendar match `events`: create new ones, patch changed ones,
        delete ones that are gone. `sync_map` remembers which Google event mirrors what."""
        service = service or self._service(creds)
        calendar_id = options.get("calendar_id") or "primary"
        existing = {(m.entity_type, str(m.entity_id)): m for m in ctx.tx.scalars(select(SyncMap).where(SyncMap.key == self.key))}
        seen, created, updated, deleted = set(), 0, 0, 0
        for item in events:
            key = (item["kind"], item["ref_id"])
            seen.add(key)
            body = to_google_event(item, ctx.tz)
            fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
            row = existing.get(key)
            if row is None:
                result = service.events().insert(calendarId=calendar_id, body=body).execute()
                ctx.tx.add(SyncMap(key=self.key, entity_type=item["kind"], entity_id=item["ref_id"],
                                   external_id=result["id"], fingerprint=fingerprint))
                created += 1
            elif row.fingerprint != fingerprint:
                service.events().patch(calendarId=calendar_id, eventId=row.external_id, body=body).execute()
                row.fingerprint = fingerprint
                updated += 1
        for key, row in existing.items():
            if key not in seen:
                try:
                    service.events().delete(calendarId=calendar_id, eventId=row.external_id).execute()
                except Exception:  # already gone on the Google side
                    pass
                ctx.tx.delete(row)
                deleted += 1
        ctx.tx.flush()
        return {"created": created, "updated": updated, "deleted": deleted}
