"""Chroma store round-trip using a deterministic fake embedding (no model needed)."""

from langchain_core.embeddings import Embeddings

from shoppi.knowledge.chroma_store import ChromaDocumentStore


class BagOfLettersEmbeddings(Embeddings):
    """Crude but deterministic: a 26-dim letter-frequency vector."""

    def _vec(self, text: str) -> list[float]:
        t = text.lower()
        return [float(t.count(c)) for c in "abcdefghijklmnopqrstuvwxyz"]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


def test_ingest_and_search(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "gym.md").write_text("Gym hours: weekdays 6am to 9pm.")
    (src / "ignored.pdf").write_text("not indexed")
    store = ChromaDocumentStore(tmp_path / "chroma", BagOfLettersEmbeddings())

    assert store.ingest(src) == 1  # only the .md file
    hits = store.search("gym hours")
    assert hits[0].source == "gym.md" and "6am" in hits[0].text

    # Re-ingest after deleting the file leaves no stale chunks.
    (src / "gym.md").unlink()
    assert store.ingest(src) == 0 and store.search("gym") == []
