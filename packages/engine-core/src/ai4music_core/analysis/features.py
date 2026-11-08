from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai4music_core.media.hashing import CancellationToken

ProgressCallback = Callable[[float, float], None]
_BLOCK_FRAMES = 65_536
_WINDOW_SECONDS = 30
_OVERLAP_SECONDS = 4


def analyze_waveform(
    path: Path,
    *,
    cancellation: CancellationToken,
    on_progress: ProgressCallback | None = None,
    max_points: int = 1600,
) -> dict[str, Any]:
    """Return equal-duration signed extrema, pooling channels without phase cancellation.

    Amplitudes retain their original digital full-scale level, clipped to [-1, 1].
    Progress counts decoded audio frames, and working audio memory is one block.
    """
    if max_points < 1:
        raise ValueError("max_points must be positive")
    cancellation.checkpoint()
    import numpy as np
    import soundfile as sf

    with sf.SoundFile(path) as source:
        frames = int(source.frames)
        points = min(max_points, frames)
        result: dict[str, Any] = {
            "schemaVersion": 1,
            "durationSeconds": frames / source.samplerate,
            "sampleRate": int(source.samplerate),
            "channels": int(source.channels),
            "peaks": [],
        }
        lows = np.full(points, np.inf)
        highs = np.full(points, -np.inf)
        position = 0
        while position < frames:
            cancellation.checkpoint()
            samples = source.read(_BLOCK_FRAMES, dtype="float32", always_2d=True)
            if not np.isfinite(samples).all():
                raise ValueError("Audio samples must be finite")
            if len(samples) == 0:
                raise ValueError("Audio ended before the declared frame count")
            samples = np.clip(samples, -1.0, 1.0)
            bins = (np.arange(position, position + len(samples), dtype=np.int64) * points) // frames
            np.minimum.at(lows, bins, samples.min(axis=1))
            np.maximum.at(highs, bins, samples.max(axis=1))
            position += len(samples)
            if on_progress is not None:
                on_progress(float(position), float(frames))
        cancellation.checkpoint()
        if frames == 0 and on_progress is not None:
            on_progress(0.0, 0.0)
        cancellation.checkpoint()
        result["peaks"] = np.column_stack((lows, highs)).tolist()
        return result


@dataclass
class _Estimate:
    bpm: float
    confidence: float
    beats: list[float]


def _novelty_blocks(
    source: Any,
    *,
    cancellation: CancellationToken,
    on_progress: ProgressCallback | None,
    hop: int,
) -> Iterator[tuple[Any, Any]]:
    """Yield a low-rate spectral-flux envelope without keeping decoded audio."""
    import numpy as np

    sample_rate = int(source.samplerate)
    fft_size = 1 << max(6, round(sample_rate * 0.046).bit_length() - 1)
    window = np.hanning(fft_size)
    pending = np.zeros(0, dtype=np.float32)
    previous = np.zeros(fft_size // 2 + 1)
    position = 0
    decoded = 0
    frames = int(source.frames)
    while decoded < frames:
        cancellation.checkpoint()
        block = source.read(_BLOCK_FRAMES, dtype="float32", always_2d=True)
        if not np.isfinite(block).all():
            raise ValueError("Audio samples must be finite")
        if len(block) == 0:
            raise ValueError("Audio ended before the declared frame count")
        # Rectified channel pooling preserves transients in opposite-polarity stereo.
        mono = np.mean(np.abs(np.clip(block, -1.0, 1.0)), axis=1)
        samples = np.concatenate((pending, mono))
        count = max(0, 1 + (len(samples) - fft_size) // hop)
        if count:
            framed = np.lib.stride_tricks.sliding_window_view(samples, fft_size)[::hop][:count]
            magnitude = np.log1p(np.abs(np.fft.rfft(framed * window, axis=1)))
            differences = np.diff(magnitude, axis=0, prepend=previous[None, :])
            flux = np.maximum(differences[:, 1:], 0).mean(axis=1)
            energy = magnitude[:, 1:].mean(axis=1)
            # Suppress numerical/window leakage from unchanging sustained tones.
            flux[(flux < energy * 0.02) | (energy < 1e-6)] = 0.0
            times = (position + np.arange(count) * hop + fft_size / 2) / sample_rate
            previous = magnitude[-1].copy()
            used = count * hop
            pending = samples[used:].copy()
            position += used
            yield flux, times
        else:
            pending = samples
        decoded += len(block)
        if on_progress is not None:
            on_progress(float(decoded), float(frames))
        cancellation.checkpoint()
    if frames == 0 and on_progress is not None:
        on_progress(0.0, 0.0)
    cancellation.checkpoint()


def _estimate_window(novelty: Any, times: Any, envelope_rate: float) -> _Estimate | None:
    """Require both autocorrelation and a supported onset phase; never invent a grid."""
    import numpy as np
    from scipy.ndimage import gaussian_filter1d, median_filter
    from scipy.signal import find_peaks

    if len(novelty) < envelope_rate * 3.5 or float(novelty.max()) < 1e-6:
        return None
    baseline = median_filter(novelty, size=max(3, int(envelope_rate * 0.5) | 1))
    cleaned = np.maximum(novelty - baseline, 0)
    deviation = float(np.median(np.abs(cleaned - np.median(cleaned))))
    peaks, _ = find_peaks(
        cleaned,
        distance=max(1, int(envelope_rate * 0.16)),
        prominence=max(float(cleaned.max()) * 0.04, deviation * 3),
    )
    if len(peaks) < 6:
        return None
    strengths = cleaned[peaks]
    impulses = np.zeros(len(cleaned))
    impulses[peaks] = np.minimum(strengths, np.quantile(strengths, 0.9))
    pulse_envelope = gaussian_filter1d(impulses, sigma=max(0.5, envelope_rate * 0.012))
    first_lag = max(1, int(envelope_rate * 60 / 220))
    last_lag = min(len(cleaned) // 3, int(envelope_rate * 60 / 50))
    scores: list[float] = []
    for lag in range(first_lag, last_lag + 1):
        left, right = pulse_envelope[:-lag], pulse_envelope[lag:]
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        scores.append(float(np.dot(left, right)) / denominator if denominator else 0.0)
    if not scores or max(scores) < 0.5:
        return None
    # Correlation at integer multiples is also strong. Prefer the fundamental's
    # shortest strong lag, then refine it using the actual onset timestamps.
    best = max(scores)
    candidates, _ = find_peaks(np.asarray(scores))
    candidates = np.unique(np.concatenate(([int(np.argmax(scores))], candidates)))
    chosen = min(int(index) for index in candidates if scores[int(index)] >= best * 0.97)
    period = (first_lag + chosen) / envelope_rate
    onset_times = times[peaks]
    origin = float(onset_times[0])
    relative = onset_times - origin
    phases = np.mod(relative, period)
    histogram, _ = np.histogram(phases, bins=60, range=(0, period), weights=strengths)
    phase_bin = int(np.argmax(gaussian_filter1d(histogram, sigma=1, mode="wrap")))
    phase = (phase_bin + 0.5) * period / 60
    matched = np.abs((relative - phase + period / 2) % period - period / 2) <= max(
        0.045, period * 0.12
    )
    if int(matched.sum()) < 6:
        return None
    indices = np.rint((relative[matched] - phase) / period)
    centered = indices - indices.mean()
    denominator = float(np.dot(centered, centered))
    if denominator == 0:
        return None
    refined_period = float(np.dot(centered, relative[matched]) / denominator)
    if refined_period <= 0:
        return None
    period = refined_period
    phase = float(np.mean(relative[matched] - indices * period))
    matched = np.abs((relative - phase + period / 2) % period - period / 2) <= max(
        0.045, period * 0.12
    )
    selected = onset_times[matched]
    phase_support = float(strengths[matched].sum() / strengths.sum())
    expected_count = max(1.0, float((selected[-1] - selected[0]) / period + 1))
    coverage = min(1.0, len(selected) / expected_count)
    bpm = 60 / period
    confidence = min(1.0, best, phase_support, coverage)
    if not 50 <= bpm <= 220 or phase_support < 0.6 or coverage < 0.55:
        return None
    return _Estimate(bpm=bpm, confidence=confidence, beats=[float(t) for t in selected])


def estimate_beats(
    path: Path,
    *,
    cancellation: CancellationToken,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Estimate steady 50–220 BPM regions with detected onset times and confidence.

    Audio and the ~100 Hz onset envelope use fixed-size streaming windows. Only
    output beat timestamps and per-window summaries grow with recording length.
    Progress counts decoded input frames. Confidence is a heuristic, not a
    calibrated probability; syncopation and half/double-tempo ambiguity remain.
    """
    cancellation.checkpoint()
    import numpy as np
    import soundfile as sf

    with sf.SoundFile(path) as source:
        duration = source.frames / source.samplerate
        hop = max(1, round(source.samplerate / 100))
        envelope_rate = source.samplerate / hop
        window_size = round(_WINDOW_SECONDS * envelope_rate)
        advance = round((_WINDOW_SECONDS - _OVERLAP_SECONDS) * envelope_rate)
        values: list[float] = []
        timestamps: list[float] = []
        estimates: list[_Estimate] = []
        accepted_until = 0.0
        for novelty, times in _novelty_blocks(
            source, cancellation=cancellation, on_progress=on_progress, hop=hop
        ):
            values.extend(novelty.tolist())
            timestamps.extend(times.tolist())
            while len(values) >= window_size:
                cancellation.checkpoint()
                cutoff = timestamps[window_size - 1] - _OVERLAP_SECONDS / 2
                estimate = _estimate_window(
                    np.asarray(values[:window_size]),
                    np.asarray(timestamps[:window_size]),
                    envelope_rate,
                )
                if estimate is not None:
                    estimate.beats = [t for t in estimate.beats if accepted_until <= t < cutoff]
                    estimates.append(estimate)
                accepted_until = cutoff
                del values[:advance]
                del timestamps[:advance]
        cancellation.checkpoint()
        if values:
            estimate = _estimate_window(np.asarray(values), np.asarray(timestamps), envelope_rate)
            if estimate is not None:
                estimate.beats = [t for t in estimate.beats if accepted_until <= t <= duration]
                estimates.append(estimate)
        cancellation.checkpoint()

    result: dict[str, Any] = {
        "schemaVersion": 1,
        "durationSeconds": float(duration),
        "bpm": None,
        "beats": [],
        "confidence": 0.0,
    }
    if duration < 8:
        result["reason"] = "insufficient_duration"
        return result
    estimates = [estimate for estimate in estimates if estimate.beats]
    if not estimates:
        result["reason"] = "no_reliable_rhythm"
        return result
    estimates.sort(key=lambda item: item.bpm)
    weights = [len(item.beats) * item.confidence for item in estimates]
    midpoint = sum(weights) / 2
    accumulated = 0.0
    representative = estimates[0].bpm
    for item, weight in zip(estimates, weights, strict=True):
        accumulated += weight
        if accumulated >= midpoint:
            representative = item.bpm
            break
    consistent = [item for item in estimates if abs(item.bpm / representative - 1) < 0.06]
    supported = sum(len(item.beats) * item.confidence for item in consistent)
    if supported < sum(weights) * 0.6:
        result["reason"] = "inconsistent_tempo"
        return result
    beats = sorted({beat for item in consistent for beat in item.beats})
    if len(beats) < 8:
        result["reason"] = "insufficient_beats"
        return result
    result.update(
        bpm=float(sum(item.bpm * len(item.beats) for item in consistent) / len(beats)),
        beats=beats,
        confidence=float(
            sum(item.confidence * len(item.beats) for item in consistent) / len(beats)
        ),
    )
    return result
