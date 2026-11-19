"""The certification- and bay-aware scheduler, exercised through the same
commands the UI and the assistant use."""

from datetime import timedelta

import pytest

from shoppi.core.commands import Conflict, Forbidden, Invalid, execute
from tests.conftest import next_weekday

MON = next_weekday(0)


def iso(dt):
    return dt.replace(tzinfo=None).isoformat(timespec="seconds")


def find(shop, wo, **kw):
    return shop.q("schedule.find_slots", {"work_order_id": wo["id"], "earliest": iso(MON), "days": 5, **kw})


def book(shop, wo, candidate, **kw):
    steps = [{k: s[k] for k in ("line_id", "step_id", "employee_id", "bay_id", "supervisor_id", "start", "end")} for s in candidate["steps"]]
    return execute(kw.pop("actor", shop.owner), "schedule.book", {"work_order_id": wo["id"], "steps": steps, **kw})


def codes(result):
    return [b["code"] for b in result["blockers"]]


def test_only_certified_mechanics_are_offered(shop):
    brakes = shop.employee("Bea Brakes", ["ASE-A5"])
    general = shop.employee("Gus General", [])
    for e in (brakes, general):
        shop.shifts(e, MON)
    wo = shop.work_order(["Brake service"])
    result = find(shop, wo)
    assert result["candidates"], result["blockers"]
    assert {c["steps"][0]["employee_id"] for c in result["candidates"]} == {brakes}
    assert result["steps"][0]["certs"] == [["ASE A5 Brakes", "ASE T4 Brakes (Truck)"]]
    # An oil change needs no certification: either mechanic will do.
    oil = find(shop, shop.work_order(["Oil & filter change"]), limit=20)
    assert {c["steps"][0]["employee_id"] for c in oil["candidates"]} == {brakes, general}


def test_certification_must_be_valid_on_the_day_of_the_job(shop):
    expires = (MON + timedelta(days=1)).date().isoformat()  # valid Monday and Tuesday only
    bea = shop.employee("Bea Brakes", ["ASE-A5"], expires=expires)
    shop.shifts(bea, MON)
    result = find(shop, shop.work_order(["Brake service"]), limit=20)
    days = {c["start"][:10] for c in result["candidates"]}
    assert days and days <= {MON.date().isoformat(), expires}

    # Search only after it has lapsed: nothing fits, and the reason names the expiry.
    later = shop.q("schedule.find_slots", {"work_order_id": shop.work_order(["Brake service"])["id"],
                                             "earliest": iso(MON + timedelta(days=2)), "days": 3})
    assert not later["candidates"] and "CERT_EXPIRES_BEFORE" in codes(later)


def test_legally_required_certification_cannot_be_overridden(shop):
    tech = shop.employee("Lee L3", ["ASE-L3"])  # hybrid cert but no high-voltage qualification
    shop.shifts(tech, MON)
    wo = shop.work_order(["High-voltage battery service"], powertrain="ev", make="Tesla", model="Model 3")
    result = find(shop, wo)
    assert not result["candidates"] and "NO_CERTIFIED_MECHANIC" in codes(result)
    assert "CERT_OVERRIDE" not in [r["code"] for r in result["relaxations"]]

    step = shop.q("services.list")
    hv = next(s for s in step if s["name"] == "High-voltage battery service")["steps"][0]
    line = shop.q("work_orders.get", {"id": wo["id"]})["lines"][0]
    plan = [{"line_id": line["id"], "step_id": hv["id"], "employee_id": tech, "bay_id": shop.ids["bays"]["Bay 2"],
             "start": iso(MON.replace(hour=9))}]
    with pytest.raises(Invalid) as e:
        execute(shop.owner, "schedule.book", {"work_order_id": wo["id"], "steps": plan, "override_reason": "customer is waiting"})
    assert e.value.extra["can_override"] is False and "legally required" in e.value.message


def test_manager_can_override_a_non_legal_requirement_with_a_reason(shop):
    gus = shop.employee("Gus General", [])
    shop.shifts(gus, MON)
    wo = shop.work_order(["Brake service"])
    result = find(shop, wo)
    assert not result["candidates"] and "NO_CERTIFIED_MECHANIC" in codes(result)
    assert "CERT_OVERRIDE" in [r["code"] for r in result["relaxations"]]

    brake_step = next(s for s in shop.q("services.list") if s["name"] == "Brake service")["steps"][0]
    line = shop.q("work_orders.get", {"id": wo["id"]})["lines"][0]
    plan = {"work_order_id": wo["id"], "steps": [{"line_id": line["id"], "step_id": brake_step["id"], "employee_id": gus,
                                                   "bay_id": shop.ids["bays"]["Bay 1"], "start": iso(MON.replace(hour=9))}]}
    with pytest.raises(Invalid) as e:
        execute(shop.owner, "schedule.book", plan)
    assert e.value.extra["can_override"] is True
    booked = execute(shop.owner, "schedule.book", {**plan, "override_reason": "Supervised by the owner"})
    assert booked["status"] == "applied"
    job = shop.q("work_orders.get", {"id": wo["id"]})["jobs"][0]
    assert job["override_reason"] == "Supervised by the owner"


def test_heavy_truck_only_fits_the_rated_bay(shop):
    tech = shop.employee("Tara Truck", ["ASE-T8", "ASE-T2"])
    shop.shifts(tech, MON)
    wo = shop.work_order(["Diesel preventive maintenance"], gvwr_class=8, weight_lb=33000, powertrain="diesel", make="Freightliner", model="Cascadia")
    result = find(shop, wo)
    assert {c["steps"][0]["bay_name"] for c in result["candidates"]} == {"Truck bay"}

    shop.run("bays.save", {"id": shop.ids["bays"]["Truck bay"], "name": "Truck bay", "active": False, "max_gvwr_class": 8})
    result = find(shop, wo)
    assert not result["candidates"] and "NO_BAY_FOR_CLASS" in codes(result)


def test_light_vehicle_gets_the_smallest_bay_that_fits(shop):
    tech = shop.employee("Gus General", [])
    shop.shifts(tech, MON)
    result = find(shop, shop.work_order(["Oil & filter change"]), limit=10)
    assert result["candidates"][0]["steps"][0]["bay_name"] != "Truck bay"
    assert "Right-sized bay" in result["candidates"][0]["reasons"]


def test_bay_keeps_its_own_hours(shop):
    tech = shop.employee("Al Align", ["ASE-A4"])
    shop.shifts(tech, MON)
    # The alignment bay only opens 1pm-5pm on Mondays and is shut on Tuesdays.
    shop.run("bays.save", {"id": shop.ids["bays"]["Alignment bay"], "name": "Alignment bay", "capabilities": ["lift", "alignment", "inspection"],
                           "hours": [{"weekday": 0, "opens": "13:00", "closes": "17:00"}, {"weekday": 1, "closed": True}]})
    result = find(shop, shop.work_order(["Wheel alignment"]), limit=20)
    monday = [c for c in result["candidates"] if c["start"][:10] == MON.date().isoformat()]
    assert monday and all(c["start"][11:16] >= "13:00" for c in monday)
    tuesday = (MON + timedelta(days=1)).date().isoformat()
    assert not [c for c in result["candidates"] if c["start"][:10] == tuesday]


def test_closing_a_bay_flags_jobs_already_booked_there(shop):
    tech = shop.employee("Al Align", ["ASE-A4"])
    shop.shifts(tech, MON)
    wo = shop.work_order(["Wheel alignment"])
    cand = find(shop, wo)["candidates"][0]
    assert book(shop, wo, cand)["status"] == "applied"

    day = cand["start"][:10]
    closure = shop.run("bays.close", {"bay_id": shop.ids["bays"]["Alignment bay"], "start": f"{day}T00:00:00",
                                      "end": f"{day}T23:59:00", "reason": "Rack calibration"})
    assert closure["jobs_needing_reassignment"] == 1
    summary = shop.q("work_orders.get", {"id": wo["id"]})
    assert summary["needs_reassignment"] == 1 and "closed" in summary["jobs"][0]["problem"]
    # While it is closed nothing new is offered in that bay on that day.
    again = find(shop, wo, limit=20)
    assert all(c["start"][:10] != day for c in again["candidates"])
    assert again["candidates"], "other days should still be offered"


def test_multi_step_job_holds_the_bay_between_steps(shop):
    tech = shop.employee("Erin Engine", ["ASE-A1"])
    other = shop.employee("Gus General", [])
    for e in (tech, other):
        shop.shifts(e, MON)
    wo = shop.work_order(["Engine rebuild"])
    cand = find(shop, wo)["candidates"][0]
    labels = [s["label"] for s in cand["steps"]]
    assert len(labels) == 3 and "Teardown" in labels[0] and "Reassembly" in labels[2]
    assert cand["steps"][0]["employee_id"] == cand["steps"][2]["employee_id"] == tech  # same mechanic finishes it
    assert cand["steps"][1]["employee_id"] is None  # machine-shop wait needs nobody
    bays = {s["bay_id"] for s in cand["steps"]}
    assert len(bays) == 1  # the car never leaves the lift
    assert book(shop, wo, cand)["status"] == "applied"

    jobs = shop.q("work_orders.get", {"id": wo["id"]})["jobs"]
    holds = [j for j in jobs if j["is_hold"]]
    assert holds, "the overnight gap between steps must be held"
    # Nobody else can be booked into that bay during the hold.
    bay = cand["steps"][0]["bay_id"]
    oil = shop.work_order(["Oil & filter change"])
    line = shop.q("work_orders.get", {"id": oil["id"]})["lines"][0]
    oil_step = next(s for s in shop.q("services.list") if s["name"] == "Oil & filter change")["steps"][0]
    with pytest.raises(Invalid, match="already booked"):
        execute(shop.owner, "schedule.book", {"work_order_id": oil["id"], "steps": [{
            "line_id": line["id"], "step_id": oil_step["id"], "employee_id": other, "bay_id": bay, "start": cand["steps"][1]["start"]}]})


def test_parts_delay_the_earliest_slot(shop):
    tech = shop.employee("Bea Brakes", ["ASE-A5"])
    shop.shifts(tech, MON)
    pads = shop.run("parts.create", {"sku": "PAD-1", "name": "Brake pads", "on_hand": 0})
    svc = next(s for s in shop.q("services.list") if s["name"] == "Brake service")
    shop.run("services.save", {"id": svc["id"], "name": svc["name"], "price": svc["price"], "parts": [{"part_id": pads["id"], "qty": 1}],
                               "steps": [{"name": "Brake service", "minutes": 120,
                                          "requirements": [{"cert_type_id": shop.ids["certs"]["ASE-A5"]}]}]})
    wo = shop.work_order(["Brake service"])

    result = find(shop, wo)
    assert not result["candidates"] and codes(result) == ["PARTS_NOT_AVAILABLE"]
    assert "PARTS" in [r["code"] for r in result["relaxations"]]

    eta = MON + timedelta(days=2, hours=10)  # Wednesday 10:00
    po = shop.run("purchase_orders.create", {"lines": [{"part_id": pads["id"], "qty": 4}], "eta": iso(eta)})
    shop.run("purchase_orders.mark_ordered", {"id": po["id"]})
    result = find(shop, wo)
    assert result["candidates"] and min(c["start"] for c in result["candidates"]) >= iso(eta)
    assert result["parts"]["lines"][0]["status"] == "on_order"

    shop.run("purchase_orders.receive", {"id": po["id"]})
    result = find(shop, wo)
    assert min(c["start"] for c in result["candidates"])[:10] == MON.date().isoformat()
    # Booking reserves the part, so the next job can't promise the same stock.
    book(shop, wo, result["candidates"][0])
    assert next(p for p in shop.q("parts.list") if p["sku"] == "PAD-1")["reserved"] == 1


def test_apprentice_needs_a_qualified_supervisor_on_shift(shop):
    apprentice = shop.employee("Andy Apprentice", [], kind="apprentice")
    shop.shifts(apprentice, MON)
    wo = shop.work_order(["Brake service"])
    assert not find(shop, wo)["candidates"]

    senior = shop.employee("Sam Senior", ["ASE-A5"], can_supervise=True, skill_level=5)
    shop.shifts(senior, MON, days=1)  # only on Monday
    result = find(shop, wo, limit=20)
    by_apprentice = [c for c in result["candidates"] if c["steps"][0]["employee_id"] == apprentice]
    assert by_apprentice and all(c["steps"][0]["supervisor_id"] == senior for c in by_apprentice)
    assert {c["start"][:10] for c in by_apprentice} == {MON.date().isoformat()}  # no supervisor, no apprentice job


def test_double_booking_is_refused(shop):
    tech = shop.employee("Gus General", [])
    shop.shifts(tech, MON)
    first, second = shop.work_order(["Oil & filter change"]), shop.work_order(["Oil & filter change"])
    cand = find(shop, first)["candidates"][0]
    book(shop, first, cand)
    line = shop.q("work_orders.get", {"id": second["id"]})["lines"][0]
    same_slot = {**cand, "steps": [{**cand["steps"][0], "line_id": line["id"]}]}
    with pytest.raises(Invalid, match="already booked"):
        book(shop, second, same_slot)
    # And the slot is no longer offered.
    taken = (cand["steps"][0]["employee_id"], cand["start"])
    assert taken not in {(c["steps"][0]["employee_id"], c["start"]) for c in find(shop, second, limit=20)["candidates"]}


def test_database_refuses_overlap_even_if_validation_is_skipped(shop):
    """The exclusion constraint is the last line of defence against a race."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from shoppi.db.session import tenant_tx

    tech = shop.employee("Gus General", [])
    shop.shifts(tech, MON)
    wo = shop.work_order(["Oil & filter change"])
    book(shop, wo, find(shop, wo)["candidates"][0])
    with pytest.raises(IntegrityError, match="ex_job_employee"):
        with tenant_tx(shop.owner, command="test.race") as tx:
            tx.execute(text("INSERT INTO job_assignments (id, shop_id, work_order_id, employee_id, starts_at, ends_at, label, status) "
                            "SELECT gen_random_uuid(), shop_id, work_order_id, employee_id, starts_at, ends_at, 'dup', 'planned' FROM job_assignments LIMIT 1"))


def test_explains_when_nothing_fits(shop):
    wo = shop.work_order(["Oil & filter change"])
    assert codes(find(shop, wo)) == ["NO_SHIFT_COVERAGE"]

    tech = shop.employee("Gus General", [])
    shop.shifts(tech, MON, days=1, start="08:00", end="09:00")
    busy = shop.work_order(["Tire rotation & balance"])
    book(shop, busy, find(shop, busy)["candidates"][0])
    result = shop.q("schedule.find_slots", {"work_order_id": wo["id"], "earliest": iso(MON), "days": 1})
    assert codes(result) == ["FULLY_BOOKED"]


def test_moving_a_job_is_validated(shop):
    bea = shop.employee("Bea Brakes", ["ASE-A5"])
    gus = shop.employee("Gus General", [])
    for e in (bea, gus):
        shop.shifts(e, MON)
    wo = shop.work_order(["Brake service"])
    book(shop, wo, find(shop, wo)["candidates"][0])
    job = shop.q("work_orders.get", {"id": wo["id"]})["jobs"][0]

    with pytest.raises(Invalid, match="does not hold"):
        execute(shop.owner, "schedule.move", {"id": job["id"], "employee_id": gus})
    with pytest.raises(Invalid, match="not on shift"):
        execute(shop.owner, "schedule.move", {"id": job["id"], "start": iso(MON.replace(hour=20))})
    moved = shop.run("schedule.move", {"id": job["id"], "start": iso((MON + timedelta(days=1)).replace(hour=13))})
    assert moved["starts_at"] == iso((MON + timedelta(days=1)).replace(hour=13))


def test_losing_a_certification_flags_future_jobs(shop):
    bea = shop.employee("Bea Brakes", ["ASE-A5"])
    shop.shifts(bea, MON)
    wo = shop.work_order(["Brake service"])
    book(shop, wo, find(shop, wo)["candidates"][0])
    cert = shop.q("employees.list")
    cert_id = next(e for e in cert if e["id"] == bea)["certs"][0]["id"]
    shop.run("certs.remove", {"id": cert_id})
    job = shop.q("work_orders.get", {"id": wo["id"]})["jobs"][0]
    assert job["status"] == "needs_reassignment" and "does not hold" in job["problem"]


def test_time_off_flags_future_jobs(shop):
    gus = shop.employee("Gus General", [])
    shop.shifts(gus, MON)
    wo = shop.work_order(["Oil & filter change"])
    cand = find(shop, wo)["candidates"][0]
    book(shop, wo, cand)
    day = cand["start"][:10]
    shop.run("time_off.create", {"employee_id": gus, "start": f"{day}T00:00:00", "end": f"{day}T23:59:00", "reason": "Sick"})
    assert shop.q("work_orders.get", {"id": wo["id"]})["jobs"][0]["status"] == "needs_reassignment"


def test_mechanic_updates_progress_on_own_job_only(shop):
    gus_emp = shop.employee("Gus General", [])
    bea_emp = shop.employee("Bea Brakes", ["ASE-A5"])
    gus = shop.member("mechanic", "Gus General", employee_id=gus_emp)
    for e in (gus_emp, bea_emp):
        shop.shifts(e, MON)
    mine, theirs = shop.work_order(["Oil & filter change"]), shop.work_order(["Brake service"])
    book(shop, mine, next(c for c in find(shop, mine, limit=20)["candidates"] if c["steps"][0]["employee_id"] == gus_emp))
    book(shop, theirs, find(shop, theirs)["candidates"][0])
    my_job = shop.q("work_orders.get", {"id": mine["id"]})["jobs"][0]["id"]
    their_job = shop.q("work_orders.get", {"id": theirs["id"]})["jobs"][0]["id"]

    assert execute(gus, "jobs.progress", {"id": my_job, "action": "start"})["status"] == "applied"
    assert shop.q("work_orders.get", {"id": mine["id"]})["status"] == "in_progress"
    with pytest.raises(Forbidden):
        execute(gus, "jobs.progress", {"id": their_job, "action": "start"})
    with pytest.raises(Conflict):
        execute(gus, "jobs.progress", {"id": my_job, "action": "start"})
    execute(gus, "jobs.progress", {"id": my_job, "action": "finish"})
    assert shop.q("work_orders.get", {"id": mine["id"]})["lines"][0]["status"] == "done"
    # A mechanic rescheduling a job is a change request, not a direct edit.
    assert execute(gus, "schedule.move", {"id": their_job, "start": iso((MON + timedelta(days=1)).replace(hour=9))})["status"] == "pending"


def test_bay_board_shows_status_and_utilisation(shop):
    gus = shop.employee("Gus General", [])
    shop.shifts(gus, MON)
    wo = shop.work_order(["Oil & filter change"])
    cand = find(shop, wo)["candidates"][0]
    book(shop, wo, cand)
    board = shop.q("bays.board", {"day": cand["start"][:10]})
    card = next(b for b in board if b["bay"]["id"] == cand["steps"][0]["bay_id"])
    assert card["utilisation"]["booked_minutes"] == 30 and card["utilisation"]["open_minutes"] == 540
    assert card["next"][0]["work_order_number"] == wo["number"]
    assert {b["status"] for b in board} <= {"free", "after_hours", "closed", "occupied", "holding", "paused", "scheduled_now"}
