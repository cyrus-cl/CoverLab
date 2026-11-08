"""Persist user-corrected subtitles and new captures inside the local project."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from ai4music_core.db.models import Artifact, Project
from ai4music_core.jobs.analysis_processing import file_artifact, write_json
from ai4music_core.jobs.repository import JobRepository
from ai4music_core.media.ffmpeg import FFmpegTools, MediaToolError
from ai4music_core.media.hashing import CancellationToken, JobCanceled
from ai4music_core.media.recording_mix import MAX_MIX_SECONDS
from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from ai4music_engine.errors import LocalEngineError
from ai4music_engine.recording_selection import (
    MAX_SELECTION_QUERY_BYTES,
    SavedMixSettings,
    validate_recording_ranges,
)
from ai4music_engine.routes.audio import serialize_artifact

outputs_router = APIRouter(tags=["project outputs"])
MAX_RECORDING_BYTES = 512 * 1024 * 1024
CAPTURE_STARTUP_ALLOWANCE_SECONDS = 2


def project_context(project_id: UUID, request: Request) -> tuple[Path, Path, float]:
    with request.app.state.session_factory() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise LocalEngineError(
                status_code=404, code="project_not_found", message="Project not found"
            )
        source = Path(project.source_path)
        output_dir = Path(project.output_dir).resolve()
        source_size, source_mtime = project.source_size, project.source_mtime_ns
    try:
        stat = source.stat()
        if stat.st_size != source_size or stat.st_mtime_ns != source_mtime:
            raise LocalEngineError(
                status_code=409,
                code="source_changed",
                message="Source file changed; create a new project",
            )
        duration = FFmpegTools.discover().probe(source).duration_seconds
    except (OSError, MediaToolError) as error:
        raise LocalEngineError(
            status_code=422, code="source_unavailable", message="The source audio cannot be read"
        ) from error
    return source, output_dir, duration


def start_output_job(project_id: UUID, operation: str, request: Request) -> tuple[Any, Any]:
    repository: JobRepository = request.app.state.job_repository
    try:
        job = repository.create_job(project_id, operation=operation)
        claimed = repository.start_attempt(job.stage.id)
    except IntegrityError as error:
        raise LocalEngineError(
            status_code=409, code="active_job_exists", message="Another audio task is active"
        ) from error
    return job, claimed


def published_outputs(job_id: UUID, request: Request) -> list[dict[str, Any]]:
    with request.app.state.session_factory() as session:
        return [
            serialize_artifact(item)
            for item in session.scalars(
                select(Artifact).where(Artifact.analysis_id == job_id).order_by(Artifact.kind)
            )
        ]


def finish_failed_save(job_id: UUID, stage_id: UUID, request: Request, code: str) -> None:
    repository: JobRepository = request.app.state.job_repository
    state = repository.require_job(job_id).status.value
    if state == "canceling":
        repository.mark_canceled(job_id, stage_id)
    elif state == "running":
        repository.fail_job(
            job_id, stage_id, code=code, message="The project result could not be saved"
        )


def save_document(
    project_id: UUID, document: dict[str, Any], output_dir: Path, request: Request
) -> list[dict[str, Any]]:
    from ai4music_core.lyrics.timed import render_lrc, render_srt

    repository: JobRepository = request.app.state.job_repository
    job, claimed = start_output_job(project_id, "lyrics_edit", request)
    token = CancellationToken()
    request.app.state.job_runner.register_external(job.id, claimed.stage_id, token)
    directory = output_dir / "lyrics" / str(uuid4())
    files: list[Path] = []
    try:
        directory.mkdir(parents=True, exist_ok=False)
        json_path = directory / "lyrics.json"
        files.append(json_path)
        write_json(json_path, document, token)
        artifacts = [
            file_artifact(
                json_path,
                "lyrics",
                "application/json",
                {"duration_seconds": document["durationSeconds"], "machine_draft": False},
                token,
            )
        ]
        for name, kind, content in [
            ("lyrics.lrc", "lyrics_lrc", render_lrc(document)),
            ("lyrics.srt", "lyrics_srt", render_srt(document)),
        ]:
            path = directory / name
            files.append(path)
            path.write_text(content, encoding="utf-8")
            artifacts.append(
                file_artifact(
                    path,
                    kind,
                    "text/plain; charset=utf-8",
                    {"duration_seconds": document["durationSeconds"], "machine_draft": False},
                    token,
                )
            )
        repository.complete_stage(
            job.id,
            claimed.stage_id,
            result={"segments": len(document["segments"])},
            artifacts=artifacts,
        )
    except Exception as error:
        finish_failed_save(job.id, claimed.stage_id, request, "lyrics_save_failed")
        for path in files:
            path.unlink(missing_ok=True)
        raise LocalEngineError(
            status_code=422, code="lyrics_save_failed", message="Could not save corrected lyrics"
        ) from error
    finally:
        request.app.state.job_runner.unregister_external(job.id)
    return published_outputs(job.id, request)


@outputs_router.post("/projects/{project_id}/lyrics", status_code=201)
def save_lyrics(
    project_id: UUID, payload: dict[str, Any], request: Request
) -> list[dict[str, Any]]:
    from ai4music_core.lyrics.timed import validate_document

    _, output_dir, duration = project_context(project_id, request)
    try:
        claimed_duration = float(payload.get("durationSeconds", -1))
        if not math.isfinite(claimed_duration) or abs(claimed_duration - duration) > 0.15:
            raise ValueError("duration mismatch")
        document = validate_document({**payload, "durationSeconds": duration})
        if len(document["segments"]) > 10000:
            raise ValueError("too many segments")
        if any(
            segment["end"] <= segment["start"] or not segment["text"].strip()
            for segment in document["segments"]
        ):
            raise ValueError("empty lyric range or text")
    except (ValueError, TypeError, KeyError) as error:
        raise LocalEngineError(
            status_code=422, code="lyrics_invalid", message="Lyric text or timestamps are invalid"
        ) from error
    return save_document(project_id, document, output_dir, request)


@outputs_router.post("/projects/{project_id}/lyrics/import", status_code=201)
def import_lyrics(
    project_id: UUID, payload: dict[str, Any], request: Request
) -> list[dict[str, Any]]:
    from ai4music_core.lyrics.timed import parse_lrc

    _, output_dir, duration = project_context(project_id, request)
    try:
        text = payload["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 2_000_000:
            raise ValueError("invalid LRC")
        document = parse_lrc(text, duration)
    except (ValueError, TypeError, KeyError) as error:
        raise LocalEngineError(
            status_code=422, code="lyrics_invalid", message="LRC text or timestamps are invalid"
        ) from error
    return save_document(project_id, document, output_dir, request)


def finalize_capture(
    source: Path, target: Path, token: CancellationToken, maximum_duration: float
) -> dict[str, Any]:
    tools = FFmpegTools.discover()
    # MediaRecorder's streaming WebM may omit duration. Remux the actual streams
    # into a seekable file before probing; audio is required, camera is optional.
    tools._run(
        [
            str(tools.ffmpeg_path),
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-map",
            "0:v:0?",
            "-t",
            str(maximum_duration),
            "-c",
            "copy",
            "-f",
            "webm",
            str(target),
        ],
        token=token,
        timeout_seconds=120,
        failure_code="recording_invalid",
        failure_message="The capture does not contain valid recording streams",
    )
    metadata = asdict(tools.probe(target, cancellation=token))
    raw = tools._run(
        [
            str(tools.ffprobe_path),
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(target),
        ],
        token=token,
        timeout_seconds=30,
        failure_code="recording_invalid",
        failure_message="The recording streams could not be checked",
    )
    metadata["has_video"] = any(
        s.get("codec_type") == "video" for s in json.loads(raw).get("streams", [])
    )
    return metadata


def recording_ranges(
    encoded: str, *, start: float, end: float, duration: float
) -> list[dict[str, float]]:
    """Validate a capture's source-time queue before accepting its bytes."""
    try:
        if len(encoded.encode("utf-8")) > MAX_SELECTION_QUERY_BYTES:
            raise ValueError("selection query too large")
        ranges = validate_recording_ranges(json.loads(encoded), duration)
        if ranges[0]["start"] != start or ranges[-1]["end"] != end:
            raise ValueError("selection bounds do not match ranges")
        return ranges
    except (ValueError, TypeError, OverflowError, RecursionError) as error:
        raise LocalEngineError(
            status_code=422,
            code="selection_invalid",
            message="Recording ranges must be ordered, nonoverlapping, and within the song",
        ) from error


@outputs_router.post("/projects/{project_id}/recordings", status_code=201)
async def save_recording(
    project_id: UUID,
    request: Request,
    start: float,
    end: float,
    ranges: str | None = None,
    lead_in: float = 0,
    mix: str | None = None,
) -> dict[str, Any]:
    _, output_dir, duration = await run_in_threadpool(project_context, project_id, request)
    if not (
        math.isfinite(start)
        and math.isfinite(end)
        and 0 <= start < end <= duration + 0.1
        and math.isfinite(lead_in)
        and 0 <= lead_in <= 3
        and (lead_in == 0 or start == 0)
        and (mix is None or lead_in == 0)
    ):
        raise LocalEngineError(
            status_code=422,
            code="selection_invalid",
            message="Recording selection is outside the source audio",
        )
    selection_ranges = (
        recording_ranges(
            ranges, start=start, end=end, duration=duration if mix is None else duration + 0.1
        )
        if ranges is not None
        else None
    )
    maximum_duration = (
        math.fsum(item["end"] - item["start"] for item in selection_ranges)
        if selection_ranges is not None
        else end - start
    ) + lead_in
    settings: SavedMixSettings | None = None
    if mix is not None:
        try:
            if len(mix.encode("utf-8")) > 1024:
                raise ValueError("Mix settings too large")
            settings = SavedMixSettings.model_validate_json(mix)
            if selection_ranges is None:
                selection_ranges = [{"start": start, "end": end}]
            maximum_duration += settings.preparation_seconds * len(selection_ranges)
            if maximum_duration > MAX_MIX_SECONDS:
                raise ValueError("Recording plan exceeds duration limit")
        except ValueError as error:
            raise LocalEngineError(
                status_code=422, code="recording_mix_invalid", message="录制混音参数无效。"
            ) from error
    mime = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if mime not in {"audio/webm", "video/webm"}:
        raise LocalEngineError(
            status_code=415, code="recording_format_invalid", message="The recording must be WebM"
        )
    length = request.headers.get("content-length")
    try:
        expected_bytes = int(length) if length else 0
        if expected_bytes < 0:
            raise ValueError
    except ValueError as error:
        raise LocalEngineError(
            status_code=422, code="recording_size_invalid", message="Invalid capture size"
        ) from error
    if expected_bytes > MAX_RECORDING_BYTES:
        raise LocalEngineError(
            status_code=413, code="recording_too_large", message="The capture exceeds 512 MiB"
        )
    repository: JobRepository = request.app.state.job_repository
    job, claimed = start_output_job(project_id, "recording", request)
    directory = output_dir / "recordings" / str(uuid4())
    temporary, target = directory / "capture.part.webm", directory / "recording.webm"
    token = CancellationToken()
    request.app.state.job_runner.register_external(job.id, claimed.stage_id, token)
    complete = False
    try:
        directory.mkdir(parents=True, exist_ok=False)
        size = 0
        with temporary.open("xb") as handle:
            async for chunk in request.stream():
                token.checkpoint()
                size += len(chunk)
                if size > MAX_RECORDING_BYTES:
                    raise LocalEngineError(
                        status_code=413,
                        code="recording_too_large",
                        message="The capture exceeds 512 MiB",
                    )
                handle.write(chunk)
        # MediaRecorder can capture frames before it dispatches the start event.
        # That event starts the guide and lyric clock. Preserve the tail of the
        # guide without removing the existing bound on an uploaded take.
        capture_limit = maximum_duration
        if settings is not None:
            capture_limit += CAPTURE_STARTUP_ALLOWANCE_SECONDS
        metadata = await run_in_threadpool(
            finalize_capture, temporary, target, token, capture_limit
        )
        metadata.update({"selection_start": start, "selection_end": end})
        if lead_in:
            metadata["preparation_silence_seconds"] = lead_in
        if selection_ranges is not None:
            metadata["selection_ranges"] = selection_ranges
        if settings is not None:
            metadata["mix"] = settings.model_dump(by_alias=True)
        mime = "video/webm" if metadata["has_video"] else "audio/webm"
        artifact = await run_in_threadpool(
            file_artifact, target, "recording", mime, metadata, token
        )
        repository.complete_stage(job.id, claimed.stage_id, result=metadata, artifacts=[artifact])
        complete = True
        return published_outputs(job.id, request)[0]
    except Exception as error:
        finish_failed_save(job.id, claimed.stage_id, request, "recording_invalid")
        if isinstance(error, JobCanceled):
            raise LocalEngineError(
                status_code=409, code="recording_canceled", message="Recording save was canceled"
            ) from error
        if isinstance(error, LocalEngineError):
            raise
        raise LocalEngineError(
            status_code=422,
            code="recording_invalid",
            message="Recording could not be validated or saved",
        ) from error
    finally:
        request.app.state.job_runner.unregister_external(job.id)
        if not complete:
            finish_failed_save(job.id, claimed.stage_id, request, "recording_save_interrupted")
        temporary.unlink(missing_ok=True)
        if not complete:
            target.unlink(missing_ok=True)
