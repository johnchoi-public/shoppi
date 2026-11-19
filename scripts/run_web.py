"""Start the web app on http://127.0.0.1:8000 (override with SHOPPI_HOST / SHOPPI_PORT)."""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("shoppi.web.app:build_app", factory=True, host=os.environ.get("SHOPPI_HOST", "127.0.0.1"),
                port=int(os.environ.get("SHOPPI_PORT", "8000")))
