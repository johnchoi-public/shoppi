"""Who may see and change what.

Access is decided per feature area ("vertical"), never per screen or per
endpoint. Owners and advisors can edit everything. Mechanics can view most
areas; to change something they either send a change request for that one edit
or ask for edit rights on the whole area (a grant, optionally time-limited).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from shoppi.core.actor import Actor
from shoppi.db.models import PermissionGrant

# key -> label shown in the UI
VERTICALS: dict[str, str] = {
    "intake": "Customer intake",
    "schedule": "Calendar & job scheduling",
    "customers": "Customers & vehicles",
    "work_orders": "Work orders",
    "inventory": "Parts inventory",
    "shifts": "Shifts & time off",
    "bays": "Service bays",
    "team": "Team & certifications",
    "services": "Service catalog",
    "documents": "Documents",
    "communications": "Phone calls",
    "admin": "Shop settings, members & integrations",
}

# `admin` can never be granted: it controls grants themselves.
GRANTABLE = [v for v in VERTICALS if v != "admin"]

MECHANIC_VIEW = {"intake", "schedule", "customers", "work_orders", "inventory", "shifts", "bays", "team", "services", "documents"}

RANK = {"none": 0, "view": 1, "edit": 2}


def role_default(role: str, vertical: str) -> str:
    # "self": things every signed-in member may do (ask for access, see their own requests).
    if vertical == "self":
        return "edit" if role in ("owner", "advisor", "mechanic", "system") else "none"
    # "public_intake": the one thing an anonymous customer intake link may do.
    if vertical == "public_intake":
        return "edit" if role in ("public", "system") else "none"
    if role in ("owner", "advisor", "system"):
        return "edit"
    if role == "mechanic":
        return "view" if vertical in MECHANIC_VIEW else "none"
    return "none"


def active_grants(tx: Session, actor: Actor) -> set[str]:
    if actor.user_id is None:
        return set()
    now = datetime.now(timezone.utc)
    rows = tx.scalars(
        select(PermissionGrant.vertical).where(
            PermissionGrant.user_id == actor.user_id,
            PermissionGrant.revoked_at.is_(None),
            (PermissionGrant.expires_at.is_(None)) | (PermissionGrant.expires_at > now),
        )
    )
    return {v for v in rows if v in GRANTABLE}


def authorize(tx: Session, actor: Actor, vertical: str) -> str:
    """Return the actor's level on a vertical: none | view | edit.

    Evaluated on every command, so a revoked or expired grant stops working
    immediately, even in the middle of a chat with the assistant.
    """
    level = role_default(actor.role, vertical)
    if actor.role != "mechanic" or level == "edit":
        return level
    if vertical in active_grants(tx, actor):
        return "edit"
    if vertical == "communications" and _mechanics_can_read_calls(tx, actor):
        return "view"
    return level


def _mechanics_can_read_calls(tx: Session, actor: Actor) -> bool:
    """Call transcripts are hidden from mechanics unless the shop turns this on."""
    from shoppi.db.models import Shop

    shop = tx.get(Shop, actor.shop_id)
    return bool(shop and (shop.settings or {}).get("mechanics_can_read_calls"))


def permission_map(tx: Session, actor: Actor) -> dict[str, str]:
    return {v: authorize(tx, actor, v) for v in VERTICALS}
