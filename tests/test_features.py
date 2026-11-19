"""Intake, calendar, inventory, knowledge base, integrations, call transcripts and the assistant."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta

import httpx
import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import text

from shoppi.core.actor import Actor
from shoppi.core.commands import Conflict, Forbidden, Invalid, decide_change_request, execute, run_query
from shoppi.db.session import identity_tx, tenant_tx
from shoppi.knowledge.embeddings import set_embeddings
from tests.conftest import TZ, next_weekday
from tests.fakes import ScriptedChatModel, WordHashEmbeddings

MON = next_weekday(0)


def iso(dt):
    return dt.replace(tzinfo=None).isoformat(timespec="seconds")


@pytest.fixture(autouse=True)
def offline_embeddings():
    set_embeddings(WordHashEmbeddings())
    yield
    set_embeddings(None)


# ---- Intake -----------------------------------------------------------------------


def public_actor(shop, link_url):
    from shoppi.auth.service import token_hash

    token = link_url.rsplit("/", 1)[1]
    with identity_tx() as tx:
        row = tx.execute(text("SELECT shop_id, link_id FROM resolve_intake_token(:h)"), {"h": token_hash(token)}).first()
    assert row is not None, "link should resolve"
    return Actor(shop_id=row.shop_id, role="public", kind="intake_link", source="intake_link", name="Customer (intake link)", ref=str(row.link_id))


FORM = {"customer": {"name": "Laura Lane", "phone": "555-201-7788", "email": "laura@example.com"},
        "vehicle": {"year": 2023, "make": "Ford", "model": "Bronco Sport", "mileage": 73418},
        "concern": "Grinding noise from the front when braking", "preferred_times": "Any morning next week", "consent": True}


def test_customer_intake_lands_in_review_queue_and_changes_nothing(shop):
    link = shop.run("intake_links.create", {"label": "Front desk", "kind": "kiosk"})
    actor = public_actor(shop, link["url"])
    result = execute(actor, "intake.submit", FORM)
    assert result["status"] == "applied" and result["result"]["received"]
    assert shop.q("customers.list") == []  # nothing live was created

    queue = shop.q("intake.list")
    assert len(queue) == 1 and queue[0]["payload"]["customer"]["name"] == "Laura Lane"
    row = shop.q("history.list", {"entity_type": "intake_submissions"})[0]
    assert row["actor_type"] == "intake_link" and row["source"] == "intake_link"

    accepted = shop.run("intake.accept", {
        "id": queue[0]["id"], "customer": FORM["customer"], "vehicle": {"year": 2023, "make": "Ford", "model": "Bronco Sport"},
        "concern": FORM["concern"], "service_type_ids": [shop.ids["services"]["Brake service"]]})
    assert accepted["work_order"]["status"] == "draft"
    assert shop.q("customers.list")[0]["name"] == "Laura Lane"
    assert shop.q("intake.list") == []
    with pytest.raises(Conflict):
        execute(shop.owner, "intake.accept", {"id": queue[0]["id"], "customer_id": accepted["customer_id"], "vehicle_id": accepted["vehicle_id"], "concern": "x"})


def test_public_link_can_do_nothing_else(shop):
    link = shop.run("intake_links.create", {"kind": "kiosk"})
    actor = public_actor(shop, link["url"])
    for name in ("customers.list", "calendar.feed", "shop.get", "history.list", "employees.list"):
        with pytest.raises(Forbidden):
            run_query(actor, name, {"start": "2026-01-01T00:00:00", "end": "2026-01-02T00:00:00"} if name == "calendar.feed" else {})
    with pytest.raises(Forbidden):
        execute(actor, "customers.create", {"name": "x"})
    with pytest.raises(Invalid):
        execute(actor, "intake.submit", {**FORM, "consent": False})


def test_single_use_and_revoked_links_stop_resolving(shop):
    from shoppi.auth.service import token_hash

    def resolves(url):
        with identity_tx() as tx:
            return tx.execute(text("SELECT 1 FROM resolve_intake_token(:h)"), {"h": token_hash(url.rsplit("/", 1)[1])}).first() is not None

    single = shop.run("intake_links.create", {"kind": "single"})
    execute(public_actor(shop, single["url"]), "intake.submit", FORM)
    assert not resolves(single["url"])
    kiosk = shop.run("intake_links.create", {"kind": "kiosk"})
    assert resolves(kiosk["url"])
    shop.run("intake_links.revoke", {"id": kiosk["id"]})
    assert not resolves(kiosk["url"])
    assert not resolves("http://x/i/not-a-real-token")


def test_staff_intake_matches_existing_customer(shop):
    first = shop.run("intake.staff_create", {"customer": {"name": "Laura Lane", "phone": "(555) 201-7788"},
                                             "vehicle": {"year": 2023, "make": "Ford", "model": "Bronco Sport", "plate": "ABC123"},
                                             "concern": "Oil change", "service_type_ids": [shop.ids["services"]["Oil & filter change"]]})
    link = shop.run("intake_links.create", {"kind": "kiosk"})
    execute(public_actor(shop, link["url"]), "intake.submit", {**FORM, "vehicle": {"plate": "abc123"}})
    sub = shop.q("intake.get", {"id": shop.q("intake.list")[0]["id"]})
    assert [c["id"] for c in sub["matches"]["customers"]] == [first["customer_id"]]  # same phone, different formatting
    assert [v["id"] for v in sub["matches"]["vehicles"]] == [first["vehicle_id"]]


# ---- Calendar ---------------------------------------------------------------------


def test_calendar_feed_merges_every_kind(shop):
    gus = shop.employee("Gus General", ["ASE-A5"], expires=(MON + timedelta(days=2)).date().isoformat())
    shop.shifts(gus, MON, days=3)
    wo = shop.work_order(["Oil & filter change"])
    cand = shop.q("schedule.find_slots", {"work_order_id": wo["id"], "earliest": iso(MON), "days": 3})["candidates"][0]
    shop.run("schedule.book", {"work_order_id": wo["id"], "steps": [{k: s[k] for k in ("line_id", "step_id", "employee_id", "bay_id", "start", "end")} for s in cand["steps"]]})
    shop.run("events.create", {"title": "Vendor visit", "kind": "vendor", "start": iso(MON.replace(hour=14)), "end": iso(MON.replace(hour=15))})
    shop.run("time_off.create", {"employee_id": gus, "start": iso((MON + timedelta(days=4)).replace(hour=8)), "end": iso((MON + timedelta(days=4)).replace(hour=17))})
    shop.run("bays.close", {"bay_id": shop.ids["bays"]["Truck bay"], "start": iso(MON.replace(hour=8)), "end": iso(MON.replace(hour=12)), "reason": "Lift service"})
    part = shop.run("parts.create", {"sku": "P1", "name": "Pads"})
    po = shop.run("purchase_orders.create", {"lines": [{"part_id": part["id"], "qty": 2}], "eta": iso((MON + timedelta(days=1)).replace(hour=10))})
    shop.run("purchase_orders.mark_ordered", {"id": po["id"]})
    shop.run("work_orders.update", {"id": wo["id"], "promised_at": iso((MON + timedelta(days=1)).replace(hour=16))})

    feed = shop.q("calendar.feed", {"start": iso(MON), "end": iso(MON + timedelta(days=5))})
    kinds = {i["kind"] for i in feed}
    assert kinds == {"job", "event", "shift", "time_off", "bay_closure", "delivery", "cert_expiry", "promised"}
    job = next(i for i in feed if i["kind"] == "job")
    assert job["employee_name"] == "Gus General" and job["bay_name"] and job["editable"] and job["start"] == cand["start"]
    # Filters
    assert {i["kind"] for i in shop.q("calendar.feed", {"start": iso(MON), "end": iso(MON + timedelta(days=5)), "kinds": ["job", "event"]})} == {"job", "event"}
    by_bay = shop.q("calendar.feed", {"start": iso(MON), "end": iso(MON + timedelta(days=5)), "bay_id": shop.ids["bays"]["Truck bay"], "kinds": ["job", "bay_closure"]})
    assert [i["kind"] for i in by_bay] == ["bay_closure"]


def test_calendar_times_are_shop_local_across_daylight_saving(shop):
    # US daylight saving ends on the first Sunday of November: 9:00 local is a different UTC hour either side.
    for day in ("2027-11-05", "2027-11-08"):
        shop.run("events.create", {"title": f"Standup {day}", "start": f"{day}T09:00:00", "end": f"{day}T09:30:00"})
    feed = shop.q("calendar.feed", {"start": "2027-11-01T00:00:00", "end": "2027-11-10T00:00:00"})
    assert [i["start"] for i in feed] == ["2027-11-05T09:00:00", "2027-11-08T09:00:00"]
    with tenant_tx(shop.owner) as tx:
        utc_hours = [r[0] for r in tx.execute(text("SELECT extract(hour from starts_at at time zone 'UTC')::int FROM calendar_events ORDER BY starts_at"))]
    assert utc_hours == [13, 14]


def test_repeating_events_and_shifts(shop):
    made = shop.run("events.create", {"title": "Safety meeting", "kind": "meeting", "start": "2027-11-01T08:00:00", "end": "2027-11-01T08:30:00",
                                      "repeat": "weekly", "repeat_until": "2027-11-29"})
    assert made["created"] == 5
    feed = shop.q("calendar.feed", {"start": "2027-11-01T00:00:00", "end": "2027-12-01T00:00:00", "kinds": ["event"]})
    assert all(i["start"].endswith("T08:00:00") for i in feed) and len(feed) == 5  # stays 8:00 across the DST change
    assert shop.run("events.delete", {"id": feed[2]["ref_id"], "whole_series": True})["deleted"] == 3

    gus = shop.employee("Gus General", [])
    made = shop.run("shifts.create", {"employee_id": gus, "start": "2027-11-01T08:00:00", "end": "2027-11-01T17:00:00",
                                      "repeat_weekdays": [0, 1, 2, 3, 4], "repeat_until": "2027-11-12"})
    assert made["created"] == 10
    with pytest.raises(Conflict):
        execute(shop.owner, "shifts.create", {"employee_id": gus, "start": "2027-11-02T10:00:00", "end": "2027-11-02T12:00:00"})


def test_calendar_needs_no_integration(shop):
    assert not [i for i in shop.q("integrations.list") if i["key"] == "google_calendar" and i["connected"]]
    shop.run("events.create", {"title": "Works offline", "start": iso(MON.replace(hour=9)), "end": iso(MON.replace(hour=10))})
    assert shop.q("calendar.feed", {"start": iso(MON), "end": iso(MON + timedelta(days=1))})[0]["title"] == "Works offline"


# ---- Inventory --------------------------------------------------------------------


def test_stock_ledger_purchase_orders_and_reorder_suggestions(shop):
    filt = shop.run("parts.create", {"sku": "OF-1", "name": "Oil filter", "on_hand": 3, "reorder_point": 4, "reorder_qty": 12, "unit_cost": 5})
    assert [p["sku"] for p in shop.q("inventory.reorder_suggestions")] == ["OF-1"]
    po = shop.run("purchase_orders.create", {"lines": [{"part_id": filt["id"], "qty": 12}]})
    shop.run("purchase_orders.mark_ordered", {"id": po["id"]})
    assert shop.q("inventory.reorder_suggestions") == []  # on order now covers it
    received = shop.run("purchase_orders.receive", {"id": po["id"], "lines": [{"line_id": po["lines"][0]["id"], "qty": 5}]})
    assert received["status"] == "ordered"
    assert shop.run("purchase_orders.receive", {"id": po["id"]})["status"] == "received"
    part = shop.q("parts.get", {"id": filt["id"]})
    assert part["on_hand"] == 15 and [m["delta"] for m in part["movements"]] == [7, 5, 3]
    with pytest.raises(Conflict):
        execute(shop.owner, "parts.adjust", {"part_id": filt["id"], "delta": -99})


def test_completing_a_work_order_consumes_reserved_parts(shop):
    gus = shop.employee("Gus General", [])
    shop.shifts(gus, MON)
    filt = shop.run("parts.create", {"sku": "OF-1", "name": "Oil filter", "on_hand": 2})
    svc = next(s for s in shop.q("services.list") if s["name"] == "Oil & filter change")
    shop.run("services.save", {"id": svc["id"], "name": svc["name"], "steps": [{"name": "Oil & filter change", "minutes": 30}],
                               "parts": [{"part_id": filt["id"], "qty": 1}]})
    wo = shop.work_order(["Oil & filter change"])
    cand = shop.q("schedule.find_slots", {"work_order_id": wo["id"], "earliest": iso(MON), "days": 3})["candidates"][0]
    shop.run("schedule.book", {"work_order_id": wo["id"], "steps": [{k: s[k] for k in ("line_id", "step_id", "employee_id", "bay_id", "start", "end")} for s in cand["steps"]]})
    assert shop.q("parts.get", {"id": filt["id"]})["available"] == 1
    shop.run("work_orders.set_status", {"id": wo["id"], "status": "completed"})
    part = shop.q("parts.get", {"id": filt["id"]})
    assert part["on_hand"] == 1 and part["reserved"] == 0 and part["movements"][0]["reason"] == "use"


# ---- Knowledge base ---------------------------------------------------------------


def test_documents_are_searchable_only_within_their_shop(shop, other_shop):
    shop.run("documents.add", {"title": "Warranty policy", "text": "Brake pads carry a 24 month warranty. Rotors carry 12 months."})
    other_shop.run("documents.add", {"title": "Their secret pricing", "text": "Brake pads warranty secret pricing sheet for the other shop."})
    hits = shop.q("documents.search", {"query": "brake pads warranty"})
    assert [h["source"] for h in hits] == ["Warranty policy"]
    assert "24 month" in hits[0]["text"]
    with pytest.raises(Conflict):
        execute(shop.owner, "documents.add", {"title": "dup", "text": "Brake pads carry a 24 month warranty. Rotors carry 12 months."})


def test_reindexing_one_document_leaves_the_others(shop):
    a = shop.run("documents.add", {"title": "A", "text": "alignment specifications for trucks"})
    shop.run("documents.add", {"title": "B", "text": "torque specifications for wheels"})
    shop.run("documents.delete", {"id": a["id"]})
    assert [h["source"] for h in shop.q("documents.search", {"query": "specifications"})] == ["B"]


# ---- VIN decoding -----------------------------------------------------------------

VPIC = {"Results": [{"ModelYear": "2019", "Make": "FREIGHTLINER", "Model": "Cascadia", "BodyClass": "Truck-Tractor",
                     "FuelTypePrimary": "Diesel", "DisplacementL": "14.8", "EngineCylinders": "6", "EngineModel": "DD15",
                     "GVWR": "Class 8: 33,001 lb and above (14,969 kg and above)", "DriveType": "6x4"}]}
GOOD_VIN = "1M8GDM9AXKP042788"  # the standard worked example of a valid check digit


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_vin_validation_is_local_and_decode_is_cached(shop, monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return FakeResponse(VPIC)

    monkeypatch.setattr(httpx, "get", fake_get)
    with tenant_tx(shop.owner) as tx:  # the cache is global: make sure this VIN isn't already in it
        tx.execute(text("DELETE FROM vin_cache WHERE vin = :v"), {"v": GOOD_VIN})

    for bad in ("1M8GDM9A1KP042788", "TOOSHORT", "1M8GDM9AXKP04278O"):
        with pytest.raises(Invalid):
            run_query(shop.owner, "vin.decode", {"vin": bad})
    assert calls == []  # an invalid VIN never leaves the server

    decoded = shop.q("vin.decode", {"vin": "1m8gdm9axkp042788"})
    assert (decoded["year"], decoded["make"], decoded["model"]) == (2019, "Freightliner", "Cascadia")
    assert decoded["gvwr_class"] == 8 and decoded["powertrain"] == "diesel" and "14.8L" in decoded["engine"]
    assert shop.q("vin.decode", {"vin": GOOD_VIN})["cached"] is True
    assert len(calls) == 1 and GOOD_VIN in calls[0]


def test_vin_lookup_failure_falls_back_to_manual_entry(shop, monkeypatch):
    def boom(url, **kw):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(httpx, "get", boom)
    with pytest.raises(Invalid, match="by hand"):
        run_query(shop.owner, "vin.decode", {"vin": "11111111111111111"})
    health = next(i for i in shop.q("integrations.list") if i["key"] == "nhtsa_vin")["health"]
    assert health["failures"] == 1 and health["calls"] == 1  # the failure is recorded for the health view
    shop.run("integrations.set_enabled", {"key": "nhtsa_vin", "enabled": False})
    with pytest.raises(Invalid, match="switched off"):
        run_query(shop.owner, "vin.decode", {"vin": "11111111111111111"})


# ---- Parts catalogs ---------------------------------------------------------------


def test_catalog_search_merges_providers_and_feeds_the_scheduler(shop):
    shop.run("integrations.set_enabled", {"key": "demo_catalog", "enabled": True})
    shop.run("catalog.replace_csv", {"supplier_name": "Acme Truck Parts", "rows": [
        {"part_number": "ACM-BP-150", "description": "Front brake pads F-150", "brand": "Acme", "cost": 35.5, "list_price": 62, "qty_available": 9, "eta_days": 2}]})
    result = shop.q("catalog.search", {"query": "front brake pads"})
    suppliers = {r["supplier"] for r in result["results"]}
    assert suppliers == {"Acme Truck Parts", "Demo Parts Supply"} and len(result["searched"]) == 2

    pads = shop.run("catalog.add_to_inventory", {"provider": "csv_catalog", "part_number": "ACM-BP-150", "description": "Front brake pads F-150",
                                                 "cost": 35.5, "eta_days": 2})
    bea = shop.employee("Bea Brakes", ["ASE-A5"])
    shop.shifts(bea, MON)
    svc = next(s for s in shop.q("services.list") if s["name"] == "Brake service")
    shop.run("services.save", {"id": svc["id"], "name": svc["name"], "parts": [{"part_id": pads["id"], "qty": 1}],
                               "steps": [{"name": "Brake service", "minutes": 120, "requirements": [{"cert_type_id": shop.ids["certs"]["ASE-A5"]}]}]})
    wo = shop.work_order(["Brake service"])
    found = shop.q("schedule.find_slots", {"work_order_id": wo["id"], "days": 21})
    # Not in stock, but the catalog says two days: the job is offered from then, not refused.
    assert found["parts"]["lines"][0]["status"] == "order_needed"
    earliest_allowed = (datetime.now(TZ) + timedelta(days=2) - timedelta(minutes=5)).replace(tzinfo=None).isoformat()
    assert found["candidates"] and min(c["start"] for c in found["candidates"]) >= earliest_allowed


def test_supplier_order_needs_a_manager_and_a_confirmed_total(shop):
    from shoppi.integrations.parts import DemoCatalogProvider

    shop.run("integrations.set_enabled", {"key": "demo_catalog", "enabled": True})
    part = shop.run("catalog.add_to_inventory", {"provider": "demo_catalog", "part_number": "BOS-BC1414", "description": "Ceramic front brake pads", "cost": 41.2, "eta_days": 0})
    po = shop.run("purchase_orders.create", {"lines": [{"part_id": part["id"], "qty": 3}]})
    mechanic = shop.member("mechanic")
    with pytest.raises(Forbidden):
        execute(mechanic, "inventory.place_supplier_order", {"po_id": po["id"], "provider": "demo_catalog", "confirm_total": 123.6})
    with pytest.raises(Conflict, match="Review"):
        execute(shop.owner, "inventory.place_supplier_order", {"po_id": po["id"], "provider": "demo_catalog", "confirm_total": 1.0})
    before = len(DemoCatalogProvider.orders)
    placed = shop.run("inventory.place_supplier_order", {"po_id": po["id"], "provider": "demo_catalog", "confirm_total": 123.6})
    assert placed["supplier_reference"].startswith("DEMO-") and len(DemoCatalogProvider.orders) == before + 1
    assert shop.q("purchase_orders.list")[0]["status"] == "ordered"
    # The assistant can draft purchase orders but has no tool to place one.
    from shoppi.agent.tools import build_tools

    assert "inventory_place_supplier_order" not in {t.name for t in build_tools()}


def test_credentials_are_encrypted_and_never_returned(shop):
    out = shop.run("integrations.connect", {"key": "transcript_webhook", "values": {}})
    secret = out["generated"]["secret"]  # shown once, at connect time
    listed = json.dumps(shop.q("integrations.list"))
    history = json.dumps(shop.q("history.list", {"entity_type": "integration_credentials"}))
    assert secret not in listed and secret not in history
    with tenant_tx(shop.owner) as tx:
        stored = tx.execute(text("SELECT secret FROM integration_credentials WHERE key = 'transcript_webhook'")).scalar()
    assert stored and secret not in stored
    mechanic = shop.member("mechanic")
    with pytest.raises(Forbidden):
        run_query(mechanic, "integrations.list")


# ---- Call transcripts -------------------------------------------------------------

CALL = {"provider": "transcript_webhook", "external_id": "call-001", "from_number": "+1 (555) 201-7788", "to_number": "555-000-1000",
        "duration_s": 95, "consent_noted": True,
        "segments": [{"speaker": "Shop", "text": "Thanks for calling, how can I help?"},
                     {"speaker": "Customer", "text": "Hi, this is Laura. My Bronco Sport is making a grinding noise when I brake. Can I bring it in Tuesday morning?"}]}


@pytest.fixture
def fake_extractor():
    from shoppi.domain.communications import IntakeDraft, set_extractor

    seen = []

    def extract(text_):
        seen.append(text_)
        return IntakeDraft(is_service_request=True, summary="Laura reports brake grinding and wants Tuesday morning.",
                           customer_name="Laura", vehicle_make="Ford", vehicle_model="Bronco Sport",
                           concern="Grinding noise when braking", preferred_times="Tuesday morning")

    set_extractor(extract)
    yield seen
    set_extractor(None)


def test_transcript_is_matched_indexed_and_drafted_for_review(shop, other_shop, fake_extractor):
    laura = shop.run("customers.create", {"name": "Laura Lane", "phone": "555.201.7788"})
    other_shop.run("customers.create", {"name": "Someone Else", "phone": "555-201-7788"})  # same number, different shop
    result = shop.run("communications.ingest_transcript", CALL)
    assert result["customer_name"] == "Laura Lane" and result["duplicate"] is False

    # A retry of the same webhook does nothing twice.
    assert shop.run("communications.ingest_transcript", CALL)["duplicate"] is True
    assert len(shop.q("calls.list")) == 1 and len(fake_extractor) == 1

    # The draft waits in the review queue; no customer or work order was touched.
    queue = shop.q("intake.list")
    assert len(queue) == 1 and queue[0]["source"] == "call" and queue[0]["payload"]["concern"] == "Grinding noise when braking"
    assert shop.q("work_orders.list") == [] and shop.q("customers.get", {"id": laura["id"]})["notes"] is None
    assert shop.q("calls.get", {"id": result["id"]})["summary"].startswith("Laura reports")

    # Searchable by this shop (linked to the customer), invisible to the other one.
    hits = shop.q("documents.search", {"query": "grinding noise brake", "customer_id": laura["id"]})
    assert hits and hits[0]["kind"] == "call_transcript"
    assert other_shop.q("documents.search", {"query": "grinding noise brake"}) == []
    assert other_shop.q("calls.list") == []


def test_unknown_caller_stays_unassigned_until_staff_link_it(shop, fake_extractor):
    result = shop.run("communications.ingest_transcript", {**CALL, "external_id": "call-002", "from_number": "555-999-0000"})
    assert result["status"] == "unassigned" and [c["id"] for c in shop.q("calls.list", {"unassigned": True})] == [result["id"]]
    customer = shop.run("customers.create", {"name": "New Caller"})
    shop.run("calls.assign", {"id": result["id"], "customer_id": customer["id"]})
    assert shop.q("calls.list", {"unassigned": True}) == []
    assert shop.q("documents.search", {"query": "grinding", "customer_id": customer["id"]})


def test_mechanics_cannot_read_calls_unless_the_shop_allows_it(shop, fake_extractor):
    shop.run("communications.ingest_transcript", CALL)
    mechanic = shop.member("mechanic")
    with pytest.raises(Forbidden):
        run_query(mechanic, "calls.list")
    assert run_query(mechanic, "documents.search", {"query": "grinding noise brake"}) == []
    assert not [r for r in run_query(mechanic, "history.list") if r["entity_type"] in ("calls", "call_transcripts")]
    shop.run("shop.update", {"settings": {"mechanics_can_read_calls": True}})
    assert len(run_query(mechanic, "calls.list")) == 1
    assert run_query(mechanic, "documents.search", {"query": "grinding noise brake"})


def test_a_failing_model_never_loses_the_call(shop):
    from shoppi.domain.communications import set_extractor

    def broken(_):
        raise RuntimeError("model offline")

    set_extractor(broken)
    try:
        result = shop.run("communications.ingest_transcript", {**CALL, "external_id": "call-003"})
    finally:
        set_extractor(None)
    assert shop.q("calls.get", {"id": result["id"]})["segments"] and shop.q("intake.list") == []


def test_webhook_signature_and_pasted_text_parsing():
    from shoppi.integrations.registry import transcript_source
    from shoppi.integrations.transcripts import parse_plain_text, transcript_from_json

    source = transcript_source("transcript_webhook")
    body = json.dumps(CALL).encode()
    good = hmac.new(b"a-long-shared-secret", body, hashlib.sha256).hexdigest()
    creds = {"secret": "a-long-shared-secret"}
    assert source.verify_webhook({"x-shoppi-signature": good}, body, creds)
    assert source.verify_webhook({"x-shoppi-signature": "sha256=" + good}, body, creds)
    assert not source.verify_webhook({"x-shoppi-signature": good}, body + b" ", creds)
    assert not source.verify_webhook({}, body, creds) and not source.verify_webhook({"x-shoppi-signature": good}, body, {})
    assert source.parse(CALL).segments[1].speaker == "Customer"

    vtt = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:04.000\n<v Shop>Thanks for calling.</v>\n\n2\n00:00:04.500 --> 00:00:09.000\n<v Customer>My truck won't start.\nIt just clicks.</v>\n"
    segs = parse_plain_text(vtt)
    assert [(s.speaker, s.text) for s in segs] == [("Shop", "Thanks for calling."), ("Customer", "My truck won't start. It just clicks.")]
    assert transcript_from_json({"external_id": "x", "text": "Customer: hello there"}).segments[0].text == "hello there"


# ---- Google Calendar mirror -------------------------------------------------------


class FakeGoogle:
    """Stands in for the Google Calendar API client."""

    def __init__(self):
        self.store, self.log, self._n = {}, [], 0

    def events(self):
        return self

    def insert(self, calendarId, body):
        self._n += 1
        return self._op("insert", f"g{self._n}", body)

    def patch(self, calendarId, eventId, body):
        return self._op("patch", eventId, body)

    def delete(self, calendarId, eventId):
        return self._op("delete", eventId, None)

    def _op(self, kind, event_id, body):
        outer = self

        class Op:
            def execute(self_inner):
                outer.log.append(kind)
                if kind == "delete":
                    outer.store.pop(event_id, None)
                else:
                    outer.store[event_id] = body
                return {"id": event_id}

        return Op()


def test_google_mirror_creates_patches_and_deletes(shop):
    from shoppi.core.commands import _ctx
    from shoppi.integrations.google import GoogleCalendarMirror, to_google_event

    google, mirror = FakeGoogle(), GoogleCalendarMirror()
    ev = shop.run("events.create", {"title": "Vendor visit", "start": "2027-07-06T14:00:00", "end": "2027-07-06T15:00:00", "location": "Front office"})

    def sync():
        feed = shop.q("calendar.feed", {"start": "2027-07-01T00:00:00", "end": "2027-07-31T00:00:00", "kinds": ["event"]})
        with tenant_tx(shop.owner, command="test.sync") as tx:
            return mirror.sync(_ctx(tx, shop.owner), {}, {"calendar_id": "primary"}, feed, service=google)

    assert sync() == {"created": 1, "updated": 0, "deleted": 0}
    body = next(iter(google.store.values()))
    assert body["summary"] == "Vendor visit" and body["start"] == {"dateTime": "2027-07-06T14:00:00-04:00", "timeZone": "America/New_York"}
    assert sync() == {"created": 0, "updated": 0, "deleted": 0}  # unchanged: no API calls
    shop.run("events.update", {"id": ev["id"], "title": "Vendor visit (moved)", "start": "2027-07-07T14:00:00", "end": "2027-07-07T15:00:00"})
    assert sync() == {"created": 0, "updated": 1, "deleted": 0}
    shop.run("events.delete", {"id": ev["id"]})
    assert sync() == {"created": 0, "updated": 0, "deleted": 1} and google.store == {}
    all_day = to_google_event({"title": "Closed", "start": "2027-07-04T00:00:00", "end": "2027-07-05T00:00:00", "all_day": True}, TZ)
    assert all_day["start"] == {"date": "2027-07-04"}


# ---- Assistant --------------------------------------------------------------------


def run_agent(actor, script, message="hi", thread="t1"):
    from shoppi.agent.build import build_agent, thread_key

    model = ScriptedChatModel(script=script, seen=[])
    agent = build_agent(model)
    out = agent.invoke({"messages": [("user", message)]}, {"configurable": {"thread_id": thread_key(actor, thread)}}, context=actor)
    return out["messages"], model


def call(name, args, id_="1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id_}])


def test_assistant_reads_through_the_same_permission_checks(shop):
    shop.run("customers.create", {"name": "Laura Lane", "phone": "555-201-7788"})
    messages, model = run_agent(shop.owner, [call("customers_list", {"q": "laura"}), AIMessage(content="Found Laura.")])
    assert "Laura Lane" in messages[2].content
    system = model.seen[0][0].content
    assert "Test Shop" in system and "Olive Owner (owner)" in system and "America/New_York" in system

    mechanic = shop.member("mechanic")
    messages, _ = run_agent(mechanic, [call("calls_list", {}), AIMessage(content="No access.")])
    assert messages[2].content.startswith("Error: You don't have access")


def test_assistant_changes_are_proposals_the_user_confirms(shop):
    script = [call("events_create", {"title": "Tool vendor demo", "start": iso(MON.replace(hour=13)), "end": iso(MON.replace(hour=14))}),
              AIMessage(content="I've proposed the event; please confirm it.")]
    messages, _ = run_agent(shop.owner, script)
    result = json.loads(messages[2].content)
    assert result["status"] == "PROPOSED_NOT_APPLIED"
    window = {"start": iso(MON), "end": iso(MON + timedelta(days=1))}
    assert shop.q("calendar.feed", window) == []  # nothing happened yet

    pending = shop.q("change_requests.list")
    assert pending[0]["source"] == "agent" and pending[0]["can_decide"] and pending[0]["preview"][0]["after"]["title"] == "Tool vendor demo"
    decide_change_request(shop.owner, result["change_request_id"], True)
    assert shop.q("calendar.feed", window)[0]["title"] == "Tool vendor demo"


def test_assistant_proposal_from_a_mechanic_needs_a_manager(shop):
    mechanic = shop.member("mechanic")
    messages, _ = run_agent(mechanic, [call("customers_create", {"name": "Walk-in Wendy"}), AIMessage(content="Proposed.")])
    cr_id = json.loads(messages[2].content)["change_request_id"]
    with pytest.raises(Forbidden):
        decide_change_request(mechanic, cr_id, True)
    decide_change_request(shop.owner, cr_id, True)
    assert shop.q("customers.list")[0]["name"] == "Walk-in Wendy"


def test_assistant_tool_schemas_never_expose_shop_or_user_ids():
    from shoppi.agent.tools import build_tools

    for tool in build_tools():
        props = tool.tool_call_schema.model_json_schema().get("properties", {})
        assert "shop_id" not in props and "runtime" not in props, tool.name
        assert "requester_id" not in props and "actor" not in props, tool.name


def test_conversation_threads_are_keyed_per_shop_and_user(shop, other_shop):
    from shoppi.agent.build import thread_key

    assert thread_key(shop.owner, "t1") != thread_key(other_shop.owner, "t1")
    assert thread_key(shop.owner, "t1") != thread_key(shop.member("mechanic"), "t1")


# ---- Diagnosis as an intake option ------------------------------------------------

NEW = {"customer": {"name": "Dee Agnosis", "phone": "555-700-1000"}, "vehicle": {"year": 2019, "make": "Honda", "model": "CR-V"}}


def line_names(shop, result):
    return [line["description"] for line in shop.q("work_orders.get", {"id": result["work_order"]["id"]})["lines"]]


def test_diagnosis_can_be_chosen_as_a_service_in_intake(shop):
    diagnosis = next(s for s in shop.q("services.list") if s["is_diagnosis"])
    assert diagnosis["name"] == "Diagnosis" and diagnosis["minutes"] == 60 and diagnosis["active"]

    alone = shop.run("intake.staff_create", {**NEW, "concern": "Rattle over bumps", "diagnosis": True})
    assert line_names(shop, alone) == ["Diagnosis"]
    both = shop.run("intake.staff_create", {"customer_id": alone["customer_id"], "vehicle_id": alone["vehicle_id"], "concern": "Also due for oil",
                                            "diagnosis": True, "service_type_ids": [shop.ids["services"]["Oil & filter change"]]})
    assert line_names(shop, both) == ["Diagnosis", "Oil & filter change"]
    # Ticking it and also passing its id doesn't add it twice.
    twice = shop.run("intake.staff_create", {"customer_id": alone["customer_id"], "vehicle_id": alone["vehicle_id"], "concern": "x",
                                             "diagnosis": True, "service_type_ids": [diagnosis["id"]]})
    assert line_names(shop, twice) == ["Diagnosis"]
    # Not asked for and something else chosen: no diagnosis.
    plain = shop.run("intake.staff_create", {"customer_id": alone["customer_id"], "vehicle_id": alone["vehicle_id"], "concern": "Oil",
                                             "service_type_ids": [shop.ids["services"]["Oil & filter change"]]})
    assert line_names(shop, plain) == ["Oil & filter change"]


def test_intake_with_only_a_concern_gets_a_schedulable_diagnosis(shop):
    gus = shop.employee("Gus General", [])
    shop.shifts(gus, MON)
    result = shop.run("intake.staff_create", {**NEW, "concern": "Won't start when cold"})
    assert line_names(shop, result) == ["Diagnosis"]
    found = shop.q("schedule.find_slots", {"work_order_id": result["work_order"]["id"], "earliest": iso(MON), "days": 3})
    assert found["steps"][0]["minutes"] == 60 and found["candidates"]


def test_diagnosis_also_works_when_accepting_a_customer_submission(shop):
    link = shop.run("intake_links.create", {"kind": "kiosk"})
    execute(public_actor(shop, link["url"]), "intake.submit", FORM)
    accepted = shop.run("intake.accept", {"id": shop.q("intake.list")[0]["id"], "customer": FORM["customer"],
                                          "vehicle": {"year": 2023, "make": "Ford", "model": "Bronco Sport"}, "concern": FORM["concern"], "diagnosis": True})
    assert line_names(shop, accepted) == ["Diagnosis"]


def test_shop_without_a_diagnosis_service_gets_one_on_first_use_and_can_change_it():
    from tests.conftest import ShopFixture

    bare = ShopFixture(starter=False)  # e.g. a shop created before this feature existed
    assert bare.q("services.list") == []
    result = bare.run("intake.staff_create", {**NEW, "concern": "Noise", "diagnosis": True})
    assert line_names(bare, result) == ["Diagnosis"]
    bare.run("intake.staff_create", {"customer_id": result["customer_id"], "vehicle_id": result["vehicle_id"], "concern": "Again", "diagnosis": True})
    services = bare.q("services.list")
    assert len(services) == 1 and services[0]["is_diagnosis"]  # created once, reused

    # The owner makes a different service the Diagnosis option: only one holds the flag.
    custom = bare.run("services.save", {"name": "Full inspection & diagnosis", "price": 149, "is_diagnosis": True, "steps": [{"name": "Inspect", "minutes": 90}]})
    flags = {s["name"]: s["is_diagnosis"] for s in bare.q("services.list")}
    assert flags == {"Diagnosis": False, "Full inspection & diagnosis": True}
    again = bare.run("intake.staff_create", {"customer_id": result["customer_id"], "vehicle_id": result["vehicle_id"], "concern": "Third", "diagnosis": True})
    assert line_names(bare, again) == ["Full inspection & diagnosis"]
    # Editing it later without mentioning the flag leaves the flag alone.
    bare.run("services.save", {"id": custom["id"], "name": "Full inspection & diagnosis", "price": 159, "steps": [{"name": "Inspect", "minutes": 90}]})
    assert next(s for s in bare.q("services.list") if s["id"] == custom["id"])["is_diagnosis"] is True


# ---- Deleting work orders and customers -------------------------------------------


def booked_oil_change(shop, mechanic):
    """A booked oil change that reserves one filter. Returns (work order, part id)."""
    filt = shop.run("parts.create", {"sku": f"OF-{len(shop.q('parts.list'))}", "name": "Oil filter", "on_hand": 3})
    svc = next(s for s in shop.q("services.list") if s["name"] == "Oil & filter change")
    shop.run("services.save", {"id": svc["id"], "name": svc["name"], "steps": [{"name": "Oil & filter change", "minutes": 30}],
                               "parts": [{"part_id": filt["id"], "qty": 1}]})
    wo = shop.work_order(["Oil & filter change"])
    cand = shop.q("schedule.find_slots", {"work_order_id": wo["id"], "earliest": iso(MON), "days": 3})["candidates"][0]
    shop.run("schedule.book", {"work_order_id": wo["id"], "steps": [{k: s[k] for k in ("line_id", "step_id", "employee_id", "bay_id", "start", "end")} for s in cand["steps"]]})
    return wo, filt["id"], cand


def test_owner_or_advisor_can_delete_a_work_order_and_it_frees_what_it_held(shop):
    from shoppi.core.commands import NotFound

    gus = shop.employee("Gus General", [])
    shop.shifts(gus, MON)
    wo, part_id, cand = booked_oil_change(shop, gus)
    assert shop.q("parts.get", {"id": part_id})["reserved"] == 1
    window = {"start": iso(MON), "end": iso(MON + timedelta(days=3)), "kinds": ["job"]}
    assert len(shop.q("calendar.feed", window)) == 1

    advisor = shop.member("advisor")
    assert execute(advisor, "work_orders.delete", {"id": wo["id"]})["result"] == {"deleted": True, "number": wo["number"]}
    with pytest.raises(NotFound):
        run_query(shop.owner, "work_orders.get", {"id": wo["id"]})
    assert shop.q("calendar.feed", window) == []  # the mechanic and bay are free again
    assert shop.q("parts.get", {"id": part_id})["reserved"] == 0
    again = shop.work_order(["Oil & filter change"])
    slots = shop.q("schedule.find_slots", {"work_order_id": again["id"], "earliest": iso(MON), "days": 3, "limit": 20})["candidates"]
    assert cand["start"] in {c["start"] for c in slots}

    # The deletion is in the history, with who did it and what the record was.
    rows = shop.q("history.list", {"entity_type": "work_orders", "entity_id": wo["id"]})
    assert rows[0]["op"] == "delete" and rows[0]["actor_name"] == advisor.name and rows[0]["before"]["number"] == wo["number"]
    assert shop.q("history.list", {"entity_type": "job_assignments"})[0]["op"] == "delete"


def test_mechanics_cannot_delete_even_with_edit_access_or_through_a_request(shop):
    mechanic = shop.member("mechanic")
    wo = shop.work_order(["Oil & filter change"])
    cid = shop.q("work_orders.get", {"id": wo["id"]})["customer_id"]
    for vertical in ("work_orders", "customers"):
        shop.run("access.grant", {"user_id": str(mechanic.user_id), "vertical": vertical})
    for name, args in (("work_orders.delete", {"id": wo["id"]}), ("customers.delete", {"id": cid})):
        with pytest.raises(Forbidden):
            execute(mechanic, name, args)
        with pytest.raises(Forbidden):  # nor by asking the assistant
            execute(mechanic.as_agent(), name, args)
    assert shop.q("change_requests.list") == []  # it did not become an approvable request either
    from shoppi.agent.tools import build_tools

    assert not {"work_orders_delete", "customers_delete"} & {t.name for t in build_tools()}


def test_work_in_progress_blocks_deletion(shop):
    gus = shop.employee("Gus General", [])
    shop.shifts(gus, MON)
    wo, _, _ = booked_oil_change(shop, gus)
    detail = shop.q("work_orders.get", {"id": wo["id"]})
    shop.run("jobs.progress", {"id": detail["jobs"][0]["id"], "action": "start"})
    for name, target in (("work_orders.delete", wo["id"]), ("customers.delete", detail["customer_id"])):
        with pytest.raises(Conflict, match="under way"):
            execute(shop.owner, name, {"id": target})
    assert shop.q("customers.delete_preview", {"id": detail["customer_id"]})["blockers"]
    shop.run("jobs.progress", {"id": detail["jobs"][0]["id"], "action": "finish"})
    assert shop.run("work_orders.delete", {"id": wo["id"]})["deleted"]


def test_deleting_a_completed_work_order_keeps_the_stock_ledger(shop):
    gus = shop.employee("Gus General", [])
    shop.shifts(gus, MON)
    wo, part_id, _ = booked_oil_change(shop, gus)
    shop.run("work_orders.set_status", {"id": wo["id"], "status": "completed"})
    shop.run("work_orders.delete", {"id": wo["id"]})
    part = shop.q("parts.get", {"id": part_id})
    assert part["on_hand"] == 2  # the filter really was used
    use = next(m for m in part["movements"] if m["reason"] == "use")
    assert use["delta"] == -1 and use["work_order_id"] is None and f"#{wo['number']}" in use["note"]


def test_deleting_a_customer_removes_their_vehicles_work_orders_and_calls(shop, other_shop, fake_extractor):
    from shoppi.core.commands import NotFound

    gus = shop.employee("Gus General", [])
    shop.shifts(gus, MON)
    wo, part_id, _ = booked_oil_change(shop, gus)
    detail = shop.q("work_orders.get", {"id": wo["id"]})
    cid, vid = detail["customer_id"], detail["vehicle_id"]
    shop.run("customers.update", {"id": cid, "phone": "555-201-7788"})
    call = shop.run("communications.ingest_transcript", CALL)
    doc = shop.run("documents.add", {"title": "Fleet agreement", "text": "Agreed fleet discount terms for this customer.", "customer_id": cid})
    keep_c, keep_v = shop.customer_vehicle()
    theirs, _ = other_shop.customer_vehicle()

    preview = shop.q("customers.delete_preview", {"id": cid})
    assert (len(preview["vehicles"]), len(preview["work_orders"]), preview["open_work_orders"], preview["calls"], preview["blockers"]) == (1, 1, 1, 1, [])

    result = shop.run("customers.delete", {"id": cid})
    assert (result["vehicles"], result["work_orders"], result["calls"]) == (1, 1, 1)
    for name, target in (("customers.get", cid), ("vehicles.get", vid), ("work_orders.get", wo["id"]), ("calls.get", call["id"])):
        with pytest.raises(NotFound):
            run_query(shop.owner, name, {"id": target})
    assert [c["id"] for c in shop.q("customers.list")] == [keep_c]
    assert shop.q("parts.get", {"id": part_id})["reserved"] == 0
    hits = shop.q("documents.search", {"query": "grinding noise brake"})
    assert all(h["kind"] != "call_transcript" for h in hits)  # the call transcript is gone from search
    assert [d["id"] for d in shop.q("documents.list")] == [doc["id"]]  # the filed document is kept, unfiled
    pending = shop.q("intake.list")  # the draft from the call stays in the queue, no longer pointing anywhere
    assert len(pending) == 1 and pending[0]["customer_id"] is None and pending[0]["call_id"] is None
    assert other_shop.q("customers.get", {"id": theirs})["id"] == theirs
    with pytest.raises(NotFound):  # and one shop can't delete another's customer
        execute(shop.owner, "customers.delete", {"id": theirs})

    history = shop.q("history.list", {"entity_type": "customers", "entity_id": cid})
    assert history[0]["op"] == "delete" and history[0]["actor_name"] == "Olive Owner"
    assert shop.q("history.list", {"entity_type": "vehicles", "entity_id": vid})[0]["op"] == "delete"
