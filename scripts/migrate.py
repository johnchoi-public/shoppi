"""Bring the database up to date. Safe to run repeatedly.

    python scripts/migrate.py

1. Alembic migrations (tables, columns, indexes).
2. Row-level security policies, the audit and version triggers, booking
   constraints, grants and the built-in certification list (db/rls.py). This is
   re-applied every time so a newly added table can never be left unprotected.
3. Tables for the assistant's conversation memory.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from shoppi.config import get_settings
from shoppi.db import rls

ROOT = Path(__file__).resolve().parent.parent
CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations")


def main() -> None:
    settings = get_settings()
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

    engine = create_engine(settings.database_owner_url)
    with engine.begin() as conn:
        import shoppi.db.models  # noqa: F401

        rls.install(conn)
    print("Row-level security, triggers and grants are in place.")

    try:
        from langgraph.checkpoint.postgres import PostgresSaver

        conninfo = settings.database_owner_url.replace("postgresql+psycopg://", "postgresql://")
        with PostgresSaver.from_conn_string(conninfo) as saver:
            saver.setup()
        with engine.begin() as conn:
            for table in CHECKPOINT_TABLES:
                conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {rls.APP_ROLE}"))
        print("Assistant conversation tables are in place.")
    except Exception as e:  # the app falls back to in-memory chat history
        print(f"Skipped assistant conversation tables: {e}")
    engine.dispose()


if __name__ == "__main__":
    main()
