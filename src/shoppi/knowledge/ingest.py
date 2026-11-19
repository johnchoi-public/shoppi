"""Turn an uploaded file into plain text for indexing."""

from __future__ import annotations

import io
from pathlib import Path

SUPPORTED = {".txt", ".md", ".pdf", ".csv", ".vtt"}
MAX_BYTES = 20 * 1024 * 1024


class UnsupportedDocument(ValueError):
    pass


def extract_text(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise UnsupportedDocument(f"Unsupported file type {suffix or '(none)'}. Supported: {', '.join(sorted(SUPPORTED))}.")
    if len(data) > MAX_BYTES:
        raise UnsupportedDocument("The file is larger than 20 MB.")
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        if not text.strip():
            raise UnsupportedDocument("No text could be read from this PDF (it may be a scan without OCR).")
        return text
    return data.decode("utf-8", errors="replace")
