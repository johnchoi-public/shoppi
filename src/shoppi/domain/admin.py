"""Shop settings, members, access requests, change requests and the change history."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field
from sqlalchemy import select

from shoppi.auth.service import new_token, token_hash
from shoppi.config import get_settings
from shoppi.core.commands import Conflict, Ctx, Forbidden, IdArg, Invalid, change_request_out, command, patch, query
from shoppi.core.timeutil import localize_json, to_local
from shoppi.core.permissions import GRANTABLE, VERTICALS, permission_map
from shoppi.db.models import (
    AccessRequest, AuditLog, ChangeRequest, Employee, Invite, Membership, PermissionGrant, User,
)

# ---- Shop -------------------------------------------------------------------------


class ShopPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    timezone: str | None = None
    phone: str | None = None
    address: str | None = None
    hours: dict[str, list[str] | None] | None = None
    settings: dict | None = None


@query("shop.get", vertical="self", description="Shop name, timezone, opening hours and settings.")
def shop_get(ctx: Ctx) -> dict:
    out = {
        "id": str(ctx.shop.id), "name": ctx.shop.name, "timezone": ctx.shop.timezone, "phone": ctx.shop.phone,
        "address": ctx.shop.address, "hours": ctx.shop.hours, "settings": ctx.shop.settings,
    }
    if ctx.actor.is_manager:
        out["hook_id"] = ctx.shop.hook_id
    return out


@command("shop.update", vertical="admin", managers_only=True, description="Change shop details, opening hours or settings.")
def shop_update(ctx: Ctx, args: ShopPatch) -> dict:
    if args.timezone:
        try:
            ZoneInfo(args.timezone)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise Invalid(f"'{args.timezone}' is not a valid IANA timezone (e.g. America/Chicago).") from e
    if args.settings is not None:
        args.settings = {**(ctx.shop.settings or {}), **args.settings}
    patch(ctx.shop, args)
    ctx.tx.flush()
    return shop_get(ctx)


# ---- Members ----------------------------------------------------------------------


class InviteIn(BaseModel):
    email: str
    role: Literal["owner", "advisor", "mechanic"] = "mechanic"
    employee_id: UUID | None = None


class MemberPatch(BaseModel):
    membership_id: UUID
    role: Literal["owner", "advisor", "mechanic"] | None = None
    status: Literal["active", "disabled"] | None = None


@query("members.list", vertical="team", description="People with a login for this shop, with their role.")
def members_list(ctx: Ctx) -> dict:
    rows = ctx.tx.execute(
        select(Membership, User).join(User, User.id == Membership.user_id).where(Membership.shop_id == ctx.actor.shop_id)
        .order_by(User.name)
    ).all()
    members = [{"id": str(m.id), "user_id": str(u.id), "name": u.name, "email": u.email, "role": m.role,
                "status": m.status, "employee_id": str(m.employee_id) if m.employee_id else None} for m, u in rows]
    invites = []
    if ctx.actor.is_manager:
        invites = [ctx.out(i) for i in ctx.tx.scalars(select(Invite).where(Invite.accepted_at.is_(None), Invite.expires_at > ctx.now()))]
    return {"members": members, "invites": invites}


@command("members.invite", vertical="admin", managers_only=True, agent=False,
         description="Invite someone to the shop by email. Returns a one-time link to send them.")
def members_invite(ctx: Ctx, args: InviteIn) -> dict:
    email = args.email.strip().lower()
    if "@" not in email:
        raise Invalid("Enter a valid email address.")
    already = ctx.tx.scalar(select(Membership.id).join(User, User.id == Membership.user_id)
                            .where(User.email == email, Membership.shop_id == ctx.actor.shop_id))
    if already:
        raise Conflict("That person is already a member of this shop.")
    if args.employee_id:
        ctx.get(Employee, args.employee_id, "Employee")
    token = new_token()
    invite = Invite(email=email, role=args.role, token_hash=token_hash(token), employee_id=args.employee_id,
                    expires_at=ctx.now() + timedelta(days=7))
    ctx.tx.add(invite)
    ctx.tx.flush()
    # The token is returned once and never stored; only its hash is kept.
    return {**ctx.out(invite), "link": f"{get_settings().base_url}/invite/{token}"}


@command("members.revoke_invite", vertical="admin", managers_only=True, agent=False, description="Cancel a pending invite.")
def members_revoke_invite(ctx: Ctx, args: IdArg) -> dict:
    ctx.tx.delete(ctx.get(Invite, args.id, "Invite"))
    return {"ok": True}


@command("members.update", vertical="admin", managers_only=True, agent=False, description="Change a member's role or disable their login.")
def members_update(ctx: Ctx, args: MemberPatch) -> dict:
    m = ctx.get(Membership, args.membership_id, "Member")
    if m.user_id == ctx.actor.user_id and (args.status == "disabled" or (args.role and args.role == "mechanic")):
        raise Invalid("You can't remove your own manager access.")
    if args.role:
        m.role = args.role
    if args.status:
        m.status = args.status
    return {"ok": True}



# ---- Access requests and grants ---------------------------------------------------


class AccessRequestIn(BaseModel):
    vertical: str
    reason: str | None = None
    hours: int | None = Field(default=None, ge=1, le=24 * 90, description="How long the access is needed; empty = until revoked.")


class AccessDecision(BaseModel):
    id: UUID
    approve: bool
    hours: int | None = Field(default=None, ge=1, le=24 * 365)
    note: str | None = None


class GrantIn(BaseModel):
    user_id: UUID
    vertical: str
    hours: int | None = Field(default=None, ge=1, le=24 * 365)


@query("me.permissions", vertical="self", agent=False)
def my_permissions(ctx: Ctx) -> dict:
    return {"permissions": permission_map(ctx.tx, ctx.actor), "verticals": VERTICALS, "grantable": GRANTABLE}


@command("access.request", vertical="self", agent=False, description="Ask for edit rights on one feature area.")
def access_request(ctx: Ctx, args: AccessRequestIn) -> dict:
    if args.vertical not in GRANTABLE:
        raise Invalid("That area can't be requested.")
    if permission_map(ctx.tx, ctx.actor)[args.vertical] == "edit":
        raise Conflict("You already have edit access to that area.")
    pending = ctx.tx.scalar(select(AccessRequest.id).where(
        AccessRequest.requester_id == ctx.actor.user_id, AccessRequest.vertical == args.vertical, AccessRequest.status == "pending"))
    if pending:
        raise Conflict("You already have a pending request for that area.")
    row = AccessRequest(requester_id=ctx.actor.user_id, vertical=args.vertical, reason=args.reason, requested_hours=args.hours)
    ctx.tx.add(row)
    ctx.tx.flush()
    return ctx.out(row)


@command("access.decide", vertical="admin", managers_only=True, agent=False, description="Approve or deny an access request.")
def access_decide(ctx: Ctx, args: AccessDecision) -> dict:
    row = ctx.get(AccessRequest, args.id, "Access request")
    if row.status != "pending":
        raise Conflict(f"This request was already {row.status}.")
    row.status = "approved" if args.approve else "denied"
    row.decided_by, row.decided_at, row.note = ctx.actor.user_id, ctx.now(), args.note
    if args.approve:
        hours = args.hours if args.hours is not None else row.requested_hours
        ctx.tx.add(PermissionGrant(user_id=row.requester_id, vertical=row.vertical, granted_by=ctx.actor.user_id,
                                   access_request_id=row.id, expires_at=ctx.now() + timedelta(hours=hours) if hours else None))
    return ctx.out(row)


@command("access.grant", vertical="admin", managers_only=True, agent=False, description="Give a member edit rights on one feature area.")
def access_grant(ctx: Ctx, args: GrantIn) -> dict:
    if args.vertical not in GRANTABLE:
        raise Invalid("That area can't be granted.")
    if not ctx.tx.scalar(select(Membership.id).where(Membership.user_id == args.user_id, Membership.shop_id == ctx.actor.shop_id)):
        raise Invalid("That person is not a member of this shop.")
    grant = PermissionGrant(user_id=args.user_id, vertical=args.vertical, granted_by=ctx.actor.user_id,
                            expires_at=ctx.now() + timedelta(hours=args.hours) if args.hours else None)
    ctx.tx.add(grant)
    ctx.tx.flush()
    return ctx.out(grant)


@command("access.revoke", vertical="admin", managers_only=True, agent=False, description="Revoke a grant.")
def access_revoke(ctx: Ctx, args: IdArg) -> dict:
    grant = ctx.get(PermissionGrant, args.id, "Grant")
    grant.revoked_at = ctx.now()
    return {"ok": True}


def _user_names(ctx: Ctx) -> dict[UUID, str]:
    rows = ctx.tx.execute(select(User.id, User.name).join(Membership, Membership.user_id == User.id)
                          .where(Membership.shop_id == ctx.actor.shop_id)).all()
    return {uid: name for uid, name in rows}


@query("access.list", vertical="self", agent=False)
def access_list(ctx: Ctx) -> dict:
    """Access requests and grants. Managers see everyone's; others see their own."""
    names = _user_names(ctx)
    rq = select(AccessRequest).order_by(AccessRequest.created_at.desc()).limit(200)
    gq = select(PermissionGrant).order_by(PermissionGrant.created_at.desc()).limit(200)
    if not ctx.actor.is_manager:
        rq = rq.where(AccessRequest.requester_id == ctx.actor.user_id)
        gq = gq.where(PermissionGrant.user_id == ctx.actor.user_id)
    now = ctx.now()
    return {
        "requests": [ctx.out(r, requester_name=names.get(r.requester_id, ""), vertical_label=VERTICALS.get(r.vertical, r.vertical))
                     for r in ctx.tx.scalars(rq)],
        "grants": [ctx.out(g, user_name=names.get(g.user_id, ""), vertical_label=VERTICALS.get(g.vertical, g.vertical),
                           active=g.revoked_at is None and (g.expires_at is None or g.expires_at > now))
                   for g in ctx.tx.scalars(gq)],
    }


# ---- Change requests --------------------------------------------------------------


class ChangeRequestFilter(BaseModel):
    status: str | None = "pending"
    limit: int = Field(default=100, le=500)


@query("change_requests.list", vertical="self", agent=False)
def change_requests_list(ctx: Ctx, args: ChangeRequestFilter) -> list[dict]:
    """Proposed changes. Managers see all; others see the ones they made."""
    q = select(ChangeRequest).order_by(ChangeRequest.created_at.desc()).limit(args.limit)
    if args.status:
        q = q.where(ChangeRequest.status == args.status)
    if not ctx.actor.is_manager:
        q = q.where(ChangeRequest.requester_id == ctx.actor.user_id)
    return [{**change_request_out(ctx, cr), "vertical_label": VERTICALS.get(cr.vertical, cr.vertical)} for cr in ctx.tx.scalars(q)]


# ---- History ----------------------------------------------------------------------

ENTITY_LABELS = {
    "customers": "Customer", "vehicles": "Vehicle", "work_orders": "Work order", "work_order_lines": "Work order line",
    "job_assignments": "Scheduled job", "calendar_events": "Calendar event", "employees": "Employee",
    "employee_certs": "Certification", "shifts": "Shift", "time_off": "Time off", "bays": "Bay", "bay_hours": "Bay hours",
    "bay_closures": "Bay closure", "bay_equipment": "Bay equipment", "parts": "Part", "stock_movements": "Stock movement",
    "part_reservations": "Part reservation", "purchase_orders": "Purchase order", "po_lines": "Purchase order line",
    "suppliers": "Supplier", "supplier_part_links": "Catalog link", "service_types": "Service", "service_steps": "Service step",
    "step_cert_requirements": "Certification requirement", "service_parts": "Service part", "intake_links": "Intake link",
    "intake_submissions": "Intake submission", "access_requests": "Access request", "permission_grants": "Access grant",
    "change_requests": "Change request", "documents": "Document", "integration_credentials": "Integration",
    "calls": "Phone call", "call_transcripts": "Call transcript", "memberships": "Member", "invites": "Invite",
    "shops": "Shop settings", "cert_types": "Certification type",
}
# History rows about these tables are only shown to people who can see that area.
SENSITIVE = {"calls": "communications", "call_transcripts": "communications", "integration_credentials": "admin",
             "invites": "admin", "memberships": "admin"}


class HistoryFilter(BaseModel):
    entity_type: str | None = None
    entity_id: UUID | None = None
    user_id: UUID | None = None
    before_id: int | None = None
    limit: int = Field(default=100, le=500)


def _row_label(data: dict | None) -> str | None:
    if not data:
        return None
    for key in ("name", "title", "label", "description", "summary", "sku", "email", "command"):
        if data.get(key):
            return str(data[key])[:80]
    if data.get("number"):
        return f"#{data['number']}"
    return None


@query("history.list", vertical="self", description="The change history: who changed what and when.")
def history_list(ctx: Ctx, args: HistoryFilter) -> list[dict]:
    perms = permission_map(ctx.tx, ctx.actor)
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(args.limit)
    if args.entity_type:
        q = q.where(AuditLog.entity_type == args.entity_type)
    if args.entity_id:
        q = q.where(AuditLog.entity_id == args.entity_id)
    if args.user_id:
        q = q.where(AuditLog.actor_user_id == args.user_id)
    if args.before_id:
        q = q.where(AuditLog.id < args.before_id)
    hidden = [table for table, vertical in SENSITIVE.items() if perms.get(vertical) == "none"]
    if hidden:
        q = q.where(AuditLog.entity_type.not_in(hidden))
    names = _user_names(ctx)
    out = []
    for r in ctx.tx.scalars(q):
        out.append({
            "id": r.id, "at": to_local(r.at, ctx.tz), "actor_type": r.actor_type,
            "actor_name": names.get(r.actor_user_id) or r.actor_name or r.actor_type.replace("_", " ").title(),
            "approved_by_name": names.get(r.approved_by) if r.approved_by else None,
            "source": r.source, "vertical": r.vertical, "command": r.command,
            "entity_type": r.entity_type, "entity_label": ENTITY_LABELS.get(r.entity_type, r.entity_type),
            "entity_id": str(r.entity_id) if r.entity_id else None, "label": _row_label(r.after or r.before),
            "op": r.op, "before": localize_json(r.before, ctx.tz), "after": localize_json(r.after, ctx.tz), "changed_fields": r.changed_fields,
            "change_request_id": str(r.change_request_id) if r.change_request_id else None,
        })
    return out


def require_manager(ctx: Ctx) -> None:
    if not (ctx.actor.is_manager or ctx.actor.role == "system"):
        raise Forbidden("Only an owner or advisor can do that.")
