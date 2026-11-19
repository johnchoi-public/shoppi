"""Parts inventory: stock, suppliers, purchase orders and reorder suggestions."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, NaiveDatetime
from sqlalchemy import func, or_, select

from shoppi.core.commands import Conflict, Ctx, IdArg, Invalid, command, patch, query
from shoppi.db.models import Part, PartReservation, PoLine, PurchaseOrder, StockMovement, Supplier, SupplierPartLink
from shoppi.domain.work_orders import next_number


class PartIn(BaseModel):
    sku: str = Field(min_length=1)
    name: str = Field(min_length=1)
    brand: str | None = None
    description: str | None = None
    on_hand: int = Field(default=0, ge=0)
    reorder_point: int = Field(default=0, ge=0)
    reorder_qty: int = Field(default=0, ge=0)
    unit_cost: float | None = Field(default=None, ge=0)
    unit_price: float | None = Field(default=None, ge=0)
    location: str | None = None
    supplier_id: UUID | None = None


class PartPatch(BaseModel):
    id: UUID
    sku: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1)
    brand: str | None = None
    description: str | None = None
    reorder_point: int | None = Field(default=None, ge=0)
    reorder_qty: int | None = Field(default=None, ge=0)
    unit_cost: float | None = Field(default=None, ge=0)
    unit_price: float | None = Field(default=None, ge=0)
    location: str | None = None
    supplier_id: UUID | None = None
    active: bool | None = None


class AdjustIn(BaseModel):
    part_id: UUID
    delta: int = Field(description="Change in on-hand quantity: positive adds stock, negative removes it.")
    reason: Literal["adjust", "return", "use", "receive"] = "adjust"
    note: str | None = None


class PartSearch(BaseModel):
    q: str | None = Field(default=None, description="SKU, name or brand to search for.")
    low_only: bool = Field(default=False, description="Only parts at or below their reorder point.")
    include_inactive: bool = False


class SupplierIn(BaseModel):
    id: UUID | None = None
    name: str = Field(min_length=1)
    phone: str | None = None
    email: str | None = None
    account_number: str | None = None
    notes: str | None = None


class PoLineIn(BaseModel):
    part_id: UUID
    qty: int = Field(ge=1)
    unit_cost: float | None = Field(default=None, ge=0)


class PoIn(BaseModel):
    supplier_id: UUID | None = None
    lines: list[PoLineIn] = Field(min_length=1)
    eta: NaiveDatetime | None = Field(default=None, description="Expected delivery, shop-local time.")
    notes: str | None = None


class PoOrderIn(BaseModel):
    id: UUID
    eta: NaiveDatetime | None = None


class ReceiveLine(BaseModel):
    line_id: UUID
    qty: int = Field(ge=1)


class ReceiveIn(BaseModel):
    id: UUID
    lines: list[ReceiveLine] | None = Field(default=None, description="Quantities received per line; omit to receive everything outstanding.")


def stock_figures(ctx: Ctx) -> tuple[dict[UUID, int], dict[UUID, int]]:
    reserved = dict(ctx.tx.execute(select(PartReservation.part_id, func.sum(PartReservation.qty))
                                   .where(PartReservation.status == "reserved").group_by(PartReservation.part_id)).all())
    on_order = dict(ctx.tx.execute(
        select(PoLine.part_id, func.sum(PoLine.qty - PoLine.received_qty)).join(PurchaseOrder, PurchaseOrder.id == PoLine.po_id)
        .where(PurchaseOrder.status == "ordered").group_by(PoLine.part_id)).all())
    return {k: int(v) for k, v in reserved.items()}, {k: int(v) for k, v in on_order.items()}


def part_out(ctx: Ctx, p: Part, reserved: dict, on_order: dict, suppliers: dict | None = None) -> dict:
    r, o = reserved.get(p.id, 0), on_order.get(p.id, 0)
    available = p.on_hand - r
    supplier = (suppliers or {}).get(p.supplier_id)
    return ctx.out(p, reserved=r, available=available, on_order=o, low=p.active and (available + o) <= p.reorder_point and p.reorder_point > 0,
                   supplier_name=supplier.name if supplier else None)


@query("parts.list", vertical="inventory", description="Parts in stock with on-hand, reserved, available and on-order quantities.")
def parts_list(ctx: Ctx, args: PartSearch) -> list[dict]:
    q = select(Part).order_by(Part.name)
    if not args.include_inactive:
        q = q.where(Part.active)
    if args.q:
        like = f"%{args.q.strip()}%"
        q = q.where(or_(Part.sku.ilike(like), Part.name.ilike(like), Part.brand.ilike(like)))
    reserved, on_order = stock_figures(ctx)
    suppliers = {s.id: s for s in ctx.tx.scalars(select(Supplier))}
    rows = [part_out(ctx, p, reserved, on_order, suppliers) for p in ctx.tx.scalars(q)]
    return [r for r in rows if r["low"]] if args.low_only else rows


@query("parts.get", vertical="inventory", description="One part with its recent stock movements and catalog links.")
def parts_get(ctx: Ctx, args: IdArg) -> dict:
    p = ctx.get(Part, args.id, "Part")
    reserved, on_order = stock_figures(ctx)
    moves = ctx.tx.scalars(select(StockMovement).where(StockMovement.part_id == p.id).order_by(StockMovement.created_at.desc()).limit(50)).all()
    links = ctx.tx.scalars(select(SupplierPartLink).where(SupplierPartLink.part_id == p.id)).all()
    return {**part_out(ctx, p, reserved, on_order), "movements": [ctx.out(m) for m in moves], "links": [ctx.out(link) for link in links]}


@command("parts.create", vertical="inventory", description="Add a part to inventory.")
def parts_create(ctx: Ctx, args: PartIn) -> dict:
    if ctx.tx.scalar(select(Part.id).where(Part.sku == args.sku.strip())):
        raise Conflict("A part with that SKU already exists.")
    data = args.model_dump()
    data["sku"] = data["sku"].strip()
    row = Part(**data)
    ctx.tx.add(row)
    ctx.tx.flush()
    if row.on_hand:
        ctx.tx.add(StockMovement(part_id=row.id, delta=row.on_hand, reason="adjust", note="Opening stock"))
    return ctx.out(row)


@command("parts.update", vertical="inventory", description="Change a part's details or reorder levels (not its stock count).")
def parts_update(ctx: Ctx, args: PartPatch) -> dict:
    row = ctx.get(Part, args.id, "Part")
    patch(row, args)
    for required in ("sku", "name", "reorder_point", "reorder_qty", "active"):
        if getattr(row, required) is None:
            raise Invalid(f"{required} can't be empty.")
    ctx.tx.flush()
    return ctx.out(row)


@command("parts.adjust", vertical="inventory", description="Add or remove stock for a part, with a reason (kept in the stock ledger).")
def parts_adjust(ctx: Ctx, args: AdjustIn) -> dict:
    row = ctx.get(Part, args.part_id, "Part")
    if args.delta == 0:
        raise Invalid("Enter a non-zero quantity.")
    if row.on_hand + args.delta < 0:
        raise Conflict(f"Only {row.on_hand} on hand.")
    row.on_hand += args.delta
    ctx.tx.add(StockMovement(part_id=row.id, delta=args.delta, reason=args.reason, note=args.note))
    ctx.tx.flush()
    return ctx.out(row)


@query("suppliers.list", vertical="inventory", description="Parts suppliers.")
def suppliers_list(ctx: Ctx) -> list[dict]:
    return [ctx.out(s) for s in ctx.tx.scalars(select(Supplier).order_by(Supplier.name))]


@command("suppliers.save", vertical="inventory", description="Create or update a supplier.")
def suppliers_save(ctx: Ctx, args: SupplierIn) -> dict:
    data = args.model_dump(exclude={"id"})
    if args.id:
        row = ctx.get(Supplier, args.id, "Supplier")
        for k, v in data.items():
            setattr(row, k, v)
    else:
        row = Supplier(**data)
        ctx.tx.add(row)
    ctx.tx.flush()
    return ctx.out(row)


def po_out(ctx: Ctx, po: PurchaseOrder) -> dict:
    supplier = ctx.tx.get(Supplier, po.supplier_id) if po.supplier_id else None
    lines = []
    for line, part in ctx.tx.execute(select(PoLine, Part).join(Part, Part.id == PoLine.part_id).where(PoLine.po_id == po.id)).all():
        lines.append(ctx.out(line, sku=part.sku, name=part.name, outstanding=line.qty - line.received_qty))
    total = sum((x["unit_cost"] or 0) * x["qty"] for x in lines)
    return ctx.out(po, supplier_name=supplier.name if supplier else None, lines=lines, total=round(total, 2))


class PoFilter(BaseModel):
    status: str | None = None


@query("purchase_orders.list", vertical="inventory", description="Purchase orders with their lines and expected delivery.")
def po_list(ctx: Ctx, args: PoFilter) -> list[dict]:
    q = select(PurchaseOrder).order_by(PurchaseOrder.number.desc()).limit(200)
    if args.status:
        q = q.where(PurchaseOrder.status == args.status)
    return [po_out(ctx, po) for po in ctx.tx.scalars(q)]


@command("purchase_orders.create", vertical="inventory", description="Draft a purchase order for parts.")
def po_create(ctx: Ctx, args: PoIn) -> dict:
    if args.supplier_id:
        ctx.get(Supplier, args.supplier_id, "Supplier")
    po = PurchaseOrder(number=next_number(ctx, PurchaseOrder), supplier_id=args.supplier_id, eta=ctx.local(args.eta), notes=args.notes)
    ctx.tx.add(po)
    ctx.tx.flush()
    for line in args.lines:
        part = ctx.get(Part, line.part_id, "Part")
        cost = line.unit_cost if line.unit_cost is not None else part.unit_cost
        ctx.tx.add(PoLine(po_id=po.id, part_id=part.id, qty=line.qty, unit_cost=cost))
    ctx.tx.flush()
    return po_out(ctx, po)


@command("purchase_orders.mark_ordered", vertical="inventory",
         description="Record that a purchase order has been placed with the supplier, with its expected delivery time.")
def po_mark_ordered(ctx: Ctx, args: PoOrderIn) -> dict:
    po = ctx.get(PurchaseOrder, args.id, "Purchase order")
    if po.status != "draft":
        raise Conflict(f"This purchase order is already {po.status}.")
    po.status = "ordered"
    if args.eta:
        po.eta = ctx.local(args.eta)
    ctx.tx.flush()
    return po_out(ctx, po)


@command("purchase_orders.receive", vertical="inventory", description="Receive parts from a purchase order into stock.")
def po_receive(ctx: Ctx, args: ReceiveIn) -> dict:
    po = ctx.get(PurchaseOrder, args.id, "Purchase order")
    if po.status not in ("ordered", "draft"):
        raise Conflict(f"This purchase order is {po.status}.")
    lines = {line.id: line for line in ctx.tx.scalars(select(PoLine).where(PoLine.po_id == po.id))}
    incoming = {r.line_id: r.qty for r in args.lines} if args.lines else {i: line.qty - line.received_qty for i, line in lines.items()}
    for line_id, qty in incoming.items():
        line = lines.get(line_id)
        if line is None:
            raise Invalid("That line is not on this purchase order.")
        if qty <= 0:
            continue
        if qty > line.qty - line.received_qty:
            raise Invalid("You can't receive more than was ordered.")
        line.received_qty += qty
        part = ctx.tx.get(Part, line.part_id)
        part.on_hand += qty
        if line.unit_cost is not None:
            part.unit_cost = line.unit_cost
        ctx.tx.add(StockMovement(part_id=part.id, delta=qty, reason="receive", po_id=po.id))
    if all(line.received_qty >= line.qty for line in lines.values()):
        po.status = "received"
    elif po.status == "draft":
        po.status = "ordered"
    ctx.tx.flush()
    return po_out(ctx, po)


@command("purchase_orders.cancel", vertical="inventory", description="Cancel a purchase order that has not been fully received.")
def po_cancel(ctx: Ctx, args: IdArg) -> dict:
    po = ctx.get(PurchaseOrder, args.id, "Purchase order")
    if po.status == "received":
        raise Conflict("This purchase order has already been received.")
    po.status = "cancelled"
    return {"ok": True}


@query("inventory.reorder_suggestions", vertical="inventory",
       description="Parts that are at or below their reorder point after reservations and open orders, with a suggested quantity.")
def reorder_suggestions(ctx: Ctx) -> list[dict]:
    reserved, on_order = stock_figures(ctx)
    suppliers = {s.id: s for s in ctx.tx.scalars(select(Supplier))}
    out = []
    for p in ctx.tx.scalars(select(Part).where(Part.active, Part.reorder_point > 0).order_by(Part.name)):
        row = part_out(ctx, p, reserved, on_order, suppliers)
        if row["low"]:
            shortfall = p.reorder_point - (row["available"] + row["on_order"])
            row["suggested_qty"] = max(p.reorder_qty, shortfall + 1) if p.reorder_qty else shortfall + 1
            out.append(row)
    return out
