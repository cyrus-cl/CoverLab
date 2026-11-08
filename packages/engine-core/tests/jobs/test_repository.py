from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from ai4music_core.db.base import Base
from ai4music_core.db.models import AnalysisStatus, Project, StageAttemptStatus, StageStatus
from ai4music_core.db.session import create_database_engine, create_session_factory
from ai4music_core.jobs.repository import InvalidJobTransition, JobRepository
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def session_factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    database_path = tmp_path / "jobs.db"
    engine = create_database_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def project(session_factory: sessionmaker[Session], tmp_path: Path) -> Project:
    source = tmp_path / "source.wav"
    source.write_bytes(b"RIFF-job-source")
    stat = source.stat()
    with session_factory.begin() as session:
        project = Project(
            name="Job Project",
            source_path=str(source.resolve()),
            source_size=stat.st_size,
            source_mtime_ns=stat.st_mtime_ns,
            output_dir=str((tmp_path / "output").resolve()),
        )
        session.add(project)
        session.flush()
        project_id = project.id
    with session_factory() as session:
        persisted = session.get(Project, project_id)
        assert persisted is not None
        return persisted


@pytest.fixture
def repository(session_factory: sessionmaker[Session]) -> JobRepository:
    return JobRepository(session_factory)


def test_job_events_use_a_monotonic_sequence(
    repository: JobRepository,
    project: Project,
) -> None:
    job = repository.create_job(project.id)
    repository.start_attempt(job.stage.id)
    repository.record_progress(job.id, job.stage.id, bytes_done=4, bytes_total=10)
    repository.record_progress(job.id, job.stage.id, bytes_done=10, bytes_total=10)

    updated = repository.get_job(job.id)
    assert updated is not None
    sequences = [event.sequence for event in updated.events]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    assert updated.progress_percent == 100


def test_recovery_requeues_an_interrupted_fingerprint_job(
    repository: JobRepository,
    project: Project,
) -> None:
    running_job = repository.create_job(project.id)
    repository.start_attempt(running_job.stage.id)

    recovered_ids = repository.recover_interrupted_jobs()

    assert recovered_ids == [running_job.id]
    job = repository.get_job(running_job.id)
    assert job is not None
    assert job.status == "queued"
    assert job.stage.status == "queued"
    assert job.stage.attempts[0].status == "worker_lost"
    assert job.events[-1].event_type == "WORKER_LOST"


def test_successful_fingerprint_is_persisted_on_the_project(
    repository: JobRepository,
    project: Project,
    session_factory: sessionmaker[Session],
) -> None:
    job = repository.create_job(project.id)
    repository.start_attempt(job.stage.id)
    digest = hashlib.sha256(b"RIFF-job-source").hexdigest()

    repository.complete_fingerprint(job.id, job.stage.id, digest=digest)

    completed = repository.get_job(job.id)
    assert completed is not None
    assert completed.status is AnalysisStatus.SUCCEEDED
    assert completed.stage.status is StageStatus.SUCCEEDED
    assert completed.stage.attempts[-1].status is StageAttemptStatus.SUCCEEDED
    with session_factory() as session:
        persisted_project = session.get(Project, project.id)
        assert persisted_project is not None
        assert persisted_project.source_sha256 == digest


def test_canceled_job_can_be_retried(
    repository: JobRepository,
    project: Project,
) -> None:
    job = repository.create_job(project.id)

    canceled = repository.request_cancel(job.id)
    retried = repository.retry_job(job.id)

    assert canceled.status is AnalysisStatus.CANCELED
    assert retried.status is AnalysisStatus.QUEUED
    assert retried.stage.status is StageStatus.QUEUED


def test_illegal_job_transition_is_rejected(
    repository: JobRepository,
    project: Project,
) -> None:
    job = repository.create_job(project.id)

    with pytest.raises(InvalidJobTransition):
        repository.complete_fingerprint(job.id, job.stage.id, digest="0" * 64)
