#!/usr/bin/env bash
# One-time local database setup. Run as a Postgres superuser (on a Homebrew
# install that is simply your own user):
#
#   ./scripts/setup_db.sh
#
# Creates two roles and two databases:
#   shoppi_owner  owns the schema and runs migrations
#   shoppi_app    what the web app connects as; row-level security applies to it
# and installs the extensions the app needs (pgvector, btree_gist, pgcrypto).
# The passwords below are for local development only: change them anywhere else.
set -euo pipefail

PSQL="${PSQL:-psql}"
command -v "$PSQL" >/dev/null 2>&1 || PSQL=/opt/homebrew/opt/postgresql@17/bin/psql
OWNER_PASSWORD="${SHOPPI_OWNER_PASSWORD:-shoppi_owner_dev}"
APP_PASSWORD="${SHOPPI_APP_PASSWORD:-shoppi_app_dev}"

# Linux: distro packages (apt, dnf, ...) make the OS user 'postgres' the database
# superuser, reachable over the local socket by peer authentication. If your own
# user can't connect as a superuser and PGHOST isn't set (Docker, remote server),
# run psql as 'postgres' through sudo instead. macOS never enters this block.
if [ "$(uname -s)" = "Linux" ]; then
  if ! command -v "$PSQL" >/dev/null 2>&1; then
    echo "psql not found. Install PostgreSQL 17 and pgvector; see the Setup section of README.md." >&2
    exit 1
  fi
  if [ -z "${PGHOST:-}" ] \
    && [ "$("$PSQL" -d postgres -Atc "SELECT rolsuper FROM pg_roles WHERE rolname = current_user" 2>/dev/null)" != "t" ] \
    && id postgres >/dev/null 2>&1; then
    echo "Your user is not a Postgres superuser here; running psql as 'postgres' via sudo." >&2
    PSQL_BIN="$(command -v "$PSQL")"
    # The rest of the script calls "$PSQL", which resolves to this function.
    # cd / avoids psql warning that 'postgres' can't read the current directory.
    psql_as_postgres() { (cd / && sudo -u postgres "$PSQL_BIN" ${PGPORT:+-p "$PGPORT"} "$@"); }
    PSQL=psql_as_postgres
  fi
fi

"$PSQL" -d postgres -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shoppi_owner') THEN CREATE ROLE shoppi_owner LOGIN PASSWORD '${OWNER_PASSWORD}'; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shoppi_app') THEN CREATE ROLE shoppi_app LOGIN PASSWORD '${APP_PASSWORD}'; END IF;
END \$\$;
SQL

for db in shoppi shoppi_test; do
  if ! "$PSQL" -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '$db'" | grep -q 1; then
    "$PSQL" -d postgres -q -c "CREATE DATABASE $db OWNER shoppi_owner"
  fi
  "$PSQL" -d "$db" -v ON_ERROR_STOP=1 -q <<SQL
ALTER SCHEMA public OWNER TO shoppi_owner;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
GRANT USAGE ON SCHEMA public TO shoppi_app;
SQL
done
echo "Databases 'shoppi' and 'shoppi_test' are ready. Next: python scripts/migrate.py"
