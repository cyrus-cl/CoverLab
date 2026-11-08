from __future__ import annotations

from typing import Annotated, Any, Literal, cast
from uuid import UUID

from ai4music_core.jobs.repository import (
    InvalidJobTransition,
    JobNotFound,
    JobRepository,
    JobView,
    ProjectNotFound,
)
from ai4music_core.jobs.runner import SingleJobRunner
from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError

from ai4music_engine.errors import LocalEngineError

jobs_router = APIRouter(tags=["analysis"])


def get_job_repository(request: Request) -> JobRepository:
    return cast(JobRepository, request.app.state.job_repository)


def get_job_runner(request: Request) -> SingleJobRunner:
    return cast(SingleJobRunner, request.app.state.job_runner)


JobRepositoryDependency = Annotated[JobRepository, Depends(get_job_repository)]
JobRunnerDependency = Annotated[SingleJobRunner, Depends(get_job_runner)]


def serialize_job(job: JobView) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "projectId": str(job.project_id),
        "status": job.status.value,
        "progressPercent": job.progress_percent,
        "errorCode": job.error_code,
        "errorMessage": job.error_message,
        "operation": job.operation,
        "stages": [serialize_stage(stage) for stage in job.stages],
        "stage": {
            "id": str(job.stage.id),
            "name": job.stage.stage_name,
            "status": job.stage.status.value,
            "progressPercent": job.stage.progress_percent,
            "bytesDone": job.stage.bytes_done,
            "bytesTotal": job.stage.bytes_total,
            "progressUnit": job.stage.progress_unit,
            "unitsDone": job.stage.units_done,
            "unitsTotal": job.stage.units_total,
            "progressPhase": job.stage.progress_phase,
            "attempts": [
                {
                    "id": str(attempt.id),
                    "number": attempt.attempt_number,
                    "status": attempt.status.value,
                    "processId": attempt.process_id,
                }
                for attempt in job.stage.attempts
            ],
        },
    }


def serialize_stage(stage: Any) -> dict[str, Any]:
    return {
        "id": str(stage.id),
        "name": stage.stage_name,
        "status": stage.status.value,
        "progressPercent": stage.progress_percent,
        "bytesDone": stage.bytes_done,
        "bytesTotal": stage.bytes_total,
        "progressUnit": stage.progress_unit,
        "unitsDone": stage.units_done,
        "unitsTotal": stage.units_total,
        "progressPhase": stage.progress_phase,
        "attempts": [
            {
                "id": str(item.id),
                "number": item.attempt_number,
                "status": item.status.value,
                "processId": item.process_id,
            }
            for item in stage.attempts
        ],
    }


class ProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["prepare", "separate", "analyze", "lyrics", "singing"]
    allowCloudUpload: Literal[True, False] = False


@jobs_router.post("/projects/{project_id}/process", status_code=status.HTTP_201_CREATED)
async def process_audio(
    project_id: UUID,
    payload: ProcessRequest,
    repository: JobRepositoryDependency,
    runner: JobRunnerDependency,
) -> dict[str, Any]:
    if payload.operation in {"lyrics", "singing"} and payload.allowCloudUpload is not True:
        raise LocalEngineError(
            status_code=422,
            code="cloud_consent_required",
            message="请先确认将分离人声发送至百炼识别。",
        )
    try:
        job = repository.create_job(
            project_id, operation=payload.operation, allow_cloud_upload=payload.allowCloudUpload
        )
    except IntegrityError as error:
        raise LocalEngineError(
            status_code=409, code="active_job_exists", message="Another audio task is active"
        ) from error
    except ProjectNotFound as error:
        raise _job_error(error) from error
    await runner.enqueue(job.id)
    return serialize_job(job)


def _job_error(error: Exception) -> LocalEngineError:
    if isinstance(error, JobNotFound | ProjectNotFound):
        return LocalEngineError(
            status_code=404,
            code="analysis_not_found",
            message="Analysis or project not found",
        )
    if isinstance(error, InvalidJobTransition):
        return LocalEngineError(
            status_code=409,
            code="invalid_job_transition",
            message="Analysis state does not allow this action",
        )
    raise error


@jobs_router.post(
    "/projects/{project_id}/analyses",
    status_code=status.HTTP_201_CREATED,
)
async def create_analysis(
    project_id: UUID,
    repository: JobRepositoryDependency,
    runner: JobRunnerDependency,
) -> dict[str, Any]:
    try:
        job = repository.create_job(project_id)
    except IntegrityError as error:
        raise LocalEngineError(
            status_code=409,
            code="active_job_exists",
            message="Another analysis is already active",
        ) from error
    except ProjectNotFound as error:
        raise _job_error(error) from error
    await runner.enqueue(job.id)
    return serialize_job(job)


@jobs_router.get("/projects/{project_id}/analyses/latest")
async def get_latest_analysis(
    project_id: UUID,
    repository: JobRepositoryDependency,
) -> dict[str, Any]:
    job = repository.latest_job_for_project(project_id)
    if job is None:
        raise LocalEngineError(
            status_code=404,
            code="analysis_not_found",
            message="Analysis not found",
        )
    return serialize_job(job)


@jobs_router.get("/analyses/{analysis_id}")
async def get_analysis(
    analysis_id: UUID,
    repository: JobRepositoryDependency,
) -> dict[str, Any]:
    try:
        return serialize_job(repository.require_job(analysis_id))
    except JobNotFound as error:
        raise _job_error(error) from error


@jobs_router.post("/analyses/{analysis_id}/cancel")
async def cancel_analysis(
    analysis_id: UUID,
    runner: JobRunnerDependency,
) -> dict[str, Any]:
    try:
        return serialize_job(await runner.cancel(analysis_id))
    except (JobNotFound, InvalidJobTransition) as error:
        raise _job_error(error) from error


@jobs_router.post("/analyses/{analysis_id}/retry")
async def retry_analysis(
    analysis_id: UUID,
    runner: JobRunnerDependency,
) -> dict[str, Any]:
    try:
        return serialize_job(await runner.retry(analysis_id))
    except (JobNotFound, InvalidJobTransition) as error:
        raise _job_error(error) from error
