# shoppi

Shop management for car and truck service shops: one place for the schedule,
customer intake, work orders, bays, the team and their certifications, and
parts — with an assistant that can look things up and draft changes.

**New to the app?** [TUTORIAL.md](TUTORIAL.md) walks through every screen with the demo shop.

## What it does

- **Built-in calendar** (month / week / day / agenda, by mechanic, by bay). No external calendar needed.
- **Scheduling that knows the rules.** Jobs are only offered to mechanics whose certifications are valid on the day, in a bay that fits the vehicle and is open, when parts are on hand or due in. If nothing fits, it says why.
- **Customer intake** as a five-step staff wizard and as a separate customer-facing form (kiosk or link) that lands in a review queue. A **Diagnosis** service is always on offer, and is added automatically when only a concern is entered, so every visit can be scheduled.
- **Customers, vehicles and work orders**, with VIN decoding to fill in the vehicle details the scheduler depends on.
- **Bays** as first-class resources: own hours, size limits, equipment, closures, live status board.
- **Inventory**: stock, reservations for booked jobs, purchase orders, reorder suggestions, parts-catalog search.
- **Team**: certifications with expiry warnings, shifts, time off, apprentice supervision.
- **Phone-call transcripts**: pasted, uploaded or received from a phone system; requests found in a call become drafts in the intake queue.
- **Roles and history.** Owners and advisors can change everything. Mechanics can view, propose changes for approval, or be granted edit rights per area. Every change is recorded: who, what, when.
- **Assistant** (RAG over your documents plus tools over shop data). Any change it suggests is a proposal you confirm.
- **Multi-shop.** Many shops share one deployment; each shop's data is isolated in the database itself.

### What changed from the original prototype

The first version was a single-user calendar chatbot. It has been rebuilt as a platform:

| Before | Now |
|---|---|
| One user, no login | Multi-shop accounts, invites, roles, per-area edit grants |
| SQLite, tables created at start-up | Postgres with row-level security, Alembic migrations |
| Calendar events in a local file or Google | Built-in calendar that unions jobs, shifts, time off, bay closures, deliveries and events; Google is an optional one-way mirror |
| No shop concepts | Intake, customers, vehicles, work orders, bays, team, certifications, shifts, inventory, purchase orders, calls |
| The model wrote straight to the calendar | One command path for every write; assistant changes are proposals; a database trigger records history |
| Chroma document store | pgvector, isolated per shop |
| Plain-JS week view | React + Vite + TypeScript app, plus a login-free customer intake form |
| — | Integration framework: VIN decoding, parts catalogs, signed transcript webhooks, Google Calendar |

Most recent additions: the Diagnosis option in intake, deleting customers and work orders (owners and advisors only), a ready-made dev login, and sign-in that still works when the browser refuses the session cookie.

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
python scripts/run_web.py        # http://127.0.0.1:8000 (SHOPPI_HOST / SHOPPI_PORT to change)
```

Open the app and choose **Create an account** to set up your own shop, or load the demo shop:

```bash
python scripts/seed_demo.py        # demo shop "Northside Auto & Truck": prints its logins and an intake link
python scripts/create_dev_login.py # an extra owner login in the demo shop, for testing
```

Both scripts print the email addresses. The passwords are local throwaway
defaults set at the top of each script (override with `SHOPPI_DEMO_PASSWORD` /
`SHOPPI_DEV_PASSWORD`). Never run either against a real deployment.

Ollama is only needed for the assistant, document search and turning phone calls into intake drafts. Everything else works without it.

For UI development run `npm run dev` in `web/` (port 5173, proxies `/api` to the Python server).
After changing anything under `web/src`, run `npm run build` again for the Python server to serve it.

`docker-compose.yml` runs Postgres with pgvector if you prefer Docker to a local install:
`docker compose up -d db`, then the same `setup_db.sh` / `migrate.py` steps with `PGHOST=localhost`.

### After pulling new code

```bash
pip install -e ".[dev]"
python scripts/migrate.py
(cd web && npm install && npm run build)
```

`migrate.py` is safe to run every time: it applies new migrations and re-applies the security policies and triggers.

## Configuration

Everything is set through `SHOPPI_*` environment variables (see `.env.example`).

| Setting | Meaning |
|---|---|
| `DATABASE_URL`, `DATABASE_OWNER_URL` | App role (subject to row-level security) and schema owner (migrations) |
| `BASE_URL` | Public address, used in invite links, intake links and OAuth redirects |
| `COOKIE_SECURE` | Set to `true` when served over HTTPS |
| `TOKEN_KEY` | Fernet key that encrypts integration credentials |
| `LLM_PROVIDER` / `LLM_MODEL` / `LLM_BASE_URL` | Chat model. Any LangChain provider: `ollama`, `anthropic`, `openai` (also vLLM and other OpenAI-compatible servers via `LLM_BASE_URL`) |
| `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` | Embedding model for document search, and its vector width |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Optional: enables "Sign in with Google" and the Google Calendar mirror |
| `UPLOAD_DIR` | Where uploaded documents are kept (default `data/uploads`) |

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
  starter.py     default bays and service catalog for a new shop
web/             React + Vite + TypeScript source
scripts/         setup_db.sh, migrate.py, run_web.py, seed_demo.py, create_dev_login.py
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
| Deleting a customer or work order | yes | never: not with edit rights, not as a change request |

A legally required certification (EV high-voltage, EPA 609, inspector licences, CDL) can never be overridden when scheduling. Other rules can be overridden by an owner or advisor with a reason, which is kept in the history.

The assistant acts with the signed-in person's permissions, its changes always wait for confirmation, and it has no tools for deleting customers or work orders.

### Deleting records

- **Work order:** removes its lines, scheduled jobs and part reservations, freeing the mechanic, bay and parts. Refused while a job is in progress or paused.
- **Customer:** removes their vehicles, work orders, phone calls and transcripts. The dialog lists what will go and asks for the customer's name.
- **Kept either way:** parts already used stay deducted from stock, uploaded documents stay (unfiled), intake submissions stay (unlinked), and the deleted records remain readable in History.
- To stop work but keep the record, **Cancel** the work order instead.

### Sign-in sessions

A session is an opaque random token in an HttpOnly, SameSite=Lax cookie. Every sign-in route also returns the token, and the API accepts it as `Authorization: Bearer <token>`. The web UI uses that only when the browser refuses to keep the cookie (cookies blocked or restricted by privacy settings): it then keeps the token for the current tab and shows a notice saying so. Without the fallback, sign-in succeeded on the server and failed silently in the browser, with `401` on `GET /api/auth/me`.

### HTTP API

The API is deliberately thin, so nothing can bypass permissions or history:

| Endpoint | Purpose |
|---|---|
| `POST /api/q/<query name>` | Read |
| `POST /api/cmd/<command name>` | Write, or propose a change if the caller only has view access |
| `POST /api/chat` | Assistant, streamed as server-sent events |
| `/api/public/intake/<token>` | The customer intake form (no login; can only create a pending submission) |
| `POST /api/integrations/<key>/webhook/<shop hook id>` | Signed inbound webhooks, e.g. call transcripts |

State-changing requests must carry the header `X-Requested-With: shoppi`. Times are shop-local with no UTC offset (`2026-11-06T15:00:00`); the server attaches the shop's timezone.

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

87 tests covering shop isolation, permissions and change requests, the scheduling rules, each feature area, and the web layer. They need the `shoppi_test` database from `scripts/setup_db.sh` (row-level security and booking constraints can't be exercised on anything but Postgres). They don't need Ollama, Google or network access.

## Deploying

- Serve behind HTTPS and set `SHOPPI_COOKIE_SECURE=true` and `SHOPPI_BASE_URL`.
- Use real passwords for both database roles; keep the app on the restricted role.
- Run `python scripts/migrate.py` on every release. It also re-applies security policies, so a new table can't ship unprotected.
- Login and public-form rate limits are kept in process memory: with several web workers, add a shared limiter at the proxy.
- Don't run `seed_demo.py` or `create_dev_login.py`: both create logins with known default passwords.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| The page doesn't load at `localhost:8000` | The server isn't running: `python scripts/run_web.py` |
| The UI looks out of date after a code change | Rebuild it: `(cd web && npm run build)` |
| Errors about a missing column or table | Run `python scripts/migrate.py` |
| A notice says you're signed in "for this tab only" | The browser isn't keeping the session cookie. The app still works; allow cookies for the site to stay signed in across tabs |
| The assistant doesn't answer, or a document shows as not indexed | Ollama isn't running or the models aren't pulled (`ollama pull …` above). The document is kept and can be indexed later |
| "Demo data not created" from `seed_demo.py` | The demo shop already exists; the script only runs once per database |
| Connecting an integration fails | `SHOPPI_TOKEN_KEY` isn't set in `.env` |
