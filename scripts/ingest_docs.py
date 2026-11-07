"""(Re)build the knowledge-base index from SHOPPI_DOCS_DIR.

Requires the embedding model: `ollama pull nomic-embed-text` (default config).
"""

from shoppi.config import get_settings
from shoppi.knowledge.chroma_store import ChromaDocumentStore
from shoppi.knowledge.embeddings import build_embeddings

s = get_settings()
store = ChromaDocumentStore(s.vector_dir, build_embeddings(s))
print(f"Indexed {store.ingest(s.docs_dir)} chunks from {s.docs_dir}")
