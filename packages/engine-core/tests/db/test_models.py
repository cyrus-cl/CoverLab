from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

import pytest
from ai4music_core.db.base import Base, utc_now
from ai4music_core.db.models import (
    AnalysisJob,
    AnalysisStatus,
    Artifact,
    JobStage,
    JobStageAttempt,
    Project,
    StageAttemptStatus,
    StageStatus,
)
from ai4music_core.db.session import create_database_engine, create_session_factory
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

ProjectFactory = Callable[..., Project]
JobFactory = Callable[..., AnalysisJob]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as database_session:
        yield database_session
    engine.dispose()


@pytest.fixture
def project_factory(session: Session, tmp_path: Path) -> ProjectFactory:
    counter = 0

    def create_project(**overrides: object) -> Project:
        nonlocal counter
        counter += 1
        source = tmp_path / f"source-{counter}.wav"
        values: dict[str, object] = {
            "name": f"Project {counter}",
            "source_path": str(source.resolve()),
            "source_size": 1024,
            "source_mtime_ns": counter,
            "output_dir": str((tmp_path / f"output-{counter}").resolve()),
        }
        values.update(overrides)
        project = Project(**values)
        session.add(project)
        session.flush()
        return project

    return create_project


@pytest.fixture
def job_factory(session: Session) -> JobFactory:
    def create_job(
        *,
        project: Project,
        status: str | AnalysisStatus = AnalysisStatus.CREATED,
    ) -> AnalysisJob:
        job = AnalysisJob(
            project_id=project.id,
            status=AnalysisStatus(status),
            configuration={},
        )
        session.add(job)
        session.flush()
        return job

    return create_job


@pytest.fixture
def running_stage(
    session: Session,
    project_factory: ProjectFactory,
    job_factory: JobFactory,
) -> JobStage:
    project = project_factory()
    job = job_factory(project=project, status=AnalysisStatus.RUNNING)
    stage = JobStage(
        analysis_id=job.id,
        stage_name="separate",
        position=0,
        status=StageStatus.RUNNING,
    )
    session.add(stage)
    session.flush()
    return stage


def create_attempt(session: Session, stage: JobStage) -> JobStageAttempt:
    attempt = JobStageAttempt(
        stage=stage,
        attempt_number=len(stage.attempts) + 1,
        status=StageAttemptStatus.RUNNING,
        started_at=utc_now(),
    )
    session.add(attempt)
    session.flush()
    return attempt


def finish_attempt(
    session: Session,
    attempt_id,
    *,
    status: StageAttemptStatus,
) -> None:
    attempt = session.get(JobStageAttempt, attempt_id)
    assert attempt is not None
    attempt.status = status
    attempt.finished_at = utc_now()
    session.flush()


def test_local_project_has_no_tenant_or_object_storage_columns() -> None:
    project_table = Project.__table__

    assert "owner_id" not in project_table.c
    assert "bucket" not in project_table.c
    assert "object_key" not in project_table.c
    assert {"source_path", "source_size", "source_mtime_ns", "output_dir"} <= set(
        project_table.c.keys()
    )


def test_artifacts_reference_absolute_local_paths() -> None:
    artifact_table = Artifact.__table__

    assert "path" in artifact_table.c
    assert "sha256" in artifact_table.c
    assert "bucket" not in artifact_table.c
    assert "object_key" not in artifact_table.c


def test_only_one_active_job_can_exist(
    session: Session,
    project_factory: ProjectFactory,
    job_factory: JobFactory,
) -> None:
    first_project = project_factory()
    second_project = project_factory()
    job_factory(project=first_project, status="running")

    with pytest.raises(IntegrityError):
        job_factory(project=second_project, status="queued")

    session.rollback()


def test_finished_job_does_not_block_a_new_job(
    project_factory: ProjectFactory,
    job_factory: JobFactory,
) -> None:
    first_project = project_factory()
    second_project = project_factory()
    job_factory(project=first_project, status="succeeded")

    second = job_factory(project=second_project, status="queued")

    assert second.status is AnalysisStatus.QUEUED


def test_stage_attempt_history_is_append_only(
    session: Session,
    running_stage: JobStage,
) -> None:
    first = create_attempt(session, running_stage)
    first_id = first.id
    finish_attempt(
        session,
        first.id,
        status=StageAttemptStatus.WORKER_LOST,
    )
    second = create_attempt(session, running_stage)
    session.expire(running_stage, ["attempts"])

    assert first_id != second.id
    assert [item.status for item in running_stage.attempts] == [
        StageAttemptStatus.WORKER_LOST,
        StageAttemptStatus.RUNNING,
    ]


def test_sqlite_connections_enable_durability_pragmas(tmp_path: Path) -> None:
    database_path = tmp_path / "durable.db"
    engine = create_database_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")

    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
        assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 5000

    engine.dispose()


def test_timestamps_are_timezone_aware() -> None:
    timestamp: datetime = utc_now()

    assert timestamp.tzinfo is not None
