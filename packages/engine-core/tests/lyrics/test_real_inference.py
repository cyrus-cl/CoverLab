"""Opt-in actual CPU checks, using an explicitly prepared model and local fixture.

Set AI4MUSIC_LYRICS_MODEL_DIR and AI4MUSIC_LYRICS_SMOKE_WAV to run these.
No model or test audio is downloaded by the default test suite.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import Timer

import numpy as np
import pytest
import soundfile as sf
from ai4music_core.media.hashing import CancellationToken, JobCanceled

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(
        not os.environ.get("AI4MUSIC_LYRICS_MODEL_DIR")
        or not os.environ.get("AI4MUSIC_LYRICS_SMOKE_WAV"),
        reason="requires explicitly prepared local model and official Whisper JFK WAV fixture",
    ),
]


def test_real_cpu_transcription_publishes_timed_words(tmp_path: Path) -> None:
    from ai4music_core.lyrics import WhisperProvider, validate_document

    provider = WhisperProvider(Path(os.environ["AI4MUSIC_LYRICS_MODEL_DIR"]))
    result = provider.transcribe(
        Path(os.environ["AI4MUSIC_LYRICS_SMOKE_WAV"]),
        tmp_path / "recognized",
        cancellation=CancellationToken(),
    )
    document = validate_document(json.loads(result.json_path.read_text(encoding="utf-8")))
    assert document["language"] == "en"
    assert "country" in " ".join(s["text"].lower() for s in document["segments"])
    assert sum(len(s["words"]) for s in document["segments"]) >= 10
    assert result.device == "cpu"
    assert result.lrc_path.is_file() and result.srt_path.is_file()


def test_real_worker_exact_silence_stays_empty(tmp_path: Path) -> None:
    from ai4music_core.lyrics import WhisperProvider

    source = tmp_path / "silence.wav"
    sf.write(source, np.zeros((44100 * 3, 2)), 44100, subtype="FLOAT")
    provider = WhisperProvider(Path(os.environ["AI4MUSIC_LYRICS_MODEL_DIR"]))
    result = provider.transcribe(source, tmp_path / "empty", cancellation=CancellationToken())
    document = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert document["segments"] == []
    assert document["durationSeconds"] == 3
    assert result.lrc_path.read_text() == result.srt_path.read_text() == ""


def test_cancel_real_native_inference_has_no_published_outputs(tmp_path: Path) -> None:
    from ai4music_core.lyrics import WhisperProvider

    provider = WhisperProvider(Path(os.environ["AI4MUSIC_LYRICS_MODEL_DIR"]))
    token = CancellationToken()
    timers = []
    requested_at = []

    def event(message):
        if message.get("event") == "phase" and message.get("phase") == "transcription":

            def cancel():
                requested_at.append(time.monotonic())
                token.request()

            timer = Timer(0.02, cancel)
            timers.append(timer)
            timer.start()

    time.monotonic()
    try:
        with pytest.raises(JobCanceled):
            provider.transcribe(
                Path(os.environ["AI4MUSIC_LYRICS_SMOKE_WAV"]),
                tmp_path / "canceled",
                cancellation=token,
                on_event=event,
            )
    finally:
        for timer in timers:
            timer.cancel()
    assert timers, "cancellation must happen after the actual model was loaded"
    assert requested_at
    assert time.monotonic() - requested_at[0] < 3
    assert not (tmp_path / "canceled").exists()
    assert not list(tmp_path.glob("*.part"))
