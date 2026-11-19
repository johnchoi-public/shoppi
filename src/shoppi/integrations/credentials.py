"""Encrypted storage for integration secrets (OAuth tokens, API keys).

Values are Fernet-encrypted with SHOPPI_TOKEN_KEY before they reach the
database, and the column is excluded from the change history. Generate a key:

    python -m shoppi.integrations.credentials
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

from cryptography.fernet import Fernet, InvalidToken

from shoppi.config import get_settings
from shoppi.integrations.base import IntegrationError


def _fernet() -> Fernet:
    key = get_settings().token_key
    if not key:
        raise IntegrationError("SHOPPI_TOKEN_KEY is not set, so credentials can't be stored. "
                               "Generate one with: python -m shoppi.integrations.credentials")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as e:
        raise IntegrationError("SHOPPI_TOKEN_KEY is not a valid Fernet key.") from e


def encrypt(values: dict) -> str:
    return _fernet().encrypt(json.dumps(values).encode()).decode()


def decrypt(blob: str | None) -> dict:
    if not blob:
        return {}
    try:
        return json.loads(_fernet().decrypt(blob.encode()))
    except InvalidToken as e:
        raise IntegrationError("Stored credentials can't be decrypted (was SHOPPI_TOKEN_KEY changed?). Reconnect the integration.") from e


# ---- Signed OAuth state -----------------------------------------------------------
# Ties an OAuth round trip to the shop and user who started it, so a callback
# can't be replayed into another shop.


def sign_state(shop_id: str, user_id: str, key: str, ttl_s: int = 600) -> str:
    expires = int(time.time()) + ttl_s
    body = f"{shop_id}.{user_id}.{key}.{expires}"
    return body + "." + _mac(body)


def verify_state(state: str, shop_id: str, user_id: str, key: str) -> bool:
    body, _, mac = state.rpartition(".")
    if not body or not hmac.compare_digest(mac, _mac(body)):
        return False
    try:
        s, u, k, expires = body.split(".")
    except ValueError:
        return False
    return s == shop_id and u == user_id and k == key and int(expires) >= int(time.time())


def _mac(body: str) -> str:
    key = (get_settings().token_key or "").encode()
    if not key:
        raise IntegrationError("SHOPPI_TOKEN_KEY is not set.")
    return hmac.new(key, body.encode(), hashlib.sha256).hexdigest()


if __name__ == "__main__":
    print(Fernet.generate_key().decode())
