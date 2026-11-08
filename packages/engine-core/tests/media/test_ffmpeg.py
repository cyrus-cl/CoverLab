from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import time
import wave
from pathlib import Path
from threading import Timer
from typing import Any

import pytest
from ai4music_core.media.hashing import CancellationToken, JobCanceled


@pytest.fixture
def tools() -> Any:
    from ai4music_core.media.ffmpeg import FFmpegTools

    configured = os.environ.get("AI4MUSIC_TEST_FFMPEG_DIR")
    directory = Path(configured) if configured else None
    if directory is None and shutil.which("ffmpeg") is None:
        local = Path(r"D:\tools\ffmpeg\ffmpeg-9.0.2-essentials_build\bin")
        if not local.is_dir():
            pytest.skip("Real FFmpeg tools are not installed")
        directory = local
    return FFmpegTools.discover(directory)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "原始声音 with spaces.wav"
    samples = struct.pack(
        "<24000h", *(round(12000 * math.sin(2 * math.pi * 440 * n / 16000)) for n in range(24000))
    )
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(samples)
    return path


def test_discover_prefers_explicit_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ai4music_core.media.ffmpeg import FFmpegTools

    suffix = ".exe" if os.name == "nt" else ""
    for name in ("ffmpeg", "ffprobe"):
        executable = tmp_path / f"{name}{suffix}"
        executable.touch()
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", "")

    found = FFmpegTools.discover(tmp_path)

    assert found.ffmpeg_path == tmp_path / f"ffmpeg{suffix}"
    assert found.ffprobe_path == tmp_path / f"ffprobe{suffix}"


def test_missing_tools_have_a_safe_stable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai4music_core.media.ffmpeg import FFmpegTools, MediaToolError

    monkeypatch.setenv("PATH", "")
    monkeypatch.delattr("sys._MEIPASS", raising=False)

    with pytest.raises(MediaToolError) as caught:
        FFmpegTools.discover()

    assert caught.value.code == "media_tools_missing"
    assert len(caught.value.message) < 200


@pytest.mark.parametrize("subdirectory", ["bin", "ffmpeg/bin", "ffmpeg", "."])
def test_discover_finds_bundled_tools_before_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, subdirectory: str
) -> None:
    from ai4music_core.media.ffmpeg import FFmpegTools

    suffix = ".exe" if os.name == "nt" else ""
    directory = tmp_path / subdirectory
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("ffmpeg", "ffprobe"):
        executable = directory / f"{name}{suffix}"
        executable.touch()
        executable.chmod(0o755)
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("PATH", "")

    found = FFmpegTools.discover(tmp_path / "missing-explicit-directory")

    assert found.ffmpeg_path == (directory / f"ffmpeg{suffix}").absolute()
    assert found.ffprobe_path == (directory / f"ffprobe{suffix}").absolute()


def test_probe_reads_true_audio_stream_metadata(tools: Any, source: Path) -> None:
    info = tools.probe(source)

    assert info.duration_seconds == pytest.approx(1.5, abs=0.001)
    assert info.sample_rate == 16000
    assert info.channels == 1
    assert info.codec == "pcm_s16le"
    assert info.container == "wav"


def test_probe_rejects_invalid_audio_without_exposing_path(tools: Any, tmp_path: Path) -> None:
    from ai4music_core.media.ffmpeg import MediaToolError

    source = tmp_path / "private-project-password.wav"
    source.write_bytes(b"this is not a valid audio file")

    with pytest.raises(MediaToolError) as caught:
        tools.probe(source)

    assert caught.value.code == "media_invalid"
    assert "private-project-password" not in str(caught.value)
    assert len(caught.value.message) < 200


def test_probe_rejects_audio_free_file(tools: Any, tmp_path: Path) -> None:
    from ai4music_core.media.ffmpeg import MediaToolError

    source = tmp_path / "image.ppm"
    source.write_bytes(b"P6\n1 1\n255\n\xff\x00\x00")

    with pytest.raises(MediaToolError) as caught:
        tools.probe(source)

    assert caught.value.code == "media_no_audio"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sample_rate", 0),
        ("sample_rate", "not-a-number"),
        ("sample_rate", 44100.5),
        ("channels", 0),
        ("channels", True),
        ("duration", "NaN"),
        ("duration", "Infinity"),
        ("duration", -1),
        ("duration", True),
    ],
)
def test_probe_rejects_invalid_numeric_metadata(
    tools: Any, source: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    from ai4music_core.media.ffmpeg import FFmpegTools, MediaToolError

    audio: dict[str, object] = {
        "codec_type": "audio",
        "codec_name": "pcm_s16le",
        "sample_rate": "16000",
        "channels": 1,
        "duration": "1.5",
    }
    audio[field] = value
    payload = json.dumps({"streams": [audio], "format": {"format_name": "wav"}}).encode()

    def captured_metadata(*_args: object, **_kwargs: object) -> bytes:
        return payload

    monkeypatch.setattr(FFmpegTools, "_run", staticmethod(captured_metadata))

    with pytest.raises(MediaToolError) as caught:
        tools.probe(source)

    assert caught.value.code == "media_invalid"


@pytest.mark.parametrize("payload", [b"not JSON", b"[]", b'{"streams": null}', b"\xff"])
def test_probe_rejects_malformed_tool_metadata(
    tools: Any, source: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    from ai4music_core.media.ffmpeg import FFmpegTools, MediaToolError

    def captured_metadata(*_args: object, **_kwargs: object) -> bytes:
        return payload

    monkeypatch.setattr(FFmpegTools, "_run", staticmethod(captured_metadata))

    with pytest.raises(MediaToolError) as caught:
        tools.probe(source)

    assert caught.value.code == "media_invalid"


def test_normalize_publishes_valid_float_wav_and_keeps_source(tools: Any, source: Path) -> None:
    before_bytes = hashlib.sha256(source.read_bytes()).hexdigest()
    before_stat = source.stat()
    destination = source.parent / "derived" / "normalized.wav"
    progress: list[tuple[float, float]] = []

    info = tools.normalize(
        source,
        destination,
        info=tools.probe(source),
        on_progress=lambda done, total: progress.append((done, total)),
    )

    assert destination.is_file()
    assert info == tools.probe(destination)
    assert info.sample_rate == 44100
    assert info.channels == 2
    assert info.codec == "pcm_f32le"
    assert info.container == "wav"
    assert info.duration_seconds == pytest.approx(1.5, abs=1 / 44100)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_bytes
    after_stat = source.stat()
    assert (after_stat.st_size, after_stat.st_mtime_ns, after_stat.st_mode) == (
        before_stat.st_size,
        before_stat.st_mtime_ns,
        before_stat.st_mode,
    )
    assert progress
    assert progress[-1][0] == pytest.approx(info.duration_seconds, abs=0.01)
    assert all(total == 1.5 and 0 <= done <= 1.51 for done, total in progress)
    assert [done for done, _ in progress] == sorted(done for done, _ in progress)
    assert list(destination.parent.iterdir()) == [destination]


def test_normalize_accepts_compressed_audio(tools: Any, source: Path) -> None:
    compressed = source.with_suffix(".mp3")
    subprocess.run(
        [str(tools.ffmpeg_path), "-v", "error", "-i", str(source), str(compressed)],
        check=True,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    info = tools.probe(compressed)

    output = tools.normalize(compressed, source.parent / "decoded.wav", info=info)

    assert info.codec == "mp3"
    assert output.codec == "pcm_f32le"
    assert output.sample_rate == 44100
    assert output.channels == 2
    assert output.duration_seconds == pytest.approx(1.5, abs=0.01)


def test_normalize_rejects_source_as_destination(tools: Any, source: Path) -> None:
    from ai4music_core.media.ffmpeg import MediaToolError

    before = source.read_bytes()

    with pytest.raises(MediaToolError) as caught:
        tools.normalize(source, source, info=tools.probe(source))

    assert caught.value.code == "media_destination_invalid"
    assert source.read_bytes() == before


def test_failed_conversion_keeps_existing_destination_and_removes_only_own_partial(
    tools: Any, source: Path
) -> None:
    from ai4music_core.media.ffmpeg import MediaToolError

    info = tools.probe(source)
    source.write_bytes(b"broken after import")
    destination = source.parent / "normalized.wav"
    destination.write_bytes(b"previous completed output")
    unrelated_partial = source.parent / ".other-job-partial.wav"
    unrelated_partial.write_bytes(b"another job owns this file")

    with pytest.raises(MediaToolError) as caught:
        tools.normalize(source, destination, info=info)

    assert caught.value.code == "media_normalization_failed"
    assert destination.read_bytes() == b"previous completed output"
    assert source.read_bytes() == b"broken after import"
    assert unrelated_partial.read_bytes() == b"another job owns this file"
    assert len(list(source.parent.iterdir())) == 3


def test_cancellation_during_progress_never_publishes_output(tools: Any, source: Path) -> None:
    destination = source.parent / "cancelled.wav"
    destination.write_bytes(b"previous completed output")
    token = CancellationToken()

    with pytest.raises(JobCanceled):
        tools.normalize(
            source,
            destination,
            info=tools.probe(source),
            cancellation=token,
            on_progress=lambda _done, _total: token.request(),
        )

    assert destination.read_bytes() == b"previous completed output"
    assert sorted(item.name for item in source.parent.iterdir()) == sorted(
        [source.name, destination.name]
    )


def test_cancellation_terminates_a_running_ffmpeg(
    tools: Any, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_popen = subprocess.Popen
    token = CancellationToken()
    children: list[subprocess.Popen[bytes]] = []
    timers: list[Timer] = []

    def realtime_process(command: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        assert kwargs["shell"] is False
        assert kwargs["creationflags"] == (subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if "-progress" in command:
            command = command.copy()
            command.insert(command.index("-i"), "-re")
        process: subprocess.Popen[bytes] = original_popen(command, **kwargs)
        if "-progress" in command:
            children.append(process)
            timer = Timer(0.15, token.request)
            timers.append(timer)
            timer.start()
        return process

    monkeypatch.setattr("ai4music_core.media.ffmpeg.subprocess.Popen", realtime_process)
    destination = source.parent / "cancelled.wav"
    info = tools.probe(source)
    started = time.monotonic()
    try:
        with pytest.raises(JobCanceled):
            tools.normalize(source, destination, info=info, cancellation=token)
    finally:
        for timer in timers:
            timer.cancel()

    assert time.monotonic() - started < 1.4
    assert children and all(child.poll() is not None for child in children)
    assert not destination.exists()
    assert list(source.parent.iterdir()) == [source]


def test_pre_cancelled_probe_does_not_launch_a_process(tools: Any, source: Path) -> None:
    token = CancellationToken()
    token.request()

    with pytest.raises(JobCanceled):
        tools.probe(source, cancellation=token)
