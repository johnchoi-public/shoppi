"""Create a demo shop with realistic data, for trying the app out.

    python scripts/seed_demo.py

Creates "Northside Auto & Truck" with an owner and a mechanic login (below),
bays, a service catalog, a team with certifications and shifts, customers and
vehicles, parts, a few booked jobs, an intake kiosk link, a document and a
phone-call transcript. Safe to run once; it stops if the demo owner exists.

These are throwaway local credentials. Override the password with
SHOPPI_DEMO_PASSWORD. Never run this against a real deployment.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import shoppi.domain  # noqa: F401
from shoppi.auth import service as auth
from shoppi.core.commands import AppError, execute, run_query
from shoppi.starter import install_starter

OWNER_EMAIL = "owner@northside.test"
MECHANIC_EMAIL = "sam@northside.test"
PASSWORD = os.environ.get("SHOPPI_DEMO_PASSWORD", "northside-demo-2026")
TZ = ZoneInfo("America/Los_Angeles")


def main() -> None:
    try:
        token, _ = auth.signup(shop_name="Northside Auto & Truck", name="Olivia Ortiz", email=OWNER_EMAIL, password=PASSWORD, timezone_name=TZ.key)
    except AppError as e:
        sys.exit(f"Demo data not created: {e.message}")
    owner = auth.resolve_session(token)

    def run(name: str, args: dict | None = None, actor=owner):
        result = execute(actor, name, args or {})
        assert result["status"] == "applied", result
        return result["result"]

    run("shop.update", {"phone": "(555) 010-2200", "address": "1200 Industrial Pkwy",
                        "hours": {"0": ["07:30", "17:30"], "1": ["07:30", "17:30"], "2": ["07:30", "17:30"], "3": ["07:30", "17:30"],
                                  "4": ["07:30", "17:30"], "5": ["08:00", "12:00"], "6": None}})
    ids = install_starter(owner)
    certs, services, bays = ids["certs"], ids["services"], ids["bays"]

    # The alignment bay opens later than the shop; the truck bay is shut on Saturdays.
    run("bays.save", {"id": bays["Alignment bay"], "name": "Alignment bay", "kind": "alignment", "capabilities": ["lift", "alignment", "inspection"],
                      "max_gvwr_class": 3, "max_weight_lb": 12000, "sort_order": 2,
                      "hours": [{"weekday": d, "opens": "09:00", "closes": "16:00"} for d in range(5)] + [{"weekday": 5, "closed": True}, {"weekday": 6, "closed": True}]})
    run("bays.add_equipment", {"bay_id": bays["Truck bay"], "name": "Four-post heavy lift, 80,000 lb", "next_service_due": (datetime.now(TZ) + timedelta(days=40)).date().isoformat()})

    # -- Team --
    today = datetime.now(TZ).date()
    soon = (today + timedelta(days=21)).isoformat()
    later = (today + timedelta(days=500)).isoformat()
    team = [
        ("Sam Rivera", "tech", 5, True, [("ASE-A1", later), ("ASE-A5", later), ("ASE-A4", later), ("ASE-A8", later), ("ASE-L1", later), ("STATE-INSP", later), ("EPA-609", None), ("ASE-A7", later)]),
        ("Priya Shah", "tech", 4, True, [("ASE-A6", later), ("ASE-A8", later), ("ASE-L3", later), ("EV-HV", soon), ("ASE-A5", later)]),
        ("Marcus Bell", "tech", 4, True, [("ASE-T2", later), ("ASE-T4", later), ("ASE-T8", later), ("DOT-INSP", None), ("ASE-A9", later), ("CDL-A", later), ("ASE-T5", later)]),
        ("Dana Kim", "tech", 3, False, [("ASE-A4", later), ("ASE-A5", soon), ("ASE-G1", later)]),
        ("Jordan Lee", "apprentice", 1, False, []),
    ]
    employees: dict[str, str] = {}
    monday = today - timedelta(days=today.weekday())
    for name, kind, level, supervise, held in team:
        emp = run("employees.create", {"name": name, "kind": kind, "skill_level": level, "can_supervise": supervise})
        employees[name] = emp["id"]
        for code, expires in held:
            run("certs.add", {"employee_id": emp["id"], "cert_type_id": certs[code], "expires_on": expires, "issued_on": (today - timedelta(days=400)).isoformat()})
        run("shifts.create", {"employee_id": emp["id"], "start": f"{monday}T07:30:00", "end": f"{monday}T16:30:00",
                              "repeat_weekdays": [0, 1, 2, 3, 4], "repeat_until": (monday + timedelta(days=27)).isoformat()})
    run("time_off.create", {"employee_id": employees["Dana Kim"], "start": f"{monday + timedelta(days=9)}T00:00:00",
                            "end": f"{monday + timedelta(days=10)}T00:00:00", "reason": "Dentist"})

    # A login for a mechanic, linked to Sam's employee record.
    link = run("members.invite", {"email": MECHANIC_EMAIL, "role": "mechanic", "employee_id": employees["Sam Rivera"]})["link"]
    auth.accept_invite(link.rsplit("/", 1)[1], "Sam Rivera", PASSWORD)

    # -- Parts --
    supplier = run("suppliers.save", {"name": "Tri-County Parts", "phone": "(555) 010-7700", "account_number": "NS-4471"})
    parts = {}
    for sku, name, brand, on_hand, point, qty, cost, price in [
        ("OF-5W30", "Oil filter (light duty)", "WIX", 14, 6, 24, 4.75, 11.99),
        ("OIL-5W30-QT", "5W-30 synthetic oil, quart", "Valvoline", 60, 24, 72, 5.10, 10.49),
        ("BP-F150-F", "Front brake pads, F-150", "Bosch", 2, 2, 6, 41.20, 89.00),
        ("ROT-F150-F", "Front rotor, F-150", "Raybestos", 0, 2, 4, 52.40, 119.00),
        ("FF-ISX15", "Fuel filter, Cummins ISX15", "Fleetguard", 3, 2, 6, 33.50, 68.00),
        ("R134A-12", "R-134a refrigerant, 12 oz", "Chemours", 9, 6, 24, 6.80, 19.00),
    ]:
        parts[sku] = run("parts.create", {"sku": sku, "name": name, "brand": brand, "on_hand": on_hand, "reorder_point": point, "reorder_qty": qty,
                                          "unit_cost": cost, "unit_price": price, "supplier_id": supplier["id"]})["id"]
    catalog = {s["name"]: s for s in run_query(owner, "services.list")}

    def set_parts(service: str, needs: list[tuple[str, int]]) -> None:
        svc = catalog[service]
        run("services.save", {
            "id": svc["id"], "name": svc["name"], "price": svc["price"],
            "steps": [{k: st[k] for k in ("name", "minutes", "hands_on", "needs_bay", "bay_capability", "holds_bay", "same_mechanic_as_prev")}
                      | {"requirements": [{k: r[k] for k in ("cert_type_id", "group_no", "applies_powertrain", "applies_min_class")} for r in st["requirements"]]}
                      for st in svc["steps"]],
            "parts": [{"part_id": parts[sku], "qty": qty} for sku, qty in needs]})

    set_parts("Oil & filter change", [("OF-5W30", 1), ("OIL-5W30-QT", 6)])
    set_parts("Brake service", [("BP-F150-F", 1), ("ROT-F150-F", 2)])
    set_parts("Diesel preventive maintenance", [("FF-ISX15", 1)])
    # Rotors are out of stock but on order: brake jobs can't be promised before they arrive.
    po = run("purchase_orders.create", {"supplier_id": supplier["id"], "lines": [{"part_id": parts["ROT-F150-F"], "qty": 4}],
                                        "eta": f"{_next_workday(today, 2)}T10:00:00"})
    run("purchase_orders.mark_ordered", {"id": po["id"]})
    run("integrations.set_enabled", {"key": "demo_catalog", "enabled": True})

    # -- Customers, vehicles, work orders --
    def intake(customer: dict, vehicle: dict, concern: str, service_names: list[str]) -> dict:
        return run("intake.staff_create", {"customer": customer, "vehicle": vehicle, "concern": concern,
                                           "service_type_ids": [services[s] for s in service_names]})["work_order"]

    def book(work_order: dict, days_ahead: int = 0) -> None:
        earliest = datetime.combine(_next_workday(today, days_ahead), datetime.min.time())
        found = run_query(owner, "schedule.find_slots", {"work_order_id": work_order["id"], "days": 14, "earliest": earliest.isoformat()})
        if not found["candidates"]:
            print(f"  (WO #{work_order['number']} left unscheduled: {found['blockers'][0]['message']})")
            return
        steps = [{k: s[k] for k in ("line_id", "step_id", "employee_id", "bay_id", "supervisor_id", "start", "end")} for s in found["candidates"][0]["steps"]]
        run("schedule.book", {"work_order_id": work_order["id"], "steps": steps})

    book(intake({"name": "Laura Chen", "phone": "(555) 201-7788", "email": "laura.chen@example.com"},
                {"year": 2023, "make": "Ford", "model": "Bronco Sport", "plate": "7KLM204", "mileage": 73418, "gvwr_class": 1},
                "Due for an oil change; also wants tires rotated.", ["Oil & filter change", "Tire rotation & balance"]), 1)
    book(intake({"name": "Hector Alvarez", "phone": "(555) 318-4402"},
                {"year": 2021, "make": "Ford", "model": "F-150", "plate": "TRK9921", "mileage": 58210, "gvwr_class": 2, "engine": "3.5L V6"},
                "Grinding from the front when braking.", ["Brake service"]), 1)
    book(intake({"name": "Great Lakes Freight", "phone": "(555) 440-9000", "email": "fleet@greatlakesfreight.example"},
                {"year": 2019, "make": "Freightliner", "model": "Cascadia", "plate": "P88-4471", "mileage": 412000, "gvwr_class": 8, "weight_lb": 34000,
                 "length_ft": 28, "powertrain": "diesel", "engine": "14.8L DD15"},
                "Scheduled PM service and DOT annual inspection.", ["Diesel preventive maintenance", "DOT annual inspection"]), 2)
    book(intake({"name": "Aisha Bello", "phone": "(555) 677-1203", "email": "aisha.bello@example.com"},
                {"year": 2022, "make": "Tesla", "model": "Model 3", "plate": "EV22AB", "mileage": 31000, "gvwr_class": 1, "powertrain": "ev"},
                "Battery warning light came on after a cold night.", ["High-voltage battery service"]), 2)
    intake({"name": "Tom Novak", "phone": "(555) 902-3345"},
           {"year": 2016, "make": "Chevrolet", "model": "Silverado 1500", "plate": "NVK-553", "mileage": 148300, "gvwr_class": 2, "engine": "5.3L V8"},
           "Engine knocking, burning oil. Wants a quote for a rebuild.", ["Engine rebuild"])

    # -- Calendar, intake, documents, calls --
    run("events.create", {"title": "Tool truck visit", "kind": "vendor", "start": f"{_next_workday(today, 1)}T12:00:00", "end": f"{_next_workday(today, 1)}T12:45:00",
                          "location": "Front lot", "repeat": "weekly", "repeat_until": (today + timedelta(days=28)).isoformat()})
    run("events.create", {"title": "Monthly safety meeting", "kind": "meeting", "start": f"{_next_workday(today, 3)}T07:30:00", "end": f"{_next_workday(today, 3)}T08:00:00"})
    kiosk = run("intake_links.create", {"kind": "kiosk", "label": "Front desk kiosk"})

    try:
        run("documents.add", {"title": "Warranty policy", "kind": "note", "text": (
            "Northside Auto & Truck warranty policy.\n\nBrake pads and rotors installed by us are covered for 24 months or 24,000 miles, whichever comes first. "
            "Labor on brake work is covered for 12 months.\n\nEngine rebuilds carry a 36 month / 36,000 mile warranty on parts and labor, provided oil changes "
            "are done here at the recommended interval.\n\nHigh-voltage battery work on electric vehicles is covered for 12 months. Customer-supplied parts are not covered.\n\n"
            "Fleet customers: DOT annual inspections include one free re-inspection within 15 days if the vehicle fails.")})
        run("documents.add", {"title": "Shop policies", "kind": "note", "text": (
            "Loaner vehicles are available for jobs expected to take more than one full day; the customer must show proof of insurance.\n\n"
            "Vehicles left more than 5 business days after completion are charged a storage fee of $25 per day.\n\n"
            "Diagnostic fees are waived if the customer approves the recommended repair. Apprentices must be supervised on all brake and steering work.")})
    except AssertionError as e:  # e.g. the embedding model isn't running
        print(f"  (documents skipped: {e})")

    from shoppi.domain.communications import IntakeDraft, set_extractor

    # A fixed draft keeps the demo independent of whichever language model is running.
    set_extractor(lambda _text: IntakeDraft(
        is_service_request=True, summary="Laura's Bronco Sport pulls to the right after hitting a pothole; she wants an alignment check on Thursday morning.",
        customer_name="Laura Chen", vehicle_year=2023, vehicle_make="Ford", vehicle_model="Bronco Sport",
        concern="Pulls to the right after hitting a pothole; wants the alignment checked.", preferred_times="Thursday morning"))
    try:
        run("communications.ingest_transcript", {
            "provider": "manual", "external_id": "demo-call-1", "from_number": "(555) 201-7788", "duration_s": 74, "consent_noted": True,
            "segments": [
                {"speaker": "Shop", "text": "Northside Auto and Truck, this is Olivia. This call may be recorded. How can I help?"},
                {"speaker": "Customer", "text": "Hi, it's Laura Chen. I hit a pothole yesterday and now my Bronco Sport pulls to the right."},
                {"speaker": "Shop", "text": "Sorry to hear that. We can check the alignment. When works for you?"},
                {"speaker": "Customer", "text": "Thursday morning would be best, I can drop it off before work."},
                {"speaker": "Shop", "text": "I'll get that set up and text you to confirm."}]})
    except AssertionError as e:
        print(f"  (call transcript skipped: {e})")
    finally:
        set_extractor(None)

    print("Demo shop created: Northside Auto & Truck")
    print(f"  Owner login:    {OWNER_EMAIL}")
    print(f"  Mechanic login: {MECHANIC_EMAIL}")
    print("  Password: the value of SHOPPI_DEMO_PASSWORD, or the default at the top of scripts/seed_demo.py")
    print(f"  Customer intake kiosk link: {kiosk['url']}")


def _next_workday(start, n: int):
    """The date n working days (Mon-Fri) after `start` (0 = the next working day on or after it)."""
    day = start
    while day.weekday() >= 5:
        day += timedelta(days=1)
    for _ in range(n):
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
    return day


if __name__ == "__main__":
    main()
