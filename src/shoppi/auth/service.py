"""Accounts, shops and sessions.

- A *user* is a global identity (email + password and/or Google).
- A *membership* links a user to a shop with a role (owner | advisor | mechanic).
- A *session* is an opaque random token in an HttpOnly cookie; only its hash is stored.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, select, text

from shoppi.config import get_settings
from shoppi.core.actor import Actor
from shoppi.core.commands import AppError, Conflict, Forbidden, Invalid, NotFound
from shoppi.db.models import AuthSession, Employee, Invite, Membership, Shop, User
from shoppi.db.session import identity_tx, tenant_tx

DEFAULT_HOURS = {"0": ["08:00", "17:00"], "1": ["08:00", "17:00"], "2": ["08:00", "17:00"],
                 "3": ["08:00", "17:00"], "4": ["08:00", "17:00"], "5": None, "6": None}


class AuthError(AppError):
    status, code = 401, "unauthenticated"


# ---- Passwords and tokens ---------------------------------------------------------


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise Invalid("Password must be at least 8 characters.")
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt_b64, digest_b64 = stored.split("$")
    digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _email(email: str) -> str:
    email = email.strip().lower()
    if "@" not in email or len(email) < 5:
        raise Invalid("Enter a valid email address.")
    return email


# ---- Sessions ---------------------------------------------------------------------


def create_session(user_id: UUID, shop_id: UUID) -> str:
    token = new_token()
    with identity_tx(user_id) as tx:
        tx.add(AuthSession(
            token_hash=token_hash(token), user_id=user_id, shop_id=shop_id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=get_settings().session_days),
        ))
    return token


def end_session(token: str) -> None:
    with identity_tx() as tx:
        tx.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(token)))


def resolve_session(token: str | None) -> Actor:
    """Session cookie -> Actor. Raises AuthError if it is missing, expired or the
    user no longer has an active membership in that shop."""
    if not token:
        raise AuthError("Please sign in.")
    with identity_tx() as tx:
        sess = tx.get(AuthSession, token_hash(token))
        if sess is None or sess.expires_at < datetime.now(timezone.utc):
            raise AuthError("Your session has expired. Please sign in again.")
        user = tx.get(User, sess.user_id)
        user_id, shop_id, name = sess.user_id, sess.shop_id, user.name if user else ""
    with identity_tx(user_id) as tx:
        member = tx.scalar(select(Membership).where(Membership.user_id == user_id, Membership.shop_id == shop_id))
    if member is None or member.status != "active":
        raise AuthError("You no longer have access to this shop.")
    return Actor(shop_id=shop_id, user_id=user_id, role=member.role, name=name, employee_id=member.employee_id)


def memberships_for(user_id: UUID) -> list[dict]:
    with identity_tx(user_id) as tx:
        rows = tx.execute(
            select(Membership.shop_id, Membership.role).where(Membership.user_id == user_id, Membership.status == "active")
        ).all()
    out = []
    for shop_id, role in rows:
        with tenant_tx(Actor(shop_id=shop_id, user_id=user_id, role=role)) as tx:
            shop = tx.get(Shop, shop_id)
            out.append({"shop_id": str(shop_id), "shop_name": shop.name if shop else "", "role": role})
    return out


# ---- Sign up / sign in ------------------------------------------------------------


def signup(*, shop_name: str, name: str, email: str, password: str, timezone_name: str = "America/New_York") -> tuple[str, UUID]:
    """Create a user, a shop and the owner membership. Returns (session token, shop id)."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    email = _email(email)
    if not shop_name.strip() or not name.strip():
        raise Invalid("Shop name and your name are required.")
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise Invalid(f"'{timezone_name}' is not a valid timezone.") from e

    with identity_tx() as tx:
        if tx.scalar(select(User.id).where(User.email == email)):
            raise Conflict("An account with that email already exists. Sign in instead.")
        user = User(email=email, name=name.strip(), password_hash=hash_password(password))
        tx.add(user)
        tx.flush()
        user_id = user.id

    shop_id = create_shop(user_id, name.strip(), shop_name.strip(), timezone_name)
    return create_session(user_id, shop_id), shop_id


def create_shop(user_id: UUID, user_name: str, shop_name: str, timezone_name: str) -> UUID:
    shop_id = uuid4()
    actor = Actor(shop_id=shop_id, user_id=user_id, role="owner", kind="system", source="signup", name=user_name)
    with tenant_tx(actor, command="shops.create", vertical="admin") as tx:
        tx.add(Shop(id=shop_id, name=shop_name, timezone=timezone_name, hours=DEFAULT_HOURS,
                    settings={}, hook_id=secrets.token_urlsafe(24)))
        tx.flush()
        employee = Employee(name=user_name, kind="owner", skill_level=3)
        tx.add(employee)
        tx.flush()
        tx.add(Membership(user_id=user_id, role="owner", employee_id=employee.id))
    return shop_id


def login(email: str, password: str, shop_id: UUID | None = None) -> tuple[str, UUID]:
    with identity_tx() as tx:
        user = tx.scalar(select(User).where(User.email == email.strip().lower()))
    # Same error for unknown email and wrong password, so accounts can't be enumerated.
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("Wrong email or password.")
    return start_session_for(user.id, shop_id)


def start_session_for(user_id: UUID, shop_id: UUID | None = None) -> tuple[str, UUID]:
    shops = memberships_for(user_id)
    if not shops:
        raise Forbidden("Your account isn't a member of any shop. Ask the shop owner for an invite.")
    chosen = next((s for s in shops if shop_id and s["shop_id"] == str(shop_id)), shops[0])
    return create_session(user_id, UUID(chosen["shop_id"])), UUID(chosen["shop_id"])


# ---- Invites ----------------------------------------------------------------------


def lookup_invite(token: str) -> dict:
    with identity_tx() as tx:
        row = tx.execute(text("SELECT shop_id, invite_id FROM resolve_invite(:h)"), {"h": token_hash(token)}).first()
    if row is None:
        raise NotFound("This invite link is invalid or has expired.")
    with tenant_tx(Actor.system(row.shop_id, "invite")) as tx:
        invite, shop = tx.get(Invite, row.invite_id), tx.get(Shop, row.shop_id)
        return {"shop_id": row.shop_id, "invite_id": row.invite_id, "email": invite.email, "role": invite.role,
                "shop_name": shop.name, "employee_id": invite.employee_id}


def accept_invite(token: str, name: str, password: str) -> tuple[str, UUID]:
    info = lookup_invite(token)
    email = _email(info["email"])
    with identity_tx() as tx:
        user = tx.scalar(select(User).where(User.email == email))
        if user is None:
            if not name.strip():
                raise Invalid("Your name is required.")
            user = User(email=email, name=name.strip(), password_hash=hash_password(password))
            tx.add(user)
            tx.flush()
        elif not verify_password(password, user.password_hash):
            raise AuthError("You already have an account with this email. Enter its password to join the shop.")
        user_id, user_name = user.id, user.name

    actor = Actor(shop_id=info["shop_id"], user_id=user_id, role=info["role"], kind="system", source="invite", name=user_name)
    with tenant_tx(actor, command="invites.accept", vertical="admin") as tx:
        invite = tx.get(Invite, info["invite_id"])
        invite.accepted_at = datetime.now(timezone.utc)
        employee_id = invite.employee_id
        if employee_id is None:
            kind = "tech" if invite.role == "mechanic" else "advisor"
            employee = Employee(name=user_name, kind=kind, email=email)
            tx.add(employee)
            tx.flush()
            employee_id = employee.id
        existing = tx.scalar(select(Membership).where(Membership.user_id == user_id, Membership.shop_id == info["shop_id"]))
        if existing:
            existing.role, existing.status, existing.employee_id = invite.role, "active", employee_id
        else:
            tx.add(Membership(user_id=user_id, role=invite.role, employee_id=employee_id))
    return create_session(user_id, info["shop_id"]), info["shop_id"]
