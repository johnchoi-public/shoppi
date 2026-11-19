"""Shop A must never see or touch shop B's data, whatever path is used."""

import pytest
from sqlalchemy import create_engine, text

from shoppi.core.commands import NotFound, execute, run_query
from shoppi.db import rls
from shoppi.db.base import Base, is_tenant_table
from shoppi.db.session import identity_tx, tenant_tx
from tests.conftest import OWNER_URL


def test_every_table_has_a_policy_or_is_explicitly_global():
    """A new table added without row-level security fails here."""
    engine = create_engine(OWNER_URL)
    with engine.connect() as conn:
        with_rls = {r[0] for r in conn.execute(text("SELECT relname FROM pg_class WHERE relrowsecurity AND relkind = 'r'"))}
        policies = {r[0] for r in conn.execute(text("SELECT tablename FROM pg_policies"))}
    for table in Base.metadata.tables.values():
        if table.name in rls.NO_RLS:
            assert "shop_id" not in table.c or table.name == "auth_sessions", f"{table.name} holds shop data but has no RLS"
            continue
        assert table.name in with_rls and table.name in policies, f"{table.name} has no row-level security policy"
        assert is_tenant_table(table) or table.name in rls.SPECIAL_POLICIES


def test_app_role_cannot_bypass_rls():
    with identity_tx() as tx:
        row = tx.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one()
        assert row == (False, False)
        owners = tx.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner = current_user")).scalar()
        assert owners == 0  # owners are exempt from RLS, so the app must own nothing


def test_queries_only_return_own_shop(shop, other_shop):
    shop.customer_vehicle()
    theirs, _ = other_shop.customer_vehicle()
    names = {c["id"] for c in shop.q("customers.list")}
    assert theirs not in names and len(names) == 1
    with pytest.raises(NotFound):
        run_query(shop.owner, "customers.get", {"id": theirs})


def test_commands_cannot_touch_other_shop(shop, other_shop):
    theirs, their_vehicle = other_shop.customer_vehicle()
    with pytest.raises(NotFound):
        execute(shop.owner, "customers.update", {"id": theirs, "name": "hacked"})
    mine, _ = shop.customer_vehicle()
    # A cross-shop reference is refused even though both ids are real.
    with pytest.raises(NotFound):
        execute(shop.owner, "work_orders.create", {"customer_id": mine, "vehicle_id": their_vehicle})


def test_raw_sql_without_filter_is_still_scoped(shop, other_shop):
    shop.customer_vehicle()
    other_shop.customer_vehicle()
    with tenant_tx(shop.owner) as tx:
        assert tx.execute(text("SELECT count(*) FROM customers")).scalar() == 1
        assert tx.execute(text("SELECT count(DISTINCT shop_id) FROM audit_log")).scalar() == 1
        # Updating without a WHERE clause only reaches this shop's rows.
        tx.execute(text("UPDATE customers SET notes = 'x'"))
    with tenant_tx(other_shop.owner) as tx:
        assert tx.execute(text("SELECT count(*) FROM customers WHERE notes = 'x'")).scalar() == 0


def test_no_shop_context_sees_nothing(shop):
    shop.customer_vehicle()
    with identity_tx() as tx:
        assert tx.execute(text("SELECT count(*) FROM customers")).scalar() == 0
        assert tx.execute(text("SELECT count(*) FROM shops")).scalar() == 0


def test_cannot_insert_into_another_shop(shop, other_shop):
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):
        with tenant_tx(shop.owner, command="test") as tx:
            tx.execute(text("INSERT INTO customers (id, shop_id, name) VALUES (gen_random_uuid(), :s, 'x')"), {"s": str(other_shop.shop_id)})


def test_writes_outside_a_command_are_refused(shop):
    """The audit trigger rejects any change that doesn't say who is making it."""
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="outside a command"):
        with identity_tx() as tx:
            tx.execute(text("SELECT set_config('app.shop_id', :s, true)"), {"s": str(shop.shop_id)})
            tx.execute(text("INSERT INTO customers (id, shop_id, name) VALUES (gen_random_uuid(), :s, 'x')"), {"s": str(shop.shop_id)})


def test_audit_log_is_append_only_for_the_app(shop):
    from sqlalchemy.exc import DBAPIError

    shop.customer_vehicle()
    for sql in ("DELETE FROM audit_log", "UPDATE audit_log SET actor_name = 'nobody'"):
        with pytest.raises(DBAPIError):
            with tenant_tx(shop.owner, command="test") as tx:
                tx.execute(text(sql))
