from logging.config import fileConfig

from sqlalchemy import create_engine, text

from alembic import context
from app.platform import registry  # noqa: F401  registers every table on Base.metadata
from app.platform.config import get_settings
from app.platform.db import Base

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)

# Every instance runs "alembic upgrade head" when it starts. This lock makes instances that start together take
# turns: the second waits, then finds the schema current and does nothing. It is a transaction-level lock on
# purpose: Neon is reached through a pooler that can hand the session to another client between transactions, so
# a session lock could be lost or never released. This one is released by the commit or rollback of the single
# transaction below, which holds every migration. The number is arbitrary; it only has to be ours in this database.
MIGRATION_LOCK = 7_203_184_519

engine = create_engine(get_settings().async_database_url)  # psycopg 3 drives sync and async
with engine.begin() as conn:
    conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK})
    conn.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
    context.configure(
        connection=conn,
        target_metadata=Base.metadata,
        include_schemas=True,
        version_table_schema="app",
    )
    with context.begin_transaction():
        context.run_migrations()
