from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from ai4music_core.db.base import Base
from ai4music_core.db.models import AnalysisJob, Project
from ai4music_core.db.session import create_database_engine, create_session_factory
from ai4music_core.jobs.repository import InvalidJobTransition, JobRepository


def make_repository(tmp_path: Path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'stages.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    source = tmp_path / "source.wav"
    source.write_bytes(b"source")
    with factory.begin() as session:
        project = Project(
            name="Stages",
            source_path=str(source),
            source_size=6,
            source_mtime_ns=source.stat().st_mtime_ns,
            output_dir=str(tmp_path / "out"),
        )
        session.add(project)
        session.flush()
        project_id = project.id
    return engine, JobRepository(factory), project_id


def test_interrupted_capture_save_fails_without_requeuing_missing_request_body(tmp_path):
    engine, repository, project_id = make_repository(tmp_path)
    try:
        job = repository.create_job(project_id, operation="recording")
        repository.start_attempt(job.stage.id)
        assert repository.recover_interrupted_jobs() == []
        assert repository.require_job(job.id).status == "failed"
        with pytest.raises(InvalidJobTransition):
            repository.retry_job(job.id)
    finally:
        engine.dispose()


@pytest.mark.parametrize("legacy", [False, True])
def test_cloud_consent_is_scoped_to_the_gateway_version(tmp_path, legacy):
    engine, repository, project_id = make_repository(tmp_path)
    try:
        job = repository.create_job(project_id, operation="singing", allow_cloud_upload=True)
        if legacy:
            factory = create_session_factory(engine)
            with factory.begin() as session:
                stored = session.get(AnalysisJob, job.id)
                assert stored is not None
                stored.configuration = {"operation": "singing", "allow_cloud_upload": True}
        assert repository.start_attempt(job.stage.id).allow_cloud_upload is (not legacy)
    finally:
        engine.dispose()


def test_preparation_advances_real_stages_without_finishing_after_fingerprint(tmp_path: Path):
    engine, repository, project_id = make_repository(tmp_path)
    try:
        job = repository.create_job(project_id, operation="prepare")
        assert [s.stage_name for s in job.stages] == [
            "fingerprint_source",
            "validate_media",
            "normalize_audio",
        ]
        repository.start_attempt(job.stage.id)
        repository.record_progress(job.id, job.stage.id, bytes_done=3, bytes_total=6)
        progress = repository.require_job(job.id)
        assert progress.events[-1].payload["progressPercent"] == progress.progress_percent
        assert progress.events[-1].payload["stageProgressPercent"] == 50
        job = repository.complete_fingerprint(
            job.id, job.stage.id, digest=hashlib.sha256(b"source").hexdigest()
        )
        assert job.status == "running"
        assert job.stage.stage_name == "validate_media"
        assert job.progress_percent == 33
        assert job.events[-1].payload["progressPercent"] == 33
        repository.start_attempt(job.stage.id)
        job = repository.complete_stage(job.id, job.stage.id, result={"duration_seconds": 2.0})
        assert job.stage.stage_name == "normalize_audio"
        repository.start_attempt(job.stage.id)
        repository.record_stage_progress(job.id, job.stage.id, completed=1, total=2, unit="seconds")
        job = repository.require_job(job.id)
        assert job.stage.progress_percent == 50
        assert job.stage.units_done == 1
        assert job.stage.progress_unit == "seconds"
        assert job.progress_percent == 66
        assert job.status == "running"
        job = repository.complete_stage(job.id, job.stage.id)
        assert job.status == "succeeded"
        assert job.progress_percent == 100
        assert all(s.status == "succeeded" for s in job.stages)
    finally:
        engine.dispose()


def test_an_acknowledged_cancel_cannot_be_overwritten_by_completion(tmp_path: Path, monkeypatch):
    engine, repository, project_id = make_repository(tmp_path)
    ready, release, cancel_returned = Event(), Event(), Event()
    original = repository._append_event
    try:
        job = repository.create_job(project_id, operation="prepare")
        repository.start_attempt(job.stage.id)
        job = repository.complete_fingerprint(job.id, job.stage.id, digest="a" * 64)
        repository.start_attempt(job.stage.id)
        job = repository.complete_stage(job.id, job.stage.id)
        repository.start_attempt(job.stage.id)

        def paused_append(*args, **kwargs):
            if kwargs.get("event_type") == "PROCESSING_SUCCEEDED":
                ready.set()
                assert release.wait(3)
            return original(*args, **kwargs)

        monkeypatch.setattr(repository, "_append_event", paused_append)

        def cancel():
            try:
                return repository.request_cancel(job.id).status
            except InvalidJobTransition:
                return "already_finished"
            finally:
                cancel_returned.set()

        with ThreadPoolExecutor(max_workers=2) as pool:
            completing = pool.submit(repository.complete_stage, job.id, job.stage.id)
            assert ready.wait(3)
            canceling = pool.submit(cancel)
            cancel_returned.wait(0.25)
            release.set()
            completing.result(timeout=3)
            acknowledged = canceling.result(timeout=3)
        final = repository.require_job(job.id)
        assert not (acknowledged == "canceling" and final.status == "succeeded")
    finally:
        release.set()
        engine.dispose()


def test_recovery_and_retry_preserve_completed_stages_and_attempt_history(tmp_path: Path):
    engine, repository, project_id = make_repository(tmp_path)
    try:
        job = repository.create_job(project_id, operation="prepare")
        repository.start_attempt(job.stage.id)
        job = repository.complete_fingerprint(job.id, job.stage.id, digest="a" * 64)
        repository.start_attempt(job.stage.id)
        assert repository.recover_interrupted_jobs() == [job.id]
        recovered = repository.require_job(job.id)
        assert recovered.stage.stage_name == "validate_media"
        assert recovered.stages[0].status == "succeeded"
        assert recovered.stage.attempts[-1].status == "worker_lost"
        repository.start_attempt(recovered.stage.id)
        repository.fail_job(
            job.id, recovered.stage.id, code="media_invalid", message="Invalid audio"
        )
        retried = repository.retry_job(job.id)
        assert retried.stage.stage_name == "fingerprint_source"
        assert retried.stages[1].attempts[-1].status == "failed"
        assert [s.status for s in retried.stages] == ["queued", "pending", "pending"]
    finally:
        engine.dispose()
