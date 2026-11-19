"""Customer intake.

Two ways in, one review queue:
- staff run the intake wizard at the counter (`intake.staff_create`);
- a customer fills in the public form from a kiosk or a link (`intake.submit`).
  That only ever creates a *pending submission*. It reads no shop data and
  changes no customer records until a member of staff accepts it.
Drafts extracted from phone calls land in the same queue.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from shoppi.auth.service import new_token, token_hash
from shoppi.config import get_settings
from shoppi.core.commands import Conflict, Ctx, IdArg, Invalid, command, query
from shoppi.core.timeutil import normalize_phone
from shoppi.db.models import Call, Customer, IntakeLink, IntakeSubmission, Vehicle
from shoppi.domain.customers import CustomerIn, VehicleFields, clean_vin, create_customer_row, create_vehicle_row, vehicle_title
from shoppi.domain.work_orders import CustomLine, WorkOrderIn, create_work_order_row, work_order_summary

# ---- Links ------------------------------------------------------------------------


class LinkIn(BaseModel):
    label: str = Field(default="", max_length=80)
    kind: Literal["kiosk", "single"] = "kiosk"
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


def link_out(ctx: Ctx, link: IntakeLink) -> dict:
    now = ctx.now()
    active = link.revoked_at is None and (link.expires_at is None or link.expires_at > now) \
        and (link.max_uses is None or link.uses < link.max_uses)
    return ctx.out(link, active=active)


@query("intake_links.list", vertical="intake", agent=False)
def intake_links_list(ctx: Ctx) -> list[dict]:
    """Customer intake links (kiosk and single-use)."""
    return [link_out(ctx, link) for link in ctx.tx.scalars(select(IntakeLink).order_by(IntakeLink.created_at.desc()))]


@command("intake_links.create", vertical="intake", agent=False,
         description="Create a customer intake link: a reusable kiosk link or a single-use link to send to one customer.")
def intake_links_create(ctx: Ctx, args: LinkIn) -> dict:
    token = new_token()
    link = IntakeLink(label=args.label or ("Front desk kiosk" if args.kind == "kiosk" else "Customer link"), kind=args.kind,
                      token_hash=token_hash(token), max_uses=1 if args.kind == "single" else None,
                      expires_at=ctx.now() + timedelta(days=args.expires_in_days) if args.expires_in_days else None)
    ctx.tx.add(link)
    ctx.tx.flush()
    # Shown once: only the hash is stored.
    return {**link_out(ctx, link), "url": f"{get_settings().base_url}/i/{token}"}


@command("intake_links.revoke", vertical="intake", agent=False, description="Stop an intake link from working.")
def intake_links_revoke(ctx: Ctx, args: IdArg) -> dict:
    ctx.get(IntakeLink, args.id, "Intake link").revoked_at = ctx.now()
    return {"ok": True}


# ---- Public submission ------------------------------------------------------------


class PublicCustomer(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=200)
    preferred_contact: Literal["phone", "text", "email"] | None = None


class PublicVehicle(BaseModel):
    vin: str | None = Field(default=None, max_length=17)
    plate: str | None = Field(default=None, max_length=15)
    year: int | None = Field(default=None, ge=1900, le=2100)
    make: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    powertrain: Literal["gas", "diesel", "hybrid", "ev"] | None = None
    gvwr_class: int | None = Field(default=None, ge=1, le=8)
    mileage: int | None = Field(default=None, ge=0, le=5_000_000)


class PublicIntake(BaseModel):
    customer: PublicCustomer
    vehicle: PublicVehicle
    concern: str = Field(min_length=1, max_length=4000)
    preferred_times: str | None = Field(default=None, max_length=500)
    drop_off: bool | None = None
    consent: bool = Field(description="Customer agreed to be contacted about this request.")


@command("intake.submit", vertical="public_intake", agent=False, description="Customer submits the intake form.")
def intake_submit(ctx: Ctx, args: PublicIntake) -> dict:
    if not args.consent:
        raise Invalid("Please confirm we may contact you about this request.")
    if not (args.customer.phone or args.customer.email):
        raise Invalid("Please give a phone number or an email so we can reach you.")
    link = ctx.get(IntakeLink, UUID(ctx.actor.ref), "Intake link") if ctx.actor.ref else None
    if link is not None:
        link.uses += 1
    payload = args.model_dump(mode="json")
    payload["vehicle"]["vin"] = clean_vin(payload["vehicle"].get("vin"))
    row = IntakeSubmission(source="link", link_id=link.id if link else None, payload=payload)
    ctx.tx.add(row)
    ctx.tx.flush()
    return {"received": True, "reference": str(row.id)[:8].upper()}


# ---- Review queue -----------------------------------------------------------------


class QueueFilter(BaseModel):
    status: str | None = "pending"


def _matches(ctx: Ctx, payload: dict) -> dict:
    """Existing records this submission probably refers to (same phone/email, VIN or plate)."""
    cust, veh = payload.get("customer") or {}, payload.get("vehicle") or {}
    conds = []
    phone = normalize_phone(cust.get("phone"))
    if phone:
        conds.append(Customer.phone_norm == phone)
    if cust.get("email"):
        conds.append(Customer.email.ilike(cust["email"].strip()))
    customers = ctx.tx.scalars(select(Customer).where(or_(*conds)).limit(5)).all() if conds else []
    vconds = []
    if veh.get("vin"):
        vconds.append(Vehicle.vin == clean_vin(veh["vin"]))
    if veh.get("plate"):
        vconds.append(Vehicle.plate.ilike(veh["plate"].strip()))
    vehicles = ctx.tx.scalars(select(Vehicle).where(or_(*vconds)).limit(5)).all() if vconds else []
    return {"customers": [ctx.out(c) for c in customers],
            "vehicles": [ctx.out(v, title=vehicle_title(v)) for v in vehicles]}


@query("intake.list", vertical="intake", description="The intake review queue: customer submissions and call drafts waiting for staff.")
def intake_list(ctx: Ctx, args: QueueFilter) -> list[dict]:
    q = select(IntakeSubmission).order_by(IntakeSubmission.created_at.desc()).limit(200)
    if args.status:
        q = q.where(IntakeSubmission.status == args.status)
    return [ctx.out(s) for s in ctx.tx.scalars(q)]


@query("intake.get", vertical="intake", description="One intake submission, with existing customers and vehicles it may match.")
def intake_get(ctx: Ctx, args: IdArg) -> dict:
    s = ctx.get(IntakeSubmission, args.id, "Submission")
    return ctx.out(s, matches=_matches(ctx, s.payload))


class IntakeData(BaseModel):
    """What the staff wizard collects. Either pick an existing record or enter a new one."""

    customer_id: UUID | None = None
    customer: CustomerIn | None = None
    vehicle_id: UUID | None = None
    vehicle: VehicleFields | None = None
    concern: str | None = None
    notes: str | None = None
    service_type_ids: list[UUID] = []
    custom_lines: list[CustomLine] = []
    mileage_in: int | None = Field(default=None, ge=0)
    preferred_employee_id: UUID | None = None


class AcceptIn(IntakeData):
    id: UUID


def _materialize(ctx: Ctx, data: IntakeData) -> dict:
    if data.customer_id:
        customer = ctx.get(Customer, data.customer_id, "Customer")
    elif data.customer:
        customer = create_customer_row(ctx, data.customer)
    else:
        raise Invalid("Choose an existing customer or enter a new one.")
    if data.vehicle_id:
        vehicle = ctx.get(Vehicle, data.vehicle_id, "Vehicle")
        if vehicle.customer_id != customer.id:
            raise Invalid("That vehicle belongs to a different customer.")
    elif data.vehicle:
        vehicle = create_vehicle_row(ctx, customer.id, data.vehicle)
    else:
        raise Invalid("Choose an existing vehicle or enter a new one.")
    if not data.service_type_ids and not data.custom_lines and not (data.concern or "").strip():
        raise Invalid("Describe the concern or choose at least one service.")
    lines = list(data.custom_lines)
    if not data.service_type_ids and not lines:
        # Nothing chosen yet: a diagnosis line so the visit can be scheduled.
        lines.append(CustomLine(description="Diagnose: " + (data.concern or "").strip()[:120]))
    wo = create_work_order_row(ctx, WorkOrderIn(
        customer_id=customer.id, vehicle_id=vehicle.id, concern=data.concern, notes=data.notes,
        service_type_ids=data.service_type_ids, custom_lines=lines, mileage_in=data.mileage_in,
        preferred_employee_id=data.preferred_employee_id))
    return {"customer_id": str(customer.id), "vehicle_id": str(vehicle.id), "work_order": work_order_summary(ctx, wo)}


@command("intake.staff_create", vertical="intake",
         description="Staff intake: create (or pick) the customer and vehicle and open a work order, ready to schedule.")
def intake_staff_create(ctx: Ctx, args: IntakeData) -> dict:
    return _materialize(ctx, args)


@command("intake.accept", vertical="intake",
         description="Accept a pending intake submission: creates or links the customer and vehicle and opens a work order.")
def intake_accept(ctx: Ctx, args: AcceptIn) -> dict:
    sub = ctx.get(IntakeSubmission, args.id, "Submission")
    if sub.status != "pending":
        raise Conflict(f"This submission was already {sub.status}.")
    result = _materialize(ctx, args)
    sub.status, sub.reviewed_by = "accepted", ctx.actor.user_id
    sub.customer_id, sub.vehicle_id = UUID(result["customer_id"]), UUID(result["vehicle_id"])
    sub.work_order_id = UUID(result["work_order"]["id"])
    if sub.call_id:
        call = ctx.tx.get(Call, sub.call_id)
        if call and call.customer_id is None:
            call.customer_id, call.status = sub.customer_id, "processed"
    ctx.tx.flush()
    return result


class RejectIn(BaseModel):
    id: UUID
    note: str | None = None


@command("intake.reject", vertical="intake", description="Dismiss a pending intake submission.")
def intake_reject(ctx: Ctx, args: RejectIn) -> dict:
    sub = ctx.get(IntakeSubmission, args.id, "Submission")
    if sub.status != "pending":
        raise Conflict(f"This submission was already {sub.status}.")
    sub.status, sub.reviewed_by, sub.review_note = "rejected", ctx.actor.user_id, args.note
    return {"ok": True}
