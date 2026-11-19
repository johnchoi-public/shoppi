"""Roles, per-area grants, change requests and the change history."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from shoppi.core.commands import Forbidden, decide_change_request, execute, run_query
from shoppi.db.session import tenant_tx


def test_owner_and_advisor_have_identical_rights(shop):
    advisor = shop.member("advisor")
    owner_perms = run_query(shop.owner, "me.permissions")["permissions"]
    advisor_perms = run_query(advisor, "me.permissions")["permissions"]
    assert owner_perms == advisor_perms
    assert set(owner_perms.values()) == {"edit"}
    assert execute(advisor, "shop.update", {"phone": "555-0100"})["status"] == "applied"


def test_mechanic_can_view_but_edits_become_change_requests(shop):
    mechanic = shop.member("mechanic")
    cid, _ = shop.customer_vehicle()
    assert run_query(mechanic, "customers.get", {"id": cid})["id"] == cid

    result = execute(mechanic, "customers.update", {"id": cid, "notes": "Prefers text messages"})
    assert result["status"] == "pending"
    cr = result["change_request"]
    assert cr["needs"] == "manager"
    # The dry run captured exactly what would change...
    assert cr["preview"][0]["changed_fields"] == ["notes"]
    assert cr["preview"][0]["after"]["notes"] == "Prefers text messages"
    # ...and changed nothing.
    assert shop.q("customers.get", {"id": cid})["notes"] is None

    # The requester can't approve their own request.
    with pytest.raises(Forbidden):
        decide_change_request(mechanic, cr["id"], True)

    assert decide_change_request(shop.owner, cr["id"], True)["status"] == "applied"
    assert shop.q("customers.get", {"id": cid})["notes"] == "Prefers text messages"

    # History shows who asked and who approved.
    row = shop.q("history.list", {"entity_type": "customers", "entity_id": cid})[0]
    assert row["actor_name"] == mechanic.name and row["approved_by_name"] == "Olive Owner"
    assert row["before"]["notes"] is None and row["after"]["notes"] == "Prefers text messages"


def test_stale_change_request_is_not_applied(shop):
    mechanic = shop.member("mechanic")
    cid, _ = shop.customer_vehicle()
    cr = execute(mechanic, "customers.update", {"id": cid, "notes": "old idea"})["change_request"]
    shop.run("customers.update", {"id": cid, "notes": "manager edited meanwhile"})
    assert decide_change_request(shop.owner, cr["id"], True)["status"] == "stale"
    assert shop.q("customers.get", {"id": cid})["notes"] == "manager edited meanwhile"


def test_rejected_and_cancelled_requests(shop):
    mechanic = shop.member("mechanic")
    cid, _ = shop.customer_vehicle()
    a = execute(mechanic, "customers.update", {"id": cid, "notes": "a"})["change_request"]
    b = execute(mechanic, "customers.update", {"id": cid, "notes": "b"})["change_request"]
    assert decide_change_request(shop.owner, a["id"], False, "no")["status"] == "rejected"
    assert decide_change_request(mechanic, b["id"], False)["status"] == "cancelled"
    assert shop.q("customers.get", {"id": cid})["notes"] is None


def test_elevation_per_area_with_expiry(shop):
    mechanic = shop.member("mechanic")
    cid, _ = shop.customer_vehicle()
    req = execute(mechanic, "access.request", {"vertical": "customers", "reason": "front desk cover", "hours": 4})
    assert req["status"] == "applied"
    shop.run("access.decide", {"id": req["result"]["id"], "approve": True})

    perms = run_query(mechanic, "me.permissions")["permissions"]
    assert perms["customers"] == "edit" and perms["inventory"] == "view"  # only the granted area
    assert execute(mechanic, "customers.update", {"id": cid, "notes": "direct"})["status"] == "applied"
    # Other areas still go through a request.
    part = shop.run("parts.create", {"sku": "X1", "name": "Widget", "on_hand": 5})
    assert execute(mechanic, "parts.adjust", {"part_id": part["id"], "delta": -1})["status"] == "pending"

    # Expire the grant: access ends immediately, with no re-login.
    with tenant_tx(shop.owner, command="test.expire") as tx:
        tx.execute(text("UPDATE permission_grants SET expires_at = :t"), {"t": datetime.now(timezone.utc) - timedelta(minutes=1)})
    assert execute(mechanic, "customers.update", {"id": cid, "notes": "after expiry"})["status"] == "pending"


def test_admin_area_can_never_be_requested_or_proposed(shop):
    mechanic = shop.member("mechanic")
    from shoppi.core.commands import Invalid

    with pytest.raises(Invalid):
        execute(mechanic, "access.request", {"vertical": "admin"})
    with pytest.raises(Forbidden):
        execute(mechanic, "shop.update", {"name": "Mine now"})
    with pytest.raises(Forbidden):
        execute(mechanic, "access.grant", {"user_id": str(mechanic.user_id), "vertical": "customers"})


def test_assistant_writes_are_always_proposals(shop):
    cid, _ = shop.customer_vehicle()
    agent = shop.owner.as_agent()
    result = execute(agent, "customers.update", {"id": cid, "notes": "from the assistant"})
    assert result["status"] == "pending" and result["change_request"]["needs"] == "self"
    assert shop.q("customers.get", {"id": cid})["notes"] is None
    # The user confirms their own assistant's proposal.
    decide_change_request(shop.owner, result["change_request"]["id"], True)
    row = shop.q("history.list", {"entity_type": "customers", "entity_id": cid})[0]
    assert row["actor_type"] == "agent" and row["source"] == "agent"


def test_every_change_is_recorded_with_before_and_after(shop):
    cid, vid = shop.customer_vehicle()
    shop.run("vehicles.update", {"id": vid, "mileage": 42000})
    rows = shop.q("history.list", {"entity_type": "vehicles", "entity_id": vid})
    assert [r["op"] for r in rows] == ["update", "insert"]
    assert rows[0]["changed_fields"] == ["mileage"] and rows[0]["after"]["mileage"] == 42000
    assert rows[0]["actor_name"] == "Olive Owner" and rows[0]["command"] == "vehicles.update"
    # Mechanics can read the history too.
    mechanic = shop.member("mechanic")
    assert run_query(mechanic, "history.list", {"entity_type": "vehicles", "entity_id": vid})


def test_secrets_never_reach_the_history(shop):
    link = shop.run("intake_links.create", {"label": "kiosk"})
    rows = shop.q("history.list", {"entity_type": "intake_links", "entity_id": link["id"]})
    assert rows and "token_hash" not in rows[0]["after"]
