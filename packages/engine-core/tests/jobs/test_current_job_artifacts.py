from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from ai4music_core.db.base import Base
from ai4music_core.db.models import Artifact, Project, StageAttemptStatus, StageStatus
from ai4music_core.db.session import create_database_engine, create_session_factory
from ai4music_core.jobs.repository import JobRepository
from sqlalchemy import select


@pytest.fixture
def artifact_repository(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'artifacts.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    output = tmp_path / "output"
    output.mkdir()
    source = tmp_path / "source.wav"
    source.write_bytes(b"source")
    with factory.begin() as session:
        project = Project(
            name="Current artifacts",
            source_path=str(source),
            source_size=6,
            source_mtime_ns=source.stat().st_mtime_ns,
            output_dir=str(output),
        )
        session.add(project)
        session.flush()
        project_id = project.id
    try:
        yield JobRepository(factory), factory, project_id, output
    finally:
        engine.dispose()


def publish_normalized(repository, project_id, output):
    job = repository.create_job(project_id, operation="analyze")
    repository.start_attempt(job.stage.id)
    job = repository.complete_fingerprint(job.id, job.stage.id, digest="a" * 64)
    repository.start_attempt(job.stage.id)
    job = repository.complete_stage(job.id, job.stage.id)
    repository.start_attempt(job.stage.id)
    path = output / f"{job.id}.wav"
    path.write_bytes(b"derived")
    job = repository.complete_stage(
        job.id,
        job.stage.id,
        artifacts=[
            {
                "kind": "normalized_audio",
                "path": str(path),
                "byte_size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "content_type": "audio/wav",
            }
        ],
    )
    return job, path


def test_current_job_lookup_reads_completed_stages_before_job_finishes(artifact_repository):
    repository, _, project_id, output = artifact_repository
    job, path = publish_normalized(repository, project_id, output)
    assert job.status == "running"
    assert repository.latest_artifact(project_id, "normalized_audio") is None
    artifact = repository.current_job_artifact(job.id, "normalized_audio")
    assert artifact is not None
    assert Path(artifact["path"]) == path
    assert repository.current_job_artifact(job.id, "vocals") is None


@pytest.mark.parametrize(
    "invalid", ["stage", "attempt", "superseded", "untracked", "outside", "missing", "size"]
)
def test_current_job_lookup_rejects_unfinished_stale_or_unsafe_outputs(
    artifact_repository, invalid
):
    repository, factory, project_id, output = artifact_repository
    job, path = publish_normalized(repository, project_id, output)
    with factory.begin() as session:
        artifact = session.scalar(select(Artifact))
        if invalid == "stage":
            artifact.stage.status = StageStatus.RUNNING
        elif invalid == "attempt":
            artifact.stage.attempts[-1].status = StageAttemptStatus.RUNNING
        elif invalid == "superseded":
            artifact.artifact_metadata = {"attempt_id": "old-attempt"}
        elif invalid == "untracked":
            artifact.artifact_metadata = {}
        elif invalid == "outside":
            outside = output.parent / "external.wav"
            outside.write_bytes(path.read_bytes())
            artifact.path = str(outside)
        elif invalid == "missing":
            path.unlink()
        elif invalid == "size":
            path.write_bytes(b"changed size")
    assert repository.current_job_artifact(job.id, "normalized_audio") is None


def test_current_job_lookup_does_not_reuse_outputs_after_retry(artifact_repository):
    repository, _, project_id, output = artifact_repository
    job, _ = publish_normalized(repository, project_id, output)
    repository.start_attempt(job.stage.id)
    repository.fail_job(job.id, job.stage.id, code="test_failure", message="Later stage failed")
    repository.retry_job(job.id)
    assert repository.current_job_artifact(job.id, "normalized_audio") is None
