"""FastAPI web app: JSON API, streaming chat, public intake, webhooks and the React UI.

The API is deliberately thin. Screens call two generic endpoints,

    POST /api/q/<query name>     read
    POST /api/cmd/<command name> write (or propose a change)

which hand straight to `run_query` / `execute`. All authorization, change
requests and history live below this layer (core/commands.py), so there is no
endpoint that can skip them.

Time convention: the API speaks shop-local times with no UTC offset
(e.g. 2026-11-06T15:00:00); the server attaches the shop's zone.

Security notes:
- sessions are opaque random tokens in an HttpOnly, SameSite=Lax cookie. Sign-in
  also returns the token, and it is accepted as "Authorization: Bearer <token>":
  the web UI falls back to that only when the browser refuses to keep the cookie
  (cookies blocked, or the app embedded in another page), where sign-in would
  otherwise succeed on the server and then silently fail in the browser;
- every state-changing request must carry the X-Requested-With header, which a
  cross-site form post cannot set;
- the public intake and webhook endpoints are the only unauthenticated writers
  and each can do exactly one thing.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from collections import defaultdict, deque
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, Field
from sqlalchemy import select, text

import shoppi.domain  # noqa: F401  (registers commands and queries)
from shoppi.auth import service as auth
from shoppi.config import get_settings
from shoppi.core.actor import Actor
from shoppi.core.commands import AppError, Forbidden, Invalid, NotFound, _ctx, decide_change_request, execute, run_query
from shoppi.core.permissions import GRANTABLE, VERTICALS, permission_map
from shoppi.db.models import ChatThread, Shop
from shoppi.db.session import identity_tx, tenant_tx
from shoppi.integrations import registry
from shoppi.integrations.base import IntegrationError
from shoppi.integrations.credentials import sign_state, verify_state

log = logging.getLogger("shoppi")
DIST_DIR = Path(__file__).parent / "dist"
COOKIE = "shoppi_session"
MAX_WEBHOOK_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


# ---- Request bodies ---------------------------------------------------------------


class SignupIn(BaseModel):
    shop_name: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    email: str
    password: str
    timezone: str = "America/New_York"
    starter: bool = True


class LoginIn(BaseModel):
    email: str
    password: str


class InviteAccept(BaseModel):
    name: str = ""
    password: str


class SwitchIn(BaseModel):
    shop_id: UUID


class DecideIn(BaseModel):
    approve: bool
    note: str | None = None


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    thread_id: UUID | None = None


class TranscriptText(BaseModel):
    text: str = Field(min_length=1, max_length=400_000)
    from_number: str | None = None
    customer_id: UUID | None = None
    consent_noted: bool = False


# ---- Small in-memory rate limiter (per process) -----------------------------------


class RateLimiter:
    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str, limit: int, window_s: int) -> None:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > window_s:
            hits.popleft()
        if len(hits) >= limit:
            raise TooMany("Too many attempts. Please wait a few minutes and try again.")
        hits.append(now)


class TooMany(AppError):
    status, code = 429, "rate_limited"


def _sse(payload: dict) -> str:
    """Format one Server-Sent Events message."""
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ---- App --------------------------------------------------------------------------


def create_app(agent=None) -> FastAPI:
    """`agent` may be injected (tests); otherwise it is built on first use from configuration."""
    settings = get_settings()
    app = FastAPI(title="shoppi", docs_url=None, redoc_url=None, openapi_url=None)
    limiter = RateLimiter()
    state = {"agent": agent}

    @app.exception_handler(AppError)
    async def app_error(_: Request, e: AppError):
        return JSONResponse(status_code=e.status, content={"error": {"code": e.code, "message": e.message, **e.extra}})

    @app.exception_handler(IntegrationError)
    async def integration_error(_: Request, e: IntegrationError):
        return JSONResponse(status_code=422, content={"error": {"code": "integration", "message": str(e)}})

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        path = request.url.path
        if request.method not in ("GET", "HEAD", "OPTIONS") and path.startswith("/api/") and "/webhook/" not in path:
            if request.headers.get("x-requested-with") != "shoppi":
                return JSONResponse(status_code=403, content={"error": {"code": "csrf", "message": "Missing X-Requested-With header."}})
        return await call_next(request)

    def set_session(response: Response, token: str) -> dict:
        """Start a browser session: set the cookie and also hand the token back, for
        browsers that will not keep the cookie (see the module docstring)."""
        response.set_cookie(COOKIE, token, max_age=settings.session_days * 86400, httponly=True, samesite="lax",
                            secure=settings.cookie_secure, path="/")
        return {"ok": True, "token": token}

    def current_actor(request: Request) -> Actor:
        return auth.resolve_session(_session_token(request))

    # -- Auth --

    @app.get("/api/health")
    def health() -> dict:
        with identity_tx() as tx:
            tx.execute(text("SELECT 1"))
        return {"ok": True}

    @app.get("/api/auth/config")
    def auth_config() -> dict:
        from shoppi.integrations.google import google_configured

        return {"google": google_configured()}

    @app.post("/api/auth/signup")
    def signup(body: SignupIn, request: Request, response: Response) -> dict:
        limiter.check(f"signup:{_client(request)}", 10, 3600)
        token, _ = auth.signup(shop_name=body.shop_name, name=body.name, email=body.email, password=body.password,
                               timezone_name=body.timezone)
        if body.starter:
            from shoppi.starter import install_starter

            install_starter(auth.resolve_session(token))
        return set_session(response, token)

    @app.post("/api/auth/login")
    def login(body: LoginIn, request: Request, response: Response) -> dict:
        limiter.check(f"login:{_client(request)}:{body.email.lower()}", 10, 300)
        token, _ = auth.login(body.email, body.password)
        return set_session(response, token)

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response) -> dict:
        # End whichever credentials this browser holds (it may have both).
        for token in {_session_token(request), request.cookies.get(COOKIE)} - {None}:
            auth.end_session(token)
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(actor: Actor = Depends(current_actor)) -> dict:
        with tenant_tx(actor) as tx:
            shop = tx.get(Shop, actor.shop_id)
            perms = permission_map(tx, actor)
            shop_out = {"id": str(shop.id), "name": shop.name, "timezone": shop.timezone, "hours": shop.hours}
        return {
            "user": {"id": str(actor.user_id), "name": actor.name, "role": actor.role,
                     "employee_id": str(actor.employee_id) if actor.employee_id else None, "is_manager": actor.is_manager},
            "shop": shop_out, "permissions": perms, "verticals": VERTICALS, "grantable": GRANTABLE,
            "shops": auth.memberships_for(actor.user_id),
        }

    @app.post("/api/auth/switch")
    def switch(body: SwitchIn, response: Response, actor: Actor = Depends(current_actor)) -> dict:
        token, _ = auth.start_session_for(actor.user_id, body.shop_id)
        return set_session(response, token)

    @app.get("/api/auth/invite/{token}")
    def invite_info(token: str) -> dict:
        info = auth.lookup_invite(token)
        with identity_tx() as tx:
            from shoppi.db.models import User

            exists = tx.scalar(select(User.id).where(User.email == info["email"])) is not None
        return {"email": info["email"], "role": info["role"], "shop_name": info["shop_name"], "has_account": exists}

    @app.post("/api/auth/invite/{token}")
    def invite_accept(token: str, body: InviteAccept, request: Request, response: Response) -> dict:
        limiter.check(f"invite:{_client(request)}", 20, 600)
        session, _ = auth.accept_invite(token, body.name, body.password)
        return set_session(response, session)

    @app.get("/api/auth/google/start")
    def google_start(request: Request):
        from shoppi.integrations.google import SIGNIN_SCOPE, authorize_url, google_configured

        if not google_configured():
            raise NotFound("Google sign-in is not configured.")
        nonce = uuid4().hex
        url = authorize_url(SIGNIN_SCOPE, sign_state("signin", nonce, "google"), f"{settings.base_url}/api/auth/google/callback", offline=False)
        response = RedirectResponse(url)
        response.set_cookie("shoppi_oauth", nonce, max_age=600, httponly=True, samesite="lax", secure=settings.cookie_secure)
        return response

    @app.get("/api/auth/google/callback")
    def google_callback(request: Request):
        from shoppi.db.models import User
        from shoppi.integrations.google import exchange_code, verify_id_token

        nonce = request.cookies.get("shoppi_oauth", "")
        params = dict(request.query_params)
        try:
            if not nonce or not verify_state(params.get("state", ""), "signin", nonce, "google") or not params.get("code"):
                raise IntegrationError("Google sign-in was cancelled or expired.")
            claims = verify_id_token(exchange_code(params["code"], f"{settings.base_url}/api/auth/google/callback").get("id_token", ""))
            if not claims.get("email_verified"):
                raise IntegrationError("That Google account's email is not verified.")
            email, sub = claims["email"].lower(), claims["sub"]
            with identity_tx() as tx:
                user = tx.scalar(select(User).where((User.google_sub == sub) | (User.email == email)))
                if user is None:
                    raise IntegrationError("No Shoppi account uses that Google address. Sign up first, or ask your shop for an invite.")
                if user.google_sub is None:
                    user.google_sub = sub
                user_id = user.id
            token, _ = auth.start_session_for(user_id)
        except (IntegrationError, AppError) as e:
            from urllib.parse import quote

            return RedirectResponse(f"/login?error={quote(str(getattr(e, 'message', e)))}")
        response = RedirectResponse("/")
        set_session(response, token)
        response.delete_cookie("shoppi_oauth")
        return response

    # -- Queries and commands --

    @app.post("/api/q/{name}")
    async def query_endpoint(name: str, request: Request, actor: Actor = Depends(current_actor)):
        return {"data": await _threaded(run_query, actor, name, await _json(request))}

    @app.post("/api/cmd/{name}")
    async def command_endpoint(name: str, request: Request, actor: Actor = Depends(current_actor)):
        return await _threaded(execute, actor, name, await _json(request))

    @app.post("/api/change_requests/{cr_id}/decide")
    def decide(cr_id: UUID, body: DecideIn, actor: Actor = Depends(current_actor)) -> dict:
        return decide_change_request(actor, cr_id, body.approve, body.note)

    # -- Uploads --

    @app.post("/api/documents/upload")
    async def upload_document(actor: Actor = Depends(current_actor), file: UploadFile = File(...), title: str = Form(""),
                              customer_id: str = Form(""), vehicle_id: str = Form(""), work_order_id: str = Form("")):
        from shoppi.knowledge.ingest import UnsupportedDocument, extract_text

        data = await file.read(MAX_UPLOAD_BYTES + 1)
        try:
            body = extract_text(file.filename or "upload.txt", data)
        except UnsupportedDocument as e:
            raise Invalid(str(e)) from e
        args = {"title": title.strip() or Path(file.filename or "Document").stem, "text": body, "filename": file.filename,
                "customer_id": customer_id or None, "vehicle_id": vehicle_id or None, "work_order_id": work_order_id or None}
        return await _threaded(execute, actor, "documents.add", args)

    @app.post("/api/catalog/upload")
    async def upload_catalog(actor: Actor = Depends(current_actor), file: UploadFile = File(...), supplier_name: str = Form("")):
        raw = (await file.read(MAX_UPLOAD_BYTES + 1)).decode("utf-8-sig", errors="replace")
        rows = []
        for record in csv.DictReader(io.StringIO(raw)):
            r = {(k or "").strip().lower().replace(" ", "_"): (v or "").strip() for k, v in record.items()}
            number = r.get("part_number") or r.get("sku") or r.get("part_no") or r.get("number")
            description = r.get("description") or r.get("name")
            if not number or not description:
                continue
            rows.append({"part_number": number, "description": description, "brand": r.get("brand") or None,
                         "fitment": r.get("fitment") or r.get("fits") or None, "list_price": _num(r.get("list_price") or r.get("price")),
                         "cost": _num(r.get("cost")), "qty_available": _num(r.get("qty_available") or r.get("qty"), int),
                         "eta_days": _num(r.get("eta_days") or r.get("lead_days"), int)})
        if not rows:
            raise Invalid("No rows found. The file needs a header row with at least part_number and description columns.")
        args = {"supplier_name": supplier_name.strip() or Path(file.filename or "Uploaded price file").stem, "rows": rows}
        return await _threaded(execute, actor, "catalog.replace_csv", args)

    @app.post("/api/calls/upload")
    async def upload_call(actor: Actor = Depends(current_actor), file: UploadFile | None = File(None), text_: str = Form("", alias="text"),
                          from_number: str = Form(""), customer_id: str = Form(""), consent_noted: bool = Form(False)):
        from shoppi.integrations.transcripts import parse_plain_text, transcript_from_json

        raw = (await file.read(MAX_UPLOAD_BYTES + 1)).decode("utf-8", errors="replace") if file is not None else text_
        if not raw.strip():
            raise Invalid("Paste a transcript or choose a file.")
        payload: dict = {"provider": "manual", "external_id": f"manual-{uuid4().hex[:12]}", "from_number": from_number or None,
                         "customer_id": customer_id or None, "consent_noted": consent_noted}
        segments = None
        if raw.lstrip().startswith("{"):
            try:
                parsed = transcript_from_json({"external_id": payload["external_id"], **json.loads(raw)})
                segments = parsed.segments
                payload.update(from_number=payload["from_number"] or parsed.from_number, to_number=parsed.to_number,
                               duration_s=parsed.duration_s, direction=parsed.direction,
                               started_at=parsed.started_at.isoformat() if parsed.started_at else None)
            except (ValueError, IntegrationError):
                segments = None
        if segments is None:
            segments = parse_plain_text(raw)
        if not segments:
            raise Invalid("No transcript text was found.")
        payload["segments"] = [{"speaker": s.speaker, "text": s.text[:5000], "start_s": s.start_s} for s in segments]
        return await _threaded(execute, actor, "communications.ingest_transcript", payload)

    # -- Integrations: OAuth connect and inbound webhooks --

    @app.get("/api/integrations/{key}/connect")
    def integration_connect(key: str, actor: Actor = Depends(current_actor)) -> dict:
        if not actor.is_manager:
            raise Forbidden("Only an owner or advisor can connect integrations.")
        integration = registry.get(key)
        available, reason = integration.is_available()
        if integration.connect != "oauth" or not available:
            raise Invalid(reason or "This integration is not connected by signing in.")
        state_token = sign_state(str(actor.shop_id), str(actor.user_id), key)
        return {"url": integration.oauth_url(state_token, f"{settings.base_url}/api/integrations/{key}/callback")}

    @app.get("/api/integrations/{key}/callback")
    def integration_callback(key: str, request: Request):
        from urllib.parse import quote

        try:
            actor = current_actor(request)
            params = dict(request.query_params)
            # The state must have been issued to this very shop and user: a callback
            # can't be replayed into someone else's session.
            if not actor.is_manager or not verify_state(params.get("state", ""), str(actor.shop_id), str(actor.user_id), key):
                raise IntegrationError("The connection attempt expired. Please try again.")
            integration = registry.get(key)
            secret, options = integration.oauth_exchange(params, f"{settings.base_url}/api/integrations/{key}/callback")
            execute(actor, "integrations.store_oauth", {"key": key, "secret": secret, "options": options})
        except (IntegrationError, AppError) as e:
            return RedirectResponse(f"/settings/integrations?error={quote(str(getattr(e, 'message', e)))}")
        registry.calendar_changed(actor.shop_id)
        return RedirectResponse(f"/settings/integrations?connected={key}")

    @app.post("/api/integrations/{key}/webhook/{hook_id}")
    async def integration_webhook(key: str, hook_id: str, request: Request, background: BackgroundTasks):
        limiter.check(f"hook:{hook_id}", 120, 60)
        body = await request.body()
        if len(body) > MAX_WEBHOOK_BYTES:
            return JSONResponse(status_code=413, content={"error": {"code": "too_large", "message": "Payload too large."}})
        # Unknown hook ids, unknown integrations and bad signatures all get the same answer.
        denied = JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "Invalid signature."}})
        with identity_tx() as tx:
            shop_id = tx.execute(text("SELECT resolve_hook(:h)"), {"h": hook_id}).scalar()
        if shop_id is None or key not in registry.REGISTRY:
            return denied
        actor = Actor(shop_id=shop_id, role="system", kind="integration", source=f"integration:{key}", name=registry.REGISTRY[key].name, ref=key)
        try:
            source = registry.transcript_source(key)
            with tenant_tx(actor) as tx:
                ctx = _ctx(tx, actor)
                enabled, creds = registry.is_enabled(ctx, registry.REGISTRY[key]), registry.creds_for(ctx, key)
            headers = {k.lower(): v for k, v in request.headers.items()}
            if not enabled or not source.verify_webhook(headers, body, creds):
                return denied
            transcript = source.parse(json.loads(body))
        except (IntegrationError, ValueError) as e:
            return JSONResponse(status_code=422, content={"error": {"code": "invalid", "message": str(e)}})
        payload = {
            "provider": key, "external_id": transcript.external_id, "direction": transcript.direction,
            "from_number": transcript.from_number, "to_number": transcript.to_number,
            "started_at": transcript.started_at.isoformat() if transcript.started_at else None,
            "duration_s": transcript.duration_s, "language": transcript.language, "consent_noted": transcript.consent_noted,
            "segments": [{"speaker": s.speaker, "text": s.text[:5000], "start_s": s.start_s} for s in transcript.segments],
        }
        # Indexing and the LLM draft can be slow: acknowledge now, process after responding.
        background.add_task(_ingest_quietly, actor, payload)
        return {"accepted": True}

    # -- Public customer intake (no login) --

    def intake_actor(token: str) -> Actor:
        with identity_tx() as tx:
            row = tx.execute(text("SELECT shop_id, link_id FROM resolve_intake_token(:h)"), {"h": auth.token_hash(token)}).first()
        if row is None:
            raise NotFound("This intake link is no longer active. Please ask the shop for a new one.")
        return Actor(shop_id=row.shop_id, role="public", kind="intake_link", source="intake_link",
                     name="Customer (intake form)", ref=str(row.link_id))

    @app.get("/api/public/intake/{token}")
    def public_intake_info(token: str, request: Request) -> dict:
        limiter.check(f"intake-info:{_client(request)}", 120, 600)
        actor = intake_actor(token)
        # The only shop data the form ever sees: the shop's name and phone number.
        with tenant_tx(Actor.system(actor.shop_id, "intake_link")) as tx:
            shop = tx.get(Shop, actor.shop_id)
            return {"shop_name": shop.name, "shop_phone": shop.phone}

    @app.post("/api/public/intake/{token}")
    async def public_intake_submit(token: str, request: Request) -> dict:
        limiter.check(f"intake-submit:{_client(request)}", 20, 600)
        return (await _threaded(execute, intake_actor(token), "intake.submit", await _json(request, limit=64 * 1024)))["result"]

    @app.get("/api/public/intake/{token}/vin/{vin}")
    def public_intake_vin(token: str, vin: str, request: Request) -> dict:
        from shoppi.domain.integrations import decode_vin

        limiter.check(f"intake-vin:{_client(request)}", 20, 600)
        actor = intake_actor(token)
        system = Actor.system(actor.shop_id, "intake_link")
        with tenant_tx(system) as tx:
            d = decode_vin(_ctx(tx, system), vin)
        return {k: d.get(k) for k in ("vin", "year", "make", "model", "powertrain", "gvwr_class")}

    # -- Assistant chat --

    def get_agent():
        if state["agent"] is None:
            from shoppi.agent.build import build_agent
            from shoppi.llm.factory import get_chat_model

            state["agent"] = build_agent(get_chat_model(), _checkpointer())
        return state["agent"]

    @app.get("/api/chat/threads")
    def chat_threads(actor: Actor = Depends(current_actor)) -> dict:
        with tenant_tx(actor) as tx:
            rows = tx.scalars(select(ChatThread).where(ChatThread.user_id == actor.user_id).order_by(ChatThread.updated_at.desc()).limit(30)).all()
            return {"data": [{"id": str(r.id), "title": r.title} for r in rows]}

    @app.get("/api/chat/threads/{thread_id}")
    def chat_history(thread_id: UUID, actor: Actor = Depends(current_actor)) -> dict:
        from shoppi.agent.build import thread_key

        _own_thread(actor, thread_id)
        snapshot = get_agent().get_state({"configurable": {"thread_id": thread_key(actor, thread_id)}})
        out = []
        for m in (snapshot.values or {}).get("messages", []):
            if isinstance(m, HumanMessage):
                out.append({"role": "user", "text": str(m.content)})
            elif isinstance(m, AIMessage) and m.content:
                out.append({"role": "assistant", "text": str(m.content)})
        return {"data": out}

    def _own_thread(actor: Actor, thread_id: UUID) -> None:
        with tenant_tx(actor) as tx:
            row = tx.get(ChatThread, thread_id)
            if row is None or row.user_id != actor.user_id:
                raise NotFound("Conversation not found.")

    @app.post("/api/chat")
    def chat(body: ChatIn, actor: Actor = Depends(current_actor)) -> StreamingResponse:
        from shoppi.agent.build import ModelCallFailed, ToolCallFailed, thread_key

        # The thread belongs to this user in this shop; its checkpoint key is derived
        # here, never taken from the browser.
        with tenant_tx(actor, command="chat.message", vertical="self") as tx:
            thread = tx.get(ChatThread, body.thread_id) if body.thread_id else None
            # A supplied id must be this user's own thread (another shop's id is simply not visible here).
            if body.thread_id and (thread is None or thread.user_id != actor.user_id):
                raise NotFound("Conversation not found.")
            if thread is None:
                thread = ChatThread(user_id=actor.user_id, title=body.message[:60])
                tx.add(thread)
                tx.flush()
            thread_id = thread.id
        agent_ = get_agent()

        def events():
            yield _sse({"type": "thread", "thread_id": str(thread_id)})
            config = {"configurable": {"thread_id": thread_key(actor, thread_id)}}
            try:
                # "messages" mode yields model output token by token;
                # "updates" mode yields each finished graph step (tool calls/results).
                for mode, chunk in agent_.stream({"messages": [("user", body.message)]}, config, context=actor,
                                                 stream_mode=["messages", "updates"]):
                    if mode == "messages":
                        msg, meta = chunk
                        if isinstance(msg, AIMessage) and msg.content and meta.get("langgraph_node") == "model":
                            yield _sse({"type": "token", "text": msg.content if isinstance(msg.content, str) else ""})
                        continue
                    for node, update in chunk.items():
                        for msg in (update or {}).get("messages", []):
                            for call in getattr(msg, "tool_calls", None) or []:
                                yield _sse({"type": "tool_call", "name": call["name"], "args": call["args"]})
                            if node == "tools" and isinstance(msg, ToolMessage):
                                content = str(msg.content)
                                proposal = _proposal(actor, content)
                                if proposal:
                                    yield _sse({"type": "proposal", "change_request": proposal})
                                else:
                                    yield _sse({"type": "tool_result", "name": msg.name, "content": content[:2000]})
            # Say which part failed, and keep the details in the server log.
            except ToolCallFailed as e:  # a bug or outage behind one of the assistant's tools
                log.exception("assistant tool %s failed", e.tool)
                yield _sse({"type": "error", "message": f"The assistant couldn't finish: its \"{e.tool.replace('_', ' ')}\" step "
                            f"failed ({type(e.__cause__).__name__}). This is a problem in the app, not the language model. "
                            "The details are in the server log."})
            except ModelCallFailed as e:
                log.exception("assistant model call failed")
                yield _sse({"type": "error", "message": "The assistant couldn't answer just now: the language model call failed "
                            f"({type(e.__cause__).__name__}). Check that the model is running and available, then try again."})
            except Exception as e:
                log.exception("assistant failed")
                yield _sse({"type": "error", "message": f"The assistant couldn't answer just now ({type(e).__name__}). "
                            "The details are in the server log."})
            yield _sse({"type": "done"})

        # A sync generator is run in Starlette's threadpool, so blocking model and
        # database calls don't stall the event loop.
        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # -- The React app --

    if DIST_DIR.exists():
        if (DIST_DIR / "assets").exists():
            app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message": "Not found."}})
            candidate = (DIST_DIR / path).resolve()
            if path and candidate.is_file() and DIST_DIR.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(DIST_DIR / "index.html", headers={"Cache-Control": "no-cache"})
    else:
        @app.get("/", include_in_schema=False)
        def no_ui():
            return JSONResponse({"message": "The API is running, but the web UI has not been built. Run: cd web && npm install && npm run build"})

    return app


# ---- Helpers ----------------------------------------------------------------------


def _session_token(request: Request) -> str | None:
    """The session token: the Authorization header if the UI sent one, else the cookie."""
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return request.cookies.get(COOKIE)


async def _json(request: Request, limit: int = 4 * 1024 * 1024) -> dict:
    body = await request.body()
    if len(body) > limit:
        raise Invalid("The request is too large.")
    if not body:
        return {}
    try:
        data = json.loads(body)
    except ValueError as e:
        raise Invalid("The request body is not valid JSON.") from e
    if not isinstance(data, dict):
        raise Invalid("The request body must be a JSON object.")
    return data


async def _threaded(fn, *args):
    """Run blocking database / model work off the event loop."""
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(fn, *args)


def _num(value, kind=float):
    if value in (None, ""):
        return None
    try:
        return kind(float(str(value).replace("$", "").replace(",", "")))
    except ValueError:
        return None


def _ingest_quietly(actor: Actor, payload: dict) -> None:
    try:
        execute(actor, "communications.ingest_transcript", payload)
    except Exception:
        log.exception("transcript ingest failed")


def _proposal(actor: Actor, tool_content: str) -> dict | None:
    """If a tool result is an assistant proposal, load it so the chat can show a confirm card."""
    if "PROPOSED_NOT_APPLIED" not in tool_content:
        return None
    try:
        cr_id = json.loads(tool_content)["change_request_id"]
        rows = run_query(actor, "change_requests.list", {"status": "pending", "limit": 50})
        return next((r for r in rows if r["id"] == cr_id), None)
    except (ValueError, KeyError, AppError):
        return None


_saver = None


def _checkpointer():
    """Conversation memory in Postgres, so chats survive restarts and work with
    several web workers. Falls back to in-memory if its tables aren't set up."""
    global _saver
    if _saver is not None:
        return _saver
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        conninfo = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
        pool = ConnectionPool(conninfo, max_size=5, kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}, open=True)
        with pool.connection() as conn:
            conn.execute("SELECT 1 FROM checkpoints LIMIT 1")
        _saver = PostgresSaver(pool)
    except Exception:
        from langgraph.checkpoint.memory import InMemorySaver

        log.warning("Chat history tables not found; conversations will be kept in memory only. Run scripts/migrate.py.")
        _saver = InMemorySaver()
    return _saver


def build_app() -> FastAPI:
    """Wire real dependencies from configuration (for `uvicorn --factory`)."""
    logging.basicConfig(level=logging.INFO)
    return create_app()
