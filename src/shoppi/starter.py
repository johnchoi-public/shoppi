"""Starter data for a new shop: typical bays and a service catalog with sensible
certification requirements. Everything is created through normal commands, so
it is editable afterwards and shows up in the history like any other change.
"""

from __future__ import annotations

from shoppi.core.actor import Actor
from shoppi.core.commands import execute, run_query


def _ok(result: dict) -> dict:
    assert result["status"] == "applied", result
    return result["result"]


def install_starter(actor: Actor) -> dict:
    """Create default bays and services for the actor's shop. Returns ids by name."""
    certs = {c["code"]: c["id"] for c in run_query(actor, "cert_types.list")}

    def req(*codes: str, group: int = 1, powertrain: str | None = None, min_class: int | None = None) -> list[dict]:
        return [{"cert_type_id": certs[c], "group_no": group, "applies_powertrain": powertrain, "applies_min_class": min_class}
                for c in codes]

    bays = {}
    for i, bay in enumerate([
        {"name": "Bay 1", "kind": "general", "capabilities": ["lift", "tire", "diagnostic"], "max_gvwr_class": 3, "max_weight_lb": 10000},
        {"name": "Bay 2", "kind": "general", "capabilities": ["lift", "ac", "diagnostic", "ev_safe"], "max_gvwr_class": 3, "max_weight_lb": 10000},
        {"name": "Alignment bay", "kind": "alignment", "capabilities": ["lift", "alignment", "inspection"], "max_gvwr_class": 3, "max_weight_lb": 12000},
        {"name": "Truck bay", "kind": "heavy_duty", "capabilities": ["heavy_lift", "diagnostic", "inspection", "ac"], "max_gvwr_class": 8,
         "max_weight_lb": 80000, "max_length_ft": 45},
    ]):
        bays[bay["name"]] = _ok(execute(actor, "bays.save", {**bay, "sort_order": i}))["id"]

    services = {}
    catalog = [
        {"name": "Oil & filter change", "price": 79, "steps": [{"name": "Oil & filter change", "minutes": 30}]},
        {"name": "Tire rotation & balance", "price": 60, "steps": [{"name": "Rotate & balance", "minutes": 45, "bay_capability": "tire"}]},
        {"name": "Brake service", "price": 320, "steps": [{
            "name": "Brake service", "minutes": 120,
            # Light vehicles: A5 or T4. Class 6 and up additionally need the truck brake certification.
            "requirements": req("ASE-A5", "ASE-T4") + req("ASE-T4", group=2, min_class=6)}]},
        {"name": "Wheel alignment", "price": 140, "steps": [{
            "name": "Four-wheel alignment", "minutes": 60, "bay_capability": "alignment", "requirements": req("ASE-A4", "ASE-T5")}]},
        {"name": "A/C service & recharge", "price": 210, "steps": [{
            "name": "A/C recovery & recharge", "minutes": 90, "bay_capability": "ac",
            "requirements": req("ASE-A7", "ASE-T7") + req("EPA-609", group=2)}]},
        {"name": "Check-engine diagnosis", "price": 150, "steps": [{
            "name": "Diagnosis", "minutes": 60, "bay_capability": "diagnostic",
            "requirements": req("ASE-A8", "ASE-L1", "ASE-A6") + req("ASE-A9", "ASE-T2", group=2, powertrain="diesel")
            + req("EV-HV", group=3, powertrain="ev")}]},
        {"name": "High-voltage battery service", "price": 480, "steps": [{
            "name": "HV battery service", "minutes": 180, "bay_capability": "ev_safe",
            "requirements": req("EV-HV") + req("ASE-L3", group=2)}]},
        {"name": "State inspection", "price": 35, "steps": [{
            "name": "Safety & emissions inspection", "minutes": 45, "bay_capability": "inspection", "requirements": req("STATE-INSP")}]},
        {"name": "DOT annual inspection", "price": 180, "steps": [{
            "name": "DOT annual inspection", "minutes": 120, "bay_capability": "inspection",
            "requirements": req("DOT-INSP") + req("ASE-T8", group=2)}]},
        {"name": "Diesel preventive maintenance", "price": 420, "steps": [{
            "name": "PM service", "minutes": 150, "requirements": req("ASE-T8", "ASE-T2", "ASE-A9")}]},
        {"name": "Engine rebuild", "price": 4200, "steps": [
            {"name": "Teardown", "minutes": 240, "requirements": req("ASE-A1", "ASE-T1", "ASE-T2")},
            {"name": "Machine-shop turnaround", "minutes": 480, "hands_on": False, "holds_bay": True},
            {"name": "Reassembly & test", "minutes": 300, "same_mechanic_as_prev": True, "requirements": req("ASE-A1", "ASE-T1", "ASE-T2")},
        ]},
    ]
    for svc in catalog:
        services[svc["name"]] = _ok(execute(actor, "services.save", svc))["id"]
    # The general "Diagnosis" option offered in the intake wizard.
    services["Diagnosis"] = _ok(execute(actor, "services.ensure_diagnosis"))["id"]
    return {"bays": bays, "services": services, "certs": certs}
