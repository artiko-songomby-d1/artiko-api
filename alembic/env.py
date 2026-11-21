from logging.config import fileConfig

from sqlalchemy import create_engine, text

from alembic import context
from app import models  # noqa: F401  registers every table on Base.metadata
from app.core.config import get_settings
from app.core.db import Base

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)

engine = create_engine(get_settings().async_database_url)  # psycopg 3 drives sync and async
with engine.begin() as conn:
    conn.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
    context.configure(
        connection=conn,
        target_metadata=Base.metadata,
        include_schemas=True,
        version_table_schema="app",
    )
    with context.begin_transaction():
        context.run_migrations()
