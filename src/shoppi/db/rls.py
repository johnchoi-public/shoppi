"""Database-level guarantees: row-level security, the audit trigger, and booking constraints.

`install(conn)` is idempotent and is run (as the schema owner) after every
migration, so a newly added tenant table gets its policy and triggers
automatically. tests/test_isolation.py fails if any table is left uncovered.

Why the database and not the ORM: a missed `WHERE shop_id = ...`, a raw SQL
statement or a bulk update cannot bypass a policy or a trigger.

The app connects as a non-owner role (APP_ROLE). The owner is exempt from
row-level security, which is what lets the SECURITY DEFINER functions below
(audit writer, token resolvers) work across shops in a controlled way.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

from shoppi.db.base import Base, is_tenant_table

APP_ROLE = "shoppi_app"

# Tables whose changes are not written to the history (bulk/derived/log data).
NOT_AUDITED = {"doc_chunks", "integration_calls", "sync_map", "catalog_items", "audit_log", "users", "auth_sessions", "vin_cache", "chat_threads"}
# Columns never copied into the history.
AUDIT_EXCLUDED_COLUMNS = {
    "integration_credentials": ["secret"],
    "invites": ["token_hash"],
    "intake_links": ["token_hash"],
}

# (code, name, category, legally_required, has_expiry)
BUILTIN_CERTS = [
    ("ASE-A1", "ASE A1 Engine Repair", "ase_auto", False, True),
    ("ASE-A2", "ASE A2 Automatic Transmission/Transaxle", "ase_auto", False, True),
    ("ASE-A3", "ASE A3 Manual Drive Train & Axles", "ase_auto", False, True),
    ("ASE-A4", "ASE A4 Suspension & Steering", "ase_auto", False, True),
    ("ASE-A5", "ASE A5 Brakes", "ase_auto", False, True),
    ("ASE-A6", "ASE A6 Electrical/Electronic Systems", "ase_auto", False, True),
    ("ASE-A7", "ASE A7 Heating & Air Conditioning", "ase_auto", False, True),
    ("ASE-A8", "ASE A8 Engine Performance", "ase_auto", False, True),
    ("ASE-A9", "ASE A9 Light Vehicle Diesel Engines", "ase_auto", False, True),
    ("ASE-G1", "ASE G1 Auto Maintenance & Light Repair", "ase_auto", False, True),
    ("ASE-L1", "ASE L1 Advanced Engine Performance", "ase_advanced", False, True),
    ("ASE-L3", "ASE L3 Light Duty Hybrid/Electric Vehicle", "ase_advanced", False, True),
    ("ASE-T1", "ASE T1 Gasoline Engines (Truck)", "ase_truck", False, True),
    ("ASE-T2", "ASE T2 Diesel Engines", "ase_truck", False, True),
    ("ASE-T3", "ASE T3 Drive Train (Truck)", "ase_truck", False, True),
    ("ASE-T4", "ASE T4 Brakes (Truck)", "ase_truck", False, True),
    ("ASE-T5", "ASE T5 Suspension & Steering (Truck)", "ase_truck", False, True),
    ("ASE-T6", "ASE T6 Electrical/Electronic Systems (Truck)", "ase_truck", False, True),
    ("ASE-T7", "ASE T7 Heating, Ventilation & A/C (Truck)", "ase_truck", False, True),
    ("ASE-T8", "ASE T8 Preventive Maintenance Inspection", "ase_truck", False, True),
    ("EV-HV", "High-voltage / EV safety qualification", "safety", True, True),
    ("EPA-609", "EPA Section 609 refrigerant handling", "legal", True, False),
    ("STATE-INSP", "State safety/emissions inspector licence", "legal", True, True),
    ("DOT-INSP", "DOT annual inspection qualified inspector", "legal", True, False),
    ("CDL-A", "Commercial driver's licence, Class A", "legal", True, True),
    ("CDL-B", "Commercial driver's licence, Class B", "legal", True, True),
]

FUNCTIONS = r"""
CREATE OR REPLACE FUNCTION app_shop() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.shop_id', true), '')::uuid $$;

CREATE OR REPLACE FUNCTION app_user() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$;

-- Bumps version/updated_at on every real update.
CREATE OR REPLACE FUNCTION touch_row() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
    NEW.version := OLD.version + 1;
    NEW.updated_at := now();
  END IF;
  RETURN NEW;
END $$;

-- Writes one history row per changed row. Refuses writes made outside a command.
CREATE OR REPLACE FUNCTION audit_row() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_actor text := NULLIF(current_setting('app.actor', true), '');
  v_excl text[] := string_to_array(COALESCE(TG_ARGV[0], ''), ',');
  v_old jsonb; v_new jsonb; v_changed text[]; v_row jsonb;
BEGIN
  IF v_actor IS NULL THEN
    RAISE EXCEPTION 'write to % outside a command (app.actor is not set)', TG_TABLE_NAME;
  END IF;
  IF TG_OP <> 'INSERT' THEN v_old := to_jsonb(OLD) - v_excl; END IF;
  IF TG_OP <> 'DELETE' THEN v_new := to_jsonb(NEW) - v_excl; END IF;
  IF TG_OP = 'UPDATE' THEN
    SELECT array_agg(n.key ORDER BY n.key) INTO v_changed
      FROM jsonb_each(v_new) n
     WHERE n.key NOT IN ('version', 'updated_at') AND n.value IS DISTINCT FROM v_old -> n.key;
    IF v_changed IS NULL THEN RETURN NULL; END IF;
  END IF;
  v_row := COALESCE(v_new, v_old);
  INSERT INTO audit_log (shop_id, actor_type, actor_user_id, actor_name, actor_ref, source, vertical, command,
                         request_id, change_request_id, approved_by, entity_type, entity_id, op,
                         before, after, changed_fields)
  VALUES (
    CASE WHEN TG_TABLE_NAME = 'shops' THEN (v_row ->> 'id')::uuid ELSE (v_row ->> 'shop_id')::uuid END,
    v_actor,
    NULLIF(current_setting('app.user_id', true), '')::uuid,
    NULLIF(current_setting('app.actor_name', true), ''),
    NULLIF(current_setting('app.actor_ref', true), ''),
    NULLIF(current_setting('app.source', true), ''),
    NULLIF(current_setting('app.vertical', true), ''),
    NULLIF(current_setting('app.command', true), ''),
    NULLIF(current_setting('app.request_id', true), ''),
    NULLIF(current_setting('app.change_request_id', true), '')::uuid,
    NULLIF(current_setting('app.approved_by', true), '')::uuid,
    TG_TABLE_NAME, (v_row ->> 'id')::uuid, lower(TG_OP), v_old, v_new, v_changed);
  RETURN NULL;
END $$;

-- The only cross-shop lookups available to the app role. Each returns the shop a
-- secret belongs to and nothing else.
CREATE OR REPLACE FUNCTION resolve_intake_token(p_hash text)
RETURNS TABLE (shop_id uuid, link_id uuid) LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT l.shop_id, l.id FROM intake_links l
   WHERE l.token_hash = p_hash AND l.revoked_at IS NULL
     AND (l.expires_at IS NULL OR l.expires_at > now())
     AND (l.max_uses IS NULL OR l.uses < l.max_uses)
$$;

CREATE OR REPLACE FUNCTION resolve_invite(p_hash text)
RETURNS TABLE (shop_id uuid, invite_id uuid) LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT i.shop_id, i.id FROM invites i
   WHERE i.token_hash = p_hash AND i.accepted_at IS NULL AND i.expires_at > now()
$$;

CREATE OR REPLACE FUNCTION resolve_hook(p_hook text)
RETURNS uuid LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT s.id FROM shops s WHERE s.hook_id = p_hook
$$;
"""

CONSTRAINTS = {
    "job_assignments": [
        ("ck_job_time", "CHECK (ends_at > starts_at)"),
        (
            "ex_job_employee",
            "EXCLUDE USING gist (shop_id WITH =, employee_id WITH =, tstzrange(starts_at, ends_at) WITH &&) "
            "WHERE (employee_id IS NOT NULL AND status IN ('planned', 'in_progress', 'paused'))",
        ),
        (
            "ex_job_bay",
            "EXCLUDE USING gist (shop_id WITH =, bay_id WITH =, tstzrange(starts_at, ends_at) WITH &&) "
            "WHERE (bay_id IS NOT NULL AND status IN ('planned', 'in_progress', 'paused'))",
        ),
    ],
    "shifts": [("ck_shift_time", "CHECK (ends_at > starts_at)")],
    "time_off": [("ck_time_off_time", "CHECK (ends_at > starts_at)")],
    "bay_closures": [("ck_bay_closure_time", "CHECK (ends_at > starts_at)")],
    "calendar_events": [("ck_event_time", "CHECK (ends_at > starts_at)")],
    "parts": [("ck_part_on_hand", "CHECK (on_hand >= 0)")],
}

# Policies for the tables that are not plain tenant tables.
SPECIAL_POLICIES = {
    "shops": ("id = app_shop()", "id = app_shop()"),
    "memberships": ("shop_id = app_shop() OR user_id = app_user()", "shop_id = app_shop()"),
    "cert_types": ("shop_id IS NULL OR shop_id = app_shop()", "shop_id = app_shop()"),
    "audit_log": ("shop_id = app_shop()", "false"),
}
# Global tables with no row-level security at all (identities, sessions, public VIN specs).
NO_RLS = {"users", "auth_sessions", "vin_cache"}


def install(conn: Connection, app_role: str = APP_ROLE) -> None:
    run = lambda sql, **p: conn.execute(text(sql), p)  # noqa: E731
    run(FUNCTIONS)
    # Lets this function insert seed rows through the audit trigger.
    run("SELECT set_config('app.actor', 'system', true), set_config('app.source', 'migration', true)")

    for table in Base.metadata.sorted_tables:
        name = table.name
        if name in NO_RLS:
            run(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {name} TO {app_role}")
            continue

        using, check = SPECIAL_POLICIES.get(name, ("shop_id = app_shop()", "shop_id = app_shop()"))
        assert name in SPECIAL_POLICIES or is_tenant_table(table), f"{name}: no row-level security rule"
        run(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
        run(f"DROP POLICY IF EXISTS tenant_isolation ON {name}")
        run(f"CREATE POLICY tenant_isolation ON {name} USING ({using}) WITH CHECK ({check})")
        if name == "audit_log":
            run(f"GRANT SELECT ON {name} TO {app_role}")  # append-only: written by the trigger
        else:
            run(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {name} TO {app_role}")

        if "version" in table.c:
            run(f"DROP TRIGGER IF EXISTS trg_touch ON {name}")
            run(f"CREATE TRIGGER trg_touch BEFORE UPDATE ON {name} FOR EACH ROW EXECUTE FUNCTION touch_row()")
        if name not in NOT_AUDITED:
            excluded = ",".join(AUDIT_EXCLUDED_COLUMNS.get(name, []))
            run(f"DROP TRIGGER IF EXISTS trg_audit ON {name}")
            run(
                f"CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON {name} "
                f"FOR EACH ROW EXECUTE FUNCTION audit_row('{excluded}')"
            )

    for table_name, constraints in CONSTRAINTS.items():
        for cname, definition in constraints:
            exists = run("SELECT 1 FROM pg_constraint WHERE conname = :n", n=cname).first()
            if not exists:
                run(f"ALTER TABLE {table_name} ADD CONSTRAINT {cname} {definition}")

    run(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_role}")
    run(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {app_role}")

    for code, name, category, legal, expiry in BUILTIN_CERTS:
        run(
            "INSERT INTO cert_types (id, shop_id, code, name, category, legally_required, has_expiry) "
            "SELECT gen_random_uuid(), NULL, :code, :name, :cat, :legal, :expiry "
            "WHERE NOT EXISTS (SELECT 1 FROM cert_types WHERE shop_id IS NULL AND code = :code)",
            code=code, name=name, cat=category, legal=legal, expiry=expiry,
        )


def create_schema(conn: Connection) -> None:
    """Create every table and install the guarantees. Used by tests and first-time setup."""
    import shoppi.db.models  # noqa: F401  (registers the tables)

    Base.metadata.create_all(conn)
    install(conn)
