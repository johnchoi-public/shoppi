"""The built-in calendar and job scheduling.

The shop's own tables are the system of record: nothing here needs an external
calendar. `calendar.feed` merges every kind of scheduled thing into one typed
stream; booking and moving jobs always go through the scheduling engine, so a
drag-and-drop can't put a job with an unqualified mechanic or in a closed bay.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, NaiveDatetime
from sqlalchemy import delete, select

from shoppi.core.commands import Conflict, Ctx, Forbidden, IdArg, Invalid, command, patch, query
from shoppi.core.permissions import permission_map
from shoppi.core.timeutil import to_local
from shoppi.db.models import (
    Bay, BayClosure, CalendarEvent, CertType, Customer, Employee, EmployeeCert, JobAssignment, Part, PartReservation,
    PurchaseOrder, ServiceStep, Shift, StepCertRequirement, Supplier, TimeOff, Vehicle, WorkOrder, WorkOrderLine,
)
from shoppi.domain.customers import vehicle_title
from shoppi.scheduling import engine
from shoppi.scheduling.engine import Hold, Interval, PlannedStep, SlotRequest, StepReq
from shoppi.scheduling.service import (
    ACTIVE, build_request, derive_holds, load_snapshot, names, parts_status, result_out, step_reqs, vehicle_spec,
)

UTC = timezone.utc

# ---- Finding slots ----------------------------------------------------------------


class FindSlots(BaseModel):
    work_order_id: UUID
    earliest: NaiveDatetime | None = Field(default=None, description="Don't start before this (shop-local). Defaults to now.")
    days: int = Field(default=14, ge=1, le=60, description="How many days ahead to search.")
    preferred_start: NaiveDatetime | None = Field(default=None, description="Start of the customer's preferred time window.")
    preferred_end: NaiveDatetime | None = None
    limit: int = Field(default=8, ge=1, le=20)


@query("schedule.find_slots", vertical="schedule",
       description="Find the best (mechanic, bay, time) options for a work order, respecting certifications, bay size and hours, shifts and parts. Explains why if nothing fits.")
def find_slots(ctx: Ctx, args: FindSlots) -> dict:
    wo = ctx.get(WorkOrder, args.work_order_id, "Work order")
    earliest = ctx.local(args.earliest) if args.earliest else ctx.now()
    earliest = max(earliest, ctx.now())
    latest = earliest + timedelta(days=args.days)
    window = None
    if args.preferred_start and args.preferred_end and args.preferred_end > args.preferred_start:
        window = Interval(ctx.local(args.preferred_start).astimezone(UTC), ctx.local(args.preferred_end).astimezone(UTC))
    # Load further than the window so "would fit if you waited longer" can be answered.
    snap = load_snapshot(ctx.tx, ctx.shop, earliest, latest + timedelta(days=max(28, args.days * 2)), exclude_work_order_id=wo.id)
    req = build_request(ctx.tx, ctx.shop, wo, earliest=earliest, latest=latest, preferred_window=window)
    result = engine.find_slots(snap, req, limit=args.limit)
    parts = parts_status(ctx.tx, wo)
    out = result_out(result, ctx.tz, names(ctx.tx))
    out["parts"] = {"ready_at": to_local(parts["ready_at"], ctx.tz), "missing": parts["missing"],
                    "lines": [{**p, "eta": to_local(p["eta"], ctx.tz)} for p in parts["lines"]]}
    out["steps"] = [{"label": s.label, "minutes": s.minutes, "hands_on": s.hands_on,
                     "certs": [sorted(snap.cert_label(c) for c in g) for g in s.cert_groups],
                     "bay_capability": s.bay_capability} for s in req.steps]
    out["searched"] = {"from": to_local(earliest, ctx.tz), "to": to_local(latest, ctx.tz)}
    return out


# ---- Booking ----------------------------------------------------------------------


class PlanStepIn(BaseModel):
    line_id: UUID | None = None
    step_id: UUID | None = None
    employee_id: UUID | None = None
    bay_id: UUID | None = None
    supervisor_id: UUID | None = None
    start: NaiveDatetime
    end: NaiveDatetime | None = None


class BookIn(BaseModel):
    work_order_id: UUID
    steps: list[PlanStepIn] = Field(min_length=1, description="One entry per step, as returned by schedule.find_slots.")
    override_reason: str | None = Field(default=None, description="Manager's reason for overriding a non-legal rule.")


def _violations_error(violations: list[engine.Violation], ctx: Ctx, override_reason: str | None):
    """Raise unless the plan is clean, or everything wrong with it is overridable and
    a manager has given a reason. Legally required certifications are never overridable."""
    if not violations:
        return
    hard = [v for v in violations if not v.overridable]
    payload = [{"code": v.code, "message": v.message, "overridable": v.overridable} for v in violations]
    if hard:
        raise Invalid(" ".join(v.message for v in hard), violations=payload, can_override=False)
    if not override_reason or not override_reason.strip():
        raise Invalid(" ".join(v.message for v in violations) + " A manager can override this with a reason.",
                      violations=payload, can_override=True)
    if not ctx.manager_authority:
        raise Forbidden("Only an owner or advisor can override a scheduling rule.")


def _reserve_parts(ctx: Ctx, wo: WorkOrder) -> None:
    for line in parts_status(ctx.tx, wo)["lines"]:
        want = line["needed"] - line["reserved"]
        take = min(want, line["available"])
        if take > 0:
            ctx.tx.add(PartReservation(part_id=UUID(line["part_id"]), work_order_id=wo.id, qty=take))
    ctx.tx.flush()


def _is_hold(a: JobAssignment) -> bool:
    return a.employee_id is None and a.step_id is None and a.line_id is None


def _write_holds(ctx: Ctx, wo: WorkOrder, holds: list[Hold]) -> None:
    for h in holds:
        ctx.tx.add(JobAssignment(work_order_id=wo.id, label="Vehicle held in bay", bay_id=h.bay_id,
                                 starts_at=h.start, ends_at=h.end))


@command("schedule.book", vertical="schedule",
         description="Book a work order onto the schedule using a plan from schedule.find_slots. Replaces any existing booking for it.")
def book(ctx: Ctx, args: BookIn) -> dict:
    wo = ctx.get(WorkOrder, args.work_order_id, "Work order")
    if wo.status in ("completed", "cancelled"):
        raise Conflict(f"This work order is {wo.status}.")
    existing = ctx.tx.scalars(select(JobAssignment).where(JobAssignment.work_order_id == wo.id,
                                                          JobAssignment.status.in_((*ACTIVE, "needs_reassignment")))).all()
    if any(a.status in ("in_progress", "paused") for a in existing):
        raise Conflict("Work has already started on this work order. Move the remaining jobs individually.")
    for a in existing:
        ctx.tx.delete(a)
    ctx.tx.flush()

    vehicle = vehicle_spec(ctx.tx.get(Vehicle, wo.vehicle_id))
    reqs = {(r.line_id, r.step_id): r for r in step_reqs(ctx.tx, wo, vehicle)}
    plan: list[PlannedStep] = []
    for s in args.steps:
        req_step = reqs.get((s.line_id, s.step_id))
        if req_step is None:
            raise Invalid("The plan doesn't match this work order's services. Search for slots again.")
        start = ctx.local(s.start).astimezone(UTC)
        end = ctx.local(s.end).astimezone(UTC) if s.end else start + timedelta(minutes=req_step.minutes)
        plan.append(PlannedStep(req_step, start, end, s.employee_id if req_step.hands_on else None,
                                s.bay_id if req_step.needs_bay else None, s.supervisor_id))
    planned_keys = {(p.step.line_id, p.step.step_id) for p in plan}
    missing = [r.label for k, r in reqs.items() if k not in planned_keys]
    if missing:
        raise Invalid("The plan is missing: " + ", ".join(missing) + ".")

    first, last = min(p.start for p in plan), max(p.end for p in plan)
    snap = load_snapshot(ctx.tx, ctx.shop, first, last, exclude_work_order_id=wo.id)
    request = build_request(ctx.tx, ctx.shop, wo, earliest=first, latest=last)
    holds = derive_holds(plan)
    _violations_error(engine.validate_plan(snap, request, plan, holds), ctx, args.override_reason)

    nm = names(ctx.tx)
    for p in plan:
        ctx.tx.add(JobAssignment(
            work_order_id=wo.id, line_id=p.step.line_id, step_id=p.step.step_id, label=p.step.label,
            employee_id=p.employee_id, supervisor_id=p.supervisor_id, bay_id=p.bay_id, starts_at=p.start, ends_at=p.end,
            override_reason=args.override_reason.strip() if args.override_reason else None))
    _write_holds(ctx, wo, holds)
    wo.status = "scheduled"
    ctx.tx.flush()
    _reserve_parts(ctx, wo)
    _notify_calendar_changed(ctx)
    return {"work_order_id": str(wo.id), "number": wo.number, "start": to_local(first, ctx.tz), "end": to_local(last, ctx.tz),
            "jobs": [{"label": p.step.label, "mechanic": nm.get(p.employee_id), "bay": nm.get(p.bay_id),
                      "start": to_local(p.start, ctx.tz), "end": to_local(p.end, ctx.tz)} for p in plan]}


def _step_req_for(ctx: Ctx, a: JobAssignment, vehicle: engine.VehicleSpec) -> StepReq:
    minutes = max(5, int((a.ends_at - a.starts_at).total_seconds() // 60))
    if a.step_id is None:
        return StepReq(label=a.label or "Job", minutes=minutes, hands_on=a.employee_id is not None,
                       needs_bay=a.bay_id is not None, line_id=a.line_id)
    step = ctx.tx.get(ServiceStep, a.step_id)
    groups: dict[int, set[UUID]] = {}
    for r in ctx.tx.scalars(select(StepCertRequirement).where(StepCertRequirement.step_id == a.step_id)):
        if r.applies_powertrain and r.applies_powertrain != vehicle.powertrain:
            continue
        if r.applies_min_class and vehicle.gvwr_class < r.applies_min_class:
            continue
        groups.setdefault(r.group_no, set()).add(r.cert_type_id)
    return StepReq(label=a.label or step.name, minutes=minutes, hands_on=step.hands_on, needs_bay=step.needs_bay,
                   bay_capability=step.bay_capability, holds_bay=step.holds_bay,
                   cert_groups=[frozenset(g) for _, g in sorted(groups.items())], line_id=a.line_id, step_id=a.step_id)


def _rebuild_holds(ctx: Ctx, wo: WorkOrder) -> None:
    """Recompute the bay holds between a work order's steps after one of them moved."""
    rows = ctx.tx.scalars(select(JobAssignment).where(JobAssignment.work_order_id == wo.id, JobAssignment.status.in_(ACTIVE))).all()
    for a in rows:
        if _is_hold(a) and a.status == "planned":
            ctx.tx.delete(a)
    ctx.tx.flush()
    vehicle = vehicle_spec(ctx.tx.get(Vehicle, wo.vehicle_id))
    plan = [PlannedStep(_step_req_for(ctx, a, vehicle), a.starts_at.astimezone(UTC), a.ends_at.astimezone(UTC), a.employee_id, a.bay_id)
            for a in rows if not _is_hold(a)]
    holds = derive_holds(plan)
    if holds:
        snap = load_snapshot(ctx.tx, ctx.shop, min(h.start for h in holds), max(h.end for h in holds), exclude_work_order_id=wo.id)
        bays = {b.id: b for b in snap.bays}
        for h in holds:
            if h.bay_id in bays and not bays[h.bay_id].is_clear(h.start, h.end):
                raise Conflict(f"{bays[h.bay_id].name} is booked or closed between this job's steps, so the vehicle can't stay there.")
        _write_holds(ctx, wo, holds)
        ctx.tx.flush()


class MoveIn(BaseModel):
    id: UUID
    start: NaiveDatetime | None = None
    end: NaiveDatetime | None = None
    employee_id: UUID | None = None
    bay_id: UUID | None = None
    override_reason: str | None = None


@command("schedule.move", vertical="schedule",
         description="Move one scheduled job to another time, mechanic or bay. Refused if it would break a certification, bay or shift rule.")
def move(ctx: Ctx, args: MoveIn) -> dict:
    a = ctx.get(JobAssignment, args.id, "Scheduled job")
    if a.status in ("done", "cancelled"):
        raise Conflict(f"This job is {a.status}.")
    if _is_hold(a):
        raise Invalid("A bay hold moves with its job: move the job instead.")
    wo = ctx.tx.get(WorkOrder, a.work_order_id)
    length = a.ends_at - a.starts_at
    start = ctx.local(args.start).astimezone(UTC) if args.start else a.starts_at.astimezone(UTC)
    end = ctx.local(args.end).astimezone(UTC) if args.end else start + length
    if end <= start:
        raise Invalid("End must be after start.")
    employee_id = args.employee_id if "employee_id" in args.model_fields_set and args.employee_id else a.employee_id
    bay_id = args.bay_id if "bay_id" in args.model_fields_set and args.bay_id else a.bay_id

    vehicle = vehicle_spec(ctx.tx.get(Vehicle, wo.vehicle_id))
    step = _step_req_for(ctx, a, vehicle)
    step.minutes = int((end - start).total_seconds() // 60)
    hold_ids = tuple(h.id for h in ctx.tx.scalars(select(JobAssignment).where(JobAssignment.work_order_id == wo.id)) if _is_hold(h))
    snap = load_snapshot(ctx.tx, ctx.shop, start, end, exclude_assignment_ids=(a.id, *hold_ids))
    parts = parts_status(ctx.tx, wo)
    request = SlotRequest(steps=[step], vehicle=vehicle, earliest=start, latest=end,
                          parts_ready_at=parts["ready_at"], parts_missing=parts["missing"])
    planned = PlannedStep(step, start, end, employee_id if step.hands_on else None, bay_id if step.needs_bay else None, a.supervisor_id)
    _violations_error(engine.validate_plan(snap, request, [planned]), ctx, args.override_reason)

    a.starts_at, a.ends_at, a.employee_id, a.bay_id = start, end, planned.employee_id, planned.bay_id
    a.supervisor_id = planned.supervisor_id
    if a.status == "needs_reassignment":
        a.status, a.problem = "planned", None
    if args.override_reason:
        a.override_reason = args.override_reason.strip()
    ctx.tx.flush()
    _rebuild_holds(ctx, wo)
    if wo.status == "draft":
        wo.status = "scheduled"
    _notify_calendar_changed(ctx)
    nm = names(ctx.tx)
    return ctx.out(a, employee_name=nm.get(a.employee_id), bay_name=nm.get(a.bay_id))


@command("schedule.unschedule", vertical="schedule", description="Take a work order off the schedule (jobs not yet started) and free its parts.")
def unschedule(ctx: Ctx, args: IdArg) -> dict:
    wo = ctx.get(WorkOrder, args.id, "Work order")
    removed = 0
    for a in ctx.tx.scalars(select(JobAssignment).where(JobAssignment.work_order_id == wo.id,
                                                        JobAssignment.status.in_(("planned", "needs_reassignment")))):
        ctx.tx.delete(a)
        removed += 1
    for r in ctx.tx.scalars(select(PartReservation).where(PartReservation.work_order_id == wo.id, PartReservation.status == "reserved")):
        r.status = "released"
    if wo.status == "scheduled":
        wo.status = "draft"
    _notify_calendar_changed(ctx)
    return {"removed": removed}


# ---- Job progress (the bay board buttons) -----------------------------------------


class JobAction(BaseModel):
    id: UUID
    action: Literal["start", "pause", "resume", "finish"]


@command("jobs.progress", vertical="self",
         description="Mark a scheduled job started, paused, resumed or finished. Mechanics can do this for their own jobs.")
def job_progress(ctx: Ctx, args: JobAction) -> dict:
    a = ctx.get(JobAssignment, args.id, "Scheduled job")
    perms = permission_map(ctx.tx, ctx.actor)
    mine = a.employee_id is not None and a.employee_id == ctx.actor.employee_id
    if perms["schedule"] != "edit" and not mine:
        raise Forbidden("You can only update jobs assigned to you.")
    if _is_hold(a):
        raise Invalid("A bay hold has no progress to record.")
    now = ctx.now()
    wo = ctx.tx.get(WorkOrder, a.work_order_id)
    if args.action == "start":
        if a.status != "planned":
            raise Conflict(f"This job is {a.status.replace('_', ' ')}.")
        a.status, a.started_at = "in_progress", now
        if wo.status in ("draft", "scheduled", "waiting_parts"):
            wo.status = "in_progress"
    elif args.action == "pause":
        if a.status != "in_progress":
            raise Conflict("Only a job in progress can be paused.")
        a.status = "paused"
    elif args.action == "resume":
        if a.status != "paused":
            raise Conflict("This job isn't paused.")
        a.status = "in_progress"
    else:
        if a.status not in ("in_progress", "paused"):
            raise Conflict("Start the job before finishing it.")
        a.status, a.finished_at = "done", now
        # Finishing early frees the mechanic and the bay for the rest of the slot.
        if a.starts_at < now < a.ends_at:
            a.ends_at = now
        if a.line_id:
            others = ctx.tx.scalar(select(JobAssignment.id).where(
                JobAssignment.line_id == a.line_id, JobAssignment.id != a.id, JobAssignment.status.in_(ACTIVE)).limit(1))
            if not others:
                line = ctx.tx.get(WorkOrderLine, a.line_id)
                if line and line.status == "open":
                    line.status = "done"
    ctx.tx.flush()
    return ctx.out(a)


# ---- Keeping bookings valid when the world changes --------------------------------


def revalidate(ctx: Ctx, *, employee_id: UUID | None = None, bay_id: UUID | None = None, vehicle_id: UUID | None = None) -> int:
    """Re-check future planned jobs after a certification, shift, time-off, bay or
    vehicle change. Jobs that no longer satisfy the rules are flagged
    `needs_reassignment` (with the reason) instead of silently staying booked."""
    now = ctx.now()
    q = select(JobAssignment).where(JobAssignment.status == "planned", JobAssignment.ends_at > now)
    if employee_id:
        q = q.where((JobAssignment.employee_id == employee_id) | (JobAssignment.supervisor_id == employee_id))
    if bay_id:
        q = q.where(JobAssignment.bay_id == bay_id)
    if vehicle_id:
        q = q.where(JobAssignment.work_order_id.in_(select(WorkOrder.id).where(WorkOrder.vehicle_id == vehicle_id)))
    rows = ctx.tx.scalars(q).all()
    if not rows:
        return 0
    ids = tuple(r.id for r in rows)
    snap = load_snapshot(ctx.tx, ctx.shop, min(r.starts_at for r in rows), max(r.ends_at for r in rows), exclude_assignment_ids=ids)
    mechanics = {m.id: m for m in snap.mechanics}
    bays = {b.id: b for b in snap.bays}
    flagged = 0
    for a in rows:
        wo = ctx.tx.get(WorkOrder, a.work_order_id)
        vehicle = vehicle_spec(ctx.tx.get(Vehicle, wo.vehicle_id))
        step = _step_req_for(ctx, a, vehicle)
        start, end = a.starts_at.astimezone(UTC), a.ends_at.astimezone(UTC)
        request = SlotRequest(steps=[step], vehicle=vehicle, earliest=start, latest=end)
        problem = None
        if _is_hold(a):
            b = bays.get(a.bay_id)
            if b is None:
                problem = "The bay is no longer active."
            elif any(c.overlaps(start, end) for c in b.closures):
                problem = f"{b.name} is closed during this hold."
        else:
            if step.hands_on:
                m = mechanics.get(a.employee_id)
                if m is None:
                    problem = "The assigned mechanic is no longer available for scheduling."
                else:
                    violation, supervisor = engine.mechanic_problem(snap, m, step, start, end, request)
                    if violation:
                        problem = violation.message
                    elif m.kind == "apprentice" and supervisor and a.supervisor_id != supervisor.id:
                        a.supervisor_id = supervisor.id
            if problem is None and step.needs_bay:
                b = bays.get(a.bay_id)
                if b is None:
                    problem = "The bay is no longer active."
                else:
                    violation = engine.bay_problem(b, step, vehicle, start, end)
                    if violation:
                        problem = violation.message
        if problem and not a.override_reason:
            a.status, a.problem = "needs_reassignment", problem
            flagged += 1
        else:
            # Still valid: put its time back so later rows in this batch see it as busy.
            iv = Interval(start, end)
            if a.employee_id in mechanics:
                mechanics[a.employee_id].busy.append(iv)
            if a.bay_id in bays:
                bays[a.bay_id].busy.append(iv)
    ctx.tx.flush()
    return flagged


def _notify_calendar_changed(ctx: Ctx) -> None:
    """Queue a push to any connected external calendar. Runs after commit and can
    never fail the booking: external calendars are mirrors, not the source of truth."""
    try:
        from shoppi.integrations.registry import calendar_changed
    except ImportError:
        return
    shop_id = ctx.actor.shop_id
    if not any(getattr(h, "_calendar_sync", False) for h in ctx.after_commit):
        hook = lambda: calendar_changed(shop_id)  # noqa: E731
        hook._calendar_sync = True  # type: ignore[attr-defined]
        ctx.after_commit.append(hook)


# ---- Calendar feed ----------------------------------------------------------------


class FeedArgs(BaseModel):
    start: NaiveDatetime = Field(description="Range start, shop-local time without offset, e.g. 2026-11-16T00:00:00.")
    end: NaiveDatetime = Field(description="Range end, shop-local time without offset.")
    kinds: list[str] | None = Field(default=None, description="Limit to these kinds: job, hold, event, shift, time_off, bay_closure, delivery, cert_expiry, promised.")
    employee_id: UUID | None = None
    bay_id: UUID | None = None
    query: str | None = Field(default=None, description="Optional text to filter titles.")


@query("calendar.feed", vertical="schedule",
       description="Everything on the shop calendar in a time range: scheduled jobs, bay holds, events, shifts, time off, bay closures, parts deliveries, certification expiries and promise times. Always call this before changing or deleting something on the calendar.")
def calendar_feed(ctx: Ctx, args: FeedArgs) -> list[dict]:
    start, end = ctx.local(args.start), ctx.local(args.end)
    if end <= start:
        raise Invalid("end must be after start")
    perms = permission_map(ctx.tx, ctx.actor)
    want = set(args.kinds) if args.kinds else None
    emp = {e.id: e for e in ctx.tx.scalars(select(Employee))}
    bays = {b.id: b for b in ctx.tx.scalars(select(Bay))}
    items: list[dict] = []

    def add(kind: str, id_, title: str, s: datetime, e: datetime, **extra):
        if want and kind not in want:
            return
        if args.employee_id and extra.get("employee_id") not in (args.employee_id, None):
            return
        if args.employee_id and extra.get("employee_id") is None and kind in ("job", "shift", "time_off", "cert_expiry"):
            return
        if args.bay_id and extra.get("bay_id") != args.bay_id and kind in ("job", "hold", "bay_closure"):
            return
        if args.query and args.query.lower() not in title.lower():
            return
        eid, bid = extra.pop("employee_id", None), extra.pop("bay_id", None)
        items.append({
            "id": f"{kind}:{id_}", "kind": kind, "ref_id": str(id_), "title": title,
            "start": to_local(s, ctx.tz), "end": to_local(e, ctx.tz), "all_day": extra.pop("all_day", False),
            "employee_id": str(eid) if eid else None, "employee_name": emp[eid].name if eid in emp else None,
            "bay_id": str(bid) if bid else None, "bay_name": bays[bid].name if bid in bays else None, **extra,
        })

    # Scheduled jobs and bay holds
    jobs = ctx.tx.scalars(select(JobAssignment).where(JobAssignment.starts_at < end, JobAssignment.ends_at > start,
                                                      JobAssignment.status != "cancelled")).all()
    wos = {w.id: w for w in ctx.tx.scalars(select(WorkOrder).where(WorkOrder.id.in_({j.work_order_id for j in jobs})))} if jobs else {}
    vehicles = {v.id: v for v in ctx.tx.scalars(select(Vehicle).where(Vehicle.id.in_({w.vehicle_id for w in wos.values()})))} if wos else {}
    customers = {c.id: c for c in ctx.tx.scalars(select(Customer).where(Customer.id.in_({w.customer_id for w in wos.values()})))} if wos else {}
    for j in jobs:
        wo = wos.get(j.work_order_id)
        v = vehicles.get(wo.vehicle_id) if wo else None
        c = customers.get(wo.customer_id) if wo else None
        vt = vehicle_title(v) if v else ""
        hold = _is_hold(j)
        add("hold" if hold else "job", j.id, (f"Hold: {vt}" if hold else f"{j.label} · {vt}"), j.starts_at, j.ends_at,
            employee_id=j.employee_id, bay_id=j.bay_id, status=j.status, problem=j.problem,
            work_order_id=str(j.work_order_id), work_order_number=wo.number if wo else None,
            customer_name=c.name if c else None, vehicle=vt, supervisor_id=str(j.supervisor_id) if j.supervisor_id else None,
            supervisor_name=emp[j.supervisor_id].name if j.supervisor_id in emp else None,
            editable=not hold and j.status in ("planned", "needs_reassignment"), overridden=bool(j.override_reason))

    for ev in ctx.tx.scalars(select(CalendarEvent).where(CalendarEvent.starts_at < end, CalendarEvent.ends_at > start)):
        add("event", ev.id, ev.title, ev.starts_at, ev.ends_at, all_day=ev.all_day, employee_id=ev.employee_id, bay_id=ev.bay_id,
            event_kind=ev.kind, notes=ev.notes, location=ev.location, series_id=str(ev.series_id) if ev.series_id else None,
            editable=True)

    if perms.get("shifts") != "none":
        for s in ctx.tx.scalars(select(Shift).where(Shift.starts_at < end, Shift.ends_at > start)):
            name = emp[s.employee_id].name if s.employee_id in emp else "Shift"
            add("shift", s.id, f"{name} on shift", s.starts_at, s.ends_at, employee_id=s.employee_id, notes=s.note, editable=False)
        for t in ctx.tx.scalars(select(TimeOff).where(TimeOff.starts_at < end, TimeOff.ends_at > start)):
            name = emp[t.employee_id].name if t.employee_id in emp else ""
            add("time_off", t.id, f"{name} off" + (f": {t.reason}" if t.reason else ""), t.starts_at, t.ends_at,
                employee_id=t.employee_id, editable=False)

    if perms.get("bays") != "none":
        for cl in ctx.tx.scalars(select(BayClosure).where(BayClosure.starts_at < end, BayClosure.ends_at > start)):
            name = bays[cl.bay_id].name if cl.bay_id in bays else "Bay"
            add("bay_closure", cl.id, f"{name} closed" + (f": {cl.reason}" if cl.reason else ""), cl.starts_at, cl.ends_at,
                bay_id=cl.bay_id, editable=False)

    if perms.get("inventory") != "none":
        for po in ctx.tx.scalars(select(PurchaseOrder).where(PurchaseOrder.status == "ordered", PurchaseOrder.eta >= start, PurchaseOrder.eta < end)):
            supplier = ctx.tx.get(Supplier, po.supplier_id) if po.supplier_id else None
            add("delivery", po.id, f"Parts delivery PO #{po.number}" + (f" · {supplier.name}" if supplier else ""),
                po.eta, po.eta + timedelta(minutes=30), editable=False)

    if perms.get("team") != "none":
        first_day, last_day = start.astimezone(ctx.tz).date(), end.astimezone(ctx.tz).date()
        types = {t.id: t for t in ctx.tx.scalars(select(CertType))}
        for c in ctx.tx.scalars(select(EmployeeCert).where(EmployeeCert.expires_on >= first_day, EmployeeCert.expires_on <= last_day)):
            if c.employee_id in emp and emp[c.employee_id].active:
                day_start = datetime.combine(c.expires_on, time.min, ctx.tz)
                label = types[c.cert_type_id].code if c.cert_type_id in types else "Certification"
                add("cert_expiry", c.id, f"{emp[c.employee_id].name}: {label} expires", day_start, day_start + timedelta(days=1),
                    all_day=True, employee_id=c.employee_id, editable=False)

    for wo in ctx.tx.scalars(select(WorkOrder).where(WorkOrder.promised_at >= start, WorkOrder.promised_at < end,
                                                     WorkOrder.status.not_in(("completed", "cancelled")))):
        add("promised", wo.id, f"Promised: WO #{wo.number}", wo.promised_at, wo.promised_at + timedelta(minutes=30),
            work_order_id=str(wo.id), work_order_number=wo.number, editable=False)

    items.sort(key=lambda i: (i["start"], i["kind"]))
    return items


# ---- Free-form calendar events ----------------------------------------------------

EventKind = Literal["event", "meeting", "closure", "reminder", "vendor"]


class EventIn(BaseModel):
    title: str = Field(min_length=1)
    start: NaiveDatetime = Field(description="Event start, shop-local time without offset, e.g. 2026-11-06T15:00:00.")
    end: NaiveDatetime = Field(description="Event end, after start.")
    all_day: bool = False
    kind: EventKind = "event"
    notes: str | None = None
    location: str | None = None
    employee_id: UUID | None = None
    bay_id: UUID | None = None
    repeat: Literal["daily", "weekly", "monthly"] | None = Field(default=None, description="Repeat the event.")
    repeat_until: date | None = Field(default=None, description="Last date to repeat on (required with repeat).")


class EventPatch(BaseModel):
    id: UUID = Field(description="An event id (ref_id) previously returned by calendar.feed. Never invent ids.")
    title: str | None = Field(default=None, min_length=1)
    start: NaiveDatetime | None = None
    end: NaiveDatetime | None = None
    all_day: bool | None = None
    kind: EventKind | None = None
    notes: str | None = None
    location: str | None = None
    employee_id: UUID | None = None
    bay_id: UUID | None = None


class EventDelete(BaseModel):
    id: UUID = Field(description="An event id (ref_id) previously returned by calendar.feed. Never invent ids.")
    whole_series: bool = Field(default=False, description="Also delete later events of the same repeating series.")


def _add_months(d: date, n: int) -> date | None:
    y, m = divmod(d.month - 1 + n, 12)
    try:
        return d.replace(year=d.year + y, month=m + 1)
    except ValueError:  # e.g. the 31st in a 30-day month: skip that month
        return None


@command("events.create", vertical="schedule", description="Create a calendar event (meeting, reminder, closure, vendor visit), optionally repeating.")
def events_create(ctx: Ctx, args: EventIn) -> dict:
    if args.end <= args.start:
        raise Invalid("end must be after start")
    if args.employee_id:
        ctx.get(Employee, args.employee_id, "Employee")
    if args.bay_id:
        ctx.get(Bay, args.bay_id, "Bay")
    starts = [args.start]
    series_id = None
    if args.repeat:
        if not args.repeat_until:
            raise Invalid("repeat_until is required for a repeating event.")
        series_id = uuid4()
        starts, i = [], 0
        while len(starts) < 400:
            if args.repeat == "daily":
                d = args.start.date() + timedelta(days=i)
            elif args.repeat == "weekly":
                d = args.start.date() + timedelta(weeks=i)
            else:
                d = _add_months(args.start.date(), i)
            i += 1
            if d is None:
                continue
            if d > args.repeat_until:
                break
            starts.append(datetime.combine(d, args.start.time()))
    length = args.end - args.start
    rows = []
    for s in starts:
        row = CalendarEvent(kind=args.kind, title=args.title, starts_at=ctx.local(s), ends_at=ctx.local(s + length),
                            all_day=args.all_day, notes=args.notes, location=args.location,
                            employee_id=args.employee_id, bay_id=args.bay_id, series_id=series_id)
        ctx.tx.add(row)
        rows.append(row)
    ctx.tx.flush()
    _notify_calendar_changed(ctx)
    return {**ctx.out(rows[0]), "created": len(rows)}


@command("events.update", vertical="schedule", description="Change fields of an existing calendar event (only the provided fields change).")
def events_update(ctx: Ctx, args: EventPatch) -> dict:
    row = ctx.get(CalendarEvent, args.id, "Event")
    patch(row, args, skip=("id", "start", "end"))
    if args.start:
        row.starts_at = ctx.local(args.start)
    if args.end:
        row.ends_at = ctx.local(args.end)
    if row.ends_at <= row.starts_at:
        raise Invalid("end must be after start")
    if not row.title:
        raise Invalid("An event needs a title.")
    ctx.tx.flush()
    _notify_calendar_changed(ctx)
    return ctx.out(row)


@command("events.delete", vertical="schedule", description="Delete a calendar event.")
def events_delete(ctx: Ctx, args: EventDelete) -> dict:
    row = ctx.get(CalendarEvent, args.id, "Event")
    if args.whole_series and row.series_id:
        result = ctx.tx.execute(delete(CalendarEvent).where(CalendarEvent.series_id == row.series_id, CalendarEvent.starts_at >= row.starts_at))
        deleted = result.rowcount
    else:
        ctx.tx.delete(row)
        deleted = 1
    _notify_calendar_changed(ctx)
    return {"deleted": deleted}


__all__ = ["revalidate", "Part"]
