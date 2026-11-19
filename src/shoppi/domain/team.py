"""Team: employees, certifications, shifts, time off, and the service catalog
(which says what certifications each kind of job needs)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, NaiveDatetime
from sqlalchemy import delete, or_, select

from shoppi.core.commands import Conflict, Ctx, IdArg, Invalid, command, patch, query
from shoppi.db.models import (
    CertType, Employee, EmployeeCert, JobAssignment, Part, ServicePart, ServiceStep, ServiceType, Shift,
    StepCertRequirement, TimeOff, WorkOrderLine,
)

EXPIRY_WARNING_DAYS = 60

# ---- Certification types ----------------------------------------------------------


class CertTypeIn(BaseModel):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    legally_required: bool = False
    has_expiry: bool = True


@query("cert_types.list", vertical="team", description="All certification types (built-in and shop-specific).")
def cert_types_list(ctx: Ctx) -> list[dict]:
    rows = ctx.tx.scalars(select(CertType).order_by(CertType.category, CertType.code)).all()
    return [{"id": str(c.id), "code": c.code, "name": c.name, "category": c.category,
             "legally_required": c.legally_required, "has_expiry": c.has_expiry, "builtin": c.shop_id is None} for c in rows]


@command("cert_types.create", vertical="team", description="Add a shop-specific certification type.")
def cert_types_create(ctx: Ctx, args: CertTypeIn) -> dict:
    code = args.code.strip().upper()
    if ctx.tx.scalar(select(CertType.id).where(CertType.code == code)):
        raise Conflict("A certification with that code already exists.")
    row = CertType(shop_id=ctx.actor.shop_id, code=code, name=args.name.strip(), category="custom",
                   legally_required=args.legally_required, has_expiry=args.has_expiry)
    ctx.tx.add(row)
    ctx.tx.flush()
    return {"id": str(row.id), "code": row.code, "name": row.name}


# ---- Employees --------------------------------------------------------------------


class EmployeeIn(BaseModel):
    name: str = Field(min_length=1)
    kind: Literal["tech", "apprentice", "advisor", "owner"] = "tech"
    skill_level: int = Field(default=3, ge=1, le=5)
    can_supervise: bool = False
    phone: str | None = None
    email: str | None = None
    color: str | None = None


class EmployeePatch(BaseModel):
    id: UUID
    name: str | None = Field(default=None, min_length=1)
    kind: Literal["tech", "apprentice", "advisor", "owner"] | None = None
    skill_level: int | None = Field(default=None, ge=1, le=5)
    can_supervise: bool | None = None
    active: bool | None = None
    phone: str | None = None
    email: str | None = None
    color: str | None = None


class CertIn(BaseModel):
    employee_id: UUID
    cert_type_id: UUID
    number: str | None = None
    issued_on: date | None = None
    expires_on: date | None = None


class CertPatch(BaseModel):
    id: UUID
    number: str | None = None
    issued_on: date | None = None
    expires_on: date | None = None


def cert_out(ctx: Ctx, c: EmployeeCert, types: dict[UUID, CertType]) -> dict:
    today = datetime.now(ctx.tz).date()
    t = types.get(c.cert_type_id)
    status = "valid"
    if c.expires_on:
        if c.expires_on < today:
            status = "expired"
        elif c.expires_on <= today + timedelta(days=EXPIRY_WARNING_DAYS):
            status = "expiring"
    return ctx.out(c, code=t.code if t else "?", name=t.name if t else "?", legally_required=bool(t and t.legally_required),
                   status=status)


@query("employees.list", vertical="team", description="Employees with their certifications and expiry status.")
def employees_list(ctx: Ctx) -> list[dict]:
    types = {t.id: t for t in ctx.tx.scalars(select(CertType))}
    certs: dict[UUID, list] = {}
    for c in ctx.tx.scalars(select(EmployeeCert).order_by(EmployeeCert.expires_on.nulls_last())):
        certs.setdefault(c.employee_id, []).append(cert_out(ctx, c, types))
    return [ctx.out(e, certs=certs.get(e.id, [])) for e in ctx.tx.scalars(select(Employee).order_by(Employee.active.desc(), Employee.name))]


@command("employees.create", vertical="team", description="Add an employee.")
def employees_create(ctx: Ctx, args: EmployeeIn) -> dict:
    row = Employee(**args.model_dump())
    ctx.tx.add(row)
    ctx.tx.flush()
    return ctx.out(row)


@command("employees.update", vertical="team", description="Change an employee's details.")
def employees_update(ctx: Ctx, args: EmployeePatch) -> dict:
    row = ctx.get(Employee, args.id, "Employee")
    patch(row, args)
    ctx.tx.flush()
    if {"active", "kind", "can_supervise"} & args.model_fields_set:
        _revalidate(ctx, employee_id=row.id)
    return ctx.out(row)


@command("certs.add", vertical="team", description="Record a certification held by an employee.")
def certs_add(ctx: Ctx, args: CertIn) -> dict:
    ctx.get(Employee, args.employee_id, "Employee")
    cert_type = ctx.tx.get(CertType, args.cert_type_id)
    if cert_type is None:
        raise Invalid("Unknown certification type.")
    if cert_type.has_expiry and args.expires_on is None:
        raise Invalid(f"{cert_type.name} expires: enter its expiry date.")
    row = EmployeeCert(**args.model_dump())
    ctx.tx.add(row)
    ctx.tx.flush()
    _revalidate(ctx, employee_id=args.employee_id)
    return ctx.out(row)


@command("certs.update", vertical="team", description="Update a certification (e.g. after renewal).")
def certs_update(ctx: Ctx, args: CertPatch) -> dict:
    row = ctx.get(EmployeeCert, args.id, "Certification")
    patch(row, args)
    ctx.tx.flush()
    _revalidate(ctx, employee_id=row.employee_id)
    return ctx.out(row)


@command("certs.remove", vertical="team", description="Remove a certification from an employee.")
def certs_remove(ctx: Ctx, args: IdArg) -> dict:
    row = ctx.get(EmployeeCert, args.id, "Certification")
    employee_id = row.employee_id
    ctx.tx.delete(row)
    ctx.tx.flush()
    _revalidate(ctx, employee_id=employee_id)
    return {"ok": True}


def _revalidate(ctx: Ctx, **kw) -> None:
    from shoppi.domain.schedule import revalidate

    revalidate(ctx, **kw)


# ---- Shifts and time off ----------------------------------------------------------


class Window(BaseModel):
    start: NaiveDatetime
    end: NaiveDatetime
    employee_id: UUID | None = None


class ShiftIn(BaseModel):
    employee_id: UUID
    start: NaiveDatetime = Field(description="Shift start, shop-local time without offset.")
    end: NaiveDatetime
    note: str | None = None
    repeat_weekdays: list[int] | None = Field(default=None, description="Repeat on these weekdays (0=Mon..6=Sun).")
    repeat_until: date | None = Field(default=None, description="Last date to repeat on.")


class ShiftPatch(BaseModel):
    id: UUID
    start: NaiveDatetime | None = None
    end: NaiveDatetime | None = None
    employee_id: UUID | None = None
    note: str | None = None


class ShiftDelete(BaseModel):
    id: UUID
    whole_series: bool = False


class TimeOffIn(BaseModel):
    employee_id: UUID
    start: NaiveDatetime
    end: NaiveDatetime
    reason: str | None = None


def employee_names(ctx: Ctx) -> dict[UUID, str]:
    return {e.id: e.name for e in ctx.tx.scalars(select(Employee))}


@query("shifts.list", vertical="shifts", description="Shifts and time off in a date range.")
def shifts_list(ctx: Ctx, args: Window) -> dict:
    start, end = ctx.local(args.start), ctx.local(args.end)
    names = employee_names(ctx)
    sq = select(Shift).where(Shift.starts_at < end, Shift.ends_at > start).order_by(Shift.starts_at)
    tq = select(TimeOff).where(TimeOff.starts_at < end, TimeOff.ends_at > start).order_by(TimeOff.starts_at)
    if args.employee_id:
        sq, tq = sq.where(Shift.employee_id == args.employee_id), tq.where(TimeOff.employee_id == args.employee_id)
    return {
        "shifts": [ctx.out(s, employee_name=names.get(s.employee_id, "")) for s in ctx.tx.scalars(sq)],
        "time_off": [ctx.out(t, employee_name=names.get(t.employee_id, "")) for t in ctx.tx.scalars(tq)],
    }


def _overlapping_shift(ctx: Ctx, employee_id: UUID, start: datetime, end: datetime, ignore: UUID | None = None) -> bool:
    q = select(Shift.id).where(Shift.employee_id == employee_id, Shift.starts_at < end, Shift.ends_at > start)
    if ignore:
        q = q.where(Shift.id != ignore)
    return ctx.tx.scalar(q) is not None


@command("shifts.create", vertical="shifts", description="Schedule a shift, optionally repeating weekly.")
def shifts_create(ctx: Ctx, args: ShiftIn) -> dict:
    ctx.get(Employee, args.employee_id, "Employee")
    if args.end <= args.start:
        raise Invalid("The shift must end after it starts.")
    if args.end - args.start > timedelta(hours=16):
        raise Invalid("A shift can't be longer than 16 hours.")
    days = [args.start.date()]
    series_id = None
    if args.repeat_weekdays and args.repeat_until:
        if args.repeat_until > args.start.date() + timedelta(days=370):
            raise Invalid("Repeat for at most one year at a time.")
        series_id = uuid4()
        days, d = [], args.start.date()
        while d <= args.repeat_until:
            if d.weekday() in args.repeat_weekdays:
                days.append(d)
            d += timedelta(days=1)
    length = args.end - args.start
    created, skipped = [], 0
    for d in days:
        # Build each day from wall-clock time so a shift stays 8:00-17:00 across a daylight-saving change.
        start = ctx.local(datetime.combine(d, args.start.time()))
        end = ctx.local(datetime.combine(d, args.start.time()) + length)
        if _overlapping_shift(ctx, args.employee_id, start, end):
            skipped += 1
            continue
        row = Shift(employee_id=args.employee_id, starts_at=start, ends_at=end, note=args.note, series_id=series_id)
        ctx.tx.add(row)
        created.append(row)
    if not created:
        raise Conflict("That employee already has a shift at that time.")
    ctx.tx.flush()
    return {"created": len(created), "skipped_overlapping": skipped, "shifts": [ctx.out(r) for r in created[:5]]}


@command("shifts.update", vertical="shifts", description="Change a shift's time or who works it.")
def shifts_update(ctx: Ctx, args: ShiftPatch) -> dict:
    row = ctx.get(Shift, args.id, "Shift")
    old_employee = row.employee_id
    if args.start:
        row.starts_at = ctx.local(args.start)
    if args.end:
        row.ends_at = ctx.local(args.end)
    if args.employee_id:
        ctx.get(Employee, args.employee_id, "Employee")
        row.employee_id = args.employee_id
    if "note" in args.model_fields_set:
        row.note = args.note
    if row.ends_at <= row.starts_at:
        raise Invalid("The shift must end after it starts.")
    if _overlapping_shift(ctx, row.employee_id, row.starts_at, row.ends_at, ignore=row.id):
        raise Conflict("That employee already has a shift at that time.")
    ctx.tx.flush()
    _revalidate(ctx, employee_id=row.employee_id)
    if old_employee != row.employee_id:
        _revalidate(ctx, employee_id=old_employee)
    return ctx.out(row)


@command("shifts.delete", vertical="shifts", description="Remove a shift (or the rest of a repeating series).")
def shifts_delete(ctx: Ctx, args: ShiftDelete) -> dict:
    row = ctx.get(Shift, args.id, "Shift")
    employee_id = row.employee_id
    if args.whole_series and row.series_id:
        rows = ctx.tx.scalars(select(Shift).where(Shift.series_id == row.series_id, Shift.starts_at >= row.starts_at)).all()
    else:
        rows = [row]
    for r in rows:
        ctx.tx.delete(r)
    ctx.tx.flush()
    _revalidate(ctx, employee_id=employee_id)
    return {"deleted": len(rows)}


@command("time_off.create", vertical="shifts", description="Record time off for an employee.")
def time_off_create(ctx: Ctx, args: TimeOffIn) -> dict:
    ctx.get(Employee, args.employee_id, "Employee")
    if args.end <= args.start:
        raise Invalid("Time off must end after it starts.")
    row = TimeOff(employee_id=args.employee_id, starts_at=ctx.local(args.start), ends_at=ctx.local(args.end), reason=args.reason)
    ctx.tx.add(row)
    ctx.tx.flush()
    _revalidate(ctx, employee_id=args.employee_id)
    return ctx.out(row)


@command("time_off.delete", vertical="shifts", description="Remove a time-off entry.")
def time_off_delete(ctx: Ctx, args: IdArg) -> dict:
    row = ctx.get(TimeOff, args.id, "Time off")
    ctx.tx.delete(row)
    return {"ok": True}


# ---- Service catalog --------------------------------------------------------------


class CertReqIn(BaseModel):
    cert_type_id: UUID
    group_no: int = Field(default=1, ge=1, description="Requirements with the same group number are alternatives.")
    applies_powertrain: Literal["gas", "diesel", "hybrid", "ev"] | None = None
    applies_min_class: int | None = Field(default=None, ge=1, le=8)


class StepIn(BaseModel):
    name: str = Field(min_length=1)
    minutes: int = Field(default=60, ge=5, le=24 * 60)
    hands_on: bool = True
    needs_bay: bool = True
    bay_capability: str | None = None
    holds_bay: bool = True
    same_mechanic_as_prev: bool = False
    requirements: list[CertReqIn] = []


class ServicePartIn(BaseModel):
    part_id: UUID
    qty: int = Field(default=1, ge=1)


class ServiceIn(BaseModel):
    id: UUID | None = None
    name: str = Field(min_length=1)
    description: str | None = None
    price: float | None = Field(default=None, ge=0)
    active: bool = True
    # True = offer this service as "Diagnosis" during intake (replaces any other); omit to leave unchanged.
    is_diagnosis: bool | None = None
    steps: list[StepIn] = Field(min_length=1)
    parts: list[ServicePartIn] = []


def ensure_diagnosis_service(ctx: Ctx) -> ServiceType:
    """The shop's Diagnosis service, created with sensible defaults the first time it
    is needed. It is an ordinary catalog service: owners can change its time, price
    and requirements under Settings -> Services."""
    svc = ctx.tx.scalar(select(ServiceType).where(ServiceType.is_diagnosis).order_by(ServiceType.active.desc(), ServiceType.created_at))
    if svc is None:
        svc = ServiceType(name="Diagnosis", description="Find the cause of the customer's concern before quoting a repair.", is_diagnosis=True)
        ctx.tx.add(svc)
        ctx.tx.flush()
        ctx.tx.add(ServiceStep(service_type_id=svc.id, seq=1, name="Diagnosis", minutes=60))
        ctx.tx.flush()
    return svc


def service_out(ctx: Ctx, s: ServiceType) -> dict:
    types = {t.id: t for t in ctx.tx.scalars(select(CertType))}
    steps = []
    for st in ctx.tx.scalars(select(ServiceStep).where(ServiceStep.service_type_id == s.id).order_by(ServiceStep.seq)):
        reqs = [{"cert_type_id": str(r.cert_type_id), "group_no": r.group_no, "applies_powertrain": r.applies_powertrain,
                 "applies_min_class": r.applies_min_class, "code": types[r.cert_type_id].code if r.cert_type_id in types else "?",
                 "name": types[r.cert_type_id].name if r.cert_type_id in types else "?"}
                for r in ctx.tx.scalars(select(StepCertRequirement).where(StepCertRequirement.step_id == st.id)
                                        .order_by(StepCertRequirement.group_no))]
        steps.append(ctx.out(st, requirements=reqs))
    parts = [{"part_id": str(sp.part_id), "qty": sp.qty, "sku": p.sku, "name": p.name}
             for sp, p in ctx.tx.execute(select(ServicePart, Part).join(Part, Part.id == ServicePart.part_id)
                                         .where(ServicePart.service_type_id == s.id)).all()]
    return ctx.out(s, steps=steps, parts=parts, minutes=sum(x["minutes"] for x in steps))


@query("services.list", vertical="services", description="The service catalog: steps, durations, required certifications and parts.")
def services_list(ctx: Ctx) -> list[dict]:
    return [service_out(ctx, s) for s in ctx.tx.scalars(select(ServiceType).order_by(ServiceType.active.desc(), ServiceType.name))]


@command("services.save", vertical="services", agent=False,
         description="Create or replace a service: its steps, certification requirements and parts.")
def services_save(ctx: Ctx, args: ServiceIn) -> dict:
    if args.id:
        svc = ctx.get(ServiceType, args.id, "Service")
        svc.name, svc.description, svc.price, svc.active = args.name, args.description, args.price, args.active
    else:
        svc = ServiceType(name=args.name, description=args.description, price=args.price, active=args.active)
        ctx.tx.add(svc)
    ctx.tx.flush()
    if args.is_diagnosis is not None:
        if args.is_diagnosis:  # only one service per shop is the Diagnosis option
            for other in ctx.tx.scalars(select(ServiceType).where(ServiceType.is_diagnosis, ServiceType.id != svc.id)):
                other.is_diagnosis = False
        svc.is_diagnosis = args.is_diagnosis
        ctx.tx.flush()

    existing = ctx.tx.scalars(select(ServiceStep).where(ServiceStep.service_type_id == svc.id).order_by(ServiceStep.seq)).all()
    # Steps already referenced by scheduled jobs are updated in place, by position, never deleted.
    for i, step_in in enumerate(args.steps):
        data = step_in.model_dump(exclude={"requirements"})
        if i < len(existing):
            step = existing[i]
            for k, v in data.items():
                setattr(step, k, v)
            step.seq = i + 1
        else:
            step = ServiceStep(service_type_id=svc.id, seq=i + 1, **data)
            ctx.tx.add(step)
        ctx.tx.flush()
        ctx.tx.execute(delete(StepCertRequirement).where(StepCertRequirement.step_id == step.id))
        for r in step_in.requirements:
            if ctx.tx.get(CertType, r.cert_type_id) is None:
                raise Invalid("Unknown certification type.")
            ctx.tx.add(StepCertRequirement(step_id=step.id, **r.model_dump()))
    for step in existing[len(args.steps):]:
        in_use = ctx.tx.scalar(select(JobAssignment.id).where(JobAssignment.step_id == step.id).limit(1))
        if in_use:
            raise Conflict(f"Step “{step.name}” is used by scheduled jobs and can't be removed.")
        ctx.tx.execute(delete(StepCertRequirement).where(StepCertRequirement.step_id == step.id))
        ctx.tx.delete(step)

    ctx.tx.execute(delete(ServicePart).where(ServicePart.service_type_id == svc.id))
    for p in args.parts:
        ctx.get(Part, p.part_id, "Part")
        ctx.tx.add(ServicePart(service_type_id=svc.id, part_id=p.part_id, qty=p.qty))
    ctx.tx.flush()
    return service_out(ctx, svc)


@command("services.ensure_diagnosis", vertical="services", agent=False,
         description="Make sure the shop has a Diagnosis service to offer during intake.")
def services_ensure_diagnosis(ctx: Ctx) -> dict:
    return service_out(ctx, ensure_diagnosis_service(ctx))


class ArchiveArg(BaseModel):
    id: UUID
    archived: bool = True


@command("services.archive", vertical="services", agent=False, description="Hide a service from new work orders (or restore it).")
def services_archive(ctx: Ctx, args: ArchiveArg) -> dict:
    svc = ctx.get(ServiceType, args.id, "Service")
    svc.active = not args.archived
    return {"ok": True}


def default_open_time(ctx: Ctx, weekday: int) -> tuple[time, time] | None:
    pair = (ctx.shop.hours or {}).get(str(weekday))
    return (time.fromisoformat(pair[0]), time.fromisoformat(pair[1])) if pair else None


__all__ = ["employee_names", "service_out", "WorkOrderLine", "or_"]
