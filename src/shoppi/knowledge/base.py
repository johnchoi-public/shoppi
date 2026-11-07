"""The document-store abstraction the agent's search tool depends on.

The MVP backend is a local Chroma index (see chroma_store.py). Anything that can
answer `search` (BM25, a hosted vector DB, ...) can replace it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchHit:
    text: str  # the matching passage
    source: str  # where it came from (file path relative to the docs dir)


class DocumentStore(ABC):
    @abstractmethod
    def search(self, query: str, k: int = 4) -> list[SearchHit]:
        """Return the k passages most relevant to `query`."""
