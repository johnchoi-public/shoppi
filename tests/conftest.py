"""Test setup: a real Postgres database (row-level security, exclusion constraints
and the audit trigger can't be exercised on anything else).

The schema is rebuilt once per run as the owner role; tests then connect as the
restricted app role, exactly like the web app. Each test creates its own shops,
so tests don't need cleaning up between them.

Override the URLs with SHOPPI_TEST_DATABASE_URL / SHOPPI_TEST_DATABASE_OWNER_URL.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine

APP_URL = os.environ.get("SHOPPI_TEST_DATABASE_URL", "postgresql+psycopg://shoppi_app:shoppi_app_dev@localhost:5432/shoppi_test")
OWNER_URL = os.environ.get("SHOPPI_TEST_DATABASE_OWNER_URL", "postgresql+psycopg://shoppi_owner:shoppi_owner_dev@localhost:5432/shoppi_test")

os.environ["SHOPPI_DATABASE_URL"] = APP_URL
os.environ["SHOPPI_DATABASE_OWNER_URL"] = OWNER_URL
os.environ.setdefault("SHOPPI_TOKEN_KEY", "x5Yq1mJ8J3k1o2Jd3vVQpQm0n5oYh3sX0o1xw1p3V5s=")
os.environ["SHOPPI_UPLOAD_DIR"] = "/tmp/shoppi-test-uploads"

TZ = ZoneInfo("America/New_York")


@pytest.fixture(scope="session", autouse=True)
def database():
    import shoppi.domain  # noqa: F401  (registers commands)
    from shoppi.db import rls
    from shoppi.db.base import Base
    from shoppi.db.session import configure_engine

    owner = create_engine(OWNER_URL)
    try:
        with owner.begin() as conn:
            import shoppi.db.models  # noqa: F401

            Base.metadata.drop_all(conn)
            rls.create_schema(conn)
    except Exception as e:  # pragma: no cover
        pytest.exit(f"Test database not available ({e}). Run scripts/setup_db.sh first.", returncode=2)
    owner.dispose()
    configure_engine(APP_URL)
    yield


class ShopFixture:
    """A fresh shop with its owner, plus helpers to add people and data."""

    def __init__(self, name: str = "Test Shop", starter: bool = True):
        from shoppi.auth import service as auth
        from shoppi.starter import install_starter

        self.auth = auth
        tag = uuid4().hex[:8]
        self.owner_email = f"owner-{tag}@example.com"
        self.password = "correct-horse-battery"
        self.token, self.shop_id = auth.signup(shop_name=name, name="Olive Owner", email=self.owner_email,
                                               password=self.password, timezone_name=TZ.key)
        self.owner = auth.resolve_session(self.token)
        self.ids = install_starter(self.owner) if starter else {"bays": {}, "services": {}, "certs": {}}

    # -- people --
    def member(self, role: str = "mechanic", name: str | None = None, employee_id=None):
        """Invite and accept a new member. Returns their Actor."""
        email = f"{role}-{uuid4().hex[:8]}@example.com"
        link = self.run("members.invite", {"email": email, "role": role, "employee_id": employee_id})["link"]
        token, _ = self.auth.accept_invite(link.rsplit("/", 1)[1], name or f"{role.title()} {uuid4().hex[:4]}", self.password)
        actor = self.auth.resolve_session(token)
        self.tokens = getattr(self, "tokens", {})
        self.tokens[actor.user_id] = token
        return actor

    def employee(self, name: str, certs: list[str] | tuple = (), kind: str = "tech", expires: str | None = "2030-01-01",
                 can_supervise: bool = False, skill_level: int = 3) -> str:
        emp = self.run("employees.create", {"name": name, "kind": kind, "can_supervise": can_supervise, "skill_level": skill_level})
        for code in certs:
            self.run("certs.add", {"employee_id": emp["id"], "cert_type_id": self.ids["certs"][code], "expires_on": expires})
        return emp["id"]

    def shifts(self, employee_id: str, first_day: datetime, days: int = 5, start: str = "08:00", end: str = "17:00"):
        for i in range(days):
            d = (first_day + timedelta(days=i)).date()
            self.run("shifts.create", {"employee_id": employee_id, "start": f"{d}T{start}:00", "end": f"{d}T{end}:00"})

    # -- data --
    def customer_vehicle(self, **vehicle) -> tuple[str, str]:
        c = self.run("customers.create", {"name": f"Customer {uuid4().hex[:5]}", "phone": "(555) 010-" + uuid4().hex[:4]})
        v = self.run("vehicles.create", {"customer_id": c["id"], "year": 2021, "make": "Ford", "model": "F-150", **vehicle})
        return c["id"], v["id"]

    def work_order(self, services: list[str], **vehicle) -> dict:
        c, v = self.customer_vehicle(**vehicle)
        return self.run("work_orders.create", {"customer_id": c, "vehicle_id": v, "concern": "test",
                                               "service_type_ids": [self.ids["services"][s] for s in services]})

    # -- commands --
    def run(self, name: str, args: dict | None = None, actor=None) -> dict:
        from shoppi.core.commands import execute

        result = execute(actor or self.owner, name, args or {})
        assert result["status"] == "applied", result
        return result["result"]

    def q(self, name: str, args: dict | None = None, actor=None):
        from shoppi.core.commands import run_query

        return run_query(actor or self.owner, name, args or {})


@pytest.fixture
def shop() -> ShopFixture:
    return ShopFixture()


@pytest.fixture
def other_shop() -> ShopFixture:
    return ShopFixture(name="Other Shop")


def next_weekday(weekday: int = 0, weeks_ahead: int = 1) -> datetime:
    """A future local date on the given weekday (0 = Monday), at midnight shop time."""
    now = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    days = (weekday - now.weekday()) % 7 + 7 * weeks_ahead
    return now + timedelta(days=days)
