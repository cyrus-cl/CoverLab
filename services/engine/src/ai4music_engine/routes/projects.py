from __future__ import annotations

from typing import Annotated, Any, cast
from uuid import UUID

from ai4music_core.db.models import Project
from ai4music_core.media.files import (
    OutputDirectoryError,
    SourceFileError,
    inspect_source,
    prepare_project_directory,
)
from ai4music_core.projects.repository import ProjectRepository
from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from ai4music_engine.errors import LocalEngineError
from ai4music_engine.serialization import utc_isoformat

projects_router = APIRouter(prefix="/projects", tags=["projects"])


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    source_path: str = Field(alias="sourcePath", min_length=1)
    output_dir: str = Field(alias="outputDir", min_length=1)
    name: str | None = Field(default=None, max_length=255)


def get_project_repository(request: Request) -> ProjectRepository:
    factory = cast(sessionmaker[Session], request.app.state.session_factory)
    return ProjectRepository(factory)


ProjectRepositoryDependency = Annotated[ProjectRepository, Depends(get_project_repository)]


def _serialize_project(project: Project) -> dict[str, Any]:
    return {
        "id": str(project.id),
        "name": project.name,
        "sourcePath": project.source_path,
        "sourceSize": project.source_size,
        "sourceMtimeNs": project.source_mtime_ns,
        "sourceSha256": project.source_sha256,
        "outputDir": project.output_dir,
        "status": project.status.value,
        "lastOpenedAt": utc_isoformat(project.last_opened_at),
        "createdAt": utc_isoformat(project.created_at),
        "updatedAt": utc_isoformat(project.updated_at),
    }


def _path_error(error: SourceFileError | OutputDirectoryError) -> LocalEngineError:
    messages = {
        "source_invalid": "Selected source path is not a regular local file",
        "source_missing": "Selected source file does not exist",
        "source_not_file": "Selected source path is not a file",
        "output_invalid": "Selected output path is invalid",
        "output_not_directory": "Selected output path is not a directory",
        "output_parent_missing": "The output directory parent does not exist",
        "output_unavailable": "The output directory cannot be created",
    }
    return LocalEngineError(
        status_code=422,
        code=error.code,
        message=messages[error.code],
    )


@projects_router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: CreateProjectRequest,
    repository: ProjectRepositoryDependency,
) -> dict[str, Any]:
    try:
        snapshot = inspect_source(payload.source_path)
        project_name = payload.name.strip() if payload.name else snapshot.path.stem
        if not project_name:
            project_name = "Untitled Project"
        project_directory = prepare_project_directory(payload.output_dir, project_name)
    except (SourceFileError, OutputDirectoryError) as error:
        raise _path_error(error) from error

    try:
        project = repository.create(
            snapshot=snapshot,
            output_dir=project_directory,
            name=project_name,
        )
    except Exception:
        try:
            project_directory.rmdir()
        except OSError:
            pass
        raise
    return _serialize_project(project)


@projects_router.get("")
async def list_projects(repository: ProjectRepositoryDependency) -> list[dict[str, Any]]:
    return [_serialize_project(project) for project in repository.list()]


@projects_router.get("/{project_id}")
async def get_project(
    project_id: UUID,
    repository: ProjectRepositoryDependency,
) -> dict[str, Any]:
    project = repository.get(project_id)
    if project is None:
        raise LocalEngineError(
            status_code=404,
            code="project_not_found",
            message="Project not found",
        )
    return _serialize_project(project)
