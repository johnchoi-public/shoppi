"""Google OAuth: one-time consent flow and credential loading/refresh.

Scope is deliberately the narrowest that allows event CRUD: `calendar.events`.
"""

from __future__ import annotations

import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from shoppi.storage.token_store import TokenStore

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def run_consent_flow(client_secrets_file: Path, store: TokenStore) -> Credentials:
    """Open a browser for Google consent on localhost and persist the result.

    Run once (see scripts/authorize_google.py). `access_type=offline` plus
    `prompt=consent` ensures Google returns a refresh token.
    """
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_file), SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    store.save(json.loads(creds.to_json()))
    return creds


def load_credentials(store: TokenStore) -> Credentials:
    """Load stored credentials, refreshing the access token if expired.

    A refreshed token is written back to the store so the next run reuses it.
    """
    data = store.load()
    if data is None:
        raise RuntimeError("No Google credentials stored. Run scripts/authorize_google.py first.")
    creds = Credentials.from_authorized_user_info(data, SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        store.save(json.loads(creds.to_json()))
    return creds
