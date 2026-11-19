"""Service bays: definitions, each bay's own weekly hours, closures, equipment,
and the live bay board."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import UUID

from pydantic import BaseModel, Field, NaiveDatetime
from sqlalchemy import delete, select

from shoppi.core.commands import Ctx, IdArg, Invalid, command, query
from shoppi.core.timeutil import to_local
from shoppi.db.models import (
    Bay, BayClosure, BayEquipment, BayHours, Customer, Employee, JobAssignment, Vehicle, WorkOrder,
)
from shoppi.scheduling.service import ACTIVE, open_intervals

CAPABILITIES = {
    "lift": "Two-post / four-post lift",
    "heavy_lift": "Heavy-duty lift or pit",
    "alignment": "Alignment rack",
    "ev_safe": "EV / high-voltage safe zone",
    "inspection": "Inspection lane",
    "tire": "Tire & wheel equipment",
    "ac": "A/C recovery machine",
    "diagnostic": "Diagnostic equipment",
    "welding": "Welding / fabrication",
}


class HoursIn(BaseModel):
    weekday: int = Field(ge=0, le=6)
    closed: bool = False
    opens: time | None = None
    closes: time | None = None


class BayIn(BaseModel):
    id: UUID | None = None
    name: str = Field(min_length=1)
    kind: str = "general"
    capabilities: list[str] = []
    max_gvwr_class: int = Field(default=3, ge=1, le=8)
    max_weight_lb: int | None = Field(default=None, ge=0)
    max_length_ft: float | None = Field(default=None, ge=0)
    active: bool = True
    sort_order: int = 0
    notes: str | None = None
    # Days listed here override the shop's opening hours for this bay; omit to keep them unchanged.
    hours: list[HoursIn] | None = None


class ClosureIn(BaseModel):
    bay_id: UUID
    start: NaiveDatetime
    end: NaiveDatetime
    reason: str | None = None


class EquipmentIn(BaseModel):
    bay_id: UUID
    name: str = Field(min_length=1)
    next_service_due: date | None = None
    notes: str | None = None


def bay_out(ctx: Ctx, b: Bay) -> dict:
    now = ctx.now()
    hours = [ctx.out(h) for h in ctx.tx.scalars(select(BayHours).where(BayHours.bay_id == b.id).order_by(BayHours.weekday))]
    closures = [ctx.out(c) for c in ctx.tx.scalars(select(BayClosure).where(BayClosure.bay_id == b.id, BayClosure.ends_at > now)
                                                   .order_by(BayClosure.starts_at))]
    equipment = [ctx.out(e) for e in ctx.tx.scalars(select(BayEquipment).where(BayEquipment.bay_id == b.id).order_by(BayEquipment.name))]
    return ctx.out(b, hours=hours, closures=closures, equipment=equipment)


@query("bays.list", vertical="bays", description="Bays with their capabilities, size limits, own hours and upcoming closures.")
def bays_list(ctx: Ctx) -> dict:
    bays = ctx.tx.scalars(select(Bay).order_by(Bay.active.desc(), Bay.sort_order, Bay.name)).all()
    return {"bays": [bay_out(ctx, b) for b in bays], "capabilities": CAPABILITIES, "shop_hours": ctx.shop.hours}


@command("bays.save", vertical="bays", description="Create or update a bay, including its own weekly hours.")
def bays_save(ctx: Ctx, args: BayIn) -> dict:
    unknown = [c for c in args.capabilities if c not in CAPABILITIES]
    if unknown:
        raise Invalid(f"Unknown capability: {', '.join(unknown)}. Known: {', '.join(CAPABILITIES)}.")
    data = args.model_dump(exclude={"id", "hours"})
    if args.id:
        bay = ctx.get(Bay, args.id, "Bay")
        for k, v in data.items():
            setattr(bay, k, v)
    else:
        bay = Bay(**data)
        ctx.tx.add(bay)
    ctx.tx.flush()
    if args.hours is not None:
        for h in args.hours:
            if not h.closed and (h.opens is None or h.closes is None or h.closes <= h.opens):
                raise Invalid("Each open day needs an opening time earlier than its closing time.")
        ctx.tx.execute(delete(BayHours).where(BayHours.bay_id == bay.id))
        ctx.tx.flush()
        for h in args.hours:
            ctx.tx.add(BayHours(bay_id=bay.id, **h.model_dump()))
        ctx.tx.flush()
    if args.id:
        _revalidate(ctx, bay.id)
    return bay_out(ctx, bay)


@command("bays.close", vertical="bays", description="Close a bay for a period (maintenance, equipment failure, reserved).")
def bays_close(ctx: Ctx, args: ClosureIn) -> dict:
    ctx.get(Bay, args.bay_id, "Bay")
    if args.end <= args.start:
        raise Invalid("The closure must end after it starts.")
    row = BayClosure(bay_id=args.bay_id, starts_at=ctx.local(args.start), ends_at=ctx.local(args.end), reason=args.reason)
    ctx.tx.add(row)
    ctx.tx.flush()
    affected = _revalidate(ctx, args.bay_id)
    return ctx.out(row, jobs_needing_reassignment=affected)


@command("bays.reopen", vertical="bays", description="Remove a bay closure.")
def bays_reopen(ctx: Ctx, args: IdArg) -> dict:
    ctx.tx.delete(ctx.get(BayClosure, args.id, "Closure"))
    return {"ok": True}


@command("bays.add_equipment", vertical="bays", description="Record a piece of equipment in a bay.")
def bays_add_equipment(ctx: Ctx, args: EquipmentIn) -> dict:
    ctx.get(Bay, args.bay_id, "Bay")
    row = BayEquipment(**args.model_dump())
    ctx.tx.add(row)
    ctx.tx.flush()
    return ctx.out(row)


@command("bays.remove_equipment", vertical="bays", description="Remove a piece of equipment from a bay.")
def bays_remove_equipment(ctx: Ctx, args: IdArg) -> dict:
    ctx.tx.delete(ctx.get(BayEquipment, args.id, "Equipment"))
    return {"ok": True}


def _revalidate(ctx: Ctx, bay_id: UUID) -> int:
    from shoppi.domain.schedule import revalidate

    return revalidate(ctx, bay_id=bay_id)


# ---- Live board -------------------------------------------------------------------


class BoardArgs(BaseModel):
    day: date | None = Field(default=None, description="Local date for utilisation figures; defaults to today.")


@query("bays.board", vertical="bays",
       description="Live status of every bay: free, occupied (vehicle, mechanic, expected finish), holding or closed; plus what is next and today's utilisation.")
def bays_board(ctx: Ctx, args: BoardArgs) -> list[dict]:
    now = ctx.now()
    day = args.day or now.astimezone(ctx.tz).date()
    day_start = datetime.combine(day, time.min, ctx.tz)
    day_end = day_start + timedelta(days=1)
    employees = {e.id: e.name for e in ctx.tx.scalars(select(Employee))}
    hours: dict[UUID, dict[int, BayHours]] = {}
    for h in ctx.tx.scalars(select(BayHours)):
        hours.setdefault(h.bay_id, {})[h.weekday] = h

    out = []
    for bay in ctx.tx.scalars(select(Bay).where(Bay.active).order_by(Bay.sort_order, Bay.name)):
        jobs = ctx.tx.scalars(
            select(JobAssignment).where(JobAssignment.bay_id == bay.id, JobAssignment.status.in_(ACTIVE),
                                        JobAssignment.ends_at > min(now, day_start), JobAssignment.starts_at < day_end + timedelta(days=7))
            .order_by(JobAssignment.starts_at)).all()
        closure = ctx.tx.scalar(select(BayClosure).where(BayClosure.bay_id == bay.id, BayClosure.starts_at <= now, BayClosure.ends_at > now))
        current = next((j for j in jobs if j.status in ("in_progress", "paused")), None) \
            or next((j for j in jobs if j.starts_at <= now < j.ends_at), None)
        upcoming = [j for j in jobs if j is not current and j.starts_at > now][:3]

        open_today = open_intervals(ctx.tz, ctx.shop.hours, hours.get(bay.id, {}), day, day)
        open_minutes = sum((o.end - o.start).total_seconds() / 60 for o in open_today)
        booked = 0.0
        for j in jobs:
            for o in open_today:
                s, e = max(j.starts_at, o.start), min(j.ends_at, o.end)
                if e > s:
                    booked += (e - s).total_seconds() / 60

        if closure:
            status = "closed"
        elif current is None:
            status = "free" if any(o.start <= now < o.end for o in open_today) or day != now.astimezone(ctx.tz).date() else "after_hours"
        elif current.employee_id is None:
            status = "holding"
        elif current.status == "paused":
            status = "paused"
        elif current.status == "in_progress":
            status = "occupied"
        else:
            status = "scheduled_now"  # booked for now but nobody has pressed Start

        out.append({
            "bay": ctx.out(bay), "status": status,
            "closure": ctx.out(closure) if closure else None,
            "current": _job_card(ctx, current, employees) if current else None,
            "overrunning": bool(current and current.status == "in_progress" and current.ends_at < now),
            "next": [_job_card(ctx, j, employees) for j in upcoming],
            "open_today": [{"start": to_local(o.start, ctx.tz), "end": to_local(o.end, ctx.tz)} for o in open_today],
            "utilisation": {"open_minutes": round(open_minutes), "booked_minutes": round(booked),
                            "percent": round(100 * booked / open_minutes) if open_minutes else 0},
        })
    return out


def _job_card(ctx: Ctx, j: JobAssignment, employees: dict[UUID, str]) -> dict:
    wo = ctx.tx.get(WorkOrder, j.work_order_id)
    vehicle = ctx.tx.get(Vehicle, wo.vehicle_id) if wo else None
    customer = ctx.tx.get(Customer, wo.customer_id) if wo else None
    from shoppi.domain.customers import vehicle_title

    return {
        "id": str(j.id), "work_order_id": str(j.work_order_id), "work_order_number": wo.number if wo else None,
        "label": j.label or "Job", "status": j.status, "is_hold": j.employee_id is None,
        "employee_id": str(j.employee_id) if j.employee_id else None, "employee_name": employees.get(j.employee_id),
        "vehicle": vehicle_title(vehicle) if vehicle else "", "plate": vehicle.plate if vehicle else None,
        "customer": customer.name if customer else "",
        "start": to_local(j.starts_at, ctx.tz), "end": to_local(j.ends_at, ctx.tz),
        "started_at": to_local(j.started_at, ctx.tz),
    }
