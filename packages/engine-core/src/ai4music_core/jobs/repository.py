from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, joinedload, selectinload, sessionmaker

from ai4music_core.db.base import utc_now
from ai4music_core.db.models import (
    AnalysisJob,
    AnalysisStatus,
    Artifact,
    JobEvent,
    JobStage,
    JobStageAttempt,
    ModelRelease,
    Project,
    ProjectStatus,
    StageAttemptStatus,
    StageStatus,
)
from ai4music_core.db.session import session_scope

JobStatus = AnalysisStatus

JOB_TRANSITIONS = {
    JobStatus.CREATED: {JobStatus.QUEUED},
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.CANCELED},
    JobStatus.RUNNING: {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELING},
    JobStatus.CANCELING: {JobStatus.CANCELED, JobStatus.FAILED},
    JobStatus.FAILED: {JobStatus.QUEUED},
    JobStatus.CANCELED: {JobStatus.QUEUED},
    JobStatus.SUCCEEDED: set(),
}
TERMINAL_JOB_STATUSES = {
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.CANCELED,
}
_CLOUD_GATEWAY_VERSION = 1

PIPELINES = {
    "fingerprint": ("fingerprint_source",),
    "prepare": ("fingerprint_source", "validate_media", "normalize_audio"),
    "separate": (
        "fingerprint_source",
        "validate_media",
        "prepare_model",
        "separate_stems",
    ),
    "analyze": (
        "fingerprint_source",
        "validate_media",
        "normalize_audio",
        "extract_waveform",
        "estimate_beats",
    ),
    "lyrics": (
        "fingerprint_source",
        "validate_media",
        "prepare_lyrics_model",
        "transcribe_lyrics",
    ),
    "singing": (
        "fingerprint_source",
        "validate_media",
        "prepare_model",
        "separate_stems",
        "prepare_lyrics_model",
        "transcribe_lyrics",
    ),
    "lyrics_edit": ("save_lyrics",),
    "recording": ("save_recording",),
}


class JobNotFound(LookupError):
    pass


class ProjectNotFound(LookupError):
    pass


class InvalidJobTransition(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AttemptView:
    id: UUID
    attempt_number: int
    status: StageAttemptStatus
    process_id: int | None


@dataclass(frozen=True, slots=True)
class StageView:
    id: UUID
    stage_name: str
    status: StageStatus
    progress_percent: int
    bytes_done: int
    bytes_total: int | None
    attempts: tuple[AttemptView, ...]
    progress_unit: str = "bytes"
    units_done: float = 0
    units_total: float | None = None
    progress_phase: str | None = None


@dataclass(frozen=True, slots=True)
class EventView:
    id: UUID
    sequence: int
    event_type: str
    message: str
    payload: dict[str, Any]
    created_at: datetime

    @property
    def progress_percent(self) -> int | None:
        value = self.payload.get("progressPercent")
        return value if isinstance(value, int) else None


@dataclass(frozen=True, slots=True)
class JobView:
    id: UUID
    project_id: UUID
    status: AnalysisStatus
    progress_percent: int
    stage: StageView
    events: tuple[EventView, ...]
    error_code: str | None
    error_message: str | None
    operation: str = "fingerprint"
    stages: tuple[StageView, ...] = ()


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: UUID
    stage_id: UUID
    project_id: UUID
    source_path: Path
    source_size: int
    source_mtime_ns: int
    output_dir: Path
    operation: str
    allow_cloud_upload: bool = False
    source_sha256: str | None = None


class JobRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_job(
        self, project_id: UUID, *, operation: str = "fingerprint", allow_cloud_upload: bool = False
    ) -> JobView:
        if operation not in PIPELINES:
            raise ValueError("Unknown audio operation")
        configuration: dict[str, Any] = {
            "operation": operation,
            "allow_cloud_upload": allow_cloud_upload,
        }
        if allow_cloud_upload:
            configuration["cloud_gateway_version"] = _CLOUD_GATEWAY_VERSION
        with session_scope(self._session_factory) as session:
            project = session.get(Project, project_id)
            if project is None:
                raise ProjectNotFound(project_id)
            job = AnalysisJob(
                project_id=project.id,
                status=JobStatus.CREATED,
                configuration=configuration,
            )
            stages = [
                JobStage(
                    analysis=job, stage_name=name, position=position, status=StageStatus.PENDING
                )
                for position, name in enumerate(PIPELINES[operation])
            ]
            stage = stages[0]
            session.add_all([job, *stages])
            session.flush()
            self._transition(job, JobStatus.QUEUED)
            job.queued_at = utc_now()
            stage.status = StageStatus.QUEUED
            self._append_event(
                session,
                job,
                stage=stage,
                event_type="JOB_QUEUED",
                message="Analysis queued",
            )
            job_id = job.id
        return self.require_job(job_id)

    def get_job(self, job_id: UUID) -> JobView | None:
        with self._session_factory() as session:
            job = session.scalar(self._job_statement(job_id))
            return self._to_view(job) if job is not None else None

    def require_job(self, job_id: UUID) -> JobView:
        job = self.get_job(job_id)
        if job is None:
            raise JobNotFound(job_id)
        return job

    def latest_job_for_project(self, project_id: UUID) -> JobView | None:
        with self._session_factory() as session:
            job_id = session.scalar(
                select(AnalysisJob.id)
                .where(AnalysisJob.project_id == project_id)
                .order_by(AnalysisJob.created_at.desc(), AnalysisJob.id.desc())
                .limit(1)
            )
        return self.get_job(job_id) if job_id is not None else None

    def queued_job_ids(self) -> list[UUID]:
        with self._session_factory() as session:
            statement = (
                select(AnalysisJob.id)
                .where(AnalysisJob.status == JobStatus.QUEUED)
                .order_by(AnalysisJob.queued_at, AnalysisJob.created_at)
            )
            return list(session.scalars(statement))

    def start_attempt(self, stage_id: UUID) -> ClaimedJob:
        with session_scope(self._session_factory) as session:
            statement = (
                select(JobStage)
                .where(JobStage.id == stage_id)
                .options(joinedload(JobStage.analysis).joinedload(AnalysisJob.project))
            )
            stage = session.scalar(statement)
            if stage is None:
                raise JobNotFound(stage_id)
            job = stage.analysis
            if (
                job.status not in {JobStatus.QUEUED, JobStatus.RUNNING}
                or stage.status is not StageStatus.QUEUED
            ):
                raise InvalidJobTransition(f"cannot start job in {job.status.value}")
            if job.status is JobStatus.QUEUED:
                self._transition(job, JobStatus.RUNNING)
                job.started_at = utc_now()
            job.finished_at = None
            job.error_code = None
            job.error_message = None
            stage.status = StageStatus.RUNNING
            stage.progress_percent = 0
            stage.bytes_done = 0
            stage.bytes_total = None
            job.progress_percent = self._overall_progress(job)
            stage.attempt_count += 1
            stage.started_at = utc_now()
            stage.finished_at = None
            stage.error_code = None
            stage.error_message = None
            attempt = JobStageAttempt(
                stage=stage,
                attempt_number=stage.attempt_count,
                status=StageAttemptStatus.RUNNING,
                heartbeat_at=utc_now(),
            )
            session.add(attempt)
            job.project.status = ProjectStatus.ANALYZING
            self._append_event(
                session,
                job,
                stage=stage,
                event_type="FINGERPRINT_STARTED"
                if stage.stage_name == "fingerprint_source"
                else "STAGE_STARTED",
                message=stage.stage_name,
                payload={"attempt": attempt.attempt_number, "stageName": stage.stage_name},
            )
            session.flush()
            return ClaimedJob(
                id=job.id,
                stage_id=stage.id,
                project_id=job.project.id,
                source_path=Path(job.project.source_path),
                source_size=job.project.source_size,
                source_mtime_ns=job.project.source_mtime_ns,
                output_dir=Path(job.project.output_dir),
                operation=str(job.configuration.get("operation", "fingerprint")),
                allow_cloud_upload=(
                    job.configuration.get("allow_cloud_upload") is True
                    and job.configuration.get("cloud_gateway_version") == _CLOUD_GATEWAY_VERSION
                ),
                source_sha256=job.project.source_sha256,
            )

    def record_progress(
        self,
        job_id: UUID,
        stage_id: UUID,
        *,
        bytes_done: int,
        bytes_total: int,
    ) -> None:
        with session_scope(self._session_factory) as session:
            job, stage = self._load_job_and_stage(session, job_id, stage_id)
            if job.status not in {JobStatus.RUNNING, JobStatus.CANCELING}:
                raise InvalidJobTransition(f"cannot record progress in {job.status.value}")
            progress = 100 if bytes_total == 0 else min(100, bytes_done * 100 // bytes_total)
            previous_progress = stage.progress_percent
            stage.bytes_done = max(stage.bytes_done, bytes_done)
            stage.bytes_total = bytes_total
            stage.progress_percent = max(stage.progress_percent, progress)
            job.progress_percent = self._overall_progress(job)
            attempt = self._running_attempt(session, stage.id)
            if attempt is not None:
                attempt.heartbeat_at = utc_now()
            if stage.progress_percent > previous_progress:
                self._append_event(
                    session,
                    job,
                    stage=stage,
                    event_type="PROGRESS",
                    message="Fingerprint progress",
                    payload={
                        "bytesDone": stage.bytes_done,
                        "bytesTotal": bytes_total,
                        "progressPercent": job.progress_percent,
                        "stageProgressPercent": stage.progress_percent,
                        "stageName": stage.stage_name,
                    },
                )

    def complete_fingerprint(self, job_id: UUID, stage_id: UUID, *, digest: str) -> JobView:
        with session_scope(self._session_factory) as session:
            job, stage = self._load_job_and_stage(session, job_id, stage_id)
            if job.status is not JobStatus.RUNNING or stage.status is not StageStatus.RUNNING:
                raise InvalidJobTransition("fingerprint completion requires a running job")
            attempt = self._require_running_attempt(session, stage.id)
            now = utc_now()
            attempt.status = StageAttemptStatus.SUCCEEDED
            attempt.finished_at = now
            stage.status = StageStatus.SUCCEEDED
            stage.progress_percent = 100
            stage.finished_at = now
            job.project.source_sha256 = digest
            self._advance_stage(job, stage)
            self._append_event(
                session,
                job,
                stage=stage,
                event_type="FINGERPRINT_SUCCEEDED",
                message="Source fingerprint complete",
                payload={
                    "sha256": digest,
                    "progressPercent": job.progress_percent,
                    "stageProgressPercent": 100,
                    "stageName": stage.stage_name,
                },
            )
        return self.require_job(job_id)

    def record_stage_progress(
        self,
        job_id: UUID,
        stage_id: UUID,
        *,
        completed: float,
        total: float,
        unit: str,
        phase: str | None = None,
    ) -> None:
        with session_scope(self._session_factory) as session:
            job, stage = self._load_job_and_stage(session, job_id, stage_id)
            if (
                job.status not in {JobStatus.RUNNING, JobStatus.CANCELING}
                or stage.status is not StageStatus.RUNNING
            ):
                raise InvalidJobTransition("stage progress requires a running stage")
            percentage = min(100, max(0, int(100 * completed / total))) if total > 0 else 0
            attempt = self._require_running_attempt(session, stage_id)
            previous = attempt.metrics.get("progress", {})
            progress = {"completed": completed, "total": total, "unit": unit, "phase": phase}
            if previous == progress:
                return
            attempt.metrics = {**attempt.metrics, "progress": progress}
            attempt.heartbeat_at = utc_now()
            stage.progress_percent = percentage
            if unit == "bytes":
                stage.bytes_done = int(completed)
                stage.bytes_total = int(total)
            job.progress_percent = self._overall_progress(job)
            self._append_event(
                session,
                job,
                stage=stage,
                event_type="STAGE_PROGRESS",
                message=stage.stage_name,
                payload={
                    **progress,
                    "stageName": stage.stage_name,
                    "stageProgressPercent": percentage,
                    "progressPercent": job.progress_percent,
                },
            )

    def complete_stage(
        self,
        job_id: UUID,
        stage_id: UUID,
        *,
        result: dict[str, Any] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
    ) -> JobView:
        with session_scope(self._session_factory) as session:
            job, stage = self._load_job_and_stage(session, job_id, stage_id)
            if job.status is not JobStatus.RUNNING or stage.status is not StageStatus.RUNNING:
                raise InvalidJobTransition("stage completion requires a running job")
            attempt = self._require_running_attempt(session, stage_id)
            attempt.metrics = {**attempt.metrics, "result": result or {}}
            if stage.stage_name == "prepare_model" and result is not None:
                provider = "demucs"
                release = session.scalar(
                    select(ModelRelease).where(
                        ModelRelease.provider == provider,
                        ModelRelease.model_id == result["model_id"],
                        ModelRelease.revision == result["revision"],
                    )
                )
                if release is None:
                    release = ModelRelease(
                        provider=provider,
                        capability="separation",
                        model_id=result["model_id"],
                        revision=result["revision"],
                        files_sha256=result["model_sha256"],
                        code_license="MIT",
                        weights_license="MIT",
                    )
                    session.add(release)
                release.enabled = True
                release.installed_path = result["path"]
            attempt.status = StageAttemptStatus.SUCCEEDED
            attempt.finished_at = utc_now()
            stage.status = StageStatus.SUCCEEDED
            stage.progress_percent = 100
            stage.finished_at = utc_now()
            for data in artifacts or []:
                data = {
                    **data,
                    "artifact_metadata": {
                        **data.get("artifact_metadata", {}),
                        "attempt_id": str(attempt.id),
                    },
                }
                session.add(
                    Artifact(
                        project_id=job.project_id, analysis_id=job.id, stage_id=stage.id, **data
                    )
                )
            finished = self._advance_stage(job, stage)
            self._append_event(
                session,
                job,
                stage=stage,
                event_type="PROCESSING_SUCCEEDED" if finished else "STAGE_SUCCEEDED",
                message=stage.stage_name,
                payload={
                    "stageName": stage.stage_name,
                    "progressPercent": job.progress_percent,
                    "result": result or {},
                },
            )
        return self.require_job(job_id)

    def stage_result(self, job_id: UUID, stage_name: str) -> dict[str, Any]:
        result = self.optional_stage_result(job_id, stage_name)
        if result is None:
            raise InvalidJobTransition("stage does not exist")
        return result

    def optional_stage_result(self, job_id: UUID, stage_name: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            job = self._load_job(session, job_id)
            stage = next((item for item in job.stages if item.stage_name == stage_name), None)
            if stage is None:
                return None
            if stage.status is not StageStatus.SUCCEEDED:
                raise InvalidJobTransition("stage has no completed result")
            return dict(stage.attempts[-1].metrics.get("result", {}))

    def latest_artifact(self, project_id: UUID, kind: str) -> dict[str, Any] | None:
        """Read a published current-attempt output without trusting stale disk paths."""
        with self._session_factory() as session:
            statement = (
                select(Artifact)
                .join(AnalysisJob, Artifact.analysis_id == AnalysisJob.id)
                .where(
                    Artifact.project_id == project_id,
                    Artifact.kind == kind,
                    AnalysisJob.status == JobStatus.SUCCEEDED,
                )
                .order_by(Artifact.published_at.desc(), Artifact.id)
            )
            for item in session.scalars(statement):
                attempt_id = item.artifact_metadata.get("attempt_id")
                if attempt_id and (
                    not item.stage
                    or not item.stage.attempts
                    or str(item.stage.attempts[-1].id) != attempt_id
                ):
                    continue
                path = Path(item.path).resolve()
                try:
                    if (
                        path.is_relative_to(Path(item.project.output_dir).resolve())
                        and path.is_file()
                        and path.stat().st_size == item.byte_size
                    ):
                        return {
                            "path": str(path),
                            "metadata": dict(item.artifact_metadata),
                            "sha256": item.sha256,
                        }
                except OSError:
                    continue
        return None

    def current_job_artifact(self, job_id: UUID, kind: str) -> dict[str, Any] | None:
        """Read a completed current-attempt stage output for an internal consumer.

        A running pipeline can consume its earlier committed stages before the
        overall job publishes results. Pending/retried stages and other jobs
        are never eligible.
        """
        with self._session_factory() as session:
            statement = (
                select(Artifact)
                .join(JobStage, Artifact.stage_id == JobStage.id)
                .join(AnalysisJob, Artifact.analysis_id == AnalysisJob.id)
                .where(
                    Artifact.analysis_id == job_id,
                    Artifact.kind == kind,
                    Artifact.project_id == AnalysisJob.project_id,
                    JobStage.analysis_id == job_id,
                    JobStage.status == StageStatus.SUCCEEDED,
                )
                .order_by(Artifact.published_at.desc(), Artifact.id)
            )
            for item in session.scalars(statement):
                stage = item.stage
                if not stage or not stage.attempts:
                    continue
                attempt = stage.attempts[-1]
                if attempt.status is not StageAttemptStatus.SUCCEEDED or item.artifact_metadata.get(
                    "attempt_id"
                ) != str(attempt.id):
                    continue
                try:
                    path = Path(item.path).resolve()
                    if (
                        path.is_relative_to(Path(item.project.output_dir).resolve())
                        and path.is_file()
                        and path.stat().st_size == item.byte_size
                    ):
                        return {
                            "path": str(path),
                            "metadata": dict(item.artifact_metadata),
                            "sha256": item.sha256,
                        }
                except OSError:
                    continue
        return None

    @staticmethod
    def _overall_progress(job: AnalysisJob) -> int:
        if len(job.stages) == 1:
            return job.stages[0].progress_percent
        # Overall counts completed stages. Current-phase measured units are
        # separate, so model-download verification cannot regress the overall.
        return (
            100
            * sum(stage.status is StageStatus.SUCCEEDED for stage in job.stages)
            // len(job.stages)
        )

    @classmethod
    def _advance_stage(cls, job: AnalysisJob, stage: JobStage) -> bool:
        following = next(
            (
                item
                for item in sorted(job.stages, key=lambda item: item.position)
                if item.position > stage.position and item.status is not StageStatus.SUCCEEDED
            ),
            None,
        )
        if following is None:
            cls._transition(job, JobStatus.SUCCEEDED)
            job.progress_percent = 100
            job.finished_at = utc_now()
            job.project.status = ProjectStatus.COMPLETE
        else:
            following.status = StageStatus.QUEUED
            job.progress_percent = cls._overall_progress(job)
        return following is None

    def fail_job(
        self,
        job_id: UUID,
        stage_id: UUID,
        *,
        code: str,
        message: str,
    ) -> JobView:
        with session_scope(self._session_factory) as session:
            job, stage = self._load_job_and_stage(session, job_id, stage_id)
            if job.status not in {JobStatus.RUNNING, JobStatus.CANCELING}:
                raise InvalidJobTransition(f"cannot fail job in {job.status.value}")
            now = utc_now()
            attempt = self._running_attempt(session, stage.id)
            if attempt is not None:
                attempt.status = StageAttemptStatus.FAILED
                attempt.finished_at = now
                attempt.error_code = code
                attempt.error_message = message
            stage.status = StageStatus.FAILED
            stage.finished_at = now
            stage.error_code = code
            stage.error_message = message
            self._transition(job, JobStatus.FAILED)
            job.finished_at = now
            job.error_code = code
            job.error_message = message
            job.project.status = ProjectStatus.ERROR
            self._append_event(
                session,
                job,
                stage=stage,
                event_type="JOB_FAILED",
                message=message,
                payload={"errorCode": code},
            )
        return self.require_job(job_id)

    def request_cancel(self, job_id: UUID) -> JobView:
        with session_scope(self._session_factory) as session:
            job = self._load_job(session, job_id)
            stage = self._foundation_stage(job)
            now = utc_now()
            if job.status is JobStatus.QUEUED:
                self._transition(job, JobStatus.CANCELED)
                job.cancel_requested_at = now
                job.canceled_at = now
                job.finished_at = now
                stage.status = StageStatus.CANCELED
                stage.finished_at = now
                job.project.status = ProjectStatus.READY
                self._append_event(
                    session,
                    job,
                    stage=stage,
                    event_type="JOB_CANCELED",
                    message="Analysis canceled",
                )
            elif job.status is JobStatus.RUNNING:
                self._transition(job, JobStatus.CANCELING)
                job.cancel_requested_at = now
                self._append_event(
                    session,
                    job,
                    stage=stage,
                    event_type="CANCEL_REQUESTED",
                    message="Cancellation requested",
                )
            elif job.status not in {JobStatus.CANCELING, JobStatus.CANCELED}:
                raise InvalidJobTransition(f"cannot cancel job in {job.status.value}")
        return self.require_job(job_id)

    def mark_canceled(self, job_id: UUID, stage_id: UUID) -> JobView:
        with session_scope(self._session_factory) as session:
            job, stage = self._load_job_and_stage(session, job_id, stage_id)
            if job.status is JobStatus.RUNNING:
                self._transition(job, JobStatus.CANCELING)
            if job.status is not JobStatus.CANCELING:
                raise InvalidJobTransition(f"cannot finish cancellation in {job.status.value}")
            now = utc_now()
            attempt = self._running_attempt(session, stage.id)
            if attempt is not None:
                attempt.status = StageAttemptStatus.CANCELED
                attempt.finished_at = now
            stage.status = StageStatus.CANCELED
            stage.finished_at = now
            self._transition(job, JobStatus.CANCELED)
            job.canceled_at = now
            job.finished_at = now
            job.project.status = ProjectStatus.READY
            self._append_event(
                session,
                job,
                stage=stage,
                event_type="JOB_CANCELED",
                message="Analysis canceled",
            )
        return self.require_job(job_id)

    def retry_job(self, job_id: UUID) -> JobView:
        with session_scope(self._session_factory) as session:
            job = self._load_job(session, job_id)
            if job.configuration.get("operation") in {"recording", "lyrics_edit"}:
                raise InvalidJobTransition("Save again from the retained capture or lyric editor")
            if job.status not in {JobStatus.FAILED, JobStatus.CANCELED}:
                raise InvalidJobTransition(f"cannot retry job in {job.status.value}")
            stage = self._foundation_stage(job)
            self._transition(job, JobStatus.QUEUED)
            now = utc_now()
            job.queued_at = now
            job.started_at = None
            job.finished_at = None
            job.cancel_requested_at = None
            job.canceled_at = None
            job.error_code = None
            job.error_message = None
            job.progress_percent = 0
            for item in job.stages:
                item.status = StageStatus.QUEUED if item.position == 0 else StageStatus.PENDING
                item.bytes_done = 0
                item.bytes_total = None
                item.progress_percent = 0
                item.started_at = None
                item.finished_at = None
                item.error_code = None
                item.error_message = None
            stage = self._foundation_stage(job)
            job.project.status = ProjectStatus.READY
            self._append_event(
                session,
                job,
                stage=stage,
                event_type="JOB_RETRIED",
                message="Analysis queued for retry",
            )
        return self.require_job(job_id)

    def recover_interrupted_jobs(self) -> list[UUID]:
        recovered: list[UUID] = []
        with session_scope(self._session_factory) as session:
            statement = (
                select(AnalysisJob)
                .where(AnalysisJob.status.in_([JobStatus.RUNNING, JobStatus.CANCELING]))
                .options(
                    joinedload(AnalysisJob.project),
                    selectinload(AnalysisJob.stages).selectinload(JobStage.attempts),
                )
                .order_by(AnalysisJob.created_at)
            )
            for job in session.scalars(statement).unique():
                stage = self._foundation_stage(job)
                attempt = next(
                    (
                        item
                        for item in reversed(stage.attempts)
                        if item.status is StageAttemptStatus.RUNNING
                    ),
                    None,
                )
                if attempt is not None:
                    attempt.status = StageAttemptStatus.WORKER_LOST
                    attempt.finished_at = utc_now()
                    attempt.error_code = "worker_lost"
                    attempt.error_message = "Local engine stopped during analysis"
                if job.status is JobStatus.CANCELING:
                    job.status = JobStatus.CANCELED
                    job.canceled_at = utc_now()
                    job.finished_at = utc_now()
                    stage.status = StageStatus.CANCELED
                    stage.finished_at = utc_now()
                    job.project.status = ProjectStatus.READY
                    event_type = "JOB_CANCELED"
                    message = "Analysis canceled after restart"
                elif job.configuration.get("operation") in {"recording", "lyrics_edit"}:
                    job.status = JobStatus.FAILED
                    job.finished_at = utc_now()
                    job.error_code = "save_interrupted"
                    job.error_message = (
                        "Saving stopped; save again from the editor or record another take"
                    )
                    stage.status = StageStatus.FAILED
                    stage.finished_at = utc_now()
                    stage.error_code = "save_interrupted"
                    job.project.status = ProjectStatus.READY
                    event_type = "PROCESSING_FAILED"
                    message = "Saving interrupted; request data is not available after restart"
                else:
                    job.status = JobStatus.QUEUED
                    job.queued_at = utc_now()
                    job.started_at = None
                    stage.status = StageStatus.QUEUED
                    stage.started_at = None
                    stage.heartbeat_at = None
                    job.project.status = ProjectStatus.READY
                    recovered.append(job.id)
                    event_type = "WORKER_LOST"
                    message = "Analysis interrupted and requeued"
                self._append_event(
                    session,
                    job,
                    stage=stage,
                    event_type=event_type,
                    message=message,
                )
        return recovered

    def interrupt_job(self, job_id: UUID, stage_id: UUID) -> JobView:
        with session_scope(self._session_factory) as session:
            job, stage = self._load_job_and_stage(session, job_id, stage_id)
            if job.status not in {JobStatus.RUNNING, JobStatus.CANCELING}:
                return self._to_view(job)
            attempt = self._running_attempt(session, stage.id)
            if attempt is not None:
                attempt.status = StageAttemptStatus.WORKER_LOST
                attempt.finished_at = utc_now()
                attempt.error_code = "worker_lost"
                attempt.error_message = "Local engine stopped during analysis"
            job.status = JobStatus.QUEUED
            job.queued_at = utc_now()
            job.started_at = None
            job.cancel_requested_at = None
            stage.status = StageStatus.QUEUED
            stage.started_at = None
            stage.heartbeat_at = None
            job.project.status = ProjectStatus.READY
            self._append_event(
                session,
                job,
                stage=stage,
                event_type="WORKER_LOST",
                message="Analysis interrupted and requeued",
            )
        return self.require_job(job_id)

    def events_after(self, job_id: UUID, sequence: int) -> list[EventView]:
        with self._session_factory() as session:
            if session.get(AnalysisJob, job_id) is None:
                raise JobNotFound(job_id)
            statement = (
                select(JobEvent)
                .where(JobEvent.analysis_id == job_id, JobEvent.sequence > sequence)
                .order_by(JobEvent.sequence)
            )
            return [self._event_view(event) for event in session.scalars(statement)]

    def is_terminal(self, job_id: UUID) -> bool:
        with self._session_factory() as session:
            status = session.scalar(select(AnalysisJob.status).where(AnalysisJob.id == job_id))
            if status is None:
                raise JobNotFound(job_id)
            return status in TERMINAL_JOB_STATUSES

    @staticmethod
    def _transition(job: AnalysisJob, new_status: AnalysisStatus) -> None:
        if new_status not in JOB_TRANSITIONS[job.status]:
            raise InvalidJobTransition(
                f"cannot transition {job.status.value} to {new_status.value}"
            )
        job.status = new_status

    @staticmethod
    def _foundation_stage(job: AnalysisJob) -> JobStage:
        if not job.stages:
            raise JobNotFound(f"job {job.id} has no stage")
        ordered = sorted(job.stages, key=lambda item: item.position)
        return next(
            (item for item in ordered if item.status is not StageStatus.SUCCEEDED), ordered[-1]
        )

    @staticmethod
    def _job_statement(job_id: UUID) -> Select[AnalysisJob]:
        return (
            select(AnalysisJob)
            .where(AnalysisJob.id == job_id)
            .options(
                joinedload(AnalysisJob.project),
                selectinload(AnalysisJob.stages).selectinload(JobStage.attempts),
                selectinload(AnalysisJob.events),
            )
        )

    def _load_job(self, session: Session, job_id: UUID) -> AnalysisJob:
        job = session.scalar(self._job_statement(job_id))
        if job is None:
            raise JobNotFound(job_id)
        return job

    def _load_job_and_stage(
        self,
        session: Session,
        job_id: UUID,
        stage_id: UUID,
    ) -> tuple[AnalysisJob, JobStage]:
        job = self._load_job(session, job_id)
        stage = next((item for item in job.stages if item.id == stage_id), None)
        if stage is None:
            raise JobNotFound(stage_id)
        return job, stage

    @staticmethod
    def _running_attempt(session: Session, stage_id: UUID) -> JobStageAttempt | None:
        statement = (
            select(JobStageAttempt)
            .where(
                JobStageAttempt.stage_id == stage_id,
                JobStageAttempt.status == StageAttemptStatus.RUNNING,
            )
            .order_by(JobStageAttempt.attempt_number.desc())
        )
        return session.scalar(statement)

    def _require_running_attempt(self, session: Session, stage_id: UUID) -> JobStageAttempt:
        attempt = self._running_attempt(session, stage_id)
        if attempt is None:
            raise InvalidJobTransition("running attempt not found")
        return attempt

    @staticmethod
    def _append_event(
        session: Session,
        job: AnalysisJob,
        *,
        stage: JobStage | None,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> JobEvent:
        # Flush state changes first so SQLite holds the writer lock while the
        # next sequence number is selected and inserted in this transaction.
        session.flush()
        last_sequence = session.scalar(
            select(func.max(JobEvent.sequence)).where(JobEvent.analysis_id == job.id)
        )
        event = JobEvent(
            analysis_id=job.id,
            stage_id=stage.id if stage is not None else None,
            sequence=(last_sequence or 0) + 1,
            event_type=event_type,
            message=message,
            payload=payload or {},
        )
        session.add(event)
        session.flush()
        return event

    @classmethod
    def _to_view(cls, job: AnalysisJob) -> JobView:
        stage = cls._foundation_stage(job)
        stages = tuple(
            cls._stage_view(item) for item in sorted(job.stages, key=lambda item: item.position)
        )
        events = tuple(
            cls._event_view(event) for event in sorted(job.events, key=lambda item: item.sequence)
        )
        return JobView(
            id=job.id,
            project_id=job.project_id,
            status=job.status,
            progress_percent=job.progress_percent,
            stage=cls._stage_view(stage),
            events=events,
            error_code=job.error_code,
            error_message=job.error_message,
            operation=str(job.configuration.get("operation", "fingerprint")),
            stages=stages,
        )

    @classmethod
    def _stage_view(cls, stage: JobStage) -> StageView:
        attempts = tuple(
            AttemptView(
                id=attempt.id,
                attempt_number=attempt.attempt_number,
                status=attempt.status,
                process_id=attempt.process_id,
            )
            for attempt in sorted(stage.attempts, key=lambda item: item.attempt_number)
        )
        progress = stage.attempts[-1].metrics.get("progress", {}) if stage.attempts else {}
        return StageView(
            id=stage.id,
            stage_name=stage.stage_name,
            status=stage.status,
            progress_percent=stage.progress_percent,
            bytes_done=stage.bytes_done,
            bytes_total=stage.bytes_total,
            attempts=attempts,
            progress_unit=str(progress.get("unit", "bytes")),
            units_done=float(progress.get("completed", stage.bytes_done)),
            units_total=progress.get("total", stage.bytes_total),
            progress_phase=progress.get("phase"),
        )

    @staticmethod
    def _event_view(event: JobEvent) -> EventView:
        return EventView(
            id=event.id,
            sequence=event.sequence,
            event_type=event.event_type,
            message=event.message,
            payload=dict(event.payload),
            created_at=event.created_at,
        )
