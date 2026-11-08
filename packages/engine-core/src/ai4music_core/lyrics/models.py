"""Atomic cache of the revision-pinned official multilingual converted model."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from ai4music_core.media.hashing import CancellationToken, hash_file

Events = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class ModelFile:
    filename: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    revision: str
    base_url: str
    files: tuple[ModelFile, ...]

    @property
    def sha256(self) -> str:
        """Manifest digest; each actual model file is checked independently."""
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


TINY_MODEL = ModelSpec(
    "faster-whisper-tiny",
    "d90ca5fe260221311c53c58e660288d3deb8d356",
    "https://huggingface.co/Systran/faster-whisper-tiny/resolve/"
    "d90ca5fe260221311c53c58e660288d3deb8d356",
    (
        ModelFile(
            "model.bin",
            "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1",
            75538270,
        ),
        ModelFile(
            "config.json", "a73a28cdfe1c43ccc7202fa333d1f89c202477271407ae9a7f19afa52039cac8", 2249
        ),
        ModelFile(
            "tokenizer.json",
            "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
            2203239,
        ),
        ModelFile(
            "vocabulary.txt",
            "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
            459861,
        ),
    ),
)

# The small multilingual checkpoint improves the measured full-song draft;
# tiny remains a curated pin for reproducible comparisons and old-cache audits.
SMALL_MODEL = ModelSpec(
    "faster-whisper-small",
    "536b0662742c02347bc0e980a01041f333bce120",
    "https://huggingface.co/Systran/faster-whisper-small/resolve/"
    "536b0662742c02347bc0e980a01041f333bce120",
    (
        ModelFile(
            "model.bin",
            "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
            483546902,
        ),
        ModelFile(
            "config.json", "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828", 2370
        ),
        ModelFile(
            "tokenizer.json",
            "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
            2203239,
        ),
        ModelFile(
            "vocabulary.txt",
            "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
            459861,
        ),
    ),
)
QWEN_MODEL = ModelSpec(
    "qwen3-asr-0.6b-hf",
    "7f1569a48a89f3e3f4dc3a5c9d28bddd903bc76c",
    "https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf/resolve/"
    "7f1569a48a89f3e3f4dc3a5c9d28bddd903bc76c",
    (
        ModelFile(
            "chat_template.jinja",
            "f50e6b694fbf4a683206e37869990d68333fe95d285730f084c838a34b0d98c2",
            1434,
        ),
        ModelFile(
            "config.json", "9eecf6f1b383e343889c2e6010e632590fa57d4bc678e151c7d6a160a0dfb04a", 2398
        ),
        ModelFile(
            "generation_config.json",
            "9939fc9388b79bd70757f938b87381e817173d6a6158f5af6506c0b73e775c3c",
            165,
        ),
        ModelFile(
            "model.safetensors",
            "d3f212dd20abecd315d830bc54ae3865e56ebfc3276484e57b771288ba27fd35",
            1564928088,
        ),
        ModelFile(
            "processor_config.json",
            "bc0b230081b44e629dd5b9045b78495615c1831b4b9f4cffe97bd37e82a6156a",
            487,
        ),
        ModelFile(
            "tokenizer.json",
            "fe1fad59be22a41ee293363fcf95fdedbc7c93f3b49270b1d2e18bd1399a7a05",
            11429653,
        ),
        ModelFile(
            "tokenizer_config.json",
            "945e980986de2ca7768f3326bfdbb4fbea3406f972b8ae0be233089f2b253c11",
            998,
        ),
    ),
)
DEFAULT_MODEL = QWEN_MODEL
SUPPORTED_MODELS = {model.model_id: model for model in (TINY_MODEL, SMALL_MODEL, QWEN_MODEL)}


def verify_model(
    path: Path,
    spec: ModelSpec,
    cancellation: CancellationToken,
    *,
    on_event: Events | None = None,
) -> None:
    for item in spec.files:
        cancellation.checkpoint()
        candidate = path / item.filename
        if not candidate.is_file() or candidate.stat().st_size != item.size_bytes:
            raise ValueError(
                f"model_checksum_mismatch: {item.filename}; remove invalid cache and retry"
            )

        def progress(completed: int, total: int, filename: str = item.filename) -> None:
            if on_event:
                on_event(
                    {
                        "event": "progress",
                        "phase": "model_verify",
                        "file": filename,
                        "completed": completed,
                        "total": total,
                        "unit": "bytes",
                    }
                )

        if hash_file(candidate, cancellation=cancellation, on_progress=progress) != item.sha256:
            raise ValueError(
                f"model_checksum_mismatch: {item.filename}; remove invalid cache and retry"
            )


def ensure_model(
    model_dir: Path,
    spec: ModelSpec,
    cancellation: CancellationToken,
    *,
    on_event: Events | None = None,
) -> Path:
    cancellation.checkpoint()
    target = model_dir / spec.model_id / spec.revision
    if target.exists():
        verify_model(target, spec, cancellation, on_event=on_event)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{spec.revision}.{uuid.uuid4().hex}.part"
    staging.mkdir()
    try:
        for item in spec.files:
            cancellation.checkpoint()
            completed = 0
            with (staging / item.filename).open("wb") as output:
                for attempt in range(8):
                    cancellation.checkpoint()
                    url = f"{spec.base_url}/{item.filename}"
                    request: str | Request = url
                    if completed:
                        request = Request(url, headers={"Range": f"bytes={completed}-"})
                    try:
                        with urlopen(request, timeout=5) as source:
                            if completed:
                                bounds = re.fullmatch(
                                    r"bytes (\d+)-(\d+)/(\d+)",
                                    source.headers.get("Content-Range", ""),
                                )
                                if (
                                    getattr(source, "status", None) != 206
                                    or bounds is None
                                    or int(bounds[1]) != completed
                                    or not completed <= int(bounds[2]) < item.size_bytes
                                    or int(bounds[3]) != item.size_bytes
                                ):
                                    raise ValueError(
                                        f"model_download_range_invalid: {item.filename}"
                                    )
                            while True:
                                cancellation.checkpoint()
                                block = source.read(1024 * 1024)
                                if not block:
                                    break
                                completed += len(block)
                                if completed > item.size_bytes:
                                    raise ValueError(f"model_checksum_mismatch: {item.filename}")
                                output.write(block)
                                if on_event:
                                    on_event(
                                        {
                                            "event": "progress",
                                            "phase": "model_download",
                                            "file": item.filename,
                                            "completed": completed,
                                            "total": item.size_bytes,
                                            "unit": "bytes",
                                        }
                                    )
                    except OSError:
                        cancellation.checkpoint()
                        if attempt == 7:
                            raise
                    if completed == item.size_bytes:
                        break
                else:
                    raise OSError(f"model_download_incomplete: {item.filename}")
        verify_model(staging, spec, cancellation, on_event=on_event)
        (staging / "manifest.json").write_text(json.dumps(asdict(spec), indent=2), encoding="utf-8")
        cancellation.checkpoint()
        os.rename(staging, target)
        return target
    finally:
        if staging.exists():
            shutil.rmtree(staging)
