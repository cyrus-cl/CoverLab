"""Lazy parent adapter for cancellable CPU transcription in a hidden child."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai4music_core.media.hashing import CancellationToken

from .models import DEFAULT_MODEL, SMALL_MODEL, Events, ensure_model
from .timed import render_lrc, render_srt, validate_document


class LyricsError(RuntimeError):
    pass


@dataclass(frozen=True)
class LyricsResult:
    json_path: Path
    lrc_path: Path
    srt_path: Path
    device: str
    model_id: str
    revision: str
    model_sha256: str
    raw_json_path: Path | None = None


def worker_command(arguments: list[str], *, frozen: bool | None = None) -> list[str]:
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    prefix = (
        [sys.executable, "--lyrics-worker"]
        if is_frozen
        else [sys.executable, "-m", "ai4music_core.lyrics.worker"]
    )
    return prefix + arguments


def run_worker(
    command: list[str],
    *,
    cancellation: CancellationToken,
    on_event: Events | None = None,
) -> dict[str, Any]:
    cancellation.checkpoint()
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    messages: queue.Queue[str | None] = queue.Queue()

    def read_events() -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                messages.put(line)
        finally:
            messages.put(None)

    reader = threading.Thread(target=read_events, daemon=True)
    reader.start()
    result = None
    error = "worker_failed: verify the local transcription runtime"
    try:
        while True:
            cancellation.checkpoint()
            try:
                line = messages.get(timeout=0.1)
            except queue.Empty:
                if process.poll() is not None and not reader.is_alive():
                    break
                continue
            if line is None:
                break
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("event") == "error":
                error = str(event.get("code", "worker_failed"))[:150]
            elif event.get("event") == "result":
                result = event
            elif on_event:
                on_event(event)
        cancellation.checkpoint()
        if process.wait(timeout=5) != 0 or result is None:
            raise LyricsError(error)
        return result
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        reader.join(timeout=2)
        if process.stdout:
            process.stdout.close()


class WhisperProvider:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir

    def runtime_info(self) -> dict[str, Any]:
        versions: dict[str, str | None] = {}
        for package in (
            "faster-whisper",
            "ctranslate2",
            "av",
            "tokenizers",
            "torch",
            "transformers",
            "safetensors",
        ):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = None
        models = (DEFAULT_MODEL, SMALL_MODEL)
        return {
            "provider": "qwen3-asr+whisper-alignment",
            "model_id": DEFAULT_MODEL.model_id,
            "revision": DEFAULT_MODEL.revision,
            "model_sha256": DEFAULT_MODEL.sha256,
            "available": all(
                importlib.util.find_spec(name) is not None
                for name in (
                    "faster_whisper",
                    "ctranslate2",
                    "av",
                    "tokenizers",
                    "torch",
                    "transformers",
                    "safetensors",
                )
            ),
            "versions": versions,
            "model_cached": all(
                (self.model_dir / model.model_id / model.revision / item.filename).is_file()
                for model in models
                for item in model.files
            ),
            "download_bytes": sum(item.size_bytes for model in models for item in model.files),
            "alignment_model_id": SMALL_MODEL.model_id,
            "alignment_revision": SMALL_MODEL.revision,
            "alignment_model_sha256": SMALL_MODEL.sha256,
            "device": "cpu",
            "compute_type": "float32 (Qwen), int8 (alignment)",
            "cpu_supported": True,
            "quality_notice": "Machine draft; Qwen recognizes audio in bounded chunks; "
            "Whisper DTW aligns that recognized text. Word probabilities are alignment likelihoods, "
            "not calibrated recognition accuracy. Review lyrics and timing before recording.",
        }

    def prepare(
        self,
        cancellation: CancellationToken,
        *,
        on_event: Events | None = None,
    ) -> dict[str, Any]:
        cancellation.checkpoint()
        if not self.runtime_info()["available"]:
            raise LyricsError("runtime_missing: install the pinned Qwen and Whisper CPU runtime")
        path = ensure_model(self.model_dir, DEFAULT_MODEL, cancellation, on_event=on_event)
        alignment_path = ensure_model(self.model_dir, SMALL_MODEL, cancellation, on_event=on_event)
        return {
            "path": str(path),
            "alignment_path": str(alignment_path),
            "model_id": DEFAULT_MODEL.model_id,
            "revision": DEFAULT_MODEL.revision,
            "model_sha256": DEFAULT_MODEL.sha256,
        }

    def transcribe(
        self,
        source: Path,
        output_dir: Path,
        *,
        cancellation: CancellationToken,
        on_event: Events | None = None,
    ) -> LyricsResult:
        cancellation.checkpoint()
        if output_dir.exists():
            raise LyricsError("output_exists: choose a fresh output directory")
        if not source.is_file():
            raise LyricsError("source_missing: local normalized audio does not exist")
        metadata = self.prepare(cancellation, on_event=on_event)
        result = run_worker(
            worker_command(
                [
                    "--model",
                    str(Path(metadata["path"]).resolve()),
                    "--model-id",
                    DEFAULT_MODEL.model_id,
                    "--alignment-model",
                    str(Path(metadata["alignment_path"]).resolve()),
                    "--source",
                    str(source.resolve()),
                ]
            ),
            cancellation=cancellation,
            on_event=on_event,
        )
        document = validate_document(result.get("document"))
        cancellation.checkpoint()
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = output_dir.parent / f".{output_dir.name}.{uuid.uuid4().hex}.part"
        staging.mkdir()
        try:
            for name, content in (
                (
                    "lyrics.json",
                    json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2),
                ),
                ("lyrics.lrc", render_lrc(document)),
                ("lyrics.srt", render_srt(document)),
                (
                    "lyrics.raw.json",
                    json.dumps(
                        result.get("raw_recognition", document),
                        ensure_ascii=False,
                        allow_nan=False,
                        indent=2,
                    ),
                ),
            ):
                cancellation.checkpoint()
                (staging / name).write_text(content, encoding="utf-8", newline="\n")
            cancellation.checkpoint()
            os.rename(staging, output_dir)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return LyricsResult(
            output_dir / "lyrics.json",
            output_dir / "lyrics.lrc",
            output_dir / "lyrics.srt",
            "cpu",
            DEFAULT_MODEL.model_id,
            DEFAULT_MODEL.revision,
            DEFAULT_MODEL.sha256,
            output_dir / "lyrics.raw.json",
        )
