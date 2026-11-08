from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from ai4music_core.db import models
from ai4music_core.db.session import create_database_engine
from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = models.Project.metadata


def database_url() -> str:
    if database_path := os.getenv("AI4MUSIC_MIGRATION_DATABASE_PATH"):
        resolved_path = Path(database_path).expanduser().resolve()
        return f"sqlite:///{resolved_path.as_posix()}"
    configured_url = config.get_main_option("sqlalchemy.url")
    if not configured_url:
        raise RuntimeError("sqlalchemy.url is required")
    return configured_url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_database_engine(database_url())

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()

    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
