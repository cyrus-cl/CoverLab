"""Assemble a sample-aligned recording guide without buffering a full track."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

PreparationAudio = Literal["instrumental", "mixed", "vocals"]
MIX_RATE = 44100
MIX_CHANNELS = 2
MIX_FRAME_BYTES = MIX_CHANNELS * 2
MAX_MIX_SECONDS = 30 * 60
_BLOCK_FRAMES = 65536
_PCM_PEAK = 32767 / 32768


@dataclass(frozen=True)
class _Span:
    start: int
    frames: int
    backing_gain: int
    vocal_gain: float


def render_recording_mix(
    instrumental: Path,
    vocals: Path,
    destination: Path,
    *,
    ranges: list[dict[str, float]],
    preparation_seconds: int,
    preparation_audio: PreparationAudio,
    vocal_percent: int,
) -> float:
    import numpy as np
    import soundfile as sf

    preparation_frames = preparation_seconds * MIX_RATE
    spans: list[_Span] = []
    for selected in ranges:
        start = round(selected["start"] * MIX_RATE)
        end = round(selected["end"] * MIX_RATE)
        if end <= start:
            raise ValueError("Selection must contain at least one sample")
        if preparation_frames:
            first = max(0, start - preparation_frames)
            if start < preparation_frames:
                spans.append(_Span(0, preparation_frames - start, 0, 0))
            if first < start:
                spans.append(
                    _Span(
                        first,
                        start - first,
                        int(preparation_audio != "vocals"),
                        float(preparation_audio != "instrumental"),
                    )
                )
        spans.append(_Span(start, end - start, 1, vocal_percent / 100))

    frames = sum(span.frames for span in spans)
    if not 0 < frames <= MAX_MIX_SECONDS * MIX_RATE:
        raise ValueError("Recording mix exceeds duration limit")

    with sf.SoundFile(instrumental) as backing_file, sf.SoundFile(vocals) as vocal_file:
        if (
            backing_file.samplerate != MIX_RATE
            or vocal_file.samplerate != MIX_RATE
            or backing_file.channels != MIX_CHANNELS
            or vocal_file.channels != MIX_CHANNELS
            or backing_file.frames != vocal_file.frames
            or any(
                span.start + span.frames > backing_file.frames
                for span in spans
                if span.backing_gain or span.vocal_gain
            )
        ):
            raise ValueError("Stem formats or lengths do not match the selection")

        def blocks() -> Iterator[NDArray[np.float64]]:
            for span in spans:
                if span.backing_gain:
                    backing_file.seek(span.start)
                if span.vocal_gain:
                    vocal_file.seek(span.start)
                for offset in range(0, span.frames, _BLOCK_FRAMES):
                    count = min(_BLOCK_FRAMES, span.frames - offset)
                    if span.backing_gain:
                        output = backing_file.read(count, dtype="float64", always_2d=True)
                    else:
                        output = np.zeros((count, MIX_CHANNELS), dtype=np.float64)
                    if span.vocal_gain:
                        voice = vocal_file.read(count, dtype="float64", always_2d=True)
                        if len(voice) != count:
                            raise ValueError("Vocal stem has missing samples")
                        output += voice * span.vocal_gain
                    if len(output) != count or not np.isfinite(output).all():
                        raise ValueError("Recording mix contains missing or non-finite samples")
                    yield output

        # Apply the same attenuation to the entire guide only when needed to
        # keep PCM within range. This keeps source and inter-section gains intact.
        peak = max(float(np.max(np.abs(block))) for block in blocks())
        scale = min(1.0, _PCM_PEAK / peak) if peak else 1.0
        with sf.SoundFile(destination, "w", MIX_RATE, MIX_CHANNELS, "PCM_16") as output_file:
            for block in blocks():
                if scale < 1.0:
                    block *= scale
                output_file.write(block)
    return frames / MIX_RATE
