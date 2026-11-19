"""Create a ready-to-use owner login in the demo shop, for local testing.

    python scripts/seed_demo.py         # once, if the demo shop doesn't exist yet
    python scripts/create_dev_login.py

Sign in at http://localhost:8000 with the email and password below. Safe to run
again: it reports the existing login instead of creating a second one.

Local development only. Override with SHOPPI_DEV_EMAIL / SHOPPI_DEV_PASSWORD.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import select

import shoppi.domain  # noqa: F401
from shoppi.auth import service as auth
from shoppi.core.actor import Actor
from shoppi.core.commands import AppError, execute
from shoppi.db.models import User
from shoppi.db.session import identity_tx

DEMO_OWNER_EMAIL = "owner@northside.test"
DEV_EMAIL = os.environ.get("SHOPPI_DEV_EMAIL", "dev@northside.test")
DEV_PASSWORD = os.environ.get("SHOPPI_DEV_PASSWORD", "shoppi-dev-login")
DEV_NAME = "Dev Tester"


def main() -> None:
    with identity_tx() as tx:
        owner = tx.scalar(select(User).where(User.email == DEMO_OWNER_EMAIL))
        existing = tx.scalar(select(User.id).where(User.email == DEV_EMAIL.lower()))
    if owner is None:
        sys.exit("The demo shop doesn't exist yet. Run: python scripts/seed_demo.py")
    if existing:
        print(f"Dev login already exists: {DEV_EMAIL}")
        return

    shop = auth.memberships_for(owner.id)[0]
    actor = Actor(shop_id=shop["shop_id"], user_id=owner.id, role="owner", name=owner.name)
    try:
        # Goes through the normal invite flow, so it is recorded in the shop's history.
        link = execute(actor, "members.invite", {"email": DEV_EMAIL, "role": "owner"})["result"]["link"]
        auth.accept_invite(link.rsplit("/", 1)[1], DEV_NAME, DEV_PASSWORD)
    except AppError as e:
        sys.exit(f"Dev login not created: {e.message}")

    print(f"Dev login created in {shop['shop_name']} (role: owner)")
    print(f"  Email:    {DEV_EMAIL}")
    print("  Password: SHOPPI_DEV_PASSWORD, or the default at the top of scripts/create_dev_login.py")


if __name__ == "__main__":
    main()
