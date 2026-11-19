"""All database tables.

Times are stored as timestamptz (UTC instants); the API and the assistant speak
shop-local wall-clock times and convert at the edge (see core/timeutil.py).
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from shoppi.config import get_settings
from shoppi.db.base import Base, TenantMixin, finalize, ref

TZ = DateTime(timezone=True)
Money = Numeric(12, 2)


def _uuid(*args, **kw):
    return mapped_column(PG_UUID(as_uuid=True), *args, **kw)


# ---- Global tables ----------------------------------------------------------------


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[UUID] = _uuid(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, default="America/New_York")
    phone: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    # Weekly opening hours: {"0": ["08:00", "17:00"], ..., "6": null}; 0 = Monday.
    hours: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Free-form per-shop options (scheduler weights, supervision cap, retention...).
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Unguessable id used in inbound webhook URLs.
    hook_id: Mapped[str] = mapped_column(Text, unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = _uuid(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(Text, unique=True)  # stored lower-case
    name: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    google_sub: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[UUID] = _uuid(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    shop_id: Mapped[UUID] = _uuid(ForeignKey("shops.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(TZ)


class Membership(TenantMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("shop_id", "user_id"),)

    user_id: Mapped[UUID] = _uuid(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(Text)  # owner | advisor | mechanic
    status: Mapped[str] = mapped_column(Text, default="active")  # active | disabled
    employee_id: Mapped[UUID | None] = ref("employees", nullable=True, ondelete=None)


class CertType(Base):
    """Certification catalog. Rows with shop_id NULL are the built-in set shared by all shops."""

    __tablename__ = "cert_types"
    # One row per code per shop; built-in rows (shop_id NULL) are compared as a fixed placeholder id.
    __table_args__ = (Index("uq_cert_types_code", "code", text("coalesce(shop_id, '00000000-0000-0000-0000-000000000000'::uuid)"), unique=True),)

    id: Mapped[UUID] = _uuid(primary_key=True, default=uuid4)
    shop_id: Mapped[UUID | None] = _uuid(ForeignKey("shops.id", ondelete="CASCADE"), nullable=True)
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text, default="other")
    # Legally required certifications can never be overridden by a manager.
    legally_required: Mapped[bool] = mapped_column(Boolean, default=False)
    has_expiry: Mapped[bool] = mapped_column(Boolean, default=True)


class VinCache(Base):
    """Decoded VIN specs. Not shop data: a VIN decodes the same for everyone."""

    __tablename__ = "vin_cache"

    vin: Mapped[str] = mapped_column(Text, primary_key=True)
    decoded: Mapped[dict] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class AuditLog(Base):
    """Append-only change history, written only by the audit trigger (db/rls.py)."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_entity", "shop_id", "entity_type", "entity_id"), Index("ix_audit_at", "shop_id", "at"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shop_id: Mapped[UUID | None] = _uuid(nullable=True)
    at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    actor_type: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[UUID | None] = _uuid(nullable=True)
    actor_name: Mapped[str | None] = mapped_column(Text)
    actor_ref: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    vertical: Mapped[str | None] = mapped_column(Text)
    command: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text, index=True)
    change_request_id: Mapped[UUID | None] = _uuid(nullable=True)
    approved_by: Mapped[UUID | None] = _uuid(nullable=True)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[UUID | None] = _uuid(nullable=True)
    op: Mapped[str] = mapped_column(Text)  # insert | update | delete
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    changed_fields: Mapped[list[str] | None] = mapped_column(ARRAY(Text))


# ---- People -----------------------------------------------------------------------


class Invite(TenantMixin, Base):
    __tablename__ = "invites"

    email: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    employee_id: Mapped[UUID | None] = ref("employees", nullable=True, ondelete=None)
    expires_at: Mapped[datetime] = mapped_column(TZ)
    accepted_at: Mapped[datetime | None] = mapped_column(TZ)


class Employee(TenantMixin, Base):
    __tablename__ = "employees"

    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, default="tech")  # tech | apprentice | advisor | owner
    skill_level: Mapped[int] = mapped_column(Integer, default=3)  # 1 (entry) .. 5 (master)
    can_supervise: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(Text)


class EmployeeCert(TenantMixin, Base):
    __tablename__ = "employee_certs"

    employee_id: Mapped[UUID] = ref("employees")
    cert_type_id: Mapped[UUID] = _uuid(ForeignKey("cert_types.id"))
    number: Mapped[str | None] = mapped_column(Text)
    issued_on: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date)


class Shift(TenantMixin, Base):
    __tablename__ = "shifts"

    employee_id: Mapped[UUID] = ref("employees")
    starts_at: Mapped[datetime] = mapped_column(TZ, index=True)
    ends_at: Mapped[datetime] = mapped_column(TZ)
    note: Mapped[str | None] = mapped_column(Text)
    series_id: Mapped[UUID | None] = _uuid(nullable=True)  # set when created as a repeating shift


class TimeOff(TenantMixin, Base):
    __tablename__ = "time_off"

    employee_id: Mapped[UUID] = ref("employees")
    starts_at: Mapped[datetime] = mapped_column(TZ, index=True)
    ends_at: Mapped[datetime] = mapped_column(TZ)
    reason: Mapped[str | None] = mapped_column(Text)


# ---- Customers --------------------------------------------------------------------


class Customer(TenantMixin, Base):
    __tablename__ = "customers"

    name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    phone_norm: Mapped[str | None] = mapped_column(Text, index=True)  # digits only, for matching calls
    email: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    preferred_contact: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class Vehicle(TenantMixin, Base):
    __tablename__ = "vehicles"

    customer_id: Mapped[UUID] = ref("customers")
    vin: Mapped[str | None] = mapped_column(Text)
    plate: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    make: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    trim: Mapped[str | None] = mapped_column(Text)
    engine: Mapped[str | None] = mapped_column(Text)
    # US GVWR class 1-8 (1-2 light duty, 3-6 medium duty, 7-8 heavy duty).
    gvwr_class: Mapped[int] = mapped_column(Integer, default=1)
    weight_lb: Mapped[int | None] = mapped_column(Integer)
    length_ft: Mapped[float | None] = mapped_column(Numeric(5, 1))
    powertrain: Mapped[str] = mapped_column(Text, default="gas")  # gas | diesel | hybrid | ev
    mileage: Mapped[int | None] = mapped_column(Integer)
    color: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


# ---- Services, bays ---------------------------------------------------------------


class ServiceType(TenantMixin, Base):
    __tablename__ = "service_types"

    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal | None] = mapped_column(Money)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # The service offered as "Diagnosis" during customer intake (at most one per shop).
    is_diagnosis: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class ServiceStep(TenantMixin, Base):
    """One stage of a service. A simple service has one step; e.g. an engine job has
    teardown -> machine-shop wait -> reassembly."""

    __tablename__ = "service_steps"

    service_type_id: Mapped[UUID] = ref("service_types")
    seq: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(Text)
    minutes: Mapped[int] = mapped_column(Integer, default=60)
    # False = nobody works on the car (cure time, waiting): no mechanic is needed.
    hands_on: Mapped[bool] = mapped_column(Boolean, default=True)
    needs_bay: Mapped[bool] = mapped_column(Boolean, default=True)
    bay_capability: Mapped[str | None] = mapped_column(Text)  # e.g. "alignment", "heavy_lift"
    # Keep the vehicle in the bay between this step and the next one.
    holds_bay: Mapped[bool] = mapped_column(Boolean, default=True)
    same_mechanic_as_prev: Mapped[bool] = mapped_column(Boolean, default=False)


class StepCertRequirement(TenantMixin, Base):
    """Certifications a mechanic needs for a step. Rows sharing a group_no are
    alternatives (any one is enough); every group must be satisfied."""

    __tablename__ = "step_cert_requirements"

    step_id: Mapped[UUID] = ref("service_steps")
    group_no: Mapped[int] = mapped_column(Integer, default=1)
    cert_type_id: Mapped[UUID] = _uuid(ForeignKey("cert_types.id"))
    # Only applies to these vehicles (NULL = always).
    applies_powertrain: Mapped[str | None] = mapped_column(Text)
    applies_min_class: Mapped[int | None] = mapped_column(Integer)


class ServicePart(TenantMixin, Base):
    __tablename__ = "service_parts"

    service_type_id: Mapped[UUID] = ref("service_types")
    part_id: Mapped[UUID] = ref("parts")
    qty: Mapped[int] = mapped_column(Integer, default=1)


class Bay(TenantMixin, Base):
    __tablename__ = "bays"

    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, default="general")
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    max_gvwr_class: Mapped[int] = mapped_column(Integer, default=3)
    max_weight_lb: Mapped[int | None] = mapped_column(Integer)
    max_length_ft: Mapped[float | None] = mapped_column(Numeric(5, 1))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text)


class BayHours(TenantMixin, Base):
    """A bay's own weekly schedule. Weekdays with no row fall back to shop hours."""

    __tablename__ = "bay_hours"
    __table_args__ = (UniqueConstraint("shop_id", "bay_id", "weekday"),)

    bay_id: Mapped[UUID] = ref("bays")
    weekday: Mapped[int] = mapped_column(Integer)  # 0 = Monday
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    opens: Mapped[time | None] = mapped_column(Time)
    closes: Mapped[time | None] = mapped_column(Time)


class BayClosure(TenantMixin, Base):
    __tablename__ = "bay_closures"

    bay_id: Mapped[UUID] = ref("bays")
    starts_at: Mapped[datetime] = mapped_column(TZ, index=True)
    ends_at: Mapped[datetime] = mapped_column(TZ)
    reason: Mapped[str | None] = mapped_column(Text)


class BayEquipment(TenantMixin, Base):
    __tablename__ = "bay_equipment"

    bay_id: Mapped[UUID] = ref("bays")
    name: Mapped[str] = mapped_column(Text)
    next_service_due: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)


# ---- Work orders and the schedule -------------------------------------------------


class WorkOrder(TenantMixin, Base):
    __tablename__ = "work_orders"
    __table_args__ = (UniqueConstraint("shop_id", "number"),)

    number: Mapped[int] = mapped_column(Integer)
    customer_id: Mapped[UUID] = ref("customers", ondelete=None)
    vehicle_id: Mapped[UUID] = ref("vehicles", ondelete=None)
    # draft | scheduled | in_progress | waiting_parts | completed | cancelled
    status: Mapped[str] = mapped_column(Text, default="draft", index=True)
    concern: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    promised_at: Mapped[datetime | None] = mapped_column(TZ)
    preferred_employee_id: Mapped[UUID | None] = ref("employees", nullable=True, ondelete=None)
    mileage_in: Mapped[int | None] = mapped_column(Integer)


class WorkOrderLine(TenantMixin, Base):
    __tablename__ = "work_order_lines"

    work_order_id: Mapped[UUID] = ref("work_orders")
    service_type_id: Mapped[UUID | None] = ref("service_types", nullable=True, ondelete=None)
    description: Mapped[str] = mapped_column(Text)
    price: Mapped[Decimal | None] = mapped_column(Money)
    status: Mapped[str] = mapped_column(Text, default="open")  # open | done | declined


class JobAssignment(TenantMixin, Base):
    """One block on the schedule: a mechanic and/or a bay, for a time range.

    A row with no employee is a bay hold (vehicle stays on the lift between steps).
    Exclusion constraints (db/rls.py) stop a mechanic or a bay being double-booked.
    """

    __tablename__ = "job_assignments"

    work_order_id: Mapped[UUID] = ref("work_orders")
    line_id: Mapped[UUID | None] = ref("work_order_lines", nullable=True)
    step_id: Mapped[UUID | None] = ref("service_steps", nullable=True, ondelete=None)
    label: Mapped[str] = mapped_column(Text, default="")
    employee_id: Mapped[UUID | None] = ref("employees", nullable=True, ondelete=None)
    supervisor_id: Mapped[UUID | None] = ref("employees", nullable=True, ondelete=None)
    bay_id: Mapped[UUID | None] = ref("bays", nullable=True, ondelete=None)
    starts_at: Mapped[datetime] = mapped_column(TZ, index=True)
    ends_at: Mapped[datetime] = mapped_column(TZ)
    # planned | in_progress | paused | done | cancelled | needs_reassignment
    status: Mapped[str] = mapped_column(Text, default="planned", index=True)
    started_at: Mapped[datetime | None] = mapped_column(TZ)
    finished_at: Mapped[datetime | None] = mapped_column(TZ)
    override_reason: Mapped[str | None] = mapped_column(Text)
    problem: Mapped[str | None] = mapped_column(Text)  # why it needs reassignment


class CalendarEvent(TenantMixin, Base):
    """Free-form calendar entries (meetings, closures, reminders, vendor visits)."""

    __tablename__ = "calendar_events"

    kind: Mapped[str] = mapped_column(Text, default="event")  # event | closure | reminder | meeting | vendor
    title: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(TZ, index=True)
    ends_at: Mapped[datetime] = mapped_column(TZ)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    employee_id: Mapped[UUID | None] = ref("employees", nullable=True, ondelete=None)
    bay_id: Mapped[UUID | None] = ref("bays", nullable=True, ondelete=None)
    series_id: Mapped[UUID | None] = _uuid(nullable=True)


# ---- Inventory --------------------------------------------------------------------


class Supplier(TenantMixin, Base):
    __tablename__ = "suppliers"

    name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    account_number: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class Part(TenantMixin, Base):
    __tablename__ = "parts"
    __table_args__ = (UniqueConstraint("shop_id", "sku"),)

    sku: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    on_hand: Mapped[int] = mapped_column(Integer, default=0)
    reorder_point: Mapped[int] = mapped_column(Integer, default=0)
    reorder_qty: Mapped[int] = mapped_column(Integer, default=0)
    unit_cost: Mapped[Decimal | None] = mapped_column(Money)
    unit_price: Mapped[Decimal | None] = mapped_column(Money)
    location: Mapped[str | None] = mapped_column(Text)
    supplier_id: Mapped[UUID | None] = ref("suppliers", nullable=True, ondelete=None)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class StockMovement(TenantMixin, Base):
    """Ledger of every change to on-hand stock."""

    __tablename__ = "stock_movements"

    part_id: Mapped[UUID] = ref("parts")
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)  # receive | use | adjust | return
    work_order_id: Mapped[UUID | None] = ref("work_orders", nullable=True, ondelete=None)
    po_id: Mapped[UUID | None] = ref("purchase_orders", nullable=True, ondelete=None)
    note: Mapped[str | None] = mapped_column(Text)


class PartReservation(TenantMixin, Base):
    __tablename__ = "part_reservations"

    part_id: Mapped[UUID] = ref("parts")
    work_order_id: Mapped[UUID] = ref("work_orders")
    qty: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, default="reserved")  # reserved | used | released


class PurchaseOrder(TenantMixin, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (UniqueConstraint("shop_id", "number"),)

    number: Mapped[int] = mapped_column(Integer)
    supplier_id: Mapped[UUID | None] = ref("suppliers", nullable=True, ondelete=None)
    status: Mapped[str] = mapped_column(Text, default="draft")  # draft | ordered | received | cancelled
    eta: Mapped[datetime | None] = mapped_column(TZ)
    provider: Mapped[str | None] = mapped_column(Text)  # catalog integration it was sent through
    external_ref: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class PoLine(TenantMixin, Base):
    __tablename__ = "po_lines"

    po_id: Mapped[UUID] = ref("purchase_orders")
    part_id: Mapped[UUID] = ref("parts", ondelete=None)
    qty: Mapped[int] = mapped_column(Integer)
    received_qty: Mapped[int] = mapped_column(Integer, default=0)
    unit_cost: Mapped[Decimal | None] = mapped_column(Money)


class SupplierPartLink(TenantMixin, Base):
    """Ties an inventory part to an entry in a connected parts catalog."""

    __tablename__ = "supplier_part_links"

    part_id: Mapped[UUID] = ref("parts")
    provider: Mapped[str] = mapped_column(Text)
    supplier_part_number: Mapped[str] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(Text)
    last_cost: Mapped[Decimal | None] = mapped_column(Money)
    last_eta_days: Mapped[int | None] = mapped_column(Integer)
    last_checked: Mapped[datetime | None] = mapped_column(TZ)


class CatalogItem(TenantMixin, Base):
    """Rows of a supplier price file uploaded by the shop (the CSV catalog provider)."""

    __tablename__ = "catalog_items"

    supplier_name: Mapped[str] = mapped_column(Text, default="Uploaded price file")
    part_number: Mapped[str] = mapped_column(Text, index=True)
    brand: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    fitment: Mapped[str | None] = mapped_column(Text)
    list_price: Mapped[Decimal | None] = mapped_column(Money)
    cost: Mapped[Decimal | None] = mapped_column(Money)
    qty_available: Mapped[int | None] = mapped_column(Integer)
    eta_days: Mapped[int | None] = mapped_column(Integer)


# ---- Intake -----------------------------------------------------------------------


class IntakeLink(TenantMixin, Base):
    __tablename__ = "intake_links"

    label: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(Text, default="kiosk")  # kiosk (reusable) | single
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(TZ)
    max_uses: Mapped[int | None] = mapped_column(Integer)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)


class IntakeSubmission(TenantMixin, Base):
    """Something waiting for staff review: a customer's intake form or a draft
    extracted from a phone call. Nothing here is live data until it is accepted."""

    __tablename__ = "intake_submissions"

    source: Mapped[str] = mapped_column(Text, default="link")  # link | call | staff
    link_id: Mapped[UUID | None] = ref("intake_links", nullable=True, ondelete=None)
    call_id: Mapped[UUID | None] = ref("calls", nullable=True, ondelete=None)
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, default="pending", index=True)  # pending | accepted | rejected
    customer_id: Mapped[UUID | None] = ref("customers", nullable=True, ondelete=None)
    vehicle_id: Mapped[UUID | None] = ref("vehicles", nullable=True, ondelete=None)
    work_order_id: Mapped[UUID | None] = ref("work_orders", nullable=True, ondelete=None)
    reviewed_by: Mapped[UUID | None] = _uuid(nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text)


# ---- Permissions ------------------------------------------------------------------


class AccessRequest(TenantMixin, Base):
    """A member asking for edit rights on one feature area."""

    __tablename__ = "access_requests"

    requester_id: Mapped[UUID] = _uuid(ForeignKey("users.id"))
    vertical: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    requested_hours: Mapped[int | None] = mapped_column(Integer)  # NULL = until revoked
    status: Mapped[str] = mapped_column(Text, default="pending", index=True)  # pending | approved | denied
    decided_by: Mapped[UUID | None] = _uuid(nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(TZ)
    note: Mapped[str | None] = mapped_column(Text)


class PermissionGrant(TenantMixin, Base):
    __tablename__ = "permission_grants"

    user_id: Mapped[UUID] = _uuid(ForeignKey("users.id"), index=True)
    vertical: Mapped[str] = mapped_column(Text)
    granted_by: Mapped[UUID] = _uuid()
    access_request_id: Mapped[UUID | None] = ref("access_requests", nullable=True, ondelete=None)
    expires_at: Mapped[datetime | None] = mapped_column(TZ)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)


class ChangeRequest(TenantMixin, Base):
    """A specific change waiting for a decision: proposed by someone without edit
    rights (needs a manager) or drafted by the assistant (needs the user's confirmation)."""

    __tablename__ = "change_requests"

    requester_id: Mapped[UUID | None] = _uuid(nullable=True)
    requester_name: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, default="ui")  # ui | agent
    vertical: Mapped[str] = mapped_column(Text)
    command: Mapped[str] = mapped_column(Text)
    args: Mapped[dict] = mapped_column(JSONB)
    summary: Mapped[str] = mapped_column(Text, default="")
    # What the change would do, captured by a dry run: [{entity_type, entity_id, op, before, after, changed_fields}]
    preview: Mapped[list] = mapped_column(JSONB, default=list)
    # Row versions seen by the dry run: {"table:id": version}. A mismatch at approval = stale.
    base_versions: Mapped[dict] = mapped_column(JSONB, default=dict)
    # manager = an owner/advisor must approve; self = the requester may confirm it themselves.
    needs: Mapped[str] = mapped_column(Text, default="manager")
    status: Mapped[str] = mapped_column(Text, default="pending", index=True)  # pending | applied | rejected | stale | cancelled
    decided_by: Mapped[UUID | None] = _uuid(nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(TZ)
    note: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSONB)


# ---- Knowledge store and assistant ------------------------------------------------


class Document(TenantMixin, Base):
    __tablename__ = "documents"

    title: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, default="file")  # file | call_transcript | note
    filename: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending")  # pending | indexed | failed
    error: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    customer_id: Mapped[UUID | None] = ref("customers", nullable=True)
    vehicle_id: Mapped[UUID | None] = ref("vehicles", nullable=True)
    work_order_id: Mapped[UUID | None] = ref("work_orders", nullable=True)
    call_id: Mapped[UUID | None] = ref("calls", nullable=True)
    uploaded_by: Mapped[UUID | None] = _uuid(nullable=True)


class DocChunk(TenantMixin, Base):
    __tablename__ = "doc_chunks"

    document_id: Mapped[UUID] = ref("documents")
    seq: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(get_settings().embedding_dim))
    embedding_model: Mapped[str | None] = mapped_column(Text)


class ChatThread(TenantMixin, Base):
    __tablename__ = "chat_threads"

    user_id: Mapped[UUID] = _uuid(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text, default="New conversation")


# ---- Integrations -----------------------------------------------------------------


class IntegrationCredential(TenantMixin, Base):
    __tablename__ = "integration_credentials"
    __table_args__ = (UniqueConstraint("shop_id", "key"),)

    key: Mapped[str] = mapped_column(Text)  # integration key, e.g. "google_calendar"
    status: Mapped[str] = mapped_column(Text, default="connected")  # connected | disabled | error
    # Fernet-encrypted JSON (tokens / API keys). Excluded from the change history.
    secret: Mapped[str | None] = mapped_column(Text)
    # Non-secret options (chosen calendar id, account label, enabled flags).
    options: Mapped[dict] = mapped_column(JSONB, default=dict)
    connected_by: Mapped[UUID | None] = _uuid(nullable=True)


class IntegrationCall(TenantMixin, Base):
    """Outbound call log for the Settings -> Integrations health view."""

    __tablename__ = "integration_calls"

    key: Mapped[str] = mapped_column(Text, index=True)
    operation: Mapped[str] = mapped_column(Text)
    ok: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class SyncMap(TenantMixin, Base):
    """Which external object mirrors which internal one (e.g. a Google event id)."""

    __tablename__ = "sync_map"
    __table_args__ = (UniqueConstraint("shop_id", "key", "entity_type", "entity_id"),)

    key: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[UUID] = _uuid()
    external_id: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str | None] = mapped_column(Text)


# ---- Communications ---------------------------------------------------------------


class Call(TenantMixin, Base):
    __tablename__ = "calls"
    __table_args__ = (UniqueConstraint("shop_id", "provider", "external_id"),)

    provider: Mapped[str] = mapped_column(Text, default="manual")
    external_id: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(Text, default="inbound")
    from_number: Mapped[str | None] = mapped_column(Text)
    to_number: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(TZ, index=True)
    duration_s: Mapped[int | None] = mapped_column(Integer)
    customer_id: Mapped[UUID | None] = ref("customers", nullable=True, ondelete=None)
    vehicle_id: Mapped[UUID | None] = ref("vehicles", nullable=True, ondelete=None)
    consent_noted: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(Text, default="new")  # new | unassigned | processed
    summary: Mapped[str | None] = mapped_column(Text)


class CallTranscript(TenantMixin, Base):
    __tablename__ = "call_transcripts"

    call_id: Mapped[UUID] = ref("calls")
    segments: Mapped[list] = mapped_column(JSONB, default=list)  # [{speaker, start_s, text}]
    text: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)


finalize()
