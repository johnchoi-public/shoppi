"""Work orders: what is being done to which vehicle, and its status."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, NaiveDatetime
from sqlalchemy import func, or_, select

from shoppi.core.commands import Conflict, Ctx, IdArg, Invalid, command, patch, query
from shoppi.core.timeutil import to_local
from shoppi.db.models import (
    Bay, Customer, Document, Employee, IntakeSubmission, JobAssignment, Part, PartReservation, ServiceType,
    StockMovement, Vehicle, WorkOrder, WorkOrderLine,
)
from shoppi.domain.customers import vehicle_title
from shoppi.scheduling.service import ACTIVE, parts_status

OPEN_STATUSES = ("draft", "scheduled", "in_progress", "waiting_parts")
Status = Literal["draft", "scheduled", "in_progress", "waiting_parts", "completed", "cancelled"]


class CustomLine(BaseModel):
    description: str = Field(min_length=1)
    price: float | None = Field(default=None, ge=0)


class WorkOrderIn(BaseModel):
    customer_id: UUID
    vehicle_id: UUID
    concern: str | None = Field(default=None, description="What the customer reports / wants done.")
    notes: str | None = None
    service_type_ids: list[UUID] = Field(default=[], description="Services from the catalog to perform.")
    custom_lines: list[CustomLine] = []
    promised_at: NaiveDatetime | None = None
    preferred_employee_id: UUID | None = None
    mileage_in: int | None = Field(default=None, ge=0)


class WorkOrderPatch(BaseModel):
    id: UUID
    concern: str | None = None
    notes: str | None = None
    promised_at: NaiveDatetime | None = None
    preferred_employee_id: UUID | None = None
    mileage_in: int | None = None


class LineIn(BaseModel):
    work_order_id: UUID
    service_type_id: UUID | None = None
    description: str | None = None
    price: float | None = Field(default=None, ge=0)


class StatusIn(BaseModel):
    id: UUID
    status: Status


class ListArgs(BaseModel):
    status: str | None = Field(default=None, description="A status, or 'open' for everything not completed/cancelled.")
    q: str | None = None
    customer_id: UUID | None = None
    limit: int = Field(default=100, le=500)


def next_number(ctx: Ctx, model) -> int:
    return int(ctx.tx.scalar(select(func.coalesce(func.max(model.number), 1000))) or 1000) + 1


def add_service_line(ctx: Ctx, wo: WorkOrder, service_type_id: UUID) -> WorkOrderLine:
    svc = ctx.get(ServiceType, service_type_id, "Service")
    line = WorkOrderLine(work_order_id=wo.id, service_type_id=svc.id, description=svc.name, price=svc.price)
    ctx.tx.add(line)
    return line


def create_work_order_row(ctx: Ctx, args: WorkOrderIn) -> WorkOrder:
    ctx.get(Customer, args.customer_id, "Customer")
    vehicle = ctx.get(Vehicle, args.vehicle_id, "Vehicle")
    if vehicle.customer_id != args.customer_id:
        raise Invalid("That vehicle belongs to a different customer.")
    wo = WorkOrder(number=next_number(ctx, WorkOrder), customer_id=args.customer_id, vehicle_id=args.vehicle_id,
                   concern=args.concern, notes=args.notes, promised_at=ctx.local(args.promised_at),
                   preferred_employee_id=args.preferred_employee_id, mileage_in=args.mileage_in)
    ctx.tx.add(wo)
    ctx.tx.flush()
    for sid in args.service_type_ids:
        add_service_line(ctx, wo, sid)
    for line in args.custom_lines:
        ctx.tx.add(WorkOrderLine(work_order_id=wo.id, description=line.description, price=line.price))
    if args.mileage_in:
        vehicle.mileage = max(vehicle.mileage or 0, args.mileage_in)
    ctx.tx.flush()
    return wo


def work_order_summary(ctx: Ctx, wo: WorkOrder, customers=None, vehicles=None) -> dict:
    customer = (customers or {}).get(wo.customer_id) or ctx.tx.get(Customer, wo.customer_id)
    vehicle = (vehicles or {}).get(wo.vehicle_id) or ctx.tx.get(Vehicle, wo.vehicle_id)
    window = ctx.tx.execute(select(func.min(JobAssignment.starts_at), func.max(JobAssignment.ends_at)).where(
        JobAssignment.work_order_id == wo.id, JobAssignment.status.in_((*ACTIVE, "done")))).one()
    problems = ctx.tx.scalar(select(func.count()).select_from(JobAssignment).where(
        JobAssignment.work_order_id == wo.id, JobAssignment.status == "needs_reassignment")) or 0
    return ctx.out(wo, customer_name=customer.name if customer else "", vehicle_title=vehicle_title(vehicle) if vehicle else "",
                   plate=vehicle.plate if vehicle else None,
                   scheduled_start=to_local(window[0], ctx.tz), scheduled_end=to_local(window[1], ctx.tz),
                   needs_reassignment=problems)


@query("work_orders.list", vertical="work_orders", description="Work orders, optionally filtered by status ('open' = not finished) or search text.")
def work_orders_list(ctx: Ctx, args: ListArgs) -> list[dict]:
    q = select(WorkOrder).order_by(WorkOrder.number.desc()).limit(args.limit)
    if args.status == "open":
        q = q.where(WorkOrder.status.in_(OPEN_STATUSES))
    elif args.status:
        q = q.where(WorkOrder.status == args.status)
    if args.customer_id:
        q = q.where(WorkOrder.customer_id == args.customer_id)
    if args.q:
        like = f"%{args.q.strip()}%"
        conds = [WorkOrder.concern.ilike(like),
                 WorkOrder.customer_id.in_(select(Customer.id).where(Customer.name.ilike(like))),
                 WorkOrder.vehicle_id.in_(select(Vehicle.id).where(or_(Vehicle.plate.ilike(like), Vehicle.make.ilike(like), Vehicle.model.ilike(like), Vehicle.vin.ilike(like))))]
        if args.q.strip().lstrip("#").isdigit():
            conds.append(WorkOrder.number == int(args.q.strip().lstrip("#")))
        q = q.where(or_(*conds))
    rows = ctx.tx.scalars(q).all()
    customers = {c.id: c for c in ctx.tx.scalars(select(Customer).where(Customer.id.in_({w.customer_id for w in rows})))}
    vehicles = {v.id: v for v in ctx.tx.scalars(select(Vehicle).where(Vehicle.id.in_({w.vehicle_id for w in rows})))}
    return [work_order_summary(ctx, w, customers, vehicles) for w in rows]


def assignment_out(ctx: Ctx, a: JobAssignment, names: dict[UUID, str]) -> dict:
    return ctx.out(a, employee_name=names.get(a.employee_id), bay_name=names.get(a.bay_id),
                   supervisor_name=names.get(a.supervisor_id), is_hold=a.employee_id is None and a.step_id is None and a.line_id is None)


@query("work_orders.get", vertical="work_orders", description="One work order with its lines, scheduled jobs and parts status.")
def work_orders_get(ctx: Ctx, args: IdArg) -> dict:
    wo = ctx.get(WorkOrder, args.id, "Work order")
    names = {e.id: e.name for e in ctx.tx.scalars(select(Employee))}
    names.update({b.id: b.name for b in ctx.tx.scalars(select(Bay))})
    lines = ctx.tx.scalars(select(WorkOrderLine).where(WorkOrderLine.work_order_id == wo.id).order_by(WorkOrderLine.created_at)).all()
    jobs = ctx.tx.scalars(select(JobAssignment).where(JobAssignment.work_order_id == wo.id, JobAssignment.status != "cancelled")
                          .order_by(JobAssignment.starts_at)).all()
    parts = parts_status(ctx.tx, wo)
    vehicle = ctx.tx.get(Vehicle, wo.vehicle_id)
    customer = ctx.tx.get(Customer, wo.customer_id)
    out = work_order_summary(ctx, wo)
    out.update(
        lines=[ctx.out(line) for line in lines],
        jobs=[assignment_out(ctx, a, names) for a in jobs],
        parts={"ready_at": to_local(parts["ready_at"], ctx.tz), "missing": parts["missing"],
               "lines": [{**p, "eta": to_local(p["eta"], ctx.tz)} for p in parts["lines"]]},
        vehicle=ctx.out(vehicle, title=vehicle_title(vehicle)) if vehicle else None,
        customer=ctx.out(customer) if customer else None,
        total=sum(float(line.price or 0) for line in lines if line.status != "declined"),
    )
    return out


@command("work_orders.create", vertical="work_orders", description="Open a work order for a customer's vehicle.")
def work_orders_create(ctx: Ctx, args: WorkOrderIn) -> dict:
    return work_order_summary(ctx, create_work_order_row(ctx, args))


@command("work_orders.update", vertical="work_orders", description="Change a work order's concern, notes, promise time or preferred mechanic.")
def work_orders_update(ctx: Ctx, args: WorkOrderPatch) -> dict:
    wo = ctx.get(WorkOrder, args.id, "Work order")
    patch(wo, args, skip=("id", "promised_at"))
    if "promised_at" in args.model_fields_set:
        wo.promised_at = ctx.local(args.promised_at)
    ctx.tx.flush()
    return work_order_summary(ctx, wo)


@command("work_orders.add_line", vertical="work_orders", description="Add a catalog service or a custom line to a work order.")
def work_orders_add_line(ctx: Ctx, args: LineIn) -> dict:
    wo = ctx.get(WorkOrder, args.work_order_id, "Work order")
    if wo.status in ("completed", "cancelled"):
        raise Conflict(f"This work order is {wo.status}.")
    if args.service_type_id:
        line = add_service_line(ctx, wo, args.service_type_id)
        if args.price is not None:
            line.price = args.price
    elif args.description:
        line = WorkOrderLine(work_order_id=wo.id, description=args.description, price=args.price)
        ctx.tx.add(line)
    else:
        raise Invalid("Choose a service or enter a description.")
    ctx.tx.flush()
    return ctx.out(line)


@command("work_orders.remove_line", vertical="work_orders", description="Remove a line that has not been scheduled or started.")
def work_orders_remove_line(ctx: Ctx, args: IdArg) -> dict:
    line = ctx.get(WorkOrderLine, args.id, "Line")
    if ctx.tx.scalar(select(JobAssignment.id).where(JobAssignment.line_id == line.id, JobAssignment.status.in_((*ACTIVE, "done"))).limit(1)):
        raise Conflict("This line is already scheduled. Unschedule the work order first.")
    ctx.tx.delete(line)
    return {"ok": True}


@command("work_orders.set_status", vertical="work_orders",
         description="Change a work order's status. Completing it uses up its reserved parts; cancelling frees its schedule and parts.")
def work_orders_set_status(ctx: Ctx, args: StatusIn) -> dict:
    wo = ctx.get(WorkOrder, args.id, "Work order")
    if wo.status == args.status:
        return work_order_summary(ctx, wo)
    if wo.status in ("completed", "cancelled") and args.status not in ("draft",):
        raise Conflict(f"This work order is {wo.status}. Reopen it as a draft first.")
    jobs = ctx.tx.scalars(select(JobAssignment).where(JobAssignment.work_order_id == wo.id)).all()
    reservations = ctx.tx.scalars(select(PartReservation).where(
        PartReservation.work_order_id == wo.id, PartReservation.status == "reserved")).all()

    if args.status == "completed":
        if any(j.status in ("in_progress", "paused") for j in jobs):
            raise Conflict("A job on this work order is still in progress. Finish it first.")
        for r in reservations:
            part = ctx.tx.get(Part, r.part_id)
            if part.on_hand < r.qty:
                raise Conflict(f"Not enough {part.name} on hand to complete this work order.")
            part.on_hand -= r.qty
            r.status = "used"
            ctx.tx.add(StockMovement(part_id=part.id, delta=-r.qty, reason="use", work_order_id=wo.id))
        for j in jobs:
            if j.status in ("planned", "needs_reassignment"):
                j.status = "cancelled"
        for line in ctx.tx.scalars(select(WorkOrderLine).where(WorkOrderLine.work_order_id == wo.id, WorkOrderLine.status == "open")):
            line.status = "done"
    elif args.status == "cancelled":
        for j in jobs:
            if j.status in (*ACTIVE, "needs_reassignment"):
                j.status = "cancelled"
        for r in reservations:
            r.status = "released"
    elif args.status == "draft":
        for j in jobs:
            if j.status in ("planned", "needs_reassignment"):
                j.status = "cancelled"
    wo.status = args.status
    ctx.tx.flush()
    return work_order_summary(ctx, wo)


def delete_work_order_row(ctx: Ctx, wo: WorkOrder) -> None:
    """Permanently remove a work order with its lines, scheduled jobs and part
    reservations. Each removed row is written to the history by the audit trigger.

    Kept, but unlinked: stock movements (parts already used stay deducted, so the
    stock ledger still adds up), attached documents, and the intake submission it
    came from."""
    busy = ctx.tx.scalar(select(func.count()).select_from(JobAssignment).where(
        JobAssignment.work_order_id == wo.id, JobAssignment.status.in_(("in_progress", "paused"))))
    if busy:
        raise Conflict(f"Work is under way on work order #{wo.number}. Finish the job or cancel the work order first.")
    for move in ctx.tx.scalars(select(StockMovement).where(StockMovement.work_order_id == wo.id)):
        move.work_order_id = None
        move.note = " ".join(x for x in (move.note, f"(work order #{wo.number}, deleted)") if x)
    for doc in ctx.tx.scalars(select(Document).where(Document.work_order_id == wo.id)):
        doc.work_order_id = None
    for sub in ctx.tx.scalars(select(IntakeSubmission).where(IntakeSubmission.work_order_id == wo.id)):
        sub.work_order_id = None
    ctx.tx.flush()
    # Lines, jobs, bay holds and reservations go with it (ON DELETE CASCADE), which
    # frees the mechanics, bays and reserved parts.
    ctx.tx.delete(wo)
    ctx.tx.flush()


@command("work_orders.delete", vertical="work_orders", managers_only=True, agent=False,
         description="Permanently delete a work order, its schedule and its part reservations. Owners and advisors only.")
def work_orders_delete(ctx: Ctx, args: IdArg) -> dict:
    wo = ctx.get(WorkOrder, args.id, "Work order")
    number = wo.number
    delete_work_order_row(ctx, wo)
    from shoppi.domain.schedule import _notify_calendar_changed

    _notify_calendar_changed(ctx)
    return {"deleted": True, "number": number}
