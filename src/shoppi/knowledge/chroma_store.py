"""Chroma-backed, read-only knowledge base.

Documents are plain .md / .txt files in a directory. `ingest` rebuilds the index
from scratch, so the files on disk are always the source of truth. The model can
only read this store; there is deliberately no write path.
"""

from __future__ import annotations

from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from shoppi.knowledge.base import DocumentStore, SearchHit

SUPPORTED_SUFFIXES = {".md", ".txt"}
COLLECTION = "shoppi_docs"


class ChromaDocumentStore(DocumentStore):
    def __init__(self, persist_dir: Path, embeddings: Embeddings):
        self._persist_dir = persist_dir
        self._embeddings = embeddings
        self._db = self._open()

    def _open(self) -> Chroma:
        return Chroma(
            collection_name=COLLECTION,
            embedding_function=self._embeddings,
            persist_directory=str(self._persist_dir),
        )

    def ingest(self, source_dir: Path) -> int:
        """Rebuild the index from every supported file under `source_dir`.

        Returns the number of chunks indexed.
        """
        # Drop the old collection so deleted/edited files don't leave stale chunks.
        # (Don't rmtree the directory: the live Chroma client would be left holding
        # a deleted database.)
        self._db.delete_collection()
        self._db = self._open()

        # ~800 chars with overlap keeps a passage coherent but small enough to embed precisely.
        splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
        chunks: list[Document] = []
        for path in sorted(source_dir.rglob("*")):
            if path.suffix.lower() not in SUPPORTED_SUFFIXES or not path.is_file():
                continue
            doc = Document(
                page_content=path.read_text(encoding="utf-8", errors="replace"),
                metadata={"source": str(path.relative_to(source_dir))},
            )
            chunks.extend(splitter.split_documents([doc]))

        if chunks:
            self._db.add_documents(chunks)
        return len(chunks)

    def search(self, query: str, k: int = 4) -> list[SearchHit]:
        docs = self._db.similarity_search(query, k=k)
        return [SearchHit(text=d.page_content, source=d.metadata.get("source", "unknown")) for d in docs]
