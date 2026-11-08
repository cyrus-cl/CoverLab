from __future__ import annotations

from pathlib import Path

from ai4music_core.db.session import create_database_engine
from ai4music_engine.migrations import migration_directory, run_migrations
from sqlalchemy import inspect

EXPECTED_TABLES = {
    "alembic_version",
    "analysis_jobs",
    "artifacts",
    "job_events",
    "job_stage_attempts",
    "job_stages",
    "model_releases",
    "projects",
}


def test_initial_migration_creates_the_local_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"

    run_migrations(database_path)
    run_migrations(database_path)

    engine = create_database_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == EXPECTED_TABLES
    assert "owner_id" not in {column["name"] for column in inspector.get_columns("projects")}
    assert "path" in {column["name"] for column in inspector.get_columns("artifacts")}
    engine.dispose()


def test_initial_migration_contains_the_partial_active_job_index(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    run_migrations(database_path)
    engine = create_database_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")

    with engine.connect() as connection:
        index_sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND name = 'uq_analysis_jobs_single_active'"
        ).scalar_one()

    assert "UNIQUE INDEX" in index_sql
    assert "WHERE status IN ('created', 'queued', 'running', 'canceling')" in index_sql
    engine.dispose()


def test_migration_source_is_sqlite_only() -> None:
    revision = migration_directory() / "versions" / "0001_local_desktop.py"
    source = revision.read_text(encoding="utf-8").lower()

    assert "postgresql" not in source
    assert "jsonb" not in source
