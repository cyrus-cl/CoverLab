"""Real audio stages; the runner owns scheduling and the repository owns state."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.error import URLError
from uuid import uuid4

from ai4music_core.jobs.repository import ClaimedJob, JobRepository
from ai4music_core.media.ffmpeg import FFmpegTools, MediaInfo, MediaToolError
from ai4music_core.media.hashing import CancellationToken, hash_file
from ai4music_core.separation import DemucsProvider


def original_playback_type(info: MediaInfo) -> str | None:
    containers = info.container.split(",")
    if "wav" in containers and info.codec in {
        "pcm_u8",
        "pcm_s16le",
        "pcm_s24le",
        "pcm_s32le",
        "pcm_f32le",
    }:
        return "audio/wav"
    if "mp3" in containers and info.codec == "mp3":
        return "audio/mpeg"
    if "flac" in containers and info.codec == "flac":
        return "audio/flac"
    if "mp4" in containers and info.codec == "aac":
        return "audio/mp4"
    if "ogg" in containers and info.codec in {"opus", "vorbis"}:
        return "audio/ogg"
    return None


class AudioStageExecutor:
    def __init__(self, repository: JobRepository, *, model_dir: Path) -> None:
        self.repository = repository
        self.provider = DemucsProvider(model_dir)

    def execute(
        self, claimed: ClaimedJob, stage_name: str, cancellation: CancellationToken
    ) -> None:
        cancellation.checkpoint()
        stat = claimed.source_path.stat()
        if stat.st_size != claimed.source_size or stat.st_mtime_ns != claimed.source_mtime_ns:
            raise MediaToolError("source_changed", "Source file changed after project creation")

        def progress(done: float, total: float, unit: str, phase: str | None = None) -> None:
            cancellation.checkpoint()
            self.repository.record_stage_progress(
                claimed.id, claimed.stage_id, completed=done, total=total, unit=unit, phase=phase
            )

        result: dict[str, Any]
        artifacts: list[dict[str, Any]] = []
        if stage_name == "validate_media":
            result = asdict(
                FFmpegTools.discover().probe(claimed.source_path, cancellation=cancellation)
            )
        elif stage_name == "normalize_audio":
            tools = FFmpegTools.discover()
            info = MediaInfo(**self.repository.stage_result(claimed.id, "validate_media"))
            # Every attempt has a unique derived-output path, so a retry never
            # mutates a previously published artifact or the referenced source.
            target = (
                claimed.output_dir
                / "processing"
                / str(claimed.id)
                / str(uuid4())
                / "normalized.wav"
            )
            normalized = tools.normalize(
                claimed.source_path,
                target,
                info=info,
                cancellation=cancellation,
                on_progress=lambda a, b: progress(a, b, "seconds"),
            )
            result = {**asdict(normalized), "path": str(target)}
            artifacts.append(
                self._artifact(target, "normalized_audio", asdict(normalized), cancellation)
            )
        elif stage_name == "prepare_model":
            try:
                result = self.provider.prepare(
                    cancellation,
                    on_event=lambda event: progress(
                        float(event["completed"]),
                        float(event["total"]),
                        "bytes",
                        str(event["phase"]),
                    ),
                )
            except ValueError as error:
                raise MediaToolError(
                    "model_checksum_mismatch", "Model file failed integrity validation"
                ) from error
            except (OSError, URLError) as error:
                raise MediaToolError(
                    "model_download_failed",
                    "Model preparation failed; check the connection and disk",
                ) from error
        elif stage_name == "separate_stems":
            legacy_input = self.repository.optional_stage_result(claimed.id, "normalize_audio")
            target = claimed.output_dir / "processing" / str(claimed.id) / str(uuid4()) / "stems"

            def on_event(event: dict[str, Any]) -> None:
                if event.get("event") == "progress" and event.get("phase") in {
                    "separation",
                    "output_write",
                }:
                    progress(
                        float(event["completed"]),
                        float(event["total"]),
                        str(event.get("unit", "segments")),
                        str(event["phase"]),
                    )

            tools = FFmpegTools.discover()
            if legacy_input is not None:
                # Persisted jobs may still need this file for later analysis stages.
                duration = float(legacy_input["duration_seconds"])
                separated = self.provider.separate(
                    Path(legacy_input["path"]), target, cancellation=cancellation, on_event=on_event
                )
            else:
                info = MediaInfo(**self.repository.stage_result(claimed.id, "validate_media"))
                playback_type = original_playback_type(info)
                with TemporaryDirectory(prefix="coverlab-decode-") as directory:
                    normalized_path = Path(directory) / "normalized.wav"
                    # Keep the decoded file only when the original format needs a playback proxy.
                    if playback_type is None:
                        normalized_path = target.parent / "normalized.wav"
                    normalized = tools.normalize(
                        claimed.source_path, normalized_path, info=info, cancellation=cancellation
                    )
                    duration = normalized.duration_seconds
                    separated = self.provider.separate(
                        normalized_path, target, cancellation=cancellation, on_event=on_event
                    )
                if playback_type is None:
                    artifacts.append(
                        self._artifact(
                            normalized_path, "normalized_audio", asdict(normalized), cancellation
                        )
                    )
                else:
                    stat = claimed.source_path.stat()
                    if (
                        stat.st_size != claimed.source_size
                        or stat.st_mtime_ns != claimed.source_mtime_ns
                    ):
                        raise MediaToolError("source_changed", "Source changed during separation")
                    artifacts.append(
                        {
                            "kind": "normalized_audio",
                            # Artifact row paths are unique, including across jobs
                            # and projects that reference the same source.
                            "path": str(target.parent / "source-reference"),
                            "byte_size": claimed.source_size,
                            "sha256": claimed.source_sha256,
                            "content_type": playback_type,
                            "artifact_metadata": {**asdict(info), "source_reference": True},
                        }
                    )
            result = {
                "device": separated.device,
                "model_id": separated.model_id,
                "revision": separated.revision,
                "model_sha256": separated.model_sha256,
            }
            for kind, path in (
                ("vocals", separated.vocals),
                ("instrumental", separated.instrumental),
            ):
                info = tools.probe(path, cancellation=cancellation)
                if abs(info.duration_seconds - duration) > 0.1:
                    raise MediaToolError(
                        "output_invalid", "Separated audio duration does not match input"
                    )
                artifacts.append(
                    self._artifact(path, kind, {**asdict(info), **result}, cancellation)
                )
        elif stage_name in {
            "extract_waveform",
            "estimate_beats",
            "prepare_lyrics_model",
            "transcribe_lyrics",
        }:
            from ai4music_core.jobs.analysis_processing import execute_analysis_stage

            result, artifacts = execute_analysis_stage(
                self.repository, claimed, stage_name, cancellation, progress
            )
        else:
            raise MediaToolError("stage_unsupported", "Unsupported audio processing stage")
        cancellation.checkpoint()
        self.repository.complete_stage(
            claimed.id, claimed.stage_id, result=result, artifacts=artifacts
        )

    @staticmethod
    def _artifact(
        path: Path, kind: str, metadata: dict[str, Any], cancellation: CancellationToken
    ) -> dict[str, Any]:
        return {
            "kind": kind,
            "path": str(path.resolve()),
            "byte_size": path.stat().st_size,
            "sha256": hash_file(path, cancellation=cancellation),
            "content_type": "audio/wav",
            "artifact_metadata": metadata,
        }
