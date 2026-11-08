"""Isolated inference entry point, with an NDJSON-only stdout protocol.

Python: python -m ai4music_core.separation.worker ...
Frozen launcher: engine.exe --separation-worker ... -> main(arguments)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, TextIO

from ai4music_core.media.hashing import hash_file

from .models import DEFAULT_MODEL

_BLOCK_FRAMES = 65536


class WorkerInputError(Exception):
    pass


def infer(model_path: Path, source: Path, output: Path, protocol: TextIO) -> None:
    def emit(event: str, **fields: Any) -> None:
        protocol.write(json.dumps({"event": event, **fields}) + "\n")
        protocol.flush()

    # Never unpickle a checkpoint until the fixed full digest is verified.
    emit("phase", phase="model_verify")
    if hash_file(model_path) != DEFAULT_MODEL.sha256:
        raise WorkerInputError("model_checksum_mismatch")
    emit("phase", phase="runtime_load")

    import numpy as np
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.states import load_model

    torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = load_model(model_path)  # type: ignore[no-untyped-call]
    model.eval()

    emit("phase", phase="audio_load", device=device)
    # Read bounded blocks straight into the channel-first layout used by Demucs.
    with sf.SoundFile(source) as audio:
        samplerate, channels, frames = audio.samplerate, audio.channels, audio.frames
        if samplerate != model.samplerate or channels != model.audio_channels:
            raise WorkerInputError("normalized_audio_required: use 44100 Hz stereo WAV")
        if not frames:
            raise WorkerInputError("audio_invalid: empty input")
        waveform = torch.empty((channels, frames), dtype=torch.float32)
        samples = waveform.numpy()
        offset = 0
        for block in audio.blocks(blocksize=_BLOCK_FRAMES, dtype="float32", always_2d=True):
            if not np.isfinite(block).all():
                raise WorkerInputError("audio_invalid: nonfinite input")
            samples[:, offset : offset + len(block)] = block.T
            offset += len(block)
        if offset != frames:
            raise WorkerInputError("audio_invalid: incomplete input")
        del samples
    reference = waveform.mean(0)
    mean = reference.mean()
    std = reference.std().clamp_min(1e-8)
    del reference
    waveform.sub_(mean).div_(std)

    # shifts=0 gives deterministic single-pass inference. The hook fires only
    # after a real model forward finishes; it never advances on elapsed time.
    stride = int((1 - 0.25) * int(model.samplerate * float(model.segment)))
    total = math.ceil(frames / stride)
    completed = 0

    def finished_segment(_model: Any, _inputs: Any, _output: Any) -> None:
        nonlocal completed
        completed += 1
        emit(
            "progress",
            phase="separation",
            completed=completed,
            total=total,
            unit="segments",
            device=device,
        )

    hook = model.register_forward_hook(finished_segment)
    emit("progress", phase="separation", completed=0, total=total, unit="segments", device=device)
    try:
        with torch.inference_mode():
            estimates = apply_model(
                model,
                waveform[None],
                shifts=0,
                split=True,
                overlap=0.25,
                progress=False,
                device=device,
                num_workers=0,
            )[0]
    finally:
        hook.remove()
    del waveform
    with torch.inference_mode():
        estimates.mul_(std).add_(mean)
    vocals_index = list(model.sources).index("vocals")
    instrumental_indices = [i for i in range(len(model.sources)) if i != vocals_index]
    if tuple(estimates.shape[1:]) != (channels, frames):
        raise WorkerInputError("output_invalid: incorrect duration or channels")
    emit("phase", phase="output_write", device=device)
    for name in ("vocals", "instrumental"):
        # Keep float WAV and sum the same model stems within each write block.
        with sf.SoundFile(output / f"{name}.wav", "w", samplerate, channels, "FLOAT") as target:
            for first in range(0, frames, _BLOCK_FRAMES):
                last = first + _BLOCK_FRAMES
                if name == "vocals":
                    stem = estimates[vocals_index, :, first:last]
                else:
                    stem = estimates[instrumental_indices, :, first:last].sum(0)
                block = stem.cpu().numpy().T
                if not np.isfinite(block).all():
                    raise WorkerInputError("output_invalid: nonfinite samples")
                target.write(block)
        emit(
            "progress",
            phase="output_write",
            completed=1 if name == "vocals" else 2,
            total=2,
            unit="files",
            device=device,
        )
    emit(
        "result",
        device=device,
        model_id=DEFAULT_MODEL.model_id,
        revision=DEFAULT_MODEL.revision,
        model_sha256=DEFAULT_MODEL.sha256,
        samplerate=samplerate,
        channels=channels,
        frames=frames,
    )


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI4Music local Demucs inference worker")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(arguments)
    protocol = sys.stdout
    try:
        # Third-party informational messages must not corrupt the protocol.
        with contextlib.redirect_stdout(sys.stderr):
            infer(args.model, args.source, args.output, protocol)
        return 0
    except WorkerInputError as exc:
        code = str(exc)
    except (ImportError, OSError):
        code = "runtime_unavailable: verify the local Demucs runtime and audio files"
    except Exception:  # noqa: BLE001 - subprocess boundary emits a safe, bounded error code
        code = "inference_failed: the local Demucs worker could not complete separation"
    protocol.write(json.dumps({"event": "error", "code": code}) + "\n")
    protocol.flush()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
