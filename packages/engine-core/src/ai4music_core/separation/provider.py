"""A lightweight parent-side adapter; ML imports stay in the child process."""

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

from .models import DEFAULT_MODEL, Events, Progress, ensure_model


class SeparationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SeparationResult:
    vocals: Path
    instrumental: Path
    device: str
    model_id: str
    revision: str
    model_sha256: str


def worker_command(arguments: list[str], *, frozen: bool | None = None) -> list[str]:
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    prefix = (
        [sys.executable, "--separation-worker"]
        if is_frozen
        else [sys.executable, "-m", "ai4music_core.separation.worker"]
    )
    return prefix + arguments


def run_worker(
    command: list[str],
    *,
    cancellation: CancellationToken,
    on_progress: Progress | None = None,
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
    result: dict[str, Any] | None = None
    error: str | None = None
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
                error = str(event.get("code", "worker_failed"))[:100]
            elif event.get("event") == "result":
                result = event
            else:
                if on_event:
                    on_event(event)
                if on_progress and event.get("event") == "progress":
                    on_progress(int(event["completed"]), int(event["total"]))
        cancellation.checkpoint()
        if process.wait(timeout=5) != 0 or result is None:
            raise SeparationError(error or "worker_failed: check the local separation runtime")
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


class DemucsProvider:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir

    def runtime_info(self) -> dict[str, Any]:
        versions: dict[str, str | None] = {}
        for package in ("demucs", "torch", "torchaudio", "soundfile"):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = None
        # Module discovery works without importing torch, including frozen
        # modules that do not retain distribution metadata.
        available = all(importlib.util.find_spec(package) is not None for package in versions)
        return {
            "provider": "demucs",
            "model_id": DEFAULT_MODEL.model_id,
            "revision": DEFAULT_MODEL.revision,
            "model_sha256": DEFAULT_MODEL.sha256,
            "available": available,
            "versions": versions,
            "model_cached": (self.model_dir / DEFAULT_MODEL.filename).is_file(),
            "device": "auto",
            "cpu_supported": True,
            "device_policy": "CUDA when torch.cuda.is_available(), otherwise CPU",
        }

    def prepare(
        self,
        cancellation: CancellationToken,
        on_progress: Progress | None = None,
        *,
        on_event: Events | None = None,
    ) -> dict[str, Any]:
        if not self.runtime_info()["available"]:
            raise SeparationError("runtime_missing: install the pinned Demucs CPU runtime")
        path = ensure_model(
            self.model_dir, DEFAULT_MODEL, cancellation, on_progress=on_progress, on_event=on_event
        )
        return {
            "model_id": DEFAULT_MODEL.model_id,
            "revision": DEFAULT_MODEL.revision,
            "model_sha256": DEFAULT_MODEL.sha256,
            "path": str(path),
        }

    def separate(
        self,
        source: Path,
        output_dir: Path,
        *,
        cancellation: CancellationToken,
        on_progress: Progress | None = None,
        on_event: Events | None = None,
    ) -> SeparationResult:
        cancellation.checkpoint()
        if not source.is_file():
            raise SeparationError("source_missing: normalized WAV does not exist")
        metadata = self.prepare(cancellation, on_event=on_event)
        # One directory rename publishes both completed stems together. Callers
        # should give each processing attempt its own output directory.
        if output_dir.exists():
            raise SeparationError("output_exists: choose a fresh output directory")
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = output_dir.parent / f".{output_dir.name}.{uuid.uuid4().hex}.part"
        staging.mkdir()
        try:
            result = run_worker(
                worker_command(
                    [
                        "--model",
                        str(Path(metadata["path"]).resolve()),
                        "--source",
                        str(source.resolve()),
                        "--output",
                        str(staging.resolve()),
                    ]
                ),
                cancellation=cancellation,
                on_progress=on_progress,
                on_event=on_event,
            )
            cancellation.checkpoint()
            for name in ("vocals.wav", "instrumental.wav"):
                if not (staging / name).is_file() or (staging / name).stat().st_size <= 44:
                    raise SeparationError("output_invalid: worker did not produce complete WAVs")
            os.replace(staging, output_dir)
            return SeparationResult(
                output_dir / "vocals.wav",
                output_dir / "instrumental.wav",
                str(result["device"]),
                DEFAULT_MODEL.model_id,
                DEFAULT_MODEL.revision,
                DEFAULT_MODEL.sha256,
            )
        finally:
            if staging.exists():
                shutil.rmtree(staging)
