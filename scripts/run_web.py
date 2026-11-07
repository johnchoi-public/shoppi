"""Start the web app on http://127.0.0.1:8000 (localhost only: there is no auth)."""

import uvicorn

uvicorn.run("shoppi.web.app:build_app", factory=True, host="127.0.0.1", port=8000)
