from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from threading import Timer

import pytest
from ai4music_core.media.hashing import CancellationToken, JobCanceled


def test_runtime_query_is_lazy_and_does_not_create_or_download_models(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys,json; from pathlib import Path; "
                "from ai4music_core.lyrics import WhisperProvider; "
                f"info=WhisperProvider(Path({str(tmp_path / 'absent')!r})).runtime_info(); "
                "assert 'faster_whisper' not in sys.modules; "
                "assert 'ctranslate2' not in sys.modules; print(json.dumps(info))"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(result.stdout)
    assert info["model_cached"] is False
    assert info["device"] == "cpu"
    assert info["model_id"] == "qwen3-asr-0.6b-hf"
    assert info["download_bytes"] == 2062575595
    assert info["alignment_model_id"] == "faster-whisper-small"
    assert not (tmp_path / "absent").exists()


def test_worker_command_supports_python_and_frozen_dispatch() -> None:
    from ai4music_core.lyrics.provider import worker_command

    assert worker_command(["--test"], frozen=False) == [
        sys.executable,
        "-m",
        "ai4music_core.lyrics.worker",
        "--test",
    ]
    assert worker_command(["--test"], frozen=True) == [sys.executable, "--lyrics-worker", "--test"]


def test_cancel_kills_live_worker_promptly(tmp_path: Path) -> None:
    from ai4music_core.lyrics.provider import run_worker

    script = tmp_path / "long_worker.py"
    script.write_text(
        'import time\nprint(\'{"event":"phase","phase":"inference"}\',flush=True)\ntime.sleep(60)'
    )
    token = CancellationToken()
    timer = Timer(0.5, token.request)
    timer.start()
    try:
        with pytest.raises(JobCanceled):
            run_worker([sys.executable, str(script)], cancellation=token)
    finally:
        timer.cancel()


@pytest.mark.parametrize("failure", [False, True])
def test_output_directory_only_appears_after_all_timed_outputs_are_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: bool,
) -> None:
    from ai4music_core.lyrics import WhisperProvider

    provider = WhisperProvider(tmp_path / "models")
    monkeypatch.setattr(
        provider,
        "prepare",
        lambda *_args, **_kw: {"path": str(tmp_path), "alignment_path": str(tmp_path)},
    )
    source = tmp_path / "source.wav"
    source.write_bytes(b"source untouched")
    target = tmp_path / "new-output"

    def run(_cmd: list[str], **_kw):
        assert not target.exists()
        if failure:
            raise ValueError("bad inference")
        return {
            "event": "result",
            "document": {
                "schemaVersion": 1,
                "durationSeconds": 2,
                "language": "en",
                "segments": [{"start": 0.5, "end": 1.5, "text": "hello", "words": []}],
            },
        }

    monkeypatch.setattr("ai4music_core.lyrics.provider.run_worker", run)
    if failure:
        with pytest.raises(ValueError, match="bad inference"):
            provider.transcribe(source, target, cancellation=CancellationToken())
        assert not target.exists()
    else:
        result = provider.transcribe(source, target, cancellation=CancellationToken())
        assert json.loads(result.json_path.read_text())["segments"][0]["text"] == "hello"
        assert result.lrc_path.read_text() == "[00:00.500]hello\n"
        assert "00:00:00,500 --> 00:00:01,500" in result.srt_path.read_text()
    assert source.read_bytes() == b"source untouched"
    assert not list(tmp_path.glob("*.part"))


def test_existing_outputs_are_never_overwritten(tmp_path: Path) -> None:
    from ai4music_core.lyrics import WhisperProvider

    output = tmp_path / "keep"
    output.mkdir()
    source = tmp_path / "source.wav"
    source.touch()
    with pytest.raises(RuntimeError, match="output_exists"):
        WhisperProvider(tmp_path).transcribe(source, output, cancellation=CancellationToken())


def test_worker_receives_explicit_model_and_raw_output_is_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai4music_core.lyrics import WhisperProvider

    provider = WhisperProvider(tmp_path / "models")
    monkeypatch.setattr(
        provider,
        "prepare",
        lambda *_args, **_kw: {"path": str(tmp_path), "alignment_path": str(tmp_path)},
    )
    source = tmp_path / "source.wav"
    source.write_bytes(b"source unchanged")
    raw = {"language": "zh", "segments": [{"text": " 其余你还爱我", "start": 0.2, "end": 1.8}]}

    def run(command, **_kwargs):
        assert command[command.index("--model-id") + 1] == "qwen3-asr-0.6b-hf"
        assert command[command.index("--alignment-model") + 1] == str(tmp_path.resolve())
        return {
            "event": "result",
            "raw_recognition": raw,
            "document": {
                "schemaVersion": 1,
                "durationSeconds": 2,
                "language": "zh",
                "segments": [{"start": 0.2, "end": 1.8, "text": "其余你还爱我", "words": []}],
            },
        }

    monkeypatch.setattr("ai4music_core.lyrics.provider.run_worker", run)
    output = provider.transcribe(source, tmp_path / "out", cancellation=CancellationToken())
    assert json.loads(output.raw_json_path.read_text(encoding="utf-8")) == raw
    assert (
        json.loads(output.json_path.read_text(encoding="utf-8"))["segments"][0]["text"]
        == "其余你还爱我"
    )
