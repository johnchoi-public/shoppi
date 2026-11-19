"""Bridges the database and the pure scheduling engine.

`load_snapshot` reads everything the engine needs for a time window in a handful
of queries; `build_request` turns a work order into the engine's step list.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shoppi.core.timeutil import to_local
from shoppi.db.models import (
    Bay, BayClosure, BayHours, CertType, Employee, EmployeeCert, JobAssignment, Part, PartReservation, PoLine,
    PurchaseOrder, ServicePart, ServiceStep, ServiceType, Shift, Shop, StepCertRequirement, SupplierPartLink,
    TimeOff, Vehicle, WorkOrder, WorkOrderLine,
)
from shoppi.scheduling.engine import (
    DEFAULT_WEIGHTS, BayRes, Candidate, Hold, Interval, Mechanic, PlannedStep, SlotRequest, SlotSearchResult,
    Snapshot, StepReq, VehicleSpec,
)

UTC = timezone.utc
ACTIVE = ("planned", "in_progress", "paused")


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)


def open_intervals(tz: ZoneInfo, shop_hours: dict, bay_hours: dict[int, BayHours], first: date, last: date) -> list[Interval]:
    """Opening windows of one bay for each local day, as UTC intervals. A bay's own
    hours win; weekdays it doesn't define fall back to the shop's hours."""
    out: list[Interval] = []
    day = first
    while day <= last:
        wd = day.weekday()
        opens = closes = None
        if wd in bay_hours:
            bh = bay_hours[wd]
            if not bh.closed and bh.opens and bh.closes:
                opens, closes = bh.opens, bh.closes
        else:
            pair = (shop_hours or {}).get(str(wd))
            if pair:
                opens, closes = time.fromisoformat(pair[0]), time.fromisoformat(pair[1])
        if opens and closes and closes > opens:
            out.append(Interval(_utc(datetime.combine(day, opens, tz)), _utc(datetime.combine(day, closes, tz))))
        day += timedelta(days=1)
    return out


def load_snapshot(tx: Session, shop: Shop, start: datetime, end: datetime, *,
                  exclude_work_order_id: UUID | None = None,
                  exclude_assignment_ids: tuple[UUID, ...] = ()) -> Snapshot:
    tz = ZoneInfo(shop.timezone)
    start, end = _utc(start), _utc(end)
    pad_start, pad_end = start - timedelta(days=1), end + timedelta(days=1)

    certs = {c.id: (c.code, c.name, c.legally_required) for c in tx.scalars(select(CertType))}

    mechanics: dict[UUID, Mechanic] = {}
    for e in tx.scalars(select(Employee).where(Employee.active, Employee.kind.in_(("tech", "apprentice"))).order_by(Employee.name)):
        mechanics[e.id] = Mechanic(e.id, e.name, e.kind, e.skill_level, e.can_supervise)
    for c in tx.scalars(select(EmployeeCert)):
        if c.employee_id in mechanics:
            m = mechanics[c.employee_id]
            # Holding the same certification twice: keep the later expiry.
            prev = m.certs.get(c.cert_type_id, date.min)
            if c.expires_on is None or prev is None:
                m.certs[c.cert_type_id] = None
            else:
                m.certs[c.cert_type_id] = max(prev, c.expires_on)
    for s in tx.scalars(select(Shift).where(Shift.starts_at < pad_end, Shift.ends_at > pad_start)):
        if s.employee_id in mechanics:
            mechanics[s.employee_id].shifts.append(Interval(_utc(s.starts_at), _utc(s.ends_at)))
    for t in tx.scalars(select(TimeOff).where(TimeOff.starts_at < pad_end, TimeOff.ends_at > pad_start)):
        if t.employee_id in mechanics:
            mechanics[t.employee_id].busy.append(Interval(_utc(t.starts_at), _utc(t.ends_at)))

    bays: dict[UUID, BayRes] = {}
    hours: dict[UUID, dict[int, BayHours]] = {}
    for h in tx.scalars(select(BayHours)):
        hours.setdefault(h.bay_id, {})[h.weekday] = h
    first_day, last_day = pad_start.astimezone(tz).date(), pad_end.astimezone(tz).date()
    for b in tx.scalars(select(Bay).where(Bay.active).order_by(Bay.sort_order, Bay.name)):
        bays[b.id] = BayRes(
            b.id, b.name, frozenset(b.capabilities or []), b.max_gvwr_class, b.max_weight_lb,
            float(b.max_length_ft) if b.max_length_ft is not None else None,
            open=open_intervals(tz, shop.hours, hours.get(b.id, {}), first_day, last_day), sort_order=b.sort_order,
        )
    for c in tx.scalars(select(BayClosure).where(BayClosure.starts_at < pad_end, BayClosure.ends_at > pad_start)):
        if c.bay_id in bays:
            bays[c.bay_id].closures.append(Interval(_utc(c.starts_at), _utc(c.ends_at)))

    q = select(JobAssignment).where(JobAssignment.status.in_(ACTIVE), JobAssignment.starts_at < pad_end, JobAssignment.ends_at > pad_start)
    for a in tx.scalars(q):
        if a.work_order_id == exclude_work_order_id or a.id in exclude_assignment_ids:
            continue
        iv = Interval(_utc(a.starts_at), _utc(a.ends_at))
        if a.employee_id in mechanics:
            mechanics[a.employee_id].busy.append(iv)
        if a.supervisor_id in mechanics:
            mechanics[a.supervisor_id].supervising.append(iv)
        if a.bay_id in bays:
            bays[a.bay_id].busy.append(iv)

    settings = shop.settings or {}
    weights = {**DEFAULT_WEIGHTS, **(settings.get("scheduler_weights") or {})}
    return Snapshot(tz=tz, now=datetime.now(UTC), mechanics=list(mechanics.values()), bays=list(bays.values()),
                    certs=certs, supervision_cap=int(settings.get("supervision_cap", 2)), weights=weights)


def vehicle_spec(v: Vehicle | None) -> VehicleSpec:
    if v is None:
        return VehicleSpec()
    return VehicleSpec(v.gvwr_class or 1, v.weight_lb, float(v.length_ft) if v.length_ft is not None else None, v.powertrain or "gas")


def step_reqs(tx: Session, work_order: WorkOrder, vehicle: VehicleSpec) -> list[StepReq]:
    """The work order's open lines, expanded into scheduling steps with the
    certification requirements that apply to this particular vehicle."""
    reqs: list[StepReq] = []
    lines = tx.scalars(select(WorkOrderLine).where(WorkOrderLine.work_order_id == work_order.id, WorkOrderLine.status == "open")
                       .order_by(WorkOrderLine.created_at, WorkOrderLine.id)).all()
    for line in lines:
        steps = []
        if line.service_type_id:
            steps = tx.scalars(select(ServiceStep).where(ServiceStep.service_type_id == line.service_type_id).order_by(ServiceStep.seq)).all()
        if not steps:
            reqs.append(StepReq(label=line.description, minutes=60, line_id=line.id))
            continue
        for s in steps:
            groups: dict[int, set[UUID]] = {}
            for r in tx.scalars(select(StepCertRequirement).where(StepCertRequirement.step_id == s.id)):
                if r.applies_powertrain and r.applies_powertrain != vehicle.powertrain:
                    continue
                if r.applies_min_class and vehicle.gvwr_class < r.applies_min_class:
                    continue
                groups.setdefault(r.group_no, set()).add(r.cert_type_id)
            label = line.description if len(steps) == 1 else f"{line.description}: {s.name}"
            reqs.append(StepReq(label=label, minutes=s.minutes, hands_on=s.hands_on, needs_bay=s.needs_bay,
                                bay_capability=s.bay_capability, holds_bay=s.holds_bay,
                                same_mechanic_as_prev=s.same_mechanic_as_prev,
                                cert_groups=[frozenset(g) for _, g in sorted(groups.items())],
                                line_id=line.id, step_id=s.id))
    return reqs


def parts_status(tx: Session, work_order: WorkOrder, now: datetime | None = None) -> dict:
    """Are the parts this work order needs available, and if not, when?

    Returns {"ready_at": datetime | None, "missing": [names], "lines": [...]}.
    ready_at None with no missing parts = everything is on hand.
    """
    now = now or datetime.now(UTC)
    needed: dict[UUID, int] = {}
    q = (select(ServicePart.part_id, func.sum(ServicePart.qty))
         .join(WorkOrderLine, WorkOrderLine.service_type_id == ServicePart.service_type_id)
         .where(WorkOrderLine.work_order_id == work_order.id, WorkOrderLine.status == "open")
         .group_by(ServicePart.part_id))
    for part_id, qty in tx.execute(q):
        needed[part_id] = int(qty)

    ready_at: datetime | None = None
    missing: list[str] = []
    lines: list[dict] = []
    for part_id, qty in needed.items():
        part = tx.get(Part, part_id)
        mine = tx.scalar(select(func.coalesce(func.sum(PartReservation.qty), 0)).where(
            PartReservation.part_id == part_id, PartReservation.work_order_id == work_order.id,
            PartReservation.status.in_(("reserved", "used")))) or 0
        others = tx.scalar(select(func.coalesce(func.sum(PartReservation.qty), 0)).where(
            PartReservation.part_id == part_id, PartReservation.work_order_id != work_order.id,
            PartReservation.status == "reserved")) or 0
        # "used" reservations have already left on_hand; "reserved" ones are still on the shelf.
        mine_reserved = tx.scalar(select(func.coalesce(func.sum(PartReservation.qty), 0)).where(
            PartReservation.part_id == part_id, PartReservation.work_order_id == work_order.id,
            PartReservation.status == "reserved")) or 0
        available = max(0, part.on_hand - int(others) - int(mine_reserved))
        short = max(0, qty - int(mine) - available)
        info = {"part_id": str(part_id), "sku": part.sku, "name": part.name, "needed": qty, "reserved": int(mine),
                "available": available, "short": short, "status": "in_stock", "eta": None}
        if short > 0:
            eta = tx.scalar(
                select(func.min(PurchaseOrder.eta)).join(PoLine, PoLine.po_id == PurchaseOrder.id)
                .where(PoLine.part_id == part_id, PurchaseOrder.status == "ordered", PoLine.qty > PoLine.received_qty,
                       PurchaseOrder.eta.is_not(None)))
            if eta is not None:
                info.update(status="on_order", eta=eta)
            else:
                days = tx.scalar(select(func.min(SupplierPartLink.last_eta_days)).where(
                    SupplierPartLink.part_id == part_id, SupplierPartLink.last_eta_days.is_not(None)))
                if days is not None:
                    eta = now + timedelta(days=int(days))
                    info.update(status="order_needed", eta=eta)
                else:
                    info["status"] = "unavailable"
                    missing.append(part.name)
            if eta is not None:
                ready_at = max(ready_at, _utc(eta)) if ready_at else _utc(eta)
        lines.append(info)
    return {"ready_at": ready_at, "missing": missing, "lines": lines}


def build_request(tx: Session, shop: Shop, work_order: WorkOrder, *, earliest: datetime, latest: datetime,
                  preferred_window: Interval | None = None, ignore_parts: bool = False,
                  ignore_optional_certs: bool = False) -> SlotRequest:
    vehicle = vehicle_spec(tx.get(Vehicle, work_order.vehicle_id))
    parts = parts_status(tx, work_order)
    return SlotRequest(
        steps=step_reqs(tx, work_order, vehicle), vehicle=vehicle, earliest=_utc(earliest), latest=_utc(latest),
        parts_ready_at=parts["ready_at"], parts_missing=parts["missing"],
        preferred_employee_id=work_order.preferred_employee_id, preferred_window=preferred_window,
        ignore_parts=ignore_parts, ignore_optional_certs=ignore_optional_certs,
    )


def derive_holds(plan: list[PlannedStep]) -> list[Hold]:
    """Bay holds implied by a plan: the vehicle stays in the bay between two steps
    when the earlier step says so and the next step uses the same bay (or no bay)."""
    holds = []
    ordered = sorted(plan, key=lambda p: p.start)
    for a, b in zip(ordered, ordered[1:]):
        if a.bay_id and a.step.holds_bay and b.start > a.end and b.bay_id in (a.bay_id, None):
            holds.append(Hold(a.bay_id, a.end, b.start))
    return holds


# ---- Serialization ----------------------------------------------------------------


def names(tx: Session) -> dict[UUID, str]:
    out = {e.id: e.name for e in tx.scalars(select(Employee))}
    out.update({b.id: b.name for b in tx.scalars(select(Bay))})
    return out


def candidate_out(c: Candidate, tz: ZoneInfo, nm: dict[UUID, str]) -> dict:
    return {
        "start": to_local(c.start, tz), "end": to_local(c.end, tz), "score": c.score,
        "score_breakdown": c.score_breakdown, "reasons": c.reasons,
        "steps": [{
            "line_id": str(s.step.line_id) if s.step.line_id else None,
            "step_id": str(s.step.step_id) if s.step.step_id else None,
            "label": s.step.label, "minutes": s.step.minutes, "hands_on": s.step.hands_on,
            "employee_id": str(s.employee_id) if s.employee_id else None, "employee_name": nm.get(s.employee_id),
            "bay_id": str(s.bay_id) if s.bay_id else None, "bay_name": nm.get(s.bay_id),
            "supervisor_id": str(s.supervisor_id) if s.supervisor_id else None, "supervisor_name": nm.get(s.supervisor_id),
            "start": to_local(s.start, tz), "end": to_local(s.end, tz),
        } for s in c.steps],
        "holds": [{"bay_id": str(h.bay_id), "bay_name": nm.get(h.bay_id), "start": to_local(h.start, tz), "end": to_local(h.end, tz)} for h in c.holds],
    }


def result_out(r: SlotSearchResult, tz: ZoneInfo, nm: dict[UUID, str]) -> dict:
    return {
        "candidates": [candidate_out(c, tz, nm) for c in r.candidates],
        "blockers": [{"code": b.code, "message": b.message} for b in r.blockers],
        "relaxations": [{"code": x.code, "message": x.message, "start": to_local(x.start, tz)} for x in r.relaxations],
    }


def service_minutes(tx: Session, service_type_id: UUID) -> int:
    return int(tx.scalar(select(func.coalesce(func.sum(ServiceStep.minutes), 0)).where(ServiceStep.service_type_id == service_type_id)) or 0)


__all__ = ["ACTIVE", "load_snapshot", "build_request", "parts_status", "step_reqs", "vehicle_spec", "derive_holds",
           "names", "candidate_out", "result_out", "open_intervals", "service_minutes", "ServiceType"]
