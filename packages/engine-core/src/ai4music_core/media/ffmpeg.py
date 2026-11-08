"""Local, cancellable FFmpeg operations that never modify source media."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import Any

from ai4music_core.media.hashing import CancellationToken

_MAX_OUTPUT_BYTES = 1024 * 1024
_MAX_LINE_BYTES = 64 * 1024


@dataclass(frozen=True)
class MediaInfo:
    duration_seconds: float
    sample_rate: int
    channels: int
    codec: str
    container: str


class MediaToolError(Exception):
    """A stable error code and user-safe text, without source paths or tool logs."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class FFmpegTools:
    ffmpeg_path: Path
    ffprobe_path: Path

    @classmethod
    def discover(cls, binary_dir: Path | None = None) -> FFmpegTools:
        suffix = ".exe" if os.name == "nt" else ""
        candidates: list[Path] = []
        if binary_dir is not None:
            candidates.append(binary_dir)
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle is not None:
            root = Path(bundle)
            candidates.extend([root / "bin", root / "ffmpeg" / "bin", root / "ffmpeg", root])
        for directory in candidates:
            ffmpeg = directory / f"ffmpeg{suffix}"
            ffprobe = directory / f"ffprobe{suffix}"
            if all(path.is_file() and os.access(path, os.X_OK) for path in (ffmpeg, ffprobe)):
                return cls(ffmpeg.absolute(), ffprobe.absolute())
        ffmpeg_on_path = shutil.which("ffmpeg")
        ffprobe_on_path = shutil.which("ffprobe")
        if ffmpeg_on_path is not None and ffprobe_on_path is not None:
            return cls(Path(ffmpeg_on_path).absolute(), Path(ffprobe_on_path).absolute())
        raise MediaToolError(
            "media_tools_missing", "FFmpeg and ffprobe are required to process local audio."
        )

    def encode_cloud_audio(
        self, source: Path, destination: Path, *, cancellation: CancellationToken
    ) -> None:
        self._run(
            [
                str(self.ffmpeg_path),
                "-v",
                "error",
                "-nostdin",
                "-n",
                "-i",
                str(source.absolute()),
                "-map",
                "0:a:0",
                "-map_metadata",
                "-1",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-sample_fmt",
                "s16",
                "-c:a",
                "pcm_s16le",
                "-bitexact",
                str(destination.absolute()),
            ],
            token=cancellation,
            timeout_seconds=120,
            failure_code="cloud_audio_encode_failed",
            failure_message="无法准备待上传的人声音频。",
        )

    def probe(self, source: Path, *, cancellation: CancellationToken | None = None) -> MediaInfo:
        token = cancellation or CancellationToken()
        token.checkpoint()
        if not source.is_file():
            raise MediaToolError("media_source_unavailable", "The audio source is unavailable.")
        raw = self._run(
            [
                str(self.ffprobe_path),
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                (
                    "stream=index,codec_type,codec_name,sample_rate,channels,duration:"
                    "format=duration,format_name"
                ),
                "-of",
                "json",
                str(source.absolute()),
            ],
            token=token,
            timeout_seconds=30,
            failure_code="media_invalid",
            failure_message="The file could not be read as valid audio.",
        )
        try:
            document = json.loads(raw)
            if not isinstance(document, dict):
                raise TypeError
            streams = document.get("streams")
            if not isinstance(streams, list):
                raise TypeError
            audio = next(
                (
                    stream
                    for stream in streams
                    if isinstance(stream, dict) and stream.get("codec_type") == "audio"
                ),
                None,
            )
            if audio is None:
                raise MediaToolError("media_no_audio", "The file does not contain an audio stream.")
            container = document.get("format")
            if not isinstance(container, dict):
                raise TypeError
            duration = self._duration(audio.get("duration"), container.get("duration"))
            rate = self._positive_integer(audio.get("sample_rate"))
            channels = self._positive_integer(audio.get("channels"))
            codec = self._name(audio.get("codec_name"))
            format_name = self._name(container.get("format_name"))
        except (ValueError, TypeError, OverflowError):
            raise MediaToolError(
                "media_invalid", "The audio metadata is invalid or incomplete."
            ) from None
        token.checkpoint()
        return MediaInfo(duration, rate, channels, codec, format_name)

    def normalize(
        self,
        source: Path,
        destination: Path,
        *,
        info: MediaInfo,
        cancellation: CancellationToken | None = None,
        on_progress: Callable[[float, float], None] | None = None,
    ) -> MediaInfo:
        token = cancellation or CancellationToken()
        token.checkpoint()
        if not source.is_file():
            raise MediaToolError("media_source_unavailable", "The audio source is unavailable.")
        if not math.isfinite(info.duration_seconds) or info.duration_seconds <= 0:
            raise MediaToolError("media_invalid", "The audio duration is invalid.")
        try:
            if source.resolve() == destination.resolve() or (
                destination.exists() and source.samefile(destination)
            ):
                raise MediaToolError(
                    "media_destination_invalid", "Choose a separate destination for derived audio."
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(
                prefix=f".{destination.stem}-", suffix=".wav", dir=destination.parent
            )
            os.close(descriptor)
        except OSError:
            raise MediaToolError(
                "media_destination_invalid", "The audio output destination is unavailable."
            ) from None
        temporary = Path(name)
        last_progress = -1.0

        def progress(line: str) -> None:
            nonlocal last_progress
            if on_progress is None or not line.startswith("out_time="):
                return
            try:
                hours, minutes, seconds = line.removeprefix("out_time=").split(":")
                completed = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            except ValueError:
                return
            if math.isfinite(completed) and completed >= 0 and completed > last_progress:
                last_progress = completed
                on_progress(completed, info.duration_seconds)

        try:
            self._run(
                [
                    str(self.ffmpeg_path),
                    "-v",
                    "error",
                    "-nostdin",
                    "-y",
                    "-i",
                    str(source.absolute()),
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-sn",
                    "-dn",
                    "-ac",
                    "2",
                    "-ar",
                    "44100",
                    "-c:a",
                    "pcm_f32le",
                    "-f",
                    "wav",
                    "-progress",
                    "pipe:1",
                    "-nostats",
                    str(temporary.absolute()),
                ],
                token=token,
                timeout_seconds=min(86400, max(120, info.duration_seconds * 20)),
                failure_code="media_normalization_failed",
                failure_message="FFmpeg could not normalize the audio.",
                on_line=progress,
            )
            normalized = self.probe(temporary, cancellation=token)
            if (
                normalized.sample_rate != 44100
                or normalized.channels != 2
                or normalized.codec != "pcm_f32le"
                or normalized.container != "wav"
            ):
                raise MediaToolError(
                    "media_normalization_failed", "The normalized audio format is invalid."
                )
            token.checkpoint()
            os.replace(temporary, destination)
            return normalized
        except OSError:
            raise MediaToolError(
                "media_normalization_failed", "The normalized audio could not be saved."
            ) from None
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _positive_integer(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise TypeError
        number = int(value)
        if number <= 0:
            raise ValueError
        return number

    @staticmethod
    def _name(value: Any) -> str:
        if not isinstance(value, str) or not value or len(value) > 128:
            raise ValueError
        return value

    @staticmethod
    def _duration(*values: Any) -> float:
        for value in values:
            try:
                duration = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if not isinstance(value, bool) and math.isfinite(duration) and duration > 0:
                return duration
        raise ValueError

    @staticmethod
    def _run(
        command: list[str],
        *,
        token: CancellationToken,
        timeout_seconds: float,
        failure_code: str,
        failure_message: str,
        on_line: Callable[[str], None] | None = None,
    ) -> bytes:
        token.checkpoint()
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError:
            raise MediaToolError(
                "media_tools_missing", "The local media tools could not be started."
            ) from None
        assert process.stdout is not None
        stream = process.stdout
        lines: Queue[bytes] = Queue(maxsize=128)
        oversized = Event()
        stopped = Event()

        def read_output() -> None:
            count = 0
            try:
                while not stopped.is_set() and (line := stream.readline(_MAX_LINE_BYTES + 1)):
                    count += len(line)
                    if len(line) > _MAX_LINE_BYTES or (
                        on_line is None and count > _MAX_OUTPUT_BYTES
                    ):
                        oversized.set()
                        return
                    while not stopped.is_set():
                        try:
                            lines.put(line, timeout=0.05)
                            break
                        except Full:
                            continue
            except (OSError, ValueError):
                return

        reader = Thread(target=read_output, daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout_seconds
        output: list[bytes] = []
        try:
            while True:
                token.checkpoint()
                if time.monotonic() > deadline:
                    raise MediaToolError("media_timeout", "The local media operation timed out.")
                if oversized.is_set():
                    raise MediaToolError(failure_code, failure_message)
                try:
                    line = lines.get(timeout=0.05)
                except Empty:
                    if process.poll() is not None and not reader.is_alive() and lines.empty():
                        break
                    continue
                if on_line is None:
                    output.append(line)
                else:
                    on_line(line.decode("utf-8", errors="replace").strip())
            token.checkpoint()
            if process.returncode != 0:
                raise MediaToolError(failure_code, failure_message)
            return b"".join(output)
        finally:
            stopped.set()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            reader.join(timeout=1)
            stream.close()
