from __future__ import annotations

import importlib
import json
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from ai4music_core.media.hashing import CancellationToken, JobCanceled


def _features() -> Any:
    return importlib.import_module("ai4music_core.analysis.features")


def _write(path: Path, samples: Any, sample_rate: int = 8000) -> Path:
    sf.write(path, samples, sample_rate, subtype="FLOAT")
    return path


def _pulse_train(
    bpm: float, *, duration: float = 24.0, start: float = 0.37, sample_rate: int = 8000
) -> tuple[Any, Any]:
    samples = np.zeros(round(duration * sample_rate), dtype=np.float32)
    times = np.arange(start, duration - 0.1, 60 / bpm)
    pulse_time = np.arange(round(sample_rate * 0.025)) / sample_rate
    pulse = 0.8 * np.exp(-pulse_time * 160) * np.cos(2 * np.pi * 900 * pulse_time)
    for beat in times:
        index = round(beat * sample_rate)
        samples[index : index + len(pulse)] += pulse
    return samples, times


def test_waveform_retains_exact_signed_extrema_and_original_stereo_channels(tmp_path: Path) -> None:
    samples = np.array(
        [[0.1, -0.1], [0.8, -0.8], [-0.2, 0.2], [0.3, -0.3], [1.0, -1.0]],
        dtype=np.float32,
    )
    source = _write(tmp_path / "opposite-polarity.wav", samples)
    progress: list[tuple[float, float]] = []

    result = _features().analyze_waveform(
        source,
        cancellation=CancellationToken(),
        on_progress=lambda done, total: progress.append((done, total)),
        max_points=2,
    )

    assert result["schemaVersion"] == 1
    assert result["sampleRate"] == 8000
    assert result["channels"] == 2
    assert result["durationSeconds"] == 5 / 8000
    assert np.asarray(result["peaks"]) == pytest.approx(np.array([[-0.8, 0.8], [-1.0, 1.0]]))
    assert progress[-1] == (5.0, 5.0)
    json.dumps(result, allow_nan=False)


def test_waveform_preserves_real_quiet_amplitude_and_caps_point_count(tmp_path: Path) -> None:
    source = _write(tmp_path / "quiet.wav", np.full(8001, 0.125, dtype=np.float32))

    result = _features().analyze_waveform(source, cancellation=CancellationToken(), max_points=33)

    assert len(result["peaks"]) == 33
    assert all(pair == [0.125, 0.125] for pair in result["peaks"])


@pytest.mark.parametrize("max_points", [0, -1])
def test_waveform_rejects_invalid_resolution(tmp_path: Path, max_points: int) -> None:
    source = _write(tmp_path / "input.wav", np.zeros(10, dtype=np.float32))

    with pytest.raises(ValueError, match="max_points"):
        _features().analyze_waveform(
            source, cancellation=CancellationToken(), max_points=max_points
        )


@pytest.mark.parametrize("operation", ["analyze_waveform", "estimate_beats"])
def test_analysis_rejects_nonfinite_samples(tmp_path: Path, operation: str) -> None:
    samples = np.zeros(8000, dtype=np.float32)
    samples[100] = np.nan
    source = _write(tmp_path / "invalid.wav", samples)

    with pytest.raises(ValueError, match="finite"):
        getattr(_features(), operation)(source, cancellation=CancellationToken())


@pytest.mark.parametrize("operation", ["analyze_waveform", "estimate_beats"])
def test_analysis_honors_preexisting_and_midstream_cancellation(
    tmp_path: Path, operation: str
) -> None:
    source = _write(tmp_path / "cancel.wav", np.zeros(200_000, dtype=np.float32))
    canceled = CancellationToken()
    canceled.request()

    with pytest.raises(JobCanceled):
        getattr(_features(), operation)(source, cancellation=canceled)

    canceled = CancellationToken()
    with pytest.raises(JobCanceled):
        getattr(_features(), operation)(
            source,
            cancellation=canceled,
            on_progress=lambda _done, _total: canceled.request(),
        )


@pytest.mark.parametrize("bpm", [90.0, 120.0, 150.0])
def test_estimates_known_tempos_and_detects_actual_nonzero_beat_phase(
    tmp_path: Path, bpm: float
) -> None:
    samples, expected_beats = _pulse_train(bpm)
    source = _write(tmp_path / f"pulse-{bpm}.wav", samples)
    progress: list[tuple[float, float]] = []

    result = _features().estimate_beats(
        source,
        cancellation=CancellationToken(),
        on_progress=lambda done, total: progress.append((done, total)),
    )

    assert result["schemaVersion"] == 1
    assert result["durationSeconds"] == 24.0
    assert result["bpm"] == pytest.approx(bpm, abs=1.0)
    assert 0.6 <= result["confidence"] <= 1.0
    beats = np.array(result["beats"])
    assert len(beats) >= len(expected_beats) - 2
    assert np.all(np.diff(beats) > 0)
    distances = np.abs(beats[:, None] - expected_beats[None, :]).min(axis=1)
    assert np.max(distances) < 0.05
    assert beats[0] > 0.25
    assert progress[-1] == (float(len(samples)), float(len(samples)))
    assert all(a[0] <= b[0] for a, b in pairwise(progress))
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("kind", ["empty", "silence", "short", "tone", "noise"])
def test_absent_or_insufficient_rhythm_returns_no_invented_tempo(tmp_path: Path, kind: str) -> None:
    if kind == "empty":
        samples = np.zeros(0, dtype=np.float32)
    elif kind == "short":
        samples, _ = _pulse_train(120, duration=2)
    elif kind == "tone":
        samples = 0.2 * np.sin(np.arange(24 * 8000) * 2 * np.pi * 440 / 8000)
    elif kind == "noise":
        samples = np.random.default_rng(2026).normal(0, 0.08, 24 * 8000)
    else:
        samples = np.zeros(24 * 8000, dtype=np.float32)
    source = _write(tmp_path / f"{kind}.wav", samples)

    result = _features().estimate_beats(source, cancellation=CancellationToken())

    assert result["bpm"] is None
    assert result["beats"] == []
    assert result["confidence"] == 0.0
    assert result["reason"]
    json.dumps(result, allow_nan=False)


def test_empty_waveform_is_empty_and_reports_actual_zero_frames(tmp_path: Path) -> None:
    source = _write(tmp_path / "empty.wav", np.zeros(0, dtype=np.float32))
    progress: list[tuple[float, float]] = []

    result = _features().analyze_waveform(
        source,
        cancellation=CancellationToken(),
        on_progress=lambda done, total: progress.append((done, total)),
    )

    assert result["durationSeconds"] == 0
    assert result["peaks"] == []
    assert progress == [(0.0, 0.0)]


def test_rhythm_tracks_across_streaming_window_boundary(tmp_path: Path) -> None:
    samples, expected_beats = _pulse_train(90, duration=67)
    source = _write(tmp_path / "longer.wav", samples)

    result = _features().estimate_beats(source, cancellation=CancellationToken())

    assert result["bpm"] == pytest.approx(90, abs=1.0)
    beats = np.array(result["beats"])
    assert len(beats) >= len(expected_beats) - 3
    assert np.max(np.diff(beats)) < 0.8
    assert np.max(np.abs(beats[:, None] - expected_beats[None, :]).min(axis=1)) < 0.05
