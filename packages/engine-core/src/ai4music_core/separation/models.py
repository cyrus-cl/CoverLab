"""Verified, atomic cache for the fixed official Demucs checkpoint."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from ai4music_core.media.hashing import CancellationToken, hash_file

Progress = Callable[[int, int], None]
Events = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    revision: str
    filename: str
    url: str
    sha256: str
    official_hash_prefix: str


# Full digest measured from the official URL, then checked against upstream's
# filename checksum. See model-registry/demucs-htdemucs.json for provenance.
DEFAULT_MODEL = ModelSpec(
    "htdemucs",
    "955717e8",
    "955717e8-8726e21a.th",
    "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/955717e8-8726e21a.th",
    "8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4",
    "8726e21a",
)


def ensure_model(
    model_dir: Path,
    spec: ModelSpec,
    cancellation: CancellationToken,
    *,
    on_progress: Progress | None = None,
    on_event: Events | None = None,
) -> Path:
    cancellation.checkpoint()
    model_dir.mkdir(parents=True, exist_ok=True)
    target = model_dir / spec.filename
    temporary = model_dir / f"{spec.filename}.{uuid.uuid4().hex}.part"

    def emit(phase: str, completed: int, total: int) -> None:
        if on_progress:
            on_progress(completed, total)
        if on_event:
            on_event(
                {
                    "event": "progress",
                    "phase": phase,
                    "completed": completed,
                    "total": total,
                    "unit": "bytes",
                }
            )

    try:
        if not target.exists():
            # Socket timeout also bounds cancellation while awaiting a network read.
            with urlopen(spec.url, timeout=5) as response, temporary.open("wb") as output:
                total = int(response.headers.get("Content-Length", 0))
                completed = 0
                emit("model_download", 0, total)
                while True:
                    cancellation.checkpoint()
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    completed += len(block)
                    emit("model_download", completed, total)
            candidate = temporary
        else:
            candidate = target
        digest = hash_file(
            candidate,
            cancellation=cancellation,
            on_progress=lambda a, b: emit("model_verify", a, b),
        )
        if digest != spec.sha256 or not digest.startswith(spec.official_hash_prefix):
            raise ValueError("model_checksum_mismatch: remove the invalid cached model and retry")
        cancellation.checkpoint()
        if candidate == temporary:
            os.replace(temporary, target)
        receipt = model_dir / f"manifest.{uuid.uuid4().hex}.part"
        try:
            receipt.write_text(json.dumps(asdict(spec), indent=2), encoding="utf-8")
            os.replace(receipt, model_dir / "manifest.json")
        finally:
            receipt.unlink(missing_ok=True)
        return target
    finally:
        temporary.unlink(missing_ok=True)
