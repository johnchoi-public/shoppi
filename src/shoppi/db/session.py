"""The only way application code talks to the database.

`tenant_tx(actor)` opens a transaction bound to one shop: it sets transaction-
local settings that the row-level security policies and the audit trigger read.
Because they are transaction-local, a pooled connection can never carry one
shop's identity into another request.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID, uuid4

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from shoppi.config import get_settings
from shoppi.core.actor import Actor
from shoppi.db.base import current_shop_id

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def configure_engine(url: str) -> Engine:
    """Point the app at a different database (tests)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = create_engine(url, pool_pre_ping=True)
    return _engine


_SET = text(
    "SELECT set_config('app.shop_id', :shop_id, true), set_config('app.user_id', :user_id, true),"
    " set_config('app.actor', :actor, true), set_config('app.actor_name', :actor_name, true),"
    " set_config('app.actor_ref', :actor_ref, true), set_config('app.source', :source, true),"
    " set_config('app.vertical', :vertical, true), set_config('app.command', :command, true),"
    " set_config('app.request_id', :request_id, true),"
    " set_config('app.change_request_id', :change_request_id, true),"
    " set_config('app.approved_by', :approved_by, true)"
)


def _s(value) -> str:
    return "" if value is None else str(value)


@contextmanager
def tenant_tx(
    actor: Actor,
    *,
    command: str = "",
    vertical: str = "",
    request_id: str | None = None,
    change_request_id: UUID | None = None,
    approved_by: UUID | None = None,
) -> Iterator[Session]:
    """A transaction scoped to `actor.shop_id`. Commits on success, rolls back on error."""
    token = current_shop_id.set(actor.shop_id)
    try:
        with Session(get_engine(), expire_on_commit=False) as session, session.begin():
            session.execute(
                _SET,
                {
                    "shop_id": str(actor.shop_id),
                    "user_id": _s(actor.user_id),
                    "actor": actor.kind,
                    "actor_name": actor.name,
                    "actor_ref": _s(actor.ref),
                    "source": actor.source,
                    "vertical": vertical,
                    "command": command,
                    "request_id": request_id or uuid4().hex,
                    "change_request_id": _s(change_request_id),
                    "approved_by": _s(approved_by),
                },
            )
            session.info["actor"] = actor
            yield session
    finally:
        current_shop_id.reset(token)


@contextmanager
def identity_tx(user_id: UUID | None = None) -> Iterator[Session]:
    """A transaction with no shop: for the global identity tables (users, sessions)
    and for listing one user's own memberships. Tenant tables return no rows here."""
    with Session(get_engine(), expire_on_commit=False) as session, session.begin():
        session.execute(
            text("SELECT set_config('app.user_id', :u, true), set_config('app.shop_id', '', true)"),
            {"u": _s(user_id)},
        )
        yield session
