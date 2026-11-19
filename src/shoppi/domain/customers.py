"""Customers and their vehicles."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from shoppi.core.commands import Ctx, IdArg, Invalid, command, patch, query
from shoppi.core.timeutil import normalize_phone
from shoppi.db.models import Call, Customer, Vehicle, WorkOrder

Powertrain = Literal["gas", "diesel", "hybrid", "ev"]


class CustomerIn(BaseModel):
    name: str = Field(min_length=1)
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    preferred_contact: str | None = None
    notes: str | None = None


class CustomerPatch(BaseModel):
    id: UUID
    name: str | None = Field(default=None, min_length=1)
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    preferred_contact: str | None = None
    notes: str | None = None


class VehicleFields(BaseModel):
    vin: str | None = None
    plate: str | None = None
    year: int | None = Field(default=None, ge=1900, le=2100)
    make: str | None = None
    model: str | None = None
    trim: str | None = None
    engine: str | None = None
    gvwr_class: int = Field(default=1, ge=1, le=8, description="US GVWR class 1-8 (1-2 light, 3-6 medium, 7-8 heavy).")
    weight_lb: int | None = Field(default=None, ge=0)
    length_ft: float | None = Field(default=None, ge=0)
    powertrain: Powertrain = "gas"
    mileage: int | None = Field(default=None, ge=0)
    color: str | None = None
    notes: str | None = None


class VehicleIn(VehicleFields):
    customer_id: UUID


class VehiclePatch(BaseModel):
    id: UUID
    vin: str | None = None
    plate: str | None = None
    year: int | None = Field(default=None, ge=1900, le=2100)
    make: str | None = None
    model: str | None = None
    trim: str | None = None
    engine: str | None = None
    gvwr_class: int | None = Field(default=None, ge=1, le=8)
    weight_lb: int | None = None
    length_ft: float | None = None
    powertrain: Powertrain | None = None
    mileage: int | None = None
    color: str | None = None
    notes: str | None = None


class Search(BaseModel):
    q: str | None = Field(default=None, description="Name, phone, email, plate or VIN to search for.")
    limit: int = Field(default=100, le=500)


def vehicle_title(v: Vehicle) -> str:
    return " ".join(str(x) for x in (v.year, v.make, v.model) if x) or (v.plate or v.vin or "Vehicle")


def clean_vin(vin: str | None) -> str | None:
    vin = (vin or "").strip().upper().replace(" ", "")
    return vin or None


def create_customer_row(ctx: Ctx, data: CustomerIn) -> Customer:
    row = Customer(**data.model_dump(), phone_norm=normalize_phone(data.phone))
    ctx.tx.add(row)
    ctx.tx.flush()
    return row


def create_vehicle_row(ctx: Ctx, customer_id: UUID, data: VehicleFields) -> Vehicle:
    fields = data.model_dump(exclude={"customer_id"})
    fields["vin"] = clean_vin(fields.get("vin"))
    row = Vehicle(customer_id=customer_id, **fields)
    ctx.tx.add(row)
    ctx.tx.flush()
    return row


@query("customers.list", vertical="customers", description="Search customers by name, phone, email, plate or VIN.")
def customers_list(ctx: Ctx, args: Search) -> list[dict]:
    q = select(Customer).order_by(Customer.name).limit(args.limit)
    if args.q:
        like = f"%{args.q.strip()}%"
        digits = normalize_phone(args.q)
        by_vehicle = select(Vehicle.customer_id).where(or_(Vehicle.plate.ilike(like), Vehicle.vin.ilike(like)))
        conds = [Customer.name.ilike(like), Customer.email.ilike(like), Customer.id.in_(by_vehicle)]
        if digits and len(digits) >= 3:
            conds.append(Customer.phone_norm.like(f"%{digits}%"))
        q = q.where(or_(*conds))
    customers = ctx.tx.scalars(q).all()
    counts = dict(ctx.tx.execute(select(Vehicle.customer_id, func.count()).group_by(Vehicle.customer_id)).all())
    return [ctx.out(c, vehicle_count=counts.get(c.id, 0)) for c in customers]


@query("customers.get", vertical="customers", description="One customer with their vehicles and work orders.")
def customers_get(ctx: Ctx, args: IdArg) -> dict:
    c = ctx.get(Customer, args.id, "Customer")
    vehicles = ctx.tx.scalars(select(Vehicle).where(Vehicle.customer_id == c.id).order_by(Vehicle.year.desc().nulls_last())).all()
    orders = ctx.tx.scalars(select(WorkOrder).where(WorkOrder.customer_id == c.id).order_by(WorkOrder.number.desc()).limit(50)).all()
    titles = {v.id: vehicle_title(v) for v in vehicles}
    return ctx.out(
        c,
        vehicles=[ctx.out(v, title=titles[v.id]) for v in vehicles],
        work_orders=[ctx.out(w, vehicle_title=titles.get(w.vehicle_id, "")) for w in orders],
        call_count=ctx.tx.scalar(select(func.count()).select_from(Call).where(Call.customer_id == c.id)) or 0,
    )


@command("customers.create", vertical="customers", description="Add a customer.")
def customers_create(ctx: Ctx, args: CustomerIn) -> dict:
    return ctx.out(create_customer_row(ctx, args))


@command("customers.update", vertical="customers", description="Change a customer's details.")
def customers_update(ctx: Ctx, args: CustomerPatch) -> dict:
    row = ctx.get(Customer, args.id, "Customer")
    patch(row, args)
    if "phone" in args.model_fields_set:
        row.phone_norm = normalize_phone(args.phone)
    if not row.name:
        raise Invalid("A customer needs a name.")
    ctx.tx.flush()
    return ctx.out(row)


@command("customers.delete", vertical="customers", managers_only=True, agent=False,
         description="Delete a customer who has no work orders.")
def customers_delete(ctx: Ctx, args: IdArg) -> dict:
    row = ctx.get(Customer, args.id, "Customer")
    if ctx.tx.scalar(select(func.count()).select_from(WorkOrder).where(WorkOrder.customer_id == row.id)):
        raise Invalid("This customer has work orders and can't be deleted.")
    ctx.tx.delete(row)
    return {"ok": True}


@query("vehicles.get", vertical="customers", description="One vehicle with its service history.")
def vehicles_get(ctx: Ctx, args: IdArg) -> dict:
    v = ctx.get(Vehicle, args.id, "Vehicle")
    owner = ctx.tx.get(Customer, v.customer_id)
    orders = ctx.tx.scalars(select(WorkOrder).where(WorkOrder.vehicle_id == v.id).order_by(WorkOrder.number.desc()).limit(50)).all()
    return ctx.out(v, title=vehicle_title(v), customer_name=owner.name if owner else "",
                   work_orders=[ctx.out(w) for w in orders])


@command("vehicles.create", vertical="customers", description="Add a vehicle to a customer.")
def vehicles_create(ctx: Ctx, args: VehicleIn) -> dict:
    ctx.get(Customer, args.customer_id, "Customer")
    v = create_vehicle_row(ctx, args.customer_id, args)
    return ctx.out(v, title=vehicle_title(v))


@command("vehicles.update", vertical="customers", description="Change a vehicle's details.")
def vehicles_update(ctx: Ctx, args: VehiclePatch) -> dict:
    v = ctx.get(Vehicle, args.id, "Vehicle")
    patch(v, args)
    if "vin" in args.model_fields_set:
        v.vin = clean_vin(args.vin)
    for required in ("gvwr_class", "powertrain"):
        if getattr(v, required) is None:
            raise Invalid(f"{required} can't be empty.")
    ctx.tx.flush()
    if {"gvwr_class", "weight_lb", "length_ft", "powertrain"} & args.model_fields_set:
        from shoppi.domain.schedule import revalidate

        revalidate(ctx, vehicle_id=v.id)
    return ctx.out(v, title=vehicle_title(v))
