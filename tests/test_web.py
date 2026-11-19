"""The HTTP layer: sessions, CSRF, the generic query/command endpoints, the public
intake form, the transcript webhook and streaming chat."""

import hashlib
import hmac
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from shoppi.knowledge.embeddings import set_embeddings
from shoppi.web.app import create_app
from tests.fakes import ScriptedChatModel, WordHashEmbeddings

H = {"X-Requested-With": "shoppi"}


@pytest.fixture(autouse=True)
def offline_embeddings():
    set_embeddings(WordHashEmbeddings())
    yield
    set_embeddings(None)


def client(agent=None) -> TestClient:
    return TestClient(create_app(agent=agent), headers=H)


def signed_up(agent=None, starter=False) -> tuple[TestClient, str]:
    c = client(agent)
    email = f"owner-{uuid4().hex[:8]}@example.com"
    r = c.post("/api/auth/signup", json={"shop_name": "Web Shop", "name": "Olive Owner", "email": email,
                                         "password": "correct-horse-battery", "starter": starter})
    assert r.status_code == 200, r.text
    return c, email


def q(c, name, args=None):
    r = c.post(f"/api/q/{name}", json=args or {})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def cmd(c, name, args=None):
    r = c.post(f"/api/cmd/{name}", json=args or {})
    assert r.status_code == 200, r.text
    return r.json()


def test_signup_login_logout_and_me():
    c, email = signed_up(starter=True)
    me = c.get("/api/auth/me").json()
    assert me["user"]["role"] == "owner" and me["shop"]["name"] == "Web Shop" and me["permissions"]["admin"] == "edit"
    assert len(q(c, "bays.list")["bays"]) == 4  # starter data was installed
    assert "httponly" in c.post("/api/auth/login", json={"email": email, "password": "correct-horse-battery"}).headers["set-cookie"].lower()
    cookie = c.cookies.get("shoppi_session")

    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").status_code == 401
    # The old cookie is dead on the server too, not just cleared in the browser.
    stale = client()
    stale.cookies.set("shoppi_session", cookie)
    assert stale.get("/api/auth/me").status_code == 401

    bad = c.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
    unknown = c.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"})
    assert bad.status_code == unknown.status_code == 401 and bad.json() == unknown.json()  # no account enumeration
    assert c.post("/api/auth/signup", json={"shop_name": "x", "name": "x", "email": email, "password": "another-password"}).status_code == 409
    assert c.post("/api/auth/signup", json={"shop_name": "x", "name": "x", "email": "new@example.com", "password": "short"}).status_code == 422


def test_requests_need_a_session_and_the_csrf_header():
    anonymous = client()
    assert anonymous.post("/api/q/customers.list", json={}).status_code == 401
    c, _ = signed_up()
    no_header = TestClient(create_app())
    no_header.cookies.set("shoppi_session", c.cookies.get("shoppi_session"))
    r = no_header.post("/api/cmd/customers.create", json={"name": "CSRF"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf"
    assert q(c, "customers.list") == []


def test_errors_are_json_with_a_message():
    c, _ = signed_up()
    r = c.post("/api/cmd/customers.create", json={})
    assert r.status_code == 422 and "name" in r.json()["error"]["message"]
    assert c.post("/api/cmd/no.such.command", json={}).status_code == 404
    assert c.post("/api/q/customers.get", json={"id": str(uuid4())}).status_code == 404


def test_one_shop_cannot_reach_another_over_http():
    a, _ = signed_up()
    b, _ = signed_up()
    customer = cmd(a, "customers.create", {"name": "Only in A"})["result"]
    assert q(b, "customers.list") == []
    assert b.post("/api/q/customers.get", json={"id": customer["id"]}).status_code == 404
    assert b.post("/api/cmd/customers.update", json={"id": customer["id"], "name": "taken"}).status_code == 404
    assert q(b, "history.list") and all(r["label"] != "Only in A" for r in q(b, "history.list"))


def test_invite_then_mechanic_requests_a_change_and_owner_approves():
    owner, _ = signed_up()
    customer = cmd(owner, "customers.create", {"name": "Laura Lane"})["result"]
    email = f"mech-{uuid4().hex[:6]}@example.com"
    link = cmd(owner, "members.invite", {"email": email, "role": "mechanic"})["result"]["link"]
    token = link.rsplit("/", 1)[1]

    mechanic = client()
    info = mechanic.get(f"/api/auth/invite/{token}").json()
    assert info == {"email": email, "role": "mechanic", "shop_name": "Web Shop", "has_account": False}
    assert mechanic.post(f"/api/auth/invite/{token}", json={"name": "Max Mechanic", "password": "wrench-and-socket"}).status_code == 200
    assert mechanic.get(f"/api/auth/invite/{token}").status_code == 404  # single use
    assert mechanic.get("/api/auth/me").json()["permissions"]["customers"] == "view"

    pending = cmd(mechanic, "customers.update", {"id": customer["id"], "notes": "Call before 5pm"})
    assert pending["status"] == "pending"
    cr = pending["change_request"]["id"]
    assert mechanic.post(f"/api/change_requests/{cr}/decide", json={"approve": True}).status_code == 403
    assert mechanic.post("/api/q/integrations.list", json={}).status_code == 403
    assert owner.post(f"/api/change_requests/{cr}/decide", json={"approve": True}).json()["status"] == "applied"
    assert q(owner, "customers.get", {"id": customer["id"]})["notes"] == "Call before 5pm"


FORM = {"customer": {"name": "Walk-in Wendy", "phone": "555-333-4444"}, "vehicle": {"year": 2020, "make": "Toyota", "model": "Camry"},
        "concern": "Check engine light is on", "consent": True}


def test_public_intake_form():
    owner, _ = signed_up()
    url = cmd(owner, "intake_links.create", {"kind": "kiosk"})["result"]["url"]
    token = url.rsplit("/", 1)[1]

    public = client()  # no session at all
    assert public.get(f"/api/public/intake/{token}").json() == {"shop_name": "Web Shop", "shop_phone": None}
    done = public.post(f"/api/public/intake/{token}", json=FORM)
    assert done.status_code == 200 and done.json()["received"] is True
    assert public.post(f"/api/public/intake/{token}", json={**FORM, "consent": False}).status_code == 422
    assert public.post(f"/api/public/intake/{token}", json={**FORM, "concern": "x" * 5000}).status_code == 422
    assert public.get("/api/public/intake/not-a-token").status_code == 404
    assert public.post("/api/q/customers.list", json={}).status_code == 401

    queue = q(owner, "intake.list")
    assert len(queue) == 1 and queue[0]["payload"]["customer"]["name"] == "Walk-in Wendy"
    assert q(owner, "customers.list") == []
    cmd(owner, "intake_links.revoke", {"id": q(owner, "intake_links.list")[0]["id"]})
    assert public.post(f"/api/public/intake/{token}", json=FORM).status_code == 404


def test_transcript_webhook_checks_signature_and_ingests_once():
    from shoppi.domain.communications import IntakeDraft, set_extractor

    set_extractor(lambda t: IntakeDraft(is_service_request=True, summary="Wants brakes checked.", concern="Brakes squeal"))
    try:
        owner, _ = signed_up()
        cmd(owner, "customers.create", {"name": "Laura Lane", "phone": "555-201-7788"})
        secret = cmd(owner, "integrations.connect", {"key": "transcript_webhook", "values": {}})["result"]["generated"]["secret"]
        hook = next(i for i in q(owner, "integrations.list") if i["key"] == "transcript_webhook")["webhook_url"]
        path = hook[hook.index("/api/"):]
        body = json.dumps({"external_id": "c-1", "from_number": "+15552017788",
                           "segments": [{"speaker": "Customer", "text": "My brakes squeal, can you look at them?"}]}).encode()
        sign = lambda b: hmac.new(secret.encode(), b, hashlib.sha256).hexdigest()  # noqa: E731

        outside = TestClient(create_app())  # a phone system has no session and no CSRF header
        assert outside.post(path, content=body, headers={"X-Shoppi-Signature": "deadbeef"}).status_code == 401
        assert outside.post(path, content=body).status_code == 401
        wrong_shop = path.rsplit("/", 1)[0] + "/not-a-real-hook-id"
        assert outside.post(wrong_shop, content=body, headers={"X-Shoppi-Signature": sign(body)}).status_code == 401
        assert q(owner, "calls.list") == []

        for _ in range(2):  # the provider retries: still one call
            r = outside.post(path, content=body, headers={"X-Shoppi-Signature": sign(body)})
            assert r.status_code == 200 and r.json() == {"accepted": True}
        calls = q(owner, "calls.list")
        assert len(calls) == 1 and calls[0]["customer_name"] == "Laura Lane" and calls[0]["provider"] == "transcript_webhook"
        queue = q(owner, "intake.list")
        assert len(queue) == 1 and queue[0]["source"] == "call"
        history = q(owner, "history.list", {"entity_type": "calls"})
        assert history[0]["actor_type"] == "integration" and history[0]["source"] == "integration:transcript_webhook"
    finally:
        set_extractor(None)


def test_pasted_transcript_follows_the_same_path():
    from shoppi.domain.communications import set_extractor

    set_extractor(lambda t: None)
    try:
        owner, _ = signed_up()
        r = owner.post("/api/calls/upload", data={"text": "Shop: Hello.\nCustomer: My truck won't start.", "from_number": "555-000-1111"})
        assert r.status_code == 200, r.text
        call = q(owner, "calls.get", {"id": r.json()["result"]["id"]})
        assert [s["speaker"] for s in call["segments"]] == ["Shop", "Customer"] and call["provider"] == "manual"
        assert q(owner, "documents.search", {"query": "truck start"})[0]["kind"] == "call_transcript"
    finally:
        set_extractor(None)


def test_document_and_price_file_uploads():
    owner, _ = signed_up()
    r = owner.post("/api/documents/upload", files={"file": ("warranty.md", b"# Warranty\nBrake pads are covered for 24 months.", "text/markdown")})
    assert r.status_code == 200 and r.json()["result"]["status"] == "indexed", r.text
    assert q(owner, "documents.search", {"query": "brake pads covered"})[0]["source"] == "warranty"
    assert owner.post("/api/documents/upload", files={"file": ("virus.exe", b"MZ", "application/octet-stream")}).status_code == 422

    csv_file = b"Part Number,Description,Brand,Cost,Qty,ETA Days\nACM-1,Front brake pads,Acme,$35.50,9,2\n,missing number,,,,\n"
    r = owner.post("/api/catalog/upload", files={"file": ("acme.csv", csv_file, "text/csv")}, data={"supplier_name": "Acme"})
    assert r.status_code == 200 and r.json()["result"]["rows"] == 1, r.text
    hit = q(owner, "catalog.search", {"query": "brake pads"})["results"][0]
    assert (hit["supplier"], hit["cost"], hit["best_eta_days"]) == ("Acme", 35.5, 2)


def sse(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_chat_streams_and_proposals_can_be_confirmed():
    from shoppi.agent.build import build_agent

    script = [AIMessage(content="", tool_calls=[{"name": "customers_create", "args": {"name": "Chat Charlie"}, "id": "1"}]),
              AIMessage(content="I've proposed adding Chat Charlie. Please confirm."),
              AIMessage(content="Second answer.")]
    c, _ = signed_up(agent=build_agent(ScriptedChatModel(script=script, seen=[])))
    events = sse(c.post("/api/chat", json={"message": "Add a customer called Chat Charlie"}))
    types = [e["type"] for e in events]
    assert types[0] == "thread" and types[-1] == "done" and "tool_call" in types and "proposal" in types and "token" in types
    proposal = next(e for e in events if e["type"] == "proposal")["change_request"]
    assert proposal["command"] == "customers.create" and proposal["can_decide"]
    assert q(c, "customers.list") == []
    assert c.post(f"/api/change_requests/{proposal['id']}/decide", json={"approve": True}).json()["status"] == "applied"
    assert q(c, "customers.list")[0]["name"] == "Chat Charlie"

    thread = events[0]["thread_id"]
    again = sse(c.post("/api/chat", json={"message": "thanks", "thread_id": thread}))
    assert again[0]["thread_id"] == thread
    history = c.get(f"/api/chat/threads/{thread}").json()["data"]
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]

    # Another shop's user can't open or continue this conversation, even knowing its id.
    other, _ = signed_up(agent=build_agent(ScriptedChatModel(script=[AIMessage(content="x")], seen=[])))
    assert other.get(f"/api/chat/threads/{thread}").status_code == 404
    assert other.post("/api/chat", json={"message": "hi", "thread_id": thread}).status_code == 404


def test_chat_reports_model_failure_without_leaking_details():
    from shoppi.agent.build import build_agent

    c, _ = signed_up(agent=build_agent(ScriptedChatModel(script=[], seen=[])))  # an empty script raises inside the model
    events = sse(c.post("/api/chat", json={"message": "hello"}))
    error = next(e for e in events if e["type"] == "error")
    assert "couldn't answer" in error["message"] and "pop from empty list" not in error["message"]
    assert events[-1]["type"] == "done"
