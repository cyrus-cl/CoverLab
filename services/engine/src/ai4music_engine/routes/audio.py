from __future__ import annotations

import hmac
import importlib.util
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from ai4music_core.db.models import AnalysisJob, AnalysisStatus, Artifact, Project
from ai4music_core.media.ffmpeg import FFmpegTools, MediaToolError
from ai4music_core.separation import DemucsProvider
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from sqlalchemy import select

from ai4music_engine.errors import LocalEngineError

audio_router = APIRouter(tags=["audio"])
playback_router = APIRouter(prefix="/api/v1", tags=["audio"])
_MEDIA_URL_TTL_SECONDS = 8 * 60 * 60
_MEDIA_HEADERS = {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"}


@audio_router.get("/runtime")
def audio_runtime(request: Request) -> dict[str, Any]:
    from ai4music_core.lyrics.bailian import BailianProvider

    try:
        FFmpegTools.discover()
        media: dict[str, Any] = {"available": True}
    except MediaToolError as error:
        media = {"available": False, "errorCode": error.code}
    return {
        "media": media,
        "separation": DemucsProvider(request.app.state.settings.model_dir).runtime_info(),
        "analysis": {
            "available": all(
                importlib.util.find_spec(name) is not None
                for name in ("numpy", "scipy", "soundfile")
            )
        },
        "lyrics": BailianProvider().runtime_info(),
    }


def references_source(artifact: Artifact) -> bool:
    return (
        artifact.kind == "normalized_audio"
        and artifact.artifact_metadata.get("source_reference") is True
    )


def serialize_artifact(artifact: Artifact) -> dict[str, Any]:
    return {
        "id": str(artifact.id),
        "analysisId": str(artifact.analysis_id),
        "kind": artifact.kind,
        "path": artifact.project.source_path if references_source(artifact) else artifact.path,
        "byteSize": artifact.byte_size,
        "sha256": artifact.sha256,
        "metadata": artifact.artifact_metadata,
        "contentUrl": f"/api/v1/artifacts/{artifact.id}/content",
    }


def is_current_artifact(artifact: Artifact) -> bool:
    attempt_id = artifact.artifact_metadata.get("attempt_id")
    if attempt_id is None:
        return True
    return bool(
        artifact.stage
        and artifact.stage.attempts
        and str(artifact.stage.attempts[-1].id) == attempt_id
    )


def artifact_path(artifact: Artifact, project: Project) -> Path:
    source_reference = references_source(artifact)
    path = Path(project.source_path if source_reference else artifact.path).resolve()
    if source_reference:
        permitted = path == Path(project.source_path) and artifact.sha256 == project.source_sha256
    else:
        permitted = path.is_relative_to(Path(project.output_dir).resolve())
    if not permitted:
        raise LocalEngineError(
            status_code=403,
            code="artifact_path_invalid",
            message="Audio output is outside the project",
        )
    stat = path.stat()
    if (
        not path.is_file()
        or stat.st_size != artifact.byte_size
        or (source_reference and stat.st_mtime_ns != project.source_mtime_ns)
    ):
        raise FileNotFoundError(path)
    return path


def is_existing_artifact(artifact: Artifact) -> bool:
    try:
        artifact_path(artifact, artifact.project)
    except (OSError, LocalEngineError):
        return False
    return True


@audio_router.get("/projects/{project_id}/artifacts")
def list_artifacts(project_id: UUID, request: Request) -> list[dict[str, Any]]:
    with request.app.state.session_factory() as session:
        if session.get(Project, project_id) is None:
            raise LocalEngineError(
                status_code=404, code="project_not_found", message="Project not found"
            )
        statement = (
            select(Artifact)
            .join(AnalysisJob, Artifact.analysis_id == AnalysisJob.id)
            .where(
                Artifact.project_id == project_id, AnalysisJob.status == AnalysisStatus.SUCCEEDED
            )
            .order_by(Artifact.published_at.desc(), Artifact.id)
        )
        return [
            serialize_artifact(item)
            for item in session.scalars(statement)
            if is_current_artifact(item) and is_existing_artifact(item)
        ]


@audio_router.get("/artifacts/{artifact_id}/content")
def artifact_content(artifact_id: UUID, request: Request) -> FileResponse:
    return serve_artifact(artifact_id, request)


def playback_signature(request: Request, artifact_id: UUID, expires: int, download: bool) -> str:
    key = request.app.state.settings.launch_token.get_secret_value().encode()
    scope = f"coverlab-media-v1:{artifact_id}:{expires}:{int(download)}"
    return hmac.digest(key, scope.encode(), "sha256").hex()


@audio_router.get("/artifacts/{artifact_id}/content/playback")
def issue_playback_url(
    artifact_id: UUID, request: Request, download: bool = False
) -> dict[str, str]:
    expires = int(time.time()) + _MEDIA_URL_TTL_SECONDS
    signature = playback_signature(request, artifact_id, expires, download)
    return {
        "contentUrl": (
            f"/api/v1/playback/{artifact_id}?expires={expires}&download={int(download)}"
            f"&signature={signature}"
        )
    }


@playback_router.api_route("/playback/{artifact_id}", methods=["GET", "HEAD"])
def signed_artifact_content(
    artifact_id: UUID, request: Request, expires: int, download: bool, signature: str
) -> FileResponse:
    now = int(time.time())
    if not (
        now < expires <= now + _MEDIA_URL_TTL_SECONDS
        and hmac.compare_digest(
            signature.encode(), playback_signature(request, artifact_id, expires, download).encode()
        )
    ):
        raise LocalEngineError(
            status_code=401, code="invalid_playback_url", message="Media link is invalid or expired"
        )
    return serve_artifact(artifact_id, request, download=download)


def serve_artifact(artifact_id: UUID, request: Request, *, download: bool = False) -> FileResponse:
    with request.app.state.session_factory() as session:
        artifact = session.get(Artifact, artifact_id)
        if artifact is None:
            raise LocalEngineError(
                status_code=404, code="artifact_not_found", message="Audio output not found"
            )
        project = session.get(Project, artifact.project_id)
        job = session.get(AnalysisJob, artifact.analysis_id)
        if project is None or job is None or job.status is not AnalysisStatus.SUCCEEDED:
            raise LocalEngineError(
                status_code=404,
                code="artifact_unavailable",
                message="Audio output is not published",
            )
        if not is_current_artifact(artifact):
            raise LocalEngineError(
                status_code=404,
                code="artifact_superseded",
                message="Audio output belongs to an earlier attempt",
            )
        try:
            path = artifact_path(artifact, project)
        except OSError as error:
            raise LocalEngineError(
                status_code=404,
                code="artifact_missing",
                message="Audio output is missing or changed",
            ) from error
        return FileResponse(
            path,
            media_type=artifact.content_type,
            filename=path.name,
            content_disposition_type="attachment" if download else "inline",
            headers=_MEDIA_HEADERS,
        )
