"""One-time Google consent. Opens a browser, stores the encrypted refresh token."""

from shoppi.calendar.google_auth import run_consent_flow
from shoppi.config import get_settings
from shoppi.storage.token_store import EncryptedFileTokenStore

s = get_settings()
run_consent_flow(s.google_client_secrets_file, EncryptedFileTokenStore(s.token_file, s.token_key))
print(f"Authorized. Encrypted token saved to {s.token_file}")
