# shoppi

Shop management for car and truck service shops: one place for the schedule,
customer intake, work orders, bays, the team and their certifications, and
parts — with an assistant that can look things up and draft changes.

- **Built-in calendar** (month / week / day / agenda, by mechanic, by bay). No external calendar needed.
- **Scheduling that knows the rules.** Jobs are only offered to mechanics whose certifications are valid on the day, in a bay that fits the vehicle and is open, when parts are on hand or due in. If nothing fits, it says why.
- **Customer intake** as a staff wizard and as a separate customer-facing form (kiosk or link) that lands in a review queue.
- **Bays** as first-class resources: own hours, size limits, equipment, closures, live status board.
- **Inventory**: stock, reservations for booked jobs, purchase orders, reorder suggestions, parts-catalog search.
- **Roles and history.** Owners and advisors can change everything. Mechanics can view, propose changes for approval, or be granted edit rights per area. Every change is recorded: who, what, when.
- **Assistant** (RAG over your documents plus tools over shop data). Any change it suggests is a proposal you confirm.
- **Multi-shop.** Many shops share one deployment; each shop's data is isolated in the database itself.

## Setup

Requirements: Python 3.11, Node 20+, PostgreSQL 15+ with the
[pgvector](https://github.com/pgvector/pgvector) extension, and (for the
assistant) a language model — [Ollama](https://ollama.com) by default.

```bash
# macOS example
brew install postgresql@17 pgvector node && brew services start postgresql@17
```

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python -m shoppi.integrations.credentials   # prints a key: put it in .env as SHOPPI_TOKEN_KEY

./scripts/setup_db.sh            # roles, databases (shoppi, shoppi_test) and extensions
python scripts/migrate.py        # tables, row-level security, triggers

(cd web && npm install && npm run build)   # builds the UI into the Python package

ollama pull nemotron-3.5-lightning          # chat model (any tool-calling model works)
ollama pull nomic-embed-text                # embeddings for document search
```

```bash
python scripts/run_web.py        # http://127.0.0.1:8000
```

Open the app and choose **Create an account** to set up your shop, or load a demo shop first:

```bash
python scripts/seed_demo.py      # prints the demo logins and a customer intake link
```

For UI development run `npm run dev` in `web/` (port 5173, proxies `/api` to the Python server).

`docker-compose.yml` runs Postgres with pgvector if you prefer Docker to a local install:
`docker compose up -d db`, then the same `setup_db.sh` / `migrate.py` steps with `PGHOST=localhost`.

## Configuration

Everything is set through `SHOPPI_*` environment variables (see `.env.example`).

| Setting | Meaning |
|---|---|
| `DATABASE_URL`, `DATABASE_OWNER_URL` | App role (subject to row-level security) and schema owner (migrations) |
| `BASE_URL` | Public address, used in invite links, intake links and OAuth redirects |
| `TOKEN_KEY` | Fernet key that encrypts integration credentials |
| `LLM_PROVIDER` / `LLM_MODEL` / `LLM_BASE_URL` | Chat model. Any LangChain provider: `ollama`, `anthropic`, `openai` (also vLLM and other OpenAI-compatible servers via `LLM_BASE_URL`) |
| `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` | Embedding model for document search, and its vector width |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Optional: enables "Sign in with Google" and the Google Calendar mirror |

**Changing the language model** is a config change only: `src/shoppi/llm/factory.py` is the one place a provider is chosen. For a hosted model install its LangChain package (e.g. `pip install langchain-anthropic`) and set the provider, model and API key.

## How it is put together

```
src/shoppi/
  db/            models, tenant_tx (the only way to get a session), row-level security, migrations
  auth/          users, shops, memberships, sessions, invites
  core/          commands.py (the single write path), permissions.py, actor.py
  domain/        one module per feature area: commands and queries
  scheduling/    engine.py (pure rules: certifications, bays, shifts, parts) + service.py (loads data)
  integrations/  interfaces + registry; VIN decoding, parts catalogs, transcripts, Google
  knowledge/     document store on pgvector
  agent/         the assistant: prompt, and tools generated from the command registry
  web/           FastAPI app; serves the built React UI from web/dist
web/             React + Vite + TypeScript source
tests/           runs against a real Postgres (shoppi_test)
```

Four ideas hold it together:

1. **Isolation is enforced by the database.** Every shop table carries `shop_id` and a row-level security policy; the app connects as a role that cannot bypass it. A query that forgets a filter still only sees its own shop. `tests/test_isolation.py` fails if a table is added without a policy.
2. **One write path.** Every change is a registered *command* (`core/commands.py`). Screens, the assistant, the public intake form and integrations all go through `execute()`, which checks permissions, turns a view-only user's edit into a change request, and is the only caller of handlers.
3. **History is written by a trigger**, not by application code, so no path can skip it. Secrets are excluded.
4. **The scheduler is deterministic code**, not the language model (`scheduling/engine.py`). The assistant asks it for options like any screen does.

### Permissions

| | Owner / Advisor | Mechanic |
|---|---|---|
| Everything | edit | view (except shop settings, integrations and — unless enabled — phone calls) |
| Changing something | applied immediately | sent as a change request for an owner or advisor to approve |
| Edit rights for one area | — | can be requested or granted, optionally time-limited (Requests → Edit access) |
| Own scheduled jobs | — | can mark started / paused / finished |

A legally required certification (EV high-voltage, EPA 609, inspector licences, CDL) can never be overridden when scheduling. Other rules can be overridden by an owner or advisor with a reason, which is kept in the history.

## Integrations

Settings → Integrations. Nothing in the app depends on them.

| Integration | What it does | Connect |
|---|---|---|
| VIN decoding (NHTSA) | Fills in year, make, model, engine, fuel type and weight class. Only the VIN is sent. | On by default |
| Supplier price file (CSV) | Upload a price list and search it like a catalog | Upload under Inventory → Find parts |
| Demo parts catalog | Sample data for trying out search and ordering | Switch on |
| Call transcripts (signed webhook) | Receives finished call transcripts; drafts go to the intake queue | Connect, copy the secret and URL into your phone system |
| Google Calendar | One-way mirror of the shop calendar | Connect (needs the Google OAuth client configured) |

### Adding an integration

Subclass `Integration` plus a capability interface from `src/shoppi/integrations/base.py`, and register an instance in `registry.py`:

- `VinDecoder.decode(vin)`
- `PartsCatalogProvider.search(...)`, optional `place_order(...)` (set `supports_ordering = True`)
- `TranscriptSource.verify_webhook(...)` and `parse(payload)` — the provider then posts to `/api/integrations/<key>/webhook/<shop hook id>`
- `CalendarMirror.sync(...)`

Declare `connect = "api_key"` with `fields` for a credentials form, or `"oauth"` and implement `oauth_url` / `oauth_exchange`. Credentials arrive decrypted in `creds`; the rest of the app never talks to a vendor directly, so nothing else changes.

Call transcripts in the normalised JSON shape (sign the raw body with HMAC-SHA256, hex, in `X-Shoppi-Signature`):

```json
{"external_id": "abc123", "from_number": "+15552017788", "started_at": "2026-11-12T14:03:00Z",
 "duration_s": 95, "consent_noted": true,
 "segments": [{"speaker": "Customer", "text": "My brakes are grinding."}]}
```

Call-recording consent rules differ by state. The app records whether consent was noted; your phone system must give whatever notice your state requires.

## Tests

```bash
pytest
```

Tests need the `shoppi_test` database from `scripts/setup_db.sh` (row-level security and booking constraints can't be exercised on anything but Postgres). They don't need Ollama, Google or network access.

## Deploying

- Serve behind HTTPS and set `SHOPPI_COOKIE_SECURE=true` and `SHOPPI_BASE_URL`.
- Use real passwords for both database roles; keep the app on the restricted role.
- Run `python scripts/migrate.py` on every release. It also re-applies security policies, so a new table can't ship unprotected.
- Login and public-form rate limits are kept in process memory: with several web workers, add a shared limiter at the proxy.
