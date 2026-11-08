from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf


def test_worker_verifies_pinned_files_before_loading_runtime(tmp_path: Path) -> None:
    from ai4music_core.lyrics.worker import infer

    with pytest.raises(ValueError, match="model_checksum_mismatch"):
        infer(tmp_path / "missing", tmp_path / "source.wav", io.StringIO())


def test_child_verifies_requested_curated_model_not_an_unrelated_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai4music_core.lyrics.worker import infer

    source = tmp_path / "silence.wav"
    sf.write(source, np.zeros(16000), 16000)
    seen = []
    monkeypatch.setattr(
        "ai4music_core.lyrics.worker.verify_model",
        lambda path, spec, token: seen.append(spec.model_id),
    )
    protocol = io.StringIO()
    infer(tmp_path, source, protocol, model_id="faster-whisper-tiny")
    assert seen == ["faster-whisper-tiny"]
    assert json.loads(protocol.getvalue().splitlines()[-1])["model_id"] == "faster-whisper-tiny"


def test_unknown_child_model_is_rejected_before_audio_or_runtime(tmp_path: Path) -> None:
    from ai4music_core.lyrics.worker import infer

    with pytest.raises(ValueError, match="model_unsupported"):
        infer(tmp_path, tmp_path / "missing.wav", io.StringIO(), model_id="untrusted-model")


def test_worker_preserves_actual_recognition_and_word_probability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai4music_core.lyrics.worker import infer

    source = tmp_path / "voice.wav"
    sf.write(source, np.sin(np.arange(32000) * 0.04) * 0.1, 16000)
    monkeypatch.setattr("ai4music_core.lyrics.worker.verify_model", lambda *_args: None)

    class Recognizer:
        def __init__(self, path, **kwargs):
            assert kwargs["local_files_only"] is True
            assert kwargs["device"] == "cpu"
            assert kwargs["compute_type"] == "int8"

        def transcribe(self, audio, **kwargs):
            assert kwargs["word_timestamps"] is True
            assert kwargs["task"] == "transcribe"
            # Compare audio-grounded previous decoded text; never seed the song
            # title, reference lyrics or a language-specific semantic guess.
            assert kwargs["condition_on_previous_text"] is True
            assert "initial_prompt" not in kwargs
            assert "prefix" not in kwargs
            assert kwargs.get("language") is None
            return iter(
                [
                    SimpleNamespace(
                        start=0.2,
                        end=1.4,
                        text=" actual words",
                        words=[
                            SimpleNamespace(start=0.2, end=0.8, word=" actual", probability=0.9),
                            SimpleNamespace(start=0.8, end=1.4, word=" words", probability=0.8),
                        ],
                    )
                ]
            ), SimpleNamespace(language="en")

    monkeypatch.setattr("faster_whisper.WhisperModel", Recognizer)
    protocol = io.StringIO()
    infer(tmp_path, source, protocol, model_id="faster-whisper-small")
    events = [json.loads(line) for line in protocol.getvalue().splitlines()]
    result = events[-1]
    assert result["event"] == "result"
    document = result["document"]
    assert document["durationSeconds"] == 2
    assert document["language"] == "en"
    assert document["segments"][0]["text"] == "actual words"
    assert document["segments"][0]["words"][1]["probability"] == 0.8
    assert result["raw_recognition"]["segments"][0]["text"] == " actual words"
    assert result["raw_recognition"]["decoding"]["condition_on_previous_text"] is True
    assert result["raw_recognition"]["model_id"] == "faster-whisper-small"
    assert events[-2]["phase"] == "transcription"


def test_exact_silence_produces_empty_document_without_fake_recognition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai4music_core.lyrics.worker import infer

    source = tmp_path / "silence.wav"
    sf.write(source, np.zeros(32000), 16000)
    monkeypatch.setattr("ai4music_core.lyrics.worker.verify_model", lambda *_args: None)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("silence should not be decoded into guessed words")

    monkeypatch.setattr("faster_whisper.WhisperModel", forbidden)
    protocol = io.StringIO()
    infer(tmp_path, source, protocol, model_id="faster-whisper-small")
    result = json.loads(protocol.getvalue().splitlines()[-1])
    assert result["document"] == {
        "schemaVersion": 1,
        "durationSeconds": 2,
        "language": None,
        "segments": [],
    }


def test_nonfinite_audio_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ai4music_core.lyrics.worker import infer

    source = tmp_path / "nan.wav"
    sf.write(source, np.full(16000, np.nan), 16000, subtype="FLOAT")
    monkeypatch.setattr("ai4music_core.lyrics.worker.verify_model", lambda *_args: None)
    with pytest.raises(ValueError, match="audio_invalid"):
        infer(tmp_path, source, io.StringIO(), model_id="faster-whisper-small")


def test_entrypoint_protocol_is_utf8_even_under_legacy_windows_encoding() -> None:
    script = (
        "from ai4music_core.lyrics import worker; "
        'worker.infer=lambda m,s,p:p.write(\'{"event":"result","text":"你好🎵"}\\n\'); '
        "raise SystemExit(worker.main(['--model','m','--source','s']))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout.decode("utf-8"))["text"] == "你好🎵"


@pytest.mark.parametrize("next_word_start", [7.18, 7.1])
def test_segment_envelope_includes_model_words_without_changing_word_timings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    next_word_start: float,
) -> None:
    """Real singing output has word bounds beyond sentence metadata boundaries."""
    from ai4music_core.lyrics.worker import infer

    source = tmp_path / "voice.wav"
    sf.write(source, np.sin(np.arange(16000 * 16) * 0.04) * 0.1, 16000)
    monkeypatch.setattr("ai4music_core.lyrics.worker.verify_model", lambda *_args: None)

    class Recognizer:
        def __init__(self, *_args, **_kwargs):
            pass

        def transcribe(self, *_args, **_kwargs):
            # Numerical boundaries from the full-song failure; text is synthetic.
            return iter(
                [
                    SimpleNamespace(
                        start=4.32,
                        end=7.0,
                        text=" model text",
                        words=[
                            SimpleNamespace(start=6.52, end=7.16, word=" text", probability=0.8),
                        ],
                    ),
                    SimpleNamespace(
                        start=7.2,
                        end=8.0,
                        text=" next",
                        words=[
                            SimpleNamespace(
                                start=next_word_start, end=8.0, word=" next", probability=0.9
                            ),
                        ],
                    ),
                    SimpleNamespace(
                        start=11.9,
                        end=14.0,
                        text=" other text",
                        words=[
                            SimpleNamespace(start=11.88, end=12.2, word=" other", probability=0.7),
                            SimpleNamespace(start=13.62, end=14.26, word=" text", probability=0.6),
                        ],
                    ),
                ]
            ), SimpleNamespace(language="zh")

    monkeypatch.setattr("faster_whisper.WhisperModel", Recognizer)
    protocol = io.StringIO()
    if next_word_start < 7.16:
        # Reject truly overlapping words rather than inventing replacement bounds.
        with pytest.raises(ValueError, match=r"segments\[1\]"):
            infer(tmp_path, source, protocol, model_id="faster-whisper-small")
        assert '"event": "result"' not in protocol.getvalue()
        return
    infer(tmp_path, source, protocol, model_id="faster-whisper-small")
    result = json.loads(protocol.getvalue().splitlines()[-1])
    first, consecutive, second = result["document"]["segments"]
    assert (first["start"], first["end"]) == (4.32, 7.16)
    assert first["words"] == [
        {"start": 6.52, "end": 7.16, "word": " text", "probability": 0.8},
    ]
    assert (consecutive["start"], consecutive["end"]) == (7.18, 8.0)
    assert first["end"] <= consecutive["start"] <= second["start"]
    assert (second["start"], second["end"]) == (11.88, 14.26)
    assert second["words"] == [
        {"start": 11.88, "end": 12.2, "word": " other", "probability": 0.7},
        {"start": 13.62, "end": 14.26, "word": " text", "probability": 0.6},
    ]
    assert (first["text"], second["text"]) == ("model text", "other text")
