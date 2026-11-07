"""Encrypted storage for OAuth credentials.

The calendar provider only depends on the `TokenStore` interface, so the
single-user encrypted file used in the MVP can later be swapped for a
per-user database table without touching provider code.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from cryptography.fernet import Fernet


class TokenStore(ABC):
    """Persists an opaque JSON-serializable credentials dict."""

    @abstractmethod
    def load(self) -> dict | None:
        """Return the stored credentials, or None if nothing is stored yet."""

    @abstractmethod
    def save(self, data: dict) -> None:
        """Replace the stored credentials."""


class EncryptedFileTokenStore(TokenStore):
    """Single-user store: one Fernet-encrypted file on local disk."""

    def __init__(self, path: Path, key: str):
        self._path = path
        try:
            self._fernet = Fernet(key.encode())
        except ValueError as e:
            # Fernet's own message is cryptic; point at the generator instead.
            raise ValueError(
                "SHOPPI_TOKEN_KEY is not a valid Fernet key (must be 44 characters). "
                "Generate one with: python -m shoppi.storage.token_store"
            ) from e

    def load(self) -> dict | None:
        if not self._path.exists():
            return None
        plaintext = self._fernet.decrypt(self._path.read_bytes())
        return json.loads(plaintext)

    def save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_bytes(self._fernet.encrypt(json.dumps(data).encode()))
        # Refresh tokens are long-lived secrets: owner read/write only.
        self._path.chmod(0o600)


if __name__ == "__main__":
    # `python -m shoppi.storage.token_store` prints a fresh key for SHOPPI_TOKEN_KEY.
    print(Fernet.generate_key().decode())
