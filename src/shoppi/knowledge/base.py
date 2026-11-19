"""The document-store abstraction the assistant's search tool depends on.

The backend is pgvector inside the app's own Postgres (see pg_store.py), which
puts document chunks under the same row-level security as everything else.
Anything that can answer `search` could replace it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class SearchHit:
    text: str  # the matching passage
    source: str  # title of the document it came from
    document_id: UUID | None = None
    kind: str = "file"  # file | call_transcript | note
    distance: float | None = None


class DocumentStore(ABC):
    @abstractmethod
    def search(self, query: str, k: int = 4, *, kinds: list[str] | None = None, customer_id: UUID | None = None,
               vehicle_id: UUID | None = None, work_order_id: UUID | None = None) -> list[SearchHit]:
        """Return the k passages most relevant to `query`, optionally limited to
        certain kinds of document or to those linked to a customer/vehicle/work order."""
