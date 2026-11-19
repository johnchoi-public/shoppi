"""pgvector-backed knowledge store.

Chunks live in `doc_chunks` next to the rest of the shop's data, so the same
row-level security isolates them: a search can only ever see the current shop's
documents, with no per-shop collection or metadata filter to forget.

Search is an exact scan over the shop's chunks (no approximate index): a shop's
corpus is small, and filtered approximate search under-returns for small tenants.
"""

from __future__ import annotations

from uuid import UUID

from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from shoppi.config import get_settings
from shoppi.db.models import DocChunk, Document
from shoppi.knowledge.base import DocumentStore, SearchHit

# ~800 chars with overlap keeps a passage coherent but small enough to embed precisely.
SPLITTER = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)


class PgDocumentStore(DocumentStore):
    def __init__(self, tx: Session, embeddings: Embeddings):
        self._tx = tx
        self._embeddings = embeddings

    def index(self, document: Document, text: str) -> int:
        """(Re)index one document. Only this document's chunks are replaced."""
        self._tx.execute(delete(DocChunk).where(DocChunk.document_id == document.id))
        chunks = [c for c in SPLITTER.split_text(text or "") if c.strip()]
        if chunks:
            vectors = self._embeddings.embed_documents(chunks)
            dim = get_settings().embedding_dim
            if vectors and len(vectors[0]) != dim:
                raise ValueError(
                    f"The embedding model returns {len(vectors[0])}-dimensional vectors but the database column is {dim}. "
                    "Set SHOPPI_EMBEDDING_DIM to match the model and re-create the doc_chunks column."
                )
            model = get_settings().embedding_model
            for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
                self._tx.add(DocChunk(document_id=document.id, seq=i, text=chunk, embedding=vector, embedding_model=model))
        document.status, document.chunk_count, document.error = "indexed", len(chunks), None
        self._tx.flush()
        return len(chunks)

    def search(self, query: str, k: int = 4, *, kinds: list[str] | None = None, customer_id: UUID | None = None,
               vehicle_id: UUID | None = None, work_order_id: UUID | None = None) -> list[SearchHit]:
        vector = self._embeddings.embed_query(query)
        distance = DocChunk.embedding.cosine_distance(vector)
        q = (select(DocChunk.text, Document.title, Document.id, Document.kind, distance.label("distance"))
             .join(Document, Document.id == DocChunk.document_id).order_by(distance).limit(k))
        if kinds is not None:
            q = q.where(Document.kind.in_(kinds))
        if customer_id:
            q = q.where(Document.customer_id == customer_id)
        if vehicle_id:
            q = q.where(Document.vehicle_id == vehicle_id)
        if work_order_id:
            q = q.where(Document.work_order_id == work_order_id)
        return [SearchHit(text=t, source=title, document_id=doc_id, kind=kind, distance=float(d))
                for t, title, doc_id, kind, d in self._tx.execute(q)]
