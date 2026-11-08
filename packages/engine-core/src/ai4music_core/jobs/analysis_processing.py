"""Measured waveform/rhythm and opt-in cloud transcription stages."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from ai4music_core.jobs.repository import ClaimedJob, JobRepository
from ai4music_core.lyrics.bailian import BailianProvider
from ai4music_core.media.ffmpeg import MediaToolError
from ai4music_core.media.hashing import CancellationToken, hash_file

Progress = Callable[[float, float, str, str | None], None]


def file_artifact(
    path: Path,
    kind: str,
    content_type: str,
    metadata: dict[str, Any],
    cancellation: CancellationToken,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": str(path.resolve()),
        "byte_size": path.stat().st_size,
        "sha256": hash_file(path, cancellation=cancellation),
        "content_type": content_type,
        "artifact_metadata": metadata,
    }


def write_json(path: Path, document: dict[str, Any], cancellation: CancellationToken) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    try:
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )
        cancellation.checkpoint()
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def execute_analysis_stage(
    repository: JobRepository,
    claimed: ClaimedJob,
    stage_name: str,
    cancellation: CancellationToken,
    progress: Progress,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if (
        stage_name in {"prepare_lyrics_model", "transcribe_lyrics"}
        and not claimed.allow_cloud_upload
    ):
        raise MediaToolError("cloud_consent_required", "请新建歌词任务并确认发送分离人声。")
    if stage_name == "prepare_lyrics_model":
        return BailianProvider().prepare(cancellation), []

    job_dir = claimed.output_dir / "processing" / str(claimed.id)
    target_dir = job_dir / str(uuid4())
    if stage_name in {"extract_waveform", "estimate_beats"}:
        from ai4music_core.analysis.features import analyze_waveform, estimate_beats

        normalized = repository.stage_result(claimed.id, "normalize_audio")
        source = Path(normalized["path"])
        metadata = {"duration_seconds": normalized["duration_seconds"]}
        function = analyze_waveform if stage_name == "extract_waveform" else estimate_beats
        kind = "waveform" if stage_name == "extract_waveform" else "beats"
        document = function(
            source,
            cancellation=cancellation,
            on_progress=lambda done, total: progress(
                done / normalized["sample_rate"], total / normalized["sample_rate"], "seconds", kind
            ),
        )
        target = target_dir / f"{kind}.json"
        write_json(target, document, cancellation)
        return document if kind == "beats" else {"points": len(document["peaks"])}, [
            file_artifact(target, kind, "application/json", metadata, cancellation)
        ]
    if stage_name == "transcribe_lyrics":
        vocals = repository.current_job_artifact(claimed.id, "vocals")
        if vocals is None and claimed.operation != "singing":
            vocals = repository.latest_artifact(claimed.project_id, "vocals")
        if vocals is None or not Path(vocals["path"]).is_file():
            raise MediaToolError("vocal_output_missing", "请先生成可用的分离人声。")
        source = Path(vocals["path"])
        if hash_file(source, cancellation=cancellation) != vocals["sha256"]:
            raise MediaToolError("output_changed", "分离人声已变更，请重新分离。")
        duration = float(vocals["metadata"]["duration_seconds"])
        metadata = {"duration_seconds": duration}

        def on_event(event: dict[str, Any]) -> None:
            if "completed" in event and "total" in event:
                progress(
                    float(event["completed"]),
                    float(event["total"]),
                    str(event.get("unit", "seconds")),
                    str(event.get("phase", "transcription")),
                )

        output = BailianProvider().transcribe(
            source,
            target_dir,
            duration=duration,
            task_state=job_dir / "bailian-task.txt",
            cancellation=cancellation,
            on_event=on_event,
        )
        metadata.update(
            {
                "model_id": output.model_id,
                "revision": output.revision,
                "device": output.device,
                "input_kind": "vocals",
                "machine_draft": True,
            }
        )
        outputs = [
            (output.json_path, "lyrics", "application/json"),
            (output.lrc_path, "lyrics_lrc", "text/plain; charset=utf-8"),
            (output.srt_path, "lyrics_srt", "application/x-subrip; charset=utf-8"),
        ]
        if output.raw_json_path is not None:
            outputs.append((output.raw_json_path, "lyrics_raw", "application/json"))
        return metadata, [
            file_artifact(path, kind, mime, metadata, cancellation) for path, kind, mime in outputs
        ]
    raise MediaToolError("stage_unsupported", "Unsupported analysis stage")
