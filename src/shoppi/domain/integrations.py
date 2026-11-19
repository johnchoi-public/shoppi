"""Settings -> Integrations, plus the capabilities integrations provide to the
rest of the app: VIN decoding and parts-catalog search/ordering."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from shoppi.config import get_settings
from shoppi.core.commands import Conflict, Ctx, IdArg, Invalid, NotFound, command, query
from shoppi.core.timeutil import to_local
from shoppi.db.models import (
    CatalogItem, IntegrationCall, IntegrationCredential, Part, PoLine, PurchaseOrder, SupplierPartLink, SyncMap,
    Vehicle, VinCache, WorkOrder,
)
from shoppi.integrations import registry
from shoppi.integrations.base import IntegrationError, OrderLine, TranscriptSource, VehicleRef
from shoppi.integrations.credentials import encrypt
from shoppi.integrations.vin import normalize_vin, vin_problem


def _wrap(fn):
    """Provider failures become normal user-facing errors."""
    try:
        return fn()
    except IntegrationError as e:
        raise Invalid(str(e)) from e


# ---- Connect / disconnect ---------------------------------------------------------


class KeyArg(BaseModel):
    key: str


class ConnectIn(BaseModel):
    key: str
    values: dict[str, str] = {}


class EnableIn(BaseModel):
    key: str
    enabled: bool


class OptionsIn(BaseModel):
    key: str
    options: dict


class OAuthStore(BaseModel):
    key: str
    secret: dict
    options: dict = {}


def _integration(key: str):
    if key not in registry.REGISTRY:
        raise NotFound("Unknown integration.")
    return registry.REGISTRY[key]


@query("integrations.list", vertical="admin", agent=False)
def integrations_list(ctx: Ctx) -> list[dict]:
    """Available integrations with their connection status and recent health."""
    rows = {r.key: r for r in ctx.tx.scalars(select(IntegrationCredential))}
    out = []
    for key, integ in registry.REGISTRY.items():
        row = rows.get(key)
        available, reason = integ.is_available()
        calls = ctx.tx.scalars(select(IntegrationCall).where(IntegrationCall.key == key)
                               .order_by(IntegrationCall.created_at.desc()).limit(20)).all()
        item = {
            "key": key, "name": integ.name, "description": integ.description, "category": integ.category,
            "connect": integ.connect, "available": available, "unavailable_reason": reason,
            "fields": [{"name": f.name, "label": f.label, "secret": f.secret, "required": f.required, "help": f.help} for f in integ.fields],
            "connected": row is not None and (integ.connect == "none" or bool(row.secret)),
            "enabled": registry.is_enabled(ctx, integ), "options": (row.options if row else {}) or {},
            "health": {"calls": len(calls), "failures": sum(1 for c in calls if not c.ok),
                       "last_status": calls[0].status if calls else None, "last_at": to_local(calls[0].created_at, ctx.tz) if calls else None,
                       "avg_ms": round(sum(c.latency_ms or 0 for c in calls) / len(calls)) if calls else None},
            "supports_ordering": bool(getattr(integ, "supports_ordering", False)),
        }
        if isinstance(integ, TranscriptSource):
            item["webhook_url"] = f"{get_settings().base_url}/api/integrations/{key}/webhook/{ctx.shop.hook_id}"
        out.append(item)
    return out


def _upsert(ctx: Ctx, key: str, *, secret: dict | None = None, options: dict | None = None, status: str = "connected") -> IntegrationCredential:
    row = registry.credential_row(ctx, key)
    if row is None:
        row = IntegrationCredential(key=key, options={}, connected_by=ctx.actor.user_id)
        ctx.tx.add(row)
    row.status = status
    if secret is not None:
        row.secret = _wrap(lambda: encrypt(secret))
        row.connected_by = ctx.actor.user_id
    if options is not None:
        row.options = {**(row.options or {}), **options}
    ctx.tx.flush()
    return row


@command("integrations.connect", vertical="admin", managers_only=True, agent=False, external=True,
         description="Connect an integration with pasted credentials (API key style).")
def integrations_connect(ctx: Ctx, args: ConnectIn) -> dict:
    integ = _integration(args.key)
    if integ.connect != "api_key":
        raise Invalid("This integration is not connected with an API key.")
    values = {k: v.strip() for k, v in args.values.items() if k in {f.name for f in integ.fields} and v and v.strip()}
    missing = [f.label for f in integ.fields if f.required and not values.get(f.name)]
    if missing:
        raise Invalid("Missing: " + ", ".join(missing))
    before = dict(values)
    _wrap(lambda: integ.validate_credentials(values))
    _upsert(ctx, args.key, secret=values)
    # Anything the integration generated (e.g. a signing secret) is shown exactly once.
    generated = {k: v for k, v in values.items() if k not in before}
    return {"key": args.key, "connected": True, "generated": generated}


@command("integrations.store_oauth", vertical="admin", managers_only=True, agent=False, external=True,
         description="Save the result of an OAuth connection.")
def integrations_store_oauth(ctx: Ctx, args: OAuthStore) -> dict:
    integ = _integration(args.key)
    if integ.connect != "oauth":
        raise Invalid("This integration does not use OAuth.")
    _upsert(ctx, args.key, secret=args.secret, options=args.options)
    return {"key": args.key, "connected": True}


@command("integrations.set_enabled", vertical="admin", managers_only=True, agent=False, description="Switch an integration on or off.")
def integrations_set_enabled(ctx: Ctx, args: EnableIn) -> dict:
    integ = _integration(args.key)
    row = registry.credential_row(ctx, args.key)
    if integ.connect != "none" and (row is None or not row.secret):
        raise Invalid("Connect this integration first.")
    _upsert(ctx, args.key, status="connected" if args.enabled else "disabled")
    return {"key": args.key, "enabled": args.enabled}


@command("integrations.set_options", vertical="admin", managers_only=True, agent=False, description="Change an integration's options.")
def integrations_set_options(ctx: Ctx, args: OptionsIn) -> dict:
    _integration(args.key)
    row = registry.credential_row(ctx, args.key)
    if row is None:
        raise Invalid("Connect this integration first.")
    row.options = {**(row.options or {}), **args.options}
    ctx.tx.flush()
    return {"key": args.key, "options": row.options}


@command("integrations.disconnect", vertical="admin", managers_only=True, agent=False, description="Disconnect an integration and forget its credentials.")
def integrations_disconnect(ctx: Ctx, args: KeyArg) -> dict:
    _integration(args.key)
    row = registry.credential_row(ctx, args.key)
    if row is not None:
        ctx.tx.delete(row)
    ctx.tx.execute(delete(SyncMap).where(SyncMap.key == args.key))
    return {"key": args.key, "connected": False}


@command("integrations.sync_calendar", vertical="admin", managers_only=True, agent=False, external=True,
         description="Push the shop calendar to the connected external calendar now.")
def integrations_sync_calendar(ctx: Ctx) -> dict:
    shop_id = ctx.actor.shop_id
    ctx.after_commit.append(lambda: registry.calendar_changed(shop_id))
    return {"started": True}


# ---- VIN decoding -----------------------------------------------------------------


class VinArg(BaseModel):
    vin: str = Field(description="The 17-character vehicle identification number.")


def decode_vin(ctx: Ctx, raw_vin: str) -> dict:
    vin = normalize_vin(raw_vin)
    problem = vin_problem(vin)
    if problem:  # rejected locally: nothing is sent anywhere
        raise Invalid(problem)
    cached = ctx.tx.get(VinCache, vin)
    if cached is not None:
        return {**cached.decoded, "cached": True}
    found = registry.vin_decoder(ctx)
    if found is None:
        raise Invalid("VIN decoding is switched off for this shop. Enter the vehicle details by hand.")
    key, decoder = found
    decoded = _wrap(lambda: registry.call(ctx, key, "decode", lambda: decoder.decode(vin))).as_dict()
    ctx.tx.add(VinCache(vin=vin, decoded=decoded))
    return {**decoded, "cached": False}


@query("vin.decode", vertical="customers",
       description="Decode a VIN into year, make, model, engine, fuel type and weight class. The result is a suggestion to check, not a fact to trust blindly.")
def vin_decode(ctx: Ctx, args: VinArg) -> dict:
    return decode_vin(ctx, args.vin)


# ---- Parts catalogs ---------------------------------------------------------------


class CatalogSearch(BaseModel):
    query: str = Field(min_length=2, description="Part description or part number, e.g. 'front brake pads'.")
    vehicle_id: UUID | None = Field(default=None, description="Search for parts that fit this vehicle.")
    work_order_id: UUID | None = Field(default=None, description="Use this work order's vehicle.")


class CsvRow(BaseModel):
    part_number: str = Field(min_length=1)
    description: str = Field(min_length=1)
    brand: str | None = None
    fitment: str | None = None
    list_price: float | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)
    qty_available: int | None = Field(default=None, ge=0)
    eta_days: int | None = Field(default=None, ge=0)


class CsvCatalogIn(BaseModel):
    supplier_name: str = Field(default="Uploaded price file", min_length=1)
    rows: list[CsvRow] = Field(min_length=1, max_length=50_000)


class LinkIn(BaseModel):
    part_id: UUID
    provider: str
    supplier_part_number: str = Field(min_length=1)
    brand: str | None = None
    cost: float | None = Field(default=None, ge=0)
    eta_days: int | None = Field(default=None, ge=0)


class AddFromCatalog(BaseModel):
    provider: str
    part_number: str = Field(min_length=1)
    description: str = Field(min_length=1)
    brand: str | None = None
    cost: float | None = Field(default=None, ge=0)
    list_price: float | None = Field(default=None, ge=0)
    eta_days: int | None = Field(default=None, ge=0)
    reorder_point: int = Field(default=0, ge=0)


class PlaceOrder(BaseModel):
    po_id: UUID
    provider: str
    confirm_total: float = Field(description="The order total the user saw and confirmed. Must match the purchase order.")


def _vehicle_ref(ctx: Ctx, vehicle_id: UUID | None, work_order_id: UUID | None) -> VehicleRef | None:
    if work_order_id and not vehicle_id:
        vehicle_id = ctx.get(WorkOrder, work_order_id, "Work order").vehicle_id
    if not vehicle_id:
        return None
    v = ctx.get(Vehicle, vehicle_id, "Vehicle")
    return VehicleRef(v.year, v.make, v.model, v.engine, v.vin)


@query("catalog.search", vertical="inventory",
       description="Search connected parts catalogs for price and availability, optionally for a specific vehicle. Results are labelled by supplier.")
def catalog_search(ctx: Ctx, args: CatalogSearch) -> dict:
    vehicle = _vehicle_ref(ctx, args.vehicle_id, args.work_order_id)
    results, searched, errors = [], [], []
    for key, provider, creds in registry.parts_catalogs(ctx):
        name = registry.REGISTRY[key].name
        try:
            found = registry.call(ctx, key, "search", lambda p=provider, c=creds: p.search(ctx, c, vehicle, args.query))
            results.extend(part.as_dict() for part in found)
            searched.append(name)
        except IntegrationError as e:
            errors.append({"provider": name, "message": str(e)})  # one catalog failing doesn't hide the others
    links = {(link.provider, link.supplier_part_number): str(link.part_id) for link in ctx.tx.scalars(select(SupplierPartLink))}
    for r in results:
        r["linked_part_id"] = links.get((r["provider"], r["part_number"]))
    results.sort(key=lambda r: (r["best_eta_days"] is None, r["best_eta_days"] or 0, r["cost"] or r["list_price"] or 0))
    return {"results": results, "searched": searched, "errors": errors,
            "vehicle": " ".join(str(x) for x in (vehicle.year, vehicle.make, vehicle.model) if x) if vehicle else None}


@command("catalog.replace_csv", vertical="inventory", agent=False,
         description="Replace the uploaded supplier price file with new rows.")
def catalog_replace_csv(ctx: Ctx, args: CsvCatalogIn) -> dict:
    ctx.tx.execute(delete(CatalogItem).where(CatalogItem.supplier_name == args.supplier_name))
    for row in args.rows:
        ctx.tx.add(CatalogItem(supplier_name=args.supplier_name, **row.model_dump()))
    ctx.tx.flush()
    return {"supplier_name": args.supplier_name, "rows": len(args.rows)}


@query("catalog.files", vertical="inventory", agent=False)
def catalog_files(ctx: Ctx) -> list[dict]:
    """Uploaded supplier price files."""
    rows = ctx.tx.execute(select(CatalogItem.supplier_name, func.count(), func.max(CatalogItem.created_at))
                          .group_by(CatalogItem.supplier_name)).all()
    return [{"supplier_name": n, "rows": c, "uploaded_at": to_local(at, ctx.tz)} for n, c, at in rows]


def _save_link(ctx: Ctx, part_id: UUID, provider: str, number: str, brand, cost, eta_days) -> SupplierPartLink:
    link = ctx.tx.scalar(select(SupplierPartLink).where(SupplierPartLink.part_id == part_id, SupplierPartLink.provider == provider))
    if link is None:
        link = SupplierPartLink(part_id=part_id, provider=provider, supplier_part_number=number)
        ctx.tx.add(link)
    link.supplier_part_number, link.brand, link.last_cost, link.last_eta_days, link.last_checked = number, brand, cost, eta_days, ctx.now()
    ctx.tx.flush()
    return link


@command("catalog.link_part", vertical="inventory",
         description="Link an inventory part to a catalog entry, so its delivery time can be used when scheduling and it can be reordered.")
def catalog_link_part(ctx: Ctx, args: LinkIn) -> dict:
    ctx.get(Part, args.part_id, "Part")
    _integration(args.provider)
    return ctx.out(_save_link(ctx, args.part_id, args.provider, args.supplier_part_number, args.brand, args.cost, args.eta_days))


@command("catalog.add_to_inventory", vertical="inventory", description="Create an inventory part from a catalog entry and link the two.")
def catalog_add_to_inventory(ctx: Ctx, args: AddFromCatalog) -> dict:
    _integration(args.provider)
    if ctx.tx.scalar(select(Part.id).where(Part.sku == args.part_number)):
        raise Conflict("A part with that number is already in inventory.")
    part = Part(sku=args.part_number, name=args.description, brand=args.brand, unit_cost=args.cost, unit_price=args.list_price,
                reorder_point=args.reorder_point)
    ctx.tx.add(part)
    ctx.tx.flush()
    _save_link(ctx, part.id, args.provider, args.part_number, args.brand, args.cost, args.eta_days)
    return ctx.out(part)


@command("inventory.place_supplier_order", vertical="inventory", managers_only=True, agent=False, external=True,
         description="Send a draft purchase order to a connected supplier catalog. This places a real order.")
def place_supplier_order(ctx: Ctx, args: PlaceOrder) -> dict:
    po = ctx.get(PurchaseOrder, args.po_id, "Purchase order")
    if po.status != "draft":
        raise Conflict(f"This purchase order is already {po.status}.")
    integ = _integration(args.provider)
    if not getattr(integ, "supports_ordering", False) or not registry.is_enabled(ctx, integ):
        raise Invalid("That catalog can't take orders.")
    lines, total, etas = [], 0.0, []
    for line in ctx.tx.scalars(select(PoLine).where(PoLine.po_id == po.id)):
        link = ctx.tx.scalar(select(SupplierPartLink).where(SupplierPartLink.part_id == line.part_id, SupplierPartLink.provider == args.provider))
        part = ctx.tx.get(Part, line.part_id)
        if link is None:
            raise Invalid(f"{part.name} isn't linked to this catalog. Link it from Find parts first.")
        lines.append(OrderLine(link.supplier_part_number, line.qty, link.brand))
        total += float(line.unit_cost or 0) * line.qty
        if link.last_eta_days is not None:
            etas.append(link.last_eta_days)
    # The user must have confirmed this exact total on the confirmation screen.
    if abs(total - args.confirm_total) > 0.005:
        raise Conflict(f"The order total is now ${total:,.2f}. Review it and confirm again.")
    creds = registry.creds_for(ctx, args.provider)
    ref = _wrap(lambda: registry.call(ctx, args.provider, "place_order", lambda: integ.place_order(ctx, creds, lines)))
    po.status, po.provider, po.external_ref = "ordered", args.provider, ref
    if etas and po.eta is None:
        po.eta = ctx.now() + timedelta(days=max(etas))
    ctx.tx.flush()
    return {"po_id": str(po.id), "number": po.number, "supplier_reference": ref, "total": round(total, 2)}


__all__ = ["decode_vin", "IdArg"]
