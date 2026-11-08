from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
TAURI_CONFIG = ROOT / "apps" / "desktop" / "src-tauri" / "tauri.conf.json"
BUILD_SCRIPT = ROOT / "scripts" / "build_windows.ps1"
VERIFY_SCRIPT = ROOT / "scripts" / "verify_windows_bundle.ps1"
NOTICES = ROOT / "docs" / "licenses" / "THIRD_PARTY_NOTICES.md"


def test_desktop_release_has_no_cloud_runtime_contract() -> None:
    config = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    serialized = json.dumps(config).lower()

    for forbidden in ("postgres", "redis", "s3", "celery"):
        assert forbidden not in serialized


def test_release_is_a_current_user_nsis_bundle_with_explicit_resources() -> None:
    config = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    bundle = config["bundle"]

    assert bundle["targets"] == ["nsis"]
    assert bundle["useLocalToolsDir"] is True
    assert bundle["windows"]["nsis"]["installMode"] == "currentUser"
    assert bundle["resources"] == {
        "resources/engine/": "engine/",
        "../../../docs/licenses/THIRD_PARTY_NOTICES.md": "THIRD_PARTY_NOTICES.md",
    }


def test_release_documents_licenses_and_model_boundary() -> None:
    assert NOTICES.is_file()
    content = NOTICES.read_text(encoding="utf-8")

    for dependency in ("Tauri", "FastAPI", "React", "PyInstaller", "SQLAlchemy"):
        assert dependency in content
    assert "FFmpeg / ffprobe" in content
    assert "LGPL-3.0-or-later" in content
    assert "PyTorch CPU" in content
    assert "Demucs" in content
    assert "模型权重不包含在安装包" in content
    assert "用户明确启动分轨处理后" in content


def test_launcher_dispatches_worker_without_api_startup_or_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai4music_engine import launcher

    arguments = ["--model", "model.th", "--source", "audio.wav", "--output", "out"]
    received: list[list[str]] = []
    worker = ModuleType("ai4music_core.separation.worker")

    def worker_main(argv: list[str]) -> int:
        received.append(argv)
        return 7

    worker.main = worker_main  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, worker.__name__, worker)
    monkeypatch.delenv("AI4MUSIC_LAUNCH_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["engine.exe", "--separation-worker", *arguments])

    def no_api() -> None:
        pytest.fail("Worker mode must not start the API")

    monkeypatch.setattr(launcher, "serve", no_api)
    with pytest.raises(SystemExit) as result:
        launcher.main()
    assert result.value.code == 7
    assert received == [arguments]


def test_media_preparation_verifies_archive_and_each_runtime_file(tmp_path: Path) -> None:
    import hashlib
    import importlib.util
    from zipfile import ZipFile

    script = ROOT / "scripts" / "prepare_media_runtime.py"
    assert script.is_file(), "Pinned FFmpeg preparation helper is required"
    spec = importlib.util.spec_from_file_location("prepare_media_runtime", script)
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    archive = tmp_path / "runtime.zip"
    payload = b"pinned ffmpeg binary"
    with ZipFile(archive, "w") as zipped:
        zipped.writestr("upstream/bin/ffmpeg.exe", payload)
        zipped.writestr("upstream/bin/unreviewed.exe", b"must not be copied")
    manifest = {
        "archive_root": "upstream",
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "files": {"bin/ffmpeg.exe": hashlib.sha256(payload).hexdigest()},
    }
    destination = tmp_path / "prepared"
    helper.extract_runtime(archive, destination, manifest)
    helper.verify_runtime(destination, manifest)
    assert (destination / "bin" / "ffmpeg.exe").read_bytes() == payload
    assert not (destination / "bin" / "unreviewed.exe").exists()
    (destination / "bin" / "ffmpeg.exe").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash"):
        helper.verify_runtime(destination, manifest)
    manifest["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        helper.extract_runtime(archive, tmp_path / "rejected", manifest)


def test_runtime_check_loads_inference_dependencies_without_model_or_api(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai4music_engine import launcher

    monkeypatch.delenv("AI4MUSIC_LAUNCH_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["engine.exe", "--runtime-check"])
    launcher.main()
    status = json.loads(capsys.readouterr().out)
    assert status["event"] == "engine_runtime"
    assert status["torch_version"] == "2.5.1+cpu"
    assert status["demucs_version"] == "4.0.1"


def test_windows_build_and_bundle_verification_are_fail_closed() -> None:
    assert BUILD_SCRIPT.is_file()
    assert VERIFY_SCRIPT.is_file()
    build = BUILD_SCRIPT.read_text(encoding="utf-8")
    verify = VERIFY_SCRIPT.read_text(encoding="utf-8")

    ordered_build_steps = (
        "uv sync --all-packages --python 3.12 --locked",
        "pytest -q",
        "ruff format --check",
        "ruff check",
        "mypy packages/engine-core/src services/engine/src",
        "build_sidecar.ps1",
        "npm ci",
        "npm test",
        "npm run build",
        "cargo fmt --manifest-path src-tauri/Cargo.toml -- --check",
        "cargo test --manifest-path src-tauri/Cargo.toml",
        "cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings",
        "npm run tauri build -- --bundles nsis",
        "verify_windows_bundle.ps1",
    )
    positions = [build.index(step) for step in ordered_build_steps]
    assert positions == sorted(positions)
    assert "$ErrorActionPreference = 'Stop'" in build

    for required_check in (
        "*-setup.exe",
        "Get-FileHash",
        "ai4music-engine.exe",
        "Join-Path $payloadEngine 'ai4music-engine.exe'",
        "Join-Path $payloadEngine '_internal'",
        "_internal",
        "THIRD_PARTY_NOTICES.md",
        ".env",
        "app.db",
        "Get-Command '7z'",
        "Payload notice hash does not match source",
        "Engine payload manifest does not match source",
    ):
        assert required_check in verify
    assert "([IO.Path]::GetPathRoot($repo) + 'ai4music')" in verify
    assert "$ErrorActionPreference = 'Stop'" in verify
