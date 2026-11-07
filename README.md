# Shoppi

A calendar assistant: chat with an open-source LLM (via Ollama by default) to list, create, move, and delete calendar events, with an optional document knowledge base. The calendar backend is either a local SQLite calendar (default) or Google Calendar.

## Setup

Requires Python 3.11 and [Ollama](https://ollama.com).

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Pull the models named in `.env` (defaults shown; change `SHOPPI_LLM_MODEL` to any tool-calling model you have):

```bash
ollama pull nemotron-3.5-lightning
ollama pull nomic-embed-text
```

Key settings in `.env` (all prefixed `SHOPPI_`):

| Setting | Purpose |
| --- | --- |
| `TIMEZONE` | IANA timezone used for prompts and date math |
| `LLM_PROVIDER` / `LLM_MODEL` / `LLM_BASE_URL` | Chat model (any LangChain provider) |
| `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` | Embeddings for the document store |
| `CALENDAR_PROVIDER` | `local` (SQLite, default) or `google` |
| `DOCS_DIR` / `VECTOR_DIR` | Source documents and Chroma index location |

## Use

Web app (http://127.0.0.1:8000, localhost only, no auth):

```bash
python scripts/run_web.py
```

Terminal chat (type `exit` to quit):

```bash
python scripts/chat_cli.py
```

### Document knowledge base

Put files in `docs_store/source/` (or `SHOPPI_DOCS_DIR`), then build the index:

```bash
python scripts/ingest_docs.py
```

### Google Calendar (optional)

1. In Google Cloud Console, enable the Calendar API and download a "Desktop app" OAuth client JSON to `secrets/client_secret.json`.
2. Generate a token encryption key and put it in `.env` as `SHOPPI_TOKEN_KEY`:
   ```bash
   python -m shoppi.storage.token_store
   ```
3. Run the one-time consent flow:
   ```bash
   python scripts/authorize_google.py
   ```
4. Set `SHOPPI_CALENDAR_PROVIDER=google` in `.env`.

To smoke-test real Google CRUD (creates, moves, then deletes a temporary event tomorrow at 10:00):

```bash
python scripts/try_calendar.py
```

## Test

```bash
pytest
```

The tests use fakes (`tests/fakes.py`) and don't need Ollama or Google credentials. Run a single file with e.g. `pytest tests/test_agent.py`.

## Layout

- `src/shoppi/agent/` – LangGraph agent and system prompt
- `src/shoppi/calendar/` – calendar providers (`local`, `google`) behind a common interface
- `src/shoppi/knowledge/` – Chroma document store and embeddings
- `src/shoppi/llm/` – chat model factory
- `src/shoppi/tools/` – calendar and document tools exposed to the model
- `src/shoppi/web/` – FastAPI app and static chat UI
- `scripts/` – entry points; `tests/` – pytest suite
