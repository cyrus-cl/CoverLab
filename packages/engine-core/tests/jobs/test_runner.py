from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from ai4music_core.db.base import Base
from ai4music_core.db.models import AnalysisStatus, Project
from ai4music_core.db.session import create_database_engine, create_session_factory
from ai4music_core.jobs.repository import JobRepository
from ai4music_core.jobs.runner import SingleJobRunner
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def runner_context(
    tmp_path: Path,
) -> Iterator[tuple[JobRepository, SingleJobRunner, sessionmaker[Session], Project]]:
    database_path = tmp_path / "runner.db"
    engine = create_database_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    source = tmp_path / "runner-source.bin"
    source.write_bytes(b"a" * 10)
    stat = source.stat()
    with factory.begin() as session:
        project = Project(
            name="Runner Project",
            source_path=str(source.resolve()),
            source_size=stat.st_size,
            source_mtime_ns=stat.st_mtime_ns,
            output_dir=str((tmp_path / "output").resolve()),
        )
        session.add(project)
        session.flush()
        project_id = project.id
    with factory() as session:
        persisted = session.get(Project, project_id)
        assert persisted is not None
        repository = JobRepository(factory)
        yield repository, SingleJobRunner(repository, hash_chunk_size=4), factory, persisted
    engine.dispose()


@pytest.mark.asyncio
async def test_runner_hashes_the_real_source_and_persists_progress(
    runner_context,
) -> None:
    repository, runner, factory, project = runner_context
    job = repository.create_job(project.id)

    await runner.run_once(job.id)

    completed = repository.get_job(job.id)
    assert completed is not None
    assert completed.status is AnalysisStatus.SUCCEEDED
    assert [
        event.progress_percent for event in completed.events if event.event_type == "PROGRESS"
    ] == [
        40,
        80,
        100,
    ]
    with factory() as session:
        persisted_project = session.get(Project, project.id)
        assert persisted_project is not None
        assert persisted_project.source_sha256 == hashlib.sha256(b"a" * 10).hexdigest()


@pytest.mark.asyncio
async def test_runner_fails_if_the_source_changes_while_hashing(
    runner_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, runner, _factory, project = runner_context
    job = repository.create_job(project.id)

    def mutate_source(path: Path, **_kwargs: object) -> str:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.write_bytes(path.read_bytes() + b"changed")
        return digest

    monkeypatch.setattr("ai4music_core.jobs.runner.hash_file", mutate_source)

    await runner.run_once(job.id)

    failed = repository.get_job(job.id)
    assert failed is not None
    assert failed.status is AnalysisStatus.FAILED
    assert failed.error_code == "source_changed"
