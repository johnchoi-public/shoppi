"""The single write path.

Every change to shop data is a registered *command*: a name, the feature area
it belongs to, a pydantic args model and a handler. `execute()` is the only
caller of handlers, so authorization, change requests and history apply the
same way whether the change comes from a screen, the assistant, the customer
intake link or an integration. Reads are registered the same way as *queries*.

    @command("customers.create", vertical="customers", description="Add a customer.")
    def create_customer(ctx: Ctx, args: CustomerIn) -> dict: ...

What `execute()` does with a command:
- actor may edit the area            -> runs it, returns {"status": "applied"}
- actor may only view the area       -> dry-runs it, stores the resulting diff as a
                                        change request for a manager, returns "pending"
- actor is the assistant             -> always a change request, which the user
                                        confirms (or a manager, if the user can't edit)
- otherwise                          -> Forbidden
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shoppi.core.actor import Actor
from shoppi.core.permissions import RANK, authorize
from shoppi.core.timeutil import jsonable, localize, localize_json, row_dict, to_local
from shoppi.db.base import Base
from shoppi.db.models import AuditLog, ChangeRequest, Membership, Shop, User
from shoppi.db.session import tenant_tx

# ---- Errors -----------------------------------------------------------------------


class AppError(Exception):
    status = 400
    code = "error"

    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.message = message
        self.extra = extra


class Forbidden(AppError):
    status, code = 403, "forbidden"


class NotFound(AppError):
    status, code = 404, "not_found"


class Invalid(AppError):
    status, code = 422, "invalid"


class Conflict(AppError):
    status, code = 409, "conflict"


class Unavailable(AppError):
    """Something this request depends on (e.g. the embedding model) can't be reached."""

    status, code = 503, "unavailable"


# ---- Registry ---------------------------------------------------------------------


class NoArgs(BaseModel):
    pass


class IdArg(BaseModel):
    id: UUID


@dataclass
class Ctx:
    """What a handler gets: the shop-scoped transaction, who is acting, and time helpers."""

    tx: Session
    actor: Actor
    shop: Shop
    tz: ZoneInfo
    # Work to run after the transaction commits (e.g. mirror to an external calendar).
    after_commit: list[Callable[[], None]] = field(default_factory=list)
    # Set when this run is an approved change request: the approving manager's user id.
    approved_by: UUID | None = None

    @property
    def manager_authority(self) -> bool:
        """True if a manager is behind this action (acting directly, or having approved it)."""
        return self.actor.is_manager or self.actor.role == "system" or self.approved_by is not None

    def local(self, dt: datetime | None) -> datetime | None:
        return localize(dt, self.tz)

    def out(self, row, **extra) -> dict:
        return {**row_dict(row, self.tz), **jsonable(extra, self.tz)}

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def get(self, model, id_: UUID | str | None, what: str | None = None):
        row = self.tx.get(model, id_) if id_ else None
        if row is None:
            raise NotFound(f"{what or model.__name__} not found")
        return row


@dataclass
class Spec:
    name: str
    vertical: str
    args_model: type[BaseModel]
    handler: Callable[[Ctx, Any], Any]
    description: str = ""
    level: str = "edit"  # level needed to run it directly
    managers_only: bool = False  # never available as a change request
    external: bool = False  # has effects outside the database: no dry run
    agent: bool = True  # exposed to the assistant as a tool


COMMANDS: dict[str, Spec] = {}
QUERIES: dict[str, Spec] = {}


def command(name: str, *, vertical: str, description: str = "", level: str = "edit",
            managers_only: bool = False, external: bool = False, agent: bool = True):
    def deco(fn):
        args_model = _args_model(fn)
        COMMANDS[name] = Spec(name, vertical, args_model, _handler(fn), description or (fn.__doc__ or "").strip(),
                              level, managers_only, external, agent)
        return fn

    return deco


def query(name: str, *, vertical: str, description: str = "", agent: bool = True):
    def deco(fn):
        QUERIES[name] = Spec(name, vertical, _args_model(fn), _handler(fn), description or (fn.__doc__ or "").strip(),
                             "view", agent=agent)
        return fn

    return deco


def _handler(fn):
    """Handlers that need no arguments may be written as `def handler(ctx)`."""
    import inspect

    if len(inspect.signature(fn).parameters) >= 2:
        return fn
    return lambda ctx, _args: fn(ctx)


def _args_model(fn) -> type[BaseModel]:
    import inspect

    params = list(inspect.signature(fn).parameters.values())
    if len(params) < 2:
        return NoArgs
    hints = inspect.get_annotations(fn, eval_str=True)
    return hints.get(params[1].name, NoArgs)


# ---- Execution --------------------------------------------------------------------


def _ctx(tx: Session, actor: Actor) -> Ctx:
    shop = tx.get(Shop, actor.shop_id)
    if shop is None:
        raise NotFound("shop not found")
    return Ctx(tx=tx, actor=actor, shop=shop, tz=ZoneInfo(shop.timezone))


def _validate(spec: Spec, raw: dict | BaseModel | None) -> BaseModel:
    if isinstance(raw, spec.args_model):
        return raw
    try:
        return spec.args_model.model_validate(raw or {})
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        raise Invalid(problems or "invalid input") from e


def _friendly_integrity(e: IntegrityError) -> AppError:
    msg = str(e.orig)
    if "ex_job_employee" in msg:
        return Conflict("That mechanic is already booked at that time.")
    if "ex_job_bay" in msg:
        return Conflict("That bay is already booked at that time.")
    if "ck_part_on_hand" in msg:
        return Conflict("Not enough stock on hand.")
    if "unique" in msg.lower():
        return Conflict("That already exists.")
    if "foreign key" in msg.lower():
        return Conflict("This record is still used elsewhere, or refers to something that doesn't exist.")
    if "ck_" in msg:
        return Invalid("The end time must be after the start time.")
    return Conflict("The change conflicts with existing data.")


def run_query(actor: Actor, name: str, raw_args: dict | None = None):
    spec = QUERIES.get(name)
    if spec is None:
        raise NotFound(f"unknown query {name}")
    args = _validate(spec, raw_args)
    with tenant_tx(actor) as tx:
        if RANK[authorize(tx, actor, spec.vertical)] < RANK["view"]:
            raise Forbidden(f"You don't have access to {spec.vertical.replace('_', ' ')}.")
        ctx = _ctx(tx, actor)
        return jsonable(spec.handler(ctx, args), ctx.tz)


def execute(actor: Actor, name: str, raw_args: dict | BaseModel | None = None) -> dict:
    """Run a command, or turn it into a change request. See the module docstring."""
    spec = COMMANDS.get(name)
    if spec is None:
        raise NotFound(f"unknown command {name}")
    args = _validate(spec, raw_args)

    with tenant_tx(actor) as tx:
        level = authorize(tx, actor, spec.vertical)

    needed = RANK[spec.level]
    if RANK[level] >= needed and not (actor.kind == "agent" and spec.level == "edit"):
        if spec.managers_only and not (actor.is_manager or actor.role == "system"):
            raise Forbidden("Only an owner or advisor can do that.")
        return {"status": "applied", "result": _apply(actor, spec, args)}

    # Not allowed to run it directly: can it become a change request?
    if level == "none" or spec.managers_only and not actor.is_manager:
        raise Forbidden(f"You don't have access to {spec.vertical.replace('_', ' ')}.")
    needs = "self" if RANK[level] >= needed else "manager"
    return {"status": "pending", "change_request": _propose(actor, spec, args, needs)}


def _apply(actor: Actor, spec: Spec, args: BaseModel, *, change_request_id: UUID | None = None,
           approved_by: UUID | None = None, extra: Callable[[Ctx, Any], None] | None = None):
    try:
        with tenant_tx(actor, command=spec.name, vertical=spec.vertical,
                       change_request_id=change_request_id, approved_by=approved_by) as tx:
            ctx = _ctx(tx, actor)
            ctx.approved_by = approved_by
            result = jsonable(spec.handler(ctx, args), ctx.tz)
            if extra:
                extra(ctx, result)
            tx.flush()
    except IntegrityError as e:
        raise _friendly_integrity(e) from e
    for hook in ctx.after_commit:
        try:
            hook()
        except Exception:  # an integration failing must never undo a saved change
            pass
    return result


class _Rollback(Exception):
    pass


def _propose(actor: Actor, spec: Spec, args: BaseModel, needs: str) -> dict:
    """Dry-run the command to capture exactly what it would change, undo it, and
    store that as a pending change request."""
    preview: list[dict] = []
    base_versions: dict[str, int] = {}
    if not spec.external:
        request_id = uuid4().hex
        try:
            with tenant_tx(actor, command=spec.name, vertical=spec.vertical, request_id=request_id) as tx:
                ctx = _ctx(tx, actor)
                spec.handler(ctx, args)
                tx.flush()
                for row in tx.scalars(select(AuditLog).where(AuditLog.request_id == request_id).order_by(AuditLog.id)):
                    preview.append({
                        "entity_type": row.entity_type, "entity_id": str(row.entity_id), "op": row.op,
                        "before": row.before, "after": row.after, "changed_fields": row.changed_fields,
                    })
                    if row.op != "insert" and row.before and "version" in row.before:
                        base_versions[f"{row.entity_type}:{row.entity_id}"] = row.before["version"]
                raise _Rollback
        except _Rollback:
            pass
        except IntegrityError as e:
            raise _friendly_integrity(e) from e

    with tenant_tx(actor, command="change_requests.create", vertical=spec.vertical) as tx:
        ctx = _ctx(tx, actor)
        cr = ChangeRequest(
            requester_id=actor.user_id, requester_name=actor.name, source="agent" if actor.kind == "agent" else "ui",
            vertical=spec.vertical, command=spec.name, args=args.model_dump(mode="json", exclude_unset=True),
            summary=spec.description, preview=preview, base_versions=base_versions, needs=needs,
        )
        tx.add(cr)
        tx.flush()
        return change_request_out(ctx, cr)


def change_request_out(ctx: Ctx, cr: ChangeRequest) -> dict:
    out = ctx.out(cr)
    out["preview"] = localize_json(cr.preview, ctx.tz)
    can_self = cr.needs == "self" and cr.requester_id == ctx.actor.user_id
    out["can_decide"] = cr.status == "pending" and (can_self or ctx.actor.is_manager)
    out["can_cancel"] = cr.status == "pending" and cr.requester_id == ctx.actor.user_id
    return out


def decide_change_request(actor: Actor, cr_id: UUID, approve: bool, note: str | None = None) -> dict:
    """Approve (and apply), reject or cancel a pending change request."""
    with tenant_tx(actor) as tx:
        cr = tx.get(ChangeRequest, cr_id)
        if cr is None:
            raise NotFound("change request not found")
        if cr.status != "pending":
            raise Conflict(f"This request was already {cr.status}.")
        spec = COMMANDS.get(cr.command)
        level = authorize(tx, actor, cr.vertical)
        is_requester = cr.requester_id is not None and cr.requester_id == actor.user_id
        requester_role = tx.scalar(select(Membership.role).where(Membership.user_id == cr.requester_id, Membership.shop_id == actor.shop_id))
        requester_name = tx.scalar(select(User.name).where(User.id == cr.requester_id)) or cr.requester_name or ""
        needs, command_name, args, base_versions, source = cr.needs, cr.command, dict(cr.args), dict(cr.base_versions), cr.source

    if not approve:
        if not (is_requester or actor.is_manager):
            raise Forbidden("Only the requester or a manager can decline this.")
        return _close(actor, cr_id, "cancelled" if is_requester else "rejected", note)

    if spec is None:
        raise NotFound(f"unknown command {command_name}")
    if needs == "self":
        if not (is_requester or actor.is_manager) or level != "edit":
            raise Forbidden("You can't confirm this change.")
    else:
        # A proposal that needed a manager can't be approved by the person who made it,
        # even if they have since been granted edit rights.
        if level != "edit" or (is_requester and not actor.is_manager):
            raise Forbidden("An owner or advisor must approve this change.")

    # Stale check: has anything this change touches moved on since it was proposed?
    with tenant_tx(actor) as tx:
        for key, version in base_versions.items():
            table, _, row_id = key.partition(":")
            if table not in Base.metadata.tables:
                continue
            current = tx.execute(text(f'SELECT version FROM "{table}" WHERE id = :id'), {"id": row_id}).scalar()
            if current != version:
                stale = True
                break
        else:
            stale = False
    if stale:
        return _close(actor, cr_id, "stale", "The record changed after this was requested.")

    requester = Actor(
        shop_id=actor.shop_id, user_id=cr.requester_id, role=requester_role or "mechanic",
        kind="agent" if source == "agent" else "user", source=source, name=requester_name,
    )
    validated = _validate(spec, args)

    def mark_applied(ctx: Ctx, result):
        row = ctx.tx.get(ChangeRequest, cr_id)
        row.status, row.decided_by, row.decided_at, row.note = "applied", actor.user_id, ctx.now(), note
        row.result = result if isinstance(result, dict) else {"value": result}

    result = _apply(requester, spec, validated, change_request_id=cr_id, approved_by=actor.user_id, extra=mark_applied)
    return {"status": "applied", "result": result}


def _close(actor: Actor, cr_id: UUID, status: str, note: str | None) -> dict:
    with tenant_tx(actor, command=f"change_requests.{status}", vertical="admin") as tx:
        row = tx.get(ChangeRequest, cr_id)
        row.status, row.decided_by, row.decided_at, row.note = status, actor.user_id, datetime.now(timezone.utc), note
    return {"status": status}


# ---- Small helpers shared by handlers ---------------------------------------------


def patch(row, args: BaseModel, *, skip: tuple[str, ...] = ("id",)) -> None:
    """Apply only the fields the caller actually sent (so a field can be cleared
    by sending null, and untouched fields stay untouched)."""
    for key in args.model_fields_set:
        if key not in skip and hasattr(row, key):
            setattr(row, key, getattr(args, key))


def local_iso(dt: datetime | None, tz: ZoneInfo) -> str | None:
    return to_local(dt, tz)
