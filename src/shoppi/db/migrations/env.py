"""Alembic environment. Connects as the schema OWNER (SHOPPI_DATABASE_OWNER_URL):
the app role cannot create tables, and must not own them (owners bypass
row-level security)."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

import shoppi.db.models  # noqa: F401  (registers the tables)
from shoppi.config import get_settings
from shoppi.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
# Tables created by other libraries (LangGraph's checkpointer) are not ours to manage.
IGNORED_TABLES = {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "table" and name in IGNORED_TABLES)


def run_migrations() -> None:
    url = config.get_main_option("sqlalchemy.url") or get_settings().database_owner_url
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported; run scripts/migrate.py against a database.")
run_migrations()
