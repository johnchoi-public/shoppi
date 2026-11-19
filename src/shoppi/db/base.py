"""Declarative base and the conventions every per-shop ("tenant") table follows.

Conventions (enforced by `finalize()` and by tests/test_isolation.py):
- a tenant table has `id`, `shop_id`, `version`, `created_at`, `updated_at`;
- it is UNIQUE (shop_id, id), and a reference to another tenant table is a
  composite foreign key (shop_id, x_id) -> (shop_id, id), so a row can never
  point at a row of a different shop;
- row-level security keyed on `shop_id` is installed by db/rls.py.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Set by tenant_tx(); lets rows default their shop_id without handlers passing it.
current_shop_id: ContextVar[UUID | None] = ContextVar("current_shop_id", default=None)


def _default_shop_id() -> UUID:
    shop_id = current_shop_id.get()
    if shop_id is None:
        raise RuntimeError("tenant row created outside tenant_tx()")
    return shop_id


class Base(DeclarativeBase):
    pass


class TenantMixin:
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    shop_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("shops.id", ondelete="CASCADE"), index=True, default=_default_shop_id
    )
    # Bumped by a database trigger on every update; used to detect stale approvals.
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def ref(table: str, *, nullable: bool = False, ondelete: str | None = "CASCADE", index: bool = True):
    """A column referencing another tenant table. `finalize()` turns it into a
    composite (shop_id, col) foreign key."""
    return mapped_column(
        PG_UUID(as_uuid=True), nullable=nullable, index=index, info={"ref": (table, ondelete)}
    )


def is_tenant_table(table) -> bool:
    return "shop_id" in table.c and table.name not in GLOBAL_TABLES


# Tables that are deliberately not scoped by a plain `shop_id = current shop` policy.
# Each has its own policy (or none) in db/rls.py; anything else must be a tenant table.
GLOBAL_TABLES = {"shops", "users", "auth_sessions", "memberships", "cert_types", "vin_cache", "audit_log"}


def finalize() -> None:
    """Add UNIQUE(shop_id, id) and composite foreign keys. Call once, after all models are defined."""
    for table in Base.metadata.tables.values():
        if "shop_id" not in table.c or table.name == "audit_log" or table.name == "cert_types":
            continue
        if "id" in table.c:
            table.append_constraint(UniqueConstraint("shop_id", "id", name=f"uq_{table.name}_shop_id_id"))
    for table in Base.metadata.tables.values():
        for col in list(table.c):
            target = col.info.get("ref")
            if not target:
                continue
            target_table, ondelete = target
            table.append_constraint(
                ForeignKeyConstraint(
                    ["shop_id", col.name],
                    [f"{target_table}.shop_id", f"{target_table}.id"],
                    ondelete=ondelete,
                    name=f"fk_{table.name}_{col.name}",
                )
            )
